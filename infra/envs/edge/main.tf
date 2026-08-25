data "cloudflare_zone" "flyingobject_ai" {
  filter = { name = "flyingobject.ai" }
}

data "cloudflare_zone" "ufo_ai" {
  filter = { name = "ufo.ai" }
}

locals {
  favicon_svg      = file("${path.root}/../../../assets/brand/ufo-mark.svg")
  favicon_dark_svg = file("${path.root}/../../../assets/brand/ufo-mark-on-dark.svg")
}

# flyingobject.ai is retired: every request in the zone answers 301 to the same subdomain and path
# under ufo.ai. The rule runs in the dynamic-redirect phase, ahead of the worker routes it made
# obsolete, and matches http and https alike.
resource "cloudflare_ruleset" "flyingobject_redirect" {
  zone_id = data.cloudflare_zone.flyingobject_ai.id
  name    = "flyingobject to ufo.ai"
  kind    = "zone"
  phase   = "http_request_dynamic_redirect"

  rules = [{
    ref         = "flyingobject_to_ufo_ai"
    description = "every flyingobject.ai host to its ufo.ai successor"
    expression  = "http.host wildcard r\"*flyingobject.ai\""
    action      = "redirect"
    action_parameters = {
      from_value = {
        status_code           = 301
        preserve_query_string = true
        target_url = {
          expression = "concat(\"https://\", wildcard_replace(http.host, r\"*flyingobject.ai\", r\"$${1}ufo.ai\"), http.request.uri.path)"
        }
      }
    }
  }]
}

resource "cloudflare_zone_setting" "always_use_https" {
  zone_id    = data.cloudflare_zone.flyingobject_ai.id
  setting_id = "always_use_https"
  value      = "off"

  depends_on = [cloudflare_ruleset.flyingobject_redirect]
}

resource "cloudflare_ruleset" "shipped_app_cache" {
  zone_id = data.cloudflare_zone.ufo_ai.id
  name    = "Cache shipped app documents"
  kind    = "zone"
  phase   = "http_request_cache_settings"

  rules = [{
    ref         = "cache_shipped_app_documents"
    description = "Cache content-addressed shipped app documents"
    expression  = "http.host wildcard r\"*.ufo.ai\" and http.request.uri.path eq \"/\" and http.request.uri.query wildcard r\"ufo-app=*\""
    action      = "set_cache_settings"
    action_parameters = {
      cache = true
    }
  }]
}

module "prod" {
  source = "../../modules/edge"

  name             = "ufo-edge"
  hostname         = "ufo.ai"
  zone_id          = data.cloudflare_zone.ufo_ai.id
  account_id       = data.cloudflare_zone.ufo_ai.account.id
  origin_base      = "https://origin.ufo.ai"
  favicon_svg      = local.favicon_svg
  favicon_dark_svg = local.favicon_dark_svg
}

module "testing" {
  source = "../../modules/edge"

  name             = "ufo-edge-testing"
  hostname         = "testing.ufo.ai"
  zone_id          = data.cloudflare_zone.ufo_ai.id
  account_id       = data.cloudflare_zone.ufo_ai.account.id
  origin_base      = "https://origin.testing.ufo.ai"
  favicon_svg      = local.favicon_svg
  favicon_dark_svg = local.favicon_dark_svg
}
