# Cross-cutting diagnostics, demos, evaluations, and conformance fixtures  `stage-21` (cross-cutting infrastructure)

This stage is shared support that helps people inspect, test, and improve the system without touching real user data or real outside services. The observability toolbox records logs, traces, and metrics, which are like the system’s dashboard gauges, while trimming sensitive or huge text before it is stored. The seed file builds a “kitchen sink” demo conversation so the web portal can show many features safely.

Several files provide controlled test extensions. The debugger and evaluation packages make their extension code importable. The evaluation manifest supplies fake email, calendar, and code-search connectors, so tests can use the normal connector route with predictable data. The sample skill probe prints a fixed success message, and the sample extension exercises nearly every public extension hook to prove third-party-style extensions still work.

The self-improvement files form a careful offline loop. They collect past failed tool conversations, talk to a language model through a small wrapper, propose better agent instructions, replay old conversations without running real tools, judge the results, and use a safety gate before accepting any new prompt.

## Files in this stage

### Runtime diagnostics and demos
These files provide operator-facing diagnostics, safe demo data, and the debugger package seam used by inspection tooling.

### `core/src/ufo/o11y.py`

`io_transport` · `startup and cross-cutting during request, turn, job, and error handling`

This file answers a practical question: when something is slow, broken, or surprising, how can a human find out what happened without exposing private data? It sets up OpenTelemetry, a standard way to send traces, metrics, and logs to an outside collector. A trace is like a timeline for one piece of work. A metric is a counted or measured value, such as “how many model calls failed.” A structured log is a named event with searchable fields.

At startup, `init_o11y` can connect the process to an OTLP endpoint, which is the HTTP destination used by OpenTelemetry collectors. If no endpoint is provided, the system still installs a safety guard for log messages. That guard stops third-party libraries from accidentally writing huge prompt-like text to stderr or to the collector.

During normal work, callers use helpers such as `turn_span`, `span`, `log`, `warn`, `log_error`, and the metric emitters. These helpers automatically attach the current workspace when one is available, so every record can be tied back to where it happened. Before anything leaves the process, fields with sensitive names such as prompt, content, token, or secret are removed, and nested values are cleaned recursively.

The file also keeps metric labels controlled. For example, unknown error class names are folded into `other`, which prevents monitoring systems from being flooded with endless one-off metric series.

#### Function details

##### `init_o11y`  (lines 250–276)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Sets up observability for the process. If an OpenTelemetry collector endpoint is provided, it wires traces, metrics, and logs to that collector; if not, it leaves OpenTelemetry mostly inactive but still installs the oversized-log safety guard.

**Data flow**: It receives an optional collector base URL. First it installs the log message guard. If the URL is missing, it stops there. If the URL is present, it builds separate trace, metric, and log URLs, creates OpenTelemetry providers for each signal, and registers them globally so later spans, metrics, and logs know where to go.

**Call relations**: This is the top-level setup point for the file. It calls `_guard_log_messages` before anything else, uses `_otlp_signal_urls` to build the three export destinations, and then calls `_bridge_warning_logs` so ordinary Python warning-and-error logs can also reach the OpenTelemetry log pipeline.

*Call graph*: calls 3 internal fn (_bridge_warning_logs, _guard_log_messages, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 279–291)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Connects normal Python warning and error logs to the OpenTelemetry log exporter. This makes warnings from other modules or libraries visible in the central collector instead of disappearing into local stderr only.

**Data flow**: It receives the OpenTelemetry logger provider created during setup. It creates a logging handler that only accepts WARNING and above, filters out this project’s own structured logger and OpenTelemetry’s own internal logs, and attaches the handler to Python’s root logger.

**Call relations**: `init_o11y` calls this after the OpenTelemetry log provider exists. From then on, warning-level records from ordinary Python logging can flow into the same external log pipeline as structured UFO logs.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_GuardedRecordFactory.__call__`  (lines 321–333)

```
def __call__(self, *args: object, **kwargs: object) -> logging.LogRecord
```

**Purpose**: Creates normal Python log records, but replaces extremely large rendered messages with a short safe summary. This prevents accidental megabyte-sized logs, especially ones containing prompts or other private text.

**Data flow**: It receives the arguments Python logging uses to make a log record. It asks the original factory to create the record, renders the message safely with `_rendered_message`, and checks its length. Short messages pass through unchanged. Oversized messages have their text replaced with a note saying which logger emitted it, what level it was, and how many characters were dropped.

**Call relations**: Python logging calls this automatically after `_guard_log_messages` installs it as the global record factory. It relies on `_rendered_message` to inspect what would actually be written before any handler or exporter sees the record.

*Call graph*: calls 1 internal fn (_rendered_message).


##### `_guard_log_messages`  (lines 336–340)

```
def _guard_log_messages() -> None
```

**Purpose**: Installs the oversized-message guard into Python’s logging system. It is careful not to install the guard twice.

**Data flow**: It reads the current global log record factory. If that factory is already `_GuardedRecordFactory`, it does nothing. Otherwise, it wraps the existing factory and sets the wrapper as the new global factory.

**Call relations**: `init_o11y` calls this every time observability is initialized. After it runs, every future standard-library log record passes through `_GuardedRecordFactory.__call__` before reaching handlers.

*Call graph*: called by 1 (init_o11y); 3 external calls (__init__, getLogRecordFactory, setLogRecordFactory).


##### `_rendered_message`  (lines 343–353)

```
def _rendered_message(record: logging.LogRecord) -> str | None
```

**Purpose**: Safely finds the final text of a Python log record. It avoids crashing the caller if a bad logging format string would normally fail while being rendered.

**Data flow**: It receives a log record. If the record is already a plain string with no arguments, it returns that string directly. Otherwise, it asks the record to render itself. If rendering raises an exception, it returns `None` instead of letting the exception escape.

**Call relations**: `_GuardedRecordFactory.__call__` uses this when deciding whether a log message is too large. This keeps the guard from changing Python logging’s usual behavior for malformed log calls.

*Call graph*: called by 1 (__call__); 1 external calls (getMessage).


##### `_otlp_signal_urls`  (lines 356–362)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Builds the three exact HTTP URLs used to export traces, metrics, and logs. This matters because the OpenTelemetry HTTP exporter expects the full endpoint path, not just the server base address.

**Data flow**: It receives a base OTLP endpoint string, removes any trailing slash, and appends `v1/traces`, `v1/metrics`, and `v1/logs`. It returns those three complete URLs as a tuple.

**Call relations**: `init_o11y` calls this during startup before creating exporters. The returned URLs are handed to the trace, metric, and log exporters so each signal is posted to the collector path that understands it.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 365–370)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Finds the current workspace and turns it into metadata for logs and spans. This lets callers avoid passing the workspace ID by hand everywhere.

**Data flow**: It reads `current_workspace`, a context-bound value from the database layer. If there is no current workspace, it returns an empty dictionary. If there is one, it returns a dictionary containing the workspace ID as text.

**Call relations**: `turn_span`, `span`, and `_emit_log` call this before emitting observability data. It is the shared doorway through which workspace context gets attached across traces and logs.

*Call graph*: called by 3 (_emit_log, span, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 373–379)

```
def current_traceparent() -> str | None
```

**Purpose**: Returns the current trace context as a `traceparent` header string, when a valid span is active. A `traceparent` header is a standard W3C text value that lets work in one place continue the same trace somewhere else.

**Data flow**: It creates an empty carrier dictionary, asks the OpenTelemetry trace context propagator to inject the current span context into it, and then returns the `traceparent` value if one was written. If no trace context is active, it returns `None`.

**Call relations**: No in-file caller is listed, which suggests other parts of the system call this when they enqueue or persist work. The returned header can later be passed to `turn_span` so a queued turn continues the trace that admitted it.


##### `turn_profile`  (lines 382–390)

```
def turn_profile(subagent_profile: str | None, spawned: bool=False) -> str
```

**Purpose**: Chooses the small, stable profile label used for a turn. This keeps metric and trace labels useful without creating an unbounded number of unique values.

**Data flow**: It receives an optional subagent profile and a boolean saying whether the turn was spawned. If a subagent profile is present, it returns that. Otherwise it returns `agent` for spawned turns and `main` for member-facing turns.

**Call relations**: `turn_span` calls this when adding trace attributes. Other callers may also use it to keep metric labels aligned with trace labels.

*Call graph*: called by 1 (turn_span).


##### `turn_span`  (lines 394–430)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None, subagent_profile: str | None, parent_turn_id: UUID | None) -> Iterator[Span]
```

**Purpose**: Opens a trace span around one durable turn. A span is a timed section on a trace, like a marked stretch on a timeline.

**Data flow**: It receives turn and conversation IDs, an optional incoming traceparent, optional profile information, and an optional parent turn ID. It builds trace attributes, adds workspace metadata, redacts sensitive fields, extracts the parent trace context if one was supplied, and starts a SERVER span named `turn`. Code inside the `with` block runs inside that span, and the span closes when the block exits.

**Call relations**: This is a public context manager used by turn-running code outside this file. Internally it calls `_ambient_scope`, `turn_profile`, and `redact_payload`, then uses OpenTelemetry’s tracer to create the span. It is the bridge between queued turn execution and the trace that caused that turn to exist.

*Call graph*: calls 3 internal fn (_ambient_scope, redact_payload, turn_profile); 2 external calls (get_tracer, cast).


##### `span`  (lines 434–446)

```
def span(name: str, kind: SpanKind=SpanKind.INTERNAL, **attributes: object) -> Iterator[Span]
```

**Purpose**: Opens a smaller trace span for a named stage of work, such as a model call, tool dispatch, or sandbox step. It helps a trace show where time was spent inside a larger operation.

**Data flow**: It receives a span name, an optional span kind, and any number of attributes. It combines those attributes with the current workspace, redacts sensitive fields, converts values into simple span-safe forms, and starts a span. The yielded span surrounds the caller’s `with` block and closes afterward.

**Call relations**: This is a general helper for code outside the file. It calls `_ambient_scope` and `redact_payload`, then hands the cleaned attributes to OpenTelemetry’s tracer.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); 1 external calls (get_tracer).


##### `redact_payload`  (lines 449–455)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Removes fields whose names look sensitive and cleans the remaining values for safe export. It is the main privacy filter for structured observability data.

**Data flow**: It receives a mapping of field names to values. For each field, it normalizes the key by removing underscores and dashes and lowercasing it, then drops keys such as prompt, content, token, secret, and credentials. Values that remain are passed to `redact_value`. It returns a new dictionary containing only safe JSON-like values.

**Call relations**: `turn_span`, `span`, and `_emit_log` call this before sending data to traces or logs. `redact_value` also calls it recursively for nested dictionaries, so the same sensitive-key rules apply inside nested structures.

*Call graph*: calls 1 internal fn (redact_value); called by 4 (_emit_log, redact_value, span, turn_span).


##### `redact_value`  (lines 458–468)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Turns one value into something safe and JSON-like for logs or span attributes. It preserves simple values, walks through lists and dictionaries, and stringifies unusual objects.

**Data flow**: It receives any Python object. Plain values such as strings, numbers, booleans, and `None` pass through. Dictionaries are converted to string-keyed mappings and sent through `redact_payload`. Lists and other non-string sequences are cleaned item by item. Anything else becomes its string representation.

**Call relations**: `redact_payload` calls this for each allowed field. When this function sees a nested mapping, it calls `redact_payload` again, forming a recursive cleaning loop for complex data.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 471–476)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Emits a structured informational event. Callers use it for normal noteworthy events that should be searchable and correlated with the current trace.

**Data flow**: It receives an event name and extra fields. It passes them to `_emit_log` with INFO severity. `_emit_log` then adds workspace context, redacts fields, and sends the event through both standard Python logging and OpenTelemetry logs.

