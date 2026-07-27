# Cross-cutting observability, usage accounting, operator inspection, and generic utilities  `stage-20` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support for running the system safely and understanding what it is doing. It is not the main conversation engine. Instead, it acts like the control room: it watches activity, records costs, and gives operators safe read-only windows into workspace state.

The observability file, o11y.py, sets up traces, metrics, and structured logs. In plain terms, traces show the path of a request, metrics are counts and timings, and logs are detailed event notes. It also strips sensitive data before sending records outward.

The accounting file records token use, turns that into billing-ready usage data, and checks spend limits for workspaces, members, or agents. This helps prevent unexpected overuse.

The debugger package marker only makes the debugger extension importable. Its surface file provides the actual operator web page and JSON API for viewing conversations, turns, files, transcripts, compactions, and live events for one workspace. The memory surface similarly offers a read-only memory explorer. Together, these pieces help operators inspect problems without changing user data.

## Files in this stage

### Observability and accounting
Core infrastructure records usage and spend while emitting sanitized traces, metrics, and logs for operational visibility.

### `core/src/ufo/accounting.py`

`domain_logic` · `cross-cutting: active during turn billing, sandbox metering, spend checks, reporting, and background usage export`

This file solves a practical money problem: every model call costs something, and the system needs one trustworthy place to record it, summarize it, export it, and stop more work when a budget is reached. The central idea is a ledger, like a checkbook register. Each row records a measured thing, such as model tokens, sandbox model tokens, or sandbox egress request counts. Token rows include their price in micro-USD, meaning millionths of a dollar, so the system can avoid floating-point rounding mistakes.

The file has writers for different sources of usage. Normal turn usage is written once per run attempt. Workspace background jobs are billed to the workspace without a turn. Sandbox egress requests and sandbox model calls are accumulated safely so repeated writes do not lose counts.

It also has an export seam for external billing consumers. Usage is first “minted” into frozen export rows, then read and acknowledged. This makes retries safe: if delivery fails, the same frozen record can be sent again without changing its meaning.

Finally, SpendEvaluator checks spend caps before work proceeds, and SpendRollup builds human-readable spending summaries for command-line or web views. Without this file, costs could be missed, double-counted, exported inconsistently, or allowed to exceed configured limits.

#### Function details

##### `applicable_caps_absent`  (lines 44–50)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: This is a quick shortcut for the common case where no spend cap applies to a specific workspace, member, and agent combination. It helps avoid a database lookup for a few seconds after the system has already confirmed there are no relevant caps.

**Data flow**: It receives a workspace ID, optional member ID, and agent ID. It looks up that exact triple in a small in-memory cache and compares the saved expiry time with the current monotonic clock, which is a clock used for measuring elapsed time. It returns true only if a recent “no caps here” decision is still fresh.

**Call relations**: It calls the system clock through time.monotonic. The related writer is _note_absent_caps, which stores the short-lived cache entry after SpendEvaluator.decide finds no applicable caps.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 53–62)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID]) -> None
```

**Purpose**: This remembers, briefly, that a particular workspace/member/agent combination has no spend caps. It is a performance helper, not the source of truth, so a stale or missing cache entry only causes an extra database check.

**Data flow**: It receives a cache key made from workspace ID, optional member ID, and agent ID. It removes expired entries if the cache has reached its soft size limit, then stores a new expiry time a few seconds in the future. It changes only the in-memory cache.

**Call relations**: SpendEvaluator.decide calls this after reading the database and finding no applicable caps. It uses time.monotonic to set and compare cache lifetimes.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `record_turn_usage`  (lines 65–105)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This records the token cost of one model turn attempt in the ledger. It exists to make sure a real provider charge is billed once, even if workflow replay or parking and resuming happens.

**Data flow**: It receives a database connection, workspace ID, turn ID, model name, usage counts, an attempt ID, and pricing rules. It totals all token categories, skips the write if the total is zero, creates a stable ledger ID, checks whether that ledger row already exists, and inserts a priced token row only if it is new. The output is no returned value; the database ledger is updated if needed.

**Call relations**: It uses ledger_id_for to make the repeat-safe ledger key, Pricing.micro_usd to calculate the cost, and SQLAlchemy database calls to read and insert rows. No in-file caller is shown, but it is designed for the turn-completion billing path.

*Call graph*: calls 1 internal fn (micro_usd); 4 external calls (execute, insert, select, ledger_id_for).


##### `read_turn_cost`  (lines 108–126)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID) -> tuple[int, int, str] | None
```

