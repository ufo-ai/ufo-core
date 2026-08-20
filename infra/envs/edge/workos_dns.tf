# The WorkOS DNS records, all DNS-only (proxied = false, ttl auto) so Cloudflare answers them
# straight through: WorkOS verifies each record itself, and a proxied record never verifies.
#
# In the ufo.ai zone: the AuthKit domain auth.ufo.ai, the Google OAuth domain oauth.ufo.ai, and the
# three SendGrid records WorkOS issues for the sending domain mail.ufo.ai — one mail CNAME and two
# DKIM keys. The em host belongs to the domain WorkOS verified it for, so it changes with the domain
# while the DKIM targets and the SendGrid user do not.
#
# In the flyingobject.ai zone: the sending domain WorkOS still has verified. It retires once
# mail.ufo.ai verifies, and its records are deleted in that same change.
#
# Every record here already exists live — created through the Cloudflare API to bring the domain up —
# so each is imported into state once, never recreated. `terraform state list` says which addresses
# state already holds; import the rest. Zone and record ids come from the Cloudflare API:
#
#   curl -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
#     "https://api.cloudflare.com/client/v4/zones?name=ufo.ai"
#   curl -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
#     "https://api.cloudflare.com/client/v4/zones/<zone>/dns_records?name=em9982.mail.ufo.ai"
#
#   terraform import 'cloudflare_dns_record.workos_ufo["em9982.mail.ufo.ai"]' <zone>/<record>

resource "cloudflare_dns_record" "workos" {
  for_each = {
    sendgrid  = { name = "em8126.mail.flyingobject.ai", content = "u36670648.wl149.sendgrid.net" }
    dkim_wos  = { name = "wos._domainkey.mail.flyingobject.ai", content = "wos.domainkey.u36670648.wl149.sendgrid.net" }
    dkim_wos2 = { name = "wos2._domainkey.mail.flyingobject.ai", content = "wos2.domainkey.u36670648.wl149.sendgrid.net" }
  }

  zone_id = data.cloudflare_zone.flyingobject_ai.id
  name    = each.value.name
  type    = "CNAME"
  content = each.value.content
  ttl     = 1
  proxied = false
}

resource "cloudflare_dns_record" "workos_ufo" {
  for_each = {
    "auth.ufo.ai"                 = "cname.workos-dns.com"
    "oauth.ufo.ai"                = "cname.workos-dns.com"
    "em9982.mail.ufo.ai"          = "u36670648.wl149.sendgrid.net"
    "wos._domainkey.mail.ufo.ai"  = "wos.domainkey.u36670648.wl149.sendgrid.net"
    "wos2._domainkey.mail.ufo.ai" = "wos2.domainkey.u36670648.wl149.sendgrid.net"
  }

  zone_id = data.cloudflare_zone.ufo_ai.id
  name    = each.key
  type    = "CNAME"
  content = each.value
  ttl     = 1
  proxied = false
}
