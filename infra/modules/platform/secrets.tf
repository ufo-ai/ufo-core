resource "random_password" "rds" {
  length  = 32
  special = false # RDS master password disallows several specials; keep it URL-safe.
}

resource "random_password" "pg_role_seed" {
  length  = 48
  special = false
}

resource "random_password" "ufo_token" {
  length  = 48
  special = false
}

resource "random_password" "cache_control_token" {
  length  = 48
  special = false
}

resource "random_password" "onboard_control_token" {
  length  = 48
  special = false
}

resource "random_password" "egress_control_token" {
  length  = 48
  special = false
}

resource "random_password" "preview_token" {
  length  = 48
  special = false
}

resource "random_id" "serve_credential_key" {
  byte_length = 32
}

resource "random_password" "serve_artifact_token" {
  length  = 64
  special = false
}

locals {
  rds_endpoint = module.rds.db_instance_endpoint # host:port

  admin_dsn = "postgresql://ufo_owner:${random_password.rds.result}@${local.rds_endpoint}/${var.app_database_name}"

  serve_password = sha256("${random_password.pg_role_seed.result}:ufo_serve")
  serve_dsn      = "postgresql+asyncpg://ufo_serve:${local.serve_password}@${local.rds_endpoint}/${var.app_database_name}"

  control_password = sha256("${random_password.pg_role_seed.result}:ufo_control")
  control_dsn      = "postgresql://ufo_control:${local.control_password}@${local.rds_endpoint}/${var.app_database_name}"

  serve_credential_key = "${random_id.serve_credential_key.b64_url}="
}

resource "aws_secretsmanager_secret" "postgres" {
  name = "${local.secret_prefix}/postgres"
  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "postgres" {
  secret_id = aws_secretsmanager_secret.postgres.id
  secret_string = jsonencode({
    "postgres-admin-dsn" = local.admin_dsn
    "control-dsn"        = local.control_dsn
    "pg-role-seed"       = random_password.pg_role_seed.result
  })
}

resource "aws_secretsmanager_secret" "platform" {
  name = "${local.secret_prefix}/platform"
  tags = local.tags
}

resource "tls_private_key" "egress_ca" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "tls_self_signed_cert" "egress_ca" {
  private_key_pem       = tls_private_key.egress_ca.private_key_pem
  validity_period_hours = 87600
  is_ca_certificate     = true
  allowed_uses          = ["cert_signing", "crl_signing", "digital_signature"]

  subject {
    common_name = "${local.name} sandbox egress CA"
  }
}

removed {
  from = tls_private_key.sandbox_proxy
  lifecycle {
    destroy = false
  }
}

removed {
  from = tls_cert_request.sandbox_proxy
  lifecycle {
    destroy = false
  }
}

removed {
  from = tls_locally_signed_cert.sandbox_proxy
  lifecycle {
    destroy = false
  }
}

data "cloudflare_zone" "dns" {
  filter = { name = var.dns_zone_names[0] }
}

resource "aws_acm_certificate" "sandbox_proxy_public" {
  domain_name       = "sandbox-proxy.${var.hostname}"
  validation_method = "DNS"
  tags              = local.tags

  lifecycle {
    create_before_destroy = true
  }
}

resource "cloudflare_dns_record" "sandbox_proxy_validation" {
  for_each = {
    for option in aws_acm_certificate.sandbox_proxy_public.domain_validation_options :
    option.domain_name => option
  }

  zone_id = data.cloudflare_zone.dns.id
  name    = trimsuffix(each.value.resource_record_name, ".")
  type    = each.value.resource_record_type
  content = trimsuffix(each.value.resource_record_value, ".")
  ttl     = 60
  proxied = false
}

resource "aws_acm_certificate_validation" "sandbox_proxy_public" {
  certificate_arn = aws_acm_certificate.sandbox_proxy_public.arn
  validation_record_fqdns = [
    for record in cloudflare_dns_record.sandbox_proxy_validation : record.name
  ]
}

resource "aws_secretsmanager_secret_version" "platform" {
  lifecycle {
    create_before_destroy = true
  }

  secret_id = aws_secretsmanager_secret.platform.id
  secret_string = jsonencode({
    "ufo-token-secret"        = random_password.ufo_token.result
    "egress-ca-cert"          = tls_self_signed_cert.egress_ca.cert_pem
    "egress-ca-key"           = tls_private_key.egress_ca.private_key_pem_pkcs8
    "egress-control-token"    = random_password.egress_control_token.result
    "onboard-control-token"   = random_password.onboard_control_token.result
    "ufo-cache-control-token" = random_password.cache_control_token.result
    "ufo-preview-token"       = random_password.preview_token.result
  })
}

resource "aws_secretsmanager_secret" "api_keys" {
  name = "${local.secret_prefix}/api-keys"
  tags = local.tags
}

resource "aws_secretsmanager_secret" "gateway_slack_connect" {
  name = "${local.secret_prefix}/gateway-slack-connect"
  tags = local.tags
}

removed {
  from = aws_secretsmanager_secret_version.api_keys
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_secretsmanager_secret_version.gateway_slack_connect
  lifecycle {
    destroy = false
  }
}

data "aws_secretsmanager_secret" "gateway_workos" {
  name = "${local.secret_prefix}/gateway-workos"
}

removed {
  from = aws_secretsmanager_secret.gateway_workos
  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_secretsmanager_secret_version.gateway_workos
  lifecycle {
    destroy = false
  }
}
