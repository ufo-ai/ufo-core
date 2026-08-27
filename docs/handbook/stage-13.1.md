# Source Connector Framework  `stage-13.1`

The Source Connector Framework is shared behind-the-scenes support for sync jobs. A sync job needs to pull records from many outside services, such as email, chat, or code hosting. Those services all behave differently, so this stage gives them a common shape before the rest of the system sees them.

`connector.py` defines the basic contract for a connector: what it must provide, how it returns records, and how it moves through pages of results. Pagination means fetching a long list in smaller chunks, like turning pages in a book, so the system does not overload itself or the provider.

`rest.py` builds on that contract for services reached through REST APIs, which are web endpoints called over HTTP. It handles the repeated chores: setting up authentication, sending requests, retrying when temporary errors happen, following pagination rules, checking responses for safety, and cleaning records into a standard form.

Together, these files let each source connector focus on service-specific details while the sync system gets dependable, predictable data.

## Files in this stage

### Connector Contract and REST Support
Defines the shared source-connector interface and the reusable REST machinery for authentication, retries, pagination, record cleanup, and safe HTTP reads.

### `core/src/ufo/sources/connector.py`

`domain_logic` · `during sync runs, especially while fetching and checkpointing source data`

This file answers a basic question: “What does it mean to connect to an outside service and read its data?” It defines small shared shapes, such as a stream description, a page of records, and cursor information. A cursor is a saved bookmark that lets the next sync continue from the right place instead of starting over.

The central idea is the Connector abstract class. Each real provider implements it by declaring its streams, fetching pages of records, and optionally turning a raw record into readable text. Without this contract, every provider would need custom wiring throughout the system.

The most important built-in helper is PartitionWalk. Some sources are not one long list; they are many smaller lists, such as one list per repository or one list per chat channel. PartitionWalk is like a careful mail carrier with a checklist for each street. It remembers which partition was finished, which one was halfway through, and where to resume next time. It supports different orderings: oldest-first, newest-first, or no useful ordering at all. It also protects against losing records when a run stops early or when new records arrive while older history is still being backfilled.

The file deliberately keeps these page and cursor objects internal and simple. They are not network API models; they are local tools for making sync runs reliable.

#### Function details

##### `PartitionWalk.stream`  (lines 233–340)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This function walks through all partitions of a stream and yields sync pages while continuously saving a safe resume point. It is used when one logical stream is split into many provider-side areas, such as channels, repositories, or folders.

**Data flow**: It starts with an optional saved cursor string, turns that into a per-partition map, then asks for the list of partitions. For each partition, it decides what boundary to pass to the page-producing function: start fresh, continue a backfill, fetch only newer records, or skip a partition already completed in an unordered pass. As pages come back, it updates the checkpoint, yields records and deletions wrapped in StreamPage objects, and includes the newly encoded cursor. If a partition is skipped because the provider refuses it temporarily, the function leaves that partition’s saved state alone and moves on. At the end, it cleans up cursors for partitions that disappeared or clears unordered one-pass markers.

**Call relations**: This is the main driver for partitioned streams. It first calls PartitionWalk._decode to understand the saved cursor, repeatedly calls PartitionWalk._encode whenever it needs to attach a fresh bookmark to an outgoing StreamPage, and builds PartitionBound objects to tell the connector’s page factory what slice to fetch next. It is the only function in this file that calls the cursor encode/decode helpers.

*Call graph*: calls 2 internal fn (_decode, _encode); 3 external calls (__init__, __init__, __init__).


##### `PartitionWalk._decode`  (lines 343–372)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: This function turns the stored cursor text back into the partition-by-partition checkpoint map used by PartitionWalk.stream. It also rejects cursor entries that look like they were written by this walker but are malformed.

**Data flow**: It receives a cursor string or nothing. If the cursor is missing, not JSON, or not a JSON object, it treats it as empty because it may have come from some other cursor style. If it is a JSON object, each value becomes either a simple watermark string or a validated backfill window containing high and until bounds. The result is a dictionary that PartitionWalk.stream can use to resume work safely. Bad entries raise an error instead of being silently ignored.