**Call relations**: This is one of the simple public logging helpers. It delegates all shared behavior to `_emit_log` so info, warning, and error logs are treated consistently.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 479–481)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Emits a structured error event. Callers use it when something failed and operators should see it as an error.

**Data flow**: It receives an event name and fields describing the failure. It passes them to `_emit_log` with ERROR severity, which attaches context, redacts sensitive data, and sends the record to logging and OpenTelemetry.

**Call relations**: Like `log` and `warn`, this is a thin public wrapper around `_emit_log`. The wrapper exists so call sites can clearly state the intended severity.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 484–486)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Emits a structured warning event. Callers use it for expected but important conditions that are not necessarily fatal.

**Data flow**: It receives an event name and descriptive fields. It passes them to `_emit_log` with warning severity. The shared emitter adds workspace context, redacts values, and publishes the record.

**Call relations**: This sits beside `log` and `log_error` as the warning-level public API. It relies on `_emit_log` for the actual publishing work.

*Call graph*: calls 1 internal fn (_emit_log).


##### `formatted_stack`  (lines 489–518)

```
def formatted_stack(error: BaseException) -> str
```

**Purpose**: Builds a stack trace string for an exception without including exception messages. This gives operators useful debugging location information while avoiding accidental leakage of user-controlled or environment text.

**Data flow**: It receives an exception. It walks through that exception and its explicit cause or unsuppressed context, records each exception class name and traceback frames, and avoids loops if the chain cycles. If the final text is short enough, it returns it whole. If it is too long, it keeps the beginning and end and replaces the middle with an elision marker.

**Call relations**: No in-file caller is listed, so other error-reporting code likely uses this before calling `log_error` or another logger. It relies on Python’s traceback formatting to produce frame information.

*Call graph*: 1 external calls (format_tb).


##### `_emit_log`  (lines 521–535)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Does the shared work behind `log`, `warn`, and `log_error`. It publishes one structured event to both normal Python logging and OpenTelemetry logs.

**Data flow**: It receives an event name, OpenTelemetry severity values, a Python logging level, and fields. It adds ambient workspace metadata, redacts the payload, writes a standard-library log record under the `ufo` logger with the structured fields attached, and emits a matching OpenTelemetry log record with those fields as attributes.

**Call relations**: `log`, `warn`, and `log_error` all call this. It calls `_ambient_scope` and `redact_payload` before handing the cleaned event to Python logging and the OpenTelemetry log API.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `_bounded_error_class`  (lines 538–551)

```
def _bounded_error_class(dimensions: dict[str, str]) -> dict[str, str]
```

**Purpose**: Keeps the `error_class` metric label within a known, limited set. This protects the monitoring system from being flooded by unlimited exception class names.

**Data flow**: It receives a dictionary of metric dimensions. If the `error_class` value is missing or already allowed, it returns the dimensions unchanged. If the value is unknown, it returns a copy with `error_class` changed to `other`.

**Call relations**: `emit_metric` and `emit_histogram` call this before recording values. That makes the limit apply at the common metric-emission boundary instead of relying on every call site to remember it.

*Call graph*: called by 2 (emit_histogram, emit_metric).


##### `emit_metric`  (lines 554–564)

```
def emit_metric(name: str, amount: int=1, /, **dimensions: str) -> None
```

**Purpose**: Increments a registered counter metric. A counter is used for things that happen as discrete events, such as retries, failures, or completed turns.

**Data flow**: It receives a metric name, an amount to add, and string dimensions. It first checks that the name is in the allowed metric list. It creates and caches the OpenTelemetry counter the first time that name is used. Then it bounds the error class label and adds the amount with the provided dimensions.

**Call relations**: No in-file caller is listed because this is a public helper for the rest of the system. It calls `_bounded_error_class` before using OpenTelemetry’s meter to create or update the counter.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_histogram`  (lines 567–589)

```
def emit_histogram(name: str, value: int, /, **dimensions: str) -> None
```

**Purpose**: Records one measured value, usually a duration in milliseconds, for a registered histogram. A histogram lets operators ask questions like “what was the 95th percentile latency?”

**Data flow**: It receives a histogram name, a numeric value, and string dimensions. It checks that the histogram name is known and that every dimension was declared for that histogram. It creates and caches the OpenTelemetry histogram on first use, bounds the error class dimension, and records the value with its labels.

**Call relations**: This is a public measurement helper for code outside the file. It calls `_bounded_error_class` and OpenTelemetry’s meter, while enforcing the dimension allowlist defined near the top of the file.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_up_down_metric`  (lines 592–604)

```
def emit_up_down_metric(name: str, amount: int, /, **dimensions: str) -> None
```

**Purpose**: Adds or subtracts from a registered current-state metric. This is used for values that can rise and fall, such as the number of active model rounds.

**Data flow**: It receives a metric name, a signed amount, and string dimensions. It verifies that the name is registered and that all dimensions are declared for that metric. It creates and caches an OpenTelemetry up-down counter on first use, then adds the amount with the provided dimensions.

**Call relations**: No in-file caller is listed, indicating that runtime code elsewhere uses it directly. Unlike counters and histograms, it does not call `_bounded_error_class`; it simply validates the declared dimensions and records the current-state change through OpenTelemetry.

*Call graph*: 1 external calls (get_meter).


### `core/src/ufo/seed.py`

`domain_logic` · `operator-triggered seeding or visual/demo setup`

This file is like a showroom setup script for the conversation UI. Instead of waiting for a real user and agent to naturally produce every possible message shape, it writes one complete example conversation directly into the project’s durable storage: database rows for conversations and turns, blob storage entries for transcripts and files, and a small web chat record used by the portal.

The main class, KitchenSink, first looks for older kitchen-sink runs. It only deletes runs that are clearly marked as seed-created and untouched by real member speech or transcript disclosure. That guard matters because this code writes and deletes audit-like history; it must never erase a real conversation just because it has the same title.

After clearing safe old data, it opens a new web conversation, adds three finished turns, gives each turn a terminal summary with model, token, and cost details, and adds a final turn that still asks the user questions. It also creates nested subagent conversations, stores transcripts showing tool use and tool results, and attaches sample files such as a Markdown audit and a CSV metrics file.

Without this file, developers and operators would need to manually create complicated real conversations to check whether the portal still draws every conversation feature correctly.

#### Function details

##### `_framed`  (lines 74–75)

```
def _framed(turn_id: UUID, said: str) -> Message
```

**Purpose**: This helper makes a user message that includes a hidden reference to the turn it belongs to. It lets the transcript look like normal speech while still carrying enough context for the system to connect the message back to a specific turn.

**Data flow**: It takes a turn ID and the words the user supposedly said. It wraps the turn ID in a small context block, appends the user text, and returns a Message object with the role set to user.

**Call relations**: KitchenSink._said calls this when building the main transcript. It supplies the user-side messages that line up with the seeded turns created earlier.

*Call graph*: called by 1 (_said); 1 external calls (__init__).


##### `KitchenSink.write`  (lines 106–116)

```
async def write(self) -> UUID
```

**Purpose**: This is the top-level action that creates a fresh kitchen-sink conversation. A caller uses it when they want the workspace to contain one complete demo conversation and no stale earlier demo copies.

**Data flow**: It starts by making a new conversation ID and three new turn IDs. It clears old safe-to-delete kitchen-sink data, writes the new conversation and turns, creates subagent runs, attaches files, stores the transcript in blob storage, and returns the new conversation ID.

**Call relations**: This method is the main coordinator for the file. It calls _clear, _open, _runs, _files, and _said in order, then uses transcript encoding and a transcript storage key to save the finished conversation body.

*Call graph*: calls 5 internal fn (_clear, _files, _open, _runs, _said); 4 external calls (__init__, encode, transcript_key, uuid4).


##### `KitchenSink._clear`  (lines 118–124)

```
async def _clear(self) -> None
```

**Purpose**: This removes earlier kitchen-sink seed runs before writing a new one. It keeps the demo area tidy while avoiding deletion of real user history.

**Data flow**: It asks _prior for seed-created runs that are safe to remove. For each one, it deletes database rows through _drop, removes the matching web chat row from the extension store, and deletes related blobs such as transcripts and shared artifacts.

**Call relations**: KitchenSink.write calls this at the beginning of every seed run. It relies on _prior to decide what may be deleted and on _drop to remove the database parts before it cleans up blob and extension-store leftovers.

*Call graph*: calls 2 internal fn (_drop, _prior); called by 1 (write); 2 external calls (__init__, transcript_key).


##### `KitchenSink._prior`  (lines 126–204)

```
async def _prior(self) -> tuple[_PriorRun, ...]
```

**Purpose**: This finds older kitchen-sink runs that belong to this seeding tool and are safe to erase. Its main job is to protect real workspace history from accidental cleanup.

**Data flow**: It reads the workspace database for web conversations whose queue key has the kitchen-sink prefix. For each root conversation, it follows child subagent conversations by matching queue keys to turn IDs, gathers their turns, and checks whether any turn looks user-created or any transcript has been disclosed. It returns only runs with no such signs of real use.

**Call relations**: KitchenSink._clear calls this before deleting anything. It uses database queries inside a workspace transaction, then packages each safe run as a _PriorRun record for _drop and later blob cleanup.

*Call graph*: called by 1 (_clear); 5 external calls (__init__, not_, or_, select, workspace_tx).


##### `KitchenSink._drop`  (lines 206–235)

```
async def _drop(self, run: _PriorRun) -> tuple[str, ...]
```

**Purpose**: This deletes the database rows for one safe prior seed run. It removes the conversation tree and remembers which attached blob files also need deletion.

**Data flow**: It receives a _PriorRun containing conversation IDs and turn IDs. It looks up blob keys for shared artifacts attached to those turns, deletes shared-artifact rows, conversation-change rows, turn rows, and conversation rows, then returns the artifact blob keys to the caller.

**Call relations**: KitchenSink._clear calls this after _prior has marked a run as safe. _drop handles only the database cleanup; _clear uses its returned blob keys to finish cleanup in blob storage.

*Call graph*: called by 1 (_clear); 3 external calls (delete, select, workspace_tx).


##### `KitchenSink._open`  (lines 237–275)

```
async def _open(self, conversation_id: UUID, turns: tuple[UUID, ...]) -> None
```

**Purpose**: This creates the main web conversation and its three completed turns. It also makes the conversation visible to the web portal by adding the chat row and giving the conversation its demo title.

**Data flow**: It receives a conversation ID and three turn IDs. It inserts one conversation row, then inserts one turn row for each terminal frame from _terminals, marking each turn as complete and seed-created. After the database write, it retitles the conversation and stores a small web chat record containing the agent ID and user email.

**Call relations**: KitchenSink.write calls this after old seed data is cleared. It calls _terminals to get the finished-turn summaries, writes the durable database state, then hands off to retitle_conversation and the scoped extension store so the web surface can show the conversation.

*Call graph*: calls 1 internal fn (_terminals); called by 1 (write); 5 external calls (__init__, insert, workspace_tx, retitle_conversation, uuid4).


##### `KitchenSink._terminals`  (lines 277–333)

```
def _terminals(self) -> tuple[TerminalFrame, ...]
```

**Purpose**: This builds the final status snapshots for the three seeded turns. These snapshots include completion state, model usage, cost, and in the last turn an unresolved question for the user.

**Data flow**: It takes no outside input beyond the KitchenSink instance. It creates three TerminalFrame objects: two plain completed frames and one completed frame that includes a structured AskUserInput prompt with questions, options, multi-select choices, and free-text input. It returns them as a tuple.

**Call relations**: KitchenSink._open calls this while inserting the main turns. The returned terminal frames become the stored end-state that the portal later draws for each turn.

*Call graph*: called by 1 (_open); 4 external calls (__init__, __init__, __init__, __init__).


##### `KitchenSink._runs`  (lines 335–368)

