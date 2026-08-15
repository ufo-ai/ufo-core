# Source synchronization framework and registry  `stage-16.4`

This stage is shared behind-the-scenes support for syncing outside content into the system. It is the framework that lets services such as GitHub, Zendesk, or other APIs all plug in the same way, even though each service has its own rules.

The package marker files simply make the source folders importable by Python. The connector module defines the basic agreement every connector must follow, including how progress is tracked with “cursors,” which are bookmarks for where the last sync stopped. The REST helper gives API-based connectors common tools for logging in, retrying failed requests, and moving through paged results. The backend module translates a connector’s raw records into the system’s standard sync output.

The sync module is the main engine. It fetches pages, stores updates, removes deleted content, retries failures, and produces a change feed for indexers. The tools module lets users create, view, edit, delete, and watch synced sources. The direct module supports API-key login. The registry is the address book that maps each source name to its connector class.

## Files in this stage

### Connector foundations
Core package markers and connector contracts establish the shared vocabulary and REST helper layer used by source implementations.

### `core/src/ufo/sources/__init__.py`

`other` · `import/package discovery`

This file does not contain any code, but it still has a job. In Python, a folder with an `__init__.py` file is treated as a package, which means code elsewhere can refer to modules inside it using package-style imports such as `ufo.sources.something`. Think of it like putting a label on a drawer: the drawer may hold many useful tools, and the label tells the rest of the system where to find them.

Because this file is empty, it does not run setup logic, define shared shortcuts, or change how imports behave. Its purpose is simply structural. Without it, depending on the Python version and packaging setup, code that expects `ufo.sources` to be a normal package might fail to import modules from this directory or behave differently than expected.


### `core/src/ufo/sources/connector.py`

`domain_logic` · `during source sync runs`

A connector is the project’s adapter for an outside service, such as a document system, mail provider, chat app, or code host. This file sets the rules for those adapters: what streams of data they expose, how they return records page by page, and how each record becomes searchable text later. Without this shared shape, every provider would speak a different language and the sync engine would not know how to resume work, detect deletions, or turn records into readable pages.

The simple data classes describe the pieces of a sync. A StreamSpec says what kind of records exist, what field identifies each record, and whether the stream is a full snapshot or an incremental feed. A StreamPage is one batch of records, optionally with deleted record IDs and a cursor for resuming later. Pagination and Ordering describe common provider behaviors in plain, reusable terms.

The most active part is PartitionWalk. Some sources are split into many smaller lanes, like one lane per Slack channel or GitHub repository. PartitionWalk is the careful notebook that remembers where syncing stopped in each lane. It supports oldest-first streams, newest-first streams, and streams with no useful time marker. It saves progress after pages, resumes unfinished backfills, and avoids skipping records when pages are cut in the middle. Finally, the Connector abstract class tells each provider what methods it must implement, while giving a default render method that turns a raw record into a title and JSON body.

#### Function details

##### `PartitionWalk.stream`  (lines 233–340)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main walking loop for a stream split across many partitions, such as many repositories or channels. It reads the saved position for each partition, asks the connector for pages within the right bounds, yields those pages to the sync engine, and updates the cursor so a later run can continue safely.

**Data flow**: It starts with an optional saved cursor string and decodes it into a per-partition progress map. It then reads partition names from the connector-provided partition iterator, builds a PartitionBound telling the page factory where to start or stop, receives WalkPage batches, converts them into StreamPage batches, and attaches a newly encoded cursor after each checkpoint. Along the way it changes its local checkpoint map, skips partitions that the provider reports as temporarily unavailable, closes async generators when done, and finally emits a cleanup cursor if finished partitions or disappeared partitions need to be removed from saved state.

**Call relations**: The sync flow calls this when a connector needs shared partition-walking behavior instead of writing its own resume loop. It first relies on PartitionWalk._decode to understand the stored cursor, repeatedly uses PartitionWalk._encode to save updated progress into outgoing StreamPage objects, and creates PartitionBound values so the connector’s page factory knows what slice of a partition to fetch. Its yielded StreamPage objects are then consumed by the outer connector sync machinery.

*Call graph*: calls 2 internal fn (_decode, _encode); 3 external calls (__init__, __init__, __init__).


##### `PartitionWalk._decode`  (lines 343–372)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: This turns a stored cursor string back into the partition progress map that PartitionWalk.stream needs. It is intentionally forgiving of cursors that came from some other cursor style, but strict about malformed partition-walk cursors.

**Data flow**: It receives a cursor string or None. If the cursor is missing, not valid JSON, or not a JSON object, it treats that as no partition progress and returns an empty map. If it is a JSON object, each partition value becomes either a plain watermark string or a validated _Window with high and until bounds; invalid entries raise an error instead of silently losing sync progress.

**Call relations**: PartitionWalk.stream calls this at the start of a walk to rebuild its in-memory checkpoint state. It uses json.loads to parse the saved text and, when a partition is in the middle of a newest-first backfill, validates that window before stream continues.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 375–380)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: This turns the current per-partition progress map into a JSON cursor string that can be stored and used by the next sync run. It is the reverse of _decode.

**Data flow**: It receives a mapping from partition names to either watermark strings or _Window objects. It converts any _Window into a plain dictionary, then serializes the whole map into sorted JSON text. The output is a stable cursor string that travels inside StreamPage.next_cursor.

**Call relations**: PartitionWalk.stream calls this whenever it needs to attach updated progress to a page or cleanup checkpoint. The resulting cursor is handed onward with StreamPage so the outer sync system can persist it.

*Call graph*: called by 1 (stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 394–395)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This abstract method requires every connector to declare the streams it can sync. A stream is one named collection of provider records, such as messages, documents, issues, or users.

**Data flow**: A concrete connector implements this with no input beyond itself. It returns a list of StreamSpec objects, each describing one source-side collection, its record ID field, cursor behavior, deletion behavior, and other sync rules.

**Call relations**: The connector framework calls this when it needs to know what data a provider offers. Because this base method is abstract, the real work is done by each provider-specific subclass.


