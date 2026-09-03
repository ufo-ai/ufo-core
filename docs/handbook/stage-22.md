# Development, evaluation, debugging, and conformance infrastructure  `stage-22` (cross-cutting infrastructure)

This stage is shared support for building, testing, and operating the system safely. It is not the main user-facing work loop. Instead, it is the test bench, dashboard, and emergency phone for the rest of the code.

The observability toolbox sets up tracing, metrics, health checks, and structured logs. In plain terms, it helps operators see what the system did and whether it is healthy, while filtering sensitive content so private data does not leak into diagnostics. The runtime step reader turns raw saved workflow history into a clear timeline for one conversation turn, so engineers can inspect model calls, tool calls, and other steps in order.

The debugger package exposes a report_problem tool. When an agent hits a serious workspace issue it cannot fix, this records a warning with enough context to find the exact turn.

The evaluation environment provides fake but predictable workplace services, such as mail, calendar, Drive, GitHub, and business tools. The sample extension goes wider: it exercises the full extension boundary with fake tools, jobs, routes, stores, search, models, browser, connectors, and surfaces, proving extensions can plug in correctly without live services.

## Files in this stage

### Diagnostic foundations
Core observability and step-history utilities provide safe operational telemetry and readable turn diagnostics.

### `core/src/ufo/harness/o11y.py`

`orchestration` · `startup and cross-cutting runtime observability`

This file answers a basic operations question: “What is the system doing, how long did it take, and what went wrong?” It connects the app to OpenTelemetry, often shortened to OTel, which is a standard way to send traces, metrics, and logs to monitoring tools. A trace is like a delivery tracking number for one piece of work; spans are the stops along the route. Metrics count or time events. Logs record named events with searchable fields.

At startup, `init_o11y` installs the OTel exporters that send data to an OTLP HTTP collector. It also bridges ordinary Python warning logs into the same pipeline, so important warnings from libraries are not lost. Even when exporting is not configured, the file installs a guard that stops huge third-party log messages from dumping megabytes of prompt-like content to stderr.

During normal work, other code calls helpers like `span`, `turn_span`, `log`, `emit_metric`, and `emit_histogram`. These helpers automatically add the current workspace when one is active, redact sensitive fields such as prompts, content, tokens, and credentials, and keep metric dimensions bounded so monitoring systems do not get flooded with endless unique labels. The file also sends Datadog service checks directly, because service checks represent current health in a way counters cannot.

#### Function details

##### `init_service_checks`  (lines 286–302)

```
def init_service_checks(url: str | None, env: str | None, api_key: str | None) -> None
```

**Purpose**: Configures where Datadog service checks should be sent. If no URL is supplied, service checks quietly become no-ops, which is useful for local or evaluation environments that should not report fleet health.

**Data flow**: It receives an intake URL, an environment name, and an API key. If the URL is missing, it clears the saved destination; if the URL is present, it requires both the environment and key, then stores them in a small immutable `_ServiceCheckIntake` record. The result is global service-check configuration for later submissions.

**Call relations**: This is called during configuration or startup before `emit_service_check` is used. It creates the intake object that `emit_service_check` later reads when it needs to send a current health status to Datadog.

*Call graph*: 1 external calls (__init__).


##### `init_o11y`  (lines 305–331)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Sets up the whole observability pipeline for traces, metrics, and logs. Without this, the app would still run, but operators would lose the exported timing, counting, and log data they rely on to understand production behavior.

**Data flow**: It receives an optional OTLP collector base URL. It always installs the oversized-log-message guard, then either stops if no collector was configured or builds separate trace, metric, and log URLs, creates OTel providers and exporters, and registers them globally. It also adds a warning-log bridge so standard Python warnings can flow into OTel logs.

**Call relations**: This is the startup entry for this file’s setup work. It calls `_guard_log_messages` first, `_otlp_signal_urls` to build exact export URLs, and `_bridge_warning_logs` after the log provider exists.

*Call graph*: calls 3 internal fn (_bridge_warning_logs, _guard_log_messages, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 334–346)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Connects normal Python warning-and-error logs to the OpenTelemetry log pipeline. This matters because many libraries use the standard logging system, not the project’s structured `log` helpers.

**Data flow**: It receives an OTel `LoggerProvider`, creates a `LoggingHandler` for warning-level and higher records, filters out this project’s own structured logger and OTel’s internal logs, and attaches the handler to the root Python logger. After that, qualifying standard logs are exported through the OTel log provider.

**Call relations**: `init_o11y` calls this after installing the OTel log provider. It sits between third-party Python loggers and the collector, while deliberately avoiding feedback loops from OTel exporter failures.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_GuardedRecordFactory.__call__`  (lines 376–388)

```
def __call__(self, *args: object, **kwargs: object) -> logging.LogRecord
```

**Purpose**: Inspects every standard Python log record before any handler sees it and replaces oversized messages with a short safe summary. This prevents accidental leaks and prevents huge library log lines from overwhelming stderr or log storage.

**Data flow**: It receives the raw arguments used to create a `LogRecord`, delegates to the original factory to build the record, then asks `_rendered_message` what the final text would be. If the text is short enough, it returns the record unchanged; if it is too long, it replaces the message with a summary naming the logger, level, dropped length, and limit.

**Call relations**: _guard_log_messages installs this object as Python’s global log-record factory. Once installed, every library log record passes through this call before any formatter, handler, or exporter can write it.

*Call graph*: calls 1 internal fn (_rendered_message).


##### `_guard_log_messages`  (lines 391–395)

```
def _guard_log_messages() -> None
```

**Purpose**: Installs the global guard that trims dangerously large standard Python log messages. It is careful not to wrap the logging system more than once.

**Data flow**: It reads the current Python log-record factory. If the factory is already a `_GuardedRecordFactory`, it leaves it alone; otherwise, it wraps the existing factory in `_GuardedRecordFactory` and installs the wrapper globally.

**Call relations**: `init_o11y` calls this every time observability is initialized. The installed factory then routes each future log record through `_GuardedRecordFactory.__call__`.

*Call graph*: called by 1 (init_o11y); 3 external calls (__init__, getLogRecordFactory, setLogRecordFactory).


##### `_rendered_message`  (lines 398–408)

```
def _rendered_message(record: logging.LogRecord) -> str | None
```

**Purpose**: Safely figures out what text a Python log record would produce. It avoids turning a broken log-formatting call into an exception at the original logging site.

**Data flow**: It receives a `LogRecord`. If the message is already a plain string with no arguments, it returns it directly; otherwise, it tries `record.getMessage()`. If rendering fails, it returns `None` so the caller can leave the record untouched.

**Call relations**: _GuardedRecordFactory.__call__ uses this before deciding whether a record is too large. It relies on Python logging’s own `getMessage` behavior but catches failures locally.

*Call graph*: called by 1 (__call__); 1 external calls (getMessage).


##### `_otlp_signal_urls`  (lines 411–417)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Builds the three exact OTLP HTTP endpoints needed for traces, metrics, and logs. This is needed because the exporter posts to the exact URL it is given and does not add paths by itself.

**Data flow**: It receives a collector base URL, removes any trailing slash, and returns three URLs ending in `v1/traces`, `v1/metrics`, and `v1/logs`. Nothing global is changed.

**Call relations**: `init_o11y` calls this during startup before constructing the OTel exporters. The returned URLs are handed to the trace, metric, and log exporters.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 420–425)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Finds the current workspace and turns it into metadata for logs and spans. This lets callers get workspace tagging automatically instead of passing the workspace ID through every logging call.

**Data flow**: It reads `current_workspace`, a context-local value set elsewhere while work is running. If no workspace is active, it returns an empty dictionary; otherwise, it returns a dictionary containing the workspace ID as a string.

**Call relations**: `turn_span`, `span`, and `_emit_log` call this when they create observability records. It is the shared doorway through which workspace context enters traces and logs.

*Call graph*: called by 3 (_emit_log, span, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 428–434)

```
def current_traceparent() -> str | None
```

**Purpose**: Returns the current trace identity in the standard W3C `traceparent` header format. Code can store or forward this value so later work can reconnect to the same trace after a queue or process boundary.

**Data flow**: It creates an empty carrier dictionary, asks the OTel trace-context propagator to inject the current span context into it, and returns the `traceparent` value if one was produced. If there is no valid current span, it returns `None`.

**Call relations**: This is a public helper for code that admits or schedules later work. The saved value can later be passed into `turn_span`, which extracts it and makes the turn span a child of the earlier trace.


##### `turn_profile`  (lines 437–445)

```
def turn_profile(subagent_profile: str | None, spawned: bool=False) -> str
```

**Purpose**: Chooses the small, stable profile label used for one turn. This keeps metrics readable by separating main turns, spawned agent turns, and named subagent profiles without using high-cardinality identifiers like turn IDs.

**Data flow**: It receives an optional subagent profile name and a flag saying whether the turn was spawned. If a profile name is present, it returns that; otherwise, it returns `agent` for spawned turns and `main` for member-facing turns.

**Call relations**: `turn_span` calls this when tagging a turn trace. Other parts of the system can also use the same helper so trace attributes and metric dimensions describe profiles consistently.

*Call graph*: called by 1 (turn_span).


##### `turn_span`  (lines 449–485)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None, subagent_profile: str | None, parent_turn_id: UUID | None) -> Iterator[Span]
```

**Purpose**: Opens the top-level trace span for one durable turn of work. It ties the turn to its conversation, workspace, profile, and possible parent turn so operators can follow a whole chain of activity.

**Data flow**: It receives turn and conversation IDs, an optional incoming `traceparent`, an optional subagent profile, and an optional parent turn ID. It builds redacted span attributes, extracts a parent trace context if one was supplied, starts an OTel server span named `turn`, yields it to the caller’s `with` block, and closes it when the block exits.

**Call relations**: This is used around turn execution. It calls `_ambient_scope` for workspace tagging, `turn_profile` for the profile label, and `redact_payload` before attributes reach OTel; it then asks OTel for the project tracer and starts the span.

*Call graph*: calls 3 internal fn (_ambient_scope, redact_payload, turn_profile); 2 external calls (get_tracer, cast).


##### `span`  (lines 489–501)

```
def span(name: str, kind: SpanKind=SpanKind.INTERNAL, **attributes: object) -> Iterator[Span]
```

**Purpose**: Opens a smaller trace span for a named step inside some larger piece of work. It is used to time and label stages such as model calls, tool calls, or sandbox operations.

**Data flow**: It receives a span name, an OTel span kind, and any number of attributes. It adds the ambient workspace, redacts sensitive data, converts non-simple values to strings, starts the span as the current span, yields it to the caller, and closes it after the caller’s block finishes.

**Call relations**: This is a general public tracing helper used throughout runtime code. It shares the same redaction path as `turn_span` and uses `_ambient_scope` so nested work automatically carries workspace context.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); 1 external calls (get_tracer).


