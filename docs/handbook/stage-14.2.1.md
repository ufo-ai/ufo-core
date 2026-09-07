# Core source sync runtime framework  `stage-14.2.1`

This stage is the main machinery that keeps outside content sources in step with UFO. It runs during the regular sync work loop, with some shared support code used by many connectors. A connector is a small adapter that knows how to talk to one outside system, such as a web app or file service.

connector.py defines the rules every connector must follow. It also provides tools for grouping incoming records into pages and for tracking cursors, which are bookmarks that say “continue from here next time,” even when a stream is split into many parts like projects or channels.

rest.py is the shared web-request layer for connectors that use REST APIs, meaning ordinary HTTP calls that return data such as JSON. It handles login, retries, response reading, and pagination.

backend.py bridges connector output into UFO’s internal format. It builds stable pages, tracks deletes, skips bad records safely, and chooses safe stopping points for large imports.

sync.py ties it all together. It fetches source documents, stores current bodies, records changes, removes deleted items, and provides a reliable change feed for indexers downstream.

## Files in this stage

### Connector ingestion bridge
Adapter code turns external connector stream records into UFO source-sync pages while managing cursors, deletes, drops, and stopping points.

### `core/src/ufo/runtime/sources/backend.py`

`orchestration` · `source sync run`

A connector knows how to talk to an outside service, such as a ticket system or code host. The rest of UFO expects a simpler result: a batch of pages, a cursor saying where to resume, and optional delete information. This file is the adapter between those two worlds.

The main class, ConnectorBackend, runs one configured account and one configured stream. First it asks the auth proxy for a Credential, so secrets stay in the protected jobs process and are not exposed to agents or logs. Then it finds the requested stream, chooses the correct base URL, and starts reading records from the connector.

Each provider record is converted into a Page, which is UFO's recallable unit of source content. Records without a usable primary key, or records that cannot be represented as a valid Page, are not allowed to break the whole run. They are warned about, counted as dropped, and the sync continues.

The file also protects workers from endless or oversized incremental runs. Incremental streams are capped at a fixed number of landed pages. If the connector gives a native cursor, that cursor is saved. If it does not, this adapter stores its own small JSON envelope with a skip count, like leaving a bookmark plus a note saying “skip the first 5,000 items next time.” Full snapshot streams that detect missing records are never capped, because stopping early could cause wrong deletions.

#### Function details

##### `binding_name`  (lines 100–110)

```
def binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: Builds a stable, human-ish name for a connector binding from the provider, account, and optional tenant URL. This matters because the same connected account should get the same object name wherever the system needs to refer to it.

**Data flow**: It takes a provider name, an account handle, and an optional base URL. It serializes those values in a consistent order, hashes them, keeps a short piece of the hash, and returns a name made from the provider plus that digest. The provider name has underscores changed to hyphens so the result is friendlier as an object name.

**Call relations**: This helper stands apart from the main fetch flow. Wherever the system needs to name a connector binding consistently, it can call this instead of inventing a name in a different way.

*Call graph*: 2 external calls (sha256, dumps).


##### `ConnectorBackend.fetch`  (lines 148–247)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one sync for one connector stream and returns UFO's standard SyncResult. It is the central routine that turns connector output into pages, delete notices, dropped-record counts, and the next cursor to store.

**Data flow**: It receives a source row config, the previously stored cursor, and source authentication context. It gets a credential, finds the requested stream, resolves the base URL, decodes any adapter-owned backfill cursor, and then reads records from the connector. Each record is either skipped because it was already counted in a previous capped run, converted into a Page, or counted as dropped. As it reads, it updates delete lists and checkpoint information. It finally returns a SyncResult containing the pages landed this run, the next resume cursor, whether this was a full snapshot, any deletes, and how many records were dropped.

**Call relations**: This is the file's main orchestration point. It calls _credential before any provider access, _stream to find the connector declaration, _base_url to know where the provider lives, _decode_cursor to understand UFO's own capped-backfill bookmarks, and _page for each record. When the run reaches the cap, it may create a _BackfillEnvelope and encode it with JSON so the next run can continue safely.

*Call graph*: calls 5 internal fn (_base_url, _credential, _decode_cursor, _page, _stream); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._credential`  (lines 249–263)

```
async def _credential(self, config: ConnectorSourceConfig, auth: SourceAuth) -> Credential
```

**Purpose**: Gets the credential needed to talk to the outside provider, without letting the raw secret leak into the connector source config. If the grant cannot be used yet, it turns that into a stream skip instead of an ordinary crash.

**Data flow**: It takes the source config and authentication context. It checks that an auth proxy is present, then asks that proxy for the credential for this workspace, connector, and account. If the proxy says the grant is unusable, it raises StreamSkipped with information about whether the system is waiting for a grant. Otherwise it returns the Credential object.

**Call relations**: fetch calls this near the start of every run. The returned credential is then handed to the connector's fetch_page call so provider access happens only after the protected auth path has approved it.

*Call graph*: calls 1 internal fn (__init__); called by 1 (fetch).


##### `ConnectorBackend._base_url`  (lines 265–274)

```
def _base_url(self, config: ConnectorSourceConfig) -> str
```

**Purpose**: Chooses the web address the connector should contact for this source row. This is important for providers where each customer has a different tenant URL.

**Data flow**: It reads the base URL from the source row first, and falls back to the connector's default base URL. If one exists, it returns it. If neither exists, it raises an error because dialing an empty or unknown host would hide a configuration mistake.

**Call relations**: fetch calls this before asking the connector for pages. The result is passed into the connector so the same connector class can work for both fixed-host providers and per-tenant providers.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._stream`  (lines 276–280)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream declaration with the requested name on the connector. A stream declaration explains how a specific kind of provider data should be synced.

**Data flow**: It receives a stream name and loops through the connector's available stream specs. If it finds a matching name, it returns that StreamSpec. If not, it raises an error saying the connector has no such stream.

**Call relations**: fetch calls this before reading provider data. The returned stream spec guides later choices, such as whether this is a full snapshot stream, what primary key to use, and which timestamp fields to read.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 283–300)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Recognizes UFO's own capped-backfill cursor envelope, while leaving all connector-owned cursors untouched. This lets the adapter store extra resume state without confusing provider cursors with adapter cursors.

**Data flow**: It receives the stored cursor string, or None. If there is no cursor, if the cursor is not JSON, or if the JSON does not contain the reserved ufo_backfill key, it returns None. If the reserved key is present, it validates the contents as a backfill envelope and returns that model. If the envelope is malformed, it raises an error because this adapter is supposed to be the only writer of that format.

**Call relations**: fetch calls this only for incremental streams, not full delete-missing snapshots. If it returns an envelope, fetch uses the saved origin cursor and skip count to re-read a stable prefix and continue a capped backfill.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 302–355)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page | None
```

**Purpose**: Converts one provider record into UFO's Page format, or safely drops it if it cannot become a valid page. This prevents one bad provider record from blocking every later record in the stream.

**Data flow**: It takes a stream spec and one raw record. It asks the connector for the record's stable identity and display reference, renders the record into a title and body, and extracts created and updated timestamps. It then builds a Page whose identity is namespaced by the stream name. If the record has no primary key, or if the Page model rejects the rendered data, it logs a warning and returns None instead of a page.

**Call relations**: fetch calls this for each record that is not being skipped by a capped-backfill resume. _page calls _record_timestamp for time fields and uses validation_fault to summarize page validation errors in warnings.

*Call graph*: calls 1 internal fn (_record_timestamp); called by 1 (fetch); 3 external calls (__init__, warn, validation_fault).


##### `_record_timestamp`  (lines 358–389)

```
def _record_timestamp(record: dict[str, Any], field: str | None, *, connector: str, stream: str) -> str | None
```

**Purpose**: Reads and normalizes one timestamp field from a provider record. It returns a clean timestamp string when possible and quietly rejects malformed timestamp values with a warning.

**Data flow**: It receives a record, the field name or path to read, and connector and stream names for warning messages. If no field is configured or the value is missing, it returns None. If the value is a string or a non-boolean integer, it passes it through the shared timestamp normalizer. If normalization fails, or if the value has an unsupported type, it warns and returns None.

**Call relations**: _page calls this for the stream's created-at and updated-at fields while building a Page. It uses get_path when the timestamp field is nested inside the record and normalize_page_timestamp to put accepted timestamps into the common format expected by the source system.

