import asyncio
import logging
import re
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from uuid import uuid4

import anthropic
import asyncpg.exceptions as asyncpg_errors
import httpx
import openai
import pytest
import sqlalchemy as sa
from opentelemetry import _logs, metrics, trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from opentelemetry.proto.metrics.v1.metrics_pb2 import AGGREGATION_TEMPORALITY_DELTA
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    ExponentialHistogramDataPoint,
    InMemoryMetricReader,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

from evals.harness.harness import TRANSIENT_ERROR_CLASSES
from ufo import o11y
from ufo.credentials import CredentialValueInvalid
from ufo.loop.engine import IntentRefused
from ufo.models import anthropic as anthropic_models
from ufo.models import openai as openai_models
from ufo.models.interface import ModelRefusal, ModelResponseTruncated
from ufo.sources.sync import CursorExpired
from ufo.tools.context import UntrustedContentError
from ufo.workspace import ws


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


def test_formatted_stack_names_the_raising_call():
    def wedged():
        raise TimeoutError

    try:
        wedged()
    except TimeoutError as error:
        stack = o11y.formatted_stack(error)
    assert "wedged" in stack
    assert "TimeoutError" in stack


def test_formatted_stack_never_carries_the_exception_message():
    """A message is text this process never wrote — a sandbox command's stderr arrives as a
    RuntimeError, and the sandbox environment echoes the turn's run token in HTTP_PROXY. Logs
    withhold messages, and a stack that formatted them would be the same export under a field
    name redaction does not match. Frames carry their own source text, which is this repo's; it is
    the runtime values that must not travel."""

    run_token = uuid4().hex

    def failing_command(stderr: str) -> None:
        raise RuntimeError(stderr)

    try:
        failing_command(f"HTTP_PROXY=https://{run_token}:@proxy.test")
    except RuntimeError as error:
        stack = o11y.formatted_stack(error)
    assert run_token not in stack
    assert "failing_command" in stack
    assert "builtins.RuntimeError" in stack


def test_formatted_stack_honours_a_severed_context():
    """`raise ... from None` severs the context deliberately — the engine does it at the model
    seam. Walking it anyway would export the class and frames of an exception the author took
    care to detach."""

    def broker_call() -> None:
        raise ConnectionResetError("upstream reset")

    try:
        broker_call()
    except ConnectionResetError:
        try:
            raise RuntimeError("severed") from None
        except RuntimeError as raised:
            severed = o11y.formatted_stack(raised)

    try:
        broker_call()
    except ConnectionResetError:
        try:
            raise RuntimeError("kept")
        except RuntimeError as raised:
            kept = o11y.formatted_stack(raised)

    assert "ConnectionResetError" not in severed
    assert "broker_call" not in severed
    assert "ConnectionResetError" in kept
    assert "broker_call" in kept


def test_formatted_stack_keeps_both_ends_of_a_long_cause_chain():
    """A one-ended truncation always drops one of the two frames worth having. `format_tb` lists
    frames caller-first, so the head holds the entry point and the tail holds the raise that
    actually went wrong — and it is the root cause, deepest in the chain, that a tail cut loses."""

    def root_cause() -> None:
        raise TimeoutError("the provider stopped answering")

    try:
        root_cause()
    except TimeoutError as raised:
        error: BaseException = raised
    for layer in range(100):
        try:
            raise RuntimeError(f"layer {layer}") from error
        except RuntimeError as raised:
            error = raised

    stack = o11y.formatted_stack(error)

    assert len(stack) <= o11y.STACK_MAX_CHARS
    assert o11y.STACK_ELISION in stack
    head, tail = stack.split(o11y.STACK_ELISION)
    assert head.startswith("builtins.RuntimeError\n")
    assert "root_cause" in tail


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
        with o11y.turn_span(uuid4(), uuid4(), None, None, None):
            pass
        o11y.log("turn.started", turn_id="abc")
    assert _attributes(reader) == {
        "ufo.turn_started_total": {},
        "ufo.model_round_ms": {"model": "claude-opus-4-8"},
    }
    assert exporter.get_finished_spans()[0].attributes["ufo.workspace_id"] == str(workspace_id)
    assert caplog.records[-1].ufo == {"workspace_id": str(workspace_id), "turn_id": "abc"}


