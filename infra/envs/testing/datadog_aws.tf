# Datadog's view of RDS. The database's own metrics — connection count, CPU, freeable memory —
# reach Datadog only through CloudWatch, and nothing was reading CloudWatch, so a saturating
# database was invisible in the same dashboards that carry every other signal. The application's
# OTLP exporter cannot supply these: it reports what the client experienced, never what the
# database was doing.
#
# The integration is per AWS account, not per environment, and both roots deploy into this one
# account. It lives in the testing root because that is the only root the deploy pipeline applies.
# Its metrics cover every RDS instance in the account, so prod's database is collected from here
# too, distinguished by the `dbinstanceidentifier` tag rather than by which root created this.
# `gates.py` holds the one-root rule.
#
# CloudWatch polling lands metrics roughly every ten minutes. That is a capacity signal, not an
# incident one — a connect that fails in sixty seconds is over long before a point arrives, which
# is what `ufo.db_tx_unavailable_total` is for.

# Datadog's own AWS account, the principal the role below trusts. Site-specific — this is us5's,
# the site `monitors.tf` pins its api_url to. A wrong value fails as sts:AssumeRole denied with no
# other symptom, so it is read off the org's AWS integration page, never assumed.
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

# Read-only and scoped to what the RDS namespace needs: the metric surface, the resource
# descriptions Datadog joins against it, and the tags it keys those metrics by. Datadog's published
# policy grants its whole product surface; nothing here collects logs, traces, or any other
# service, so the rest would be authority with no reader.
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

  # Only the namespace whose metrics the role can actually read. An unfiltered integration would
  # bill for every namespace in the account while the policy above refuses most of them.
  metrics_config {
    namespace_filters {
      include_only = ["AWS/RDS"]
    }
  }

  # The provider requires these blocks, so each names the collection it is turning off rather than
  # omitting it: no forwarder to send logs, no service to trace, and resource collection off, which
  # otherwise defaults on. The IAM policy above already refuses all three; this is the same refusal
  # said where a reader looks for it.
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