```
async def _runs(self, conversation_id: UUID, parent: UUID) -> None
```

**Purpose**: This adds nested subagent activity to the demo conversation. It shows the portal what it looks like when an agent spawns another agent, and that subagent spawns one more.

**Data flow**: It receives the main conversation ID and the parent turn ID that should appear to spawn subagent work. It creates IDs for a child and grandchild turn, calls _run to insert their conversations and turns, and writes a transcript for the spawned child conversation showing text, a tool call, and a tool result.

**Call relations**: KitchenSink.write calls this after the main conversation is opened. It delegates the database creation of each subagent run to _run, then saves a transcript blob so the subagent conversation has readable message content.

*Call graph*: calls 1 internal fn (_run); called by 1 (write); 8 external calls (__init__, __init__, __init__, __init__, __init__, encode, transcript_key, uuid4).


##### `KitchenSink._run`  (lines 370–405)

```
async def _run(self, turn_id: UUID, parent: UUID, profile: str, answered: str) -> UUID
```

**Purpose**: This creates one subagent conversation with one completed turn. It is the building block used to make the nested subagent examples.

**Data flow**: It receives a turn ID, the parent turn ID that caused this subagent run, a subagent profile name, and a short answer. It creates a new conversation ID, inserts a subagent conversation linked through the parent’s ID, inserts a completed turn with the answer stored in its terminal frame, and returns the new conversation ID.

**Call relations**: KitchenSink._runs calls this twice: once for the child subagent and once for the grandchild. It performs the database inserts that make the subagent chain visible to the rest of the system.

*Call graph*: called by 1 (_runs); 4 external calls (__init__, insert, workspace_tx, uuid4).


##### `KitchenSink._files`  (lines 407–428)

```
async def _files(self, turn_id: UUID) -> None
```

**Purpose**: This attaches sample files to one of the seeded turns. The files let the portal demonstrate shared artifacts such as a Markdown report and a CSV data file.

**Data flow**: It receives the turn ID that should own the files. For each built-in sample body, it encodes the text as bytes, writes it to blob storage under a fresh artifact key, then inserts a shared_artifact database row with filename, media type, size, and workspace details.

**Call relations**: KitchenSink.write calls this after creating the main conversation and subagent runs. It uses blob storage for the file contents and the database for the metadata that lets the UI find and label those files.

*Call graph*: called by 1 (write); 3 external calls (insert, workspace_tx, uuid4).


##### `KitchenSink._said`  (lines 430–484)

```
def _said(self, turns: tuple[UUID, ...]) -> tuple[Message, ...]
```

**Purpose**: This builds the main transcript messages for the seeded web conversation. It gives the demo conversation realistic readable content, including user requests, assistant replies, tool calls, and tool results.

**Data flow**: It receives the three turn IDs created for the main conversation. It uses _framed to make user messages tied to each turn, creates assistant messages with tool-use blocks, creates user messages with tool-result blocks, and returns the full ordered message tuple.

**Call relations**: KitchenSink.write calls this at the end, just before saving the transcript blob. The messages it returns are wrapped in a Conversation object, encoded, and stored under the conversation’s transcript key.

*Call graph*: calls 1 internal fn (_framed); called by 1 (write); 3 external calls (__init__, __init__, __init__).


### `extensions/debugger/ufo_ext_debugger/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a folder: the label does not do the work inside the folder, but it lets other parts of the program find and use what is stored there. Because this file has no code, it does not set up the debugger, define functions, or change any program state. Its value is structural: without it, depending on the Python version and import style, code that expects to import `ufo_ext_debugger` as a package might fail or behave differently.


### Controlled evaluation connectors
These files expose deterministic fake mailbox, calendar, and code-search connectors for tests and evaluations.

### `extensions/eval_env/ufo_ext_eval_env/__init__.py`

`other` · `import time`

This file is intentionally tiny, but it still matters. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as a package, meaning other code can import from it. Here, the package represents a controlled evaluation environment: instead of connecting to real email or calendar services, it offers fake providers whose behavior is predictable. That is useful when testing or judging the system, because the same inputs should lead to the same results every time. Think of it like a practice stage set: it looks enough like the real world for the system to act, but nothing depends on live accounts, network timing, or changing external data. Without this package marker, imports that expect `ufo_ext_eval_env` to be a proper package could fail or behave differently depending on the Python setup.


### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `evaluation setup and connector request handling`

This file gives automated evaluations a small, predictable world for an assistant to act in: a mailbox, a calendar, and a code search service. The important idea is that these are not loose mocks hidden beside the test. They are real connector providers registered through the normal extension manifest, so the assistant discovers tools, reads schemas, and calls tools through the same route it would use in production.

The email and calendar parts store rows in extension-owned database tables. When the assistant sends an email or creates, updates, or cancels a calendar event, the change is written durably under the current workspace. A grader can later inspect the same stored rows and judge what happened. This is like giving the assistant a training-room mailbox and calendar: safe, isolated, but still operated through the real doors and buttons.

The code search part is different. It is read-only. Evaluations seed an exact response under a search query, and the tool returns that response byte-for-byte as normal data. If the query was not seeded, it fails loudly, because an accidental empty result would make the evaluation misleading.

The file also defines simple OAuth stubs. The login flow is not really used in evaluations, but the connector registry still needs provider descriptors. Finally, manifest() packages the three providers so the host system can load them.

#### Function details

##### `_transaction`  (lines 171–175)

```
def _transaction()
```

**Purpose**: Opens a database transaction for this evaluation extension’s workspace-scoped storage. Code uses it whenever it needs to read or write the email and calendar tables safely.

**Data flow**: It takes no direct input. It builds an ExtensionContext with a ScopedStore for the eval_env extension and an empty credential access object, then returns that context’s transaction object. The caller uses the returned transaction to run database statements.

**Call relations**: The email and calendar methods call this just before touching stored rows. It is the shared doorway into durable storage for sending emails, listing emails, creating events, listing events, and changing events.

*Call graph*: called by 5 (_change_event, _create_event, _list_emails, _list_events, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 178–182)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns an ISO 8601 time string into a Python datetime value. If the string has no timezone, it assumes UTC so event times are still unambiguous.

**Data flow**: It receives a text timestamp. It parses the text into a datetime; if no timezone is present, it adds UTC. It returns the normalized datetime for database storage.

**Call relations**: Event creation and event updates use this before saving start and end times. It keeps calendar tools from storing raw strings or timezone-free times.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 190–198)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the tools available for one provider, such as email, calendar, or code search. If a search phrase is supplied, it narrows the list to tools whose name or description matches.

**Data flow**: It receives a workspace id, provider name, and search text. It looks up that provider’s tool catalog, compares the lowercase search text to tool slugs and descriptions, and returns matching tool definitions. If nothing matches, it falls back to the full catalog.

**Call relations**: The broker search method calls this when the host asks what tools are discoverable. It is part of the assistant’s normal tool-discovery path before any tool is executed.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 200–204)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the detailed definition for a specific tool. The definition includes the input shape the assistant must follow when calling that tool.

**Data flow**: It receives a workspace id, provider name, and tool slug. It scans that provider’s catalog for the matching slug and returns the BrokerTool record. If the slug is unknown, it raises UnknownBrokerTool.

**Call relations**: This is used by the connector layer when it needs to describe one tool. It does not call the tool itself; it only answers what the tool expects.

*Call graph*: 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 206–245)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Dispatches an actual tool call to the correct email, calendar, or code-search action. It is the main switchboard for the evaluation broker.

**Data flow**: It receives the workspace, provider, tool slug, raw argument values, account id, and idempotency key. It validates the raw arguments against the matching Pydantic model, then calls the private method that performs the work. It returns a plain dictionary response, or raises UnknownBrokerTool if the provider and slug do not match a known tool.

**Call relations**: The connector runtime calls this when the assistant invokes a tool. From there it hands off to _send_email, _list_emails, _create_event, _list_events, _update_event, _cancel_event, or _search_code depending on the requested slug.

*Call graph*: calls 7 internal fn (_cancel_event, _create_event, _list_emails, _list_events, _search_code, _send_email, _update_event); 1 external calls (__init__).


##### `EvalEnvBroker._search_code`  (lines 247–255)

```
async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]
```

**Purpose**: Returns a pre-seeded code-search response for the exact query the assistant asked for. This makes code-search evaluations deterministic.

**Data flow**: It receives validated search arguments containing a query string. It reads the extension’s scoped store at the key made from the code-search prefix plus that query. If the stored value is a dictionary, it copies and returns it; otherwise it raises an error explaining that no fixture was seeded.

**Call relations**: execute calls this for the search_code tool. Unlike the email and calendar tools, it does not mutate tables; it reads a fixture prepared by the evaluation case.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 257–272)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Creates a sent email in the evaluation mailbox. This lets a test later verify that the assistant really chose to send the expected message.

**Data flow**: It receives a workspace id and validated email arguments. It creates a new email id, opens a transaction, inserts a row in the email table with folder sent, the fixed assistant sender address, recipients, subject, body, and current UTC time. It returns the new id, sent status, and recipient list.

**Call relations**: execute calls this when the assistant invokes send_email. It relies on _transaction to write the row into the extension database.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 274–309)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Reads emails from the evaluation mailbox, optionally filtering them by text. This gives the assistant a controlled inbox or sent folder to inspect.

**Data flow**: It receives a workspace id and validated list arguments. It builds database conditions for the workspace and folder, adds a case-insensitive substring search over sender, subject, and body if a query was supplied, then reads the newest matching rows up to the requested limit. It returns those rows as simple email dictionaries.

**Call relations**: execute calls this for the list_emails tool. It uses _transaction for the database read and SQLAlchemy query helpers to select and filter rows.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 311–325)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Adds a confirmed event to the evaluation calendar. It records the event so later assistant actions or graders can see it.

**Data flow**: It receives a workspace id and validated event arguments. It creates a new event id, parses the start and end strings into datetimes, inserts a calendar row with confirmed status, and returns the id and status.

**Call relations**: execute calls this for create_event. It uses _moment to normalize times and _transaction to persist the new row.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 327–340)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Returns calendar events for the workspace, ordered by start time. It can optionally filter events by title text.

**Data flow**: It receives a workspace id and validated list arguments. It builds a database query for that workspace, optionally adds a title substring filter, reads rows up to the requested limit, and converts each row into a plain event dictionary. It returns those dictionaries under the events key.

**Call relations**: execute calls this for list_events. It uses _transaction to read from storage and _event_json to shape database rows into connector responses.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 342–354)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Prepares changes for an existing calendar event. It supports changing the title, start time, end time, and attendees.

**Data flow**: It receives a workspace id and validated update arguments. It builds a changes dictionary only from fields the caller actually supplied, parsing new times when needed. If no fields were provided, it raises an error; otherwise it sends the change request to _change_event and returns the updated event.

**Call relations**: execute calls this for update_event. It does the argument-to-database-field translation, then delegates the actual database update and reload to _change_event.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 356–357)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Marks an existing calendar event as cancelled while keeping it in the calendar list. This mirrors calendars where cancelled events may still be visible with a cancelled status.

**Data flow**: It receives a workspace id and validated cancel arguments. It builds a small change saying the status should become cancelled, sends that to _change_event, and returns the updated event data.

**Call relations**: execute calls this for cancel_event. It reuses _change_event so cancellation follows the same workspace checks and response formatting as ordinary updates.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 359–378)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Applies database changes to one calendar event and returns its new state. It also protects workspace isolation by only updating an event inside the calling workspace.

**Data flow**: It receives a workspace id, an event id string, and a dictionary of changed fields. It opens a transaction, updates the matching calendar row, checks that exactly one row changed, then reads the event back. If no matching event exists in that workspace, it raises an error. It returns the updated event as a plain dictionary.

