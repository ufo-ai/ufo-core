# Source synchronization and page-change replay  `stage-12`

This stage is the system’s intake and replay line. It runs behind the scenes after a source is connected. Its job is to fetch records from outside tools, page through long result lists safely, save the raw content, notice what changed or was deleted, and then replay those changes to search indexing, alerts, and other follow-up work.

The connector groups are the many “front doors” to outside services. They cover communications and meetings, documents and workspaces, project and developer tools, CRM and support systems, marketing tools, HR and recruiting, and finance or commerce products. Each connector speaks one service’s web API, then translates that service’s records into the same internal shape.

The shared source files are the machinery behind those doors. The connector contract defines the rules every connector follows. The REST base supplies safe web requests, retries, and pagination. The backend converts connector records into stored sync results. The sync core stores raw bodies and records page changes or deletions, then replays them in order. Page alerts watch those changes and notify chats when watched pages match.

## Sub-stages

- [Communications, calendar, and meeting source connectors](stage-12.1.md) `stage-12.1` — 7 files
- [Documents, knowledge bases, and structured workspace content connectors](stage-12.2.md) `stage-12.2` — 7 files
- [Work management, development, and operations source connectors](stage-12.3.md) `stage-12.3` — 9 files
- [CRM, sales, support, and customer-success source connectors](stage-12.4.md) `stage-12.4` — 6 files
- [Marketing, ads, social, and forms source connectors](stage-12.5.md) `stage-12.5` — 7 files
- [HR and recruiting source connectors](stage-12.6.md) `stage-12.6` — 6 files
- [Finance, billing, accounting, and commerce source connectors](stage-12.7.md) `stage-12.7` — 7 files

## Files in this stage

### Connector foundations
Defines the shared connector contract and REST API support used by external source providers to fetch records safely.

### `core/src/ufo/sources/connector.py`

`domain_logic` · `cross-cutting during sync runs`

A connector is the project’s adapter for an outside service, such as a document app, email system, or code host. Each service has its own API shape, but the rest of the system needs a simple promise: tell me what streams of records you can provide, fetch those records page by page, and turn each record into readable text for recall.

This file supplies that promise. `StreamSpec` describes one collection of source data, such as “issues” or “messages”: what it is called, which field is its stable ID, whether it can be synced incrementally, and how paging works. `StreamPage` represents one batch of changes, including normal records, optional deletes, and a cursor, which is a bookmark used to resume later.

The most involved piece is `PartitionWalk`. Some sources are split into many smaller areas, like repositories, channels, or folders. `PartitionWalk` is like a careful mail carrier with a notebook: it remembers how far it got in each area, so a stopped sync can continue without skipping records or rereading everything unnecessarily. It supports oldest-first streams, newest-first streams, and streams with no useful ordering.

Finally, `Connector` is the abstract base class. Real connectors subclass it and provide the actual stream list and fetching logic. The default `render` method gives a basic readable page by choosing a title-like field and dumping the record as JSON.

#### Function details

##### `PartitionWalk.stream`  (lines 205–297)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This method walks through all partitions of a source stream and yields sync pages with updated resume bookmarks. It exists so large, split-up sources can be synced safely across interruptions, run limits, deleted partitions, and different record orderings.

**Data flow**: It starts with an incoming cursor string, which is the saved bookmark from a previous run. It decodes that into a per-partition map, asks the connector for the list of partitions, then asks for pages inside each partition using the right boundary: after a known watermark, before an unfinished newest-first window, or from the start. As pages arrive, it updates the bookmark, yields `StreamPage` objects containing records, deletes, and the next cursor, and finally cleans up stale partition state when a full pass completes.

**Call relations**: This is the central driver for partitioned streams. It calls `PartitionWalk._decode` before walking so it knows where to resume, repeatedly calls `PartitionWalk._encode` when it needs to hand back a new bookmark, and wraps connector-provided page data into `StreamPage` objects. If a partition is skipped by the provider, it moves on without pretending that partition was completed.

*Call graph*: calls 2 internal fn (_decode, _encode); 3 external calls (__init__, __init__, __init__).


##### `PartitionWalk._decode`  (lines 300–329)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: This helper turns the stored cursor text back into the partition notebook used by `PartitionWalk.stream`. It also protects the sync from corrupted cursor data by rejecting malformed entries rather than silently losing progress.

**Data flow**: It receives a cursor string, or no cursor at all. If the cursor is empty, not JSON, or not a JSON object, it treats it as no partition state and returns an empty map. If it is a valid object, each partition entry becomes either a simple watermark string or a validated unfinished window with `high` and `until` bounds; malformed entries cause an error.

**Call relations**: `PartitionWalk.stream` calls this at the start of a walk. The decoded result tells the stream whether each partition is fresh, already completed for an unordered pass, partway through a newest-first backfill, or ready for normal incremental syncing.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 332–337)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: This helper turns the current per-partition progress map into a JSON cursor string that can be stored and used later. It is how the sync leaves a clear bookmark after each page.

**Data flow**: It receives a map from partition name to either a watermark string or an unfinished window object. It converts window objects into plain dictionaries, then serializes the whole map into sorted JSON text. The returned string becomes the next cursor carried by a yielded page.

**Call relations**: `PartitionWalk.stream` calls this whenever it yields progress. That lets the outer sync runner save a cursor often, so a stopped run can restart close to where it left off instead of repeating the whole source.

*Call graph*: called by 1 (stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 351–352)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This abstract method asks a real connector to list the source collections it knows how to sync. A connector implements it so the system can discover available streams in a standard shape.

**Data flow**: There is no meaningful input beyond the connector instance. The implementation in a concrete connector returns a list of `StreamSpec` objects, where each object describes one stream’s name, IDs, cursor behavior, and paging style. This base version only defines the required method; it does not provide the list itself.

**Call relations**: The sync machinery calls this on a concrete connector before fetching data. The returned stream descriptions are then passed into fetching and rendering so the rest of the system does not need service-specific knowledge.


##### `Connector.fetch_page`  (lines 355–363)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This abstract method is the standard way to ask a connector for records from one stream, page by page. Real connectors implement it with the service-specific API calls needed to retrieve data.

**Data flow**: It receives a stream description, an optional cursor bookmark, a resolved credential for authentication, and a base URL. A concrete implementation uses those inputs to contact the provider and asynchronously yields either plain lists of records or richer `StreamPage` objects that can also include deletes and a next cursor. This base version defines the contract but does not fetch anything itself.

**Call relations**: The sync runner calls this after choosing a stream from `Connector.streams`. For simple services, implementations may yield plain record batches; for incremental or deletion-aware services, they can yield `StreamPage` objects so the wider sync flow can advance bookmarks and remove vanished records.


