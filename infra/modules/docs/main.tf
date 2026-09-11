terraform {
  required_providers {
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 5.12" }
  }
}

# The built site is the whole worker: no code runs and the assets answer. `make docs` writes
# `site/dist`, which a plan reads, so the deploy runs that build ahead of terraform.
resource "cloudflare_workers_script" "docs" {
  account_id  = var.account_id
  script_name = var.name

  assets = {
    directory = "${path.module}/site/dist"
    config = {
      html_handling      = "auto-trailing-slash"
      not_found_handling = "404-page"
    }
  }
}

# A custom domain writes the host's own DNS record, which takes the request ahead of the zone's `*`
# wildcard — the record every hosted member site answers under.
resource "cloudflare_workers_custom_domain" "docs" {
  account_id = var.account_id
  zone_id    = var.zone_id
  hostname   = var.hostname
  service    = cloudflare_workers_script.docs.script_name
}
