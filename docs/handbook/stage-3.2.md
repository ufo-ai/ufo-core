# Runtime process supervision  `stage-3.2`

This stage is shared behind-the-scenes support for the running system. Its job is to make sure server processes can be seen, stopped safely, and cleaned up if they disappear. It is like the control room for work that is already in progress.

runtime_instance.py keeps each live server process registered so the rest of the fleet knows it exists. It also runs background checks that look for work owned by processes that have died. When it finds such work, it helps recover it so jobs do not stay stuck forever. It also spreads a cancellation from a parent turn to any child work that was started under it.

cancellation.py defines the safe way to cancel one turn of work. A “turn” is one unit of activity in a workflow. It first tells the running workflow to stop, then marks the database record as cancelled, so the stored state matches what actually happened.

o11y.py sets up observability: traces, metrics, and structured logs that explain what the system did. It also redacts sensitive text so secrets are not sent to monitoring tools.

## Files in this stage

### Turn cancellation
Shared cancellation logic safely stops active workflow work before marking the corresponding database record as cancelled.

### `core/src/ufo/cancellation.py`

`domain_logic` · `cancellation flow`

A “turn” is one unit of work in the system, and some turns can start child turns of their own. This file does not try to cancel a whole tree of related turns. Instead, it provides the small, reliable building block for cancelling exactly one turn. Other parts of the system can call this same function whether the cancel request comes from a user-facing tool, an evaluation driver, or a background cleanup process.

The important rule here is order. The system first asks DBOS, the durable workflow engine, to cancel the workflow for the turn. Only after that request is made does it write “cancelled” into the turn’s database row. This is like turning off a machine before putting a “shut down” label on it. If the program crashes halfway through, the database will not falsely claim the turn is cancelled before the workflow was ever told to stop.

The function also avoids overwriting a turn that already finished normally or failed in its own way. It checks the current status first, and later updates the row only if the status is still non-terminal, meaning still not finished. This protects races where cancellation and normal completion happen at nearly the same time.

#### Function details

##### `cancel_one_turn`  (lines 23–58)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> bool
```

**Purpose**: Cancels one turn if it is still active. It first cancels the durable DBOS workflow, then records a cancelled terminal state in the database, so the stored status does not get ahead of the real workflow cancellation.

**Data flow**: It receives a DBOS client and a turn ID. It opens a workspace database transaction, reads the turn’s current status, and stops immediately with False if the turn is already in a final state. If the turn is still active, it asks DBOS to cancel the workflow whose ID matches the turn ID. Then it opens another database transaction and tries to update that turn row to status cancelled, storing the cancelled terminal frame and a fresh update time, but only if the row is still active. It returns True only when that database update actually changed one row; otherwise it returns False.

**Call relations**: This function is the shared cancellation primitive used by higher-level cancellation paths. Inside its own flow, it relies on workspace_tx to safely read and write the database, uses SQLAlchemy select and update statements to inspect and change the turn row, and hands the actual workflow stop request to DBOSClient.cancel_workflow_async before committing the cancelled status.

*Call graph*: 4 external calls (cancel_workflow_async, select, update, workspace_tx).


### Observability plumbing
Telemetry setup centralizes traces, metrics, structured logging, and sensitive-text protection for runtime operations.

### `core/src/ufo/o11y.py`

`io_transport` · `startup and cross-cutting during request or background work`

This file is the project’s observability toolkit. Observability means the clues operators use to understand a running system: logs for events, metrics for counts, and traces for following one piece of work across steps. Without this file, failures would be much harder to investigate, background work could lose its connection to the request that started it, and private data might accidentally be written into monitoring systems.

At startup, `init_o11y` can connect the app to an OpenTelemetry collector. OpenTelemetry is a common standard for sending traces, metrics, and logs to outside monitoring tools. If no endpoint is given, the file leaves the default no-op behavior in place, so the app can run without telemetry export.

During normal work, the file adds useful context automatically. It reads the current workspace from a shared context variable, like a name tag attached to the current task, and adds that workspace ID to spans and logs. It can also carry a trace link across a queue boundary by saving and later restoring a `traceparent` header.

The logging helpers (`log`, `warn`, and `log_error`) all pass through one central emitter. Before anything leaves the process, `redact_payload` and `redact_value` remove sensitive fields and make sure values are safe JSON-like data. Metrics are also centralized: only known metric names are allowed, so typos fail loudly instead of silently creating misleading dashboards.

#### Function details

##### `init_o11y`  (lines 64–83)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Connects the application to an OpenTelemetry collector, if one was configured. This sets up exporting for traces, metrics, and logs so outside tools can show what the system is doing.

**Data flow**: It receives an optional collector base URL. If the URL is missing, it changes nothing. If present, it builds the three exact export URLs, creates OpenTelemetry providers for traces, metrics, and logs, registers them globally, and installs a bridge so ordinary Python warnings can also be exported.

**Call relations**: This is the setup step for the whole file. It asks `_otlp_signal_urls` to build the collector URLs, then calls `_bridge_warning_logs` after the OpenTelemetry log pipeline exists so standard library warnings can flow into the same monitoring stream.

*Call graph*: calls 2 internal fn (_bridge_warning_logs, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 86–98)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Sends ordinary Python warning-and-error log messages into the OpenTelemetry log pipeline. This helps catch problems from libraries or extension points that do not use this file’s structured logging helpers.

**Data flow**: It receives the OpenTelemetry log provider created during startup. It creates a Python logging handler that listens for warning-level and higher messages, filters out this project’s own structured logger and OpenTelemetry’s own exporter logs, and attaches the handler to the root logger.

**Call relations**: It is called by `init_o11y` after logging export has been configured. From then on, warnings from other modules can reach the collector, while this file’s direct structured records still use `_emit_log`.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_otlp_signal_urls`  (lines 101–107)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Builds the exact HTTP endpoints used to send traces, metrics, and logs to an OpenTelemetry collector. This matters because the exporter sends to the precise URL it is given; it does not add the path automatically.

