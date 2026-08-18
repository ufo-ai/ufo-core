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

resource "datadog_monitor" "deploy_failed" {
  name    = "ufo prod deploy failed on main"
  type    = "service check"
  query   = "\"ufo.deploy.main\".over(\"env:prod\").by(\"host\").last(1).count_by_status()"
  message = "Production deploy failed on main: the fleet is still running the previous images. Open the run linked from the check, then fix forward or revert. @slack-alerts @ops@flyingobject.ai"

  monitor_thresholds {
    critical = 1
  }

  tags = ["env:prod", "managed-by:terraform"]
}

# The database a turn could not reach. Each event loop holds its own pool, so a transaction is
# normally handed a warm connection; the ones that still dial — a loop's first touch, a recycled
# connection, the replacement `pool_pre_ping` opens for one the database dropped — are where a lost
# packet still ends whatever was waiting on it. Postgres never sees that connection, so no
# database-side metric can show this; the count is taken in `db._opened`, where the wait happens.
# Any occurrence is a turn or a job that died, so the threshold is one.
resource "datadog_monitor" "db_tx_unavailable" {
  name    = "ufo prod could not reach the database"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.db_tx_unavailable_total{env:prod}.as_count() >= 1"
  message = "A transaction never opened: {{value}} in 15 minutes. This is a turn or job that ended with no answer. Read `db_pool_exhausted_total` first — it is what says whether the fleet hit its own ceiling — then RDS reachability and connection count. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 1
  }

  require_full_window = false

  tags = ["env:prod", "managed-by:terraform"]
}

# The fleet at its own ceiling, which the count above cannot distinguish: SQLAlchemy raises its own
# `TimeoutError` when a checkout waits out `pool_timeout`, and a lost dial raises the builtin one, so
# both arrive under the same `error_class`. This is the half that is ours to fix — a pool sized too
# small for the loop it serves, or a transaction held open across a network await — rather than the
# network's. Prod runs a `db.m6g.large`, so a pool exhausted here is a shape problem, never a ceiling
# the instance imposed.
resource "datadog_monitor" "db_pool_exhausted" {
  name    = "ufo prod exhausted a database connection pool"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.db_pool_exhausted_total{env:prod}.as_count() >= 1"
  message = "A transaction waited out the pool timeout and never got a connection: {{value}} in 15 minutes. The database was reachable — this fleet ran out of its own slots. Check `db_tx_acquire_ms` for which path queued and whether a transaction is held across an await. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 1
  }

  require_full_window = false

  tags = ["env:prod", "managed-by:terraform"]
}

resource "datadog_monitor" "source_sync_failed" {
  name    = "ufo prod source sync failed"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.source_sync_failed_total{env:prod} by {provider,stream}.as_count() >= 1"
  message = "{{provider.name}} {{stream.name}} source sync failed {{value}} times in 15 minutes. Search source_sync.failed for the source, account, error class, and next attempt. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 1
  }

  require_full_window = false

  tags = ["env:prod", "managed-by:terraform"]
}

resource "datadog_monitor" "surface_listener_parked" {
  name    = "ufo prod surface listener parked"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.surface_listener_parked_total{env:prod} by {surface}.as_count() >= 1"
  message = "{{surface.name}} listener parked after a failure. Inbound messages have stopped while its lease stays active. Search surface.listener_failed. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 1
  }

  require_full_window = false

  tags = ["env:prod", "managed-by:terraform"]
}

resource "datadog_monitor" "db_storage_low" {
  name    = "ufo prod database is low on storage"
  type    = "query alert"
  query   = "min(last_30m):avg:aws.rds.free_storage_space{dbinstanceidentifier:${module.platform.db_instance_identifier}} / avg:aws.rds.total_storage_space{dbinstanceidentifier:${module.platform.db_instance_identifier}} < 0.05"
  message = "Free storage on the database is under 5% of its allocation — past the point where autoscaling should have grown the disk. Check whether it has reached `rds_max_allocated_storage`. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 0.05
    warning  = 0.08
  }

  evaluation_delay = 900

  tags = ["env:prod", "managed-by:terraform"]
}

resource "datadog_monitor" "db_memory_low" {
  name    = "ufo prod database is low on memory"
  type    = "query alert"
  query   = "min(last_15m):avg:aws.rds.freeable_memory{dbinstanceidentifier:${module.platform.db_instance_identifier}} < 419430400"
  message = "Freeable memory on the database is under 400 MB. Postgres starts refusing connections before it starts refusing queries. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 419430400
    warning  = 838860800
  }

  evaluation_delay = 900

  tags = ["env:prod", "managed-by:terraform"]
}

resource "datadog_monitor" "db_connections_high" {
  name    = "ufo prod database is holding too many connections"
  type    = "query alert"
  query   = "avg(last_15m):avg:aws.rds.database_connections{dbinstanceidentifier:${module.platform.db_instance_identifier}} > 300"
  message = "The database is holding more connections than the fleet's 268 pooled slots. Something is opening connections outside that budget, or engines outlived the loop that built them. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 300
    warning  = 268
  }

  evaluation_delay = 900

  tags = ["env:prod", "managed-by:terraform"]
}
