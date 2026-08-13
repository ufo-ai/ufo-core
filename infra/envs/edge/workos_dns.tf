# The WorkOS DNS records in the flyingobject.ai zone, all DNS-only (proxied = false, ttl auto) so
# Cloudflare answers them straight through: three SendGrid CNAMEs that verify the WorkOS email
# sending domain mail.flyingobject.ai (one mail CNAME, two DKIM keys), and the AuthKit custom
# domain auth.flyingobject.ai. Each already exists live — created through the Cloudflare API to
# bring the domains up — so they are imported into edge state once, never recreated:
#
#   terraform import 'cloudflare_dns_record.workos["sendgrid"]'  7d788212bd48a93f3d7df3bfd9b599e1/98a1065aa3740f04f2cb218cd0cc4e32
#   terraform import 'cloudflare_dns_record.workos["dkim_wos"]'  7d788212bd48a93f3d7df3bfd9b599e1/75b93b7a52ac22b5f8254914c9e92447
#   terraform import 'cloudflare_dns_record.workos["dkim_wos2"]' 7d788212bd48a93f3d7df3bfd9b599e1/a9943b080a94733fc4e6dc1dea652082
#   terraform import 'cloudflare_dns_record.workos["authkit"]'   7d788212bd48a93f3d7df3bfd9b599e1/273b627b6e91d98b1c6873f23333ab66

resource "cloudflare_dns_record" "workos" {
  for_each = {
    sendgrid  = { name = "em8126.mail.flyingobject.ai", content = "u36670648.wl149.sendgrid.net" }
    dkim_wos  = { name = "wos._domainkey.mail.flyingobject.ai", content = "wos.domainkey.u36670648.wl149.sendgrid.net" }
    dkim_wos2 = { name = "wos2._domainkey.mail.flyingobject.ai", content = "wos2.domainkey.u36670648.wl149.sendgrid.net" }
    authkit   = { name = "auth.flyingobject.ai", content = "cname.workos-dns.com" }
  }

  zone_id = data.cloudflare_zone.flyingobject_ai.id
  name    = each.value.name
  type    = "CNAME"
  content = each.value.content
  ttl     = 1
  proxied = false
}