##### `Connector.render`  (lines 365–376)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This method turns one raw source record into a title and readable body that the recall system can store. It gives every connector a basic fallback, while content-heavy connectors can override it to produce nicer text.

**Data flow**: It receives a record dictionary and the stream it came from. It looks for the first title-like field, such as `title`, `name`, `login`, or `subject`, and uses that as the title if present. It returns a pair: the chosen title and a body containing a heading plus the record serialized as sorted JSON.

**Call relations**: After records are fetched through `Connector.fetch_page`, the adapter can call this to turn each record into recallable content. Connectors for documents, email, or similar sources often replace this default so users see natural prose rather than a raw JSON dump.

*Call graph*: 1 external calls (dumps).


### `core/src/ufo/sources/rest.py`

`io_transport` · `source data fetching during sync runs`

Many services expose data through REST APIs, where the program must make web requests, authenticate, retry temporary failures, and keep asking for “the next page” until all records are fetched. Without this file, every individual connector would have to rewrite that same careful plumbing, and small mistakes could cause missing records, duplicate loops, unauthenticated calls, or stalled syncs.

The central piece is `RestConnector`, a base class for read-only API connectors. A specific provider connector supplies things like its API base URL and stream list, then either declares a standard pagination style or overrides the pagination method for unusual APIs. Think of this file like a reusable delivery route planner: each provider says where the stops are, and this code handles the vehicle, fuel, retries, and rules for moving from stop to stop.

It builds an asynchronous HTTP client, meaning it can wait on network calls without blocking other work. It supports credentials that come through a proxy transport, bearer token, or headers. It wraps GET and read-style POST calls in retry logic for temporary failures such as rate limits or server errors. It also includes several pagination loops: next cursor tokens, `Link` headers, page numbers, offset/limit, OData next links, and simple time-window requests. Safety checks stop runaway pagination when a provider repeats a cursor or never ends.

#### Function details

##### `get_path`  (lines 42–51)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a value from nested dictionary-like data using a dotted path such as `paging.next.cursor`. It is useful when different APIs hide records or cursor tokens inside nested response objects.

**Data flow**: It receives a mapping, a dot-separated path, and a default value. It walks one path part at a time through nested mappings; if any part is missing or not a mapping, it returns the default. If the full path exists, it returns the found value.

**Call relations**: Pagination helpers call this when they need to pull a cursor, a continuation flag, a server-reported page size, or nested records out of an API response. `records_at` also relies on it to find lists of records inside response envelopes.

*Call graph*: called by 3 (_get_cursor_pages, _get_offset_pages, records_at).


##### `list_or_empty`  (lines 54–58)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: Turns an unknown value into a clean list of record dictionaries. If the value is not a list, or if some list items are not dictionaries, it safely filters them out.

**Data flow**: It receives any value. If the value is not a list, it returns an empty list. If it is a list, it keeps only items that are dictionaries and returns those.

**Call relations**: This is the small safety filter used when parsing API bodies. `_response_list`, `records_at`, and `_get_odata_pages` call it before yielding records, so later code can assume it is working with dictionary-shaped records.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 61–64)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Returns a value only if it is a dictionary-shaped record; otherwise it returns an empty dictionary. It is a helper for connectors that need to reach into a nested object safely.

**Data flow**: It receives any value. If the value is a dictionary, that dictionary comes out unchanged. Anything else becomes `{}`.

**Call relations**: This helper is available for provider-specific connector code that imports it. It is not used inside this file’s listed call flow, but it matches the same defensive parsing style as `list_or_empty`.


##### `records_at`  (lines 67–72)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: Finds the list of records in an API response, either at the top level or under a named nested path. This lets one pagination loop work with APIs that wrap records in different envelope shapes.

**Data flow**: It receives response data and an optional path. If no path is given, it treats the data itself as the record list. If a path is given, it first uses `get_path` to find that nested value. In both cases it passes the value through `list_or_empty`, so the result is always a list of dictionaries.

**Call relations**: The pagination methods use this right after receiving a response body. `paginate_from_strategy` also creates a small parser for link-header pagination that calls `records_at` when records are nested inside a response object.

*Call graph*: calls 2 internal fn (get_path, list_or_empty); called by 5 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, paginate_from_strategy, parse).


##### `_int_or_none`  (lines 75–80)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: Converts a simple integer-like value into an integer, or returns nothing if it cannot be trusted as an integer. This is used when an API reports the page size it actually used.

**Data flow**: It receives any value. Real integers pass through unchanged. Decimal strings like `"100"` become `100`. Everything else becomes `None`.

**Call relations**: `_get_offset_pages` calls this when deciding how far to move the offset after a page. That lets the loop respect a server-provided limit when available, while falling back safely when it is not.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 83–86)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: Copies records and adds extra identifying information to each one, such as a parent ID or cloud ID. This helps later stages know where a record came from after a connector fans out across parent objects.

**Data flow**: It receives a collection of record dictionaries and keyword context values. For every record, it creates a new dictionary containing the original fields plus the context fields, then returns the new list.

**Call relations**: This helper is meant for provider-specific pagination code outside this base flow. It prepares records before they are yielded so downstream rendering or child fetches can trace their origin.


##### `next_link`  (lines 89–94)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: Extracts the URL for the next page from an HTTP `Link` header. Some APIs put pagination directions in headers instead of the JSON body.

**Data flow**: It receives HTTP response headers. It looks for the `link` header and searches for a link marked as `rel=next`. If found, it returns that URL; otherwise it returns `None`.

**Call relations**: `_get_link_header_pages` calls this after each request. If it returns a URL, the pagination loop follows it; if it returns nothing, the loop stops.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 97–102)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: Decides whether a failed request is worth trying again. It treats network transport failures and common temporary HTTP statuses, like rate limiting or server errors, as retryable.

**Data flow**: It receives an exception. If it is a network-level error, it returns `true`. If it is an HTTP status error, it checks the response status code against the retryable set. Other errors return `false`.

**Call relations**: `_send` uses this after a request fails. This function is the gatekeeper that prevents retries for permanent problems like most bad requests, while allowing retries for temporary trouble.

*Call graph*: called by 1 (_send).


##### `_raise_for_status`  (lines 105–116)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: Raises a clear error when an HTTP response is not successful, including a capped copy of the response body. This preserves the API’s actual explanation, such as an invalid filter message.

**Data flow**: It receives an HTTP response. Successful responses return with no change. Failed responses produce an `HTTPStatusError` whose message includes the status, request method, URL, and up to a limited amount of response text.

