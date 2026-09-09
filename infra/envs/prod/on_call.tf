data "datadog_user" "alex" {
  filter                   = "alex@metalcraft.ai"
  exact_match              = true
  exclude_service_accounts = true
}

data "datadog_user" "marshall" {
  filter                   = "marshall@metalcraft.ai"
  exact_match              = true
  exclude_service_accounts = true
}

resource "datadog_team" "ufo" {
  name        = "UFO"
  handle      = "ufo"
  description = "UFO production operations."
}

resource "datadog_team_membership" "alex" {
  team_id = datadog_team.ufo.id
  user_id = data.datadog_user.alex.id
}

resource "datadog_team_membership" "marshall" {
  team_id = datadog_team.ufo.id
  user_id = data.datadog_user.marshall.id
}

import {
  to = datadog_on_call_schedule.ufo
  id = "d4c50744-7563-4f43-8e25-d2ed5df3a5f3"
}

resource "datadog_on_call_schedule" "ufo" {
  name      = "UFO primary"
  time_zone = "America/Los_Angeles"
  teams     = [datadog_team.ufo.id]

  layer {
    name           = "Daily"
    effective_date = "2026-09-09T19:00:00Z"
    rotation_start = "2026-09-09T19:00:00Z"
    users          = [data.datadog_user.alex.id, data.datadog_user.marshall.id]

    interval {
      days = 1
    }
  }
}

resource "datadog_on_call_escalation_policy" "ufo" {
  name    = "UFO production"
  retries = 1
  teams   = [datadog_team.ufo.id]

  step {
    assignment             = "default"
    escalate_after_seconds = 300

    target {
      schedule = datadog_on_call_schedule.ufo.id
      position = "current"
    }
  }

  step {
    assignment             = "default"
    escalate_after_seconds = 300

    target {
      schedule = datadog_on_call_schedule.ufo.id
      position = "next"
    }
  }
}

resource "datadog_on_call_team_routing_rules" "ufo" {
  id = datadog_team.ufo.id

  rule {
    escalation_policy = datadog_on_call_escalation_policy.ufo.id
    urgency           = "dynamic"
  }
}

resource "datadog_monitor_notification_rule" "prod" {
  name = "UFO production alerts"

  filter {
    tags = ["env:prod"]
  }

  conditional_recipients {
    conditions {
      scope      = "priority:p1"
      recipients = ["oncall-ufo", "slack-on-call"]
    }

    fallback_recipients = ["slack-on-call"]
  }
}
