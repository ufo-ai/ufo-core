# Every pod's telemetry reaches Datadog through the one otel-collector, so the outage signature is
# silence: a dead pipeline emits nothing and an error sweep reads it as green.
provider "datadog" {
  api_url = "https://api.us5.datadoghq.com/"
}

resource "datadog_synthetics_test" "member_paths" {
  name      = "ufo prod member paths are unavailable"
  type      = "api"
  subtype   = "multi"
  status    = "live"
  locations = ["aws:us-east-2", "aws:us-west-2"]
  message   = "{{#is_alert}}The public health or portal path failed from two regions for five minutes. Open the failed synthetic step, then check Cloudflare, ingress, gateway, serve, and the control database in that order.{{/is_alert}}{{#is_recovery}}The public health and portal paths pass from both regions.{{/is_recovery}}"
  tags      = ["env:prod", "managed-by:terraform", "team:ufo"]

  api_step {
    name    = "Control database is reachable"
    subtype = "http"

    request_definition {
      method           = "GET"
      url              = "https://ufo.ai/healthz"
      follow_redirects = false
    }

    assertion {
      type     = "statusCode"
      operator = "is"
      target   = "200"
    }

    assertion {
      type     = "body"
      operator = "contains"
      target   = "\"status\":\"ok\""
    }
  }

  api_step {
    name    = "Portal route reaches the runtime"
    subtype = "http"

    request_definition {
      method           = "GET"
      url              = "https://app.ufo.ai/surface/web"
      follow_redirects = false
    }

    assertion {
      type     = "statusCode"
      operator = "is"
      target   = "303"
    }

    assertion {
      type     = "header"
      operator = "is"
      property = "location"
      target   = "/login"
    }
  }

  options_list {
    tick_every           = 60
    min_failure_duration = 300
    min_location_failed  = 2
    monitor_priority     = 1

    retry {
      count    = 2
      interval = 5000
    }

    monitor_options {
      renotify_interval = 60
    }
  }
}

