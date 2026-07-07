data "aws_caller_identity" "current" {}

locals {
  name = var.name
  tags = merge({
    "app.kubernetes.io/part-of" = "metalcraft"
    "metalcraft.ai/environment" = var.name
  }, var.tags)

  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  ecr_registry = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com"

  sandbox_egress_proxy_enabled = !contains(["", "0", "false", "off"], lower(trimspace(var.sandbox_egress_proxy)))
  sandbox_proxy_hostname       = "sandbox-proxy.${var.hostname}"
  sandbox_proxy_url            = local.sandbox_egress_proxy_enabled ? "http://${local.sandbox_proxy_hostname}:8888" : ""

  # System namespace the chart installs into; IRSA trust + ESO target it.
  system_namespace = "metalcraft-system"

  # Service accounts (in system_namespace) that read/write the S3 store bucket.
  s3_service_accounts = ["operator", "gateway", "executor", "job-runner"]

  # Secrets Manager path prefix for this environment.
  secret_prefix = "metalcraft/${var.name}"
}

data "aws_availability_zones" "available" {
  state = "available"
}