**Purpose**: This reads the total billed cost for a turn. It is useful because a turn can be split across multiple run attempts, and the final displayed cost should include all of them.

**Data flow**: It receives a database connection and a turn ID. It sums token amounts and priced micro-USD across all token ledger rows for that turn and also reads the model value. It returns nothing if no billing row exists, or returns total tokens, total micro-USD, and model as a tuple.

**Call relations**: It uses SQLAlchemy select and the async database connection to query the ledger. No in-file caller is shown; it is a read-side companion to record_turn_usage.

*Call graph*: 2 external calls (execute, select).


##### `record_workspace_usage`  (lines 129–165)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This bills model usage that belongs to a workspace but not to a specific turn, such as a background job. It keeps workspace-level spend accurate while avoiding false attribution to a member or agent.

**Data flow**: It receives a database connection, workspace ID, model name, usage counts, and pricing rules. It totals the tokens, skips zero usage, prices the usage, creates a fresh ledger row ID, and inserts a token ledger row with no turn ID. The result is a new ledger record for each real completed call.

**Call relations**: It calls Pricing.micro_usd for the price, uuid4 for a fresh row ID, and SQLAlchemy insert through the async connection. No in-file caller is shown; it is meant for background model-call billing.

*Call graph*: calls 1 internal fn (micro_usd); 3 external calls (execute, insert, uuid4).


##### `record_egress_request`  (lines 168–197)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int=1) -> None
```

**Purpose**: This counts sandbox egress requests, meaning requests leaving the sandbox through the proxy. These requests are measured for reporting but are priced at zero, so they do not move spend caps.

**Data flow**: It receives a database connection, workspace ID, turn ID, and a count amount, defaulting to one. It builds a stable ledger ID for the turn’s egress dimension, then inserts a row or atomically adds to the existing amount if the row is already there. It changes the ledger count and returns nothing.

**Call relations**: It uses ledger_id_for for the per-turn egress row and chooses the proper database upsert form for PostgreSQL or SQLite before executing it. No in-file caller is shown; it is intended for the sandbox egress proxy path.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_sandbox_tokens`  (lines 200–251)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This records model tokens used from inside the sandbox through the egress proxy. It keeps those costs separate from normal host-side turn tokens so the two sources can add together without colliding.

**Data flow**: It receives a database connection, workspace ID, turn ID, model, usage counts, and pricing rules. It totals tokens, skips zero usage, calculates the price, builds a stable sandbox-token ledger ID, and inserts or atomically increments the existing row for that turn. The ledger gains both more token count and more priced micro-USD.

**Call relations**: It calls Pricing.micro_usd to price the usage, ledger_id_for to create the dimension-specific ID, and the async database connection to perform an insert-or-update. No in-file caller is shown; it belongs to sandbox proxy metering.

*Call graph*: calls 1 internal fn (micro_usd); 2 external calls (execute, ledger_id_for).


##### `mint_usage_exports`  (lines 276–381)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: This freezes new billable ledger growth into export-intent rows for an external billing consumer. Freezing matters because retries should resend the exact same usage delta, not a newly recalculated one.

**Data flow**: It receives a database connection, workspace ID, consumer name, backfill floor time, and a function that maps model names to credential slots. It reads workspace credential slots, finds ledger rows that have grown beyond what this consumer has already exported, applies settlement rules, and inserts one frozen export row per new delta. It does not return data; it creates durable pending export records.

**Call relations**: It calls datetime and timedelta to calculate settlement timing, SQLAlchemy select and or_ to find eligible growth, and the async connection to insert export rows. It sits between ledger writers such as record_turn_usage and record_sandbox_tokens and later readers such as read_pending_usage_exports.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 384–429)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: This reads already-minted usage exports that have not yet been acknowledged by an external consumer. It provides stable batches for delivery to another billing system.

**Data flow**: It receives a database connection, workspace ID, consumer name, and maximum number of rows. It joins pending export rows to their ledger rows to recover descriptive details like dimension, model, price digest, and turn ID. It returns a tuple of UsageExport objects in mint order.

**Call relations**: It uses SQLAlchemy select through the async connection and constructs UsageExport records from the database rows. It reads the export intents created by mint_usage_exports and supplies the records that ack_usage_exports can later mark as delivered.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 432–456)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: This marks usage export intents as acknowledged after an external billing consumer has accepted them. Acknowledging removes them from future pending reads.

**Data flow**: It receives a database connection, workspace ID, consumer name, and the UsageExport records that were successfully delivered. It builds matching conditions from each export’s ledger ID and starting amount, then updates those rows with an acknowledged timestamp. It returns nothing; the database state changes from pending to acknowledged.

