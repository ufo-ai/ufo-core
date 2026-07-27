# Source connector framework and registry  `stage-13.1.1`

This stage is shared behind-the-scenes support for bringing outside data into the system. It does not collect one specific source by itself. Instead, it provides the common “plug shape” that all source connectors must fit, plus helpers for common web API work and a registry that says which connectors exist.

The connector framework defines the basic terms: a stream is a sequence of records from one place, a page is one batch of records, and saved progress is the bookmark that lets the system resume later without starting over. It also defines how raw source records are turned into readable text, and how to move safely through partitioned sources, such as many repositories or chat channels.

The REST helper supports connectors that read from web services. It makes authenticated HTTP requests, retries temporary failures, and follows paginated results.

The registry is the address book. It maps provider accounts to supported connectors and creates stable binding names so the rest of the system can refer to each source consistently.

## Files in this stage

### Connector abstractions
Shared source connector contracts define streams, record pages, progress state, partition traversal, and readable record conversion.

### `core/src/ufo/sources/connector.py`

`domain_logic` · `during source sync runs`

A connector is the project’s adapter for an outside service, such as a document app, email system, or code host. This file sets the rules every connector follows so the rest of the system can sync different services in the same way. It defines stream descriptions, page shapes, pagination options, and the abstract Connector class that real providers must implement.

The most important idea is that records arrive in pages, not all at once. A page may contain live records, deleted record IDs, and a cursor. A cursor is a bookmark that lets the next sync continue from the right place. Without this shared shape, each provider would need custom sync code throughout the system.

The file also solves a harder problem: some streams are split into partitions, like one stream per repository or one stream per Slack channel. PartitionWalk keeps a separate bookmark for each partition but packs them into one cursor string for the wider sync system. It knows how to resume streams that go oldest-first, newest-first, or have no useful time ordering. Think of it like a delivery driver with a checklist for many streets: it marks which streets are done, where it paused, and how to continue tomorrow without skipping houses.

Finally, Connector.render provides a default way to turn a record into a searchable, recallable page: choose a sensible title, then include the record as JSON. Content-focused connectors can override this to produce nicer prose.

#### Function details

##### `PartitionWalk.stream`  (lines 209–301)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This drives syncing for a stream that is split into many partitions, such as repositories, folders, or channels. It yields normal stream pages while continually updating a single cursor that remembers progress for every partition.

**Data flow**: It starts with an incoming cursor string, decodes it into a per-partition progress map, then asks the connector for partitions one by one. For each partition, it chooses the right boundary to fetch from, reads pages from the connector’s page factory, updates the checkpoint after each page, and yields a StreamPage containing records, deletes, and the latest encoded cursor. If a partition is skipped because the provider refuses it temporarily, the stored progress for that partition is left intact. At the end of a completed pass, it removes stale partition state when appropriate and may yield one final empty page just to publish the cleaned-up cursor.

**Call relations**: The wider connector implementation calls this when a stream fans out across partitions and needs safe resume behavior. Inside the flow, it relies on PartitionWalk._decode to understand the stored cursor before work begins, and PartitionWalk._encode to turn each updated checkpoint back into a cursor string. It wraps connector-supplied pages into StreamPage objects so the rest of the sync pipeline sees the same page shape as any other stream.

*Call graph*: calls 2 internal fn (_decode, _encode); 3 external calls (__init__, __init__, __init__).


##### `PartitionWalk._decode`  (lines 304–333)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: This turns a stored cursor string back into the partition progress map used by PartitionWalk. It is deliberately cautious: cursors that are clearly from some other shape are treated as empty, while malformed partition-walk cursors raise an error instead of silently dropping progress.

**Data flow**: It receives a cursor string or nothing. If there is no cursor, invalid JSON, or JSON that is not an object, it returns an empty map, meaning the walk starts fresh. If the JSON object contains string values, those become simple per-partition watermarks. If an entry is an object with the expected high and until bounds, it becomes a validated window. Anything else is treated as corruption and raises an error.

