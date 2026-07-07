import logging
from uuid import uuid4

import pytest
from opentelemetry import metrics, trace
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
    with o11y.turn_span(uuid4(), uuid4()) as span:
        assert isinstance(span, trace.Span)
        assert trace.get_current_span() is span
    assert trace.get_current_span() is trace.INVALID_SPAN


def test_init_o11y_none_installs_no_providers():
    o11y.init_o11y(None)
    assert not isinstance(trace.get_tracer_provider(), TracerProvider)
    assert not isinstance(metrics.get_meter_provider(), MeterProvider)