**Call relations**: `_send` calls this immediately after receiving a response. If it raises, `_send` can decide whether to retry or let the error fail the sync.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 119–123)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: Turns an HTTP response into a JSON dictionary, while treating empty responses as an empty dictionary. This keeps read helpers simple when an endpoint has no body.

**Data flow**: It receives an HTTP response. If the status is 204 or the body is empty, it returns `{}`. Otherwise it parses the response JSON and returns it as a dictionary.

**Call relations**: `_get` and `_post` call this after `_send` has completed a successful request. It is the standard object-response parser for this REST connector base.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 126–129)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Turns an HTTP response whose body should be a top-level list into a clean list of record dictionaries. Empty responses become an empty list.

**Data flow**: It receives an HTTP response. If there is no body, it returns `[]`. Otherwise it parses the JSON and passes it through `list_or_empty`, keeping only dictionary records.

**Call relations**: `_get_link_header_pages` uses this when a link-header-paginated endpoint returns records directly as a JSON array rather than inside an envelope.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 132–138)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: Stops pagination if it runs for too many pages without ending. This protects the sync from getting stuck forever on a broken or hostile API response.

**Data flow**: It receives a label describing the current connector/path and the number of pages fetched so far. If the count is above the maximum allowed, it raises an error. Otherwise it does nothing.

**Call relations**: Every built-in pagination loop calls this once per page. It acts like an emergency brake before the connector can hold resources indefinitely.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 141–147)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: Stops cursor-based pagination if the API repeats a cursor or next-link that has already been seen. A repeated cursor means the provider is not advancing to new data.

**Data flow**: It receives a label, the current token or URL, and a set of tokens already seen. If the token is already in the set, it raises an error. Otherwise it records the token in the set.

**Call relations**: Cursor, link-header, and OData pagination loops call this whenever they receive a continuation value. It catches endless loops earlier and more precisely than the broad page limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 156–157)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the list of streams this connector can read. A stream is a named collection of records, such as users, tickets, or repositories.

**Data flow**: It reads the class-level `streams_list` and returns a new list copy. The copy means callers can inspect or modify their local list without changing the class’s shared definition.

**Call relations**: The broader connector framework calls this to discover what data a provider connector offers. Subclasses fill in `streams_list`; this base method exposes it in the expected shape.


##### `RestConnector._make_client`  (lines 159–176)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the asynchronous HTTP client used for one account’s API requests. It applies the base URL, timeouts, standard JSON headers, and the resolved credential.

**Data flow**: It receives a base URL and a credential. It trims the URL, creates timeout settings, adds JSON headers, and then chooses one authentication path: proxy transport, bearer token, or explicit headers. It returns an `httpx.AsyncClient`, or raises an error if there is no usable authentication.

**Call relations**: `fetch_page` calls this at the start of a fetch. All later request helpers use the returned client, so this is where authentication and network settings enter the read flow.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 178–181)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a GET request and returns the response body as a dictionary. It is the common helper for JSON object endpoints.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It asks `_get_raw` to make the request with retries, then passes the successful response to `_json_or_empty`. The result is a dictionary, possibly empty.

**Call relations**: Cursor, offset, page-number, and time-window pagination use this when they only need the JSON body. `_get_raw` does the network work, and `_json_or_empty` shapes the result.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, paginate_from_strategy).


##### `RestConnector._get_raw`  (lines 183–187)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Performs a GET request and returns the full HTTP response, including headers. This is needed when pagination information lives outside the body.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It wraps `client.get(...)` in `_send`, so the request gets the shared retry and error behavior. The output is the successful raw response.

**Call relations**: `_get` uses this for ordinary JSON GETs. Link-header and OData pagination call it directly because they need headers or absolute next-link behavior from the response.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 189–194)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a read-style POST request and returns the JSON object body. Some APIs require POST even for search or query operations that do not write data.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It sends the POST through `_send` for retries and status checking, then parses the response with `_json_or_empty`. The result is a dictionary.

**Call relations**: Provider-specific connectors can call this from custom pagination code. It shares the same retry path as GET, but it does not introduce write support.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 196–202)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Performs a read-style POST request and returns the raw HTTP response. This is useful for APIs whose POST query response is not a normal JSON object, such as a top-level array.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It sends `client.post(...)` through `_send`, then returns the successful response unchanged.

**Call relations**: Provider-specific connectors can call this when they need custom parsing but still want the shared retry and error behavior.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 204–218)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: Runs one HTTP request with retry protection. It retries temporary network failures, rate limits, and selected server errors using exponential backoff, meaning the wait time grows after each failed attempt.

**Data flow**: It receives a callable that starts an HTTP request. It calls it, checks the response with `_raise_for_status`, and returns the response if successful. If a retryable error happens, it waits, doubles the delay, and tries again until the maximum attempts are used; non-retryable errors are raised immediately.

**Call relations**: `_get_raw`, `_post`, and `_post_raw` all hand their actual HTTP calls to this method. It centralizes retry behavior so every provider connector gets consistent treatment of temporary API problems.

*Call graph*: calls 2 internal fn (_is_retryable, _raise_for_status); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 220–249)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Fetches pages for one stream using a credential and base URL, then validates and flattens the records before yielding them to the sync engine. This is the main bridge from the connector framework into REST pagination.

**Data flow**: It receives a stream, an optional cursor, a credential, and a base URL. It builds an HTTP client, calls `paginate` to produce raw pages, skips empty pages, validates that records are dictionaries, applies `flatten` to each record, and yields either plain record lists or `StreamPage` objects with delete and cursor information preserved. It also closes async generators cleanly when finished.

**Call relations**: The core sync driver calls this when it wants data from a REST stream. Inside, `_make_client` prepares network access, `paginate` supplies pages, `_validate_page` checks shape, and `flatten` gives subclasses one last chance to reshape each record.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate); 1 external calls (__init__).


##### `RestConnector.paginate`  (lines 251–263)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Provides the default way to turn a stream’s declared pagination settings into pages of records. Connectors override this only when a provider’s API shape is too unusual for the built-in strategies.

**Data flow**: It receives an HTTP client, a stream, and an optional cursor. If the stream has no usable pagination declaration, it raises `NotImplementedError`. Otherwise it delegates to `paginate_from_strategy` and yields each page it produces.

**Call relations**: `fetch_page` calls this after creating the client. For standard streams, this method is just a doorway into `paginate_from_strategy`; for custom connectors, subclasses can replace it while still using `_get` or `_post` helpers.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (fetch_page).


##### `RestConnector.paginate_from_strategy`  (lines 265–358)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs one of the built-in pagination styles described by a stream’s pagination settings. It lets connectors describe common API paging patterns with data instead of custom code.

