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

variable "dns_zone_names" {
  type        = list(string)
  description = "Cloudflare DNS zone names external-dns publishes into; the first owns hostname. Ingress hosts may live in any of them."

  validation {
    condition     = length(var.dns_zone_names) > 0
    error_message = "dns_zone_names must name at least the zone that owns hostname."
  }
}

variable "cloudflare_api_token" {
  type        = string
  sensitive   = true
  description = "Cloudflare API token (Zone:Read + DNS:Edit on every dns_zone_names zone) for external-dns and cert-manager's DNS-01 solver. Supplied out-of-band (TF_VAR_cloudflare_api_token / gitignored tfvars), never committed."

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

variable "owns_account_resources" {
  type        = bool
  description = "Whether this environment owns the account-wide ECR repositories and SES domain identity."
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
  description = "IAM principal ARNs granted EKS cluster-admin via access entries. One entry matches one exact principal and never a role assumed through it, so every principal that needs kubectl — the CI deploy role, an operator's Identity Center role — is named here in its own right."
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
  description = "Block deletion of the database that holds workspace and turn state."
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
