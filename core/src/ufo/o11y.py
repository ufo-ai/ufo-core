"""Tracing, metrics, and redacting structured logs; OTel SDK with OTLP export at init."""

import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import cast
from uuid import UUID

from opentelemetry import _logs, metrics, trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Counter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from ufo.db import current_workspace

INSTRUMENTATION_NAME = "ufo"
TRACEPARENT_HEADER = "traceparent"
TRACE_CONTEXT_PROPAGATOR = TraceContextTextMapPropagator()
METRIC_EXPORT_INTERVAL_MILLIS = 30_000
OTLP_TRACES_PATH = "v1/traces"
OTLP_METRICS_PATH = "v1/metrics"
OTLP_LOGS_PATH = "v1/logs"
METRICS = (
    "turn_started_total",
    "turn_terminal_total",
    "turn_parked_total",
    "turn_round_budget_exhausted_total",
    "turn_context_overflow_recovered_total",
    "sandbox_egress_total",
)
SENSITIVE_FIELD_KEYS = frozenset(
    {
        "prompt",
        "content",
        "modelcontent",
        "toolpayload",
        "credential",
        "credentials",
        "secret",
        "token",
    }
)

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]

_counters: dict[str, Counter] = {}


def init_o11y(otlp_endpoint: str | None) -> None:
    """Install OTel providers exporting to the OTLP/HTTP collector; None keeps no-op defaults."""
    if otlp_endpoint is None:
        return
    traces_url, metrics_url, logs_url = _otlp_signal_urls(otlp_endpoint)
    resource = Resource.create({"service.name": INSTRUMENTATION_NAME})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_url)))
    trace.set_tracer_provider(tracer_provider)
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=metrics_url),
        export_interval_millis=METRIC_EXPORT_INTERVAL_MILLIS,
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=logs_url))
    )
    _logs.set_logger_provider(logger_provider)
    _bridge_warning_logs(logger_provider)


def _bridge_warning_logs(logger_provider: LoggerProvider) -> None:
    """Root-logger handler exporting WARNING-and-up stdlib records through the logs pipeline, so a
    warning from any module — an extension surface's swallowed delivery failure, a library fault —
    reaches the collector instead of dying in an unhandled stdlib logger. Direct structured records
    from `ufo` and OTel's own exporter logs are excluded: the former already emit through OTel, and
    export failures report locally instead of feeding the failing pipeline."""
    handler = LoggingHandler(level=logging.WARNING, logger_provider=logger_provider)
    handler.addFilter(
        lambda record: (
            record.name != INSTRUMENTATION_NAME and not record.name.startswith("opentelemetry")
        )
    )
    logging.getLogger().addHandler(handler)


def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]:
    """(traces, metrics, logs) URLs off the collector base. The OTLP/HTTP exporter posts to the
    exact endpoint it is handed — it never appends a signal path — so the per-signal path the
    collector receiver serves is built here, else exports hit the base URL and the collector 404s
    them."""
    base = otlp_endpoint.rstrip("/")
    return f"{base}/{OTLP_TRACES_PATH}", f"{base}/{OTLP_METRICS_PATH}", f"{base}/{OTLP_LOGS_PATH}"


def _ambient_scope() -> dict[str, str]:
    """The ambient workspace as log/trace metadata, read from the `with ws(...)` scope the turn or
    job bound — so every record and span inside a scope is tagged with the workspace it ran under,
    no call site passing it. Empty outside a scope (deploy-level boot work)."""
    workspace_id = current_workspace.get()
    return {} if workspace_id is None else {"workspace_id": str(workspace_id)}


def current_traceparent() -> str | None:
    """The active span as a W3C traceparent header, None when no valid span context is current —
    captured where a turn is admitted and stored on its row, so the turn's own span joins the
    admitting trace across the queue hop."""
    carrier: dict[str, str] = {}
    TRACE_CONTEXT_PROPAGATOR.inject(carrier)
    return carrier.get(TRACEPARENT_HEADER)


@contextmanager
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None) -> Iterator[Span]:
    """Open the SERVER span wrapping one durable turn, tagged with the ambient workspace.
    `traceparent` parents the span on the trace that admitted the turn — a subagent's turn lands
    in the trace of the turn that spawned it; None roots a fresh trace (a member-facing turn)."""
    attributes = cast(
        dict[str, str],
        redact_payload(
            {
                "ufo.turn_id": str(turn_id),
                "ufo.conversation_id": str(conversation_id),
                **{f"ufo.{key}": value for key, value in _ambient_scope().items()},
            }
        ),
    )
    parent = (
        TRACE_CONTEXT_PROPAGATOR.extract({TRACEPARENT_HEADER: traceparent})
        if traceparent is not None
        else None
    )
    tracer = trace.get_tracer(INSTRUMENTATION_NAME)
    with tracer.start_as_current_span(
        "turn", context=parent, kind=SpanKind.SERVER, attributes=attributes
    ) as span:
        yield span


def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]:
    """Drop sensitive keys (matched with ``_``/``-`` stripped, lowercased) and redact values."""
    return {
        key: redact_value(value)
        for key, value in fields.items()
        if (key.replace("_", "").replace("-", "").lower()) not in SENSITIVE_FIELD_KEYS
    }


def redact_value(value: object) -> JsonValue:
    """Pass JSON scalars through, recurse into containers, stringify everything else."""
    match value:
        case None | bool() | int() | float() | str():
            return value
        case Mapping():
            return redact_payload({str(key): item for key, item in value.items()})
        case Sequence() if not isinstance(value, str | bytes | bytearray):
            return [redact_value(item) for item in value]
        case _:
            return str(value)


def log(event: str, **fields: object) -> None:
    """Emit a structured info record — tagged with the ambient workspace, sensitive fields redacted
    — to stdlib logging and the OTel logs pipeline; the OTel record correlates to the active span.
    The workspace is read from the `with ws(...)` scope, never passed, so every record inside a turn
    or job carries the workspace it ran under."""
    _emit_log(event, SeverityNumber.INFO, "INFO", logging.INFO, fields)


def log_error(event: str, **fields: object) -> None:
    """Emit a structured error record with the same scoping and redaction as `log`."""
    _emit_log(event, SeverityNumber.ERROR, "ERROR", logging.ERROR, fields)


def _emit_log(
    event: str,
    severity_number: SeverityNumber,
    severity_text: str,
    level: int,
    fields: Mapping[str, object],
) -> None:
    redacted = redact_payload({**_ambient_scope(), **fields})
    logging.getLogger(INSTRUMENTATION_NAME).log(level, event, extra={"ufo": redacted})
    _logs.get_logger(INSTRUMENTATION_NAME).emit(
        severity_number=severity_number,
        severity_text=severity_text,
        body=event,
        attributes=redacted,
    )


def emit_metric(name: str, amount: int = 1, **dimensions: str) -> None:
    """Increment a registered counter; unregistered names fail loud."""
    if name not in METRICS:
        raise ValueError(f"unknown metric: {name}")
    counter = _counters.get(name)
    if counter is None:
        counter = metrics.get_meter(INSTRUMENTATION_NAME).create_counter(f"ufo.{name}")
        _counters[name] = counter
    counter.add(amount, attributes=dimensions)
