# Observability, accounting, live infrastructure, and operator diagnostics  `stage-21` (cross-cutting infrastructure)

This stage is the system’s control room. It is shared behind-the-scenes infrastructure used during startup, normal requests, background jobs, and shutdown. It helps operators see what is happening, understand costs, and inspect live data safely.

The observability file sets up traces, metrics, and structured logs. In plain terms, it leaves organized breadcrumbs about what the system is doing, while hiding sensitive values before they are sent out. The accounting file is the bookkeeper. It records token use, network use, spending limits, billing changes, and summaries. The pricing file supplies the price list used to turn token counts into money, and fingerprints that list so old bills can be checked later.

Operator access is handled by a shared helper that checks bearer-token login, stores it in a secure cookie, and limits each request to the right workspace. The debugger extension then uses those rules to serve read-only pages and endpoints for conversations, turns, files, transcripts, compactions, and live streams. The memory extension adds a similar read-only view of stored memories. The small package files simply make the debugger and Redis hub extensions importable.

## Files in this stage

### Usage pricing and accounting
Core billing logic records usage, computes model costs, enforces spend limits, and exposes auditable spend summaries.

### `core/src/ufo/accounting.py`

`domain_logic` · `cross-cutting billing, spend checks, export jobs, and reporting`

This file centers on a ledger, which is like an accounting notebook in the database. Every billable or countable event is written there: model tokens used during a turn, model tokens used by background workspace jobs, sandbox model calls, and sandbox egress request counts. Without this file, the system could lose track of what providers charged, double-count retries, fail to stop work when a budget is reached, or be unable to explain where money went.

The file has three main jobs. First, it writes usage rows carefully. Turn token usage is written once per run attempt, while sandbox usage and egress counts are accumulated safely so simultaneous writes do not overwrite each other. Each priced row stores the pricing version used, so later audits can match charges to the rate table in force at the time.

Second, it creates export records for outside billing systems. Instead of re-computing what to send each time, it freezes a usage delta into a durable export row. That makes retrying safe: if delivery fails, the same frozen record can be sent again without changing the amount.

Third, it checks and reports spending. Spend caps can apply to a whole workspace, one member, or one agent. The evaluator sums recent ledger entries and decides whether a turn may continue, should be parked, or must be rejected. The rollup reader turns ledger rows into human-facing spend reports.

#### Function details

##### `applicable_caps_absent`  (lines 44–50)

```
def applicable_caps_absent(workspace_id: UUID, member_id: UUID | None, agent_id: UUID) -> bool
```

**Purpose**: This is a quick memory check for the common case where no spend caps apply. It lets the system skip a database lookup for a few seconds when it has just learned that a particular workspace, member, and agent combination has no caps.

**Data flow**: It receives a workspace id, optional member id, and agent id. It looks up that exact triple in a small in-memory cache and compares the saved expiry time with the current monotonic clock, which is a clock used for measuring elapsed time. It returns true only if the cache entry exists and has not expired.

