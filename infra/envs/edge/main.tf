data "cloudflare_zone" "flyingobject_ai" {
  filter = { name = "flyingobject.ai" }
}

# `curl flyingobject.ai` must answer with the card, not a 301: the zone-wide Always Use HTTPS
# redirect fires before worker routes, so it moves here as a redirect rule that exempts the two
# apexes' `/` (the worker serves CLI clients over plain http and bounces browsers to https
# itself). Every other host and path in the zone — gateway and app ingress — keeps the
# https redirect. The rule is in place before the zone setting turns off, so no request ever
# sees a gap.
resource "cloudflare_ruleset" "https_redirect" {
  zone_id = data.cloudflare_zone.flyingobject_ai.id
  name    = "https redirect"
  kind    = "zone"
  phase   = "http_request_dynamic_redirect"

  rules = [{
    ref         = "https_redirect_except_apex_card"
    description = "http to https everywhere except the apexes' CLI landing card"
    expression  = "not ssl and not (http.host in {\"flyingobject.ai\" \"testing.flyingobject.ai\"} and http.request.uri.path eq \"/\")"
    action      = "redirect"
    action_parameters = {
      from_value = {
        status_code           = 301
        preserve_query_string = true
        target_url = {
          expression = "concat(\"https://\", http.host, http.request.uri.path)"
        }
      }
    }
  }]
}

resource "cloudflare_zone_setting" "always_use_https" {
  zone_id    = data.cloudflare_zone.flyingobject_ai.id
  setting_id = "always_use_https"
  value      = "off"

  depends_on = [cloudflare_ruleset.https_redirect]
}

# One live fleet (testing) behind two front doors: both apexes proxy /ufo from the testing
# gateway, whose stamped script boards clients onto that fleet, and stamp the landing page with
# its craft count. The apex has no gateway origin of its own — pointing origin_base at
# flyingobject.ai makes the worker fetch its own originless zone (404). The doors' skies diverge
# when prod gets its own fleet (repoint origin_base).
module "prod" {
  source = "../../modules/edge"

  name        = "ufo-edge"
  hostname    = "flyingobject.ai"
  zone_id     = data.cloudflare_zone.flyingobject_ai.id
  account_id  = data.cloudflare_zone.flyingobject_ai.account.id
  origin_base = "https://testing.flyingobject.ai"
}

module "testing" {
  source = "../../modules/edge"

  name        = "ufo-edge-testing"
  hostname    = "testing.flyingobject.ai"
  zone_id     = data.cloudflare_zone.flyingobject_ai.id
  account_id  = data.cloudflare_zone.flyingobject_ai.account.id
  origin_base = "https://testing.flyingobject.ai"
}