##### `redact_payload`  (lines 504–510)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Removes fields whose names look sensitive and safely redacts the remaining values. It is the main safety gate before structured data is sent to logs or traces.

**Data flow**: It receives a mapping of field names to values. For each field, it normalizes the name by removing underscores and hyphens and lowercasing it; if that normalized name is sensitive, the field is dropped. All remaining values are passed through `redact_value`, and a JSON-shaped dictionary comes out.

**Call relations**: `_emit_log`, `span`, and `turn_span` call this before exporting data. `redact_value` also calls it recursively for nested dictionaries, so the same rules apply deep inside structured values.

*Call graph*: calls 1 internal fn (redact_value); called by 4 (_emit_log, redact_value, span, turn_span).


##### `redact_value`  (lines 513–523)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Turns an arbitrary Python value into something safe and JSON-like for logs or trace attributes. It preserves simple values, walks through containers, and stringifies anything unusual.

**Data flow**: It receives any value. `None`, booleans, numbers, and strings pass through unchanged; mappings are converted to string-keyed dictionaries and sent through `redact_payload`; non-string sequences become lists with each item redacted; everything else becomes `str(value)`. The output is a JSON-shaped value.

**Call relations**: `redact_payload` calls this for each non-dropped field. When it sees nested mappings, it calls `redact_payload` again, creating a recursive cleanup path.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 526–532)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured informational event. Code uses it for normal noteworthy events that should be searchable and linked to the current trace.

**Data flow**: It receives an event name and keyword fields. It passes them to `_emit_log` with info-level severity, where workspace context is added, sensitive data is removed, and the event is sent to both Python logging and OTel logs.

**Call relations**: This is one of the public logging entry points used by the rest of the app. It delegates all shared behavior to `_emit_log` so info, warning, and error logs stay consistent.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 535–537)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured error event. Code uses it when something failed and operators should be able to find it as an error in logs.

**Data flow**: It receives an event name and keyword fields. It passes them to `_emit_log` with error-level severity, which redacts and exports the event with the current workspace and trace correlation.

**Call relations**: This is the error-level sibling of `log` and `warn`. Like them, it relies on `_emit_log` for the actual logging and export work.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 540–542)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured warning event for expected but important conditions. It signals that something deserves attention without being a full error.

**Data flow**: It receives an event name and keyword fields. It calls `_emit_log` with warning-level severity, so the event is cleaned, scoped, and sent through both logging paths.

**Call relations**: This is the warning-level public logging helper. It shares `_emit_log` with `log` and `log_error`, which keeps severity as the main difference rather than duplicating formatting logic.

*Call graph*: calls 1 internal fn (_emit_log).


##### `formatted_stack`  (lines 545–574)

```
def formatted_stack(error: BaseException) -> str
```

**Purpose**: Builds a safe stack trace string for an exception without including exception messages. This matters because exception messages can contain user text, command output, tokens, or other sensitive data.

**Data flow**: It receives an exception, walks through its cause or context chain while avoiding loops, records each exception class name and traceback frames, and respects Python’s explicit `from None` suppression. If the result is too long, it keeps the beginning and end and replaces the middle with an elision marker. The output is a bounded string for a log field.

**Call relations**: Other error-reporting code can call this before passing stack information into `log_error` or another structured log. Internally it uses Python’s `traceback.format_tb` to format frames while deliberately leaving messages out.

*Call graph*: 1 external calls (format_tb).


##### `_emit_log`  (lines 577–595)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Performs the shared work behind `log`, `warn`, and `log_error`. It adds context, removes sensitive data, drops empty fields, and sends the same event to both standard logging and OTel logs.

**Data flow**: It receives an event name, severity information, a standard Python log level, and fields. It merges in the ambient workspace, redacts the payload, removes fields whose value is `None`, writes a Python log record under the `ufo` logger with the cleaned data, and emits an OTel log record with the same attributes.

**Call relations**: `log`, `warn`, and `log_error` all call this. It calls `_ambient_scope` and `redact_payload` before handing the cleaned event to Python logging and the OTel logger.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `_bounded_error_class`  (lines 598–611)

```
def _bounded_error_class(dimensions: dict[str, str]) -> dict[str, str]
```

**Purpose**: Keeps the `error_class` metric label within a fixed approved set. This protects the monitoring system from being flooded by endlessly unique exception class names.

**Data flow**: It receives a dictionary of metric dimensions. If the `error_class` value is missing or already approved, it returns the dimensions unchanged; if the value is not approved, it returns a copy with `error_class` changed to `other`.

**Call relations**: `emit_metric` and `emit_histogram` call this before sending metric attributes. It is the common guardrail that prevents error-count and latency metrics from creating unplanned time-series labels.

*Call graph*: called by 2 (emit_histogram, emit_metric).


##### `emit_metric`  (lines 614–624)

```
def emit_metric(name: str, amount: int=1, /, **dimensions: str) -> None
```

**Purpose**: Increments a registered counter metric, usually to count that an event happened. It refuses unknown metric names so typos or undeclared measurements do not silently create confusing monitoring data.

**Data flow**: It receives a metric name, an amount, and string dimensions. It checks that the name is registered, creates and caches the OTel counter the first time that name is used, bounds the `error_class` dimension if present, and adds the amount to the counter. The visible result is an exported count increment.

**Call relations**: Runtime code calls this when notable events occur. It uses `_bounded_error_class` before sending attributes to the OTel meter obtained from `opentelemetry.metrics.get_meter`.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_histogram`  (lines 627–649)

```
def emit_histogram(name: str, value: int, /, **dimensions: str) -> None
```

**Purpose**: Records one timing or size observation on a registered histogram, usually in milliseconds. It enforces the allowed dimensions for each histogram so dashboards and Datadog tag allowlists stay aligned.

**Data flow**: It receives a histogram name, a numeric value, and string dimensions. It verifies the name exists, rejects any undeclared dimension, creates and caches the OTel histogram if needed, bounds the `error_class` dimension, and records the value. The output is one observation for later percentile and distribution views.

**Call relations**: Runtime code calls this after operations whose duration should be measured. It shares error-class bounding with `emit_metric` and uses OTel’s meter to create the actual histogram instrument.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_up_down_metric`  (lines 652–664)

```
def emit_up_down_metric(name: str, amount: int, /, **dimensions: str) -> None
```

**Purpose**: Adds or subtracts from a registered current-state metric. This is useful for values that can go up and down, such as the number of active model rounds.

**Data flow**: It receives a metric name, a signed amount, and string dimensions. It verifies the name is registered, rejects undeclared dimensions, creates and caches the OTel up-down counter if needed, and applies the signed amount. The result is a changed current value for that metric series.

**Call relations**: Runtime code calls this around work that starts and later finishes. It uses OTel’s meter directly; unlike counters and histograms here, it does not pass through `_bounded_error_class` because its declared dimensions do not include that field.

*Call graph*: 1 external calls (get_meter).


##### `emit_service_check`  (lines 667–699)

```
async def emit_service_check(name: str, status: int, message: str='', /, **tags: str) -> None
```

**Purpose**: Sends one Datadog service-check status, such as OK or CRITICAL, for a registered check. A service check represents current health, so a later OK can clear an earlier failure in a way a counter cannot.

**Data flow**: It receives a check name, status, optional message, and tags. It verifies the check name, returns immediately if service checks were not configured, builds a Datadog report with a stable host name, environment tag, and caller tags, then posts it to the configured intake URL using `httpx.AsyncClient`. If Datadog returns an error status, it raises through `response.raise_for_status()`.

**Call relations**: This is called by health-reporting paths such as source sync monitoring. It depends on `init_service_checks` having stored an intake URL, API key, and environment; it sends directly to Datadog because the OTLP collector path used for traces, logs, and metrics has no service-check signal.

*Call graph*: 1 external calls (AsyncClient).


### `core/src/ufo/runtime/steps.py`

`domain_logic` · `diagnostic read of a recorded workflow turn`

A conversation turn can involve several hidden steps: the model may think and write, ask to use a tool, receive the tool result, and the workflow may do extra bookkeeping. DBOS records those steps as typed outputs, but that raw record is not shaped for a person or a surface API to read directly. This file acts like a translator from the machine log into a simple timeline.

The main class, DurableTurnSteps, asks DBOS for all recorded steps for a workflow. It first scans model outputs to remember which tool call ID belongs to which tool name. That matters because later tool-result records may only carry the ID, and the timeline should show a useful name instead of an opaque identifier.

Then it walks through each recorded step in order and creates a TurnStep. Each TurnStep says what number the step was, whether it was a model step, a tool step, or a general workflow step, what it was called, when it started and ended, how long it took, and what messages can be reconstructed from it.

The helper _step_messages rebuilds only the message-like parts of the history. Model outputs become assistant messages. Tool results become user-side tool result messages. Image bytes are deliberately not restored; they are replaced with a short note saying how many images were omitted. This keeps the diagnostic view useful without reloading heavy binary data.

#### Function details

##### `_step_messages`  (lines 16–54)

```
def _step_messages(output: object) -> tuple[Message, ...]
```

**Purpose**: This function rebuilds the small message window that a recorded step contributed to the conversation. It turns model outputs into assistant messages, tool dispatch results into tool-result messages, and ignores step types that do not represent visible conversation content.

**Data flow**: It receives one recorded step output object. If the object is a model stream result, it collects the model's reasoning blocks, visible text, and tool calls into an assistant Message; if the stream failed but left partial output, it preserves that partial output instead. If the object is a tool dispatch result, it builds a user Message containing a ToolResultBlock, adding a note when image attachments were omitted. For anything else, it returns an empty tuple, meaning this step adds no reconstructed messages.

**Call relations**: DurableTurnSteps.read calls this while building each TurnStep. _step_messages does the message reconstruction work, then hands the finished Message objects back so the timeline can include what the model or tool effectively saw.

*Call graph*: called by 1 (read); 3 external calls (__init__, __init__, __init__).


##### `DurableTurnSteps.read`  (lines 63–103)

```
async def read(self, workflow_id: str) -> tuple[TurnStep, ...]
```

**Purpose**: This asynchronous method reads the saved DBOS step history for one workflow and converts it into an ordered tuple of TurnStep objects. It is the main entry point for getting a human-readable timeline of a durable conversation turn.

**Data flow**: It takes a workflow ID and asks the DBOS client for that workflow's recorded steps. It scans model results to map tool call IDs to tool names, then walks through every recorded step in order. For each step, it decides whether it is a model step, a tool step, or a general workflow step; converts start and end times from milliseconds into datetime objects; computes a non-negative duration when possible; asks _step_messages to rebuild any conversation messages; and returns all projected TurnStep objects as a tuple.

**Call relations**: Callers use this method when they need to inspect a stored workflow turn. Inside, it delegates timestamp conversion to DurableTurnSteps._timestamp and message reconstruction to _step_messages, then packages everything into TurnStep records for the surface layer to read.

*Call graph*: calls 2 internal fn (_timestamp, _step_messages); 1 external calls (__init__).


