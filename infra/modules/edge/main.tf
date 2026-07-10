# The apex edge worker: `GET /` answers CLI user-agents with the text landing card — over plain
# http too, so `curl <hostname>` works verbatim — and serves browsers the embedded landing page
# (`landing.html`, substituted into the script below) on the `site_base` host, stamped with the
# craft count from the gateway's `/fleet`; browsers on any other host are redirected there, and on
# plain http bounced to https first. `POST /waitlist`
# records emails in D1 (the card's counter reads them back), and `GET /install(.sh)` proxies the
# gateway's version-stamped client script. The route claims the whole host — Cloudflare matches patterns against the URL
# including its query, so exact-path routes never fire for query'd URLs (`/?utm=…`). Paths the
# worker doesn't handle pass through to origin via its default `fetch(request)`. The zone-level
# https redirect that exempts the card path lives in envs/edge.

terraform {
  required_providers {
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 5.7" }
  }
}

resource "cloudflare_d1_database" "waitlist" {
  account_id = var.account_id
  name       = "${var.name}-waitlist"

  # Unset, provider 5.x PUTs `read_replication: null` on every update and the API refuses it
  # ("Expected object, received null"). Disabled is D1's actual default.
  read_replication = { mode = "disabled" }
}

resource "cloudflare_workers_script" "edge" {
  account_id  = var.account_id
  script_name = var.name
  content = replace(
    file("${path.module}/worker.js"),
    "\"__LANDING_HTML__\"",
    jsonencode(file("${path.module}/landing.html")),
  )
  main_module = "worker.js"

  bindings = [
    {
      name = "DB"
      type = "d1"
      id   = cloudflare_d1_database.waitlist.id
    },
    {
      name = "ORIGIN_BASE"
      type = "plain_text"
      text = var.origin_base
    },
    {
      name = "SITE_BASE"
      type = "plain_text"
      text = var.site_base
    },
  ]
}

resource "cloudflare_workers_route" "edge" {
  zone_id = var.zone_id
  pattern = "${var.hostname}/*"
  script  = cloudflare_workers_script.edge.script_name
}
