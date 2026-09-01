# External Source Connectors and Feed Sync  `stage-12.1`

This stage is the system’s intake hub for outside information. It runs mostly behind the scenes during sync work, after a user connects an account or provides an API key. Its job is to reach into many services, read their records, and turn them into the same kind of searchable pages.

The connector groups are the provider-specific “adapters.” They cover local Markdown folders and Git pages, Google and Microsoft workspaces, Slack, CRMs, sales and ads tools, project trackers, developer tools, HR systems, finance platforms, forms, scheduling, documents, and support desks. Each one knows that service’s API, which is its internet doorway for data, and translates provider-specific records into a common flow.

The shared source runtime is the engine under those adapters. The connector contract defines what every adapter must provide. The REST helper handles repeated web tasks like authentication, retries, and paged results. The backend runs connectors as sync jobs, tracks where to resume, and limits runaway fetches. The registry is the address book of known providers. The connected-account logic creates private feeds when accounts are linked, while the direct-auth path supports connectors that use user-provided API keys.

## Sub-stages

- [gbrain Markdown, Folder, and Git Page Sources](stage-12.1.1.md) `stage-12.1.1` — 3 files
- [Google, Microsoft, and Chat Workspace Connectors](stage-12.1.2.md) `stage-12.1.2` — 10 files
- [CRM, Sales, Marketing, and Advertising Connectors](stage-12.1.3.md) `stage-12.1.3` — 10 files
- [Project, Product, Developer, and Operations Connectors](stage-12.1.4.md) `stage-12.1.4` — 11 files
- [HR, Recruiting, and Workforce Connectors](stage-12.1.5.md) `stage-12.1.5` — 6 files
- [Finance, Billing, Banking, and Commerce Connectors](stage-12.1.6.md) `stage-12.1.6` — 9 files
- [Structured Apps, Documents, Forms, Scheduling, and Support Connectors](stage-12.1.7.md) `stage-12.1.7` — 8 files

## Files in this stage

### Account Feed Provisioning
Handles account-connection triggers that create or retry private feed rows for newly connected external sources.

### `extensions/sources/ufo_ext_sources/connected.py`

`domain_logic` · `connection hook and retry job`

When someone connects an outside account, the system has proof that the account is allowed, but it still needs actual feed records saying which streams of data to read. This file closes that gap. Think of the connection as getting a library card, and the source rows as choosing the main shelves you want delivered. For each connected account, it creates one source row for each “canonical” stream, meaning the connector’s core streams rather than every possible list the outside service exposes.

There are two ways this happens. The hook path runs immediately after a connection is recorded, so the member does not have to take a second action. The retry path runs later and fills in anything that was missed because a process failed or a hook did not finish.

The code is careful not to overwrite the member’s choices. If a matching source already exists, it leaves it alone. If the member previously removed that source, it does not recreate it, because that would undo the removal. It also refuses to auto-create sources for connectors that need a tenant-specific URL, because only the member knows that URL. New source rows are private to the member who owns the connection, and they are tied to the existing connection rather than creating a separate binding.

#### Function details

##### `on_connection_recorded`  (lines 51–58)

```
async def on_connection_recorded(ctx: HookContext) -> HookOutcome
```

**Purpose**: This is the immediate hook that runs when a connection has just been recorded. Its job is to create the account’s default feed rows before the connection flow finishes answering the member.

**Data flow**: It receives a hook context containing an event payload. If the payload says a connection was recorded, it takes the connection id, builds a ConnectedSources helper using the extension context, and asks it to register sources for that one connection. It returns no special outcome. If the hook was called with the wrong kind of payload, it raises an error instead of silently doing the wrong thing.

**Call relations**: This function is the fast path. The connection system calls it right after saving a new connection. It hands the real work to ConnectedSources, which knows how to inspect connectors and create the needed source rows.

*Call graph*: 1 external calls (__init__).


##### `retry_connected_sources`  (lines 61–63)

```
async def retry_connected_sources(ctx: ExtensionContext) -> None
```

**Purpose**: This is the safety-net job for source rows that should exist but were not created at connection time. It lets the system recover from interrupted hooks or other missed creation paths.

**Data flow**: It receives the extension context, builds a ConnectedSources helper from it, and asks that helper to register missing sources across eligible main-agent connections. It does not return data; its effect is that missing source rows may be added.

**Call relations**: This function is the slower retry path. A scheduler or background job calls it later, and it delegates to the same ConnectedSources logic used by the connection hook so both paths follow the same rules.

*Call graph*: 1 external calls (__init__).


##### `ConnectedSources.register`  (lines 74–82)

```
async def register(self, connection_id: UUID | None=None) -> None
```

**Purpose**: This method finds connected accounts that should have automatic source rows and sends each eligible one through the detailed registration process. It can work on one specific connection or on all current main-agent connections.

**Data flow**: It first reads the currently live source records. Then it asks for the connections held by the main agent, which is the system actor allowed to use connected accounts. If a specific connection id was supplied, it skips all others. For each remaining connection, it looks up the matching connector type, skips unknown connectors and connectors that lack a usable base URL, and then passes the connection, connector instance, and live source list to the lower-level registration method.

**Call relations**: The hook calls this with one connection id so it only touches the account just connected. The retry job calls it without an id so it can scan all main-agent connections. For each suitable connection, it hands off to ConnectedSources._register, which decides exactly which stream rows to create.

*Call graph*: calls 1 internal fn (_register); 2 external calls (main_agent_connections, get).


##### `ConnectedSources._register`  (lines 84–129)

```
async def _register(self, connection: MainAgentConnection, connector: Connector, live: tuple[SourceRecord, ...]) -> None
```

**Purpose**: This method creates the missing source rows for one connected account, one canonical stream at a time. It protects existing rows, shared rows, and previously removed rows from being changed or recreated.

**Data flow**: It starts with one connection, one connector, and the list of currently live sources. It turns the connection owner into the member subject used for private ownership, then finds source records already bound to this connection. If any bound row belongs to a different subject, it stops, because the connection is already tied into a sharing shape this automatic path should not alter. If there is an existing bound row, it reads its requested backfill period, meaning how far back the first sync should look. Then it walks the connector’s streams, keeps only canonical streams, builds a source configuration for each one, calculates the deterministic source id, and remembers only the ones that are not already live. Before creating them, it asks which of those source ids were previously removed. It finally registers only the fresh, non-removed sources, making them private to the connection owner and tied to the same connection.

