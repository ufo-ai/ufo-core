# Provider polling and page storage  `stage-14.1`

This stage is the system’s main intake and storage loop for outside information. It talks to many providers, reads records in safe batches, turns them into standard pages, stores the page text and details, and tells the indexing system which pages changed.

The connector groups are the adapter plugs. Some read Markdown from folders or GitHub repositories. Others register available connectors and prepare account feeds when a user links a service. The Google, workplace, engineering, CRM, support, finance, HR, and recruiting connectors each know how to ask their own service for data, follow that service’s paging rules, and reshape the results into the same internal format.

The shared files are the machinery underneath. The REST helper provides the web client, retries, and page-by-page fetching. The connector contract defines what every adapter must provide and how to walk partitions, such as channels or repositories, without losing progress. The backend turns connector output into stored sync results, including changes, deletions, and warnings. The sync file ties it together by saving page bodies and exposing changed pages for indexing.

## Sub-stages

- [Markdown and repository-backed page sources](stage-14.1.1.md) `stage-14.1.1` — 3 files
- [Source connector registration and account bootstrap](stage-14.1.2.md) `stage-14.1.2` — 2 files
- [Google provider connectors](stage-14.1.3.md) `stage-14.1.3` — 8 files
- [Workplace communication and knowledge connectors](stage-14.1.4.md) `stage-14.1.4` — 5 files
- [Work management, engineering, and operational connectors](stage-14.1.5.md) `stage-14.1.5` — 10 files
- [CRM, sales, advertising, and marketing connectors](stage-14.1.6.md) `stage-14.1.6` — 11 files
- [Customer support connectors](stage-14.1.7.md) `stage-14.1.7` — 3 files
- [Finance, billing, spend, and document-commerce connectors](stage-14.1.8.md) `stage-14.1.8` — 11 files
- [HR and recruiting connectors](stage-14.1.9.md) `stage-14.1.9` — 6 files

## Files in this stage

### Connector contracts and REST traversal
Defines the shared connector shape and REST polling utilities used to safely read provider streams and walk paginated or partitioned data.

### `core/src/ufo/runtime/sources/rest.py`

`io_transport` · `request handling`

Many outside services expose data through REST APIs, where a connector must ask for one page of records at a time and keep asking until there are no more. Without this file, every REST connector would have to repeat the same fragile work: build an authenticated HTTP client, retry temporary failures, understand pagination, reject malformed results, and avoid infinite loops when an API behaves badly.

`RestConnector` is the reusable base class for those connectors. A provider-specific connector supplies things like its name, base URL, stream list, and sometimes custom pagination. This base class then does the routine work. It opens an async HTTP client, meaning network waits do not block other work. It sends GET or read-only POST requests through one retry wrapper, so rate limits and temporary server errors are treated consistently. It also includes several pagination patterns: cursor tokens, `Link: rel=next` headers, offset and limit, page numbers, and Microsoft-style OData next links.

Think of it like a careful librarian fetching boxes from a remote archive. It knows how to ask again if the archive is temporarily busy, how to follow “next box” labels, and when to stop if the labels loop forever. Before yielding data onward, it checks that each page is a list of dictionary-like records and gives connectors a hook to flatten records into the final shape.

#### Function details

##### `list_or_empty`  (lines 50–54)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: This helper safely turns a value into a list of record dictionaries. If the value is not a list, or if some list items are not dictionaries, it filters them out instead of letting bad shapes flow onward.

**Data flow**: It receives any value. If that value is a list, it keeps only the items that look like records, meaning dictionaries; otherwise it returns an empty list. The result is always a list that later pagination code can safely loop over.

**Call relations**: Pagination helpers call this when reading API response bodies. It supports `records_at`, `_response_list`, and the OData pager so those functions do not have to repeat shape checks.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 57–60)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This helper safely treats a value as one record-like object. If the value is not a dictionary, it returns an empty dictionary.

**Data flow**: It receives any value. A dictionary passes through unchanged; anything else becomes `{}`. It does not change anything outside itself.

**Call relations**: This is a small companion to `list_or_empty`, useful for connectors that need to pull a nested object out of a larger response. It is available as shared record-shaping support even though this file does not call it directly.


##### `records_at`  (lines 63–68)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: This helper extracts a list of records from either the whole response body or from a named nested path inside it. It lets connectors describe where records live without writing custom extraction code each time.

**Data flow**: It receives parsed response data and an optional path. With no path, it treats the whole value as the record list; with a path, it first walks into that location and then keeps only dictionary records. It returns a clean list of records, or an empty list if the shape is not as expected.

**Call relations**: Cursor, offset, page-number, and link-header pagination use this when an API wraps records inside an envelope object. It relies on `get_path` to walk nested data and on `list_or_empty` to enforce a safe list shape.

*Call graph*: calls 1 internal fn (list_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, parse); 1 external calls (get_path).


##### `_int_or_none`  (lines 71–76)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: This helper accepts numbers that may arrive as either real integers or decimal strings. It returns an integer only when the value is clearly usable.

**Data flow**: It receives any value. Integers pass through; strings containing only decimal digits are converted to integers; everything else becomes `None`. That result helps callers avoid guessing about invalid values.

**Call relations**: The offset pager uses this when an API reports the actual page size it applied. If the reported value is not usable, the pager falls back to safer ways to advance.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 79–82)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: This helper copies records and adds extra context fields, such as a parent ID or cloud site ID. It helps later stages know where each record came from.

**Data flow**: It receives an iterable of record dictionaries plus named context values. It creates a new list where each record is copied and extended with those context values. The original records are not modified.

**Call relations**: Provider-specific connectors can use this during fan-out, where one request leads to child requests. It prepares records so downstream rendering or syncing can resolve their origin.


##### `next_link`  (lines 85–90)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper finds the next-page URL from an HTTP `Link` header. Some APIs put pagination instructions in headers instead of the response body.

**Data flow**: It receives response headers. It reads the `link` header and searches for a link marked as `rel=next`; if found, it returns that URL, otherwise it returns `None`.

**Call relations**: The link-header pagination loop calls this after each fetched page. Its answer decides whether `_get_link_header_pages` stops or follows another URL.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 93–98)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: This helper decides whether a request failure is temporary enough to try again. It treats network transport failures and selected HTTP status codes, such as rate limits and server errors, as retryable.

**Data flow**: It receives an exception. It checks whether the exception is a network error or an HTTP error with a status code known to be temporary. It returns `true` for retryable cases and `false` otherwise.

**Call relations**: `_send` uses this after a request fails. If this helper says the error is not retryable, `_send` stops immediately and lets the error surface.

*Call graph*: called by 1 (_send).


##### `_retry_after`  (lines 101–121)

```
def _retry_after(error: BaseException) -> float | None
```

**Purpose**: This helper reads an API's requested wait time from a `Retry-After` header. It only trusts that header on statuses where waiting makes sense, such as rate limits or temporary unavailability.

**Data flow**: It receives an exception. If it is an HTTP error with a relevant status, it reads the `retry-after` header, parses it as a finite non-negative number, and returns that many seconds. Missing, invalid, negative, infinite, or irrelevant values become `None`.

**Call relations**: `_retry_wait` calls this while choosing how long to sleep before the next attempt. This keeps bad header values from poisoning the shared async event loop.

*Call graph*: called by 1 (_retry_wait); 1 external calls (isfinite).


##### `_retry_wait`  (lines 124–141)

```
def _retry_wait(error: BaseException, delay: float) -> float
```

**Purpose**: This helper chooses the delay before retrying a failed request. It combines exponential backoff, provider-supplied `Retry-After` advice, caps, and random jitter so many workers do not retry at the exact same moment.

**Data flow**: It receives the error and the current backoff delay. It checks for a usable `Retry-After`, compares that with the normal retry delay, clamps both to safe maximums, then returns a randomized wait time within the allowed range.

**Call relations**: `_send` calls this whenever a retryable request fails. It uses `_retry_after` for provider hints and hands the final sleep time back to `_send`.

*Call graph*: calls 1 internal fn (_retry_after); called by 1 (_send); 1 external calls (uniform).


##### `_raise_for_status`  (lines 144–155)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: This helper turns unsuccessful HTTP responses into useful errors. Unlike the default behavior, it includes a capped slice of the response body so the real API complaint is visible.

**Data flow**: It receives an HTTP response. Successful responses return unchanged; failures raise an `HTTPStatusError` whose message includes status, request method, URL, and part of the response body.

**Call relations**: `_send` calls this after every response. That makes all GET and POST helpers fail in the same informative way when an API rejects a request.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 158–162)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: This helper parses an HTTP response as a JSON object, while treating empty responses as an empty dictionary. It is used for endpoints expected to return object-shaped data.