**Data flow**: It receives a stream, HTTP client, and optional cursor. It reads the stream’s pagination specification, chooses a request path, checks that required settings are present, then calls the matching helper for cursor, link-header, page-number, offset/limit, or time-window pagination. It yields lists of record dictionaries from each page.

**Call relations**: `paginate` calls this for standard streams. It hands work to `_get_cursor_pages`, `_get_link_header_pages`, `_get_page_number_pages`, `_get_offset_pages`, or a one-shot `_get`, depending on the declared strategy.

*Call graph*: calls 7 internal fn (_get, _get_cursor_pages, _get_link_header_pages, _get_offset_pages, _get_page_number_pages, _strategy_path, records_at); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 297–299)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Parses records from a link-header-paginated response when the records are nested inside the JSON body. It is a small local helper created only for that pagination case.

**Data flow**: It receives an HTTP response. If the response has content, it parses the JSON body; otherwise it uses an empty object. It then calls `records_at` with the configured record path and returns the resulting record list.

**Call relations**: `paginate_from_strategy` passes this parser into `_get_link_header_pages` when a `record_path` is configured. The link-header loop uses it instead of assuming the response body is a top-level list.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 360–388)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: Fetches pages from APIs that point to the next page using an HTTP `Link: rel=next` header. This style is common in web APIs such as GitHub-like endpoints.

**Data flow**: It receives a client, path, optional parameters, optional page size settings, and an optional record parser. It requests the first page, yields parsed records when present, reads the next-page URL from headers, checks that the URL has not repeated, and keeps following links until no next link remains.

**Call relations**: `paginate_from_strategy` calls this for the `next_link` strategy. It uses `_get_raw` for requests, `_response_list` or the supplied parser for records, `next_link` for continuation, and the pagination safety checks to prevent endless loops.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 390–422)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: Fetches pages from APIs that return a next-cursor token in the response body. A cursor is a bookmark the API gives back so the client can ask for the following page.

**Data flow**: It receives a client, path, paths for records and the next cursor, parameter names, page size settings, and extra parameters. It repeatedly builds a query, adds the previous cursor when present, fetches JSON with `_get`, extracts records, yields them, then extracts the next cursor. If there is no valid new cursor, it stops.

**Call relations**: `paginate_from_strategy` calls this for the `next_cursor` strategy. It uses `records_at` for records, `get_path` for the next token, `_bound_pages` for a hard page cap, and `_bound_cursor` to detect repeated bookmarks.

*Call graph*: calls 5 internal fn (_get, _bound_cursor, _bound_pages, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_odata_pages`  (lines 424–450)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Microsoft Graph or OData-style pages, where records live under `value` and the next page is named `@odata.nextLink`. OData is a common API convention used by Microsoft services.

**Data flow**: It receives a client, path, and optional parameters. It requests the current path, reads the `value` list as records, yields records when present, then replaces the path with `@odata.nextLink`. Only the first request uses the original parameters because later next links already contain their own continuation query.

**Call relations**: This helper is available to provider-specific connectors that need OData behavior. It uses `_get_raw`, `list_or_empty`, and the same page and cursor bounds as the other pagination loops.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 452–492)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: Fetches pages from APIs that use offset and limit numbers. The offset says how many records to skip, and the limit says how many to ask for next.

**Data flow**: It receives a client, path, record path, limit, parameter names, optional extra parameters, and optional response paths that describe whether more pages exist or what limit the server used. It starts at offset zero, requests a page, yields records, decides whether to stop, and then advances the offset before requesting again.

**Call relations**: `paginate_from_strategy` calls this for the `offset_limit` strategy. It uses `_get` for requests, `records_at` for records, `get_path` for optional continuation details, `_int_or_none` for server-reported limits, and `_bound_pages` as a runaway-loop guard.

*Call graph*: calls 5 internal fn (_get, _bound_pages, _int_or_none, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_page_number_pages`  (lines 494–523)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: Fetches pages from APIs that use page numbers such as page 1, page 2, and so on. It stops when a page contains fewer records than the requested page size.

**Data flow**: It receives a client, path, record path, page size, optional parameters, page parameter names, and a starting page. It requests the current page number, extracts and yields records, stops on a short page, or increments the page number and repeats.

**Call relations**: `paginate_from_strategy` calls this for the `page_number` strategy. It relies on `_get`, `records_at`, and `_bound_pages` to perform a safe standard numbered-page loop.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._strategy_path`  (lines 525–531)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: Supplies a request path for a stream when its pagination settings do not include one. The base version deliberately fails so subclasses must make the mapping explicit.

**Data flow**: It receives a stream. The base implementation does not compute a path; it raises an error explaining that no path was provided and no override exists.

**Call relations**: `paginate_from_strategy` calls this only when a stream’s pagination specification leaves `path` unset. Connectors with their own per-stream path table override it to provide the missing route.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 533–536)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Converts one API record into the flat dictionary shape that the sync writer expects. By default it leaves the record unchanged.

**Data flow**: It receives a record dictionary and the stream it belongs to. The base version returns the same record. Subclasses can override it to lift nested fields or remove envelopes before storage.

**Call relations**: `fetch_page` calls this for every validated record just before yielding the page onward. It is the final record-shaping hook in the base read flow.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 538–549)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: Checks that a pagination method yielded the expected shape: a list of dictionaries. It fails early with a clear message if a connector returns the wrong kind of data.

**Data flow**: It receives a page and stream. If the page is not a list, it raises a type error. If any item in the list is not a dictionary, it raises a type error naming the bad item type. If everything is valid, it returns without changing anything.

**Call relations**: `fetch_page` calls this before flattening and yielding records. This protects downstream sync code from confusing errors caused by malformed connector output.

*Call graph*: called by 1 (fetch_page).


### Sync replay and alerts
Transforms connector records into synchronized pages, records changes and deletions, replays changes downstream, and notifies subscribed users.

### `core/src/ufo/sources/backend.py`

`domain_logic` · `source sync run`

Connectors know how to talk to outside services, such as GitHub or Zendesk, and return records in pages. The rest of the system wants a simpler answer from each sync run: here are the pages found, here is the cursor to resume from next time, and here are any records that disappeared. This file is the adapter between those two worlds.

The main class, ConnectorBackend, runs exactly one connector stream for one account. It first asks an authentication proxy for a Credential, so secrets stay out of logs and away from sandboxed code. It then finds the requested stream, checks the provider base URL, calls the connector, and turns each provider record into a Page with a stable reference and a content hash.

A key job here is stopping large incremental syncs from running forever. Incremental syncs are capped at a fixed number of records per run. If the connector gives a real next cursor, the backend uses it. If not, it stores its own small “backfill envelope” in the cursor: where it started, how many records to skip next time, and the latest watermark seen. This is like leaving a bookmark plus a note saying “skip the first 5,000 pages when you reopen the book.” Full snapshot streams are different: they are never capped, because deletion detection only works if the system sees the whole collection.

#### Function details

##### `ConnectorBackend.fetch`  (lines 105–194)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one connector stream for one account and returns the system’s standard SyncResult. It gathers records, turns them into recallable pages, tracks deletions, and decides where the next run should resume.

**Data flow**: It receives a source config, an optional stored cursor, and source authentication information. It asks the auth proxy for a credential, finds the named stream, chooses the base URL, decodes any special backfill cursor, and then reads pages from the connector. Each record becomes a Page, deletion notices are collected, and cursor or watermark information is updated. The output is a SyncResult containing the pages, the next cursor, deletion references, and whether this was a full snapshot.

**Call relations**: This is the central flow in the file. As it runs, it calls ConnectorBackend._stream to find the requested stream, ConnectorBackend._decode_cursor to understand any adapter-owned resume state, ConnectorBackend._page to convert records into Pages, and _max_str to advance a string watermark. When a capped run cannot advance through a provider cursor, it creates a backfill envelope, serializes it with json.dumps, and may report a warning through ufo.o11y.warn before returning a partial SyncResult.

*Call graph*: calls 4 internal fn (_decode_cursor, _page, _stream, _max_str); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._stream`  (lines 196–200)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream definition with the requested name inside the connector. A stream definition says what kind of records to fetch, how they are keyed, and whether missing records mean deletions.

**Data flow**: It receives a stream name and reads the list of streams exposed by the connector. It compares each stream’s name to the requested name. It returns the matching StreamSpec, or raises an error if the connector does not offer that stream.

**Call relations**: ConnectorBackend.fetch calls this near the start of a sync run. The returned StreamSpec then guides the rest of the run: which records are fetched, how pages are named, whether the run is a snapshot, and how cursor fields are read.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 203–220)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Checks whether a stored cursor is one of this backend’s special backfill envelopes. If it is not, the cursor is treated as normal connector-owned state and left alone.

