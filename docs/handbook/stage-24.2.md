# Shared Core Runtime Utilities  `stage-24.2`

This stage is shared behind-the-scenes support. It is not one feature by itself, and it is not only for startup or shutdown. Instead, many parts of the system call these helpers while doing their normal work.

The listings helper gives the project a safe, consistent way to move through long lists a page at a time. A page is just a small slice of a bigger result set, like one screen of search results. It turns an item’s position into a cursor token, which acts like a bookmark. The next request uses that bookmark to continue from the right place, reducing the chance of missing items or showing the same item twice.

The observability helper is the system’s “dashboard wiring.” It sets up tracing, metrics, and structured logs. In plain terms, these record what happened, how long it took, and where work traveled through the system. Before this information leaves the process, it redacts sensitive fields so private data is not accidentally exposed. Together, these utilities make runtime behavior easier to use, inspect, and trust.

## Files in this stage

### Pagination Helpers
Shared cursor-based listing utilities provide consistent pagination across runtime features.

### `core/src/ufo/listings.py`

`domain_logic` · `request handling`

This file solves a common problem in growing lists: while someone is reading page 1, new rows may be added. If the system used simple page numbers or offsets, an item could move between pages and be shown twice or missed entirely. Instead, this file uses keyset paging, which means “start after this exact row position” rather than “start at item number 20.”

Every listing is expected to sort the same way: newest first, using `created_at` and then the row id to break ties. The tie-breaker matters because two rows can have the same timestamp. Together, the timestamp and id act like a precise bookmark.

`ListingCursor` is that bookmark. It can be encoded into a query-string token for a web link, then decoded later. If the token is broken or fake, the code raises `MalformedCursor` instead of silently showing the wrong page.

`page_query` prepares a database query for one page. It asks for one extra row so the system can tell whether there is another page beyond this one. `page_of` then turns those raw rows into a `ListingPage`, trims the extra row, reverses rows when needed, and creates “older” and “newer” cursors for navigation. In short, this file is the shared paging ruler used by different listings so they all behave consistently.

#### Function details

##### `ListingCursor.encode`  (lines 42–45)

```
def encode(self) -> str
```

**Purpose**: Turns a cursor bookmark into a single text token that can be placed in a URL or request. This lets a client come back later and ask for rows older or newer than the same exact position.

**Data flow**: It starts with a `ListingCursor` containing a timestamp, an item id, and a direction flag. It chooses the word `newer` or `older`, joins that with the timestamp and item id using a separator, and returns the resulting string. It does not change the cursor itself.

**Call relations**: This is the outward-facing half of the cursor pair: page-building code creates cursors, and this method makes them safe to send to a client as text. Later, `ListingCursor.decode` performs the opposite job when that text comes back.


##### `ListingCursor.decode`  (lines 48–60)

```
def decode(cls, token: str) -> 'ListingCursor'
```

**Purpose**: Reads a cursor token from a client and turns it back into a precise listing position. It rejects tokens that are missing parts, have an invalid direction, contain a bad timestamp, or do not contain a valid UUID-style item id.

**Data flow**: It receives a text token. It splits the token into direction, timestamp, and item id; checks that the direction is either `newer` or `older`; converts the timestamp from ISO text into a `datetime`; and validates the item id as a UUID. If everything is valid, it returns a `ListingCursor`; if not, it raises `MalformedCursor` so the caller can report a bad cursor instead of guessing.

**Call relations**: The web artifact and memory listing surfaces call this when a request arrives with a cursor. It relies on Python’s timestamp parser and UUID validator to prove the token names a real-shaped position, and uses `MalformedCursor` to signal that the request cannot be trusted.

*Call graph*: called by 2 (workspace_artifacts, workspace_memory); 3 external calls (__init__, fromisoformat, UUID).


##### `page_query`  (lines 74–96)

```
def page_query(query: sa.Select[Any], cursor: ListingCursor | None, limit: int, *, created_at: sa.ColumnElement[datetime], ident: sa.ColumnElement[Any]) -> sa.Select[Any]
```

**Purpose**: Takes a caller’s database query and adds the ordering, cursor boundary, and limit needed to fetch one stable page. It is the database-side half of the paging system.

**Data flow**: It receives an existing SQLAlchemy query, an optional cursor, a page size, and the two database columns that define position: creation time and id. If there is no cursor, it asks for the newest rows first. If the cursor asks for newer rows, it temporarily reverses the ordering so the database can walk in that direction. It adds a `limit + 1` cap, and if a cursor exists, it adds a comparison that keeps only rows beyond that cursor position. The result is a new SQL query ready to run.

**Call relations**: Listing code uses this before reading from the database. It uses `sqlalchemy.tuple_` to compare the timestamp and id together as one position, and `UUID` to turn the cursor’s item id text back into the id value used by the database. After the query is run, its rows are meant to be passed to `page_of`.

