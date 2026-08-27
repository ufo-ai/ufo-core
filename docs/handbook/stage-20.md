# Observability, Telemetry, and Operator Inspection  `stage-20` (cross-cutting infrastructure)

This stage is the system’s set of “dashboard and black box recorder” tools. It is not one single moment in the workflow. It runs behind the scenes during startup, normal requests, agent turns, tool calls, background jobs, and shutdown, so operators can see what happened and diagnose problems safely.

The main toolbox is core/src/ufo/o11y.py. It sets up tracing, which follows work as it moves through the system, metrics, which are counted measurements like timing and success rates, and structured logs, which are machine-readable notes about events. It also redacts sensitive text, meaning it removes or hides prompts, secrets, and tokens before they can leak into logs. It can report service health to Datadog, an external monitoring system.

core/src/ufo/loop/steps.py turns raw stored workflow history into a readable timeline of a turn: each step, its order, and how long it took. extensions/debugger/ufo_ext_debugger/report.py adds a tool for agents to flag serious workspace problems with enough context for engineers to investigate.

## Files in this stage

### Turn Step Inspection
Prepares DBOS workflow step history as safe, readable turn timelines for external inspection.

### `core/src/ufo/loop/steps.py`

`domain_logic` · `request handling`

A workflow turn can involve several hidden pieces: the model may produce tool calls, tools may run, and workflow helper functions may execute. DBOS, the durable workflow system, records those steps in its own structured form. That raw record is useful to the engine, but it is not the friendly shape wanted by the surface layer, which needs simple items like “model round” or “tool call,” with start and finish times.

`DurableTurnSteps` is the translator between those worlds. Given a workflow id, it asks DBOS for the recorded steps. It first looks through model-stream results to learn which tool-call id belongs to which tool name. That matters because later tool results may only refer to the id, like a receipt number, and this file uses the earlier model output to recover the readable tool name.

It then walks through every recorded step in order and builds a `TurnStep`. Model stream outputs become `model` steps. Tool dispatch results become `tool` steps. Anything else is treated as a `workflow` step and named from the function that ran. It also converts millisecond timestamps into timezone-aware datetimes and calculates a non-negative duration when both start and finish times are present.

#### Function details

##### `DurableTurnSteps.read`  (lines 19–58)

```
async def read(self, workflow_id: str) -> tuple[TurnStep, ...]
```

**Purpose**: Reads the saved step history for one workflow and turns it into a tuple of `TurnStep` objects, which are simpler records meant for the surface layer. Someone would use this when they need to show or inspect what happened during a durable workflow turn.

**Data flow**: It takes a `workflow_id` and uses the stored `DBOSClient` to fetch that workflow’s recorded steps. It scans the records once to connect tool-call ids to human-readable tool names, then scans them again to label each step as a model step, tool step, or workflow step. For each record it converts timing fields through `_timestamp`, computes a duration when possible, builds a `TurnStep`, and finally returns all projected steps as an immutable tuple.

**Call relations**: This is the main public action in the file. When called, it relies on DBOS to supply the raw history, uses `DurableTurnSteps._timestamp` to make stored millisecond times readable, and hands each completed surface record to `TurnStep.__init__` so the rest of the system receives the expected boundary shape.

*Call graph*: calls 1 internal fn (_timestamp); 1 external calls (__init__).


##### `DurableTurnSteps._timestamp`  (lines 61–62)

```
def _timestamp(epoch_ms: int | None) -> datetime | None
```

**Purpose**: Converts a stored time value from milliseconds since the Unix epoch into a timezone-aware `datetime`. If DBOS did not record a time, it leaves it as `None` instead of inventing one.

**Data flow**: It receives either an integer millisecond timestamp or `None`. If the input is `None`, the output is `None`; otherwise it divides by 1000 to get seconds and asks Python’s `datetime.fromtimestamp` to create a UTC datetime. Nothing outside the function is changed.

**Call relations**: `DurableTurnSteps.read` calls this helper for each step’s start and completion times. Keeping the conversion here makes the main projection code easier to read and ensures all returned `TurnStep` times use the same UTC interpretation.

