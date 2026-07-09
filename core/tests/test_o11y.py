import logging
from uuid import uuid4

import pytest
from opentelemetry import _logs, metrics, trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from ufo import o11y


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


def test_log_exports_redacted_otel_record():
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    _logs.set_logger_provider(provider)
    o11y.log("turn.started", turn_id="abc", prompt="leak")
    (record,) = (item.log_record for item in exporter.get_finished_logs())
    assert record.body == "turn.started"
    assert record.severity_number == SeverityNumber.INFO
    assert dict(record.attributes) == {"turn_id": "abc"}


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
