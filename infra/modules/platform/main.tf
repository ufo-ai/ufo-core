data "aws_caller_identity" "current" {}

locals {
  name = var.name
  tags = merge({
    "app.kubernetes.io/part-of"   = "ufo"
    "flyingobject.ai/environment" = var.name
  }, var.tags)

  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  ecr_registry     = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com"
  app_s3_role_name = "${local.name}-app-s3"

  # The gateway, shared serve fleet, proxy, and observability stack run here.
  system_namespace = "ufo-system"

  # Secrets Manager path prefix for this environment.
  secret_prefix = "ufo/${var.name}"
}

data "aws_availability_zones" "available" {
  state = "available"
}
