
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

locals {
  # Campaigns send from the ufo.ai identity infra/envs/edge owns, which is a different domain from
  # the one the transactional sender still uses. Retiring that one is a later change: SES verifies a
  # domain per account, so destroying it stops every environment that has not yet rolled.
  founder_domain = "ufo.ai"
  founder_senders = [
    "ufo founders <founders@ufo.ai>",
    "Marshall at ufo <marshall@ufo.ai>",
    "Alex at ufo <alex@ufo.ai>",
    "ufo support <support@ufo.ai>",
  ]
  founder_sender_addresses = [
    for sender in local.founder_senders : trimsuffix(element(split("<", sender), 1), ">")
  ]
  founder_contact_list = "ufo-users"
  founder_topic        = "founder-updates"
  # `name` is "prod" in one environment and "ufo-testing" in the other, so the prefix is trimmed
  # before it is applied and neither reads `ufo-ufo-testing`.
  founder_environment       = trimprefix(local.name, "ufo-")
  founder_configuration_set = "ufo-${local.founder_environment}-founder-email"
  founder_queue_name        = "ufo-${local.founder_environment}-founder-email-feedback"
  # Derived, never read off the queue: the hosted manifest keys come from the rendered template, and
  # one value unknown until apply makes every key unknown and fails the plan.
  founder_queue_url = "https://sqs.${var.region}.amazonaws.com/${data.aws_caller_identity.current.account_id}/${local.founder_queue_name}"
  ses_arn_prefix    = "arn:aws:ses:${var.region}:${data.aws_caller_identity.current.account_id}"
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

resource "aws_sesv2_configuration_set" "founder_email" {
  configuration_set_name = local.founder_configuration_set

  delivery_options {
    tls_policy = "REQUIRE"
  }

  reputation_options {
    reputation_metrics_enabled = true
  }

  sending_options {
    sending_enabled = true
  }
}

resource "aws_sns_topic" "founder_email" {
  name = local.founder_configuration_set
  tags = local.tags
}

data "aws_iam_policy_document" "founder_email_topic" {
  statement {
    sid       = "PublishSesEvents"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.founder_email.arn]

    principals {
      type        = "Service"
      identifiers = ["ses.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = ["${local.ses_arn_prefix}:configuration-set/${local.founder_configuration_set}"]
    }
  }
}

resource "aws_sns_topic_policy" "founder_email" {
  arn    = aws_sns_topic.founder_email.arn
  policy = data.aws_iam_policy_document.founder_email_topic.json
}

# A message the consumer cannot decode after five receives is a bug in our parsing, not a transient
# fault, so it parks here for an operator to read rather than cycling forever.
resource "aws_sqs_queue" "founder_feedback_dead" {
  name                      = "${local.founder_queue_name}-dead"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = local.tags
}

resource "aws_sqs_queue" "founder_feedback" {
  name                       = local.founder_queue_name
  message_retention_seconds  = 1209600
  receive_wait_time_seconds  = 20
  visibility_timeout_seconds = 60
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.founder_feedback_dead.arn
    maxReceiveCount     = 5
  })

  tags = local.tags
}

data "aws_iam_policy_document" "founder_feedback_queue" {
  statement {
    sid       = "DeliverTopicNotifications"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.founder_feedback.arn]

    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_sns_topic.founder_email.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "founder_feedback" {
  queue_url = aws_sqs_queue.founder_feedback.id
  policy    = data.aws_iam_policy_document.founder_feedback_queue.json
}

# Raw delivery, so the queue body is the SES event itself rather than an SNS envelope carrying it.
resource "aws_sns_topic_subscription" "founder_feedback" {
  topic_arn            = aws_sns_topic.founder_email.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.founder_feedback.arn
  raw_message_delivery = true
}

# Open and click are absent: SES rewrites every link in a message to track them, which costs
# deliverability and a redirect domain, and no decision here turns on who clicked.
resource "aws_sesv2_configuration_set_event_destination" "founder_email" {
  configuration_set_name = aws_sesv2_configuration_set.founder_email.configuration_set_name
  event_destination_name = "feedback"

  event_destination {
    enabled = true

    matching_event_types = [
      "SEND",
      "REJECT",
      "BOUNCE",
      "COMPLAINT",
      "DELIVERY",
      "DELIVERY_DELAY",
      "RENDERING_FAILURE",
      "SUBSCRIPTION",
    ]

    sns_destination {
      topic_arn = aws_sns_topic.founder_email.arn
    }
  }

  depends_on = [aws_sns_topic_policy.founder_email]
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

  statement {
    sid     = "SendFounderCampaign"
    actions = ["ses:SendEmail"]
    resources = [
      "${local.ses_arn_prefix}:identity/${local.founder_domain}",
      aws_sesv2_configuration_set.founder_email.arn,
    ]
    condition {
      test     = "StringEquals"
      variable = "ses:FromAddress"
      values   = local.founder_sender_addresses
    }
  }

  # Read alone. SES records an unsubscribe itself and account-level suppression already holds every
  # bounce and complaint, so nothing here writes a contact.
  statement {
    sid       = "ReadTheContactList"
    actions   = ["ses:ListContacts"]
    resources = ["${local.ses_arn_prefix}:contact-list/${local.founder_contact_list}"]
  }

  statement {
    sid       = "ConsumeSendFeedback"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.founder_feedback.arn]
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
