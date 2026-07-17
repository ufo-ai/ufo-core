# The apex edge worker: `GET /` answers CLI user-agents with the text landing card — over plain
# http too, so `curl <hostname>` works verbatim — and serves browsers the embedded landing page
# (`landing.html`, substituted into the script below), stamped with the craft count from this
# front door's own gateway (`origin_base`'s `/fleet`); plain-http browsers are bounced to https
# first. `POST /waitlist`
# records emails in D1 (the card's counter reads them back), and `GET /ufo` proxies the
# gateway's version-stamped client script. The route claims the whole host — Cloudflare matches patterns against the URL
# including its query, so exact-path routes never fire for query'd URLs (`/?utm=…`). Paths the
# worker doesn't handle pass through to origin via its default `fetch(request)`. The zone-level
# https redirect that exempts the card path lives in envs/edge.

terraform {
  required_providers {
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 5.12" }
  }
}

locals {
  waitlist_sender = "no-reply@flyingobject.ai"
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
      file("${path.module}/worker.js"),
      "\"__LANDING_HTML__\"",
      jsonencode(file("${path.module}/landing.html")),
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