**Data flow**: It receives an HTTP response. If the response is empty or has status 204, it returns `{}`; otherwise it parses the response body as JSON and returns it as a dictionary.

**Call relations**: `_get` and `_post` call this after `_send` succeeds. It gives higher-level pagination code a simple parsed object instead of raw HTTP bytes.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 165–168)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: This helper parses an HTTP response that is expected to be a top-level list of records. Empty responses become an empty list.

**Data flow**: It receives an HTTP response. If there is no content, it returns `[]`; otherwise it parses JSON and keeps only dictionary items if the result is a list.

**Call relations**: The link-header pager uses this when no custom record parser is supplied. It depends on `list_or_empty` to avoid passing through non-record values.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 171–177)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: This safety check stops a pagination loop that runs for too many pages. It prevents a broken or hostile API from making a sync run spin forever.

**Data flow**: It receives a label describing the request and the number of pages fetched so far. If the count is above the maximum, it raises an error; otherwise it does nothing.

**Call relations**: Every built-in pagination loop calls this once per page. If an API never provides a stopping signal, this function forces the run to fail clearly instead of hanging.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 180–186)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: This safety check detects pagination cursors or next links that repeat. A repeated cursor means the provider is sending the same page again and again.

**Data flow**: It receives a label, a cursor token or link, and a set of previously seen tokens. If the token was already seen, it raises an error; otherwise it records the token in the set.

**Call relations**: Cursor, link-header, and OData pagination loops call this before following the next page marker. It catches infinite loops earlier than the general page-count limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 195–196)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This method returns the streams declared by a REST connector. A stream is a named collection of records the system can read, such as users, issues, or tickets.

**Data flow**: It reads the connector class's `streams_list` and returns a new list copy. Returning a copy prevents callers from accidentally modifying the shared class-level list.

**Call relations**: The wider source system asks connectors for their available streams. Provider-specific subclasses fill in `streams_list`, and this method exposes it in the common connector interface.


##### `RestConnector._make_client`  (lines 198–215)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method builds the authenticated async HTTP client used for one account. It supports credentials carried through a broker transport, a bearer token, or explicit authentication headers.

**Data flow**: It receives a base URL and resolved credential. It trims the base URL, sets JSON headers and timeouts, then chooses the right authentication path. It returns an `httpx.AsyncClient`, or raises an error if there is no usable authentication.

**Call relations**: `fetch_page` calls this before pagination begins. All lower-level request helpers then use the client it created, so authentication and timeout behavior stay consistent.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 217–220)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This method performs a GET request and returns the parsed JSON object. It is the usual read helper for API endpoints that return object-shaped responses.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It asks `_get_raw` to send the request with retries, then converts the response through `_json_or_empty`. The output is a dictionary.

**Call relations**: Cursor, offset, and page-number pagination call this when they need the response body. It delegates network reliability to `_get_raw` and response parsing to `_json_or_empty`.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 3 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages).


##### `RestConnector._get_raw`  (lines 222–226)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: This method performs a GET request and returns the raw HTTP response. It is useful when pagination information lives in headers or when a caller needs response details beyond the JSON body.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It wraps `client.get` in `_send`, so temporary failures are retried. It returns the final successful response object.

**Call relations**: `_get` builds on this for normal JSON reads. Link-header and OData pagination call it directly because they need headers or next-link behavior from the full response.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 228–233)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This method performs a read-only POST request and returns a parsed JSON object. It exists for APIs that expose search or listing endpoints through POST even though no data is being written.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It sends the request through `_send` for retry behavior, then parses an empty-or-JSON response into a dictionary.

**Call relations**: Provider-specific connectors can call this for read endpoints such as search APIs. It shares the same retry and error behavior as GET requests.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 235–241)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: This method performs a read-only POST request and returns the raw HTTP response. It is for APIs whose read response is not a simple object, such as a top-level array or streamed batch shape.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It sends `client.post` through `_send` and returns the successful response without parsing it.

**Call relations**: Provider-specific connectors can call this when they need custom parsing after a POST. It still uses the same retry wrapper as `_get_raw` and `_post`.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 243–264)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: This method is the shared retry wrapper for HTTP requests. It retries temporary network failures, rate limits, and selected server errors without making every request helper duplicate that logic.

**Data flow**: It receives a callable that starts one HTTP request. It runs the request, checks the status, and returns the response if successful. On retryable failure, it waits with backoff and jitter until attempts or total wait budget run out; then it raises the last error.

**Call relations**: `_get_raw`, `_post`, and `_post_raw` all hand their actual HTTP call to this method. It uses `_raise_for_status`, `_is_retryable`, `_retry_wait`, and `asyncio.sleep` to provide one consistent network reliability policy.

