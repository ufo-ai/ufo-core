# Core source synchronization framework  `stage-14.9`

This stage is the shared engine for bringing outside content into UFO. It sits behind the scenes during regular syncing, between connectors that talk to services like GitHub or Zendesk and the stored page records used later for search or recall.

The package marker, __init__.py, simply makes this folder importable by the rest of the program. connector.py defines the common contract that every connector must follow: what it can fetch, how records are grouped into pages, and how to walk through separate partitions such as repositories, projects, or channels while keeping progress. rest.py supplies common tools for connectors that call REST APIs, meaning web services reached through standard HTTP requests. It provides safe request handling, retries, and pagination, which is the process of reading results a page at a time. backend.py turns raw connector streams into a single sync outcome the system can understand. sync.py registers sources, runs fetches, stores changed pages, marks vanished pages as deleted, and exposes those changes for later indexing.

## Files in this stage

### Connector foundations
Package setup and shared connector contracts define the vocabulary and partition-walking behavior used by source integrations.

### `core/src/ufo/sources/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code inside `core/src/ufo/sources` using normal Python import paths, rather than treating the folder as just a collection of files. Think of it like putting a label on a drawer: the label does not do the work, but it tells the rest of the system that the drawer belongs to the organized cabinet. If this file were removed, imports may still work in some modern Python setups because of “namespace packages,” but keeping it here makes the package boundary explicit and compatible with tooling that expects traditional packages. Since the file is empty, it does not set up shared state, expose helper names, or run code when the package is imported.


### `core/src/ufo/sources/connector.py`

`domain_logic` · `sync run`

Connectors are the bridge between outside services and the rest of the system. This file sets the contract they must follow: a connector declares its streams, fetches records page by page, and turns each record into text the system can store and later recall. Without this file, every provider would invent its own shape for cursors, pages, deleted records, and rendered content, making the sync engine much harder to trust.

The small data classes in the file describe the pieces of a sync. A StreamSpec says what kind of object is being synced and how to identify each record. Pagination and PaginationStrategy describe common ways web APIs split long result lists into pages. StreamPage carries records, optional deletions, and the next place to resume.

The most involved part is PartitionWalk. Some sources are not one long list; they are many lists, like one list per Slack channel or GitHub repository. PartitionWalk is like a careful delivery driver with a notebook: it records which partition it is visiting, how far it got, and whether it was moving from old to new or new to old. This lets a later run resume safely after a capped or interrupted sync.

Finally, Connector is the abstract base class, meaning each real connector must implement the required methods. Its default render method gives every record a readable title and a JSON body unless a content-focused connector provides a nicer version.

#### Function details

##### `PartitionWalk.stream`  (lines 209–301)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This function turns many per-partition page streams into one overall stream of pages, while keeping a resumable cursor for each partition. It is used when a source is split into separate buckets, such as repositories, channels, or folders, and the system needs to sync them without starting over every time.

**Data flow**: It receives an optional saved cursor as text. It decodes that into a map of partition names to progress markers, asks the partition provider for each partition, then asks the page provider for records within the right bounds. As pages arrive, it updates the checkpoint map and yields StreamPage objects containing the records, deletions, and the newly encoded cursor. It also closes async generators when finished, skips partitions that report a non-data failure, and cleans up cursor entries for partitions that disappeared after a completed pass.

**Call relations**: The sync runner consumes the pages this method yields. At the start it calls PartitionWalk._decode to understand the saved cursor. During the walk it creates PartitionBound values to tell the connector where to resume, wraps outgoing records in StreamPage values, and uses PartitionWalk._encode whenever it needs to publish an updated resume point.

*Call graph*: calls 2 internal fn (_decode, _encode); 3 external calls (__init__, __init__, __init__).


##### `PartitionWalk._decode`  (lines 304–333)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: This helper reads the saved partition cursor back into a structured map that PartitionWalk can use. It protects the sync from silently ignoring corrupted partition state.

**Data flow**: It takes a cursor string or no cursor at all. Empty, non-JSON, or non-object cursors are treated as no partition progress, so the walk starts fresh. If the cursor is a JSON object, each entry becomes either a simple watermark string or a validated in-progress window with high and until bounds. Malformed entries raise an error instead of being dropped.

**Call relations**: PartitionWalk.stream calls this once at the beginning of a walk. The result becomes the starting notebook of per-partition progress that the rest of the stream function updates as pages are fetched.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 336–341)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: This helper turns PartitionWalk's internal progress map into a stable JSON string that can be saved as the next cursor. It is how progress survives between sync runs.

**Data flow**: It receives a map from partition names to either watermark strings or window objects. It converts window objects into plain dictionaries, then serializes the whole map to JSON with sorted keys. The output is a cursor string that can later be passed back into _decode.

**Call relations**: PartitionWalk.stream calls this whenever it yields a page or checkpoint-only update. The encoded string is placed in StreamPage.next_cursor so the outer sync machinery can store the latest safe resume point.

*Call graph*: called by 1 (stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 355–356)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This abstract method asks a connector to list the streams it knows how to sync. A real connector implements it to say, for example, which collections or object types are available from its provider.

**Data flow**: There is no fixed input beyond the connector instance. The concrete connector returns a list of StreamSpec objects, each describing one syncable stream: its name, source object, primary key, cursor field, and related behavior.

**Call relations**: The broader sync setup calls this on a concrete connector to discover what can be synced. This base method is only the required contract; subclasses provide the actual stream list.


##### `Connector.fetch_page`  (lines 359–367)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This abstract method is the connector's required way to fetch records from an outside service. A real connector implements it to yield one page of records at a time, starting from an optional cursor.

