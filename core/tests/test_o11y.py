import logging
from uuid import uuid4

import pytest
from opentelemetry import _logs, metrics, trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ufo import o11y
from ufo.ext.loader import HOOK_TIMEOUT_SECONDS
from ufo.loop.engine import MAIN_ROUND_LIMIT
from ufo.models.anthropic import (
    INITIAL_RETRY_DELAY_SECONDS,
    MAX_EMPTY_PROVIDER_RETRIES,
    MAX_PROVIDER_RETRIES,
    MAX_RETRY_DELAY_SECONDS,
    PROVIDER_TIMEOUT_SECONDS,
)
from ufo.tools.builtins import ARTIFACT_PUT_TIMEOUT_SECONDS, SHARE_PREFLIGHT_TIMEOUT_SECONDS
from ufo.workspace import ws

BRACKETING_HOOKS = ("pre_tool_use", "post_tool_use")


def test_redact_payload_drops_sensitive_keys_at_depth():
    payload = {
        "turn_id": "abc",
        "status": "done",
        "token": "leak",
        "Model-Content": "leak",
        "tool_payload": "leak",
        "nested": {"credentials": ["leak"], "count": 2},
        "items": [{"secret": "leak", "ok": True}],
    }
    assert o11y.redact_payload(payload) == {
        "turn_id": "abc",
        "status": "done",
        "nested": {"count": 2},
        "items": [{"ok": True}],
    }


def test_redact_value_stringifies_non_json_scalars():
    value = uuid4()
    assert o11y.redact_value(value) == str(value)


def test_log_carries_redacted_fields(caplog):
    with caplog.at_level(logging.INFO, logger="ufo"):
        o11y.log("turn.started", turn_id="abc", prompt="leak")
    record = caplog.records[-1]
    assert record.getMessage() == "turn.started"
    assert record.ufo == {"turn_id": "abc"}


def test_emit_metric_rejects_unregistered_names():
    with pytest.raises(ValueError, match="unknown metric"):
        o11y.emit_metric("model_call_total")


def test_emit_metric_caches_instruments():
    o11y.emit_metric("turn_started_total")
    instrument = o11y._counters["turn_started_total"]
    o11y.emit_metric("turn_started_total", status="done")
    assert o11y._counters["turn_started_total"] is instrument


def test_emit_histogram_rejects_unregistered_names():
    with pytest.raises(ValueError, match="unknown histogram"):
        o11y.emit_histogram("model_call_ms", 1)


def test_emit_histogram_caches_instruments():
    o11y.emit_histogram("model_round_ms", 12)
    instrument = o11y._histograms["model_round_ms"]
    o11y.emit_histogram("model_round_ms", 34, model="claude-opus-4-8")
    assert o11y._histograms["model_round_ms"] is instrument


