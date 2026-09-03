# External source connector polling  `stage-15.1`

This stage is the system’s behind-the-scenes intake layer for outside services. It runs during syncing, after an account is connected, and repeatedly asks external APIs, or software “front desks,” what data is available. The many connector groups cover workplace tools, project trackers, developer tools, CRMs, support systems, marketing platforms, finance apps, HR tools, scheduling services, and document-signing systems. Each connector knows the quirks of one service, such as Slack, GitHub, Stripe, HubSpot, Google Sheets, or Linear, and turns that service’s records into a common shape.

The shared files are the engine under these adapters. The registry is the address book that maps a source name to the right connector. The connector code defines the common rules, including cursors, which are bookmarks that let a sync resume safely. The REST helper makes web requests, retries temporary failures, and follows page-by-page results. The backend turns connector output into internal pages and guards against bad records or runaway backfills. The connected-account helper creates the first feed rows when a member links an account, and can repair missing rows later.

## Sub-stages

- [Workspace, communications, and office-suite sources](stage-15.1.1.md) `stage-15.1.1` — 10 files
- [Work management, knowledge, developer, and incident sources](stage-15.1.2.md) `stage-15.1.2` — 12 files
- [CRM, sales, and customer-support sources](stage-15.1.3.md) `stage-15.1.3` — 7 files
- [Marketing, ads, social, forms, and tabular-data sources](stage-15.1.4.md) `stage-15.1.4` — 8 files
- [Finance, billing, accounting, and spend sources](stage-15.1.5.md) `stage-15.1.5` — 9 files
- [People operations, recruiting, scheduling, and agreement sources](stage-15.1.6.md) `stage-15.1.6` — 9 files

## Files in this stage

### Account feed binding
Creates and repairs feed rows when members connect external source accounts.

### `extensions/sources/ufo_ext_sources/connected.py`

`domain_logic` · `connection hook and retry job`

When a member connects an outside account, the system records permission to use that account. But the content itself is read through separate “source” rows, one per stream of content. This file closes that gap: after a connection is recorded, it creates private sources for the connector’s canonical streams, meaning the main streams the connector exists to carry.

It works like setting up mailboxes right after someone gives you a key to a post office box. The key alone is not enough; the system also needs to know which mail slots to check.

There are two ways this code runs. The hook `on_connection_recorded` runs immediately after a connection is made. The scheduled retry function `retry_connected_sources` runs later and creates only rows that are still missing. Both paths use `ConnectedSources`.

The code is careful not to undo a member’s choices. If a source row already exists, it leaves it alone. If the member removed a source before, it does not recreate it. It also avoids auto-registering connectors that need a tenant-specific base URL, because only the member can provide that URL. New canonical streams added by a later connector version can be picked up for existing connections, but deleted streams stay deleted.

#### Function details

##### `on_connection_recorded`  (lines 51–58)

```
async def on_connection_recorded(ctx: HookContext) -> HookOutcome
```

**Purpose**: This is the hook that runs when the system announces that a connection has just been recorded. Its job is to give that new connection its default feed sources before the connect flow finishes.

**Data flow**: It receives a hook context containing a payload. If the payload is a `ConnectionRecorded` event, it takes the connection ID, builds a `ConnectedSources` helper from the extension context, and asks it to register sources for just that connection. If the payload is not the expected kind, it raises an error because this hook was called for the wrong event. It returns no special outcome.

**Call relations**: This function is the immediate path. When the connection-recorded hook fires, it creates `ConnectedSources` and hands the newly committed connection ID to the registration flow, so only that connection is considered.

*Call graph*: 1 external calls (__init__).


##### `retry_connected_sources`  (lines 61–63)

```
async def retry_connected_sources(ctx: ExtensionContext) -> None
```

**Purpose**: This is the repair path for connected-account sources. It runs later and fills in feed rows that were not created during the original connection flow.

**Data flow**: It receives an extension context, builds a `ConnectedSources` helper from it, and asks that helper to register sources without naming a single connection. That means all eligible main-agent connections are checked. It returns nothing after the missing rows have been attempted.

**Call relations**: This function is used after the fact, such as by a scheduled job. Instead of producing new connection events, it reuses the same `ConnectedSources.register` logic as the hook so the retry behaves the same way as the original path.

*Call graph*: 1 external calls (__init__).


##### `ConnectedSources.register`  (lines 74–82)

```
async def register(self, connection_id: UUID | None=None) -> None
```

**Purpose**: This method decides which connected accounts should receive automatic source rows. It can work on one specific connection or scan every connection held by the main agent.

**Data flow**: It first reads the currently live source rows from the extension context. Then it asks for the main agent’s connections and, if a specific connection ID was provided, skips every other connection. For each remaining connection, it looks up the connector class for that provider. If there is no connector, or the connector has no base URL and therefore needs member-supplied tenant information, it skips it. Otherwise it creates a connector instance and passes the connection, connector, and live source list into `_register`.

**Call relations**: Both entry paths use this method: the hook calls it for one new connection, and the retry job calls it for all connections. It is the dispatcher that filters connections and hands eligible ones to `ConnectedSources._register` for the detailed per-stream work.

*Call graph*: calls 1 internal fn (_register); 2 external calls (main_agent_connections, get).


##### `ConnectedSources._register`  (lines 84–129)

```
async def _register(self, connection: MainAgentConnection, connector: Connector, live: tuple[SourceRecord, ...]) -> None
```

**Purpose**: This method creates the missing source rows for the canonical streams of one connected account. It protects existing rows and past removals so automatic setup does not override what the member has already chosen.

**Data flow**: It receives one connection, its connector, and the already-live source rows. It builds the member subject for the connection owner, then finds existing rows tied to that connection. If any existing bound row belongs to a different subject, it stops, because this automatic private setup should not add to a shared or otherwise different binding. If there is an existing bound row, it reads its saved configuration to reuse the requested backfill window. It then checks each stream exposed by the connector, keeps only canonical streams, calculates the earliest date to sync from, builds a source configuration, and computes the source ID that row would have. If that ID is not already live, it is marked as a fresh candidate. Before creating anything, it asks the extension context which of those candidate IDs were removed before. Finally, it registers only the candidates that are neither live nor previously removed, setting them as private to the connection owner and tied to the connection.