**Call relations**: PartitionWalk.stream calls this at the start of a run to recover where each partition previously stopped. Its output decides whether a partition is skipped, resumed from a watermark, continued downward through a backfill window, or walked from scratch.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 336–341)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: This turns the in-memory partition progress map into a stable JSON cursor string that can be stored between sync runs. It is the counterpart to PartitionWalk._decode.

**Data flow**: It receives a map whose values are either simple watermark strings or window objects. It converts window objects into plain dictionaries, keeps watermark strings as they are, and serializes the whole map to JSON with sorted keys. The result is a cursor string that can be saved and later decoded.

**Call relations**: PartitionWalk.stream calls this whenever it needs to publish updated progress, usually after yielding records from a partition or after marking a partition as complete. The encoded string travels outward inside StreamPage.next_cursor so the sync runner can store it.

*Call graph*: called by 1 (stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 355–356)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This is the required method where a connector says which streams it can sync. A stream is one named collection from the outside service, such as users, messages, documents, or issues.

**Data flow**: A concrete connector implements this with no input besides itself. It returns a list of StreamSpec objects, each describing a source-side collection, its primary ID field, whether it supports incremental progress, and related sync behavior. Because this base method is abstract, it does not produce data itself.

**Call relations**: The sync system calls this on a concrete connector before running a sync so it knows what collections are available. Provider-specific connector classes fill in the actual stream list; this base class only defines the contract they must follow.


##### `Connector.fetch_page`  (lines 359–367)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the required method where a connector retrieves records for one stream in pages. It lets each provider translate the project’s common sync request into that provider’s own API calls and pagination rules.

**Data flow**: It receives a StreamSpec, an optional cursor bookmark, a resolved Credential containing access information, and a base URL. A concrete connector uses those inputs to fetch provider data and asynchronously yields either simple lists of records or richer StreamPage objects with records, deletes, and a next cursor. This base method is abstract, so it only defines the expected behavior.

**Call relations**: The sync runner calls this while syncing a selected stream. Concrete connectors implement it directly or use helpers such as PartitionWalk for partitioned streams. The pages it yields are then consumed by the source-sync adapter, which writes records, records deletions, and stores the cursor for the next run.


##### `Connector.render`  (lines 369–388)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns one raw provider record into a title and body that the system can store as readable recall content. It gives every connector a usable default, while allowing content-heavy providers to override it with cleaner text.

**Data flow**: It receives a record dictionary and the StreamSpec it came from. It first looks for a human-friendly title field such as title, name, login, or subject. If none exists, it falls back to the record’s primary key and builds a title from the stream name and ID. If it cannot find either a title or a usable ID, it raises an error. It returns a pair: the chosen title and a body containing a heading plus the record serialized as sorted JSON.

**Call relations**: After records are fetched, the sync adapter can call this to convert raw data into the page content used for recall or search. Provider connectors for documents, email, or similar content may override it so users see prose rather than raw JSON, but the default keeps ordinary structured records usable.

*Call graph*: 1 external calls (dumps).


### REST connector support
Reusable REST helpers provide authenticated requests, retry handling, and paginated result traversal for web API sources.

### `core/src/ufo/sources/rest.py`

`io_transport` · `request handling during source sync reads`

Many outside services expose data through REST APIs, which are web endpoints that return records in small chunks. Without this file, every connector would have to rewrite the same fragile code for authentication, retries, page-by-page fetching, and response cleanup. This file acts like a reusable travel guide for APIs: it knows how to ask for the next page, when to slow down and retry, and when to stop because the route is looping.

The small helper functions pull records out of nested JSON, turn missing or oddly shaped responses into safe empty lists, read “next page” links from HTTP headers, and guard against endless pagination. The main class, `RestConnector`, is the base class that specific service connectors inherit from. A service-specific connector supplies details such as its base URL and stream list, then either uses the built-in pagination strategies or overrides pagination for unusual API shapes.

