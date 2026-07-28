# The fleet's liveness signal in Datadog. Every pod's telemetry reaches Datadog through the one
# otel-collector, so the outage signature is silence, not errors — a dead pipeline emits nothing
# and an error sweep reads it as green. This monitor alerts on the absence itself.
provider "datadog" {
  api_url = "https://api.us5.datadoghq.com/"
}

resource "datadog_monitor" "telemetry_silent" {
  name    = "ufo prod telemetry is silent"
  type    = "log alert"
  query   = "logs(\"env:prod\").index(\"*\").rollup(\"count\").last(\"15m\") < 1"
  message = "No logs from the prod fleet reached Datadog for 15 minutes: the pipeline (pods → otel-collector → Datadog exporter) or cluster egress/DNS is down. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 1
  }

  notify_no_data    = true
  no_data_timeframe = 15

  tags = ["env:prod", "managed-by:terraform"]
}

# The database a turn could not reach. Serve holds no connection pool — a pooled connection binds
# to one event loop and this process runs several — so every transaction dials Postgres fresh and
# a single lost packet ends whatever was waiting on it. Postgres never sees the connection, so no
# database-side metric can show this; the count is taken in `db._opened`, where the wait happens.
# Any occurrence is a turn or a job that died, so the threshold is one.
resource "datadog_monitor" "db_tx_unavailable" {
  name    = "ufo prod could not reach the database"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.db_tx_unavailable_total{env:prod}.as_count() >= 1"
  message = "A transaction never opened: {{value}} in 15 minutes. Serve dials Postgres per transaction, so this is a turn or job that ended with no answer. Check RDS reachability and connection count before assuming a blip. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 1
  }

  tags = ["env:prod", "managed-by:terraform"]
}