**Data flow**: It receives a stream description, the previous cursor if any, a resolved credential for authentication, and the base URL to talk to. The concrete implementation uses those inputs to call the provider and yields either plain lists of record dictionaries or StreamPage objects when it also needs to report deletions or a provider-specific next cursor.

**Call relations**: The sync adapter calls this while running a stream. This base method defines the shape all connectors must follow, while provider-specific subclasses do the actual network or API work.


##### `Connector.render`  (lines 369–388)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This method turns one raw provider record into a readable title and body for storage and recall. It gives all connectors a safe default, while content-heavy connectors can override it to produce nicer prose.

**Data flow**: It receives a record dictionary and the stream it came from. It looks for a useful title in common fields such as title, name, login, or subject. If none exists, it falls back to the record's primary key and raises an error if even that is missing or empty. It returns a title plus a body containing a heading and the record serialized as sorted JSON.

**Call relations**: After records are fetched, the adapter can call this to create the text that will be landed as a recallable page. It uses json.dumps to make the raw record visible in the default body, and subclasses may replace this behavior for documents, emails, or other content where plain JSON would be less useful.

*Call graph*: 1 external calls (dumps).


### REST paging support
Shared REST transport utilities provide HTTP safety, retries, and pagination helpers for API-backed connectors.

### `core/src/ufo/sources/rest.py`

`io_transport` · `source sync fetching`

Many outside services expose data through REST APIs, which are web endpoints that return records as JSON. Each service has its own details, but they all need the same basic chores: authenticate, send requests, retry temporary failures, follow pages of results, and turn responses into lists of records. This file is the reusable scaffolding for that work.

The main class, RestConnector, is meant to be inherited by provider-specific connectors. A provider supplies things like its base URL and stream list, then either declares a pagination style or overrides pagination for unusual APIs. Think of this file as the conveyor belt: a connector places an API endpoint on the belt, and this code repeatedly asks for pages, checks that each page contains records shaped like dictionaries, optionally flattens each record, and yields them to the sync system.

It also protects the system from common API trouble. Temporary network errors and rate limits are retried with increasing waits. Bad responses include part of the response body in the error so the real API complaint is visible. Pagination loops are capped, and repeated cursors are rejected, so a buggy API cannot make a sync run spin forever. The file deliberately only supports reading data, not creating or updating remote data.

#### Function details

##### `get_path`  (lines 42–51)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a nested value from a dictionary using a dotted path like "page.next.cursor". It gives callers a safe way to look inside API responses without crashing if a piece is missing.

**Data flow**: It receives a dictionary, a dotted path, and an optional fallback value. It walks through the dictionary one part at a time; if the current value is not dictionary-like or a part is missing, it returns the fallback. If every part exists, it returns the found value.

**Call relations**: Pagination helpers use this when an API hides useful information inside a response envelope. records_at uses it to find record lists, cursor pagination uses it to find the next cursor, and offset pagination uses it to read continuation details.

*Call graph*: called by 3 (_get_cursor_pages, _get_offset_pages, records_at).


##### `list_or_empty`  (lines 54–58)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: Turns an unknown value into a clean list of record dictionaries. If the value is not a list, or if some list items are not dictionaries, those bad shapes are ignored.

**Data flow**: It receives any value. If the value is a list, it keeps only items that are dictionaries and returns them. If not, it returns an empty list.

**Call relations**: This is the small shape-checking helper used wherever API JSON is expected to become records. records_at, _response_list, and the OData pager all rely on it before yielding records downstream.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 61–64)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Returns a value only if it is a dictionary-shaped record. It is useful when connector-specific code reaches into a nested object and wants a safe empty dictionary instead of a crash.

**Data flow**: It receives any value. If the value is a dictionary, that dictionary comes out unchanged. Otherwise, the result is an empty dictionary.

**Call relations**: This helper is not called inside this file, but it is provided for REST connector subclasses that need the same safe record-shaping behavior as list_or_empty.


##### `records_at`  (lines 67–72)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: Extracts a list of record dictionaries from an API response, either from the response itself or from a named nested path. It lets pagination code treat many different response shapes in the same way.

**Data flow**: It receives response data and an optional path. If there is no path, it tries to treat the whole data value as the list of records. If there is a path, it first uses get_path to find that nested value, then uses list_or_empty to return only dictionary records.

**Call relations**: Most pagination paths call this after fetching a page. paginate_from_strategy also uses it for one-shot time-window fetches and for parsing link-header pages whose records are wrapped inside a response object.

*Call graph*: calls 2 internal fn (get_path, list_or_empty); called by 5 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, paginate_from_strategy, parse).


##### `_int_or_none`  (lines 75–80)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: Converts a value to an integer only when that conversion is clearly safe. It accepts real integers and strings made only of digits.

**Data flow**: It receives any value. If the value is already an integer, it returns it. If it is a decimal string, it converts and returns the integer. Otherwise, it returns None.

**Call relations**: Offset pagination uses this when an API reports the page size it actually applied. If the reported value cannot be trusted as a number, the pager falls back to other ways of advancing.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 83–86)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: Copies records and stamps extra context fields onto each one, such as a parent ID or site ID. This helps later stages know where each record came from.

**Data flow**: It receives an iterable of record dictionaries plus named context values. For each record, it creates a new dictionary containing the original fields and the context fields. It returns the new list of records.

**Call relations**: This helper is available to provider-specific connectors, especially those that fan out from one object to child objects and need to preserve that parent information.


##### `next_link`  (lines 89–94)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: Finds the next-page URL in an HTTP Link header. Some APIs put pagination instructions in headers instead of the JSON body.

**Data flow**: It receives HTTP response headers. It reads the "link" header, searches for the part marked as the next page, and returns that URL if found. If the header is absent or does not contain a next link, it returns None.