**Call relations**: This is called by `ConnectedSources.register` after a connection has passed the provider and connector checks. It uses the connector’s stream list, source configuration validation, date calculation, member-subject creation, and backfill-day helper to turn a connected account into concrete source rows. It then hands each safe new row to the extension context’s source registration API.

*Call graph*: calls 1 internal fn (streams); called by 1 (register); 6 external calls (__init__, model_validate, now, timedelta, member_subject, effective_days).


### Source sync runtime
Provides the shared REST fetching, pagination, cursor, and internal page conversion machinery used by source connectors.

### `core/src/ufo/runtime/sources/rest.py`

`io_transport` · `source fetch / API request handling`

Many web APIs do not return all records at once. They return one page, plus some clue for how to get the next page: a cursor token, a link header, an offset number, or an OData next link. This file is the common toolkit for that job. It builds an HTTP client with the right authentication, sends GET or read-only POST requests, retries problems that are likely temporary, and turns API responses into pages of plain records. It also protects the system from bad API behavior. For example, if an API keeps giving the same next-page token, the connector stops with a clear error instead of looping forever. If an API returns too many pages without ending, it also stops. Think of it like a careful librarian fetching boxes of documents: it follows the label for the next box, checks that the label changes, and refuses to wander in circles. The `RestConnector` class is the main piece. Specific service connectors can use its standard pagination strategies, or override small seams such as how to choose a path or how to flatten a nested record before writing it downstream.

#### Function details

##### `list_or_empty`  (lines 50–54)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: This helper safely turns an unknown value into a list of record dictionaries. If the value is not a list, or if the list contains non-record items, those items are ignored.

**Data flow**: It receives any value. If that value is a list, it keeps only the items that are dictionaries; otherwise it returns an empty list. The output is always safe for later code to treat as a list of records.

**Call relations**: Pagination helpers use this when reading records from API responses. It is called by `records_at`, `_response_list`, and `_get_odata_pages` so those flows do not crash or pass along badly shaped data.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 57–60)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This helper safely turns an unknown value into a dictionary-shaped record. It is useful when a connector wants to reach into a nested object but needs a harmless fallback if the object is missing or malformed.

**Data flow**: It receives any value. If the value is a dictionary, it returns that same dictionary; otherwise it returns an empty dictionary. It does not change anything outside itself.

**Call relations**: This is a small shaping utility for connector code that needs a predictable dictionary. In this chunk, no callers are shown, so it appears to be available for connector-specific parsing elsewhere in the file or project.


##### `records_at`  (lines 63–68)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: This helper extracts a list of records from a response body, either from the body itself or from a named nested path inside it. It keeps pagination code simple when different APIs put their records in different places.

**Data flow**: It receives response data and an optional path. With no path, it treats the whole data value as the possible record list. With a path, it first checks the data is dictionary-like, uses `get_path` to find the nested value, and then uses `list_or_empty` to return only dictionary records.

**Call relations**: It is the common record extractor for cursor, offset, page-number, and link-header pagination. `paginate_from_strategy.parse` also uses it when a link-header API stores records inside a JSON envelope instead of returning a top-level list.

*Call graph*: calls 1 internal fn (list_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, parse); 1 external calls (get_path).


##### `_int_or_none`  (lines 71–76)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: This helper reads a value as an integer only when that is safe and obvious. It avoids guessing when an API returns a value that is not clearly a whole number.

**Data flow**: It receives any value. If the value is already an integer, it returns it. If it is a string made only of decimal digits, it converts it to an integer. Otherwise it returns `None`.

**Call relations**: `_get_offset_pages` uses this when an API reports the actual page size it applied. If the reported size cannot be trusted as a number, the pagination loop falls back to safer step sizes.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 79–82)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: This helper adds extra context fields to every record, such as which account, cloud site, or parent object the record came from. That lets later fan-out or rendering steps trace records back to their origin.

**Data flow**: It receives an iterable of record dictionaries plus named context values. For each record, it makes a new dictionary containing the original fields and the context fields. It returns a new list and does not mutate the original records.

**Call relations**: No callers are shown in this chunk, but it is designed for connector pagination flows that fetch child records or partitioned data. Those flows can stamp origin information before passing records downstream.


##### `next_link`  (lines 85–90)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper reads the next-page URL from an HTTP `Link` header. Some APIs put pagination instructions in headers instead of the JSON body.

**Data flow**: It receives HTTP headers, looks for the `link` header, and searches it for the part marked as the next page. It returns the next URL string if found, or `None` if there is no next page signal.

**Call relations**: `_get_link_header_pages` calls this after each response. If `next_link` returns a URL, the pager follows it; if it returns `None`, the pager stops.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 93–98)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: This helper decides whether a failed request is worth trying again. It treats network transport problems and selected temporary HTTP status codes as retryable.

**Data flow**: It receives an exception. If the exception is a network-level `httpx.TransportError`, it returns `true`. If it is an HTTP error response, it checks whether the status code is in the configured retryable set. Otherwise it returns `false`.

**Call relations**: `_send` uses this inside its retry loop. When `_is_retryable` says no, `_send` stops immediately and lets the error fail the fetch instead of wasting more attempts.

*Call graph*: called by 1 (_send).


##### `_retry_after`  (lines 101–121)

```
def _retry_after(error: BaseException) -> float | None
```

**Purpose**: This helper reads an API's `Retry-After` instruction when the API says, in effect, “come back later.” It accepts only finite, non-negative numbers so the event loop is not given unsafe sleep times.

**Data flow**: It receives an exception. It only continues if the exception is an HTTP status error whose status is allowed to carry useful retry timing. It reads the `retry-after` header, parses it as a number of seconds, rejects invalid, infinite, `NaN`, or negative values, and returns either that wait time or `None`.

