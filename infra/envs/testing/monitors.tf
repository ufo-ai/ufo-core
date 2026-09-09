# Every pod's telemetry reaches Datadog through the one otel-collector, so the outage signature is
# silence: a dead pipeline emits nothing and an error sweep reads it as green.
provider "datadog" {
  api_url = "https://api.us5.datadoghq.com/"
}

resource "datadog_monitor" "telemetry_silent" {
  name    = "ufo testing telemetry is silent"
  type    = "log alert"
  query   = "logs(\"env:testing\").index(\"*\").rollup(\"count\").last(\"15m\") < 1"
  message = "No logs from the testing fleet reached Datadog for 15 minutes: the pipeline (pods → otel-collector → Datadog exporter) or cluster egress/DNS is down."

  priority = 4

  monitor_thresholds {
    critical = 1
  }

  notify_no_data    = true
  no_data_timeframe = 15

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "deploy_failed" {
  name    = "ufo testing deploy failed on main"
  type    = "service check"
  query   = "\"ufo.deploy.main\".over(\"env:testing\").by(\"host\").last(1).count_by_status()"
  message = "Deploy (testing) failed on main: the fleet is still running the previous images. Open the run linked from the check, then fix forward or revert."

  priority = 4

  monitor_thresholds {
    critical = 1
  }

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

# Postgres never sees a connection that never arrives, so no database-side metric can show this; the
# count is taken in `db._opened`, where the wait happens.
resource "datadog_monitor" "db_tx_unavailable" {
  name    = "ufo testing could not reach the database"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.db_tx_unavailable_total{env:testing}.as_count() >= 1"
  message = "A transaction never opened: {{value}} in 15 minutes. This is a turn or job that ended with no answer. Read `db_pool_exhausted_total` first — it is what says whether the fleet hit its own ceiling — then RDS reachability and connection count."

  priority = 4

  monitor_thresholds {
    critical = 1
  }

  require_full_window = false

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

# SQLAlchemy raises its own `TimeoutError` when a checkout waits out `pool_timeout` and a lost dial
# raises the builtin one, so both arrive under the same `error_class`. This is the half that is ours.
resource "datadog_monitor" "db_pool_exhausted" {
  name    = "ufo testing exhausted a database connection pool"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.db_pool_exhausted_total{env:testing}.as_count() >= 1"
  message = "A transaction waited out the pool timeout and never got a connection: {{value}} in 15 minutes. The database was reachable — this fleet ran out of its own slots. Check `db_tx_acquire_ms` for which path queued and whether a transaction is held across an await."

  priority = 4

  monitor_thresholds {
    critical = 1
  }

  require_full_window = false

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

# The grouping carries the row id: two workspaces that each connect Slack run the same provider
# stream, and one group over both would let the healthy row's OK clear the failing row's alert.
resource "datadog_monitor" "source_sync_failed" {
  name    = "ufo testing source sync failing"
  type    = "service check"
  query   = "\"ufo.source_sync\".over(\"env:testing\").by(\"provider\",\"stream\",\"source_id\").last(2).count_by_status()"
  message = "{{#is_alert}}{{provider.name}} {{stream.name}} source sync failed two runs in a row and is still failing for source {{source_id.name}}. Search source_sync.failed for that source id, its account, error class, and next attempt. The next successful run clears this, and this message repeats every hour until one does.{{/is_alert}}{{#is_recovery}}{{provider.name}} {{stream.name}} source sync succeeded again for source {{source_id.name}} — that row is syncing and needs no operator.{{/is_recovery}}"

  priority = 4

  monitor_thresholds {
    critical = 2
    ok       = 1
  }

  notify_no_data    = false
  renotify_interval = 60
  timeout_h         = 2

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

# A parked row is re-read at `SOURCE_PARK_RETRY_SECONDS` and each refused read re-parks, so one row
# contributes one point an hour: ten in six hours is only reachable by two rows or more.
resource "datadog_monitor" "source_stream_refused_everywhere" {
  name    = "ufo testing source stream refused on every account"
  type    = "query alert"
  query   = "sum(last_6h):sum:ufo.source_sync_parked_total{env:testing} by {provider,stream}.as_count() >= 10"
  message = "{{provider.name}} {{stream.name}} is parked on more than one account at once, so the refusal is not a grant one member declined — the connector asks for a scope the provider does not give it, or the stream does not belong in its catalog. Search source_sync.parked for that provider stream to read the reason and the accounts. Widening the connector's scopes or dropping the stream is what clears this."

  priority = 4

  monitor_thresholds {
    critical = 10
  }

  require_full_window = false
  notify_no_data      = false

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

# A parked page re-counts every hour it stays refused, so ten in six hours is two pages stuck for
# the window or one burst of them — a provider or a handler that needs a person.
resource "datadog_monitor" "page_change_parked" {
  name    = "ufo testing page change pages parked"
  type    = "query alert"
  query   = "sum(last_6h):sum:ufo.page_change_parked_total{env:testing} by {extension,discriminator}.as_count() >= 10"
  message = "{{extension.name}} {{discriminator.name}} has set {{value}} page deliveries aside in six hours; those pages reach nothing that reads them. Search jobs.page_change_parked for the page ids, the workspace, and the error class. The pages land by themselves once the refusal is fixed."

  priority = 4

  monitor_thresholds {
    critical = 10
  }

  require_full_window = false
  notify_no_data      = false

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

# A provider blip stops the drive once or twice and then passes; a consumer that cannot run stops
# at the tick rate. Ten in fifteen minutes is only reachable by the second.
resource "datadog_monitor" "page_change_stalled" {
  name    = "ufo testing page change consumer stalled"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.page_change_stalled_total{env:testing} by {extension,discriminator}.as_count() >= 10"
  message = "{{extension.name}} {{discriminator.name}} has stopped at the same cursor {{value}} times in 15 minutes with its parked list full, so it refuses every page it is given. Every later page in that workspace is held behind it. Search jobs.page_change_stalled for the workspace, cursor, and error class."

  priority = 4

  monitor_thresholds {
    critical = 10
  }

  require_full_window = false

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

# A job holds nothing durable of its own — an execution that raises acknowledges nothing and the next
# tick re-reads the same work — so its failure reaches no table and reads only here.
resource "datadog_monitor" "job_failed" {
  name    = "ufo testing background job failing"
  type    = "query alert"
  query   = "sum(last_30m):sum:ufo.job_failed_total{env:testing} by {job}.as_count() >= 20"
  message = "{{job.name}} raised on {{value}} executions in 30 minutes, each one completing nothing it was fired to do. Search jobs.failed for that key, its workspace, error class, and stack."

  priority = 4

  monitor_thresholds {
    critical = 20
  }

  require_full_window = false

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "surface_listener_parked" {
  name    = "ufo testing surface listener parked"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.surface_listener_parked_total{env:testing} by {surface}.as_count() >= 1"
  message = "{{surface.name}} listener parked after a failure. Inbound messages have stopped while its lease stays active. Search surface.listener_failed."

  priority = 4

  monitor_thresholds {
    critical = 1
  }

  require_full_window = false

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "problem_reported" {
  name    = "ufo testing agent reported a problem"
  type    = "log alert"
  query   = "logs(\"service:ufo \\\"problem.reported\\\" env:testing\").index(\"*\").rollup(\"count\").last(\"15m\") > 0"
  message = "An agent reported a problem no turn can repair — {{log.attributes.impact}} impact, category {{log.attributes.category}}. Open the reporting turn: {{log.attributes.debug_url}}\n\n{{log.attributes.problem}}\n\n{{value}} reports arrived in this window; the samples below carry the rest."

  priority = 4

  monitor_thresholds {
    critical = 0
  }

  notify_no_data     = false
  enable_logs_sample = true

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "db_cpu_surplus_charged" {
  name    = "ufo testing database is bursting past its baseline"
  type    = "query alert"
  query   = "min(last_1h):avg:aws.rds.cpusurplus_credits_charged{dbinstanceidentifier:${module.platform.db_instance_identifier}} > 0"
  message = "The database has spent its earned CPU credits and has been paying for surplus burst for an hour straight. Unlimited mode bills this rather than throttling, so nothing will slow down to tell you: find what is burning CPU, or raise the instance class."

  priority = 4

  monitor_thresholds {
    critical = 0
  }

  evaluation_delay = 900

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "db_memory_low" {
  name    = "ufo testing database is low on memory"
  type    = "query alert"
  query   = "min(last_15m):avg:aws.rds.freeable_memory{dbinstanceidentifier:${module.platform.db_instance_identifier}} < 209715200"
  message = "Freeable memory on the database is under 200 MB. Postgres starts refusing connections before it starts refusing queries."

  priority = 4

  monitor_thresholds {
    critical = 209715200
    warning  = 419430400
  }

  evaluation_delay = 900

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "db_storage_low" {
  name    = "ufo testing database is low on storage"
  type    = "query alert"
  query   = "min(last_30m):avg:aws.rds.free_storage_space{dbinstanceidentifier:${module.platform.db_instance_identifier}} / avg:aws.rds.total_storage_space{dbinstanceidentifier:${module.platform.db_instance_identifier}} < 0.05"
  message = "Free storage on the database is under 5% of its allocation — past the point where autoscaling should have grown the disk. Check whether it has reached `rds_max_allocated_storage`."

  priority = 4

  monitor_thresholds {
    critical = 0.05
    warning  = 0.08
  }

  evaluation_delay = 900

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "db_connections_high" {
  name    = "ufo testing database is holding too many connections"
  type    = "query alert"
  query   = "avg(last_15m):avg:aws.rds.database_connections{dbinstanceidentifier:${module.platform.db_instance_identifier}} > 380"
  message = "The database is holding more connections than the fleet's pool ceilings add up to. Something is opening connections outside that budget — a preview environment on this instance, or engines outliving the loop that built them. Postgres refuses new connections at 397."

  priority = 4

  monitor_thresholds {
    critical = 380
    warning  = 350
  }

  evaluation_delay = 900

  tags = ["env:testing", "managed-by:terraform", "team:ufo"]
}