def test_an_error_class_the_code_does_not_act_on_folds_into_one_series(monkeypatch):
    """A class name is whatever raised — an extension's handler here — so passing it through mints
    a series across every other dimension of the metric, for a class nothing reads. It folds to
    `other` on the counter and on the histogram alike. A class the code branches on keeps its own
    series, and an end that carried no exception keeps the empty class the series is dimensioned
    by."""
    reader = _reader(monkeypatch)
    o11y.emit_metric("tool_call_total", tool="run_command", error_class="ZeroDivisionError")
    o11y.emit_histogram("tool_call_ms", 12, tool="run_command", error_class="ZeroDivisionError")
    o11y.emit_metric("db_tx_unavailable_total", path="workspace", error_class="TimeoutError")
    o11y.emit_metric("turn_terminal_total", status="done", error_class="")
    assert _attributes(reader) == {
        "ufo.tool_call_total": {"tool": "run_command", "error_class": "other"},
        "ufo.tool_call_ms": {"tool": "run_command", "error_class": "other"},
        "ufo.db_tx_unavailable_total": {"path": "workspace", "error_class": "TimeoutError"},
        "ufo.turn_terminal_total": {"status": "done", "error_class": ""},
    }


PROVIDER_FAULT_ROOTS = (
    (anthropic.APIError, "anthropic"),
    (openai.APIError, "openai"),
    (httpx.TransportError, "httpx"),
)
DRIVER_FAULT_ROOTS = (
    (OSError, "builtins"),
    (asyncpg_errors.PostgresConnectionError, "asyncpg"),
    (asyncpg_errors.InsufficientResourcesError, "asyncpg"),
    (asyncpg_errors.InvalidAuthorizationSpecificationError, "asyncpg"),
    (asyncpg_errors.OperatorInterventionError, "asyncpg"),
    (asyncpg_errors.InterfaceError, "asyncpg"),
    (asyncpg_errors.InternalClientError, "asyncpg"),
)
CAUGHT_ERROR_ROOTS = (
    *anthropic_models.STREAM_TRANSPORT_ERRORS,
    *anthropic_models.STREAM_STATUS_ERRORS,
    *openai_models.STREAM_TRANSPORT_ERRORS,
    *openai_models.STREAM_STATUS_ERRORS,
)
MEASURED_DRIVER_ERRORS = (ConnectionRefusedError, socket.gaierror)
NAMED_ERRORS = (
    CredentialValueInvalid,
    CursorExpired,
    IntentRefused,
    ModelRefusal,
    ModelResponseTruncated,
    UntrustedContentError,
    asyncio.CancelledError,
    httpx.HTTPStatusError,
    sa.exc.OperationalError,
    ValidationError,
    KeyError,
    RuntimeError,
    TimeoutError,
    ValueError,
)


def test_every_allowed_error_class_names_a_class_the_code_can_meet():
    """Every entry resolves to a class, and set equality means an entry that resolves to nothing
    fails as loudly as a class that reaches the dimension with no entry.

    A root contributes its whole family, filtered to the classes the owning package declares.
    Both halves are load-bearing. Walking `__subclasses__()` is what sees a class the package does
    not export — `DeadlineExceededError` and `ServiceUnavailableError` live in
    `anthropic._exceptions` and never appear in `vars(anthropic)`, so a namespace scan alone leaves
    them bound to nothing and lets the next unexported class an SDK adds fold silently. Filtering by
    `__module__` is what keeps that walk from being a live graph of whatever the interpreter has
    loaded: `httpx_sse.SSEError` subclasses `httpx.TransportError`, and before the filter this test
    passed alone and failed in the one shard that imported `httpx_sse`.

    `PROVIDER_FAULT_ROOTS` is each SDK's own error base, because every class a provider can raise
    out of a stream reaches this dimension whether or not a client catches it — the SDK maps a
    status onto its own subclass and returns the base only for an unmapped one.
    `DRIVER_FAULT_ROOTS` is the same question for the database: nothing between asyncpg and the
    emitter re-wraps the fault, so `db_tx_unavailable_total` reports whatever the driver raised, and
    `path` is that counter's only other dimension — the class is its whole information content. Its
    roots are asyncpg's two client-side bases, the builtin `OSError` tree, and the four SQLSTATE
    groups the tuple above names. `OSError` rather than `ConnectionError`, because CPython gives
    only five errnos a `ConnectionError` subclass and every other one — the unreachable network or
    withdrawn route a host delivers mid-incident — arrives bare, one errno from a refused connect
    that would keep its own series. That tree is the widest root here: it admits the filesystem
    names too, which a handler can raise for reasons of its own, and the trade is deliberate — 16
    names fixed at import against a counter whose entire content is this dimension.

    `CAUGHT_ERROR_ROOTS` is what the clients' retry clauses catch. It cannot widen coverage — those
    classes sit under the SDK roots already — so it earns its place by failing when a clause grows
    past them, the one way a client starts meeting a fault the roots do not describe. A clause that
    narrows correctly fails nothing: which classes we retry is not which classes reach the
    dimension.

    `TRANSIENT_ERROR_CLASSES` is this repo's own vocabulary for a provider fault, already read off
    an `error_class` field by the eval harness, so a name it starts treating as transient fails here
    until this dimension can carry it too.

    Leaves contribute their own name and nothing beneath it: expanding `ValueError` or
    `RuntimeError` would drag in every unrelated builtin subclass. The measured pair is named
    nowhere in the code — it is whatever the driver raises — so `test_db.py` asks the real stack and
    binds the answer.

    What no rule can bind is the open population past all of that: an extension handler or a hook
    raises whatever it likes, which is what `OTHER_ERROR_CLASS` exists for."""
    declared = {
        subclass.__name__
        for root, package in (*PROVIDER_FAULT_ROOTS, *DRIVER_FAULT_ROOTS)
        for subclass in _family(root)
        if subclass.__module__.split(".")[0] == package
    }
    assert o11y.ERROR_CLASSES == (
        {o11y.NO_ERROR_CLASS}
        | set(TRANSIENT_ERROR_CLASSES)
        | declared
        | {cls.__name__ for cls in (*CAUGHT_ERROR_ROOTS, *MEASURED_DRIVER_ERRORS, *NAMED_ERRORS)}
    )