**Call relations**: _get_link_header_pages calls this after each response. If a next URL is found, that pager asks for the next page; otherwise, it stops.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 97–102)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: Decides whether a failed request is worth trying again. It treats network transport failures and common temporary HTTP status codes as retryable.

**Data flow**: It receives an exception. If the exception is a network-level transport error, it returns true. If it is an HTTP status error, it checks whether the status code is one of the retryable codes such as rate limit or server error. Otherwise, it returns false.

**Call relations**: _send uses this after a request fails. This decision controls whether the connector waits and tries again or gives the error back to the sync run.

*Call graph*: called by 1 (_send).


##### `_raise_for_status`  (lines 105–116)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: Turns an unsuccessful HTTP response into a useful error message. Unlike a plain status error, it includes a bounded piece of the response body, where APIs often explain what went wrong.

**Data flow**: It receives an HTTP response. If the response is successful, it does nothing. If not, it reads up to a fixed number of characters from the body and raises an HTTPStatusError containing the status, request, URL, and body snippet.

**Call relations**: _send calls this immediately after each response arrives. That means every GET and POST helper gets the same clearer error behavior.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 119–123)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: Reads a JSON object from a response, while treating an empty response as an empty dictionary. This avoids special-case code for APIs that return no body.

**Data flow**: It receives an HTTP response. If the status is 204 or the body is empty, it returns an empty dictionary. Otherwise, it parses the response JSON and returns it as a dictionary.

**Call relations**: _get and _post call this after _send has already checked success and retries. It is the standard conversion path for object-shaped REST responses.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 126–129)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Reads a top-level JSON list of records from a response. Empty responses become an empty list, and non-dictionary items are filtered out.

**Data flow**: It receives an HTTP response. If there is no content, it returns an empty list. Otherwise, it parses the JSON and passes it through list_or_empty to keep only dictionary records.

**Call relations**: _get_link_header_pages uses this when the API returns records as the whole response body instead of nesting them under a named field.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 132–138)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: Stops a pagination loop that has run for too many pages. This protects the system from an API that never signals the end of results.

**Data flow**: It receives a label naming the current fetch and the number of pages already seen. If the count is over the maximum limit, it raises an error. Otherwise, it returns without changing anything.

**Call relations**: All built-in pagination loops call this on each page. It acts like a circuit breaker so a stuck source cannot hold the sync forever.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 141–147)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: Stops cursor-based pagination when the API repeats a cursor or next-link that was already used. A repeated cursor means the API is not moving forward.

**Data flow**: It receives a label, the new cursor token or next URL, and a set of previously seen tokens. If the token is already in the set, it raises an error. Otherwise, it records the token in the set.

**Call relations**: Cursor, link-header, and OData pagination call this whenever they receive a continuation value. It catches infinite loops earlier and more precisely than the page-count limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 156–157)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the streams declared by a REST connector subclass. A stream is a named collection of records the sync system can read.

**Data flow**: It reads the class-level streams_list and returns a fresh list copy. The copy prevents callers from accidentally editing the shared class definition.

**Call relations**: The broader connector system calls this when it needs to know what a provider can read. Provider-specific subclasses fill in streams_list; this base method exposes it.


##### `RestConnector._make_client`  (lines 159–176)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to one account or provider. It applies the right authentication method and sensible request timeouts.

**Data flow**: It receives a base URL and a resolved Credential. It trims the URL, prepares JSON headers, then chooses one authentication path: a proxy transport, a bearer token, or explicit auth headers. It returns an httpx AsyncClient ready for requests, or raises an error if no authentication is present.

**Call relations**: fetch_page calls this before pagination starts. All later GET and POST helpers use the client it creates, so authentication and timeouts are consistent for the whole fetch.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 178–181)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Fetches a URL with HTTP GET and returns the response as a JSON dictionary. It is the normal read helper for object-shaped API responses.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It calls _get_raw to send the retried request, then passes the response to _json_or_empty. The result is a dictionary, with empty responses represented as {}.

**Call relations**: Cursor, offset, page-number, and time-window pagination use this when they need the response body. It delegates retry and status checking to _get_raw and _send.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, paginate_from_strategy).


##### `RestConnector._get_raw`  (lines 183–187)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Fetches a URL with HTTP GET and returns the full raw response. This is needed when pagination information lives in headers, not just in the JSON body.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It wraps client.get in the shared _send retry envelope. The result is a successful httpx Response, or an error if retries fail or the status is not acceptable.

**Call relations**: _get uses this before parsing JSON. Link-header and OData pagination use it directly because they need response headers or absolute continuation links.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 189–194)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Sends a POST request for APIs that use POST for read-style endpoints. It keeps this read path consistent with GET by using the same retry and JSON parsing behavior.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It sends the request through _send, then turns the successful response into a dictionary with _json_or_empty.

**Call relations**: This helper is available to subclasses for read endpoints like search APIs. It shares _send with GET so retries and errors behave the same way.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 196–202)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Sends a POST request and returns the raw response. It is useful for read APIs whose response is not a normal JSON object, such as a top-level list.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It sends client.post through _send. The output is the successful raw response.

**Call relations**: Provider-specific connectors can call this when _post would parse the response into the wrong shape. It still benefits from the same retry wrapper used by the other request helpers.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 204–218)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: Runs one HTTP request with retry behavior around it. It is the shared safety wrapper that makes temporary API and network problems less likely to fail a sync immediately.

**Data flow**: It receives a zero-argument async request function. It calls the request, checks the response status with _raise_for_status, and returns the response if successful. If a retryable error happens, it waits, doubles the delay, and tries again until the attempt limit is reached.