**Call relations**: ConnectedSources.register calls this after it has chosen an eligible connection and connector. This method uses the connector’s stream list, source configuration validation, member-subject creation, and backfill-day calculation to turn the account’s canonical streams into concrete source rows. It then calls the extension’s source registration path, which is the part that actually records the new feeds.

*Call graph*: calls 1 internal fn (streams); called by 1 (register); 6 external calls (__init__, model_validate, now, timedelta, member_subject, effective_days).


### Connector Runtime Framework
Defines the shared source connector package, connector contract, HTTP REST helpers, and backend sync orchestration.

### `core/src/ufo/runtime/sources/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes the `core/src/ufo/runtime/sources` directory available as `ufo.runtime.sources` in import statements.

There is no code to run, no settings to load, and no objects defined here. Its value is structural: it helps organize source-related runtime code under a clear namespace. Without this file, depending on the Python version and packaging setup, imports that expect `ufo.runtime.sources` to be a regular package could fail or behave differently.

An everyday analogy is a labeled folder in a filing cabinet. The folder may be empty at first, but the label still matters because it gives everything placed inside a known address.


### `core/src/ufo/runtime/sources/connector.py`

`domain_logic` · `source sync runs`

This file is the shared vocabulary for syncing data from outside services. A connector is the project’s adapter for a provider such as mail, chat, documents, or code hosting. Without this file, each provider would invent its own way to describe streams, identify records, paginate through results, save progress, and render records into searchable page text.

The simple data classes describe what a stream is, how paging works, what counts as a record’s stable provider ID, and how deletions or resume cursors are carried forward. A cursor is a saved bookmark that lets the next sync continue from the right place instead of starting over.

The `Connector` base class is the promise every connector must keep: say which streams it supports, fetch pages of records, and render one record into a title and body. Most connectors can use the default rendering, which makes a readable page containing the record’s JSON data.

The more complex part is `PartitionWalk`. Some streams are not one long list; they are many smaller lists, like “all messages per channel” or “all issues per repository.” `PartitionWalk` is the careful librarian that visits each shelf, remembers where it stopped on each one, and avoids losing records when a run is interrupted or capped. It supports oldest-first streams, newest-first feeds, and streams with no useful ordering.

#### Function details

##### `get_path`  (lines 36–45)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a value from a nested dictionary using a dotted path such as `author.id`. This lets stream definitions point to IDs or dates that are tucked inside provider records, not only fields at the top level.

**Data flow**: It receives a mapping, a dotted path, and an optional fallback value. It walks through the mapping one part at a time; if a part is missing, is `None`, or the current value is no longer a mapping, it returns the fallback. If every part is found, it returns the final value.

**Call relations**: This is a small helper used by `record_key` when a record’s declared primary key is not present as a direct field. It gives connector definitions a flexible way to name nested provider fields.

*Call graph*: called by 1 (record_key).


##### `record_key`  (lines 48–60)

```
def record_key(record: Mapping[str, Any], primary_key: str) -> str | None
```

**Purpose**: Finds the stable provider identity for a record. This identity is what lets the system recognize that the same outside record has appeared again in a later sync.

**Data flow**: It receives a record and the name of the primary key field. It first looks for that key directly on the record, then uses `get_path` to try a nested dotted path. If the found value is a string or integer, it converts it to a non-empty string; booleans, missing values, and other shapes are rejected by returning `None`.

**Call relations**: `Connector.record_identity` calls this when the default connector behavior needs a provider-stable ID. That ID is later used by rendering and page creation logic to keep records tied to the same source object over time.

*Call graph*: calls 1 internal fn (get_path); called by 1 (record_identity).


##### `PartitionWalk.stream`  (lines 261–289)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Runs a full partitioned sync pass and yields pages with updated cursors along the way. It is the public entry point for walking many partitions while preserving a separate bookmark for each one.

**Data flow**: It receives the saved cursor string from a previous run. It decodes that into a per-partition map, asks the connector for the current partitions, then streams each partition using either ordered or unordered logic. As pages come back, it yields `StreamPage` objects containing records, deletions, and a newly encoded cursor. At the end, it also prunes cursor entries for partitions that no longer exist, when ordering allows that.

**Call relations**: A connector that has partitioned data uses this method as the driver for its stream. `stream` delegates each partition to `_stream_ordered` or `_stream_unordered`, uses `_decode` at the start to understand saved state, and uses `_encode` when it needs to publish a new saved state.

*Call graph*: calls 4 internal fn (_decode, _encode, _stream_ordered, _stream_unordered); 1 external calls (__init__).


##### `PartitionWalk._stream_unordered`  (lines 291–313)

```
async def _stream_unordered(self, partition: str, stored: dict[str, str | _Window], checkpoint: dict[str, str | _Window]) -> AsyncIterator[StreamPage]
```

**Purpose**: Streams one partition when records do not have a useful date or cursor field. Its main job is to avoid repeating a partition within the same interrupted pass, while still allowing a full re-walk on a later completed pass.

**Data flow**: It receives a partition name, the saved state from the start of the run, and the mutable checkpoint being built. If the partition was already marked as finished in this pass, it returns without yielding anything. Otherwise it asks the page factory for pages with an empty `PartitionBound`, yields each page with the current encoded checkpoint, then marks the partition as done and yields an empty page carrying the updated cursor.

**Call relations**: `PartitionWalk.stream` calls this only when the stream ordering is `none`. It hands off to the connector-provided page factory for the actual provider requests, and it catches `PartitionSkipped` so one unavailable partition does not stop the whole walk.

*Call graph*: calls 1 internal fn (_encode); called by 1 (stream); 2 external calls (__init__, __init__).


##### `PartitionWalk._stream_ordered`  (lines 315–361)

```
async def _stream_ordered(self, partition: str, stored: dict[str, str | _Window], checkpoint: dict[str, str | _Window]) -> AsyncIterator[StreamPage]
```

**Purpose**: Streams one partition when records have an ordering field that can be used as a bookmark. It updates that bookmark carefully so interrupted runs can resume without skipping records.

