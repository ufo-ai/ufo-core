# Connector-backed source sync  `stage-14.1`

This stage is the system’s data intake area for read-only external services. It runs behind the scenes during syncs, when the system asks connected tools for their latest records and turns them into searchable pages. The connector groups cover the many kinds of tools people use: workplace suites like Gmail, Drive, Outlook, and Teams; engineering tools like GitHub, Jira, Slack, PagerDuty, and Sentry; planning tools like Airtable, Asana, Notion, and Wrike; CRM and support tools like HubSpot, Salesforce, Intercom, and Zendesk; marketing and ad tools; finance systems like Stripe, QuickBooks, and Xero; and HR or recruiting tools like Greenhouse, BambooHR, and Deel.

The shared files are the engine underneath these adapters. The connector contract defines what every adapter must provide. The REST helper handles web requests, retries, and “pagination,” which means fetching long result lists in chunks. The backend turns fetched records into stored pages and remembers where to resume. The registry maps service names to connector code. The connected, tools, and pages files create feed rows, expose sources to the rest of the app, and let users read or forget synced pages.

## Sub-stages

- [Workspace suites, mail, docs, and calendars](stage-14.1.1.md) `stage-14.1.1` — 8 files
- [Engineering collaboration and service operations connectors](stage-14.1.2.md) `stage-14.1.2` — 7 files
- [Work management and productivity connectors](stage-14.1.3.md) `stage-14.1.3` — 7 files
- [CRM and customer support connectors](stage-14.1.4.md) `stage-14.1.4` — 6 files
- [Marketing, advertising, social, and form connectors](stage-14.1.5.md) `stage-14.1.5` — 7 files
- [Finance, billing, spend, and accounting connectors](stage-14.1.6.md) `stage-14.1.6` — 7 files
- [HR and recruiting connectors](stage-14.1.7.md) `stage-14.1.7` — 6 files

## Files in this stage

### Connection bootstrap
Creates and repairs default feed rows when a member connects an external source account.

### `extensions/sources/ufo_ext_sources/connected.py`

`domain_logic` · `connection hook and scheduled retry job`

When a member connects an outside account, the system records permission to use that account, but it still needs separate “source” rows for the actual feeds to read. This file closes that gap. It turns a new connection into one feed per important stream, such as the core collections that a connector is built to carry.

There are two paths into the same logic. The first runs immediately when a connection is recorded, so the member can get feeds without doing a second setup step. The second is a retry job that scans existing main-agent connections and creates only the source rows that are still missing.

The code is careful not to undo a member’s choices. If a matching source already exists, it leaves it alone. If the member removed that source before, it does not recreate it, because doing so would erase the meaning of the removal. It also avoids providers that need a tenant-specific URL, because only the member can supply that missing URL.

A useful way to picture this file is as a clerk who, when a library card is issued, automatically prepares the standard reading lists for that card. But the clerk checks first: if a list already exists, or the person deliberately threw it away, it is not recreated.

#### Function details

##### `on_connection_recorded`  (lines 51–58)

```
async def on_connection_recorded(ctx: HookContext) -> HookOutcome
```

**Purpose**: This is the hook that runs right after a connection has been recorded. It starts feed creation for that one new connection before the connection flow finishes.

**Data flow**: It receives a hook context containing an event payload. If the payload is a connection-recorded event, it takes the connection ID, builds a ConnectedSources helper using the extension context, and asks it to register feeds for that connection. If the payload is the wrong kind, it raises an error because this hook should not have been called for anything else. It returns no special outcome.

**Call relations**: This function is the immediate path. The connect flow calls it when a new account connection lands, and it hands the real work to ConnectedSources.register for just that connection.

*Call graph*: 1 external calls (__init__).


##### `retry_connected_sources`  (lines 61–63)

```
async def retry_connected_sources(ctx: ExtensionContext) -> None
```

**Purpose**: This is the safety-net job that creates feed rows that should have been made earlier but were missed. It is useful after a hook failure, process crash, or any other gap between recording a connection and creating its sources.

**Data flow**: It receives the extension context, builds a ConnectedSources helper, and asks it to register feeds without naming a specific connection. That means the helper scans all eligible main-agent connections and creates only the missing rows.

**Call relations**: This function is the retry path. A background job or scheduled tick calls it, and it delegates to ConnectedSources.register to do the same checks and creation logic used by the immediate hook.

*Call graph*: 1 external calls (__init__).


##### `ConnectedSources.register`  (lines 74–82)

```
async def register(self, connection_id: UUID | None=None) -> None
```

**Purpose**: This method finds eligible connected accounts and asks for their canonical feed streams to be registered. It can work on one specific connection or on all main-agent connections.

**Data flow**: It first reads the current source rows so it knows what already exists. Then it looks through the connections held by the main agent. If a specific connection ID was requested, it ignores all others. For each remaining connection, it looks up the connector for that provider. If there is no connector, or the connector lacks a base URL and therefore needs tenant-specific setup, it skips that connection. For each eligible one, it calls the lower-level registration method with the connection, a connector instance, and the already-loaded source rows.

**Call relations**: Both entry functions call this method: the hook calls it for one fresh connection, and the retry job calls it for all connections. It uses the connector registry to identify what kind of provider each connection belongs to, then hands each valid connection to ConnectedSources._register for the per-stream decisions.

*Call graph*: calls 1 internal fn (_register); 2 external calls (main_agent_connections, get).


##### `ConnectedSources._register`  (lines 84–129)

```
async def _register(self, connection: MainAgentConnection, connector: Connector, live: tuple[SourceRecord, ...]) -> None
```

**Purpose**: This method decides exactly which canonical streams for one connected account should become source rows, then creates the missing ones. It protects existing sources and respects sources the member previously removed.

**Data flow**: It receives one connection, its connector, and the current source rows. It builds the member subject for the connection owner, then finds any existing source rows already bound to that connection. If any bound row belongs to a different subject, it stops, because this automatic private registration should not mix with shared or otherwise different ownership. If there is an existing bound row, it reads its configuration to keep the same requested backfill window. Then it checks each stream exposed by the connector, keeps only canonical streams, calculates how far back the first sync should read, and builds the source configuration for that stream. It computes the source ID that such a row would have. If that ID is not already live, it marks it as a candidate. Before creating anything, it asks which candidates were previously removed. Finally, it registers only the candidates that are both missing and not removed.

**Call relations**: ConnectedSources.register calls this once per eligible connection. Inside, this method asks the connector for its streams, uses effective_days to combine requested and stream-specific backfill limits, and calls the extension context to check removed source IDs and register new sources. It is the place where the file’s main promise is enforced: create missing canonical feeds, but never overwrite existing or deliberately removed ones.

*Call graph*: calls 1 internal fn (streams); called by 1 (register); 6 external calls (__init__, model_validate, now, timedelta, member_subject, effective_days).


### REST transport
Provides the reusable HTTP, retry, and pagination foundation used by REST-backed source connectors.

### `core/src/ufo/sources/rest.py`

`io_transport` · `sync read / page fetching`

Many outside services return data a little at a time, and they can also fail briefly because of rate limits, network trouble, or server overload. This file keeps every REST-based source connector from having to solve those same problems again. Think of it as the shared “delivery route planner” for API reads: it knows how to authenticate, ask for a page, wait and retry if the road is temporarily blocked, follow signs to the next page, and stop if the signs loop forever.

The main class, RestConnector, is meant to be subclassed by a specific provider connector. That provider supplies things like its base web address and the streams, or categories of records, it can read. The base class then creates an asynchronous HTTP client, so one slow network call does not freeze other work. It supports credentials sent through a proxy transport, bearer tokens, or custom headers.

The file also contains small helpers for reading nested JSON-like data, turning unknown values into safe empty lists or dictionaries, parsing “next page” links, and validating that pages really contain dictionaries. Pagination is central here: it supports cursor tokens, HTTP Link headers, offset-and-limit pages, page numbers, and Microsoft/OData-style next links. It deliberately only reads data; create, update, and delete write operations are outside this source seam.

#### Function details

##### `get_path`  (lines 49–58)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a dotted path such as "user.profile.name" from nested dictionary-like data. It is used when an API hides records or cursor tokens inside a larger response body.

**Data flow**: It receives a mapping, a dot-separated path, and a fallback value. It walks one key at a time through the nested data; if any step is missing or not dictionary-shaped, it returns the fallback. If every step exists, it returns the final value.

**Call relations**: Pagination helpers call this when they need to pull something specific out of an API response, such as the list of records, a next cursor, a flag saying there is more data, or the actual page size the server used.

*Call graph*: called by 3 (_get_cursor_pages, _get_offset_pages, records_at).


##### `list_or_empty`  (lines 61–65)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: Safely turns a value into a list of record dictionaries, or returns an empty list if it is not shaped correctly. This protects the sync flow from treating bad or unexpected response data as real records.

**Data flow**: It receives any value. If the value is not a list, it returns an empty list. If it is a list, it keeps only items that are dictionaries and drops everything else.

**Call relations**: This is the common cleanup step used after reading JSON responses. It supports record extraction through records_at, plain list responses through _response_list, and OData pages where records live under a standard "value" key.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 68–71)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Safely turns a value into a dictionary record, or returns an empty dictionary if the value is not dictionary-shaped. It is a convenience for connectors that need to pull a nested object out of a page record.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: This helper is not used inside this file’s listed call flow, but it is available to provider connectors that need the same defensive shaping as list_or_empty for one object instead of a list.