**Call relations**: _update_event and _cancel_event call this after deciding what should change. It uses _transaction for the write and _event_json to format the final row.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 380–388)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Converts a database event row into the simple response format returned by calendar tools. This keeps event output consistent across listing, updating, and cancelling.

**Data flow**: It receives a SQLAlchemy row with event fields. It turns the UUID into text, converts start and end datetimes into ISO strings, and copies title, attendees, and status into a dictionary. That dictionary is returned to the tool caller.

**Call relations**: _list_events uses this for every row it returns. _change_event uses it after updating and rereading one event.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 390–391)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Declares that these evaluation tools do not produce downloadable files. Every tool response is returned as ordinary structured data instead.

**Data flow**: It receives a tool response dictionary but does not inspect it. It always returns an empty tuple, meaning there are no BrokerFile outputs attached.

**Call relations**: This satisfies the broker interface expected by the connector system. Nothing in this file calls it directly, but the host can ask after a tool call whether files were produced.


##### `EvalEnvBroker.stage_upload`  (lines 393–402)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for the evaluation providers. Email, calendar, and code search in this environment accept only normal arguments, not uploaded files.

**Data flow**: It receives upload details such as workspace, provider, tool slug, filename, mimetype, and checksum. Instead of staging anything, it raises a RuntimeError saying uploads are not accepted.

**Call relations**: This is present because the broker interface includes upload support. If the host ever tries to upload a file to these eval tools, this method stops that path immediately.


##### `EvalEnvBroker.search`  (lines 404–405)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps tool discovery results in the connector system’s search response object. It lets the host search for tools by text.

**Data flow**: It receives a workspace id, provider, and query. It calls tools to get matching BrokerTool definitions, then returns a BrokerSearch object containing them.

**Call relations**: The connector runtime calls this during discovery. It delegates the real matching logic to tools and only packages the answer in the expected response type.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 407–408)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a simple synthetic credential for the requested eval account. It gives the connector system something credential-shaped without contacting a real service.

**Data flow**: It receives a workspace id, provider, and account id. It creates and returns a Credential with a bearer token string that includes the account id.

**Call relations**: This supports the broker interface when a tool call needs credentials. The token is not used to authenticate to a real outside system; it is part of the controlled evaluation setup.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 419–420)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a fake authorization URL for an eval provider. It exists so the provider looks like a normal OAuth connector, even though evals usually seed grants directly.

**Data flow**: It receives a state value and redirect URI. It combines them with the provider’s configured host into an HTTPS authorize URL string and returns that string.

**Call relations**: The connector registry can call this if it needs to start a connect flow. In normal eval use, the flow is not exercised, but the method keeps the provider descriptor complete.


##### `_EvalEnvOAuth.exchange`  (lines 422–425)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the fake OAuth exchange by returning the fixed eval account id. OAuth here means a standard connect-and-grant flow, but this implementation does not contact any outside service.

**Data flow**: It receives an authorization code, redirect URI, workspace id, and state. It ignores the external details and returns an OAuthAccount with the constant eval account id.

**Call relations**: This would be called if the connector flow tried to exchange a code for an account. It keeps the stub honest enough to satisfy the registry while evaluations normally bypass it.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 428–450)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension manifest that registers the eval email, calendar, and code-search providers. This is the entry point the extension loader uses to learn what this extension offers.

**Data flow**: It creates one shared EvalEnvBroker, then creates three ConnectorProvider objects with OAuth stubs, labels, and that broker. It returns a Manifest containing the extension name, version, and provider list.

**Call relations**: The host calls this when loading the extension. Everything else in the file becomes reachable through the providers registered here.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### Extension conformance samples
These files exercise the public extension and sample-skill surfaces used to confirm third-party integration behavior.

### `extensions/sample/skills/sample_skill/probe.py`

`entrypoint` · `diagnostic check`

This file exists as a simple probe, like tapping a microphone and hearing a clear sound back. It does not load complex code, read settings, or perform any real skill work. It only prints the text `sample-skill-probe-ok`.

That fixed message is useful because it gives the surrounding system a very easy way to check whether this sample skill is present and runnable. If a launcher, installer, or test process runs this file and sees the expected message, it can treat that as a basic sign that the sample skill is wired in correctly. If the file is missing, cannot run, or prints something unexpected, that points to a setup or packaging problem.

Nothing else changes when this file runs. It has no functions, no inputs, and no stored state. Its value is in being predictable: the same command should always produce the same short confirmation.


### `extensions/sample/ufo_ext_sample.py`

`entrypoint` · `extension startup, then active whenever tests or the host exercise extension seams`

Think of this file as a full-size showroom model for the extension system. It does not connect to real outside services. Instead, it returns predictable answers and records what happened in durable extension storage, so tests can later verify that the platform called the right thing in the right way.

The file declares tools, jobs, web routes, onboarding, hooks, surfaces, sources, indexes, model backends, browser leases, connector OAuth, sandbox carriers, search, memory search, and object stores. Each piece is intentionally small. For example, the echo tool stores its input and echoes text back. The search backend returns one canned result. The sandbox carrier stores bytes in memory and echoes commands. The object store has writable "widgets" and read-only "relics" so both success and refusal paths can be tested.

The key entry is `manifest()`. It packages all these sample parts into one `Manifest`, which is the extension's contract with the host system. Without this file, the project would lose an end-to-end safety check that the SDK surface still works for a real installed extension, not just internal mocks.

#### Function details

##### `_echo`  (lines 277–281)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: Runs the sample echo tool. It records the received message in the extension's durable store, then returns the same message as tool output.

**Data flow**: It receives a tool context and typed echo arguments. It reads the extension context from the tool context, saves the argument data under the sample tool key, and returns a text tool result containing the original message.

**Call relations**: The manifest registers this as the `sample_echo` tool. The host calls it during tool execution unless the pre-tool hook denies the call first.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 284–306)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: Runs the sample note tool, proving an extension can read and write its own database table. It stores one note per workspace and returns the note that was actually saved.

**Data flow**: It receives a tool context and note text. It opens a workspace-scoped database transaction, updates or inserts the note row for that workspace, reads the row back, and returns the stored text as tool output.

**Call relations**: The manifest registers this as the note tool. It is also granted to the sample subagent, so tests can prove both normal tools and subagent-granted tools can reach extension-owned database state.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 309–335)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample background job. It marks that the job ran, then exercises optional job-time abilities such as reading trajectories, proposing an agent prompt change, writing a workspace file, and running a probe command.

**Data flow**: It receives an extension context. It writes a job marker, reads recent trajectories if available, proposes a prompt update for the first one, optionally writes a file into that conversation's workspace, optionally runs a probe command, and stores each result.

**Call relations**: The manifest registers this as the sample job. The job runner calls it, and it hands off to extension-context services such as trajectory lookup and change proposal.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 338–341)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Runs the sample HTTP route. It records the request body and replies with the same body as plain text.

**Data flow**: It receives an extension context and HTTP request. It reads the request body bytes, decodes them to text, stores that text, and returns a plain-text response.

**Call relations**: The manifest exposes this through a POST route. The route uses workspace identification before this handler runs.

*Call graph*: 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 376–387)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists sample widgets stored by this extension. It turns raw stored widget records into object rows suitable for the platform's object-listing surface.

**Data flow**: It receives a tool context and list query. It reads all extension-store keys with the widget prefix, validates each stored value, builds rows showing the widget name and fields, and returns a paged object result.

**Call relations**: The object system calls this when someone lists the sample widget kind. It uses `WidgetStore._ext` to get the extension context and `object_page` to apply paging.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `WidgetStore.get`  (lines 389–396)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WidgetSpec] | None
```

**Purpose**: Fetches one sample widget by name. It returns the widget's saved specification and timestamps if it exists.

**Data flow**: It receives a tool context and widget name. It reads the matching extension-store key, validates the stored record if present, and returns object detail or nothing if the widget is absent.

**Call relations**: The object system calls this for a widget read. It relies on `WidgetStore._ext` to reach the durable extension store.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (__init__).


##### `WidgetStore.status`  (lines 398–405)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Reports status for a sample widget. In this sample, widgets have no extra live status, so the function deliberately returns nothing.

**Data flow**: It receives the context, name, and optional generation check. It does not read or change any data and returns `None`.

**Call relations**: The object system may call this when it wants status separate from stored spec. Here it proves that status can be empty.


##### `WidgetStore.apply`  (lines 407–423)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a sample widget. It preserves the original creation time when updating and refreshes the updated time.

**Data flow**: It receives a widget name and desired spec. It reads any existing stored widget, chooses a creation timestamp, writes a new stored record with current update time, and returns no separate value.

**Call relations**: The object system calls this for create or update operations. It uses `WidgetStore._ext` to write through the extension store.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, now).


##### `WidgetStore.delete`  (lines 425–434)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a sample widget, but only if the current speaker is a workspace admin. This tests permission-gated object deletion.

**Data flow**: It receives a context and widget name. It asks whether the speaker is an admin; if not, it raises an admin-required error. If yes, it removes the widget key from extension storage.

**Call relations**: The object system calls this for delete operations. It uses the tool context for the permission check and `WidgetStore._ext` for the store deletion.

*Call graph*: calls 2 internal fn (speaker_is_admin, _ext); 1 external calls (__init__).


##### `WidgetStore._ext`  (lines 436–439)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Extracts the extension context from a tool context. It fails loudly if the object store was called without the extension context it needs.

**Data flow**: It receives a tool context. It checks the `ext` field and returns it, or raises an error if it is missing.

**Call relations**: The widget list, get, apply, and delete methods call this before touching extension storage.

*Call graph*: called by 4 (apply, delete, get, list).


##### `RelicStore.list`  (lines 447–451)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the sample read-only relic objects. It always returns one canned relic.

**Data flow**: It receives a context and list query. It builds a single object row for the relic and wraps it in a paged response.

**Call relations**: The object system calls this when listing the relic kind. It demonstrates a system-produced object kind with no user-authored storage.

*Call graph*: 2 external calls (__init__, object_page).


##### `RelicStore.get`  (lines 453–458)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[RelicSpec] | None
```

**Purpose**: Fetches the one sample relic by name. It returns nothing for any other name.

**Data flow**: It receives a relic name. If the name matches the canned relic, it returns object detail with the fixed inscription; otherwise it returns `None`.

**Call relations**: The object system calls this for relic reads. It complements `RelicStore.list` by giving details for the listed object.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.status`  (lines 460–467)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports live status for a relic. The sample status says the relic was "excavated," reinforcing that it is system-produced.

**Data flow**: It receives context, name, and generation information. It ignores stored state and returns a small status dictionary.

**Call relations**: The object system may call this after listing or getting a relic. It shows that read-only objects can still have status.


##### `RelicStore.apply`  (lines 469–478)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update relics. This proves the object surface can clearly say a verb is not supported.

**Data flow**: It receives a requested relic change. It does not inspect or save it; it raises a `VerbNotSupported` error with the sample refusal message.

**Call relations**: The object system calls this only if someone tries to mutate a relic. The refusal is expected behavior for this read-only kind.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 480–487)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete relics. Relics are intentionally read-only in this sample.

**Data flow**: It receives a relic name and generation information. It changes nothing and raises a `VerbNotSupported` error.

**Call relations**: The object system calls this on relic deletion attempts. It mirrors `RelicStore.apply` so every mutation path is refused.

*Call graph*: 1 external calls (__init__).


##### `SampleSource.fetch`  (lines 506–515)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Produces one predictable source page from typed source configuration. This tests that source backends can be registered, configured, and polled.

**Data flow**: It receives source config, an optional cursor, and auth information. It turns the configured topic into a page with a title and body, then returns a sync result with no next cursor.

**Call relations**: The source sync runner calls this after onboarding registers the sample source. It hands back a `SyncResult` that the platform can index or store.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleIndex.upsert`  (lines 528–530)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds or replaces chunks in the sample in-memory index. A chunk is a piece of text prepared for search.

