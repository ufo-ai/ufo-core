resource "random_password" "rds" {
  length  = 32
  special = false # RDS master password disallows several specials; keep it URL-safe.
}

# The seed derives the shared RLS-subject serve role password.
resource "random_password" "pg_role_seed" {
  length  = 48
  special = false
}

# UFO_TOKEN_SECRET — the member-surface bearer HMAC (RFC 0011 §4): the ufo surface verifies member
# tokens and the gateway mints them, both keyed by this.
resource "random_password" "ufo_token" {
  length  = 48
  special = false
}

# The shared serve fleet's process keys live only in the ufo-serve Secret.
# The Fernet credential key seals every hosted workspace's BYOK credential rows, so it is minted once
# and reused verbatim every apply — a re-minted key orphans every stored credential. A Fernet key is
# url-safe base64 of 32 bytes; random_id.b64_url is exactly that without padding, so the trailing "="
# (the single pad byte a 32-byte value needs) is appended in the serve_credential_key local below.
resource "random_id" "serve_credential_key" {
  byte_length = 32
}

# UFO_ARTIFACT_TOKEN_SECRET — the shared fleet's artifact-delivery signing secret (config
# artifacts.token_secret_env).
resource "random_password" "serve_artifact_token" {
  length  = 64
  special = false
}

locals {
  rds_endpoint = module.rds.db_instance_endpoint # host:port

  # Owner admin DSN: LOGIN as ufo_owner (RDS master, table owner → RLS bypass) on the shared `ufo`
  # database. The bootstrap dials this with asyncpg, so it is a plain libpq URL.
  admin_dsn = "postgresql://ufo_owner:${random_password.rds.result}@${local.rds_endpoint}/${var.app_database_name}"

  # The shared serve fleet's DSN — the RLS-*subject* ufo_serve role on the shared app database, the
  # one role for every hosted workspace (it sets app.workspace_id per transaction). The password is
  # derived from the same seed + formula as control/rls.py serve_password()
  # (sha256("<seed>:ufo_serve")), reproduced here so the terraform-rendered config matches the role the
  # rls-bootstrap Job creates — a cross-runtime contract, control/src/ufo_control/rls.py is the
  # source of truth. Core
  # derives the DBOS system store as the `<name>_dbos` sibling (config.DatabaseConfig), so this url
  # alone resolves the fleet's shared `ufo_dbos` system database; the rls-bootstrap Job provisions it.
  serve_password = sha256("${random_password.pg_role_seed.result}:ufo_serve")
  serve_dsn      = "postgresql+asyncpg://ufo_serve:${local.serve_password}@${local.rds_endpoint}/${var.app_database_name}"

  # Fernet key: url-safe base64 of 32 bytes needs one "=" of padding, which random_id.b64_url omits.
  serve_credential_key = "${random_id.serve_credential_key.b64_url}="
}