**Data flow**: It receives a base collector URL, removes any trailing slash, and returns three URLs: one ending in `v1/traces`, one in `v1/metrics`, and one in `v1/logs`.

**Call relations**: It is used by `init_o11y` before creating the trace, metric, and log exporters. Its output tells each exporter where to send its specific kind of telemetry.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 110–115)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Finds the current workspace ID and formats it as metadata for logs and traces. This lets call sites avoid passing the workspace ID everywhere by hand.

**Data flow**: It reads `current_workspace`, which is a context value set elsewhere for the current turn or job. If no workspace is active, it returns an empty dictionary. If one is active, it returns a dictionary containing that workspace ID as text.

**Call relations**: It is used by `turn_span` when creating trace spans and by `_emit_log` when writing logs. In both cases it acts like an automatic label maker for the current unit of work.

*Call graph*: called by 2 (_emit_log, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 118–124)

```
def current_traceparent() -> str | None
```

**Purpose**: Captures the currently active trace as a standard `traceparent` header. A traceparent is a small text value that lets another piece of work join the same trace later.

**Data flow**: It starts with an empty carrier dictionary, asks the trace context propagator to inject the current trace information into it, and returns the `traceparent` value if one was produced. If there is no valid active trace, it returns `None`.

**Call relations**: This function is meant to be called when a turn is admitted or queued, so the trace link can be stored with that work. Later, `turn_span` can read that saved value and connect the new span back to the original trace.


