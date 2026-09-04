# Percentile aggregations on the latency distributions. A distribution reaches Datadog as a sketch
# either way, but p50/p95/p99 are computable only where a tag configuration enables them, so without
# these the sketches arrive and no percentile can be read off them.
#
# A tag configuration also pins which tags stay queryable: a dimension the fleet emits but this list
# omits aggregates away silently. Each list is therefore the dimensions its name declares in
# `HISTOGRAMS` (`core/src/ufo/harness/o11y.py`) plus what the pipeline stamps on every metric — `env` from
# the collector's resource processor, `service` from the OTLP resource, `host` from the exporter.
# The tag keys Datadog currently reports are not the source: those show only the dimensions some
# turn already produced inside the lookback window.
#
# A tag configuration is keyed by metric name alone, so one root owns it: this one, the only root
# the deploy pipeline applies. Both fleets' metrics are configured from here, prod's included.
#
# Datadog refuses to configure tags on a distribution metric it holds no point for, and answers a
# metadata write for a name it holds no point for with 404. A new histogram's first point comes from
# the fleet build the same apply rolls out — so every resource below that names a metric, the tag
# configurations and the metadata alike, waits on a seed that creates its name. The seed submits one
# point per name and only where Datadog holds none, so it runs on the apply that introduces a metric
# and never again.
#
# The seed is keyed by name, so renaming or removing a histogram plans a delete of the instance the
# old name held, and a failed seed provisioner taints its instance so every later plan proposes the
# replace. `terraform_data` is regenerable to `.github/scripts/terraform_plan_guard.py` for both:
# it holds only the name in state and creates nothing outside it, so the deploy plans either
# cleanly, and a transient seed failure clears on the next apply rather than deadlocking the
# pipeline on a manual `terraform untaint`.
locals {
  latency_metrics = [
    "ufo.db_tx_acquire_ms",
    "ufo.model_first_visible_event_ms",
    "ufo.model_provider_start_ms",
    "ufo.model_round_ms",
    "ufo.onboarding_step_latency_ms",
    "ufo.tool_call_ms",
    "ufo.turn_ms",
    "ufo.turn_slot_wait_ms",
  ]
}

resource "terraform_data" "metric_seed" {
  for_each = toset(local.latency_metrics)
  input    = each.key

  provisioner "local-exec" {
    command = "python3 ${path.module}/seed_distribution_metrics.py ${each.key}"
  }
}

resource "datadog_metric_tag_configuration" "db_tx_acquire_ms" {
  depends_on          = [terraform_data.metric_seed]
  metric_name         = "ufo.db_tx_acquire_ms"
  metric_type         = "distribution"
  include_percentiles = true
  tags                = ["env", "host", "path", "service"]
}

resource "datadog_metric_tag_configuration" "turn_slot_wait_ms" {
  depends_on          = [terraform_data.metric_seed]
  metric_name         = "ufo.turn_slot_wait_ms"
  metric_type         = "distribution"
  include_percentiles = true
  tags                = ["env", "host", "service"]
}

resource "datadog_metric_tag_configuration" "turn_ms" {
  depends_on          = [terraform_data.metric_seed]
  metric_name         = "ufo.turn_ms"
  metric_type         = "distribution"
  include_percentiles = true
  tags                = ["env", "host", "profile", "service", "status"]
}

resource "datadog_metric_tag_configuration" "model_round_ms" {
  depends_on          = [terraform_data.metric_seed]
  metric_name         = "ufo.model_round_ms"
  metric_type         = "distribution"
  include_percentiles = true
  tags                = ["env", "error_class", "host", "job", "model", "profile", "provider", "service"]
}

resource "datadog_metric_tag_configuration" "model_first_visible_event_ms" {
  depends_on          = [terraform_data.metric_seed]
  metric_name         = "ufo.model_first_visible_event_ms"
  metric_type         = "distribution"
  include_percentiles = true
  tags                = ["conversation_ttl", "env", "gap", "host", "model", "profile", "provider", "result", "round", "service"]
}

