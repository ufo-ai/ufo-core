locals {
  latency_metrics = [
    "ufo.db_tx_acquire_ms",
    "ufo.model_first_visible_event_ms",
    "ufo.model_provider_start_ms",
    "ufo.model_round_ms",
    "ufo.onboarding_step_latency_ms",
    "ufo.tool_call_ms",
    "ufo.turn_dispatch_wait_ms",
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

resource "datadog_metric_tag_configuration" "turn_dispatch_wait_ms" {
  depends_on          = [terraform_data.metric_seed]
  metric_name         = "ufo.turn_dispatch_wait_ms"
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

resource "datadog_metric_metadata" "turn_dispatch_wait_ms" {
  depends_on = [terraform_data.metric_seed]
  metric     = "ufo.turn_dispatch_wait_ms"
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