**Call relations**: PartitionWalk.stream calls this once at the start of a walk. After that, stream uses the decoded map to decide which partitions are already done, which are mid-backfill, and which need a fresh scan.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 375–380)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: This function turns the current per-partition checkpoint map into a stable JSON cursor string that can be saved and used by a later sync run.

**Data flow**: It receives a mapping from partition names to either watermark strings or backfill-window objects. It converts any window object into plain data, then serializes the whole map as JSON with sorted keys. The output is a string suitable for storing as the next cursor.

**Call relations**: PartitionWalk.stream calls this whenever it yields a page or records a checkpoint change. The encoded string becomes the resume bookmark carried on StreamPage.next_cursor.

*Call graph*: called by 1 (stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 399–400)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This abstract method tells the system which streams a connector can sync. A stream is one source-side collection, such as messages, users, documents, or repositories.

**Data flow**: A concrete connector supplies no input beyond itself and returns a list of StreamSpec objects. Each StreamSpec describes important facts such as the stream name, record identity field, cursor field, deletion behavior, and pagination style.

**Call relations**: Concrete connector classes implement this method. During source registration, ConnectedSources._register calls it to learn what streams the connector offers and how those streams should be set up.

*Call graph*: called by 1 (_register).


##### `Connector.fetch_page`  (lines 403–418)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None) -> AsyncIterator[list[dict[str, Any]]
```

**Purpose**: This abstract method is the connector’s promise for actually reading records from the outside service. Each provider implements it with the API calls, authentication, filtering, and paging rules needed for that service.

**Data flow**: It receives a stream description, an optional cursor bookmark, a resolved credential, a base URL, an optional user id to exclude, and an optional backfill floor date. The concrete implementation uses those inputs to request data from the provider and asynchronously yields pages. Each yielded page is either a plain list of records or a StreamPage that can also name deletions and carry a provider-specific next cursor.

**Call relations**: No direct caller is shown in the supplied graph because this is an abstract contract. In the broader connector flow, concrete implementations are called by the sync machinery when it needs to pull records for one stream.


##### `Connector.render`  (lines 420–439)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function converts one raw provider record into a title and a readable body for recall or indexing. It gives connectors a safe default so simple data sources do not need to write their own rendering code.

**Data flow**: It receives a record dictionary and its StreamSpec. It first looks for a human-friendly title field such as title, name, login, or subject. If none exists, it falls back to the stream’s primary key and builds a title from that. If neither a usable title nor identity is present, it raises an error because the record cannot be named. It returns a pair: the title, and a body containing a heading plus the record serialized as sorted JSON.

**Call relations**: This default method uses json.dumps to turn the record into stable text. Content-heavy connectors, such as document or email providers, can override it when raw JSON would be less useful than prose.

*Call graph*: 1 external calls (dumps).


### `core/src/ufo/sources/rest.py`

`io_transport` · `source sync read fetching`

Many services expose data through REST APIs, which are web endpoints that return structured data such as JSON. Each service has its own details, but they all share the same chores: build an HTTP client with the right credential, ask for pages of records, wait and retry when the service is temporarily busy, and stop safely if pagination goes wrong. This file is the reusable foundation for that work.

RestConnector is the base class that provider-specific connectors inherit from. A connector declares its streams, meaning the named groups of records it can read, and may describe how each stream is paginated. If the pagination is a common style, this file can do the whole loop. If the provider has an unusual shape, the connector can override paginate while still using the same safe HTTP helpers.

The file also protects the wider sync run. It rejects unauthenticated requests, includes response bodies in HTTP errors so failures are understandable, retries temporary network and server problems with backoff, and limits pagination loops so a bad API cannot spin forever. Think of it like a reusable delivery route planner: individual drivers know the destination, but this file supplies the vehicle, traffic rules, retry policy, and safeguards.

#### Function details

##### `get_path`  (lines 49–58)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a value from nested dictionary-like data using a dotted path such as "user.name". It is used when an API response stores records or cursors inside several layers of JSON.

**Data flow**: It takes a mapping, a dotted path, and a default value. It walks through each path part one by one; if a step is missing or stops being dictionary-like, it returns the default. Otherwise it returns the final value it found.

**Call relations**: Pagination helpers call this when they need to find records, continuation tokens, or provider-specific flags inside response bodies. records_at also uses it as the common way to reach a nested list.

*Call graph*: called by 3 (_get_cursor_pages, _get_offset_pages, records_at).


##### `list_or_empty`  (lines 61–65)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: Turns a possible list of records into a clean list of dictionaries. If the value is not a list, or if some list items are not dictionaries, those invalid parts are ignored.

**Data flow**: It receives any value. If the value is a list, it keeps only the items that look like record dictionaries and returns them; otherwise it returns an empty list.

**Call relations**: Response parsing code uses this before yielding records, so later sync code receives predictable dictionary records instead of random JSON shapes.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 68–71)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Returns a value only if it is a dictionary-like record. It is a small helper for connectors that need to pull a nested object out of a larger API response.

**Data flow**: It receives any value. If that value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: This helper is available for provider-specific connector code, even though this file does not call it directly. It supports the same defensive style as list_or_empty.


##### `records_at`  (lines 74–79)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: Finds a list of record dictionaries at a chosen place in a response body. It lets pagination code work with APIs that either return a top-level list or hide records under a named field.

**Data flow**: It receives response data and an optional path. With no path, it treats the whole value as the list; with a path, it first walks to that nested value. In both cases it returns only dictionary records, or an empty list if none are found.

**Call relations**: Cursor, offset, page-number, and link-header pagination use this to extract records before yielding them. The nested parse function inside paginate_from_strategy also uses it for link-header APIs with enveloped responses.

*Call graph*: calls 2 internal fn (get_path, list_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, parse).


##### `_int_or_none`  (lines 82–87)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: Converts a value to an integer only when that conversion is clearly safe. It avoids guessing when an API sends an unexpected value.

**Data flow**: It receives any value. If it is already an integer, it returns it; if it is a string made only of digits, it converts and returns it. Otherwise it returns None.

**Call relations**: Offset pagination uses this when a provider reports the page size it actually used. That helps the next request advance by the provider's real step instead of assuming.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 90–93)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: Copies records and adds extra context fields to each one, such as a parent account or site id. This helps later fan-out reads remember where each record came from.

**Data flow**: It receives an iterable of record dictionaries plus named context values. It creates new dictionaries that combine each original record with the context, and returns the new list.

**Call relations**: This is a convenience for provider-specific connectors that split work across parent objects. It prepares records so later rendering or child requests can trace their origin.


##### `next_link`  (lines 96–101)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: Reads the standard HTTP Link header and extracts the URL marked as the next page. Some APIs put pagination instructions in headers instead of the JSON body.

**Data flow**: It receives response headers. It looks for a Link header, searches for a rel="next" entry, and returns that URL if present; otherwise it returns None.

**Call relations**: The link-header pagination loop calls this after each response to decide whether to fetch another page or stop.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 104–109)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: Decides whether a failed request is worth trying again. It treats network transport problems and temporary HTTP statuses like rate limits or server errors as retryable.

**Data flow**: It receives an exception. If the exception is a network-level error, it returns true; if it is an HTTP error with a retryable status code, it returns true; otherwise it returns false.

**Call relations**: The shared _send retry wrapper calls this after a failed attempt. Its answer decides whether the wrapper sleeps and tries again or lets the error fail the sync.

*Call graph*: called by 1 (_send).


##### `_retry_after`  (lines 112–132)

```
def _retry_after(error: BaseException) -> float | None
```

**Purpose**: Reads a provider's Retry-After instruction when the response says to come back later. This lets the connector respect rate limits instead of hammering the service.

**Data flow**: It receives an exception. If it is an HTTP error with a status that may include Retry-After, it reads the header, parses it as a finite non-negative number of seconds, and returns that number; otherwise it returns None.

**Call relations**: _retry_wait uses this as one possible source of the next sleep time. It filters out invalid values before they can reach asyncio.sleep.

*Call graph*: called by 1 (_retry_wait); 1 external calls (isfinite).


##### `_retry_wait`  (lines 135–152)

```
def _retry_wait(error: BaseException, delay: float) -> float
```

**Purpose**: Chooses how long to wait before retrying a temporary failure. It combines exponential backoff, provider Retry-After hints, caps, and random jitter so many workers do not retry at the exact same moment.

**Data flow**: It receives the error and the current base delay. It checks for a usable Retry-After value, compares that with the normal backoff delay, applies maximum limits, adds randomness within a safe range, and returns the number of seconds to sleep.

**Call relations**: _send calls this whenever it has decided a request may be retried. It relies on _retry_after for provider-supplied timing.

*Call graph*: calls 1 internal fn (_retry_after); called by 1 (_send); 1 external calls (uniform).


##### `_raise_for_status`  (lines 155–166)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: Turns an unsuccessful HTTP response into an exception with a useful message. Unlike the default behavior, it includes a capped slice of the response body, which often contains the real API error.

**Data flow**: It receives an HTTP response. If the response is successful, it does nothing; otherwise it builds an HTTPStatusError containing the status, request, URL, and a short body excerpt, then raises it.

**Call relations**: _send calls this after every response. That means all GET and POST helpers share the same error format.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 169–173)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: Parses a response body as a JSON object, while treating empty successful responses as an empty dictionary. It is for endpoints expected to return object-shaped data.

**Data flow**: It receives an HTTP response. If the response has no body or is a 204 No Content response, it returns {}; otherwise it parses and returns the JSON body.

**Call relations**: _get and _post use this after _send has finished the actual HTTP request. It turns raw responses into ordinary dictionaries for pagination code.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 176–179)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Parses a response body as a list of record dictionaries, while treating empty responses as no records. It is useful for APIs that return the page itself as a top-level array.

**Data flow**: It receives an HTTP response. If there is no content, it returns an empty list; otherwise it parses JSON and keeps only dictionary items from the resulting list.

**Call relations**: Link-header pagination uses this when no custom record parser was supplied. It relies on list_or_empty to keep the output safe and predictable.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 182–188)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: Stops a pagination loop that has gone on for too many pages. This prevents a broken or hostile API from keeping a sync run stuck forever.

**Data flow**: It receives a human-readable name for the loop and the current page count. If the count is above the maximum allowed pages, it raises an error; otherwise it lets the loop continue.

**Call relations**: Every built-in pagination loop calls this once per page. It is the shared safety rail for cursor, link, OData, offset, and page-number pagination.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 191–197)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: Stops pagination when a provider repeats the same next-page token or URL. A repeated cursor means the API is not advancing and would otherwise fetch the same page forever.

**Data flow**: It receives a loop name, the new token, and a set of tokens already seen. If the token is already in the set, it raises an error; otherwise it records the token.

**Call relations**: Cursor-style loops, link-header loops, and OData loops call this after finding the next-page marker. It catches infinite loops earlier and more clearly than the page-count limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 206–207)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the list of streams this connector can read. A stream is a named collection of records, such as users, tickets, or repositories.

**Data flow**: It reads the class's streams_list and returns a new list copy. The copy prevents callers from accidentally modifying the connector's class-level declaration.

**Call relations**: The wider connector framework asks for streams when planning what to sync. Provider-specific subclasses usually fill in streams_list rather than rewriting this method.


##### `RestConnector._make_client`  (lines 209–226)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to one provider account. It applies the resolved credential, common JSON headers, base URL, and request timeouts.

**Data flow**: It receives a base URL and a Credential. If the credential supplies a proxy transport, it builds a client that sends through that transport; if it supplies a bearer token or headers, it adds them to outgoing requests. If no authentication is available, it raises an error.

**Call relations**: fetch_page calls this before pagination starts. All later _get and _post calls use the client it creates, so authentication and timeout behavior stay consistent.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 228–231)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a GET request and returns the response as a dictionary. It is the common helper for read endpoints that return object-shaped JSON.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It asks _get_raw to make the retried request, then converts the raw response with _json_or_empty. The result is a dictionary response body.

**Call relations**: Cursor, offset, and page-number pagination call this for each page request. It hands the network work to _get_raw so retries stay centralized.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 3 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages).


##### `RestConnector._get_raw`  (lines 233–237)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Performs a GET request and returns the raw HTTP response. This is needed when pagination information is in headers or when callers need more than the JSON body.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It wraps client.get in the shared _send retry logic and returns the successful response.

**Call relations**: _get builds on this for normal JSON-object responses. Link-header and OData pagination also call it directly because they need headers or full response details.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 239–244)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a POST request for read-only API endpoints that require POST, then returns object-shaped JSON. It does not add a write path; it is still for reading data.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It sends the POST through the shared retry wrapper and parses the response into a dictionary or empty dictionary.

**Call relations**: Provider-specific connectors can use this for APIs such as search endpoints that read data through POST. It shares retry and error behavior with GET requests.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 246–252)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Performs a POST request for read-only endpoints and returns the raw HTTP response. It is useful when the response shape is not a normal JSON object.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It sends the POST through _send and returns the successful raw response.

**Call relations**: Provider-specific connectors can call this when they need headers or array-shaped bodies from a POST-based read endpoint. It keeps the same retry policy as _post.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 254–275)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: Runs one HTTP request with the connector's shared retry rules. It protects syncs from short-lived network failures, rate limits, and temporary server problems.

**Data flow**: It receives a no-argument async request function. It calls it, checks the response status, and returns the successful response. If a retryable failure happens, it chooses a wait time, sleeps, and tries again until attempts or total waiting budget run out; non-retryable failures are raised.

**Call relations**: _get_raw, _post, and _post_raw all use this. It is the single retry envelope, so provider-specific connectors get the same behavior when they use the base HTTP helpers.

*Call graph*: calls 3 internal fn (_is_retryable, _raise_for_status, _retry_wait); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 277–314)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[
```