**Data flow**: It starts by turning the stored partition state into a fetch boundary through `_ordered_state`. It then asks the page factory for bounded pages. For each page, it updates the highest seen value, and during newest-first backfills also tracks the lowest value reached. It yields records with an encoded checkpoint after each page. If a backfill reaches its floor, or if a steady-state newest-first page is older than the synced watermark, it stops at the safe point. When a backfill finishes, it collapses the temporary window into a normal watermark.

**Call relations**: `PartitionWalk.stream` uses this for ascending and newest-first streams. It cooperates with `_ordered_state` to decide how to resume, `_encode` to publish progress, and the connector’s page factory to fetch provider data inside the chosen bounds.

*Call graph*: calls 2 internal fn (_encode, _ordered_state); called by 1 (stream); 2 external calls (__init__, __init__).


##### `PartitionWalk._ordered_state`  (lines 363–379)

```
def _ordered_state(self, stored: str | _Window | None) -> tuple[PartitionBound, str | None, str | None, str | None, bool]
```

**Purpose**: Translates one partition’s saved cursor entry into the next request boundary. It decides whether the partition is resuming a backfill, doing a normal incremental pass, or starting fresh.

**Data flow**: It receives the stored state for one partition, which may be a simple watermark, a temporary backfill window, or nothing. A backfill window becomes a `PartitionBound` with an inclusive `before` value. A simple watermark becomes a bound with `after`. No stored state starts either a newest-first backfill, possibly with a floor, or an unbounded first pass for other ordered streams. It returns the bound plus bookkeeping values needed by `_stream_ordered`.

**Call relations**: `_stream_ordered` calls this before fetching any pages. The result tells the connector-provided page factory what slice of the provider’s data to request next.

*Call graph*: called by 1 (_stream_ordered); 1 external calls (__init__).


##### `PartitionWalk._decode`  (lines 382–411)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: Reads the saved partition cursor string back into a safe in-memory map. It treats unknown older cursor shapes as empty, but rejects malformed partition maps that this walker itself would have produced.

**Data flow**: It receives a cursor string or `None`. Empty, invalid JSON, or JSON that is not an object becomes an empty map. For each object entry, string values become simple watermarks, and dictionary values are validated as backfill windows with `high` and `until`. Bad entries raise an error instead of being silently ignored.

**Call relations**: `PartitionWalk.stream` calls this once at the start of a partitioned walk. The decoded map is the memory of where each partition stopped during previous runs.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 414–419)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: Turns the per-partition checkpoint map into the JSON string that can be saved as the stream cursor. This is how progress survives between sync runs.

**Data flow**: It receives a mapping from partition names to either simple watermark strings or `_Window` objects. It converts windows into plain dictionaries, leaves strings as strings, and serializes the whole map to JSON with stable key ordering. The result is a cursor string.

**Call relations**: `PartitionWalk.stream`, `_stream_unordered`, and `_stream_ordered` call this whenever they need to attach updated progress to a yielded `StreamPage`.

*Call graph*: called by 3 (_stream_ordered, _stream_unordered, stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 438–439)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Declares which streams a connector can sync. A stream is a named collection from the provider, such as messages, documents, users, or repositories.

**Data flow**: A concrete connector implements this method with no input beyond itself. It returns a list of `StreamSpec` objects that describe each stream’s name, primary key, cursor field, deletion behavior, pagination style, and related sync settings.

**Call relations**: The connector registration flow calls this when adding connected sources, so the rest of the system knows what this provider can offer. Because this base method is abstract, each real connector must supply the provider-specific list.

*Call graph*: called by 1 (_register).


