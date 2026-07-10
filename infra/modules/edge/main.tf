# The apex edge worker: `GET /` answers CLI user-agents with the text landing card (browsers pass
# through to whatever the host serves), `POST /waitlist` records emails in D1 (the card's counter
# reads them back), and `GET /install(.sh)` proxies the gateway's version-stamped client script.
# Routes claim only these paths — every other request on the host never touches the worker.

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
  content     = file("${path.module}/worker.js")
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
  ]
}

resource "cloudflare_workers_route" "edge" {
  for_each = toset([
    "${var.hostname}/",
    "${var.hostname}/waitlist",
    "${var.hostname}/install",
    "${var.hostname}/install.sh",
  ])

  zone_id = var.zone_id
  pattern = each.value
  script  = cloudflare_workers_script.edge.script_name
}