##### `DurableTurnSteps._timestamp`  (lines 106–107)

```
def _timestamp(epoch_ms: int | None) -> datetime | None
```

**Purpose**: This helper converts DBOS timestamps from epoch milliseconds into timezone-aware datetime objects. It also safely preserves missing timestamps as None.

**Data flow**: It receives either an integer number of milliseconds since the Unix epoch or None. If the value is None, it returns None. Otherwise, it divides by 1000 to get seconds and creates a UTC datetime object.

**Call relations**: DurableTurnSteps.read calls this for each step's start and completion time. This keeps the main timeline-building code readable and ensures all displayed times use the same UTC conversion.

*Call graph*: called by 1 (read); 1 external calls (fromtimestamp).


### Debugger reporting
The debugger extension package exposes a problem-reporting tool for surfacing serious workspace failures to operators.

### `extensions/debugger/ufo_ext_debugger/__init__.py`

`other` · `package import`

In Python, an `__init__.py` file acts like a label on a folder saying, “this folder is a package you can import from.” This particular file is empty, so it does not define any functions, classes, settings, or startup behavior. Its value is structural: it lets code elsewhere refer to the debugger extension package using normal Python import paths. Think of it like a blank cover page for a chapter in a handbook. The cover page does not contain the chapter’s content, but it tells the reader and the filing system where the chapter begins. Without this file, depending on the Python version and packaging setup, imports for `ufo_ext_debugger` could fail or behave differently, which would make the debugger extension harder or impossible to load in some environments.


### `extensions/debugger/ufo_ext_debugger/report.py`

`domain_logic` · `request handling`

This file is a safety valve for problems that cannot be fixed from inside the current conversation. If a credential is missing, a sandbox is broken, a task fails every time, or a member explicitly asks for a report, the agent can call this tool to alert the people who operate the system.

The file does two main things. First, it defines the shape of a valid report with `ReportProblemInput`. A report must include a short plain-language problem description, a category so engineers can group and route it, an impact level so they know how costly it is, and an origin saying whether the report came from an actual fault or a member request. It also blocks a dangerous kind of pasted text: URLs that contain credentials, because command output or environment variables can accidentally include secrets.

Second, `report_problem` turns that report into a warning record in the telemetry system, which is the system’s stream of operational events. When possible, it adds a debugger link pointing to the exact workspace, conversation, and turn. This is like attaching a map pin to an incident report: the engineer can jump straight to the transcript instead of guessing where it happened. The tool does not store reports, deduplicate them, or send a reply from an engineer. It only logs the event and returns a short confirmation to the agent.

#### Function details

##### `ReportProblemInput._is_the_agents_own_account`  (lines 112–115)

```
def _is_the_agents_own_account(cls, value: str) -> str
```

**Purpose**: This checks that the report’s problem text is the agent’s own short summary, not copied output that may contain secrets. In particular, it rejects URLs that include embedded credentials, a common shape for accidentally leaked proxy or login information.

**Data flow**: It receives the proposed `problem` text from the tool input. It scans that text for a credential-bearing URL pattern; if it finds one, it stops validation with an error. If the text looks safe by this rule, it passes the same text through unchanged.

**Call relations**: This runs automatically while `ReportProblemInput` is being validated before the report tool can proceed. It protects the later `report_problem` step from sending sensitive text into telemetry, so the warning record contains only the agent’s own account of the fault.


##### `report_problem`  (lines 118–139)

```
async def report_problem(ctx: ToolContext, args: ReportProblemInput) -> ToolResult
```

**Purpose**: This is the tool action that records a problem for engineers. It gathers the report details, adds turn and member context, builds a debugger link when a public base URL is available, emits one warning event, and returns a confirmation message.

**Data flow**: It takes the tool context, which contains information such as the current workspace, conversation, turn, agent, authority, and public base URL, plus the already validated report input. It builds a debug URL if possible, extracts the member identity from the authority, and sends a warning event containing the problem, category, impact, origin, IDs, member ID, and link. It then returns a `ToolResult` containing text that tells the agent the report was sent and that no answer will arrive in the conversation.

**Call relations**: This function is called when the `report_problem` tool is invoked. During that flow it asks `authority_member_id` to identify the member connected to the current authority, sends the actual event through `warn`, then wraps the user-facing confirmation using `TextContent` and `ToolResult` so the tool system can return it.

*Call graph*: 4 external calls (__init__, __init__, authority_member_id, warn).


### Evaluation environments
Deterministic fake workplace connectors and a full sample extension make evaluations and extension-boundary conformance tests independent of live services.

### `extensions/eval_env/ufo_ext_eval_env/__init__.py`

`other` · `startup/import`

This package exists to provide a predictable evaluation environment. Instead of connecting to real email inboxes or calendars, it offers fake providers that behave in controlled ways. That matters because tests and evaluations need repeatable results: if a real mailbox changes or a calendar service is unavailable, the system could fail for reasons unrelated to the code being checked. Think of it like a stage set instead of a real office: the doors, desks, and phones are there so actors can practice the scene, but nothing unpredictable from the outside world can interrupt it. This particular file does not define any functions or classes. Its main job is to identify the package and give readers a short summary of what the package is for when it is imported or browsed.


### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `evaluation setup and tool request handling`

This file creates the “test office” that evaluation agents work inside. Instead of calling real Gmail, Google Calendar, GitHub, or billing systems, the agent reaches these services through the same connector path production uses. That matters because the evaluation is testing the real tool-dispatch seam, not a shortcut mock. Think of it like a movie set: the doors, desks, and phones work enough for the scene, but everything is controlled and resettable.

The file has three main parts. First, it declares the available tools and their input shapes, such as sending email, listing events, searching code, or listing GitHub issues. Second, `EvalEnvBroker` executes those tools. Email and calendar changes are stored in database tables scoped to one workspace, so later grading can inspect the exact rows the agent changed. Read-only app data is pulled from a scoped store that evaluations seed beforehand; if data is missing, the code raises an error instead of silently returning an empty answer. Third, `AppActionStore` records fixed application actions and mutates seeded fixture data in predictable ways.

The file also defines a tightly restricted repair agent for product QA. A hook allows that agent to read and edit only one source file, with limits on edit count and byte size. Finally, `manifest()` registers all of these connectors, objects, agents, and hooks with the extension system.

#### Function details

##### `_transaction`  (lines 371–375)

```
def _transaction()
```

**Purpose**: Creates a database transaction for this extension’s private, workspace-aware storage. The email and calendar tool code uses it whenever it needs to read or write durable evaluation state.

**Data flow**: It starts with the extension name and an empty credential declaration, builds an extension context around the scoped store, and returns a transaction object. Code that receives it can safely run database reads and writes inside that transaction.

**Call relations**: The broker’s email and calendar methods call this helper whenever they touch the tables. It centralizes the setup so those methods do not each have to rebuild the same extension context.

*Call graph*: called by 6 (_change_event, _create_event, _list_emails, _list_events, _reply_all_email, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 378–382)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns an ISO-formatted time string into a timezone-aware timestamp. It keeps calendar events consistent even when the input forgot to include a timezone.

**Data flow**: It receives a text timestamp, parses it as a date and time, and if no timezone is present it assumes UTC. It returns a `datetime` value ready to store in the calendar table.

**Call relations**: Calendar creation and update call this before saving event times. It acts as the small translation step between tool input text and database-ready time values.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 390–398)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the tools available for one provider, optionally filtered by a search string. This is how the connector can answer “what can this service do?”

**Data flow**: It receives a workspace id, provider name, and query text. It looks up the provider’s catalog, filters by tool name or description when a query is present, and returns the matching tool descriptions, falling back to the full catalog if nothing matches.

**Call relations**: The broker’s `search` method calls this when the platform asks for connector tools. It does not execute tools; it only describes what is available.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 400–404)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the declared input schema for a specific tool. This lets the connector explain exactly what arguments a tool expects before the tool is called.

**Data flow**: It receives a provider and tool slug, scans that provider’s catalog, and returns the matching tool definition. If the slug is not known, it raises an error for an unknown broker tool.

**Call relations**: Tool execution uses this for seeded read-only providers to confirm that the requested tool actually exists before returning fixture data. It is also part of the broker contract expected by the connector system.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 406–467)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Routes a tool call to the correct deterministic implementation. This is the main doorway used when an agent actually calls one of the eval connectors.

**Data flow**: It receives the workspace, provider, tool name, arguments, account id, and optional idempotency key. It validates the arguments with the right model, calls the matching email, calendar, GitHub-status, code-search, or fixture-backed reader method, and returns a plain dictionary response. If the provider/tool pair is invalid or unseeded, it raises an error.

**Call relations**: The connector runtime calls this after an agent chooses a tool. This method then hands off to focused helper methods such as `_send_email`, `_list_events`, or `_search_code`, so each tool’s behavior stays understandable.

*Call graph*: calls 10 internal fn (_cancel_event, _create_commit_status, _create_event, _list_emails, _list_events, _reply_all_email, _search_code, _send_email, _update_event, schema); 2 external calls (__init__, __init__).


##### `EvalEnvBroker._create_commit_status`  (lines 469–476)

```
async def _create_commit_status(self, args: CreateCommitStatusArgs) -> dict[str, object]
```

**Purpose**: Accepts a GitHub-like commit status and echoes it back as if it had been published. It lets evaluations test workflows that require posting a review verdict without needing a real GitHub repository.

**Data flow**: It receives already-validated status fields such as commit SHA, state, context, description, and target URL. It packages those same values into a response dictionary and returns it without changing external state.

**Call relations**: `execute` calls this for the GitHub commit-status tool. It is intentionally small because the evaluation only needs to verify that the agent attempted a correctly shaped publication.

*Call graph*: called by 1 (execute).


##### `EvalEnvBroker._search_code`  (lines 478–486)

```
async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]
```

**Purpose**: Returns the pre-seeded code search answer for an exact query. It makes code-search evaluations repeatable and fails loudly if the test forgot to seed the expected query.

**Data flow**: It receives a validated search query, builds the scoped-store key for that query, and reads the stored response. If the stored value is a dictionary, it returns a copy; otherwise it raises an error explaining that the fixture is missing.

**Call relations**: `execute` calls this when the agent uses the code-search provider. It depends on evaluation setup having placed the exact response in the scoped store beforehand.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 488–503)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Writes a new outgoing email into the eval mailbox. This gives the agent a realistic “send email” action whose result can be graded later.

**Data flow**: It receives the workspace id and validated email fields. It creates a new id, stores a sent-mail row with the fixed mailbox sender, recipients, subject, body, and current time, then returns the new id, sent status, and recipients.