*Call graph*: called by 1 (read); 1 external calls (fromtimestamp).


### Telemetry and Problem Reporting
Provides the shared observability toolbox and debugger-facing reporting hook used by operators and engineers to diagnose failures safely.

### `core/src/ufo/o11y.py`

`io_transport` · `startup and cross-cutting runtime`

This file makes the running system visible from the outside. It connects the application to OpenTelemetry, a standard way to collect traces, metrics, and logs. A trace is like a timeline for one piece of work, showing which steps happened and how long each took. A metric is a counted or measured signal, such as “how many model calls failed” or “how long did a turn take.” A structured log is a log message with searchable fields attached.

The file also protects privacy and reliability. Before logs and trace attributes leave the process, fields with sensitive names such as prompt, content, credential, secret, or token are removed. Very large messages from third-party libraries are replaced with a short note saying which logger emitted them and how large they were. This prevents a library from accidentally dumping a full prompt or huge object to stderr or the log collector.

At startup, init_o11y can install OpenTelemetry exporters that send traces, metrics, and logs to an OTLP HTTP collector. During normal work, helper functions open spans, emit logs, record counters and histograms, and attach the current workspace automatically. The file also supports Datadog service checks, which report the current health of long-running background services in a way that can clear alerts when the service recovers.

#### Function details

##### `init_service_checks`  (lines 271–287)

```
def init_service_checks(url: str | None, env: str | None, api_key: str | None) -> None
```

**Purpose**: Sets up where Datadog service check reports should be sent. It also refuses half-configured setups, because a missing environment tag or API key would make health reports useless or rejected.

**Data flow**: It receives an optional intake URL, environment name, and API key. If there is no URL, it clears the saved intake so later service check submissions do nothing. If there is a URL, it checks that the environment and key are present, then stores them together for emit_service_check to use later.

**Call relations**: This is a startup setup step. It creates the saved _ServiceCheckIntake configuration that emit_service_check later reads when it needs to send a health status to Datadog.

*Call graph*: 1 external calls (__init__).


##### `init_o11y`  (lines 290–316)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Installs the application’s tracing, metrics, and log exporters. It also always installs the log message size guard, even when no collector endpoint is configured.

**Data flow**: It receives an optional OTLP collector endpoint. First it installs the log guard. If the endpoint is absent, it leaves OpenTelemetry in its default no-op state. If present, it builds separate trace, metric, and log URLs, creates OpenTelemetry providers and exporters for each signal, and connects warning-level standard Python logs into the OpenTelemetry log pipeline.

**Call relations**: This is the main observability startup function. It calls _guard_log_messages to protect logs, _otlp_signal_urls to form collector URLs, and _bridge_warning_logs so ordinary warning logs from other modules can reach the collector.

*Call graph*: calls 3 internal fn (_bridge_warning_logs, _guard_log_messages, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 319–331)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Routes warning and error messages from normal Python logging into the OpenTelemetry log exporter. This helps warnings from libraries or other modules show up in the central log system.

**Data flow**: It receives an OpenTelemetry LoggerProvider. It creates a standard logging handler connected to that provider, filters out this project’s own structured logger and OpenTelemetry’s own exporter logs, then attaches the handler to the root logger.