*Call graph*: calls 3 internal fn (_is_retryable, _raise_for_status, _retry_wait); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 266–303)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[
```

**Purpose**: This is the main read entry for one stream page sequence. It opens the HTTP client, runs pagination, validates record shapes, flattens records, and yields pages to the sync engine.

**Data flow**: It receives a stream, cursor, credential, base URL, user identity, and optional backfill date. It creates a client, gets an async page source, skips empty pages, validates each page, flattens each record, preserves delete and cursor metadata when present, and yields cleaned pages. It also closes async generators on exit.

**Call relations**: The core sync driver calls this when it wants records from a REST source. This method coordinates `_make_client`, `paginate_source`, `_validate_page`, and `flatten`, making it the bridge between provider-specific pagination and the rest of the sync pipeline.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate_source); 1 external calls (__init__).


##### `RestConnector.paginate_source`  (lines 305–318)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This method is a small extension point between `fetch_page` and the connector's pagination code. By default it ignores extra run context and calls `paginate`.

**Data flow**: It receives the client, stream, cursor, self user ID, and optional backfill date. The default implementation passes only the client, stream, and cursor into `paginate` and returns that async iterator.

**Call relations**: `fetch_page` calls this instead of calling `paginate` directly. Connectors that need the acting user or backfill floor can override this method without changing the rest of the fetch flow.

*Call graph*: calls 1 internal fn (paginate); called by 1 (fetch_page).


##### `RestConnector.paginate`  (lines 320–332)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This method yields raw pages of records for a stream. The default version only works when the stream declares one of the standard pagination strategies.

**Data flow**: It receives an HTTP client, stream, and optional cursor. If the stream has a usable pagination strategy, it delegates to `paginate_from_strategy` and yields each page. If no strategy is declared, it raises `NotImplementedError` so the connector must provide custom pagination.

**Call relations**: `paginate_source` calls this in the default flow. Provider-specific connectors can override it for unusual API shapes, while ordinary streams use `paginate_from_strategy`.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (paginate_source).


##### `RestConnector.paginate_from_strategy`  (lines 334–396)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method runs one of the built-in pagination patterns declared on a stream. It turns a stream's pagination settings into calls to the right page-fetching loop.

**Data flow**: It reads the stream's pagination specification, resolves the request path, checks that required fields are present, and then calls the matching helper for cursor, link-header, or offset-limit pagination. It yields each list of records produced by that helper.

**Call relations**: `paginate` calls this for streams with declared standard pagination. It hands off to `_get_cursor_pages`, `_get_link_header_pages`, `_get_offset_pages`, or `_strategy_path` as needed.

*Call graph*: calls 4 internal fn (_get_cursor_pages, _get_link_header_pages, _get_offset_pages, _strategy_path); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 366–368)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: This nested parser extracts records from a link-header paginated response when records are inside a named JSON path. It adapts header-based pagination to APIs that still wrap records in a body envelope.

**Data flow**: It receives a raw HTTP response. It parses the body as JSON when content exists, then uses `records_at` to pull records from the configured path. It returns a list of dictionary records.

**Call relations**: `paginate_from_strategy` creates this parser only for `next_link` streams with a `record_path`. `_get_link_header_pages` then calls it for each response instead of using the default top-level-list parser.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 398–426)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: This pagination loop follows `Link: rel=next` headers. It supports APIs where the response header, not the body, tells the client where the next page lives.

**Data flow**: It receives a client, initial path, optional parameters, page size settings, and optional record parser. It fetches the first page, yields records if any, reads the next link from headers, checks for loops, and repeats until no next link exists.

**Call relations**: `paginate_from_strategy` calls this for `next_link` pagination. It uses `_get_raw` for HTTP, `_response_list` or a custom parser for records, `next_link` for the continuation URL, and the bound checks for safety.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 428–460)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: This pagination loop follows cursor tokens stored in the response body. A cursor is a marker from the API saying where to resume for the next page.

**Data flow**: It receives a client, path, record path, cursor path, query parameter names, page size, and extra parameters. It repeatedly builds a query, fetches JSON, extracts records, yields them, reads the next cursor, checks for repeats, and stops when there is no valid cursor.

**Call relations**: `paginate_from_strategy` calls this for `next_cursor` pagination. It depends on `_get` for reliable JSON requests, `records_at` for record extraction, `get_path` for nested cursor lookup, and bound checks for infinite-loop protection.

*Call graph*: calls 4 internal fn (_get, _bound_cursor, _bound_pages, records_at); called by 1 (paginate_from_strategy); 1 external calls (get_path).


##### `RestConnector._get_odata_pages`  (lines 462–488)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This pagination loop reads Microsoft Graph and OData-style responses. In that format, records are usually under `value` and the next-page URL is in `@odata.nextLink`.

**Data flow**: It receives a client, path, and optional first-request parameters. It fetches the current URL, extracts records from `value`, yields them, reads `@odata.nextLink`, checks for repeats, and then follows that full next URL without reusing the original parameters.

**Call relations**: Provider-specific connectors can call this for Microsoft-style APIs. It uses `_get_raw`, `list_or_empty`, `_bound_pages`, and `_bound_cursor` to share the same safety and request behavior as the other pagers.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 490–530)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: This pagination loop reads APIs that use offset and limit parameters. The offset says how many records to skip, and the limit says how many to ask for.

**Data flow**: It receives a client, path, record path, limit, parameter names, optional extra parameters, and optional response fields that say whether more data exists. It fetches pages with increasing offsets, yields records, stops on no records, no more flag, or a short page, and advances the offset safely.

**Call relations**: `paginate_from_strategy` calls this for `offset_limit` pagination. It uses `_get` for requests, `records_at` for extraction, `get_path` for optional continuation fields, `_int_or_none` for reported page sizes, and `_bound_pages` for runaway protection.

*Call graph*: calls 4 internal fn (_get, _bound_pages, _int_or_none, records_at); called by 1 (paginate_from_strategy); 1 external calls (get_path).


##### `RestConnector._get_page_number_pages`  (lines 532–561)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: This pagination loop reads APIs that use page numbers, such as page 1, page 2, and so on. It stops when a page comes back shorter than the requested page size.

**Data flow**: It receives a client, path, record path, page size, optional parameters, parameter names, and start page. It fetches each numbered page, yields any records, stops when fewer records than requested arrive, and otherwise increments the page number.

**Call relations**: Provider-specific connectors can call this for page-numbered APIs. It uses `_get`, `records_at`, and `_bound_pages`, matching the safety style of the built-in strategy pagers.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at).


##### `RestConnector._strategy_path`  (lines 563–569)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: This method resolves a request path for a stream whose pagination settings did not include one. The base version raises an error because only a provider-specific connector knows its path table.

**Data flow**: It receives a stream. Instead of guessing, it raises `NotImplementedError` explaining that the path is missing and no override exists.

**Call relations**: `paginate_from_strategy` calls this when a pagination spec omits its path. Connectors with centralized per-stream paths can override it to supply the missing URL path.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 571–574)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method gives connectors a chance to reshape each record before it is yielded to the sync engine. The default behavior leaves the record unchanged.

**Data flow**: It receives one record dictionary and its stream. It returns the same record as-is, unless a subclass overrides the method to lift nested fields or otherwise flatten the shape.

**Call relations**: `fetch_page` calls this after validating each record. Provider-specific connectors override it when an API returns records wrapped in an envelope or nested structure that the sync writer should not see.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 576–587)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: This method checks that pagination produced the expected shape: a list of dictionary records. It catches connector mistakes early with clear errors.

**Data flow**: It receives a page value and stream. If the page is not a list, it raises a type error; if any item is not a dictionary, it raises a type error naming the bad item type. Valid pages pass through with no return value.

**Call relations**: `fetch_page` calls this before flattening and yielding data. This protects the downstream sync writer from surprising shapes produced by custom pagination code.

*Call graph*: called by 1 (fetch_page).


### `core/src/ufo/runtime/sources/connector.py`

`domain_logic` · `during source sync runs`

A connector is the project’s adapter for an outside service, such as a document app, email system, code host, or chat tool. This file gives all connectors the same vocabulary: a stream is one collection to sync, a page is one batch of records, a cursor is the saved bookmark for where the next sync should resume, and a render step turns a raw record into readable text for recall. Without this shared contract, every provider would report records, deletions, titles, and resume positions differently, and the rest of the system could not treat them uniformly.

The file also solves a harder problem: some streams are split into partitions, like “one channel at a time” or “one repository at a time.” `PartitionWalk` is the guide for that journey. It keeps a small saved map of each partition’s progress, decides whether to resume after an old watermark or continue walking backward through history, and yields normal `StreamPage` objects to the rest of the sync system. Think of it like a checklist plus bookmarks: it remembers which shelves were already scanned and where scanning stopped on each shelf.

The base `Connector` class is intentionally abstract. Real connectors provide their available streams and fetching code. This file supplies the stable defaults: how to find record identities, how to make fallback references, and how to render a basic JSON record if a richer provider-specific renderer is not supplied.

#### Function details

##### `get_path`  (lines 36–45)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a value from a nested dictionary using a dotted path such as `author.id`. This lets stream definitions point at IDs or timestamps that are not stored at the top level of a provider record.

**Data flow**: It receives a mapping, a dotted path, and an optional default value. It walks through the mapping one path part at a time; if any step is missing, is not a mapping, or becomes `None`, it returns the default. If every step succeeds, it returns the nested value it found.

**Call relations**: `record_key` calls this when a record’s declared primary key is not present as a simple top-level field. In that flow, `get_path` is the fallback reader that makes nested provider records usable.

*Call graph*: called by 1 (record_key).


##### `record_key`  (lines 48–60)

```
def record_key(record: Mapping[str, Any], primary_key: str) -> str | None
```

**Purpose**: Finds the stable provider identity for a record. The system uses this identity to know that the same outside record is still the same record across sync runs.

**Data flow**: It receives a record and the stream’s primary-key path. It first checks for that key directly on the record, then asks `get_path` to look through nested fields. If the value is a string or integer, it converts it to a string and returns it; if it is missing, empty, a boolean, or another unsuitable type, it returns `None`.

**Call relations**: `Connector.record_identity` calls this as the standard way to identify a record. Because `record_identity` is used by `Connector.render`, this helper also affects whether a record can get a fallback title when no title-like field is present.

*Call graph*: calls 1 internal fn (get_path); called by 1 (record_identity).


##### `PartitionWalk.stream`  (lines 261–289)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Runs a partitioned sync from a saved cursor and yields ordinary stream pages that the rest of the system can consume. It is the top-level driver for walking many partitions while preserving resumable progress.

**Data flow**: It receives the previously saved cursor string. It decodes that cursor into a per-partition progress map, asks the connector-provided partition iterator for each partition, and routes each one through either ordered or unordered walking. As pages come back, it yields `StreamPage` objects that include records, deletions, and an updated cursor. At the end, it may emit one final empty page to clean up saved cursor state.

**Call relations**: This method starts by calling `_decode`, then chooses `_stream_unordered` or `_stream_ordered` for each partition depending on the configured ordering. Those helpers produce page updates; `stream` may then call `_encode` to publish the final cleaned cursor state.

*Call graph*: calls 4 internal fn (_decode, _encode, _stream_ordered, _stream_unordered); 1 external calls (__init__).


##### `PartitionWalk._stream_unordered`  (lines 291–313)

```
async def _stream_unordered(self, partition: str, stored: dict[str, str | _Window], checkpoint: dict[str, str | _Window]) -> AsyncIterator[StreamPage]
```

**Purpose**: Walks one partition for a stream that has no timestamp or other ordering field to resume from. Its job is to avoid repeating partitions within a capped run, while allowing a full re-walk on a later completed run.

**Data flow**: It receives one partition name, the cursor state read at the start, and a mutable checkpoint map. If the partition was already marked as seen in the stored cursor, it stops immediately. Otherwise it asks the page factory for pages with an empty `PartitionBound`, yields each page as a `StreamPage`, and finally marks the partition as complete in the checkpoint. If the provider signals `PartitionSkipped`, it leaves the checkpoint unchanged for that partition.

**Call relations**: `PartitionWalk.stream` calls this for streams whose ordering is `none`. This helper calls `_encode` whenever it needs to attach the current checkpoint to an outgoing `StreamPage`, and it creates a blank `PartitionBound` because unordered streams have no after-or-before boundary to pass down.

*Call graph*: calls 1 internal fn (_encode); called by 1 (stream); 2 external calls (__init__, __init__).


##### `PartitionWalk._stream_ordered`  (lines 315–361)

```
async def _stream_ordered(self, partition: str, stored: dict[str, str | _Window], checkpoint: dict[str, str | _Window]) -> AsyncIterator[StreamPage]
```

**Purpose**: Walks one partition for a stream that can be resumed using an ordering value, such as a creation time or message timestamp. It carefully updates watermarks so a sync can stop and continue later without dropping records.

**Data flow**: It receives a partition name plus stored and current checkpoint maps. It asks `_ordered_state` what bounds to use, then calls the page factory with those bounds. For each page, it updates the highest seen value, and during newest-first backfill it also tracks the lowest value reached. It yields each page with an encoded cursor. If the backfill reaches the configured floor, or the provider is exhausted, it converts an in-progress window into a plain watermark. If the partition is skipped, it preserves the previous cursor state.

**Call relations**: `PartitionWalk.stream` calls this for ordered streams. This method depends on `_ordered_state` to decide the starting mode, calls `_encode` to attach updated progress to each `StreamPage`, and creates `_Window` objects when a newest-first backfill is paused mid-way.

*Call graph*: calls 2 internal fn (_encode, _ordered_state); called by 1 (stream); 2 external calls (__init__, __init__).


##### `PartitionWalk._ordered_state`  (lines 363–379)

```
def _ordered_state(self, stored: str | _Window | None) -> tuple[PartitionBound, str | None, str | None, str | None, bool]
```

**Purpose**: Interprets one partition’s saved cursor entry and decides how the next ordered walk should begin. It turns stored progress into a clear request boundary for the connector’s page factory.

**Data flow**: It receives the stored value for one partition, which may be absent, a plain watermark string, or an in-progress window with a high and until bound. If it sees a window, it returns a `PartitionBound` that continues walking downward. If it sees a string watermark, it returns a bound that fetches records after that value. If nothing is stored, it decides whether this is a newest-first backfill or a fresh ascending walk and returns the matching initial state.

**Call relations**: `_stream_ordered` calls this before asking the connector for pages. The returned `PartitionBound` is the instruction that tells the page factory whether to fetch newer records, continue an old backfill window, or start from scratch.

*Call graph*: called by 1 (_stream_ordered); 1 external calls (__init__).


##### `PartitionWalk._decode`  (lines 382–411)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: Turns a saved cursor string back into the per-partition progress map used by `PartitionWalk`. It is strict about malformed map entries so corrupted state does not silently skip or reprocess the wrong data.

**Data flow**: It receives a cursor string or `None`. Empty, non-JSON, or non-object cursor values are treated as no partition progress and become an empty map. If the cursor is a JSON object, each value is accepted only if it is a string watermark or a valid window object with the expected fields. Bad entries raise an error instead of being ignored.

**Call relations**: `PartitionWalk.stream` calls this at the start of a partitioned walk. The map it returns becomes the stored baseline that `_stream_unordered` and `_stream_ordered` compare against while producing new checkpoints.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 414–419)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: Turns the in-memory per-partition progress map into a JSON cursor string that can be saved and used on the next sync run.

**Data flow**: It receives a mapping from partition names to either string watermarks or `_Window` objects. It converts windows into plain dictionaries, then serializes the whole map as sorted JSON. The output is a stable cursor string.

**Call relations**: `_stream_ordered`, `_stream_unordered`, and `stream` call this whenever they need to put the latest checkpoint into a `StreamPage`. That encoded cursor is how the next run knows where to resume.

*Call graph*: called by 3 (_stream_ordered, _stream_unordered, stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 438–439)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Defines which streams a concrete connector can sync. A real connector implements this to list the outside-service collections it knows how to read.

**Data flow**: There is no implementation in this base class; subclasses return a list of `StreamSpec` objects. Those specs describe names, record IDs, cursor fields, deletion behavior, ordering, pagination, and any default backfill window.

**Call relations**: `ConnectedSources._register` calls this when a connector is registered, so the system can learn what streams the connector offers. The base method exists as a required contract that provider-specific connectors must fulfill.

*Call graph*: called by 1 (_register).


##### `Connector.fetch_page`  (lines 442–457)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None) -> AsyncIterator[list[dict[str, Any]]
```

**Purpose**: Defines the required fetching method for concrete connectors. Implementations use credentials and cursor information to retrieve batches of provider records.

**Data flow**: A concrete implementation receives a stream spec, previous cursor, resolved credential, base URL, optional current-user ID to exclude, and optional backfill floor. It asynchronously yields either plain lists of records or richer `StreamPage` objects that may include deletions and a next cursor. The base class itself provides only the required shape, not the fetching logic.

**Call relations**: No direct caller is shown in the provided call facts, but this abstract method is the central contract the sync runner relies on through concrete connector implementations. Provider-specific code supplies the actual API calls and yields pages in this shape.


##### `Connector.render`  (lines 459–479)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns one raw provider record into a readable title and body for recall. It gives every connector a safe default output, while allowing content-heavy connectors to override it with nicer prose.

**Data flow**: It receives a record and its stream spec. It first looks for a non-empty title-like field such as `title`, `name`, or `subject`. If none exists, it asks `record_identity` for the provider ID and `record_ref` for a reference, then builds a fallback title from the stream name and reference. It returns a pair: the title and a Markdown-like body containing a heading plus the record serialized as sorted JSON. If neither a title nor an identity exists, it raises an error.

**Call relations**: This method calls `Connector.record_identity` and `Connector.record_ref` when it needs a fallback title, then uses JSON serialization for the body. It is the default render hook that concrete connectors inherit unless they need provider-specific readable content.

*Call graph*: calls 2 internal fn (record_identity, record_ref); 1 external calls (dumps).


##### `Connector.record_identity`  (lines 481–483)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Returns the stable provider identity for a single record within a stream. This is the identity used to decide whether a provider record is addressable at all.

**Data flow**: It receives a record and stream spec. It passes the record and the stream’s primary key to `record_key`, then returns the resulting string identity or `None` if no valid identity can be found.

**Call relations**: `Connector.render` calls this when it needs to make a fallback title. This method delegates the actual lookup rules to `record_key`, keeping the connector-level API simple.

*Call graph*: calls 1 internal fn (record_key); called by 1 (render).


##### `Connector.record_ref`  (lines 485–492)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Builds the source-side reference used when a page row is first created. It prefers the provider’s primary key when it is a simple usable value, and otherwise falls back to a hash of the whole record.

**Data flow**: It receives a record and stream spec. If the primary-key value is a string or integer, it returns it as a string; if it is a boolean, it returns `None`. For other shapes, it serializes the whole record in a stable order and hashes it with SHA-256, producing a deterministic reference string.

**Call relations**: `Connector.render` calls this when it has to build a fallback title. It works alongside `record_identity`: identity decides whether the record is validly addressable, while the reference supplies a usable label or fallback key.

*Call graph*: called by 1 (render); 2 external calls (sha256, dumps).


### Source sync and page storage
Converts provider records into standard sync results, persists page bodies and metadata, and exposes changed pages for downstream indexing.

### `core/src/ufo/runtime/sources/backend.py`

`domain_logic` · `source sync run`

Connectors speak their own language: they fetch pages of records from an outside service, sometimes with provider-specific cursors, deletions, timestamps, and account credentials. The rest of the system wants one clear answer from each sync run: pages to store, records to delete, the next place to resume, and how many bad records were dropped. This file is the adapter between those two worlds.

The main class, ConnectorBackend, runs one connector stream for one configured account. It first gets a credential through an authentication proxy, so secrets are not passed through unsafe places. It then finds the requested stream, decides what base URL to use, calls the connector, and converts each provider record into a Page that the source system can remember and update later.

A lot of this file is about safe progress. Incremental streams are capped so one huge backfill cannot keep a worker busy forever. If the connector gives a real checkpoint, the backend saves it. If not, it creates its own small “envelope” cursor that says, in effect, “start from the same place next time, but skip the first N records.” Full snapshot streams are different: they must list everything so missing records can be tombstoned correctly, so they are not capped.

Bad individual records do not fail the whole run. Records with no usable identity, invalid page data, or malformed timestamps are warned about and skipped where possible, allowing the rest of the stream to land.

#### Function details

##### `binding_name`  (lines 101–111)

```
def binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: Creates a stable, human-safe object name for a connector binding. It uses the provider, account, and optional tenant URL so the same connected account gets the same name wherever the system refers to it.

**Data flow**: It receives a provider name, an account handle, and an optional base URL. It turns those values into sorted JSON, hashes that JSON with SHA-256, keeps a short digest, and returns a name made from the provider plus that digest. The original details are not exposed directly in the name, but the name stays repeatable for the same inputs.

**Call relations**: This is a standalone helper for code that needs to name connector bindings consistently. Inside the function, JSON formatting makes the input order stable, and hashing turns the binding identity into a compact suffix.

*Call graph*: 2 external calls (sha256, dumps).


##### `ConnectorBackend.fetch`  (lines 149–243)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one sync of one connector stream and returns the project’s standard SyncResult. It is where credentials, connector pages, record conversion, cursor progress, deletion reporting, and run-size limits all come together.

**Data flow**: It receives a source row config, the previously saved cursor, and source authentication context. It gets a credential, finds the requested stream, resolves the base URL, decodes any special backfill cursor, then asks the connector for records. Each record is either skipped because it was already counted in a previous capped run, converted into a Page, or dropped with a warning. As it reads, it tracks deletes, timestamps, watermarks, and whether the run has reached the record cap. It returns a SyncResult containing stored pages, the next cursor, any deletes, whether this was a full snapshot, and the number of dropped records.

**Call relations**: This is the central flow of the file. It calls _credential before talking to the provider, _stream and _base_url to prepare the connector request, _decode_cursor to understand the saved resume state, _page for each record that should become a stored page, and _max_str to advance a string watermark. It hands the final package back as a SyncResult for the source driver to persist and act on.

*Call graph*: calls 6 internal fn (_base_url, _credential, _decode_cursor, _page, _stream, _max_str); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._credential`  (lines 245–259)

```
async def _credential(self, config: ConnectorSourceConfig, auth: SourceAuth) -> Credential
```

**Purpose**: Gets the credential needed to call the outside provider for this connector account. It also turns an unusable grant into a skip signal, so the sync can report that it is waiting for access instead of crashing mysteriously.

**Data flow**: It receives the connector source config and the source authentication context. It checks that an authentication proxy exists, then asks that proxy for the credential for this workspace, connector, and account. If the grant cannot currently be used, it raises StreamSkipped with information about whether a grant is awaited. Otherwise, it returns a Credential object for fetch to use.

**Call relations**: ConnectorBackend.fetch calls this at the start of every run, before contacting the connector. If this succeeds, fetch can call the provider; if it raises StreamSkipped, the wider sync flow can treat the stream as intentionally skipped because access is not ready.

*Call graph*: calls 1 internal fn (__init__); called by 1 (fetch).


##### `ConnectorBackend._base_url`  (lines 261–270)

```
def _base_url(self, config: ConnectorSourceConfig) -> str
```

**Purpose**: Chooses the host URL that the connector should call. This matters for providers where each customer has a different tenant URL, such as a per-company helpdesk domain.

**Data flow**: It receives the source config. It first uses the config’s base_url if present, otherwise it falls back to the connector’s built-in base URL. If neither exists, it raises an error instead of letting the connector accidentally call an empty or wrong host.

**Call relations**: ConnectorBackend.fetch calls this while preparing the connector request. The returned URL is passed into the connector’s fetch_page call so the connector knows which provider host to contact.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._stream`  (lines 272–276)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream definition named by the source row. A stream definition tells the backend things like the primary key, cursor field, timestamp fields, and whether missing records should be treated as deletes.

**Data flow**: It receives a stream name. It loops through the connector’s declared streams and returns the one with the matching name. If no stream matches, it raises an error because the source row points at a stream this connector does not provide.

**Call relations**: ConnectorBackend.fetch calls this near the start of a run. The returned StreamSpec guides the rest of fetch: which records are valid, how pages are identified, how cursors advance, and whether the result is a snapshot.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 279–296)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Recognizes the backend’s own special backfill cursor format. If the saved cursor is ordinary connector state, it leaves it alone by returning None.

**Data flow**: It receives the saved cursor string or None. If there is no cursor, or if the cursor is not JSON, or if it is JSON but does not contain the reserved ufo_backfill key, it returns None. If it does contain that key, it validates the value as a backfill envelope containing the original cursor, a skip count, and a watermark. If that reserved envelope is malformed, it raises an error because this backend is supposed to be the only writer of that format.

**Call relations**: ConnectorBackend.fetch calls this for non-snapshot streams before fetching records. When it returns an envelope, fetch resumes by re-reading from the envelope’s origin and skipping records already consumed in earlier capped slices.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 298–351)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page | None
```

**Purpose**: Turns one provider record into a Page, which is the project’s recallable stored unit for source content. If the record cannot be safely identified or represented, it warns and returns None so one bad record does not block the whole stream.

**Data flow**: It receives a stream definition and one raw record dictionary. It asks the connector for the record’s stable identity, display reference, title, and body. It extracts and normalizes created and updated timestamps. Then it builds a Page whose identity is namespaced by the stream name. If the record has no identity, or if the Page model rejects the rendered data, it emits a warning and returns None instead of a Page.

**Call relations**: ConnectorBackend.fetch calls this for each record that is not being skipped as part of a capped backfill resume. This function calls _record_timestamp for timestamp cleanup, creates the Page object, and uses validation_fault when reporting page validation failures.

*Call graph*: calls 1 internal fn (_record_timestamp); called by 1 (fetch); 3 external calls (__init__, warn, validation_fault).


##### `_record_timestamp`  (lines 354–385)

```
def _record_timestamp(record: dict[str, Any], field: str | None, *, connector: str, stream: str) -> str | None
```

**Purpose**: Extracts a timestamp field from a provider record and converts it into the normalized format expected by Page. It treats bad or missing timestamps as non-fatal, because a bad timestamp should not prevent the record itself from syncing.

**Data flow**: It receives a record, the field name or path to read, and labels for the connector and stream. If no timestamp field is configured, it returns None. Otherwise it reads the value directly or through a nested path, accepts strings and non-boolean integers, and passes them through timestamp normalization. If the value is missing, invalid, or the wrong type, it warns and returns None.

**Call relations**: ConnectorBackend._page calls this once for the created-at field and once for the updated-at field. Its result is passed into the Page constructor so stored pages can carry normalized time metadata when the provider supplies usable values.

*Call graph*: called by 1 (_page); 3 external calls (warn, get_path, normalize_page_timestamp).


##### `_max_str`  (lines 388–393)

```
def _max_str(current: str | None, value: Any) -> str | None
```

**Purpose**: Keeps the largest string value seen so far, used as a simple watermark for incremental streams. This helps the backend remember the furthest cursor-field value it has landed.

**Data flow**: It receives the current saved string and a new value from a record. If the new value is not a string, it leaves the current value unchanged. If there is no current value, or the new string sorts after it, it returns the new string. Otherwise it returns the current string.

**Call relations**: ConnectorBackend.fetch calls this while reading records from streams that declare a cursor field. The returned watermark may become the next cursor when the connector itself did not provide a page cursor.

*Call graph*: called by 1 (fetch).


### `core/src/ufo/runtime/sources/sync.py`

`orchestration` · `background source-sync loop and downstream page-feed reads`

This file is like a careful librarian for outside content. A source backend knows how to read one kind of place, such as a local folder or an external provider. The sync driver decides which sources are due, temporarily claims each one so two workers do not sync it at the same time, asks the right backend for pages, stores page text in the blob store, and updates the database record for each page.

It separates two jobs. First, syncing brings the raw documents into the system. It skips unchanged documents by comparing content fingerprints, marks missing documents as tombstones when a full scan proves they are gone, and keeps cursors so incremental providers can resume where they left off. Second, the page feed lets an indexer replay database changes in revision order, using a cursor like a bookmark.

The file is also careful about failures. Provider failures are logged and retried with backoff. Refused streams, such as missing permissions, can be parked so the system does not keep asking every minute. Database outages are treated differently from provider failures, because blaming a source when the local database was unavailable would send people to fix the wrong thing.

#### Function details

##### `SourceRowConfig.requested_fields`  (lines 106–109)

```
def requested_fields(cls) -> frozenset[str]
```

**Purpose**: Returns the config fields that a caller must repeat exactly when registering the same source again. This lets the system distinguish fields that identify the dataset from fields that were resolved automatically.

**Data flow**: It reads the class-level sets of non-identity fields and resolved fields, subtracts the resolved ones, and returns the remaining field names. It does not change any stored data.

**Call relations**: Source row registration logic can use this rule when deciding whether a new request matches an existing source. The method belongs to backend-owned config models, so each backend defines its own identity rules instead of relying on global field names.


##### `normalize_page_timestamp`  (lines 112–132)

```
def normalize_page_timestamp(value: str) -> str
```

**Purpose**: Turns a provider timestamp into one consistent UTC timestamp string. It accepts numeric timestamps and ISO-style date strings, but rejects ambiguous times that lack a timezone unless they are plain dates.

**Data flow**: It receives a timestamp string, parses it either as seconds or milliseconds since the Unix epoch or as an ISO date/time, converts it to UTC, and returns a normalized ISO string with microseconds. Bad or timezone-less values become clear validation errors.

**Call relations**: Page.normalize_timestamp calls this when Page models are validated. That means source backends can provide timestamps in common formats, while the rest of the sync system receives one standard shape.

*Call graph*: called by 1 (normalize_timestamp); 2 external calls (fromisoformat, fromtimestamp).


##### `Page.digest`  (lines 149–150)

```
def digest(self) -> str
```

**Purpose**: Computes a stable fingerprint for a page body. The driver uses this fingerprint to tell whether the page text really changed.

**Data flow**: It reads the page body text, encodes it, runs SHA-256 hashing over it, and returns a string beginning with `sha256:`. It does not store anything by itself.

**Call relations**: SyncDriver._commit reads this property while comparing fetched pages with prior database rows. If the digest matches an existing live page, the driver can skip rewriting the body.

*Call graph*: 1 external calls (sha256).


##### `Page.normalize_timestamp`  (lines 154–157)

```
def normalize_timestamp(cls, value: str | None) -> str | None
```

**Purpose**: Validates and normalizes the optional created and updated timestamps on a fetched page. This keeps page metadata consistent before it reaches the database.

**Data flow**: It receives either a timestamp string or `None`; `None` passes through unchanged, while strings are sent to normalize_page_timestamp and returned in normalized UTC form.

**Call relations**: Pydantic calls this validator when Page objects are created by backends such as FolderSource or connector sources. It delegates the actual parsing rules to normalize_page_timestamp.

*Call graph*: calls 1 internal fn (normalize_page_timestamp).


##### `StreamSkipped.__init__`  (lines 207–210)

```
def __init__(self, reason: str, *, awaits_grant: bool=False) -> None
```

**Purpose**: Creates an exception that means a provider refused this stream in a non-crash way, such as missing permission or a plan gate. It carries both the reason and whether the stream is waiting for a grant event.

**Data flow**: It receives a reason string and an `awaits_grant` flag, stores both on the exception, and initializes the runtime error text with the reason.

**Call relations**: Connector backends raise this when they know a stream should be skipped rather than treated as broken. SyncDriver._sync_claimed catches it and sends the source through the skip and possible parking path instead of the failure backoff path.

*Call graph*: called by 62 (_credential, paginate, paginate, paginate, paginate, paginate, _paginate_named, paginate, paginate, paginate (+15 more)).


##### `validation_fault`  (lines 213–219)

```
def validation_fault(error: ValidationError) -> str
```

**Purpose**: Turns a model validation error into a safe, short explanation. It names which fields failed and why, without including the rejected values.

**Data flow**: It receives a Pydantic ValidationError, reads its structured errors, formats each location and rule type, and joins them into one string. Sensitive provider data is intentionally not copied into the result.

**Call relations**: SyncDriver._report_failed uses this when a backend result or config fails validation. The formatted text becomes the provider-fault detail in failure telemetry.

*Call graph*: called by 1 (_report_failed); 1 external calls (errors).


##### `response_fault`  (lines 222–269)

```
def response_fault(response: httpx.Response) -> str
```

**Purpose**: Extracts a safe reason from an HTTP error response. It reads common GraphQL and Google-style error envelopes without logging the whole response body.

**Data flow**: It receives an httpx response, tries to parse JSON, looks for known error arrays or status fields, and returns a concise reason string. If the response is unreadable or unfamiliar, it returns an empty string.

**Call relations**: SyncDriver._report_failed calls this for HTTP status errors. It helps logs say why a provider refused a request while avoiding accidental credential or payload leaks.

*Call graph*: called by 1 (_report_failed); 2 external calls (json, list_or_empty).


##### `StreamFault.__init__`  (lines 279–281)

```
def __init__(self, reason: str) -> None
```

**Purpose**: Creates an exception for provider data that a backend cannot safely read or interpret. It lets a backend provide a clean reason without exposing raw provider payloads.

**Data flow**: It receives a reason string, stores it on the exception, and initializes the runtime error text with the same reason.

**Call relations**: Provider and folder backends raise this when they detect malformed or unusable stream data. SyncDriver._report_failed recognizes it and records the backend-authored reason.

*Call graph*: called by 9 (_read, _markdown_entries, _spool_tarball, _refuse_client_error, decoded, _account_base, _sheet_value_records, paginate, _ensure_tenant).


##### `SourceBackend.config_model`  (lines 321–321)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: Defines the type of configuration a source backend expects. This is part of the backend contract, not a concrete implementation here.

**Data flow**: A caller asks the backend for its config model type, then uses that model to validate the JSON config stored on a source row. The property itself produces the model class.

**Call relations**: SyncDriver._fetch relies on this protocol member before calling the backend. Every real backend must provide it so core does not treat source configs as untyped dictionaries.


##### `SourceBackend.fetch`  (lines 323–323)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Defines the contract for fetching pages from a source. A backend implements this to return the current or incremental set of documents plus a cursor for next time.

**Data flow**: It receives typed config, the previous cursor, and source authentication context, then returns a SyncResult containing pages, deletes, cursor, and snapshot information. Implementations may raise special exceptions for expired cursors or skipped streams.

**Call relations**: SyncDriver._fetch calls this after validating config and building SourceAuth. FolderSource and connector backends provide concrete versions of this protocol.


##### `FolderSource.fetch`  (lines 337–348)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Reads all files in a local folder and turns each file into a page. It is the built-in backend for syncing simple filesystem content.

**Data flow**: It receives a folder config, ignores cursor and auth, reads file paths and text on a worker thread, wraps each file as a Page, and returns a snapshot SyncResult. Because it is a snapshot, missing files can later be tombstoned.

**Call relations**: SyncDriver._fetch calls this through the SourceBackend interface when a source row uses the folder backend. It hands file entries to Page and SyncResult so the common commit path can store them like any provider pages.

*Call graph*: 4 external calls (__init__, __init__, to_thread, Path).


##### `FolderSource._read`  (lines 351–358)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: Performs the blocking filesystem scan for FolderSource. It reads every file under the configured root as UTF-8 text.

**Data flow**: It receives a root path, verifies it is a directory, walks all files below it in sorted order, and returns pairs of relative path and decoded text. If the folder itself is missing, it raises an error rather than pretending all pages were deleted.

**Call relations**: FolderSource.fetch runs this in a background thread so filesystem work does not block the async event loop. Its output is converted directly into Page objects.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 361–381)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object], *, connection_id: UUID | None=None, non_identity_keys: frozenset[str]=frozenset()) -> UUID
```

**Purpose**: Builds the stable database ID for a source row. The same workspace, backend, and identifying config produce the same UUID, so restarting does not create duplicates.

**Data flow**: It receives workspace ID, backend name, config, optional connection ID, and config keys to ignore for identity. It removes non-identity keys, serializes the rest in sorted order, and hashes that string into a deterministic UUID.

**Call relations**: register_sources calls this while bootstrapping configured sources. The same idea is also important for connector registrations so one real dataset maps to one source row.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 384–387)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: Builds the stable database ID for a page within a source. This lets updates and deletes for the same source reference find the same row.

**Data flow**: It receives a source ID and a source reference string, combines them into a namespaced string, and returns a deterministic UUID.

**Call relations**: SyncDriver._commit uses this when deciding where a fetched page or explicit delete should land. It is the fallback identity when a provider does not supply a separate source identity.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `source_body_ref_matches`  (lines 390–400)

```
def source_body_ref_matches(body_ref: str, source_id: UUID, page_id: UUID, digest: str) -> bool
```

**Purpose**: Checks whether a blob-store reference looks like the expected body location for a specific source, page, and digest. This is a safety helper for validating source-owned blob paths.

**Data flow**: It receives a blob reference, source ID, page ID, and digest, then checks the prefix, suffix, and claim-shaped middle segment. It returns true only if all pieces match the expected source-sync pattern.

**Call relations**: This helper is available to code that needs to confirm a body reference belongs to a page written by source sync. It mirrors the body reference format created in SyncDriver._commit.


##### `register_sources`  (lines 403–468)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: Creates database rows for sources listed in static configuration. It makes configured sources exist at startup without duplicating rows on every restart.

**Data flow**: It receives configured source entries, opens a workspace transaction, finds the workspace and main agent, computes each source ID, inserts missing source rows, and grants the main agent access. Removed sources are left alone.

**Call relations**: Startup code calls this before the polling sync job runs. It uses source_row_id to make registration repeatable and writes rows that SyncDriver later claims and syncs.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `_rescheduled`  (lines 486–497)

```
def _rescheduled(claimed: ClaimedSource, when: datetime | sa.Case[datetime]) -> sa.Case[datetime]
```

**Purpose**: Protects manual resync requests that arrive while a source is already being synced. It decides whether the finishing worker may push the next sync time forward.

**Data flow**: It receives a claimed source and a proposed next time, then builds a database expression: use the proposed time only if the row was not rescheduled after this claim began; otherwise keep the newer database value.

**Call relations**: SyncDriver._write, _release, and _skip use this when completing a run. It keeps a fresh resync request from being accidentally buried under a normal interval or backoff.

*Call graph*: called by 3 (_release, _skip, _write); 1 external calls (case).


##### `_stream_tags`  (lines 500–505)

```
def _stream_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: Builds low-cardinality telemetry tags that identify the provider and stream. These tags are safe for metrics because they do not include every source row ID.