**Data flow**: It receives a tuple of chunks. For each one, it stores the chunk by its digest, replacing any older chunk with the same digest.

**Call relations**: The indexing system calls this when it wants this backend to remember searchable chunks.


##### `SampleIndex.delete`  (lines 532–534)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes all indexed chunks that belong to a requested scope. A scope identifies one owner, such as one document or source item.

**Data flow**: It receives an index scope. It finds stored chunks whose owner kind and owner id match that scope, then removes them from the in-memory dictionary.

**Call relations**: The indexing system calls this for cleanup. It uses `_in_scope` to decide which chunks belong to the requested scope.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.has_chunks`  (lines 536–537)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether any chunks exist for a requested scope.

**Data flow**: It receives an index scope. It scans stored chunks, tests each with `_in_scope`, and returns true as soon as one match exists.

**Call relations**: The indexing system can call this before deciding whether indexing work is needed.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 539–545)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes old chunks within a scope while keeping a named set of current chunks. This mimics trimming an index after content changes.

**Data flow**: It receives a scope and a set of chunk digests to keep. It deletes stored chunks that are in the scope but not in the keep set.

**Call relations**: The indexing system calls this during refresh. It uses `_in_scope` to avoid deleting chunks from unrelated owners.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 547–556)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by counting matching words from the query. This is a simple text-search stand-in, not a production search engine.

**Data flow**: It receives a query, allowed subjects, owner kind, and limit. It filters chunks by subject and owner kind, counts occurrences of query terms, converts positive scores into hits, sorts them, and returns the best hits.

**Call relations**: The retrieval system calls this for keyword-style search. It uses `_scoped` for filtering and `_hit` to shape results.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 558–566)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by comparing numeric embeddings. An embedding is a list of numbers representing text meaning.

**Data flow**: It receives an embedding, allowed subjects, owner kind, and limit. It filters chunks, computes a dot-product score against each chunk embedding, keeps positive matches, sorts them, and returns the best hits.

**Call relations**: The retrieval system calls this for vector-style search. It uses `_scoped`, `_dot`, and `_hit` as helper steps.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 568–573)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: Filters stored chunks to the subject set and owner kind requested by a search.

**Data flow**: It receives allowed subjects and an owner kind. It scans all stored chunks and returns only those whose owner kind and subject match.

**Call relations**: Both `SampleIndex.lexical` and `SampleIndex.vector` call this before scoring, so searches do not leak unrelated chunks.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 582–583)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: Returns a fixed embedding vector for every input text. This lets tests exercise embedding selection without calling a real model service.

**Data flow**: It receives a tuple of texts. It ignores the content and returns the same sample vector once for each text.

**Call relations**: The manifest registers this as an embedding backend. The host calls it when it wants embeddings from the sample backend.


##### `_in_scope`  (lines 586–587)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a chunk belongs to a particular index scope.

**Data flow**: It receives a chunk and a scope. It compares owner kind and owner id and returns true only when both match.

**Call relations**: The sample index's delete, has-chunks, and prune methods call this to avoid touching chunks outside the requested owner.

*Call graph*: called by 3 (delete, has_chunks, prune).


##### `_dot`  (lines 590–593)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Computes a dot product between two vectors, which is a simple similarity score for numeric embeddings.

**Data flow**: It receives two tuples of numbers. If either is empty, it returns zero; otherwise it multiplies matching positions and sums the products.

**Call relations**: `SampleIndex.vector` calls this to score each candidate chunk against the query embedding.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 596–605)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: Converts a stored chunk and score into a search hit object.

**Data flow**: It receives a chunk and score. It copies the chunk's identifying fields, text, and subject into a `Hit` and attaches the score.

**Call relations**: Both lexical and vector search call this after scoring chunks, so both return the same result shape.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 608–615)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample onboarding step. It records that onboarding happened and registers the sample content source.

**Data flow**: It receives an extension context. It writes an onboarding marker to extension storage, builds sample source config, and asks the platform to register that source for the shared subject.

**Call relations**: The manifest registers this as an onboarding step. The onboarding runner calls it when a workspace installs or configures the sample extension.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 618–621)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: Always denies the sample echo tool when used as a pre-tool hook. This proves that a hook can stop a tool before its handler runs.

**Data flow**: It receives hook context. It does not read the payload and returns a deny outcome with the sample reason.

**Call relations**: The manifest attaches this to the pre-tool-use event for the echo tool. If it fires, `_echo` should not be called.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 624–632)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records successful tool calls after they finish. It proves the success hook receives the tool name.

**Data flow**: It receives hook context. If the payload is a successful post-tool-use event, it stores the tool name under the post-hook key and returns no further outcome.

**Call relations**: The manifest registers this for all post-tool-use events. It observes successful dispatches, unlike `_record_post_failure`.


##### `_record_post_failure`  (lines 635–641)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records failed tool calls after they error. It proves failure hooks are separate from success hooks.

**Data flow**: It receives hook context. If the payload is a post-tool-use-failure event, it stores the failed tool name and returns no further outcome.

**Call relations**: The manifest registers this for tool failure events. It is the counterpart to `_record_post`.


##### `_record_stop`  (lines 644–650)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records the final answer when a turn is stopping. This proves the extension sees turn-end events.

**Data flow**: It receives hook context. If the payload contains a stop event, it stores the final answer text.

**Call relations**: The manifest registers this for stop hooks. The turn runner calls it near the end of a response.


##### `_record_pre_compact`  (lines 653–660)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information just before conversation compaction. Compaction means shortening stored context so it fits within model limits.

**Data flow**: It receives hook context. If the payload is a pre-compaction event, it stores the reason and token estimate before compaction.

**Call relations**: The manifest registers this for pre-compact hooks. It pairs with `_record_post_compact` to prove both sides of compaction fire.


##### `_record_post_compact`  (lines 663–675)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information after conversation compaction finishes.

**Data flow**: It receives hook context. If the payload is a post-compaction event, it stores the summary and token counts before and after compaction.

**Call relations**: The manifest registers this for post-compact hooks. It complements `_record_pre_compact`.


##### `_record_page_change`  (lines 678–691)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records delivered page-change events. It also notes whether a model client was wired into the off-turn extension context.

**Data flow**: It receives hook context. If the payload is a page-change batch, it stores page ids and a boolean showing whether `ctx.ext.model` is available.

**Call relations**: The manifest registers this for page-change hooks. The page-change runner calls it when source pages change.


##### `_SampleConnectorOAuth.authorize_url`  (lines 704–705)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the sample OAuth authorization URL. OAuth is the common browser-based flow for granting an app access to an account.

**Data flow**: It receives state and redirect URI strings. It combines them with the canned sample authorization endpoint and returns the URL.

**Call relations**: The connector setup flow calls this when it needs to send a user to the provider's authorization page.


##### `_SampleConnectorOAuth.exchange`  (lines 707–710)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the sample OAuth exchange by returning a canned connected account. It does not produce a secret token.

**Data flow**: It receives the OAuth code, redirect URI, workspace id, and state. It ignores the details and returns an `OAuthAccount` with the fixed account id.

**Call relations**: The connector setup flow calls this after the pretend provider redirects back. The returned account id is later resolved by connector tools.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 723–724)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the broker's available tool catalog. In this sample, the catalog contains exactly one canned tool.

**Data flow**: It receives workspace, provider, and query information. It ignores the query and returns one broker tool with a slug and description.

**Call relations**: The connector broker search path calls this directly, and `_SampleBroker.search` also calls it when building a search response.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 726–733)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the input schema for the sample broker tool, or rejects unknown tools.

**Data flow**: It receives a tool slug. If the slug is the sample slug, it returns a broker tool with a small JSON-style input schema; otherwise it raises an unknown-tool error.

**Call relations**: The dynamic connector tool system calls this when it needs to know what arguments a broker tool accepts.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 735–752)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs the sample broker tool by echoing the call details. This proves account id, arguments, provider, and idempotency key reach the broker.

**Data flow**: It receives workspace, provider, slug, arguments, account id, and idempotency key. It rejects unknown slugs; otherwise it returns those values in a dictionary.

**Call relations**: The connector execution path calls this for dynamic broker tools. It is separate from the server-side `_connector_execute` tool.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 754–765)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Turns URLs listed in a broker response into produced file records. This tests the connector file-output bridge.

**Data flow**: It receives the broker response dictionary. It looks for an argument called `file_output_urls`, and for each string URL it creates a broker file named from the URL path.

**Call relations**: The connector tooling calls this after broker execution to discover files the tool produced.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 767–793)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Creates a sample upload slot for a file headed to a connector tool. It also simulates deduplication by returning no upload URL for an already staged key.

**Data flow**: It receives file identity details such as filename, mimetype, and MD5 checksum. It builds a content-addressed key, remembers newly minted keys, and returns upload instructions plus the argument that should be passed to the tool.

**Call relations**: The connector file-upload path calls this before execution. The sandbox can write to the returned file URL when a new upload is needed.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 795–798)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Returns a broker search result containing available tools and a simple plan.

**Data flow**: It receives workspace, provider, and query. It calls `_SampleBroker.tools` for the tool list, adds the canned plan text, and returns a broker search object.

**Call relations**: The connector search feature calls this when it wants broker-guided tool discovery.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 800–801)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a bearer credential for a connected account. A bearer credential is a token sent with requests to prove access.

**Data flow**: It receives workspace, provider, and account id. It prefixes the account id with the sample bearer prefix and returns it as a credential.

**Call relations**: The connector credential path calls this when the platform needs a token-like value for broker-backed access.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 812–830)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: Runs the sample connector's server-side tool. It resolves the account bound to the agent and records the account plus idempotency key.

**Data flow**: It receives a tool context and typed input. It asks the tool context for the connected account for the sample provider, stores the account, requested tool name, and idempotency key, then returns the account as text.

**Call relations**: The manifest registers this under the connector provider. The host calls it when an agent uses the connector's declared server-side tool.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 840–841)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Wraps bytes as a one-piece asynchronous stream. This is a tiny helper for writing an inbound file.

**Data flow**: It receives bytes. When iterated, it yields those bytes once and then ends.

**Call relations**: `_surface_ingest` calls this when it needs to pass uploaded text to the workspace-file writer, which expects a stream.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_ingest`  (lines 844–871)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts an inbound message on the durable sample surface. It links identity, finds or creates a conversation, optionally writes an inbound file, and admits a turn.

**Data flow**: It receives a surface context and HTTP request. It parses JSON input, resolves or creates a member link, gets the conversation for the external id, optionally streams a file into the workspace, admits the message with an idempotency key, and returns ids plus whether a run was opened.

**Call relations**: The durable surface route calls this. It uses surface-context services such as linked-member lookup, conversation creation, file writing, and turn admission.

*Call graph*: calls 6 internal fn (admit, conversation_for, link_member, linked_member, write_workspace_file, _one_chunk); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_post`  (lines 874–875)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Returns a fixed external reference for a delivered surface reply.

**Data flow**: It receives surface context and writeback data. It ignores the details and returns the sample post reference string.

**Call relations**: The durable surface writeback flow calls this after the platform has a reply to post.


##### `_surface_attach`  (lines 878–883)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Copies shared reply artifacts into delivered blob keys. This proves attachments can be streamed out and back through blob storage.

**Data flow**: It receives a writeback with artifacts. For each artifact, it builds a delivered key and streams bytes from the original blob key into that delivered key.

**Call relations**: The durable surface writeback flow calls this after `_surface_post` when artifacts need to be attached.


##### `_surface_live_admit`  (lines 886–908)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts an inbound message on the live sample surface. Live here means the caller follows progress through the hub instead of a durable writeback queue.

**Data flow**: It receives a surface context and HTTP request. It parses input, finds or adopts identity, gets a conversation, admits a turn, reads the turn owner and workspace spend rollup, and returns those details as JSON.

**Call relations**: The live surface POST route calls this. It contrasts with `_surface_ingest` by not declaring a post/writeback handler.

*Call graph*: calls 6 internal fn (admit, adopt_identity, conversation_for, linked_member, spend_rollup, turn_owner); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_live_stream`  (lines 911–915)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams live turn frames as newline-delimited JSON. Newline-delimited JSON means one JSON object per line.

