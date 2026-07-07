variable "name" {
  type        = string
  description = "Environment name; prefixes every resource (e.g. prod)."
}

variable "region" {
  type        = string
  description = "AWS region to deploy into."
}

variable "hostname" {
  type        = string
  description = "Public apex FQDN the service serves (e.g. flyingobject.ai)."
}

variable "dns_zone_name" {
  type        = string
  description = "DNS zone name that owns hostname (e.g. flyingobject.ai), authoritative on Cloudflare. external-dns publishes records and cert-manager solves DNS-01 there."
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
  description = "Instance types for the managed node group (amd64 — matches the bundle image)."
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

variable "cluster_admin_principal_arns" {
  type        = list(string)
  default     = []
  description = "IAM principal ARNs granted EKS cluster-admin via access entries (e.g. the CI deploy role and the account root for local kubectl)."
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
  type        = string
  default     = "ufo"
  description = "The shared application database (RFC 0011 §2: one DB, RLS on workspace_id)."
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

# ---- Sandbox ----

variable "e2b_sandbox_template" {
  type        = string
  default     = "ufo-sbx"
  description = "E2B template id the runtime launches sandboxes from; seeded into the platform Secret (e2b-sandbox-template) and replicated to tenant pods."
}