**Data flow**: It receives a claimed source, reads its backend name and `stream` config value if present, and returns a small tag dictionary.

**Call relations**: Logging, metrics, and service-check code call this throughout the driver. _check_tags builds on it when row-level status needs the source ID too.

*Call graph*: calls 1 internal fn (_config_value); called by 7 (_defer, _report_failed, _report_ok, _report_parked, _run_with_lease, _sync_claimed, _check_tags).


##### `_check_tags`  (lines 508–516)

```
def _check_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: Builds service-check tags for one exact source row. Service checks need the row ID so one failing Slack source does not get hidden by another healthy Slack source.

**Data flow**: It receives a claimed source, starts with provider and stream tags, adds the source ID as text, and returns the combined dictionary.

**Call relations**: SyncDriver._report_ok and _report_failed use this when sending health checks. It calls _stream_tags to keep tag naming consistent.

*Call graph*: calls 1 internal fn (_stream_tags); called by 2 (_report_failed, _report_ok).


##### `_config_value`  (lines 519–521)

```
def _config_value(source: ClaimedSource, key: str) -> str
```

**Purpose**: Safely reads a string value from a source config dictionary. It avoids putting non-string config values into telemetry fields.

**Data flow**: It receives a claimed source and a key, looks up that key in the config, and returns the value only if it is a string; otherwise it returns an empty string.

**Call relations**: Telemetry helpers and report methods use this for fields such as stream or account. It keeps logging code simple and defensive.

*Call graph*: called by 4 (_defer, _report_failed, _report_ok, _stream_tags).


##### `_database_unreachable`  (lines 524–546)

```
def _database_unreachable(error: BaseException) -> bool
```

**Purpose**: Decides whether an error came from this system's own database being unavailable, rather than from the source provider. This prevents false provider alerts during database trouble.

**Data flow**: It receives an exception, checks for SQLAlchemy pool and interface errors, and checks whether operational errors wrap known asyncpg connection errors. It returns true only for database reachability failures.

**Call relations**: SyncDriver._sync_claimed calls this after any unexpected error. A true result sends the run to _defer instead of provider failure reporting.

*Call graph*: called by 1 (_sync_claimed).


##### `_readers_remain`  (lines 570–596)

```
def _readers_remain() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a source still has at least one live reader. Sources granted only to archived agents are skipped until someone can read them again.