The class builds an `httpx.AsyncClient`, which is an asynchronous HTTP client. “Asynchronous” means one slow network call does not block the rest of the sync system. All GET and read-only POST requests pass through the same retry wrapper, so temporary network failures and server errors are treated consistently. Before yielding data onward, `fetch_page` validates that each page is a list of dictionary-like records and optionally flattens records into the shape the sync writer expects.

#### Function details

##### `get_path`  (lines 42–51)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a dotted path, such as `meta.next.cursor`, from a nested dictionary. It gives callers a safe way to look inside API responses without crashing when a layer is missing.

**Data flow**: It receives a dictionary, a dot-separated path, and a fallback value. It walks through the dictionary one part at a time; if it reaches something that is not a dictionary or finds a missing value, it returns the fallback. Otherwise it returns the value found at the end of the path.

**Call relations**: Pagination helpers use this when an API hides records or continuation tokens inside nested response objects. `records_at`, `_get_cursor_pages`, and `_get_offset_pages` call it while turning raw API replies into usable pages.

*Call graph*: called by 3 (_get_cursor_pages, _get_offset_pages, records_at).


##### `list_or_empty`  (lines 54–58)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: Turns a possible list of records into a clean list of dictionaries. If the value is not a list, or if some list items are not record-shaped dictionaries, those parts are ignored.

**Data flow**: It receives any value. If the value is a list, it keeps only the items that are dictionaries and returns them. If the value is not a list, it returns an empty list.

**Call relations**: This is the safety filter used when reading API responses. `records_at`, `_response_list`, and `_get_odata_pages` call it so malformed or unexpected JSON does not flow forward as records.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 61–64)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Returns a value only if it is a dictionary-shaped record. This is useful when a connector reaches into a nested object and wants a safe record instead of an unexpected type.

**Data flow**: It receives any value. If that value is a dictionary, it returns it unchanged. Otherwise it returns an empty dictionary.

**Call relations**: This helper is available for connectors that need to shape provider-specific records. It is not called inside this file, but it matches the same defensive style as `list_or_empty`.


##### `records_at`  (lines 67–72)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: Finds the list of records inside an API response. Some APIs return a list directly, while others wrap records under a field like `data.items`; this function supports both cases.

**Data flow**: It receives raw response data and an optional path. If there is no path, it treats the data itself as the list. If there is a path, it first finds that nested value with `get_path`. In either case, it returns only dictionary-shaped list items.

**Call relations**: Most pagination methods call this after each HTTP response arrives. It sits between raw JSON and the page stream, ensuring that only clean record lists are yielded.

*Call graph*: calls 2 internal fn (get_path, list_or_empty); called by 5 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, paginate_from_strategy, parse).


##### `_int_or_none`  (lines 75–80)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: Converts a value to an integer only when it is already an integer or a string of digits. It avoids guessing when the value is not clearly a number.

**Data flow**: It receives any value. It returns the integer unchanged, converts digit-only strings to integers, and returns `None` for everything else.

**Call relations**: `_get_offset_pages` uses this when an API tells the connector what page size it actually used. If that value is usable, the next offset can advance correctly.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 83–86)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: Adds extra context fields to every record, such as a parent ID or site ID. This helps later steps know where each record came from after records from many places are combined.

**Data flow**: It receives a collection of record dictionaries and named context values. It copies each record, adds the context fields, and returns a new list of enriched records.

**Call relations**: This helper is available to connector-specific pagination code, especially when one API request fans out into child requests. It is not called inside this file.


##### `next_link`  (lines 89–94)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: Reads the HTTP `Link` header and extracts the URL marked as the next page. Many APIs use this header to say, “go here for more results.”

**Data flow**: It receives HTTP response headers. It looks for a `link` header and searches it for a `rel=next` entry. If found, it returns that URL; otherwise it returns `None`.