##### `Connector.fetch_page`  (lines 398–413)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None) -> AsyncIterator[list[dict[str, Any]]
```

**Purpose**: This abstract method is the required provider-specific fetch operation. It asks a connector to produce records for one stream, page by page, starting from a saved cursor and using an already resolved credential.

**Data flow**: It receives a StreamSpec, an optional cursor, a credential, a base URL, an optional current user ID to exclude where needed, and an optional backfill floor date. A concrete connector uses those inputs to call the outside service and asynchronously yields either simple record lists or richer StreamPage objects that may include deletions and a next cursor.

**Call relations**: The sync adapter calls this while running a stream. This base class only defines the contract; provider subclasses implement the actual network requests, pagination, filtering, and conversion into the page shapes defined in this file.


##### `Connector.render`  (lines 415–434)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns one raw provider record into a human-readable page title and body for recall or search. It gives every connector a safe default, while allowing content-heavy connectors to override it with nicer prose.

**Data flow**: It receives a record dictionary and its StreamSpec. It looks for a useful title-like field such as title, name, login, or subject; if none exists, it falls back to the record’s primary key and raises an error if even that is missing or empty. It returns a pair: the chosen title, and a body containing a heading plus the record serialized as sorted JSON.

**Call relations**: The connector adapter uses this after records are fetched to create the text that gets stored as recallable content. It calls json.dumps for the default body, but provider subclasses such as document or email connectors can override this method when raw JSON would be less useful to readers.

*Call graph*: 1 external calls (dumps).


### `core/src/ufo/sources/rest.py`

`io_transport` · `request handling during source sync reads`

Many external services expose data through REST APIs, but each service has slightly different rules for logging in, asking for the next page, and packaging records. This file keeps every connector from rewriting that same plumbing. It is like a reusable travel kit for API readers: it prepares the HTTP client, makes requests safely, waits and retries when a service is temporarily busy, and walks through pages until all records have been read.

The main class, RestConnector, is meant to be subclassed by a provider-specific connector. A subclass names its streams, gives a base URL, and either describes a standard pagination style or overrides pagination for unusual APIs. The file supports several common paging patterns: a next cursor in the response body, a next link in the response headers, offset-and-limit pages, page numbers, and Microsoft-style OData links.

The retry logic is important. Without it, a short network hiccup or a rate-limit response could make a whole sync fail immediately. The file also protects against broken pagination, such as an API returning the same cursor forever, by stopping with a clear error instead of looping endlessly. It deliberately only supports reading data; write operations are not part of this source connector layer.

#### Function details

##### `get_path`  (lines 49–58)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a dotted path, such as "data.items", from a nested dictionary-like object. It lets pagination code pull values out of API response envelopes without hard-coding each level by hand.

**Data flow**: It receives a mapping, a path string, and a fallback value. It walks through the path one piece at a time; if any level is missing or stops being dictionary-like, it returns the fallback. If the full path exists, it returns the found value.

**Call relations**: Pagination helpers call this when they need to find records, next cursors, continuation flags, or server-reported page sizes inside a response body. records_at uses it as its path-reading step before turning a value into a list of records.

*Call graph*: called by 3 (_get_cursor_pages, _get_offset_pages, records_at).


##### `list_or_empty`  (lines 61–65)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: Turns a value into a clean list of record dictionaries, or an empty list if the value is not a list. It filters out non-dictionary items so downstream code only sees record-shaped data.

**Data flow**: It receives any value. If the value is not a list, it returns an empty list. If it is a list, it keeps only the items that are dictionaries and returns those.

**Call relations**: This is used by response readers and pagination helpers whenever an API might return an unexpected shape. It helps _response_list, records_at, and the OData pager avoid passing bad record values onward.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 68–71)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Returns a value only if it is a dictionary, otherwise returns an empty dictionary. It is a small safety helper for cases where a connector expects a nested object.

**Data flow**: It receives any value. A dictionary goes through unchanged; anything else becomes an empty dictionary.

**Call relations**: This helper is available to provider connectors or nearby code that need a safe record-shaped object. It is not called by the functions listed for this file, but matches the same defensive style as list_or_empty.


##### `records_at`  (lines 74–79)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: Finds the list of records inside an API response, optionally under a named nested path. It gives pagination code one consistent way to extract rows from different response shapes.

**Data flow**: It receives response data and either a path or no path. With no path, it treats the data itself as the record list. With a path, it first reads that nested value using get_path, then returns only dictionary items using list_or_empty.

**Call relations**: Cursor, offset, page-number, and next-link pagination use this to turn a provider's response body into records. In the next-link strategy, the small inner parse function also uses it when records are not at the top level.

*Call graph*: calls 2 internal fn (get_path, list_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, parse).


##### `_int_or_none`  (lines 82–87)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: Converts a value to an integer only when it is already an integer or a string made only of digits. It avoids guessing when a server sends a strange page-size value.

**Data flow**: It receives any value. Integers pass through, digit-only strings become integers, and all other values become None.

**Call relations**: The offset pager uses this when a response says how many records the server actually returned or applied as a limit. If the value is not trustworthy, the pager falls back to safer local counts.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 90–93)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: Copies each record and adds extra context fields, such as a parent account or site identifier. This helps later steps know where a record came from after records from many sources are mixed together.

**Data flow**: It receives an iterable of record dictionaries plus named context values. For each record, it creates a new dictionary containing the original fields and the added context fields, then returns the new list.

**Call relations**: This is a convenience helper for connectors that fan out from parent objects to child records. It is not part of the default paging path in this file, but supports provider-specific pagination overrides.


##### `next_link`  (lines 96–101)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: Reads an HTTP Link header and extracts the URL marked as the next page. This supports APIs that put pagination instructions in response headers instead of the response body.

**Data flow**: It receives HTTP headers. It looks for a Link header, searches it for a rel=next entry, and returns that URL if found; otherwise it returns None.

**Call relations**: The link-header pager calls this after each response. If next_link returns a URL, the pager follows it; if it returns None, pagination stops.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 104–109)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: Decides whether a failed request is worth trying again. It treats network transport failures and temporary HTTP status codes, such as rate limits or server errors, as retryable.

**Data flow**: It receives an exception. Transport errors return true, HTTP status errors return true only for configured temporary status codes, and all other errors return false.

**Call relations**: RestConnector._send calls this inside its retry loop. The answer decides whether the connector waits and tries again or lets the error fail the sync.

*Call graph*: called by 1 (_send).


##### `_retry_after`  (lines 112–132)

```
def _retry_after(error: BaseException) -> float | None
```

**Purpose**: Reads a server's Retry-After instruction when the response status means "try again later." It ignores invalid, negative, infinite, or irrelevant values so the event loop is not given a bad sleep time.

**Data flow**: It receives an exception. If it is an HTTP status error with a suitable status code, it reads the retry-after header and converts it to a safe number of seconds. If anything does not fit, it returns None.

**Call relations**: _retry_wait calls this before choosing how long to pause. It gives server-provided timing a chance to guide the retry without letting an extreme value stall the run too long.

*Call graph*: called by 1 (_retry_wait); 1 external calls (isfinite).


##### `_retry_wait`  (lines 135–152)

```
def _retry_wait(error: BaseException, delay: float) -> float
```

**Purpose**: Chooses how long to wait before another request attempt. It combines exponential backoff, which means waiting longer after repeated failures, with random jitter so many workers do not retry at the exact same moment.

**Data flow**: It receives the error that happened and the current retry delay. It checks for a Retry-After value, compares it with the normal delay, caps both to safe limits, adds randomness within bounds, and returns the wait time in seconds.

**Call relations**: RestConnector._send calls this after a retryable failure. It uses _retry_after for server guidance and hands the final number to asyncio.sleep.

*Call graph*: calls 1 internal fn (_retry_after); called by 1 (_send); 1 external calls (uniform).


##### `_raise_for_status`  (lines 155–166)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: Turns an unsuccessful HTTP response into an exception, while including a short piece of the response body in the error message. This preserves useful API error details, such as "invalid filter."

**Data flow**: It receives an HTTP response. Successful responses return unchanged. Failed responses have their body text trimmed to a small limit and are raised as an HTTPStatusError containing the method, URL, status, and body snippet.

**Call relations**: RestConnector._send calls this after every raw request. If it raises, the retry loop decides whether the status is temporary or should fail immediately.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 169–173)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: Turns a response body into a dictionary, while treating empty responses as an empty dictionary. This avoids trying to parse JSON when there is no content.

**Data flow**: It receives an HTTP response. If the response is 204 No Content or has no body, it returns {}. Otherwise it parses the response JSON and returns it as a dictionary-shaped value.

**Call relations**: RestConnector._get and RestConnector._post use this after their raw requests succeed. It is the standard body reader for read endpoints that return object-shaped JSON.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 176–179)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Turns a response body into a list of record dictionaries, while treating empty responses as no records. It is used for APIs whose page response is a top-level array.

**Data flow**: It receives an HTTP response. Empty or no-content responses become an empty list. Otherwise it parses JSON and passes the result through list_or_empty so only dictionary records are returned.

**Call relations**: The link-header pager uses this when no custom record parser was supplied. That lets simple APIs return a plain array and still fit the shared pagination loop.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 182–188)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: Stops a pagination loop if it has gone through too many pages. This prevents a broken or hostile API response from making a sync run forever.

**Data flow**: It receives a label for the current operation and the number of pages already fetched. If the count is above the maximum allowed pages, it raises an error; otherwise it does nothing.

**Call relations**: Every built-in pager calls this on each loop. It acts like an emergency brake before the connector requests yet another page.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 191–197)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: Stops cursor-based pagination when the same next-page token appears twice. Repeating a token means the API is not advancing and would likely return the same page again and again.

**Data flow**: It receives a label, the new token or next-link, and a set of tokens already seen. If the token is already in the set, it raises an error. Otherwise it records the token for future checks.

**Call relations**: Cursor, link-header, and OData pagers call this whenever they discover a next-page marker. It catches infinite loops earlier and more clearly than the page-count limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 206–207)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the list of streams this connector can read. A stream is a named collection of records, such as users, tickets, or repositories.

**Data flow**: It reads the class's streams_list and returns a new list copy. Returning a copy keeps callers from accidentally changing the shared class-level list.

**Call relations**: The broader sync system asks connectors for their streams before reading. Provider subclasses fill in streams_list, and this method exposes it in the standard Connector shape.


##### `RestConnector._make_client`  (lines 209–226)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to one account's API. It applies the right authentication method, timeout settings, base URL, and JSON headers.

**Data flow**: It receives a base URL and a resolved credential. If the credential has a custom transport, it builds a client around that. Otherwise it adds a bearer token or provided headers; if no authentication exists, it raises an error. The result is an async HTTP client ready for requests.

**Call relations**: fetch_page calls this at the start of a read. All later GET and POST helpers use the client it creates, so authentication and timeout behavior stay consistent across connector implementations.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 228–231)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a GET request and returns the response body as a dictionary. It is the usual helper for read endpoints that return JSON objects.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It delegates the network call to _get_raw, then converts the successful response through _json_or_empty. The returned value is a dictionary, or {} for an empty response.

**Call relations**: Cursor, offset, and page-number pagers call this for each page. It inherits retry behavior through _get_raw and _send, so individual pagers do not need their own retry code.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 3 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages).


##### `RestConnector._get_raw`  (lines 233–237)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Performs a GET request and returns the full HTTP response. This is needed when pagination depends on headers or other response details, not just the JSON body.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It wraps client.get in the shared _send retry envelope and returns the successful response object.

**Call relations**: RestConnector._get uses this before parsing JSON. Link-header and OData pagination call it directly because they need headers or absolute next-link behavior.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 239–244)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a POST request for APIs that use POST to read data, not to write it. It keeps those read endpoints inside the same retry and JSON parsing path as GET requests.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It sends the POST through _send, then turns the response into a dictionary with _json_or_empty.

**Call relations**: Provider-specific connectors can call this from custom pagination when a read endpoint requires a request body. It shares _send with GET helpers so transient failures are treated the same way.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 246–252)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Performs a POST request and returns the full HTTP response. It is useful for read endpoints whose response is not a simple JSON object, such as a top-level list or streamed batches.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It sends the POST through the shared retry wrapper and returns the raw successful response.

**Call relations**: Provider-specific connectors can call this when they need to parse the response themselves. Like _post and _get_raw, it relies on _send for status checking and retries.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 254–275)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: Runs one HTTP request with shared retry rules. It protects connector reads from temporary network failures, rate limits, and short-lived server problems.

**Data flow**: It receives a no-argument function that starts an HTTP request. It tries the request, raises detailed errors for bad statuses, and returns the response on success. On retryable failures, it calculates a wait, sleeps, and tries again until it hits the attempt limit or total waiting budget.

**Call relations**: _get_raw, _post, and _post_raw all pass their actual HTTP calls into this function. _send then coordinates _raise_for_status, _is_retryable, _retry_wait, and asyncio.sleep to provide one uniform retry envelope.

*Call graph*: calls 3 internal fn (_is_retryable, _raise_for_status, _retry_wait); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 277–314)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[
```

**Purpose**: This is the main read path used by the sync driver to fetch pages from a REST connector. It opens the HTTP client, runs pagination, validates records, flattens them, and yields clean pages onward.

**Data flow**: It receives the stream to read, cursor and credential information, a base URL, user context, and optional backfill timing. It creates an authenticated client, asks paginate_source for pages, skips empty pages, validates that pages contain dictionaries, applies flatten to each record, and yields either plain record lists or StreamPage objects with delete and cursor information preserved.

**Call relations**: The core sync driver calls this to read data. It hands setup to _make_client, page production to paginate_source, record checking to _validate_page, and final record shaping to flatten.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate_source); 1 external calls (__init__).


##### `RestConnector.paginate_source`  (lines 316–329)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides a small seam between the general fetch_page method and connector-specific pagination. By default it ignores extra run context and calls paginate, but subclasses can override it when they need that context.

**Data flow**: It receives the client, stream, cursor, self-user identifier, and optional backfill time. The default implementation passes the client, stream, and cursor to paginate and returns that async iterator.