*Call graph*: called by 1 (_page); 3 external calls (warn, get_path, normalize_page_timestamp).


### REST connector foundation
Shared REST transport and connector contracts define how external APIs are authenticated, paginated, normalized, and partition-cursor tracked.

### `core/src/ufo/runtime/sources/rest.py`

`io_transport` · `source fetch and pagination`

Many outside services do not return all records in one response. They return a page of records plus some hint for the next page, such as a cursor token, a next-link header, an offset number, or an OData next URL. This file is the reusable machinery that lets source connectors fetch those pages safely and consistently instead of each connector rewriting the same fragile code.

The central class is RestConnector. It builds an HTTP client from a credential, sends GET or POST requests, retries temporary failures, and turns successful responses into plain Python dictionaries and lists. Think of it like a careful postal worker: it addresses each request, waits and tries again if the delivery route is temporarily blocked, rejects bad replies with useful details, then sorts the returned package into records.

The file also protects the system from bad API behavior. It stops pagination loops that run forever, detects repeated cursor tokens, ignores malformed record shapes, and bounds retry delays so one stuck request cannot pause the whole sync for too long. Finally, fetch_page is the public flow that connector code uses: it opens the client, asks a pagination method for raw pages, validates that each page is a list of record dictionaries, optionally flattens nested records, and yields clean pages downstream.

#### Function details

##### `list_or_empty`  (lines 50–54)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: Returns only dictionary items from a value when that value is a list. It is a safe way to treat uncertain API data as a list of records without crashing on unexpected shapes.

**Data flow**: It receives any value. If the value is not a list, it returns an empty list. If it is a list, it keeps only the items that are dictionaries and returns those as the record list.

**Call relations**: Pagination helpers call this when they have found the part of a response that should contain records. It supports records_at, _response_list, and OData pagination by making malformed or missing record arrays become an empty result instead of a type error.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 57–60)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Returns a value only if it is a dictionary-shaped record. It is used for safely reaching into nested objects where an API may or may not return the expected object.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged. Otherwise it returns an empty dictionary.

**Call relations**: This is a small safety helper for connector code that needs a dictionary but is reading from untrusted API responses. It is the single-record sibling of list_or_empty.


##### `records_at`  (lines 63–68)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: Finds the list of records inside an API response, optionally at a named nested path. This lets one pagination routine work with APIs that put records at different places in their JSON.

**Data flow**: It receives response data and an optional path. If no path is given, it treats the whole data value as the possible record list. If a path is given, it first requires the response to be dictionary-like, then follows the path and filters the result through list_or_empty. The output is always a list of record dictionaries.

**Call relations**: Cursor, offset, page-number, and strategy-based pagination call this after each response arrives. It delegates path lookup to get_path and record-shape cleanup to list_or_empty.

*Call graph*: calls 1 internal fn (list_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, parse); 1 external calls (get_path).


##### `_int_or_none`  (lines 71–76)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: Converts a value into an integer only when it is clearly safe to do so. It helps pagination advance by a server-reported page size without guessing.

**Data flow**: It receives any value. If the value is already an integer, it returns it. If it is a decimal string such as "100", it converts and returns 100. Otherwise it returns None.

**Call relations**: Offset pagination uses this when an API response says what limit it actually applied. If conversion fails, the caller falls back to other safe step sizes.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 79–82)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: Copies each record and adds extra context fields, such as an account, cloud, or parent identifier. This preserves where a record came from when later steps split or transform data.

**Data flow**: It receives an iterable of record dictionaries plus named context values. For each record, it creates a new dictionary containing the original fields and the context fields. It returns the new list and does not mutate the original records.

**Call relations**: Connector-specific code can use this before handing records to later fan-out or rendering steps. It acts as a stamp on each record, like writing the source folder name on every copied document.


##### `next_link`  (lines 85–90)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: Reads an HTTP Link header and extracts the URL marked as the next page. This supports APIs that advertise pagination through response headers instead of the response body.

**Data flow**: It receives HTTP response headers. It looks for the link header; if it is missing or does not contain a rel="next" style match, it returns None. If a next link is present, it returns that URL string.

**Call relations**: _get_link_header_pages calls this after each page to decide whether another request is needed. The result becomes the next path fetched by _get_raw.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 93–98)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: Decides whether a failed request is worth trying again. It separates temporary failures from permanent ones so the connector does not endlessly retry bad requests.

**Data flow**: It receives an exception. Network transport errors are treated as retryable. HTTP errors are retryable only when their status code is in the configured retryable set. Other errors return false.

**Call relations**: _send calls this after a request fails. If it returns true and retry limits allow it, _send waits and tries again; otherwise the error is raised.

*Call graph*: called by 1 (_send).


##### `_retry_after`  (lines 101–121)

```
def _retry_after(error: BaseException) -> float | None
```

**Purpose**: Reads a Retry-After header from certain temporary HTTP errors and turns it into a safe number of seconds to wait. This respects API rate-limit instructions while rejecting unsafe values like negative numbers or NaN.

**Data flow**: It receives an exception. If the exception is not an HTTP status error, or the status is not one that can sensibly include Retry-After, it returns None. If the header is present and can be parsed as a finite non-negative number, it returns that number; otherwise it returns None.

**Call relations**: _retry_wait calls this while calculating the next pause before retrying. It gives provider-supplied backoff advice a chance to influence the wait.

*Call graph*: called by 1 (_retry_wait); 1 external calls (isfinite).


##### `_retry_wait`  (lines 124–141)

```
def _retry_wait(error: BaseException, delay: float) -> float
```

**Purpose**: Chooses how long to sleep before retrying a failed request. It combines exponential backoff, provider Retry-After advice, maximum caps, and random jitter so many workers do not all retry at the same instant.

**Data flow**: It receives the error that happened and the current base delay. It checks for a usable Retry-After value, clamps both that value and the normal delay to safe limits, chooses the larger relevant floor, then returns a random wait time between that floor and an allowed cap.

**Call relations**: _send calls this after a retryable failure. It uses _retry_after to honor server guidance, and its returned seconds are passed to asyncio.sleep.

*Call graph*: calls 1 internal fn (_retry_after); called by 1 (_send); 1 external calls (uniform).


##### `_raise_for_status`  (lines 144–155)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: Raises a useful error when an HTTP response is not successful. Unlike the default behavior, it includes a bounded slice of the response body, where APIs often explain the real problem.

**Data flow**: It receives an HTTP response. If the response is successful, it does nothing. Otherwise it reads a limited amount of response text and raises an HTTPStatusError containing the status, request method, URL, and body snippet.

**Call relations**: _send calls this immediately after each HTTP response. A raised error then flows into the retry decision logic, where temporary statuses may be retried and permanent ones are surfaced.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 158–162)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: Turns an HTTP response into a JSON dictionary, while treating empty responses as an empty dictionary. This avoids trying to parse JSON when an API legitimately returns no body.

**Data flow**: It receives an HTTP response. If the status is 204, meaning no content, or the body is empty, it returns {}. Otherwise it parses the response JSON and returns it as a dictionary.

**Call relations**: _get and _post use this after _send returns a successful response. It is the normal body parser for endpoints expected to return a JSON object.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 165–168)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Turns an HTTP response into a list of record dictionaries when the whole response body is expected to be an array. Empty responses become an empty list.

**Data flow**: It receives an HTTP response. If the response has no content, it returns an empty list. Otherwise it parses JSON and passes it through list_or_empty, keeping only dictionary items.

**Call relations**: _get_link_header_pages uses this by default when a link-header-paginated endpoint returns records as a top-level JSON array. Custom parsing can replace it when records live deeper in the body.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 171–177)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: Stops a pagination loop that has fetched too many pages without ending. This prevents a broken or hostile API from trapping the sync in an endless loop.

**Data flow**: It receives a human-readable label for the request and the current page count. If the count is within the configured maximum, it does nothing. If the count is too high, it raises a RuntimeError.

**Call relations**: All pagination loops call this once per page. When it raises, the fetch fails loudly instead of holding resources forever or committing a partial, suspicious result.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 180–186)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: Detects when a cursor or next-link repeats during pagination. A repeated token usually means the API is not moving forward and would return the same page forever.

**Data flow**: It receives a label, the latest token or next URL, and a set of already-seen tokens. If the token is already in the set, it raises a RuntimeError. Otherwise it adds the token to the set.

