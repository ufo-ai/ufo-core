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

import {
  to = datadog_team.ufo
  id = "d4c50744-7563-4f43-8e25-d2ed5df3a5f3"
}

resource "datadog_team" "ufo" {
  name        = "UFO"
  handle      = "ufo"
  description = "UFO production operations."
}

import {
  to = datadog_team_membership.alex
  id = "d4c50744-7563-4f43-8e25-d2ed5df3a5f3:95bdda15-44fc-45f1-83ca-a7c58b2b887a"
}

# An omitted role is not an unmanaged one: the provider sends an empty role and the API clears the
# membership to plain member, dropping the right to edit the team's own on-call configuration.
resource "datadog_team_membership" "alex" {
  team_id = datadog_team.ufo.id
  user_id = data.datadog_user.alex.id
  role    = "admin"
}

import {
  to = datadog_team_membership.marshall
  id = "d4c50744-7563-4f43-8e25-d2ed5df3a5f3:19b2a4c5-ceba-4d62-9fc0-3c08400759af"
}

resource "datadog_team_membership" "marshall" {
  team_id = datadog_team.ufo.id
  user_id = data.datadog_user.marshall.id
  role    = "admin"
}

import {
  to = datadog_on_call_schedule.ufo
  id = "64f82149-e8df-4380-93e4-426f2ba4e364"
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

import {
  to = datadog_on_call_escalation_policy.ufo
  id = "f3c45cbd-7744-4128-b9c2-d74048e08869"
}

resource "datadog_on_call_escalation_policy" "ufo" {
  name    = "UFO production"
  retries = 1
  teams   = [datadog_team.ufo.id]

  # The API returns the current position as a plain schedule target carrying no position at all, and
  # only "next" as its own object, so sending "current" back fails the apply on an inconsistent result.
  step {
    assignment             = "default"
    escalate_after_seconds = 300

    target {
      schedule = datadog_on_call_schedule.ufo.id
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

# A rule recipient is the monitor-message handle with its "@" removed: the notification rule API
# refuses a recipient that starts with one, so the channel and the on-call team are bare handles.
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