**Call relations**: `_retry_wait` calls this before deciding how long to sleep. This lets the retry logic respect a provider's rate-limit or temporary-unavailable timing when the header is usable.

*Call graph*: called by 1 (_retry_wait); 1 external calls (isfinite).


##### `_retry_wait`  (lines 124–141)

```
def _retry_wait(error: BaseException, delay: float) -> float
```

**Purpose**: This helper chooses how long to wait before the next retry. It balances the connector's own growing backoff delay with any valid `Retry-After` value from the API, then adds random jitter so many workers do not retry at the exact same moment.

**Data flow**: It receives the error that happened and the current base delay. It asks `_retry_after` whether the response named a wait time, clamps both the API-stated wait and the connector's own delay to maximum limits, chooses the stronger floor, and returns a random wait between that floor and a capped upper bound.

**Call relations**: `_send` calls this after a retryable failure. The returned number controls the `asyncio.sleep` before the request is tried again.

*Call graph*: calls 1 internal fn (_retry_after); called by 1 (_send); 1 external calls (uniform).


##### `_raise_for_status`  (lines 144–155)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: This helper turns unsuccessful HTTP responses into clear errors that include a short slice of the response body. That matters because APIs often put the real explanation, such as “filter is not valid,” in the body.

**Data flow**: It receives an HTTP response. If the response is successful, it does nothing. Otherwise it takes a bounded amount of response text and raises an `httpx.HTTPStatusError` containing the status, request method, URL, and body excerpt.

**Call relations**: `_send` calls this immediately after each HTTP request. A bad response becomes an exception, which `_send` can either retry or pass upward depending on `_is_retryable`.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 158–162)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: This helper reads a JSON object from a response, while treating empty responses as an empty dictionary. It smooths over APIs that return no body for successful requests.

**Data flow**: It receives an HTTP response. If the status is 204, meaning “no content,” or the body is empty, it returns `{}`. Otherwise it parses the response JSON and returns it as a dictionary.

**Call relations**: `_get` and `_post` use this after `_send` returns a successful response. Those methods then give callers a predictable dictionary-shaped body.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 165–168)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: This helper reads a response whose JSON body is expected to be a top-level list of records. Empty responses become an empty list.

**Data flow**: It receives an HTTP response. If the response has no content, it returns an empty list. Otherwise it parses JSON and uses `list_or_empty` so only dictionary records are returned.

**Call relations**: `_get_link_header_pages` uses this when no custom record parser is supplied. It lets link-header pagination work with APIs that return each page as a simple JSON array.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 171–177)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: This guard stops pagination loops that run for too many pages. Without it, a broken or hostile API could make a fetch run forever.

**Data flow**: It receives a human-readable label for the operation and the number of pages fetched so far. If the count is greater than the configured maximum, it raises an error. Otherwise it returns silently.

**Call relations**: All the page-walking methods call this on each loop. It is the shared safety brake for cursor, link-header, OData, offset, and page-number pagination.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 180–186)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: This guard detects when an API repeats the same next-page token or URL. A repeated cursor usually means the provider is not advancing and the connector would fetch the same page forever.

**Data flow**: It receives a label, the current token or link, and a set of tokens already seen. If the token is already in the set, it raises an error. Otherwise it adds the token to the set.

**Call relations**: Cursor-like pagers call this whenever they receive a next-page signal. It catches infinite loops earlier and more clearly than the general page-count limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 195–196)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This method returns the list of streams this connector can read. A stream is a named collection of records, such as users, issues, or events.

**Data flow**: It reads `self.streams_list`, copies it into a new list, and returns that copy. The connector's stored list is not handed out directly.

**Call relations**: No callers are shown in this chunk, but this is the standard way outside orchestration code can ask a connector what data collections it supports before fetching them.


##### `RestConnector._make_client`  (lines 198–215)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method builds the HTTP client used for one account or credential. It attaches the right authentication, sets JSON headers, applies timeouts, and supports either direct credentials or a proxy transport that keeps secrets server-side.

**Data flow**: It receives a base URL and a resolved credential. It normalizes the base URL, prepares JSON request headers, chooses between a custom transport, a bearer token, or provided headers, and returns an `httpx.AsyncClient`. If no authentication path is present, it raises an error instead of making anonymous requests.

**Call relations**: `fetch_page` calls this before pagination begins. The resulting client is then passed into `paginate_source`, which uses it for all requests in that fetch.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 217–220)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This method performs a GET request and returns the response body as a dictionary. It is the convenient path for endpoints whose useful data is in JSON.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It calls `_get_raw` to send the request with retry behavior, then passes the response to `_json_or_empty`. The result is a dictionary, with empty bodies represented as `{}`.

**Call relations**: Cursor, offset, and page-number pagers call this when they need the parsed response body. `_get_raw` does the actual HTTP work underneath.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 3 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages).


##### `RestConnector._get_raw`  (lines 222–226)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: This method performs a GET request and returns the full raw HTTP response. It is used when pagination needs headers or other response details, not just the JSON body.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It wraps `client.get(...)` in `_send`, so temporary failures are retried and bad statuses are raised. The output is the successful raw response.

**Call relations**: `_get` calls this before parsing JSON. Link-header and OData pagination call it directly because they need headers, absolute next links, or custom body parsing.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 228–233)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This method performs a read-style POST request and returns a JSON dictionary. It exists for APIs that expose read operations through POST, such as search endpoints.

**Data flow**: It receives an HTTP client, a path, and an optional JSON request body. It sends `client.post(...)` through `_send`, then converts the successful response with `_json_or_empty`. The result is a dictionary response body.

**Call relations**: No callers are shown in this chunk, but connector-specific pagination methods can use it when an API requires POST for reading. It shares the same retry and error behavior as GET requests.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 235–241)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: This method performs a read-style POST request and returns the raw HTTP response. It is useful for APIs whose read response is not a dictionary, such as a top-level array of result batches.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It sends the POST through `_send` and returns the successful raw response without parsing it.

