# Shared REST source connector foundation  `stage-12.1.1`

This stage is shared behind-the-scenes support for connectors that read from REST APIs. A REST API is a web service that returns data when the program sends HTTP requests, like asking a website for a specific page of information. Provider-specific connectors can focus on the details of one service, while this foundation handles the repeated plumbing.

The file `core/src/ufo/sources/rest.py` is the common engine for that work. It builds and sends authenticated requests, meaning it includes the right proof of identity, such as tokens or keys, so the remote service accepts the call. If a request fails because of a temporary problem, such as a busy server or network hiccup, it retries instead of giving up immediately. It also walks through paginated results, where an API returns data in pages rather than all at once, and supports cursor-style progress markers so reading can continue from the right place. Together, these pieces act like a reusable transport and paging machine for many REST-based source connectors.

## Files in this stage

### Shared REST source connector foundation
### `core/src/ufo/sources/rest.py`

`io_transport` · `request handling during source sync reads`

Many services expose data through REST APIs, which are web endpoints that return records over HTTP. Those APIs differ in annoying but predictable ways: some use bearer tokens, some need special headers, some return one page at a time with a cursor, and some tell the client to wait when rate-limited. This file is the shared toolkit that keeps all of that consistent.

The central class, RestConnector, is a base class for read-only source connectors. A specific provider, such as GitHub or another SaaS service, supplies its base URL and stream definitions, then either uses the built-in pagination strategies or overrides the paging method for unusual API shapes. RestConnector builds an async HTTP client from the resolved credential, so network waits do not block other sync work. It wraps GET and read-style POST calls in one retry policy for temporary network failures, rate limits, and server errors.

The file also contains small helpers for safely pulling records out of nested JSON responses, reading next-page links, and stopping broken pagination loops. Those safety checks matter: if an API keeps returning the same next cursor forever, the connector fails loudly instead of spinning endlessly. In short, this file is the reusable “fetch pages safely” engine for REST-based sources.

#### Function details

##### `get_path`  (lines 49–58)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a dotted path like "data.items" from a nested dictionary. It gives callers a safe way to look inside API JSON without crashing when a field is missing or shaped differently than expected.

**Data flow**: It receives a dictionary, a dotted path string, and an optional default value. It walks through the dictionary one part at a time; if any step is not a dictionary or is missing, it returns the default. Otherwise it returns the value found at the end of the path.

**Call relations**: Pagination helpers use this when they need to find records, next cursors, continuation flags, or server-reported page sizes inside a response body. records_at also relies on it to extract a nested list before filtering it.

*Call graph*: called by 3 (_get_cursor_pages, _get_offset_pages, records_at).


##### `list_or_empty`  (lines 61–65)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: Turns an uncertain value into a clean list of record dictionaries. It protects the rest of the code from API responses that are not lists or contain non-record items.

**Data flow**: It receives any value. If the value is a list, it keeps only the items that are dictionaries. If the value is not a list, it returns an empty list.

**Call relations**: It is used after decoding JSON responses, including link-header and OData pagination, so later code can assume it is working with a list of record-shaped dictionaries.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 68–71)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Returns a value only if it is a dictionary, otherwise returns an empty dictionary. It is a small shaping helper for connectors that need to pull a nested object out of a larger API response.

**Data flow**: It receives any value. Dictionary-shaped values pass through unchanged; all other shapes become an empty dictionary.

**Call relations**: This helper is not called inside this file, but it is available to REST connector subclasses that need the same safe “only accept object-shaped data” behavior.


##### `records_at`  (lines 74–79)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: Finds the list of records inside an API response. Some APIs return a top-level list, while others wrap records under a field such as "data.items"; this function covers both cases.

**Data flow**: It receives decoded response data and an optional path. With no path, it treats the data itself as the candidate list. With a path, it first looks up that nested value, then returns only dictionary items from it.

**Call relations**: Pagination methods call this after each response to extract the actual records to yield. For path-based extraction it delegates to get_path, then cleans the result through list_or_empty.