def _family(root: type[BaseException]) -> set[type[BaseException]]:
    """`root` and every class beneath it. Recursive rather than a namespace read so a class the
    owning package never exports is still seen; the caller filters by declaring package."""
    found = {root}
    for subclass in root.__subclasses__():
        found |= _family(subclass)
    return found


CACHED_FILE_READ_MS = 1
LONG_TURN_MS = 24 * 60 * 60 * 1000
PERCENTILE_CONFIG = Path(__file__).parents[2] / "infra/envs/testing/metrics.tf"
PIPELINE_TAGS = ("env", "host", "service")


def test_every_histogram_declares_the_tags_it_emits():
    """Both ends of the deployed allowlist. A distribution reaches Datadog whether or not
    percentiles are enabled, so a histogram with no tag configuration reads as a working metric no
    percentile can be read off; and because the configuration is also the allowlist of queryable
    tags, a dimension missing from it aggregates away silently. Each list is the name's declared
    dimensions plus what the pipeline stamps on every metric."""
    text = PERCENTILE_CONFIG.read_text()
    configured = {
        name: tuple(sorted(re.findall(r'"(\w+)"', tags)))
        for name, tags in re.findall(
            r'metric_name\s+=\s+"ufo\.(\w+)".*?tags\s+=\s+\[([^\]]*)\]', text, re.DOTALL
        )
    }
    assert configured == {
        name: tuple(sorted({*PIPELINE_TAGS, *dimensions}))
        for name, dimensions in o11y.HISTOGRAMS.items()
    }
    assert text.count("include_percentiles = true") == len(o11y.HISTOGRAMS)


def test_every_histogram_declares_its_unit():
    """`emit_histogram` records milliseconds and Datadog does not learn that from the OTLP payload,
    so the declaration is deployed config nothing else holds against the emitter. `type` is asserted
    beside it because it is the load-bearing value: the provider reads the type back as `gauge`
    whatever the metric is, so any other value leaves a `~ type` diff in every unattended apply."""
    blocks = re.findall(
        r'resource\s+"datadog_metric_metadata"\s+"\w+"\s+\{([^}]*)\}', PERCENTILE_CONFIG.read_text()
    )
    declared = {
        name: (metric_type, unit)
        for name, metric_type, unit in re.findall(
            r'metric\s+=\s+"ufo\.(\w+)"[\s\S]*?type\s+=\s+"(\w+)"[\s\S]*?unit\s+=\s+"(\w+)"',
            "".join(blocks),
        )
    }
    assert declared == {name: ("gauge", "millisecond") for name in o11y.HISTOGRAMS}


def test_emit_histogram_rejects_a_dimension_the_name_does_not_declare():
    """The allowlist is deployed config: a tag it omits is dropped at Datadog, so a call site that
    invents a dimension would emit a series whose new tag is readable nowhere. It fails at the one
    boundary both emitters pass through instead."""
    with pytest.raises(ValueError, match="undeclared dimensions on turn_ms: outcome"):
        o11y.emit_histogram("turn_ms", 1, status="done", outcome="ok")


