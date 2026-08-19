# The database, from both sides at once. CloudWatch says what Postgres was doing; it cannot say
# whether a turn reached it, because a connection that never arrives is not a connection Postgres
# ever sees. `ufo.db_tx_unavailable_total` is that other side, and it sits on this board next to the
# instance's own health so the pair is read together — the failure in #834 was invisible on every
# database-side graph while a turn died waiting on a connect.
#
# One board for both fleets, in the root the deploy pipeline applies.

resource "datadog_dashboard" "database" {
  title       = "ufo database"
  layout_type = "ordered"

  # Every `ufo.*` query scopes to `$env` rather than a literal, so the board reads either fleet.
  template_variable {
    name             = "env"
    prefix           = "env"
    defaults         = ["testing"]
    available_values = ["testing", "prod"]
  }

  # The CloudWatch half cannot ride `$env`: the AWS integration carries the instance's own tags,
  # where the environment reads `ufo-testing` on one fleet and `prod` on the other, so nothing there
  # matches the `env` tag the OTLP pipeline stamps. The RDS queries key on the instance identifier
  # instead, as every monitor does, and the presets below move both selectors as one.
  template_variable {
    name             = "dbinstance"
    prefix           = "dbinstanceidentifier"
    defaults         = ["ufo-testing-postgres"]
    available_values = ["ufo-testing-postgres", "prod-postgres"]
  }

  template_variable_preset {
    name = "testing"
    template_variable {
      name   = "env"
      values = ["testing"]
    }
    template_variable {
      name   = "dbinstance"
      values = ["ufo-testing-postgres"]
    }
  }

  template_variable_preset {
    name = "prod"
    template_variable {
      name   = "env"
      values = ["prod"]
    }
    template_variable {
      name   = "dbinstance"
      values = ["prod-postgres"]
    }
  }

  widget {
    note_definition {
      content          = <<-EOT
        Every event loop holds its own pool, so a transaction waits twice: for a slot, then for a
        dial only if no warm connection is free. Read the first three graphs before the rest — the
        instance can look healthy on every CloudWatch series while turns die waiting on a connection
        Postgres never received, and the pool graphs are what say whether the wait was ours or the
        network's. The CloudWatch series land minutes after the minute they describe; they answer
        capacity questions, never incident ones.

        Read `connections` against the fleet the selector holds. On the testing fleet, 268 is what
        every pool ceiling in `core/src/ufo/db.py` adds to and 397 is where that instance refuses;
        between them is the fleet holding connections it did not budget for. The prod instance is a
        larger class with its own ceiling, and its connections monitor is critical at 300 against
        testing's 350.
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
        q            = "sum:ufo.db_tx_unavailable_total{$env} by {path,error_class}.as_count()"
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
        q            = "p95:ufo.db_tx_acquire_ms{$env} by {path}"
        display_type = "line"
      }
      request {
        q            = "p99:ufo.db_tx_acquire_ms{$env} by {path}"
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
        q            = "sum:ufo.db_pool_exhausted_total{$env} by {path}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "connections"
      request {
        q            = "avg:aws.rds.database_connections{$dbinstance}"
        display_type = "line"
      }
    }
  }

  # The graph that arrives before the charge, by a wide margin: the balance drains while CPU holds
  # above the baseline. Burstable classes only, so it reports for the testing `db.t4g.medium` and
  # reads empty under the prod preset, where the instance is a `db.m6g.large`.
  widget {
    timeseries_definition {
      title = "cpu credit balance"
      request {
        q            = "avg:aws.rds.cpucredit_balance{$dbinstance}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "cpu utilization"
      request {
        q            = "avg:aws.rds.cpuutilization{$dbinstance}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "freeable memory"
      request {
        q            = "avg:aws.rds.freeable_memory{$dbinstance}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "read and write latency"
      request {
        q            = "avg:aws.rds.read_latency{$dbinstance}"
        display_type = "line"
      }
      request {
        q            = "avg:aws.rds.write_latency{$dbinstance}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "free storage space"
      request {
        q            = "avg:aws.rds.free_storage_space{$dbinstance}"
        display_type = "line"
      }
    }
  }
}

resource "datadog_dashboard" "model_latency" {
  title       = "ufo model latency"
  layout_type = "ordered"

  # Every query scopes to `$env` rather than a literal, so the board reads either fleet.
  template_variable {
    name             = "env"
    prefix           = "env"
    defaults         = ["testing"]
    available_values = ["testing", "prod"]
  }

  widget {
    note_definition {
      content          = <<-EOT
        `provider response start` measures transport and provider admission. `first visible event`
        adds hidden reasoning and any retry before text or a tool call. `whole round` adds output
        generation. Read all three by model and profile before changing a route.

        Read a difference only where `rounds by provider` is high enough to carry a percentile and
        `failed rounds` and `provider retries` are flat.

        The `background job` graphs are those same series for the model calls made off a turn:
        `profile:background`, each tagged with the `job` that made it — memory consolidation, fact
        derivation, chat titles, the ambient reply gate. One configured model serves all of them, so
        a rise there is that job's own volume or payload, never a route change.
      EOT
      background_color = "yellow"
      font_size        = "14"
      text_align       = "left"
      show_tick        = false
    }
  }

  widget {
    timeseries_definition {
      title = "provider response start by model and profile"
      request {
        q            = "p50:ufo.model_provider_start_ms{$env} by {provider,model,profile}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.model_provider_start_ms{$env} by {provider,model,profile}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "first visible event wait by model and profile"
      request {
        q            = "p50:ufo.model_first_visible_event_ms{$env} by {provider,model,profile}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.model_first_visible_event_ms{$env} by {provider,model,profile}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "whole round by model and profile"
      request {
        q            = "p50:ufo.model_round_ms{$env,!error_class:*} by {provider,model,profile}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.model_round_ms{$env,!error_class:*} by {provider,model,profile}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "active model rounds"
      request {
        q            = "sum:ufo.model_round_active{$env} by {provider,model,profile}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "provider retries"
      request {
        q            = "sum:ufo.model_provider_retry_total{$env} by {provider,model,kind}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "generation rate by provider (tokens/s)"
      request {
        q            = "sum:ufo.model_round_tokens_total{$env,kind:output} by {provider}.as_count() / (sum:ufo.model_round_ms{$env,!error_class:*} by {provider} / 1000)"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "rounds by provider"
      request {
        q            = "count:ufo.model_round_ms{$env,!error_class:*} by {provider,model,profile}"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "failed rounds by provider and class"
      request {
        q            = "count:ufo.model_round_ms{$env,error_class:*} by {provider,model,profile,error_class}"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "background job tokens by job and kind"
      request {
        q            = "sum:ufo.model_round_tokens_total{$env,profile:background} by {job,kind}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "background job whole round by job"
      request {
        q            = "p50:ufo.model_round_ms{$env,profile:background,!error_class:*} by {job,model}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.model_round_ms{$env,profile:background,!error_class:*} by {job,model}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "background job rounds by job and class"
      request {
        q            = "count:ufo.model_round_ms{$env,profile:background} by {job,error_class}"
        display_type = "bars"
      }
    }
  }

}

# Where a turn spends its wall clock, and what it spent it on. The latency distributions arrive as
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
      title = "round wall clock against first visible event"
      request {
        q            = "p95:ufo.model_round_ms{$env}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.model_first_visible_event_ms{$env}"
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

        Set `$profile` to `coding`. Turns that report no profile are excluded, and the last graph
        is how large that excluded share is — read it before trusting a rate above.
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

  # The one number here drawn from the agent's own work rather than from the machinery around it:
  # `bash_handler` marks any non-zero exit an error, so this is the share of commands the agent ran
  # that came back red — a test, a build, a lint it invoked on the code it had just written.
  #
  # Uncoloured deliberately. Low is not good: an agent that never sees a failing command is an agent
  # that never ran its own tests, and the healthy shape is a rate that exists and then falls within a
  # turn as failures get fixed. A `grep` with no match exits non-zero too, so read this as a ceiling.
  widget {
    query_value_definition {
      title       = "failed commands"
      autoscale   = false
      custom_unit = "%"
      precision   = 1
      request {
        q          = "100 * sum:ufo.tool_call_total{$env,$profile,tool:bash,outcome:handler_error}.as_count() / sum:ufo.tool_call_total{$env,$profile,tool:bash}.as_count()"
        aggregator = "sum"
      }
    }
  }

  # Every way an edit gets refused, in one number: the read-before-edit precondition in
  # `edit_handler`, plus everything `sbxfs` rejects — an `old_string` matching nothing, an anchor
  # matching more than once, a missing file, a path leaving the workspace. `run_sbxfs` turns each of
  # those into the same `ValueError`, so this cannot separate the agent misremembering a file's
  # contents from the agent skipping the read that would have shown them. Splitting them needs a
  # distinct error class on the precondition; until then read the total, not a cause.
  #
  # All of it is the agent's doing rather than the tool failing, which is why it sits here.
  widget {
    query_value_definition {
      title       = "edits the tool refused"
      autoscale   = false
      custom_unit = "%"
      precision   = 1
      request {
        q          = "100 * sum:ufo.tool_call_total{$env,$profile,tool:edit,outcome:handler_raised,error_class:valueerror}.as_count() / sum:ufo.tool_call_total{$env,$profile,tool:edit}.as_count()"
        aggregator = "sum"
        conditional_formats {
          comparator = ">"
          value      = 10
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
    note_definition {
      content          = <<-EOT
        The failure outcomes do not share a culprit, and the next two graphs separate them. The
        outcome alone does not decide it — `error_class` is half the answer.

        `handler_error` is a tool reporting that what it ran did not work; on `bash` and `js_repl`
        that is the agent's own code failing. `handler_raised` splits: a `ValueError` is a handler
        refusing the arguments or preconditions it was handed, which is the agent's doing, while a
        `RuntimeError` or `OSError` is the machinery breaking underneath and belongs to whoever
        owns the tool. `invalid_call` is a call that failed schema validation before any handler
        saw it — a prompt or schema problem, never a tool one.

        So the agent's own failures are `handler_error` plus the `ValueError` half of
        `handler_raised`, which is what the graph below counts. Reading `handler_error` alone would
        miss `edit` entirely: it never returns a failing result, it raises.
      EOT
      background_color = "white"
      font_size        = "13"
      text_align       = "left"
      show_tick        = false
    }
  }

  # Split from the rate below because that one folds infrastructure faults in with these: a tool the
  # agent drove into a wall reads the same there as one that fell over on its own.
  #
  # The `ValueError` arm is not decoration. `edit` never returns a failing result — it raises — so an
  # `outcome:handler_error` filter alone reports zero for the tool the agent gets wrong most often.
  widget {
    toplist_definition {
      title = "the agent's own actions that failed, by tool"
      request {
        q = "sum:ufo.tool_call_total{$env AND $profile AND (outcome:handler_error OR (outcome:handler_raised AND error_class:valueerror))} by {tool}.as_count()"
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

resource "datadog_dashboard" "prompt_cache" {
  title       = "ufo prompt cache"
  layout_type = "ordered"

  template_variable {
    name     = "env"
    prefix   = "env"
    defaults = ["prod"]
  }

  template_variable {
    name     = "profile"
    prefix   = "profile"
    defaults = ["main"]
  }

  template_variable {
    name     = "provider"
    prefix   = "provider"
    defaults = ["*"]
  }

  widget {
    note_definition {
      content          = <<-EOT
        Anthropic charges 1.25x base input for a 5-minute write, 2x for a 1-hour write, and
        0.1x for a read. Tools and the system prompt use 1 hour. A non-spawned turn's changing
        conversation tail uses 1 hour. Spawned turns use 5 minutes. Auxiliary calls such as find,
        compaction, titles, classifiers, and jobs use 5 minutes. A read refreshes the entry.

        `gap` is the time from the prior turn's terminal write to this turn's first model request.
        `within_turn` marks later rounds and `new` marks a conversation's first turn. A hit after
        5 minutes proves that the 1-hour window had value. Later-round reads show the value of the
        first round's write inside one turn.

        The 1-hour write premium over a 5-minute write is 0.75x base input. A protected read avoids
        a 1.25x rewrite and costs 0.1x, so it saves 1.15x. The extended window pays for itself above
        65.2 read tokens per 100 write tokens, before latency and rate limit value.

        The board does not link a first-round hit to the prior turn's round count. Use it to select
        a policy, then compare that policy with the same token and visible-event measures.
      EOT
      background_color = "yellow"
      font_size        = "14"
      text_align       = "left"
      show_tick        = false
    }
  }

  widget {
    query_value_definition {
      title       = "completed executions with one model round"
      autoscale   = false
      custom_unit = "%"
      precision   = 1
      request {
        q          = "100 * sum:ufo.turn_round_path_total{$env,$profile,status:done,path:single}.as_count() / sum:ufo.turn_round_path_total{$env,$profile,status:done}.as_count()"
        aggregator = "sum"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "first model requests by idle gap"
      request {
        q            = "sum:ufo.model_cache_round_total{$env,$profile,$provider,round:first} by {gap}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    query_value_definition {
      title       = "extended-window token return"
      autoscale   = false
      custom_unit = "%"
      precision   = 1
      request {
        q          = "100 * sum:ufo.model_cache_tokens_total{$env,$profile,provider:anthropic,round:first,gap:5m_1h,kind:cache_read}.as_count() / sum:ufo.model_cache_tokens_total{$env,$profile,provider:anthropic,kind:cache_write_1h}.as_count()"
        aggregator = "sum"
        conditional_formats {
          comparator = ">="
          value      = 65.2
          palette    = "white_on_green"
        }
        conditional_formats {
          comparator = "<"
          value      = 65.2
          palette    = "white_on_yellow"
        }
      }
    }
  }

  widget {
    timeseries_definition {
      title = "first-round prompt cache hit ratio by idle gap"
      request {
        q            = "100 * sum:ufo.model_cache_tokens_total{$env,$profile,$provider,round:first,kind:cache_read} by {gap}.as_count() / (sum:ufo.model_cache_tokens_total{$env,$profile,$provider,round:first,kind:cache_read} by {gap}.as_count() + sum:ufo.model_cache_tokens_total{$env,$profile,$provider,round:first,kind:input} by {gap}.as_count() + sum:ufo.model_cache_tokens_total{$env,$profile,$provider,round:first,kind:cache_write_5m} by {gap}.as_count() + sum:ufo.model_cache_tokens_total{$env,$profile,$provider,round:first,kind:cache_write_1h} by {gap}.as_count())"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "cache tokens by round, kind, and conversation TTL"
      request {
        q            = "sum:ufo.model_cache_tokens_total{$env,$profile,$provider} by {round,kind,conversation_ttl}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "first visible event wait by cache result and idle gap"
      request {
        q            = "p50:ufo.model_first_visible_event_ms{$env,$profile,$provider,round:first} by {result,gap}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.model_first_visible_event_ms{$env,$profile,$provider,round:first} by {result,gap}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "model requests by round and conversation TTL"
      request {
        q            = "sum:ufo.model_cache_round_total{$env,$profile,$provider} by {round,conversation_ttl}.as_count()"
        display_type = "bars"
      }
    }
  }
}

# The nightly eval sweep's scores over time. The sweep submits counts, not rates, so every query
# here divides: averaging per-suite rates would weight a one-case suite like a sixty-nine-case one.
# The points arrive once a night from `.github/workflows/evals-nightly.yml`, which is sparse enough
# that a line joins two points a day apart — read a step as one night's result, never as a trend
# between them, and read the digest events below before reading a drop as a regression, since a
# suite whose cases changed is a different test under the same name.

resource "datadog_dashboard" "evals" {
  title       = "ufo evals"
  layout_type = "ordered"

  # `sweep` is the nightly run of every suite; `smoke` is the proving subset a dispatch asks for.
  # They are different populations, so nothing here mixes them.
  template_variable {
    name             = "mode"
    prefix           = "mode"
    defaults         = ["sweep"]
    available_values = ["sweep", "smoke"]
  }

  widget {
    timeseries_definition {
      title = "pass rate across every suite"
      request {
        q            = "sum:ufo.evals.cases_passed{$mode} / sum:ufo.evals.cases_scored{$mode}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "pass rate by suite"
      request {
        q            = "sum:ufo.evals.cases_passed{$mode} by {suite} / sum:ufo.evals.cases_scored{$mode} by {suite}"
        display_type = "line"
      }
    }
  }

  widget {
    toplist_definition {
      title = "cases failing last night, by suite"
      request {
        q = "top(sum:ufo.evals.cases_scored{$mode} by {suite}.last('1d') - sum:ufo.evals.cases_passed{$mode} by {suite}.last('1d'), 25, 'max', 'desc')"
      }
    }
  }

  widget {
    event_stream_definition {
      title = "suite digests, per sweep"
      query = "source:github ufo evals"
    }
  }
}