**Call relations**: It uses SQLAlchemy or_ to combine the export keys and SQLAlchemy update through the async connection. It should be called after read_pending_usage_exports has supplied a batch and the outside consumer has accepted it.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 459–462)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: This identifies workspaces that have ever produced ledger usage and therefore might need export work. It is deliberately broad, because checking a workspace with nothing pending is cheap.

**Data flow**: It takes no input. It builds a candidate source based on distinct workspace IDs found in the ledger table. It returns a WorkspaceCandidates object for a background job or scheduler to iterate.

**Call relations**: It hands a ledger workspace query to owner_candidates, which wraps it in the project’s candidate-selection mechanism. No in-file caller is shown; it supports the usage-export job’s workspace discovery.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 497–512)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: This decides whether a turn may proceed under the current spend caps. It returns allow, park, or reject, where park means pause until a cap is raised and reject means decline the turn.

**Data flow**: It receives a database connection and the expected pending cost in micro-USD. It reads all caps that apply to this workspace/member/agent, records a short-lived no-cap cache if none apply, sums current usage for each cap window, adds the pending cost, and compares the result with each limit. It returns a SpendDecision with an outcome and, when blocked, a user-facing message.

**Call relations**: It drives the SpendEvaluator workflow. It calls _applicable_caps first, may call _note_absent_caps for the no-cap fast path, calls _used_micro_usd for each cap it must test, and calls _message when it needs to explain a park or reject decision.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 1 external calls (__init__).


##### `SpendEvaluator._applicable_caps`  (lines 514–542)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: This reads the spend caps that apply to the evaluator’s workspace, member, and agent. It filters out caps for other members or agents so one person’s limit does not block the wrong turn.

**Data flow**: It receives a database connection and reads the evaluator’s stored workspace ID, member ID, and agent ID. It selects matching cap rows from the database and converts each row into a SpendCap object. It returns those caps as a tuple.

**Call relations**: SpendEvaluator.decide calls this at the start of a decision. It uses SQLAlchemy select and or_ through the async connection, then constructs SpendCap records for the later usage checks.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 544–570)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: This calculates how much money has already been spent inside one cap’s rolling time window. It answers the question, “How full is this budget right now?”

**Data flow**: It receives a database connection and one SpendCap. It computes the cutoff time from the cap’s window length, builds the right ledger-summing query for workspace, member, or agent scope, and returns the summed priced micro-USD as an integer. The database is only read, not changed.

**Call relations**: SpendEvaluator.decide calls this once for each applicable cap before comparing usage plus pending cost to the cap limit. It uses datetime and timedelta for the rolling window and SQLAlchemy select through the async connection for the sum.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 572–583)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: This creates the explanation shown when spending is blocked. It turns a technical cap breach into a plain sentence that says what kind of cap was reached and whether the turn was parked or declined.

**Data flow**: It receives the chosen outcome and the list of breached SpendCap objects. It picks the tightest breached cap, converts micro-USD into dollars, and formats a message. It returns that string without reading or writing the database.

**Call relations**: SpendEvaluator.decide calls this only after it has found one or more breaches. The returned message is placed into the SpendDecision that the caller receives.

*Call graph*: called by 1 (decide).


##### `SpendRollup.read`  (lines 633–709)

```
async def read(self, connection: AsyncConnection, window_seconds: int) -> SpendReport
```

**Purpose**: This builds a spending report for a workspace over a recent rolling window. It gives both the total and several breakdowns so people can understand where the money went.

**Data flow**: It receives a database connection and a window length in seconds. It calculates the cutoff time, sums total priced micro-USD for the workspace, then runs grouped queries by ledger dimension, member, agent, and price digest. It returns a SpendReport containing the total and all breakdown records.

**Call relations**: It uses datetime and timedelta to define the reporting window, SQLAlchemy select through the async connection for each grouped sum, and constructs DimensionTotal, SubjectTotal, PriceDigestTotal, and SpendReport objects. No in-file caller is shown; it supports spend views such as command-line and web reporting.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


### `core/src/ufo/o11y.py`

`io_transport` · `startup and cross-cutting during turn/job execution`

This file is the observability hub for the system. Observability means the signals operators use to understand what the program is doing: logs for events, traces for following one request across many steps, and metrics for counts over time. Without this file, failures would be harder to connect to the turn or workspace that caused them, and private fields like prompts or tokens could accidentally end up in logs.