def test_a_subagent_profile_splits_the_latency_series_from_the_main_agents(monkeypatch):
    """What the dimension buys: a subagent's turns and tool calls are readable apart from the main
    agent's, one series per profile rather than one per turn. A turn with no profile is the main
    agent and reports as `main`, so no series carries an empty tag and the profiles sum to the
    fleet."""
    reader = _reader(monkeypatch)
    for profile in (o11y.turn_profile("coding"), o11y.turn_profile(None)):
        o11y.emit_histogram("turn_ms", 12, status="done", profile=profile)
        o11y.emit_histogram("tool_call_ms", 3, tool="bash", outcome="ok", profile=profile)
    assert {
        metric.name: sorted(point.attributes["profile"] for point in metric.data.data_points)
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    } == {
        "ufo.turn_ms": ["coding", "main"],
        "ufo.tool_call_ms": ["coding", "main"],
    }


def test_histograms_resolve_a_millisecond_and_a_day_as_delta_exponential(monkeypatch):
    """The production pairing, held against the SDK that implements it. One instrument carries a
    cached file read and a turn that ran for a day: an exponential histogram spends buckets on
    relative error rather than a declared range, so neither end saturates a top bucket and both
    survive as percentiles. Delta is what reaches Datadog — its exporter maps an exponential
    histogram to a sketch only in delta and drops a cumulative one, so a temporality regression
    would mean silence in production and fails here instead."""
    reader = InMemoryMetricReader(
        preferred_aggregation=o11y.HISTOGRAM_AGGREGATION,
        preferred_temporality=o11y.EXPORT_TEMPORALITY,
    )
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(o11y.metrics, "get_meter", provider.get_meter)
    monkeypatch.setattr(o11y, "_histograms", {})
    o11y.emit_histogram("turn_ms", CACHED_FILE_READ_MS)
    o11y.emit_histogram("turn_ms", LONG_TURN_MS)
    metric = next(
        metric
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    )
    point = metric.data.data_points[0]
    assert metric.name == "ufo.turn_ms"
    assert metric.unit == "ms"
    assert isinstance(point, ExponentialHistogramDataPoint)
    assert metric.data.aggregation_temporality is AggregationTemporality.DELTA
    assert (point.min, point.max, point.count) == (CACHED_FILE_READ_MS, LONG_TURN_MS, 2)
    assert point.positive.bucket_counts.count(1) == 2


def test_init_o11y_ships_histograms_as_delta_exponential(monkeypatch):
    """The pairing where it is installed. Holding the constants against the SDK proves what they
    mean, not that anything passes them: dropping both keyword arguments from the exporter leaves
    every other assertion in this file passing, while production goes silent because the Datadog
    exporter drops the cumulative histograms it would then receive. So this reads the bytes the real
    exporter puts on the wire, built by `init_o11y` itself."""
    provider, exported, server = _init_o11y_against_an_intake(monkeypatch)
    o11y.emit_histogram("turn_ms", CACHED_FILE_READ_MS, status="done")
    o11y.emit_histogram("turn_ms", LONG_TURN_MS, status="done")
    assert provider.force_flush(timeout_millis=15_000)
    server.shutdown()
    metric = exported[0].resource_metrics[0].scope_metrics[0].metrics[0]
    point = metric.exponential_histogram.data_points[0]
    assert metric.name == "ufo.turn_ms"
    assert metric.WhichOneof("data") == "exponential_histogram"
    assert metric.exponential_histogram.aggregation_temporality == AGGREGATION_TEMPORALITY_DELTA
    assert (point.min, point.max, point.count) == (CACHED_FILE_READ_MS, LONG_TURN_MS, 2)


def test_init_o11y_ships_one_increment_as_one_counted_delta(monkeypatch):
    """One event is one count. The delta carries the increment itself, so the count stands without
    anything downstream holding per-series state to recover it, and an interval where nothing is
    counted exports no point at all, which is what the second flush pins."""
    provider, exported, server = _init_o11y_against_an_intake(monkeypatch)
    o11y.emit_metric("source_sync_failed_total", provider="googlesheets", stream="sheet_values")
    assert provider.force_flush(timeout_millis=15_000)
    assert provider.force_flush(timeout_millis=15_000)
    server.shutdown()
    counters = [
        metric
        for export in exported
        for resource in export.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.WhichOneof("data") == "sum"
    ]
    assert len(counters) == 1
    metric = counters[0]
    assert metric.name == "ufo.source_sync_failed_total"
    assert metric.sum.aggregation_temporality == AGGREGATION_TEMPORALITY_DELTA
    assert metric.sum.is_monotonic
    assert [point.as_int for point in metric.sum.data_points] == [1]