##### `turn_span`  (lines 128–151)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None) -> Iterator[Span]
```

**Purpose**: Creates a trace span around one durable turn of work. A span is a timed section of a trace, like a chapter in the story of a request.

**Data flow**: It receives the turn ID, conversation ID, and an optional saved `traceparent`. It builds safe span attributes, including the ambient workspace when present, removes sensitive fields through `redact_payload`, extracts the parent trace context if a traceparent was provided, and starts a server-style span named `turn`. It yields that span to the code inside the `with` block, then closes it when the block ends.

**Call relations**: It calls `_ambient_scope` to tag the span and `redact_payload` to keep attributes safe. It also uses OpenTelemetry’s tracer to make the span current, so logs emitted through `_emit_log` while the span is active can be correlated with it.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); 2 external calls (get_tracer, cast).


##### `redact_payload`  (lines 154–160)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Cleans a dictionary of fields before it is logged or attached to a trace. It removes fields whose names look sensitive, such as prompt, credential, secret, or token.

**Data flow**: It receives a mapping of field names to values. For each field, it normalizes the key by removing underscores and hyphens and lowercasing it, drops the field if the key is sensitive, and otherwise passes the value through `redact_value`. It returns a new JSON-like dictionary safe for telemetry.

**Call relations**: It is used by `turn_span` for trace attributes and by `_emit_log` for log attributes. It also works together recursively with `redact_value` when nested dictionaries appear inside larger data.

*Call graph*: calls 1 internal fn (redact_value); called by 3 (_emit_log, redact_value, turn_span).


##### `redact_value`  (lines 163–173)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Turns an arbitrary Python value into a safe JSON-like value for telemetry. It preserves simple values, cleans nested containers, and stringifies unusual objects.

**Data flow**: It receives any object. If the object is already a simple JSON-style value, it returns it unchanged. If it is a mapping, it converts keys to strings and sends the nested dictionary through `redact_payload`. If it is a non-string sequence, it cleans each item. For anything else, it returns the object’s string form.

**Call relations**: It is called by `redact_payload` for each non-sensitive field. When it sees a nested dictionary, it calls `redact_payload` again, so redaction applies at every depth rather than only at the top level.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 176–181)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Writes a normal informational structured log event. Use it for routine events that are useful to understand what happened but are not warnings or errors.

**Data flow**: It receives an event name and any extra fields. It passes them to `_emit_log` with information-level severity, so they are redacted, tagged with the current workspace, and sent to both Python logging and OpenTelemetry logging.

**Call relations**: This is one of the public convenience wrappers around `_emit_log`. Code elsewhere can call `log` without needing to know the OpenTelemetry severity values or Python logging levels.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 184–186)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured error log event. Use it when something has gone wrong and should be visible as an error in monitoring tools.

**Data flow**: It receives an event name and extra fields. It forwards them to `_emit_log` with error severity, where fields are combined with ambient workspace data, redacted, and emitted.

**Call relations**: Like `log` and `warn`, this function funnels all actual log writing through `_emit_log`. That keeps redaction and workspace tagging consistent for error records.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 189–191)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured warning log event. Use it for expected but notable situations that an operator may want to notice.

**Data flow**: It receives an event name and extra fields. It sends them to `_emit_log` with warning severity, which produces the final redacted structured log record.

**Call relations**: This is the warning-level wrapper around `_emit_log`. It gives callers a simple way to mark an event as important without duplicating the shared logging steps.

*Call graph*: calls 1 internal fn (_emit_log).


##### `_emit_log`  (lines 194–208)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Performs the actual structured log emission for info, warning, and error events. It is the central checkpoint where workspace context is added and sensitive data is removed.

**Data flow**: It receives an event name, OpenTelemetry severity information, a Python logging level, and caller-supplied fields. It adds the ambient workspace fields, redacts the combined payload, writes a Python log record under the `ufo` logger, and emits an OpenTelemetry log record with the same event and attributes.

**Call relations**: It is called by `log`, `warn`, and `log_error`, which choose the severity. It calls `_ambient_scope` for automatic workspace labels and `redact_payload` for safety before handing the event to Python logging and OpenTelemetry.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `emit_metric`  (lines 211–219)

```
def emit_metric(name: str, amount: int=1, **dimensions: str) -> None
```

**Purpose**: Increments one of the project’s approved counter metrics. A counter is a number that only goes up, useful for tracking how often important events happen.

**Data flow**: It receives a metric name, an amount to add, and optional text dimensions that describe the count. It first checks the name against the approved metric list and raises an error if the name is unknown. For a known name, it reuses an existing OpenTelemetry counter or creates it the first time, then adds the amount with the given dimensions.

**Call relations**: This function is the public metric entry point for the rest of the system. It talks directly to OpenTelemetry’s meter and keeps a local cache of counters so repeated metric updates do not recreate the same counter.

*Call graph*: 1 external calls (get_meter).


### Process supervision
Runtime instance orchestration keeps server processes visible, recovers work from dead processes, and propagates cancellations to child work.

### `core/src/ufo/runtime_instance.py`

`orchestration` · `startup, main loop, shutdown`

This file is the fleet’s safety patrol. Each server process writes a small database row saying “I am here,” then keeps updating that row every few seconds like a heartbeat. Other processes use those fresh heartbeats to tell which workers are alive. If a process dies, its heartbeat stops getting updated; after a short stale period, its unfinished DBOS workflows can be safely recovered and sent back through the durable work system. DBOS is the workflow engine here: it records work so it can resume after crashes instead of losing progress.

The file also runs a cancellation reconciler. Cancelling one turn only marks that turn as cancelled. But turns can spawn child turns, like branches from a tree. The reconciler periodically looks for live descendants under any cancelled ancestor and cancels them too. This makes cancellation eventually reach the whole branch, even if the original canceller crashed halfway through.

The important idea is caution. A live process must never have its work “recovered” by another process, because that could start duplicate work. So recovery only touches executors whose heartbeat is missing or stale. Each loop logs temporary failures and keeps going, because these background jobs are meant to survive brief database or workflow-system problems.

#### Function details

##### `record_fleet_seat`  (lines 38–53)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: Creates the database row that represents this running server process before the workflow system starts. This makes the process visible as alive so other processes do not mistake its future work for abandoned work.

**Data flow**: It receives this process’s unique instance id. It opens a database transaction, inserts a runtime instance row with no workspace attached, stamps the current time as its heartbeat and creation time, then writes a log entry saying the fleet seat was recorded.

**Call relations**: This is the first part of the liveness story. It uses the shared database transaction helper to write the seat, and later the Heartbeat methods keep that same row fresh or remove it at shutdown.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 66–76)

```
async def run(self) -> None
```

**Purpose**: Runs forever, refreshing this process’s heartbeat at a fixed interval. If one database update fails, it logs the problem and keeps trying so a brief database hiccup does not make a healthy process look dead.

**Data flow**: It takes the Heartbeat object’s instance id as stored state. On each loop it calls Heartbeat.beat to update the database row, catches database errors, logs them, waits a short time, and repeats without returning.

**Call relations**: This is the continuous driver for Heartbeat.beat. It is meant to run beside the server while the process is alive, so ExecutorRecovery can later compare fresh heartbeat rows against pending workflow executors.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 78–88)

```
async def beat(self) -> None
```

**Purpose**: Writes one fresh liveness stamp for this process. It is the actual database update behind the heartbeat loop.

**Data flow**: It reads the Heartbeat object’s instance id. It opens a database transaction and updates that runtime instance row’s heartbeat and updated timestamps to the current database time. It does not return data; the database row is the output.

**Call relations**: Heartbeat.run calls this on every tick. The rows it refreshes are later read by ExecutorRecovery._live_executors to decide which executors are still alive and must not be recovered.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 90–96)

```
async def retire(self) -> None
```

**Purpose**: Removes this process’s runtime instance row during a graceful shutdown. This tells peers immediately that the seat is gone instead of making them wait for the heartbeat to become stale.

**Data flow**: It reads the Heartbeat object’s instance id. It opens a database transaction and deletes the matching runtime instance row. It returns nothing, but changes the shared liveness table.

**Call relations**: The server shutdown path calls this through core/src/ufo/serve._stop_executor. After it runs, ExecutorRecovery._live_executors will no longer see this executor as alive.

*Call graph*: called by 1 (_stop_executor); 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 116–122)

```
async def run(self) -> None
```

**Purpose**: Runs the recovery sweep forever on a timer. Its job is to keep checking for workflow work that was assigned to processes that are no longer alive.

**Data flow**: It uses the configured interval from the ExecutorRecovery object. Each cycle it sleeps, calls ExecutorRecovery.sweep, logs database or DBOS workflow errors if they happen, and then continues looping.

**Call relations**: This is the background driver for ExecutorRecovery.sweep. Every server process can run it, so any surviving process can help recover work left behind by a crashed peer.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 124–132)

```
async def sweep(self) -> None
```

**Purpose**: Finds workflow executors that have pending work but no fresh heartbeat, then asks DBOS to recover that work. This prevents queued or half-dispatched work from staying stuck after a process crash.

**Data flow**: It asks ExecutorRecovery._pending_executors for executor ids attached to pending workflows, and ExecutorRecovery._live_executors for executor ids with fresh runtime rows. It subtracts live executors from pending executors. For each remaining stranded executor, it calls DBOS recovery in a worker thread and logs how many workflows were recovered.

**Call relations**: ExecutorRecovery.run calls this repeatedly. It relies on Heartbeat.beat keeping live rows fresh, and hands stranded executor ids to DBOS._recover_pending_workflows so the durable workflow system can re-dispatch them safely.

*Call graph*: calls 2 internal fn (_live_executors, _pending_executors); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_executors`  (lines 134–146)