**Call relations**: No callers are shown in this chunk, but custom connector pagination can use it when it needs to parse a non-standard POST response itself. `_send` supplies the shared retry wrapper.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 243–264)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: This is the shared retry wrapper for HTTP requests. It tries a request, turns bad HTTP statuses into errors, retries temporary failures, and gives up when attempts or waiting budget are exhausted.

**Data flow**: It receives a no-argument function that, when called, makes one HTTP request. For each attempt, it awaits the request, checks the status with `_raise_for_status`, and returns the response if successful. If a network or HTTP error occurs, it uses `_is_retryable` and `_retry_wait` to decide whether and how long to sleep before trying again; otherwise it raises the error.

**Call relations**: `_get_raw`, `_post`, and `_post_raw` all hand their actual HTTP calls to this method. This centralizes retry behavior so every REST read follows the same rules.

*Call graph*: calls 3 internal fn (_is_retryable, _raise_for_status, _retry_wait); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 266–303)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[
```

**Purpose**: This is the high-level fetch method that opens an authenticated client, gets pages from the connector's pagination logic, validates their shape, flattens records, and yields them downstream. It is the bridge between raw API pagination and the rest of the sync system.

**Data flow**: It receives a stream, cursor, credential, base URL, acting user ID, and optional backfill date. It chooses the usable base URL, builds a client with `_make_client`, asks `paginate_source` for pages, skips empty pages, validates record lists with `_validate_page`, flattens each record with `flatten`, and yields either plain record lists or `StreamPage` objects preserving deletes and next cursors. When finished or interrupted, it closes async generators cleanly.

**Call relations**: Higher-level source execution code would call this to fetch a stream. Inside, it delegates API traversal to `paginate_source`, then prepares the yielded records for writing by validation and flattening.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate_source); 1 external calls (__init__).


##### `RestConnector.paginate_source`  (lines 305–318)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This method is a customization seam between the general fetch flow and a connector's pagination method. By default it ignores extra run context and calls the simpler `paginate` method.

**Data flow**: It receives the HTTP client, stream, cursor, acting user ID, and optional backfill date. The default implementation passes only the client, stream, and cursor to `paginate`, returning that async iterator unchanged.

**Call relations**: `fetch_page` calls this after creating the client. Connectors that need the acting identity or backfill floor can override this method while leaving `fetch_page` unchanged.

*Call graph*: calls 1 internal fn (paginate); called by 1 (fetch_page).


##### `RestConnector.paginate`  (lines 320–332)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This method yields raw pages of records for a stream. The default implementation uses the stream's declared pagination strategy, while streams with unusual API shapes are expected to override it.

**Data flow**: It receives an HTTP client, a stream, and an optional cursor. It checks the stream's pagination settings. If there is no usable strategy, it raises `NotImplementedError`; otherwise it yields each page produced by `paginate_from_strategy`.

**Call relations**: `paginate_source` calls this in the default path. It hands standard pagination work to `paginate_from_strategy`, keeping service-specific overrides optional.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (paginate_source).


##### `RestConnector.paginate_from_strategy`  (lines 334–396)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method runs one of the built-in pagination strategies described on a stream. It turns a stream's pagination settings into calls to the correct page-walking helper.

**Data flow**: It receives a stream and HTTP client. It reads the stream's `Pagination` specification, chooses the request path, checks that required settings are present, and then yields pages from the matching helper: cursor tokens, link headers, or offset-and-limit. Extra query parameters and page size settings are passed into those helpers.

**Call relations**: `paginate` calls this for streams that declare standard pagination. Depending on the strategy, it hands off to `_get_cursor_pages`, `_get_link_header_pages`, or `_get_offset_pages`; for link-header responses with nested records it creates the local `parse` function.

*Call graph*: calls 4 internal fn (_get_cursor_pages, _get_link_header_pages, _get_offset_pages, _strategy_path); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 366–368)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: This small local parser extracts records from a link-header paginated response when the records are nested inside the JSON body. It adapts a header-driven pager to APIs that do not return a top-level list.

**Data flow**: It receives an HTTP response. If the response has content, it parses the JSON body; otherwise it uses an empty dictionary. It then calls `records_at` with the configured record path and returns the extracted list of record dictionaries.

**Call relations**: `paginate_from_strategy` creates this function only for the `next_link` strategy when a record path is supplied. It passes the function into `_get_link_header_pages`, which calls it for each response.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 398–426)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: This method reads pages from APIs that put the next-page URL in an HTTP `Link` header. This style is common in APIs that follow web linking conventions.

**Data flow**: It receives a client, first path, optional query parameters, optional page size settings, and an optional response parser. It sends the first GET, yields records from each response, reads the next URL with `next_link`, checks repeated links with `_bound_cursor`, and continues until no next link is present.

**Call relations**: `paginate_from_strategy` calls this for the `next_link` strategy. It relies on `_get_raw` for retried HTTP requests, `_response_list` or a custom parser for records, and the pagination guards to avoid endless loops.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 428–460)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: This method reads pages from APIs whose response body contains a next-page cursor token. A cursor is a bookmark from the server that says where to continue.

**Data flow**: It receives a client, path, record path, cursor path, query parameter names, optional page size, and extra parameters. It repeatedly builds a query, includes the latest cursor when present, fetches JSON with `_get`, extracts records with `records_at`, yields non-empty pages, reads the next cursor with `get_path`, and stops when there is no valid cursor.

**Call relations**: `paginate_from_strategy` calls this for the `next_cursor` strategy. `_bound_pages` and `_bound_cursor` protect the loop from APIs that never end or repeat the same cursor.

*Call graph*: calls 4 internal fn (_get, _bound_cursor, _bound_pages, records_at); called by 1 (paginate_from_strategy); 1 external calls (get_path).


##### `RestConnector._get_odata_pages`  (lines 462–488)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads Microsoft Graph / OData-style pages. In that format, records are usually under `value`, and the next page is named by `@odata.nextLink`.

**Data flow**: It receives a client, starting path, and optional parameters. It fetches the current path with `_get_raw`, parses JSON if present, extracts dictionary records from the `value` field, yields them, then follows `@odata.nextLink` until it disappears. Only the first request uses the original parameters because later next links already include their own continuation query.

**Call relations**: No caller is shown in this chunk, but it is a ready-made helper for connectors that speak OData. It uses `_bound_pages` and `_bound_cursor` like other cursor-style pagers.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 490–530)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: This method reads pages from APIs that use an offset and limit. The offset says how many records to skip, and the limit says how many to ask for.

**Data flow**: It receives a client, path, record path, limit, parameter names, optional extra parameters, and optional response paths that describe continuation. It repeatedly sends a GET with the current offset and limit, extracts records, yields them, and decides whether to stop based on an explicit `more` flag or by seeing a short page. It advances the offset by the requested limit or by the server-reported applied limit when available.

**Call relations**: `paginate_from_strategy` calls this for the `offset_limit` strategy. It uses `_get` for retried JSON requests, `records_at` for extraction, `_int_or_none` for safe numeric parsing, and `_bound_pages` as the loop safety limit.

*Call graph*: calls 4 internal fn (_get, _bound_pages, _int_or_none, records_at); called by 1 (paginate_from_strategy); 1 external calls (get_path).


##### `RestConnector._get_page_number_pages`  (lines 532–561)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: This method reads APIs that use page numbers, such as `page=1`, `page=2`, and so on. It keeps requesting the next page until a page comes back shorter than the expected size.

**Data flow**: It receives a client, path, record path, page size, optional parameters, parameter names, and a starting page number. It builds a query with the current page and page size, fetches JSON with `_get`, extracts records, yields them, and stops when the number of records is less than the page size. Otherwise it increments the page number.

**Call relations**: No caller is shown in this chunk, but it is another standard pagination helper available to connectors. It shares `_get`, `records_at`, and `_bound_pages` with the other strategies.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at).


##### `RestConnector._strategy_path`  (lines 563–569)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: This method supplies a request path for strategy-based pagination when the stream's pagination settings do not name one directly. The base version raises an error so connectors must be explicit.

**Data flow**: It receives a stream. Instead of guessing a URL path, it raises `NotImplementedError` explaining that the pagination path is missing and no connector override exists.

**Call relations**: `paginate_from_strategy` calls this when `Pagination.path` is unset. Service-specific connectors can override it to look up paths from their own stream tables.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 571–574)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method converts a raw API record into the flat dictionary shape the sync writer expects. The default does nothing because many APIs already return suitable records.

**Data flow**: It receives one record dictionary and the stream it belongs to. The base implementation returns the same record unchanged. Overrides can copy or lift nested fields into a flatter shape.

**Call relations**: `fetch_page` calls this for every record after validation and before yielding downstream. Connectors with nested payloads can override it without changing pagination or retry behavior.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 576–587)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: This method checks that pagination produced the kind of page the rest of the pipeline expects: a list of dictionaries. It fails early with a clear message if a connector yields the wrong shape.

**Data flow**: It receives any page value and the stream being fetched. It first checks the page is a list, then checks each item is a dictionary. If either check fails, it raises a `TypeError`; otherwise it returns silently.

**Call relations**: `fetch_page` calls this before flattening and yielding records. This protects downstream writing code from confusing errors caused by malformed connector output.

*Call graph*: called by 1 (fetch_page).


### `core/src/ufo/runtime/sources/backend.py`

`orchestration` · `source sync runs`

Connectors talk to outside services such as GitHub, Zendesk, or Freshdesk. They usually return records page by page, with provider-specific cursors and deletion signals. The rest of this system wants a simpler result from each run: a batch of internal Page objects, a next cursor, deletion notices, and a count of records that had to be skipped. This file is the adapter that translates between those worlds.

The main class, ConnectorBackend, syncs one configured account and one configured stream at a time. It first asks the authentication proxy for a usable Credential, finds the requested stream, chooses the right base URL, then drives the connector's async record stream. Each provider record is rendered into an internal Page with a stable identity like `stream/id`, so future updates and deletes refer to the same item.

The file is careful about safety. A record with no primary key, or one that cannot become a valid Page, is warned about and counted as dropped instead of failing the whole run. Incremental streams are capped so one huge history import does not occupy a worker forever. If the connector does not provide its own checkpoint, this adapter stores a small private resume envelope in the cursor, like leaving a bookmark that says “start from the old place, then skip the first N records.” Full snapshot streams are not capped, because deletion detection is only correct if the whole collection is seen.

#### Function details

##### `binding_name`  (lines 101–111)

```
def binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: Builds a stable, human-safe object name for a connector binding. It uses the provider, account, and tenant base URL so the same connected account gets the same name wherever the system needs to refer to it.

**Data flow**: It takes a provider name, an account handle, and an optional base URL. It turns those values into sorted JSON, hashes that text, keeps a short digest, and returns a name made from the provider plus that digest. Nothing outside the function is changed.

**Call relations**: This helper sits at the naming edge of the connector system. Other parts of the system can use the returned name when they need a consistent label for a binding, while the function itself only relies on standard hashing and JSON formatting.

*Call graph*: 2 external calls (sha256, dumps).


##### `ConnectorBackend.fetch`  (lines 149–236)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one sync pass for one connector stream. It fetches records from the external provider, converts good records into internal pages, records deletes, updates the cursor, and stops safely when an incremental run reaches its size cap.

**Data flow**: It receives a source-row config, the previously stored cursor, and authentication context. It gets a credential, resolves the stream and base URL, decodes any private backfill cursor, then reads pages from the connector. For each record it may skip already-consumed records, turn the record into a Page, update the watermark, and gather provider-reported deletions. It returns a SyncResult containing the pages, the next cursor, delete markers, whether this was a full snapshot, and how many records were dropped.

**Call relations**: This is the file's central workflow. It calls _credential before talking to the provider, _stream and _base_url to know what to fetch, _decode_cursor when resuming a capped backfill, _page for each record, and _max_str to advance a string watermark. When the batch is complete or capped, it hands a SyncResult back to the sync driver.

*Call graph*: calls 6 internal fn (_base_url, _credential, _decode_cursor, _page, _stream, _max_str); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._credential`  (lines 238–252)

```
async def _credential(self, config: ConnectorSourceConfig, auth: SourceAuth) -> Credential
```

**Purpose**: Gets the credential needed to call the external provider without exposing raw secrets to the wrong part of the system. If the grant cannot be used yet, it turns that problem into a skipped stream instead of a normal crash.

**Data flow**: It reads the connector name and account from the config, plus workspace and proxy information from the auth object. If no auth proxy is wired, it raises an error explaining the setup problem. If the proxy returns a credential, that credential comes out. If the proxy says the grant is unusable, the function raises StreamSkipped with enough information for the run to report that it is waiting on authorization.

**Call relations**: ConnectorBackend.fetch calls this at the start of every run, before any provider records are requested. It is the gatekeeper between the sync job and the authentication layer.

*Call graph*: calls 1 internal fn (__init__); called by 1 (fetch).


##### `ConnectorBackend._base_url`  (lines 254–263)

```
def _base_url(self, config: ConnectorSourceConfig) -> str
```

**Purpose**: Chooses the web address the connector should call. This matters for providers where each customer has a different tenant URL, such as a company-specific helpdesk domain.

**Data flow**: It looks first at the source row's configured base URL and then at the connector's default base URL. If it finds one, it returns that string. If neither exists, it raises an error rather than letting the connector accidentally call an empty or wrong host.

**Call relations**: ConnectorBackend.fetch calls this before starting the connector request. The returned URL is passed into the connector's fetch routine so all provider calls go to the intended tenant.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._stream`  (lines 265–269)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream definition named in the source row. A stream definition explains things like the stream's name, primary key, cursor field, and deletion behavior.

**Data flow**: It receives a stream name, loops through the connector's declared streams, and returns the matching StreamSpec. If no stream with that name exists, it raises a clear error because the source row is asking for something the connector does not provide.

**Call relations**: ConnectorBackend.fetch calls this near the beginning of a run. The returned stream spec guides later choices about cursor handling, page identity, timestamps, deletes, and whether the result is a snapshot.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 272–289)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Recognizes the adapter's own private backfill cursor format. If the stored cursor belongs to the connector itself, this function leaves it alone by returning nothing.