##### `records_at`  (lines 74–79)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: Finds the list of records inside an API response, either at the top level or at a named nested path. This lets different APIs keep their records under different envelopes without each connector rewriting the same checks.

**Data flow**: It receives response data and an optional path. If there is no path, it treats the data itself as the record list. If there is a path, it first confirms the response is dictionary-like, follows the path with get_path, and then turns the result into a safe list of dictionaries with list_or_empty.

**Call relations**: The pagination methods use this after each HTTP response to extract the records they should yield. The next-link strategy can also create a small parser that calls records_at when records are nested inside a response body.

*Call graph*: calls 2 internal fn (get_path, list_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, parse).


##### `_int_or_none`  (lines 82–87)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: Converts a simple integer-like value into an integer, or returns nothing if it cannot be trusted. It is used when an API reports a page size that may arrive as either a number or a numeric string.

**Data flow**: It receives any value. If it is already an integer, it returns it. If it is a string made only of digits, it converts and returns that number. Anything else becomes None.

**Call relations**: The offset pagination helper uses this when a server tells the connector how far to move the next offset. If the server’s value is missing or unusable, the pagination logic falls back to safer local counts.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 90–93)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: Copies records and stamps extra context onto each one, such as a parent ID or cloud/account ID. This helps later steps know where a record came from after a connector fans out across parent objects.

**Data flow**: It receives an iterable of record dictionaries plus named context fields. For each record, it creates a new dictionary that contains the original fields plus the context fields, and returns the new list.

**Call relations**: This helper is not called by the base class in this file, but provider-specific connectors can use it when records need origin information before they are flattened or rendered downstream.


##### `next_link`  (lines 96–101)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: Reads an HTTP Link header and extracts the URL marked as the next page. Some APIs put pagination directions in headers rather than in the JSON body.

**Data flow**: It receives response headers. It looks for the "link" header, searches it for a relation labeled "next", and returns that URL if found. If there is no such header or no next relation, it returns None.

**Call relations**: The link-header pagination loop calls this after every page. If it gets a URL, the loop fetches that next page; if it gets nothing, pagination stops.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 104–109)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: Decides whether a failed request is worth trying again. It treats network transport problems and selected temporary HTTP status codes as retryable.

**Data flow**: It receives an exception. Network-level errors are marked retryable. HTTP errors are retryable only for known temporary statuses such as rate limiting or server overload. Other errors are treated as final failures.

**Call relations**: The shared _send method calls this inside its retry loop. This function is the gatekeeper that keeps the connector from retrying permanent problems, such as a bad request, while still riding out temporary outages.

*Call graph*: called by 1 (_send).


##### `_retry_after`  (lines 112–132)

```
def _retry_after(error: BaseException) -> float | None
```

**Purpose**: Reads a server’s Retry-After instruction when the server says to slow down or come back later. It avoids unsafe values so the event loop is not poisoned by impossible sleep times.

**Data flow**: It receives an exception. If it is not an HTTP error, or the status is not one where Retry-After is meaningful, it returns None. Otherwise it reads the header, parses it as a finite non-negative number of seconds, and returns that number if valid.

**Call relations**: _retry_wait calls this while choosing how long to pause before the next attempt. It lets the retry system respect a provider’s rate-limit timing without blindly trusting broken header values.

*Call graph*: called by 1 (_retry_wait); 1 external calls (isfinite).


##### `_retry_wait`  (lines 135–152)

```
def _retry_wait(error: BaseException, delay: float) -> float
```

**Purpose**: Chooses how long to wait before retrying a temporary failure. It combines exponential backoff, meaning waits grow after repeated failures, with random jitter so many workers do not retry at the same instant.

**Data flow**: It receives the error that happened and the current base delay. It checks whether the response gave a Retry-After time, clamps both the server-stated wait and the local delay to safe maximums, picks the stronger wait floor, and returns a randomized wait time within the allowed range.

**Call relations**: _send calls this after a retryable failure. It hands back the sleep duration that _send uses before making the next network attempt.

*Call graph*: calls 1 internal fn (_retry_after); called by 1 (_send); 1 external calls (uniform).


##### `_raise_for_status`  (lines 155–166)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: Turns an unsuccessful HTTP response into an exception, while keeping a short copy of the response body in the message. This matters because API error bodies often contain the real explanation.

**Data flow**: It receives an HTTP response. If the response was successful, it does nothing. Otherwise it takes a capped snippet of the body and raises an HTTPStatusError containing the status, request details, URL, and body text.

**Call relations**: _send calls this immediately after each HTTP response. That makes all GET and POST helpers fail in the same clear way when a server returns an error.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 169–173)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: Turns a response into a JSON dictionary, while treating empty responses as an empty dictionary. This gives callers a predictable shape for object-style API responses.

**Data flow**: It receives an HTTP response. If the status is 204, which means no content, or the response body is empty, it returns {}. Otherwise it parses the response body as JSON and returns it as a dictionary.

**Call relations**: _get and _post use this after _send has already checked for HTTP failures. It is the final step that changes a successful response into data the connector can inspect.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 176–179)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Turns a response whose whole body should be a list into a safe list of record dictionaries. Empty responses become an empty list.

**Data flow**: It receives an HTTP response. If there is no content, it returns an empty list. Otherwise it parses the JSON and passes it through list_or_empty, keeping only dictionary records.

**Call relations**: The link-header pagination helper uses this when no custom record parser was supplied. It covers APIs where each page response is directly an array of records.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 182–188)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: Stops a pagination loop that has run for far too many pages. This prevents a broken or hostile API response from making the sync run spin forever.

**Data flow**: It receives a label describing the pagination source and the number of pages already fetched. If the count is over the maximum allowed page count, it raises an error; otherwise it lets the loop continue.

**Call relations**: Every built-in pagination loop calls this as it advances. It is the emergency brake that turns endless pagination into a loud failure rather than a stuck job.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 191–197)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: Stops cursor-based pagination if the provider repeats a cursor or next-link already seen. A repeated cursor usually means the server is not advancing and the same page would be fetched forever.

**Data flow**: It receives a source label, the new cursor or next URL, and the set of previously seen tokens. If the token is already in the set, it raises an error. Otherwise it records the token for future checks.

**Call relations**: Cursor, link-header, and OData pagination use this after discovering the next page marker. It catches loops earlier and more precisely than the general page-count limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 206–207)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the list of streams that this connector can read. A stream is a named category of records, such as users, tickets, or repositories.

**Data flow**: It reads the class-level streams_list and returns a new list copy. The copy means callers can inspect or modify their local list without directly changing the class’s shared list.

**Call relations**: The broader connector framework asks connectors what streams they offer. Provider subclasses fill in streams_list, and this method exposes that declaration in the standard Connector interface.


##### `RestConnector._make_client`  (lines 209–226)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the asynchronous HTTP client used to talk to one account’s API. It applies the base URL, timeout settings, JSON headers, and the resolved authentication method.

**Data flow**: It receives a base URL and a Credential. It normalizes the URL, prepares default JSON headers, then chooses one authentication route: a proxy transport, a bearer token, or custom headers. It returns an httpx AsyncClient ready to make requests, or raises an error if no authentication is present.

**Call relations**: fetch_page calls this at the start of a page-fetching session. The request helpers then use the returned client for all API calls, so authentication and timeout behavior stay consistent.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 228–231)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Makes a GET request and returns the response body as a dictionary. A GET request is the usual web request for reading data.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It asks _get_raw to perform the request with retries, then passes the successful response to _json_or_empty. The result is a dictionary, or {} for an empty response.

**Call relations**: Several pagination helpers call this when they only need the JSON body, not response headers. It depends on _get_raw so all GET reads share the same retry and error behavior.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 3 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages).


##### `RestConnector._get_raw`  (lines 233–237)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Makes a GET request and returns the full HTTP response. This is needed when pagination information lives in headers or when the caller needs more than just the JSON body.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It wraps client.get in the shared _send retry envelope and returns the successful raw response.

**Call relations**: _get uses this for ordinary JSON reads. Link-header and OData pagination call it directly because they need headers or next-link information from the full response.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 239–244)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Makes a POST request for read-only API endpoints that require POST instead of GET. POST usually means sending a body, but here it is still used only for reading, such as search endpoints.

**Data flow**: It receives an HTTP client, a path, and an optional JSON body. It sends the request through _send so retries and errors are uniform, then converts the successful response to a dictionary with _json_or_empty.

**Call relations**: Provider connectors can call this for read endpoints like searches. It shares the same retry path as GET requests, keeping REST read behavior consistent.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 246–252)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Makes a POST request and returns the full response instead of forcing it into a dictionary. This is useful for read endpoints whose response is not a top-level JSON object.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It sends the request through _send and returns the raw successful response.

**Call relations**: Provider connectors can use this when a POST-based read returns a list or another special shape. It hands off the retry and status checking to _send just like _post does.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 254–275)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: Runs one HTTP request with the shared retry policy. It is the central safety wrapper that helps connectors survive temporary network failures, rate limits, and server errors.

**Data flow**: It receives a no-argument callable that creates an awaitable HTTP request. It tries the request, checks the response status, and returns the successful response. If a retryable error happens, it computes a wait time, sleeps asynchronously, and tries again until attempt or total-wait limits are reached; unretryable or exhausted failures are raised.