resource "datadog_metric_tag_configuration" "model_provider_start_ms" {
  depends_on          = [terraform_data.metric_seed]
  metric_name         = "ufo.model_provider_start_ms"
  metric_type         = "distribution"
  include_percentiles = true
  tags                = ["conversation_ttl", "env", "gap", "host", "model", "profile", "provider", "result", "round", "service"]
}

resource "datadog_metric_tag_configuration" "tool_call_ms" {
  depends_on          = [terraform_data.metric_seed]
  metric_name         = "ufo.tool_call_ms"
  metric_type         = "distribution"
  include_percentiles = true
  tags = [
    "binding",
    "call",
    "contributor",
    "env",
    "error_class",
    "host",
    "kind",
    "outcome",
    "profile",
    "service",
    "tool",
  ]
}

resource "datadog_metric_tag_configuration" "onboarding_step_latency_ms" {
  depends_on          = [terraform_data.metric_seed]
  metric_name         = "ufo.onboarding_step_latency_ms"
  metric_type         = "distribution"
  include_percentiles = true
  tags                = ["env", "host", "service", "status", "step", "surface"]
}

# The unit each latency distribution is in. Datadog does not read it off the OTLP payload, so without
# this a wall clock renders as a bare number — "1150000" rather than "19min" — on every graph reading
# them.
#
# `type` is declared `gauge` against a distribution deliberately. The provider reads the type back as
# `gauge` whatever the metric is and stores that, while the field is optional and not computed, so
# any other value — or none — leaves `~ type` in every plan of this root forever, applied
# unattended by `deploy.yml`. Measured at provider 3.91.0: `gauge` plans clean, `distribution`
# re-diffs, and a fresh create with `gauge` leaves the API reporting `distribution` unchanged, so
# this settles the plan without touching what Datadog stores.
#
# Each block waits on the seed for the same reason the tag configurations do: Datadog answers a
# metadata write for a metric name it holds no point for with 404, so without the wait the apply
# that adds a histogram fails here instead.
resource "datadog_metric_metadata" "db_tx_acquire_ms" {
  depends_on = [terraform_data.metric_seed]
  metric     = "ufo.db_tx_acquire_ms"
  type       = "gauge"
  unit       = "millisecond"
}

resource "datadog_metric_metadata" "turn_slot_wait_ms" {
  depends_on = [terraform_data.metric_seed]
  metric     = "ufo.turn_slot_wait_ms"
  type       = "gauge"
  unit       = "millisecond"
}

resource "datadog_metric_metadata" "turn_ms" {
  depends_on = [terraform_data.metric_seed]
  metric     = "ufo.turn_ms"
  type       = "gauge"
  unit       = "millisecond"
}

resource "datadog_metric_metadata" "model_round_ms" {
  depends_on = [terraform_data.metric_seed]
  metric     = "ufo.model_round_ms"
  type       = "gauge"
  unit       = "millisecond"
}

resource "datadog_metric_metadata" "model_first_visible_event_ms" {
  depends_on = [terraform_data.metric_seed]
  metric     = "ufo.model_first_visible_event_ms"
  type       = "gauge"
  unit       = "millisecond"
}

resource "datadog_metric_metadata" "model_provider_start_ms" {
  depends_on = [terraform_data.metric_seed]
  metric     = "ufo.model_provider_start_ms"
  type       = "gauge"
  unit       = "millisecond"
}

resource "datadog_metric_metadata" "tool_call_ms" {
  depends_on = [terraform_data.metric_seed]
  metric     = "ufo.tool_call_ms"
  type       = "gauge"
  unit       = "millisecond"
}

resource "datadog_metric_metadata" "onboarding_step_latency_ms" {
  depends_on = [terraform_data.metric_seed]
  metric     = "ufo.onboarding_step_latency_ms"
  type       = "gauge"
  unit       = "millisecond"
}