**Call relations**: init_o11y calls this after the OpenTelemetry log provider is ready. From then on, Python logging records at WARNING or above can be forwarded through the same log pipeline as structured UFO logs.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_GuardedRecordFactory.__call__`  (lines 361–373)

```
def __call__(self, *args: object, **kwargs: object) -> logging.LogRecord
```

**Purpose**: Creates Python log records while preventing oversized messages from passing through unchanged. It is a safety net for third-party libraries that may accidentally log huge or sensitive object representations.

**Data flow**: The logging system passes in the normal record creation arguments. This wrapper asks the original factory to make the record, renders the message if it safely can, and checks its length. Short messages are returned unchanged; overly long ones have their text replaced with a short dropped-message notice and their arguments removed.

**Call relations**: _guard_log_messages installs this object as the global log record factory. Every later Python log record flows through its __call__, which uses _rendered_message to see the final text before any handler writes it.

*Call graph*: calls 1 internal fn (_rendered_message).


##### `_guard_log_messages`  (lines 376–380)

```
def _guard_log_messages() -> None
```

**Purpose**: Installs the oversized-log-message guard once. It makes sure the guard is active without wrapping the logging system repeatedly.

**Data flow**: It reads the current Python log record factory. If that factory is already a _GuardedRecordFactory, it does nothing. Otherwise, it wraps the existing factory in _GuardedRecordFactory and sets that wrapper as the new global factory.

**Call relations**: init_o11y calls this at startup before any exporter setup matters. The wrapper it installs later invokes _GuardedRecordFactory.__call__ for each new log record.

*Call graph*: called by 1 (init_o11y); 3 external calls (__init__, getLogRecordFactory, setLogRecordFactory).


##### `_rendered_message`  (lines 383–393)

```
def _rendered_message(record: logging.LogRecord) -> str | None
```

**Purpose**: Safely figures out what text a log record would print. If rendering would fail, it returns nothing instead of breaking the caller’s logging call.

**Data flow**: It receives a logging.LogRecord. If the record has a plain string message and no arguments, it returns that string directly. Otherwise, it asks the record to format itself; if formatting raises an exception, it returns None.

**Call relations**: _GuardedRecordFactory.__call__ uses this helper before deciding whether a log message is too large. This keeps the guard from changing Python logging’s normal tolerance for formatting mistakes.

*Call graph*: called by 1 (__call__); 1 external calls (getMessage).


##### `_otlp_signal_urls`  (lines 396–402)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Builds the exact HTTP endpoints used for OpenTelemetry traces, metrics, and logs. This matters because the OTLP HTTP exporters expect the full signal-specific path, not just a base URL.

**Data flow**: It receives a collector base endpoint, removes any trailing slash, and returns three URLs: one ending in v1/traces, one in v1/metrics, and one in v1/logs.

**Call relations**: init_o11y calls this when an OTLP endpoint is configured. The returned URLs are then handed to the trace, metric, and log exporters.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 405–410)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Finds the current workspace and turns it into metadata for logs and traces. This lets code inside a workspace scope be tagged automatically without every call site passing the workspace ID around.

**Data flow**: It reads current_workspace, a context value set elsewhere in the application. If there is no active workspace, it returns an empty dictionary. If there is one, it returns a dictionary containing that workspace_id as text.

**Call relations**: _emit_log, span, and turn_span call this whenever they create telemetry. It is the shared place where workspace context enters logs and traces.

*Call graph*: called by 3 (_emit_log, span, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 413–419)

```
def current_traceparent() -> str | None
```

**Purpose**: Captures the current trace identity as a W3C traceparent header string. This can be stored or passed through a queue so later work can join the same trace.

**Data flow**: It creates an empty carrier dictionary and asks the trace context propagator to inject the current trace into it. It then returns the traceparent value if one was added, or None if there is no valid current trace.

**Call relations**: This is a public helper for code that admits or schedules work. The traceparent it returns can later be passed into turn_span so a queued turn continues the original trace instead of starting an unrelated one.


##### `turn_profile`  (lines 422–430)

```
def turn_profile(subagent_profile: str | None, spawned: bool=False) -> str
```

**Purpose**: Chooses the profile label used for a turn in traces and metrics. The label stays small and predictable, which keeps metric storage from exploding into too many unique series.

**Data flow**: It receives an optional subagent profile and a flag saying whether this is a spawned agent turn. If a subagent profile is present, it returns that. Otherwise it returns agent for spawned work or main for ordinary member-facing work.

**Call relations**: turn_span calls this when it tags a turn span. Other metric call sites can also use the same rule so traces and metrics use matching profile names.

*Call graph*: called by 1 (turn_span).


##### `turn_span`  (lines 434–470)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None, subagent_profile: str | None, parent_turn_id: UUID | None) -> Iterator[Span]
```

**Purpose**: Opens the main trace span for one durable turn of work. It ties the turn to its conversation, workspace, profile, and possible parent turn.

