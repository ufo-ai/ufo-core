# Platform secrets live in AWS Secrets Manager; External Secrets Operator (chart-side)
# syncs them into the K8s Secrets the deployments reference. Terraform GENERATES the
# platform-internal material (DB creds, egress CA, artifact token) and SEEDS empty
# containers for third-party API keys — those values are set out-of-band so they never
# enter Terraform state.

resource "random_password" "rds" {
  length  = 32
  special = false # RDS master password disallows several specials; keep it URL-safe.
}

# Non-owner application role's password. The owner role is the RDS master (random_password.rds);
# the app role is provisioned by the owner-run product migration (db.app_role_grants), which
# CREATE ROLEs it with this password and GRANTs it CRUD on the tenant tables. Tenant-request
# components connect as this role so RLS enforces — the owner would bypass RLS.
resource "random_password" "app_role" {
  length  = 32
  special = false
}

resource "random_password" "artifact_token" {
  length  = 48
  special = false
}

# Egress MITM CA the sandbox-proxy uses to terminate sandbox egress (ca-cert/ca-key).
resource "tls_private_key" "egress_ca" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_self_signed_cert" "egress_ca" {
  private_key_pem   = tls_private_key.egress_ca.private_key_pem
  is_ca_certificate = true

  subject {
    common_name  = "metalcraft-egress-ca (${var.name})"
    organization = "metalcraft"
  }

  validity_period_hours = 87600 # 10 years
  allowed_uses          = ["cert_signing", "crl_signing", "key_encipherment", "digital_signature"]
}

locals {
  rds_endpoint = module.rds.db_instance_endpoint # host:port

  # Two-role split for RLS tenant isolation:
  #   owner role  = metalcraft (RDS master) — owns the tables, so bypasses RLS. Runs migrations
  #                 and the cross-tenant platform workers (job-runner, scan/reap CronJobs).
  #   app role    = metalcraft_app (non-owner) — RLS-subject. Tenant-request components (gateway,
  #                 executor, sandbox-proxy) connect as this, so a missing/wrong set_config of
  #                 metalcraft.namespace can never leak another tenant's rows.
  app_role_password = random_password.app_role.result

  # application-url: NON-OWNER app role on the app DB. RLS enforces.
  application_url = "${var.db_url_scheme}://${var.app_role_name}:${local.app_role_password}@${local.rds_endpoint}/${var.app_database_name}"
  # owner-url: OWNER role on the app DB. Migrations + platform workers (RLS bypass).
  owner_url = "${var.db_url_scheme}://metalcraft:${random_password.rds.result}@${local.rds_endpoint}/${var.app_database_name}"
  # system-url: OWNER role on the DBOS system DB (workflow/queue state; not tenant-scoped).
  dbos_system_url = "${var.db_url_scheme}://metalcraft:${random_password.rds.result}@${local.rds_endpoint}/${var.dbos_database_name}"
}

# 1) Postgres connection URLs → K8s secret metalcraft-postgres {application-url, owner-url, system-url}.
#    app-role-password is consumed by the owner-run product migration to CREATE the non-owner role.
resource "aws_secretsmanager_secret" "postgres" {
  name = "${local.secret_prefix}/postgres"
  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "postgres" {
  secret_id = aws_secretsmanager_secret.postgres.id
  secret_string = jsonencode({
    "application-url"   = local.application_url
    "owner-url"         = local.owner_url
    "system-url"        = local.dbos_system_url
    "app-role-name"     = var.app_role_name
    "app-role-password" = local.app_role_password
  })
}

# 2) Platform-generated material → metalcraft-runtime-secrets + metalcraft-egress-ca
resource "aws_secretsmanager_secret" "platform" {
  name = "${local.secret_prefix}/platform"
  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "platform" {
  secret_id = aws_secretsmanager_secret.platform.id
  secret_string = jsonencode({
    "artifact-token-secret" = random_password.artifact_token.result
    "egress-ca-cert"        = tls_self_signed_cert.egress_ca.cert_pem
    "egress-ca-key"         = tls_private_key.egress_ca.private_key_pem
  })
}

# 3) Third-party API keys → seeded empty, filled out-of-band, never in TF state.
resource "aws_secretsmanager_secret" "api_keys" {
  name = "${local.secret_prefix}/api-keys"
  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "api_keys" {
  secret_id = aws_secretsmanager_secret.api_keys.id
  secret_string = jsonencode({
    "anthropic-api-key"   = ""
    "openai-api-key"      = ""
    "composio-api-key"    = ""
    "e2b-api-key"         = ""
    "exa-api-key"         = ""
    "browser-cdp-headers" = ""
    "datadog-api-key"     = ""
    "turbopuffer-api-key" = ""
  })

  lifecycle {
    ignore_changes = [secret_string] # operator sets real values; don't clobber on re-apply.
  }
}