**Call relations**: `execute` calls this for the email `send_email` tool. It uses `_transaction` so the sent message becomes durable state visible to graders.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._reply_all_email`  (lines 505–546)

```
async def _reply_all_email(self, workspace_id: UUID, args: ReplyAllEmailArgs) -> dict[str, object]
```

**Purpose**: Creates a reply-all message to an existing email in the workspace mailbox. It mirrors normal email behavior while keeping everything inside the eval database.

**Data flow**: It receives a message id and reply body, looks up the original email in the same workspace, builds the recipient list from the original sender and recipients except the eval mailbox itself, adds `Re:` to the subject if needed, stores a new sent email, and returns its id and recipients. If the original message or recipients are missing, it raises an error.

**Call relations**: `execute` calls this for the email `reply_all_email` tool. It reads and writes through `_transaction`, so replies become part of the same mailbox history as seeded and sent messages.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 4 external calls (now, insert, select, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 548–583)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Lists emails from the eval mailbox, optionally filtered by text. Agents use this to inspect seeded inbox messages or their own sent messages.

**Data flow**: It receives a workspace id plus folder, query, and limit. It builds database conditions for that workspace and folder, optionally searches sender, subject, and body, reads the newest matching rows, and returns them as simple email dictionaries.

**Call relations**: `execute` calls this for the email `list_emails` tool. It is the read side of the mailbox, paired with `_send_email` and `_reply_all_email` as the write side.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 585–599)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Adds a confirmed calendar event to the eval calendar. This lets an agent schedule something and leaves a durable record for evaluation.

**Data flow**: It receives event title, start and end strings, and attendees. It parses the times, creates a new event id, inserts a confirmed event row for the workspace, and returns the id and status.

**Call relations**: `execute` calls this for the calendar `create_event` tool. It uses `_moment` for time parsing and `_transaction` for the database write.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 601–614)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Returns calendar events for a workspace, optionally filtered by title. Agents use this to see what meetings exist before changing the calendar.

**Data flow**: It receives a workspace id plus query and limit. It selects matching event rows ordered by start time, converts each row into a public event dictionary, and returns the list.

**Call relations**: `execute` calls this for the calendar `list_events` tool. It uses `_event_json` so listed events have the same response shape as changed events.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 616–628)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Prepares changes for an existing calendar event. It accepts only the fields the caller actually wants to change.

**Data flow**: It receives an event id and optional replacement title, times, and attendees. It builds a change dictionary, parsing any new times, rejects an empty update, and passes the actual database update to `_change_event`.

**Call relations**: `execute` calls this for the calendar `update_event` tool. It acts as the input-to-change translator before `_change_event` performs the shared update-and-return flow.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 630–631)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Marks an event as cancelled while keeping it visible in the calendar. This matches the evaluation rule that cancelled events remain listed with a cancelled status.

**Data flow**: It receives a workspace id and event id, builds a status change to `cancelled`, and asks `_change_event` to apply it. The returned result is the updated event dictionary.

**Call relations**: `execute` calls this for the calendar `cancel_event` tool. It reuses `_change_event` instead of duplicating the update logic.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 633–652)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Applies a set of changes to one calendar event and returns the updated event. It is the shared update engine for both editing and cancelling events.

**Data flow**: It receives a workspace id, event id text, and a dictionary of database column changes. It updates exactly one matching event in that workspace, raises an error if none was found, reloads the row, converts it to response JSON, and returns it.

**Call relations**: `_update_event` and `_cancel_event` call this after deciding what should change. It uses `_transaction` for the write and `_event_json` for the final response.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 654–662)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Converts a database calendar row into the event shape returned by tools. It keeps calendar responses consistent across list, update, and cancel operations.

**Data flow**: It receives a database row with event fields. It turns ids and timestamps into strings and returns a dictionary containing id, title, start, end, attendees, and status.

**Call relations**: `_list_events` uses this for every listed event, and `_change_event` uses it after an event is updated. It is a formatting helper at the edge between storage and tool output.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 664–665)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Declares that eval connector tools do not produce downloadable files. It satisfies the broker interface with an explicit empty answer.

**Data flow**: It receives a tool response but does not inspect it. It always returns an empty tuple, meaning there are no file attachments to expose.

**Call relations**: The connector platform may ask a broker for files after a tool call. For this eval broker, that path ends here because none of these tools create files.


##### `EvalEnvBroker.stage_upload`  (lines 667–676)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for eval providers. These deterministic tools are text-and-data only, so uploads would be outside the intended test surface.

**Data flow**: It receives upload metadata such as provider, tool, filename, type, and checksum. Instead of creating an upload target, it raises an error saying uploads are not accepted.

**Call relations**: If the connector runtime ever tries to prepare an upload for these providers, this method stops it immediately. That keeps the eval environment narrower and more predictable.


##### `EvalEnvBroker.search`  (lines 678–679)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps matching tool descriptions in the broker search response type. It is the connector-facing search endpoint for available tools.

**Data flow**: It receives a workspace, provider, and query. It asks `tools` for matching tool definitions, places them in a `BrokerSearch` object, and returns that object.

**Call relations**: The platform calls this when it wants to discover connector tools. This method delegates the actual filtering to `tools` and only packages the result in the expected response object.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 681–682)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a simple fake bearer credential for an eval connector account. It gives the connector system something credential-shaped without contacting a real auth service.

**Data flow**: It receives a workspace, provider, and account id. It creates a bearer token string containing the account id and returns it as a credential object.

**Call relations**: The connector infrastructure can call this when it needs credentials for a provider. In this eval environment, the credential is only a marker and is not used to authenticate with a real service.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 693–694)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a placeholder authorization URL for an eval provider. It exists because connector providers expect an OAuth-style descriptor, even though eval grants are normally seeded directly.

**Data flow**: It receives a state value and redirect URI. It formats them into an HTTPS URL under the provider’s fake host and returns that string.

**Call relations**: This belongs to the OAuth descriptor used when `manifest` registers providers. Normal evaluations do not rely on a real browser authorization flow, but the method keeps the provider shape complete.


##### `_EvalEnvOAuth.exchange`  (lines 696–699)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the placeholder OAuth flow by returning the fixed eval account id. It avoids real token exchange while still satisfying the connector interface.

**Data flow**: It receives an authorization code, redirect URI, workspace id, and state, but does not need to validate them against a real service. It returns an OAuth account object with the constant eval account id.

**Call relations**: The provider descriptor exposes this to the connector system. If the connect flow is ever exercised in an eval, it produces the same seeded account identity used elsewhere.

*Call graph*: 1 external calls (__init__).


##### `AppActionStore.list`  (lines 706–724)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists recorded application actions as objects the platform can show or query. These actions represent fixed changes applied to seeded app fixtures.

**Data flow**: It receives a tool context and list query, reads stored action records with the action key prefix, converts each stored action into an object row with summary and fields, and returns a paged result.

**Call relations**: The object system calls this when someone lists eval application actions. It uses `_ext` to reach the extension store and `object_page` to apply the requested paging/filtering behavior.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `AppActionStore.get`  (lines 726–734)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AppActionSpec] | None
```

**Purpose**: Fetches the details for one recorded application action. It lets the object system show the original action request and timestamps.

**Data flow**: It receives a context and action name, loads the stored record, and returns `None` if it does not exist. If found, it returns an object detail containing the action spec and creation/update times.

**Call relations**: The object system calls this when a specific action object is requested. It relies on `_stored` for the actual lookup and validation.

*Call graph*: calls 1 internal fn (_stored); 1 external calls (__init__).


##### `AppActionStore.status`  (lines 736–746)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports whether a recorded application action has been applied. In this eval store, existing actions are always treated as applied.

**Data flow**: It receives a context, action name, and optional expected generation. It loads the stored action; if missing, it returns `None`, and if present, it returns a small status dictionary with the result text.

**Call relations**: The object system calls this to check action state after apply-like operations. It uses `_stored`, the same lookup path as `get` and `apply`.

*Call graph*: calls 1 internal fn (_stored).


##### `AppActionStore.apply`  (lines 748–772)