**Purpose**: This is the main read entry for one stream page sequence. It opens the HTTP client, runs pagination, validates each page, flattens records, and yields clean pages to the sync driver.

**Data flow**: It receives the stream, cursor, credential, base URL, acting user id, and optional backfill boundary. It creates a client, gets an async page source, skips empty pages, validates record shapes, applies flatten to each record, and yields either plain record lists or StreamPage objects with deletes and next cursors preserved. When done or interrupted, it closes the async generator if needed.

**Call relations**: The core sync driver calls this to read from a REST source. It delegates provider-specific page production to paginate_source and uses _make_client, _validate_page, and flatten around that output.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate_source); 1 external calls (__init__).


##### `RestConnector.paginate_source`  (lines 316–329)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Bridges fetch_page to the connector's pagination method. It exists so subclasses can accept extra run-time information, such as the acting user or backfill floor, without changing the basic fetch flow.

**Data flow**: It receives the HTTP client, stream, cursor, self user id, and optional backfill timestamp. By default it ignores the extra fields and returns paginate(client, stream, cursor=cursor).

**Call relations**: fetch_page calls this after opening the client. Most connectors use the default path into paginate, while specialized connectors can override it to pass extra context into custom pagination.

*Call graph*: calls 1 internal fn (paginate); called by 1 (fetch_page).