*Call graph*: 2 external calls (tuple_, UUID).


##### `page_of`  (lines 99–128)

```
def page_of(rows: Sequence[SourceT], cursor: ListingCursor | None, limit: int, *, render: Callable[[SourceT], RowT], position: Callable[[SourceT], tuple[datetime, str]]) -> ListingPage[RowT]
```

**Purpose**: Turns the rows returned by `page_query` into a clean page response with rows plus navigation cursors. It decides whether there are older or newer pages available.

**Data flow**: It receives the raw rows, the cursor that led here if any, the requested page size, a `render` function for converting raw rows into output rows, and a `position` function for reading each row’s timestamp and id. It checks whether an extra row was fetched, trims the page to the requested size, reverses it if the database was walked toward newer rows, and then builds a `ListingPage`. The output contains the rendered rows and optional cursors for the older and newer directions.

**Call relations**: This is the response-side partner to `page_query`: `page_query` fetches the right slice, and `page_of` packages that slice for the caller. It creates the final `ListingPage` and uses its nested helper `page_of.at` to make boundary cursors from the first and last visible rows.

*Call graph*: 1 external calls (__init__).


##### `page_of.at`  (lines 118–120)

```
def at(source: SourceT, *, newer: bool) -> ListingCursor
```

**Purpose**: Builds a cursor for one visible row at the edge of a page. That cursor becomes the bookmark a client can use to move older or newer from that edge.

**Data flow**: It receives one source row and a direction flag. It calls the supplied `position` function to extract the row’s timestamp and item id, then creates and returns a `ListingCursor` with that position and direction.

**Call relations**: This helper is used inside `page_of` when the page has rows and needs navigation controls. `page_of` calls it for the last row to make an older cursor and for the first row to make a newer cursor, depending on whether those directions actually have more data.

*Call graph*: 1 external calls (__init__).


### Observability Helpers
Shared tracing, metrics, logging, and redaction utilities make runtime behavior visible and safe to report.

### `core/src/ufo/o11y.py`

`io_transport` · `startup and cross-cutting runtime`

Observability means leaving useful breadcrumbs while the program runs: logs say what happened, metrics count and time things, and traces connect related work across steps. This file wires the project into OpenTelemetry, a standard way to collect those breadcrumbs and send them to an outside collector using OTLP, OpenTelemetry’s network format.

At startup, `init_o11y` can install exporters for traces, metrics, and logs. If no endpoint is configured, it leaves OpenTelemetry in its harmless default state. Once enabled, the rest of the file provides small, consistent helpers for the application to use: `log`, `warn`, and `log_error` write structured events; `emit_metric` increments known counters; `emit_histogram` records timings; and `turn_span` wraps one durable “turn” of work in a trace span, like putting a labeled folder around everything that happens during that turn.

A major concern here is safety. Log and trace fields are passed through redaction before export, so keys such as prompts, content, credentials, secrets, and tokens are dropped. Error classes are also bounded to a known list so one unexpected exception name cannot create an unlimited number of metric labels, which would make dashboards expensive or noisy. Without this file, the system would still run, but operators would lose much of their ability to debug slow turns, failed tools, database pressure, and model errors safely.

#### Function details

##### `init_o11y`  (lines 200–223)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Sets up OpenTelemetry export for traces, metrics, and logs when an OTLP collector endpoint is provided. This is the main switch that turns observability from local no-op behavior into real exported telemetry.

**Data flow**: It receives an optional collector base URL. If the URL is missing, it changes nothing. If present, it builds separate URLs for trace, metric, and log uploads, creates providers for each signal, attaches exporters and batching, registers them globally, and finally connects standard Python warnings and errors into the same log pipeline.

**Call relations**: This is the top-level setup function for this file. During setup it asks `_otlp_signal_urls` to create the exact upload URLs, creates OpenTelemetry providers and exporters, and then calls `_bridge_warning_logs` so ordinary warning-level logs from other modules also reach the collector.

*Call graph*: calls 2 internal fn (_bridge_warning_logs, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 226–238)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Connects normal Python logging to the OpenTelemetry log exporter for warning and error messages. This prevents important warnings from libraries or extension points from disappearing just because they did not use this file’s structured log helpers.

**Data flow**: It receives the OpenTelemetry logger provider that was just created. It builds a logging handler that only accepts warning-and-above records, filters out this project’s own structured logger and OpenTelemetry’s own exporter logs, and attaches the handler to the root Python logger.