```
async def apply(self, ctx: ToolContext, name: str, spec: AppActionSpec, old: AppActionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Applies one fixed application action, unless the same action was already applied. It makes app-bench changes idempotent, meaning repeating the identical request does not create duplicate effects.

**Data flow**: It receives a context, action name, desired action spec, previous spec, and optional generation. It checks for an existing stored action, rejects conflicting repeats, otherwise mutates the matching seeded fixture through `_apply_fixture`, records the result with timestamps, and stores the action record.

**Call relations**: The object system calls this when an agent applies an eval app action. This method coordinates checking existing state, changing the fixture, and writing the durable action record.

*Call graph*: calls 3 internal fn (_apply_fixture, _ext, _stored); 2 external calls (__init__, now).


##### `AppActionStore.delete`  (lines 774–781)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses to delete eval application actions. Once an action is applied, the evaluation wants that history to stay fixed for grading.

**Data flow**: It receives a context, action name, and optional generation, but does not remove anything. It raises a “verb not supported” error explaining that these actions are immutable.

**Call relations**: If the object system asks this store to delete an action, this method blocks the request. That protects the evaluation record from being erased.

*Call graph*: 1 external calls (__init__).


##### `AppActionStore._apply_fixture`  (lines 783–847)

```
async def _apply_fixture(self, ctx: ToolContext, name: str, spec: AppActionSpec) -> str
```

**Purpose**: Performs the actual deterministic mutation behind an app action. It changes seeded GitHub-like fixture data in one of a few allowed, hard-coded ways.

**Data flow**: It receives a context, action name, and action spec. It chooses the correct seeded fixture, deep-copies the response, finds or adds the relevant issue or pull request, writes the changed fixture under an action-specific key, and returns a human-readable result. If the requested action does not match the allowed contract, it raises an error.

**Call relations**: `apply` calls this only for new actions. This helper is where the visible app action becomes a concrete change in the fixture data that later grading or listing can inspect.

*Call graph*: calls 1 internal fn (_ext); called by 1 (apply); 2 external calls (dumps, loads).


##### `AppActionStore._stored`  (lines 849–851)

```
async def _stored(self, ctx: ToolContext, name: str) -> StoredAppAction | None
```

**Purpose**: Loads and validates one stored application action record. It keeps all callers using the same storage key format and data model.

**Data flow**: It receives a context and action name, reads the matching key from the extension store, and returns either `None` or a validated `StoredAppAction` object.

**Call relations**: `get`, `status`, and `apply` call this before deciding what to show or whether to apply a new action. It uses `_ext` to reach the store safely.

*Call graph*: calls 1 internal fn (_ext); called by 3 (apply, get, status).


##### `AppActionStore._ext`  (lines 853–856)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Returns the extension context from a tool context, or fails if it is missing. This prevents app actions from running without access to the correct scoped store.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it; otherwise it raises an error explaining that the action was dispatched incorrectly.

**Call relations**: The action store’s storage-related methods call this before reading or writing fixture data. It is a guardrail that keeps these operations tied to the eval extension’s own store.

*Call graph*: called by 4 (_apply_fixture, _stored, apply, list).


##### `bound_app_qa_repair_tools`  (lines 869–924)

```
async def bound_app_qa_repair_tools(ctx: HookContext)
```

**Purpose**: Enforces strict tool limits for the special app QA repair agent. It allows that agent to read and make small edits to one source file, and denies everything else.

**Data flow**: It receives a hook context before a tool runs. If the current agent is not the repair agent, it does nothing. For the repair agent, it checks the tool name, file path, edit style, number of edit calls, and total bytes changed; it updates the stored edit budget when allowed, or returns a denial reason when a rule is broken.

**Call relations**: The manifest registers this as a pre-tool-use hook for read and edit tools. It sits between the agent and the filesystem tools, like a security guard checking every attempted action before it happens.

*Call graph*: 2 external calls (__init__, __init__).


##### `manifest`  (lines 944–1000)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension manifest that registers all eval providers, the app action object, the repair agent, and the tool-safety hook. This is the file’s public entry point for the extension system.

**Data flow**: It creates one shared eval broker, wraps each fake provider with its label and OAuth descriptor, includes the object kind and agent provision, attaches the pre-tool-use hook, and returns the completed manifest object.

**Call relations**: The extension loader calls this to discover what the eval environment offers. Everything defined earlier in the file is gathered here so the platform can route connector calls, object actions, agent provisioning, and hooks correctly.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).


### `extensions/sample/ufo_ext_sample.py`

`test` · `cross-cutting conformance runs`

Think of this file as a working showroom for the extension system. Instead of connecting to real APIs, it returns fixed answers and writes small records into the extension store, which is durable storage owned by the extension. That lets conformance tests ask, “Did the core system really call this extension through the public SDK?” and then verify the answer by reading what the extension wrote.

The file declares many constants, small input models, object stores, tool handlers, hook handlers, and backend classes. The final `manifest` function ties them together into one extension: tools can echo text, write notes, edit sample widgets, or ask the user a question; jobs can run off-turn; routes and surfaces can receive HTTP requests; hooks can deny, rewrite, or observe tool calls; fake providers stand in for connectors, search, memory, embeddings, models, browser CDP, sandbox carriers, flags, and more.

Most behavior is intentionally simple and predictable. A search always returns the same hit. The model always streams one reply. The embedder always returns the same vector. This simplicity is the point: if something breaks, it usually means the public extension seam changed or was not wired correctly.

#### Function details

##### `_echo`  (lines 344–348)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: This sample tool echoes back the message it was given and records the call in the extension store. It proves that a tool receives its extension context and can write durable extension data.

**Data flow**: It receives a tool context and an input object with a message. It checks that the extension context is present, stores the input message under a known key, and returns a tool result containing the same message as text.

**Call relations**: Core calls this when the `sample_echo` tool is dispatched. Its output is wrapped as tool content, while its stored record lets later tests confirm the tool really ran.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 351–373)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: This tool writes a note into the sample extension's own database table and reads it back. It proves that extension migrations and workspace-scoped database transactions work end to end.

**Data flow**: It receives note text and the tool context. It uses the workspace id from the extension store, updates or inserts that workspace's note row, reads the row back, and returns the saved note as tool text.

**Call relations**: Core calls it as the `sample_note` tool. It uses SQL insert, update, and select operations inside the extension transaction rather than a mock store.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 376–404)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the sample scheduled job. It records that it ran, then, when workspace features are available, inspects trajectories, proposes an agent prompt change, writes a workspace file, and probes that file.

**Data flow**: It receives an extension context. It first writes a `ran` marker, then reads trajectories if a corpus exists, stores their count, proposes a prompt change for the first one, writes a file into that conversation's workspace, and optionally runs a probe command to read it back.

**Call relations**: Core calls this through the job system. It hands off to the extension context for trajectory lookup, proposal creation, file writing, and probe execution.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 407–410)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is a sample HTTP route handler. It records the posted request body and the extension home URL, then echoes the body back.

**Data flow**: It receives an extension context and request. It reads the request body bytes, decodes them, stores the body plus home URL, and returns a plain text response containing the same body.

**Call relations**: Core calls it for the manifest-declared POST route. It uses the request object for input and the extension context to prove route handlers can access extension services.

*Call graph*: calls 1 internal fn (home_url); 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 449–460)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists sample widget objects stored by the extension. It turns raw stored rows into object-list rows that the platform can page and display.

**Data flow**: It receives a tool context and list query. It reads all extension-store keys with the widget prefix, validates each stored widget, builds display rows with selected fields, and returns a paged object result.

**Call relations**: The object system calls this when listing `sample_widget` objects. It relies on `WidgetStore._ext` for the extension context and uses `object_page` to shape the result.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `WidgetStore.get`  (lines 462–472)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WidgetSpec] | None
```

**Purpose**: This fetches one sample widget by name. It returns the widget's authored spec and timing metadata if the widget exists.

**Data flow**: It receives a tool context and widget name. It reads the matching extension-store key, validates the stored data, and returns an object detail with spec, timestamps, and generation; if missing, it returns nothing.

**Call relations**: The object system calls this for object reads. It uses `WidgetStore._ext` to get the extension store before producing an `ObjectDetail`.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (__init__).


##### `WidgetStore.status`  (lines 474–485)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This checks whether a widget is still at the expected generation. A generation is a version marker used to notice when someone edited an object after it was read.

**Data flow**: It receives a widget name and optional expected generation. It reads the widget, and if it exists, compares its current generation with the expected one. It returns no status data, but may raise an error if the widget changed.

**Call relations**: The object system calls this as part of fenced object operations. It delegates the comparison to `WidgetStore._require_current`.

*Call graph*: calls 2 internal fn (_ext, _require_current).


##### `WidgetStore.apply`  (lines 487–512)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This creates or updates a sample widget. It preserves the original creation time on updates and gives every write a fresh generation marker.

**Data flow**: It receives the new spec, widget name, and expected generation. It reads any existing widget, refuses stale edits, chooses a creation time, writes the new stored widget with a new UUID generation, and returns nothing.

**Call relations**: The object system calls this for create and update verbs. It uses `_require_current` to enforce safe editing before writing through the extension store.

*Call graph*: calls 2 internal fn (_ext, _require_current); 3 external calls (__init__, now, uuid4).


##### `WidgetStore.delete`  (lines 514–527)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This deletes a sample widget, but only if the speaker is a workspace admin. It also refuses stale deletes when the generation no longer matches.

**Data flow**: It receives a widget name and optional expected generation. It reads the current widget, checks the generation if present, asks the tool context whether the speaker is an admin, and then deletes the stored row or raises an admin-required error.

**Call relations**: The object system calls this for deletes. It combines extension-store access, generation checking, and the tool context's admin check.

*Call graph*: calls 3 internal fn (speaker_is_admin, _ext, _require_current); 1 external calls (__init__).


##### `WidgetStore._require_current`  (lines 529–533)

```
def _require_current(self, name: str, stored: StoredWidget, expected_generation: UUID | None) -> None
```

**Purpose**: This small guard checks that a widget has not changed since a caller last read it. It is the sample store's protection against editing stale data.

**Data flow**: It receives a widget name, the stored widget row, and the expected generation. If the stored generation differs from the expected one, it raises an error; otherwise it allows the caller to continue.

**Call relations**: The apply, delete, and status methods call this before accepting an operation that depends on a previous read.

*Call graph*: called by 3 (apply, delete, status).


##### `WidgetStore._ext`  (lines 535–538)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper extracts the extension context from a tool context. It gives the widget store access to the extension's durable store.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it; otherwise it raises an error because the store cannot work without it.

**Call relations**: All widget store read and write methods call this before touching extension storage.

*Call graph*: called by 5 (apply, delete, get, list, status).


##### `RelicStore.list`  (lines 546–550)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists the sample read-only relic object. It demonstrates an object kind that exists for reading but not for authoring.

**Data flow**: It receives a tool context and list query. It creates one row for the canned relic and returns it as a paged object result.

**Call relations**: The object system calls this when listing `sample_relic` objects. It uses the shared object paging helper.

*Call graph*: 2 external calls (__init__, object_page).


##### `RelicStore.get`  (lines 552–557)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[RelicSpec] | None
```

**Purpose**: This fetches the one canned relic by name. It returns nothing for any other name.

**Data flow**: It receives a relic name. If the name matches the fixed relic, it returns detail data with the relic inscription; otherwise it returns no result.

**Call relations**: The object system calls this for relic reads. It builds an `ObjectDetail` using the relic's simple spec model.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.status`  (lines 559–566)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports live status for a relic. The status says the relic was “excavated,” reinforcing that it is system-produced rather than user-authored.

**Data flow**: It receives the usual object status inputs and ignores them. It returns a small status dictionary with the origin value.

**Call relations**: The object system calls this for relic status checks. Unlike widget status, it does not need generation fencing.


##### `RelicStore.apply`  (lines 568–577)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to create or update a relic. It proves that an object kind can be read-only.

**Data flow**: It receives the requested relic write inputs, but does not store anything. It raises a not-supported error with the sample refusal message.

**Call relations**: The object system calls this when someone tries to apply a relic change. It stops the operation immediately.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 579–586)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to delete a relic. It keeps the sample relic read-only in both directions: no writes and no deletes.

**Data flow**: It receives the requested delete inputs, ignores them, and raises a not-supported error. Nothing is changed.

**Call relations**: The object system calls this for relic delete attempts. It mirrors `RelicStore.apply` by refusing mutation.

*Call graph*: 1 external calls (__init__).


##### `_target_record`  (lines 626–637)

```
def _target_record(target: ObjectActionTarget | None) -> dict[str, JsonValue] | None
```

**Purpose**: This converts an optional object action target into plain JSON-friendly data. It makes stored audit records easy to compare in tests.

**Data flow**: It receives a target object or nothing. If there is no target it returns nothing; otherwise it copies the kind, name, agent name, generation, and expected generation into a dictionary, converting UUID-like values to strings.

**Call relations**: Object action tools and bless hooks call this whenever they record what object a tool call was aimed at.

*Call graph*: called by 9 (_audit, _beseech, _bless, _bless_fold, _bless_replace, _calibrate, _divine, _engrave, _polish).


##### `_action_ext`  (lines 640–643)

```
def _action_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper gets the extension context for object action tools. It prevents those tools from silently running without access to their store.

**Data flow**: It receives a tool context. It returns the embedded extension context or raises an error if none is present.

**Call relations**: The audit, polish, engrave, divine, calibrate, bless, and beseech actions call this before writing records.

*Call graph*: called by 7 (_audit, _beseech, _bless, _calibrate, _divine, _engrave, _polish).


##### `_audit`  (lines 646–656)

```
async def _audit(ctx: ToolContext, args: AuditInput) -> ToolResult
```

**Purpose**: This collection-level action records that the workspace was audited. It demonstrates an action bound to a whole object collection rather than one object instance.

**Data flow**: It receives a subject string and tool context. It records the subject, extension name, and any target information, then returns a text confirmation.

**Call relations**: Core calls it as the `audit` action. It uses `_action_ext` for storage and `_target_record` for target details.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_polish`  (lines 659–662)