def _init_o11y_against_an_intake(
    monkeypatch,
) -> tuple[MeterProvider, list[ExportMetricsServiceRequest], HTTPServer]:
    """`init_o11y` pointed at a real OTLP/HTTP intake: the provider it installed, the export
    requests that reach the intake, and the server to shut down. Both instrument caches are emptied
    alongside the provider they bind to."""
    exported: list[ExportMetricsServiceRequest] = []

    class Intake(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            export = ExportMetricsServiceRequest()
            export.ParseFromString(self.rfile.read(int(self.headers["content-length"])))
            exported.append(export)
            self.send_response(200)
            self.send_header("content-length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Intake)
    Thread(target=server.serve_forever, daemon=True).start()
    installed: list[MeterProvider] = []
    monkeypatch.setattr(o11y.metrics, "set_meter_provider", installed.append)
    monkeypatch.setattr(o11y.trace, "set_tracer_provider", lambda provider: None)
    monkeypatch.setattr(o11y._logs, "set_logger_provider", lambda provider: None)
    monkeypatch.setattr(o11y, "_bridge_warning_logs", lambda provider: None)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})
    o11y.init_o11y(f"http://127.0.0.1:{server.server_port}")
    provider = installed[0]
    monkeypatch.setattr(o11y.metrics, "get_meter", provider.get_meter)
    return provider, exported, server


def test_turn_span_yields_and_closes():
    with o11y.turn_span(uuid4(), uuid4(), None, None, None) as span:
        assert isinstance(span, trace.Span)
        assert trace.get_current_span() is span
    assert trace.get_current_span() is trace.INVALID_SPAN


def test_a_turn_span_carries_its_profile_and_the_turn_that_spawned_it(monkeypatch):
    """A subagent's span sits inside the spawning turn's trace, so the profile and the parent turn
    are attributes too: a trace search selects one profile's turns without walking every trace to
    its root. A member-facing turn has no parent to name and reports as `main`."""
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(o11y.trace, "get_tracer", tracer_provider.get_tracer)
    parent_turn_id = uuid4()
    with o11y.turn_span(uuid4(), uuid4(), None, "coding", parent_turn_id):
        pass
    with o11y.turn_span(uuid4(), uuid4(), None, None, None):
        pass
    subagent, main = exporter.get_finished_spans()
    assert subagent.attributes["ufo.profile"] == "coding"
    assert subagent.attributes["ufo.parent_turn_id"] == str(parent_turn_id)
    assert main.attributes["ufo.profile"] == "main"
    assert "ufo.parent_turn_id" not in main.attributes


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
    with o11y.turn_span(uuid4(), uuid4(), traceparent, None, None) as span:
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


def test_an_oversized_library_warning_is_capped_before_the_exporter():
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    o11y._bridge_warning_logs(provider)
    handler = logging.getLogger().handlers[-1]
    try:
        logging.getLogger("ufo_ext_slack").warning("upload failed for %s", "x" * 100_000)
        (body,) = [item.log_record.body for item in exporter.get_finished_logs()]
    finally:
        logging.getLogger().removeHandler(handler)
    dropped = len("upload failed for ") + 100_000 - o11y.LIBRARY_MESSAGE_MAX_CHARS
    assert body == (
        f"upload failed for {'x' * (o11y.LIBRARY_MESSAGE_MAX_CHARS - len('upload failed for '))}"
        f"{o11y.LIBRARY_MESSAGE_ELISION.format(dropped=dropped)}"
    )


class _CapturingHandler(logging.Handler):
    def __init__(self, messages: list[str]) -> None:
        super().__init__()
        self.messages = messages

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_an_oversized_dbos_warning_is_capped_on_the_logger_that_bypasses_the_root_handler():
    warning = f"Asyncio task cancelled for workflow or step {'x' * 500_000}"
    o11y.init_o11y(None)
    logger = logging.getLogger(o11y.DBOS_LOGGER_NAME)
    captured: list[str] = []
    handler = _CapturingHandler(captured)
    logger.addHandler(handler)
    try:
        logger.warning(warning)
    finally:
        logger.removeHandler(handler)
    dropped = len(warning) - o11y.LIBRARY_MESSAGE_MAX_CHARS
    assert captured == [
        warning[: o11y.LIBRARY_MESSAGE_MAX_CHARS]
        + o11y.LIBRARY_MESSAGE_ELISION.format(dropped=dropped)
    ]