At startup, `init_o11y` can connect the process to an OTLP endpoint. OTLP is the OpenTelemetry Protocol, a standard way to send telemetry to a collector. If no endpoint is given, the program keeps OpenTelemetry's no-op defaults, so calls still work but do not export anything.

During normal work, the file adds useful context automatically. It reads the current workspace from ambient state, like a nametag everyone in a room can see without passing it around by hand. It can open a `turn_span`, which wraps one durable turn in a trace span, and it can preserve trace context across queue boundaries with a `traceparent` header.

For logs, callers use simple helpers such as `log`, `warn`, and `log_error`. These helpers redact sensitive keys, attach workspace context, write to normal Python logging, and emit OpenTelemetry log records that line up with the active trace. For metrics, `emit_metric` increments only approved counters, so misspelled metric names fail loudly instead of silently creating messy data.

#### Function details

##### `init_o11y`  (lines 63–82)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Sets up OpenTelemetry exporting for traces, metrics, and logs when an OTLP collector endpoint is configured. If there is no endpoint, it intentionally leaves the system in a quiet no-export mode.

**Data flow**: It receives an optional collector base URL. When the URL is present, it builds separate URLs for trace, metric, and log uploads, creates OpenTelemetry providers for each signal, attaches batch exporters, and installs them globally so the rest of the program can emit telemetry without knowing the setup details. It also connects ordinary Python warning-and-error logs into the OpenTelemetry log pipeline.

**Call relations**: This is the top-level setup function for the file. It asks `_otlp_signal_urls` to prepare the exact collector endpoints, then creates the OpenTelemetry exporters and providers, and finally calls `_bridge_warning_logs` so important standard logging records are not lost.

*Call graph*: calls 2 internal fn (_bridge_warning_logs, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 85–97)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Connects ordinary Python logging records at warning level or higher to the OpenTelemetry log exporter. This catches important warnings from other modules or libraries that do not use this file's structured log helpers.

**Data flow**: It receives the OpenTelemetry logger provider created during setup. It builds a logging handler that forwards warning, error, and critical records, filters out this project's own structured logger and OpenTelemetry's own internal logs, and attaches the handler to the root Python logger.

**Call relations**: `init_o11y` calls this after creating the OpenTelemetry log provider. It acts as a bridge between the standard logging system and the OpenTelemetry log pipeline, while avoiding feedback loops from exporter failures.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_otlp_signal_urls`  (lines 100–106)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Turns one collector base URL into the three exact HTTP URLs needed for traces, metrics, and logs. This matters because the OTLP HTTP exporter uses exactly the URL it is given and does not add the signal path itself.

**Data flow**: It receives a base endpoint string, removes any trailing slash, and appends the standard paths for traces, metrics, and logs. It returns the three finished URLs as a tuple.

**Call relations**: `init_o11y` calls this before creating exporters. The returned URLs are handed to the trace, metric, and log exporters so each kind of telemetry is posted to the correct collector route.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 109–114)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Reads the current workspace from shared execution context and turns it into metadata for logs and spans. This avoids making every caller pass the workspace ID manually.

**Data flow**: It reads `current_workspace`, which may or may not be set for the current execution scope. If a workspace is present, it returns a small dictionary containing that workspace ID as text; otherwise it returns an empty dictionary.

**Call relations**: `turn_span` uses this when adding workspace information to a trace span. `_emit_log` uses it when adding workspace information to every structured log record.

*Call graph*: called by 2 (_emit_log, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 117–123)

```
def current_traceparent() -> str | None
```

**Purpose**: Captures the currently active trace context as a W3C `traceparent` header string. A `traceparent` is a standard text value that lets later work join the same trace, even if it runs after a queue hop.

**Data flow**: It starts with an empty carrier dictionary, asks the trace context propagator to write the current trace information into it, and then returns the `traceparent` value if one was produced. If there is no valid active span, it returns `None`.

**Call relations**: This helper stands at the boundary where work is admitted or queued. Other code can store its result so a later `turn_span` can continue the same trace instead of starting an unrelated one.


##### `turn_span`  (lines 127–150)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None) -> Iterator[Span]
```

**Purpose**: Opens a trace span around one durable turn. A span is a timed section of work in a trace, and this one marks the turn as server-side work with turn, conversation, and workspace metadata.

**Data flow**: It receives a turn ID, a conversation ID, and an optional incoming `traceparent`. It builds span attributes, adds the ambient workspace if present, redacts the attributes, and extracts a parent trace context when a `traceparent` was supplied. It then starts a span named `turn`, yields it to the caller's `with` block, and closes the span when that block finishes.

