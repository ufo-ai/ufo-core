# A rule recipient is the monitor-message handle with its "@" removed: the notification rule API
# refuses a recipient that starts with one, so the "@slack-alerts" channel is "slack-alerts" here.
resource "datadog_monitor_notification_rule" "testing" {
  name = "UFO testing alerts"

  filter {
    tags = ["env:testing"]
  }

  conditional_recipients {
    conditions {
      scope      = "transition_type:is_alert"
      recipients = ["slack-alerts"]
    }

    conditions {
      scope      = "transition_type:is_recovery"
      recipients = ["slack-alerts"]
    }
  }
}