##### `RestConnector.paginate`  (lines 331–343)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Produces raw pages of records for a stream. The default implementation only works when the stream declares a standard pagination strategy.

**Data flow**: It receives the HTTP client, stream, and optional cursor. If the stream has no usable pagination declaration, it raises NotImplementedError. Otherwise it delegates to paginate_from_strategy and yields each page it produces.

**Call relations**: paginate_source calls this by default. Provider-specific connectors override it when their API needs a custom shape, such as fan-out across organizations or repositories.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (paginate_source).


##### `RestConnector.paginate_from_strategy`  (lines 345–407)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs one of the built-in pagination styles described on a stream. It turns a declarative pagination setting into actual repeated HTTP requests.

**Data flow**: It reads the stream's pagination spec, chooses the request path, validates required fields, and calls the helper for cursor, Link-header, or offset-limit pagination. Each helper yields lists of record dictionaries, which this method passes onward.

**Call relations**: paginate calls this for streams with standard pagination. It hands work to _get_cursor_pages, _get_link_header_pages, or _get_offset_pages, and may call _strategy_path if the spec did not include a path.

*Call graph*: calls 4 internal fn (_get_cursor_pages, _get_link_header_pages, _get_offset_pages, _strategy_path); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 377–379)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Extracts records from a link-header paginated response when those records are nested inside the JSON body. It is a small parser built only when the stream declares a record path.