**Call relations**: _get_raw, _post, and _post_raw all route through this method. It calls the smaller retry helpers to decide whether to retry, how long to wait, and how to turn bad HTTP responses into clear exceptions.

*Call graph*: calls 3 internal fn (_is_retryable, _raise_for_status, _retry_wait); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 277–314)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[
```

**Purpose**: This is the main read entry for one stream: it opens a client, runs pagination, validates pages, flattens records, and yields them to the sync driver. It is where provider-specific pagination meets the common connector contract.

**Data flow**: It receives a stream, cursor, credential, base URL, acting user ID, and optional backfill floor. It chooses a base URL, creates an authenticated client, gets an async page source from paginate_source, then loops over pages. Each page is checked, each record is flattened, and either a plain list of records or a StreamPage with records, deletes, and next cursor is yielded. When finished or interrupted, it closes the async generator if needed.

**Call relations**: The core sync driver calls this to read data. It calls _make_client for transport, paginate_source for provider pagination, _validate_page for shape safety, and flatten before handing records onward.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate_source); 1 external calls (__init__).


##### `RestConnector.paginate_source`  (lines 316–329)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides a narrow extension point between fetch_page and paginate. It lets special connectors accept extra run-scoped information, such as the acting user or backfill cutoff, without forcing every connector to care about it.

**Data flow**: It receives the HTTP client, stream, cursor, self user ID, and optional backfill date. The default implementation ignores the extra fields and simply returns the async iterator from paginate.

**Call relations**: fetch_page calls this when it needs pages. Most connectors inherit the default path to paginate, while connectors that need the extra context can override this method and still fit into the same fetch_page flow.

*Call graph*: calls 1 internal fn (paginate); called by 1 (fetch_page).


##### `RestConnector.paginate`  (lines 331–343)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Produces raw pages of records for a stream. The default version uses the stream’s declared pagination strategy, while unusual APIs can override it with custom logic.

**Data flow**: It receives an HTTP client, stream, and optional cursor. It checks the stream’s pagination settings. If no usable strategy is declared, it raises NotImplementedError. Otherwise it delegates to paginate_from_strategy and yields each page it produces.

**Call relations**: paginate_source calls this in the normal path. This method then hands off to paginate_from_strategy for the built-in pagination patterns, or is replaced by provider-specific connectors for custom API shapes.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (paginate_source).


##### `RestConnector.paginate_from_strategy`  (lines 345–407)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs one of the standard pagination recipes declared on a stream. This lets a provider describe common paging behavior with configuration instead of writing new loop code.

**Data flow**: It receives a stream and HTTP client. It reads the stream’s pagination specification, resolves the request path, checks that required settings are present for the chosen strategy, and then calls the matching page helper. It yields each list of records returned by that helper.

**Call relations**: paginate calls this for streams with built-in pagination. Depending on the strategy, it hands off to cursor pagination, link-header pagination, or offset-limit pagination; if the path is not declared, it asks _strategy_path for one.

*Call graph*: calls 4 internal fn (_get_cursor_pages, _get_link_header_pages, _get_offset_pages, _strategy_path); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 377–379)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Extracts records from a link-header paginated response when those records are nested inside the response body. It is a small local parser created only when the stream declares a record path.

**Data flow**: It receives a raw HTTP response. It parses the JSON body if present, then uses records_at to pull the record list from the declared nested path. It returns a safe list of record dictionaries.

**Call relations**: paginate_from_strategy passes this parser into _get_link_header_pages for next-link streams with nested records. The page loop then calls it for each response instead of assuming the whole response body is a list.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 409–437)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: Reads pages by following the HTTP Link header’s "next" URL. This is common in APIs that put navigation hints in headers instead of response bodies.

**Data flow**: It receives an HTTP client, first path, optional query parameters, optional page-size settings, and an optional record parser. It fetches the first response, extracts records from each response, yields non-empty record pages, reads the next URL from headers, checks that the URL has not repeated, and continues until no next link is present.

**Call relations**: paginate_from_strategy calls this for next_link streams. It uses _get_raw for retried HTTP calls, _response_list or a custom parser for records, next_link for header parsing, and the bound checks to prevent endless loops.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 439–471)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: Reads pages where the response body contains a token for the next page. A cursor token is like a bookmark the server gives back so the client can ask where to continue.

**Data flow**: It receives an HTTP client, path, record path, cursor path, query parameter names, optional page size, and extra parameters. It repeatedly builds query parameters, fetches JSON with _get, extracts records, yields non-empty pages, reads the next cursor with get_path, and stops when there is no valid cursor. Each new cursor is checked for repeats.

**Call relations**: paginate_from_strategy calls this for next_cursor streams. It combines _get, records_at, get_path, _bound_pages, and _bound_cursor to turn cursor-style API responses into ordinary record pages.

*Call graph*: calls 5 internal fn (_get, _bound_cursor, _bound_pages, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_odata_pages`  (lines 473–499)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Microsoft Graph/OData-style pages, where records are under "value" and the next URL is stored in "@odata.nextLink". OData is a common API format used by Microsoft services.

**Data flow**: It receives an HTTP client, path, and optional parameters for the first request. It fetches each response, parses the body, yields dictionary records from the "value" list, then follows the absolute next-link URL if present. Only the first request uses the original parameters because the next-link already contains continuation details.

**Call relations**: This helper is available for provider connectors that need OData behavior. It uses _get_raw for requests, list_or_empty for safe record extraction, and the pagination bound helpers to avoid endless next-link loops.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 501–541)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: Reads pages using an offset and a limit. In this style, the client asks for a fixed number of records starting at position 0, then 100, then 200, and so on.

**Data flow**: It receives an HTTP client, path, record path, limit, parameter names, optional fixed parameters, and optional response paths that tell whether more data exists or what limit the server actually used. It fetches a page, extracts records, stops on no records, yields records, decides whether another page should be fetched, and advances the offset.

**Call relations**: paginate_from_strategy calls this for offset_limit streams. It uses _get to fetch JSON, records_at to find records, get_path for provider continuation hints, _int_or_none for server-reported limits, and _bound_pages as a safety stop.