```
async def _pending_executors(self) -> set[str]
```

**Purpose**: Looks in DBOS for executors that currently own pending workflows. These are candidates for recovery, but only if their executor is not still alive.

**Data flow**: It asks DBOS to list pending workflows, without loading large input or output payloads. It warns in the log if the scan hits the configured limit, then extracts and returns the set of executor ids found on those pending workflow records.

**Call relations**: ExecutorRecovery.sweep calls this before comparing against live executors. Its result is only half the decision: ExecutorRecovery._live_executors supplies the safety check that prevents recovering work from a process that is still running.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 148–158)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: Reads the database to find executor ids whose heartbeat is still fresh. These executors are considered alive and their workflows must not be recovered by another process.

**Data flow**: It computes a cutoff time by subtracting the allowed stale age from the current time. It queries runtime instance rows whose heartbeat is newer than that cutoff, converts their ids to strings, and returns them as a set.

**Call relations**: ExecutorRecovery.sweep calls this alongside ExecutorRecovery._pending_executors. The difference between the two sets is what tells the sweep which pending workflow owners are truly stranded.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `CancelReconciler.run`  (lines 182–188)

```
async def run(self) -> None
```

**Purpose**: Runs the cancellation reconciliation loop forever. It periodically checks whether any live child or grandchild turns should be cancelled because an ancestor turn was cancelled.

