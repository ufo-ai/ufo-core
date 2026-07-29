# The database, from both sides at once. CloudWatch says what Postgres was doing; it cannot say
# whether a turn reached it, because a connection that never arrives is not a connection Postgres
# ever sees. `ufo.db_tx_unavailable_total` is that other side, and it sits on this board next to the
# instance's own health so the pair is read together — the failure in #834 was invisible on every
# database-side graph while a turn died waiting on a connect.
#
# One board for the testing fleet, in the root that owns its instance.

resource "datadog_dashboard" "database" {
  title       = "ufo testing database"
  layout_type = "ordered"

  widget {
    note_definition {
      content          = <<-EOT
        Serve holds no connection pool: a pooled connection binds to one event loop and the process
        runs several, so every transaction dials Postgres fresh. Read the first graph before the
        rest — the instance can look healthy on every CloudWatch series while turns die on connects
        Postgres never received. Those series land minutes after the minute they describe; they
        answer capacity questions, never incident ones.
      EOT
      background_color = "yellow"
      font_size        = "14"
      text_align       = "left"
      show_tick        = false
    }
  }

  widget {
    timeseries_definition {
      title = "transactions that never opened (client side)"
      request {
        q            = "sum:ufo.db_tx_unavailable_total{env:testing} by {path,error_class}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "connections"
      request {
        q            = "avg:aws.rds.database_connections{dbinstanceidentifier:${module.platform.db_instance_identifier}}"
        display_type = "line"
      }
    }
  }

  # The graph that arrives before the charge, by a wide margin: the balance drains while CPU holds
  # above the baseline.
  widget {
    timeseries_definition {
      title = "cpu credit balance"
      request {
        q            = "avg:aws.rds.cpucredit_balance{dbinstanceidentifier:${module.platform.db_instance_identifier}}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "cpu utilization"
      request {
        q            = "avg:aws.rds.cpuutilization{dbinstanceidentifier:${module.platform.db_instance_identifier}}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "freeable memory"
      request {
        q            = "avg:aws.rds.freeable_memory{dbinstanceidentifier:${module.platform.db_instance_identifier}}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "read and write latency"
      request {
        q            = "avg:aws.rds.read_latency{dbinstanceidentifier:${module.platform.db_instance_identifier}}"
        display_type = "line"
      }
      request {
        q            = "avg:aws.rds.write_latency{dbinstanceidentifier:${module.platform.db_instance_identifier}}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "free storage space"
      request {
        q            = "avg:aws.rds.free_storage_space{dbinstanceidentifier:${module.platform.db_instance_identifier}}"
        display_type = "line"
      }
    }
  }
}