**Data flow**: It takes the stored cursor string. If it is missing, not JSON, not a JSON object, or does not contain the reserved `ufo_backfill` key, the function returns None. If it does contain that key, it validates the enclosed origin, skip count, and watermark and returns them as a _BackfillEnvelope. If that reserved envelope is malformed, it raises an error because this adapter is supposed to be the only writer of that shape.

**Call relations**: ConnectorBackend.fetch calls this only for incremental streams. Its answer decides whether fetch resumes using a connector-native cursor or replays from an earlier origin while skipping records already landed in a capped run.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 291–344)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page | None
```

**Purpose**: Turns one provider record into one internal Page. It also protects the run by dropping and warning about records that have no stable key or cannot fit the Page model.

**Data flow**: It receives the stream definition and a raw provider record. It asks the connector for the record's stable identity, display reference, title, and rendered body. It extracts created and updated timestamps, then tries to build a Page whose identity is tied to the stream and record key. If the record lacks a key or validation fails, it logs a warning and returns None; otherwise it returns the Page.

**Call relations**: ConnectorBackend.fetch calls this for every record that is not being skipped during resume. _page relies on _record_timestamp for timestamp cleanup and hands valid Page objects back to fetch so they can be included in the final SyncResult.

*Call graph*: calls 1 internal fn (_record_timestamp); called by 1 (fetch); 3 external calls (__init__, warn, validation_fault).


##### `_record_timestamp`  (lines 347–378)

```
def _record_timestamp(record: dict[str, Any], field: str | None, *, connector: str, stream: str) -> str | None
```

**Purpose**: Reads and normalizes a timestamp field from a provider record. It accepts usable string or integer timestamps and warns when the value cannot be understood.

**Data flow**: It receives a record, the configured timestamp field, and names for logging. If no field is configured or the field value is missing, it returns None. It reads either a direct field or a nested path, tries to normalize strings and non-boolean integers into the system's timestamp format, and returns the normalized text. If parsing fails or the value has the wrong type, it warns and returns None.

**Call relations**: ConnectorBackend._page calls this for created-at and updated-at fields while building an internal Page. The normalized values flow into the Page, while bad timestamp values are treated as missing instead of blocking the whole record.

*Call graph*: called by 1 (_page); 3 external calls (warn, get_path, normalize_page_timestamp).


##### `_max_str`  (lines 381–386)

```
def _max_str(current: str | None, value: Any) -> str | None
```

**Purpose**: Keeps the largest string value seen so far, which is used as a simple watermark for incremental streams. A watermark is a saved “latest seen” marker that helps the next sync resume from the right place.

**Data flow**: It receives the current saved string and a new value from a record. If the new value is not a string, it returns the current value unchanged. If the current value is empty or the new string sorts after it, it returns the new string; otherwise it keeps the current one.

**Call relations**: ConnectorBackend.fetch calls this while scanning records from a stream with a cursor field. Its result becomes part of the next cursor or the private backfill envelope, helping later runs continue after the records already landed.

*Call graph*: called by 1 (fetch).


### `core/src/ufo/runtime/sources/connector.py`

`domain_logic` · `source sync runs`

A connector is the project’s adapter for an outside service, such as email, chat, documents, or a code host. This file says what every connector must provide: a list of streams it can sync, a way to fetch pages of records from one stream, and a way to turn one raw record into readable text that UFO can recall later.

The file also defines small value objects that describe streams, pages, pagination, and partition boundaries. A stream is one collection from the provider, like “messages” or “repositories.” A page is a batch of records, sometimes with extra information such as deleted record IDs or a resume cursor.

The most involved part is `PartitionWalk`. Some providers split data into many buckets, like one chat channel at a time or one repository at a time. `PartitionWalk` is like a careful bookmark keeper for all those buckets. It remembers which partitions have been finished, which ones are midway through a backfill, and where the next run should resume. This matters because sync jobs can be capped, interrupted, or rerun while new records are arriving. Without this logic, the system could reread huge histories, miss records at page boundaries, or forget deletions.

#### Function details

##### `get_path`  (lines 36–45)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a value from a nested dictionary using a dotted path such as `author.id`. This lets connector definitions point to IDs or dates that are buried inside provider records.

**Data flow**: It receives a mapping, a dotted path, and an optional default value. It walks through the mapping one path part at a time; if any step is missing or not a mapping, it returns the default. If the full path exists, it returns the found value.

**Call relations**: This is a small helper used by `record_key` when the primary key is not a simple top-level field. It gives connector records one consistent way to look up nested identities.

*Call graph*: called by 1 (record_key).


##### `record_key`  (lines 48–60)

```
def record_key(record: Mapping[str, Any], primary_key: str) -> str | None
```

**Purpose**: Finds the stable provider ID for a record. This ID is what lets UFO recognize that the same outside record is still the same record across sync runs.

**Data flow**: It receives one record and the name of the primary key field. It first checks for that field directly, then uses `get_path` for dotted nested paths. It accepts strings and integers, rejects booleans and other shapes, and returns the ID as a string or `None` if no usable ID exists.

**Call relations**: `Connector.record_identity` calls this to get a record’s durable identity. By relying on this instead of hashing the whole record, UFO avoids creating a new page every time a provider changes some unrelated field.

*Call graph*: calls 1 internal fn (get_path); called by 1 (record_identity).


##### `PartitionWalk.stream`  (lines 261–289)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Runs a sync across many partitions while keeping a single encoded cursor for the whole stream. It is the top-level walk that decides which partition to visit, how to resume it, and when to publish a new checkpoint.

**Data flow**: It receives the previously stored cursor string. It decodes that cursor into a partition map, asks the connector for partitions, and streams pages from each partition using either ordered or unordered logic. As it goes, it yields `StreamPage` objects whose `next_cursor` contains updated progress; at the end it may yield one final empty page to prune or clear completed state.

**Call relations**: This method calls `_decode` at the start, then delegates each partition to `_stream_unordered` or `_stream_ordered` depending on the declared ordering. It uses `_encode` whenever it needs to hand the outside sync driver a new cursor.

*Call graph*: calls 4 internal fn (_decode, _encode, _stream_ordered, _stream_unordered); 1 external calls (__init__).


##### `PartitionWalk._stream_unordered`  (lines 291–313)

```
async def _stream_unordered(self, partition: str, stored: dict[str, str | _Window], checkpoint: dict[str, str | _Window]) -> AsyncIterator[StreamPage]
```

**Purpose**: Streams one partition when there is no usable date or cursor field inside its records. In this mode, the safest option is to finish a partition, mark it done for this pass, and fully reread it on a later complete pass.

**Data flow**: It receives a partition name, the stored partition state, and the mutable checkpoint map. If the partition is already marked in the stored state, it skips it. Otherwise it asks the page factory for pages with an empty `PartitionBound`, yields those records as `StreamPage`s, and then marks the partition as completed in the checkpoint.

**Call relations**: `PartitionWalk.stream` calls this for streams declared with no ordering. It calls `_encode` to attach the current checkpoint to yielded pages, and it creates `PartitionBound` and `StreamPage` objects to speak the common sync format.

*Call graph*: calls 1 internal fn (_encode); called by 1 (stream); 2 external calls (__init__, __init__).


##### `PartitionWalk._stream_ordered`  (lines 315–361)

```
async def _stream_ordered(self, partition: str, stored: dict[str, str | _Window], checkpoint: dict[str, str | _Window]) -> AsyncIterator[StreamPage]
```

**Purpose**: Streams one partition when records have an ordering value, such as a timestamp. It updates watermarks so later runs can fetch only newer records or continue a descending backfill safely.

**Data flow**: It receives a partition name plus stored and current checkpoint maps. It asks `_ordered_state` what bounds to use, then reads pages from the page factory. For each page it updates the highest and sometimes lowest seen cursor values, stores either a plain watermark or an in-progress backfill window, yields a `StreamPage`, and stops when it has safely reached the backfill floor or old synced data.

**Call relations**: `PartitionWalk.stream` calls this for ordered partitioned streams. It relies on `_ordered_state` to translate saved cursor state into request bounds, uses `_encode` to publish progress, and creates `_Window` values when a newest-first backfill is only partly complete.

*Call graph*: calls 2 internal fn (_encode, _ordered_state); called by 1 (stream); 2 external calls (__init__, __init__).


##### `PartitionWalk._ordered_state`  (lines 363–379)

```
def _ordered_state(self, stored: str | _Window | None) -> tuple[PartitionBound, str | None, str | None, str | None, bool]
```

**Purpose**: Turns one partition’s saved cursor entry into the starting instructions for the next fetch. It decides whether the partition is continuing a backfill, doing a normal incremental update, or starting fresh.

**Data flow**: It receives a saved value that may be missing, a plain watermark string, or an in-progress window. It returns a `PartitionBound` plus the current high value, low/until value, synced watermark, and a flag saying whether this is a backfill.

**Call relations**: `_stream_ordered` calls this before asking the connector for pages. The returned `PartitionBound` tells the connector whether to fetch after a watermark, before an inclusive boundary, or from an initial floor.

*Call graph*: called by 1 (_stream_ordered); 1 external calls (__init__).


##### `PartitionWalk._decode`  (lines 382–411)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: Reads the stored cursor string for a partitioned walk and turns it back into a usable map. It also protects the sync from silently continuing with corrupted partition state.

**Data flow**: It receives a cursor string or `None`. Empty, invalid JSON, or non-object JSON becomes an empty map, meaning the walk starts fresh. A valid JSON object is checked entry by entry; string values become watermarks, valid window objects become `_Window` instances, and malformed values raise an error.

**Call relations**: `PartitionWalk.stream` calls this once at the beginning of a walk. Its output becomes the stored state that `_stream_unordered` and `_stream_ordered` use to decide what to skip or resume.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 414–419)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: Turns the in-memory partition progress map into the JSON cursor string that can be stored between sync runs.

**Data flow**: It receives a mapping from partition names to either watermark strings or `_Window` objects. It converts window objects into plain dictionaries and serializes the whole map as sorted JSON. The result is a stable cursor string.

**Call relations**: `PartitionWalk.stream`, `_stream_unordered`, and `_stream_ordered` call this whenever they need to attach updated progress to a `StreamPage`. That cursor is what the larger sync system saves and passes back on the next run.

*Call graph*: called by 3 (_stream_ordered, _stream_unordered, stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 438–439)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Declares which streams a connector knows how to sync. Each stream describes one provider-side collection and the fields UFO needs to identify and resume records.

**Data flow**: An implementation takes no runtime data and returns a list of `StreamSpec` objects. Those specs tell the rest of the system names, primary keys, cursor fields, deletion behavior, pagination style, and optional backfill windows.

**Call relations**: Connector implementations provide this method, and `ConnectedSources._register` calls it during source registration. That registration step is how the system learns what a provider can offer before any sync run starts.

*Call graph*: called by 1 (_register).


##### `Connector.fetch_page`  (lines 442–457)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None) -> AsyncIterator[list[dict[str, Any]]
```