**Call relations**: fetch_page calls this after the client is ready. Most connectors use the default route into paginate, while special connectors can override this method to pass along extra information.

*Call graph*: calls 1 internal fn (paginate); called by 1 (fetch_page).


##### `RestConnector.paginate`  (lines 331–343)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Produces raw record pages for a stream. The default implementation supports streams that declare one of the built-in pagination strategies.

**Data flow**: It receives an HTTP client, a stream, and an optional cursor. If the stream has no usable pagination strategy, it raises NotImplementedError so subclasses know they must provide custom logic. Otherwise it yields pages from paginate_from_strategy.

**Call relations**: paginate_source calls this in the normal path. It then hands declared strategies to paginate_from_strategy, which chooses the concrete paging loop.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (paginate_source).


##### `RestConnector.paginate_from_strategy`  (lines 345–407)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs the pagination loop described by a stream's pagination settings. It is the dispatcher that maps a declared strategy to the right helper method.

**Data flow**: It receives a stream and client. It reads the stream's pagination specification, chooses a request path, checks that required settings are present, then yields pages from the cursor, link-header, or offset-limit helper. If records are nested in a next-link response, it builds a small parser for them.

**Call relations**: paginate calls this for standard streams. Depending on the strategy, it hands work to _get_cursor_pages, _get_link_header_pages, or _get_offset_pages, and may call _strategy_path when the stream did not give an explicit path.

*Call graph*: calls 4 internal fn (_get_cursor_pages, _get_link_header_pages, _get_offset_pages, _strategy_path); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 377–379)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Extracts records from a next-link response when the records are nested under a configured path. It is a small local parser made only for that pagination run.

**Data flow**: It receives an HTTP response. It parses JSON if there is a body, then uses records_at to pull the configured record list from that body. It returns a list of dictionary records.

**Call relations**: paginate_from_strategy creates this function for next-link pagination when record_path is set. _get_link_header_pages calls it for each response instead of assuming the body is a top-level list.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 409–437)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: Reads pages from APIs that put the next-page URL in the HTTP Link header. This is common in REST APIs that follow web linking conventions.

**Data flow**: It receives a client, starting path, optional parameters, page-size settings, and optional record parser. It requests the first page, yields any records, reads the next link from the response headers, checks for repeated links, and follows that link until no next link remains.

**Call relations**: paginate_from_strategy calls this for the next_link strategy. It uses _get_raw for retried requests, _response_list or a supplied parser for records, next_link for header parsing, and the bound checks to avoid endless loops.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 439–471)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: Reads pages from APIs that return a next cursor token in the response body. A cursor is a bookmark the server gives back so the client can ask for the following page.

**Data flow**: It receives a client, path, record path, cursor path, query parameter names, page size, and extra parameters. Each loop builds a query, includes the previous token if one exists, fetches JSON with _get, extracts records, yields them, reads the next token, and stops when no valid token is present.

**Call relations**: paginate_from_strategy calls this for the next_cursor strategy. It relies on records_at for record extraction, get_path for the next token, and page and cursor bounds to catch non-ending pagination.

*Call graph*: calls 5 internal fn (_get, _bound_cursor, _bound_pages, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_odata_pages`  (lines 473–499)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Microsoft Graph or OData-style pages. In this style, records usually live under "value" and the next page is named by "@odata.nextLink".

**Data flow**: It receives a client, path, and optional first-request parameters. It requests the current path, extracts dictionary records from the value field, yields them, then follows @odata.nextLink if present. Only the first request uses the original parameters because later links already contain their own query information.

**Call relations**: This helper is available for provider-specific connectors that need OData paging. It uses _get_raw for retried requests, list_or_empty for safe record extraction, and the bound helpers to prevent runaway loops.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 501–541)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: Reads pages from APIs that use offset and limit parameters. Offset means "start after this many records," and limit means "return this many records."

**Data flow**: It receives a client, path, record path, limit, query parameter names, extra parameters, and optional response paths for continuation details. It repeatedly sends the current offset and limit, extracts records, yields them, and advances the offset. It stops on no records, on a server-provided "no more" flag, or on a short page when no such flag is configured.

**Call relations**: paginate_from_strategy calls this for the offset_limit strategy. It uses _get for requests, records_at for records, get_path for optional continuation fields, _int_or_none for server-reported page size, and _bound_pages for safety.

*Call graph*: calls 5 internal fn (_get, _bound_pages, _int_or_none, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_page_number_pages`  (lines 543–572)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: Reads pages from APIs that ask for a page number, such as page=1, page=2, and so on. It stops when the returned page is smaller than the requested page size.

**Data flow**: It receives a client, path, record path, page size, optional parameters, parameter names, and the starting page number. Each loop sends the current page number, extracts records, yields them, stops if the page is short, and otherwise increments the page number.

**Call relations**: This helper is available for connector-specific pagination code, even though the standard strategy dispatcher shown here does not call it. It shares _get, records_at, and _bound_pages with the other pagers.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at).


##### `RestConnector._strategy_path`  (lines 574–580)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: Supplies a request path for a stream when its pagination settings did not include one. The base version raises an error so subclasses must be explicit.

**Data flow**: It receives a stream. The default implementation does not guess; it raises NotImplementedError explaining that no path was set and no override exists.

**Call relations**: paginate_from_strategy calls this only when a pagination specification lacks a path. Provider connectors with their own stream-to-path table can override it.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 582–585)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Converts one raw API record into the flat dictionary that the sync writer expects. The default does nothing because many APIs already return flat enough records.

**Data flow**: It receives a record dictionary and its stream. The base implementation returns the same record unchanged. Subclasses can override it to lift nested fields or reshape envelopes.

**Call relations**: fetch_page calls this for every record after validation and before yielding the page. This keeps record shaping separate from the pagination loops.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 587–598)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: Checks that a pagination method yielded a list of dictionary records. It catches connector mistakes early with a clear error message.

**Data flow**: It receives a page value and the stream being read. If the page is not a list, it raises a TypeError. If any item in the list is not a dictionary, it raises a TypeError naming the bad item type. Otherwise it changes nothing.

**Call relations**: fetch_page calls this before flattening and yielding records. It forms a safety gate between provider-specific pagination code and the shared sync writer.

*Call graph*: called by 1 (fetch_page).


### Sync orchestration
The backend bridge and sync driver transform provider records into stored pages, progress state, retries, deletions, and change feeds.

### `core/src/ufo/sources/backend.py`

`orchestration` · `source sync run`

A connector knows how to talk to one outside provider and yield records, often page by page. The rest of the system does not want to know those provider details. It wants one clear answer from each sync run: these pages were found, these items were deleted, and this is where to resume next time. This file provides that adapter through `ConnectorBackend`.

The main job is to fetch one configured stream for one account. It first asks an authentication proxy for a credential, so secrets do not have to be stored in the source row or passed to unsafe places. It then calls the connector, turns each provider record into a `Page`, and returns a `SyncResult`.

The file also protects the sync system from endless or oversized runs. Incremental streams are capped at a fixed number of records per run. If the connector gives a real checkpoint, the backend uses it. If not, it stores its own small “resume note” in the cursor: start from the same place again, skip records already consumed, and continue. This is like putting a bookmark plus a note saying “skip the first 5,000 lines next time.” Full snapshot streams, which are used to detect missing deleted records, are not capped because an incomplete snapshot could cause live records to be wrongly tombstoned.

If one record cannot be represented as a valid page, the backend logs a warning and drops just that record, instead of failing the whole run.

#### Function details

##### `binding_name`  (lines 99–109)

```
def binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: Creates a stable, human-safe name for a connector binding. The name is based on the provider, account, and optional tenant URL, so the same real-world connection gets the same object name wherever it appears.

**Data flow**: It takes a provider name, an account handle, and an optional base URL. It puts those values into a sorted JSON string, hashes that string, keeps a short prefix of the hash, and combines it with the provider name. The result is a compact name such as a provider label plus a short fingerprint.

**Call relations**: This helper stands apart from the sync run itself. Other registration or object-naming code can call it when it needs one consistent name for the same connector account, while the hashing work is delegated to standard JSON and SHA-256 hashing tools.

*Call graph*: 2 external calls (sha256, dumps).


##### `ConnectorBackend.fetch`  (lines 147–243)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one sync for one connector stream and returns the project’s standard `SyncResult`. It is the central bridge that gets credentials, calls the connector, converts records into pages, tracks deletions, and decides where the next run should resume.

**Data flow**: It receives a source row config, the previously saved cursor, and authentication context. It asks the auth proxy for a credential, finds the requested stream, chooses the base URL, decodes any special backfill cursor, then reads pages from the connector. Each record is either skipped because it was already counted in a previous capped run, converted into a `Page`, or dropped with a warning if invalid. It gathers deleted IDs reported by the provider, updates a watermark when the stream has a cursor field, and returns a `SyncResult` containing pages, deletes, a next cursor, and whether this run is a full snapshot.

**Call relations**: This is the file’s main flow. It calls `_stream` to find the stream definition, `_decode_cursor` when it needs to understand the backend’s own resume envelope, `_page` to turn each provider record into a core page, and `_max_str` to advance simple string watermarks. When a capped run must stop, it may create a `_BackfillEnvelope`, serialize it with JSON, and return early. It also warns through the observability system when a provider keeps returning records without making checkpoint progress.

*Call graph*: calls 4 internal fn (_decode_cursor, _page, _stream, _max_str); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._stream`  (lines 245–249)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream specification with the requested name inside the connector. A stream specification describes which records to fetch and how to interpret them.

**Data flow**: It receives a stream name. It looks through the connector’s declared streams until it finds one with that name. It returns that stream specification, or raises an error if the connector does not offer it.

**Call relations**: `ConnectorBackend.fetch` calls this near the start of a sync run, before it can fetch records. If the source row names a stream that the connector does not know about, this function stops the run immediately with a clear error.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 252–269)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Checks whether a saved cursor is one of this backend’s special backfill resume notes. If it is not, the cursor is treated as opaque connector state and left alone.