**Call relations**: _get_raw, _post, and _post_raw all hand their actual HTTP call to this function. It calls _is_retryable to decide whether another attempt is allowed and asyncio.sleep to pause between attempts.

*Call graph*: calls 2 internal fn (_is_retryable, _raise_for_status); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 220–249)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Opens the HTTP client, runs pagination for one stream, validates each page, flattens records, and yields data to the sync engine. This is the main read entry point of the REST connector base class.

**Data flow**: It receives a stream, optional cursor, credential, and base URL. It chooses the final URL, creates an authenticated client, asks paginate for pages, skips empty pages, validates that records are dictionaries, flattens each record, and yields either a list of records or a StreamPage with records, deletes, and a next cursor. When done, it closes any async generator source cleanly.

**Call relations**: The core sync driver calls this to fetch data. Inside, it calls _make_client for transport setup, paginate for provider-specific or strategy-based paging, _validate_page for shape safety, and flatten before handing records onward.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate); 1 external calls (__init__).


##### `RestConnector.paginate`  (lines 251–263)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Provides the default pagination behavior for a stream. If the stream declares a supported pagination strategy, it routes to the shared strategy runner; otherwise, subclasses must override it.

**Data flow**: It receives an HTTP client, a stream, and an optional cursor. It checks the stream's pagination settings. If there is no usable strategy, it raises NotImplementedError. Otherwise, it yields pages produced by paginate_from_strategy.

**Call relations**: fetch_page calls this after creating the HTTP client. Provider connectors can override it for unusual APIs, but ordinary streams pass through to paginate_from_strategy.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (fetch_page).


##### `RestConnector.paginate_from_strategy`  (lines 265–358)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs one of the built-in pagination patterns declared on a stream. It lets many connectors describe pagination with data instead of writing custom loops.

**Data flow**: It receives a stream, HTTP client, and optional cursor. It reads the stream's pagination specification, resolves the request path, validates that required settings are present, and then calls the matching helper for cursor, link-header, page-number, offset-limit, or time-window pagination. It yields record pages from that helper.

**Call relations**: paginate calls this for streams with declared pagination. It hands off to _get_cursor_pages, _get_link_header_pages, _get_page_number_pages, _get_offset_pages, or a one-request _get flow depending on the strategy.

*Call graph*: calls 7 internal fn (_get, _get_cursor_pages, _get_link_header_pages, _get_offset_pages, _get_page_number_pages, _strategy_path, records_at); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 297–299)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Parses records out of a link-header paginated response when the records are nested inside the JSON body. It is a small custom parser created only for that strategy case.

**Data flow**: It receives an HTTP response. It parses the body as JSON if content exists, then uses records_at with the configured record path to return a clean list of record dictionaries.

**Call relations**: paginate_from_strategy creates this function and passes it to _get_link_header_pages when a link-header stream also has a record_path. The link-header pager calls it for each response instead of assuming the whole body is a list.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 360–388)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: Fetches pages for APIs that put the next-page URL in the HTTP Link header. This is common in REST APIs that follow web linking conventions.

**Data flow**: It receives a client, path, optional query parameters, optional page-size settings, and an optional record parser. It fetches the first page, yields records from the body, reads the next link from headers, checks that the link is not repeating, and follows it until no next link remains.

**Call relations**: paginate_from_strategy calls this for the next_link strategy. It relies on _get_raw for retried requests, _response_list or the supplied parser for records, next_link for continuation, and the bound helpers to prevent endless loops.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 390–422)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: Fetches pages for APIs that return a next cursor token in the response body. A cursor is like a bookmark the API gives back so the next request can continue from the right place.

**Data flow**: It receives a client, path, record path, cursor path, parameter names, page-size settings, and extra parameters. For each request, it builds query parameters, adds the current cursor if there is one, fetches JSON, extracts records, yields them, then reads the next cursor. If there is no next cursor, it stops.

**Call relations**: paginate_from_strategy calls this for the next_cursor strategy. It uses _get for requests, records_at for records, get_path for the cursor, and the bound helpers to stop runaway or repeating pagination.