**Call relations**: Cursor-based, link-header, and OData pagination call this after discovering the next-page marker. It catches infinite loops earlier and more precisely than the page-count limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 195–196)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the list of streams this REST connector can read. A stream is a named collection of records, such as users, issues, or messages.

**Data flow**: It reads the connector's stored streams_list and returns a new list copy. The connector's original list is not handed out directly.

**Call relations**: Other parts of the source runtime can call this to discover what the connector offers before selecting and fetching individual streams.


##### `RestConnector._make_client`  (lines 198–215)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to one account's API. It applies the base URL, JSON headers, timeouts, and the chosen credential style.

**Data flow**: It receives a base URL and a resolved Credential. It trims the base URL, creates default JSON headers, and either attaches a proxy transport, a bearer token, or credential headers. If no usable authentication is present, it raises an error. The output is an httpx AsyncClient ready to send requests.

**Call relations**: fetch_page calls this at the start of a stream fetch. The returned client is then passed through pagination and request helpers until the fetch is finished and the async context closes it.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 217–220)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Sends a GET request and returns its JSON object body, with empty bodies represented as an empty dictionary. Use it for normal read endpoints where the response body is a JSON object.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It asks _get_raw to send the request with retry behavior, then passes the response to _json_or_empty. It returns the parsed dictionary.

**Call relations**: Cursor, offset, and page-number pagination call this for ordinary body-based pagination. It builds on _get_raw rather than duplicating send and retry logic.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 3 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages).


##### `RestConnector._get_raw`  (lines 222–226)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Sends a GET request and returns the full raw HTTP response. This is needed when pagination depends on headers or when callers need more than just the JSON body.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It creates a GET request function and passes it into _send. The result is a successful httpx Response or an exception if retries fail.

**Call relations**: _get wraps this for JSON-object responses. Link-header and OData pagination call it directly because they need headers or special response handling.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 228–233)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Sends a POST request for read-style API endpoints that require POST instead of GET. It still treats the call as a read and returns a JSON object body.

**Data flow**: It receives an HTTP client, a path, and an optional JSON request body. It sends the POST through _send so temporary failures are retried, then parses the response with _json_or_empty. The output is a dictionary.

**Call relations**: Connector-specific pagination or fetch code can use this for APIs like search endpoints that read data through POST. It shares the same retry envelope as GET requests.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 235–241)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Sends a POST request and returns the raw response. It is useful for read endpoints whose response is not a normal JSON object, such as a top-level array.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It sends the POST through _send and returns the successful raw response. Parsing is left to the caller.

**Call relations**: Connector-specific code can call this when _post would parse the body in the wrong shape. It still relies on _send for retry and error behavior.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 243–264)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: Wraps every HTTP request with consistent retry behavior. It retries temporary network or server failures, waits between attempts, and gives up when attempt or time budgets are exhausted.

**Data flow**: It receives a callable that, when invoked, performs one async HTTP request. For each attempt, it awaits the response, raises for bad statuses, and returns the response if successful. On retryable transport or HTTP status errors, it computes a wait, sleeps, increases the delay, and tries again. If the error is not retryable or limits are reached, it raises the error.

**Call relations**: _get_raw, _post, and _post_raw all route through this method. It calls _raise_for_status, _is_retryable, _retry_wait, and asyncio.sleep to make request handling uniform across all REST connectors.