**Data flow**: It creates SQL expressions that look for any grants and any grants to non-archived agents, then returns a predicate allowing sources with no grants or with at least one live granted agent.

**Call relations**: SyncDriver.candidate_workspaces and _claim_due include this predicate when finding work. It avoids spending provider calls and indexing work on content no active agent can use.

*Call graph*: called by 2 (_claim_due, candidate_workspaces); 5 external calls (and_, exists, literal, or_, select).


##### `SyncDriver.candidate_workspaces`  (lines 619–640)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds workspaces that currently have at least one due source. This lets a dispatcher avoid opening per-workspace sync runs where nothing needs doing.

**Data flow**: It reads the current time, opens an owner-level transaction, selects distinct workspace IDs for due, unremoved, unclaimed-or-expired sources with readers, and returns those IDs.

**Call relations**: The scheduler or dispatcher calls this before binding a workspace run. It uses _readers_remain so archived-only sources do not wake the sync driver.

*Call graph*: calls 1 internal fn (_readers_remain); 4 external calls (now, or_, select, owner_tx).


##### `SyncDriver.run`  (lines 642–653)

```
async def run(self) -> None
```

**Purpose**: Runs one sync tick for the current workspace. It claims due sources, starts lease-renewal tasks, and syncs each claimed source while keeping the claim alive.