**Data flow**: It receives a cursor string or `None`. If there is no cursor, invalid JSON, or JSON that does not contain the reserved `ufo_backfill` key, it returns `None`. If the reserved key is present, it validates the stored origin, skip count, and watermark. A malformed reserved envelope raises an error because this backend is supposed to be the only writer of that format.

**Call relations**: `ConnectorBackend.fetch` calls this before fetching an incremental stream. The result tells `fetch` whether to pass the cursor straight to the connector, or whether to re-drive an earlier fetch and skip records that were already consumed in a previous capped run.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 271–312)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page | None
```

**Purpose**: Turns one provider record into the project’s `Page` shape, which is the recallable document the rest of the system stores and searches. If the record cannot become a valid page, it logs a warning and returns nothing so one bad record does not block the whole stream.

**Data flow**: It receives a stream specification and one raw record dictionary. It chooses a stable source reference for the record, asks the connector to render a title and body, extracts created and updated timestamps, and tries to build a `Page`. If page validation succeeds, the `Page` comes out. If validation fails, it logs the source reference and validation fault, then returns `None`.

**Call relations**: `ConnectorBackend.fetch` calls this for every record that should be landed after any skip-count resume logic. `_page` relies on `_record_ref` for the page identity and `_record_timestamp` for normalized timestamps, then hands the finished page back to `fetch` to include in the final `SyncResult`.

*Call graph*: calls 2 internal fn (_record_ref, _record_timestamp); called by 1 (fetch); 3 external calls (__init__, warn, validation_fault).


##### `_record_timestamp`  (lines 315–346)

```
def _record_timestamp(record: dict[str, Any], field: str | None, *, connector: str, stream: str) -> str | None
```

**Purpose**: Extracts and normalizes a timestamp from a provider record. It makes provider-specific date values safe for the standard page model.

**Data flow**: It receives a record, the field name or nested path to read, and labels for the connector and stream. If no field is configured or the value is missing, it returns `None`. If the value is a string or integer timestamp, it tries to normalize it into the expected page timestamp format. If the value is malformed or of an unsupported type, it logs a warning and returns `None`.

**Call relations**: `ConnectorBackend._page` calls this for both created-at and updated-at fields while building a `Page`. It uses `get_path` when the timestamp is nested inside the record, `normalize_page_timestamp` to standardize accepted values, and the warning system to report bad provider data without failing the whole sync.

*Call graph*: called by 1 (_page); 3 external calls (warn, get_path, normalize_page_timestamp).


##### `_record_ref`  (lines 349–353)

```
def _record_ref(stream: StreamSpec, record: dict[str, Any]) -> str
```

**Purpose**: Creates a stable reference for one provider record. This reference lets future updates, re-fetches, and deletion notices point to the same page.

**Data flow**: It receives a stream specification and a raw record. It first looks for the stream’s declared primary key. If that value is a string or integer, it returns it as text. If no suitable primary key is present, it hashes the whole record in sorted JSON form and returns that hash as a fallback identity.

**Call relations**: `ConnectorBackend._page` calls this before creating the `Page` source reference. The resulting reference is combined with the stream name so the sync system can consistently identify records from the same stream.

*Call graph*: called by 1 (_page); 2 external calls (sha256, dumps).


##### `_max_str`  (lines 356–361)

```
def _max_str(current: str | None, value: Any) -> str | None
```

**Purpose**: Keeps the greatest string value seen so far, used as a simple watermark for incremental syncs. A watermark is a remembered “latest point reached” value.

**Data flow**: It receives the current stored string or `None`, plus a new value from a record. If the new value is not a string, it leaves the current value unchanged. If there is no current value, or the new string sorts after the current one, it returns the new string. Otherwise it returns the current string.

**Call relations**: `ConnectorBackend.fetch` calls this while reading records from a stream that declares a cursor field. The updated watermark helps `fetch` decide what cursor to return when the connector does not provide a stronger page-level checkpoint.

*Call graph*: called by 1 (fetch).


### `core/src/ufo/sources/sync.py`

`orchestration` · `startup registration, periodic source sync, and downstream page-feed polling`

This file is like the loading dock for documents. A source backend knows how to read one kind of place, such as a local folder or an external service. The sync driver does the common work around that: it finds sources that are due, temporarily claims one so another worker does not process the same source at the same time, asks the right backend to fetch documents, writes document bodies to the blob store, and records page metadata in the database.

The file separates “what to fetch” from “how to store it.” Backends return `Page` objects and a `SyncResult`; the driver decides which pages are new, unchanged, updated, or deleted. If a backend returns a full snapshot, missing pages are tombstoned, meaning they are marked as deleted without removing the row. If a backend returns only a delta, only explicitly named deletes are tombstoned.

It also protects the rest of the system from bad or flaky providers. A skipped stream is rescheduled normally, a real failure backs off so it does not hammer the provider, and an expired cursor is cleared so the next run can start fresh. Finally, `CorePageFeed` lets downstream indexers replay page changes in a stable order using a cursor, so indexing can resume safely after a restart.

#### Function details

##### `SourceRowConfig.requested_fields`  (lines 73–76)

```
def requested_fields(cls) -> frozenset[str]
```

**Purpose**: Returns the source configuration fields that a caller must repeat exactly when re-registering the same source. This helps decide which settings identify the dataset and which settings are only resolved or adjusted by the system.

**Data flow**: It reads the class-level sets of non-identity fields and resolved fields. It subtracts the resolved fields from the non-identity fields, then returns the remaining field names as a frozen set.

**Call relations**: This is a helper on the base configuration model for source rows. Backends that use `SourceRowConfig` can rely on it when deciding whether a new registration matches an existing source row.


##### `normalize_page_timestamp`  (lines 79–99)

```
def normalize_page_timestamp(value: str) -> str
```

**Purpose**: Turns a page timestamp into one standard UTC string. This prevents different source providers from storing dates in incompatible formats.

**Data flow**: It receives a timestamp string. If it looks like a number, it treats it as seconds or milliseconds since the Unix epoch; otherwise it parses it as an ISO date/time string, requiring a timezone unless it is a plain date. It returns the same moment as a UTC ISO string with microseconds, or raises an error if the value is unclear or invalid.

**Call relations**: `Page.normalize_timestamp` calls this when validating `created_at` and `updated_at`. It relies on Python date parsing helpers to convert raw strings into real date/time values.

*Call graph*: called by 1 (normalize_timestamp); 2 external calls (fromisoformat, fromtimestamp).


##### `Page.digest`  (lines 115–116)

```
def digest(self) -> str
```

**Purpose**: Computes a stable fingerprint for a page body. The sync driver uses this to avoid rewriting pages whose text has not changed.

**Data flow**: It reads the page body text, encodes it as bytes, runs SHA-256 hashing over it, and returns a string beginning with `sha256:` followed by the hex digest.

**Call relations**: `SyncDriver._commit` reads this property while comparing fetched pages with prior database rows. It calls the standard `hashlib.sha256` function to create the fingerprint.

*Call graph*: 1 external calls (sha256).


##### `Page.normalize_timestamp`  (lines 120–123)

```
def normalize_timestamp(cls, value: str | None) -> str | None
```

**Purpose**: Validates and standardizes optional page creation and update times. It keeps provider-specific timestamp formats from leaking into the stored page rows.

**Data flow**: It receives either a timestamp string or `None`. `None` passes through unchanged; a string is sent to `normalize_page_timestamp`, and the normalized UTC string comes back.

**Call relations**: Pydantic calls this validator when a `Page` is created. It delegates the actual parsing rules to `normalize_page_timestamp`.

*Call graph*: calls 1 internal fn (normalize_page_timestamp).


##### `StreamSkipped.__init__`  (lines 155–157)

```
def __init__(self, reason: str) -> None
```

**Purpose**: Creates an exception that means a stream could not be read for an expected, non-fatal reason, such as a missing permission or plan limit. This tells the driver not to treat the run as a provider failure.

**Data flow**: It receives a human-readable reason, stores that reason on the exception, and initializes the base runtime error with the same text.

**Call relations**: Many connector backends raise this during pagination when the provider refuses a stream in a controlled way. `SyncDriver.run` catches it, logs a skip, and calls `_skip` instead of failure handling.

*Call graph*: called by 48 (paginate, paginate, paginate, paginate, _org_stream, paginate, paginate, paginate, paginate, paginate (+15 more)).


##### `validation_fault`  (lines 160–166)

```
def validation_fault(error: ValidationError) -> str
```

**Purpose**: Turns a Pydantic validation error into a safe, compact explanation. It reports which fields failed and why, without including rejected values that might contain provider data or secrets.

**Data flow**: It receives a validation error, reads its structured list of field errors, formats each as `field.path: error_type`, and joins them into one semicolon-separated string.

**Call relations**: `SyncDriver._report_failed` calls this when a backend configuration or provider payload fails validation. It uses the validation error’s own structured `errors` output rather than the full exception text.

*Call graph*: called by 1 (_report_failed); 1 external calls (errors).


##### `StreamFault.__init__`  (lines 176–178)

```
def __init__(self, reason: str) -> None
```

**Purpose**: Creates an exception for provider data that has an unexpected shape. It lets a connector explain what broke without logging the raw provider payload.

**Data flow**: It receives a safe reason string, stores it on the exception, and initializes the base runtime error with that same reason.

**Call relations**: A connector such as the Google Sheets source can raise this when it cannot interpret provider data. `SyncDriver._report_failed` recognizes it and includes its safe reason in failure reporting.

*Call graph*: called by 1 (_sheet_value_records).


##### `SourceBackend.config_model`  (lines 218–218)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: Declares which typed configuration model a source backend expects. This keeps each backend’s settings structured rather than treating them as an untyped dictionary.

**Data flow**: A backend implementation exposes a Pydantic model class. The sync driver reads that model class and uses it to validate the source row’s stored JSON configuration before fetching.

**Call relations**: `SyncDriver._fetch` uses this protocol property before calling the backend. Every source backend must provide it so the driver can safely understand that backend’s configuration.


##### `SourceBackend.fetch`  (lines 220–220)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Defines the main contract a source backend must fulfill: fetch documents for one source and return what changed or what exists now. This is the seam where external connectors plug into the core sync system.

**Data flow**: It receives typed source config, the previous cursor if any, and source authentication context. A backend implementation contacts or reads its source, then returns a `SyncResult` containing pages, a next cursor, delete markers, and whether the result is a full snapshot.

**Call relations**: `SyncDriver._fetch` calls this after validating config and preparing auth. Implementations may raise `CursorExpired`, `StreamSkipped`, or other errors, which `SyncDriver.run` handles.


##### `FolderSource.fetch`  (lines 234–245)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Reads a local folder source and turns each file into a page. It is the built-in backend for syncing files from disk.

**Data flow**: It receives a folder config, ignores the cursor and auth, reads the folder in a worker thread, creates a `Page` for each file using the relative path as the page key and title, and returns a full-snapshot `SyncResult` with no cursor.

**Call relations**: The sync driver calls this through the `SourceBackend.fetch` contract when a source uses the folder backend. It delegates the actual disk scan to `FolderSource._read` and wraps the results as pages.

*Call graph*: 4 external calls (__init__, __init__, to_thread, Path).


##### `FolderSource._read`  (lines 248–255)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: Scans a directory and reads every file as UTF-8 text. It provides the raw file list for the folder backend.

**Data flow**: It receives a root path. If the path is not a directory, it raises `FileNotFoundError`; otherwise it walks all files below the root, sorts them, reads their bytes, decodes them as UTF-8, and returns pairs of relative path and text.

**Call relations**: `FolderSource.fetch` calls this inside `asyncio.to_thread` so disk work does not block the async event loop. Its output becomes the pages returned by the folder source.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 258–278)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object], *, connection_id: UUID | None=None, non_identity_keys: frozenset[str]=frozenset()) -> UUID
```