**Data flow**: It receives a cursor string or None. If there is no cursor, or if the cursor is not JSON, or if the JSON does not contain the reserved ufo_backfill key, it returns None. If the reserved key is present, it validates the stored origin, skip count, and watermark, then returns them as a _BackfillEnvelope. If that reserved data is malformed, it raises an error because this backend is supposed to be the only writer of that format.

**Call relations**: ConnectorBackend.fetch calls this before starting an incremental stream. The result tells fetch whether to pass the cursor straight to the connector or to re-drive an older position and skip records already landed in an earlier capped run.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 222–232)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page
```

**Purpose**: Turns one provider record into the project’s Page format. The Page has a stable source reference and a digest, which is a hash used to tell whether the page body changed.

**Data flow**: It receives a stream definition and one record dictionary. It asks _record_ref for a stable record identifier, asks the connector to render the record into text, hashes that text with SHA-256, and builds a Page containing the source reference, digest, and body. The returned Page is ready for the rest of the sync system to store or compare.

**Call relations**: ConnectorBackend.fetch calls this for every record that is not being skipped during a resumed backfill. This function delegates identifier choice to _record_ref, then hands fetch a normalized Page so fetch can collect it into the final SyncResult.

*Call graph*: calls 1 internal fn (_record_ref); called by 1 (fetch); 2 external calls (__init__, sha256).


##### `_record_ref`  (lines 235–239)

```
def _record_ref(stream: StreamSpec, record: dict[str, Any]) -> str
```

**Purpose**: Chooses the stable identifier used to name a record inside a stream. This makes repeated fetches, updates, and delete notices point to the same logical item.

**Data flow**: It receives a stream definition and a record. It reads the record’s primary key field. If that value is a string or integer, it returns it as text. If the primary key is missing or not a simple value, it creates a fallback identifier by JSON-encoding the whole record in a stable order and hashing it.

**Call relations**: ConnectorBackend._page calls this before building a Page. The identifier it returns becomes part of the Page source_ref, so it must match the way deletion entries and future re-fetches refer to the same record.

*Call graph*: called by 1 (_page); 2 external calls (sha256, dumps).


##### `_max_str`  (lines 242–247)

```
def _max_str(current: str | None, value: Any) -> str | None
```

**Purpose**: Keeps the greatest string watermark seen so far. A watermark is a remembered progress value, often a timestamp-like string, used to resume later syncs.

**Data flow**: It receives the current watermark and a candidate value from a record. If the candidate is not a string, it leaves the current watermark unchanged. If the candidate is a string and is greater than the current value, it returns the candidate; otherwise it returns the current value.

**Call relations**: ConnectorBackend.fetch calls this while reading records from an incremental stream that has a cursor field. The updated watermark helps fetch decide what cursor to save when the stream finishes or when it needs to store progress inside a backfill envelope.

*Call graph*: called by 1 (fetch).


### `core/src/ufo/sources/sync.py`

`domain_logic` · `startup registration, scheduled sync runs, and downstream indexing catch-up`

This file solves the problem of keeping UFO's internal page table in step with outside content. A source might be a local folder, or an extension-backed service such as a SaaS connector. The shared idea is simple: a backend fetches documents, the sync driver compares them with what was already stored, and only real changes are written.

The file defines the contract that source backends must follow. A backend receives typed configuration, an optional cursor that says where the last sync left off, and workspace authentication context. It returns pages, a new cursor, and information about deletions. The built-in FolderSource is the simplest example: it scans a directory, treats each file as one page, and reports a full snapshot.

SyncDriver is the worker. It claims sources that are due, fetches them, writes changed page bodies to the blob store, updates database rows, and marks missing pages as tombstoned rather than physically deleting them. A tombstone is like putting a removal notice on a page so later systems know to delete their derived data.

Finally, CorePageFeed lets an indexer read changed pages in a stable order using a cursor. This keeps syncing and indexing loosely connected: the sync job writes pages, and indexers catch up later without being triggered directly.

#### Function details

##### `StreamSkipped.__init__`  (lines 89–91)

```
def __init__(self, reason: str) -> None
```

**Purpose**: Creates a special skip signal with a human-readable reason. A connector uses this when a stream cannot be read for expected account or permission reasons, rather than because the system is broken.

**Data flow**: A reason string goes in. The exception stores that reason on itself and also passes it to the normal RuntimeError machinery. The result is an exception object that the sync driver can recognize and treat as a skipped run.

**Call relations**: Many extension connectors raise this while paginating provider data when the provider refuses a stream. SyncDriver.run catches it, logs a skipped sync, and hands the source to _skip instead of treating it like a failure.

*Call graph*: called by 49 (paginate, paginate, paginate, paginate, _org_stream, paginate, paginate, paginate, paginate, paginate (+15 more)).


##### `SourceBackend.config_model`  (lines 125–125)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: Declares the exact configuration shape a source backend expects. This keeps source settings typed and checked instead of passing around an unclear dictionary of values.

**Data flow**: The backend exposes a Pydantic model class, which is a validation class for structured data. Sync code later feeds the stored source configuration into that model before fetching.

**Call relations**: SyncDriver._fetch relies on this property before calling the backend. Each backend implementation supplies its own model so the central sync code does not need to know every connector's settings.


##### `SourceBackend.fetch`  (lines 127–127)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Defines the main job every source backend must perform: read documents from its source and return what changed or what currently exists. It is the contract that lets the core sync driver work with folders, cloud services, and other connectors in the same way.

**Data flow**: Typed configuration, the previous cursor, and workspace authentication context go in. The backend talks to its source and returns a SyncResult containing pages, a next cursor, deletion information, and whether the result is a full snapshot.

**Call relations**: SyncDriver._fetch calls this method through the SourceBackend protocol. Concrete backends such as FolderSource or extension connectors provide the actual behavior.


##### `FolderSource.fetch`  (lines 141–151)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Reads all files from a configured local folder and turns them into sync pages. It is the built-in source backend for simple local document ingestion.

**Data flow**: A SourceConfig containing a root folder path goes in, along with an unused cursor and auth object. The function reads the folder in a background thread, computes a SHA-256 digest for each file's text so changes can be detected, wraps each file as a Page, and returns a SyncResult marked as a full snapshot.

**Call relations**: SyncDriver._fetch can call this through the SourceBackend interface when a source row names the folder backend. It delegates file walking to FolderSource._read, then hands the resulting SyncResult back to the driver for comparison and database writes.

*Call graph*: 5 external calls (__init__, __init__, to_thread, sha256, Path).


##### `FolderSource._read`  (lines 154–161)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: Scans a directory and returns the text of every file inside it. It keeps the folder backend's filesystem work separate from the sync result-building code.

**Data flow**: A filesystem path goes in. The function checks that it is a directory, walks every file below it in sorted order, reads each file as UTF-8 text, and returns pairs of relative file path and file contents. If the folder itself is missing, it raises FileNotFoundError.

**Call relations**: FolderSource.fetch calls this using asyncio.to_thread so blocking disk reads do not stall the async event loop. Its output becomes the raw material for Page objects.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 164–171)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object]) -> UUID
```