*Call graph*: calls 3 internal fn (_is_retryable, _raise_for_status, _retry_wait); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 266–305)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[
```

**Purpose**: This is the main fetch flow for a REST stream. It opens the HTTP client, runs pagination, validates and flattens records, and yields clean pages for downstream syncing.

**Data flow**: It receives a stream description, cursor, credential, base URL information, optional user identity, and optional backfill time. It chooses the base URL, creates an authenticated client, asks paginate_source for pages, skips empty pages, validates that each page is a list of dictionaries, flattens each record, and yields either a plain record list or a StreamPage preserving deletes and next-cursor metadata. At the end it closes any async generator source.

**Call relations**: The runtime calls this when it needs records from a REST-backed stream. It coordinates _make_client, paginate_source, _validate_page, flatten, and StreamPage construction, making it the bridge between low-level HTTP fetching and the rest of the sync pipeline.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate_source); 1 external calls (__init__).


##### `RestConnector.paginate_source`  (lines 307–320)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides a small extension point between the standard fetch flow and a connector's pagination method. It lets specialized connectors accept extra run-time information without forcing every connector to use it.

**Data flow**: It receives the HTTP client, stream, cursor, acting user id, and optional backfill time. The default implementation ignores the extra identity and backfill values and simply returns paginate(client, stream, cursor=cursor).

**Call relations**: fetch_page calls this instead of calling paginate directly. Connectors that need self_user_id or backfill_after can override this method while ordinary connectors keep the default path.

*Call graph*: calls 1 internal fn (paginate); called by 1 (fetch_page).


##### `RestConnector.paginate`  (lines 322–334)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Produces raw pages of records for a stream. The default implementation works only when the stream declares one of the built-in pagination strategies.

**Data flow**: It receives an HTTP client, stream, and cursor. It checks the stream's pagination settings. If no usable strategy is declared, it raises NotImplementedError so the connector author knows to provide custom logic. Otherwise it yields pages from paginate_from_strategy.

**Call relations**: paginate_source calls this by default. It hands strategy-based streams to paginate_from_strategy and leaves unusual API shapes to connector-specific overrides.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (paginate_source).


##### `RestConnector.paginate_from_strategy`  (lines 336–398)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs the built-in pagination loop declared by a stream's Pagination settings. It turns configuration like "cursor pagination" or "offset pagination" into actual repeated HTTP requests.

**Data flow**: It reads the stream's pagination specification, resolves the request path, checks that required settings are present, and dispatches to the matching helper. For next-cursor pagination it follows response-body cursor tokens. For next-link pagination it follows Link headers. For offset-limit pagination it increases offsets. It yields each list of record dictionaries it receives from the helper.

**Call relations**: paginate calls this for streams that use standard pagination. It calls _strategy_path when the path is not directly configured, and then hands off to _get_cursor_pages, _get_link_header_pages, or _get_offset_pages depending on the declared strategy.

*Call graph*: calls 4 internal fn (_get_cursor_pages, _get_link_header_pages, _get_offset_pages, _strategy_path); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 368–370)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Extracts records from a link-header-paginated response when the records are nested inside the JSON body. It is a small custom parser created only for that pagination run.

**Data flow**: It receives an HTTP response. If there is content, it parses the JSON body; otherwise it uses an empty dictionary. It then calls records_at with the configured record path and returns the resulting list of record dictionaries.

**Call relations**: paginate_from_strategy creates this function for next-link pagination when a record_path is provided. It passes the parser into _get_link_header_pages so that helper can follow headers while still reading records from the right body location.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 400–428)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: Fetches pages from APIs that put the next-page URL in the HTTP Link header. This is common in REST APIs that keep pagination metadata outside the JSON body.

**Data flow**: It receives a client, starting path, optional query parameters, optional page-size settings, and an optional record parser. It fetches the first response, yields records from each response, reads the next URL from headers, checks page and cursor bounds, and continues until no next link is present.

**Call relations**: paginate_from_strategy calls this for the next_link strategy. It uses _get_raw to fetch responses, _response_list or a supplied parser to read records, next_link to find the following URL, and the bound helpers to prevent endless loops.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 430–462)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: Fetches pages from APIs whose response body contains a cursor token for the next request. A cursor is like a bookmark the server gives back so the client can continue where it left off.

**Data flow**: It receives a client, path, record path, next-cursor path, query parameter names, page size, and extra parameters. It repeatedly builds query parameters, adds the current cursor when present, sends a GET, extracts records, yields them, reads the next cursor from the body, and stops when no valid cursor remains.

**Call relations**: paginate_from_strategy calls this for the next_cursor strategy. It uses _get for requests, records_at for record extraction, get_path for cursor lookup, and bound helpers to stop runaway pagination.

*Call graph*: calls 4 internal fn (_get, _bound_cursor, _bound_pages, records_at); called by 1 (paginate_from_strategy); 1 external calls (get_path).


##### `RestConnector._get_odata_pages`  (lines 464–490)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Microsoft Graph or OData-style pages, where records are under a value field and the continuation URL is stored in @odata.nextLink. OData is a common Microsoft-style API format.

**Data flow**: It receives a client, starting path, and optional parameters. It sends the first request with those parameters, reads records from the value array, yields them, then follows @odata.nextLink as the next request path. Later requests do not reuse the original parameters because the next-link already contains them.

**Call relations**: Connector-specific code can use this for OData APIs. It relies on _get_raw for HTTP, list_or_empty for record cleanup, and bound helpers to prevent too many pages or repeated next links.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 492–532)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: Fetches pages from APIs that use offset and limit numbers. In this style, the client asks for records starting at position 0, then 100, then 200, and so on.

**Data flow**: It receives a client, path, record path, limit, parameter names, optional extra parameters, and optional response paths for continuation details. It repeatedly sends GET requests with the current offset and limit, extracts records, yields them, and decides whether to stop based on empty records, a server-provided more flag, or a short page. It advances the offset by the configured limit or by a server-reported applied limit when available.

**Call relations**: paginate_from_strategy calls this for the offset_limit strategy. It uses _get for requests, records_at for records, get_path for optional server metadata, _int_or_none for safe numeric conversion, and _bound_pages for loop safety.

*Call graph*: calls 4 internal fn (_get, _bound_pages, _int_or_none, records_at); called by 1 (paginate_from_strategy); 1 external calls (get_path).


##### `RestConnector._get_page_number_pages`  (lines 534–563)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: Fetches pages from APIs that use page numbers, such as page=1, page=2, and so on. It stops when a page contains fewer records than the requested page size.

**Data flow**: It receives a client, path, record path, page size, optional parameters, parameter names, and a starting page. It builds query parameters for the current page, sends a GET, extracts records, yields them when present, and increments the page number. If the returned record count is smaller than the page size, it returns.

**Call relations**: Connector-specific code can use this helper for page-number APIs. It shares _get, records_at, and _bound_pages with the other pagination styles.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at).


##### `RestConnector._strategy_path`  (lines 565–571)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: Resolves the request path for a strategy-based stream when the Pagination object does not include one. The base implementation deliberately fails so connector authors provide the missing mapping.

**Data flow**: It receives a stream description. Instead of returning a path, the default method raises NotImplementedError with a message naming the connector and stream.

**Call relations**: paginate_from_strategy calls this only when the stream's pagination strategy needs a path and none is configured. Connectors with their own stream-to-path table override it.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 573–576)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Converts one fetched record into the flat dictionary shape that the sync writer expects. The default does nothing because many APIs already return usable flat records.

**Data flow**: It receives a record dictionary and its stream description. It returns the same record unchanged. Overrides may copy fields out of nested structures or rename them.

**Call relations**: fetch_page calls this for every validated record before yielding it downstream. Connector subclasses override it when their API payloads need reshaping.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 578–589)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: Checks that a pagination method returned the expected shape: a list of dictionary records. This catches connector mistakes early with a clear error message.

**Data flow**: It receives a page value and the stream description. If the page is not a list, it raises a TypeError. If any item inside the list is not a dictionary, it raises a TypeError naming the bad item type. If everything is valid, it returns nothing and changes nothing.

**Call relations**: fetch_page calls this before flattening or yielding records. It protects the downstream sync code from surprising data shapes produced by custom pagination methods.

*Call graph*: called by 1 (fetch_page).


### `core/src/ufo/runtime/sources/connector.py`

`domain_logic` · `cross-cutting during source registration and sync runs`

A connector is the project’s adapter for an outside service, such as email, documents, chat, or a code host. This file gives all connectors a shared language: a stream is a collection to sync, a record is one item from that collection, and a page is a batch of records plus optional delete and resume information. Without this common shape, every provider would need its own special path through the sync system.

The small data classes describe what a stream looks like, how it is paginated, how records are ordered, and what cursor should be saved so the next run can continue safely. A cursor is like a bookmark in a long book: it says where syncing stopped.

The most involved part is PartitionWalk. Some providers require walking many separate buckets, for example one channel at a time. PartitionWalk keeps a separate bookmark for each bucket while still presenting one combined cursor to the rest of the system. It is careful about two difficult cases: feeds that arrive oldest-first, and feeds that arrive newest-first. For newest-first feeds, it avoids losing records when new items appear at the top while an older backfill is still in progress.

The Connector base class is the contract provider-specific connectors implement. It says which streams exist, how to fetch pages, how to checkpoint progress, and how to turn one raw record into readable text for recall.

#### Function details

##### `get_path`  (lines 36–45)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a value from a nested dictionary using a dotted path such as "author.id". This lets stream definitions refer to IDs or timestamps that are not at the top level of a provider record.

**Data flow**: It receives a mapping, a dotted path, and an optional fallback value. It walks through the mapping one path part at a time; if any step is missing or no longer points to a mapping, it returns the fallback. If the full path exists, it returns the value found there.

**Call relations**: record_key uses this when a stream’s primary key is not a simple top-level field. In that flow, get_path is the backup lookup after checking the record directly.

*Call graph*: called by 1 (record_key).


##### `record_key`  (lines 48–60)

```
def record_key(record: Mapping[str, Any], primary_key: str) -> str | None
```

**Purpose**: Finds the stable provider identity for one record. This identity is used to decide whether a later sync record is the same outside item as an earlier one.

**Data flow**: It receives a record and the stream’s primary key name. It first checks for that key directly, then tries it as a dotted path through nested data. It accepts strings and integers, rejects booleans and other shapes, and returns the identity as a non-empty string or returns nothing if no valid identity exists.

**Call relations**: Connector.record_identity calls this to apply the stream’s declared primary key rule. It relies on get_path for nested keys, so both flat and nested provider records can be treated consistently.

*Call graph*: calls 1 internal fn (get_path); called by 1 (record_identity).


##### `PartitionWalk.stream`  (lines 260–288)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Runs a partitioned stream and yields ordinary stream pages while keeping per-partition progress hidden inside one cursor. It is the main driver for syncing sources that must be visited bucket by bucket.

**Data flow**: It receives the previously saved cursor as text. It decodes that cursor into a partition map, asks for the list of partitions, then walks each partition using either ordered or unordered rules. As pages come back, it yields StreamPage objects with records, deletes, and an updated encoded cursor. When the full pass finishes, it may emit a final empty page to prune or clear completed cursor state.

**Call relations**: This is the public entry into PartitionWalk. It calls _decode at the start, chooses _stream_unordered or _stream_ordered for each partition, and uses _encode when it needs to publish changed progress back to the sync system.

*Call graph*: calls 4 internal fn (_decode, _encode, _stream_ordered, _stream_unordered); 1 external calls (__init__).


##### `PartitionWalk._stream_unordered`  (lines 290–312)

```
async def _stream_unordered(self, partition: str, stored: dict[str, str | _Window], checkpoint: dict[str, str | _Window]) -> AsyncIterator[StreamPage]
```

**Purpose**: Walks one partition for a stream that has no usable ordering field. Because there is no timestamp or cursor value to resume from, it only remembers which partitions have already been finished within the current pass.

**Data flow**: It receives a partition name, the cursor map loaded at the start, and the mutable checkpoint map being built. If the partition was already marked, it skips it. Otherwise it asks the page factory for pages with an empty PartitionBound, yields each page with the current encoded checkpoint, and then marks the partition as done. If the provider says the partition should be skipped, it leaves the checkpoint unchanged for that partition.

**Call relations**: PartitionWalk.stream calls this when the stream ordering is none. This helper creates the partition boundary, wraps each provider WalkPage as a StreamPage, and calls _encode whenever progress must be saved.

*Call graph*: calls 1 internal fn (_encode); called by 1 (stream); 2 external calls (__init__, __init__).


##### `PartitionWalk._stream_ordered`  (lines 314–360)

```
async def _stream_ordered(self, partition: str, stored: dict[str, str | _Window], checkpoint: dict[str, str | _Window]) -> AsyncIterator[StreamPage]
```

**Purpose**: Walks one partition when records have an ordering value, such as a timestamp. It updates watermarks so later runs can fetch only the records that might be new or unfinished.

**Data flow**: It starts by asking _ordered_state how this partition should resume. It then asks the page factory for bounded pages. For each page, it updates the highest and sometimes lowest ordering values seen, stores either a plain watermark or an in-progress backfill window, and yields a StreamPage with the encoded checkpoint. It may stop early when older pages are safely below the synced watermark, or when a first backfill reaches its floor. At the end of a backfill, it collapses the temporary window into a plain watermark.

**Call relations**: PartitionWalk.stream calls this for ascending and newest-first streams. It depends on _ordered_state to translate saved cursor state into a provider-facing PartitionBound, and it uses _encode to hand updated progress back with each yielded page.

*Call graph*: calls 2 internal fn (_encode, _ordered_state); called by 1 (stream); 2 external calls (__init__, __init__).


##### `PartitionWalk._ordered_state`  (lines 362–378)

```
def _ordered_state(self, stored: str | _Window | None) -> tuple[PartitionBound, str | None, str | None, str | None, bool]
```

**Purpose**: Interprets the saved state for one ordered partition and decides how the next provider request should be bounded. It turns stored cursor data into a clear instruction such as “fetch after this value” or “continue downward before this value.”

**Data flow**: It receives one partition’s saved value, which may be absent, a plain watermark string, or an in-progress backfill window. It returns five pieces: the PartitionBound to pass to the connector, the current high value, the current lower backfill value, the synced watermark, and whether this is a backfill walk.

**Call relations**: _stream_ordered calls this before it asks the connector’s page factory for data. The result tells that later flow whether it is doing steady-state syncing or continuing an unfinished newest-first backfill.

*Call graph*: called by 1 (_stream_ordered); 1 external calls (__init__).


##### `PartitionWalk._decode`  (lines 381–410)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: Turns the saved cursor text for a partitioned stream back into a usable per-partition map. It also protects the sync from corrupted cursor entries that this walker would not know how to use safely.

**Data flow**: It receives cursor text or nothing. Empty, invalid JSON, or JSON that is not an object becomes an empty map, meaning the partitions will be walked fresh. For each object entry, it accepts either a plain string watermark or a valid window containing high and until values. Bad entries raise an error instead of silently skipping data.

**Call relations**: PartitionWalk.stream calls this once at the beginning of a walk. The decoded map is then shared with _stream_unordered or _stream_ordered so they can resume each partition correctly.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 413–418)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: Turns the per-partition cursor map into stable JSON text that can be saved as the stream cursor. This is the inverse of _decode.

**Data flow**: It receives a mapping from partition names to either plain watermark strings or window objects. It converts window objects into ordinary dictionaries, then serializes the whole map as sorted JSON text. The result is the cursor string carried on StreamPage.next_cursor.

**Call relations**: PartitionWalk.stream, _stream_unordered, and _stream_ordered call this whenever they need to publish the latest checkpoint. The sync system can then store that text and provide it back on the next run.

*Call graph*: called by 3 (_stream_ordered, _stream_unordered, stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 437–438)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Declares which streams a connector can sync. A provider connector implements this to advertise its available collections, such as messages, documents, issues, or users.

**Data flow**: It takes the connector instance and returns a list of StreamSpec objects. Each StreamSpec tells the system the stream name, source object, primary key, cursor rules, deletion behavior, and related sync settings.

**Call relations**: ConnectedSources._register calls this when a connector is registered, so the project can know what streams exist before any actual data is fetched. Provider-specific connector classes supply the real implementation.

*Call graph*: called by 1 (_register).


##### `Connector.fetch_page`  (lines 441–456)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None) -> AsyncIterator[list[dict[str, Any]]
```

**Purpose**: Defines the contract for fetching records from an outside provider, one page at a time. Each concrete connector implements this with the provider’s actual API calls.

**Data flow**: It receives the stream to fetch, the prior cursor, resolved credentials, the base URL, an optional current user ID to exclude where needed, and an optional backfill floor. It asynchronously yields either simple lists of record dictionaries or StreamPage objects that can also include deletes and a provider-supplied next cursor.

**Call relations**: This abstract method is the point where provider-specific code plugs into the shared source sync flow. The rest of the connector framework expects implementations to yield pages in order and to include enough cursor information for checkpointing.


##### `Connector.checkpoint`  (lines 458–463)

```
def checkpoint(self, stream: StreamSpec, records: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: Returns the cursor that should be saved after a page of records has been accepted. The base behavior simply preserves the cursor already supplied by the page.

**Data flow**: It receives the stream, the records just accepted, and the current cursor value. It returns that cursor unchanged. A connector can override this if it needs to compute progress from the records themselves instead of relying on a page cursor.

**Call relations**: This method sits between fetching records and saving sync progress. The default is intentionally simple, while specialized connectors can replace it when their provider’s cursor rules require extra logic.


##### `Connector.render`  (lines 465–485)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns one raw provider record into a readable title and body for recall. This gives even plain structured data a human-readable page representation.

**Data flow**: It receives a record and its stream. It looks for a title-like field such as title, name, login, or subject. If none exists, it asks record_identity for a stable identity and record_ref for a reference, then builds a fallback title. It returns a pair: the chosen title and a body containing a heading plus the record serialized as sorted JSON.

**Call relations**: This method calls Connector.record_identity and Connector.record_ref when it needs a fallback title, and it uses JSON serialization to produce the default body. Content-heavy connectors can override it to render cleaner prose, such as an email body or document text.

*Call graph*: calls 2 internal fn (record_identity, record_ref); 1 external calls (dumps).


##### `Connector.record_identity`  (lines 487–489)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Gets the stable outside identity for a record according to the stream’s primary key. This identity is used to recognize the same provider item across sync runs.

**Data flow**: It receives a record and stream. It passes the record and the stream’s primary key to record_key, then returns the valid string identity or nothing if the record cannot provide one.

**Call relations**: Connector.render calls this when it needs a fallback title. It delegates the actual lookup and validation rules to record_key so identity handling stays consistent across connectors.

*Call graph*: calls 1 internal fn (record_key); called by 1 (render).


##### `Connector.record_ref`  (lines 491–498)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Builds the source-side reference used when a page row is first created. It prefers the provider’s primary key when it is simple and safe, but can fall back to a hash of the whole record.

**Data flow**: It receives a record and stream. If the primary key value is a string or integer, it returns that value as text; if it is a boolean, it rejects it. For other shapes or missing values, it serializes the record in a stable order and returns a SHA-256 hash, which is a fixed-length fingerprint of that content.

**Call relations**: Connector.render calls this when constructing a fallback title after record_identity succeeds. It uses JSON serialization and hashing to produce a deterministic reference when the direct primary key is not usable.

*Call graph*: called by 1 (render); 2 external calls (sha256, dumps).


### Source sync engine
The central sync runtime stores fetched content, detects updates and deletions, and exposes reliable downstream change feeds.

### `core/src/ufo/runtime/sources/sync.py`

`orchestration` · `startup registration, scheduled sync runs, and downstream page-feed polling`

This file turns outside content into stable internal “pages.” A source backend is like a delivery service: it knows how to read one kind of place, such as a local folder or an external provider, and returns documents plus a cursor, which is a bookmark for where to resume next time. The SyncDriver is the scheduler and clerk. It finds sources that are due, claims each one so two workers do not sync the same source at once, asks the right backend to fetch content, writes changed document bodies to the blob store, and updates page rows in the database.

It is careful about failures. If the provider fails, the source backs off so it does not hammer the provider. If the system’s own database is unreachable, the run is deferred rather than blamed on the provider. If a provider refuses a stream because of permissions or plan limits, the source can be “parked,” meaning retried less often without waking an operator.

The file also defines PageFeed, the interface indexers use to replay page changes in a stable order. Think of it as a checkout counter receipt roll: each material page change gets a revision number, and readers advance through that roll using a cursor.

#### Function details

##### `SourceRowConfig.requested_fields`  (lines 107–110)

```
def requested_fields(cls) -> frozenset[str]
```

**Purpose**: Returns the configuration fields that must match when a source is registered again. These are fields that affect the requested sync behavior but are not part of the source’s permanent identity.

**Data flow**: It reads the class-level sets of non-identity fields and resolved fields → subtracts fields that callers resolve automatically → returns the remaining field names as a frozen set.

**Call relations**: This belongs to backend-specific source configuration models. Registration code can use this idea to decide whether a new request should attach to an existing source row or should be treated as different.


##### `normalize_page_timestamp`  (lines 113–133)

```
def normalize_page_timestamp(value: str) -> str
```

**Purpose**: Turns provider timestamps into one consistent UTC timestamp string. This keeps dates comparable even when providers send milliseconds, seconds, ISO strings, or a plain date.

**Data flow**: It receives a timestamp string → parses it as either a numeric Unix time or an ISO date/time → rejects ambiguous values without a timezone except plain dates → returns a UTC ISO string with microseconds.

**Call relations**: Page.normalize_timestamp calls this whenever a Page model receives created_at or updated_at. That means timestamps are cleaned at the edge, before pages reach the database.

*Call graph*: called by 1 (normalize_timestamp); 2 external calls (fromisoformat, fromtimestamp).


##### `Page.digest`  (lines 150–151)

```
def digest(self) -> str
```

**Purpose**: Computes a fingerprint of a page body. The sync driver uses this to tell whether content actually changed without comparing whole documents later.

**Data flow**: It reads the page’s body text → encodes it and hashes it with SHA-256, a standard one-way content fingerprint → returns a string prefixed with sha256:.

**Call relations**: SyncDriver._commit uses this property while deciding whether to write a new blob and update the page row. If the digest matches the existing row, the body can be skipped.

*Call graph*: 1 external calls (sha256).


##### `Page.normalize_timestamp`  (lines 155–158)

```
def normalize_timestamp(cls, value: str | None) -> str | None
```

**Purpose**: Validates and normalizes Page timestamp fields as Page objects are created. It makes sure stored provider times are consistent and timezone-aware.

**Data flow**: It receives either a timestamp string or None → leaves None alone → sends real strings to normalize_page_timestamp → returns the cleaned string.

**Call relations**: Pydantic, the data validation library used here, calls this automatically for Page.created_at and Page.updated_at. It delegates the actual parsing rules to normalize_page_timestamp.

*Call graph*: calls 1 internal fn (normalize_page_timestamp).


##### `StreamSkipped.__init__`  (lines 208–211)

```
def __init__(self, reason: str, *, awaits_grant: bool=False) -> None
```

**Purpose**: Creates an exception meaning the provider refused this stream in a non-broken way, such as a missing grant or plan gate. It carries both the human reason and whether the stream is waiting for a grant event.

**Data flow**: It receives a reason string and an awaits_grant flag → stores both on the exception → produces an exception object that fetchers can raise.

**Call relations**: Connector backends raise this when a stream should not be treated as a provider failure. SyncDriver._sync_claimed catches it and routes the source to _skip instead of the normal failure path.

*Call graph*: called by 62 (_credential, paginate, paginate, paginate, paginate, paginate, _paginate_records, paginate, paginate, paginate (+15 more)).


##### `validation_fault`  (lines 214–220)

```
def validation_fault(error: ValidationError) -> str
```

**Purpose**: Turns a validation error into a safe, compact explanation. It names which fields failed and why, without logging the rejected values.

**Data flow**: It receives a ValidationError → reads each reported field path and error type → joins them into a semicolon-separated string.

**Call relations**: SyncDriver._report_failed calls this when a backend or config model rejects data. The result becomes safe telemetry for the failed sync.

*Call graph*: called by 1 (_report_failed); 1 external calls (errors).


##### `response_fault`  (lines 223–270)

```
def response_fault(response: httpx.Response) -> str
```

**Purpose**: Extracts a safe reason from an HTTP error response body. It avoids logging full provider responses because those can contain private request details or credentials.

**Data flow**: It receives an httpx Response → tries to parse JSON → looks for known Google-style or GraphQL-style error envelopes → returns only reason codes, statuses, or messages that are considered safe.

**Call relations**: SyncDriver._report_failed uses this for HTTP status failures. It adds context to failure logs without exposing the provider’s raw response body.

*Call graph*: called by 1 (_report_failed); 2 external calls (json, list_or_empty).


##### `StreamFault.__init__`  (lines 280–282)

```
def __init__(self, reason: str) -> None
```

**Purpose**: Creates an exception for a provider stream whose data shape could not be read. It lets a backend provide a safe explanation written by the backend itself.

**Data flow**: It receives a reason string → stores it on the exception → produces an exception object that can travel up to the sync driver.

**Call relations**: Several provider and extension backends raise this when provider data is unusable. SyncDriver._report_failed recognizes it and logs its reason as the provider fault.

*Call graph*: called by 9 (_read, _markdown_entries, _spool_tarball, _refuse_client_error, decoded, _account_base, _sheet_value_records, paginate, _ensure_tenant).


##### `SourceBackend.config_model`  (lines 322–322)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: Defines the typed configuration model a backend expects. This is part of the SourceBackend protocol, meaning any backend must provide it.

**Data flow**: A backend exposes a model class → the sync driver uses that class to validate the JSON config stored on the source row → validated config goes into fetch.

**Call relations**: SyncDriver._fetch relies on this protocol member before calling SourceBackend.fetch. It is the seam that keeps backend configs typed instead of loose dictionaries.


##### `SourceBackend.fetch`  (lines 324–324)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Defines the standard method every source backend must implement to fetch documents. It returns pages, deletions, and a next cursor for future runs.

**Data flow**: It receives validated backend config, the previous cursor, and source auth information → the backend reads its provider or storage → it returns a SyncResult.

**Call relations**: SyncDriver._fetch calls this after preparing config and authentication. FolderSource.fetch is the built-in implementation for local folders, while extension backends implement it for external providers.


##### `FolderSource.fetch`  (lines 338–349)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Reads a local folder and turns every file into a Page. This provides the built-in source type for simple filesystem-backed content.

**Data flow**: It receives a SourceConfig with a root path, ignores cursor/auth, and reads files in a background thread → wraps each file’s relative path and text into a Page → returns a snapshot SyncResult.

**Call relations**: SyncDriver._fetch calls this when the source backend is the built-in folder backend. It hands actual disk reading to FolderSource._read.

*Call graph*: 4 external calls (__init__, __init__, to_thread, Path).


##### `FolderSource._read`  (lines 352–359)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: Scans a directory and reads all files as UTF-8 text. It fails if the root directory is missing so a temporary mount problem does not erase all pages.

**Data flow**: It receives a root Path → checks that it is a directory → walks all files below it in sorted order → returns pairs of relative path and file text.

**Call relations**: FolderSource.fetch runs this in a worker thread so disk I/O does not block the async event loop. Its output becomes Pages.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 362–382)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object], *, connection_id: UUID | None=None, non_identity_keys: frozenset[str]=frozenset()) -> UUID
```

**Purpose**: Builds a stable UUID for a source row from its workspace, backend, and identity-defining config. This prevents duplicate rows when the same source is registered again.

**Data flow**: It receives workspace id, backend name, config, optional connection id, and keys to ignore for identity → removes non-identity config keys → JSON-serializes the identity → returns a deterministic UUID.

**Call relations**: register_sources calls this during startup registration. The same configured source produces the same database id across restarts.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 385–388)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: Builds a stable UUID for a page inside a source. A document with the same source reference lands on the same page row each sync.

