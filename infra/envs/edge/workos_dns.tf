# All DNS-only: WorkOS verifies each record itself, and a proxied record never verifies. Every record
# here already exists live, so each is imported into state once and never recreated.

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