*Call graph*: calls 2 internal fn (get_path, list_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, parse).


##### `_int_or_none`  (lines 82–87)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: Converts a clear integer-like value into an integer, or gives up safely. It is used when an API reports a page size that might arrive as either a number or a numeric string.

**Data flow**: It receives any value. Integers pass through, decimal strings become integers, and everything else becomes None.

**Call relations**: Offset pagination uses this when deciding how far to move the offset after a page, especially when the server reports the limit it actually applied.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 90–93)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: Adds context fields, such as a parent ID or cloud ID, to every record in a page. This helps downstream code remember where a record came from after fan-out requests collect data from many parents.

**Data flow**: It receives an iterable of record dictionaries plus named context values. It copies each record and merges the context fields into the copy, returning a new list of enriched records.

**Call relations**: This helper is not called by the base class itself, but provider-specific pagination code can use it before yielding records to fetch_page.


##### `next_link`  (lines 96–101)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: Reads the HTTP Link header and extracts the URL marked as the next page. This supports APIs that put pagination instructions in headers instead of the response body.

**Data flow**: It receives response headers. It looks for a Link header, searches it for a rel=next entry, and returns that URL if found; otherwise it returns None.

**Call relations**: _get_link_header_pages calls this after each response to decide whether to stop or fetch another page.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 104–109)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: Decides whether a request failure looks temporary enough to try again. It treats network transport problems and selected status codes, such as rate limits and server errors, as retryable.

**Data flow**: It receives an exception. Network-level httpx errors return true. HTTP status errors return true only for configured retryable status codes. Other errors return false.

**Call relations**: _send calls this inside its retry loop to decide whether to sleep and retry or immediately pass the error upward.

*Call graph*: called by 1 (_send).


##### `_retry_after`  (lines 112–132)

```
def _retry_after(error: BaseException) -> float | None
```

**Purpose**: Reads a usable Retry-After value from certain temporary HTTP errors. Retry-After is a server hint that says how many seconds the client should wait before trying again.

**Data flow**: It receives an exception. If it is an HTTP status error for a status that may carry a wait hint, it reads the retry-after header, parses it as a finite non-negative number, and returns it. Bad, missing, negative, infinite, or irrelevant values become None.

**Call relations**: _retry_wait uses this to respect server-provided wait times when choosing how long _send should pause before the next attempt.

*Call graph*: called by 1 (_retry_wait); 1 external calls (isfinite).


##### `_retry_wait`  (lines 135–152)

```
def _retry_wait(error: BaseException, delay: float) -> float
```

**Purpose**: Chooses how long to wait before retrying a failed request. It balances an exponential backoff schedule with any Retry-After hint from the server, and adds random jitter so many workers do not retry at the exact same moment.

**Data flow**: It receives the error and the current backoff delay. It checks for a Retry-After hint, clamps waits to safety caps, chooses the stronger wait floor, adds randomness within bounds, and returns the number of seconds to sleep.

**Call relations**: _send calls this after a retryable failure. It delegates server-hint parsing to _retry_after and uses random.uniform to spread out retries.

*Call graph*: calls 1 internal fn (_retry_after); called by 1 (_send); 1 external calls (uniform).


##### `_raise_for_status`  (lines 155–166)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: Turns an unsuccessful HTTP response into a clear exception that includes part of the response body. This is useful because API error bodies often contain the real explanation, such as an invalid filter message.

**Data flow**: It receives an HTTP response. Successful responses do nothing. Failed responses produce an HTTPStatusError whose message includes the status, request method, URL, and a capped snippet of the body.

**Call relations**: _send calls this after every response. If it raises, _send can either retry the error or let it fail the fetch.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 169–173)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: Decodes a response body as JSON, but treats empty responses as an empty dictionary. This avoids special-case code for endpoints that return no body.

**Data flow**: It receives an HTTP response. If the response is 204 No Content or has no content, it returns {}. Otherwise it parses and returns the JSON body as a dictionary.