**Data flow**: It receives turn and conversation IDs, an optional incoming traceparent, an optional subagent profile, and an optional parent turn ID. It builds redacted span attributes, extracts the parent trace context if a traceparent was provided, starts a SERVER span named turn, yields that span to the caller’s code, and closes it when the caller leaves the with-block.

**Call relations**: Code that runs a turn wraps the turn body in this context manager. Inside it, smaller spans created by span become children in the same trace, and logs emitted by log, warn, or log_error can correlate with the active span.

*Call graph*: calls 3 internal fn (_ambient_scope, redact_payload, turn_profile); 2 external calls (get_tracer, cast).


##### `span`  (lines 474–486)

```
def span(name: str, kind: SpanKind=SpanKind.INTERNAL, **attributes: object) -> Iterator[Span]
```

**Purpose**: Opens a smaller trace span for a named step inside existing work, such as a model call, tool call, or sandbox action. It gives operators a timeline of where time was spent.

**Data flow**: It receives a span name, a span kind, and arbitrary attributes. It adds the current workspace, redacts sensitive fields, converts non-simple values to strings, starts the span, yields it to the caller, and closes it when the with-block ends.

**Call relations**: Application code uses this inside broader flows such as turn_span. It calls _ambient_scope and redact_payload before asking OpenTelemetry for a tracer and starting the span.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); 1 external calls (get_tracer).


##### `redact_payload`  (lines 489–495)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Removes sensitive fields from a dictionary before it is logged or attached to a trace. It protects prompts, content, credentials, secrets, and tokens from being exported.

**Data flow**: It receives a mapping of field names to values. For each field, it normalizes the key by removing underscores and dashes and lowercasing it, skips keys on the sensitive list, and redacts the remaining values through redact_value. It returns a new safe dictionary.

**Call relations**: turn_span, span, _emit_log, and redact_value all call this before data leaves the process through telemetry. It is the central redaction gate for structured fields.

*Call graph*: calls 1 internal fn (redact_value); called by 4 (_emit_log, redact_value, span, turn_span).


##### `redact_value`  (lines 498–508)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Converts a value into a JSON-friendly, safely redacted form. It walks nested lists and dictionaries so redaction also applies inside containers.

**Data flow**: It receives any Python object. Simple JSON values pass through unchanged. Dictionaries are converted to string keys and sent through redact_payload. Lists and other non-string sequences are processed item by item. Everything else is turned into text.

**Call relations**: redact_payload calls this for every kept field. When redact_value sees a nested mapping, it calls redact_payload again, creating a recursive cleanup path for nested telemetry data.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 511–517)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured informational log event. It is for normal noteworthy events that operators may want to search or correlate with traces.

**Data flow**: It receives an event name and named fields. It passes them to _emit_log with INFO severity, where workspace metadata is added, sensitive values are redacted, and the log is emitted.

**Call relations**: Application code calls this for ordinary structured logs. It delegates all shared behavior to _emit_log so info, warning, and error logs stay consistent.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 520–522)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured error log event. It uses the same redaction and workspace tagging as normal logs, but marks the event as an error.

**Data flow**: It receives an event name and fields describing the error. It passes them to _emit_log with ERROR severity, which cleans and emits the record.

**Call relations**: Failure paths call this when an operator should see an error in logs. It shares the same _emit_log path used by log and warn.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 525–527)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured warning log event. It is for expected but important conditions that are not full errors.

**Data flow**: It receives an event name and fields. It passes them to _emit_log with WARN severity, where they are scoped, redacted, and sent out.

**Call relations**: Application code calls this for notable conditions. Like log and log_error, it depends on _emit_log for the actual formatting and export.

*Call graph*: calls 1 internal fn (_emit_log).


##### `formatted_stack`  (lines 530–559)

```
def formatted_stack(error: BaseException) -> str
```

**Purpose**: Builds a safe stack trace string for an exception without including the exception message. This avoids leaking sensitive text that may have appeared in an error message.

**Data flow**: It receives an exception. It walks through the exception and its cause or context chain, records each exception class name and traceback frames, and avoids loops. If the result is short enough, it returns it as-is; if too long, it keeps the beginning and end and replaces the middle with an elision note.