##### `Connector.fetch_page`  (lines 442–457)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None) -> AsyncIterator[list[dict[str, Any]]
```

**Purpose**: Fetches records for one stream from an external provider, page by page. This is the method real connectors implement to make API calls or otherwise read source data.

**Data flow**: It receives the stream definition, the previous cursor, resolved credentials, the base URL, an optional current-user ID to exclude, and an optional backfill floor. A concrete implementation uses those inputs to request provider data and asynchronously yields either plain lists of records or richer `StreamPage` objects containing records, deletion IDs, and a next cursor.

**Call relations**: The sync runner calls this through concrete connector classes while syncing a stream. This base method is abstract, so it defines the shape of the handoff but leaves provider-specific fetching to implementations.


##### `Connector.render`  (lines 459–479)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns one provider record into the title and body text that the system can store and later recall. It provides a safe default for connectors that do not need custom prose rendering.

**Data flow**: It receives a record and its stream definition. It looks for a human-friendly title field such as `title`, `name`, or `subject`. If none exists, it asks `record_identity` and `record_ref` for a stable fallback name. It returns a pair: the chosen title, and a body containing a heading plus the record serialized as sorted JSON. If no title or usable identity exists, it raises an error.

**Call relations**: The sync adapter uses this after records are fetched to create recallable page content. Connectors for content-heavy providers can override it, but the default relies on `record_identity`, `record_ref`, and JSON serialization to make generic records readable enough.

*Call graph*: calls 2 internal fn (record_identity, record_ref); 1 external calls (dumps).


##### `Connector.record_identity`  (lines 481–483)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Returns the stable provider identity for a record within its stream. This is the identity used to recognize the same outside object across sync runs.

**Data flow**: It receives a record and stream definition. It passes the record and the stream’s primary key to `record_key`, then returns the resulting string ID or `None` if the record does not contain a usable identity.

**Call relations**: `Connector.render` calls this when it needs a fallback title for a record without a title-like field. The actual extraction work is delegated to `record_key`.

*Call graph*: calls 1 internal fn (record_key); called by 1 (render).


##### `Connector.record_ref`  (lines 485–492)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Builds the source-side reference used when creating a page row for a record. It prefers the provider’s declared primary key, but can fall back to a content hash when the direct key is not a simple value.

**Data flow**: It receives a record and stream definition. If the primary key field is a string or integer, it returns it as text. If it is a boolean, it returns `None`. For other shapes or missing direct keys, it serializes the whole record in a stable order and hashes it with SHA-256, producing a long deterministic fingerprint.

**Call relations**: `Connector.render` calls this when it needs a fallback title and has already found an identity. The reference helps form a readable stream/name label for records that lack their own title.

*Call graph*: called by 1 (render); 2 external calls (sha256, dumps).


### `core/src/ufo/runtime/sources/rest.py`

`io_transport` · `source data fetching`

Many outside services expose data through REST APIs, which are web endpoints that return data, usually as JSON. Each service has its own quirks, but many chores are the same: add authentication, send GET or read-only POST requests, wait and retry when the service is busy, and keep asking for the next page until all records are fetched. This file is the reusable toolkit for that work.

A concrete connector, such as one for GitHub or Microsoft Graph, subclasses `RestConnector` and declares which streams it can read. A stream is a named collection of records, like users, tickets, or projects. If the stream uses a common pagination style, the subclass can describe it with a `PaginationStrategy`. If the service has a stranger shape, the subclass can override the pagination method while still using this file’s safe request helpers.

The file also protects the rest of the sync run. It refuses unauthenticated requests, retries only errors that are likely temporary, respects usable `Retry-After` headers, and stops pagination loops that appear stuck. That last part is like refusing to follow a “next page” sign that keeps pointing back to the same page forever.

#### Function details

##### `list_or_empty`  (lines 50–54)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: This helper safely turns a value into a list of record dictionaries. If the value is not a list, or if some list items are not dictionary-shaped records, it filters them out instead of letting bad shapes spread further.

**Data flow**: It receives any value. If that value is a list, it keeps only the items that are dictionaries; otherwise it returns an empty list. The result is always a list that later code can treat as records.

**Call relations**: Pagination helpers use this when reading API responses whose record area may or may not be in the expected shape. It is the small safety gate used by `records_at`, `_response_list`, and the OData pager before pages are yielded onward.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 57–60)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This helper safely treats a value as a single record-like object. It is useful when connector code reaches into a nested API response and wants a dictionary or a harmless empty fallback.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary. Nothing else is changed.

**Call relations**: This file defines it as a sibling to `list_or_empty` for connector implementations that need safe record shaping, although the listed call graph does not show an internal caller in this file.


##### `records_at`  (lines 63–68)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: This helper extracts a list of records from an API response, optionally from a named nested path. It lets pagination code say, for example, “the records are under `data.items`” without repeating shape checks.

**Data flow**: It receives a response-like value and an optional path. With no path, it treats the value itself as the record list. With a path, it first checks that the response is dictionary-like, follows the path, and then returns only dictionary records from that location.

**Call relations**: The cursor, offset, page-number, and link-header parsing paths call this whenever a stream says where records live inside a response body. It relies on the shared path lookup helper, then hands clean record lists back to the pagination loops.

*Call graph*: calls 1 internal fn (list_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, parse); 1 external calls (get_path).


##### `_int_or_none`  (lines 71–76)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: This helper reads an integer from either an actual integer or a string made only of digits. It avoids guessing on messy values.

**Data flow**: It receives any value. If the value is already an integer, it returns it. If it is a decimal string like `"50"`, it converts it to `50`; otherwise it returns `None`.

**Call relations**: The offset-based pager uses this when an API reports the page size it actually used. That helps the pager advance the next offset correctly when the server does not use the requested limit.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 79–82)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: This helper copies records and stamps extra identifying information onto each one. It is used when records are fetched from a partition or parent object and need to remember where they came from.

**Data flow**: It receives many record dictionaries plus named context values, such as a site ID or parent ID. It creates new dictionaries containing the original record fields plus the context fields. The output is a new list; the original records are not edited in place.

**Call relations**: This is a convenience for connector-specific fan-out flows. Although no internal caller is shown here, subclasses can use it before yielding records so later rendering or downstream fetches know each record’s origin.


##### `next_link`  (lines 85–90)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper finds the URL for the next page in an HTTP `Link` header. Some APIs put pagination directions in response headers rather than in the JSON body.

**Data flow**: It receives response headers. It looks for a `link` header containing a `rel=next` entry. If found, it returns the URL inside angle brackets; otherwise it returns `None`.

**Call relations**: `_get_link_header_pages` calls this after each response. If it returns a URL, the pager follows it; if it returns nothing, pagination ends.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 93–98)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: This helper decides whether a failed request is worth trying again. It treats network-level failures and common temporary server or rate-limit statuses as retryable.

**Data flow**: It receives an exception. If the exception is a transport problem, or an HTTP error with a status such as 429, 500, 502, 503, or 504, it returns `true`; otherwise it returns `false`.

**Call relations**: `_send` asks this after a request fails. The answer controls whether the connector waits and tries again or gives the error back to the caller immediately.

*Call graph*: called by 1 (_send).


##### `_retry_after`  (lines 101–121)

```
def _retry_after(error: BaseException) -> float | None
```

**Purpose**: This helper reads an API’s requested wait time from a `Retry-After` header when that header is safe to use. It is careful not to accept invalid, negative, infinite, or not-a-number waits.

**Data flow**: It receives an exception. If the exception is an HTTP error with a status that can reasonably include a retry schedule, it reads the `retry-after` header and converts it to seconds. It returns a usable non-negative number or `None`.

**Call relations**: `_retry_wait` calls this before choosing a retry delay. This lets the retry system honor a service’s “come back later” instruction when it is sensible.

*Call graph*: called by 1 (_retry_wait); 1 external calls (isfinite).


##### `_retry_wait`  (lines 124–141)

```
def _retry_wait(error: BaseException, delay: float) -> float
```

**Purpose**: This helper chooses how long to sleep before the next retry. It combines the connector’s growing backoff delay with any usable `Retry-After` value from the server, then adds jitter so many workers do not retry at the exact same moment.

**Data flow**: It receives the error that happened and the current base delay. It checks for a server-provided retry delay, clamps waits to configured maximums, chooses the stronger floor, and returns a random wait within a safe range.

**Call relations**: `_send` calls this whenever a retryable request fails. The returned delay is then passed to the event loop sleep before the next attempt.

*Call graph*: calls 1 internal fn (_retry_after); called by 1 (_send); 1 external calls (uniform).


##### `_raise_for_status`  (lines 144–155)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: This helper turns unsuccessful HTTP responses into useful errors. Unlike the default behavior, it includes a capped copy of the response body, which often contains the real reason an API rejected the request.

**Data flow**: It receives an HTTP response. If the response is successful, it does nothing. If not, it builds an error message with the status, request, URL, and a shortened body, then raises an HTTP status exception.

**Call relations**: `_send` calls this after every HTTP response. Successful responses move on; unsuccessful responses become exceptions that the retry logic can inspect or report.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 158–162)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: This helper reads a JSON object from a response, while treating an empty response as an empty object. It is for endpoints where the expected result is dictionary-shaped JSON.

**Data flow**: It receives an HTTP response. If the response has no content or is a 204 “no content” response, it returns `{}`. Otherwise it parses and returns the JSON body as a dictionary.

**Call relations**: `_get` and `_post` call this after their retried HTTP request succeeds. It lets callers work with a plain dictionary instead of raw HTTP details.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 165–168)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: This helper reads a top-level JSON list of records from a response. If the response is empty or not a list of dictionaries, it gives back an empty list or filters out non-record items.

**Data flow**: It receives an HTTP response. Empty responses become `[]`; otherwise the JSON body is parsed and passed through `list_or_empty`. The output is always a list of dictionary records.

**Call relations**: The link-header pagination helper uses this by default when no custom record parser is supplied. It turns each fetched response into records that can be yielded.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 171–177)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: This safety check stops a pagination loop that keeps going for too long. Without it, a broken API could make a sync job run forever.

**Data flow**: It receives a label describing the pager and the number of pages fetched so far. If the count is above the configured maximum, it raises an error; otherwise it lets the loop continue.

**Call relations**: All built-in pagination loops call this once per page. It acts like a circuit breaker before the connector spends unlimited time fetching pages.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 180–186)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: This safety check stops cursor-based pagination when the API repeats a next-page token or URL. A repeated cursor means the provider is not advancing and would fetch the same page again.

**Data flow**: It receives a label, the next cursor or next-link token, and a set of tokens already seen. If the token was seen before, it raises an error. If not, it records the token and allows the loop to continue.

**Call relations**: The cursor, link-header, and OData pagers call this before following a continuation. It catches stuck pagination earlier and more clearly than the general page limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 195–196)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This method returns the list of streams that this connector knows how to read. A stream is a named collection of records from the outside service.

**Data flow**: It reads the connector class’s `streams_list` and returns a new list copy. The copy prevents callers from accidentally editing the class-level list directly.

**Call relations**: The wider sync system asks connectors for their streams before deciding what to fetch. This method provides that inventory for REST-based connectors.


##### `RestConnector._make_client`  (lines 198–215)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method creates the authenticated asynchronous HTTP client used for one account. Asynchronous means it can wait on the network without blocking other work in the same event loop.

**Data flow**: It receives a base URL and a resolved credential. It sets timeouts and JSON headers, then chooses one authentication path: proxy transport, bearer token, or custom headers. It returns an `httpx.AsyncClient`, or raises an error if no usable authentication is present.

**Call relations**: `fetch_page` calls this at the start of a fetch. All later GET and POST helpers use the returned client, so authentication and timeouts are consistent across requests.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 217–220)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This method performs a retried HTTP GET and returns the response body as a dictionary. It is the common helper for read endpoints that return object-shaped JSON.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It asks `_get_raw` to send the request with retry behavior, then converts the successful response through `_json_or_empty`. The result is a dictionary.

**Call relations**: Several pagination loops call this when they only need the JSON body. It delegates the network work to `_get_raw`, keeping retry rules in one place.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 3 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages).


##### `RestConnector._get_raw`  (lines 222–226)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: This method performs a retried HTTP GET and returns the full raw response. It is used when pagination needs headers or other response details, not just the JSON body.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It builds a GET request function and passes it into `_send`. The output is the successful HTTP response object.

**Call relations**: `_get` uses this for ordinary JSON GETs, while link-header and OData pagination use it directly because they need headers or continuation URLs from the raw response.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 228–233)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This method performs a retried read-only POST and returns a dictionary body. Some APIs use POST for search or query endpoints even when no data is being modified.

**Data flow**: It receives an HTTP client, a path, and an optional JSON request body. It sends the POST through `_send`, then converts the successful response through `_json_or_empty`. The result is a dictionary.

**Call relations**: Connector subclasses can call this for provider-specific read endpoints such as search APIs. It shares the same retry envelope as GET requests.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 235–241)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: This method performs a retried read-only POST and returns the raw response. It is useful for APIs whose POST response is not a normal JSON object, such as a top-level array.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It passes a POST request function to `_send` and returns the successful raw response.

**Call relations**: Connector subclasses can use this when they need to parse the response themselves. Like `_post`, it keeps all retry behavior centralized in `_send`.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 243–264)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: This is the shared retry wrapper for outgoing HTTP requests. It protects sync jobs from temporary network failures, rate limits, and short-lived server problems.

**Data flow**: It receives a no-argument function that will send one HTTP request. It tries the request, turns bad HTTP statuses into exceptions, and returns the response on success. On retryable failures, it waits using the retry-delay rules, up to both an attempt limit and a total waiting budget; otherwise it raises the error.

**Call relations**: `_get_raw`, `_post`, and `_post_raw` all route through this method. It calls the status checker, retryability checker, and wait calculator so every request follows the same safety rules.

*Call graph*: calls 3 internal fn (_is_retryable, _raise_for_status, _retry_wait); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 266–303)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[
```

