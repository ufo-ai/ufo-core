# The public worker serves one apex from its environment's configured origin. Waitlist state stays
# in that door's D1 database and confirmation queue. The route claims the whole host because
# Cloudflare matches patterns against the URL including its query, so exact-path patterns miss it.

terraform {
  required_providers {
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 5.12" }
  }
}

locals {
  waitlist_sender     = "no-reply@flyingobject.ai"
  landing_html        = file("${path.module}/landing.html")
  legal_shell         = file("${path.module}/legal.html")
  privacy_description = "What ufo.ai collects when you sign in and use the service, how that information is used, and how long it is kept."
  terms_description   = "The terms that govern your use of ufo.ai: accounts, acceptable use, intellectual property, and liability."
  privacy_html = replace(
    replace(
      replace(
        replace(local.legal_shell, "__TITLE__", "Privacy Policy"),
        "__DESCRIPTION__", local.privacy_description,
      ),
      "__CANONICAL__", "https://ufo.ai/privacy",
    ),
    "__BODY__", file("${path.module}/privacy.html"),
  )
  terms_html = replace(
    replace(
      replace(
        replace(local.legal_shell, "__TITLE__", "Terms of Service"),
        "__DESCRIPTION__", local.terms_description,
      ),
      "__CANONICAL__", "https://ufo.ai/terms",
    ),
    "__BODY__", file("${path.module}/terms.html"),
  )
}

resource "cloudflare_d1_database" "waitlist" {
  account_id = var.account_id
  name       = "${var.name}-waitlist"

  # Unset, provider 5.x PUTs `read_replication: null` on every update and the API refuses it
  # ("Expected object, received null"). Disabled is D1's actual default.
  read_replication = { mode = "disabled" }
}

resource "cloudflare_queue" "waitlist_email" {
  account_id = var.account_id
  queue_name = "${var.name}-waitlist-email"
}

resource "cloudflare_queue" "waitlist_email_dead_letters" {
  account_id = var.account_id
  queue_name = "${var.name}-waitlist-email-dead-letters"
}

resource "cloudflare_workers_script" "edge" {
  account_id  = var.account_id
  script_name = var.name
  content = replace(
    replace(
      replace(
        replace(
          replace(
            replace(
              file("${path.module}/worker.js"),
              "\"__LANDING_HTML__\"",
              jsonencode(local.landing_html),
            ),
            "\"__FAVICON_SVG__\"",
            jsonencode(var.favicon_svg),
          ),
          "\"__FAVICON_DARK_SVG__\"",
          jsonencode(var.favicon_dark_svg),
        ),
        "\"__PRIVACY_HTML__\"",
        jsonencode(local.privacy_html),
      ),
      "\"__TERMS_HTML__\"",
      jsonencode(local.terms_html),
    ),
    "\"__WAITLIST_SENDER__\"",
    jsonencode(local.waitlist_sender),
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
      name       = "WAITLIST_EMAILS"
      type       = "queue"
      queue_name = cloudflare_queue.waitlist_email.queue_name
    },
    {
      name                     = "EMAIL"
      type                     = "send_email"
      allowed_sender_addresses = [local.waitlist_sender]
    },
    {
      name = "WAITLIST_DEAD_LETTER_QUEUE"
      type = "plain_text"
      text = cloudflare_queue.waitlist_email_dead_letters.queue_name
    },
  ]
}

resource "cloudflare_queue_consumer" "waitlist_email" {
  account_id        = var.account_id
  queue_id          = cloudflare_queue.waitlist_email.queue_id
  script_name       = cloudflare_workers_script.edge.script_name
  type              = "worker"
  dead_letter_queue = cloudflare_queue.waitlist_email_dead_letters.queue_name
  settings = {
    batch_size      = 1
    max_concurrency = 1
    max_retries     = 5
    retry_delay     = 60
  }
}

resource "cloudflare_queue_consumer" "waitlist_email_dead_letters" {
  account_id  = var.account_id
  queue_id    = cloudflare_queue.waitlist_email_dead_letters.queue_id
  script_name = cloudflare_workers_script.edge.script_name
  type        = "worker"
  settings = {
    batch_size      = 1
    max_concurrency = 1
  }
}

resource "cloudflare_workers_route" "edge" {
  zone_id = var.zone_id
  pattern = "${var.hostname}/*"
  script  = cloudflare_workers_script.edge.script_name
}

# A public site's share card is the one anonymous address on the app host, and one pasted link is
# fetched by every unfurler that reads it. This route hands that path to the worker, which answers it
# off one stored copy so the app host reads the row and streams the bytes once per window rather than
# once per request. Only the card path is claimed: everything else on that host is a member's own
# authenticated traffic and keeps going straight to the origin. The pattern is prefixed rather than
# exact because Cloudflare matches it against the URL including its query.
resource "cloudflare_workers_route" "site_cards" {
  zone_id = var.zone_id
  pattern = "app.${var.hostname}/surface/sites/share/site/*"
  script  = cloudflare_workers_script.edge.script_name
}

# An artifact URL's query is its whole grant, so the worker stores a served response under the exact
# signed URL and answers repeats of it from the edge — previews the portal draws on every poll stop
# re-reading S3 per request. Only 200s the origin marked public are stored; refusals and the
# member-refresh redirect say no-store and pass straight through.
resource "cloudflare_workers_route" "artifact_bytes" {
  zone_id = var.zone_id
  pattern = "app.${var.hostname}/artifacts/*"
  script  = cloudflare_workers_script.edge.script_name
}