**Data flow**: It receives a source id and source reference → combines them into a deterministic namespace string → returns a UUID.

**Call relations**: SyncDriver._commit uses this when matching fetched pages and delete references to database rows.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `source_body_ref_matches`  (lines 391–401)

```
def source_body_ref_matches(body_ref: str, source_id: UUID, page_id: UUID, digest: str) -> bool
```

**Purpose**: Checks whether a blob reference looks like the expected body location for one synced page and digest. This is a safety check for source-owned blob paths.

**Data flow**: It receives a blob reference, source id, page id, and digest → checks the prefix, claim-shaped middle part, and digest suffix → returns true or false.

**Call relations**: This helper is available to code that needs to verify source blob references. It follows the same path format that SyncDriver._commit writes.


##### `register_sources`  (lines 404–469)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: Creates database rows for sources listed in static configuration. It runs at boot so configured sources become syncable without manual database setup.

**Data flow**: It receives configured source entries → opens a workspace transaction → finds the workspace and main agent → computes each source id → inserts missing source rows and grants, skipping removed rows.

**Call relations**: It calls source_row_id to avoid duplicate source rows. After it runs, SyncDriver can later claim and sync those rows on its polling schedule.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `_rescheduled`  (lines 487–498)

```
def _rescheduled(claimed: ClaimedSource, when: datetime | sa.Case[datetime]) -> sa.Case[datetime]
```

