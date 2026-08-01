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

# The database a turn could not reach. Serve holds no connection pool — a pooled connection binds
# to one event loop and this process runs several — so every transaction dials Postgres fresh and
# a single lost packet ends whatever was waiting on it. Postgres never sees the connection, so no
# database-side metric can show this; the count is taken in `db._opened`, where the wait happens.
# Any occurrence is a turn or a job that died, so the threshold is one.
resource "datadog_monitor" "db_tx_unavailable" {
  name    = "ufo testing could not reach the database"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.db_tx_unavailable_total{env:testing}.as_count() >= 1"
  message = "A transaction never opened: {{value}} in 15 minutes. Serve dials Postgres per transaction, so this is a turn or job that ended with no answer. Check RDS reachability and connection count before assuming a blip. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 1
  }

  tags = ["env:testing", "managed-by:terraform"]
}

resource "datadog_monitor" "source_sync_failed" {
  name    = "ufo testing source sync failed"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.source_sync_failed_total{env:testing} by {provider,stream}.as_count() >= 1"
  message = "{{provider.name}} {{stream.name}} source sync failed {{value}} times in 15 minutes. Search source_sync.failed for the source, account, error class, and next attempt. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 1
  }

  require_full_window = false

  tags = ["env:testing", "managed-by:terraform"]
}

# The database's own health, from CloudWatch. These answer capacity questions — is the instance
# running out of something — which is a different question from whether a turn reached it, and a
# slower one: a point lands well after the minute it describes, so none of these can catch an
# incident as it happens. `db_tx_unavailable` above is the one that fires within a turn's lifetime.
#
# That lag is also why each carries `evaluation_delay`, at Datadog's stated minimum for a backfilled
# source: the end of an undelayed window is always empty, and a monitor that never gets a full
# window skips its evaluation rather than failing it, which reads exactly like a quiet one.

# A burstable class earns CPU credits at a fixed rate and spends them above its baseline share, and
# RDS configures `db.t4g` for Unlimited mode: a balance at zero does not throttle the instance, it
# keeps bursting on surplus. AWS bills that surplus only once it passes what the class can earn in a
# day, so the charge trails an empty balance by a wide margin — which is what makes it the alert and
# the balance the graph. An hour of it is an instance too small for the fleet, presenting as a bill
# rather than as latency.
resource "datadog_monitor" "db_cpu_surplus_charged" {
  name    = "ufo testing database is bursting past its baseline"
  type    = "query alert"
  query   = "min(last_1h):avg:aws.rds.cpusurplus_credits_charged{dbinstanceidentifier:${module.platform.db_instance_identifier}} > 0"
  message = "The database has spent its earned CPU credits and has been paying for surplus burst for an hour straight. Unlimited mode bills this rather than throttling, so nothing will slow down to tell you: find what is burning CPU, or raise the instance class. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 0
  }

  evaluation_delay = 900

  tags = ["env:testing", "managed-by:terraform"]
}

# 4 GiB of RAM. Postgres refuses connections before it refuses queries, so this is upstream of the
# symptom rather than the symptom.
resource "datadog_monitor" "db_memory_low" {
  name    = "ufo testing database is low on memory"
  type    = "query alert"
  query   = "min(last_15m):avg:aws.rds.freeable_memory{dbinstanceidentifier:${module.platform.db_instance_identifier}} < 209715200"
  message = "Freeable memory on the database is under 200 MB. Postgres starts refusing connections before it starts refusing queries. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 209715200
    warning  = 419430400
  }

  evaluation_delay = 900

  tags = ["env:testing", "managed-by:terraform"]
}

# Autoscaling grows the disk whenever free space falls under 10% of the allocation, so the floor
# that catches it not keeping up sits under that trigger and moves with the allocation:
# `total_storage_space` is the allocation. Both sides submit a point a minute, so every bucket in
# the window has a value to divide.
resource "datadog_monitor" "db_storage_low" {
  name    = "ufo testing database is low on storage"
  type    = "query alert"
  query   = "min(last_30m):avg:aws.rds.free_storage_space{dbinstanceidentifier:${module.platform.db_instance_identifier}} / avg:aws.rds.total_storage_space{dbinstanceidentifier:${module.platform.db_instance_identifier}} < 0.05"
  message = "Free storage on the database is under 5% of its allocation — past the point where autoscaling should have grown the disk. Check whether it has reached `rds_max_allocated_storage`. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 0.05
    warning  = 0.08
  }

  evaluation_delay = 900

  tags = ["env:testing", "managed-by:terraform"]
}

# Pool-less serve dials per transaction, so a runaway is a connection storm rather than a slow
# climb. Measured over three hours of real traffic the instance sits at 15-33 connections; RDS's
# formula puts the ceiling near 450 for 4 GiB. Critical at 150 is four times the observed peak and a
# third of the ceiling — far enough above normal to mean something is wrong, far enough below the
# limit to arrive before Postgres starts refusing.
resource "datadog_monitor" "db_connections_high" {
  name    = "ufo testing database is holding too many connections"
  type    = "query alert"
  query   = "avg(last_15m):avg:aws.rds.database_connections{dbinstanceidentifier:${module.platform.db_instance_identifier}} > 150"
  message = "The database is holding far more connections than this fleet opens in normal traffic. Serve dials per transaction, so this is churn outpacing teardown rather than a busy hour. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 150
    warning  = 100
  }

  evaluation_delay = 900

  tags = ["env:testing", "managed-by:terraform"]
}