resource "datadog_monitor" "telemetry_silent" {
  name    = "ufo prod telemetry is silent"
  type    = "log alert"
  query   = "logs(\"env:prod\").index(\"*\").rollup(\"count\").last(\"15m\") < 1"
  message = "No logs from the prod fleet reached Datadog for 15 minutes: the pipeline (pods → otel-collector → Datadog exporter) or cluster egress/DNS is down."

  priority = 1

  monitor_thresholds {
    critical = 1
  }

  notify_no_data    = true
  no_data_timeframe = 15

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "deploy_failed" {
  name    = "ufo prod deploy failed on main"
  type    = "service check"
  query   = "\"ufo.deploy.main\".over(\"env:prod\").by(\"host\").last(1).count_by_status()"
  message = "Production deploy failed on main: the fleet is still running the previous images. Open the run linked from the check, then fix forward or revert."

  priority = 2

  monitor_thresholds {
    critical = 1
  }

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

# Postgres never sees a connection that never arrives, so no database-side metric can show this; the
# count is taken in `db._opened`, where the wait happens.
resource "datadog_monitor" "db_tx_unavailable" {
  name    = "ufo prod could not reach the database"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.db_tx_unavailable_total{env:prod}.as_count() >= 1"
  message = "A transaction never opened: {{value}} in 15 minutes. This is a turn or job that ended with no answer. Read `db_pool_exhausted_total` first — it is what says whether the fleet hit its own ceiling — then RDS reachability and connection count."

  priority = 1

  monitor_thresholds {
    critical = 1
  }

  require_full_window = false
  on_missing_data     = "resolve"

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

# SQLAlchemy raises its own `TimeoutError` when a checkout waits out `pool_timeout` and a lost dial
# raises the builtin one, so both arrive under the same `error_class`. This is the half that is ours.
resource "datadog_monitor" "db_pool_exhausted" {
  name    = "ufo prod exhausted a database connection pool"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.db_pool_exhausted_total{env:prod}.as_count() >= 1"
  message = "A transaction waited out the pool timeout and never got a connection: {{value}} in 15 minutes. The database was reachable — this fleet ran out of its own slots. Check `db_tx_acquire_ms` for which path queued and whether a transaction is held across an await."

  priority = 2

  monitor_thresholds {
    critical = 1
  }

  require_full_window = false
  timeout_h           = 1

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

# The grouping carries the row id: two workspaces that each connect Slack run the same provider
# stream, and one group over both would let the healthy row's OK clear the failing row's alert.
resource "datadog_monitor" "source_sync_failed" {
  name    = "ufo prod source sync failing"
  type    = "service check"
  query   = "\"ufo.source_sync\".over(\"env:prod\").by(\"provider\",\"stream\",\"source_id\").last(2).count_by_status()"
  message = "{{#is_alert}}{{provider.name}} {{stream.name}} source sync failed two runs in a row and is still failing for source {{source_id.name}}. Search source_sync.failed for that source id, its account, error class, and next attempt. The next successful run clears this, and this message repeats every hour until one does.{{/is_alert}}{{#is_recovery}}{{provider.name}} {{stream.name}} source sync succeeded again for source {{source_id.name}} — that row is syncing and needs no operator.{{/is_recovery}}"

  priority = 2

  monitor_thresholds {
    critical = 2
    ok       = 1
  }

  notify_no_data    = false
  renotify_interval = 60
  timeout_h         = 2

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

# A parked row is re-read at `SOURCE_PARK_RETRY_SECONDS` and each refused read re-parks, so one row
# contributes one point an hour: ten in six hours is only reachable by two rows or more.
resource "datadog_monitor" "source_stream_refused_everywhere" {
  name    = "ufo prod source stream refused on every account"
  type    = "query alert"
  query   = "sum(last_6h):sum:ufo.source_sync_parked_total{env:prod} by {provider,stream}.as_count() >= 10"
  message = "{{provider.name}} {{stream.name}} is parked on more than one account at once, so the refusal is not a grant one member declined — the connector asks for a scope the provider does not give it, or the stream does not belong in its catalog. Search source_sync.parked for that provider stream to read the reason and the accounts. Widening the connector's scopes or dropping the stream is what clears this."

  priority = 2

  monitor_thresholds {
    critical = 10
  }

  require_full_window = false
  timeout_h           = 1

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

# A parked page re-counts every hour it stays refused, so ten in six hours is two pages stuck for
# the window or one burst of them — a provider or a handler that needs a person.
resource "datadog_monitor" "page_change_parked" {
  name    = "ufo prod page change pages parked"
  type    = "query alert"
  query   = "sum(last_6h):sum:ufo.page_change_parked_total{env:prod} by {extension,discriminator}.as_count() >= 10"
  message = "{{extension.name}} {{discriminator.name}} has set {{value}} page deliveries aside in six hours; those pages reach nothing that reads them. Search jobs.page_change_parked for the page ids, the workspace, and the error class. The pages land by themselves once the refusal is fixed."

  priority = 2

  monitor_thresholds {
    critical = 10
  }

  require_full_window = false
  timeout_h           = 1

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

# A provider blip stops the drive once or twice and then passes; a consumer that cannot run stops
# at the tick rate. Ten in fifteen minutes is only reachable by the second.
resource "datadog_monitor" "page_change_stalled" {
  name    = "ufo prod page change consumer stalled"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.page_change_stalled_total{env:prod} by {extension,discriminator}.as_count() >= 10"
  message = "{{extension.name}} {{discriminator.name}} has stopped at the same cursor {{value}} times in 15 minutes with its parked list full, so it refuses every page it is given. Every later page in that workspace is held behind it. Search jobs.page_change_stalled for the workspace, cursor, and error class."

  priority = 2

  monitor_thresholds {
    critical = 10
  }

  require_full_window = false
  timeout_h           = 1

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

# A job holds nothing durable of its own — an execution that raises acknowledges nothing and the next
# tick re-reads the same work — so its failure reaches no table and reads only here.
resource "datadog_monitor" "job_failed" {
  name    = "ufo prod background job failing"
  type    = "query alert"
  query   = "sum(last_30m):sum:ufo.job_failed_total{env:prod} by {job}.as_count() >= 20"
  message = "{{job.name}} raised on {{value}} executions in 30 minutes, each one completing nothing it was fired to do. Search jobs.failed for that key, its workspace, error class, and stack."

  priority = 2

  monitor_thresholds {
    critical = 20
  }

  require_full_window = false
  timeout_h           = 1

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "surface_listener_parked" {
  name    = "ufo prod surface listener parked"
  type    = "query alert"
  query   = "sum(last_15m):sum:ufo.surface_listener_parked_total{env:prod} by {surface}.as_count() >= 1"
  message = "{{surface.name}} listener parked after a failure. Inbound messages have stopped while its lease stays active. Search surface.listener_failed."

  priority = 1

  monitor_thresholds {
    critical = 1
  }

  require_full_window = false
  on_missing_data     = "resolve"

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "problem_reported" {
  name    = "ufo prod agent reported a problem"
  type    = "log alert"
  query   = "logs(\"service:ufo \\\"problem.reported\\\" env:prod\").index(\"*\").rollup(\"count\").last(\"15m\") > 0"
  message = "An agent reported a problem no turn can repair — {{log.attributes.impact}} impact, category {{log.attributes.category}}. Open the reporting turn: {{log.attributes.debug_url}}\n\n{{log.attributes.problem}}\n\n{{value}} reports arrived in this window; the samples below carry the rest."

  priority = 3

  monitor_thresholds {
    critical = 0
  }

  on_missing_data    = "resolve"
  enable_logs_sample = true

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "portal_unhandled_error" {
  name    = "ufo prod portal threw an unhandled error"
  type    = "rum alert"
  query   = "rum(\"env:prod @type:error @error.handling:unhandled\").rollup(\"count\").last(\"15m\") > 0"
  message = "A portal screen threw where nothing caught it, so what the member was reading is what they are still looking at. {{value}} in this window. The session replay is the whole story — open the error in RUM, then the session it belongs to."

  priority = 2

  monitor_thresholds {
    critical = 0
  }

  on_missing_data = "resolve"

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "db_storage_low" {
  name    = "ufo prod database is low on storage"
  type    = "query alert"
  query   = "min(last_30m):avg:aws.rds.free_storage_space{dbinstanceidentifier:${module.platform.db_instance_identifier}} / avg:aws.rds.total_storage_space{dbinstanceidentifier:${module.platform.db_instance_identifier}} < 0.05"
  message = "Free storage on the database is under 5% of its allocation — past the point where autoscaling should have grown the disk. Check whether it has reached `rds_max_allocated_storage`."

  priority = 1

  monitor_thresholds {
    critical = 0.05
    warning  = 0.08
  }

  evaluation_delay = 900

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "db_memory_low" {
  name    = "ufo prod database is low on memory"
  type    = "query alert"
  query   = "min(last_15m):avg:aws.rds.freeable_memory{dbinstanceidentifier:${module.platform.db_instance_identifier}} < 419430400"
  message = "Freeable memory on the database is under 400 MB. Postgres starts refusing connections before it starts refusing queries."

  priority = 1

  monitor_thresholds {
    critical = 419430400
    warning  = 838860800
  }

  evaluation_delay = 900

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}

resource "datadog_monitor" "db_connections_high" {
  name    = "ufo prod database is holding too many connections"
  type    = "query alert"
  query   = "avg(last_15m):avg:aws.rds.database_connections{dbinstanceidentifier:${module.platform.db_instance_identifier}} > 380"
  message = "The database is holding more connections than the fleet's 348 pooled slots. Something is opening connections outside that budget, or engines outlived the loop that built them."

  priority = 2

  monitor_thresholds {
    critical = 380
    warning  = 350
  }

  evaluation_delay = 900

  tags = ["env:prod", "managed-by:terraform", "team:ufo"]
}