**Call relations**: Error-reporting code can call this before passing stack information into log_error. It uses traceback.format_tb to get frame locations while deliberately leaving out exception messages.

*Call graph*: 1 external calls (format_tb).


##### `_emit_log`  (lines 562–580)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Performs the shared work behind info, warning, and error structured logs. It sends each event both through standard Python logging and through OpenTelemetry logs.

**Data flow**: It receives the event name, severity details, a Python logging level, and fields. It adds the ambient workspace, redacts sensitive data, drops fields whose value is None, writes to the ufo standard logger with the cleaned fields under extra data, and emits an OpenTelemetry log record with the same attributes.

**Call relations**: log, warn, and log_error all call this. It calls _ambient_scope and redact_payload before handing records to Python logging and the OpenTelemetry logger.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `_bounded_error_class`  (lines 583–596)

```
def _bounded_error_class(dimensions: dict[str, str]) -> dict[str, str]
```

**Purpose**: Keeps the error_class metric dimension limited to an approved set. This prevents one-off exception class names from creating endless new metric series.

**Data flow**: It receives a dictionary of metric dimensions. If error_class is absent or already one of the allowed names, it returns the dimensions unchanged. If error_class is unknown, it returns a copy with error_class changed to other.

**Call relations**: emit_metric and emit_histogram call this right before recording metric data. This makes the protection happen at the shared metric boundary instead of relying on every caller to remember it.

*Call graph*: called by 2 (emit_histogram, emit_metric).


##### `emit_metric`  (lines 599–609)

```
def emit_metric(name: str, amount: int=1, /, **dimensions: str) -> None
```

**Purpose**: Increments one registered counter metric. It is used for countable events, such as a turn starting or a provider retry happening.

**Data flow**: It receives a metric name, an amount that defaults to 1, and string dimensions. It rejects unknown metric names, creates and caches the OpenTelemetry counter on first use, bounds the error_class dimension, and adds the amount to the counter.

**Call relations**: Application code calls this when an event should be counted. It uses _bounded_error_class before sending the measurement through the OpenTelemetry meter.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_histogram`  (lines 612–634)

```
def emit_histogram(name: str, value: int, /, **dimensions: str) -> None
```

**Purpose**: Records one timing or size-like observation for a registered histogram, usually in milliseconds. Histograms let operators ask questions like “what was the p95 model latency?”

**Data flow**: It receives a histogram name, numeric value, and dimensions. It rejects unknown histogram names and dimensions not declared for that histogram, creates and caches the OpenTelemetry histogram on first use, bounds the error_class dimension, and records the value.

**Call relations**: Application code calls this around measured operations such as model rounds or tool calls. It goes through _bounded_error_class and then the OpenTelemetry meter so exported metrics stay queryable and controlled.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_up_down_metric`  (lines 637–649)

```
def emit_up_down_metric(name: str, amount: int, /, **dimensions: str) -> None
```

**Purpose**: Adds or subtracts from a registered current-state metric. Unlike a simple counter, this can show how many things are active right now.

**Data flow**: It receives a metric name, signed amount, and dimensions. It checks that the metric exists and that all dimensions are allowed, creates and caches the OpenTelemetry up-down counter on first use, and adds the amount with the provided attributes.

**Call relations**: Application code calls this when entering or leaving an active state, such as starting and finishing an in-flight model round. It uses OpenTelemetry’s meter directly after validating the metric name and dimensions.

*Call graph*: 1 external calls (get_meter).


##### `emit_service_check`  (lines 652–684)

```
async def emit_service_check(name: str, status: int, message: str='', /, **tags: str) -> None
```

**Purpose**: Sends the current health status of a registered service check to Datadog. This is for states that need to recover, such as a background sync being OK again after being critical.

**Data flow**: It receives a service check name, status number, optional message, and tags. It rejects unknown check names, returns immediately if service checks were not configured, builds a Datadog report with a stable host name and environment tag, posts it to the configured intake URL with the API key, and raises if Datadog rejects the request.