**Purpose**: Creates a deterministic database ID for a source row. This means the same configured source gets the same ID after a restart instead of creating duplicates.

**Data flow**: It receives the workspace ID, backend name, config values, an optional connection ID, and config keys that should not identify the source. It removes non-identity keys, serializes the remaining config in sorted order, combines it with the workspace and backend, and returns a UUID generated from that stable text.

**Call relations**: `register_sources` calls this while creating configured sources at boot. It uses JSON serialization and UUID version 5, which creates the same UUID from the same namespace and text.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 281–284)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: Creates a deterministic page ID for one document within one source. This lets updates and deletes find the same database row every time.

**Data flow**: It receives a source ID and the source’s own stable page reference. It combines them into a stable text key and returns a UUID generated from that key.

**Call relations**: `SyncDriver._commit` calls this for every fetched page and every explicit delete reference. It ensures inserts, updates, and tombstones all point to the same page row.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `register_sources`  (lines 287–352)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: Creates database rows for sources listed in static configuration. It runs at boot so configured sources are present before the periodic sync job starts polling.

**Data flow**: It receives configured source entries. It opens a workspace transaction, finds the workspace and main agent, computes a stable source ID for each entry, skips sources that were already removed, inserts missing source rows, and grants the main agent access to each new source.

**Call relations**: This function calls `source_row_id` to avoid duplicate rows across restarts and uses database insert/select operations inside `workspace_tx`. It is separate from the sync driver’s polling loop.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `_rescheduled`  (lines 370–381)

```
def _rescheduled(claimed: ClaimedSource, when: datetime) -> sa.Case[datetime]
```

**Purpose**: Decides what `next_sync_at` should become when a sync finishes. It preserves a resync request that arrived while the current sync was already running.

**Data flow**: It receives the claimed source and a proposed future time. It builds a database expression: if the row’s current `next_sync_at` is no later than when the claim began, use the proposed time; otherwise keep the newer database value.

**Call relations**: `SyncDriver._write`, `_release`, and `_skip` use this when freeing a claim. It prevents a finishing sync from accidentally pushing away a fresh request to sync immediately.

*Call graph*: called by 3 (_release, _skip, _write); 1 external calls (case).


##### `_stream_tags`  (lines 384–389)

```
def _stream_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: Builds small, consistent labels for logs and metrics that identify which provider stream a sync result belongs to. These labels help operators see which source type and stream are failing or succeeding.

**Data flow**: It receives a claimed source, reads the backend name, asks `_config_value` for the `stream` config value, and returns a dictionary with `provider` and `stream` strings.

**Call relations**: `SyncDriver.run`, `_report_ok`, and `_report_failed` use this for telemetry. It delegates safe config reading to `_config_value`.

*Call graph*: calls 1 internal fn (_config_value); called by 3 (_report_failed, _report_ok, run).


##### `_config_value`  (lines 392–394)

```
def _config_value(source: ClaimedSource, key: str) -> str
```

**Purpose**: Safely reads a string value from a source config dictionary. It avoids putting non-string data into log and metric fields that expect text.

**Data flow**: It receives a claimed source and a config key. It looks up the value and returns it only if it is a string; otherwise it returns an empty string.

**Call relations**: `_stream_tags`, `SyncDriver._report_ok`, and `SyncDriver._report_failed` call this when building telemetry fields such as stream or account.

*Call graph*: called by 3 (_report_failed, _report_ok, _stream_tags).


##### `SyncDriver.candidate_workspaces`  (lines 428–448)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds workspaces that have at least one source ready to sync. This lets a dispatcher skip opening work for workspaces that have nothing due.

**Data flow**: It gets the current time, opens an owner-level database transaction, selects distinct workspace IDs from source rows that are due, not removed, and not currently claimed unless their claim expired, then returns those IDs as a tuple.

**Call relations**: This method is used before running per-workspace sync work. It reads through `owner_tx`, which is a broader database context than normal workspace-scoped transactions.

*Call graph*: 4 external calls (now, or_, select, owner_tx).


##### `SyncDriver.run`  (lines 450–469)

```
async def run(self) -> None
```

**Purpose**: Runs one polling pass of the source sync job for the current workspace. It claims due sources, fetches them, commits successful results, and deals with skips or failures.

**Data flow**: It creates a unique claim token, asks `_claim_due` for sources it owns for this pass, then processes each source. Success goes through `_fetch` and `_commit`; skipped streams are logged and passed to `_skip`; errors are classified, logged, assigned a backoff, and released through `_release`.

**Call relations**: This is the main orchestration method for the driver. It calls most of the driver’s private helpers and uses `_stream_tags` for skipped-stream logging.

*Call graph*: calls 8 internal fn (_claim_due, _commit, _error_backoff, _fetch, _release, _report_failed, _skip, _stream_tags); 4 external calls (suppress, now, log, uuid4).


##### `SyncDriver._claim_due`  (lines 471–521)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: Locks a batch of due sources for this driver run. The claim stops two workers from syncing the same source at the same time.

**Data flow**: It receives a claim token, finds due source rows that are not removed and not actively claimed, optionally uses database row locking for PostgreSQL, writes the claim token and expiry time to those rows, and returns them as `ClaimedSource` objects.

**Call relations**: `SyncDriver.run` calls this at the start of a pass. The returned claim token is later checked by `_write`, `_release`, and `_skip` before they update the source row.

*Call graph*: called by 1 (run); 7 external calls (__init__, now, timedelta, or_, select, update, workspace_tx).


##### `SyncDriver._fetch`  (lines 523–542)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: Prepares everything a backend needs and asks it to fetch pages. It is the bridge between stored source rows and backend-specific fetch code.

**Data flow**: It receives a claimed source, finds the matching backend, validates the stored config using that backend’s config model, optionally resolves the system’s own external user ID, builds `SourceAuth` with workspace and credential access, and returns the backend’s `SyncResult`.

**Call relations**: `SyncDriver.run` calls this after claiming a source. It hands off to the backend’s `fetch` method, and any exception it raises is handled by `run`.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 544–585)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: Compares fetched pages with stored pages and prepares the minimal set of database and blob changes. It avoids rewriting unchanged content while still updating changed metadata.

**Data flow**: It receives a claimed source and fetch result, loads prior page state with `_prior_pages`, computes page IDs, separates content changes from metadata-only changes, writes new page bodies to the blob store, converts explicit delete references into page IDs, then calls `_write` to persist rows and `_report_ok` to log the outcome.

**Call relations**: `SyncDriver.run` calls this after `_fetch` succeeds. It uses `page_id_for`, `Page.digest`, `ChangedPage`, and `PageBrowse` to prepare the work that `_write` commits.

*Call graph*: calls 4 internal fn (_prior_pages, _report_ok, _write, page_id_for); called by 1 (run); 2 external calls (__init__, __init__).


##### `SyncDriver._prior_pages`  (lines 587–619)

```
async def _prior_pages(self, source_id: UUID) -> dict[UUID, tuple[str, bool, PageBrowse]]
```

**Purpose**: Loads the current stored state for all pages belonging to one source. This gives `_commit` something to compare newly fetched pages against.

**Data flow**: It receives a source ID, opens a workspace transaction, selects page IDs, digests, tombstone flags, and browsing metadata, then returns a dictionary keyed by page ID.

**Call relations**: `SyncDriver._commit` calls this before deciding which fetched pages are unchanged, changed, metadata-only, or previously tombstoned. It creates `PageBrowse` objects for the stored metadata.

*Call graph*: called by 1 (_commit); 3 external calls (__init__, select, workspace_tx).


##### `SyncDriver._write`  (lines 621–743)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[ChangedPage], metadata: list[PageBrowse], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> int
```

**Purpose**: Persists one sync result to the database and releases the successful claim. This is where page rows are inserted, updated, revived, or tombstoned.

**Data flow**: It receives the claimed source, next cursor, changed pages, metadata-only pages, fetched page IDs, explicit deleted page IDs, and whether the result is a snapshot. Inside one workspace transaction it verifies the claim, writes changed page rows, updates metadata-only rows, tombstones explicit deletes, tombstones missing pages for snapshots, updates live page subject if needed, clears the claim, resets errors, stores the cursor, schedules the next sync, and returns the number of pages tombstoned.