```
async def _polish(ctx: ToolContext, args: PolishInput) -> ToolResult
```

**Purpose**: This instance action records that a widget was polished a certain number of times. It demonstrates an agent-targetable object action.

**Data flow**: It receives a coat count and tool context. It stores the coat count plus target information, then returns a short confirmation message.

**Call relations**: Core calls it as the `polish` action on a widget. It records the target through `_target_record`.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_engrave`  (lines 665–701)

```
async def _engrave(ctx: ToolContext, args: EngraveInput) -> ToolResult
```

**Purpose**: This side-effecting widget action simulates engraving an existing widget. It updates the widget generation and records an idempotency key so repeating the same call can be safely recognized.

**Data flow**: It receives engraving text, a possible interrupt flag, and a targeted tool context. It verifies there is an instance target, skips duplicate work for the same idempotency key, checks generation safety, updates the stored widget with a new generation, records the action, and returns confirmation unless asked to interrupt once.

**Call relations**: Core calls it as the `engrave` action. It reads and writes widget storage directly and uses `_target_record` to preserve the action target in the audit trail.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 5 external calls (__init__, __init__, __init__, now, uuid4).


##### `_divine`  (lines 704–707)

```
async def _divine(ctx: ToolContext, args: DivineInput) -> ToolResult
```

**Purpose**: This collection action returns a canned “untrusted” answer about widgets. It demonstrates tool output that should be treated as outside data, not trusted instructions.

**Data flow**: It receives a query and context. It stores the query and target data, then returns the fixed divination text.

**Call relations**: Core calls it as the `divine` action. The manifest marks it untrusted, and the function records its call through the extension store.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_calibrate`  (lines 710–715)

```
async def _calibrate(ctx: ToolContext, args: CalibrateInput) -> ToolResult
```

**Purpose**: This collection action records a calibration offset. It demonstrates a profile-only primitive action for the sample object kind.

**Data flow**: It receives an offset and context. It stores the offset plus target data, then returns a text confirmation.

**Call relations**: Core calls it as the `calibrate` action when that profile-held tool is available. It uses the shared action helpers for context and target recording.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_bless`  (lines 718–723)

```
async def _bless(ctx: ToolContext, args: BlessInput) -> ToolResult
```

**Purpose**: This widget action records a blessing phrase, unless the input asks it to fail. It exists mainly so hooks can modify its input, replace its output, or observe its failure.

**Data flow**: It receives a phrase and fail flag. If failure is requested it raises an error; otherwise it stores the phrase and target data and returns a blessing message.

**Call relations**: Core calls it as the `bless` action. The manifest attaches pre- and post-tool hooks specifically to this action.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_beseech`  (lines 726–734)

```
async def _beseech(ctx: ToolContext, args: BeseechInput) -> ToolResult
```

**Purpose**: This final-act action asks the user a question about widgets. It demonstrates returning a structured user-input request inside a tool result.

**Data flow**: It receives a question and context. It stores the question and target data, builds an `AskUserInput` payload, and returns text containing a directive plus the serialized question payload.

**Call relations**: Core calls it as the `beseech` action. It uses `_action_ext` and `_target_record`, then builds the user-question objects.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 4 external calls (__init__, __init__, __init__, __init__).


##### `_bless_fold`  (lines 737–751)

```
async def _bless_fold(ctx: HookContext) -> HookOutcome
```

**Purpose**: This pre-tool hook edits the input to the bless action before the action runs. It appends a suffix to the phrase while preserving the failure flag.

**Data flow**: It receives a hook context. If the payload is a matching pre-tool bless call, it stores the call and target details, then returns a modified input object; otherwise it returns nothing.

**Call relations**: Core calls it before the canonical bless action. Its returned `ModifyInput` is handed back to core so the actual bless tool receives changed input.

*Call graph*: calls 1 internal fn (_target_record); 2 external calls (__init__, __init__).


##### `_bless_replace`  (lines 754–762)

```
async def _bless_replace(ctx: HookContext) -> HookOutcome
```

**Purpose**: This post-tool hook replaces the bless action's successful output. It proves that hooks can observe a result and change what is passed onward.

**Data flow**: It receives a hook context. If the payload is a post-tool-use event, it stores the call, original output, and target, then returns a replacement output string.

**Call relations**: Core calls it after a successful bless action. Its `ModifyOutput` tells core to use the sample replacement text.

*Call graph*: calls 1 internal fn (_target_record); 1 external calls (__init__).


##### `_bless_failure`  (lines 765–769)

```
async def _bless_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: This failure hook records when the bless action errors. It proves failed tool calls go to a separate hook path from successful calls.

**Data flow**: It receives a hook context. If the payload is a post-tool failure, it stores the call and failure output, then returns nothing.

**Call relations**: Core calls it after a failed bless action. It does not repair the failure; it only records that the failure hook fired.


##### `SampleSource.fetch`  (lines 788–797)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This fake source backend returns one page built from its typed configuration. It proves source polling can call an extension-provided backend and receive pages.

**Data flow**: It receives source config, an optional cursor, and auth data. It creates a page whose body and title come from the configured topic and returns it in a sync result with no next cursor.

**Call relations**: Core calls it when syncing the sample source registered during onboarding. It constructs SDK `Page` and `SyncResult` objects.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleIndex.upsert`  (lines 810–812)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This inserts or replaces chunks in the in-memory sample index. A chunk is a piece of text prepared for search.

**Data flow**: It receives a tuple of chunks. For each chunk, it stores it in a dictionary keyed by the chunk digest, replacing any older chunk with the same digest.

**Call relations**: Core calls it when adding indexed content to the sample index backend.


##### `SampleIndex.delete`  (lines 814–816)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This removes all indexed chunks belonging to a given scope. A scope identifies one indexed owner, such as one source or conversation.

**Data flow**: It receives an index scope. It finds stored chunks whose owner kind and owner id match that scope, then deletes those chunks from the dictionary.