**Data flow**: It receives a request containing a turn id path parameter. It parses that id, creates a streaming response from `_surface_frames`, and sets the response media type.

**Call relations**: The live surface GET route calls this when a client wants to tail a turn. It delegates the actual frame iteration to `_surface_frames`.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 918–921)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: Reads live frames for a turn from the surface tail API and yields them as bytes.

**Data flow**: It receives a surface context and turn id. It opens a tail stream, converts each frame to JSON, adds a newline, and yields the bytes.

**Call relations**: `_surface_live_stream` calls this to supply the streaming HTTP body. It uses `SurfaceContext.tail` to receive hub frames.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 932–935)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Streams a canned model response. This lets the platform test model-provider registration, streaming, and usage accounting without an external model API.

**Data flow**: It receives a model request. It yields a stream-start event, one text delta containing the fixed reply, and a usage event with one input and one output token.

**Call relations**: The manifest registers this through the sample model spec. The model runner calls it when the sample model is selected.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 945–946)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the browser debugging endpoint for the sample browser lease. CDP means Chrome DevTools Protocol, a way to control a browser remotely.

**Data flow**: It receives no extra input. It returns a `CdpEndpoint` with the fixed sample WebSocket URL.

**Call relations**: Browser-driving code calls this on a lease obtained from `SampleCdpProvider.lease` or `reattach`.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 948–949)

```
async def token(self) -> str
```

**Purpose**: Returns a durable token that can be used to reattach to the sample browser lease.

**Data flow**: It receives no extra input and returns the fixed sample CDP URL as the token.

**Call relations**: Browser lifecycle code calls this when it needs to save a handle for later reattachment.


##### `SampleCdpLease.place_file`  (lines 951–952)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Pretends to place a file for browser use and returns the same path. The sample does not move bytes.

**Data flow**: It receives a path and a byte reader. It ignores the reader and returns the original path unchanged.

**Call relations**: Browser upload code can call this through the CDP lease protocol when preparing files for a page.


##### `SampleCdpLease.download_dir`  (lines 954–955)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the sample download directory path.

**Data flow**: It receives no extra input and returns the fixed download directory string.

**Call relations**: Browser code calls this when it needs to know where downloads should appear for the lease.


##### `SampleCdpLease.fetch_download`  (lines 957–958)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a downloaded file from the sample download directory.

**Data flow**: It receives a download id. It builds a path inside the fixed download directory and reads the file bytes in a worker thread, then returns those bytes.

**Call relations**: Browser download handling calls this through the lease protocol when retrieving a completed download.

*Call graph*: 2 external calls (to_thread, Path).


##### `SampleCdpLease.aclose`  (lines 960–961)

```
async def aclose(self) -> None
```

**Purpose**: Closes the sample browser lease. There is no real resource to clean up, so it does nothing.

**Data flow**: It receives no extra input, changes nothing, and returns `None`.

**Call relations**: Browser lifecycle code calls this when finished with a lease, just as it would close a real browser connection.


##### `SampleCdpProvider.lease`  (lines 971–972)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Creates a new sample CDP lease. It always returns the canned lease object.

**Data flow**: It receives an optional sandbox session. It ignores it and returns a new `SampleCdpLease`.

**Call relations**: The browser provider registry calls this when the sample CDP backend is selected for a new browser session.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 974–975)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reattaches to an existing sample CDP lease. In the sample, every token leads to the same canned lease.

**Data flow**: It receives a token string. It ignores the token content and returns a new `SampleCdpLease`.

**Call relations**: Browser lifecycle code calls this when resuming a previously saved browser connection.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 986–987)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fixed bearer credential for the sample auth proxy backend.

**Data flow**: It receives workspace, provider, and account values. It ignores them and returns a credential containing the fixed sample bearer value.

**Call relations**: The auth-proxy registry calls this when the sample backend is selected for a credential lookup.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 999–1007)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Returns one canned web-search result and a canned direct answer.

**Data flow**: It receives a search query. It ignores the query text and returns a search result containing one hit with fixed URL, title, and text, plus a fixed answer.

**Call relations**: Research or search tools call this through the registered search-provider backend.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 1009–1010)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches a canned page for a requested URL.

**Data flow**: It receives a fetch request. It copies the requested URL into a fetched-page object and uses fixed sample text as the page body.

**Call relations**: Search tooling calls this when it wants page contents after a search hit, because the provider declares fetch support.

*Call graph*: 1 external calls (__init__).


##### `SampleMemorySearch.search`  (lines 1019–1037)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Records a scoped memory search and returns one canned memory match. A memory match is a stored fact or note that may help the agent.

**Data flow**: It receives search queries, a source reader with allowed subjects, and optional time bounds. It stores the queries, subjects, and time range in extension storage, then returns one fixed memory match.

**Call relations**: The memory-search system calls this through the registered provider. The stored record lets tests confirm the requested scope arrived.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleMemorySearch.listable_kinds`  (lines 1039–1040)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which memory kinds this provider can list. The sample exposes one kind.

**Data flow**: It receives no extra input and returns a tuple containing the fixed sample memory kind.

**Call relations**: The memory listing system can call this before asking for recent memories by kind.


##### `SampleMemorySearch.list_recent`  (lines 1042–1068)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Records a request for recent memories and returns one canned memory match.

**Data flow**: It receives subjects, a limit, optional kinds, and an optional listing cursor. It stores those request details in extension storage and returns a listing page containing one fixed memory match.

**Call relations**: The memory listing system calls this through the sample provider when recent items are requested.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCarrier.__init__`  (lines 1080–1081)

```
def __init__(self) -> None
```

**Purpose**: Creates the in-process sample sandbox carrier and its small byte store.

**Data flow**: It receives no input. It initializes an empty dictionary that maps file paths to bytes written through the carrier.

**Call relations**: The carrier factory registered in the manifest constructs this when the platform selects the sample carrier.


##### `SampleCarrier.create`  (lines 1083–1088)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a fake sandbox handle for a new sample sandbox.

**Data flow**: It receives a sandbox spec. It copies the conversation id and run token into a handle and uses the fixed sample container id.

**Call relations**: Sandbox orchestration calls this when starting a sandbox with the sample carrier.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.attach`  (lines 1090–1097)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Attaches to a fake existing sandbox when a resume id is present.

**Data flow**: It receives a sandbox spec. If there is no resume id, it returns `None`; otherwise it returns a handle using the resume id as the container id.

**Call relations**: Sandbox orchestration calls this before creating a new sandbox when it wants to resume an existing one.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 1099–1102)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Pretends to run a command in the sandbox by echoing the command arguments.

**Data flow**: It receives a sandbox handle, argument tuple, and timeout. It joins the arguments into one stdout string and returns a successful execution result.

**Call relations**: Sandbox command runners call this through the carrier protocol. It proves the selected carrier received the command.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.write`  (lines 1104–1105)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Stores bytes at a path inside the fake sandbox.

**Data flow**: It receives a sandbox handle, path, and bytes. It saves the bytes in the carrier's in-memory dictionary under that path.

**Call relations**: Sandbox file-copy code calls this when sending files into the sample sandbox.


##### `SampleCarrier.read`  (lines 1107–1110)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams bytes back from a path inside the fake sandbox.

**Data flow**: It receives a sandbox handle and path. If the path was never written, it raises `FileNotFoundError`; otherwise it yields the stored bytes.

**Call relations**: Sandbox file-read code calls this when retrieving files from the sample carrier.


##### `SampleCarrier.file_op`  (lines 1112–1115)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level sandbox file operation using the shared sandbox filesystem helper.

**Data flow**: It receives a handle, operation name, and parameters. It delegates the operation to `sbxfs_file_op`, which uses this carrier's read and write behavior, and returns that result.

**Call relations**: Sandbox filesystem tooling calls this for structured file operations instead of raw read or write.

*Call graph*: 1 external calls (sbxfs_file_op).


##### `SampleCarrier.dial`  (lines 1117–1118)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Returns a fake network target for a port inside the sample container.

**Data flow**: It receives a sandbox handle and port. It builds a host string from the fixed container name and port, marks TLS as false, and returns the dial target.

**Call relations**: Sandbox networking code calls this when it wants to connect to a service exposed from the sandbox.

*Call graph*: 1 external calls (__init__).


##### `resolve_workspace`  (lines 1121–1129)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace for a normal extension HTTP route from a bearer token. If the request is not properly authorized, it returns nothing.

**Data flow**: It receives an HTTP request. It reads the Authorization header, checks for the Bearer scheme and a non-empty token, then asks `workspace_claim` to extract the workspace id.

**Call relations**: The route spec uses this as its identify function. `resolve_surface_workspace` also calls it for surface routes.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 1132–1134)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Identifies the workspace for a surface route using the same bearer-token logic as normal routes.

**Data flow**: It receives an HTTP request and surface auth object. It passes the request to `resolve_workspace` and returns that result.

**Call relations**: Surface specs use this as their async identify function before running surface handlers.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `_conversation_slot_summary`  (lines 1137–1138)

```
async def _conversation_slot_summary(_ctx: ConversationSlotContext) -> None
```

**Purpose**: Provides the summary callback for the sample conversation slot. The sample does not add any summary content.

**Data flow**: It receives conversation-slot context. It does not read or change anything and returns `None`.

**Call relations**: The conversation-slot system calls this when it asks providers to summarize slot content.


##### `_conversation_slot_read`  (lines 1141–1142)

```
async def _conversation_slot_read(_ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Provides the read callback for the sample conversation slot. It returns an empty set of workspace changes.

**Data flow**: It receives conversation-slot context. It creates and returns a `WorkspaceChanges` object with no changes and not marked as truncated.

**Call relations**: The conversation-slot system calls this when it needs the slot's current content.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 1145–1334)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension manifest, which is the contract telling the host everything this sample extension contributes. This is the main entry point for the file.

**Data flow**: It creates the sample broker and many manifest entries: tools, object kinds, jobs, routes, hooks, surfaces, sources, indexes, embeddings, models, hubs, terminals, skills, browser providers, carriers, auth proxies, search providers, memory search, and conversation slots. It returns one `Manifest` containing all of them.

**Call relations**: The extension loader calls this at startup. Every other handler in the file is connected to the host through objects registered here.

*Call graph*: 37 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


### Self-improvement replay loop
These files build failure corpora, propose prompt changes, replay saved tasks, and gate whether a new prompt is safe to promote.

### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus building`

The self-improvement loop needs real examples of where the system struggled. This file finds those examples by scanning saved trajectories, which are full conversation histories. Since the system does not have a separate “I had trouble here” signal, it treats a tool error inside the transcript as the useful clue. In everyday terms, it is like reviewing support tickets and sorting them by which machine broke.

Each useful trajectory becomes a TaskExample. That keeps the conversation id, the user’s original request, the full messages needed to replay the situation, and a plain description of the problem, such as “the search tool errored: timeout.” The file then groups examples into TaskClass objects named by failed tool, like “tool:browser.”

The split is important. For each class, some examples go into the “mine” set, which a proposer can learn from, and some go into the “held_out” set, which is used later to check whether a proposed fix really works. This prevents the system from grading a fix only on the same examples that inspired it. Very small classes are discarded because there would not be enough material to both learn and test.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

**Purpose**: Finds the first real user request in a conversation. This gives the evaluation loop a clear question or task to judge the later replay against.