**Call relations**: `SyncDriver._commit` calls this after preparing page changes. It uses `_rescheduled` so a new sync request made during the current run is not lost.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (_commit); 6 external calls (now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._report_ok`  (lines 745–757)

```
def _report_ok(self, source: ClaimedSource, fetched: int, written: int, tombstoned: int) -> None
```

**Purpose**: Writes a success log for one source sync. The log records how many pages were fetched, written, and tombstoned.

**Data flow**: It receives the source and counts, builds stream and account fields, and sends a `source_sync.ok` log event. If logging itself fails, the error is suppressed so it does not break the sync.

**Call relations**: `SyncDriver._commit` calls this after `_write` completes. It uses `_stream_tags` and `_config_value` to build safe telemetry fields.

*Call graph*: calls 2 internal fn (_config_value, _stream_tags); called by 1 (_commit); 2 external calls (suppress, log).


##### `SyncDriver._error_backoff`  (lines 759–767)

```
def _error_backoff(self, source: ClaimedSource, now: datetime) -> tuple[int, datetime]
```

**Purpose**: Calculates when a failing source should be tried again. Repeated failures wait longer, up to a fixed cap, so the system does not repeatedly hammer a broken provider.

**Data flow**: It receives the source and the current time, increments the consecutive error count, doubles the base interval according to that count, caps the wait time, and returns the new error count plus the next retry time.

**Call relations**: `SyncDriver.run` calls this when `_fetch` or `_commit` raises an error. The result is passed to `_report_failed` and `_release`.

*Call graph*: called by 1 (run); 1 external calls (timedelta).


##### `SyncDriver._report_failed`  (lines 769–816)

```
def _report_failed(self, source: ClaimedSource, error: Exception, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: Reports a failed source sync in logs and metrics without leaking sensitive provider data. It captures enough information to diagnose the stream, error type, retry time, and whether the cursor was reset.

**Data flow**: It receives the source, exception, cursor-reset flag, error count, and next retry time. It classifies the exception, builds a safe provider fault message for known cases, logs a `source_sync.failed` event, and emits a failure metric; logging and metric errors are each suppressed.

**Call relations**: `SyncDriver.run` calls this after calculating backoff. It uses `_stream_tags`, `_config_value`, and `validation_fault` to prepare safe telemetry.

*Call graph*: calls 3 internal fn (_config_value, _stream_tags, validation_fault); called by 1 (run); 4 external calls (suppress, isoformat, emit_metric, log_error).


##### `SyncDriver._release`  (lines 818–844)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: Frees a source claim after a real failure and schedules the next retry. It also decides whether to keep or clear the stored cursor.

**Data flow**: It receives the source, whether the cursor should be reset, the new error count, and the retry time. It updates the source row to clear the claim, set the cursor to `None` for expired cursors or keep the old cursor otherwise, record the error count, and set `next_sync_at` through `_rescheduled`.

**Call relations**: `SyncDriver.run` calls this on the failure path after `_report_failed`. It uses `_rescheduled` so a manual resync requested during the failed run can still happen promptly.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (run); 2 external calls (update, workspace_tx).


##### `SyncDriver._skip`  (lines 846–869)

```
async def _skip(self, source: ClaimedSource) -> None
```

**Purpose**: Frees a source claim after a backend intentionally skipped a stream. This is not treated as a failure, so existing pages remain untouched and retry timing stays normal.

**Data flow**: It receives the claimed source, gets the current time, updates the source row to clear the claim, reset the error count, and schedule the next normal sync interval while leaving the cursor unchanged.

**Call relations**: `SyncDriver.run` calls this after catching `StreamSkipped`. It uses `_rescheduled` to preserve any newer sync request made while the skipped run was in progress.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (run); 4 external calls (now, timedelta, update, workspace_tx).


##### `PageFeed.pages_changed_since`  (lines 909–909)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Defines the interface an indexer uses to read page changes from core. It promises batches of page changes after a cursor, in a stable order.

**Data flow**: An implementation receives a cursor and limit. It returns a `PageBatch` containing page changes and a next cursor for resuming later.

**Call relations**: `CorePageFeed.pages_changed_since` is the core implementation of this protocol. Extensions read through this interface instead of directly knowing the database and blob layout.


##### `page_cursor`  (lines 912–921)

```
def page_cursor(cursor: object) -> tuple[int, UUID]
```

**Purpose**: Parses a page-feed cursor into its two ordering parts: revision number and page ID. This lets the feed resume exactly after the last change it returned.

**Data flow**: It receives an object, requires it to be a string shaped like `revision|uuid`, checks that the revision is decimal, parses the UUID, and returns both pieces. Invalid input raises `ValueError`.

**Call relations**: `CorePageFeed.pages_changed_since` calls this when a caller supplies a cursor. The parsed values become the database filter for changes after that cursor.

*Call graph*: called by 1 (pages_changed_since); 1 external calls (UUID).


##### `CorePageFeed.pages_changed_since`  (lines 933–991)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Reads changed pages from the database and includes their bodies for downstream indexers. It is the core replay feed that turns stored sync results into indexable change batches.

**Data flow**: It receives an optional cursor and requested limit, builds a query ordered by revision and page ID, caps the batch size, filters after the cursor if present, and reads matching page rows. For live pages it fetches the body from the blob store; for tombstoned pages it uses an empty body. It creates `PageChange` objects, sets the next cursor from the last row, and returns a `PageBatch`.

**Call relations**: Indexing code calls this through the `PageFeed` interface. It uses `page_cursor` for resume positions, database reads through `workspace_tx`, and blob reads for non-deleted page bodies.

*Call graph*: calls 1 internal fn (page_cursor); 7 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx).


### Source management tools
Extension tools expose lifecycle operations for synced sources and notify conversations when shared source content changes.

### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object requests and page-change hooks`

A “source” here means a saved connection to an outside service, such as a provider account plus the streams of content to sync. This file turns those saved connections into normal objects that agents and members can list, inspect, apply, resync, share, or delete. Without it, synced providers would exist as low-level database rows, but users would not have a safe, understandable way to register them or control who can read their synced pages.

The file also defines “source triggers.” A trigger is like a standing alarm: when a shared source receives new or changed pages, the trigger wakes a conversation so an agent can react. Private sources cannot be watched this way, because their pages are only meant for the registering member.

The main flow is careful about identity and safety. Source names are not arbitrary; they are derived from the provider, account, and tenant URL, so the same binding cannot be accidentally registered twice under different names. The code validates provider names, stream names, account access, tenant URLs, and backfill windows before writing anything. It allows widening a sync window, but refuses narrowing it in place because that could leave old synced pages stranded. On page changes, it filters to shared, readable changes, writes a compact change log when possible, and sends an alert message with enough detail for the agent to investigate.

#### Function details

##### `_Binding.name`  (lines 190–191)

```
def name(self) -> str
```

**Purpose**: Gives a source binding its official object name. The name is derived from the provider, account, and tenant URL so identity is stable and not chosen by hand.

**Data flow**: It reads the binding’s provider, account, and base URL, passes them to the shared naming helper, and returns the resulting name string.

**Call relations**: Other code uses this property whenever it needs to compare, list, delete, or alert on a binding. It delegates the exact name format to the common source naming helper.

*Call graph*: 1 external calls (binding_name).


##### `_Binding.created_at`  (lines 194–195)

```
def created_at(self) -> datetime
```

**Purpose**: Reports when this whole binding was first created. Since a binding can contain several stream rows, it uses the oldest stream creation time.

**Data flow**: It reads all stream creation timestamps inside the binding and returns the earliest one.

**Call relations**: The object-detail view uses this to present one creation time for a binding made from multiple stored streams.


##### `_Binding.updated_at`  (lines 198–199)

```
def updated_at(self) -> datetime
```

**Purpose**: Reports the most recent update time for the binding. This tells readers when any stream in the binding last changed.

**Data flow**: It reads all stream update timestamps and returns the latest one.

**Call relations**: The object-detail view uses this as the binding’s overall updated time.


##### `_Binding.links`  (lines 201–214)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: Builds the object links that explain what credential or connection the source uses. These links help users understand how the source authenticates without exposing secrets.

**Data flow**: It reads whether the binding uses a direct workspace credential, a connected account, or is shared. It returns zero or one link pointing to the relevant credential slot or connection object.

**Call relations**: Source object details call this when showing a binding. It creates links to credential or connection objects using the project’s common object-reference types.

*Call graph*: 4 external calls (__init__, __init__, credential_object_name, account_object_name).


##### `_Binding.spec`  (lines 216–224)

```
def spec(self) -> SourceSpec
```

**Purpose**: Turns the internal binding record back into the public source specification. This is what users see when they inspect the object.

**Data flow**: It reads provider, stream names, account, base URL, shared/private status, and backfill setting from the binding, then returns a SourceSpec model.

**Call relations**: Source object lookup uses this when returning details, and apply logic uses it to compare an existing binding with a requested change.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Binding.summary`  (lines 226–228)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable label for the binding. It names the provider, account, and selected streams.

**Data flow**: It joins the stream names, combines them with provider and account, trims the text to the maximum summary length, and returns the string.

**Call relations**: Listing code uses this for object summaries, and alert messages use it to explain which source changed.

*Call graph*: called by 1 (_alert_message).


##### `_require_ext`  (lines 237–240)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure the extension runtime context is present before source code tries to use it. This prevents later failures that would be harder to understand.

**Data flow**: It receives an optional extension context. If it is missing, it raises an error; otherwise it returns the context unchanged.

**Call relations**: Most operations that read or write source rows call this first. It acts like a guard at the door before touching extension services.

*Call graph*: called by 9 (_apply_owned, _delete_owned, _member_rows, _resolved_account, _resync, _widen_window, _member_rows, _binding_named, _require_triggers).


##### `_require_connectors`  (lines 243–246)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Makes sure the current tool call has a connector registry available. The registry is the catalog used to discover accounts and authentication paths.

**Data flow**: It reads the connector registry from the tool context. If none is present, it raises an error; otherwise it returns the registry.

**Call relations**: Account resolution calls this before deciding whether a provider should use a connected account or a direct workspace credential.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 249–282)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: Reconstructs user-facing source bindings from the lower-level stored stream rows. A single binding may be stored as one row per stream, so this function groups them back together.

**Data flow**: It reads all source records from the extension context, ignores providers not known to this extension, parses each row’s config, groups rows by provider/account/base URL, and returns sorted _Binding objects.

**Call relations**: Listing, lookup, trigger listing, and page-change handling all rely on this as their shared view of registered sources.

*Call graph*: calls 1 internal fn (sources); called by 4 (_member_rows, _member_rows, _binding_named, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_binding_named`  (lines 285–295)

```
async def _binding_named(ext: ExtensionContext | None, name: str) -> _Binding | None
```

**Purpose**: Finds one registered binding by its derived object name. Both source objects and source triggers use this because triggers refer to the source they watch by name.

**Data flow**: It requires an extension context, rebuilds all bindings, compares each binding’s name with the requested name, and returns the matching binding or None.

**Call relations**: Source get, status, apply, resync, delete, trigger creation, and page-change cleanup call this when they need to confirm that a named source exists.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 6 (_apply_owned, _delete_owned, _member_object, _resync, _status, _apply_owned).


##### `_require_triggers`  (lines 298–299)

```
def _require_triggers(ext: ExtensionContext | None) -> SourceTriggerStore
```

**Purpose**: Creates access to the trigger store after confirming the extension context exists. The trigger store is where standing source wake-ups are saved.

**Data flow**: It receives an optional extension context, validates it, wraps it in a SourceTriggerStore, and returns that store.

**Call relations**: Trigger list, create, find, delete, source deletion, and page-change delivery all use this helper before reading or changing trigger records.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_delete_owned, _apply_owned, _delete_owned, _find, _member_rows, on_page_change); 1 external calls (__init__).


