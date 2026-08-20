# The WorkOS DNS records, all DNS-only (proxied = false, ttl auto) so Cloudflare answers them
# straight through: WorkOS verifies each record itself, and a proxied record never verifies.
#
# The AuthKit domain auth.ufo.ai and the Google OAuth domain oauth.ufo.ai answer the same WorkOS
# target. The email sending domain mail.flyingobject.ai carries its own SendGrid CNAMEs — one mail
# CNAME and two DKIM keys. WorkOS issues fresh targets for each sending domain it verifies, so
# mail.ufo.ai means new values read out of WorkOS, never these renamed.
#
# Each record already exists live — created through the Cloudflare API to bring the domain up — so
# they are imported into edge state once, never recreated:
#
#   terraform import 'cloudflare_dns_record.workos["sendgrid"]'  7d788212bd48a93f3d7df3bfd9b599e1/98a1065aa3740f04f2cb218cd0cc4e32
#   terraform import 'cloudflare_dns_record.workos["dkim_wos"]'  7d788212bd48a93f3d7df3bfd9b599e1/75b93b7a52ac22b5f8254914c9e92447
#   terraform import 'cloudflare_dns_record.workos["dkim_wos2"]' 7d788212bd48a93f3d7df3bfd9b599e1/a9943b080a94733fc4e6dc1dea652082
#
# The two ufo.ai record ids come from the zone listing at import time:
#
#   curl -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
#     "https://api.cloudflare.com/client/v4/zones?name=ufo.ai"
#   curl -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
#     "https://api.cloudflare.com/client/v4/zones/<zone>/dns_records?name=auth.ufo.ai"

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
  for_each = toset(["auth.ufo.ai", "oauth.ufo.ai"])

  zone_id = data.cloudflare_zone.ufo_ai.id
  name    = each.value
  type    = "CNAME"
  content = "cname.workos-dns.com"
  ttl     = 1
  proxied = false
}