*Call graph*: calls 5 internal fn (_get, _bound_pages, _int_or_none, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_page_number_pages`  (lines 543–572)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: Reads pages using page numbers such as page 1, page 2, and page 3. This is another common API paging style.

**Data flow**: It receives an HTTP client, path, record path, page size, optional parameters, parameter names, and starting page number. It requests one page at a time, extracts records, yields any records found, and stops when a page contains fewer records than the requested page size.

**Call relations**: This helper is available to provider-specific connectors, even though the default strategy dispatcher does not call it here. It uses _get, records_at, and _bound_pages to provide the same safety as the other pagination loops.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at).


##### `RestConnector._strategy_path`  (lines 574–580)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: Resolves the request path for a stream when the pagination configuration did not include one. The base version fails on purpose because only a provider connector can know its endpoint layout.

**Data flow**: It receives a stream. Instead of guessing, it raises NotImplementedError explaining that the pagination path is missing and no override exists.

**Call relations**: paginate_from_strategy calls this only when it needs a path and the stream did not declare one. Provider subclasses can override it to look up paths from their own stream tables.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 582–585)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Converts one raw API record into the flat dictionary shape the sync writer expects. The default does nothing because many APIs already return records in a usable shape.

**Data flow**: It receives a record dictionary and the stream it belongs to. It returns the same record unchanged.

**Call relations**: fetch_page calls this for every record before yielding it onward. Provider connectors override it when API payloads wrap useful fields inside nested envelopes or need light reshaping.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 587–598)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: Checks that a pagination method yielded a list of dictionary records. This catches connector bugs early, before malformed data reaches the sync writer.

**Data flow**: It receives a page value and a stream. If the page is not a list, it raises a TypeError. If any item in the list is not a dictionary, it raises a TypeError naming the bad item type. If everything is shaped correctly, it changes nothing.

**Call relations**: fetch_page calls this on every yielded page, including the records inside StreamPage objects. It acts as the last shape check between provider pagination code and the rest of the sync pipeline.

*Call graph*: called by 1 (fetch_page).


### Source management tools
Exposes connected provider accounts and streams as editable system objects and lets conversations subscribe to synced sources.

### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object request handling and page-change hook`

This file is the bridge between the object system that agents use and the lower-level source syncing system that stores rows, credentials, pages, and triggers. A “source” here means one provider account plus optional tenant URL, carrying one or more named streams to sync. The file enforces that source names are derived from that identity, so the same account cannot accidentally be registered twice under different names. Applying a source validates the provider, streams, account access, tenant URL, sharing setting, and backfill window before it writes anything. Adding a stream registers a new sync row; dropping a stream removes that row and its synced pages.

It also defines “source triggers.” A trigger is a standing request for one conversation to be notified when a shared source changes. Private sources cannot be watched, because their pages are not meant to be visible to the wider conversation. When page changes arrive, the file groups them by source binding, filters out anything the trigger’s agent cannot read, writes a compact change log file when possible, and wakes the right conversation with a human-readable alert. In short, this file is the control panel and alarm system for synced external content.

#### Function details

##### `_Binding.name`  (lines 200–201)

```
def name(self) -> str
```

**Purpose**: Returns the official object name for a source binding. The name is derived from the provider, account, and tenant URL so identity is consistent and not chosen by hand.

**Data flow**: It reads the binding’s provider, account, and base URL, passes them to the shared naming helper, and returns the resulting string.

**Call relations**: Other source and trigger code relies on this name when listing bindings, looking up a binding by name, matching page changes to triggers, and building alert text.

*Call graph*: 1 external calls (binding_name).


##### `_Binding.created_at`  (lines 204–205)

```
def created_at(self) -> datetime
```

**Purpose**: Reports when the binding first came into existence. Because a binding is stored as several stream rows, it uses the oldest stream creation time as the binding’s creation time.

**Data flow**: It reads all stream records inside the binding, finds the earliest creation timestamp, and returns that timestamp.

**Call relations**: The object-detail view uses this so a source made of multiple stream rows still appears as one object with one creation date.


##### `_Binding.updated_at`  (lines 208–209)

```
def updated_at(self) -> datetime
```

**Purpose**: Reports when any part of the binding was last changed. Since each stream row has its own update time, the newest one represents the binding’s latest change.

**Data flow**: It reads every stream’s update timestamp, selects the latest timestamp, and returns it.

**Call relations**: The source object view uses this to show a meaningful last-updated time for the whole binding.


##### `_Binding.links`  (lines 211–224)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: Builds the object links that explain what credential or connection a source uses. This helps readers understand where the source gets permission to sync from.

**Data flow**: It looks at whether the binding uses a direct workspace credential, a private connected account, or a shared source. It returns an access link for direct credentials or private connections, and returns no link for shared connected sources.

**Call relations**: Source object detail calls this when presenting a source. It creates links to credential or connection objects so the object browser can show the relationship.

*Call graph*: 4 external calls (__init__, __init__, credential_object_name, account_object_name).


##### `_Binding.spec`  (lines 226–234)

```
def spec(self) -> SourceSpec
```

**Purpose**: Turns the stored binding back into the public source specification that agents read and re-apply. It hides internal row details and shows the user-facing fields.

**Data flow**: It reads the binding’s provider, stream names, account, base URL, sharing subject, and backfill setting, then builds and returns a SourceSpec object.

**Call relations**: Source object detail and update paths use this to compare the requested spec with the current source state.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Binding.summary`  (lines 236–238)

```
def summary(self) -> str
```

**Purpose**: Creates a short label for a source binding, showing the provider, account, and streams. This is for list views and notification messages where full details would be too bulky.

**Data flow**: It joins the stream names, formats them with the provider and account, trims the text to the maximum summary length, and returns the string.

**Call relations**: Source listings and alert messages use this as the human-friendly description of a binding.

*Call graph*: called by 1 (_alert_message).


##### `_require_ext`  (lines 247–250)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure an ExtensionContext is present before source code tries to use storage, files, credentials, or sync APIs. It fails fast with a clear internal error if the object was dispatched incorrectly.

**Data flow**: It receives an optional extension context. If it is missing, it raises an error; otherwise it returns the context unchanged.

**Call relations**: Most source and trigger operations call this before touching extension services. It is a small guardrail used throughout the file.

*Call graph*: called by 10 (_apply_owned, _delete_owned, _grant_settled, _member_rows, _resolved_account, _resync, _widen_window, _member_rows, _binding_named, _require_triggers).


##### `_require_connectors`  (lines 253–256)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Makes sure the current tool request has access to the connector registry, which knows what external providers and account paths are available.

**Data flow**: It reads the connector registry from the tool context. If none is available, it raises an error; otherwise it returns the registry.

**Call relations**: Account resolution calls this before deciding whether a provider uses a connected account, a direct workspace credential, or neither.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 259–292)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: Rebuilds high-level source bindings from the lower-level source rows stored by the sync system. This is like taking individual line items and grouping them back into one invoice.

**Data flow**: It reads all source records from the extension context, ignores records for unknown providers, validates each row’s config, groups rows by provider, account, and base URL, and returns one _Binding per group.

**Call relations**: Listing, lookup, trigger listing, and page-change processing all use this as their common view of registered sources.

*Call graph*: calls 1 internal fn (sources); called by 4 (_member_rows, _member_rows, _binding_named, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_binding_named`  (lines 295–305)

```
async def _binding_named(ext: ExtensionContext | None, name: str) -> _Binding | None
```

**Purpose**: Finds one registered source binding by its derived object name. Both source operations and trigger operations use this because triggers watch source names.

**Data flow**: It requires an extension context, rebuilds all bindings, scans for the binding whose derived name matches the requested name, and returns that binding or nothing.

**Call relations**: Source get, status, apply, delete, resync, and trigger creation all call this when they need the current binding behind a name.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 7 (_apply_owned, _delete_owned, _grant_settled, _member_object, _resync, _status, _apply_owned).


##### `_require_triggers`  (lines 308–309)

```
def _require_triggers(ext: ExtensionContext | None) -> SourceTriggerStore
```

**Purpose**: Creates access to the source-trigger store after confirming the extension context exists. The trigger store is where standing wake-up requests are saved.

**Data flow**: It receives an optional extension context, checks it with _require_ext, wraps it in a SourceTriggerStore, and returns that store.

**Call relations**: Trigger listing, creation, deletion, source deletion cleanup, and page-change delivery all use this to read or update trigger rows.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_delete_owned, _apply_owned, _delete_owned, _find, _member_rows, on_page_change); 1 external calls (__init__).


##### `effective_days`  (lines 312–321)

```
def effective_days(request: int | Literal['all'] | None, declared: int | None) -> int | None
```

**Purpose**: Decides how far back a stream should sync when a source is first registered or re-windowed. It combines the user’s request with the stream’s default backfill window.

**Data flow**: It receives the requested backfill value and the stream’s declared default. A number wins, no request uses the declared default, and “all” or no declared window becomes no cutoff date.

**Call relations**: Source registration uses this to compute initial cutoff dates. Window-widening uses it again to decide whether a later request moves the cutoff earlier or tries to narrow it.

*Call graph*: called by 2 (_apply_owned, _widen_window).