def _reader(monkeypatch) -> InMemoryMetricReader:
    """Route what the emitters record onto a reader the test reads back, installing no global meter
    provider. Both instrument caches hold instruments bound to the provider they were created
    against, so they are emptied alongside it."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(o11y.metrics, "get_meter", provider.get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})
    return reader


def _attributes(reader: InMemoryMetricReader) -> dict[str, dict[str, str]]:
    return {
        metric.name: dict(metric.data.data_points[0].attributes)
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def test_logs_and_spans_carry_the_workspace_and_metrics_carry_none(monkeypatch, caplog):
    """A workspace on a metric is one time series per workspace, multiplied by every other dimension
    — the one dimension that grows with the customer base. A record and a span cost their own
    storage and are read one at a time, so the scope tags both: the same turn is findable by
    workspace in logs and traces, and its metrics aggregate across the fleet."""
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(o11y.trace, "get_tracer", tracer_provider.get_tracer)
    reader = _reader(monkeypatch)
    workspace_id = uuid4()
    with ws(workspace_id), caplog.at_level(logging.INFO, logger="ufo"):
        o11y.emit_metric("turn_started_total")
        o11y.emit_histogram("model_round_ms", 12, model="claude-opus-4-8")
        with o11y.turn_span(uuid4(), uuid4(), None):
            pass
        o11y.log("turn.started", turn_id="abc")
    assert _attributes(reader) == {
        "ufo.turn_started_total": {},
        "ufo.model_round_ms": {"model": "claude-opus-4-8"},
    }
    assert exporter.get_finished_spans()[0].attributes["ufo.workspace_id"] == str(workspace_id)
    assert caplog.records[-1].ufo == {"workspace_id": str(workspace_id), "turn_id": "abc"}


PROVIDER_RETRY_BAND_MS = int(
    (
        PROVIDER_TIMEOUT_SECONDS * (MAX_PROVIDER_RETRIES + MAX_EMPTY_PROVIDER_RETRIES + 1)
        + sum(
            min(INITIAL_RETRY_DELAY_SECONDS * 2**attempt, MAX_RETRY_DELAY_SECONDS)
            for attempt in range(MAX_PROVIDER_RETRIES)
        )
    )
    * 1000
)


LONGEST_BOUNDED_DISPATCH_MS = int(
    (
        SHARE_PREFLIGHT_TIMEOUT_SECONDS
        + ARTIFACT_PUT_TIMEOUT_SECONDS
        + HOOK_TIMEOUT_SECONDS * len(BRACKETING_HOOKS)
    )
    * 1000
)


MODEL_ROUND_WORST_CASE_MS = o11y.HISTOGRAMS["model_first_event_ms"][-1] + 1


WORST_CASE_MS = {
    "model_first_event_ms": PROVIDER_RETRY_BAND_MS,
    "model_round_ms": MODEL_ROUND_WORST_CASE_MS,
    "tool_call_ms": LONGEST_BOUNDED_DISPATCH_MS,
    "turn_ms": MAIN_ROUND_LIMIT * MODEL_ROUND_WORST_CASE_MS,
}


def test_registered_histograms_bucket_their_own_worst_case(monkeypatch):
    """Each name's worst case is its own. The client re-issues only until the first event is
    yielded — a timed-out attempt with its backoff, an empty completion with none — so the band its
    retry constants derive is what the first-event latency has to bucket. A round's wall clock is
    that latency plus the stream, and nothing bounds the stream, so it has to bucket past the
    highest first-event latency there is. A dispatch's is `share_file` — the only tool that bounds
    two transfers of its own — inside the hook pair the step brackets every handler with, since the
    metered wall is the step and not the handler. A foreground subagent bounds its wall at nothing,
    so the tail past this is deliberately unresolved; what has a bound has to land under one. A turn
    is up to `MAIN_ROUND_LIMIT` of those rounds, every one of them able to reach the worst case
    charged one round here, so its own worst case is that product — charging a round any less would
    contradict the line above it. Everything above a top bound shares one bucket, where no
    percentile survives, so a dropped tail fails here."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(o11y.metrics, "get_meter", provider.get_meter)
    monkeypatch.setattr(o11y, "_histograms", {})
    for name in o11y.HISTOGRAMS:
        o11y.emit_histogram(name, WORST_CASE_MS[name])
    exported = {
        metric.name: metric
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    for name, boundaries in o11y.HISTOGRAMS.items():
        metric = exported[f"ufo.{name}"]
        point = metric.data.data_points[0]
        assert metric.unit == "ms"
        assert tuple(point.explicit_bounds) == boundaries
        assert point.count == 1
        assert point.bucket_counts[-1] == 0


def test_turn_span_yields_and_closes():
    with o11y.turn_span(uuid4(), uuid4(), None) as span:
        assert isinstance(span, trace.Span)
        assert trace.get_current_span() is span
    assert trace.get_current_span() is trace.INVALID_SPAN


def test_current_traceparent_is_none_without_an_active_span():
    assert o11y.current_traceparent() is None


def test_traceparent_round_trips_a_turn_span_into_the_capturing_trace():
    """What `current_traceparent` captures under one span, `turn_span` extracts into a span of the
    same trace — the property that lands a subagent's turn in the trace that spawned it."""
    capturing = trace.NonRecordingSpan(
        trace.SpanContext(
            trace_id=0x0AF7651916CD43DD8448EB211C80319C,
            span_id=0xB7AD6B7169203331,
            is_remote=False,
            trace_flags=trace.TraceFlags(0x01),
        )
    )
    with trace.use_span(capturing):
        traceparent = o11y.current_traceparent()
    assert traceparent == "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    with o11y.turn_span(uuid4(), uuid4(), traceparent) as span:
        assert span.get_span_context().trace_id == 0x0AF7651916CD43DD8448EB211C80319C


def test_init_o11y_none_installs_no_providers():
    o11y.init_o11y(None)
    assert not isinstance(trace.get_tracer_provider(), TracerProvider)
    assert not isinstance(metrics.get_meter_provider(), MeterProvider)
    assert not isinstance(_logs.get_logger_provider(), LoggerProvider)


def test_otlp_signal_urls_append_the_per_signal_paths():
    traces_url, metrics_url, logs_url = o11y._otlp_signal_urls(
        "http://otel-collector.ufo-system.svc.cluster.local:4318/"
    )
    assert traces_url == "http://otel-collector.ufo-system.svc.cluster.local:4318/v1/traces"
    assert metrics_url == "http://otel-collector.ufo-system.svc.cluster.local:4318/v1/metrics"
    assert logs_url == "http://otel-collector.ufo-system.svc.cluster.local:4318/v1/logs"


def test_structured_logs_export_one_otel_record_each():
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    _logs.set_logger_provider(provider)
    o11y._bridge_warning_logs(provider)
    handler = logging.getLogger().handlers[-1]
    try:
        o11y.log("turn.started", turn_id="abc", prompt="leak")
        o11y.log_error("jobs.failed", job="core:broken", error_class="TimeoutError")
        o11y.warn("jobs.tick_skipped", key="core:slow", prompt="leak")
        records = [item.log_record for item in exporter.get_finished_logs()]
        assert [(record.body, record.severity_number) for record in records] == [
            ("turn.started", SeverityNumber.INFO),
            ("jobs.failed", SeverityNumber.ERROR),
            ("jobs.tick_skipped", SeverityNumber.WARN),
        ]
        assert dict(records[0].attributes) == {"turn_id": "abc"}
        assert dict(records[1].attributes) == {
            "job": "core:broken",
            "error_class": "TimeoutError",
        }
        assert dict(records[2].attributes) == {"key": "core:slow"}
    finally:
        logging.getLogger().removeHandler(handler)


def test_warn_carries_redacted_fields(caplog):
    with caplog.at_level(logging.WARNING, logger="ufo"):
        o11y.warn("jobs.tick_skipped", key="core:slow", prompt="leak")
    record = caplog.records[-1]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "jobs.tick_skipped"
    assert record.ufo == {"key": "core:slow"}


def test_stdlib_warnings_export_through_the_logs_pipeline():
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    o11y._bridge_warning_logs(provider)
    handler = logging.getLogger().handlers[-1]
    try:
        logging.getLogger("ufo_ext_slack").warning("upload failed for %s", "report.pdf")
        logging.getLogger("ufo_ext_slack").info("below the bridge level")
        logging.getLogger("opentelemetry.exporter.otlp").warning("export failed")
        bodies = [item.log_record.body for item in exporter.get_finished_logs()]
        assert bodies == ["upload failed for report.pdf"]
    finally:
        logging.getLogger().removeHandler(handler)