**Call relations**: `_get_link_header_pages` calls this after each response. The returned URL becomes the next request target when an API uses header-based pagination.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 97–102)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: Decides whether a failed request is worth trying again. Temporary network problems and common rate-limit or server errors are considered retryable.

**Data flow**: It receives an exception from an HTTP request. It returns `true` for network transport errors and for HTTP status errors with retryable status codes such as 429 or 500. Otherwise it returns `false`.

**Call relations**: `_send` calls this inside its retry loop. It is the rulebook that tells `_send` whether to wait and try again or immediately raise the error.

*Call graph*: called by 1 (_send).


##### `_raise_for_status`  (lines 105–116)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: Turns unsuccessful HTTP responses into clear exceptions. Unlike the default behavior, it includes part of the response body, which often contains the real reason an API rejected the request.

**Data flow**: It receives an HTTP response. If the response was successful, it does nothing. If not, it reads a capped portion of the body and raises an `HTTPStatusError` containing the status, request, URL, and body text.

**Call relations**: `_send` calls this after every HTTP response. This keeps all request helpers using the same error format.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 119–123)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: Parses a response body as JSON when there is content, and returns an empty dictionary when there is no body. This keeps empty successful responses from causing parsing errors.

**Data flow**: It receives an HTTP response. If the status is 204 or the body is empty, it returns `{}`. Otherwise it parses and returns the JSON object.

**Call relations**: `_get` and `_post` use this after `_send` returns a successful response. It converts raw HTTP responses into dictionary data for pagination code.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 126–129)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Parses a response whose top-level JSON body should be a list of records. Empty responses become an empty list, and non-dictionary items are filtered out.

**Data flow**: It receives an HTTP response. If there is no content, it returns an empty list. Otherwise it parses the JSON and passes it through `list_or_empty`.

**Call relations**: `_get_link_header_pages` uses this when no custom record parser is supplied. It is the default way to read record lists from link-header paginated APIs.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 132–138)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: Stops a pagination loop if it runs for too many pages. This prevents a broken or hostile API response from keeping a sync job stuck forever.

**Data flow**: It receives a label describing the caller and the current page count. If the count is above the maximum allowed number of pages, it raises an error. Otherwise it lets the loop continue.

**Call relations**: Every built-in multi-page fetcher calls this once per page. It acts like a trip odometer that shuts down the journey if the destination never arrives.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 141–147)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: Stops cursor-based pagination when the API repeats a cursor or next-page URL. A repeated cursor means the API is not advancing and would fetch the same page forever.

**Data flow**: It receives a label, the new cursor token or URL, and a set of already seen tokens. If the token was seen before, it raises an error. If not, it records the token in the set.

**Call relations**: Cursor, link-header, and OData pagination helpers call this after discovering the next page marker. It catches endless loops earlier than the page-count limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 156–157)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the stream definitions supported by this connector. A stream is one kind of data the connector can read, such as users, projects, or tickets.

**Data flow**: It reads the class-level `streams_list` and returns a new list copy. The caller gets the available stream specifications without being able to mutate the original list by accident.

**Call relations**: The broader connector framework calls this when it needs to know what data surfaces a REST connector can expose.


##### `RestConnector._make_client`  (lines 159–176)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the authenticated asynchronous HTTP client used for one account. It supports credentials delivered through a proxy transport, a bearer token, or custom authentication headers.

**Data flow**: It receives a base URL and a resolved credential. It prepares timeouts and JSON headers, then chooses the correct authentication path. It returns an `httpx.AsyncClient`; if no usable authentication is present, it raises an error instead of making anonymous requests.

**Call relations**: `fetch_page` calls this before any API reading begins. The returned client is then passed into pagination and request helpers, so every request shares the same base URL, timeout, and authentication setup.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 178–181)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a GET request and returns the response body as a dictionary. It is the common helper for read endpoints that return JSON objects.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It calls `_get_raw` to send the request with retry behavior, then turns the raw response into a dictionary with `_json_or_empty`.

