"""Tracing, metrics, and redacting structured logs; OTel SDK with OTLP export at init."""

import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from uuid import UUID

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Counter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind

INSTRUMENTATION_NAME = "selfhost"
METRIC_EXPORT_INTERVAL_MILLIS = 30_000
METRICS = ("turn_started_total", "turn_terminal_total", "sandbox_egress_total")
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
    """Install OTel providers exporting to the OTLP endpoint; None keeps the no-op defaults."""
    if otlp_endpoint is None:
        return
    resource = Resource.create({"service.name": INSTRUMENTATION_NAME})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    trace.set_tracer_provider(tracer_provider)
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=otlp_endpoint),
        export_interval_millis=METRIC_EXPORT_INTERVAL_MILLIS,
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))


@contextmanager
def turn_span(turn_id: UUID, conversation_id: UUID) -> Iterator[Span]:
    """Open the SERVER span wrapping one durable turn."""
    attributes = redact_payload(
        {"selfhost.turn_id": str(turn_id), "selfhost.conversation_id": str(conversation_id)}
    )
    tracer = trace.get_tracer(INSTRUMENTATION_NAME)
    with tracer.start_as_current_span("turn", kind=SpanKind.SERVER, attributes=attributes) as span:
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
    """Emit a structured info record with sensitive fields redacted."""
    logging.getLogger(INSTRUMENTATION_NAME).info(event, extra={"selfhost": redact_payload(fields)})


def emit_metric(name: str, amount: int = 1, **dimensions: str) -> None:
    """Increment a registered counter; unregistered names fail loud."""
    if name not in METRICS:
        raise ValueError(f"unknown metric: {name}")
    counter = _counters.get(name)
    if counter is None:
        counter = metrics.get_meter(INSTRUMENTATION_NAME).create_counter(f"selfhost.{name}")
        _counters[name] = counter
    counter.add(amount, attributes=dimensions)
