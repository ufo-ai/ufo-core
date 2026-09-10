# Pinned to what the active extensions declare at the `flags` Manifest point, by a gate, so a key
# the portal reads and this file omits fails CI. Terraform owns that a flag exists, never what it serves.
locals {
  flagship_apps = {
    testing = "88d0356a-b440-48cf-ac58-d66f0b65f0c3"
    prod    = "c51fc738-0fd1-49b4-a195-3ef7d737005e"
  }

  portal_flags = {
    testing = {
      "enable-app-store"        = true
      "enable-apps-tab"         = false
      "enable-assistant-app"    = true
      "enable-code-app"         = false
      "enable-community-skills" = true
      "enable-context-rollover" = true
      "enable-installed-skills" = true
      "enable-issues-app"       = false
      "enable-lanes-shell"      = false
      "enable-meetings-app"     = false
      "enable-memory-tab"       = true
      "enable-metrics-app"      = false
      "enable-notification-app" = false
      "enable-radar-app"        = false
      "enable-wiki-app"         = false
    }
    prod = {
      "enable-app-store"        = false
      "enable-apps-tab"         = false
      "enable-assistant-app"    = false
      "enable-code-app"         = false
      "enable-community-skills" = false
      "enable-context-rollover" = false
      "enable-installed-skills" = false
      "enable-issues-app"       = false
      "enable-lanes-shell"      = false
      "enable-meetings-app"     = false
      "enable-memory-tab"       = false
      "enable-metrics-app"      = false
      "enable-notification-app" = false
      "enable-radar-app"        = false
      "enable-wiki-app"         = false
    }
  }
}

resource "cloudflare_flagship_flag" "testing_portal" {
  for_each = local.portal_flags.testing
  provider = cloudflare.flagship_testing

  account_id        = data.cloudflare_zone.ufo_ai.account.id
  app_id            = local.flagship_apps.testing
  flag_key          = each.key
  key               = each.key
  type              = "boolean"
  enabled           = true
  variations        = { on = "true", off = "false" }
  default_variation = each.value ? "on" : "off"
  rules             = []

  lifecycle {
    ignore_changes = [default_variation, rules]
  }
}

resource "cloudflare_flagship_flag" "prod_portal" {
  for_each = local.portal_flags.prod
  provider = cloudflare.flagship_prod

  account_id        = data.cloudflare_zone.ufo_ai.account.id
  app_id            = local.flagship_apps.prod
  flag_key          = each.key
  key               = each.key
  type              = "boolean"
  enabled           = true
  variations        = { on = "true", off = "false" }
  default_variation = each.value ? "on" : "off"
  rules             = []

  lifecycle {
    ignore_changes = [default_variation, rules]
  }
}