**Data flow**: It creates a random claim token, asks _claim_due for rows, starts a renewal task per row, processes each source through _run_with_lease, and finally cancels and gathers renewal tasks.

**Call relations**: This is the main entry method for the background sync job once a workspace has been selected. It hands each source to _run_with_lease so syncing and lease renewal are coordinated.

*Call graph*: calls 3 internal fn (_claim_due, _renew_claim, _run_with_lease); 3 external calls (create_task, gather, uuid4).


##### `SyncDriver._run_with_lease`  (lines 655–675)

```
async def _run_with_lease(self, source: ClaimedSource, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs one claimed source while watching its lease-renewal task. If the claim is lost, it stops rather than writing under an invalid claim.

**Data flow**: It starts _sync_claimed as a task, waits until either syncing or renewal finishes, propagates renewal errors, logs lost claims, and cancels leftover tasks on exit.

**Call relations**: SyncDriver.run calls this for each claimed source. It sits between the high-level loop and _sync_claimed to make sure only the worker that still owns the claim commits changes.

*Call graph*: calls 2 internal fn (_sync_claimed, _stream_tags); called by 1 (run); 4 external calls (create_task, gather, wait, log).


##### `SyncDriver._sync_claimed`  (lines 677–699)

```
async def _sync_claimed(self, source: ClaimedSource) -> None
```

**Purpose**: Performs the fetch-and-commit flow for one claimed source and chooses the right recovery path on errors. It is the decision point for success, skipped streams, provider failures, and database deferrals.

**Data flow**: It calls _fetch, then _commit. If a stream is skipped, it logs and calls _skip; if the database is unreachable, it calls _defer; otherwise it computes backoff, reports the failure, and releases the source.

**Call relations**: _run_with_lease calls this after a source is claimed. It delegates specialized work to _fetch, _commit, _skip, _defer, _report_failed, and _release.

*Call graph*: calls 9 internal fn (_commit, _defer, _error_backoff, _fetch, _release, _report_failed, _skip, _database_unreachable, _stream_tags); called by 1 (_run_with_lease); 3 external calls (suppress, now, log).


##### `SyncDriver._renew_claim`  (lines 701–704)

```
async def _renew_claim(self, source: ClaimedSource) -> None
```

**Purpose**: Keeps a long-running source claim from expiring. This prevents another worker from picking up the same source while the first worker is still fetching or committing.

**Data flow**: It loops forever, sleeps for the refresh interval, and calls _refresh_claim each time. It exits only by cancellation or if refreshing raises an error.

**Call relations**: SyncDriver.run starts this as a background task for each claimed source. _run_with_lease watches it alongside the actual sync task.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (run); 1 external calls (sleep).


##### `SyncDriver._refresh_claim`  (lines 706–722)

```
async def _refresh_claim(self, source: ClaimedSource) -> None
```

**Purpose**: Extends the database lease for a claimed source. If the row no longer belongs to this claim, it signals that the claim was lost.

**Data flow**: It computes a new expiry time, updates the source row only if the claim token still matches and the source is not removed, and raises _SourceClaimLost unless exactly one row was updated.

**Call relations**: _renew_claim calls this repeatedly during long work, and _commit calls it once before writing. A lost claim bubbles up to _run_with_lease, which logs and stops.

*Call graph*: called by 2 (_commit, _renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `SyncDriver._claim_due`  (lines 724–775)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: Takes ownership of a batch of due source rows for this worker. Claiming is the lock that stops two sync workers from processing the same source at once.

**Data flow**: It selects due, readable, unremoved sources whose claims are empty or expired, optionally uses database row locking on Postgres, writes the claim token and expiry to those rows, and returns ClaimedSource objects.

**Call relations**: SyncDriver.run calls this at the start of a tick. The returned ClaimedSource values drive all later fetch, commit, renewal, and reporting steps.

*Call graph*: calls 1 internal fn (_readers_remain); called by 1 (run); 7 external calls (__init__, now, timedelta, or_, select, update, workspace_tx).


##### `SyncDriver._fetch`  (lines 777–796)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: Calls the correct backend to read one claimed source. It also prepares typed config and authentication context before the backend sees the request.

**Data flow**: It looks up the backend by name, validates the stored config with the backend's config model, resolves optional self identity and credentials, builds SourceAuth, and awaits the backend's fetch result.

**Call relations**: _sync_claimed calls this before committing anything. It is the seam between core sync orchestration and source-specific backend code.

*Call graph*: called by 1 (_sync_claimed); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 798–900)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: Turns a backend's SyncResult into blob writes and database page updates. It writes only changed bodies, updates changed metadata, and prepares deletes or snapshot tombstones.

**Data flow**: It refreshes the claim, reads prior pages, assigns stable page IDs, compares digests and metadata, writes new bodies to the blob store, calls _write for database changes, cleans up newly written blobs if commit fails, and reports success.

**Call relations**: _sync_claimed calls this after _fetch succeeds. It uses _prior_pages, page_id_for, _write, and _report_ok to move from fetched pages to durable synced state.

*Call graph*: calls 5 internal fn (_prior_pages, _refresh_claim, _report_ok, _write, page_id_for); called by 1 (_sync_claimed); 4 external calls (__init__, __init__, gather, uuid5).


##### `SyncDriver._prior_pages`  (lines 902–946)

```
async def _prior_pages(self, source_id: UUID) -> tuple[dict[UUID, tuple[str, bool, PageBrowse]], dict[str, tuple[str, bool, PageBrowse]]]
```

**Purpose**: Loads the existing page records for a source so the commit step can compare old and new state. This is how the driver knows what changed, what only changed metadata, and what may be deleted.

**Data flow**: It receives a source ID, queries page rows for that source, builds one dictionary by page ID and another by source identity, and returns both with PageBrowse metadata.

**Call relations**: SyncDriver._commit calls this before processing fetched pages. The returned maps guide ID reuse, digest comparison, and delete resolution.

*Call graph*: called by 1 (_commit); 3 external calls (__init__, select, workspace_tx).


##### `SyncDriver._write`  (lines 948–1076)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[ChangedPage], metadata: list[PageBrowse], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> int
```