**Call relations**: It is called by `init_o11y` after the log provider is ready. From then on, warning-level standard logging from the wider process can flow into the same exported log stream, while avoiding feedback loops from OpenTelemetry exporter failures.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_otlp_signal_urls`  (lines 241–247)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Builds the three exact HTTP endpoints used to send traces, metrics, and logs to an OTLP collector. This matters because the exporter does not add these paths automatically.

**Data flow**: It receives a base collector URL, removes any trailing slash, and returns three URLs ending in `v1/traces`, `v1/metrics`, and `v1/logs`. Nothing outside the return value is changed.

**Call relations**: It is used by `init_o11y` before creating the OpenTelemetry exporters. Its output tells each exporter where to send its specific kind of telemetry.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 250–255)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Finds the current workspace and turns it into metadata for logs and traces. This lets call sites avoid passing the workspace ID around by hand.

**Data flow**: It reads `current_workspace`, a context-bound value set elsewhere in the program. If a workspace is active, it returns a small dictionary containing its ID as text; if not, it returns an empty dictionary.

**Call relations**: `turn_span` uses this to label trace spans with the workspace, and `_emit_log` uses it to label structured logs. It is the shared doorway through which workspace context enters observability records.

*Call graph*: called by 2 (_emit_log, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 258–264)

```
def current_traceparent() -> str | None
```

**Purpose**: Captures the currently active trace context as a `traceparent` header. A trace context is the small piece of information that lets later work join the same trace instead of starting an unrelated one.

**Data flow**: It creates an empty carrier dictionary, asks the W3C trace-context propagator to inject the current trace information into it, and returns the `traceparent` value if one was produced. If there is no valid active trace, it returns `None`.

**Call relations**: Other parts of the system can call this when they queue or persist work, so a later `turn_span` can resume the same trace across that handoff. It does not call other project helpers in this file.


##### `turn_profile`  (lines 267–272)

```
def turn_profile(subagent_profile: str | None) -> str
```

**Purpose**: Chooses the stable profile label used for a turn. It returns the subagent profile when there is one, or `main` for normal member-facing work.

**Data flow**: It receives an optional profile name. If the name is present, it returns it; otherwise it returns the constant main profile label.

**Call relations**: `turn_span` calls this when adding trace attributes. The same idea is also used by metrics in this file so dashboards can separate main-agent behavior from subagent behavior without using unbounded IDs.

*Call graph*: called by 1 (turn_span).


##### `turn_span`  (lines 276–310)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None, subagent_profile: str | None, parent_turn_id: UUID | None) -> Iterator[Span]
```

**Purpose**: Creates a tracing span around one durable turn of work. A span is a timed section of a trace, like a labeled stopwatch that records what happened during that turn.

**Data flow**: It receives turn IDs, conversation ID, an optional incoming `traceparent`, an optional subagent profile, and an optional parent turn ID. It builds safe trace attributes, adds workspace information, redacts sensitive-looking data, extracts a parent trace context if one was supplied, then yields an active OpenTelemetry span named `turn` for the code inside the context block.

**Call relations**: Callers wrap turn execution in this context manager. Inside, logs emitted through `_emit_log` can correlate with the active span. It relies on `_ambient_scope` for workspace labels, `turn_profile` for the profile label, and `redact_payload` before giving attributes to OpenTelemetry.

*Call graph*: calls 3 internal fn (_ambient_scope, redact_payload, turn_profile); 2 external calls (get_tracer, cast).


##### `redact_payload`  (lines 313–319)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Removes sensitive fields from a dictionary before it is logged or attached to a trace. It protects values such as prompts, content, credentials, secrets, and tokens from being exported.

**Data flow**: It receives a mapping of field names to values. For each key, it normalizes the name by removing underscores and hyphens and lowercasing it; if that normalized key is sensitive, the field is skipped. All remaining values are passed through `redact_value`, and a JSON-safe dictionary comes out.

**Call relations**: `_emit_log` calls this before writing structured logs, `turn_span` calls it before creating trace attributes, and `redact_value` calls it again when it finds nested dictionaries. Together they form the safety filter for exported structured data.

*Call graph*: calls 1 internal fn (redact_value); called by 3 (_emit_log, redact_value, turn_span).


##### `redact_value`  (lines 322–332)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Converts one value into a safe JSON-like value while recursively redacting nested data. This keeps exported telemetry simple and avoids leaking sensitive nested fields.

**Data flow**: It receives any Python object. Plain JSON values such as strings, numbers, booleans, and `None` pass through. Dictionaries are converted to string-keyed dictionaries and sent to `redact_payload`; list-like values are processed item by item; other objects are turned into strings.

**Call relations**: `redact_payload` calls this for each non-sensitive field. If a value contains another dictionary, `redact_value` hands it back to `redact_payload`, so nested structures get the same redaction rules as top-level fields.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 335–340)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured informational event. It is for normal noteworthy events that should appear in logs and correlate with the current trace.