**Call relations**: Pagination helpers call `_get` when they only need the JSON body, not headers. It relies on `_get_raw` and `_send` so retry and error handling stay uniform.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, paginate_from_strategy).


##### `RestConnector._get_raw`  (lines 183–187)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Performs a GET request and returns the full HTTP response. This is needed when pagination information lives in headers instead of the JSON body.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It wraps `client.get` in `_send`, which applies retries and status checks, then returns the successful raw response.

**Call relations**: `_get` uses this for ordinary JSON reads, and header-driven pagination helpers use it directly when they need headers or raw response details.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 189–194)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a read-only POST request and returns a JSON object. Some services use POST for searches or queries even though no data is being written.

**Data flow**: It receives an HTTP client, a path, and an optional JSON request body. It sends the POST through `_send` for retries and converts the successful response with `_json_or_empty`.

**Call relations**: Connector subclasses can use this for provider APIs such as search endpoints. It shares the same retry envelope as GET requests.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 196–202)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Performs a read-only POST request and returns the full HTTP response. This is useful when the response shape is not a normal JSON object, such as a top-level array.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It sends `client.post` through `_send` and returns the successful raw response.

**Call relations**: Connector subclasses can call this for unusual read endpoints. Like `_post`, it keeps retries and error handling centralized in `_send`.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 204–218)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: Runs one HTTP request with retry behavior. It gives all REST connector requests the same response checking, temporary-failure retries, and exponential backoff, meaning each retry waits longer than the last.

**Data flow**: It receives a callable that creates an awaitable HTTP request. It tries the request, checks the status, and returns the response if successful. If a retryable error occurs, it sleeps, doubles the delay, and tries again until the attempt limit is reached; then it raises the error.

**Call relations**: `_get_raw`, `_post`, and `_post_raw` all hand their actual network request to `_send`. `_send` in turn uses `_raise_for_status` and `_is_retryable` to decide whether to return, retry, or fail.

*Call graph*: calls 2 internal fn (_is_retryable, _raise_for_status); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 220–249)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main read entry for a REST connector page fetch. It opens the HTTP client, runs pagination, validates each page, flattens each record, and yields records to the sync system.

**Data flow**: It receives a stream definition, an optional cursor, credentials, and a base URL. It builds a client, asks `paginate` for pages, skips empty pages, validates record shape, applies `flatten`, and yields either plain record lists or `StreamPage` objects with delete and cursor metadata preserved. It also closes an async generator cleanly if needed.

**Call relations**: The core sync driver calls this when it wants data for a stream. `fetch_page` coordinates `_make_client`, `paginate`, `_validate_page`, and `flatten`, making it the bridge between provider-specific pagination and the rest of the sync pipeline.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate); 1 external calls (__init__).


##### `RestConnector.paginate`  (lines 251–263)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses how raw pages should be produced for a stream. The default implementation uses the stream’s declared pagination strategy, while unusual connectors can override this method.

**Data flow**: It receives an HTTP client, stream definition, and optional cursor. If the stream has no usable pagination strategy, it raises `NotImplementedError`. Otherwise it delegates to `paginate_from_strategy` and yields each page it produces.

**Call relations**: `fetch_page` calls this after opening the HTTP client. In ordinary connectors it passes work to `paginate_from_strategy`; custom connectors may replace it for special API layouts.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (fetch_page).


##### `RestConnector.paginate_from_strategy`  (lines 265–358)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs one of the built-in pagination patterns declared on a stream. It turns configuration like “use cursor pagination” or “use offset and limit” into actual page-by-page HTTP requests.

**Data flow**: It receives a stream, client, and optional cursor. It reads the stream’s pagination settings, checks required fields, prepares request parameters, and calls the matching helper for cursor, link-header, page-number, offset-limit, or time-window pagination. It yields lists of record dictionaries from those helpers.