**Call relations**: _get and _post use this after _send returns a successful response, so callers receive decoded data instead of raw HTTP objects.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 176–179)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Decodes a response body that should be a top-level list of records. Empty responses become an empty list, and non-dictionary list entries are discarded.

**Data flow**: It receives an HTTP response. Empty content returns []. Otherwise it parses JSON and passes the result through list_or_empty.

**Call relations**: _get_link_header_pages uses this when no custom record parser was supplied for link-header pagination.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 182–188)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: Stops a pagination loop that has gone on far too long. It is a safety brake against buggy or hostile APIs that never signal the end of data.

**Data flow**: It receives a label describing the fetch and the number of pages seen so far. If the count exceeds the maximum allowed pages, it raises an error; otherwise it does nothing.

**Call relations**: Every built-in pagination loop calls this once per page. If the loop is not ending naturally, this function turns an endless fetch into a visible failed run.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 191–197)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: Stops cursor-based pagination when the provider repeats a cursor or next-link that was already used. A repeated cursor means the API is not moving forward.

**Data flow**: It receives a label, a cursor or URL token, and a set of tokens already seen. If the token is already in the set, it raises an error. Otherwise it records the token.

**Call relations**: Cursor, link-header, and OData pagination call this before following a continuation value. It catches infinite loops earlier than the general page-count limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 206–207)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the stream definitions declared by a concrete REST connector. A stream is a named collection of records that the sync system can read.

**Data flow**: It reads the class-level streams_list and returns a new list copy. The connector’s stored stream definitions are not modified.

**Call relations**: The broader connector framework asks for streams when it needs to know what this source can read. This base implementation lets subclasses declare streams as simple class data.


##### `RestConnector._make_client`  (lines 209–226)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the async HTTP client used to talk to one account’s API. It applies the base URL, JSON headers, timeout settings, and whichever authentication form the resolved credential provides.

**Data flow**: It receives a base URL and credential. If the credential contains a custom transport, it builds a client around that. Otherwise it adds a bearer token or auth headers. If no usable auth exists, it raises an error. The output is an httpx.AsyncClient ready to send requests.

**Call relations**: fetch_page calls this at the start of a read. All lower-level GET and POST helpers then use the resulting client for actual network calls.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 228–231)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Sends a GET request and returns the decoded JSON object. It is the common helper for normal read endpoints whose useful data is in the response body.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It asks _get_raw to send the request with retry behavior, then converts the successful response through _json_or_empty.

**Call relations**: Cursor, offset, and page-number pagination use this when they only need the JSON body and not response headers.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 3 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages).


##### `RestConnector._get_raw`  (lines 233–237)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Sends a GET request and returns the full HTTP response. This is needed when pagination information lives in headers or when a caller wants to parse the body itself.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It wraps client.get in _send, so temporary failures are retried, then returns the successful response object.

**Call relations**: _get builds on this for JSON-only reads. Link-header and OData pagination call it directly because they need headers or full response control.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 239–244)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Sends a read-style POST request and returns the decoded JSON object. Some APIs use POST for searches or reports even though the connector is only reading data.

**Data flow**: It receives an HTTP client, path, and optional JSON request body. It sends the POST through _send for retries, then converts the response through _json_or_empty.

**Call relations**: This base helper is available to provider-specific connectors that override pagination for read endpoints exposed as POST, while keeping the same retry behavior as GET.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 246–252)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Sends a read-style POST request and returns the raw HTTP response. It is useful for APIs whose POST response is not a normal JSON object, such as a top-level array or streamed batch list.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It wraps client.post in _send and returns the successful response without decoding it.

**Call relations**: Provider-specific connectors can call this inside custom pagination when they need full response control but still want the shared retry policy.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 254–275)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: Runs one HTTP request with retry protection. It is the shared envelope that makes GET and POST calls resilient to temporary network issues, rate limits, and server-side failures.

