variable "name" {
  type        = string
  description = "Environment name; prefixes every resource (e.g. metalcraft-testing)."
}

variable "region" {
  type        = string
  description = "AWS region to deploy into."
}

variable "hostname" {
  type        = string
  description = "Public FQDN the gateway serves (e.g. testing.flyingobject.ai)."
}

variable "dns_zone_name" {
  type        = string
  description = "DNS zone name that owns hostname (e.g. flyingobject.ai), authoritative on Cloudflare. external-dns publishes records and cert-manager solves DNS-01 there."
}

variable "gateway_backend" {
  type        = string
  default     = "action-gateway"
  description = "Service (and pod label) the gateway HTTPRoute routes the public hostname to. Hosted envs run the extended cloud-gateway."
}

variable "cloudflare_api_token" {
  type        = string
  sensitive   = true
  description = "Cloudflare API token (Zone:Read + DNS:Edit on dns_zone_name's Cloudflare zone) for external-dns and cert-manager's DNS-01 solver. Supplied out-of-band (TF_VAR_cloudflare_api_token / gitignored tfvars), never committed."

  validation {
    condition     = length(var.cloudflare_api_token) >= 20
    error_message = "cloudflare_api_token must be set (a Cloudflare API token with Zone:Read + DNS:Edit). Provide it via TF_VAR_cloudflare_api_token — in CI, the CLOUDFLARE_API_TOKEN GitHub Actions secret."
  }
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Extra tags applied to every taggable resource."
}

# ---- Networking ----

variable "vpc_cidr" {
  type        = string
  default     = "10.0.0.0/16"
  description = "VPC CIDR. Must be large enough for EKS pod ENIs."
}

variable "az_count" {
  type        = number
  default     = 3
  description = "Number of AZs to spread subnets across."
}

variable "single_nat_gateway" {
  type        = bool
  default     = false
  description = "One NAT gateway (cheap, non-HA) vs one per-AZ. Set true for testing."
}

# ---- EKS ----

variable "kubernetes_version" {
  type        = string
  default     = "1.31"
  description = "EKS control-plane version."
}

variable "node_instance_types" {
  type        = list(string)
  default     = ["m6i.large"]
  description = "Instance types for the managed node group (amd64 — matches the platform image)."
}

variable "node_min_size" {
  type    = number
  default = 2
}

variable "node_max_size" {
  type    = number
  default = 6
}

variable "node_desired_size" {
  type    = number
  default = 3
}

# ---- RDS (PostgreSQL 16) ----

variable "postgres_version" {
  type    = string
  default = "16"
}

variable "rds_instance_class" {
  type        = string
  default     = "db.m6g.large"
  description = "RDS instance class. Smaller (db.t4g.medium) is fine for testing."
}

variable "rds_allocated_storage" {
  type    = number
  default = 50
}

variable "rds_max_allocated_storage" {
  type        = number
  default     = 200
  description = "Storage autoscaling ceiling."
}

variable "rds_multi_az" {
  type        = bool
  default     = true
  description = "Multi-AZ standby. Set false for testing to halve cost."
}

variable "rds_deletion_protection" {
  type        = bool
  default     = true
  description = "Block deletion of the DB (holds all tenant + turn state). Flip off by hand to recreate an env."
}

variable "app_database_name" {
  type    = string
  default = "metalcraft"
}

variable "app_role_name" {
  type        = string
  default     = "metalcraft_app"
  description = "Non-owner Postgres role tenant-request components connect as. RLS-subject (the owner role 'metalcraft' bypasses RLS). The owner-run product migration provisions it; see db.app_role_grants."
}

variable "dbos_database_name" {
  type        = string
  default     = "metalcraft_dbos"
  description = "DBOS system database (workflow/queue state), separate from the application DB."
}

variable "db_url_scheme" {
  type        = string
  default     = "postgresql+psycopg"
  description = "SQLAlchemy URL scheme. Must select psycopg3 — the app pins psycopg[binary] only, so bare postgresql:// (psycopg2) fails."
}

# ---- ElastiCache (Redis live-frame hub) ----

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}

variable "redis_engine_version" {
  type    = string
  default = "7.1"
}

variable "redis_num_nodes" {
  type        = number
  default     = 2
  description = "Nodes in the replication group (primary + replicas). >1 enables automatic failover."
}

# ---- Application image ----

variable "image_tag" {
  type        = string
  description = "Container image tag for the metalcraft platform + sandbox-proxy images (e.g. a git SHA)."
}

variable "enable_app" {
  type        = bool
  default     = true
  description = "Install the metalcraft Helm chart. Set false to bring up substrate+addons only (e.g. before the first image is pushed)."
}

variable "chart_path" {
  type        = string
  default     = "../../../charts/metalcraft"
  description = "Path to the metalcraft Helm chart, relative to the env root module."
}

variable "cluster_admin_principal_arns" {
  type        = list(string)
  default     = []
  description = "IAM principal ARNs granted EKS cluster-admin via access entries (e.g. the CI deploy role)."
}

variable "e2b_sandbox_template" {
  type        = string
  description = "E2B template id the executor launches sandboxes from (runtime-config e2b-sandbox-template)."
}

variable "sandbox_egress_proxy" {
  type        = string
  default     = "off"
  description = "Sandbox egress mode (runtime-config sandbox-egress-proxy): 'off'/'false'/'0' forces direct egress; otherwise routes through the CONNECT proxy at sandbox-proxy-url."
}

variable "index_backend" {
  type        = string
  default     = "pgvector"
  description = "Vector index backend new tenants provision with (chart tenant.indexBackend). 'turbopuffer' requires turbopuffer-api-key in the api-keys Secrets Manager secret, set out-of-band before apply, or turbopuffer Stores never go Ready."

  validation {
    condition     = contains(["pgvector", "turbopuffer"], var.index_backend)
    error_message = "index_backend must be pgvector or turbopuffer."
  }
}

# ---- Add-on toggles ----

variable "letsencrypt_email" {
  type        = string
  description = "Contact email for the Let's Encrypt ACME account (gateway TLS)."
}

variable "acme_server" {
  type        = string
  default     = "https://acme-v02.api.letsencrypt.org/directory"
  description = "ACME directory. Point at staging for testing to avoid rate limits."
}

variable "datadog_enabled" {
  type        = bool
  default     = false
  description = "Ship OTLP telemetry to Datadog: enables app-pod instrumentation and the collector's datadog exporter. Requires datadog-api-key set out-of-band in the api-keys Secrets Manager secret BEFORE apply — an empty key crash-loops the collector."
}

variable "datadog_site" {
  type        = string
  default     = "datadoghq.com"
  description = "Datadog site (DD_SITE), e.g. datadoghq.com, datadoghq.eu, us5.datadoghq.com."
}