**Call relations**: This function uses `_ambient_scope` to attach workspace context and `redact_payload` to make attributes safe. It then asks OpenTelemetry for the project tracer and starts the span, allowing code inside the block to emit logs and child spans that correlate with the turn.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); 2 external calls (get_tracer, cast).


##### `redact_payload`  (lines 153–159)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Removes fields with sensitive names and cleans the remaining values so they are safe to place in logs or span attributes. It is the main privacy guard for structured telemetry in this file.

**Data flow**: It receives a mapping of field names to values. For each key, it normalizes the name by removing underscores and hyphens and lowercasing it, then drops fields such as prompts, content, credentials, secrets, and tokens. For fields that remain, it passes each value to `redact_value` and returns a new JSON-like dictionary.

**Call relations**: `turn_span` uses this before putting attributes on a trace span. `_emit_log` uses it before writing log attributes. `redact_value` calls it again when it finds a nested dictionary, so sensitive keys are removed even inside deeper objects.

*Call graph*: calls 1 internal fn (redact_value); called by 3 (_emit_log, redact_value, turn_span).


##### `redact_value`  (lines 162–172)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Converts one value into something safe and JSON-like for telemetry. It keeps simple values, recursively cleans lists and dictionaries, and turns unusual objects into strings.

**Data flow**: It receives any Python object. If the value is already a basic JSON value, it returns it unchanged. If it is a mapping, it converts keys to strings and sends the dictionary through `redact_payload`; if it is a non-string sequence, it cleans each item. Anything else is converted to text.

**Call relations**: `redact_payload` calls this for every non-sensitive field. When this function sees a nested mapping, it calls `redact_payload` again, creating a back-and-forth recursion that walks through complex payloads safely.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 175–180)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Writes a normal informational structured log event. Callers use it for noteworthy events that are not warnings or errors.

**Data flow**: It receives an event name and any number of named fields. It passes them to `_emit_log` with info-level severity, so the fields are redacted, workspace context is added, and the event is sent through both Python logging and OpenTelemetry logs.

**Call relations**: This is the friendly public helper for info logs. It delegates the real work to `_emit_log`, which keeps redaction and exporting behavior consistent across all log severities.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 183–185)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured error log event. Callers use it when something has gone wrong and should be visible as an error in logs and telemetry.

**Data flow**: It receives an event name and fields describing the problem. It passes them to `_emit_log` with error severity, where they are combined with ambient workspace context, redacted, and emitted.

**Call relations**: This is the public helper for error logs. Like `log` and `warn`, it relies on `_emit_log` so errors are formatted, redacted, and exported in the same way as other structured events.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 188–190)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured warning log event. It is meant for expected but important conditions that an operator may need to notice.

**Data flow**: It receives an event name and extra fields. It sends them to `_emit_log` with warning severity, which adds context, removes sensitive data, and emits the log through the configured channels.

**Call relations**: This is the public helper for warning logs. It shares the same `_emit_log` path as info and error logs, so severity changes without changing the privacy and context rules.

*Call graph*: calls 1 internal fn (_emit_log).


##### `_emit_log`  (lines 193–207)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Performs the shared work behind `log`, `warn`, and `log_error`: add context, redact fields, and send the event to both logging systems. It is the central log emission path.

**Data flow**: It receives an event name, OpenTelemetry severity values, a standard Python logging level, and the caller's fields. It merges the fields with the ambient workspace context, redacts the result, writes a record to the project's standard Python logger, and emits an OpenTelemetry log record with the same event body and attributes.

**Call relations**: `log`, `warn`, and `log_error` all call this with different severities. Inside, it calls `_ambient_scope` to add workspace metadata and `redact_payload` to remove private data before handing the log to Python logging and the OpenTelemetry logger.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `emit_metric`  (lines 210–218)

```
def emit_metric(name: str, amount: int=1, **dimensions: str) -> None
```

**Purpose**: Increments one of the project's approved metric counters. It prevents accidental metric-name drift by rejecting unknown names.

**Data flow**: It receives a metric name, an optional amount, and string dimensions that describe the measurement. It checks that the name is in the allowed metric list, creates and caches the OpenTelemetry counter the first time that name is used, and then adds the amount with the given dimensions. If the name is not approved, it raises an error.

**Call relations**: Other parts of the system call this when a counted event happens, such as a turn starting or a sandbox egress event. It talks directly to OpenTelemetry's meter to create counters lazily, then reuses them from the local cache for later calls.

*Call graph*: 1 external calls (get_meter).