##### `_effective_days`  (lines 302–310)

```
def _effective_days(request: int | Literal['all'] | None, declared: int | None) -> int | None
```

**Purpose**: Decides the actual number of days a stream should sync back. It combines the user’s request with the stream’s own default window.

**Data flow**: It receives the requested backfill setting and the stream’s declared window. A numeric request wins, a missing request uses the declared default, and “all” or no declared window becomes no cutoff.

**Call relations**: Source registration and window-widening call this so they interpret backfill settings the same way.

*Call graph*: called by 2 (_apply_owned, _widen_window).


##### `_binding_identity`  (lines 313–323)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool, int | Literal['all'] | None]
```

**Purpose**: Creates a comparable fingerprint of a source specification. It lets the code tell whether an apply request is truly the same binding or is trying to change its identity.

**Data flow**: It reads provider, sorted streams, account ID, base URL, shared flag, and backfill setting from a SourceSpec, then returns them as a tuple.

**Call relations**: The apply and resync paths use this to allow no-op reapplies and to reject resync requests that also try to edit the source.

*Call graph*: called by 2 (_resync, apply).


##### `SourceObjects.apply`  (lines 351–370)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Receives an apply request for a source object and chooses the right path. It treats resync as an action, identical reapplies as no-ops, and other changes as normal owned-object mutations.

**Data flow**: It takes the tool context, object name, requested spec, old visible spec, and expected generation. It either schedules resync, returns without change, or passes the request to the base object logic.

**Call relations**: This is the public apply entry for source objects. It calls _resync for immediate sync requests and uses _binding_identity to decide when nothing changed.

*Call graph*: calls 2 internal fn (_resync, _binding_identity).


##### `SourceObjects._resync`  (lines 372–396)

```
async def _resync(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None) -> None
```

**Purpose**: Schedules an immediate sync for an existing binding without changing its configuration. It protects credentials by allowing only the registrar or an admin to do this.

**Data flow**: It checks that the submitted spec exactly matches the old one except for the resync action, verifies visibility and ownership/admin rights, finds the binding, and asks the extension context to schedule all its stream IDs.

**Call relations**: SourceObjects.apply calls this when spec.resync is true. It hands the actual scheduling to the extension context after doing permission and identity checks.

*Call graph*: calls 4 internal fn (speaker_is_admin, _binding_identity, _binding_named, _require_ext); called by 1 (apply); 3 external calls (__init__, __init__, __init__).


##### `SourceObjects._member_rows`  (lines 398–411)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the rows shown when a member lists source objects. Each row represents one grouped binding, not one stream row.

**Data flow**: It reconstructs bindings, turns each into a name, summary, and owner record, and returns the list of owned rows.

**Call relations**: The inherited member-readable object system calls this when listing sources for a member or admin.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `SourceObjects._member_object`  (lines 413–429)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceSpec] | None
```

**Purpose**: Builds the detailed view of one source object. It includes the public spec, timestamps, and links to its credential or connection where appropriate.

**Data flow**: It looks up the binding by name. If found, it converts the binding to a spec and detail record; if not, it returns None.

**Call relations**: The object system calls this after ownership and visibility are decided by the base class.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (__init__).


##### `SourceObjects._status`  (lines 431–453)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status information for a source, such as next sync time and error counts. This helps people see whether each stream is healthy and how far back it syncs.

**Data flow**: It finds the binding, builds a dictionary with shared/private status and per-stream sync fields, and includes the owner member ID only for private sources.

**Call relations**: The object status path calls this when a caller asks for operational details about a source.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (subject_shared).


##### `SourceObjects._apply_owned`  (lines 455–563)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Performs the real work of registering or changing a source after ownership rules allow it. It validates provider, streams, URL, account access, sharing changes, and backfill rules before writing rows.

**Data flow**: It takes the requested spec, checks it against known connectors, resolves the account, verifies the derived name, and either updates an existing binding’s sharing/window or registers one source row per stream.

**Call relations**: The base object apply flow calls this for permitted source changes. It delegates account choice to _resolved_account, URL checking to _validated_base_url, and widening to _widen_window.

*Call graph*: calls 6 internal fn (_resolved_account, _widen_window, _binding_named, _effective_days, _require_ext, _validated_base_url); 8 external calls (__init__, __init__, __init__, now, timedelta, binding_name, member_subject, get).


##### `SourceObjects._widen_window`  (lines 565–634)

```
async def _widen_window(self, ctx: ToolContext, binding: _Binding, *, declared: dict[str, int | None], windowed: frozenset[str], account: str, base_url: str | None, request: int | Literal['all'] | Non
```

**Purpose**: Moves an existing source’s backfill cutoff earlier so it syncs more history. It refuses changes that would make the window smaller, because old pages could otherwise remain without being cleaned up.

**Data flow**: It compares the old and requested backfill windows for each stream, computes new cutoff dates pinned to the original registration anchor, and asks the extension context to rewrite configs and refetch streams whose cutoff changed.

**Call relations**: SourceObjects._apply_owned calls this when only the backfill window is changing. It uses _effective_days so old and new windows are interpreted consistently.

*Call graph*: calls 2 internal fn (_effective_days, _require_ext); called by 1 (_apply_owned); 3 external calls (__init__, __init__, timedelta).


##### `SourceObjects._delete_owned`  (lines 636–643)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a source binding and any triggers watching it. Removing the source rows also lets the rest of the system tombstone synced pages through its normal cleanup path.

**Data flow**: It finds the binding, removes each stream’s source row, then removes trigger records tied to the binding name.

**Call relations**: The base object delete flow calls this after permission checks. It uses _binding_named to find streams and the trigger store to clean up alarms.

*Call graph*: calls 3 internal fn (_binding_named, _require_ext, _require_triggers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 645–718)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> _ResolvedAccount
```

**Purpose**: Decides which account or credential a source should use. It hides the complexity of brokered connected accounts versus direct workspace credentials.

**Data flow**: It reads connector registry data, connected accounts, connection ownership, declared credentials, and the requested account ID. It returns an account handle and optional connection ID, or raises a clear message telling the user what to connect or configure.

**Call relations**: Source registration calls this before writing source rows, because the chosen account becomes part of the saved source config and future sync authentication.

*Call graph*: calls 4 internal fn (connector_accounts, connector_connection, _require_connectors, _require_ext); called by 1 (_apply_owned); 1 external calls (__init__).


##### `trigger_name`  (lines 721–725)

```
def trigger_name(binding: str, conversation_id: UUID) -> str
```

**Purpose**: Builds the official object name for a source trigger. A trigger is identified by the source it watches and the conversation it belongs to.

**Data flow**: It receives a binding name and conversation UUID, combines them with the UUID in hex form, and returns the trigger name string.

**Call relations**: Trigger listing, lookup, and creation all use this so the same source/conversation pair cannot be filed under multiple names.

*Call graph*: called by 3 (_apply_owned, _find, _member_rows).


##### `SourceTriggerObjects._member_rows`  (lines 756–790)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the rows shown when listing source triggers. Each row explains which source wakes which conversation, who created it, and whether it belongs to the current member.

**Data flow**: It reads reported triggers, rebuilds source summaries, looks up creator emails, and returns owned rows with display fields such as conversation, source, delivery, origin, owner email, and mine.

**Call relations**: The object system calls this for trigger lists. It combines trigger-store records with source binding summaries from _bindings_from_ext.

*Call graph*: calls 4 internal fn (_bindings_from_ext, _require_ext, _require_triggers, trigger_name); 4 external calls (__init__, __init__, owner_emails, subject_shared).


##### `SourceTriggerObjects._member_object`  (lines 792–827)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceTriggerSpec] | None
```

**Purpose**: Builds the detailed view of one source trigger. It shows the trigger spec and links to the watched source and, for current delivery, the conversation it reports to.

**Data flow**: It finds the trigger by name, checks that the stored generation matches the owner record, builds links, and returns an object detail record or None.

**Call relations**: The object system calls this after listing/ownership checks. It uses _find to avoid showing stale trigger details.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `SourceTriggerObjects._status`  (lines 829–843)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns status fields for a source trigger, including its conversation, source, delivery mode, origin, creator email, and whether it is the caller’s trigger.

**Data flow**: It finds the trigger, checks its generation, looks up the creator email, and returns a status dictionary.

**Call relations**: The object status path calls this for trigger objects. It shares much of the same display information as trigger listing.

*Call graph*: calls 1 internal fn (_find); 1 external calls (owner_emails).


