# Transactional email for onboarding verification codes (the gateway extension's claim machine,
# RFC 0011 §4). This verifies the sending domain (Easy DKIM) and grants ses:SendEmail for that
# identity, scoped to the From address. ses_sender = "" leaves SES uncreated. The grant lands on a
# dedicated IRSA role annotated on the gateway's ServiceAccount (ufo-system:ufo-operator) — the pod
# exchanges its projected web identity at STS; the FromAddress condition pins it to ses_sender.
#
# After apply: add the ses_dkim_records CNAMEs to the authoritative DNS (Cloudflare) to verify the
# domain, and request SES production access to send beyond the verified set (a new account is
# sandboxed to verified recipients only).

variable "ses_sender" {
  type        = string
  default     = ""
  description = "From address for onboarding email (e.g. no-reply@flyingobject.ai). Empty leaves SES uncreated. Its domain is verified as the SES sending identity — use the brand apex, not the per-env hostname, so user-facing mail isn't from a 'testing.' subdomain."

  validation {
    condition     = var.ses_sender == "" || can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.ses_sender))
    error_message = "ses_sender must be an email address, e.g. no-reply@flyingobject.ai."
  }
}

locals {
  ses_enabled = var.ses_sender != ""
  # The verified identity is the sender's domain. It is SES-account-global, so a second env in the
  # same AWS account must reference this identity (data source), not recreate it.
  ses_domain = local.ses_enabled ? element(split("@", var.ses_sender), 1) : ""
}

resource "aws_sesv2_email_identity" "onboard" {
  count          = local.ses_enabled ? 1 : 0
  email_identity = local.ses_domain

  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }
  tags = local.tags
}

data "aws_iam_policy_document" "gateway_ses" {
  count = local.ses_enabled ? 1 : 0
  statement {
    sid       = "SendOnboardingEmail"
    actions   = ["ses:SendEmail"]
    resources = [aws_sesv2_email_identity.onboard[0].arn]
    condition {
      test     = "StringEquals"
      variable = "ses:FromAddress"
      values   = [var.ses_sender]
    }
  }
}

resource "aws_iam_policy" "gateway_ses" {
  count  = local.ses_enabled ? 1 : 0
  name   = "${local.name}-gateway-ses"
  policy = data.aws_iam_policy_document.gateway_ses[0].json
  tags   = local.tags
}

module "irsa_gateway_ses" {
  count   = local.ses_enabled ? 1 : 0
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.48"

  role_name        = "${local.name}-gateway-ses"
  role_policy_arns = { ses = aws_iam_policy.gateway_ses[0].arn }

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["${local.system_namespace}:ufo-operator"]
    }
  }
  tags = local.tags
}

output "ses_dkim_records" {
  description = "DKIM CNAMEs to add to DNS (Cloudflare) to verify the SES sending domain."
  value = local.ses_enabled ? [
    for token in aws_sesv2_email_identity.onboard[0].dkim_signing_attributes[0].tokens : {
      name  = "${token}._domainkey.${local.ses_domain}"
      type  = "CNAME"
      value = "${token}.dkim.amazonses.com"
    }
  ] : []
}