*Call graph*: calls 5 internal fn (_get, _bound_cursor, _bound_pages, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_odata_pages`  (lines 424–450)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Microsoft Graph or OData-style pages. In this format, records are usually under "value" and the next URL is stored in "@odata.nextLink".

**Data flow**: It receives a client, path, and optional parameters. It requests the current path, reads records from the "value" field, yields them, then follows the "@odata.nextLink" URL if present. Only the first request uses the original parameters because later next links already contain their own query details.

**Call relations**: This helper is available for connector subclasses that need OData behavior. It uses _get_raw for requests, list_or_empty for record shape checking, and the bound helpers to keep pagination finite.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 452–492)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: Fetches pages for APIs that use offset and limit parameters. Offset means “start after this many records,” and limit means “return up to this many records.”

**Data flow**: It receives a client, path, record path, limit, parameter names, optional extra parameters, and optional response fields that describe continuation. It repeatedly sends the current offset and limit, extracts records, yields them, and advances the offset. It stops on no records, on a server-provided “no more” signal, or on a short page when no explicit signal exists.

**Call relations**: paginate_from_strategy calls this for the offset_limit strategy. It uses _get for each request, records_at for record extraction, get_path for continuation fields, _int_or_none for safe offset stepping, and _bound_pages for loop protection.

*Call graph*: calls 5 internal fn (_get, _bound_pages, _int_or_none, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_page_number_pages`  (lines 494–523)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: Fetches pages for APIs that use page numbers. It starts at a page number and keeps asking for the next page until the API returns fewer records than a full page.

**Data flow**: It receives a client, path, record path, page size, optional parameters, and parameter names. It builds a query with the current page number and page size, fetches JSON, extracts records, yields them if present, and increments the page number. A short page ends the loop.

**Call relations**: paginate_from_strategy calls this for the page_number strategy. It uses _get to retrieve data, records_at to find records, and _bound_pages to prevent an endless page-number loop.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._strategy_path`  (lines 525–531)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: Resolves the request path for a stream when the pagination specification did not include one. The base class raises an error because only a provider-specific subclass knows that mapping.

**Data flow**: It receives a stream. In this base implementation, it does not return a path; it raises NotImplementedError with a message explaining that the stream needs either a Pagination.path or a subclass override.

**Call relations**: paginate_from_strategy calls this when it needs a request path and the pagination spec leaves it blank. Connectors with their own stream-to-path table override this method.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 533–536)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns a raw API record into the flat dictionary the sync writer expects. The default does nothing because many APIs already return flat enough records.

**Data flow**: It receives one record dictionary and its stream. It returns the same record unchanged. Subclasses can override it to lift nested fields or remove an envelope before records are written.

**Call relations**: fetch_page calls this on every validated record just before yielding it. This gives provider-specific connectors one final shaping step after pagination but before sync output.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 538–549)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: Checks that a page yielded by pagination is a list of dictionary records. This catches connector mistakes early with a clear error message.

**Data flow**: It receives a page value and the stream it belongs to. If the page is not a list, it raises a TypeError. If any item in the list is not a dictionary, it raises a TypeError naming the bad item type. If the shape is correct, it returns nothing.

**Call relations**: fetch_page calls this before flattening or yielding records. It protects later sync stages from receiving unexpected shapes that would be harder to diagnose downstream.

*Call graph*: called by 1 (fetch_page).


### Source synchronization
Backend orchestration and sync persistence turn connector records into registered, stored, updated, deleted, and feedable source pages.

### `core/src/ufo/sources/backend.py`

`orchestration` · `source sync run`

Connectors speak the language of external services: they fetch pages of records, may provide their own pagination cursor, and may report deleted items. The rest of UFO wants a simpler package: a set of recallable pages, a cursor for next time, and a flag saying whether this was a full snapshot. This file translates between those worlds.

The main class, ConnectorBackend, runs one stream for one account. It first asks the auth proxy for a credential, so secrets stay behind the proper boundary and are not exposed to agents or logs. It then finds the requested stream, calls the connector, and converts each provider record into a Page with a stable reference, text body, digest, and timestamps.

A key job here is keeping sync runs bounded. Incremental streams stop after a record cap so one huge backfill does not occupy a worker forever. If the connector gives a native cursor, UFO stores that. If not, this file stores its own small “envelope” cursor: where the run started, how many records to skip next time, and the latest watermark. This is like putting a bookmark in a long stack of papers, even when the paper source did not provide page numbers.

Full snapshot streams are different. They are never capped, because UFO needs the whole list to safely detect missing records and tombstone only things that truly disappeared.

#### Function details

##### `ConnectorBackend.fetch`  (lines 106–195)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one connector stream for one account and returns the sync result UFO expects. It protects the system from endless or oversized incremental runs by capping them, while letting full snapshots finish completely so delete detection stays correct.

**Data flow**: It receives a connector source config, the previously stored cursor, and auth information. It asks the auth proxy for a credential, finds the named stream, decides whether the cursor is a normal connector cursor or UFO's own backfill envelope, then reads records from the connector. Each landed record becomes a Page, deletes are collected, timestamps and watermarks are advanced, and the function returns a SyncResult with pages, deletes, the next cursor, and whether this was a full snapshot. It may also write a warning if a provider keeps returning records without making cursor progress.

**Call relations**: This is the main flow in the file. It calls ConnectorBackend._stream to locate the stream, ConnectorBackend._decode_cursor to understand UFO-owned backfill cursors, ConnectorBackend._page to turn raw records into internal pages, and _max_str to advance a string watermark. When a capped run has no safe connector checkpoint, it creates a _BackfillEnvelope and serializes it with json.dumps so the next run can resume by re-reading and skipping already consumed records.

*Call graph*: calls 4 internal fn (_decode_cursor, _page, _stream, _max_str); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._stream`  (lines 197–201)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream definition with the requested name inside the connector. A stream definition tells the backend things like the primary key, cursor field, timestamp fields, and whether the stream is a full snapshot.

**Data flow**: It receives a stream name and reads the connector's advertised streams. If it finds a matching stream, it returns that StreamSpec. If none match, it raises an error so the sync fails clearly instead of silently fetching the wrong data.

**Call relations**: ConnectorBackend.fetch calls this near the start of a sync run, before any network fetching happens. The returned stream specification guides the rest of fetch, including how records are identified, whether deletes are authoritative, and whether cursor capping rules apply.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 204–221)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Checks whether a stored cursor is UFO's own backfill envelope or just opaque connector state. This matters because UFO must only interpret cursors that it created itself.

**Data flow**: It receives the stored cursor string, if any. It tries to parse it as JSON, then looks for the reserved ufo_backfill key. If that key is present, it validates the envelope fields and returns the envelope object. If not, it returns None, meaning the cursor should be passed through untouched. If the reserved envelope is malformed, it raises an error because this adapter is supposed to be the only writer of that format.