**Purpose**: Creates a stable database ID for a source based on workspace, backend name, and source configuration. This prevents the same configured source from being inserted again every time the app restarts.

**Data flow**: A workspace ID, backend name, and configuration mapping go in. The configuration is converted to sorted JSON so the same settings always produce the same text, then uuid5 creates a deterministic UUID. That UUID comes out as the source row ID.

**Call relations**: register_sources calls this while installing configured sources at boot. Because the ID is deterministic, the registration step can safely check whether the row already exists.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 174–177)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: Creates a stable database ID for one page inside one source. This lets repeated fetches, updates, and deletion notices all point to the same page row.

**Data flow**: A source ID and a source-specific page reference go in. The function combines them into a namespaced string and turns it into a deterministic UUID. The resulting UUID is used as the page's database key.

**Call relations**: SyncDriver._commit calls this for fetched pages and explicit deletes. That lets the driver compare new data to prior page rows and update or tombstone the right record.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `register_sources`  (lines 180–214)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: Ensures that sources listed in configuration exist in the database. It is a startup step that turns configured source entries into durable source rows for the sync job to pick up later.

**Data flow**: A tuple of configured SourceEntry objects goes in. The function opens a workspace transaction, reads the workspace ID, computes each source's stable ID, checks whether that source already exists, and inserts a new source row when needed. It changes the database but returns no value.

**Call relations**: This function calls source_row_id to avoid duplicate rows. It runs outside the normal sync polling loop; once it has inserted source rows, SyncDriver.candidate_workspaces and SyncDriver._claim_due can find them when they are due.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `SyncDriver.candidate_workspaces`  (lines 242–262)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces have at least one source ready to sync. This saves work by not opening per-workspace sync runs when nothing is due.

**Data flow**: It reads the current time and queries the owner-level database view for source rows whose next sync time has arrived, which are not removed, and which are unclaimed or have an expired claim. It returns a tuple of workspace IDs.

**Call relations**: A scheduler or dispatcher can call this before binding a workspace-specific SyncDriver.run. It uses owner_tx because it is looking across workspaces, while the actual sync writes later use workspace_tx.

*Call graph*: 4 external calls (now, or_, select, owner_tx).


##### `SyncDriver.run`  (lines 264–287)

```
async def run(self) -> None
```

**Purpose**: Runs one sync pass for the current workspace. It claims due sources, fetches each one, commits successful changes, and carefully releases or reschedules sources when something goes wrong.

**Data flow**: No direct input is needed beyond the SyncDriver's configured backends, blob store, database mode, and auth proxy. It creates a unique claim token, asks _claim_due for work, then for each source either fetches and commits results, records a skip, or records a failure with possible cursor reset. It changes source and page rows, writes blobs, and returns nothing.

**Call relations**: This is the main coordinator in the file. It calls _claim_due, _fetch, _commit, _skip, and _release, and it logs skipped or failed runs so operators can see what happened.

*Call graph*: calls 5 internal fn (_claim_due, _commit, _fetch, _release, _skip); 2 external calls (log, uuid4).


##### `SyncDriver._claim_due`  (lines 289–333)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: Reserves a batch of sources that are ready to sync so another worker does not do the same work at the same time. A claim is like putting a temporary 'I'm working on this' note on each source row.

**Data flow**: A claim token goes in. The function queries due source rows, using PostgreSQL row locking when available, sets claimed_by and claim_expires_at on the selected rows, and returns them as ClaimedSource objects. The database rows are changed to show they are leased.

**Call relations**: SyncDriver.run calls this at the start of a pass. The claimed sources it returns are then passed one by one into _fetch, _commit, _release, or _skip.

*Call graph*: called by 1 (run); 7 external calls (__init__, now, timedelta, or_, select, update, workspace_tx).


##### `SyncDriver._fetch`  (lines 335–341)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: Calls the correct backend for a claimed source and gets its latest documents. It bridges the generic database source row to the specific connector implementation.