**Call relations**: This function is a fast-path helper used before doing full spend-cap enforcement. It relies on entries written by SpendEvaluator.decide through _note_absent_caps when a real database check found no applicable caps.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_caps`  (lines 53–62)

```
def _note_absent_caps(key: tuple[UUID, UUID | None, UUID]) -> None
```

**Purpose**: This remembers, briefly, that a specific workspace/member/agent combination had no spend caps. It keeps the no-cap fast path useful without letting the memory cache grow forever.

**Data flow**: It receives the cache key for one workspace, optional member, and agent. It checks the current monotonic time, removes expired entries if the cache is already large, then stores a new expiry time a few seconds in the future. It changes only the in-memory cache.

**Call relations**: SpendEvaluator.decide calls this after it asks the database for caps and finds none. Later, applicable_caps_absent can use that note to avoid another database round trip for the same exact combination.

*Call graph*: called by 1 (decide); 1 external calls (monotonic).


##### `record_turn_usage`  (lines 65–105)

```
async def record_turn_usage(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, attempt: str='', pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This records the token usage for one turn attempt in the billing ledger. It is built to avoid double billing if the same attempt is replayed, while still billing separate resumed attempts correctly.

**Data flow**: It receives a database connection, workspace id, turn id, model name, token usage details, an attempt id, and a pricing table. It totals all token categories, stops if the total is zero, creates a stable ledger id for this workspace/turn/token-dimension/attempt combination, checks whether that ledger row already exists, and inserts a priced ledger row if it does not. The output is no returned value; the database ledger may gain one row.

**Call relations**: Turn-running code calls this when model usage needs to be billed. It asks Pricing.micro_usd to calculate the cost, uses ledger_id_for to make the write replay-safe, and writes through the async database connection.

*Call graph*: calls 1 internal fn (micro_usd); 4 external calls (execute, insert, select, ledger_id_for).


##### `read_turn_cost`  (lines 108–126)

```
async def read_turn_cost(connection: AsyncConnection, turn_id: UUID) -> tuple[int, int, str] | None
```

**Purpose**: This reads the final billed token total and cost for a turn. It matters because a parked and later resumed turn may have several billing rows, one per run attempt.

**Data flow**: It receives a database connection and turn id. It sums token ledger rows for that turn, including both token count and priced micro-dollars, and also reads the model name. It returns a tuple of total tokens, total micro-USD, and model, or returns null if the turn has no token billing.

**Call relations**: Code that needs to display or reason about a turn’s cost calls this after usage has been recorded by record_turn_usage. It reads directly from the ledger and does not modify anything.

*Call graph*: 2 external calls (execute, select).


##### `record_workspace_usage`  (lines 129–165)

```
async def record_workspace_usage(connection: AsyncConnection, workspace_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This records token usage from a background workspace job that is not tied to a specific turn. It makes sure workspace-level spend totals and workspace caps include that model usage.

**Data flow**: It receives a database connection, workspace id, model name, usage details, and pricing table. It totals the token counts, exits if the total is zero, computes the price, and inserts a new ledger row with no turn id. It returns nothing, but the ledger gains a fresh usage row for a real provider call.

**Call relations**: Background job code calls this after completing a model call outside the normal turn flow. It uses Pricing.micro_usd for the amount and uuid4 for a fresh ledger id, because each real job invocation should be represented separately.

*Call graph*: calls 1 internal fn (micro_usd); 3 external calls (execute, insert, uuid4).


##### `record_egress_request`  (lines 168–197)

```
async def record_egress_request(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, amount: int=1) -> None
```

**Purpose**: This counts sandbox egress requests for a turn. These requests are metered as counts, not charged as dollars, so they are tracked separately from token spend.

**Data flow**: It receives a database connection, workspace id, turn id, and request count. It builds a stable ledger id for the egress dimension and performs an insert-or-increment operation: if the row does not exist, it creates it; if it exists, it adds to the amount. It returns nothing, and the ledger’s egress count for the turn increases.

**Call relations**: The sandbox egress proxy calls this when a sandbox makes outbound requests. It uses ledger_id_for so all egress for the same turn lands in one egress row, and the database conflict update prevents concurrent requests from losing counts.

*Call graph*: 2 external calls (execute, ledger_id_for).


##### `record_sandbox_tokens`  (lines 200–251)

```
async def record_sandbox_tokens(connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, model: str, usage: Usage, pricing: Pricing=CORE_PRICING) -> None
```

**Purpose**: This records model tokens used inside the sandbox through the egress proxy. It keeps these costs separate from host-side turn tokens while still adding them to the same overall ledger.

**Data flow**: It receives a database connection, workspace id, turn id, model name, usage details, and pricing table. It totals tokens, exits if there are none, calculates the price, and then inserts or updates one sandbox-token ledger row for the turn. Existing rows have their token amount and priced cost increased atomically.

**Call relations**: The sandbox egress path calls this when sandbox code invokes a model provider. It uses Pricing.micro_usd for pricing and ledger_id_for for a stable per-turn sandbox-token row, while the database conflict update keeps multiple sandbox calls from overwriting each other.

*Call graph*: calls 1 internal fn (micro_usd); 2 external calls (execute, ledger_id_for).


##### `mint_usage_exports`  (lines 276–381)

```
async def mint_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, floor: datetime, key_slot_for: Callable[[str], str | None]) -> None
```

**Purpose**: This freezes newly available usage into export-intent rows for an external billing consumer. It is the safety layer that lets the system retry billing delivery without changing the amounts being resent.

**Data flow**: It receives a database connection, workspace id, consumer name, earliest allowed export time, and a function that maps model names to provider credential slots. It reads stored workspace credential slots, finds ledger rows that have grown beyond what was previously exported, skips zero-priced egress, waits for sandbox-token rows to settle after a terminal turn, and inserts export rows describing each new delta. It returns nothing, but the ledger_export table may gain frozen pending export records.

**Call relations**: A usage-export job calls this before reading pending exports. It reads from ledger, turn, credential, and prior ledger_export rows, and writes new ledger_export rows with conflict protection so repeated or concurrent minting does not create duplicates.

*Call graph*: 5 external calls (now, timedelta, execute, or_, select).


##### `read_pending_usage_exports`  (lines 384–429)

```
async def read_pending_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: This retrieves frozen usage export records that have not yet been acknowledged by an external billing consumer. It gives the delivery code a stable batch to send.

**Data flow**: It receives a database connection, workspace id, consumer name, and maximum number of records. It joins pending export rows with their ledger rows to include descriptive fields such as dimension, model, price digest, and turn id. It returns a tuple of UsageExport objects in mint order.

**Call relations**: After mint_usage_exports creates export intents, the export delivery worker calls this to fetch what still needs to be sent. It does not recalculate usage; it reads the frozen deltas already stored in ledger_export.

*Call graph*: 3 external calls (__init__, execute, select).


##### `ack_usage_exports`  (lines 432–456)