**Call relations**: Core calls it when clearing indexed content for a scope. It uses `_in_scope` to decide which chunks belong there.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.has_chunks`  (lines 818–819)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This answers whether the sample index contains any chunks in a given scope. It is a quick presence check.

**Data flow**: It receives a scope. It scans stored chunks and returns true if at least one chunk matches the scope, otherwise false.

**Call relations**: Core calls it when it needs to know whether indexed data already exists. It uses `_in_scope` for the match.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 821–827)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This removes old chunks in a scope while keeping a named set of current chunk digests. It is like cleaning a shelf but leaving the books on a keep list.

**Data flow**: It receives a scope and a set of digests to keep. It deletes stored chunks that are in the scope but whose digest is not in the keep set.

**Call relations**: Core calls it after re-indexing content. It uses `_in_scope` to limit pruning to the requested owner.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 829–838)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This performs a simple word-based search over indexed chunks. It scores chunks by how often query terms appear in their text.

**Data flow**: It receives query text, allowed subjects, owner kind, and limit. It filters chunks to that owner and subjects, counts matching terms, converts positive scores into hits, sorts them highest first, and returns up to the limit.

**Call relations**: Core calls it for lexical retrieval. It uses `_scoped` to filter candidates and `_hit` to shape each result.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 840–848)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This performs a simple vector search over indexed chunks. A vector is a list of numbers used to compare meaning-like similarity.

**Data flow**: It receives an embedding vector, allowed subjects, owner kind, and limit. It filters chunks, computes a dot product score with each chunk embedding, keeps positive scores, sorts them, and returns hits.

**Call relations**: Core calls it for embedding-based retrieval. It uses `_scoped`, `_dot`, and `_hit`.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 850–855)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: This filters stored chunks to the owner kind and subjects a search is allowed to see. It keeps searches from crossing boundaries.

**Data flow**: It receives allowed subjects and an owner kind. It returns only chunks whose owner kind matches and whose subject is in the allowed set.

**Call relations**: The lexical and vector search methods call this before scoring chunks.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 864–865)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This fake embedding backend returns the same vector for every input text. It proves the embedding backend seam is wired without doing real machine learning.

**Data flow**: It receives a tuple of texts. For each text, it returns the fixed sample embedding vector.

**Call relations**: Core calls it when it selects the sample embed backend from the manifest.


##### `_in_scope`  (lines 868–869)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: This helper checks whether a chunk belongs to a requested index scope. It compares the chunk's owner kind and owner id.

**Data flow**: It receives a chunk and scope. It returns true when both owner fields match, otherwise false.

**Call relations**: Sample index delete, presence check, and prune operations call this to avoid touching unrelated chunks.

*Call graph*: called by 3 (delete, has_chunks, prune).


##### `_dot`  (lines 872–875)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: This calculates a dot product score between two vectors. In this sample, that score is used as a simple similarity measure.

**Data flow**: It receives two number tuples. If either is empty it returns zero; otherwise it multiplies matching positions together and sums the results.

**Call relations**: The sample vector search calls this to score each candidate chunk.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 878–887)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: This turns an indexed chunk and score into a search hit object. It copies the fields core expects from index results.

**Data flow**: It receives a chunk and numeric score. It builds and returns a hit containing the chunk digest, owner data, subject, ordinal, text, and score.

**Call relations**: Both lexical and vector search call this after scoring a chunk.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 890–897)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This onboarding step records that setup ran and registers the sample content source. It proves onboarding can perform extension writes and create sources.

**Data flow**: It receives an extension context. It writes an onboarding marker, builds source configuration with the sample topic, and asks core to register that source for the shared subject.

**Call relations**: Core calls it during the sample onboarding step. It hands source registration back to the extension context.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 900–903)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: This pre-tool hook always denies the echo tool. It proves a hook can stop a tool before its handler runs.

**Data flow**: It receives a hook context and ignores the details. It returns a deny outcome with the sample reason.

**Call relations**: Core calls it before `sample_echo` because the manifest binds it to that tool. The returned denial short-circuits tool dispatch.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 906–914)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records successful tool calls. It proves success events reach the post-tool hook path.

**Data flow**: It receives a hook context. If the payload is a successful post-tool event, it stores the tool name; it always returns no modification.

**Call relations**: Core calls it after successful dispatched calls. Failed calls go to the separate failure recorder instead.


##### `_record_post_failure`  (lines 917–923)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records failed tool calls. It proves errors are reported through the failure hook path instead of the success path.

**Data flow**: It receives a hook context. If the payload is a post-tool failure event, it stores the failed tool name and returns nothing.

**Call relations**: Core calls it after a tool reports an error. It complements `_record_post`.


##### `_record_stop`  (lines 926–932)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records the final answer just before a turn stops. It proves turn-end hooks receive the answer being committed.

**Data flow**: It receives a hook context. If the payload is a stop event, it stores the final answer and returns nothing.

**Call relations**: Core calls it at the stop event during a turn. Tests can read the stored answer later.


##### `_record_pre_compact`  (lines 935–942)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records information before conversation compaction. Compaction means shortening stored context so it fits within model limits.

**Data flow**: It receives a hook context. If the payload is a pre-compaction event, it stores the reason and token estimate before compaction.

**Call relations**: Core calls it before compaction. It records the event without changing it.


##### `_record_post_compact`  (lines 945–957)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records information after conversation compaction. It captures the produced summary and token counts before and after.

**Data flow**: It receives a hook context. If the payload is a post-compaction event, it stores the summary and token counts.

**Call relations**: Core calls it after compaction. It pairs with `_record_pre_compact`.


##### `_record_page_change`  (lines 960–973)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records delivered page-change events. It also notes whether a model was wired into the off-turn extension context.

**Data flow**: It receives a hook context. If the payload contains page changes, it stores their page ids and whether `ctx.ext.model` is present.

**Call relations**: Core calls it when page changes are delivered. The stored record proves the data-plane hook ran with expected context.


##### `_SampleConnectorOAuth.authorize_url`  (lines 986–987)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This returns the fake OAuth authorization URL for the sample connector. OAuth is the common “connect your account” web flow.

**Data flow**: It receives a state value and redirect URI. It formats them into the canned connector authorization URL and returns that string.

**Call relations**: Core calls it when beginning connector account linking for the sample provider.


##### `_SampleConnectorOAuth.exchange`  (lines 989–992)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: This completes the fake OAuth exchange and returns a connected account id. It deliberately returns no secret token.

**Data flow**: It receives the OAuth code, redirect URI, workspace id, and state. It ignores the fake inputs and returns an OAuth account object with the fixed sample account id.

**Call relations**: Core calls it after the OAuth callback. The connector broker is treated as the place holding real credentials.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 1005–1006)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: This returns the sample connector broker's tool catalog. The catalog contains one canned broker tool.

**Data flow**: It receives workspace, provider, and query values. It returns a tuple with one broker tool slug and description.

**Call relations**: Core may call it when discovering dynamic connector tools, and `_SampleBroker.search` calls it when building search results.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 1008–1015)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: This returns the input schema for the one sample broker tool. It refuses unknown tool slugs.

**Data flow**: It receives workspace, provider, and slug. If the slug matches the sample tool, it returns a broker tool with a small JSON schema; otherwise it raises an unknown-tool error.

**Call relations**: Core calls it when it needs the detailed schema for a broker-provided tool.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 1017–1034)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: This executes the fake broker tool by echoing the call details back. It proves the connector execution path carries provider, account, arguments, and idempotency data.

**Data flow**: It receives workspace id, provider, slug, arguments, account id, and idempotency key. It rejects unknown slugs; otherwise it returns a dictionary containing those values.

**Call relations**: Core calls it for dynamic connector tool execution. The response can later be inspected or passed to file-output handling.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 1036–1047)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: This extracts produced files from a broker response. It treats a `file_output_urls` argument as the list of files the fake tool produced.

**Data flow**: It receives the broker response dictionary. It looks inside the echoed arguments, and if there is a list of string URLs, it returns broker file objects named from the URL path; otherwise it returns no files.

**Call relations**: Core calls it after broker execution when it needs to bridge provider-produced files into the workspace.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 1049–1075)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: This stages a fake upload destination for connector tool inputs. It also simulates deduplication by returning no upload URL for a file key it has already minted.

**Data flow**: It receives workspace, provider, slug, filename, MIME type, and MD5 hash. It builds a content-addressed key; if already seen, it returns an upload argument without a put URL, otherwise it records the key and returns a file URL plus the argument metadata.

**Call relations**: Core calls it before broker execution when a tool input needs an uploaded file. The sandbox can write to the returned file URL.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 1077–1080)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: This returns a fake broker search result with available tools and a suggested plan. It demonstrates connector search discovery.

**Data flow**: It receives workspace, provider, and query. It reuses the broker's tool catalog and returns it with the fixed search plan.

**Call relations**: Core calls it when searching broker capabilities. It delegates tool listing to `_SampleBroker.tools`.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 1082–1083)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This returns a fake bearer credential for a connected account. A bearer credential is a token sent in an authorization header.

**Data flow**: It receives workspace, provider, and account id. It returns a credential whose bearer token is the sample prefix plus the account id.

**Call relations**: Core calls it when it needs a broker-held credential for egress or connector work.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 1090–1108)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: This sample connector tool resolves the current agent's connected account and records the call. It proves server-side connector execution can find the account grant.

**Data flow**: It receives a tool context and requested connector tool name. It asks the context for the bound sample connector account, stores the account, tool name, and idempotency key, then returns the account id as tool text.

**Call relations**: Core calls it as a manifest-declared connector tool. It hands account resolution to `ToolContext.connector_account`.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 1118–1119)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: This tiny async generator yields one byte chunk. It lets the surface ingestion path stream text into a workspace file.

**Data flow**: It receives bytes. It yields those exact bytes once and then ends.

**Call relations**: `_surface_ingest` calls it when an inbound text attachment should be written as a streamed workspace file.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_model`  (lines 1125–1131)

```
async def _surface_model(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This surface route reports which model was wired into the surface context. It proves surface routes can see the deployed model when one is provided.

**Data flow**: It receives a surface context and request. It returns JSON with the model id, or null if no model is attached.

**Call relations**: Core calls it for the sample surface model route. It does not call the model; it only reports the wiring.

*Call graph*: 1 external calls (JSONResponse).


##### `_surface_ingest`  (lines 1134–1161)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This durable surface route admits an external message into a conversation. It also links identities and can write an inbound file.

**Data flow**: It receives a JSON request body. It parses the external id, email, message, and optional inbound text; finds or creates a linked member; gets or creates a conversation; optionally writes the inbound text as a workspace file; admits the message with an idempotency key; and returns ids plus whether a run was opened.

**Call relations**: Core calls it for the sample durable surface POST route. It uses several `SurfaceContext` methods and `_one_chunk` for file streaming.

*Call graph*: calls 6 internal fn (admit, conversation_for, link_member, linked_member, write_workspace_file, _one_chunk); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_post`  (lines 1164–1165)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: This returns the fake external reply reference for durable surface writeback. It stands in for posting a reply to an outside system.

**Data flow**: It receives a surface context and writeback object. It ignores the details and returns the fixed sample post reference string.

**Call relations**: Core calls it when a durable surface turn needs to post a reply back to the external surface.


##### `_surface_attach`  (lines 1168–1173)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: This copies shared artifacts from the blob store to delivered blob keys. It proves surface attachment delivery can stream bytes out and back in.

**Data flow**: It receives a writeback with artifacts. For each artifact, it builds a delivered key and streams the artifact blob into that new key.

**Call relations**: Core calls it after surface posting when attachments must be delivered. It uses the surface context's blob store streams.


##### `_surface_live_admit`  (lines 1176–1198)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This live surface route admits a message without durable writeback. It demonstrates the “live” mode where clients tail the hub instead of polling stored writebacks.

**Data flow**: It receives a JSON request. It finds or adopts a member identity, gets a conversation, admits a turn, reads the turn owner and recent spend rollup, and returns those details as JSON.

**Call relations**: Core calls it for the live surface POST route. It uses identity adoption, admission, ownership lookup, and spending summary methods on `SurfaceContext`.

*Call graph*: calls 6 internal fn (admit, adopt_identity, conversation_for, linked_member, spend_rollup, turn_owner); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_live_stream`  (lines 1201–1205)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This live surface route streams live turn frames as newline-delimited JSON. Newline-delimited JSON means one JSON object per line.

**Data flow**: It receives a request with a turn id path parameter. It converts the id to a UUID and returns a streaming response that reads frames from `_surface_frames`.

**Call relations**: Core calls it for the live stream GET route. It delegates the actual frame iteration to `_surface_frames`.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 1208–1211)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: This reads live frames for a turn from the surface tail and serializes them. It is the generator behind the streaming response.

**Data flow**: It receives a surface context and turn id. It opens a tail stream, then for each frame it yields the frame as JSON bytes followed by a newline.

**Call relations**: `_surface_live_stream` calls this to supply the response body. It uses `SurfaceContext.tail` to receive live frames.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 1222–1225)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This fake model backend streams one canned response. It proves the model provider seam works without calling a real AI service.

**Data flow**: It receives a model request. It yields a stream-start event, then one text delta with the fixed reply, then usage data saying one input and one output token were used.

**Call relations**: Core calls it when the sample model is selected from the manifest. Pricing and usage tests can consume its emitted events.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 1235–1236)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This returns the fake browser CDP endpoint. CDP is the Chrome DevTools Protocol, a way to control a browser.

**Data flow**: It receives no input beyond the lease. It returns a CDP endpoint object with the fixed sample WebSocket URL.

**Call relations**: Browser-driving code calls it after obtaining a sample CDP lease.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 1238–1239)

```
async def token(self) -> str
```

**Purpose**: This returns a reattach token for the fake browser lease. In this sample, the token is simply the fixed endpoint URL.

**Data flow**: It receives no input beyond the lease and returns the sample CDP URL string.

**Call relations**: Core calls it when it needs a durable handle to reconnect to the same browser lease later.


##### `SampleCdpLease.place_file`  (lines 1241–1242)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: This pretends to place a file for browser use and returns the same path. It models a browser that can already see that sandbox-local path.

**Data flow**: It receives a path and a file-byte reader. It does not read or move the file; it returns the original path unchanged.

**Call relations**: Browser code calls it when preparing files for upload through the leased browser.


##### `SampleCdpLease.download_dir`  (lines 1244–1245)

```
async def download_dir(self) -> str
```

**Purpose**: This reports the fake browser download directory. It tells callers where sample downloads would appear.

**Data flow**: It receives no input beyond the lease and returns the fixed download directory path.

**Call relations**: Browser code calls it when it needs to locate downloads from the lease.


##### `SampleCdpLease.fetch_download`  (lines 1247–1248)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: This reads a downloaded file from the fake download directory. It performs the blocking file read in a worker thread.

**Data flow**: It receives a download GUID. It builds a path under the sample download directory, reads the file bytes, and returns them.

**Call relations**: Browser code calls it to retrieve a completed download. It uses `asyncio.to_thread` so the synchronous disk read does not block the event loop.

*Call graph*: 2 external calls (to_thread, Path).


##### `SampleCdpLease.aclose`  (lines 1250–1251)

```
async def aclose(self) -> None
```

**Purpose**: This closes the fake browser lease. Because the lease owns no real browser, it does nothing.

**Data flow**: It receives no input beyond the lease and returns nothing. No state changes.

**Call relations**: Core calls it during browser lease cleanup through the standard async close protocol.


##### `SampleCdpProvider.lease`  (lines 1261–1262)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: This creates a new fake CDP lease. It proves core can select an extension-provided browser provider.

**Data flow**: It receives an optional sandbox and ignores it. It returns a new `SampleCdpLease`.

**Call relations**: Core calls it when a browser session is requested from the sample CDP provider.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 1264–1265)

```
async def reattach(self, token: str, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: This reconnects to a fake CDP lease from a token. In the sample, every token leads to the same fixed lease.

**Data flow**: It receives a token and optional sandbox, ignores both, and returns a new `SampleCdpLease`.

**Call relations**: Core calls it when reattaching to a browser session through the provider protocol.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 1276–1277)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This fake auth proxy returns a fixed bearer credential. It proves sync or connector flows can request credentials from an extension auth proxy.