**Data flow**: A ClaimedSource goes in. The function looks up the backend by name, validates the stored JSON configuration using that backend's config_model, builds SourceAuth from the workspace ID and auth proxy, and awaits backend.fetch. A SyncResult comes out, or an error is raised if the backend is missing or fails.

**Call relations**: SyncDriver.run calls this after claiming a source. On success, its SyncResult goes to _commit; on CursorExpired, StreamSkipped, or another exception, run chooses the appropriate recovery path.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 343–356)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: Turns a backend's fetch result into page storage updates. It writes only changed bodies and prepares the lists of pages to update, keep, or tombstone.

**Data flow**: A claimed source and SyncResult go in. The function reads prior page digests through _prior_pages, computes each fetched page's stable ID with page_id_for, compares digests to skip unchanged pages, writes changed bodies to the blob store, converts explicit delete references into page IDs, and passes the prepared work to _write. The output is database and blob-store changes, not a returned value.

**Call relations**: SyncDriver.run calls this after _fetch succeeds. It delegates the database snapshot and tombstone rules to _write after doing the comparison and blob writes.

*Call graph*: calls 3 internal fn (_prior_pages, _write, page_id_for); called by 1 (run).


##### `SyncDriver._prior_pages`  (lines 358–371)

```
async def _prior_pages(self, source_id: UUID) -> dict[UUID, tuple[str, bool]]
```

**Purpose**: Loads the current known pages for one source so the driver can tell what is new, changed, already deleted, or unchanged. This avoids rewriting bodies that have the same digest.

**Data flow**: A source ID goes in. The function queries the page table for that source's page IDs, digests, and tombstone flags. It returns a dictionary keyed by page ID, with each value holding the digest and whether the page is tombstoned.

**Call relations**: SyncDriver._commit calls this before comparing fetched pages. The returned map lets _commit decide which bodies must be written to the blob store and which rows can be left alone.

*Call graph*: called by 1 (_commit); 2 external calls (select, workspace_tx).