**Data flow**: It receives the full message history. It scans from the beginning until it finds a message written by the user whose content is plain, non-empty text. It returns that text, or returns nothing if no suitable user request exists.

**Call relations**: When bad_trajectory is deciding whether a transcript is useful, it calls first_request to get the original user goal. Without that request, the transcript cannot become a TaskExample because there is no clear grading target.

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

**Purpose**: Finds the first failed tool call in a conversation and identifies which tool failed. This is the file’s main way of spotting a useful failure case.

**Data flow**: It receives the full message history. First it builds a small lookup table from each tool-use id to the tool’s name, because tool results refer back to tool uses by id. Then it scans again for the first tool result marked as an error. If it can match that error to a tool name, it returns the tool name and the error text; otherwise it returns nothing.

**Call relations**: bad_trajectory calls first_tool_error before creating an example. The returned tool name becomes the class label, and the error text becomes part of the problem description that the proposer can learn from.

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

**Purpose**: Decides whether one saved conversation is a useful failure example. A conversation qualifies only if it has both a user request and a tool error.

**Data flow**: It receives one trajectory, which includes a conversation id and all messages. It asks first_tool_error for the failed tool and error text, and first_request for the user’s original request. If either is missing, it returns nothing. If both exist, it creates a TaskExample and returns it together with a class name such as “tool:calculator.”

**Call relations**: task_classes calls bad_trajectory for every trajectory it is given. bad_trajectory is the filter between raw transcripts and structured training/testing examples: it uses first_tool_error and first_request, then hands back a ready-to-group example.

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

**Purpose**: Builds the final collection of task classes from many trajectories. It groups failure examples by the tool that failed, removes groups that are too small, and returns the rest in a stable order.

**Data flow**: It receives a tuple of trajectories. For each one, it asks bad_trajectory whether the trajectory contains a usable tool failure. Usable examples are collected under their class name. Each group is then passed to _split, which divides it into learning and held-out examples. The function returns the surviving TaskClass objects, sorted so larger classes come first and names break ties.

**Call relations**: This is the main public builder in the file. It coordinates the smaller helpers: bad_trajectory extracts one usable example at a time, and _split turns each group into the mine-versus-held-out shape expected by the self-improvement loop.

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

**Purpose**: Divides one group of examples into two sets: examples to learn from and examples to test against. It refuses groups that are too small to split safely.

**Data flow**: It receives a class name and all examples for that class. If there are not enough examples to provide both a mine set and a held-out set, it returns nothing. Otherwise it sorts examples by conversation id for a repeatable split, chooses a held-out count, and returns a TaskClass containing the remaining examples as mine and the first portion as held_out.

**Call relations**: task_classes calls _split after grouping examples by failed tool. _split is the gate that makes sure each returned TaskClass has enough material for the proposer to learn from some conversations while the gate can replay different conversations later.

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `active whenever the extension asks the model to propose, replay, or grade work`

The self-improvement extension needs to ask a language model for several kinds of help: proposing changes, replaying behavior, and grading results. Without this file, each part of the extension would have to know the details of the SDK's model request format, token limits, caching, and tool settings. That would make the code easier to get wrong and harder to change.

The file defines two small promises, or protocols. A protocol is like saying, “anything with this method shape can be used here.” `ModelLeg` promises a `complete` method that returns plain text from the model. `ReplayLeg` promises a `turn` method that returns a full model message and can include tools the model is allowed to call.

`ModelAccessLeg` is the real adapter. It wraps the SDK's `ModelAccess`, which is the metered, workspace-aware connection to the model. “Metered” means usage can be tracked or charged. Both adapter methods build a `ModelRequest` with the same shared settings: the selected model, the system instructions, the conversation messages, a 2048-token output limit, a short conversation cache lifetime, and model reasoning turned off. The `turn` path also includes tool schemas, which describe tools the model may use. In short, this file is the extension’s single controlled doorway to model calls.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This defines the shape of a simple model text-completion call. Any object used as a `ModelLeg` must accept system instructions and prior messages, then return text from the model.

**Data flow**: It receives a system prompt and a tuple of conversation messages. The promised behavior is to send that context to a model-like service and return the model's text answer as a string. Because this is only a protocol, it does not do the work itself; it describes what other code must provide.

**Call relations**: Other self-improvement code can depend on this small promise instead of depending on a specific SDK class. That lets the real adapter, `ModelAccessLeg.complete`, or a test double with the same shape be used in the same place.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This defines the shape of a model call that returns a full message and may use tools. It is meant for replay-style interactions where the model's response can include more structure than plain text.

**Data flow**: It receives system instructions, conversation messages, and a set of tool descriptions. The promised behavior is to give those to a model-like service and return one `Message`, which may represent the model's next turn. As a protocol method, it only states the contract and does not perform the call itself.

**Call relations**: Replay-related code can ask for a `ReplayLeg` without caring which concrete model connection is underneath. `ModelAccessLeg.turn` is the concrete implementation in this file that satisfies that promise.


##### `ModelAccessLeg.complete`  (lines 28–38)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This sends a plain text request to the SDK model connection using the extension's standard model settings. It is the concrete implementation of the simple completion path.

**Data flow**: It receives a system prompt and previous messages. It wraps them into a `ModelRequest`, adds the selected model name from `self.model`, limits the answer to 2048 tokens, enables a five-minute conversation cache, and turns off extra reasoning mode. It then awaits the SDK model's `complete` call and returns the resulting text string.

**Call relations**: Code that only needs text can call this through the `ModelLeg` interface. Inside, this method hands the prepared request to `self.model.complete`; the important construction step is the `ModelRequest`, which translates the extension's simple inputs into the SDK's expected request object.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 40–53)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This sends a tool-aware model request through the SDK and returns the model's next message. It is used when the model may need to respond in a structured conversation turn rather than only produce text.

**Data flow**: It receives a system prompt, previous messages, and tool schemas that describe the tools available to the model. It builds a `ModelRequest` containing those pieces plus the selected model, a 2048-token cap, a five-minute cache lifetime, and reasoning turned off. It awaits `self.model.turn` and returns the resulting `Message`.

**Call relations**: Replay-style code can call this through the `ReplayLeg` interface. This method prepares the SDK request, including the tools, then hands it to `self.model.turn` so the underlying model service can produce the next conversation message.

*Call graph*: 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal phase`

This file is part of a self-improvement loop. Its job is to create a candidate prompt update: a possible new version of an agent’s instructions that should help with a task type where the agent has shown friction. Think of it like asking an editor to revise a worker’s instruction manual after seeing a few places where the worker got confused.

The main class, PromptProposer, is given a model connection and uses it for one focused job. It takes the current system prompt, gathers a small set of mined examples from a TaskClass, and writes a clear request to the model: keep the agent’s general role intact, preserve its voice, and make only the smallest helpful change. This matters because a self-improving agent should not overfit to one narrow problem and accidentally damage its broader behavior.

The file also includes PromptCandidate, a small data container holding the task name and proposed prompt text. After the model replies, the code cleans common formatting wrappers, such as Markdown code fences. If the model gives back an empty answer or simply repeats the existing prompt, the proposal is treated as a no-op and rejected early by returning None.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This is the main entry point for creating a revised prompt candidate. It checks whether there are useful failure examples, asks the model for an improved full prompt, and only returns a candidate if the answer is non-empty and actually different from the current prompt.

**Data flow**: It receives the current system prompt and a task class containing mined examples of trouble. If the task class has no examples, it stops and returns None. Otherwise it builds a user message, sends it to the model with the proposer instructions, cleans the model’s text, compares it with the original prompt, and either returns None or a PromptCandidate containing the task name and revised prompt.

**Call relations**: During the proposal flow, this function calls PromptProposer._prompt to build the detailed request sent to the model. It wraps that request in a Message object for the model call, then passes the model’s reply through _clean before deciding whether to create a PromptCandidate.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This function writes the actual user-facing request that will be sent to the model. It combines the task class name, the current system prompt, and a limited number of problem examples so the model has enough context to suggest a careful revision.

**Data flow**: It receives the current prompt and a task class. It reads the task name and selected mined examples, trims each example’s request and problem text to a safe size, formats them into a readable block, and returns one complete instruction string for the model.

**Call relations**: PromptProposer.propose calls this when it is ready to ask the model for a revision. The resulting text becomes the content of the user Message that is sent to the model completion step.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This helper tidies the model’s answer so the rest of the code can compare and store the prompt text directly. It mainly removes extra whitespace and strips Markdown code fences if the model wrapped the prompt in them.

**Data flow**: It receives raw text from the model. It trims leading and trailing whitespace, checks whether the answer starts with a code fence like ``` and, if so, removes the opening and closing fence lines. It returns the cleaned prompt body as plain text.

**Call relations**: PromptProposer.propose calls this immediately after the model replies. The cleaned result is then checked for emptiness and compared with the current prompt before any PromptCandidate is created.

*Call graph*: called by 1 (propose).


### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `self-improvement evaluation`

This file is the evidence-gathering step for self-improvement. When the system invents a candidate prompt, it cannot simply trust that the new wording is better. It needs a fair trial. This file runs that trial by replaying the same saved tasks twice: once with the current prompt and once with the candidate prompt. Because the task and archived replay conditions stay the same, the prompt is the main thing being compared.

The central class is `CandidateEvaluation`. It has two model connections: one that replays the agent's behavior and one that judges the final answer. For each saved `TaskExample`, it asks the replay system to regenerate an answer under each prompt. Then it asks the judge model a simple question: did this answer correctly satisfy the user's request? The judge is instructed to return only a small JSON object, like a yes-or-no scorecard.

The results become `OutcomeLabel` records saying whether the candidate prompt was present and whether the answer succeeded. The file collects labels for the candidate's own task class, called the local set, and also for other task classes, called the global set. Finally it calls `two_stage_gate`, which accepts the candidate only if it improves where it is supposed to improve and does not meaningfully hurt other work.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: Runs the full comparison between a candidate prompt and the current prompt. It gathers success-or-failure evidence on both the focused held-out tasks and the broader held-out tasks, then asks the gate to decide whether the candidate should pass.

**Data flow**: It receives the candidate prompt, the current prompt, a local group of saved task examples, and optionally a global group. It turns each group into outcome labels by calling `_labels`. Those labels are then passed to `two_stage_gate`, which returns a `GateVerdict` saying whether the candidate cleared the improvement test.

**Call relations**: This is the public entry point of this evaluator. It calls `_labels` twice: first for the candidate's target area and then for the wider safety check. After both sets of evidence are ready, it hands them to `two_stage_gate`, which makes the final pass-or-fail decision.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: Builds the raw scorecard for a group of saved examples. For every task, it tries both prompts and records whether each resulting answer was accepted by the judge.

**Data flow**: It receives the candidate prompt, the current prompt, and a tuple of held-out task examples. It creates a `ReplayEvaluation`, then loops through each example twice: once using the current prompt and once using the candidate prompt. Each replay produces final answer text, which `_accepts` turns into true or false. The function returns a tuple of `OutcomeLabel` values, each marking which prompt arm was used and whether it succeeded.

**Call relations**: `evaluate` calls this helper whenever it needs evidence for a task set. Inside the loop, this function asks `ReplayEvaluation` to regenerate answers and asks `_accepts` to judge them. It packages the judged results into `OutcomeLabel` objects for the later gate decision.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: Asks the judge model whether one answer satisfies one user request. It converts the judge's JSON reply into a simple true-or-false result.

**Data flow**: It receives the original user request and the answer produced by replay. It sends both to the judge model with instructions to return only JSON containing an `accepted` field. It then looks for the JSON object in the judge's text, tries to parse it, and returns true only when the parsed object says `accepted` is exactly true. If the judge reply is missing JSON or contains invalid JSON, the answer is treated as not accepted.

