# The feature flags each environment serves, one resource per key the code reads.
#
# They live in this root because it is the one that holds Cloudflare state for both environments —
# an environment root declares no Cloudflare resource — and they are declared per environment rather
# than inside `module.testing`/`module.prod` so each deploy targets its own set and carries only the
# credential that set needs. A pull request plans neither.
#
# The set is pinned to what the active extensions declare at the `flags` Manifest point, by a gate,
# so a key the portal reads and this file omits fails CI rather than reading its default forever in
# a live deploy. The state beside each key is the answer a workspace no rule matches is served:
# testing offers everything, production withholds it until this file says otherwise.
#
# Terraform owns whether each flag exists and what it serves. It does not own targeting: a rollout
# somebody builds to reach one workspace is exactly what the flag service is for, and an apply that
# erased it would take away the thing the workspace targeting key exists for, with nothing here able
# to put it back.
locals {
  flagship_apps = {
    testing = "88d0356a-b440-48cf-ac58-d66f0b65f0c3"
    prod    = "c51fc738-0fd1-49b4-a195-3ef7d737005e"
  }

  portal_flags = {
    testing = {
      "enable-admin-settings"   = true
      "enable-assistant-app"    = true
      "enable-community-skills" = true
      "enable-installed-skills" = true
      "enable-issues-app"       = true
      "enable-memory-tab"       = true
      "enable-wiki-app"         = true
    }
    prod = {
      "enable-admin-settings"   = false
      "enable-assistant-app"    = false
      "enable-community-skills" = false
      "enable-installed-skills" = false
      "enable-issues-app"       = false
      "enable-memory-tab"       = false
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
    ignore_changes = [rules]
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
    ignore_changes = [rules]
  }
}