```
async def ack_usage_exports(connection: AsyncConnection, workspace_id: UUID, consumer: str, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: This marks exported usage records as acknowledged after the external billing system accepts them. Once acknowledged, they stop appearing in pending export reads.

**Data flow**: It receives a database connection, workspace id, consumer name, and the UsageExport records that were successfully delivered. It builds matching conditions from each export’s ledger id and starting amount, then updates those rows with an acknowledgement time. It returns nothing, but matching export rows are marked complete.

**Call relations**: The export delivery worker calls this only after the outside consumer accepts a batch. If a crash happens before this function runs, read_pending_usage_exports will return the same frozen records again, allowing safe retry.

*Call graph*: 3 external calls (execute, or_, update).


##### `metered_workspaces`  (lines 459–462)

```
def metered_workspaces() -> WorkspaceCandidates
```

**Purpose**: This identifies workspaces that have ever had metered usage. It gives export jobs a broad but simple list of places worth checking.

**Data flow**: It takes no direct input. It creates a workspace-candidate query based on distinct workspace ids found in the ledger table. It returns a WorkspaceCandidates object that the job system can use to iterate possible owners.

**Call relations**: Usage-export scheduling code calls this to decide which workspaces to scan. It delegates the candidate wrapping to owner_candidates, while the later export steps do the precise pending-work checks.

*Call graph*: 1 external calls (owner_candidates).


##### `SpendEvaluator.decide`  (lines 497–512)

```
async def decide(self, connection: AsyncConnection, pending_micro_usd: int) -> SpendDecision
```

**Purpose**: This decides whether a turn is allowed to spend more money under the workspace’s configured caps. It can allow the turn, park it until the cap is raised, or reject it outright.

**Data flow**: It receives a database connection and the extra pending cost in micro-USD. It reads all caps that apply to this workspace/member/agent, caches the no-cap case if none exist, sums recent spend for each cap, adds the pending cost, and compares that total to each limit. It returns a SpendDecision with an outcome and, when blocked, a human-readable message.

**Call relations**: Admission or mid-turn enforcement code calls this before allowing more work. It coordinates _applicable_caps, _used_micro_usd, _message, and _note_absent_caps; reject wins if any breached cap is configured to reject.

*Call graph*: calls 4 internal fn (_applicable_caps, _message, _used_micro_usd, _note_absent_caps); 1 external calls (__init__).


##### `SpendEvaluator._applicable_caps`  (lines 514–542)

```
async def _applicable_caps(self, connection: AsyncConnection) -> tuple[SpendCap, ...]
```

**Purpose**: This finds the spend caps that matter for the current turn. A cap may apply to the whole workspace, the current member, or the current agent.

**Data flow**: It receives a database connection and reads the evaluator’s stored workspace id, optional member id, and agent id. It queries the spend_cap table for matching rows and converts each row into a SpendCap object. It returns all applicable caps as a tuple.

**Call relations**: SpendEvaluator.decide calls this first. The returned caps are then passed one by one into _used_micro_usd so decide can see whether any limit has been exceeded.

*Call graph*: called by 1 (decide); 4 external calls (__init__, execute, or_, select).


##### `SpendEvaluator._used_micro_usd`  (lines 544–570)

```
async def _used_micro_usd(self, connection: AsyncConnection, cap: SpendCap) -> int
```

**Purpose**: This calculates how much money has already been spent within one cap’s rolling time window. The answer is used to decide whether the next bit of work would exceed the cap.

**Data flow**: It receives a database connection and one SpendCap. It computes the cutoff time from the cap’s window, builds the right database query for the cap scope, and sums priced ledger rows since that cutoff. It returns the used amount as an integer number of micro-USD.

**Call relations**: SpendEvaluator.decide calls this for each applicable cap. Depending on the cap scope, it sums by workspace directly, by member through conversations and turns, or by agent through turns.

*Call graph*: called by 1 (decide); 4 external calls (now, timedelta, execute, select).


##### `SpendEvaluator._message`  (lines 572–583)

```
def _message(self, outcome: SpendOutcome, breaches: list[SpendCap]) -> str
```

**Purpose**: This creates the user-facing explanation when a spend cap blocks a turn. It chooses the tightest breached cap so the message names the most relevant limit.

**Data flow**: It receives the final outcome, either park or reject, and the list of breached caps. It finds the breached cap with the smallest dollar limit, converts micro-USD into ordinary dollars, and formats a short message. It returns that message string.

**Call relations**: SpendEvaluator.decide calls this only after it has found cap breaches. The returned text is placed inside the SpendDecision that the caller can show to a user or store with the turn state.

*Call graph*: called by 1 (decide).


##### `SpendRollup.read`  (lines 633–709)

```
async def read(self, connection: AsyncConnection, window_seconds: int) -> SpendReport
```

**Purpose**: This builds a spend report for a workspace over a recent rolling window. It gives both the grand total and useful breakdowns for humans and audits.

**Data flow**: It receives a database connection and a window length in seconds. It computes the cutoff time, sums all priced ledger rows for the workspace, then separately groups the same window by usage dimension, member, agent, and pricing digest. It returns a SpendReport containing the total and all breakdown rows.

**Call relations**: The command-line spend view and web spend view call this when they need a report. It reads only from the ledger and related turn, conversation, member, and agent tables, then packages the results into DimensionTotal, SubjectTotal, PriceDigestTotal, and SpendReport objects.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, now, timedelta, execute, select).


### `core/src/ufo/models/pricing.py`

`domain_logic` · `during usage accounting and billing calculations`

This file is the small billing calculator for model usage. In this project, usage is counted in different kinds of tokens: input tokens, output tokens, cache reads, and cache writes. Each kind can have a different price, so the file keeps one rate for each kind in `ModelPrice`.

The prices are stored as micro-USD per million tokens. A micro-USD is one millionth of a US dollar, which lets the code use whole numbers instead of floating-point money values. That avoids rounding surprises.

The file also creates a digest, which is a stable SHA-256 fingerprint of the whole price table. Think of it like a tamper-evident label on a jar: if any model price changes, the label changes too. Billing records can store this digest to show which exact price list was used at the time.

When the system needs to price usage, it looks up the model name, multiplies each token count by its matching rate, adds the parts together, and divides by one million tokens. If the model is unknown, it logs a warning and returns zero. That is important for old or historical records: billing should not crash just because a model name is missing from the current table.

#### Function details

##### `price_digest`  (lines 24–39)

```
def price_digest(prices: Mapping[str, ModelPrice]) -> str
```

**Purpose**: Creates a stable version stamp for a table of model prices. Someone uses this when they need to prove or record exactly which prices were used for billing.

**Data flow**: It receives a mapping from model names to `ModelPrice` values. It sorts the models, turns the prices into compact JSON text, then runs SHA-256 over that text. It returns a string beginning with `sha256:` followed by the fingerprint.

**Call relations**: When a new `Pricing` object is built, `pricing_from` calls this function to attach a digest to the price table. Inside, it relies on JSON serialization to make a repeatable text form and `hashlib.sha256` to make the fingerprint.

*Call graph*: called by 1 (pricing_from); 2 external calls (sha256, dumps).


##### `usage_priced_micro_usd`  (lines 42–54)

```
def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int
```

**Purpose**: Calculates the cost of one usage record for one model, in micro-USD. It is the core price calculation used by the billing path.

**Data flow**: It receives a model name, a `Usage` record with token counts, and a price table. It looks up the model's rates, multiplies each token count by the matching rate, adds the results, and converts from per-million-token pricing to a final micro-USD amount. If the model is not in the table, it logs `pricing.unknown_model` and returns zero instead of failing.

**Call relations**: `Pricing.micro_usd` calls this function whenever accounting code asks for the cost of a model usage record. If a model is missing, this function hands the situation to the logging system so the problem is visible without stopping accounting.

*Call graph*: called by 1 (micro_usd); 1 external calls (log).


##### `Pricing.micro_usd`  (lines 64–65)

```
def micro_usd(self, model: str, usage: Usage) -> int
```

**Purpose**: Provides the convenient method for pricing usage through a `Pricing` object. Callers do not need to pass the price table separately because the object already carries it.

**Data flow**: It receives a model name and a `Usage` record. It uses the `prices` stored inside the `Pricing` instance and passes everything to `usage_priced_micro_usd`. It returns the calculated micro-USD cost.

**Call relations**: Accounting code calls this method when recording sandbox token usage, turn usage, or workspace usage. This method is the friendly doorway into the lower-level calculation function, keeping the accounting code focused on recording usage rather than knowing how prices are stored.

*Call graph*: calls 1 internal fn (usage_priced_micro_usd); called by 3 (record_sandbox_tokens, record_turn_usage, record_workspace_usage).


##### `pricing_from`  (lines 68–71)

```
def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing
```

**Purpose**: Builds a complete `Pricing` object from a raw table of model prices. It also freezes in the digest that identifies that exact table.

**Data flow**: It receives a mapping of model names to `ModelPrice` values. It copies that mapping into a plain dictionary, calculates its digest with `price_digest`, and returns a new `Pricing` object containing both the table and the digest.

**Call relations**: This function is used when the system needs to prepare a price table for later billing calculations. It calls `price_digest` first so the returned `Pricing` object can both calculate costs and carry the version stamp for those prices.

*Call graph*: calls 1 internal fn (price_digest); 1 external calls (__init__).


### Observability foundations
Shared observability utilities initialize traces, metrics, structured logs, and redaction for safe operational insight.

### `core/src/ufo/o11y.py`

`util` · `startup and cross-cutting runtime observability`

This file is the system’s “black box recorder.” It records what happened, how long work took, which workspace it belonged to, and which counters changed, then sends that information to an OpenTelemetry collector. OpenTelemetry is a standard toolkit for collecting logs, traces, and metrics from running software.

At startup, `init_o11y` can connect the app to an OTLP endpoint, which is the collector address that receives observability data. If no endpoint is configured, the app keeps OpenTelemetry’s default no-op behavior, meaning these calls are safe but do not export anything.

During runtime, this file adds useful context automatically. If code is running inside a workspace scope, logs and spans are tagged with that workspace ID without every caller having to pass it by hand. A “span” is one timed piece of work inside a trace; `turn_span` wraps a durable turn so it can be connected to the trace that caused it, even across a queue hop.

The file also provides friendly logging helpers: `log`, `warn`, and `log_error`. Before records are emitted, fields like prompts, tokens, secrets, and credentials are removed, and nested values are converted into safe JSON-like data. Metrics are limited to a known list so misspelled or accidental metric names fail immediately instead of silently polluting dashboards.

#### Function details

##### `init_o11y`  (lines 63–82)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Sets up OpenTelemetry exporting for traces, metrics, and logs when an OTLP collector endpoint is provided. Without this setup, observability calls still exist, but they do not send useful data outside the process.

**Data flow**: It receives an optional collector base URL. If the value is missing, it returns immediately. If present, it builds separate URLs for traces, metrics, and logs, creates OpenTelemetry providers for each signal, attaches exporters that send data to those URLs, and installs a bridge so warning-level standard Python logs can also reach the collector.

**Call relations**: This is the startup entry for this file’s observability wiring. It asks `_otlp_signal_urls` to prepare the exact collector paths, builds the OpenTelemetry pieces around those paths, and then calls `_bridge_warning_logs` so warnings from ordinary Python logging are not lost.

*Call graph*: calls 2 internal fn (_bridge_warning_logs, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 85–97)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Connects ordinary Python warning and error logs to the OpenTelemetry log pipeline. This matters because not every library or module emits structured OpenTelemetry logs directly.

**Data flow**: It receives a log provider that knows how to export OpenTelemetry log records. It creates a standard logging handler that only accepts warning-and-above records, filters out this project’s own structured logger and OpenTelemetry’s internal exporter logs, and attaches the handler to the root Python logger.

**Call relations**: It is called by `init_o11y` after the OpenTelemetry log provider exists. From then on, warnings from other modules can flow into the same collector path as the project’s structured logs, while avoiding feedback loops from OpenTelemetry’s own failure messages.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_otlp_signal_urls`  (lines 100–106)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Builds the exact HTTP URLs where traces, metrics, and logs should be sent. This avoids sending all data to the collector’s base URL, which would not be the correct endpoint.