**Call relations**: ConnectorBackend.fetch calls this for incremental streams before fetching records. If it returns an envelope, fetch resumes by starting from the envelope's origin and skipping records already consumed. If it returns None, fetch treats the cursor as normal connector-owned state.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 223–249)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page
```

**Purpose**: Turns one raw provider record into UFO's internal Page object. A Page is the stored, recallable form of a record, with stable identity, text content, a digest, and timestamps.

**Data flow**: It receives the stream specification and one record dictionary from the connector. It chooses a stable record reference with _record_ref, asks the connector to render the record into a title and body, extracts created and updated timestamps with _record_timestamp, hashes the body to make a content digest, and returns a Page ready for the sync result.

**Call relations**: ConnectorBackend.fetch calls this for every record that should be landed after any resume-skip logic. It relies on _record_ref so updates and deletes point at the same source reference, and on _record_timestamp so stored pages have normalized time metadata when the provider gives usable dates.

*Call graph*: calls 2 internal fn (_record_ref, _record_timestamp); called by 1 (fetch); 2 external calls (__init__, sha256).


##### `_record_timestamp`  (lines 252–283)

```
def _record_timestamp(record: dict[str, Any], field: str | None, *, connector: str, stream: str) -> str | None
```

**Purpose**: Extracts and normalizes a timestamp from a provider record. It accepts either a direct field or a nested path, and turns valid timestamp-like values into the standard format used by source pages.

**Data flow**: It receives a record, the field or path to read, and labels naming the connector and stream for warnings. If no field is configured or the value is missing, it returns None. If the value is a string or non-boolean integer, it asks normalize_page_timestamp to convert it. If the value is invalid or cannot be converted, it logs a malformed timestamp warning and returns None.

**Call relations**: ConnectorBackend._page calls this while building each Page's created_at and updated_at values. It uses get_path when timestamps are nested inside the record and warn when provider data is shaped badly enough that UFO should ignore the timestamp rather than store a misleading one.

*Call graph*: called by 1 (_page); 3 external calls (warn, get_path, normalize_page_timestamp).


##### `_record_ref`  (lines 286–290)

```
def _record_ref(stream: StreamSpec, record: dict[str, Any]) -> str
```

**Purpose**: Chooses the stable identifier UFO will use for a provider record. This lets the same record land on the same internal page across re-fetches, updates, and delete reports.

**Data flow**: It receives the stream specification and one record. It first looks for the configured primary key. If that value is a string or integer, it returns it as text. If the record does not have a simple usable primary key, it hashes the whole record as a fallback identifier.

**Call relations**: ConnectorBackend._page calls this before creating a Page. The result becomes part of the page's source_ref, so it must line up with how ConnectorBackend.fetch records delete references for the same stream.

*Call graph*: called by 1 (_page); 2 external calls (sha256, dumps).


##### `_max_str`  (lines 293–298)

```
def _max_str(current: str | None, value: Any) -> str | None
```

**Purpose**: Keeps the greatest string watermark seen so far. A watermark is a marker, often a timestamp-like string, that tells the next incremental sync where to continue.

**Data flow**: It receives the current watermark and a candidate value from the record's cursor field. If the candidate is not a string, it leaves the current watermark unchanged. If there is no current watermark, or the candidate sorts after it, it returns the candidate. Otherwise it returns the existing watermark.

**Call relations**: ConnectorBackend.fetch calls this while reading records from an incremental stream that has a cursor field. The updated watermark can become the next cursor when the connector does not provide a stronger page cursor.

*Call graph*: called by 1 (fetch).


### `core/src/ufo/sources/sync.py`

`orchestration` · `startup, scheduled sync, and downstream indexing`

This file solves a practical problem: outside content changes over time, and the rest of the system needs a reliable local record of those changes. A “source backend” is the plug-in point for different places content can come from, such as a local folder or an external connector. Each backend returns pages, which are documents with a stable source key, a content fingerprint, optional timestamps, browse-friendly metadata, and the body text.

The main worker is `SyncDriver`. On each scheduled run, it finds source rows that are due, claims them so two workers do not sync the same source at once, asks the right backend to fetch content, writes changed page bodies to the blob store, and updates database rows. If a full snapshot no longer includes a page, the driver marks that page as a tombstone, meaning “deleted” without immediately erasing its history. If a source fails, the driver releases it and waits longer before retrying, so a broken provider is not hammered repeatedly.

The file also defines `CorePageFeed`, which lets an indexer replay page changes in a stable order using a cursor. Think of it like a conveyor belt: source sync puts changed pages onto the belt, and the indexer picks them up later without needing to know how they were fetched.

#### Function details

##### `normalize_page_timestamp`  (lines 50–70)

```
def normalize_page_timestamp(value: str) -> str
```

**Purpose**: Converts a page timestamp into one consistent UTC format. This lets different sources send dates as Unix numbers or ISO date strings while the database receives a predictable value.

**Data flow**: It receives a text timestamp. If the text is numeric, it treats it as seconds or milliseconds since the Unix epoch; otherwise it parses it as an ISO date string. It rejects invalid values and date-times without a timezone, then returns a UTC ISO string with microsecond precision.

**Call relations**: This is used by `Page.normalize_timestamp` when a `Page` object is being validated, so every backend benefits from the same timestamp cleanup before syncing continues.

*Call graph*: called by 1 (normalize_timestamp); 2 external calls (fromisoformat, fromtimestamp).


##### `Page.normalize_timestamp`  (lines 88–91)

```
def normalize_timestamp(cls, value: str | None) -> str | None
```

**Purpose**: Validates and normalizes the optional creation and update timestamps on a fetched page. It keeps missing timestamps as missing, but cleans present ones into the shared format.

**Data flow**: It receives either `None` or a timestamp string from a backend. `None` passes through unchanged; a string is handed to `normalize_page_timestamp`, and the normalized UTC timestamp comes back.

**Call relations**: This runs automatically during `Page` validation. It delegates the actual parsing rules to `normalize_page_timestamp` so the `Page` model stays simple.

*Call graph*: calls 1 internal fn (normalize_page_timestamp).


##### `StreamSkipped.__init__`  (lines 123–125)

```
def __init__(self, reason: str) -> None
```

**Purpose**: Creates a special error for cases where a source stream cannot be read for an expected account or permission reason. It carries a human-readable reason so the sync driver can record why the stream was skipped.

**Data flow**: It receives a reason string, stores it on the exception, and also passes it to the base error class so normal exception messages still work.

**Call relations**: Many connector extensions raise this when a provider refuses access because of things like missing scopes or plan limits. `SyncDriver.run` recognizes it and reschedules the source without treating it as a real failure.

*Call graph*: called by 49 (paginate, paginate, paginate, paginate, _org_stream, paginate, paginate, paginate, paginate, paginate (+15 more)).


##### `SourceBackend.config_model`  (lines 159–159)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: Defines the type of configuration a source backend expects. This keeps each backend’s settings structured instead of passing around an untyped dictionary.

**Data flow**: A backend exposes a model class. The sync driver reads source configuration from the database and validates it against that model before calling the backend.

**Call relations**: The protocol requires all source backends to provide this property. `SyncDriver._fetch` relies on it before asking the backend to fetch content.


##### `SourceBackend.fetch`  (lines 161–161)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Defines the main contract for fetching content from a source. A backend implements this to turn its configured external source into a `SyncResult` containing pages, deletes, and a next cursor.

**Data flow**: It receives typed source configuration, the previous cursor if there is one, and workspace authentication context. It returns the current batch of pages plus information about what to fetch next and what should be considered removed.

**Call relations**: This is the seam between core sync and source-specific code. `SyncDriver._fetch` calls the concrete backend’s implementation during each sync run.


##### `FolderSource.fetch`  (lines 175–187)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Reads a local folder and turns each file into a page. It provides the built-in source backend for local files.

**Data flow**: It receives folder configuration, ignores the cursor and authentication context, reads the folder on a worker thread, computes a SHA-256 digest for each file body, creates `Page` objects, and returns a full snapshot `SyncResult`.

**Call relations**: This is a concrete implementation of the `SourceBackend.fetch` contract. It calls `FolderSource._read` to get file contents, then the sync driver can commit the returned pages just like pages from any other backend.

*Call graph*: 5 external calls (__init__, __init__, to_thread, sha256, Path).


##### `FolderSource._read`  (lines 190–197)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: Scans a folder and reads all files as UTF-8 text. It is separated from `fetch` so the blocking file work can run outside the main async event loop.

**Data flow**: It receives a filesystem path. If the path is not a directory, it raises an error; otherwise it walks all files below it, reads their bytes, decodes them as UTF-8, and returns relative file paths paired with text.

**Call relations**: This helper is called by `FolderSource.fetch` through `asyncio.to_thread`, which prevents slow disk reads from blocking other async work.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 200–207)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object]) -> UUID
```