# Postgres bootstrap credentials.
resource "aws_secretsmanager_secret" "postgres" {
  name = "${local.secret_prefix}/postgres"
  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "postgres" {
  secret_id = aws_secretsmanager_secret.postgres.id
  secret_string = jsonencode({
    "postgres-admin-dsn" = local.admin_dsn
    "pg-role-seed"       = random_password.pg_role_seed.result
  })
}

# Platform-generated runtime material.
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

resource "tls_private_key" "sandbox_proxy" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "sandbox_proxy" {
  private_key_pem = tls_private_key.sandbox_proxy.private_key_pem
  dns_names       = ["sandbox-proxy.${var.hostname}"]

  subject {
    common_name = "sandbox-proxy.${var.hostname}"
  }
}

resource "tls_locally_signed_cert" "sandbox_proxy" {
  cert_request_pem      = tls_cert_request.sandbox_proxy.cert_request_pem
  ca_private_key_pem    = tls_private_key.egress_ca.private_key_pem
  ca_cert_pem           = tls_self_signed_cert.egress_ca.cert_pem
  validity_period_hours = 8760
  early_renewal_hours   = 720
  allowed_uses          = ["digital_signature", "key_encipherment", "server_auth"]
}

# The retired front door's certificate, kept only until its `create_before_destroy` is out of state:
# the flag propagates to every resource it depends on and inverts their destroy edges, so deleting
# the chain while state still records it makes terraform's graph cyclic against the manifests that
# once read its ARN. Dropping the flag here is what lets the next apply delete the chain outright.
resource "aws_acm_certificate" "sandbox_proxy" {
  private_key       = tls_private_key.sandbox_proxy.private_key_pem
  certificate_body  = tls_locally_signed_cert.sandbox_proxy.cert_pem
  certificate_chain = tls_self_signed_cert.egress_ca.cert_pem
  tags              = local.tags
}

# The proxy's front door is a public endpoint, so its NLB listener carries a publicly trusted
# certificate and a sandbox reaches the proxy on the trust store it already has; the egress CA above
# is left to the leaves the proxy mints inside the tunnel. The member's terminal is the carrier that
# makes the split load-bearing — it runs commands on a machine whose trust store nothing may touch,
# and curl reads no environment variable for the certificate of an HTTPS proxy. The validation
# record names this environment's own host, so each environment owns its own; the zone's shared
# singletons stay with testing.
data "cloudflare_zone" "dns" {
  filter = { name = var.dns_zone_name }
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
  secret_id = aws_secretsmanager_secret.platform.id
  secret_string = jsonencode({
    "ufo-token-secret" = random_password.ufo_token.result
    "egress-ca-cert"   = tls_self_signed_cert.egress_ca.cert_pem
    "egress-ca-key"    = tls_private_key.egress_ca.private_key_pem
  })
}

resource "aws_secretsmanager_secret" "api_keys" {
  name = "${local.secret_prefix}/api-keys"
  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "api_keys" {
  count = var.manage_runtime_secret_versions ? 1 : 0

  secret_id = aws_secretsmanager_secret.api_keys.id
  secret_string = jsonencode({
    "anthropic-api-key"                      = ""
    "bedrock-api-key"                        = ""
    "openai-api-key"                         = ""
    "openrouter-api-key"                     = ""
    "composio-api-key"                       = ""
    "pipedream-client-id"                    = ""
    "pipedream-client-secret"                = ""
    "pipedream-project-id"                   = ""
    "pipedream-gmail-oauth-app-id"           = ""
    "e2b-api-key"                            = ""
    "browserbase-api-key"                    = ""
    "exa-api-key"                            = ""
    "turbopuffer-api-key"                    = ""
    "datadog-api-key"                        = ""
    "metronome-bearer-token"                 = ""
    "metronome-package-alias"                = ""
    "stripe-secret-key"                      = ""
    "stripe-billing-portal-configuration-id" = ""
    "slack-client-id"                        = ""
    "slack-client-secret"                    = ""
    "slack-signing-secret"                   = ""
    "github-app-id"                          = ""
    "github-app-client-id"                   = ""
    "github-app-client-secret"               = ""
    "github-app-private-key"                 = ""
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

moved {
  from = aws_secretsmanager_secret_version.api_keys
  to   = aws_secretsmanager_secret_version.api_keys[0]
}

resource "aws_secretsmanager_secret" "gateway_slack_connect" {
  name = "${local.secret_prefix}/gateway-slack-connect"
  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "gateway_slack_connect" {
  count = var.manage_runtime_secret_versions ? 1 : 0

  secret_id = aws_secretsmanager_secret.gateway_slack_connect.id
  secret_string = jsonencode({
    "bot-token" = ""
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

moved {
  from = aws_secretsmanager_secret_version.gateway_slack_connect
  to   = aws_secretsmanager_secret_version.gateway_slack_connect[0]
}

# The gateway's WorkOS credentials are a standing prerequisite, created and seeded out-of-band once
# per environment before its first deploy (docs/onboarding.md). Terraform reads the secret rather
# than owning it: an empty placeholder version would boot the gateway with blank WORKOS_API_KEY /
# WORKOS_CLIENT_ID, which fails its startup check, so the seed must precede the rollout and can never
# be produced by the same apply that rolls the gateway.
data "aws_secretsmanager_secret" "gateway_workos" {
  name = "${local.secret_prefix}/gateway-workos"
}

# Testing already holds this secret and its placeholder version in terraform state from when they
# were managed resources; drop both from state without deleting the live seeded secret in AWS. Prod
# never applied them, so both are a no-op there.
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