**Data flow**: It receives a no-argument async request function. It calls it, checks the status, and returns the response on success. On retryable failures, it calculates a wait, sleeps, and tries again until it runs out of attempts or retry budget; then it raises the error.

**Call relations**: _get_raw, _post, and _post_raw all hand their actual HTTP calls to this function. It uses _raise_for_status, _is_retryable, _retry_wait, and asyncio.sleep to turn low-level request attempts into a controlled retry loop.

*Call graph*: calls 3 internal fn (_is_retryable, _raise_for_status, _retry_wait); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 277–314)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[
```

**Purpose**: This is the main read entry for one stream page sequence. It opens the HTTP client, runs pagination, validates records, flattens each record into the output shape, and yields pages to the sync driver.

**Data flow**: It receives a stream, cursor, credential, base URL, acting user ID, and optional backfill floor. It builds a client, asks paginate_source for raw pages, skips empty pages, validates that pages contain dictionaries, flattens records, preserves StreamPage metadata when present, and yields cleaned pages. It also closes async generators when done.

**Call relations**: The core sync driver calls this when it needs records from a REST source. fetch_page wires together _make_client, paginate_source, _validate_page, and flatten so subclasses can focus mostly on how to fetch provider-specific pages.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate_source); 1 external calls (__init__).


##### `RestConnector.paginate_source`  (lines 316–329)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides a small extension point between fetch_page and paginate. It lets special connectors accept extra run-scoped information, such as the acting user or backfill date, without changing the default paginate method for everyone.

**Data flow**: It receives the client, stream, cursor, self user ID, and optional backfill date. The default ignores the extra values and returns the async iterator from paginate.

**Call relations**: fetch_page calls this instead of calling paginate directly. The default hands off to paginate, but subclasses can override it when their paging logic needs the extra context.

*Call graph*: calls 1 internal fn (paginate); called by 1 (fetch_page).


##### `RestConnector.paginate`  (lines 331–343)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Produces raw pages of records for a stream. The default implementation uses the stream’s declared pagination strategy; connectors with unusual API shapes override this method.

**Data flow**: It receives an HTTP client, stream, and optional cursor. If the stream has no supported pagination declaration, it raises NotImplementedError. Otherwise it yields each page produced by paginate_from_strategy.

**Call relations**: paginate_source calls this in the normal flow. It delegates strategy-based work to paginate_from_strategy so common pagination patterns stay centralized.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (paginate_source).


##### `RestConnector.paginate_from_strategy`  (lines 345–407)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs one of the built-in pagination patterns declared on a stream. It is the dispatcher that turns a stream’s pagination settings into the correct page-fetching loop.

**Data flow**: It receives a stream and HTTP client. It reads the stream’s pagination specification, chooses the request path, validates that required settings are present, and then yields pages from the matching helper: cursor, Link header, or offset/limit. If there is no active strategy, it returns without yielding.

**Call relations**: paginate calls this for streams that use declarative pagination. It hands off to _get_cursor_pages, _get_link_header_pages, or _get_offset_pages, and may call _strategy_path when the stream did not specify its own path.

*Call graph*: calls 4 internal fn (_get_cursor_pages, _get_link_header_pages, _get_offset_pages, _strategy_path); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 377–379)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Extracts records from the JSON body of a link-header paginated response when those records are nested under a configured path. It is a small parser created only for that pagination run.

**Data flow**: It receives an HTTP response. It parses the JSON body if present, then uses records_at with the configured record path to return a clean list of record dictionaries.

**Call relations**: paginate_from_strategy passes this parser into _get_link_header_pages when a link-header stream says its records are not a top-level list.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 409–437)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: Fetches pages from APIs that put the next-page URL in an HTTP Link header. This is common in REST APIs that say “here is the next page” outside the JSON body.

**Data flow**: It receives a client, starting path, optional query parameters, optional page size settings, and an optional parser. It fetches the first page, yields records from each response, reads the next link from headers, checks for repeated links and excessive pages, and follows links until none remain.

**Call relations**: paginate_from_strategy calls this for next_link streams. It uses _get_raw for retried network calls, _response_list or a custom parser for records, next_link for continuation, and the bound helpers for loop safety.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 439–471)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: Fetches pages from APIs that return a next cursor token inside the response body. A cursor is a marker the server gives back so the client can ask for the following page.

**Data flow**: It receives a client, path, record path, cursor path, query settings, and optional page size. Each loop builds query parameters, includes the previous cursor if there is one, fetches JSON, extracts records, yields them, reads the next cursor, and stops when no valid cursor remains. It also checks page and cursor bounds.

**Call relations**: paginate_from_strategy calls this for next_cursor streams. It relies on _get for retried JSON reads, records_at for records, get_path for cursor lookup, and the bound helpers to prevent endless loops.

*Call graph*: calls 5 internal fn (_get, _bound_cursor, _bound_pages, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_odata_pages`  (lines 473–499)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Microsoft Graph or OData-style pages. In this style, records live under a "value" field and the next page URL appears as "@odata.nextLink".

**Data flow**: It receives a client, path, and optional first-request parameters. It fetches each page, extracts dictionary records from value, yields them, then follows @odata.nextLink if present. Only the first request uses the original parameters because the next link already contains continuation details.

**Call relations**: This helper is not reached by the declarative dispatcher in this file, but REST connector subclasses can call it from custom pagination. It uses _get_raw, list_or_empty, and the pagination safety checks.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 501–541)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: Fetches pages from APIs that use offset and limit numbers. This style asks for “records starting at position N, up to this many records.”