**Purpose**: Defines the required method that actually fetches records from an outside provider. Every concrete connector must implement it for its provider’s API.

**Data flow**: It receives the stream to fetch, the previous cursor, a resolved credential, the base URL to contact, an optional current user ID to exclude, and an optional backfill floor. It asynchronously yields batches of records, either as plain lists or richer `StreamPage` objects that can include deletes and a next cursor.

**Call relations**: This is an abstract contract rather than working code in this file. The source sync runner expects concrete connectors to implement it, and the pages it yields are later rendered and collapsed into the project’s normal sync result.


##### `Connector.render`  (lines 459–479)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns one raw provider record into a title and readable body for recall. The default version is generic, while content-heavy connectors can override it to produce nicer prose.

**Data flow**: It receives a record and its stream spec. It looks for a human-friendly title field such as `title`, `name`, or `subject`; if none exists, it asks `record_identity` and `record_ref` for a fallback identity. It returns a title plus a Markdown-like body containing a heading and the record JSON.

**Call relations**: The connector backend uses this kind of method when landing fetched records as recallable pages. Inside this default implementation, `record_identity` supplies a stable provider identity, `record_ref` supplies a source-side page reference, and `json.dumps` turns the raw record into text.

*Call graph*: calls 2 internal fn (record_identity, record_ref); 1 external calls (dumps).