**Call relations**: `paginate` calls this for standard streams. It dispatches to `_get_cursor_pages`, `_get_link_header_pages`, `_get_page_number_pages`, `_get_offset_pages`, or `_get` depending on the declared strategy, and may use `_strategy_path` if the stream did not name its request path.

*Call graph*: calls 7 internal fn (_get, _get_cursor_pages, _get_link_header_pages, _get_offset_pages, _get_page_number_pages, _strategy_path, records_at); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 297–299)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Extracts records from a link-header paginated response when the records are nested inside the JSON body. It is a small custom parser created only for that pagination path.

**Data flow**: It receives an HTTP response. It parses the JSON body if present, then uses `records_at` to pull records from the configured record path. It returns a list of dictionary records.

**Call relations**: `paginate_from_strategy` creates this parser for `next_link` pagination when a record path is configured. `_get_link_header_pages` then calls it for each response instead of using the default top-level list parser.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 360–388)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: Fetches pages by following `Link: rel=next` HTTP headers. This is common for APIs that put the next-page URL in response headers rather than in the body.

**Data flow**: It receives a client, starting path, optional parameters, page-size settings, and an optional record parser. It sends the first request, yields records from each response, reads the next link from headers, checks for page and cursor loops, and keeps requesting until no next link remains.

**Call relations**: `paginate_from_strategy` calls this for the `next_link` strategy. It uses `_get_raw` for responses, `_response_list` or a custom parser for records, `next_link` for continuation, and the bound checks for safety.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 390–422)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: Fetches pages where the JSON body contains a cursor token for the next page. A cursor is like a bookmark handed back by the API.

**Data flow**: It receives a client, path, record path, cursor path, parameter names, page size, and extra parameters. It repeatedly sends GET requests, adds the current cursor to the query when present, yields records, reads the next cursor from the response, and stops when there is no valid next cursor.

**Call relations**: `paginate_from_strategy` calls this for the `next_cursor` strategy. It uses `_get` for each request, `records_at` for records, `get_path` for the next token, and safety checks to prevent endless pagination.