**Data flow**: It receives an event name and any extra fields. It passes them to `_emit_log` with information-level severity; `_emit_log` adds workspace context, redacts fields, and sends the result to logging and OpenTelemetry.

**Call relations**: Application code calls this directly for ordinary events. It is a small friendly wrapper around `_emit_log`, choosing the correct severity settings so callers do not repeat them.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 343–345)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured error event. It is used when something has gone wrong and operators should see it as an error.

**Data flow**: It receives an event name and extra fields, then forwards them to `_emit_log` with error severity. The shared emit path performs redaction, adds workspace context, and exports the record.

**Call relations**: Application code calls this for failures. Like `log` and `warn`, it funnels through `_emit_log` so all structured logs behave consistently.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 348–350)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured warning event. It is for expected but important conditions that are not full errors but still deserve attention.

**Data flow**: It receives an event name and extra fields, then sends them to `_emit_log` with warning severity. The common emit function adds context, redacts sensitive data, and publishes the event.

**Call relations**: Application code uses this when it wants a warning-level structured log. It shares the same `_emit_log` path as information and error logs.

*Call graph*: calls 1 internal fn (_emit_log).


##### `formatted_stack`  (lines 353–382)

```
def formatted_stack(error: BaseException) -> str
```

**Purpose**: Builds a safe stack trace string for an exception without including the exception message. This avoids exporting operator-controlled text that might contain secrets.

**Data flow**: It receives an exception. It walks through the exception and its cause or context chain, records each exception class name and traceback frames, avoids loops, and respects Python’s `raise ... from None` suppression. If the result is too long, it keeps the beginning and end and replaces the middle with an elision marker.

**Call relations**: This helper is meant for error logging fields elsewhere in the system. It uses Python’s traceback formatting, but deliberately omits exception messages so it works safely with the redaction approach used by this file.

*Call graph*: 1 external calls (format_tb).


##### `_emit_log`  (lines 385–399)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Performs the shared work behind `log`, `warn`, and `log_error`. It creates one redacted structured log record and sends it through both standard Python logging and OpenTelemetry logs.

**Data flow**: It receives the event text, severity details, a Python logging level, and extra fields. It adds the ambient workspace, redacts the combined fields, writes to the `ufo` standard logger with structured extras, and emits an OpenTelemetry log record with the same event body and attributes.

**Call relations**: `log`, `warn`, and `log_error` all call this so severity is the only difference between them. It depends on `_ambient_scope` for workspace context and `redact_payload` for safety before handing data to the logging systems.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `_bounded_error_class`  (lines 402–415)

```
def _bounded_error_class(dimensions: dict[str, str]) -> dict[str, str]
```

**Purpose**: Keeps the `error_class` metric label within a known set. This prevents one-off exception names from creating unlimited metric series, which can make monitoring noisy and expensive.

**Data flow**: It receives a dictionary of metric dimensions. If the `error_class` value is missing or is in the approved list, it returns the dimensions unchanged. If the class is not approved, it returns a copy where `error_class` is replaced with `other`.

**Call relations**: Both `emit_metric` and `emit_histogram` call this just before recording telemetry. By putting the check at this shared boundary, every metric emission gets the same protection.

*Call graph*: called by 2 (emit_histogram, emit_metric).


##### `emit_metric`  (lines 418–427)

```
def emit_metric(name: str, amount: int=1, /, **dimensions: str) -> None
```

**Purpose**: Increments one of the project’s approved counters. A counter is a metric that goes up, such as counting started turns or tool failures.

**Data flow**: It receives a metric name, an optional amount, and string dimensions. It first checks that the name is registered. It lazily creates and caches the OpenTelemetry counter if needed, bounds the `error_class` dimension, and then adds the amount with those attributes.

**Call relations**: Application code calls this when something countable happens. It uses `_bounded_error_class` before handing dimensions to OpenTelemetry and uses the OpenTelemetry meter to create the instrument the first time each metric name is seen.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_histogram`  (lines 430–452)

```
def emit_histogram(name: str, value: int, /, **dimensions: str) -> None
```

**Purpose**: Records one timing or size observation for an approved histogram. A histogram groups many observations so operators can ask questions like “how slow are the slowest model calls?”

**Data flow**: It receives a histogram name, a numeric value in milliseconds, and string dimensions. It checks that the histogram exists, rejects any dimension that was not declared for that histogram, lazily creates and caches the OpenTelemetry histogram if needed, bounds the `error_class` dimension, and records the value.

**Call relations**: Application code calls this after timed operations such as database acquisition, model rounds, tool calls, or full turns. It uses `_bounded_error_class` for safe labels and OpenTelemetry’s meter to create the histogram instrument when first needed.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).
