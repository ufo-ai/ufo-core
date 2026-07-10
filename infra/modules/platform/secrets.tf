# Platform secrets live in AWS Secrets Manager; External Secrets Operator (ufo.tf) syncs them into
# the K8s Secrets the control plane and tenant pods reference. Terraform GENERATES the
# platform-internal material (DB owner password, role-derivation seed, token secrets) and SEEDS empty
# containers for third-party API keys — those values are set out-of-band so they never enter state.

resource "random_password" "rds" {
  length  = 32
  special = false # RDS master password disallows several specials; keep it URL-safe.
}

# The seed the control plane derives each tenant's Postgres role password from — sha256(seed ‖ name),
# nothing persisted (control/postgres.py). Consumed only by the operator (never replicated to tenants:
# holding it would let one tenant re-derive a sibling's login).
resource "random_password" "pg_role_seed" {
  length  = 48
  special = false
}

# UFO_TOKEN_SECRET — the member-surface bearer HMAC (RFC 0011 §4): the ufo surface verifies member
# tokens and the gateway mints them, both keyed by this. Platform-wide so a token minted at the apex
# verifies in any tenant; replicated to every tenant namespace. (The per-tenant artifact-token and
# Fernet credential key are minted by the control plane's render.py, not here.)
resource "random_password" "ufo_token" {
  length  = 48
  special = false
}

# The shared serve fleet's own platform keys (RFC 0011 §2 hosted tier). Unlike a per-tenant serve —
# whose Fernet + artifact keys the operator's render.py mints into that tenant's Secret — the ONE
# shared fleet has no per-tenant Secret, so the platform mints its keys here and they live only in the
# ufo-serve Secret (ufo-system), never replicated to enterprise tenant namespaces.
#
# The Fernet credential key seals every hosted workspace's BYOK credential rows, so it is minted once
# and reused verbatim every apply — a re-minted key orphans every stored credential. A Fernet key is
# url-safe base64 of 32 bytes; random_id.b64_url is exactly that without padding, so the trailing "="
# (the single pad byte a 32-byte value needs) is appended in the serve_credential_key local below.
resource "random_id" "serve_credential_key" {
  byte_length = 32
}

# UFO_SESSION_SECRET — the HMAC the shared fleet signs member session tokens with (config
# serve.session_secret_env); the token carries the workspace claim the surface trusts before any
# RLS-scoped read.
resource "random_password" "serve_session_secret" {
  length  = 48
  special = false
}

# UFO_ARTIFACT_TOKEN_SECRET — the shared fleet's artifact-delivery signing secret (config
# artifacts.token_secret_env), the hosted counterpart to the per-tenant minted artifact token.
resource "random_password" "serve_artifact_token" {
  length  = 64
  special = false
}

locals {
  rds_endpoint = module.rds.db_instance_endpoint # host:port

  # Owner admin DSN: LOGIN as ufo_owner (RDS master, table owner → RLS bypass) on the shared `ufo`
  # database. The control plane's postgres.py dials this with asyncpg to mint per-tenant roles, so it
  # is a plain libpq URL (postgresql://), NOT a SQLAlchemy +driver scheme.
  admin_dsn = "postgresql://ufo_owner:${random_password.rds.result}@${local.rds_endpoint}/${var.app_database_name}"

  # The shared serve fleet's DSN — the RLS-*subject* ufo_serve role on the shared app database, the
  # one role for every hosted workspace (it sets app.workspace_id per transaction). The password is
  # derived from the same seed + formula as control/postgres.py serve_password()
  # (sha256("<seed>:ufo_serve")), reproduced here so the terraform-rendered config matches the role the
  # rls-bootstrap Job creates — a cross-runtime contract, postgres.py is the source of truth. Core
  # derives the DBOS system store as the `<name>_dbos` sibling (config.DatabaseConfig), so this url
  # alone resolves the fleet's shared `ufo_dbos` system database; the rls-bootstrap Job provisions it.
  serve_password = sha256("${random_password.pg_role_seed.result}:ufo_serve")
  serve_dsn      = "postgresql+asyncpg://ufo_serve:${local.serve_password}@${local.rds_endpoint}/${var.app_database_name}"

  # Fernet key: url-safe base64 of 32 bytes needs one "=" of padding, which random_id.b64_url omits.
  serve_credential_key = "${random_id.serve_credential_key.b64_url}="
}

# 1) Postgres control-plane credentials → operator-only (NOT the replicated platform secret).
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

# 2) Platform-generated material tenant pods read from env → replicated into each tenant namespace.
#    e2b-sandbox-template and ses-sender are non-secret config co-located here so one ExternalSecret
#    carries everything a tenant's serve needs.
resource "aws_secretsmanager_secret" "platform" {
  name = "${local.secret_prefix}/platform"
  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "platform" {
  secret_id = aws_secretsmanager_secret.platform.id
  secret_string = jsonencode({
    "ufo-token-secret"     = random_password.ufo_token.result
    "e2b-sandbox-template" = var.e2b_sandbox_template
    "ses-sender"           = var.ses_sender
  })
}

# 3) Third-party API keys → seeded empty, filled out-of-band, never in TF state. Replicated to tenants.
resource "aws_secretsmanager_secret" "api_keys" {
  name = "${local.secret_prefix}/api-keys"
  tags = local.tags
}

resource "aws_secretsmanager_secret_version" "api_keys" {
  secret_id = aws_secretsmanager_secret.api_keys.id
  secret_string = jsonencode({
    "anthropic-api-key"            = ""
    "openai-api-key"               = ""
    "openrouter-api-key"           = ""
    "composio-api-key"             = ""
    "pipedream-client-id"          = ""
    "pipedream-client-secret"      = ""
    "pipedream-project-id"         = ""
    "pipedream-gmail-oauth-app-id" = ""
    "e2b-api-key"                  = ""
    "exa-api-key"                  = ""
    "turbopuffer-api-key"          = ""
    "datadog-api-key"              = ""
  })

  lifecycle {
    ignore_changes = [secret_string] # operator sets real values; don't clobber on re-apply.
  }
}