### Debugger inspection surface
The debugger extension package exposes read-only operator views and APIs for workspace conversations, turns, files, transcripts, compactions, and live events.

### `extensions/debugger/ufo_ext_debugger/__init__.py`

`other` · `import/package discovery`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a folder: the label does not do the work itself, but it lets the rest of the system find and open the folder correctly.

Here, the folder is for the `ufo_ext_debugger` extension. Even though this file has no code, it still matters because other parts of the project may import modules from this package by name. Without this file, package discovery or imports may behave differently depending on the Python version and tooling being used.

There are no functions, classes, settings, or side effects here. Importing this package does not start the debugger, configure anything, or change program state. Its role is simply structural: it makes the debugger extension’s directory visible as a package boundary.


### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

This file exists so an authorized operator can look inside a workspace without changing anything. Think of it as a secure observation window: the React app is the window frame, and the API routes here supply the glass panels showing conversations, transcripts, files, and live activity. The actual decision about who may look at which workspace happens before these handlers run, in the shared surface identity and session binding code. By the time a request reaches this file, `SurfaceContext` already represents the workspace the operator is allowed to inspect.

The file serves the compiled frontend from `static/index.html`. If that file has not been built, it fails clearly instead of showing a broken page. Most routes then fetch read-only data from `SurfaceContext` and return it as JSON. They carefully check path values such as conversation IDs and turn IDs, returning a 404-style JSON error when something is missing or malformed.

One route streams live turn updates using Server-Sent Events, a simple browser-friendly way for a server to keep sending new messages over one open HTTP connection. The helper `_sse` turns internal live frames, such as text updates, tool calls, cost ticks, or terminal events, into named events the debugger UI can display raw for diagnosis.

#### Function details

##### `app_page`  (lines 40–45)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the debugger’s web application shell. It lets the browser load the built React app that will then ask the API routes for real data.

**Data flow**: It receives the already-scoped surface context and the HTTP request. It reads the preloaded HTML text from disk state prepared at import time. If the app was not built, it raises a clear error; otherwise it returns the HTML page as the response.

**Call relations**: This is the handler for the debugger surface’s main GET route. After it returns the page, the browser uses the other API handlers in this file to fill the interface with workspace data.

*Call graph*: 1 external calls (HTMLResponse).


##### `workspace_meta`  (lines 48–55)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns basic information about the current workspace, including its workspace ID and, when available, the connected Slack team ID. This helps the debugger UI label what the operator is looking at.

**Data flow**: It receives the scoped context, asks that context whether the Slack surface is installed, and strips the internal `team:` prefix when present. It returns a small JSON object with the workspace ID and optional Slack team value.

**Call relations**: The frontend calls this near page load to establish context for the operator. It relies on `SurfaceContext.installation` for workspace-scoped installation data and then wraps the result in a JSON response.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 58–60)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the conversations visible in the current workspace. This gives the debugger UI its top-level list of sessions to inspect.

**Data flow**: It receives the workspace-scoped context, asks for the conversation list, converts each entry into JSON-friendly data, and returns the whole list as a JSON response.

**Call relations**: The app calls this when it needs a conversation index. It delegates the actual read to `SurfaceContext.list_conversations`, so workspace filtering stays centralized in the context.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 63–68)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the turns for one conversation. A turn is one unit of interaction or processing inside a conversation, so this lets an operator drill into the timeline.

**Data flow**: It reads the `conversation_id` from the request path and tries to parse it as a UUID, which is a standard unique identifier. If parsing fails, it returns a JSON error. Otherwise it asks the context for that conversation’s turns and returns them as JSON.

**Call relations**: This route is used after a conversation is selected. It first depends on `_uuid_param` to validate the path value, then hands the valid ID to `SurfaceContext.list_turns`.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 71–78)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the saved transcript for one conversation. This gives the debugger a readable record of what happened in the conversation.

**Data flow**: It takes a conversation ID from the path, validates it as a UUID, and asks the context for the transcript. If the ID is bad or no transcript exists, it returns a JSON error; otherwise it returns the transcript data as JSON.

**Call relations**: The frontend calls this when showing the full conversation text. This function uses `_uuid_param` for safe path parsing and `SurfaceContext.read_transcript` for the workspace-scoped read.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 81–85)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the compaction records for a conversation. A compaction is where a long conversation history is shortened or summarized so the system can keep working with it.

**Data flow**: It validates the conversation ID from the URL. If valid, it asks the context for the available compaction indexes or records and returns them as a JSON list; if invalid, it returns a JSON error.

