
variable "ses_sender" {
  type        = string
  description = "From address for onboarding email. Its domain is the verified SES identity."

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.ses_sender))
    error_message = "ses_sender must be an email address, e.g. no-reply@flyingobject.ai."
  }
}

locals {
  gateway_ses_role_name = "${local.name}-gateway-ses"
  ses_domain            = element(split("@", var.ses_sender), 1)
}

resource "aws_sesv2_email_identity" "onboard" {
  count = var.owns_account_resources ? 1 : 0

  email_identity = local.ses_domain

  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }
  tags = local.tags
}

moved {
  from = aws_sesv2_email_identity.onboard
  to   = aws_sesv2_email_identity.onboard[0]
}

data "aws_iam_policy_document" "gateway_ses" {
  statement {
    sid       = "SendOnboardingEmail"
    actions   = ["ses:SendEmail"]
    resources = ["arn:aws:ses:${var.region}:${data.aws_caller_identity.current.account_id}:identity/${local.ses_domain}"]
    condition {
      test     = "StringEquals"
      variable = "ses:FromAddress"
      values   = [var.ses_sender]
    }
  }
}

resource "aws_iam_policy" "gateway_ses" {
  name   = "${local.name}-gateway-ses"
  policy = data.aws_iam_policy_document.gateway_ses.json
  tags   = local.tags
}

moved {
  from = module.irsa_gateway_ses[0]
  to   = module.irsa_gateway_ses
}

module "irsa_gateway_ses" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.48"

  role_name        = local.gateway_ses_role_name
  role_policy_arns = { ses = aws_iam_policy.gateway_ses.arn }

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["${local.system_namespace}:ufo-gateway"]
    }
  }
  tags = local.tags
}

output "ses_dkim_records" {
  description = "DKIM CNAMEs for the sending domain."
  value = var.owns_account_resources ? [
    for token in aws_sesv2_email_identity.onboard[0].dkim_signing_attributes[0].tokens : {
      name  = "${token}._domainkey.${local.ses_domain}"
      type  = "CNAME"
      value = "${token}.dkim.amazonses.com"
    }
  ] : []
}
