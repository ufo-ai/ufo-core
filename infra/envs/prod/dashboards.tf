
resource "datadog_dashboard" "database" {
  title       = "ufo prod database"
  layout_type = "ordered"

  widget {
    note_definition {
      content          = <<-EOT
        Every event loop holds its own pool, so a transaction waits twice: for a slot, then for a
        dial only if no warm connection is free. Read the first three graphs before the rest — the
        instance can look healthy on every CloudWatch series while turns die waiting on a connection
        Postgres never received, and the pool graphs are what say whether the wait was ours or the
        network's. The CloudWatch series land minutes after the minute they describe; they answer
        capacity questions, never incident ones.
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
        q            = "sum:ufo.db_tx_unavailable_total{env:prod} by {path,error_class}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "how long a transaction waited for a connection"
      request {
        q            = "p95:ufo.db_tx_acquire_ms{env:prod} by {path}"
        display_type = "line"
      }
      request {
        q            = "p99:ufo.db_tx_acquire_ms{env:prod} by {path}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "pools exhausted at their ceiling (client side)"
      request {
        q            = "sum:ufo.db_pool_exhausted_total{env:prod} by {path}.as_count()"
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
resource "datadog_dashboard" "model_latency" {
  title       = "ufo prod model latency"
  layout_type = "ordered"

  widget {
    note_definition {
      content          = <<-EOT
        Compare backends on `first token wait`, not on `whole round`: a round's wall clock is
        dominated by how many tokens it emitted, so a model asked to think longer reads as a slower
        provider. `generation rate` is the token-normalized view and is what a routing decision
        should rest on.

        Read a difference only where `rounds by provider` is high enough to carry a percentile and
        `failed rounds` is flat.
      EOT
      background_color = "yellow"
      font_size        = "14"
      text_align       = "left"
      show_tick        = false
    }
  }

  widget {
    timeseries_definition {
      title = "first token wait by provider"
      request {
        q            = "p50:ufo.model_first_event_ms{env:prod} by {provider}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.model_first_event_ms{env:prod} by {provider}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "whole round by provider"
      request {
        q            = "p50:ufo.model_round_ms{env:prod,!error_class:*} by {provider}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.model_round_ms{env:prod,!error_class:*} by {provider}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "generation rate by provider (tokens/s)"
      request {
        q            = "sum:ufo.model_round_tokens_total{env:prod,kind:output} by {provider}.as_count() / (sum:ufo.model_round_ms{env:prod,!error_class:*} by {provider} / 1000)"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "rounds by provider"
      request {
        q            = "count:ufo.model_round_ms{env:prod,!error_class:*} by {provider}"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "failed rounds by provider and class"
      request {
        q            = "count:ufo.model_round_ms{env:prod,error_class:*} by {provider,error_class}"
        display_type = "bars"
      }
    }
  }

  widget {
    toplist_definition {
      title = "first token wait by model"
      request {
        q = "p95:ufo.model_first_event_ms{env:prod} by {model,provider}"
      }
    }
  }
}
