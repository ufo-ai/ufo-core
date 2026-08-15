locals {
  proxy_tags = {
    "flyingobject.ai/environment" = "testing"
    "ManagedBy"                   = "terraform"
  }
}

locals {
  # What `ufoctl proxy` actually reads. The cluster's own ufo.toml carries a redis url, an
  # in-cluster collector address and a serve dsn: from this region those are unused, unreachable
  # and unused, so the proxy is given its own rather than three statements that are not true.
  proxy_config_toml = <<-TOML
    [database]
    url = "${module.platform.serve_dsn}"

    [models]
    anthropic_api_key_env = "ANTHROPIC_API_KEY"
    openai_api_key_env = "OPENAI_API_KEY"

    [credentials]
    key_env = "UFO_CREDENTIAL_KEY"

    [blob]
    backend = "s3"
    bucket = "${module.platform.blob_bucket}"
    region = "${var.region}"

    [sandbox]
    backend = "e2b"
    proxy_port = 8888
    proxy_public_url = "https://sandbox-proxy-west.${module.platform.hostname}"

    [serve]
    graceful_shutdown_seconds = ${local.graceful_shutdown_seconds}

    [o11y]
    otlp_endpoint = "http://${local.collector_peered_hostname}:4318"

    [pack]
    name = "assistant_hosted"
  TOML
}

# The sandbox egress proxy, colocated with the sandboxes it serves. e2b runs sandboxes in GCP
# us-west1; measured from inside one, a TLS handshake to the us-east-1 proxy costs ~130ms against
# ~58ms to us-west-2, because the handshake's two round trips cross the country. Every egress call a
# sandbox makes pays it, and a dependency install pays it thousands of times.
#
# Only Postgres crosses back, and it is read more often than per turn: rules and credentials are
# cached per run token, but `turn_live` is deliberately not — it is re-read on every CONNECT so a
# turn that ends between requests can no longer draw a key. That costs ~260ms per CONNECT over the
# peering. It still wins because the saving is per request, not per CONNECT: measured, a
# 163-package install issues 433 fetches over npm's 15 sockets, so ~29 requests ride each
# authorization and each of them drops ~126ms.
# The colocated proxy answers on its own name, so external-dns keeps ownership of the in-cluster
# one and the two never contend. An ACM certificate cannot cross a region, so this one is issued
# where the proxy runs and publishes its own validation records.
resource "aws_acm_certificate" "sandbox_proxy_west" {
  provider          = aws.proxy
  domain_name       = "sandbox-proxy-west.${module.platform.hostname}"
  validation_method = "DNS"
  tags              = local.proxy_tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "cloudflare_dns_record" "sandbox_proxy_west_validation" {
  for_each = {
    for option in aws_acm_certificate.sandbox_proxy_west.domain_validation_options :
    option.domain_name => option
  }

  zone_id = data.cloudflare_zone.flyingobject_ai.id
  name    = trimsuffix(each.value.resource_record_name, ".")
  type    = each.value.resource_record_type
  content = trimsuffix(each.value.resource_record_value, ".")
  ttl     = 60
  proxied = false
}

resource "aws_acm_certificate_validation" "sandbox_proxy_west" {
  provider        = aws.proxy
  certificate_arn = aws_acm_certificate.sandbox_proxy_west.arn
  validation_record_fqdns = [
    for record in cloudflare_dns_record.sandbox_proxy_west_validation : record.name
  ]
}

# `secret_string` is ForceNew, so adding a key replaces the version. Destroying the old one first
# would leave everything that reads this secret — serve, the gateway, the proxy — without a current
# version for the width of the apply, so the replacement is ordered the other way.

module "sandbox_proxy_west" {
  source = "../../modules/sandbox_proxy_west"

  providers = {
    aws      = aws.proxy
    aws.east = aws
  }

  name                       = "ufo-testing"
  vpc_cidr                   = var.proxy_vpc_cidr
  east_vpc_id                = module.platform.vpc_id
  east_vpc_cidr              = module.platform.vpc_cidr_block
  east_route_table_ids       = module.platform.private_route_table_ids
  east_rds_security_group_id = module.platform.rds_security_group_id
  config_toml                = local.proxy_config_toml

  # The platform region's registry. A cross-region pull costs transfer and start latency, but ECR
  # replicates only what is pushed after the configuration exists, so the first apply would name a
  # digest this region does not hold and the tasks would never start.
  image               = local.bundle_image
  certificate_arn     = aws_acm_certificate_validation.sandbox_proxy_west.certificate_arn
  otlp_endpoint       = "http://${local.collector_peered_hostname}:4318"
  postgres_secret_arn = module.platform.secret_arns.postgres
  platform_secret_arn = module.platform.secret_arns.platform
  api_keys_secret_arn = module.platform.secret_arns.api_keys

  tags = {
    "flyingobject.ai/environment" = "testing"
    "ManagedBy"                   = "terraform"
  }
}

# The name the sandboxes dial. It is this module's own, so external-dns keeps the in-cluster name
# and neither system can take the other's record. Reverting `proxy_public_url` returns every
# sandbox to the platform region without touching DNS at all.
resource "cloudflare_dns_record" "sandbox_proxy" {
  zone_id = data.cloudflare_zone.flyingobject_ai.id
  name    = "sandbox-proxy-west.${module.platform.hostname}"
  type    = "CNAME"
  content = module.sandbox_proxy_west.load_balancer_dns_name
  ttl     = 60
  proxied = false
}
