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

# The database a turn could not reach. Each event loop holds its own pool, so a transaction is
# normally handed a warm connection; the ones that still dial — a loop's first touch, a recycled
# connection, the replacement `pool_pre_ping` opens for one the database dropped — are where a lost
# packet still ends whatever was waiting on it. Postgres never sees that connection, so no
# database-side metric can show this; the count is taken in `db._opened`, where the wait happens.
# Any occurrence is a turn or a job that died, so the threshold is one.
resource "datadog_monitor" "db_tx_unavailable" {
  name    = "ufo testing could not reach the database"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.db_tx_unavailable_total{env:testing}.as_count() >= 1"
  message = "A transaction never opened: {{value}} in 15 minutes. This is a turn or job that ended with no answer. Read `db_pool_exhausted_total` first — it is what says whether the fleet hit its own ceiling — then RDS reachability and connection count. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 1
  }

  require_full_window = false

  tags = ["env:testing", "managed-by:terraform"]
}

# The fleet at its own ceiling, which the count above cannot distinguish: SQLAlchemy raises its own
# `TimeoutError` when a checkout waits out `pool_timeout`, and a lost dial raises the builtin one, so
# both arrive under the same `error_class`. This is the half that is ours to fix — a pool sized too
# small for the loop it serves, or a transaction held open across a network await — rather than the
# network's. `db_tx_acquire_ms` on the database board is the wait this is the end of.
resource "datadog_monitor" "db_pool_exhausted" {
  name    = "ufo testing exhausted a database connection pool"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.db_pool_exhausted_total{env:testing}.as_count() >= 1"
  message = "A transaction waited out the pool timeout and never got a connection: {{value}} in 15 minutes. The database was reachable — this fleet ran out of its own slots. Check `db_tx_acquire_ms` for which path queued and whether a transaction is held across an await. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 1
  }

  require_full_window = false

  tags = ["env:testing", "managed-by:terraform"]
}

# One source row's current sync state. `_report_failed` and `_report_ok` submit every run's outcome
# as the `ufo.source_sync` service check, tagged with `_check_tags` — the provider stream it ran for
# and the row id — so each row holds its own last status and the alert is that row's state rather
# than a window over the failure counter. The grouping carries the row id for the same reason the
# submission does: two workspaces that each connect Slack run the same provider stream, and one
# group over both would let the healthy row's OK clear the failing row's alert. Two consecutive
# CRITICAL runs are what alerts: one provider blip fails a single run and is not an incident, while a
# stream that cannot sync fails every run at the sync interval. One OK clears it, so the recovered
# stream needs no operator and no waiting for a window to roll off. The failure counter stands
# alongside, with its `error_class` dimension, for what failed rather than whether it is still
# failing. No no-data clause: a source a member removed reports nothing again by design, and a fleet
# whose telemetry stopped is what the silence monitor above alerts on.
#
# Both transitions carry their own text, because the clear is the answer an operator waits for and
# the failure text sent again on OK reads as a second incident. The handles stay outside both blocks,
# so the failure and the clear reach the same Slack target. The hourly renotify is the other half: a
# row that cannot sync holds CRITICAL for as long as nobody fixes it, and one message at the
# transition is all the channel would ever hold.
#
# A refused run submits nothing here, so a row that failed twice and then started being refused would
# hold CRITICAL with no later run able to clear it — the park writes a log, never a status, and the
# stream is not failing any more. `timeout_h` is what ends that: a failing row submits CRITICAL every
# interval, so two hours of silence on one source id means the failure path stopped running for it,
# whether the row parked, recovered under a status this monitor does not watch, or was removed. The
# fleet going quiet altogether is the silence monitor's alert, not this one's.
resource "datadog_monitor" "source_sync_failed" {
  name    = "ufo testing source sync failing"
  type    = "service check"
  query   = "\"ufo.source_sync\".over(\"env:testing\").by(\"provider\",\"stream\",\"source_id\").last(2).count_by_status()"
  message = "{{#is_alert}}{{provider.name}} {{stream.name}} source sync failed two runs in a row and is still failing for source {{source_id.name}}. Search source_sync.failed for that source id, its account, error class, and next attempt. The next successful run clears this, and this message repeats every hour until one does.{{/is_alert}}{{#is_recovery}}{{provider.name}} {{stream.name}} source sync succeeded again for source {{source_id.name}} — that row is syncing and needs no operator.{{/is_recovery}} @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 2
    ok       = 1
  }

  notify_no_data    = false
  renotify_interval = 60
  timeout_h         = 2

  tags = ["env:testing", "managed-by:terraform"]
}

# A page-change consumer advances its cursor only after its handler returns, so a batch the handler
# cannot accept is replayed every tick and holds every later page in that workspace behind it. The
# threshold is what separates the two faults this counts: a provider blip fails once or twice and
# then passes, while a batch that can never be accepted keeps failing at the tick rate. Ten in
# fifteen minutes is only reachable by the second.
resource "datadog_monitor" "page_change_stalled" {
  name    = "ufo testing page change consumer stalled"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.page_change_stalled_total{env:testing} by {extension,discriminator}.as_count() >= 10"
  message = "{{extension.name}} {{discriminator.name}} has replayed the same page batch {{value}} times in 15 minutes and advanced no cursor. Every later page in that workspace is held behind it. Search jobs.page_change_stalled for the workspace, cursor, and error class. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 10
  }

  require_full_window = false

  tags = ["env:testing", "managed-by:terraform"]
}

resource "datadog_monitor" "surface_listener_parked" {
  name    = "ufo testing surface listener parked"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.surface_listener_parked_total{env:testing} by {surface}.as_count() >= 1"
  message = "{{surface.name}} listener parked after a failure. Inbound messages have stopped while its lease stays active. Search surface.listener_failed. @ops@flyingobject.ai @slack-alerts"

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

# Pools make this threshold arithmetic rather than a multiple of an observed peak. `show
# max_connections` on this instance is 400 with 3 superuser-reserved, and the pool ceilings in
# `core/src/ufo/db.py` add to 268 across every `init_db` root plus DBOS's own pools — so the fleet is
# entitled to 268 and Postgres starts refusing at 397. Warning at 300 is the fleet holding more than
# its own budget allows, which means connections opened outside it (a PR preview against this
# instance, a pool that outlived its loop). Critical at 350 is that, still climbing, with the refusal
# in sight.
resource "datadog_monitor" "db_connections_high" {
  name    = "ufo testing database is holding too many connections"
  type    = "query alert"
  query   = "avg(last_15m):avg:aws.rds.database_connections{dbinstanceidentifier:${module.platform.db_instance_identifier}} > 350"
  message = "The database is holding more connections than the fleet's pool ceilings add up to. Something is opening connections outside that budget — a preview environment on this instance, or engines outliving the loop that built them. Postgres refuses new connections at 397. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 350
    warning  = 300
  }

  evaluation_delay = 900

  tags = ["env:testing", "managed-by:terraform"]
}