**Purpose**: This is the main entry point for fetching pages from a REST stream. It creates the HTTP client, runs the connector’s pagination, validates each page, flattens records if needed, and yields clean pages to the sync driver.

**Data flow**: It receives the stream to read, cursor and credential details, a base URL, the acting user ID, and an optional backfill date. It opens an authenticated client, asks `paginate_source` for pages, skips empty pages, checks that records are dictionaries, applies `flatten`, and yields either plain record lists or `StreamPage` objects with deletes and next cursors preserved. When done or interrupted, it closes an async generator if one was opened.

**Call relations**: The wider source sync flow calls this to get data. Inside, it connects setup (`_make_client`), provider pagination (`paginate_source`), validation (`_validate_page`), and final shaping (`flatten`) into one safe read pipeline.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate_source); 1 external calls (__init__).


##### `RestConnector.paginate_source`  (lines 305–318)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This method is the extension seam between the generic fetch flow and a connector’s pagination logic. By default it ignores extra run context and calls the simpler `paginate` method.

**Data flow**: It receives the HTTP client, stream, cursor, acting user ID, and optional backfill floor. The default implementation passes only the client, stream, and cursor into `paginate` and returns that async iterator.

**Call relations**: `fetch_page` calls this rather than calling `paginate` directly. Connectors that need the acting user or backfill date can override this one method without rewriting the rest of the fetch pipeline.