*Call graph*: calls 5 internal fn (_get, _bound_cursor, _bound_pages, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_odata_pages`  (lines 424–450)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Microsoft Graph or OData-style pages. In this format, records usually live under `value`, and the next page is named by `@odata.nextLink`.

**Data flow**: It receives a client, path, and optional first-request parameters. It requests the current path, yields dictionary records from the `value` list, reads `@odata.nextLink`, and follows it until it disappears. Only the first request uses the original parameters because the next-link already includes continuation details.

**Call relations**: This helper is available to connector-specific pagination code. It uses `_get_raw`, `list_or_empty`, `_bound_pages`, and `_bound_cursor` to follow OData links safely.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 452–492)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: Fetches pages using offset and limit numbers. This style asks for “start at item 0, give me 100,” then “start at item 100,” and so on.

**Data flow**: It receives a client, path, record path, limit, parameter names, optional extra parameters, and optional response paths that describe continuation. It sends GET requests with the current offset and limit, yields records, decides whether more pages exist, and advances the offset by the configured limit or the server-reported page size.

**Call relations**: `paginate_from_strategy` calls this for the `offset_limit` strategy. It relies on `_get`, `records_at`, `get_path`, `_int_or_none`, and `_bound_pages` to read each page and stop correctly.

*Call graph*: calls 5 internal fn (_get, _bound_pages, _int_or_none, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_page_number_pages`  (lines 494–523)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: Fetches pages by increasing a page number. This style asks for page 1, then page 2, and stops when a page comes back shorter than the expected page size.

**Data flow**: It receives a client, path, record path, page size, parameter names, optional extra parameters, and a starting page number. It sends a GET request for each page, yields any records, stops when the page is short, and otherwise increments the page number.

**Call relations**: `paginate_from_strategy` calls this for the `page_number` strategy. It uses `_get`, `records_at`, and `_bound_pages` to turn numbered API pages into record batches.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._strategy_path`  (lines 525–531)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: Resolves the request path for a stream when the pagination configuration did not include one. The base class raises an error because only a specific connector knows its provider’s path table.

**Data flow**: It receives a stream definition. In this base implementation, it does not return a path; it raises `NotImplementedError` explaining that the connector must either set `Pagination.path` or override this method.

**Call relations**: `paginate_from_strategy` calls this only when a stream’s pagination settings omit the path. Connector subclasses can override it to map stream names to API paths.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 533–536)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Converts one provider record into the flat dictionary shape the sync writer expects. The default does nothing because many APIs already return usable flat records.

**Data flow**: It receives a record dictionary and its stream definition. It returns the record unchanged. Subclasses can override it to lift nested fields or reshape records.

**Call relations**: `fetch_page` calls this on every validated record before yielding it onward. This gives each connector one final chance to normalize provider-specific payloads.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 538–549)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: Checks that pagination produced a list of dictionary records. It catches connector bugs early, before malformed data reaches the sync writer.

**Data flow**: It receives a page and stream definition. If the page is not a list, it raises a `TypeError`. If any item in the list is not a dictionary, it raises a `TypeError` naming the bad item type. Otherwise it returns without changing anything.

**Call relations**: `fetch_page` calls this before flattening and yielding records. It is the quality gate between pagination code and downstream sync writing.

*Call graph*: called by 1 (fetch_page).


### Source registry
The registry maps provider accounts to supported connectors and stable binding names used elsewhere in the system.

### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup and source registration`

This file answers two basic questions for the source-sync system: “Which outside services can we connect to?” and “What should we call a specific connected account?” It imports every connector class, such as Slack, GitHub, Airtable, Zendesk, and many others, then builds one explicit lookup table called CONNECTORS. The key in that table is the connector’s short name, also called its slug, and the value is the connector class that knows how to talk to that service. This is deliberately written out by hand instead of discovered automatically, so adding a new service means adding one clear line here rather than relying on hidden import-time scanning. The file also defines binding_name, the shared naming rule for a registered connection. Given a provider, an account, and an optional base URL, it creates a short, repeatable name using a cryptographic hash, which is a one-way fingerprint of that information. This avoids long or awkward names while still making different accounts unlikely to collide. Without this file, the sync driver would not have a dependable list of available source backends, and different parts of the system could invent different names for the same connected account.

#### Function details

##### `_connector_registry`  (lines 64–72)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: This function builds the lookup table from connector name to connector class. It also protects the system from accidentally registering two connectors with the same name, which would make the name ambiguous.

**Data flow**: It receives a tuple of connector classes. It starts with an empty dictionary, reads the name stored on each connector class, and inserts that class under that name. If it sees the same name twice, it stops with an error instead of silently choosing one. The result is a dictionary that other code can use to find the right connector class from a backend name.

**Call relations**: This function is used when the module is loaded to create the CONNECTORS registry. The registry then becomes the shared map that the rest of the source system can consult when it needs to turn a stored backend name into the connector code for that service.


##### `binding_name`  (lines 79–85)

```
def binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: This function creates the official short name for a registered source binding. It makes the name stable, compact, and safe to use as an identifier even when the original account or URL text is long.

**Data flow**: It receives a provider name, an account value, and an optional base URL. It puts those values into a small JSON object with sorted keys, turns that into bytes, and runs it through SHA-256, a standard fingerprinting algorithm. It keeps the first eight hexadecimal characters of that fingerprint, replaces underscores in the provider name with hyphens, and returns a name like provider-1a2b3c4d.

**Call relations**: When code needs the canonical name for a connected source account, this function supplies it. Inside the function, json.dumps creates a consistent text representation of the provider, account, and base URL, and hashlib.sha256 turns that text into the short digest used in the final name.

*Call graph*: 2 external calls (sha256, dumps).