**Call relations**: Background service code calls this when a monitored service changes or confirms its status. It reads the configuration saved by init_service_checks and uses httpx.AsyncClient to send the report directly to Datadog because OTLP does not carry service checks.

*Call graph*: 1 external calls (AsyncClient).


### `extensions/debugger/ufo_ext_debugger/report.py`

`domain_logic` · `request handling`

This file exists for problems that cannot be fixed inside the current conversation turn. For example, a broken credential, a sandbox that will not start, or a member explicitly asking that something be reported. Instead of trying to solve those cases silently, the tool writes one warning event into the system’s telemetry pipeline, which is the stream engineers use to review issues.

The file is careful about what gets reported. The agent must give a short human-written summary, choose a category so engineers can group and route the issue, state the impact, and say whether the report came from an actual fault or from a member request. If the deploy has a public base URL, the report also includes a debugger link that opens the exact workspace, conversation, and turn. That link is like putting a sticky note on the right page of a notebook, so the engineer does not have to search.

A key safety rule is that the problem text must not contain copied command output or credential-bearing URLs. The transcript already contains the full details, and copied environment text can accidentally include secret run tokens. After logging the warning, the tool returns a short confirmation to the agent. It does not open a ticket, page anyone, store state, or deduplicate repeated reports; it only creates one readable telemetry record.

#### Function details

##### `ReportProblemInput._is_the_agents_own_account`  (lines 110–113)

```
def _is_the_agents_own_account(cls, value: str) -> str
```

**Purpose**: This validates the problem description before a report is accepted. Its main job is to reject text that looks like a URL containing a username or password, because that shape often means a secret was copied from the environment.

**Data flow**: It receives the proposed problem text. It checks the text against a pattern for credential-bearing URLs, such as a URL with user information before the @ sign. If it finds one, it stops the report with an error; otherwise it returns the same text unchanged.

**Call relations**: This runs as part of building and checking a ReportProblemInput object before report_problem receives it. It acts as the safety gate so the later telemetry warning does not accidentally carry a copied credential.


##### `report_problem`  (lines 116–136)

```
async def report_problem(ctx: ToolContext, args: ReportProblemInput) -> ToolResult
```

**Purpose**: This is the tool action that records a serious problem for engineers. It creates a telemetry warning with the problem summary, category, impact, origin, and turn identifiers, then tells the agent the report was made.

**Data flow**: It receives the current tool context and the already-validated report inputs. From the context it reads the public base URL, workspace ID, conversation ID, turn ID, agent ID, and possibly the acting member ID. If a public base URL exists, it builds a debugger link to this exact turn; then it sends all of that information to the warning telemetry system. It returns a ToolResult containing a short text confirmation for the agent to show or use.

**Call relations**: This is the handler connected to the report_problem tool definition in the same file. When the tool is called, it hands the report details to ufo.sdk.o11y.warn so engineers can see the event, then uses TextContent and ToolResult to package the confirmation back to the tool caller.

*Call graph*: 3 external calls (__init__, __init__, warn).

## 📊 State Registers Touched

- `reg-effective-config` — The current trusted settings for how the service should run, including database, provider, deployment, and safety options.
- `reg-member-auth-principals` — The shared answer to who the current person or service is and what member identity they are acting as.
- `reg-turn-state` — The shared status record for each unit of agent work, including claiming, running, completion, failure, parent-child links, and billing markers.
- `reg-transcript-store` — The saved conversation history and compacted summaries that later turns, portals, and auditors read back.
- `reg-background-job-queue` — The shared pool of delayed or recurring work that workers claim, run, retry, and clean up.
- `reg-object-registry` — The shared object front desk that gives stable names, views, permissions, and change history for workspace records.
- `reg-observability-context` — The shared tracing, logging, metrics, health, and redaction context used to understand what happened safely.
- `reg-transcript-access-audit-log` — Durable audit trail of privacy-sensitive transcript reads, especially admin access to another member’s private conversation history.
- `reg-evaluation-run-store` — Durable evaluation test cases, replay runs, comparison results, and self-improvement validation state used to accept or reject changes.
- `reg-debug-feedback-store` — Stored debugger or agent-submitted problem reports and feedback records used for later operator inspection.
