data "aws_caller_identity" "current" {}

locals {
  name = var.name
  tags = merge({
    "app.kubernetes.io/part-of"   = "ufo"
    "flyingobject.ai/environment" = var.name
  }, var.tags)

  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  ecr_registry = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com"

  # The control plane + apex workspace run here; the operator, ESO target, and IRSA trust name it.
  # Tenants run in their own ufo-<name> namespaces (the operator creates them at provisioning).
  system_namespace = "ufo-system"

  # Service accounts (in system_namespace) that read/write the S3 store bucket via IRSA.
  s3_service_accounts = ["ufo-operator", "ufo-serve"]

  # Secrets Manager path prefix for this environment.
  secret_prefix = "ufo/${var.name}"
}

data "aws_availability_zones" "available" {
  state = "available"
}
