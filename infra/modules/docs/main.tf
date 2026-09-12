terraform {
  required_providers {
    cloudflare = { source = "cloudflare/cloudflare", version = "~> 5.12" }
  }
}

resource "cloudflare_workers_script" "docs" {
  account_id  = var.account_id
  script_name = var.name

  assets = {
    directory = var.dist
    config = {
      html_handling      = "auto-trailing-slash"
      not_found_handling = "404-page"
    }
  }
}

resource "cloudflare_workers_route" "docs" {
  for_each = var.routes

  zone_id = var.zone_id
  pattern = "${var.hostname}${each.value}"
  script  = cloudflare_workers_script.docs.script_name
}