*Call graph*: calls 1 internal fn (paginate); called by 1 (fetch_page).


##### `RestConnector.paginate`  (lines 320–332)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This method produces raw pages of records for a stream. The default implementation works only when the stream declares one of the built-in pagination strategies.

**Data flow**: It receives the HTTP client, stream, and optional cursor. It checks the stream’s pagination description. If no usable strategy is declared, it raises a clear “not implemented” error; otherwise it yields pages produced by `paginate_from_strategy`.

**Call relations**: `paginate_source` calls this in the default flow. Subclasses override it when a provider’s API shape is too custom for the declarative strategies.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (paginate_source).


##### `RestConnector.paginate_from_strategy`  (lines 334–396)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method runs one of the built-in pagination patterns described on a stream. It is the dispatcher that turns a declarative pagination setting into actual page-fetching requests.

**Data flow**: It receives a stream and HTTP client. It reads the stream’s pagination settings, resolves the request path, validates that required fields are present, and then delegates to the matching pager: cursor token, link header, or offset/limit. It yields lists of record dictionaries from that pager.

**Call relations**: `paginate` uses this for streams that can describe their pagination without custom code. It hands off to `_get_cursor_pages`, `_get_link_header_pages`, or `_get_offset_pages` depending on the chosen strategy.

*Call graph*: calls 4 internal fn (_get_cursor_pages, _get_link_header_pages, _get_offset_pages, _strategy_path); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 366–368)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: This small inner parser extracts records from a link-header-paginated response when the records are nested inside the response body. It exists so link-header pagination can support both top-level arrays and enveloped JSON.

**Data flow**: It receives a raw HTTP response. It parses the body as JSON when present, then uses `records_at` to pull records from the configured record path. It returns a list of dictionary records.

**Call relations**: `paginate_from_strategy` creates this parser only for link-header pagination with a configured record path. `_get_link_header_pages` then calls it for each response instead of using its default top-level-list parser.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 398–426)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: This method fetches pages from APIs that put the next-page URL in the HTTP `Link` header. This is common in web APIs where the body contains data and the headers contain navigation.

**Data flow**: It receives an HTTP client, starting path, optional query parameters, optional page-size settings, and an optional record parser. It fetches the first page, yields records if present, reads the next link from headers, checks that the link is not repeating, and keeps following links until there is no next link.

**Call relations**: `paginate_from_strategy` calls this for the `next_link` strategy. It uses `_get_raw` because headers matter, `next_link` to find the continuation, and the page/cursor bounds to prevent endless loops.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 428–460)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: This method fetches pages from APIs that return a next-page cursor token in the JSON body. A cursor is a bookmark that tells the API where to continue.

**Data flow**: It receives an HTTP client, path, record path, cursor path, query parameter names, page size settings, and extra parameters. It repeatedly builds a query, adds the latest cursor when present, fetches JSON, extracts records, yields them, reads the next cursor, and stops when no valid cursor remains.

**Call relations**: `paginate_from_strategy` calls this for the `next_cursor` strategy. It uses `_get` for retried JSON requests, `records_at` and path lookup to read the body, and cursor/page guards to catch broken pagination.

*Call graph*: calls 4 internal fn (_get, _bound_cursor, _bound_pages, records_at); called by 1 (paginate_from_strategy); 1 external calls (get_path).


##### `RestConnector._get_odata_pages`  (lines 462–488)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method fetches Microsoft Graph or OData-style pages. In this style, records are usually under `value`, and the next page is named by an `@odata.nextLink` URL.

**Data flow**: It receives an HTTP client, starting path, and optional parameters. It fetches the current URL, reads dictionary records from the `value` field, yields them, then follows `@odata.nextLink` if present. Only the first request uses the original parameters because the next link already contains continuation details.

**Call relations**: This helper is available to provider-specific connector code that needs OData pagination. It shares the raw GET helper and the same page and cursor safety checks used by the built-in strategies.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 490–530)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: This method fetches pages from APIs that use an offset and a limit. The offset says how many records to skip; the limit says how many to ask for next.

**Data flow**: It receives an HTTP client, path, record path, limit, parameter names, optional extra parameters, and optional response paths that describe continuation. It sends requests with the current offset, yields records, decides whether another page exists, and advances the offset by either the requested limit or the server-reported page size.

**Call relations**: `paginate_from_strategy` calls this for the `offset_limit` strategy. It uses `_get` to fetch JSON, `records_at` to extract records, `_int_or_none` when the server reports a limit, and the page bound to avoid infinite loops.

*Call graph*: calls 4 internal fn (_get, _bound_pages, _int_or_none, records_at); called by 1 (paginate_from_strategy); 1 external calls (get_path).


##### `RestConnector._get_page_number_pages`  (lines 532–561)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: This method fetches pages from APIs that use page numbers such as page 1, page 2, and so on. It stops when a page comes back shorter than the requested page size.

**Data flow**: It receives an HTTP client, path, record path, page size, optional parameters, parameter names, and a starting page. It repeatedly requests the current page, yields any records, stops on a short page, or increments the page number and continues.

**Call relations**: This helper is available for connector-specific pagination code. It uses the same retried GET helper, record extraction helper, and page-limit guard as the other pagers, even though it is not one of the shown declarative strategy targets here.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at).


##### `RestConnector._strategy_path`  (lines 563–569)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: This method resolves the request path for streams whose pagination description does not include one. The base version deliberately fails so subclasses must explain how to find the path.

**Data flow**: It receives a stream. Instead of guessing, it raises a clear error saying that the pagination path is missing and no override exists.

**Call relations**: `paginate_from_strategy` calls this when a pagination strategy has no explicit path. Connectors with their own per-stream path table can override it to supply the missing route.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 571–574)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method converts one fetched record into the flat dictionary shape the sync writer expects. The default does nothing because many APIs already return records in a usable shape.

**Data flow**: It receives a record dictionary and the stream it belongs to. It returns the record unchanged. Subclasses can override it to lift nested fields or remove an envelope around the real data.

**Call relations**: `fetch_page` calls this on every validated record just before yielding the page. That makes record shaping a final, consistent step after pagination.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 576–587)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: This method checks that pagination produced the kind of data the sync pipeline expects: a list of dictionary records. It catches connector mistakes early with clear errors.