##### `_binding_identity`  (lines 324–334)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool, int | Literal['all'] | None]
```

**Purpose**: Extracts the fields that define whether two source specs describe the same binding state. It is used to tell a harmless re-apply from a real edit.

**Data flow**: It reads provider, sorted streams, account ID, base URL, sharing flag, and backfill setting from a SourceSpec and returns them as one comparable tuple.

**Call relations**: The public apply path and the resync path call this before deciding whether to grant access, schedule a resync, or run the full mutation flow.

*Call graph*: called by 2 (_resync, apply).


##### `SourceObjects.apply`  (lines 363–384)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Implements the top-level apply behavior for source objects. It separates three cases: resyncing, re-applying the exact same source, and actually changing or creating the source.

**Data flow**: It receives the request context, object name, new spec, old visible spec, and generation check. A resync request goes to _resync; an identical visible spec grants the current agent access; everything else is passed to the base object mutation flow.

**Call relations**: This is the public entry for applying source objects. It calls helper paths before falling back to the inherited object machinery, which later calls _apply_owned for real writes.

*Call graph*: calls 3 internal fn (_grant_settled, _resync, _binding_identity).


##### `SourceObjects._grant_settled`  (lines 386–407)

```
async def _grant_settled(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Grants the current agent access to an already-registered source when the agent re-applies the exact same spec. Without this, a no-op apply could look successful but leave the agent without the feed it asked for.

**Data flow**: It reads the current speaker, owner, and binding. If the speaker is allowed to receive the feed, it grants each stream’s source row to the current agent.

**Call relations**: SourceObjects.apply calls this only for identical re-applies. It uses _binding_named and the extension context to make the grant without changing the source itself.

*Call graph*: calls 2 internal fn (_binding_named, _require_ext); called by 1 (apply).


##### `SourceObjects._resync`  (lines 409–433)

```
async def _resync(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None) -> None
```

**Purpose**: Schedules an existing source binding to sync immediately, without changing its definition. It protects credentials by allowing only the registering member or an admin to do this.

**Data flow**: It checks that the request is exactly the current spec except for the resync flag, verifies visibility and permission, looks up the binding, and asks the extension context to schedule all its stream rows for syncing.

**Call relations**: SourceObjects.apply calls this when spec.resync is true. It depends on owner checks, _binding_named, and the extension sync scheduler.

*Call graph*: calls 4 internal fn (speaker_is_admin, _binding_identity, _binding_named, _require_ext); called by 1 (apply); 3 external calls (__init__, __init__, __init__).


##### `SourceObjects._member_rows`  (lines 435–448)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the rows shown when someone lists source objects. Each row represents a whole binding, not the individual stream rows underneath.

**Data flow**: It rebuilds bindings from stored source rows, converts each binding into a name, summary, and owner record, and returns the list of owned rows.

**Call relations**: The inherited object listing flow calls this to supply source-specific rows while the base class applies visibility rules.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `SourceObjects._member_object`  (lines 450–466)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceSpec] | None
```

**Purpose**: Builds the detailed view of one source object. It turns the stored binding into the public spec, timestamps, and explanatory links.

**Data flow**: It looks up the binding by name. If found, it returns an ObjectDetail containing the binding’s spec, creation time, update time, and access links; if not, it returns nothing.

**Call relations**: The inherited object get flow calls this after it has determined the caller may read the object.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (__init__).


##### `SourceObjects._status`  (lines 468–490)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Reports operational status for a source, such as the next sync time and recent error count for each stream. This is the health dashboard for the binding.

**Data flow**: It looks up the binding, reads each stream’s sync schedule, error count, and backfill cutoff, and returns them in a simple dictionary. For private sources, it may include the owner member ID.

**Call relations**: The object status flow calls this when a caller asks for source status after ownership and visibility have been checked.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (subject_shared).


##### `SourceObjects._apply_owned`  (lines 492–596)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Performs the real work of creating or changing a source after permission checks have passed. It validates the provider, streams, URL, account, sharing rules, and backfill rules, then registers, grants, updates, or removes stream rows.

**Data flow**: It receives the desired spec and current owner state. It validates every risky input before writing, resolves the account to use, checks the derived name, optionally widens the backfill window or shares an existing source, registers missing streams, grants kept streams to the agent, and removes dropped streams.

**Call relations**: The base object mutation flow calls this for source changes. It coordinates helpers such as _resolved_account, _validated_base_url, _binding_named, effective_days, and _widen_window, then calls extension APIs to change stored sources.

*Call graph*: calls 6 internal fn (_resolved_account, _widen_window, _binding_named, _require_ext, _validated_base_url, effective_days); 7 external calls (__init__, __init__, now, timedelta, binding_name, member_subject, get).


##### `SourceObjects._widen_window`  (lines 598–672)

```
async def _widen_window(self, ctx: ToolContext, binding: _Binding, *, kept: tuple[_Stream, ...], declared: dict[str, int | None], windowed: frozenset[str], account: str, base_url: str | None, request:
```

**Purpose**: Allows an existing backfill window to move farther into the past, but refuses to make it narrower. This avoids leaving already-synced pages stranded outside the new window without being cleaned up.

**Data flow**: It receives the current binding, the streams still being kept, declared stream windows, and the new request. It computes a new cutoff date for each kept windowed stream, refuses any narrowing, and asks the extension context to update configs and refetch streams whose cutoff moved.

**Call relations**: SourceObjects._apply_owned calls this before making other writes when a binding’s backfill setting changes. It uses effective_days so the same window rules apply during creation and later widening.

*Call graph*: calls 2 internal fn (_require_ext, effective_days); called by 1 (_apply_owned); 3 external calls (__init__, __init__, timedelta).


##### `SourceObjects._delete_owned`  (lines 674–681)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a source binding by removing every stream row that belongs to it. It also removes any triggers that were watching that binding.

**Data flow**: It looks up the binding, removes each stream source ID from the extension context, then asks the trigger store to remove triggers for that source name.

**Call relations**: The inherited delete flow calls this after it has checked that the caller may delete the source.

*Call graph*: calls 3 internal fn (_binding_named, _require_ext, _require_triggers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 683–756)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> _ResolvedAccount
```

**Purpose**: Decides which account or credential a source will use to authenticate with its provider. It prevents ambiguous or unsafe choices, such as using someone else’s connected account or naming an account for a provider that only uses a workspace key.

**Data flow**: It reads connector registry information, active connected accounts, connection ownership, declared credential slots, and fallback support. It returns a resolved account handle plus an optional connection ID, or raises a clear error telling the user what to connect or configure.

**Call relations**: SourceObjects._apply_owned calls this before registering streams, because every source row must store the exact account path that future sync runs will replay.

*Call graph*: calls 4 internal fn (connector_accounts, connector_connection, _require_connectors, _require_ext); called by 1 (_apply_owned); 1 external calls (__init__).


##### `trigger_name`  (lines 759–763)

```
def trigger_name(binding: str, conversation_id: UUID) -> str
```

**Purpose**: Returns the official name for a source trigger. A trigger is identified by the source it watches and the conversation it wakes, so the name is derived from that pair.

**Data flow**: It receives a source binding name and conversation ID, joins them into one stable string, and returns it.

**Call relations**: Trigger listing, lookup, and creation all use this rule so the system refuses duplicate or incorrectly named trigger objects.

*Call graph*: called by 3 (_apply_owned, _find, _member_rows).


##### `SourceTriggerObjects._member_rows`  (lines 794–828)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the rows shown when someone lists source triggers. The rows explain which source is watched, which conversation owns the trigger, who created it, and how delivery works.

**Data flow**: It reads reported triggers, rebuilds source bindings for summaries, looks up creator emails, derives each trigger name, and returns owned rows with useful fields for list filtering and display.

**Call relations**: The inherited object listing flow calls this for source_trigger objects while the base class applies conversation-based visibility.

*Call graph*: calls 4 internal fn (_bindings_from_ext, _require_ext, _require_triggers, trigger_name); 4 external calls (__init__, __init__, owner_emails, subject_shared).


##### `SourceTriggerObjects._member_object`  (lines 830–865)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceTriggerSpec] | None
```

**Purpose**: Builds the detailed view of one source trigger. It shows the public trigger spec and links to the watched source and, for current-conversation delivery, the reporting conversation.

**Data flow**: It finds the trigger by name and generation. If it still matches, it returns an ObjectDetail with source, delivery, timestamps, and relationship links; otherwise it returns nothing.

**Call relations**: The inherited get flow calls this after selecting a visible trigger row. It uses _find to guard against stale generated trigger identities.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `SourceTriggerObjects._status`  (lines 867–881)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Reports readable status fields for a source trigger, such as source, conversation, delivery mode, origin, owner email, and whether it belongs to the current member.

**Data flow**: It finds the trigger, confirms the generation still matches, looks up the creator’s email, and returns a dictionary of display fields.

**Call relations**: The object status flow calls this for trigger status. It shares most of its facts with the trigger list view so different surfaces show the same story.

*Call graph*: calls 1 internal fn (_find); 1 external calls (owner_emails).


##### `SourceTriggerObjects._apply_owned`  (lines 883–920)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceTriggerSpec, old: SourceTriggerSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates a source trigger for the current conversation, or accepts an identical re-apply as a no-op. It refuses edits because a trigger’s identity is the source-conversation pair.

**Data flow**: It derives the expected trigger name from the requested source and current conversation, checks that the source is watchable, creates the trigger row, then re-checks that the source still exists and cleans up if it disappeared during the race.

**Call relations**: The base object mutation flow calls this for trigger applies. It calls _watchable first, uses the trigger store to create the row, and uses _binding_named afterward to protect against source deletion races.

*Call graph*: calls 4 internal fn (_watchable, _binding_named, _require_triggers, trigger_name); 1 external calls (__init__).


##### `SourceTriggerObjects._watchable`  (lines 922–935)

```
async def _watchable(self, ctx: ToolContext, source: str) -> None
```

**Purpose**: Checks whether the requested source can be watched by a trigger. The source must exist, be visible to the caller, and be shared.

**Data flow**: It asks the source object store to get the source under normal visibility rules. If the source is missing it raises an unknown-object error; if it is private it raises a clear refusal; otherwise it returns successfully.

**Call relations**: SourceTriggerObjects._apply_owned calls this before creating a trigger so private or guessed source names cannot become active watches.

*Call graph*: called by 1 (_apply_owned); 1 external calls (__init__).


##### `SourceTriggerObjects._delete_owned`  (lines 937–941)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Deletes one source trigger, but only if the stored trigger still matches the generation the caller intended to delete. This avoids deleting a different trigger after a concurrent change.

**Data flow**: It finds the trigger by object name, compares its stored generation with the owner generation, and removes it from the trigger store if they match.

**Call relations**: The inherited delete flow calls this after permission checks. It uses _find and the trigger store to perform the final removal.

*Call graph*: calls 2 internal fn (_find, _require_triggers).


##### `SourceTriggerObjects._find`  (lines 943–951)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTrigger | None
```

**Purpose**: Finds a reported source trigger by its derived object name. It is the trigger-side equivalent of looking up a source binding by name.

**Data flow**: It lists reported triggers from the trigger store, derives the name for each trigger, and returns the matching row or nothing.

**Call relations**: Trigger get, status, and delete paths call this to locate the current trigger row before building details or removing it.

*Call graph*: calls 2 internal fn (_require_triggers, trigger_name); called by 3 (_delete_owned, _member_object, _status).


##### `on_page_change`  (lines 954–1034)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds when synced pages change by waking conversations that have matching source triggers. It filters carefully so only shared pages readable by the trigger’s agent can cause a notification.

**Data flow**: It receives a hook payload, verifies it is a page-change batch, maps changed source rows back to bindings, groups changes by binding, finds triggers for each binding, filters to shared and authorized changes, writes optional change logs, and invokes the target conversations with alert messages.

**Call relations**: The manifest hook system calls this on page-change events. It coordinates binding lookup, trigger lookup, authorization checks, _write_change_log, and _alert_message before invoking agents.

*Call graph*: calls 4 internal fn (_alert_message, _bindings_from_ext, _require_triggers, _write_change_log); 1 external calls (__init__).


##### `_write_change_log`  (lines 1037–1070)

```
async def _write_change_log(ext: ExtensionContext, conversation_id: UUID, binding: _Binding, latest: str, changes: list[PageChange]) -> str | None
```

**Purpose**: Writes the changed-page list into a conversation file as JSON Lines, meaning one small JSON object per line. This keeps large change details out of the prompt while still making them available to the agent.

**Data flow**: It receives an extension context, conversation ID, binding, timestamp label, and changes. If file storage exists, it writes a deterministic log file under the source’s change-log directory, prunes old files there, and returns the file path; without file storage it returns nothing.

**Call relations**: on_page_change calls this before waking agents. Alert messages can then point the agent to the file instead of listing every changed page inline.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (on_page_change); 1 external calls (dumps).


##### `_disposition`  (lines 1073–1079)

```
def _disposition(change: PageChange) -> str
```

**Purpose**: Classifies a page change as added, updated, or removed. This gives notifications simple words instead of raw database timestamps and tombstone flags.

**Data flow**: It reads a PageChange. Tombstones become removed; otherwise a change whose creation time equals its changed time becomes added; everything else becomes updated.

**Call relations**: _write_change_log uses this for each log line, and _stream_counts uses it to summarize batches.

*Call graph*: called by 2 (_stream_counts, _write_change_log).


##### `_stream_counts`  (lines 1082–1096)

```
def _stream_counts(changes: list[PageChange]) -> str
```

**Purpose**: Summarizes a batch of page changes by stream and change type. For example, it can say that a stream had several additions and updates without listing every page.

**Data flow**: It receives a list of changes, classifies each one with _disposition, counts them per stream, and returns a compact text summary.

**Call relations**: _alert_message calls this to include the main change summary in the wake-up message.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_alert_message); 1 external calls (defaultdict).


