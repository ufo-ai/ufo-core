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