**Data flow**: It receives a client, path, record path, limit, parameter names, optional base params, and optional response paths that describe continuation. It repeatedly sends requests with the current offset, yields extracted records, decides whether another page exists, and advances the offset by the limit or by a server-reported step.

**Call relations**: paginate_from_strategy calls this for offset_limit streams. It uses _get for retried JSON reads, records_at and get_path to inspect responses, _int_or_none for server-reported limits, and _bound_pages as the safety brake.

*Call graph*: calls 5 internal fn (_get, _bound_pages, _int_or_none, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_page_number_pages`  (lines 543–572)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: Fetches pages from APIs that use page numbers, such as page=1, page=2, and so on. It stops when a page contains fewer records than the requested page size.

**Data flow**: It receives a client, path, record path, page size, optional params, parameter names, and starting page. It requests one page number at a time, yields any records found, stops on a short page, and otherwise increments the page number.

**Call relations**: This helper is available to subclasses with custom pagination needs. It uses _get for network reads, records_at for extracting records, and _bound_pages to avoid endless page-number loops.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at).


##### `RestConnector._strategy_path`  (lines 574–580)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: Resolves the request path for a stream when the pagination specification did not include one. The base class raises an error because only provider-specific connectors know their path table.

**Data flow**: It receives a stream and immediately raises NotImplementedError with a message explaining that no path was available and no override was provided.

**Call relations**: paginate_from_strategy calls this only when a stream’s pagination settings omit the path. Subclasses can override it to supply paths from their own stream metadata.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 582–585)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Converts one provider record into the flat dictionary shape that the sync writer expects. The default returns the record unchanged.

**Data flow**: It receives a record dictionary and its stream. It returns the same record as-is, unless a subclass overrides the method to lift nested fields or reshape the record.

**Call relations**: fetch_page calls this for every record after validation and before yielding pages to the rest of the sync system.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 587–598)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: Checks that pagination yielded the expected shape: a list of dictionaries. This catches connector bugs early, before bad data reaches the writer.

**Data flow**: It receives a page-like value and stream. If the page is not a list, it raises TypeError. If any item in the list is not a dictionary, it raises TypeError naming the bad item type. Valid pages pass through without changes.

**Call relations**: fetch_page calls this before flattening and yielding records. It acts as a guardrail between provider-specific pagination code and the shared sync pipeline.

*Call graph*: called by 1 (fetch_page).