**Data flow**: It receives a base OTLP endpoint string. It removes any trailing slash, appends the standard per-signal paths for traces, metrics, and logs, and returns the three finished URLs.

**Call relations**: It is used by `init_o11y` before exporters are created. The returned URLs are handed to the trace, metric, and log exporters so each kind of observability data goes to the collector route that expects it.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 109–114)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Finds the current workspace ID, if code is running inside one, and turns it into metadata for logs and traces. This saves callers from manually adding the workspace ID everywhere.

**Data flow**: It reads the current workspace value from `current_workspace`. If there is no active workspace, it returns an empty dictionary. If there is one, it returns a dictionary containing the workspace ID as text.

**Call relations**: It is used by `_emit_log` so log records know which workspace they belong to, and by `turn_span` so trace spans carry the same workspace context. It acts like a label maker that quietly tags records created inside a workspace scope.

*Call graph*: called by 2 (_emit_log, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 117–123)

```
def current_traceparent() -> str | None
```

**Purpose**: Returns the current trace context in the standard W3C `traceparent` header format. This lets the system preserve a trace across a boundary such as putting work on a queue.

**Data flow**: It starts with an empty carrier dictionary, asks the OpenTelemetry trace context propagator to write the current span context into it, and then returns the `traceparent` value if one was written. If no valid current span exists, it returns `None`.

**Call relations**: Other code can call this when admitting or scheduling a turn, store the returned header, and later pass it into `turn_span`. That allows a later piece of work to rejoin the trace that originally caused it.


