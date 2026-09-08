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
  name    = "ufo prod source sync failing"
  type    = "service check"
  query   = "\"ufo.source_sync\".over(\"env:prod\").by(\"provider\",\"stream\",\"source_id\").last(2).count_by_status()"
  message = "{{#is_alert}}{{provider.name}} {{stream.name}} source sync failed two runs in a row and is still failing for source {{source_id.name}}. Search source_sync.failed for that source id, its account, error class, and next attempt. The next successful run clears this, and this message repeats every hour until one does.{{/is_alert}}{{#is_recovery}}{{provider.name}} {{stream.name}} source sync succeeded again for source {{source_id.name}} — that row is syncing and needs no operator.{{/is_recovery}} @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 2
    ok       = 1
  }

  notify_no_data    = false
  renotify_interval = 60
  timeout_h         = 2

  tags = ["env:prod", "managed-by:terraform"]
}

# A refused stream parks, and a park pages nobody — the refusal is one member's grant, and the row
# reads itself back every hour without an operator. That reasoning holds for one connection and
# fails for many: a stream refused on every account that ever connects the provider is not a grant
# anyone declined, it is a scope the connector never asks for or a stream that does not belong in
# its catalog, and no member can widen their way out of it. This is the monitor for that second
# case, and the count is what separates them. A parked row is re-read at
# `SOURCE_PARK_RETRY_SECONDS` and each refused read re-parks, so one row contributes one point an
# hour and no more; ten in six hours is only reachable by two rows or more. Below that the park
# stays what it is — a warning to read in `source_sync.parked`, never an alert to answer.
resource "datadog_monitor" "source_stream_refused_everywhere" {
  name    = "ufo prod source stream refused on every account"
  type    = "query alert"
  query   = "sum(last_6h):sum:ufo.source_sync_parked_total{env:prod} by {provider,stream}.as_count() >= 10"
  message = "{{provider.name}} {{stream.name}} is parked on more than one account at once, so the refusal is not a grant one member declined — the connector asks for a scope the provider does not give it, or the stream does not belong in its catalog. Search source_sync.parked for that provider stream to read the reason and the accounts. Widening the connector's scopes or dropping the stream is what clears this. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 10
  }

  require_full_window = false
  notify_no_data      = false

  tags = ["env:prod", "managed-by:terraform"]
}

# A page-change consumer advances its cursor only after its handler returns, so a batch the handler
# cannot accept is replayed every tick and holds every later page in that workspace behind it. The
# threshold is what separates the two faults this counts: a provider blip fails once or twice and
# then passes, while a batch that can never be accepted keeps failing at the tick rate. Ten in
# fifteen minutes is only reachable by the second.
resource "datadog_monitor" "page_change_stalled" {
  name    = "ufo prod page change consumer stalled"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.page_change_stalled_total{env:prod} by {extension,discriminator}.as_count() >= 10"
  message = "{{extension.name}} {{discriminator.name}} has replayed the same page batch {{value}} times in 15 minutes and advanced no cursor. Every later page in that workspace is held behind it. Search jobs.page_change_stalled for the workspace, cursor, and error class. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 10
  }

  require_full_window = false

  tags = ["env:prod", "managed-by:terraform"]
}

# One job execution that raised, under the key the dispatcher fired: `<extension>:<job>`. A job
# holds nothing durable of its own — an execution that raises acknowledges nothing and the next
# tick re-reads the same work — so its failure reaches no table and reads only here. That is how a
# billing pipeline stops silently: `metronome:usage_shipper` confirms its ingest alias before it
# ships anything under it, and a provider that cannot answer that read leaves the workspace's
# settled usage unshipped every minute, with a stderr trace as the only trace.
#
# The threshold separates the two faults this counts: a provider blip fails a tick or two and then
# passes, while a job that cannot run fails at its tick rate, once per workspace holding work. A
# single workspace on a per-minute job reaches twenty inside the window, and no blip either fleet
# recorded over a week came near it — the one burst that would have alerted was a job failing every
# tick for half an hour, which is the condition. A job ticking more slowly than the window is read
# from `jobs.failed`, not here.
#
# The grouping is the key alone: the class an extension raises is not one `ERROR_CLASSES` holds, so
# `error_class` folds to `other` on the series, and `jobs.failed` carries the class and the stack.
resource "datadog_monitor" "job_failed" {
  name    = "ufo prod background job failing"
  type    = "query alert"
  query   = "sum(last_30m):sum:ufo.job_failed_total{env:prod} by {job}.as_count() >= 20"
  message = "{{job.name}} raised on {{value}} executions in 30 minutes, each one completing nothing it was fired to do. Search jobs.failed for that key, its workspace, error class, and stack. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 20
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

resource "datadog_monitor" "problem_reported" {
  name    = "ufo prod agent reported a problem"
  type    = "log alert"
  query   = "logs(\"service:ufo \\\"problem.reported\\\" env:prod\").index(\"*\").rollup(\"count\").last(\"15m\") > 0"
  message = "An agent reported a problem no turn can repair — {{log.attributes.impact}} impact, category {{log.attributes.category}}. Open the reporting turn: {{log.attributes.debug_url}}\n\n{{log.attributes.problem}}\n\n{{value}} reports arrived in this window; the samples below carry the rest. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 0
  }

  notify_no_data     = false
  enable_logs_sample = true

  tags = ["env:prod", "managed-by:terraform"]
}

resource "datadog_monitor" "portal_unhandled_error" {
  name    = "ufo prod portal threw an unhandled error"
  type    = "rum alert"
  query   = "rum(\"env:prod @type:error @error.handling:unhandled\").rollup(\"count\").last(\"15m\") > 0"
  message = "A portal screen threw where nothing caught it, so what the member was reading is what they are still looking at. {{value}} in this window. The session replay is the whole story — open the error in RUM, then the session it belongs to. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 0
  }

  notify_no_data = false

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
  query   = "avg(last_15m):avg:aws.rds.database_connections{dbinstanceidentifier:${module.platform.db_instance_identifier}} > 380"
  message = "The database is holding more connections than the fleet's 348 pooled slots. Something is opening connections outside that budget, or engines outlived the loop that built them. @ops@flyingobject.ai @slack-alerts"

  monitor_thresholds {
    critical = 380
    warning  = 350
  }

  evaluation_delay = 900

  tags = ["env:prod", "managed-by:terraform"]
}