**Data flow**: It uses the reconciler’s interval setting. Each cycle it sleeps, calls CancelReconciler.sweep, logs database or DBOS errors if they happen, and continues looping.

**Call relations**: This is the timed driver for CancelReconciler.sweep. Every server process can run it, so cancellation cleanup does not depend on the original process that noticed or requested the cancellation.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 190–197)

```
async def sweep(self) -> None
```

**Purpose**: Finds live turns that sit underneath a cancelled ancestor and cancels them one by one. This is how cancellation spreads down a tree of spawned work.

**Data flow**: It opens a database transaction and runs the query built by CancelReconciler._orphans_query. For each matching turn, it enters that turn’s workspace context, calls cancel_one_turn with the DBOS client and turn id, and logs when a turn was actually cancelled.

**Call relations**: CancelReconciler.run calls this on each interval. It depends on CancelReconciler._orphans_query to identify descendants needing cancellation, then hands each turn to ufo.cancellation.cancel_one_turn so cancellation uses the same safe primitive as direct turn cancellation.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (cancel_one_turn, owner_tx, log, ws).


##### `CancelReconciler._orphans_query`  (lines 199–234)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: Builds the database query that finds every non-finished turn with a cancelled ancestor. It climbs parent links, so it catches not only direct children but also deeper descendants.

**Data flow**: It starts from turns whose status is still non-terminal, then constructs a recursive SQL query. The query repeatedly follows parent_turn_id upward until it finds a cancelled ancestor or runs out of parents, and returns each matching live turn id with its workspace id.

**Call relations**: CancelReconciler.sweep calls this to decide what needs cancellation. The query only identifies the orphaned live turns; the sweep then performs the actual cancellation through cancel_one_turn.

*Call graph*: called by 1 (sweep); 1 external calls (select).