##### `turn_span`  (lines 127–150)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None) -> Iterator[Span]
```

**Purpose**: Creates a trace span around one durable turn, so that turn appears as a named unit of work in observability tools. It can either start a fresh trace or attach to an existing one using a saved `traceparent` value.

**Data flow**: It receives a turn ID, a conversation ID, and an optional traceparent header. It builds span attributes from those IDs plus the current workspace, redacts them for safety, extracts a parent trace context if a traceparent was provided, and starts a server-style span named `turn`. It yields that span to the caller and closes it when the caller leaves the context block.

**Call relations**: This function calls `_ambient_scope` to tag the span with the workspace and `redact_payload` to keep attributes safe. It is meant to wrap turn execution, linking queued or spawned turns back to the trace that admitted them.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); 2 external calls (get_tracer, cast).


##### `redact_payload`  (lines 153–159)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Removes sensitive fields from a structured set of log or trace attributes. This prevents prompts, tokens, secrets, credentials, and similar data from being exported to observability systems.

**Data flow**: It receives a mapping of field names to values. For each field, it normalizes the name by removing underscores and dashes and lowercasing it; if the normalized name is sensitive, the field is dropped. All remaining values are passed through `redact_value`, and the function returns a JSON-like dictionary safe for logging or tracing.

**Call relations**: It is used by `turn_span` before span attributes are attached and by `_emit_log` before log attributes are emitted. It also works together recursively with `redact_value` when nested dictionaries appear inside a value.

*Call graph*: calls 1 internal fn (redact_value); called by 3 (_emit_log, redact_value, turn_span).


##### `redact_value`  (lines 162–172)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Converts one value into a safe JSON-like form while preserving useful information where possible. It is the helper that lets redaction work on nested lists and dictionaries, not just flat fields.

**Data flow**: It receives any Python object. Simple JSON-friendly values, such as strings, numbers, booleans, and `None`, pass through unchanged. Dictionaries are converted to string-keyed mappings and sent through `redact_payload`; non-string sequences are converted item by item; anything else is turned into text.

**Call relations**: It is called by `redact_payload` for every non-sensitive field value. When it sees a nested dictionary, it calls `redact_payload` again, so sensitive keys are removed even deep inside structured data.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 175–180)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Emits a normal informational structured log event. Callers use it for ordinary noteworthy events that should appear in logs and correlate with the active trace.

**Data flow**: It receives an event name and any number of extra fields. It forwards those values to `_emit_log` with information-level severity, where workspace context is added and sensitive data is redacted before output.

**Call relations**: This is the friendly public helper for routine logs. It delegates the shared logging work to `_emit_log`, which sends the record to both standard Python logging and the OpenTelemetry log pipeline.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 183–185)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Emits a structured error log event. Callers use it when something failed and operators should be able to find it as an error.

**Data flow**: It receives an event name and extra fields describing the error or situation. It passes them to `_emit_log` with error-level severity, so the final record is tagged, redacted, and emitted as an error.

**Call relations**: This is the error-level companion to `log`. It relies on `_emit_log` for the common path of adding workspace context, redacting fields, and sending the record through the logging systems.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 188–190)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Emits a structured warning log event. Callers use it for expected but important conditions that are not full errors but still deserve operator attention.

**Data flow**: It receives an event name and extra descriptive fields. It passes them to `_emit_log` with warning-level severity, which adds context, removes sensitive data, and emits the final log record.

**Call relations**: This is the warning-level public helper. Like `log` and `log_error`, it funnels into `_emit_log` so all structured logs behave consistently.

*Call graph*: calls 1 internal fn (_emit_log).


##### `_emit_log`  (lines 193–207)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Does the shared work behind `log`, `warn`, and `log_error`: add context, redact sensitive values, and emit the record through both logging paths. It is the central point that keeps structured logs consistent.

**Data flow**: It receives an event name, OpenTelemetry severity details, a standard Python logging level, and caller-provided fields. It combines those fields with the current workspace metadata, redacts the result, writes one record to the project’s standard Python logger with the redacted data attached, and emits one OpenTelemetry log record with the same event and attributes.

**Call relations**: It is called by the three public logging helpers. It calls `_ambient_scope` to add workspace context and `redact_payload` to make the attributes safe before handing the record to Python logging and OpenTelemetry logging.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `emit_metric`  (lines 210–218)

```
def emit_metric(name: str, amount: int=1, **dimensions: str) -> None
```

**Purpose**: Increments one of the project’s approved counter metrics. It prevents accidental new metric names by rejecting anything not listed in this file.

**Data flow**: It receives a metric name, an amount to add, and optional string dimensions that label the measurement. It checks the name against the registered metric list, creates and caches the OpenTelemetry counter the first time that name is used, and then adds the amount with the provided dimensions.

**Call relations**: Runtime code calls this when a counted event happens, such as a turn starting or a recovery path being used. It talks to OpenTelemetry’s meter system to create counters lazily, so counters are only built when first needed.

*Call graph*: 1 external calls (get_meter).


### Operator access control
Operator authentication helpers provide the shared session and workspace authorization rules used by web diagnostics.

### `core/src/ufo/ext/operator.py`

`domain_logic` · `request handling`

This file solves a security and convenience problem for internal operator tools. Operators need to browse special web surfaces, but their long-lived access token must not leak into URLs, browser history, or server access logs. So this file accepts the token only from safer places: an Authorization header, an HTTP-only session cookie, or the body of the one POST request that starts a session.

The flow is like checking in at a secure front desk. First, `operator_bearer` looks for the visitor’s credential in approved places. Then `resolve_operator_workspace` verifies that credential and checks that the email belongs to the operator-only domain. Only after that gate passes can the request choose a workspace with `?ws=`. If no workspace is requested, it uses the workspace already written into the token. A requested workspace can be either a raw UUID or a customer domain name, which is converted into a stable UUID.

Finally, `bind_operator_session` turns the posted token into a browser session cookie and redirects back to the page. The cookie is shared by all operator surfaces, so the operator does not need to log in separately for each tool. Importantly, this module does not keep the token signing secret itself; it asks the token verifier to check the token.

#### Function details

##### `operator_bearer`  (lines 24–37)

```
async def operator_bearer(request: Request) -> str
```

**Purpose**: Finds the bearer token for an operator request from approved locations only. It deliberately refuses to read tokens from the URL, because URLs are often stored in logs and browser history.

**Data flow**: It receives a web request. It first checks the Authorization header for a `Bearer` token, then checks the shared operator session cookie, and finally, only for POST requests, checks the submitted form field named `token`. It returns the cleaned token text if one is found, or an empty string if the request has no usable token.

**Call relations**: This is the first step in deciding whether an operator request is allowed. `resolve_operator_workspace` calls it when it needs the token before verifying who the operator is and what workspace the request should use.

*Call graph*: called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `resolve_operator_workspace`  (lines 40–64)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Decides which workspace an operator request is allowed to access. It verifies the token, checks that the user belongs to the operator email domain, and then chooses either the token’s own workspace or a workspace requested through `?ws=`.

**Data flow**: It receives a web request and the surface authentication object. It asks `operator_bearer` for the token, verifies the token’s claims, reads the claimed workspace and email address, and rejects the request if the token is missing, invalid, or not from the operator domain. If there is no `ws` query value, it returns the workspace UUID from the token. If `ws` is present, it returns it as a UUID when possible, or turns the lowercased domain name into a stable UUID using DNS-style UUID generation. If anything required is invalid, it returns `None`.

**Call relations**: This is the authorization gate for operator surfaces. After getting the token from `operator_bearer`, it hands verification to `verified_claims`, uses `_email_domain` to enforce the operator-domain rule, and then uses UUID parsing or UUID generation to produce the workspace scope that the rest of the page should use.

*Call graph*: calls 1 internal fn (operator_bearer); 4 external calls (verified_claims, _email_domain, UUID, uuid5).


##### `bind_operator_session`  (lines 67–79)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts an operator browser session by saving the posted token into the shared operator cookie. This lets the operator authenticate once and then browse multiple internal tools without putting the token in the URL.

**Data flow**: It receives the current surface context and web request. It reads the submitted form field named `token`. If the field is missing or blank, it returns a JSON error response with a bad-request status. If the token is present, it creates a redirect response back to the same URL, sets the operator session cookie on that response, and returns it to the browser.

**Call relations**: This function is used when the operator session is being opened. It reads the form body, builds either a `JSONResponse` for an error or a `RedirectResponse` for success, and calls `set_session_cookie` so later requests can be authenticated by `operator_bearer` through the cookie.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


### Operator diagnostic surfaces
Debugger and memory extension surfaces expose read-only operator tools for inspecting workspaces, conversations, files, transcripts, live streams, and stored memories.

### `extensions/debugger/ufo_ext_debugger/__init__.py`

`other` · `import/package discovery`

In Python, a folder often needs an `__init__.py` file to be treated as an importable package. This file plays that simple packaging role for the `ufo_ext_debugger` extension. Think of it like a label on a drawer: it does not contain the tools, but it tells Python that the drawer is part of the project and can be opened by name. Because the file is empty, it does not set up any objects, run startup code, or expose helper functions directly. Its importance is structural: without it, depending on the Python version and packaging setup, other parts of the system might not be able to import modules from this debugger extension in the expected way.


### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

This file is the bridge between a browser-based debugging page and the stored session data for a single workspace. Think of it like a locked observation window: an operator can look inside a workspace's sessions, but this file does not provide tools to change them. The broader system decides who is allowed in and which workspace they are allowed to see; once a request reaches these handlers, the provided SurfaceContext already carries that workspace scope, so every lookup is naturally limited to that workspace. The file first loads the built React app from static/index.html and serves it as the main page. The page then calls JSON endpoints under api/ to ask for workspace metadata, conversations, turns, transcripts, compaction records, and workspace files. Most handlers follow the same pattern: read an ID from the URL, reject it if it is not a valid UUID, ask SurfaceContext for the relevant read-only view, and return either JSON or a 404-style error. One endpoint streams a live turn using Server-Sent Events, a simple browser-friendly way for the server to keep sending updates over one connection. The helper _sse converts internal live frames, such as text updates or tool calls, into named browser events. The ROUTES table at the bottom wires these handlers to their HTTP paths, including a POST path that stores an operator bearer token in a safer cookie rather than putting it in a URL.

#### Function details

##### `app_page`  (lines 40–45)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the debugger's browser app. If the frontend has not been built yet, it fails loudly with an instruction so the operator does not see a blank or misleading page.

**Data flow**: It receives the workspace-bound context and the incoming web request, but it does not need to read details from either one. It checks the already-loaded APP_HTML text; if it exists, it wraps that HTML in an HTTP response, and if it does not, it raises an error explaining how to build the app.

**Call relations**: This is the handler for the debugger's main GET page. Once the route calls it, it hands the finished HTML to HTMLResponse so the browser can load the frontend that will later call the API routes in this file.

*Call graph*: 1 external calls (HTMLResponse).


##### `workspace_meta`  (lines 48–55)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns basic information about the current workspace for display in the debugger. It also tries to expose the Slack team ID when this workspace is connected to Slack.

**Data flow**: It starts with the SurfaceContext, which already knows the workspace being inspected. It asks for the Slack installation value, strips the internal team: prefix if present, and returns JSON containing the workspace UUID and the Slack team ID or null.

**Call relations**: The frontend calls this after loading so it can label the workspace it is showing. The function relies on SurfaceContext.installation for the stored Slack connection and then hands the plain metadata to JSONResponse.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 58–60)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the list of conversations visible in the current workspace. This gives the debugger its starting list of sessions to inspect.

**Data flow**: It asks the SurfaceContext for the workspace's conversation summaries. Each returned entry is turned into JSON-friendly data, and the whole list is sent back as a JSON response.

**Call relations**: This API route is used by the debugger page when it needs to show available conversations. It delegates the actual workspace-scoped lookup to SurfaceContext.list_conversations and only formats the result for the browser.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 63–68)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the turns, or individual back-and-forth steps, for one conversation. It protects the rest of the lookup from malformed conversation IDs.

**Data flow**: It reads conversation_id from the request path and passes it through _uuid_param. If that cannot produce a valid UUID, it returns a not-found JSON error; otherwise it asks SurfaceContext for the turns in that conversation and returns them as JSON.

**Call relations**: The conversation detail view calls this when an operator opens a conversation. This handler uses _uuid_param for safe ID parsing, then relies on SurfaceContext.list_turns for the actual read.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 71–78)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the transcript for one conversation, if a transcript exists. A transcript is the readable record of what happened in that conversation.

**Data flow**: It takes the conversation_id from the URL, turns it into a UUID with _uuid_param, and returns a not-found error if the ID is invalid. With a valid ID, it asks SurfaceContext.read_transcript for the transcript; if none exists it returns a not-found error, otherwise it serializes the transcript into JSON.

**Call relations**: The debugger calls this when an operator wants the full conversation record. The function performs only validation and response shaping, while SurfaceContext.read_transcript supplies the workspace-scoped data.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 81–85)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the compaction points for a conversation. A compaction is when older conversation context is summarized to save space while keeping the important meaning.

**Data flow**: It reads and validates the conversation_id from the path. If the ID is bad, it returns a not-found JSON error; if it is good, it asks SurfaceContext for the compaction indexes or records available for that conversation and returns them as a JSON list.

**Call relations**: The frontend uses this before asking for a specific compaction record. This handler depends on _uuid_param for the URL ID and SurfaceContext.list_compactions for the stored compaction information.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 88–103)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the details of one compaction, showing what messages existed before, what remained after, and what summary replaced the removed detail.

**Data flow**: It reads conversation_id and index from the request path. The conversation ID must be a valid UUID and the index must be all digits; otherwise it returns a not-found error. It then asks SurfaceContext.read_compaction for that exact record and, if found, returns JSON with the index, before messages, after messages, and summary.

**Call relations**: After the debugger has listed compactions, it calls this route to inspect one of them. The function validates the path values, asks SurfaceContext.read_compaction for the record, and formats the nested message and summary objects for JSONResponse.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 106–111)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files associated with a particular conversation's workspace area. This lets an operator see what files were available or produced during that conversation.

**Data flow**: It validates the conversation_id from the URL. If invalid, it returns a not-found JSON error; if valid, it asks SurfaceContext.list_workspace_files for file entries and returns their JSON form as a list.

**Call relations**: The debugger calls this when showing the files panel for a conversation. The route uses _uuid_param for safe parsing and SurfaceContext.list_workspace_files for the actual file listing.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 114–124)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams the bytes of one workspace file to the browser. It is used when an operator chooses to inspect or download a file from a conversation.

**Data flow**: It reads the conversation_id and requested file path from the route. If the conversation ID is invalid, if the file path is rejected, or if no stream is found, it returns a not-found JSON error. Otherwise it returns a streaming binary response so the file can be delivered without loading it all into memory at once.

**Call relations**: This is the follow-up route after workspace_files shows available files. It uses _uuid_param for the conversation ID, asks SurfaceContext.read_workspace_file for a safe byte stream, and hands that stream to StreamingResponse.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 127–134)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns detailed information about one turn. A turn is a single unit of activity in a conversation, such as one model response or tool-using step.

**Data flow**: It reads turn_id from the URL and converts it with _uuid_param. If the ID is invalid, or SurfaceContext.turn_detail finds no matching turn, it returns a not-found JSON error. Otherwise it serializes the turn detail and returns it as JSON.

**Call relations**: The debugger calls this when an operator selects a specific turn. The function validates the route ID, delegates the lookup to SurfaceContext.turn_detail, and formats the result for JSONResponse.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 137–142)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a live stream of updates for one turn. This lets the debugger watch a turn as it produces text, tool calls, cost updates, and completion signals.

**Data flow**: It reads and validates turn_id, then checks that the turn exists. If not, it returns a not-found JSON error. If the turn exists, it reads the Last-Event-ID header, which tells where a previously dropped stream should resume, and returns a text/event-stream response backed by _events.

**Call relations**: The browser calls this endpoint when it wants live updates for a turn. This function checks the turn with SurfaceContext.turn_detail, then hands the ongoing work to _events and wraps that async stream in StreamingResponse.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 145–147)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Turns the system's live turn feed into bytes that can be sent over an HTTP stream. It is the small adapter between internal live frames and browser-readable Server-Sent Events.

**Data flow**: It receives a SurfaceContext, a turn UUID, and a cursor string saying where to resume. It asks SurfaceContext.tail for each new live frame after that cursor, converts every frame with _sse, and yields the resulting bytes one event at a time.

**Call relations**: stream calls this after it has checked that the requested turn exists. _events then follows SurfaceContext.tail for ongoing updates and hands each frame to _sse so the browser receives correctly formatted event messages.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 150–170)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one live frame as a Server-Sent Event message. It gives each frame a readable event name, such as text, tool, cost, or terminal, so the debugger frontend can react appropriately.

**Data flow**: It receives a cursor and one LiveFrame object. If the cursor is not empty, it writes it as the event id for resume support. It then identifies the frame type, serializes the frame's raw JSON payload, and returns the complete byte string in Server-Sent Events format.

**Call relations**: _events calls this for every live frame it receives from the context tail. This helper is the last formatting step before StreamingResponse sends the bytes to the browser.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 173–177)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: Safely reads a UUID from a route parameter. It prevents malformed IDs in URLs from causing the rest of a handler to run as if the ID were valid.

**Data flow**: It takes a request and the name of a path parameter. It reads that path value and tries to build a UUID object from it; if the text is not a valid UUID, it returns None instead.

**Call relations**: Most detail routes call this before reading conversation or turn data. By returning either a real UUID or None, it gives those handlers a simple choice: continue with a trusted ID or return a not-found error.

*Call graph*: called by 8 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, workspace_file, workspace_files); 1 external calls (UUID).


### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `request handling`

This file is the doorway for the “memory explorer,” a browser page used by operators to see what the Memory extension has saved for a workspace. Think of it like a stockroom inventory sheet: it does not change anything in storage, but it shows what is on the shelves.

The file serves two things. First, it serves a static HTML page from `static/memory.html`. That page is the user interface. Second, it provides an API endpoint that returns the actual memory records as JSON, a common web format for structured data.

A key idea here is workspace scoping. The operator session has already been checked and tied to a workspace before these reads happen. The code then opens the Memory extension’s own workspace-scoped store, so it reads from the extension’s `memory_item` table under the same workspace boundary. This matters because memory data can belong to different workspaces, and the explorer must not accidentally show one workspace’s records to another.

The route list at the bottom connects web requests to the right action: show the page, bind an operator session, or return the memory list. Without this file, operators would have no built-in surface for checking what the memory system currently contains.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the memory explorer web page to the browser. It is used when an operator opens the surface itself, before the page asks for any memory data.

**Data flow**: It receives the surface context and the incoming web request, checks whether the HTML file was successfully loaded when the module started, and then wraps that HTML text in an HTTP response. If the file is missing, it raises an error instead of serving a broken or empty page.

**Call relations**: The route for the main GET request calls this function when the operator visits the memory surface. Its main handoff is to `HTMLResponse`, which turns the saved HTML text into a browser-readable response.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns all memory records for the current workspace as JSON. It is what the explorer page calls when it needs the data to display.

**Data flow**: It receives the surface context, including the workspace id, and the incoming request. It creates an extension context for the Memory extension’s own scoped store, asks the store inventory code for memory items in that workspace, converts each item into JSON-friendly data, and returns the list as a JSON HTTP response.

**Call relations**: The `api/memories` route calls this function when the browser asks for the memory list. Inside, it builds the extension transaction context with `ScopedStore`, `CredentialAccess`, and `ExtensionContext`, then hands the actual database read to `ufo_ext_memory.store.inventory`. After that, it hands the final list to `JSONResponse` so the browser can consume it.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).


### Live hub package marker
The Redis hub package initializer makes live infrastructure modules importable by the rest of the system.

### `extensions/redis_hub/ufo_ext_redis_hub/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder often needs an `__init__.py` file to be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools in other files, but this label tells Python that the drawer belongs to the project and can be opened by name. Nothing runs from this file, and it does not set up Redis, connect to anything, or expose helper functions. Its value is structural: without it, some Python versions, packaging tools, or import paths might not recognize `ufo_ext_redis_hub` as a proper package, which could make the Redis hub extension harder or impossible to import reliably.