**Data flow**: It receives a page-like value and the stream being fetched. If the page is not a list, it raises a type error. If any item in the list is not a dictionary, it raises a type error naming the bad item type. If all is well, it returns nothing.

**Call relations**: `fetch_page` calls this before flattening or yielding records. It is the quality gate between provider-specific pagination code and the shared sync writer.

*Call graph*: called by 1 (fetch_page).


### `core/src/ufo/runtime/sources/backend.py`

`orchestration` · `during each source sync run`

Connectors know how to talk to outside services such as GitHub, Zendesk, or Freshdesk. The rest of the source-sync system expects one neat result per run: pages to save, records to delete, a cursor showing where to continue next time, and a count of records that had to be dropped. This file is the adapter between those two worlds.

The main class, ConnectorBackend, runs one connector stream for one account. First it asks the authentication proxy for a Credential, so secrets stay behind the broker or credential store instead of being exposed to callers. Then it finds the requested stream, chooses the right base URL, and asks the connector to fetch pages of records.

Each provider record is converted into a Page, which is the system’s recallable unit of source content. If a record has no stable key, or cannot fit the Page shape, it is skipped with a warning instead of stopping the whole sync.

The file also decides how far a run may go. Incremental streams are capped at a safe number of records, then resumed later using either the connector’s own checkpoint or a small stored “backfill envelope” containing where to restart and how many already-seen records to skip. Full snapshot streams are not capped, because missing-record detection only works if the whole collection is seen.

#### Function details

##### `binding_name`  (lines 101–111)

```
def binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: Builds a stable, human-readable object name for a connector binding. It uses the provider name, account, and tenant URL so the same connection gets the same name wherever the system refers to it.

**Data flow**: It receives a provider name, an account handle, and an optional base URL. It packages those values into sorted JSON, hashes that text, takes a short digest, and returns a name like the provider plus that digest. The inputs are not changed.

**Call relations**: This is a standalone naming helper. It relies on JSON formatting and SHA-256 hashing so other parts of the system can use a compact name without embedding the full account and URL.

*Call graph*: 2 external calls (sha256, dumps).


##### `ConnectorBackend.fetch`  (lines 149–236)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one sync pass for one connector stream and returns the system’s standard SyncResult. This is the heart of the adapter: it fetches provider records, turns usable ones into Pages, notes deletes, advances the cursor, and stops safely when an incremental run grows too large.

**Data flow**: It receives a source row config, the previous cursor, and source authentication context. It resolves a credential, finds the requested stream, decodes any saved backfill envelope, calls the connector for records, skips any already-counted prefix when resuming, converts records into Pages, updates the watermark, gathers provider-reported deletes, and returns a SyncResult. Along the way it may warn and count dropped records, and it may return early with a resume cursor if the run hits the incremental record cap.

**Call relations**: This method coordinates all helpers in this file. It calls _credential before talking to the connector, _stream and _base_url to select what to fetch, _decode_cursor when a previous capped run stored adapter-owned resume state, _page for each record, and _max_str to advance a string watermark. It hands the final pages, deletes, cursor, snapshot flag, and dropped count back to the source-sync driver.

*Call graph*: calls 6 internal fn (_base_url, _credential, _decode_cursor, _page, _stream, _max_str); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._credential`  (lines 238–252)

```
async def _credential(self, config: ConnectorSourceConfig, auth: SourceAuth) -> Credential
```

**Purpose**: Gets the credential needed to call the outside provider for this account. It also turns an unusable grant into a StreamSkipped error, which means the stream should pause rather than fail as a coding bug.

**Data flow**: It receives the source config and auth context. If no auth proxy is wired, it raises a runtime error because the connector has no safe way to obtain credentials. Otherwise it asks the auth proxy for the workspace, connector, and account credential; if the grant is unusable, it raises StreamSkipped with information about whether user action is needed.

**Call relations**: ConnectorBackend.fetch calls this before fetching any provider data. The returned Credential is passed only into the connector fetch call, preserving the rule that secrets stay inside the sync job and are not exposed to sandbox or agent-facing surfaces.

*Call graph*: calls 1 internal fn (__init__); called by 1 (fetch).


##### `ConnectorBackend._base_url`  (lines 254–263)

```
def _base_url(self, config: ConnectorSourceConfig) -> str
```

**Purpose**: Chooses the web host that the connector should call. This matters for providers where each tenant has a different URL, such as a company-specific helpdesk domain.

**Data flow**: It receives the source config. It first uses the row’s base_url if present, otherwise the connector’s default base_url. If neither exists, it raises an error instead of letting the connector try to call an empty or wrong host.

**Call relations**: ConnectorBackend.fetch calls this once before asking the connector for records. The chosen URL is then handed into the connector’s fetch_page call.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._stream`  (lines 265–269)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream definition named by the source row. A stream definition tells the backend things like the stream’s name, primary key, cursor field, and whether it represents a full snapshot.

**Data flow**: It receives a stream name, checks the connector’s available streams one by one, and returns the matching StreamSpec. If there is no match, it raises a ValueError because the source row points at a stream this connector does not offer.

**Call relations**: ConnectorBackend.fetch calls this near the start of a sync run. The returned StreamSpec guides record conversion, cursor handling, delete handling, and snapshot behavior for the rest of the run.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 272–289)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Recognizes the adapter’s own saved backfill resume state inside a cursor. If the cursor is just normal connector state, it leaves it alone by returning None.

**Data flow**: It receives a stored cursor string or None. If there is no cursor, invalid JSON, or JSON that does not contain the reserved ufo_backfill key, it returns None. If the reserved key is present, it validates the stored origin, skip count, and watermark and returns them as a _BackfillEnvelope; malformed adapter-owned data raises an error.

**Call relations**: ConnectorBackend.fetch uses this only for incremental streams before starting connector fetches. Its job is to tell fetch whether to resume normally from the cursor or to re-drive from an earlier origin and skip records already landed in a capped previous run.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 291–344)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page | None
```

**Purpose**: Turns one raw provider record into a Page the UFO source system can store and later recall. If the record cannot be safely identified or represented, it warns and returns None so the rest of the stream can still sync.

