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

# The zone's cache-settings phase for the portal hosts, held empty: the portal rides Cloudflare's
# default caching, extension-matched edge entries and the zone browser TTL included, so a portal
# asset can sit in a browser for that TTL after a deploy. The ruleset resource stands even with no
# rules because the deploy pipeline applies this stack by target, and a rule can only be added or
# removed through an address the pipeline already names.
resource "cloudflare_ruleset" "portal_origin_cache" {
  zone_id = data.cloudflare_zone.ufo_ai.id
  name    = "portal host cache settings"
  kind    = "zone"
  phase   = "http_request_cache_settings"
}

# Browsers cache what the origin says, not a zone-stamped TTL. The portal's assets ship `no-cache`
# plus a content ETag so a deploy is visible on the next revalidation; a zone TTL rewriting that to
# hours is how a rolled-out fix keeps failing in every browser that holds the old copy. The edge
# keeps caching under HTTP's own rules — `no-cache` stores and revalidates per request, so edge
# entries refresh on the first request after a roll and the session gate still answers every
# revalidation.
resource "cloudflare_zone_setting" "ufo_browser_cache_ttl" {
  zone_id    = data.cloudflare_zone.ufo_ai.id
  setting_id = "browser_cache_ttl"
  value      = 0
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
