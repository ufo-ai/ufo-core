# The fleet's liveness signal in Datadog. Every pod's telemetry reaches Datadog through the one
# otel-collector, so the outage signature is silence, not errors — a dead pipeline emits nothing
# and an error sweep reads it as green. This monitor alerts on the absence itself.
provider "datadog" {
  api_url = "https://api.us5.datadoghq.com/"
}

resource "datadog_monitor" "telemetry_silent" {
  name    = "ufo testing telemetry is silent"
  type    = "log alert"
  query   = "logs(\"env:testing\").index(\"*\").rollup(\"count\").last(\"15m\") < 1"
  message = "No logs from the testing fleet reached Datadog for 15 minutes: the pipeline (pods → otel-collector → Datadog exporter) or cluster egress/DNS is down. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 1
  }

  notify_no_data    = true
  no_data_timeframe = 15

  tags = ["env:testing", "managed-by:terraform"]
}

# The deploy's own verdict. A stuck rollout keeps the previous pods alive and talking, so the
# silence monitor above is blind to it. The `deployment` job submits every main conclusion as the
# `ufo.deploy.main` service check, which holds its last status: the alert is the fleet's current
# state rather than a window over a moment, and a later successful deploy is what clears it. The
# check name is this signal's whole namespace, so nothing else can land in it. No no-data clause:
# deploys are merge-driven, so a quiet weekend is not an incident — a reporter that breaks fails
# the deploy step instead.
resource "datadog_monitor" "deploy_failed" {
  name    = "ufo testing deploy failed on main"
  type    = "service check"
  query   = "\"ufo.deploy.main\".over(\"env:testing\").by(\"host\").last(1).count_by_status()"
  message = "Deploy (testing) failed on main: the fleet is still running the previous images. Open the run linked from the check, then fix forward or revert. @slack-alerts @ops@flyingobject.ai"

  monitor_thresholds {
    critical = 1
  }

  tags = ["env:testing", "managed-by:terraform"]
}