**Purpose**: Creates a stable database ID for a source from its workspace, backend name, and configuration. This prevents duplicate source rows when the same source is registered again after a restart.

**Data flow**: It receives a workspace ID, backend name, and config mapping. It serializes the config with sorted keys, combines those values into a stable string, and turns that into a deterministic UUID.

**Call relations**: This is called by `register_sources` while creating configured sources at startup. Because the same inputs make the same ID, re-registration finds the existing row instead of inserting another one.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 210–213)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: Creates a stable page ID for one document inside one source. This lets fetches, updates, and deletes all point to the same database row.

**Data flow**: It receives a source ID and the source’s own reference for a document. It combines them into a deterministic UUID and returns that ID.

**Call relations**: This is used by `SyncDriver._commit` when comparing fetched pages with existing database pages and when translating explicit delete references into page rows.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `register_sources`  (lines 216–250)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: Ensures that sources listed in configuration exist in the database. It runs at boot so configured sources are ready for the scheduled sync job.

**Data flow**: It receives configured source entries. For each one, it reads the current workspace, calculates the stable source ID, checks whether that row already exists, and inserts a new source row only if needed.

**Call relations**: This function calls `source_row_id` to avoid duplicates. It uses a workspace transaction so the inserted source rows belong to the active workspace.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `SyncDriver.candidate_workspaces`  (lines 294–314)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds workspaces that actually have sources due for syncing. This avoids opening per-workspace work when nothing is ready to run.

**Data flow**: It checks the current time, queries source rows whose next sync time has arrived and whose claim is missing or expired, and returns the distinct workspace IDs.

**Call relations**: A scheduler or dispatcher can call this before running workspace-bound sync work. It uses an owner-level transaction so it can see due sources across workspaces.

*Call graph*: 4 external calls (now, or_, select, owner_tx).


##### `SyncDriver.run`  (lines 316–339)

```
async def run(self) -> None
```

**Purpose**: Runs one sync pass for due sources in the current workspace. It is the high-level flow that claims work, fetches content, commits results, and handles errors.

**Data flow**: It creates a unique claim token, gets due sources, and processes each one. Successful fetches are committed; skipped streams are logged and rescheduled normally; failures are logged and released with retry backoff.

**Call relations**: This method calls `_claim_due`, `_fetch`, `_commit`, `_skip`, and `_release` in the main sync story. It is the place where normal success, intentional skip, and true failure split into different paths.

*Call graph*: calls 5 internal fn (_claim_due, _commit, _fetch, _release, _skip); 2 external calls (log, uuid4).


##### `SyncDriver._claim_due`  (lines 341–385)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: Reserves a batch of due sources for this worker. The claim stops another worker from syncing the same source at the same time.

**Data flow**: It receives a claim token, finds due source rows that are not currently claimed or whose claim has expired, marks them with the claim and a lease expiry time, and returns lightweight `ClaimedSource` records.

