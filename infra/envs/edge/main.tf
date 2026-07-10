data "cloudflare_zone" "flyingobject_ai" {
  filter = { name = "flyingobject.ai" }
}

# One live fleet (testing) behind two front doors: both apexes proxy /install from the testing
# gateway, whose stamped script boards clients onto that fleet, and land browsers on the one site
# at the prod apex.
module "prod" {
  source = "../../modules/edge"

  name        = "ufo-edge"
  hostname    = "flyingobject.ai"
  zone_id     = data.cloudflare_zone.flyingobject_ai.id
  account_id  = data.cloudflare_zone.flyingobject_ai.account.id
  origin_base = "https://testing.flyingobject.ai"
  site_base   = "https://flyingobject.ai"
}

module "testing" {
  source = "../../modules/edge"

  name        = "ufo-edge-testing"
  hostname    = "testing.flyingobject.ai"
  zone_id     = data.cloudflare_zone.flyingobject_ai.id
  account_id  = data.cloudflare_zone.flyingobject_ai.account.id
  origin_base = "https://testing.flyingobject.ai"
  site_base   = "https://flyingobject.ai"
}
