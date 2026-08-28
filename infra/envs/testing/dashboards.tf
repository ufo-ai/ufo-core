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

# The count behind the two `report_problem` widgets at the foot of the turns board.
# `report_problem` emits one log record per report and nothing else, so the class and the cost of a
# report are log fields; this derives a counter from them, because a log widget columns and groups
# only by a Datadog-side facet and no terraform resource creates one. Grouping a log-based metric
# needs no facet, so the board's shape is provisioned here rather than made by hand in the UI and
# kept alive there. Only the three dimensions the board reads are grouped: `category` and `impact`
# by their widgets, `env` by the board's template variable.

resource "datadog_logs_metric" "problem_reported" {
  name = "ufo.problem_reported"
  compute {
    aggregation_type = "count"
  }
  filter {
    query = "service:ufo \"problem.reported\""
  }
  group_by {
    path     = "@category"
    tag_name = "category"
  }
  group_by {
    path     = "@impact"
    tag_name = "impact"
  }
  group_by {
    path     = "env"
    tag_name = "env"
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

  # An agent's own report of a problem it could not repair, from `report_problem`. It sits on this
  # board because a report is produced inside a turn and read while watching the fleet, and because
  # nothing else counts one: `tool_call_total{tool:report_problem}` says a call happened, never that
  # a workspace is broken — a refusal and a landed report are the same count there.
  #
  # By category, because that is what a reader routes on: which subsystem is generating reports this
  # week decides who picks them up.

  widget {
    timeseries_definition {
      title = "problems reported by category"
      request {
        q            = "sum:ufo.problem_reported{$env} by {category}.as_count()"
        display_type = "bars"
      }
    }
  }

  # And by what it cost the member, which is a different question from how many: one critical report
  # outranks a week of minor ones, and the two series move independently.

  widget {
    timeseries_definition {
      title = "problems reported by impact"
      request {
        q            = "sum:ufo.problem_reported{$env} by {impact}.as_count()"
        display_type = "bars"
      }
    }
  }

  # The reports themselves, because the counts alone cannot be acted on. Each row carries the
  # workspace, the agent's own account of the problem, its category and impact, the origin (`fault`,
  # or `member_request` where a member asked us to be told), and a debugger link scoped to the
  # reporting turn — a click on the row opens the transcript. It carries no columns and no grouping:
  # a log widget columns and groups only by a Datadog-side facet, and no terraform resource creates
  # one, so the board reads the whole fleet's reports as one list and the counts above carry the
  # grouping.
  widget {
    log_stream_definition {
      title               = "what they reported"
      query               = "$env service:ufo \"problem.reported\""
      indexes             = ["*"]
      show_date_column    = true
      show_message_column = true
      message_display     = "expanded-md"
      sort {
        column = "time"
        order  = "desc"
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
  # `edit_handler`, plus everything `ufo fs` rejects — an `old_string` matching nothing, an anchor
  # matching more than once, a missing file, a path leaving the workspace. `run_ufo_fs` turns each of
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

  template_variable {
    name             = "target_model"
    prefix           = "target_model"
    defaults         = ["claude-opus-5"]
    available_values = ["claude-opus-5", "z-ai/glm-5.3", "z-ai/glm-5.3-flash"]
  }

  widget {
    timeseries_definition {
      title = "pass rate across every suite"
      request {
        q            = "sum:ufo.evals.cases_passed{$mode,$target_model} / sum:ufo.evals.cases_scored{$mode,$target_model}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "pass rate by suite"
      request {
        q            = "sum:ufo.evals.cases_passed{$mode,$target_model} by {suite} / sum:ufo.evals.cases_scored{$mode,$target_model} by {suite}"
        display_type = "line"
      }
    }
  }

  widget {
    toplist_definition {
      title = "cases failing last night, by suite"
      request {
        q = "top(sum:ufo.evals.cases_scored{$mode,$target_model} by {suite} - sum:ufo.evals.cases_passed{$mode,$target_model} by {suite}, 25, 'last', 'desc')"
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

# The sandbox layer, by failure rather than by resource. A container fails in four distinguishable
# ways and each has its own counter, so this board's job is to say which one happened — the
# signatures are close enough that reading the wrong one sends the next hour in the wrong direction.
# A container that stopped answering returns nothing and every later command pays its whole
# deadline, which arrives as `exec timeouts` rather than as an error, and a filesystem with no room
# left produces that same silence for an entirely different reason.
#
# What this board cannot show: nothing emits a gauge for a container's memory or disk. Every series
# here is an event counter, so a container filling up is visible only once it starts failing. e2b's
# own API holds the resource curves and keeps roughly fifteen minutes of them.
#
# One board for both fleets, in the root the deploy pipeline applies.

resource "datadog_dashboard" "sandbox_health" {
  title       = "ufo sandbox health"
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
        Read `exec timeouts` first, by carrier. A command that outgrew its own budget and a
        container that stopped answering both land there, and the second is the one that matters:
        every later call on that container pays its full deadline, so one silent box accounts for a
        burst rather than a rise. `containers that stopped answering` is the same event caught at
        the launch instead of at the deadline, so the two move together — that one climbing while
        `exec timeouts` stays flat is the detection working, not a regression.

        `resumed unprepared` is a resumed container whose preparation exceeded its window, so its
        commands ran with the CA install, the workspace check and the memory ceiling skipped. A
        handful a week is normal; a rise means resumed boxes are serving turns unprepared.

        `stops that missed the process group` fails no turn by itself, but it leaves the work
        running inside the container, so it is read beside the timeouts that produced it.

        Scratch files resolve to `/var/tmp`, the container's disk, not the memory-backed `/tmp`. A
        test suite costs a few gigabytes of it, so disk is the axis that fills — and neither axis
        has a series here.
      EOT
      background_color = "yellow"
      font_size        = "14"
      text_align       = "left"
      show_tick        = false
    }
  }

  widget {
    query_value_definition {
      title = "exec timeouts"
      request {
        q          = "sum:ufo.sandbox_exec_timeout_total{$env}.as_count()"
        aggregator = "sum"
      }
    }
  }

  widget {
    query_value_definition {
      title = "containers that stopped answering"
      request {
        q          = "sum:ufo.sandbox_unreachable_total{$env}.as_count()"
        aggregator = "sum"
      }
    }
  }

  widget {
    query_value_definition {
      title = "resumed unprepared"
      request {
        q          = "sum:ufo.sandbox_prepare_deferred_total{$env}.as_count()"
        aggregator = "sum"
      }
    }
  }

  widget {
    query_value_definition {
      title = "stops that missed the process group"
      request {
        q          = "sum:ufo.sandbox_exec_stop_failed_total{$env}.as_count()"
        aggregator = "sum"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "exec timeouts by carrier"
      request {
        q            = "sum:ufo.sandbox_exec_timeout_total{$env} by {carrier}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "containers that stopped answering, by carrier"
      request {
        q            = "sum:ufo.sandbox_unreachable_total{$env} by {carrier}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "resumed unprepared, and stops that missed the group"
      request {
        q            = "sum:ufo.sandbox_prepare_deferred_total{$env}.as_count()"
        display_type = "bars"
      }
      request {
        q            = "sum:ufo.sandbox_exec_stop_failed_total{$env}.as_count()"
        display_type = "bars"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "bash wall-clock by profile — a container that stopped answering shows in the tail"
      request {
        q            = "p50:ufo.tool_call_ms{$env,tool:bash} by {profile}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.tool_call_ms{$env,tool:bash} by {profile}"
        display_type = "line"
      }
      request {
        q            = "p99:ufo.tool_call_ms{$env,tool:bash} by {profile}"
        display_type = "line"
      }
    }
  }

  widget {
    toplist_definition {
      title = "which tool holds the longest calls"
      request {
        q = "top(p99:ufo.tool_call_ms{$env} by {tool}, 12, 'max', 'desc')"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "turn wall-clock by profile"
      request {
        q            = "p50:ufo.turn_ms{$env} by {profile}"
        display_type = "line"
      }
      request {
        q            = "p95:ufo.turn_ms{$env} by {profile}"
        display_type = "line"
      }
    }
  }

  widget {
    timeseries_definition {
      title = "egress through the proxy, as volume context for the rest"
      request {
        q            = "sum:ufo.sandbox_egress_total{$env} by {dimension}.as_count()"
        display_type = "line"
      }
    }
  }
}

# The product funnel, derived rather than captured. Every number here comes off rows the product
# already writes — a connector grant, an invited member, a charged purchase — so a stage redefined
# next month re-derives itself over the whole history rather than starting from the day it shipped.
#
# `product_census` fires once per workspace per period and increments each stage that workspace has
# reached, so the count of workspaces at a stage is one tick's worth of increments. Every workspace
# count therefore counts with `.as_count()` like the rest of this file and pins the rollup to that
# period, so one bucket holds exactly one tick and reads as the count the census recorded; a bucket
# Datadog sizes for itself holds as many ticks as it is wide, which multiplies the number. `max`
# then picks a whole bucket over the one still filling at the right-hand edge, and the toplists rank
# by the same `max` so a stage reads the same in the big number and in the funnel.
#
# One board for both fleets, in the root the deploy pipeline applies.

locals {
  # `PRODUCT_CENSUS_SECONDS` in `core/src/ufo/product.py`, and held equal to it by
  # `_census_period_failures` in the gates: the census period is the bucket every workspace count on
  # this board is read out of, so a bucket wider than the census fires counts one workspace once per
  # tick it holds and inflates every number here at once.
  product_census_seconds = 600
}

resource "datadog_dashboard" "product" {
  title       = "ufo product"
  layout_type = "ordered"

  template_variable {
    name     = "env"
    prefix   = "env"
    defaults = ["testing"]
  }

  widget {
    note_definition {
      content          = <<-EOT
        Workspace counts are a census, not a running total: a stage counts the workspaces standing
        at it right now, and a workspace that reaches a later stage still counts at every earlier
        one, so the funnel's steps never rise. The stages are seated, connector, invited, app,
        chatted, active_1d, active_7d, paid. `app` counts an app the workspace built for itself: the
        apps a pack ships reach every workspace, and are counted apart from the funnel below. Chats
        and dollars are the other kind of number — events counted as they happen — so a range shows
        their volume over it rather than a standing count.

        What this board cannot say, because the rows are not ours to read:

        - Signup conversion. The gateway deletes a claim that is never verified, so an address
          submitted and abandoned leaves nothing. Acquisition starts at workspace creation.
        - Whether a member arrived through the terminal or the browser, and whether they founded
          their workspace or joined one. Both live in `ufo_control.onboard_claim`, which the fleet
          holds no privilege on.
        - Waitlist size. That table is Cloudflare D1, reachable only by the edge worker.
        - Refunds and operator corrections. `dollars the fleet charged` counts what the fleet
          charged a card; `ufoctl balance credit` writes a purchase in a process that exports no
          metrics, so read a correction off `balance_purchase` rather than off this board.

        A workspace is counted the tick after it changes, and one whose census is still running
        absorbs a tick, so read a step change against the period rather than the minute.
      EOT
      background_color = "yellow"
      font_size        = "14"
      text_align       = "left"
      show_tick        = false
    }
  }

  widget {
    query_value_definition {
      title     = "workspaces"
      autoscale = false
      precision = 0
      request {
        q          = "sum:ufo.product_stage_total{$env,stage:seated}.as_count().rollup(sum, ${local.product_census_seconds})"
        aggregator = "max"
      }
    }
  }

  widget {
    query_value_definition {
      title     = "attached a connector"
      autoscale = false
      precision = 0
      request {
        q          = "sum:ufo.product_stage_total{$env,stage:connector}.as_count().rollup(sum, ${local.product_census_seconds})"
        aggregator = "max"
      }
    }
  }

  widget {
    query_value_definition {
      title     = "invited a teammate"
      autoscale = false
      precision = 0
      request {
        q          = "sum:ufo.product_stage_total{$env,stage:invited}.as_count().rollup(sum, ${local.product_census_seconds})"
        aggregator = "max"
      }
    }
  }

  widget {
    query_value_definition {
      title     = "chatted at least once"
      autoscale = false
      precision = 0
      request {
        q          = "sum:ufo.product_stage_total{$env,stage:chatted}.as_count().rollup(sum, ${local.product_census_seconds})"
        aggregator = "max"
      }
    }
  }

  widget {
    query_value_definition {
      title     = "chatted in the last 7 days"
      autoscale = false
      precision = 0
      request {
        q          = "sum:ufo.product_stage_total{$env,stage:active_7d}.as_count().rollup(sum, ${local.product_census_seconds})"
        aggregator = "max"
      }
    }
  }

  widget {
    query_value_definition {
      title     = "paid"
      autoscale = false
      precision = 0
      request {
        q          = "sum:ufo.product_stage_total{$env,stage:paid}.as_count().rollup(sum, ${local.product_census_seconds})"
        aggregator = "max"
      }
    }
  }

  # The funnel itself. Read downward: each step is a subset of the one above it, so the gap between
  # two rows is where workspaces are stopping.
  widget {
    toplist_definition {
      title = "workspaces by funnel stage"
      request {
        q = "top(sum:ufo.product_stage_total{$env} by {stage}.as_count().rollup(sum, ${local.product_census_seconds}), 8, 'max', 'desc')"
      }
    }
  }

  # What is attached, by the name the row carries rather than a name core holds: the connector's
  # provider, the surface, the credential slot, the app. A connector added to the catalogue appears
  # here with no change to this board.
  widget {
    toplist_definition {
      title = "workspaces holding a connector, by provider"
      request {
        q = "top(sum:ufo.product_attach_total{$env,kind:connector} by {name}.as_count().rollup(sum, ${local.product_census_seconds}), 12, 'max', 'desc')"
      }
    }
  }

  widget {
    toplist_definition {
      title = "workspaces with a surface installed"
      request {
        q = "top(sum:ufo.product_attach_total{$env,kind:surface} by {name}.as_count().rollup(sum, ${local.product_census_seconds}), 12, 'max', 'desc')"
      }
    }
  }

  # An installation routes nothing until a member has proved an address against it, so this is the
  # number that says a workspace can actually be reached on that surface. Read it against the
  # installation count above: the difference is claims nobody finished.
  widget {
    toplist_definition {
      title = "workspaces reachable on a surface"
      request {
        q = "top(sum:ufo.product_attach_total{$env,kind:address} by {name}.as_count().rollup(sum, ${local.product_census_seconds}), 12, 'max', 'desc')"
      }
    }
  }

  # Where GitHub and every bring-your-own-key connector appear: they are credential slots, not
  # connector grants, so they are counted by slot rather than by provider.
  widget {
    toplist_definition {
      title = "workspaces holding a credential, by slot"
      request {
        q = "top(sum:ufo.product_attach_total{$env,kind:credential} by {name}.as_count().rollup(sum, ${local.product_census_seconds}), 12, 'max', 'desc')"
      }
    }
  }

  # A pack provisions its apps into every workspace it covers, so this counts how far each shipped
  # app reached and never what a member adopted: a name standing below the workspace count is a
  # provision that did not land everywhere. The funnel's `app` step answers the other question — an
  # app the workspace made for itself.
  widget {
    toplist_definition {
      title = "workspaces each shipped app reached"
      request {
        q = "top(sum:ufo.product_attach_total{$env,kind:app} by {name}.as_count().rollup(sum, ${local.product_census_seconds}), 12, 'max', 'desc')"
      }
    }
  }

  # Counted at admission, so one row is one turn: a message that folds into a turn already running
  # adds neither.
  widget {
    timeseries_definition {
      title = "member chats by surface"
      request {
        q            = "sum:ufo.admitted_turn_total{$env,admission_source:member} by {surface}.as_count()"
        display_type = "bars"
      }
    }
  }

  # `member` and `intent` are a person acting; `scheduled` and `internal` are the fleet acting on
  # their behalf. Reading the two together is what says whether volume is demand or our own work.
  widget {
    timeseries_definition {
      title = "turns admitted by source"
      request {
        q            = "sum:ufo.admitted_turn_total{$env} by {admission_source}.as_count()"
        display_type = "bars"
      }
    }
  }

  # Money in, off the purchase the ledger kept rather than the call that asked for it and only once
  # its transaction committed, so a rolled-back or redelivered payment is not counted. A signup grant
  # charges nothing and never appears here, and neither does an operator's correction — see the note.
  widget {
    timeseries_definition {
      title = "dollars the fleet charged"
      request {
        q            = "sum:ufo.balance_charged_micro_usd_total{$env}.as_count() / 1000000"
        display_type = "bars"
      }
    }
  }
}