**Call relations**: The debugger UI uses this to show where summarization happened in a conversation. It calls `_uuid_param` first, then relies on `SurfaceContext.list_compactions` for the actual workspace-scoped data.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 88–103)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the detailed before-and-after view for one compaction. This helps an operator see exactly what messages were summarized and what summary replaced them.

**Data flow**: It reads the conversation ID and compaction index from the request path. The conversation ID must be a valid UUID, and the index must be digits. It then reads the matching compaction record and returns its index, messages before, messages after, and summary as JSON, or a JSON error if anything is missing.

**Call relations**: This is called when the UI opens a specific compaction entry. It uses `_uuid_param` for the conversation ID and `SurfaceContext.read_compaction` to fetch the detailed record.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 106–111)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files associated with a conversation’s workspace area. This lets the debugger show what files were available or produced during that conversation.

**Data flow**: It validates the conversation ID from the URL. With a valid ID, it asks the context for file entries, converts each entry to JSON-friendly form, and returns the list. With an invalid ID, it returns a JSON error.

**Call relations**: The frontend calls this when displaying the file browser for a conversation. It uses `_uuid_param` before delegating the read to `SurfaceContext.list_workspace_files`.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 114–124)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams the contents of one workspace file to the browser. This lets an operator download or inspect a file without loading the whole thing into memory at once.

**Data flow**: It validates the conversation ID and reads the requested file path from the URL. It asks the context for a stream of that file’s bytes. If the path is invalid, the file is missing, or the ID is bad, it returns a JSON error; otherwise it returns a binary streaming response.

**Call relations**: The debugger UI calls this after a file has been chosen from the file list. It uses `_uuid_param` for the ID, `SurfaceContext.read_workspace_file` for safe access, and `StreamingResponse` to pass the file contents back efficiently.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 127–134)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns detailed information about one turn. This is the static snapshot view of a single unit of work in a conversation.

**Data flow**: It reads a turn ID from the URL and validates it as a UUID. If valid, it asks the context for turn details. Missing or invalid turns become JSON errors; found details are converted into JSON and returned.

**Call relations**: The UI calls this when an operator selects a turn. The function relies on `_uuid_param` for validation and `SurfaceContext.turn_detail` for the workspace-scoped lookup.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 137–142)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a live event stream for one turn. This lets the debugger watch new activity arrive, such as text changes or tool calls, while a turn is still running.

**Data flow**: It validates the turn ID and confirms the turn exists. It also reads the `Last-Event-ID` request header, which tells the server where to resume after a dropped connection. If the turn is valid, it returns a text event stream powered by `_events`; otherwise it returns a JSON error.

**Call relations**: The browser calls this for live updates after choosing a turn. This function checks the turn through `SurfaceContext.turn_detail`, then hands streaming work to `_events`, which converts the context’s live tail into browser-readable events.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 145–147)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Turns the context’s live turn feed into bytes suitable for an HTTP stream. It is the small adapter between internal live frames and the browser’s event stream format.

**Data flow**: It receives the workspace context, a turn ID, and a resume cursor. It asks the context to tail the turn from that cursor, then for each incoming frame it calls `_sse` to format it and yields the resulting bytes.

**Call relations**: `stream` uses this as the body of its streaming response. `_events` stays in the middle: it reads live updates from `SurfaceContext.tail` and passes each one to `_sse` for formatting.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 150–170)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one live frame as a Server-Sent Event, which is the plain-text event format browsers can read over a long-lived HTTP connection. It names each event by what kind of thing happened, such as text, tool, cost, or terminal output.

**Data flow**: It receives a cursor and one live frame. If the cursor is present, it writes it as the event ID so the browser can resume later. It then inspects the frame type, serializes the frame to JSON, and returns the finished event as bytes; if the frame type is unknown, it raises an error rather than silently hiding it.

**Call relations**: `_events` calls this for every live frame it receives. This helper is the final formatting step before the bytes are sent by the streaming HTTP response created in `stream`.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 173–177)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: Safely reads a UUID value from a route parameter. It prevents the rest of the code from treating random text in a URL as a valid conversation or turn ID.

**Data flow**: It takes the request and the parameter name to read. It tries to convert that path value into a UUID object. If conversion works, it returns the UUID; if the text is malformed, it returns `None` so the caller can produce a not-found response.

**Call relations**: Most routes that look up a conversation or turn call this before touching the context. It acts like a small gatekeeper, keeping invalid URL IDs out of the workspace read methods.

*Call graph*: called by 8 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, workspace_file, workspace_files); 1 external calls (UUID).