##### `_alert_message`  (lines 1099–1118)

```
def _alert_message(binding: _Binding, changes: list[PageChange], log_path: str | None) -> str
```

**Purpose**: Builds the text sent to an agent when a watched source changes. It tells the agent what source changed, what kinds of changes happened, and where to look next.

**Data flow**: It receives the binding, authorized changes, and optional log path. For small batches it names the changed pages directly; for larger batches it points to the log file when available, or tells the agent how to list pages.

**Call relations**: on_page_change calls this immediately before invoking the target conversation. It uses _stream_counts, _page_reference, and the binding summary to make the alert useful.

*Call graph*: calls 3 internal fn (summary, _page_reference, _stream_counts); called by 1 (on_page_change).


##### `_page_reference`  (lines 1121–1125)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: Formats one changed page as an object reference that an agent can fetch. It includes a short title so the reference is easier for a human to recognize.

**Data flow**: It reads the page ID and title from a PageChange, truncates the title when needed, and returns a string like a page object reference plus label.

**Call relations**: _alert_message calls this when a notification has few enough changed pages to list them inline.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 1128–1162)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: Checks and normalizes tenant-specific provider URLs. This prevents unsafe or malformed URLs from being stored as API targets.

**Data flow**: It receives a provider and optional base URL. Providers with fixed hosts must not receive a URL; tenant-based providers must match a strict HTTPS host and path rule with no username, password, port, query, or fragment. It returns the normalized URL or no URL.

**Call relations**: SourceObjects._apply_owned calls this before account resolution and registration so every stored source row has a safe provider endpoint.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).


### Sync adapter and connector contract
Defines the connector interface and adapts provider records into resumable, bounded source sync runs that store searchable pages.

### `core/src/ufo/sources/backend.py`

`orchestration` · `source sync run`

A connector knows how to talk to one outside service, such as a support tool or code host. The rest of the source system does not want to know each connector’s details. This file is the adapter between those worlds. Think of it like a customs desk: records arrive in the connector’s own stream format, and this file stamps each acceptable record into the project’s standard Page format.

The main class, ConnectorBackend, runs one connector stream for one account. It first asks the authentication proxy for a credential, so secrets stay in the right place and are not exposed to the sandbox or agent side. It then chooses the requested stream, resolves the service base URL, asks the connector for pages of records, and converts each record into a recallable Page.

It also decides how the next run should continue. Some streams provide their own checkpoint, which is a saved “resume from here” marker. Others do not, so this adapter stores its own small cursor envelope with a starting point, a skip count, and a watermark. That lets very large backfills happen in bounded slices instead of one unbounded job. Full-snapshot streams used for delete detection are intentionally not capped, because deleting correctly requires seeing the whole collection.

#### Function details

##### `binding_name`  (lines 99–109)

```
def binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: Creates a stable, human-ish name for a connector binding. The name is based on the provider, account, and base URL, so the same connected account gets the same name wherever the system refers to it.

**Data flow**: It receives a provider name, an account identifier, and an optional base URL. It packages those values in a consistent order, hashes them into a short digest, and returns a string like a provider label plus that digest. Nothing outside the function is changed.

**Call relations**: This is a standalone naming helper. Other code can call it when it needs one reliable object name for the same connector account, and it uses hashing and JSON formatting so small identity details produce a repeatable compact name.

*Call graph*: 2 external calls (sha256, dumps).


##### `ConnectorBackend.fetch`  (lines 147–243)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one sync for one connector stream and returns the pages, deletions, snapshot flag, and next cursor that the core sync driver understands. It is the central bridge from connector-style fetching to the project’s standard SyncResult.

**Data flow**: It receives a source row config, the previously saved cursor, and authentication context. It asks the auth proxy for a credential, finds the configured stream, decodes any adapter-owned backfill cursor, and then reads records from the connector. Each record is either skipped because it belongs to an already-processed prefix, converted into a Page, or dropped with a warning if it cannot fit the Page model. While reading, it updates deletion IDs, watermarks, and resume markers. It returns a SyncResult containing accepted pages, provider-reported deletes, whether the run is a full snapshot, and the cursor to save for next time.

**Call relations**: This is the main method the sync runner calls when it wants this backend to fetch data. During the run it calls _stream to find the stream declaration, _decode_cursor to understand any saved tier-2 backfill state, _page to convert records into pages, and _max_str to advance a string watermark. If the run hits the record cap, it either saves the connector’s own checkpoint or creates a backfill envelope and returns early so the next run can continue safely.

*Call graph*: calls 4 internal fn (_decode_cursor, _page, _stream, _max_str); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._stream`  (lines 245–249)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream definition with the requested name inside the connector. A stream definition tells the backend things like the stream’s primary key, cursor field, and whether it is a full snapshot.

**Data flow**: It receives a stream name. It looks through the connector’s declared streams and returns the matching StreamSpec. If no match exists, it raises an error instead of syncing the wrong thing.

**Call relations**: ConnectorBackend.fetch calls this near the start of a sync run. The returned stream specification guides the rest of the run: how records are keyed, whether deletes are authoritative, how cursors advance, and how pages are rendered.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 252–269)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Recognizes whether a stored cursor is one of this adapter’s own backfill envelopes. If it is not, the cursor is treated as opaque connector state and passed through unchanged.

**Data flow**: It receives the saved cursor string or None. If there is no cursor, invalid JSON, or JSON without the reserved ufo_backfill key, it returns None. If the reserved key is present, it validates the contents as an origin, skip count, and watermark. A malformed adapter-owned envelope raises an error, because this file is supposed to be the only writer of that format.