**Purpose**: Protects a resync request that arrived while a source was already being synced. It decides whether the finishing run may move next_sync_at forward.

**Data flow**: It receives the claimed source and the desired next time → builds a database expression: use the desired time only if next_sync_at has not been updated since the claim began → otherwise keep the newer requested time.

**Call relations**: SyncDriver._write, _release, and _skip use this when finishing a run. It prevents a live resync request from being accidentally postponed.

*Call graph*: called by 3 (_release, _skip, _write); 1 external calls (case).


##### `_stream_tags`  (lines 501–506)

```
def _stream_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: Builds metric tags that identify the provider and stream involved in a sync outcome. Tags are labels used by monitoring systems to group events.

**Data flow**: It receives a claimed source → reads the backend name and the stream value from config → returns a small dictionary of tag strings.

**Call relations**: Reporting and logging methods call this for success, failure, deferral, parking, skipped streams, and lost claims. _check_tags builds on it.

*Call graph*: calls 1 internal fn (_config_value); called by 7 (_defer, _report_failed, _report_ok, _report_parked, _run_with_lease, _sync_claimed, _check_tags).


##### `_check_tags`  (lines 509–517)

```
def _check_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: Builds service-check tags that identify one exact source row. This avoids mixing health status for different sources that happen to use the same provider stream.