**Call relations**: `_labels` calls this after each replayed answer is produced. This function creates the user message for the judge model, uses `json.loads` to read the judge's response, and hands back the boolean success value that becomes part of an `OutcomeLabel`.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `self-improvement evaluation`

This file solves a careful testing problem: how can you compare two prompt versions fairly when the original conversation used tools, such as searches or file actions? Instead of calling those tools again, it replays the conversation like a flight simulator. The model is asked to continue from the archived conversation, but whenever it asks for a tool, the code feeds back the exact tool result that was recorded in the original run.

The key idea is to isolate the prompt. The system prompt changes, but the tool results do not. First, the old final answer is removed, so the model must produce a fresh answer. Then the file builds a small tool list from the tools that appeared in the archive. It also builds a lookup table that says, “if the model asks for this tool with these same inputs, return this archived result.”

If the model asks for a tool call that was not in the archive, the replay is marked as “diverged.” That means the new prompt led the model off the known path, so the system cannot safely invent a tool result. The replay then returns the best text seen so far and marks the result as weaker evidence. A round limit prevents endless tool-call loops.

#### Function details

##### `_canonical_input`  (lines 35–36)

```
def _canonical_input(value: object) -> str
```

**Purpose**: This helper turns a tool input into a stable text form so the same input can be recognized later. It is used because dictionaries and nested data can otherwise appear in different orders while meaning the same thing.

**Data flow**: It receives any Python value used as a tool input. It converts that value into compact JSON text with keys sorted in a fixed order. The output is a string that can safely be used as part of a lookup key.

**Call relations**: When archived tool calls are indexed, archived_tool_results uses this helper to label each call by its exact input. Later, _feed_archived uses the same helper on a replayed tool call so it can find the matching archived result.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 39–52)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function prepares the conversation history that the replay model should see. It removes the original assistant’s final answer, so the model must generate a new one under the candidate prompt.

**Data flow**: It receives the full archived message sequence. Starting from the end, it removes trailing assistant messages that are plain final answers, but stops if it reaches an assistant message that contains tool requests. It returns the shortened conversation as the replay starting point.

**Call relations**: ReplayEvaluation.replay calls this near the start of a replay. The returned message history becomes the context sent to the model for the first replayed turn.

*Call graph*: called by 1 (replay).


##### `archived_tool_results`  (lines 55–77)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: This function builds the lookup table that lets replay answer tool calls from the archive instead of executing tools again. It connects each recorded tool request to the recorded tool result that answered it.

**Data flow**: It receives the archived messages. First it scans for tool result blocks and stores them by their tool-use id. Then it scans for tool-use blocks, finds their matching result, and stores that result under a key made from the tool name and canonicalized input. It returns that dictionary of reusable archived results.

**Call relations**: ReplayEvaluation.replay calls this before asking the model to replay anything. _feed_archived later depends on this table to answer replayed tool calls safely and consistently.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 80–97)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: This function creates the limited tool catalog shown to the replay model. The catalog includes only tool names that appeared in the archived run, which keeps the replay tied to the original path.

**Data flow**: It receives archived messages and scans them for tool-use blocks. For each distinct tool name, it creates a permissive ToolSchema, meaning a simple description of a tool and an input shape that allows any object fields. It returns the schemas as a tuple.

**Call relations**: ReplayEvaluation.replay calls this during setup and passes the resulting tool list into the model. The model sees enough information to reproduce archived calls, but this file never connects it to live tools.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 100–116)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: This function answers one round of replayed tool requests using archived results. If every requested tool call matches the archive, it builds the user message that gives those results back to the model.

**Data flow**: It receives the tool calls the model just requested and the archived-result lookup table. For each call, it canonicalizes the input and looks for the matching archived result. If any call is missing, it returns None to signal divergence. If all calls match, it creates new ToolResultBlock objects using the replay’s tool-use ids but the archived content and error flags, then returns them inside a user Message.

**Call relations**: ReplayEvaluation.replay calls this whenever the model asks for tools. A normal returned message is appended to the replay conversation; None tells the replay to stop and mark the outcome as diverged.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 129–150)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: This is the main replay procedure. It tests one archived task against one system prompt by repeatedly asking the model for the next assistant turn and feeding back archived tool results when needed.

**Data flow**: It receives the archived conversation and the system prompt to test. It builds the archived result lookup, creates the replay tool list, removes the old final answer, and then runs model turns up to the round limit. If the model gives a final text answer with no tool calls, it returns that text as a non-diverged ReplayResult. If the model asks for a tool call that cannot be matched to the archive, or if the round limit is reached, it returns the latest useful text and marks the result as diverged.

**Call relations**: This method ties together the helper functions in this file. It calls archived_tool_results, replay_tools, and replay_head to set up the replay, uses _feed_archived after each model tool request, and finally packages the outcome into ReplayResult for the evaluator or grader that asked for the replay.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation`

This file is the promotion gate for self-improvement. Imagine trying a new recipe against the old one: you do not want to switch after one lucky taste test, and you also do not want a recipe that improves dessert but ruins dinner. Here, the “recipe” is a candidate prompt, and the taste tests are replayed tasks judged as accepted or not accepted.

The file compares two groups, called arms: examples where the candidate prompt was present, and examples where it was absent. It counts successes in each group, then estimates how much the candidate improves the acceptance rate. Because small samples are noisy, it does not trust the raw difference alone. It builds a conservative confidence bound, meaning a cautious estimate of the improvement that tries to account for uncertainty.

There are two checks. First, the local check asks whether the candidate clearly improves the task class it was designed for, with enough examples on both sides. Second, the global check asks whether the candidate harms other task classes. This second check is intentionally forgiving: it blocks only when there is strong evidence of real regression. The main result is a GateVerdict, which says pass or fail, gives a human-readable reason, and records the key sample counts and lower-bound score.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This calculates a cautious lower estimate for a success rate, such as “how low might the true acceptance rate reasonably be?” It is used when the system wants to avoid over-trusting a small number of successes.

**Data flow**: It receives a number of accepted examples, a total number of examples, and optionally a confidence setting. If there are no examples, it returns 0. Otherwise it computes the observed success rate, adjusts it for uncertainty, and returns a value between 0 and 1 that represents the lower plausible success rate.

**Call relations**: This is a building block for the lift calculations. When lift_lower_bound and lift_upper_bound need cautious edges for each prompt arm, they call this function and combine its result with the matching upper-bound calculation.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This calculates a cautious upper estimate for a success rate, such as “how high might the true acceptance rate reasonably be?” It is used when the system needs to understand the most optimistic plausible outcome.

**Data flow**: It receives accepted count, total count, and optionally a confidence setting. If there are no examples, it returns 1, meaning the rate is completely uncertain at the top end. Otherwise it computes an uncertainty-adjusted upper value between 0 and 1.

**Call relations**: This supports both lower and upper lift estimates. lift_lower_bound uses it to be pessimistic about the old or absent arm, while lift_upper_bound uses it to be optimistic about the candidate or present arm.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: This estimates the candidate prompt’s improvement in the cautious direction. It answers: “What is the smallest improvement we can reasonably believe, after accounting for uncertainty?”

**Data flow**: It receives a Contingency summary: accepted and total counts for candidate-present examples and candidate-absent examples. If either side has no examples, it returns 0. Otherwise it compares the two observed acceptance rates and subtracts uncertainty from that difference, producing a conservative lower bound on the candidate’s lift.

**Call relations**: score_gate calls this after building the counts. Internally, this function asks wilson_lower_bound and wilson_upper_bound for cautious rate estimates, then combines them with a square-root calculation to measure uncertainty in the difference itself.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: This estimates the candidate prompt’s improvement in the optimistic direction. It answers: “Even giving the candidate the benefit of the doubt, how good could the improvement be?”

**Data flow**: It receives a Contingency summary for present and absent prompt arms. If either side has no examples, it returns 0. Otherwise it compares the observed rates and adds uncertainty, producing an upper bound on the candidate’s lift.

**Call relations**: global_non_inferior calls this when checking for harm on other task classes. It relies on wilson_upper_bound and wilson_lower_bound to build the optimistic end of the possible lift range.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: This turns individual replay outcomes into the four counts needed for comparison. It separates examples where the candidate prompt was present from examples where it was absent, then counts totals and successes for each side.

**Data flow**: It receives a tuple of OutcomeLabel records. Each label says whether the candidate was present and whether the answer succeeded. The function splits those labels into present and absent groups, counts accepted examples in each group, and returns a Contingency object with those four numbers.

**Call relations**: score_gate and global_non_inferior both call this first, because their statistical checks need counts rather than raw per-example labels. It hands those counts to the lift-bound functions through the returned Contingency.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: This performs the local promotion check for the target task class. It decides whether the candidate prompt shows enough reliable acceptance improvement to pass the main gate.

**Data flow**: It receives replay labels, plus optional thresholds for the required lower-bound lift and minimum examples per side. It converts labels into counts, calculates the cautious lower lift, and then checks two things: both sides have enough examples, and the lower-bound improvement is above the required floor. It returns a GateVerdict explaining pass or fail.

**Call relations**: two_stage_gate calls this as the first stage. score_gate delegates counting to contingency and cautious improvement scoring to lift_lower_bound, then packages the outcome into a GateVerdict for the caller.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: This checks whether the candidate avoids clear harm on other task classes. It is not trying to prove the candidate is globally better; it only rejects candidates when there is confident evidence they make other work meaningfully worse.

**Data flow**: It receives replay labels for the broader or held-out task set, plus optional harm margin and minimum sample count. It counts present and absent outcomes. If there are too few examples on either side, it returns true, allowing the candidate through because the evidence is too weak. Otherwise it calculates the optimistic upper lift and returns true unless even that optimistic estimate is worse than the allowed negative margin.

**Call relations**: two_stage_gate calls this only after the local score_gate has passed. It uses contingency for counts and lift_upper_bound to decide whether the global results show a real regression.

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: This is the full promotion decision. A candidate must first show a reliable local win, and then it must not show clear global harm.

**Data flow**: It receives two sets of replay labels: one for the local task class the candidate is meant to improve, and one for other task classes. It runs the local gate first. If that fails, it returns the local failure verdict immediately. If the local gate passes, it runs the global non-regression check. If global harm is detected, it returns a new failure verdict using the local score details. Otherwise it returns the successful local verdict.

**Call relations**: This function ties the file’s pieces together. It calls score_gate for the positive-evidence stage, then global_non_inferior for the safety stage, and returns the final GateVerdict that outside self-improvement code can use to decide whether to promote the candidate prompt.

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system what is enabled, safe, and available in this run.
- `reg-extension-catalog` — The shared list of installed extensions and the capabilities they registered for this deployment.
- `reg-model-catalog` — The shared catalog of AI models, providers, routing rules, reasoning modes, key lookup rules, and usage shapes.
- `reg-tool-registry` — The shared catalog of tools the model is allowed to call and the input rules for each tool.
- `reg-connector-tool-catalog` — The discovered connector actions from systems like Gmail, Slack, GitHub, Composio, Pipedream, and MCP servers.
- `reg-transcript-state` — The saved message history and transcript snapshots that are read, compacted, updated, audited, and shown later.
- `reg-turn-queue` — The durable queue of conversation turns, including admitted work, claimed work, failures, retries, and completion state.
- `reg-subagent-delivery` — The parent-child turn links and pending result records used when helper agents run work and report back.
- `reg-observability` — The shared logs, traces, metrics, trace links, and sanitized diagnostic records used to understand system behavior.
- `reg-prompt-governance` — The saved prompt proposals, approval status, evaluation results, and safety checks for changing agent instructions.
- `reg-evaluation-fixture-store` — Controlled non-production fixture data such as fake email inboxes, calendars, sample notes, and deterministic connector data used by demos, evaluations, and conformance tests.