**Call relations**: ConnectorBackend.fetch calls this before reading an incremental stream. The answer decides whether fetch should start from the cursor directly, or re-drive an earlier origin and skip records that were already consumed in a capped previous run.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 271–312)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page | None
```

**Purpose**: Turns one provider record into the project’s standard Page object. If a single record cannot be represented, it warns and drops that record so the whole stream is not blocked forever.

**Data flow**: It receives a stream specification and one raw record. It builds a stable source reference, asks the connector to render a title and body, extracts created and updated timestamps, and tries to construct a Page. On success it returns that Page. On validation failure it logs a warning naming the bad record and returns None.

**Call relations**: ConnectorBackend.fetch calls this for each record that should be landed. _page relies on _record_ref for the stable record key and _record_timestamp for timestamp cleanup, then hands the finished Page back to fetch so it can be included in the SyncResult.

*Call graph*: calls 2 internal fn (_record_ref, _record_timestamp); called by 1 (fetch); 3 external calls (__init__, warn, validation_fault).


##### `_record_timestamp`  (lines 315–346)

```
def _record_timestamp(record: dict[str, Any], field: str | None, *, connector: str, stream: str) -> str | None
```

**Purpose**: Extracts and normalizes a timestamp field from a provider record. It protects the Page model from malformed dates by returning None and warning instead of passing bad timestamp data onward.

**Data flow**: It receives a record, the field path to read, and labels for the connector and stream. If no field is configured or the value is missing, it returns None. If it finds a string or integer timestamp, it asks the sync layer to normalize it into the expected format. If parsing fails, or the value is the wrong kind, it logs a malformed-timestamp warning and returns None.

**Call relations**: ConnectorBackend._page calls this while building Page objects. It uses get_path when the timestamp field is nested inside the record, normalize_page_timestamp to put acceptable values into the standard shape, and warn to report bad provider data without failing the whole sync.

*Call graph*: called by 1 (_page); 3 external calls (warn, get_path, normalize_page_timestamp).


##### `_record_ref`  (lines 349–353)

```
def _record_ref(stream: StreamSpec, record: dict[str, Any]) -> str
```

**Purpose**: Creates the stable per-record reference used in page IDs and delete IDs. This lets a re-fetched record, an update, and a deletion all point to the same stored page.

**Data flow**: It receives a stream specification and a raw record. It first tries to read the stream’s declared primary key and returns it if it is a string or integer. If the primary key is missing or unusable, it hashes the whole record in a consistent JSON order and returns that hash as a fallback reference.

**Call relations**: ConnectorBackend._page calls this before constructing a Page. The reference it returns becomes part of the page’s source_ref, which is how later syncs recognize the same external record again.

*Call graph*: called by 1 (_page); 2 external calls (sha256, dumps).


##### `_max_str`  (lines 356–361)

```
def _max_str(current: str | None, value: Any) -> str | None
```

**Purpose**: Advances a string watermark only when a new value is greater than the current one. A watermark is a saved progress marker, often a timestamp-like string, used to resume incremental syncing.

**Data flow**: It receives the current watermark and a candidate value from a record. If the candidate is not a string, it leaves the current watermark unchanged. If there is no current watermark or the candidate sorts later, it returns the candidate. Otherwise it returns the original current value.

**Call relations**: ConnectorBackend.fetch calls this while reading records from a stream with a cursor field. The updated watermark becomes part of the next cursor, either directly after a completed run or inside the adapter’s backfill envelope during a capped run.

*Call graph*: called by 1 (fetch).


### `core/src/ufo/sources/connector.py`

`domain_logic` · `source sync`

A connector is the project’s standard plug shape for outside services like document stores, email, chats, or code hosts. Without this file, each provider would invent its own way to describe streams, fetch pages, remember progress, and turn records into readable content, making the sync system hard to drive consistently.

The file starts by defining small value objects. A StreamSpec says what collection can be synced, which field identifies each record, whether the stream is a full snapshot or incremental, and how pagination or ordering works. StreamPage is a batch of records, optionally with deleted record IDs and a cursor for resuming later. Pagination and Ordering give shared names to common provider behaviors.

The most involved piece is PartitionWalk. Some services are not one long list; they are many lists, like one feed per channel or repository. PartitionWalk is the careful notebook that remembers where each separate list got to. It stores that notebook as JSON in the one cursor slot the rest of the system understands. It supports oldest-first streams, newest-first streams, and streams with no usable ordering field.

Finally, Connector is an abstract base class, meaning a template that real connectors must fill in. It requires connectors to list their streams and fetch pages. It also supplies a default render method that turns a raw record into a title plus JSON body, while content-heavy connectors can override it with friendlier prose.

#### Function details

##### `PartitionWalk.stream`  (lines 233–340)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This function walks through all partitions of a stream and yields sync pages while updating a per-partition resume cursor. It is used when one logical stream is really many smaller streams, such as one channel, repository, or folder at a time.

**Data flow**: It starts with an incoming cursor string, decodes it into a map of partition names to saved progress, then asks for the current partitions. For each partition, it decides the right boundary to fetch from: start fresh, resume after a known point, continue a newest-first backfill, or skip a finished unordered partition. As pages arrive from the connector’s page factory, it updates the checkpoint map, wraps the records and deletes into StreamPage objects, and includes an encoded cursor so a later run can resume. At the end, it cleans up cursors for partitions that disappeared or, for unordered walks, clears temporary pass markers.

**Call relations**: This is the main driver for partitioned syncs in this file. It calls PartitionWalk._decode at the beginning to read saved progress and PartitionWalk._encode whenever it needs to attach updated progress to an outgoing StreamPage. It creates PartitionBound values to tell the page factory what slice to fetch, and it emits StreamPage values for the rest of the sync pipeline to consume. If a partition is skipped by the provider, it preserves that partition’s saved state and moves on.

*Call graph*: calls 2 internal fn (_decode, _encode); 3 external calls (__init__, __init__, __init__).


##### `PartitionWalk._decode`  (lines 343–372)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: This helper reads the saved cursor for a partitioned walk and turns it back into an in-memory map. It protects the sync from silently accepting malformed progress data that this walker itself would have written.

**Data flow**: It receives a cursor string, which may be empty, invalid JSON, an old-style plain watermark, or the JSON object used by PartitionWalk. Empty, non-JSON, and non-object values become an empty map, meaning the walk starts over for its partitions. A valid JSON object is checked entry by entry: strings become simple watermarks, and objects must validate as backfill windows with high and until bounds. Bad entries raise an error instead of being ignored.

**Call relations**: PartitionWalk.stream calls this once at the start of a walk so it knows what each partition has already synced. This helper uses JSON parsing to translate the stored text form into structured state that stream can reason about.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 375–380)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: This helper turns the per-partition progress map into a stable JSON cursor string. That cursor is what lets a future sync run continue from the same place.

**Data flow**: It receives a map whose values are either plain watermark strings or window objects that mark an unfinished newest-first backfill. It converts any window objects into ordinary dictionaries, then serializes the whole map as sorted JSON text. The output is a string suitable to store as the stream’s next cursor.

**Call relations**: PartitionWalk.stream calls this whenever it yields a StreamPage with updated progress. In the bigger flow, _encode is the counterpart to _decode: one writes the notebook, the other reads it back.

*Call graph*: called by 1 (stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 394–395)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This abstract method tells the system which streams a connector can provide. A real connector implements it to return descriptions such as “messages,” “documents,” or “repositories,” each with its identifying fields and sync behavior.

**Data flow**: There is no fixed input beyond the connector instance. The concrete connector returns a list of StreamSpec objects, and those specs become the menu of source-side collections that can be registered and synced.

**Call relations**: ConnectedSources._register calls this when setting up connected sources, so it can discover and register the streams offered by a connector. In this base class it is only a required contract; provider-specific subclasses supply the actual list.

*Call graph*: called by 1 (_register).


##### `Connector.fetch_page`  (lines 398–413)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None) -> AsyncIterator[list[dict[str, Any]]
```

**Purpose**: This abstract method is the standard way a connector fetches records from an outside service. Real connectors implement it to produce records page by page, starting from a saved cursor and using the resolved credential and base URL.

**Data flow**: It receives a stream description, an optional cursor, authentication information, a base URL, an optional current user ID to exclude where relevant, and an optional backfill floor date. The implementation contacts or otherwise reads from the provider, groups records into pages, and yields either plain lists of record dictionaries or StreamPage objects that can also report deletes and a provider cursor.

**Call relations**: This base method defines the contract that concrete connectors follow during sync. Although this function list shows no direct caller for the abstract method itself, the surrounding connector system relies on implementations of this method to supply the pages that are later rendered and stored.


##### `Connector.render`  (lines 415–434)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This method turns one raw provider record into a human-readable title and body for recall or search. It gives every connector a safe default, while allowing content-focused connectors to override it with richer text.

**Data flow**: It receives a record dictionary and the stream it came from. It first looks for a useful title-like field, such as title, name, login, or subject. If none exists, it falls back to the record’s primary key and builds a title like stream/id; if even that is missing or empty, it raises an error because the record cannot be identified. It returns a pair: the title and a Markdown-style body containing a heading plus the record serialized as sorted JSON.

**Call relations**: This method uses JSON serialization to make the raw record readable and stable in the default page body. Concrete connectors can rely on this default when raw JSON is good enough, or override it when the provider record contains content such as an email body or document text that should be rendered as prose.

*Call graph*: 1 external calls (dumps).


### Pages and connector registry
Defines the read-only synced page object and the registry that maps source names to connector implementations.

### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `request handling`

A page here is not something a user writes directly. It is a document brought into the workspace by a background content-sync driver from a registered source. This file gives those synced documents a safe object interface: users can browse them, read them, and admins can delete them in the special sense of “forgetting” them.

The main idea is like a library catalogue. The sync driver stocks the shelves, while this file lets people look up what is available and read a limited preview of the book. Users cannot add or edit books through this interface, because that would conflict with the source system and the sync process.

The file defines the shape of a page’s public data with PageSpec. It includes where the page came from, its title, timestamps, visibility subject, content digest, blob reference, and a bounded body. The internal _Page class turns raw stored records into object names, summaries, links back to the source, and public fields.

PageObjects is the object-store implementation. Listing pages reads only metadata that the caller is allowed to see. Getting a page also reads its body from blob storage, stopping at 65,536 UTF-8 bytes so a huge document cannot overwhelm the response. Before returning the body, it rechecks that the page has not changed since the metadata was read. Deleting requires an admin and calls the extension’s forget_page path so the rest of the system can clean up derived index data.

#### Function details

##### `_require_ext`  (lines 62–65)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This function makes sure the tool request has an ExtensionContext attached. The extension context is the gateway to source-page operations, so page objects cannot work safely without it.

**Data flow**: It receives a ToolContext. If that context contains an extension context, it returns it. If not, it stops the request with a RuntimeError, because the page object code would otherwise have no way to read sources, read pages, or forget pages.

**Call relations**: PageObjects._pages, PageObjects.get, and PageObjects.delete call this before using extension-only abilities. It acts as the entry check before those flows reach the source-page store or the forget-page operation.

*Call graph*: called by 3 (_pages, delete, get).


##### `_page_timestamp`  (lines 68–78)

```
def _page_timestamp(provider_value: str | None, row_value: datetime) -> str
```

**Purpose**: This function turns a page timestamp into a consistent UTC timestamp string. It accepts either a provider-supplied timestamp or, if that is missing, the workspace row’s own timestamp.

**Data flow**: It receives an optional timestamp string from the external provider and a datetime from the local row. If the provider value is missing, it uses the row value and assumes UTC when needed. If the provider value exists, it parses it and rejects it if it has no timezone. The output is an ISO-formatted UTC timestamp with microsecond precision.

