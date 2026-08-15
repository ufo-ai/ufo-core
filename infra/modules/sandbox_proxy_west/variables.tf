variable "name" {
  type        = string
  description = "Deployment name prefix, matching the platform module's."
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR for the proxy VPC. Must not overlap the platform VPC it peers with."
}

variable "az_count" {
  type        = number
  default     = 2
  description = "Availability zones the proxy subnets span."
}

variable "east_vpc_id" {
  type        = string
  description = "Platform VPC the proxy peers with to reach Postgres."
}

variable "east_vpc_cidr" {
  type        = string
  description = "Platform VPC CIDR, routed over the peering connection."
}

variable "east_route_table_ids" {
  type        = list(string)
  description = "Platform route tables that must reach this VPC over the peering connection."
}

variable "east_rds_security_group_id" {
  type        = string
  description = "Postgres security group the proxy is admitted to over the peering connection."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Tags applied to every resource in this module."
}

variable "config_toml" {
  type        = string
  sensitive   = true
  description = "The proxy's rendered ufo.toml. Secret: it carries the database URL."
}

variable "image" {
  type        = string
  description = "The digest-pinned bundle image, pulled from this region's replicated repository."
}

variable "certificate_arn" {
  type        = string
  description = "The proxy hostname's certificate in this region, terminated at the load balancer."
}

variable "otlp_endpoint" {
  type        = string
  description = "Collector address reachable over the peering, so this region's telemetry joins the same pipeline."
}

variable "postgres_secret_arn" {
  type        = string
  description = "Primary-region ARN of the Secrets Manager entry holding the owner DSN."
}

variable "platform_secret_arn" {
  type        = string
  description = "Primary-region ARN of the Secrets Manager entry holding the token secret, egress CA and credential key."
}

variable "api_keys_secret_arn" {
  type        = string
  description = "Primary-region ARN of the Secrets Manager entry holding the model and connector keys."
}

variable "desired_count" {
  type        = number
  default     = 2
  description = "Proxy tasks. Two so a deploy or an AZ loss never leaves the fleet without egress."
}

variable "task_cpu" {
  type        = number
  default     = 1024
  description = "Fargate CPU units per proxy task."
}

variable "task_memory" {
  type        = number
  default     = 2048
  description = "Fargate memory (MiB) per proxy task."
}

variable "log_retention_days" {
  type        = number
  default     = 30
  description = "Retention for the proxy's task logs."
}