**Data flow**: It receives an HTTP response. It parses the body as JSON if present, then uses records_at to pull out the list at the declared path. The result is a clean list of record dictionaries.

**Call relations**: paginate_from_strategy passes this parser into _get_link_header_pages for APIs that use headers for the next page but put records inside a response envelope.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 409–437)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: Fetches pages from APIs that advertise the next page in the HTTP Link header. This is common in REST APIs that return a URL marked rel="next".

**Data flow**: It receives a client, path, optional query parameters, optional page-size settings, and an optional record parser. It fetches the first page, yields records if present, reads the next link from headers, checks for repeated links, and follows that URL until no next link remains.

**Call relations**: paginate_from_strategy calls this for the next_link strategy. It uses _get_raw for requests, next_link for header parsing, _response_list or a custom parser for records, and the bound checks for safety.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 439–471)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: Fetches pages from APIs that put a next-page cursor token inside the response body. A cursor is like a bookmark the API gives back for the next request.

**Data flow**: It receives a client, path, record path, cursor path, cursor parameter name, optional page-size setting, and extra parameters. It repeatedly sends GET requests, extracts records, yields them, reads the next cursor, and stops when there is no valid cursor. It checks that cursors do not repeat.

**Call relations**: paginate_from_strategy calls this for the next_cursor strategy. It uses _get to fetch JSON dictionaries, records_at to find records, get_path to find the cursor, and bound helpers to prevent endless loops.