**Call relations**: _Page.spec and _Page.fields use this whenever they expose created_at or updated_at. That keeps list output and detailed get output using the same timestamp rules.

*Call graph*: called by 2 (fields, spec); 2 external calls (fromisoformat, replace).


##### `_Page.name`  (lines 99–100)

```
def name(self) -> str
```

**Purpose**: This property gives a page its object name. The name is simply the page row’s UUID written as text.

**Data flow**: It reads the _Page id value. It converts that UUID into a string. The result is used as the public object name that callers pass to get or delete a page.

**Call relations**: PageObjects.list uses the name when building rows for browsing, and PageObjects._find compares this name with the requested name. It is the bridge between the stored page identity and the object API’s naming system.


##### `_Page.links`  (lines 102–110)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: This function describes the relationship between a page and the source that synced it. If the source has a known object name, it creates a link saying the page was synced by that source.

**Data flow**: It reads the page’s source_name. If there is no source_name, it returns an empty set of links. If there is one, it builds an ObjectLink pointing to the corresponding source object and returns it.

**Call relations**: PageObjects.get includes these links in the returned ObjectDetail. This lets a reader move from a page back to the registered source that produced it.

*Call graph*: 2 external calls (__init__, __init__).


##### `_Page.spec`  (lines 112–125)

```
def spec(self, body: str, body_truncated: bool) -> PageSpec
```

**Purpose**: This function builds the full public description of a page, including its body. It is used when someone asks to get one page in detail.

**Data flow**: It receives the already-read body text and a flag saying whether that body was cut short. It combines those with the page’s metadata, normalizes the created and updated timestamps, and returns a PageSpec object for the API response.

**Call relations**: PageObjects.get calls this after reading and validating the page body. It hands the result into ObjectDetail so the caller receives both metadata and the bounded body content.

*Call graph*: calls 1 internal fn (_page_timestamp); 1 external calls (__init__).


##### `_Page.summary`  (lines 127–128)

```
def summary(self) -> str
```

**Purpose**: This function creates a short, human-readable line for browsing page lists. It shows the title, source provider, stream, and visibility subject.

**Data flow**: It reads the page title, backend, stream, and subject. It formats them into one sentence-like string and trims it to the configured summary length. The result is used as the page’s list summary.

**Call relations**: PageObjects.list uses this while building ObjectRow entries. It gives the list view enough context to recognize a page without opening it.


##### `_Page.fields`  (lines 130–138)

```
def fields(self) -> dict[str, JsonValue]
```

**Purpose**: This function exposes the page metadata that can be shown, filtered, or ordered in list views. It deliberately leaves out the body, because listing should stay lightweight.

**Data flow**: It reads the page’s source id, backend, stream, title, and timestamps. It normalizes the timestamps and returns a dictionary of simple JSON-compatible values. That dictionary becomes the row fields shown by the object list API.

**Call relations**: PageObjects.list calls this for every visible page. It relies on _page_timestamp so list fields use the same time format as detailed page reads.

*Call graph*: calls 1 internal fn (_page_timestamp).


##### `PageObjects.list`  (lines 149–154)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This function lists synced pages that the caller is allowed to see. It returns compact rows, not full page bodies, so browsing stays fast and safe.

**Data flow**: It receives the tool context and a list query, such as filters or ordering. It asks _pages for the visible page records, turns each one into an ObjectRow with a name, summary, and fields, then passes those rows through object_page so the query rules are applied. The result is an ObjectPage for the caller.

**Call relations**: This is the list operation for PAGE_OBJECT. It calls PageObjects._pages to gather the allowed pages, then hands the rows to the common object paging helper.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, object_page).


##### `PageObjects.get`  (lines 156–201)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None
```

**Purpose**: This function returns one synced page in detail, including a safely bounded body. It also checks that the page did not change while it was being read.

**Data flow**: It receives the context and page name. It finds the matching visible page, then reads the page body from blob storage in chunks, stopping once it has just enough data to know whether the body is over the size limit. It decodes the bounded bytes as UTF-8 text, trimming an incomplete final character if necessary. Then it rereads the current page state through the source reader and compares key fields. If the page disappeared or changed, it returns None; otherwise it returns an ObjectDetail with the spec, timestamps, and links.

**Call relations**: This is the get operation for PAGE_OBJECT. It first calls PageObjects._find, uses the blob reader from the context for the body, calls _require_ext to recheck current readable state, and finally uses _Page.spec and _Page.links to build the response.

*Call graph*: calls 3 internal fn (source_reader, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects.status`  (lines 203–210)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This function reports no separate status for page objects. Synced pages are read-only here, so there is no apply operation to track.

**Data flow**: It receives the context, name, and optional expected generation. It ignores them and returns None. Nothing is changed.

**Call relations**: This fills the object-kind status slot for PAGE_OBJECT. Unlike object kinds that run long updates or deployments, pages do not expose a status lifecycle here.


##### `PageObjects.apply`  (lines 212–221)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function blocks attempts to create or update pages through the object API. Pages must come from the sync driver, not from direct user edits.

**Data flow**: It receives the desired page name, new spec, optional old spec, and optional generation check. Instead of saving anything, it raises VerbNotSupported with a message explaining that pages are synced from sources. No page data is changed.

**Call relations**: This is the create/update path for PAGE_OBJECT, but it always refuses the request. It protects the larger sync flow by making the external source and sync driver the only writers of page content.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 223–235)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function forgets a synced page, but only for workspace admins. Forgetting tombstones the page so the existing page-change cleanup pipeline can remove derived index data.

**Data flow**: It receives the context, page name, and optional generation check. It first asks whether the speaker is an admin. If not, it raises AdminRequired. If the speaker is an admin, it finds the named page; if there is no such page, it raises a ValueError. Otherwise it calls forget_page with the page id. The visible page is then marked for forgetting by the extension layer.

**Call relations**: This is the delete operation for PAGE_OBJECT. It calls ToolContext.speaker_is_admin for the permission gate, PageObjects._find to resolve the name to a page id, and _require_ext to reach the extension’s forget_page operation.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 237–238)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This helper finds one visible page by its object name. It keeps get and delete from duplicating the same search logic.

**Data flow**: It receives the context and a name string. It asks _pages for all pages visible to the caller, compares each page’s public name to the requested name, and returns the first match. If nothing matches, it returns None.

**Call relations**: PageObjects.get uses this before reading a body, and PageObjects.delete uses it before forgetting a page. It depends on PageObjects._pages so permission and visibility filtering happen before a page can be found.

*Call graph*: calls 1 internal fn (_pages); called by 2 (delete, get).


##### `PageObjects._pages`  (lines 240–268)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This helper gathers the live synced pages the caller is allowed to read and enriches them with source information. It turns lower-level source-page records into _Page objects used by the rest of this file.

**Data flow**: It receives the tool context. It gets the ExtensionContext, reads all registered sources, builds a lookup from source id to backend name, and builds source object names for recognized connector sources. It then asks the extension for source pages using the caller’s source_reader, which represents the caller’s read permissions. For each returned record, it creates an _Page containing page metadata, source metadata, and the optional source object name. The output is a tuple of _Page objects.

**Call relations**: PageObjects.list calls this to build browse rows, and PageObjects._find calls it to search by page name. It calls _require_ext for extension access, ToolContext.source_reader for permission-scoped reading, ConnectorSourceConfig.model_validate to understand connector source configuration, and binding_name to produce links back to source objects.

*Call graph*: calls 2 internal fn (source_reader, _require_ext); called by 2 (_find, list); 3 external calls (__init__, model_validate, binding_name).


### `extensions/sources/ufo_ext_sources/registry.py`

`config` · `startup`

This file works like a phone book for source integrations. Each imported connector class knows how to read data from one outside service, such as GitHub, Slack, Stripe, or Google Drive. The rest of the system needs a simple way to say “use the Slack connector” or “use the Airtable connector” without searching through every connector module at runtime.

Instead of automatically scanning files, this registry is explicit: adding a new source provider means importing its connector class here and adding it to the tuple used to build `CONNECTORS`. That makes startup predictable and avoids hidden import-time discovery work.

The key idea is that every connector class has a `name`. That name becomes the lookup key in the `CONNECTORS` dictionary. The same name is also used elsewhere as the source backend name and as the credential slot where the direct backend finds the right secret or API key. In other words, the name is the shared label that ties together configuration, credentials, and the connector implementation.

The file also protects against a subtle but serious mistake: two connectors using the same name. If that happens, one would silently replace the other in a normal dictionary. `_connector_registry` catches this and raises an error instead, so the problem is found early.

#### Function details

##### `_connector_registry`  (lines 61–69)

```
def _connector_registry(connector_types: tuple[type[Connector], ...]) -> dict[str, type[Connector]]
```

**Purpose**: Builds the connector lookup table from a fixed list of connector classes. It also checks that every connector has a unique name, so the system never has to guess which connector a backend name refers to.

**Data flow**: It receives a tuple of connector classes. It reads the `name` value from each class, adds an entry from that name to the class, and stops with an error if the same name appears twice. The result is a dictionary where a source backend name can be used to find the correct connector class.

**Call relations**: This function is used when the module is loaded to create the module-level `CONNECTORS` registry. The rest of the source-sync system can then look up connector classes from `CONNECTORS` by backend name, instead of importing or discovering connector modules itself.