##### `SyncDriver._write`  (lines 373–446)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[tuple[UUID, str, str]], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> None
```

**Purpose**: Applies the final sync result to the database: insert or update changed pages, mark deleted pages as tombstones, and reschedule the source for its next normal sync. This is where the database becomes the official record of the fetch.

**Data flow**: It receives the claimed source, next cursor, changed page details, fetched page IDs, explicit deleted page IDs, and a snapshot flag. It timestamps the write, upserts changed pages, tombstones explicit deletes, tombstones missing pages if this was a full snapshot, clears the claim, resets error count, stores the new cursor, and sets the next sync time. It returns nothing but updates database rows.

**Call relations**: SyncDriver._commit calls this after preparing changed and deleted page lists. PageFeed later depends on the updated_at timestamps written here to replay changes in the correct order.

*Call graph*: called by 1 (_commit); 6 external calls (now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._release`  (lines 448–472)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool) -> None
```

**Purpose**: Releases a source after a failed sync and delays the next attempt. The delay grows after repeated errors so a broken source does not hammer a provider or block other sources.

**Data flow**: A claimed source and a cursor_reset flag go in. The function increments the source's error count, computes a capped exponential backoff, optionally clears the cursor, clears the claim, and writes the next retry time to the source row. It returns nothing.

**Call relations**: SyncDriver.run calls this for ordinary exceptions and for CursorExpired. If the cursor expired, run passes cursor_reset as true so the next fetch starts fresh instead of reusing a rejected cursor.

*Call graph*: called by 1 (run); 4 external calls (now, timedelta, update, workspace_tx).


##### `SyncDriver._skip`  (lines 474–491)

```
async def _skip(self, source: ClaimedSource) -> None
```

**Purpose**: Releases a source after a deliberate skip without treating it as a failure. This preserves existing pages when a provider says a stream is unavailable for permission, plan, or account reasons.

**Data flow**: A claimed source goes in. The function clears the claim, schedules the next normal sync interval, resets the error counter, and leaves the cursor and pages unchanged. It returns nothing.

**Call relations**: SyncDriver.run calls this only after catching StreamSkipped. Because _commit is not called, no fetched pages are written and no snapshot deletion sweep can accidentally tombstone existing content.

*Call graph*: called by 1 (run); 4 external calls (now, timedelta, update, workspace_tx).


##### `PageFeed.pages_changed_since`  (lines 525–525)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Defines the interface an indexer uses to read page changes after a cursor. It is the handoff point between source syncing and downstream indexing.

**Data flow**: A cursor and a maximum batch size go in. An implementation returns a PageBatch containing ordered page changes and a next cursor to resume from later.

**Call relations**: CorePageFeed.pages_changed_since implements this protocol. Extension contexts can expose a PageFeed so indexers do not need to know how pages are stored internally.


##### `CorePageFeed.pages_changed_since`  (lines 537–579)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Reads recently changed pages from the core database and includes their body text when the page still exists. This lets an indexer catch up safely, one ordered batch at a time.

**Data flow**: A cursor string and requested limit go in. The function builds a query ordered by updated_at and page ID, applies the cursor if present, caps the batch size, reads page rows, loads each non-tombstoned body from the blob store, uses an empty body for tombstones, and returns a PageBatch with changes plus the next cursor. It does not modify the database.

**Call relations**: This is the concrete PageFeed implementation used by downstream indexers. It relies on SyncDriver._write having stamped page updates in a strict order, then hands each PageChange to the caller so derived indexes can add, update, or remove their own records.

*Call graph*: 8 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx, UUID).


### `extensions/page_alerts/ufo_ext_page_alerts/alerts.py`

`domain_logic` · `request handling and page-change hook`

This file is the “page watch” feature for an extension. A user can say, in a chat, that they want to watch synced pages for a topic such as “pricing changes” or “security incidents.” The file records that watch with the topic, the chat conversation, and the agent that should respond later. Without this file, page changes could still be synced, but nobody would be notified when a change mattered to a topic they cared about.

It has two main halves. The tool functions are used during conversation: one creates a watch, one lists existing watches, and one cancels a watch. Watch names are cleaned into simple safe labels, like turning “Security Alerts!” into “security-alerts,” so they can be stored and looked up consistently.

The hook function runs when the page-sync system reports changed pages. It loads all saved watches, skips deleted pages, and takes a short excerpt from each changed page. For every page-and-watch pair, it asks the configured language model a narrow yes/no question: does this document concern this watch topic? If the model says “MATCH,” it invokes a new turn in the original conversation so the user is alerted in the same place where they asked for the watch. It also uses an idempotency key, which is like a receipt number, so replaying the same page-change batch does not send duplicate alerts.

#### Function details

##### `_slug`  (lines 38–42)

```
def _slug(raw: str) -> str
```

**Purpose**: This helper turns a user-provided watch name into a simple storage-safe label. It makes names lowercase, replaces runs of non-letter-or-number characters with dashes, and rejects names that contain no usable characters.

**Data flow**: It receives a raw name string. It reads the text, cleans it into a short slug, and returns that slug. If the cleaned result would be empty, it raises an error instead of allowing an unusable watch name.

**Call relations**: When a user creates a watch, watch_pages uses this helper to choose the stored watch name. When a user cancels a watch, cancel_page_watch uses the same cleaning rule so the name they type points to the same stored key.

*Call graph*: called by 2 (cancel_page_watch, watch_pages); 1 external calls (sub).


##### `watch_pages`  (lines 45–66)

```
async def watch_pages(ctx: ToolContext, args: WatchPagesInput) -> ToolResult
```

**Purpose**: This is the chat tool that creates a new page watch. It remembers the topic, the current conversation, and the current agent so a later matching page change can alert the same conversation.

**Data flow**: It receives the tool context and the user’s requested topic and optional name. It checks that extension services are available, turns the chosen name into a slug, stores the watch under a key starting with "watch:", and returns a short confirmation message to the chat.

**Call relations**: This function is called when the user asks to watch pages. It relies on _slug to create a consistent watch key, writes the watch into the extension store, then builds a ToolResult with TextContent so the user sees that the watch is active.

*Call graph*: calls 1 internal fn (_slug); 2 external calls (__init__, __init__).


##### `list_page_watches`  (lines 69–79)

```
async def list_page_watches(ctx: ToolContext, args: ListPageWatchesInput) -> ToolResult
```

**Purpose**: This is the chat tool that shows the user which page watches currently exist. It gives a simple list of watch names and their topics, or says there are none.

**Data flow**: It receives the tool context and an empty input object. It checks that extension services are available, reads all stored entries whose keys start with "watch:", converts each stored record into readable fields, and returns a text response containing the list.

**Call relations**: This function is called when the user asks what watches are active. It uses _watch_fields to safely read each stored watch record before formatting the reply, then returns the result through TextContent and ToolResult.

*Call graph*: calls 1 internal fn (_watch_fields); 2 external calls (__init__, __init__).


##### `cancel_page_watch`  (lines 82–89)

```
async def cancel_page_watch(ctx: ToolContext, args: CancelPageWatchInput) -> ToolResult
```

**Purpose**: This is the chat tool that removes an existing page watch. It lets a user stop receiving alerts for a named watch.

**Data flow**: It receives the tool context and the watch name to cancel. It checks that extension services are available, normalizes the name with _slug, looks up the matching stored watch, raises an error if it does not exist, deletes it if it does, and returns a confirmation message.

**Call relations**: This function is called when the user asks to cancel a watch. It uses the same _slug helper as watch_pages so creation and cancellation agree on the storage key, then reports the outcome through TextContent and ToolResult.

*Call graph*: calls 1 internal fn (_slug); 2 external calls (__init__, __init__).


##### `on_page_change`  (lines 92–146)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook runs after synced pages change and decides whether any saved watch should produce an alert. For each changed page and each watch, it asks the model for a tight match/no-match decision, then invokes an alert turn for matches.

**Data flow**: It receives a hook context whose payload should be a batch of page changes. It verifies the payload type, loads stored watches, checks that an off-turn model is available, skips tombstoned pages, clips each page body to a safe excerpt length, and asks the model whether the excerpt matches each watch topic. If the answer includes "MATCH," it converts the saved conversation and agent IDs back into UUID values and invokes the extension with an alert prompt. It returns no visible hook result, but it may create alert turns as a side effect.

**Call relations**: The page-change system calls this function when new page-change batches arrive. It uses _watch_fields to read each saved watch, builds Message and ModelRequest objects for the model classification step, and uses UUID conversion before handing the alert back to the extension runtime. The idempotency key ties the alert to the watch and page digest so repeated delivery of the same change does not duplicate the alert.

*Call graph*: calls 1 internal fn (_watch_fields); 3 external calls (__init__, __init__, UUID).


##### `_watch_fields`  (lines 149–154)

```
def _watch_fields(key: str, value: object) -> tuple[str, str, str]
```

**Purpose**: This helper validates and unpacks one stored watch record. It makes sure the record contains the topic, conversation ID, and agent ID in the expected string form.

**Data flow**: It receives the storage key and the stored value object. If the value has the expected fields and types, it returns them as a three-part tuple. If the record is missing fields or has the wrong shape, it raises an error that names the malformed watch.

**Call relations**: list_page_watches uses this helper when turning stored watches into a readable list. on_page_change uses it before checking a watch against changed pages, so only well-formed watch records are used to produce alerts.

*Call graph*: called by 2 (list_page_watches, on_page_change).

## 📊 State Registers Touched

- `reg-workspace-context` — The current workspace and member context that keeps every request acting inside the right tenant boundary.
- `reg-connected-credentials` — The encrypted store of outside account connections and secrets that tools and sync jobs may use safely.
- `reg-permission-grants` — The permission ledger saying which agent may use which connected account or provider access.
- `reg-extension-state-store` — The per-workspace key-value storage area where extensions keep their own persistent settings and data.
- `reg-capability-registry` — The loaded menu of extension-provided routes, tools, skills, hooks, jobs, credentials, models, and search backends.
- `reg-connector-catalog` — The shared directory of external service connectors and broker-backed provider access.
- `reg-search-index-state` — The searchable content indexes, chunks, embeddings, and search backend choices used for recall and source replay.
- `reg-egress-proxy-state` — The controlled network gateway state that decides which outside sites sandboxed work may contact and how usage is counted.
- `reg-source-sync-state` — The remembered external sources, imported pages, raw bodies, change records, cursors, errors, and deletion markers.
- `reg-knowledge-graph` — The stored people, companies, things, and relationships used as structured background knowledge.
- `reg-workspace-object-catalog` — The named workspace objects and object-type registry used to list, inspect, validate, change, or delete stored things.
- `reg-database-connection-pool` — The process-wide database engine/session factory and connection pool used by request handlers, workers, schedulers, and persistence code.
- `reg-page-alert-watch-state` — The stored watches/subscriptions and notification targets used to replay source page changes into chat alerts.
- `reg-adapter-implementation-registry` — Process-wide mapping from configured backend/provider names to implementation adapters for Redis hubs, sandboxes, browsers, models, search, sources, and related services.
- `reg-http-client-pools` — Shared outbound HTTP client/session pools and retry-capable transport state used for provider APIs, OAuth/credential bridges, connectors, model calls, billing, email, and other integrations.