*Call graph*: calls 5 internal fn (_get, _bound_cursor, _bound_pages, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_odata_pages`  (lines 473–499)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Microsoft Graph or OData-style pages. In this style, records live under a value field and the next-page URL lives under @odata.nextLink.

**Data flow**: It receives a client, path, and optional parameters. It fetches the first URL with the caller's parameters, yields dictionary records from value, then follows @odata.nextLink URLs until none remain. Only the first request uses the original parameters because later links already contain their own query.

**Call relations**: This helper is available to provider-specific connectors that need OData pagination. It uses _get_raw, list_or_empty, and the shared page and cursor bounds.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 501–541)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: Fetches pages from APIs that use offset and limit parameters. This means each request asks for records starting at a numbered position with a chosen page size.

**Data flow**: It receives the client, path, record path, limit, parameter names, optional extra parameters, and optional response fields that say whether more data exists or what limit was applied. It requests a page, yields records, decides whether to stop, and advances the offset for the next request.

**Call relations**: paginate_from_strategy calls this for the offset_limit strategy. It uses _get to fetch data, records_at to extract records, get_path for provider continuation fields, _int_or_none for reported limits, and _bound_pages for safety.

*Call graph*: calls 5 internal fn (_get, _bound_pages, _int_or_none, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_page_number_pages`  (lines 543–572)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: Fetches pages from APIs that use page numbers rather than cursors or offsets. It starts at a chosen page number and increments by one.

**Data flow**: It receives the client, path, record path, page size, optional parameters, parameter names, and start page. It requests each page, yields records if any, and stops when the returned page is shorter than the requested page size.

**Call relations**: This helper is available for provider-specific connectors with page-number pagination. It uses _get for requests, records_at for extraction, and _bound_pages to avoid endless loops.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at).


##### `RestConnector._strategy_path`  (lines 574–580)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: Provides a request path for a stream whose pagination spec did not name one. The base version fails so subclasses must be explicit about how to resolve such paths.

**Data flow**: It receives a stream. Instead of guessing, it raises NotImplementedError explaining that the pagination path is missing and no subclass override exists.

**Call relations**: paginate_from_strategy calls this only when the pagination declaration has no path. Connectors with their own stream-to-path table can override it.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 582–585)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Converts a provider record into the flat dictionary shape written by the sync. The default returns the record unchanged.

**Data flow**: It receives one record dictionary and its stream. It returns the same dictionary unless a subclass overrides the method to move nested fields into a flatter shape.

**Call relations**: fetch_page calls this on every record after validation and before yielding pages. Provider-specific connectors override it when an API wraps useful fields inside nested envelopes.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 587–598)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: Checks that pagination produced the expected shape: a list of dictionary records. It catches connector mistakes early with a clear error message.

**Data flow**: It receives a page and stream. If the page is not a list, it raises TypeError; if any item is not a dictionary, it raises TypeError naming the bad item type. If all records are valid, it changes nothing.

**Call relations**: fetch_page calls this before flattening and yielding any page. This protects downstream sync code from unexpected values produced by custom paginate implementations.

*Call graph*: called by 1 (fetch_page).
