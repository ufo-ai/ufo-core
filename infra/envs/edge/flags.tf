# The feature flags each environment serves, one resource per key the code reads.
#
# They live in this root because it is the one that holds Cloudflare state for both environments —
# an environment root declares no Cloudflare resource — and they are declared per environment rather
# than inside `module.testing`/`module.prod` so each deploy targets its own set and carries only the
# credential that set needs. A pull request plans neither.
#
# The set is pinned to what the active extensions declare at the `flags` Manifest point, by a gate,
# so a key the portal reads and this file omits fails CI rather than reading its default forever in
# a live deploy. Testing creates a flag over one of the portal's own screens on, because that is
# where a screen is seen first; production creates each one off, so nothing reaches a member there
# before somebody turns it on. A flag over a shipped app is created off in both, because it is what
# offers that app rather than what withholds it — the apps a member is offered today are the ones
# somebody turned on, and every other one is drawn in no list while the workspace still holds it and
# its standing work keeps running.
#
# Terraform owns that each flag exists and the shape it has — its type, its two variations, and
# `enabled`. It does not own what the flag serves. With no rule matching, the served value is
# `default_variation`, so owning that here would mean every flip waited on a deploy; it is created
# once and the flag service holds it from then on. Targeting is the same: a rollout somebody builds
# to reach one workspace is what the flag service is for, and an apply that erased it would take
# away the thing the workspace targeting key exists for.
#
# So `ufoctl flags set <key> --on/--off`, or a toggle in the dashboard, changes what a member sees
# with no deploy, and this file still guarantees the flag is there to toggle. What it does not
# answer is what an environment serves today: a value below is what its flag is created at, so
# editing one moves a flag that does not exist yet and nothing else.
locals {
  flagship_apps = {
    testing = "88d0356a-b440-48cf-ac58-d66f0b65f0c3"
    prod    = "c51fc738-0fd1-49b4-a195-3ef7d737005e"
  }

  portal_flags = {
    testing = {
      "enable-admin-settings"   = true
      "enable-assistant-app"    = true
      "enable-code-app"         = false
      "enable-community-skills" = true
      "enable-installed-skills" = true
      "enable-issues-app"       = false
      "enable-lanes-shell"      = false
      "enable-meetings-app"     = false
      "enable-memory-tab"       = true
      "enable-metrics-app"      = false
      "enable-radar-app"        = false
      "enable-wiki-app"         = false
    }
    prod = {
      "enable-admin-settings"   = false
      "enable-assistant-app"    = false
      "enable-code-app"         = false
      "enable-community-skills" = false
      "enable-installed-skills" = false
      "enable-issues-app"       = false
      "enable-lanes-shell"      = false
      "enable-meetings-app"     = false
      "enable-memory-tab"       = false
      "enable-metrics-app"      = false
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