**Data flow**: It receives a claimed source → starts with _stream_tags → adds the source id → returns tags for service-check reporting.

**Call relations**: SyncDriver._report_ok and _report_failed use this when emitting service checks. It makes recovery and failure status attach to the right row.

*Call graph*: calls 1 internal fn (_stream_tags); called by 2 (_report_failed, _report_ok).


##### `_config_value`  (lines 520–522)

```
def _config_value(source: ClaimedSource, key: str) -> str
```

**Purpose**: Safely reads a string value from a source’s config. If the value is absent or not a string, it returns an empty string.

**Data flow**: It receives a claimed source and a config key → looks up the key in the config mapping → returns the string value or an empty string.

**Call relations**: Metric and log helpers use this for fields such as stream and account. It keeps telemetry code from crashing on unexpected config shapes.

*Call graph*: called by 4 (_defer, _report_failed, _report_ok, _stream_tags).


##### `_database_unreachable`  (lines 525–547)

```
def _database_unreachable(error: BaseException) -> bool
```

**Purpose**: Decides whether an exception came from this system’s own database being unavailable. That matters because such a run should not be counted as a provider failure.

**Data flow**: It receives an exception → checks SQLAlchemy and asyncpg connection-related error types → returns true for pool/connection failures and false for other errors.

**Call relations**: SyncDriver._sync_claimed calls this in its broad exception path. A true result sends the run to _defer rather than _report_failed.

*Call graph*: called by 1 (_sync_claimed).


##### `_readers_remain`  (lines 571–609)