**Call relations**: `SyncDriver.run` calls this at the start of a sync pass. The returned claimed sources are then passed one by one into `_fetch` and the later commit or release steps.

*Call graph*: called by 1 (run); 7 external calls (__init__, now, timedelta, or_, select, update, workspace_tx).


##### `SyncDriver._fetch`  (lines 387–393)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: Calls the correct backend for a claimed source. It also validates the saved configuration before letting backend-specific code use it.

**Data flow**: It receives a `ClaimedSource`, looks up the backend by name, validates the source config with the backend’s model, builds `SourceAuth` for the workspace, and awaits the backend’s `fetch` result.

**Call relations**: `SyncDriver.run` calls this after claiming a source. The `SyncResult` it returns is handed to `_commit`; any raised error is handled by the surrounding `run` method.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 395–432)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: Decides what changed after a backend fetch and prepares those changes for writing. It avoids rewriting unchanged page bodies by comparing content digests.

**Data flow**: It receives a claimed source and a fetch result. It loads prior page records, creates stable page IDs for fetched pages, compares digests and browse metadata, writes changed bodies to the blob store, collects metadata-only updates and deletes, then calls `_write` to update the database.

**Call relations**: `SyncDriver.run` calls this after `_fetch` succeeds. It uses `_prior_pages` to know what already exists, `page_id_for` to match fetched documents to rows, and `_write` to make the database changes atomically.

*Call graph*: calls 3 internal fn (_prior_pages, _write, page_id_for); called by 1 (run); 2 external calls (__init__, __init__).


##### `SyncDriver._prior_pages`  (lines 434–466)

```
async def _prior_pages(self, source_id: UUID) -> dict[UUID, tuple[str, bool, PageBrowse]]
```

**Purpose**: Loads the existing pages for a source so the new fetch can be compared against them. This is how the driver knows whether a page is new, changed, deleted, or only has metadata changes.

**Data flow**: It receives a source ID, queries page rows for that source, and returns a dictionary keyed by page ID. Each value contains the stored digest, tombstone flag, and browse metadata.

**Call relations**: `SyncDriver._commit` calls this before examining fetched pages. The returned snapshot guides which bodies need blob writes and which rows only need lighter updates.

*Call graph*: called by 1 (_commit); 3 external calls (__init__, select, workspace_tx).


##### `SyncDriver._write`  (lines 468–561)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[ChangedPage], metadata: list[PageBrowse], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> None
```

**Purpose**: Writes the final sync outcome to the database. It inserts or updates changed pages, refreshes metadata, marks deleted pages as tombstones, and reschedules the source.

**Data flow**: It receives the claimed source, next cursor, changed pages, metadata-only pages, fetched IDs, explicit deleted IDs, and whether the fetch was a full snapshot. It writes page changes inside a workspace transaction, tombstones explicit or missing pages when appropriate, clears the claim, resets the error count, and sets the next sync time.

**Call relations**: `SyncDriver._commit` calls this after it has separated body changes, metadata changes, and deletes. This is the final successful database step before control returns to `SyncDriver.run`.

*Call graph*: called by 1 (_commit); 6 external calls (now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._release`  (lines 563–587)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool) -> None
```

**Purpose**: Frees a claimed source after a real failure and schedules a retry later. It uses bounded exponential backoff, meaning repeated failures wait longer but only up to a cap.

**Data flow**: It receives the failed source and a flag saying whether the cursor should be reset. It increments the error count, calculates the next retry time, optionally clears the cursor, clears the claim, and updates the source row.

**Call relations**: `SyncDriver.run` calls this when `_fetch` or `_commit` raises an error other than a planned skip. If the error was `CursorExpired`, `run` passes `cursor_reset=True` so the next attempt starts fresh.

*Call graph*: called by 1 (run); 4 external calls (now, timedelta, update, workspace_tx).


##### `SyncDriver._skip`  (lines 589–606)

```
async def _skip(self, source: ClaimedSource) -> None
```

**Purpose**: Frees a claimed source when the backend says this stream should be skipped rather than treated as broken. Existing pages are left untouched.

**Data flow**: It receives the skipped source, sets its next sync time to the normal interval, resets the error count, clears the claim fields, and does not write or tombstone any pages.

**Call relations**: `SyncDriver.run` calls this after catching `StreamSkipped`. This keeps expected permission or plan-limit problems from triggering failure backoff or accidental snapshot deletion.

*Call graph*: called by 1 (run); 4 external calls (now, timedelta, update, workspace_tx).


##### `PageFeed.pages_changed_since`  (lines 642–642)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Defines the interface an indexer uses to read page changes from core. It promises a bounded batch of changes after a cursor.

**Data flow**: An implementation receives a cursor and a requested limit. It returns a `PageBatch` containing page changes and the next cursor to resume from.

**Call relations**: This protocol is implemented by `CorePageFeed.pages_changed_since`. Extensions can depend on the protocol without knowing the database and blob-store details.


##### `CorePageFeed.pages_changed_since`  (lines 654–704)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Reads changed pages in a stable order and includes each page body when the page is not deleted. This is how downstream indexing catches up after source sync writes page rows.

**Data flow**: It receives an optional cursor and a limit. It queries page rows after that cursor, capped by `PAGE_FEED_BATCH_MAX`; for each non-tombstoned row it reads the body from the blob store, while tombstones get an empty body. It returns `PageChange` records plus a cursor pointing at the last row read.

**Call relations**: This implements the `PageFeed` contract. It is consumed by downstream indexers that repeatedly ask for changes, process them, and store the returned cursor so they can resume safely later.

*Call graph*: 8 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx, UUID).