**Purpose**: Persists one completed sync result into the database. It upserts changed pages, updates metadata-only pages, tombstones deleted pages, updates the source cursor and schedule, and clears the claim.

**Data flow**: It receives changed pages, metadata updates, fetched IDs, deleted IDs, the next cursor, and snapshot flag. Inside one workspace transaction, it verifies the claim, writes page rows, applies tombstones, updates subjects, resets error/refusal state, and returns the tombstone count.

**Call relations**: SyncDriver._commit calls this after blob bodies are safely written. It uses _rescheduled to avoid overwriting newer resync requests and raises _SourceClaimLost if the claim is no longer valid.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (_commit); 7 external calls (__init__, now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._report_ok`  (lines 1078–1101)

```
async def _report_ok(self, source: ClaimedSource, fetched: int, written: int, tombstoned: int, dropped: int) -> None
```

**Purpose**: Records that a source sync succeeded. It logs useful counts and sends an OK health check so previous alerts for that row can recover.

**Data flow**: It receives the source and counts for fetched, written, tombstoned, and dropped pages. It builds tags, writes a success log, and emits a service check, suppressing telemetry failures so they do not break sync.

**Call relations**: SyncDriver._commit calls this after _write succeeds. It uses _stream_tags, _check_tags, and _config_value to shape safe telemetry.

*Call graph*: calls 3 internal fn (_check_tags, _config_value, _stream_tags); called by 1 (_commit); 3 external calls (suppress, emit_service_check, log).


##### `SyncDriver._error_backoff`  (lines 1103–1111)

```
def _error_backoff(self, source: ClaimedSource, now: datetime) -> tuple[int, datetime]
```

**Purpose**: Calculates how long to wait before retrying a failing source. Repeated failures wait longer, up to a cap, so the system does not hammer a broken provider.

**Data flow**: It receives the source and current time, increments the consecutive error count, computes an exponential backoff from the normal sync interval, caps it, and returns the new count and next sync time.

**Call relations**: SyncDriver._sync_claimed calls this before reporting and releasing a failed source. _report_failed and _release both use its result.

*Call graph*: called by 1 (_sync_claimed); 1 external calls (timedelta).


##### `SyncDriver._report_failed`  (lines 1113–1171)

```
async def _report_failed(self, source: ClaimedSource, error: Exception, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: Records a provider-side or sync-side failure in logs, metrics, and service checks. It is careful to include useful error shape without leaking raw provider data or credentials.

**Data flow**: It receives the source, exception, cursor-reset flag, error count, and next retry time. It chooses a safe provider-fault message based on error type, logs the failure, increments a metric, and emits a critical service check.

**Call relations**: SyncDriver._sync_claimed calls this for non-database failures. It calls response_fault for HTTP errors and validation_fault for validation errors, then uses tag helpers for telemetry.

*Call graph*: calls 5 internal fn (_check_tags, _config_value, _stream_tags, response_fault, validation_fault); called by 1 (_sync_claimed); 5 external calls (suppress, isoformat, emit_metric, emit_service_check, log_error).


##### `SyncDriver._defer`  (lines 1173–1199)

```
async def _defer(self, source: ClaimedSource, error: Exception) -> None
```

**Purpose**: Records a run that was interrupted by this system's own database problem, not by the provider. It avoids raising a source health alert for the wrong subsystem.

**Data flow**: It computes a normal retry time, writes a warning log with the database error class, and tries to release the source without increasing its provider error count. Release failures are suppressed because the claim lease will eventually expire.

**Call relations**: SyncDriver._sync_claimed calls this when _database_unreachable says the database caused the failure. It may call _release, but it does not emit failed-source metrics or critical checks.

*Call graph*: calls 3 internal fn (_release, _config_value, _stream_tags); called by 1 (_sync_claimed); 4 external calls (suppress, now, timedelta, warn).


##### `SyncDriver._release`  (lines 1201–1227)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: Frees a claimed source after a failed run and schedules its retry. It also clears an expired cursor when needed.

**Data flow**: It receives the source, whether to reset the cursor, the new error count, and next sync time. It updates the source row to clear the claim, store the retry schedule, set the error count, and optionally clear the cursor.

**Call relations**: SyncDriver._sync_claimed calls this after reporting a failure, and _defer may call it for database interruptions. It uses _rescheduled so manual resync requests are not overwritten.

*Call graph*: calls 1 internal fn (_rescheduled); called by 2 (_defer, _sync_claimed); 2 external calls (update, workspace_tx).


##### `SyncDriver._skip`  (lines 1229–1294)

```
async def _skip(self, source: ClaimedSource, reason: str, *, awaits_grant: bool) -> None
```

**Purpose**: Handles a stream that was refused but not truly failed. It keeps existing pages untouched, clears provider error count, increments refusal count, and may park the source to reduce pointless polling.

**Data flow**: It receives the source, refusal reason, and grant-wait flag. It updates the source row with the next sync time, refusal count, possible parked marker and reason, clears the claim, and reports parking if the threshold was reached.

**Call relations**: SyncDriver._sync_claimed calls this after catching StreamSkipped. It uses _rescheduled for safe scheduling and calls _report_parked when the database row actually entered a parked state.

*Call graph*: calls 2 internal fn (_report_parked, _rescheduled); called by 1 (_sync_claimed); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `SyncDriver._report_parked`  (lines 1296–1321)

```
async def _report_parked(self, source: ClaimedSource, reason: str, refusals: int) -> None
```

**Purpose**: Records that a repeatedly refused stream has been parked. Parking slows retries but does not page an operator.

**Data flow**: It receives the source, reason, and refusal count, then writes a warning log and emits a parked metric. Telemetry failures are suppressed.

**Call relations**: SyncDriver._skip calls this only after successfully updating the source row into a parked state. It uses _stream_tags so the warning and metric identify the provider stream.

*Call graph*: calls 1 internal fn (_stream_tags); called by 1 (_skip); 3 external calls (suppress, emit_metric, warn).


##### `PageFeed.pages_changed_since`  (lines 1361–1361)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Defines the interface an indexer uses to read changed pages. It is a protocol method, so concrete feed implementations must provide it.

**Data flow**: A caller passes a cursor and limit; an implementation returns a PageBatch containing ordered changes and a next cursor. This declaration itself does not read or write data.

**Call relations**: CorePageFeed.pages_changed_since is the core implementation of this contract. Extensions receive a PageFeed so they can replay page changes without knowing the sync driver's internals.


##### `page_cursor`  (lines 1364–1373)

```
def page_cursor(cursor: object) -> tuple[int, UUID]
```

**Purpose**: Parses the page-feed cursor format. The cursor is a bookmark made from a revision number and a page ID.

**Data flow**: It receives an object, requires it to be a string shaped like `revision|uuid`, converts the revision to an integer and the ID to a UUID, and returns both. Invalid cursors raise ValueError.

**Call relations**: CorePageFeed.pages_changed_since calls this when a reader supplies a cursor. The parsed values become the lower bound for the next page-change query.

*Call graph*: called by 1 (pages_changed_since); 1 external calls (UUID).


##### `CorePageFeed.pages_changed_since`  (lines 1385–1443)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Reads changed pages for downstream indexing in a stable order. It includes body text for live pages and an empty body for tombstones so consumers know what to delete.

**Data flow**: It receives an optional cursor and requested limit, caps the limit, queries page rows after the cursor ordered by revision and ID, fetches each live body from the blob store, builds PageChange objects, and returns them with a next cursor.

**Call relations**: Indexers call this through the PageFeed interface. It uses page_cursor to resume safely and reads the same page rows written by SyncDriver._write.

*Call graph*: calls 1 internal fn (page_cursor); 7 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx).
