# Percentile aggregations on the latency distributions. A distribution reaches Datadog as a sketch
# either way, but p50/p95/p99 are computable only where a tag configuration enables them, so without
# these the sketches arrive and no percentile can be read off them.
#
# A tag configuration also pins which tags stay queryable: a dimension the fleet emits but this list
# omits aggregates away silently. Each list is therefore the dimensions its name declares in
# `HISTOGRAMS` (`core/src/ufo/o11y.py`) plus what the pipeline stamps on every metric — `env` from
# the collector's resource processor, `service` from the OTLP resource, `host` from the exporter.
# The tag keys Datadog currently reports are not the source: those show only the dimensions some
# turn already produced inside the lookback window.
#
# A tag configuration is keyed by metric name alone, so one root owns it: this one, the only root
# the deploy pipeline applies. Both fleets' metrics are configured from here, prod's included.

resource "datadog_metric_tag_configuration" "turn_ms" {
  metric_name         = "ufo.turn_ms"
  metric_type         = "distribution"
  include_percentiles = true
  tags                = ["env", "host", "service", "status"]
}

resource "datadog_metric_tag_configuration" "model_round_ms" {
  metric_name         = "ufo.model_round_ms"
  metric_type         = "distribution"
  include_percentiles = true
  tags                = ["env", "error_class", "host", "model", "provider", "service"]
}

resource "datadog_metric_tag_configuration" "model_first_event_ms" {
  metric_name         = "ufo.model_first_event_ms"
  metric_type         = "distribution"
  include_percentiles = true
  tags                = ["env", "host", "model", "provider", "service"]
}

resource "datadog_metric_tag_configuration" "tool_call_ms" {
  metric_name         = "ufo.tool_call_ms"
  metric_type         = "distribution"
  include_percentiles = true
  tags                = ["env", "error_class", "host", "outcome", "service", "tool"]
}