```
def _readers_remain() -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database condition that says a source still has someone who can read it. Sources whose readers are all archived are not worth syncing.

**Data flow**: It creates SQL subqueries checking grants, live agents, and the live main agent for shared sources → combines them into one true/false database expression.

**Call relations**: SyncDriver.candidate_workspaces and _claim_due include this condition. It keeps abandoned feeds out of scheduled sync work until a reader returns.

*Call graph*: called by 2 (_claim_due, candidate_workspaces); 5 external calls (and_, exists, literal, or_, select).


##### `SyncDriver.candidate_workspaces`  (lines 635–657)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces currently have at least one due, readable, funded source. This lets the dispatcher avoid opening per-workspace work when nothing is ready.

**Data flow**: It reads the current time → queries source rows through an owner-level transaction → filters for due, unremoved, unclaimed or expired, readable, funded sources → returns distinct workspace ids.

**Call relations**: A higher-level scheduler calls this before binding workspaces. It uses _readers_remain and funded to decide whether a workspace should be considered.

*Call graph*: calls 1 internal fn (_readers_remain); 5 external calls (now, or_, select, owner_tx, funded).


##### `SyncDriver.run`  (lines 659–670)

```
async def run(self) -> None
```

**Purpose**: Runs one sync polling pass for the current workspace. It claims due sources, keeps their claims alive, and syncs them one by one.

**Data flow**: It creates a unique claim token → asks _claim_due for source rows → starts renewal tasks for those claims → sends each source through _run_with_lease → cancels renewal tasks at the end.

**Call relations**: This is the main entry method for the sync job. It coordinates _claim_due, _renew_claim, and _run_with_lease.

*Call graph*: calls 3 internal fn (_claim_due, _renew_claim, _run_with_lease); 3 external calls (create_task, gather, uuid4).


##### `SyncDriver._run_with_lease`  (lines 672–692)

```
async def _run_with_lease(self, source: ClaimedSource, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs one claimed source while watching the claim-renewal task. If the lease cannot be renewed, it stops treating the source as safely owned.

**Data flow**: It receives a claimed source and its renewal task → starts the actual sync task → waits until either sync or renewal finishes → logs claim loss if appropriate → cancels leftover tasks.

**Call relations**: SyncDriver.run calls this for each claimed source. It hands the real fetch/commit work to _sync_claimed while protecting against duplicate workers.

*Call graph*: calls 2 internal fn (_sync_claimed, _stream_tags); called by 1 (run); 4 external calls (create_task, gather, wait, log).


##### `SyncDriver._sync_claimed`  (lines 694–716)

```
async def _sync_claimed(self, source: ClaimedSource) -> None
```

**Purpose**: Performs the fetch-and-commit flow for one claimed source and chooses the right recovery path on errors. It is the central decision point for success, failure, skip, and deferral.

**Data flow**: It receives a claimed source → calls _fetch and then _commit → if skipped, calls _skip → if database unreachable, calls _defer → otherwise computes backoff, reports failure, and releases the claim.

**Call relations**: _run_with_lease calls this after a source is claimed. It delegates detailed actions to _fetch, _commit, _skip, _defer, _report_failed, and _release.

*Call graph*: calls 9 internal fn (_commit, _defer, _error_backoff, _fetch, _release, _report_failed, _skip, _database_unreachable, _stream_tags); called by 1 (_run_with_lease); 3 external calls (suppress, now, log).


##### `SyncDriver._renew_claim`  (lines 718–721)

```
async def _renew_claim(self, source: ClaimedSource) -> None
```

**Purpose**: Keeps a source claim alive while a sync is waiting or running. This prevents another worker from picking up the same source during a slow fetch.

**Data flow**: It receives a claimed source → sleeps for the refresh interval in a loop → calls _refresh_claim each time.

**Call relations**: SyncDriver.run starts this as a background task for every claimed source. _run_with_lease watches it and stops if renewal fails.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (run); 1 external calls (sleep).


##### `SyncDriver._refresh_claim`  (lines 723–739)

```
async def _refresh_claim(self, source: ClaimedSource) -> None
```

**Purpose**: Extends the database lease for a claimed source. If the row is gone or claimed by someone else, it reports that the claim was lost.

**Data flow**: It receives a claimed source → updates the source row only if the same claim token still owns it → moves claim_expires_at forward → raises _SourceClaimLost if no row was updated.

**Call relations**: _renew_claim calls this repeatedly. _commit also calls it just before writing so the claim is fresh during the database update.

*Call graph*: called by 2 (_commit, _renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `SyncDriver._claim_due`  (lines 741–793)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: Claims a batch of due source rows for this worker. Claiming is a lock-like marker in the database that prevents two workers syncing the same source.

**Data flow**: It receives a claim token → selects due, readable, funded, unremoved sources whose claim is absent or expired → marks them with the claim and lease expiry → returns ClaimedSource objects.

**Call relations**: SyncDriver.run calls this at the start of a polling pass. It uses _readers_remain and database locking behavior suited to PostgreSQL or SQLite.

*Call graph*: calls 1 internal fn (_readers_remain); called by 1 (run); 8 external calls (__init__, now, timedelta, or_, select, update, workspace_tx, funded).


##### `SyncDriver._fetch`  (lines 795–814)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: Prepares the right backend call for a claimed source. It validates the source config and provides authentication context without core itself holding provider tokens.

**Data flow**: It receives a claimed source → finds the backend by name → validates the stored config with the backend’s model → resolves optional identity and credentials → calls backend.fetch → returns SyncResult.

**Call relations**: _sync_claimed calls this before committing. It is the bridge between generic core scheduling and backend-specific provider reading.

*Call graph*: called by 1 (_sync_claimed); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 816–918)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: Turns fetched pages into blob writes and database updates. It skips unchanged bodies, updates changed metadata, and prepares tombstones for deleted pages.

**Data flow**: It receives a claimed source and SyncResult → refreshes the claim → reads prior pages → matches fetched pages to stable ids → writes changed bodies to the blob store → calls _write to commit rows → cleans up blobs if the commit path fails → reports success.

**Call relations**: _sync_claimed calls this after _fetch succeeds. It relies on _prior_pages, page_id_for, _write, and _report_ok.

*Call graph*: calls 5 internal fn (_prior_pages, _refresh_claim, _report_ok, _write, page_id_for); called by 1 (_sync_claimed); 4 external calls (__init__, __init__, gather, uuid5).


##### `SyncDriver._prior_pages`  (lines 920–964)

```
async def _prior_pages(self, source_id: UUID) -> tuple[dict[UUID, tuple[str, bool, PageBrowse]], dict[str, tuple[str, bool, PageBrowse]]]
```

**Purpose**: Loads the existing page rows for a source so the commit can tell what is new, changed, unchanged, or already tombstoned.

**Data flow**: It receives a source id → queries page rows for that source → builds one lookup by page id and another by source identity → returns both dictionaries.

**Call relations**: SyncDriver._commit calls this before comparing fetched pages with stored pages. It creates PageBrowse objects used for change detection.

*Call graph*: called by 1 (_commit); 3 external calls (__init__, select, workspace_tx).


##### `SyncDriver._write`  (lines 966–1094)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[ChangedPage], metadata: list[PageBrowse], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> int
```

**Purpose**: Writes one completed sync result to the database. It updates changed pages, metadata-only changes, tombstones deletions, and reschedules the source after success.

**Data flow**: It receives changed pages, metadata changes, fetched ids, explicit deletes, snapshot flag, and next cursor → verifies the source claim still owns the row → upserts page rows → tombstones explicit or missing pages → resets error/refusal state and clears the claim → returns the number tombstoned.

**Call relations**: SyncDriver._commit calls this after blob bodies are written. It uses _rescheduled so a resync request made during the run is not lost.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (_commit); 7 external calls (__init__, now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._report_ok`  (lines 1096–1119)

```
async def _report_ok(self, source: ClaimedSource, fetched: int, written: int, tombstoned: int, dropped: int) -> None
```

**Purpose**: Emits success telemetry for one source sync. It logs how many pages were fetched, written, tombstoned, and dropped, and marks the source service check as healthy.

**Data flow**: It receives counts and the source → builds stream and check tags → writes a success log → emits an OK service check, suppressing telemetry failures.

**Call relations**: SyncDriver._commit calls this after _write succeeds. It is the success counterpart to _report_failed.

*Call graph*: calls 3 internal fn (_check_tags, _config_value, _stream_tags); called by 1 (_commit); 3 external calls (suppress, emit_service_check, log).


##### `SyncDriver._error_backoff`  (lines 1121–1129)

```
def _error_backoff(self, source: ClaimedSource, now: datetime) -> tuple[int, datetime]
```

**Purpose**: Calculates how long to wait before retrying a failed source. Repeated failures wait longer, up to a maximum cap.

**Data flow**: It receives a source and current time → increments the consecutive error count → doubles the base interval according to that count, capped at the maximum → returns the new count and next retry time.

**Call relations**: SyncDriver._sync_claimed calls this before reporting and releasing a failed source. _release then persists the chosen retry time.

*Call graph*: called by 1 (_sync_claimed); 1 external calls (timedelta).


##### `SyncDriver._report_failed`  (lines 1131–1193)

```
async def _report_failed(self, source: ClaimedSource, error: Exception, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: Emits safe failure telemetry for one provider stream. It records the error class, safe provider fault text, retry timing, and a critical service check.

**Data flow**: It receives the source, exception, cursor reset flag, error count, and next retry time → extracts safe details depending on error type → logs the failure → emits a failure metric and CRITICAL service check.

**Call relations**: SyncDriver._sync_claimed calls this for real sync/provider failures. It uses response_fault and validation_fault to avoid leaking sensitive payloads.

*Call graph*: calls 5 internal fn (_check_tags, _config_value, _stream_tags, response_fault, validation_fault); called by 1 (_sync_claimed); 5 external calls (suppress, isoformat, emit_metric, emit_service_check, log_error).


##### `SyncDriver._defer`  (lines 1195–1221)

```
async def _defer(self, source: ClaimedSource, error: Exception) -> None
```

**Purpose**: Records a run interrupted by this system’s own database instead of the provider. It avoids falsely blaming or alerting on the source stream.

**Data flow**: It receives the source and database-related error → computes a normal next retry time → emits a warning log → tries to release the claim without increasing provider error count.

**Call relations**: SyncDriver._sync_claimed calls this when _database_unreachable says the exception belongs to the database layer. It hands the row update to _release.

*Call graph*: calls 3 internal fn (_release, _config_value, _stream_tags); called by 1 (_sync_claimed); 4 external calls (suppress, now, timedelta, warn).


##### `SyncDriver._release`  (lines 1223–1249)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: Frees a claimed source after a failed or deferred run. It updates retry timing, error count, cursor reset behavior, and clears the claim.

**Data flow**: It receives a source, cursor reset flag, error count, and next sync time → updates the source row if this claim still owns it → clears the claim and stores the new schedule.

**Call relations**: _sync_claimed calls this after reporting failures, and _defer calls it for database interruptions. It uses _rescheduled to preserve resync requests made during the run.

*Call graph*: calls 1 internal fn (_rescheduled); called by 2 (_defer, _sync_claimed); 2 external calls (update, workspace_tx).


##### `SyncDriver._skip`  (lines 1251–1316)

```
async def _skip(self, source: ClaimedSource, reason: str, *, awaits_grant: bool) -> None
```

**Purpose**: Handles a stream the backend says should be skipped rather than failed. It keeps existing pages intact and may park repeatedly refused streams so they retry less often.

**Data flow**: It receives a source, refusal reason, and awaits_grant flag → increments refusal count in the database → schedules a normal retry or longer park delay → resets error count and clears the claim → reports parking if the threshold was reached.

**Call relations**: SyncDriver._sync_claimed calls this when a backend raises StreamSkipped. If the row becomes parked, it calls _report_parked.

*Call graph*: calls 2 internal fn (_report_parked, _rescheduled); called by 1 (_sync_claimed); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `SyncDriver._report_parked`  (lines 1318–1343)

```
async def _report_parked(self, source: ClaimedSource, reason: str, refusals: int) -> None
```

**Purpose**: Emits telemetry when a refused stream is parked. Parking is a warning, not an alert, because an operator usually cannot fix missing user grants.

**Data flow**: It receives the source, reason, and refusal count → builds stream tags → writes a warning log → emits a parked metric, suppressing telemetry failures.

**Call relations**: SyncDriver._skip calls this only after the database update actually parks the row. It does not emit a service-check status.

*Call graph*: calls 1 internal fn (_stream_tags); called by 1 (_skip); 3 external calls (suppress, emit_metric, warn).


##### `PageFeed.pages_changed_since`  (lines 1383–1383)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Defines the interface an indexer uses to read changed pages. Implementations return changes after a cursor in a stable order.

**Data flow**: A caller supplies a cursor and limit → the implementation reads later page changes → returns a PageBatch with changes and the next cursor.

**Call relations**: CorePageFeed.pages_changed_since implements this protocol. Extension contexts can depend on PageFeed without knowing the database and blob details.


##### `page_cursor`  (lines 1386–1395)

```
def page_cursor(cursor: object) -> tuple[int, UUID]
```

**Purpose**: Parses a page-feed cursor into its revision number and page id. It rejects malformed cursors early so feed reads do not behave unpredictably.

**Data flow**: It receives an object → verifies it is a string shaped like revision|uuid → converts the revision to an integer and the id to a UUID → returns both.

**Call relations**: CorePageFeed.pages_changed_since calls this when a reader resumes from a cursor. The parsed values become the database boundary for the next batch.

*Call graph*: called by 1 (pages_changed_since); 1 external calls (UUID).


##### `CorePageFeed.pages_changed_since`  (lines 1407–1465)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Reads changed pages for downstream indexers, including page bodies for live pages and empty bodies for tombstones. It returns changes in revision order so readers can resume safely.

**Data flow**: It receives a cursor and requested limit → caps the limit → optionally parses the cursor → queries page rows after that cursor → fetches each non-tombstoned body from the blob store → builds PageChange objects → returns them with the next cursor.

**Call relations**: This is the core implementation of the PageFeed protocol. Indexers call it to follow the page change stream produced by SyncDriver._write.

*Call graph*: calls 1 internal fn (page_cursor); 7 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx).