##### `SourceTriggerObjects._apply_owned`  (lines 845–882)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceTriggerSpec, old: SourceTriggerSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates a new trigger for the current conversation, or treats an identical existing trigger as a no-op. It refuses attempts to rename or edit a trigger in place.

**Data flow**: It derives the expected name from the requested source and current conversation, checks the watched source is visible and shared, writes the trigger, then rechecks that the source still exists.

**Call relations**: The base object apply flow calls this for trigger creation. It calls _watchable before creating and uses the trigger store to save the standing wake-up.

*Call graph*: calls 4 internal fn (_watchable, _binding_named, _require_triggers, trigger_name); 1 external calls (__init__).


##### `SourceTriggerObjects._watchable`  (lines 884–897)

```
async def _watchable(self, ctx: ToolContext, source: str) -> None
```

**Purpose**: Checks whether the requested source can be watched by a trigger. Only visible, shared sources qualify.

**Data flow**: It asks the source object store for the named source. If the source is missing it raises an unknown-object error; if it is private it raises a clear refusal; otherwise it returns successfully.

**Call relations**: SourceTriggerObjects._apply_owned calls this before creating a trigger, so private sources do not create alarms that could never deliver useful changes.

*Call graph*: called by 1 (_apply_owned); 1 external calls (__init__).


##### `SourceTriggerObjects._delete_owned`  (lines 899–903)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Deletes a source trigger if it still matches the expected stored generation. This prevents deleting the wrong record if the trigger changed during the operation.

**Data flow**: It finds the trigger by name, compares its ID with the owner generation, and removes it from the trigger store.

**Call relations**: The base object delete flow calls this after permission checks. It relies on _find to locate the current trigger row.

*Call graph*: calls 2 internal fn (_find, _require_triggers).


##### `SourceTriggerObjects._find`  (lines 905–913)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTrigger | None
```

**Purpose**: Finds a reported trigger by its derived object name. It is the trigger equivalent of looking up a source binding by name.

**Data flow**: It reads all reported triggers from the trigger store, derives each trigger’s name, and returns the matching row or None.

**Call relations**: Trigger detail, status, and delete operations call this to locate the current trigger record.

*Call graph*: calls 2 internal fn (_require_triggers, trigger_name); called by 3 (_delete_owned, _member_object, _status).


##### `on_page_change`  (lines 916–996)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds when synced pages change and wakes the conversations whose triggers care about those sources. It is the bridge between background syncing and conversational alerts.

**Data flow**: It receives a page-change batch, groups changes by source binding, finds triggers for each binding, filters to shared pages readable by the trigger’s agent, writes change logs when needed, and invokes the appropriate conversation or per-page conversation.

**Call relations**: The manifest hook system calls this on page-change events. It calls _bindings_from_ext to map source IDs to bindings, _write_change_log to store details, and _alert_message to build the wake-up text.

*Call graph*: calls 4 internal fn (_alert_message, _bindings_from_ext, _require_triggers, _write_change_log); 1 external calls (__init__).


##### `_write_change_log`  (lines 999–1032)

```
async def _write_change_log(ext: ExtensionContext, conversation_id: UUID, binding: _Binding, latest: str, changes: list[PageChange]) -> str | None
```

**Purpose**: Writes a compact file listing all changed pages for an alert. This keeps large change details out of the message while still giving the agent a file it can inspect.

**Data flow**: It receives the extension context, conversation, binding, change stamp, and changes. If file storage exists, it writes one JSON line per changed page, prunes old logs in the same directory, and returns the path; otherwise it returns None.

**Call relations**: on_page_change calls this before invoking a conversation. It uses _disposition to label each change as added, updated, or removed.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (on_page_change); 1 external calls (dumps).


##### `_disposition`  (lines 1035–1041)

```
def _disposition(change: PageChange) -> str
```

**Purpose**: Labels what happened to a page in a change batch. The label is one of added, updated, or removed.

**Data flow**: It reads the page-change flags and timestamps. Tombstones become removed, pages whose creation time equals the change time become added, and the rest become updated.

**Call relations**: _write_change_log and _stream_counts call this so file logs and alert summaries use the same wording.

*Call graph*: called by 2 (_stream_counts, _write_change_log).


##### `_stream_counts`  (lines 1044–1058)

```
def _stream_counts(changes: list[PageChange]) -> str
```

**Purpose**: Summarizes changes by stream for an alert message. It turns many page changes into a short count such as how many were added or updated.

**Data flow**: It receives a list of page changes, counts each disposition per stream, and returns a readable summary string.

**Call relations**: _alert_message calls this to give the agent a quick overview before it looks at individual pages or the change log.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_alert_message); 1 external calls (defaultdict).


##### `_alert_message`  (lines 1061–1080)

```
def _alert_message(binding: _Binding, changes: list[PageChange], log_path: str | None) -> str
```

**Purpose**: Builds the message sent to an agent when a watched source changes. It balances brevity with enough instructions for the agent to inspect the changed pages.

**Data flow**: It receives the binding, changed pages, and optional log path. For small batches it names pages directly; for larger batches it points to the log file or tells the agent how to list pages; then it returns the final message text.

**Call relations**: on_page_change calls this just before invoking a conversation. It uses _stream_counts, _page_reference, and the binding summary to make the alert understandable.

*Call graph*: calls 3 internal fn (summary, _page_reference, _stream_counts); called by 1 (on_page_change).


##### `_page_reference`  (lines 1083–1087)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: Formats one changed page as an object reference the agent can fetch. It includes a short title so the reference is easier to recognize.

**Data flow**: It reads the page ID and title from a change, shortens the title if needed, and returns a string like a page object reference plus label.

**Call relations**: _alert_message calls this when a change batch is small enough to list individual pages in the alert.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 1090–1124)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: Checks and normalizes tenant-specific API URLs for providers that need them. This protects the system from unsafe or malformed URLs while giving users clear examples.

**Data flow**: It reads the provider’s connector rules and the submitted base URL. It rejects forbidden overrides, missing required URLs, unsafe URL parts, invalid ports, and host/path mismatches; otherwise it returns a normalized HTTPS URL or None.

**Call relations**: SourceObjects._apply_owned calls this before registering or changing a source, so bad tenant URLs are refused before any source rows are written.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).


### Extension authentication and registry
The extension package, direct API-key authentication helper, and registry make source connector implementations importable, credentialed, and discoverable by name.

### `extensions/sources/ufo_ext_sources/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as a package, which means code elsewhere can import things from files inside that folder using a package-style name. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label is what lets the rest of the system find and refer to the drawer cleanly. Because the file has no code, it does not run setup steps, create objects, or change program behavior directly. Its value is structural: without it, depending on the Python version and import setup, parts of the project might not recognize `extensions/sources/ufo_ext_sources` as an importable package.


### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `feed-sync authentication`

Some feed-sync sources need to talk to an outside provider using an API key that a workspace member supplied directly. This file is the small bridge that makes that possible without exposing the secret more widely than needed. Think of it like a locked key cabinet: the source run asks for the key for a named provider, and this code retrieves only that key from the workspace-scoped credential store.

The main class, `DirectAuthProxy`, is given a `CredentialAccess` object. That object is the approved way to read stored secrets. When a source needs credentials, it calls `credential` with the workspace, provider name, and account handle. In this direct model, the account handle is only a routing signal; the real secret is stored under the provider name. The method fetches that stored secret and wraps it as a bearer credential, meaning a token used in an HTTP `Authorization: Bearer ...` style request.

The important safety rule is that the secret is read host-side during the sync job. It is used to authenticate outgoing provider requests, but it is not sent to the sandbox or exposed to the agent. That keeps “bring your own key” useful while preserving a tight boundary around the secret.

#### Function details

##### `DirectAuthProxy.credential`  (lines 29–30)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This method retrieves the API key for a provider and returns it in the standard credential shape used by the rest of the auth system. A source uses it when it has been routed through the direct-auth path and needs a bearer token for provider HTTP requests.

**Data flow**: It receives a workspace ID, a provider name, and an account handle. The provider name is used to read the matching secret from `self.credentials`; the account handle is not used here because, in this direct model, it only identifies the route into this backend. The fetched secret is then wrapped in a `Credential` object as its bearer token and returned to the caller.

**Call relations**: When a direct-auth source run needs credentials, it asks this method for them. The method delegates the actual secret lookup to `CredentialAccess.get`, then hands the result into `Credential.__init__` so the rest of the system receives a normal credential object instead of a raw secret string.

*Call graph*: 1 external calls (__init__).


### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup`

This file solves a simple but important problem: when the system sees a source row that says it uses a certain backend, it needs a reliable way to find the connector code for that backend. Rather than searching the whole codebase at startup, this file lists every supported connector explicitly. That is like keeping a printed directory at the front desk instead of walking through every office to ask who works there.

The file imports connector classes for many outside services, such as GitHub, Slack, Stripe, Google Drive, Zendesk, and others. Each connector class has a `name`, which is the short label used elsewhere in the system to identify that backend. The helper function builds a dictionary where each name points to its connector class. While doing that, it checks for duplicate names, because two connectors with the same name would make the system unable to know which one to use.

The finished `CONNECTORS` dictionary is the main product of the file. Other parts of the sync system can look up a backend name and get the right connector class. The file also defines `SOURCE_KIND` as the object kind name for sources. Without this registry, new source backends would not be discoverable in a predictable way, and duplicate backend names could silently cause confusing behavior.

#### Function details

##### `_connector_registry`  (lines 61–69)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: This function turns a list of connector classes into a lookup table keyed by each connector’s name. It also protects the system from ambiguity by refusing to build the table if two connectors claim the same name.

**Data flow**: It receives a tuple of connector classes. It starts with an empty dictionary, reads the `name` from each class, checks whether that name has already been used, and then stores the class under that name. It returns the completed dictionary, or raises an error if it finds a duplicate name.

**Call relations**: This function is used in this file when `CONNECTORS` is created during module loading. The file hands it the full explicit list of imported connector classes, and it hands back the registry that the rest of the source-sync system can use to find the correct connector for a backend name.