## 📊 State Registers Touched

- `reg-config` — The effective deployment settings that tell the system how to start, what services to use, and what safety rules are enabled.
- `reg-workspace-directory` — The shared record of workspaces, members, owners, agents, and workspace boundaries.
- `reg-auth-session` — The login and token state that proves who a user or client is across gateway, web, terminal, and admin requests.
- `reg-seat-entitlements` — The shared seat and access-limit state that decides which members may use the agent in a workspace.
- `reg-surface-installations` — The stored links between outside surfaces, workspaces, channels, conversations, and agents.
- `reg-conversation-transcript` — The stored conversation history, messages, files, speakers, and outcomes that later stages read and append to.
- `reg-turn-state` — The durable status of each unit of agent work, including whether it is waiting, running, paused, finished, failed, or cancelled.
- `reg-live-stream` — The live feed of turn updates, text chunks, tool events, costs, and final frames that clients and debuggers can watch.
- `reg-runtime-fleet` — The shared heartbeat and ownership records that show which server processes are alive and which work they are responsible for.
- `reg-model-catalog` — The shared list of available AI models, providers, limits, prices, and client adapters.
- `reg-prompt-state` — The agent instructions, rendered prompt templates, fingerprints, and governed prompt-change proposals.
- `reg-compaction-state` — The saved summaries and reduced conversation versions used when a conversation is too large for a model call.
- `reg-workspace-storage` — The shared file, blob, artifact, and mount state that stores workspace bytes and files shared back to users.
- `reg-egress-policy` — The network access and proxy state that decides which sandbox traffic is allowed, audited, billed, or given injected secrets.
- `reg-subagent-tree` — The shared parent-child work structure for delegated agents, including child turns, messages, waits, and cancellations.
- `reg-source-pages` — The source connections, sync cursors, imported pages, removal markers, and page-change records from outside systems.
- `reg-search-index` — The searchable text chunks, embeddings, and selected index backend used to find relevant stored content.
- `reg-memory-store` — The durable memories, memory pages, recall events, and consolidation state used for long-term recall.
- `reg-knowledge-graph` — The stored entities and relationships extracted from pages so the system can look up connected facts.
- `reg-accounting-ledger` — The usage, price, spend-cap, billing, export, and cost records used to track and limit money spent by workspaces and turns.
- `reg-observability-context` — The shared tracing, metrics, structured logs, and trace-parent links used to understand work across processes and turns.
- `reg-self-improvement-evals` — The mined failure examples, replay results, grades, and statistical evidence used by self-improvement jobs before proposing prompt changes.
- `reg-source-sync-backoff` — Per-source sync error counters, retry/backoff state, and last-result throttling used to decide when background imports should run again.
- `reg-redis-backplane-pool` — The optional Redis client/backplane connection state used to share live stream infrastructure across server processes.
- `reg-slack-connect-provisioning` — Durable Slack Connect customer-channel and invitation provisioning state, including retry/idempotency progress for admin background jobs.