### Memory exploration surface
The memory extension provides an operator-facing read-only page for inspecting raw workspace memory records.

### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `request handling`

This file is the small web surface for inspecting a workspace’s durable memory store. In plain terms, it is like a viewing window into the memory extension’s filing cabinet: it does not edit anything, but it shows what is inside.

The file serves two things. First, it serves a static HTML page, `memory.html`, which is the browser interface an operator sees. Second, it provides a JSON endpoint that the page can call to fetch every stored memory item for the selected workspace.

Access is not open to everyone. The routes rely on the shared operator session flow, where the request must already be tied to an operator identity and a workspace. That workspace binding matters because memory records are workspace-scoped: the code should only read the memory belonging to the chosen workspace.

The important detail is that this surface reads from the memory extension’s own storage table, not from some central application table. To do that, the `memories` endpoint creates an `ExtensionContext` with a scoped store for the `memory` extension, then asks the store layer for an inventory of memory items. The result is returned as JSON for the page to display.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the memory explorer web page to the browser. It is used when an operator opens the surface itself, before the page asks for any memory data.

**Data flow**: It receives the surface context and the incoming web request, but it does not need to inspect them. It checks whether the HTML file was successfully loaded when the module started. If the file is present, it wraps that HTML text in an HTTP HTML response; if the file is missing, it raises an error so the missing page is noticed instead of silently showing a broken interface.

**Call relations**: This function is attached to the GET route for the surface’s root path. When that route is requested, it hands the already-loaded page content to `HTMLResponse`, which turns the text into a browser-ready response.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the workspace’s memory records as JSON. The browser page uses it to show the operator what memory items exist and what recall can draw from.

**Data flow**: It receives the surface context, which includes the currently bound workspace ID, and the incoming web request. It builds an extension-specific context so it can open the memory extension’s own scoped database transaction. It then asks `inventory` for the memory items in that workspace, converts each item into JSON-friendly data, and returns the list in a JSON response.

**Call relations**: This function is attached to the GET route `api/memories`, which the memory explorer page calls after loading. It creates `ScopedStore`, `CredentialAccess`, and `ExtensionContext` so the storage read happens through the memory extension’s proper workspace-scoped path, then delegates the actual lookup to `ufo_ext_memory.store.inventory` and wraps the result with `JSONResponse` for the HTTP client.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).

## 📊 State Registers Touched

- `reg-effective-config` — The running service’s merged settings, such as required keys, enabled backends, safety options, and service behavior.
- `reg-workspace-tenant-record` — The customer workspace record that all users, conversations, data, tools, and billing are kept under.
- `reg-member-session-auth` — The signed-in person’s identity and session proof used to decide who is making a request.
- `reg-surface-installation-binding` — The stored connection between outside channels like Slack, web chat, or terminal clients and an internal workspace conversation.
- `reg-conversation-thread-state` — The saved conversation identity and history that let the system continue the same thread over time.
- `reg-turn-record-lifecycle` — The durable record of each unit of agent work, including who started it, its status, parent links, and final result.
- `reg-runtime-presence` — The live roll-call of server and worker processes used to recover abandoned work safely.
- `reg-seat-entitlement` — The workspace membership and seat-limit state that decides which people the agent may serve.
- `reg-spend-ledger-billing` — The shared usage ledger, prices, caps, exports, and billing records used to track and limit spending.
- `reg-model-catalog-pricing` — The shared list of available AI models, provider details, limits, credentials, and prices.
- `reg-blob-storage-backend` — The shared large-file storage used for workspace files, transcripts, source snapshots, and artifacts.
- `reg-source-page-sync-state` — The saved sources, pages, sync cursors, deletion markers, and retry state for imported external content.
- `reg-search-index-memory-graph` — The shared recall stores for searchable chunks, remembered facts, memory pages, and knowledge-graph links.
- `reg-live-stream-hub` — The live stream of turn updates that clients can watch and replay after reconnecting.
- `reg-transcript-compaction-store` — The saved conversation transcript and compacted summaries used to rebuild context and inspect past turns.
- `reg-artifact-download-tokens` — The short-lived signed passes that let private files produced by a turn be downloaded safely.
- `reg-observability-trace-context` — The trace, metric, and log context that follows requests and turns so operators can understand what happened.
- `reg-row-level-security-context` — The database safety context that keeps each workspace’s rows separated even when code uses shared tables.
- `reg-slack-connect-provisioning-state` — The hosted-control-plane state for creating, retrying, and inspecting customer Slack Connect channels during workspace onboarding.