**Data flow**: It receives a stream definition and one provider record. It asks the connector for the record’s stable identity, display reference, title, and body; extracts created and updated timestamps; then tries to build a Page with source identity and source reference names scoped by the stream. If the identity is missing or Page validation fails, it logs a warning and returns None instead of a Page.

**Call relations**: ConnectorBackend.fetch calls this for every record that is not being skipped as part of resume logic. This function delegates timestamp cleanup to _record_timestamp and uses validation_fault to make validation warnings understandable.

*Call graph*: calls 1 internal fn (_record_timestamp); called by 1 (fetch); 3 external calls (__init__, warn, validation_fault).


##### `_record_timestamp`  (lines 347–378)

```
def _record_timestamp(record: dict[str, Any], field: str | None, *, connector: str, stream: str) -> str | None
```

**Purpose**: Reads and normalizes a timestamp field from a provider record. It accepts common timestamp forms and drops bad values with a warning instead of poisoning the whole page.

**Data flow**: It receives a record, an optional field name, and connector/stream names for warnings. If no field is configured or the value is missing, it returns None. Otherwise it reads either a direct field or a nested path, converts strings or non-boolean integers through normalize_page_timestamp, and returns the normalized timestamp string; malformed or unsupported values produce a warning and return None.

**Call relations**: ConnectorBackend._page calls this for created-at and updated-at fields before constructing a Page. It uses get_path when the connector declares a nested field path and warn when provider data does not look like a valid timestamp.

*Call graph*: called by 1 (_page); 3 external calls (warn, get_path, normalize_page_timestamp).


##### `_max_str`  (lines 381–386)

```
def _max_str(current: str | None, value: Any) -> str | None
```

**Purpose**: Keeps the largest string value seen so far, used as a simple watermark for incremental syncing. A watermark is a remembered marker that helps the next run continue after records already processed.

**Data flow**: It receives the current watermark and a candidate value from the record. If the candidate is not a string, it returns the current watermark unchanged. If the candidate is a string and is greater than the current value, it returns the candidate; otherwise it keeps the current value.

**Call relations**: ConnectorBackend.fetch calls this while reading records from streams that declare a cursor field. The resulting watermark can become the next cursor when the connector does not provide a more specific page cursor.

*Call graph*: called by 1 (fetch).


### Direct Source Authentication
Provides the API-key based credential path for connectors that do not use an installed broker account.

### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `during source feed sync authentication`

Some source connectors need to talk to an outside provider using an API key that the workspace member added directly. This file is the small bridge that makes that possible. The key is stored encrypted in the system’s credential store under the provider’s name. When a sync run needs it, the run is routed to this direct auth proxy rather than to a normal account broker.

The important safety rule is that the secret stays on the host side. In plain terms, the sync job may unlock and use the key to make provider HTTP requests, but the key is not passed into a sandbox or exposed to an agent. This is like keeping a building key with a trusted front desk: the front desk can open the right door when needed, but it does not hand the key to visitors.

The `DirectAuthProxy` class holds a `CredentialAccess` object, which is the controlled doorway into the workspace’s credential store. Its only behavior is to look up the secret slot named after the connector’s provider and wrap the result as a bearer token credential. The `account` value is accepted because the auth-proxy interface expects it, but for this direct mode the real secret is the provider-named credential, not the account handle.

#### Function details

##### `DirectAuthProxy.credential`  (lines 29–30)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This function fetches the API key for a provider from the credential store and returns it in the standard credential shape used for bearer-token authentication. Someone would use it when a source sync needs to call a provider with a member-added key.

**Data flow**: It receives a workspace ID, a provider name, and an account handle. It uses the provider name to read the matching secret through `CredentialAccess`, then places that secret into a new `Credential` as its bearer value. The result is a credential object ready for provider HTTP authentication; the stored secret is read but not otherwise changed.

**Call relations**: During a direct-auth source sync, the wider auth-proxy flow calls this method to obtain credentials for the provider. Inside the method, it hands the fetched secret to `Credential.__init__` so the rest of the sync code can receive it in the normal credential format.

*Call graph*: 1 external calls (__init__).


### Provider Catalog
Exposes provider modules and registers each known external connector by backend name.

### `extensions/sources/ufo_ext_sources/providers/__init__.py`

`other` · `cross-cutting`

This is an empty Python package marker. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That matters here because the surrounding project likely keeps different source-provider implementations inside the `providers` directory, and other parts of the system need to import them using package-style paths. Think of this file like a label on a drawer: it does not contain tools, but it tells Python that the drawer is part of the organized toolbox. Without it, depending on the Python version and packaging setup, imports from this folder could become less reliable or fail in environments that expect traditional packages. Because it is empty, it adds no startup work, no configuration, and no runtime decisions.


### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup`

This file answers a simple but important question: when a saved source says it uses a backend like “slack” or “stripe”, which connector code should the system run? It does that with an explicit registry, which is like a phone book for integrations: each provider name points to the connector class that knows how to fetch data from that provider.

The file imports every supported connector class, from tools like Airtable, GitHub, Google Drive, Salesforce, Slack, Stripe, Zendesk, and many others. It then passes that list into a small helper that builds the final dictionary called CONNECTORS. The keys in that dictionary are the connector names, and the values are the connector classes themselves.

The registry is deliberately written out by hand rather than discovered automatically by scanning files. That makes startup more predictable and avoids hidden import-time work. It also means adding a new provider is a clear, intentional change: import its connector and add it to the list.

One important safety check happens while building the registry: two connectors are not allowed to share the same name. Without that check, the system might silently pick the wrong connector for a source, which would make credentials, source rows, and sync behavior confusing or broken.

#### Function details

##### `_connector_registry`  (lines 66–74)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: This function turns a list of connector classes into a lookup table keyed by each connector’s name. It also protects the system from accidentally registering two connectors with the same backend name.

**Data flow**: It receives a tuple of connector classes. It starts with an empty dictionary, reads the name stored on each connector class, and adds an entry from that name to the class. If a name has already been used, it stops immediately with an error instead of overwriting the earlier connector. The result is a dictionary that the rest of the source system can use to find the right connector class by name.

**Call relations**: This function is called in this file when CONNECTORS is created during import, usually as the application starts up or loads source support. It does not hand work off to other project functions; its job is to prepare the registry that later sync and source-loading code can consult when it needs the connector for a specific backend.
