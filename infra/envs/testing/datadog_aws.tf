
locals {
  datadog_aws_account_id = "464622532012"
}

resource "datadog_integration_aws_external_id" "ufo" {}

data "aws_iam_policy_document" "datadog_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.datadog_aws_account_id}:root"]
    }

    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [datadog_integration_aws_external_id.ufo.id]
    }
  }
}

data "aws_iam_policy_document" "datadog_rds_metrics" {
  statement {
    effect = "Allow"
    actions = [
      "cloudwatch:Describe*",
      "cloudwatch:GetMetricData",
      "cloudwatch:GetMetricStatistics",
      "cloudwatch:ListMetrics",
      "rds:Describe*",
      "rds:ListTagsForResource",
      "tag:GetResources",
      "tag:GetTagKeys",
      "tag:GetTagValues",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "datadog" {
  name               = "ufo-datadog-integration"
  description        = "Datadog reads CloudWatch RDS metrics for the ufo fleet"
  assume_role_policy = data.aws_iam_policy_document.datadog_assume_role.json
}

resource "aws_iam_role_policy" "datadog_rds_metrics" {
  name   = "rds-metrics"
  role   = aws_iam_role.datadog.id
  policy = data.aws_iam_policy_document.datadog_rds_metrics.json
}

resource "datadog_integration_aws_account" "ufo" {
  aws_account_id = data.aws_caller_identity.current.account_id
  aws_partition  = "aws"

  auth_config {
    aws_auth_config_role {
      role_name   = aws_iam_role.datadog.name
      external_id = datadog_integration_aws_external_id.ufo.id
    }
  }

  aws_regions {
    include_only = [var.region]
  }

  metrics_config {
    namespace_filters {
      include_only = ["AWS/RDS"]
    }
  }

  logs_config {
    lambda_forwarder {}
  }

  traces_config {
    xray_services {}
  }

  resources_config {
    cloud_security_posture_management_collection = false
    extended_collection                          = false
  }
}
