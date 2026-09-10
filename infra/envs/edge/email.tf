locals {
  ses_region       = "us-east-1"
  ses_domain       = "ufo.ai"
  ses_mail_from    = "bounce.ufo.ai"
  ses_contact_list = "ufo-users"
  ses_topic        = "founder-updates"
}

resource "aws_sesv2_email_identity" "ufo_ai" {
  email_identity = local.ses_domain

  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }
}

# REJECT_MESSAGE rather than USE_DEFAULT: a broken MX must stop the send, not fall back to
# amazonses.com, whose Return-Path no longer aligns with the visible From.
resource "aws_sesv2_email_identity_mail_from_attributes" "ufo_ai" {
  email_identity         = aws_sesv2_email_identity.ufo_ai.email_identity
  mail_from_domain       = local.ses_mail_from
  behavior_on_mx_failure = "REJECT_MESSAGE"
}

resource "cloudflare_dns_record" "ufo_ai_dkim" {
  count = 3

  zone_id = data.cloudflare_zone.ufo_ai.id
  name    = "${aws_sesv2_email_identity.ufo_ai.dkim_signing_attributes[0].tokens[count.index]}._domainkey.${local.ses_domain}"
  type    = "CNAME"
  content = "${aws_sesv2_email_identity.ufo_ai.dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"
  ttl     = 1
  proxied = false
}

# The bounce subdomain alone. The apex MX routes to Google and the apex SPF authorizes Google — both
# are outside this state, and both must stay that way for replies to founders@ufo.ai to arrive.
resource "cloudflare_dns_record" "ufo_ai_bounce_mx" {
  zone_id  = data.cloudflare_zone.ufo_ai.id
  name     = local.ses_mail_from
  type     = "MX"
  content  = "feedback-smtp.${local.ses_region}.amazonses.com"
  priority = 10
  ttl      = 1
  proxied  = false
}

resource "cloudflare_dns_record" "ufo_ai_bounce_spf" {
  zone_id = data.cloudflare_zone.ufo_ai.id
  name    = local.ses_mail_from
  type    = "TXT"
  content = "\"v=spf1 include:amazonses.com ~all\""
  ttl     = 1
  proxied = false
}

# SES allows one contact list per account, so this is the account's list and both environments
# send against it. SES refuses a send to a contact who unsubscribed from the topic.
resource "aws_sesv2_contact_list" "ufo_users" {
  contact_list_name = local.ses_contact_list
  description       = "Everyone who has an address on a ufo workspace."

  # Nothing writes a topic preference, so this default is the only value a contact ever holds:
  # OPT_IN reaches members until they unsubscribe, OPT_OUT would reach nobody, ever.
  topic {
    topic_name                  = local.ses_topic
    display_name                = "Founder updates"
    description                 = "Occasional notes from the people building ufo."
    default_subscription_status = "OPT_IN"
  }
}
