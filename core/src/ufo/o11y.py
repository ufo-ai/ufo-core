"""Tracing, metrics, and redacting structured logs; OTel SDK with OTLP export at init."""

import logging
import traceback
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import cast
from uuid import UUID

from opentelemetry import _logs, metrics, trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Counter, Histogram
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import Counter as CounterInstrument
from opentelemetry.sdk.metrics import Histogram as HistogramInstrument
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.metrics.view import (
    Aggregation,
    ExponentialBucketHistogramAggregation,
)
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
STACK_MAX_CHARS = 8_000
STACK_ELISION = "\n... middle frames elided ...\n"
METRICS = (
    "turn_started_total",
    "turn_terminal_total",
    "turn_rounds_total",
    "turn_parked_total",
    "turn_round_budget_exhausted_total",
    "turn_context_overflow_recovered_total",
    "turn_truncation_recovered_total",
    "compaction_verified_total",
    "sandbox_egress_total",
    "sandbox_prepare_deferred_total",
    "sandbox_prepare_retried_total",
    "sandbox_exec_timeout_total",
    "sandbox_tool_output_dir_reclaimed_total",
    "tool_offload_failed_total",
    "db_tx_unavailable_total",
    "db_pool_exhausted_total",
    "model_round_tokens_total",
    "tool_call_total",
    "source_sync_failed_total",
    "repl_run_total",
    "objective_step_recorded_total",
    "objective_condition_total",
    "objective_frontier_injected_total",
)
ERROR_CLASS_DIMENSION = "error_class"
PROFILE_DIMENSION = "profile"
MAIN_PROFILE = "main"
HISTOGRAMS = {
    "db_tx_acquire_ms": ("path",),
    "model_round_ms": ("model", "provider", ERROR_CLASS_DIMENSION, PROFILE_DIMENSION),
    "model_first_event_ms": ("model", "provider", PROFILE_DIMENSION),
    "tool_call_ms": ("tool", "outcome", ERROR_CLASS_DIMENSION, PROFILE_DIMENSION),
    "turn_ms": ("status", PROFILE_DIMENSION),
}
HISTOGRAM_AGGREGATION: dict[type, Aggregation] = {
    HistogramInstrument: ExponentialBucketHistogramAggregation()
}
EXPORT_TEMPORALITY: dict[type, AggregationTemporality] = {
    CounterInstrument: AggregationTemporality.DELTA,
    HistogramInstrument: AggregationTemporality.DELTA,
}
NO_ERROR_CLASS = ""
OTHER_ERROR_CLASS = "other"
ERROR_CLASSES = frozenset(
    {
        NO_ERROR_CLASS,
        "APIConnectionError",
        "APIError",
        "APIResponseValidationError",
        "APIStatusError",
        "APITimeoutError",
        "APIWebhookValidationError",
        "AdminShutdownError",
        "AuthenticationError",
        "BadRequestError",
        "BlockingIOError",
        "BrokenPipeError",
        "CancelledError",
        "CannotConnectNowError",
        "ChildProcessError",
        "ClientCannotConnectError",
        "ClientConfigurationError",
        "CloseError",
        "ConfigurationLimitExceededError",
        "ConflictError",
        "ConnectError",
        "ConnectTimeout",
        "ConnectionAbortedError",
        "ConnectionDoesNotExistError",
        "ConnectionError",
        "ConnectionFailureError",
        "ConnectionRefusedError",
        "ConnectionRejectionError",
        "ConnectionResetError",
        "CrashShutdownError",
        "CredentialValueInvalid",
        "CursorExpired",
        "DataError",
        "DatabaseDroppedError",
        "DeadlineExceededError",
        "DiskFullError",
        "FileExistsError",
        "FileNotFoundError",
        "HTTPStatusError",
        "IdleSessionTimeoutError",
        "InsufficientResourcesError",
        "IntentRefused",
        "InterfaceError",
        "InternalClientError",
        "InternalServerError",
        "InterruptedError",
        "InvalidAuthorizationSpecificationError",
        "InvalidPasswordError",
        "IsADirectoryError",
        "KeyError",
        "LocalProtocolError",
        "ModelRefusal",
        "ModelResponseTruncated",
        "NetworkError",
        "NotADirectoryError",
        "NotFoundError",
        "OAuthError",
        "OSError",
        "OperationalError",
        "OperatorInterventionError",
        "OutOfMemoryError",
        "OutdatedSchemaCacheError",
        "OverloadedError",
        "PermissionDeniedError",
        "PermissionError",
        "PoolTimeout",
        "PostgresConnectionError",
        "ProcessLookupError",
        "ProtocolError",
        "ProtocolViolationError",
        "ProxyError",
        "QueryCanceledError",
        "RateLimitError",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "RequestTooLargeError",
        "RuntimeError",
        "ServiceUnavailableError",
        "TargetServerAttributeNotMatched",
        "TimeoutError",
        "TimeoutException",
        "TooManyConnectionsError",
        "TransactionResolutionUnknownError",
        "TransportError",
        "UnprocessableEntityError",
        "UnsupportedClientFeatureError",
        "UnsupportedProtocol",
        "UnsupportedServerFeatureError",
        "UntrustedContentError",
        "ValidationError",
        "ValueError",
        "WriteError",
        "WriteTimeout",
        "gaierror",
    }
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
_histograms: dict[str, Histogram] = {}


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
        OTLPMetricExporter(
            endpoint=metrics_url,
            preferred_aggregation=HISTOGRAM_AGGREGATION,
            preferred_temporality=EXPORT_TEMPORALITY,
        ),
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


def turn_profile(subagent_profile: str | None) -> str:
    """The `profile` dimension for one turn: the subagent profile it runs under, or `main` for a
    member-facing turn. The profile name, never the turn's agent or id — the set of profiles a
    deploy declares is fixed and small, so the dimension costs a bounded number of series per
    metric while separating a subagent's latency and steps from the main agent's."""
    return subagent_profile or MAIN_PROFILE


@contextmanager
def turn_span(
    turn_id: UUID,
    conversation_id: UUID,
    traceparent: str | None,
    subagent_profile: str | None,
    parent_turn_id: UUID | None,
) -> Iterator[Span]:
    """Open the SERVER span wrapping one durable turn, tagged with the ambient workspace.
    `traceparent` parents the span on the trace that admitted the turn — a subagent's turn lands
    in the trace of the turn that spawned it; None roots a fresh trace (a member-facing turn).
    The profile and the spawning turn are attributes as well as the parent link, so a trace search
    can select one profile's turns without walking every trace to its root; `main` for a
    member-facing turn, matching the `profile` metric dimension."""
    attributes = cast(
        dict[str, str],
        redact_payload(
            {
                "ufo.turn_id": str(turn_id),
                "ufo.conversation_id": str(conversation_id),
                "ufo.profile": turn_profile(subagent_profile),
                **({} if parent_turn_id is None else {"ufo.parent_turn_id": str(parent_turn_id)}),
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


def warn(event: str, **fields: object) -> None:
    """`log` at warning severity, for expected-but-notable conditions worth an operator's eye."""
    _emit_log(event, SeverityNumber.WARN, "WARN", logging.WARNING, fields)


def formatted_stack(error: BaseException) -> str:
    """Where an exception was raised — its frames and the class of every exception in its cause
    chain, for a log field.

    Carries no exception message. A message is operator-controlled text this process never wrote:
    a sandbox command's stderr reaches here as `RuntimeError`, and the sandbox's own environment
    echoes the turn's run token in `HTTP_PROXY`. A stack that formatted them would be that export
    through a field name redaction does not match.

    A `raise ... from None` severs its context deliberately, and that severing is honoured: the
    exception it was raised inside names neither its class nor its frames here.

    An over-long chain keeps both ends: `format_tb` lists frames caller-first, so the head holds
    the entry point the turn came in through and the tail holds the raise that actually went wrong.
    The elision falls in the middle, where the frames are the ones a reader can reconstruct."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__module__}.{type(current).__qualname__}\n")
        parts.extend(traceback.format_tb(current.__traceback__))
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    frames = "".join(parts)
    if len(frames) <= STACK_MAX_CHARS:
        return frames
    half = (STACK_MAX_CHARS - len(STACK_ELISION)) // 2
    return f"{frames[:half]}{STACK_ELISION}{frames[-half:]}"


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


def _bounded_error_class(dimensions: dict[str, str]) -> dict[str, str]:
    """A series' attributes with `error_class` folded onto `ERROR_CLASSES`, everything else
    `OTHER_ERROR_CLASS`. A series costs the product of its dimensions, and a class name is whatever
    raised — an extension's handler, a gating hook, a provider SDK, a database driver — so one
    passed through mints a series across every other dimension of the metric, permanently, for a
    class nothing reads. A base alone would fold the whole family: the SDK maps a status to its own
    subclass and returns the base only for one it does not map, so listing `APIStatusError` without
    `RateLimitError` beneath it puts every rate limit in the same bucket as an extension's crash.
    The fold is here, at the one boundary both emitters pass through, so no call site can mint an
    unlisted series, and an unlisted class folds rather than raises — every emission carrying one
    sits in a failure path, where raising would destroy the error the count exists to report."""
    if dimensions.get(ERROR_CLASS_DIMENSION, NO_ERROR_CLASS) in ERROR_CLASSES:
        return dimensions
    return {**dimensions, ERROR_CLASS_DIMENSION: OTHER_ERROR_CLASS}


def emit_metric(name: str, amount: int = 1, /, **dimensions: str) -> None:
    """Increment a registered counter; unregistered names fail loud. What is measured is positional
    so that every keyword is a dimension. `EXPORT_TEMPORALITY` ships the increment as a delta, so
    one event is one count."""
    if name not in METRICS:
        raise ValueError(f"unknown metric: {name}")
    counter = _counters.get(name)
    if counter is None:
        counter = metrics.get_meter(INSTRUMENTATION_NAME).create_counter(f"ufo.{name}")
        _counters[name] = counter
    counter.add(amount, attributes=_bounded_error_class(dimensions))


def emit_histogram(name: str, value: int, /, **dimensions: str) -> None:
    """Record one observation in milliseconds on a registered histogram; an unregistered name or a
    dimension the name does not declare fails loud. Percentiles are computable only where a tag
    configuration enables them, and that configuration is also the allowlist of queryable tags — so
    a dimension absent from it aggregates away, readable nowhere. Declaring dimensions here is what
    the deployed allowlist is held against, and no call site can reach Datadog with a tag the
    allowlist omits. `HISTOGRAM_AGGREGATION` resolves a cached file read and a turn that ran for
    hours on one instrument, at bounded relative error, so no name declares a range;
    `EXPORT_TEMPORALITY` is what carries it, since the Datadog exporter maps an exponential
    histogram to a sketch only in delta and drops a cumulative one."""
    declared = HISTOGRAMS.get(name)
    if declared is None:
        raise ValueError(f"unknown histogram: {name}")
    undeclared = sorted(set(dimensions) - set(declared))
    if undeclared:
        raise ValueError(f"undeclared dimensions on {name}: {', '.join(undeclared)}")
    histogram = _histograms.get(name)
    if histogram is None:
        histogram = metrics.get_meter(INSTRUMENTATION_NAME).create_histogram(
            f"ufo.{name}", unit="ms"
        )
        _histograms[name] = histogram
    histogram.record(value, attributes=_bounded_error_class(dimensions))