**Data flow**: It receives workspace id, provider, and account id. It returns a credential object containing the fixed sample bearer token.

**Call relations**: Core calls it after selecting the sample auth proxy backend from the manifest.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 1289–1297)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This fake search backend returns one canned search hit and a canned direct answer. It proves search provider selection and result shaping work.

**Data flow**: It receives a search query. It ignores the query content and returns search results containing one fixed URL, title, text, and answer.

**Call relations**: Research or search tools call it through the provider interface after core selects the sample backend.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 1299–1300)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This fake fetch call returns canned page text for a requested URL. It proves the optional fetch side of the search provider works.

**Data flow**: It receives a fetch request. It returns a fetched page with the same URL and fixed sample text.

**Call relations**: Search tooling calls it when it wants to fetch a result page through the provider.

*Call graph*: 1 external calls (__init__).


##### `build_flag_provider`  (lines 1303–1316)

```
def build_flag_provider(_cache_ttl_seconds: float) -> InMemoryProvider
```

**Purpose**: This builds an in-memory feature flag provider with one true flag and one false flag. A feature flag is a named switch used to turn behavior on or off.

**Data flow**: It receives a cache time value, though the in-memory provider does not need it. It creates two flags with fixed variants and returns the provider.

**Call relations**: Core calls it when constructing the sample flag backend declared in the manifest.

*Call graph*: 2 external calls (InMemoryFlag, InMemoryProvider).


##### `SampleMemorySearch.search`  (lines 1325–1343)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This fake memory search records the scoped query and returns one canned memory match. It proves memory search providers receive query, subject, and time-window data.

**Data flow**: It receives queries, a source reader with subjects, and optional start and end times. It stores those values in the extension store, converting times to strings, then returns one fixed memory match.

**Call relations**: Core calls it through the sample memory search provider. The stored record lets tests verify the exact scope passed to the provider.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleMemorySearch.listable_kinds`  (lines 1345–1346)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: This reports which memory kinds the sample provider can list. It returns the one fixed sample kind.

**Data flow**: It receives no extra input. It returns a tuple containing the sample memory kind string.

**Call relations**: Core calls it before or during recent-memory listing to know what kinds are supported.


##### `SampleMemorySearch.list_recent`  (lines 1348–1374)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: This fake recent-memory listing records the requested filters and returns one canned memory match. It proves listing passes subject, limit, kind, and cursor data.

**Data flow**: It receives subjects, a limit, optional kinds, and an optional cursor. It stores those values in JSON-friendly form, then returns a listing page with one fixed memory match.

**Call relations**: Core calls it through the memory search provider when asking for recent memories.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCarrier.__init__`  (lines 1386–1387)

```
def __init__(self) -> None
```

**Purpose**: This initializes the fake sandbox carrier's in-memory file storage. A carrier is the backend that creates and talks to sandboxes.

**Data flow**: It receives no inputs beyond the new object. It creates an empty dictionary that will hold written file bytes by path.

**Call relations**: Core constructs `SampleCarrier` through the manifest-declared carrier factory.


##### `SampleCarrier.create`  (lines 1389–1395)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: This creates a fake sandbox handle. It does not start a real container, but returns the fields core expects from one.

**Data flow**: It receives a sandbox spec. It builds a handle with the conversation id, fixed container id, run token, and a runtime root path derived from the conversation id.

**Call relations**: Core calls it when selecting the sample sandbox carrier for a new sandbox.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.attach`  (lines 1397–1405)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: This reattaches to a fake sandbox if a resume id is present. Without a resume id, it says there is nothing to attach to.

**Data flow**: It receives a sandbox spec. If `resume_id` is missing, it returns nothing; otherwise it returns a sandbox handle using that resume id as the container id.

**Call relations**: Core calls it when attempting to resume a sandbox through the carrier interface.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 1407–1414)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: This fake command execution echoes the command arguments. It proves core is talking to the selected carrier.

**Data flow**: It receives a sandbox handle, argument tuple, timeout, and optional model command. It joins the arguments into stdout and returns an execution result with exit code zero.

**Call relations**: Core calls it when executing a command in the sample sandbox carrier.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.write`  (lines 1416–1417)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: This writes file bytes into the fake carrier's in-memory storage. It stands in for copying a file into a sandbox.

**Data flow**: It receives a handle, path, and bytes. It stores the bytes under that path in the carrier's dictionary.

**Call relations**: Core calls it when writing workspace or runtime files into the sandbox.


##### `SampleCarrier.read`  (lines 1419–1422)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: This reads file bytes back from the fake carrier's in-memory storage. It stands in for streaming a file out of a sandbox.

**Data flow**: It receives a handle and path. If the path was not written, it raises file-not-found; otherwise it yields the stored bytes.

**Call relations**: Core calls it when reading sandbox files through the carrier interface.


##### `SampleCarrier.file_op`  (lines 1424–1427)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This delegates structured file operations to the shared UFO filesystem helper. It lets the fake carrier support the same file operation protocol as real carriers.

**Data flow**: It receives a handle, operation name, and parameters. It passes them to `ufo_fs_file_op` along with this carrier and returns that helper's result.

**Call relations**: Core calls it for higher-level sandbox file operations. The shared helper may call the carrier's read and write methods.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `SampleCarrier.dial`  (lines 1429–1430)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: This returns a fake network dial target for a sandbox port. A dial target tells callers what host and port-like endpoint to connect to.

**Data flow**: It receives a handle and port number. It returns a target host made from the fixed container name and port, with TLS disabled.

**Call relations**: Core calls it when it needs to expose or connect to a service inside the sample sandbox.

*Call graph*: 1 external calls (__init__).


##### `resolve_workspace`  (lines 1433–1441)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: This identifies the workspace for a normal HTTP route from a bearer token. It rejects requests without a usable bearer authorization header.

**Data flow**: It receives a request. It parses the `Authorization` header, requires the `Bearer` scheme and a non-empty token, then asks `workspace_claim` to decode the workspace id; otherwise it returns nothing.

**Call relations**: Route specs use it as their identify function, and `resolve_surface_workspace` reuses it for surfaces.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 1444–1446)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: This identifies the workspace for a surface request using the same bearer-token logic as normal routes.

**Data flow**: It receives a request and surface auth object. It passes the request to `resolve_workspace` and returns that result.

**Call relations**: Surface specs use it as their async identify function. It delegates all parsing to `resolve_workspace`.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `_conversation_slot_summary`  (lines 1449–1450)

```
async def _conversation_slot_summary(_ctx: ConversationSlotContext) -> None
```

**Purpose**: This placeholder summary function does nothing for the sample conversation slot. It exists to prove a slot provider can declare a summarize function.

**Data flow**: It receives a conversation slot context and returns nothing. It reads and changes no data.

**Call relations**: Core may call it when summarizing the sample conversation slot.


##### `_conversation_slot_read`  (lines 1453–1454)

```
async def _conversation_slot_read(_ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: This returns an empty set of workspace changes for the sample conversation slot. It proves the read side of a typed conversation slot is wired.

**Data flow**: It receives a conversation slot context. It returns a `WorkspaceChanges` object with no changes and `truncated` set to false.

**Call relations**: Core calls it when reading the sample conversation slot's content.

*Call graph*: 1 external calls (__init__).


##### `_workspace_fact_held`  (lines 1457–1461)

```
async def _workspace_fact_held(ext: ExtensionContext) -> bool
```

**Purpose**: This checks whether the sample workspace fact should appear in the agent prompt. It bases the answer on a key in the extension store.

**Data flow**: It receives an extension context. It reads the known workspace fact key and returns true only if the stored value is exactly true.

**Call relations**: Core calls it during prompt assembly for the manifest-declared workspace fact. If it returns true, the fact line can be included.


##### `manifest`  (lines 1464–1738)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension's declaration of everything it contributes to UFO. It is the central catalog that tells core which tools, jobs, routes, hooks, providers, objects, surfaces, and skills exist.

**Data flow**: It creates a sample broker, then builds and returns a `Manifest` filled with tool definitions, object kinds, jobs, routes, onboarding, prompt sections, workspace facts, agents, subagents, credentials, connector settings, hooks, surfaces, source/index/embed/model providers, hub and terminal backends, skills, CDP, carrier, auth proxy, search, flags, memory search, and conversation slot entries.

**Call relations**: The extension loader calls this entry point when installing or starting the extension. Every other handler and backend in this file is wired into core through the manifest it returns.

*Call graph*: 43 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).

## 📊 State Registers Touched

- `reg-feature-flags` — The shared on/off switches and rollout choices that let operators change behavior without redeploying.
- `reg-extension-registry` — The live catalog of installed extensions and the capabilities each one has registered.
- `reg-model-catalog` — The shared list of available AI models, their abilities, providers, prices, and credential needs.
- `reg-database-schema` — The durable database layout and connection layer used to store and retrieve system records safely.
- `reg-conversation-transcript` — The saved conversation history, turns, compactions, titles, audiences, and generated references.
- `reg-live-workflow-state` — The shared run-state for active turns, including locks, progress, retry guards, cancellation, and completion markers.
- `reg-tool-catalog` — The shared catalog of tools and the policies that decide which tools may run with which permissions.
- `reg-browser-sessions` — The active or reusable Chrome browser sessions, tabs, downloads, and remote-control connections used by agents.
- `reg-connector-brokers` — The shared catalog and runtime state for service connectors, MCP servers, broker accounts, and approved actions.
- `reg-scheduled-jobs` — The durable background work list for timers, recurring conversations, monitors, reports, and long-running tasks.
- `reg-extension-store` — The per-workspace storage area where extensions keep their own durable settings and small JSON records.
- `reg-runtime-instances` — The shared record of which server processes are alive and which background or surface duties they have claimed.
- `reg-observability-trace` — The tracing, metrics, health, logs, and saved step history used to understand what the system did.
- `reg-subagent-state` — The parent-child turn links, delegation contracts, spawn identities, and pending result deliveries for helper agents.
- `reg-self-improvement-state` — The saved failure cases, prompt-change proposals, evaluation results, and approval status used by the self-improvement loop.
- `reg-transcript-access-audit` — The durable audit trail recording privileged reads of private transcripts for later security review.
- `reg-evaluation-fixture-state` — Mutable fake-service data for evaluation runs, such as test mail, calendar, Drive, GitHub, and business-tool records.
- `reg-debug-problem-reports` — Durable agent/operator problem reports and warnings with workspace, turn, and diagnostic context for later debugging.
