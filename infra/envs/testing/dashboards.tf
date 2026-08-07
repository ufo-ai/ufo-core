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
        q            = "sum:ufo.db_tx_unavailable_total{env:testing} by {path,error_class}.as_count()"
        display_type = "bars"
      }
    }
  }

  # The wait that precedes both of the graphs around it. A pool filling shows here as a rising tail
  # long before it shows anywhere else, and a wait that ends at `pool_timeout` is what the exhaustion
  # count is the other end of.
  widget {
    timeseries_definition {
      title = "how long a transaction waited for a connection"
      request {
        q            = "p95:ufo.db_tx_acquire_ms{env:testing} by {path}"
        display_type = "line"
      }
      request {
        q            = "p99:ufo.db_tx_acquire_ms{env:testing} by {path}"
        display_type = "line"
      }
    }
  }

  # The half of the unavailable count that is ours rather than the network's: both raise a
  # `TimeoutError`, so this is the only thing that separates the fleet at its own ceiling from a lost
  # packet.
  widget {
    timeseries_definition {
      title = "pools exhausted at their ceiling (client side)"
      request {
        q            = "sum:ufo.db_pool_exhausted_total{env:testing} by {path}.as_count()"
        display_type = "bars"
      }
    }
  }

  # 268 is what every pool ceiling in `core/src/ufo/db.py` adds to, and 397 is where this instance
  # refuses. Between them is the fleet holding connections it did not budget for.
  widget {
    timeseries_definition {
      title = "connections (fleet budget 268, refused at 397)"
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

resource "datadog_dashboard" "model_latency" {
  title       = "ufo testing model latency"
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
        q            = "p50:ufo.model_first_event_ms{env:testing} by {provider}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.model_first_event_ms{env:testing} by {provider}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "whole round by provider"
      request {
        q            = "p50:ufo.model_round_ms{env:testing,!error_class:*} by {provider}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.model_round_ms{env:testing,!error_class:*} by {provider}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "generation rate by provider (tokens/s)"
      request {
        q            = "sum:ufo.model_round_tokens_total{env:testing,kind:output} by {provider}.as_count() / (sum:ufo.model_round_ms{env:testing,!error_class:*} by {provider} / 1000)"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "rounds by provider"
      request {
        q            = "count:ufo.model_round_ms{env:testing,!error_class:*} by {provider}"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "failed rounds by provider and class"
      request {
        q            = "count:ufo.model_round_ms{env:testing,error_class:*} by {provider,error_class}"
        display_type = "bars"
      }
    }
  }

  widget {
    toplist_definition {
      title = "first token wait by model"
      request {
        q = "p95:ufo.model_first_event_ms{env:testing} by {model,provider}"
      }
    }
  }
}

# Where a turn spends its wall clock, and what it spent it on. The four `_ms` distributions arrive as
# sketches and answer percentiles because `metrics.tf` enables them.
#
# The board is ordered as the question is asked: the turn first, then the model rounds inside it, then
# the tool calls between them.

resource "datadog_dashboard" "turns" {
  title       = "ufo turns"
  layout_type = "ordered"

  # Every query scopes to `$env` rather than a literal, so the board reads either fleet.
  template_variable {
    name     = "env"
    prefix   = "env"
    defaults = ["testing"]
  }

  widget {
    note_definition {
      content          = <<-EOT
        Everything from `turn_ms` and `turn_rounds_total` is per execution, not per turn: a turn that
        parks and resumes, or that a crash re-dispatches, reaches an exit more than once and is
        counted at each one. `turn_terminal_total` is the once-per-turn number, so read "turns by
        terminal status" against the execution graphs rather than adding them together.

        Comparing backends is the model latency board's question, and `provider` is charted there.
      EOT
      background_color = "yellow"
      font_size        = "14"
      text_align       = "left"
      show_tick        = false
    }
  }

  # The only once-per-turn producer on the board: committed terminals, counted behind the transition
  # guard, so a park or a re-dispatch never adds a second one.
  widget {
    timeseries_definition {
      title = "turns by terminal status"
      request {
        q            = "sum:ufo.turn_terminal_total{$env} by {status}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "execution wall clock"
      request {
        q            = "p50:ufo.turn_ms{$env}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.turn_ms{$env}"
        display_type = "line"
      }
      request {
        q            = "p99:ufo.turn_ms{$env}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "executions by exit"
      request {
        q            = "count:ufo.turn_ms{$env} by {status}"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "execution wall clock by exit"
      request {
        q            = "p95:ufo.turn_ms{$env} by {status}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "rounds by execution exit"
      request {
        q            = "sum:ufo.turn_rounds_total{$env} by {status}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "model round wall clock"
      request {
        q            = "p50:ufo.model_round_ms{$env}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.model_round_ms{$env}"
        display_type = "line"
      }
      request {
        q            = "p99:ufo.model_round_ms{$env}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "round wall clock against time to first event"
      request {
        q            = "p95:ufo.model_round_ms{$env}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.model_first_event_ms{$env}"
        display_type = "line"
      }
    }
  }

  # A round that returned carries no `error_class` at all, so the filter is what keeps the healthy
  # population out of a graph about failures rather than under an `N/A` bar that dwarfs them.
  widget {
    timeseries_definition {
      title = "model round failures by error class"
      request {
        q            = "count:ufo.model_round_ms{$env,error_class:*} by {error_class}"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "tokens by kind"
      request {
        q            = "sum:ufo.model_round_tokens_total{$env} by {kind}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "tool call wall clock by tool"
      request {
        q            = "p95:ufo.tool_call_ms{$env} by {tool}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "tool calls by tool"
      request {
        q            = "sum:ufo.tool_call_total{$env} by {tool}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "tool calls by outcome"
      request {
        q            = "sum:ufo.tool_call_total{$env} by {outcome}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "tool call error classes"
      request {
        q            = "sum:ufo.tool_call_total{$env,error_class:*} by {error_class}.as_count()"
        display_type = "bars"
      }
    }
  }
}

# How well the coding agent executes, as distinct from how fast: the turns board answers throughput
# and latency, this one answers completion, tool-call correctness, and effort spent per result.
#
# Every number here is a process proxy. Nothing in `ufo.*` records whether the code the agent wrote
# was correct — no eval score, test result, or review verdict is emitted — so this board catches an
# agent that gives up, loops, or misuses a tool, and is blind to one that confidently ships wrong
# code. That gap is the note's first paragraph because a reader who misses it over-trusts the board.

resource "datadog_dashboard" "coding_quality" {
  title       = "ufo coding agent quality"
  layout_type = "ordered"

  template_variable {
    name     = "env"
    prefix   = "env"
    defaults = ["testing"]
  }

  template_variable {
    name     = "profile"
    prefix   = "profile"
    defaults = ["coding"]
  }

  widget {
    note_definition {
      content          = <<-EOT
        Nothing on this board says the code was correct. There is no eval score, test result, or
        review verdict in `ufo.*`, so every number here is a process proxy: it catches an agent that
        gives up, loops, or misuses a tool, and misses one that ships wrong code confidently.

        Set `$profile` to `coding` or `code_review`. Turns that report no profile are excluded, and
        the last graph is how large that excluded share is — read it before trusting a rate above.
      EOT
      background_color = "yellow"
      font_size        = "14"
      text_align       = "left"
      show_tick        = false
    }
  }

  widget {
    query_value_definition {
      title       = "turn success rate"
      autoscale   = false
      custom_unit = "%"
      precision   = 1
      request {
        q          = "100 * sum:ufo.turn_terminal_total{$env,$profile,status:done}.as_count() / sum:ufo.turn_terminal_total{$env,$profile}.as_count()"
        aggregator = "sum"
        conditional_formats {
          comparator = "<"
          value      = 90
          palette    = "white_on_red"
        }
        conditional_formats {
          comparator = "<"
          value      = 97
          palette    = "white_on_yellow"
        }
        conditional_formats {
          comparator = ">="
          value      = 97
          palette    = "white_on_green"
        }
      }
    }
  }

  # `invalid_call` is the model's fault and the rest are the tool's or the environment's, so the two
  # rates sit apart: one says fix the prompt, the other says fix the tool.
  widget {
    query_value_definition {
      title       = "tool call error rate"
      autoscale   = false
      custom_unit = "%"
      precision   = 2
      request {
        q          = "100 * sum:ufo.tool_call_total{$env,$profile,!outcome:ok}.as_count() / sum:ufo.tool_call_total{$env,$profile}.as_count()"
        aggregator = "sum"
        conditional_formats {
          comparator = ">"
          value      = 5
          palette    = "white_on_red"
        }
        conditional_formats {
          comparator = ">"
          value      = 2
          palette    = "white_on_yellow"
        }
        conditional_formats {
          comparator = "<="
          value      = 2
          palette    = "white_on_green"
        }
      }
    }
  }

  widget {
    query_value_definition {
      title       = "invalid call rate"
      autoscale   = false
      custom_unit = "%"
      precision   = 2
      request {
        q          = "100 * sum:ufo.tool_call_total{$env,$profile,outcome:invalid_call}.as_count() / sum:ufo.tool_call_total{$env,$profile}.as_count()"
        aggregator = "sum"
        conditional_formats {
          comparator = ">"
          value      = 1
          palette    = "white_on_red"
        }
        conditional_formats {
          comparator = ">"
          value      = 0.2
          palette    = "white_on_yellow"
        }
        conditional_formats {
          comparator = "<="
          value      = 0.2
          palette    = "white_on_green"
        }
      }
    }
  }

  # Rounds and tokens are per execution, terminals are per turn, so both ratios read high wherever a
  # turn parked and resumed. They are trend series, not absolute counts.
  widget {
    query_value_definition {
      title     = "rounds per completed turn"
      autoscale = false
      precision = 1
      request {
        q          = "sum:ufo.turn_rounds_total{$env,$profile,status:done}.as_count() / sum:ufo.turn_terminal_total{$env,$profile,status:done}.as_count()"
        aggregator = "sum"
      }
    }
  }

  widget {
    query_value_definition {
      title     = "output tokens per completed turn"
      autoscale = true
      precision = 0
      request {
        q          = "sum:ufo.model_round_tokens_total{$env,$profile,kind:output}.as_count() / sum:ufo.turn_terminal_total{$env,$profile,status:done}.as_count()"
        aggregator = "sum"
      }
    }
  }

  widget {
    query_value_definition {
      title       = "prompt cache hit ratio"
      autoscale   = false
      custom_unit = "%"
      precision   = 1
      request {
        q          = "100 * sum:ufo.model_round_tokens_total{$env,$profile,kind:cache_read}.as_count() / (sum:ufo.model_round_tokens_total{$env,$profile,kind:cache_read}.as_count() + sum:ufo.model_round_tokens_total{$env,$profile,kind:input}.as_count())"
        aggregator = "sum"
        conditional_formats {
          comparator = "<"
          value      = 80
          palette    = "white_on_yellow"
        }
        conditional_formats {
          comparator = ">="
          value      = 80
          palette    = "white_on_green"
        }
      }
    }
  }

  widget {
    timeseries_definition {
      title = "turns by terminal status"
      request {
        q            = "sum:ufo.turn_terminal_total{$env,$profile} by {status}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "turn success rate over time"
      request {
        q            = "100 * sum:ufo.turn_terminal_total{$env,$profile,status:done}.as_count() / sum:ufo.turn_terminal_total{$env,$profile}.as_count()"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "turn failures by error class"
      request {
        q            = "sum:ufo.turn_terminal_total{$env,$profile,status:failed,!error_class:n/a} by {error_class}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "tool calls by outcome"
      request {
        q            = "sum:ufo.tool_call_total{$env,$profile} by {outcome}.as_count()"
        display_type = "bars"
      }
    }
  }

  # `ok` outruns every failure by three orders of magnitude and flattens them off the axis.
  widget {
    timeseries_definition {
      title = "failed tool calls by outcome"
      request {
        q            = "sum:ufo.tool_call_total{$env,$profile,!outcome:ok} by {outcome}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    toplist_definition {
      title = "tool error rate by tool (%)"
      request {
        q = "100 * sum:ufo.tool_call_total{$env,$profile,!outcome:ok} by {tool}.as_count() / sum:ufo.tool_call_total{$env,$profile} by {tool}.as_count()"
        conditional_formats {
          comparator = ">"
          value      = 10
          palette    = "white_on_red"
        }
        conditional_formats {
          comparator = ">"
          value      = 3
          palette    = "white_on_yellow"
        }
        conditional_formats {
          comparator = "<="
          value      = 3
          palette    = "white_on_green"
        }
      }
    }
  }

  widget {
    toplist_definition {
      title = "invalid calls by tool"
      request {
        q = "sum:ufo.tool_call_total{$env,$profile,outcome:invalid_call} by {tool}.as_count()"
      }
    }
  }

  # Rising against a flat success rate is the earliest read on a prompt or tool regression: the same
  # result bought with more work.
  widget {
    timeseries_definition {
      title = "rounds per completed turn over time"
      request {
        q            = "sum:ufo.turn_rounds_total{$env,$profile,status:done}.as_count() / sum:ufo.turn_terminal_total{$env,$profile,status:done}.as_count()"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "rounds by execution exit"
      request {
        q            = "sum:ufo.turn_rounds_total{$env,$profile} by {status}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "tokens by kind"
      request {
        q            = "sum:ufo.model_round_tokens_total{$env,$profile} by {kind}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    note_definition {
      content          = <<-EOT
        Round budget exhausted is the closest signal to the agent giving up. Both should sit at zero.

        Both counters began carrying `profile` with the deploy that tagged them, so a window opened
        before it holds emissions this filter cannot match and these two graphs alone read empty
        while the rest of the board does not.
      EOT
      background_color = "gray"
      font_size        = "13"
      text_align       = "left"
      show_tick        = false
    }
  }

  widget {
    timeseries_definition {
      title = "round budget exhausted"
      request {
        q            = "sum:ufo.turn_round_budget_exhausted_total{$env,$profile}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "context truncation recovered"
      request {
        q            = "sum:ufo.turn_truncation_recovered_total{$env,$profile}.as_count()"
        display_type = "bars"
      }
    }
  }

  # What share of the fleet the profile filter can see. Every rate above is drawn from the tagged
  # slice alone, and untagged turns currently outnumber `coding` ones by more than thirty to one.
  widget {
    toplist_definition {
      title = "turns by profile tag"
      request {
        q = "sum:ufo.turn_terminal_total{$env} by {profile}.as_count()"
      }
    }
  }
}