##### `Connector.record_identity`  (lines 481–483)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Returns the stable provider identity for one record. This is the value UFO uses to know that two fetched records refer to the same outside item.

**Data flow**: It receives a record and stream spec, then passes the record and the stream’s primary key to `record_key`. It returns the resulting string ID or `None` if the record does not contain a usable identity.

**Call relations**: `Connector.render` calls this when it needs an identity for a record without a natural title. The method centralizes identity lookup so connector subclasses can override it if a provider needs special rules.

*Call graph*: calls 1 internal fn (record_key); called by 1 (render).


##### `Connector.record_ref`  (lines 485–492)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Builds the source-side reference used when creating a page row for a record. It prefers the declared primary key, but can fall back to a content hash if the direct field is not a simple value.

**Data flow**: It receives a record and stream spec. If the primary key field is a string or integer, it returns it as text; if it is a boolean, it returns `None`; otherwise it serializes the whole record in a stable order and hashes it with SHA-256 to create a deterministic reference.

**Call relations**: `Connector.render` calls this when it needs a fallback page reference for a title. Its hash fallback keeps the default renderer usable for unusual records, though stable provider IDs from `record_identity` are still preferred for long-term identity.

*Call graph*: called by 1 (render); 2 external calls (sha256, dumps).


### Connector catalog
Maps public source names to the connector implementations available to the system.

### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup`

This file solves a simple but important problem: when a source row says its backend is, for example, "slack" or "github", the system needs a reliable way to find the matching connector code. Instead of searching the whole codebase at startup, this file imports every supported connector and lists them explicitly in one place. That is like keeping a printed phone directory at the front desk instead of asking every department whether they exist each morning.

The main result is `CONNECTORS`, a dictionary whose keys are connector names and whose values are the connector classes. A connector class is the piece of code that knows how to talk to one outside service and pull data from it. The file also defines `SOURCE_KIND = "source"`, which names this group of objects as source integrations.

The important safety check is that two connectors cannot share the same name. If they did, a backend name could point to the wrong service or hide another connector. The helper function builds the registry and raises an error immediately if it sees a duplicate. Without this file, the sync system would not have a central, predictable catalog of available source backends.

#### Function details

##### `_connector_registry`  (lines 66–74)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: This function builds the connector lookup table used by the rest of the source system. It also protects the system from confusing duplicate connector names by failing early if two connector classes claim the same name.

**Data flow**: It receives a tuple of connector classes. For each class, it reads that class’s `name` value, uses it as the dictionary key, and stores the class as the value. If a name has already been used, it raises a `ValueError` instead of returning a bad registry. If all names are unique, it returns the completed name-to-connector dictionary.

**Call relations**: This function is called in this file when `CONNECTORS` is created. The long explicit list of imported connector classes is handed to it, and it returns the registry that other source-sync code can later use to turn a backend name into the correct connector class.
