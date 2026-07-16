# DKIM CNAMEs verifying the SES sending domain in the Cloudflare zone. Easy DKIM issues exactly
# three; count (not for_each) keeps the plan keys known on a fresh apply, where the identity's
# tokens are apply-time values.

data "cloudflare_zone" "flyingobject_ai" {
  filter = { name = "flyingobject.ai" }
}

resource "cloudflare_dns_record" "ses_dkim" {
  count   = 3
  zone_id = data.cloudflare_zone.flyingobject_ai.id
  name    = module.platform.ses_dkim_records[count.index].name
  type    = module.platform.ses_dkim_records[count.index].type
  content = module.platform.ses_dkim_records[count.index].value
  ttl     = 1
  proxied = false
}
