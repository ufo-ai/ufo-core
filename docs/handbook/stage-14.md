# Source ingestion, page sync, indexing, and memory building  `stage-14`

This stage is the system’s knowledge intake and memory workshop. It runs mostly behind the scenes during setup and ongoing sync. When a member connects an account, the connected helper creates the needed feeds, and can repair missed setup later. The connector rules define the shared language every source must use. The REST helper handles ordinary web API calls, including sign-in, retries, and paging. The backend turns provider records into standard pages, deletes, cursors, and warnings. The sync engine stores page bodies, notices changes, and passes updated pages onward.

Around this core, many adapters bring in data from Markdown folders and GitHub, Google and Microsoft, workplace tools, CRM and support systems, HR tools, finance services, forms, documents, and seeded evaluation data. All of them feed the same pipeline.

After pages arrive, the indexing parts split text into smaller searchable chunks, create embeddings, and store them in the default database index or Turbopuffer. The memory parts then recall useful facts, extract lasting notes, merge duplicates, and keep long-term knowledge tidy.

## Sub-stages

- [Local and repository Markdown sources](stage-14.1.md) `stage-14.1` — 3 files
- [Search chunking and index backends](stage-14.2.md) `stage-14.2` — 3 files
- [Memory storage, recall, and consolidation](stage-14.3.md) `stage-14.3` — 4 files
- [Google and Microsoft source connectors](stage-14.4.md) `stage-14.4` — 10 files
- [Work, engineering, and collaboration connectors](stage-14.5.md) `stage-14.5` — 14 files
- [CRM, support, marketing, and social connectors](stage-14.6.md) `stage-14.6` — 12 files
- [HR and recruiting connectors](stage-14.7.md) `stage-14.7` — 6 files
- [Finance, billing, commerce, and document connectors](stage-14.8.md) `stage-14.8` — 12 files

## Files in this stage

### Feed bootstrap and connector ingestion
Account connection hooks create default feeds, while connector utilities define and adapt provider data into UFO’s standard source records.

### `extensions/sources/ufo_ext_sources/connected.py`

`domain_logic` · `connection hook and periodic retry`

When a member connects an outside service, the system records that the account is connected. But the content from that account is stored separately as source rows, one row per stream of content. Without this file, a member could connect an account and still not get its main content feeds unless another step created those rows.

The file closes that gap. Right after a connection is recorded, it looks up the connector for that provider and creates sources for the connector’s canonical streams. “Canonical” means the main streams the connector exists to bring in, not every extra list or endpoint the provider offers. Each new source is private to the member who owns the connection, and it is granted to the main agent by the normal source registration behavior.

The same logic is also used by a retry job. If the hook failed, a process died, or a source grant arrived another way, the retry job checks all main-agent connections and creates only the missing source rows.

The careful part is that it does not blindly recreate things. If a source row already exists, it leaves it alone. If the member removed that source before, it stays removed. If any existing row for the connection is already shared with a different subject, this file backs away rather than mixing private automatic rows into a shared binding. It also skips providers that need a tenant-specific URL, because only the member can supply that.

#### Function details

##### `on_connection_recorded`  (lines 51–58)

```
async def on_connection_recorded(ctx: HookContext) -> HookOutcome
```

**Purpose**: This is the hook that runs immediately after an account connection is recorded. Its job is to give that new connection its default source rows before the connect flow finishes for the member.

**Data flow**: It receives a hook context containing an event payload and an extension context. If the payload says a connection was recorded, it takes the connection ID, builds a ConnectedSources helper around the extension context, and asks it to register sources for that one connection. If the payload is not the expected kind, it raises an error because this hook was called for the wrong event. It returns no special outcome when registration is done.

**Call relations**: The connect flow calls this hook after saving a connection. This function is only the front door: it checks that the event is the right one, creates ConnectedSources, and hands the real work to its register method for the specific connection.

*Call graph*: 1 external calls (__init__).


##### `retry_connected_sources`  (lines 61–63)

```
async def retry_connected_sources(ctx: ExtensionContext) -> None
```

**Purpose**: This is the safety-net job for connections whose automatic source creation did not happen the first time. It scans existing main-agent connections and fills in any missing default source rows.

**Data flow**: It receives the extension context for the running job. It builds a ConnectedSources helper and calls register without naming a single connection, which means the helper considers every relevant main-agent connection. It returns nothing; its effect is any source rows it creates.

**Call relations**: A scheduled or background retry flow calls this when it wants to repair missing connected-account sources. Like the hook, it delegates the detailed checking and creation work to ConnectedSources.register, but it asks for the broad all-connections path instead of one just-created connection.

*Call graph*: 1 external calls (__init__).


##### `ConnectedSources.register`  (lines 74–82)

```
async def register(self, connection_id: UUID | None=None) -> None
```

**Purpose**: This method decides which connected accounts should get automatic source rows. It can work on one connection, for the immediate hook, or on all connections, for the retry job.

**Data flow**: It first reads the current live source rows so it can avoid duplicating or disturbing them. Then it asks for the connections held by the main agent. For each connection, it optionally filters down to the requested connection ID, finds the connector class for that provider, and skips providers it cannot register safely, such as tenant-specific providers with no base URL. For each suitable connection, it creates a connector instance and passes the connection, connector, and current source list to _register. Its output is not a return value; the result is that missing source rows may be added.

**Call relations**: Both entry paths in this file call this method: on_connection_recorded calls it for one fresh connection, and retry_connected_sources calls it for all connections. It uses the connector registry to understand each provider, reads main-agent connections, and then hands each eligible connection to ConnectedSources._register for the stream-by-stream work.

*Call graph*: calls 1 internal fn (_register); 2 external calls (main_agent_connections, get).


##### `ConnectedSources._register`  (lines 84–129)

```
async def _register(self, connection: MainAgentConnection, connector: Connector, live: tuple[SourceRecord, ...]) -> None
```

**Purpose**: This method creates the missing canonical source rows for one connected account, while respecting existing rows and past removals. It is the part that turns a connection into actual feed entries.

**Data flow**: It receives one connection, the connector for that provider, and the current live source records. It builds the member subject for the connection owner, then finds any existing rows already bound to that connection. If any of those rows belongs to another subject, it stops, because automatic private rows should not be mixed into an already shared binding. If there is an existing bound row, it reads that row’s source configuration to reuse its requested backfill setting. It then checks every stream exposed by the connector, keeps only canonical streams, calculates how far back the first sync should reach, and builds a source configuration for that stream. For each would-be source, it computes the stable source ID and skips it if the source already exists. Before creating anything, it asks which of the missing IDs were previously removed, and skips those too. Finally, it registers each remaining source as private to the connection owner and tied to the connection.

**Call relations**: ConnectedSources.register calls this after it has found an eligible connection and connector. This method calls into the connector to learn its streams, uses effective_days to combine a requested backfill with the stream’s own limit, uses member_subject to identify the owner’s private subject, and finally uses the extension context to register only the rows that are truly new and not deleted.

*Call graph*: calls 1 internal fn (streams); called by 1 (register); 6 external calls (__init__, model_validate, now, timedelta, member_subject, effective_days).


### `core/src/ufo/runtime/sources/backend.py`

`orchestration` · `source sync run`

A connector knows how to talk to one outside service, such as a ticketing or code-hosting provider, and return records from one stream of data. The rest of the system expects a simpler shape: one sync run should produce a bundle of internal Pages, a next-place-to-resume marker, and any records that should be deleted. ConnectorBackend is the adapter that makes those two worlds fit together.

For each source row, it finds the right stream, asks the authentication proxy for a Credential, calls the connector, and converts each provider record into a recallable Page. If a record has no usable primary key, or the Page model rejects it, the backend does not fail the whole run. It warns, counts the record as dropped, and keeps going. This prevents one bad provider row from blocking every later row.

The file also protects sync jobs from running forever. Incremental streams are capped at a fixed number of stored records per run. If the connector provides its own checkpoint, the backend uses it. If not, the backend stores its own small “backfill envelope” in the cursor: where it started, how many records were already consumed, and the best timestamp watermark seen so far. On the next run it replays from the same origin and skips ahead. Full snapshot streams are different: they are never capped, because the system must see the complete collection before safely tombstoning missing records.

#### Function details

##### `binding_name`  (lines 101–111)

```
def binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: Creates a stable, human-safe name for a connector binding from the provider, account, and optional tenant base URL. Someone would use this when the same connected account needs to be referred to consistently across source rows, UI actions, and stored pages.

**Data flow**: It takes a provider name, an account identifier, and maybe a base URL. It turns those values into sorted JSON, hashes that text with SHA-256, keeps a short digest, replaces underscores in the provider name with dashes, and returns a name like provider-digest. The original inputs are not changed.

**Call relations**: This is a standalone naming helper for the connector source layer. It relies on JSON formatting and hashing so the same identity always produces the same short name, while different accounts or tenant URLs get different names.

*Call graph*: 2 external calls (sha256, dumps).


##### `ConnectorBackend.fetch`  (lines 149–256)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one sync for one connector stream and returns the system’s standard SyncResult. It is the main workhorse that obtains credentials, reads provider records, converts good records into Pages, records deletions, advances the cursor, and stops safely when an incremental run reaches its cap.

**Data flow**: It receives a source config, the previous cursor, and authentication context. First it asks the auth proxy for a Credential, finds the requested stream, resolves the base URL, and interprets the cursor if it is one of this backend’s backfill envelopes. Then it calls the connector to fetch pages of records. Each record is either skipped because it belongs to an already-consumed prefix, converted into an internal Page, or counted as dropped if it cannot be represented. Along the way it gathers deletes, tracks a watermark from the stream’s cursor field, and watches for the run-size cap. It returns a SyncResult containing stored pages, the next cursor, deletes, whether this was a full snapshot, and the dropped-record count.

**Call relations**: This function drives the whole flow. It calls _stream to choose the stream, _decode_cursor to understand UFO-owned resume state, _page to turn provider records into Pages, and _max_str to advance a text watermark. If the grant cannot be used, it raises StreamSkipped so the wider sync runner can treat the stream as waiting for authorization instead of broken. When a capped run needs to resume later, it creates a _BackfillEnvelope and serializes it as JSON for the next fetch call.

*Call graph*: calls 5 internal fn (_decode_cursor, _page, _stream, _max_str, __init__); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._stream`  (lines 258–262)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream definition with the requested name inside the connector. This lets a source row say “sync this one stream” and turns that name into the StreamSpec that describes how the stream behaves.

**Data flow**: It receives a stream name. It loops over the connector’s declared streams and returns the one whose name matches. If no stream matches, it raises an error saying the connector does not have that stream.

**Call relations**: ConnectorBackend.fetch calls this near the start of a sync run, before any provider request is made. The returned StreamSpec controls later choices such as which primary key to use, whether missing records mean deletes, and whether cursor or timestamp fields exist.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 265–282)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Checks whether a stored cursor is one of this backend’s special backfill envelopes. If it is, it converts it back into a validated object; if it is not, it leaves the cursor to be treated as ordinary connector state.

**Data flow**: It receives a cursor string or None. None, invalid JSON, JSON that is not an object, or JSON without the reserved ufo_backfill key all produce None. If the reserved key is present, it validates the contents as an origin, skip count, and watermark. A malformed reserved envelope raises an error, because only this backend is supposed to write that format.

**Call relations**: ConnectorBackend.fetch calls this before starting an incremental stream. When it returns an envelope, fetch knows to replay from the envelope’s origin and skip records already consumed. When it returns None, fetch passes the original cursor through as opaque connector-owned state.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 284–337)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page | None
```

**Purpose**: Turns one raw provider record into one internal Page, or decides that the record must be dropped. It protects the sync from being blocked by a single bad record while still warning operators that data was lost.

**Data flow**: It receives a stream definition and a provider record. It asks the connector for the record’s stable identity and display reference; if there is no identity, it warns and returns None. Otherwise it asks the connector to render a title and body, extracts created and updated timestamps, and builds a Page whose source identity is based on stream name plus record identity. If Page validation fails, it warns with the validation fault and returns None. A successful conversion returns the Page.

**Call relations**: ConnectorBackend.fetch calls this for every record that is not skipped by a backfill envelope. _page calls _record_timestamp for date fields, uses the Page model to enforce the internal shape, and sends warnings through the observability system when a record cannot be safely stored.

*Call graph*: calls 1 internal fn (_record_timestamp); called by 1 (fetch); 3 external calls (__init__, warn, validation_fault).


##### `_record_timestamp`  (lines 340–371)

```
def _record_timestamp(record: dict[str, Any], field: str | None, *, connector: str, stream: str) -> str | None
```

**Purpose**: Extracts and normalizes one timestamp field from a provider record. It gives Pages consistent timestamp strings while tolerating missing or malformed provider data.

**Data flow**: It receives a record, a field name or None, and connector/stream names for warning messages. If there is no field name, or the field value is missing or None, it returns None. If the field is present, it reads either a direct key or a nested path, accepts string timestamps and non-boolean integers, and passes them through normalize_page_timestamp. If normalization fails or the value has an unsupported type, it warns and returns None.

**Call relations**: ConnectorBackend._page calls this for the stream’s created-at and updated-at fields while building a Page. It delegates nested lookup to get_path, timestamp formatting to normalize_page_timestamp, and warning emission to the observability layer.

*Call graph*: called by 1 (_page); 3 external calls (warn, get_path, normalize_page_timestamp).


##### `_max_str`  (lines 374–379)

```
def _max_str(current: str | None, value: Any) -> str | None
```

**Purpose**: Keeps the larger of two string cursor values. It is used as a simple watermark helper for streams whose progress is represented by sortable text, such as timestamp strings.

**Data flow**: It receives the current watermark and a new value from a record. If the new value is not a string, it returns the current watermark unchanged. If there is no current watermark, or the new string sorts after the current one, it returns the new string. Otherwise it returns the current string.

**Call relations**: ConnectorBackend.fetch calls this while reading records from a stream with a cursor field. The resulting watermark can become the next cursor at the end of an uncapped run, or be stored inside a backfill envelope when a capped run must resume later.

*Call graph*: called by 1 (fetch).


### `core/src/ufo/runtime/sources/rest.py`

`io_transport` · `request handling during source sync reads`

Many outside services do not return all records in one reply. They return one page at a time, may ask the caller to slow down, and may use different ways to say where the next page is. This file keeps every REST connector from having to rewrite that same careful plumbing.

The main class, RestConnector, is like a reusable delivery truck for API data. A specific connector supplies the destination details: the base web address, the available streams of records, and sometimes special pagination rules. RestConnector builds an asynchronous HTTP client, meaning it can wait for the network without freezing other work. It sends GET or read-only POST requests, checks errors, retries temporary failures such as rate limits and server errors, and gives up safely if a service keeps looping forever.

It also normalizes the shape of results. Pages must be lists of dictionary-like records, and each record can be flattened before the sync system writes it onward. The file supports several common pagination styles: next cursor tokens, Link headers, offset and limit, page numbers, and Microsoft OData next links. Without this file, each connector would have to solve authentication, retries, pagination safety, and record validation on its own, which would make bugs and inconsistent behavior much more likely.

#### Function details

##### `list_or_empty`  (lines 50–54)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: This small helper safely turns a value into a list of records. It only keeps items that are dictionaries, because downstream sync code expects each record to be a dictionary-shaped object.

**Data flow**: It receives any value. If the value is not a list, it returns an empty list; if it is a list, it filters out anything that is not a dictionary and returns the remaining records.

**Call relations**: Pagination helpers use this when reading API responses so that badly shaped or unexpected data does not flow forward as records. It is used by record extraction, raw response list parsing, and the OData page reader.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 57–60)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This helper safely treats a value as a single record-like dictionary. It is useful when a connector reaches into nested API data and wants a harmless empty object if the expected object is missing.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: This is a standalone shaping helper for connector code that needs it. It is not called inside this file, but it belongs with the other safe record-shaping functions.


##### `records_at`  (lines 63–68)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: This helper pulls a list of records out of an API response, optionally from a nested path. It lets pagination code say, for example, 'the records live under data.items' without duplicating path-walking logic.

**Data flow**: It receives a response-like value and an optional path. With no path, it treats the whole value as the list; with a path, it first walks into the mapping at that path, then returns only dictionary items from the list found there.

**Call relations**: Several pagination loops call this after receiving JSON. The small parser inside link-header pagination also uses it when a stream declares that records are nested inside a response body.

*Call graph*: calls 1 internal fn (list_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, parse); 1 external calls (get_path).


##### `_int_or_none`  (lines 71–76)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: This helper reads a value as an integer only when that is clearly safe. It avoids guessing when an API returns something that is not a clean whole number.

**Data flow**: It receives any value. It returns the integer if the value is already an integer, or if it is a string made only of digits; otherwise it returns nothing.

**Call relations**: Offset-based pagination uses this when an API reports the page size it actually applied. That lets the next request advance by the server's real page size when available.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 79–82)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: This helper copies records and stamps extra context onto each one, such as a parent ID or cloud account ID. That helps later steps know where a record came from.

**Data flow**: It receives many record dictionaries and named context values. It returns a new list where each record is copied and combined with the context fields, leaving the original records untouched.

**Call relations**: This is a convenience for provider-specific connectors, especially those that fan out from one object to child objects. It is not used by the base class itself.


##### `next_link`  (lines 85–90)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper finds the 'next page' URL in an HTTP Link header. Some APIs put pagination directions in headers instead of in the JSON body.

**Data flow**: It receives response headers, reads the Link header, searches for a link marked as rel=next, and returns that URL if found. If there is no such link, it returns nothing.

**Call relations**: The link-header pagination loop calls this after each page. If it returns a URL, the loop follows it; if it returns nothing, the loop stops.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 93–98)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: This helper decides whether a failed request is worth trying again. It treats network transport problems and temporary HTTP statuses, such as rate limits or server errors, as retryable.

**Data flow**: It receives an error. It checks the error type and, for HTTP status errors, the response status code, then returns true for temporary failures and false for permanent-looking failures.

**Call relations**: The shared send routine calls this inside its retry loop. It prevents retries for errors that are unlikely to succeed on a second attempt.

*Call graph*: called by 1 (_send).


##### `_retry_after`  (lines 101–121)

```
def _retry_after(error: BaseException) -> float | None
```

**Purpose**: This helper reads a Retry-After header, which is an API's way of saying 'wait this many seconds before trying again.' It only accepts safe, finite, non-negative numbers.

**Data flow**: It receives an error. If the error is a suitable HTTP response with a Retry-After header, it converts that header to a number of seconds; otherwise it returns nothing.

**Call relations**: The retry-wait calculator calls this before choosing a sleep time. It gives the API's own backoff instruction a chance to influence the retry delay.

*Call graph*: called by 1 (_retry_wait); 1 external calls (isfinite).


##### `_retry_wait`  (lines 124–141)

```
def _retry_wait(error: BaseException, delay: float) -> float
```

**Purpose**: This helper chooses how long to wait before retrying a temporary failure. It mixes exponential backoff, which waits longer after repeated failures, with jitter, which adds randomness so many workers do not retry at the exact same moment.

**Data flow**: It receives the error and the current delay level. It checks for a Retry-After value, compares it with the normal retry delay, applies caps, adds random jitter, and returns the number of seconds to sleep.

**Call relations**: The shared send routine asks this for the next wait time after a retryable failure. It relies on _retry_after when the server has provided explicit timing.

*Call graph*: calls 1 internal fn (_retry_after); called by 1 (_send); 1 external calls (uniform).


##### `_raise_for_status`  (lines 144–155)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: This function turns an unsuccessful HTTP response into a clear exception. Unlike the default library error, it includes a capped snippet of the response body so the real API complaint is visible.

**Data flow**: It receives an HTTP response. If the status is successful, it does nothing; otherwise it reads a short part of the body and raises an HTTP status error containing the method, URL, status, and body text.

**Call relations**: The shared send routine calls this after every response. That makes all GET and POST helpers fail consistently and with useful error messages.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 158–162)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: This helper reads a JSON object from a response, while treating empty responses as an empty dictionary. It is useful for endpoints that return no body when there is nothing to say.

**Data flow**: It receives an HTTP response. If the response is empty or has status 204, it returns an empty dictionary; otherwise it parses and returns the JSON body.

**Call relations**: The higher-level _get and _post methods use this after the raw request succeeds, so callers get a dictionary-shaped body instead of an HTTP response object.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 165–168)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: This helper reads a JSON response that is expected to be a top-level list of records. It safely returns only dictionary-shaped items.

**Data flow**: It receives an HTTP response. Empty responses become an empty list; non-empty responses are parsed as JSON and passed through list_or_empty.

**Call relations**: The link-header pagination loop uses this when no custom record parser is supplied. It is the default way to treat each linked page as a list of records.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 171–177)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: This safety check stops a pagination loop that has run for an unreasonable number of pages. It protects the sync from getting stuck forever on a broken or hostile API response.

**Data flow**: It receives a label describing the request and the current page count. If the count is above the maximum, it raises an error; otherwise it lets the loop continue.

**Call relations**: Every built-in pagination loop calls this once per page. It acts as a circuit breaker before a sync can hold resources forever.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 180–186)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: This safety check stops pagination when an API repeats the same next-page token or URL. A repeated cursor usually means the API is not advancing and would make the connector fetch the same page forever.

**Data flow**: It receives a label, the next cursor or link, and a set of cursors already seen. If the token is already in the set, it raises an error; otherwise it records the token.

**Call relations**: Cursor, link-header, and OData pagination call this whenever they receive a new continuation value. It catches endless loops earlier than the general page limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 195–196)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This method returns the streams this connector can read. A stream is a named collection of records, such as users, issues, or projects.

**Data flow**: It reads the class's streams_list and returns a fresh list copy. The copy means callers can inspect the list without directly mutating the class-level definition.

**Call relations**: The broader sync system asks connectors what streams they expose. Provider-specific subclasses fill in streams_list, and this base method reports it.


##### `RestConnector._make_client`  (lines 198–215)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method builds the asynchronous HTTP client used for one account's API calls. It applies the right authentication style, timeout settings, base URL, and JSON headers.

**Data flow**: It receives a base URL and a resolved credential. It creates a client using either a proxy transport, a bearer token, or explicit headers; if no authentication is available, it raises an error.

**Call relations**: fetch_page calls this at the start of a read. All later request helpers use the client it creates, so authentication and timeouts are consistent for the whole fetch.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 217–220)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This method performs a GET request and returns the response body as a JSON dictionary. It is the common helper for API endpoints that return normal object-shaped JSON.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It asks _get_raw to send the request with retries, then converts the successful response through _json_or_empty.

**Call relations**: Several pagination loops call this when they only need the JSON body. It delegates the network and retry work to _get_raw and the response shaping to _json_or_empty.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 3 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages).


##### `RestConnector._get_raw`  (lines 222–226)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: This method performs a GET request and returns the full HTTP response. It is used when callers need headers as well as the body, such as for Link-header pagination.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It wraps client.get in the shared _send retry routine and returns the successful response.

**Call relations**: _get builds on this for JSON-only calls. Link-header and OData pagination use it directly because their continuation information may live in headers or full response metadata.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 228–233)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This method performs a read-only POST request and returns a JSON dictionary. Some APIs use POST for searches or queries even when no data is being written.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It sends the POST through the shared retry routine and converts the response into a dictionary or an empty dictionary.

**Call relations**: Provider-specific connectors can call this for read endpoints such as search APIs. It shares the same retry and error behavior as GET requests.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 235–241)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: This method performs a read-only POST request and returns the full HTTP response. It is for unusual read endpoints where the caller needs to parse the raw body shape itself.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It sends the POST through the shared retry routine and returns the successful response without parsing it.

**Call relations**: Provider-specific connectors can use this when _post's dictionary parsing is not suitable. It still benefits from the same retry and status-checking path.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 243–264)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: This is the shared retry wrapper for HTTP requests. It makes one request, checks whether it succeeded, and retries temporary failures within fixed attempt and time limits.

**Data flow**: It receives a callable that starts an HTTP request. It runs it, raises clear errors for bad statuses, sleeps and retries for retryable failures, and finally returns a successful response or raises the last failure.

**Call relations**: _get_raw, _post, and _post_raw all route through this method. It calls the retry decision, wait calculation, and status-check helpers so every request behaves the same way.

*Call graph*: calls 3 internal fn (_is_retryable, _raise_for_status, _retry_wait); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 266–303)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[
```

**Purpose**: This is the main reading method used by the sync engine to fetch records from a stream. It opens the HTTP client, runs pagination, validates each page, flattens records, and yields clean pages onward.

**Data flow**: It receives the stream to read, authentication, base URL details, cursor information, and optional run context. It creates a client, gets pages from paginate_source, skips empty pages, validates record shape, flattens records, preserves delete and cursor metadata when present, and yields the prepared page.

**Call relations**: This method is the bridge between the wider connector framework and this REST helper. It calls _make_client for transport setup, paginate_source for provider-specific page production, and flatten and _validate_page before handing data back to the sync driver.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate_source); 1 external calls (__init__).


##### `RestConnector.paginate_source`  (lines 305–318)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This method is a narrow extension point between fetch_page and pagination. By default it ignores extra run context, but subclasses can override it if they need details such as the acting user or a backfill floor.

**Data flow**: It receives the client, stream, cursor, user identity, and optional backfill date. The default implementation passes only the client, stream, and cursor into paginate and returns that async stream of pages.

**Call relations**: fetch_page calls this rather than calling paginate directly. That gives specialized connectors a place to widen the pagination inputs without changing the main fetch_page flow.

*Call graph*: calls 1 internal fn (paginate); called by 1 (fetch_page).


##### `RestConnector.paginate`  (lines 320–332)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This method produces raw pages of records for a stream. The default version uses the stream's declared pagination strategy, while subclasses can override it for APIs with special shapes.

**Data flow**: It receives the HTTP client, stream, and optional cursor. If the stream has a supported pagination declaration, it yields pages from paginate_from_strategy; otherwise it raises an error saying the connector must implement pagination itself.

**Call relations**: paginate_source calls this in the default flow. It hands ordinary declared pagination to paginate_from_strategy and leaves unusual provider-specific pagination to subclasses.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (paginate_source).


##### `RestConnector.paginate_from_strategy`  (lines 334–396)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method turns a stream's pagination declaration into actual page-fetching behavior. It chooses the correct loop for cursor, link-header, or offset-and-limit pagination.

**Data flow**: It reads the stream's pagination settings, resolves the request path, checks that required fields are present, and then yields pages from the matching helper. If the strategy is absent or says none, it simply returns.

**Call relations**: The default paginate method calls this for normal streams. It dispatches to _get_cursor_pages, _get_link_header_pages, or _get_offset_pages, and may call _strategy_path when the stream did not provide an explicit path.

*Call graph*: calls 4 internal fn (_get_cursor_pages, _get_link_header_pages, _get_offset_pages, _strategy_path); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 366–368)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: This inner parser extracts records from a link-header paginated response when the records are nested inside the JSON body. It adapts a generic linked-page loop to APIs that wrap their lists.

**Data flow**: It receives an HTTP response, parses its JSON body if present, and returns the list of dictionary records found at the configured record path.

**Call relations**: paginate_from_strategy creates this parser only for next-link streams with a record_path. It passes the parser into _get_link_header_pages so that loop can focus on following links while this function handles body shape.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 398–426)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: This method reads pages from APIs that put the next-page URL in the HTTP Link header. It follows each rel=next link until there is no next link.

**Data flow**: It receives a client, starting path, optional query parameters, optional page size settings, and an optional record parser. It fetches the first page, yields records from each response, checks for too many pages or repeated links, and follows the next link until finished.

**Call relations**: paginate_from_strategy calls this for next_link streams. It uses _get_raw to fetch responses, next_link to find the continuation, _response_list or a supplied parser to read records, and the pagination safety checks to avoid infinite loops.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 428–460)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: This method reads pages from APIs that return a next cursor token inside the response body. The cursor is sent back on the next request to continue where the last page ended.

**Data flow**: It receives a client, path, record path, cursor path, parameter names, page size, and extra query parameters. It repeatedly sends GET requests, extracts records, yields non-empty pages, reads the next cursor, and stops when no valid cursor remains.

**Call relations**: paginate_from_strategy calls this for next_cursor streams. It uses _get for JSON requests, records_at for record extraction, get_path for cursor extraction, and safety checks for page count and repeated cursors.

*Call graph*: calls 4 internal fn (_get, _bound_cursor, _bound_pages, records_at); called by 1 (paginate_from_strategy); 1 external calls (get_path).


##### `RestConnector._get_odata_pages`  (lines 462–488)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads Microsoft Graph or OData-style pages, where records live under value and the next page is named by @odata.nextLink. OData is a common web API convention used by Microsoft services.

**Data flow**: It receives a client, path, and optional parameters. It fetches the current URL, yields dictionary records from the value field, follows @odata.nextLink when present, and stops when there is no next link.

**Call relations**: This helper is available for provider-specific connectors that need OData pagination. It uses _get_raw for requests, list_or_empty for record safety, and the shared page and cursor bounds to avoid endless loops.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 490–530)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: This method reads pages from APIs that use offset and limit numbers. In plain terms, it asks for 'start at record 0, give me 100,' then 'start at record 100, give me 100,' and so on.

**Data flow**: It receives a client, path, record path, limit, parameter names, optional extra parameters, and optional response paths that say whether more data exists or what limit was actually used. It fetches each page, yields records, decides whether to stop, and advances the offset.

**Call relations**: paginate_from_strategy calls this for offset_limit streams. It uses _get for JSON requests, records_at for extracting records, get_path for optional continuation signals, _int_or_none for server-reported limits, and _bound_pages for safety.

*Call graph*: calls 4 internal fn (_get, _bound_pages, _int_or_none, records_at); called by 1 (paginate_from_strategy); 1 external calls (get_path).


##### `RestConnector._get_page_number_pages`  (lines 532–561)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: This method reads APIs that use page numbers instead of cursors or offsets. It starts at a page number and increases it until a short page shows there is no more data.

**Data flow**: It receives a client, path, record path, page size, optional parameters, parameter names, and starting page. It requests each numbered page, yields records if present, stops when fewer than the requested page size are returned, and otherwise moves to the next page number.

**Call relations**: This helper is available for subclasses or custom pagination flows. It uses _get for requests, records_at for extracting records, and _bound_pages to prevent runaway loops.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at).


##### `RestConnector._strategy_path`  (lines 563–569)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: This method resolves the request path for a stream when the pagination declaration did not include one. The base class raises an error because only a provider-specific connector can know that mapping.

**Data flow**: It receives a stream. In the base class, it does not produce a path; it raises an error explaining that the connector must either set Pagination.path or override this method.

**Call relations**: paginate_from_strategy calls this only when it needs a path and the stream did not provide one. Subclasses can override it to look up paths from their own per-stream table.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 571–574)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method converts one API record into the flat dictionary shape the sync writer expects. The default does nothing because many APIs already return usable records.

**Data flow**: It receives a record and the stream it belongs to. It returns the same record unchanged unless a subclass overrides the method to lift nested fields or reshape the record.

**Call relations**: fetch_page calls this for every record after validation. Provider-specific connectors override it when their API wraps useful fields inside an envelope.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 576–587)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: This method checks that pagination produced the kind of data the sync system can write: a list of dictionary records. It fails early with a clear error when connector code yields the wrong shape.

**Data flow**: It receives a page-like value and the stream being read. It raises a type error if the page is not a list or if any item inside it is not a dictionary; otherwise it changes nothing.

**Call relations**: fetch_page calls this before flattening and yielding data. It protects the rest of the sync pipeline from malformed pages produced by custom pagination code.

*Call graph*: called by 1 (fetch_page).


### `core/src/ufo/runtime/sources/connector.py`

`domain_logic` · `source registration and sync runs`

This file is the contract and toolkit for bringing outside data into the system. A connector is the piece that knows how to talk to one provider, such as a mail service, chat service, document store, or repository host. It declares its streams, meaning the provider-side collections it can sync, and then yields records from those streams in pages.

The file also defines small value objects that describe those streams: what field is the stable record ID, whether the stream is a full snapshot or an incremental update, how paging works, and which timestamp acts like a bookmark. Think of a cursor as a bookmark in a long book: after one sync stops, the next sync can reopen at the right place instead of rereading everything.

The most involved part is `PartitionWalk`. It supports streams split into many partitions, such as many repositories or many chat channels. Each partition needs its own bookmark, but the wider sync system expects one cursor. `PartitionWalk` packs all those per-partition bookmarks into one JSON string and updates it safely as pages are read. It also knows how to resume oldest-first, newest-first, and unordered streams without skipping records.

Finally, the base `Connector` class provides default record rendering. If a provider does not supply custom readable text, the record is turned into a simple title plus JSON body so it can still be stored and recalled.

#### Function details

##### `get_path`  (lines 36–45)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a value from nested dictionary-like data using a dotted path such as `author.id`. This lets stream definitions point to IDs or timestamps that are not at the top level of a record.

**Data flow**: It receives a mapping, a dotted path, and an optional default value. It walks through the mapping one part at a time; if any step is missing, not a mapping, or `None`, it returns the default. If the full path exists, it returns the found value.

**Call relations**: This is a small helper used by `record_key` when a record’s primary key is not a simple top-level field. It does not call back into the connector system; it only reads nested data.

*Call graph*: called by 1 (record_key).


##### `record_key`  (lines 48–60)

```
def record_key(record: Mapping[str, Any], primary_key: str) -> str | None
```

**Purpose**: Finds the stable provider ID for one record. The system needs this so the same outside record updates the same stored page instead of creating a new page every time its content changes.

**Data flow**: It receives a record and the name of the primary-key field. It first checks for that field directly on the record, then tries the same name as a dotted nested path through `get_path`. If the value is a string or integer, it returns it as text; if it is missing, empty, a boolean, or another unsupported type, it returns `None`.

**Call relations**: This function is called by `Connector.record_identity`, which is the base connector’s way to ask, “What is this record’s durable identity?” It relies on `get_path` for nested keys.

*Call graph*: calls 1 internal fn (get_path); called by 1 (record_identity).


##### `PartitionWalk.stream`  (lines 261–368)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Turns many separately bookmarked partitions into one continuous async stream of sync pages. It exists so a source split across many buckets, like channels or repositories, can still fit into the system’s single-cursor sync model.

**Data flow**: It receives the previously stored cursor as text. First it decodes that cursor into a per-partition map. Then it asks for partitions one by one, requests pages for each partition using the right boundary, updates that partition’s bookmark after each page, and yields `StreamPage` objects containing records, deletes, and the newly encoded cursor. At the end, it cleans up cursor entries for partitions that disappeared or dissolves temporary backfill windows when a walk finishes.

**Call relations**: This is the main driver inside `PartitionWalk`. It calls `_decode` before walking, calls `_encode` whenever it needs to publish a fresh cursor, creates `PartitionBound` values to tell the page factory where to resume, and yields `StreamPage` values to the sync layer. If a page factory raises `PartitionSkipped`, it leaves that partition’s saved state alone and moves on.

*Call graph*: calls 2 internal fn (_decode, _encode); 3 external calls (__init__, __init__, __init__).


##### `PartitionWalk._decode`  (lines 371–400)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: Converts the stored cursor string back into the per-partition bookmark map used by `PartitionWalk`. It also protects the sync from silently accepting corrupted cursor data.

**Data flow**: It receives a cursor string or `None`. Empty, non-JSON, or non-object cursor values become an empty map, meaning the walk starts fresh. A JSON object is read entry by entry: plain strings become normal watermarks, dictionaries are validated as temporary backfill windows, and malformed entries raise an error instead of being ignored.

**Call relations**: `PartitionWalk.stream` calls this at the start of a walk to recover where each partition left off. It uses JSON parsing to read the cursor text and validation to check stored backfill windows.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 403–408)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: Converts the current per-partition bookmark map into a JSON cursor string that can be saved between sync runs.

**Data flow**: It receives a mapping from partition names to either plain string watermarks or `_Window` objects. It turns any `_Window` into a regular dictionary, leaves string watermarks as strings, and serializes the whole map to sorted JSON text. The output is the next cursor stored by the sync system.

**Call relations**: `PartitionWalk.stream` calls this whenever it yields a page or needs to publish a cleanup checkpoint. It is the matching opposite of `_decode`.

*Call graph*: called by 1 (stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 427–428)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Declares which streams a connector knows how to sync. A stream is one provider-side collection, such as messages, documents, issues, or users.

**Data flow**: A concrete connector implements this method with no input besides itself. It returns a list of `StreamSpec` objects, each describing one stream’s name, stable ID field, cursor field, deletion behavior, pagination style, and related sync rules.

**Call relations**: During registration, `ConnectedSources._register` calls this method to discover what the connector offers. The returned stream definitions become the system’s map for later sync runs.

*Call graph*: called by 1 (_register).


##### `Connector.fetch_page`  (lines 431–446)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None) -> AsyncIterator[list[dict[str, Any]]
```

**Purpose**: Defines the contract for fetching records from one connector stream. Concrete connectors implement it to call the provider and yield pages of records, optionally including deletes and a provider cursor.

**Data flow**: It receives the stream definition, the previous cursor, a resolved credential, the provider base URL, an optional current user ID to exclude where needed, and an optional backfill floor date. An implementation uses those inputs to request provider data and asynchronously yields either plain lists of record dictionaries or richer `StreamPage` objects. It does not return one final list; it produces pages over time.

**Call relations**: This abstract method is the fetching hook that provider-specific connector classes must fill in. The rest of the connector framework can treat every provider the same because each implementation follows this shape.


##### `Connector.render`  (lines 448–468)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns one provider record into a title and body text that the system can store as a recallable page. It gives every connector a useful default, while content-heavy connectors can override it for nicer prose.

**Data flow**: It receives a record and its stream definition. It looks for a human-friendly title field such as `title`, `name`, or `subject`. If none exists, it asks `record_identity` and `record_ref` for a stable fallback name. It then returns a pair: the chosen title and a body containing a heading plus the record serialized as sorted JSON. If the record has neither a title nor a usable identity, it raises an error.

**Call relations**: This method calls `Connector.record_identity` to find the provider-stable ID, `Connector.record_ref` to build a source-side reference when needed, and JSON serialization to write the default body. It is the base rendering path used when a connector does not provide custom record-to-text behavior.

*Call graph*: calls 2 internal fn (record_identity, record_ref); 1 external calls (dumps).


##### `Connector.record_identity`  (lines 470–472)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Returns the stable identity for a record inside its provider stream. This is the value used to recognize the same outside record across sync runs.

**Data flow**: It receives a record and the stream definition. It passes the record and the stream’s primary-key path to `record_key`, then returns the resulting string ID or `None` if no valid ID is present.

**Call relations**: `Connector.render` calls this when it needs a fallback title and identity for a record. This method delegates the actual key lookup rules to `record_key`.

*Call graph*: calls 1 internal fn (record_key); called by 1 (render).


##### `Connector.record_ref`  (lines 474–481)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Builds the reference used when a page row is first created for a record. It prefers the provider’s primary key but can fall back to a hash when the primary key is not a simple string or number.

**Data flow**: It receives a record and the stream definition. It reads the stream’s primary-key field directly from the record. If the value is a string or integer, it returns that value as text; if it is a boolean, it returns `None`; otherwise it serializes the whole record and returns a SHA-256 hash, which is a compact fingerprint of the content.

**Call relations**: `Connector.render` calls this when it needs a reference for a fallback title. It uses JSON serialization and hashing to create a deterministic fallback when a direct reference is not available.

*Call graph*: called by 1 (render); 2 external calls (sha256, dumps).


### Source sync storage
The sync engine stores incoming documents, tracks deletions and cursors, and exposes changed pages for downstream indexing.

### `core/src/ufo/runtime/sources/sync.py`

`orchestration` · `scheduled source sync and downstream page-feed replay`

This file turns outside content into the system’s internal “pages.” A source backend is like a plug-in adapter: one backend may read a local folder, another may read a service such as Slack or GitHub. The sync driver claims sources that are due to run, asks the right backend to fetch documents, writes changed document bodies to blob storage, and updates database rows that describe each page. It avoids doing extra work by comparing a content digest, which is a fingerprint of the page body. If the fingerprint has not changed, it can skip rewriting the body and only update browsing metadata such as title or timestamps.

The file also defines how failures are treated. A real error backs off future retries so the system does not hammer a broken provider. A refused stream, such as one blocked by missing permissions, is skipped rather than treated as corrupt data; repeated refusals can “park” the source so it retries less often. This distinction matters because old pages should remain visible when a provider temporarily refuses access.

Finally, the file provides a page feed. Indexers read this feed using a cursor, like a bookmark, to replay page changes in a stable order. Tombstoned pages carry an empty body so downstream systems know to remove their derived data.

#### Function details

##### `SourceRowConfig.requested_fields`  (lines 94–97)

```
def requested_fields(cls) -> frozenset[str]
```

**Purpose**: Returns the configuration fields that a caller must repeat exactly when registering the same source again. It separates fields that describe what was requested from fields that are resolved later by the system.

**Data flow**: It reads the class-level sets of non-identity fields and resolved fields. It subtracts the resolved fields from the non-identity fields, then returns the remaining field names as an immutable set.

**Call relations**: This is used by source configuration models that inherit from SourceRowConfig. It supports the larger registration flow by helping decide whether a new registration matches an existing source row or represents a different request.


##### `normalize_page_timestamp`  (lines 100–120)

```
def normalize_page_timestamp(value: str) -> str
```

**Purpose**: Turns a page timestamp into a consistent UTC ISO timestamp string. This keeps timestamps from different providers comparable, even if one sends Unix seconds and another sends an ISO date.

**Data flow**: It receives a string. If the string is numeric, it treats it as seconds or milliseconds since the Unix epoch; otherwise it parses it as an ISO-style timestamp. It rejects unclear timestamps without a timezone except plain dates, then returns the time converted to UTC with microsecond precision.

**Call relations**: Page.normalize_timestamp calls this whenever a Page model receives created_at or updated_at. It is the shared gate that keeps provider timestamps clean before they reach the database.

*Call graph*: called by 1 (normalize_timestamp); 2 external calls (fromisoformat, fromtimestamp).


##### `Page.digest`  (lines 137–138)

```
def digest(self) -> str
```

**Purpose**: Builds a stable fingerprint for a page body. The sync driver uses this fingerprint to tell whether a document’s content actually changed.

**Data flow**: It reads the Page body text, encodes it as bytes, hashes it with SHA-256, and returns the hash with a sha256: prefix. It does not change the page.

**Call relations**: SyncDriver._commit reads this property while comparing fetched pages with prior database rows. If the digest matches, the driver can avoid rewriting the body to blob storage.

*Call graph*: 1 external calls (sha256).


##### `Page.normalize_timestamp`  (lines 142–145)

```
def normalize_timestamp(cls, value: str | None) -> str | None
```

**Purpose**: Validates and standardizes the optional created_at and updated_at fields on a Page. It keeps each page model from carrying ambiguous time values.

**Data flow**: It receives either a timestamp string or None. None passes through unchanged; a string is sent to normalize_page_timestamp and returned in normalized form.

**Call relations**: Pydantic calls this validator when a Page is created by a backend such as FolderSource or an extension source. It delegates the actual parsing rules to normalize_page_timestamp.

*Call graph*: calls 1 internal fn (normalize_page_timestamp).


##### `StreamSkipped.__init__`  (lines 195–198)

```
def __init__(self, reason: str, *, awaits_grant: bool=False) -> None
```

**Purpose**: Creates a skip signal for a source stream that cannot be read right now for a non-data-failure reason, such as missing permission or a provider gate. It records whether the stream is waiting for a grant event before it can recover.

**Data flow**: It receives a human-readable reason and an awaits_grant flag. It stores both on the exception and passes the reason to the base RuntimeError.

**Call relations**: Connector backends and provider paginators raise this when they know the stream should be skipped, not failed. SyncDriver.run catches it, logs the skip, and hands the source to SyncDriver._skip for rescheduling or parking.

*Call graph*: called by 54 (fetch, paginate, paginate, paginate, paginate, paginate, _org_stream, paginate, paginate, paginate (+15 more)).


##### `validation_fault`  (lines 201–207)

```
def validation_fault(error: ValidationError) -> str
```

**Purpose**: Turns a validation error into a safe, compact explanation of which fields failed and why. It avoids including raw provider values, which may contain private data.

**Data flow**: It receives a Pydantic ValidationError, reads its structured error list, and formats each item as a field path plus an error type. It returns one semicolon-separated string.

**Call relations**: SyncDriver._report_failed calls this when a fetched config or page shape fails validation. The resulting text becomes the safe provider_fault field in failure telemetry.

*Call graph*: called by 1 (_report_failed); 1 external calls (errors).


##### `response_fault`  (lines 210–236)

```
def response_fault(response: httpx.Response) -> str
```

**Purpose**: Extracts useful, safe error reasons from an HTTP response body, especially GraphQL-style errors. It helps failure logs say what the provider rejected without storing the whole response body.

**Data flow**: It receives an httpx Response, tries to parse JSON, looks for an errors array, and collects each error message plus an optional code. If the body is missing, unreadable, or not in the expected shape, it returns an empty string.

**Call relations**: SyncDriver._report_failed calls this for HTTP status errors. It supplements the status code and URL with provider-authored messages while avoiding sensitive response payloads.

*Call graph*: called by 1 (_report_failed); 1 external calls (json).


##### `StreamFault.__init__`  (lines 246–248)

```
def __init__(self, reason: str) -> None
```

**Purpose**: Creates a failure signal for a provider response whose shape cannot be read safely. Backends use it when they can describe the problem without exposing the provider’s raw data.

**Data flow**: It receives a reason string, stores it on the exception, and passes the same text to RuntimeError. It has no other side effects.

**Call relations**: Several extension backends raise this when provider content or metadata is unusable. SyncDriver._report_failed recognizes it and records its reason as the safe provider fault.

*Call graph*: called by 8 (_read, _markdown_entries, _spool_tarball, _refuse_client_error, decoded, _account_base, _sheet_value_records, _ensure_tenant).


##### `SourceBackend.config_model`  (lines 288–288)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: Defines the typed configuration model a source backend expects. This lets each backend validate its own settings instead of receiving an unstructured dictionary.

**Data flow**: As a protocol property, it promises that a backend will provide a Pydantic model class. The sync driver reads that class and uses it to validate the source row’s stored config.

**Call relations**: SyncDriver._fetch relies on this protocol member before calling SourceBackend.fetch. FolderSource and extension backends supply concrete versions.


##### `SourceBackend.fetch`  (lines 290–290)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Defines the contract for fetching documents from a source. A backend implements this to turn provider-specific data into SyncResult pages, deletes, and a next cursor.

**Data flow**: It receives typed backend config, the previous cursor, and SourceAuth for workspace/provider access. The implementation returns a SyncResult or raises a meaningful exception such as CursorExpired, StreamSkipped, or StreamFault.

**Call relations**: SyncDriver._fetch calls this after choosing and validating the backend. Concrete backends, including FolderSource and extension connectors, provide the real fetching behavior.


##### `FolderSource.fetch`  (lines 304–315)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Reads a configured local folder and turns every file into a Page. It is the built-in source backend for local filesystem content.

**Data flow**: It receives SourceConfig, ignores cursor and auth, and reads the folder path in a background thread so file I/O does not block the event loop. It wraps each file’s relative path and text content as a Page, then returns a full snapshot SyncResult.

**Call relations**: SyncDriver._fetch calls this through the SourceBackend interface when a source row uses the folder backend. It delegates the actual filesystem walk to FolderSource._read.

*Call graph*: 4 external calls (__init__, __init__, to_thread, Path).


##### `FolderSource._read`  (lines 318–325)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: Scans a local directory and reads every file as UTF-8 text. It provides the raw file list used by FolderSource.fetch.

**Data flow**: It receives a root Path, checks that it is a directory, walks all files below it in sorted order, and returns pairs of relative path and decoded file text. If the folder itself is missing, it raises FileNotFoundError.

**Call relations**: FolderSource.fetch runs this in a worker thread. Its choice to fail when the root folder is missing protects the system from treating a temporary missing mount as a mass deletion.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 328–348)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object], *, connection_id: UUID | None=None, non_identity_keys: frozenset[str]=frozenset()) -> UUID
```

**Purpose**: Creates a deterministic database id for a source row. The same workspace, backend, and identity-defining config always produce the same UUID, so restarting does not duplicate sources.

**Data flow**: It receives a workspace id, backend name, config mapping, optional connection id, and keys that should not count toward identity. It removes non-identity keys, serializes the rest in a stable order, includes the connection generation if present, and returns a UUID version 5.

**Call relations**: register_sources calls this while bootstrapping configured sources. The same idea is used across the system to make source rows stable and repeatable.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 351–354)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: Creates a deterministic id for a page inside a source. This lets re-fetches, updates, and delete records all point to the same page row.

**Data flow**: It receives a source id and a source_ref string. It combines them into a stable UUID version 5 and returns that UUID.

**Call relations**: SyncDriver._commit calls this while matching fetched pages and explicit delete references to existing database rows.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `register_sources`  (lines 357–422)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: Ensures that sources listed in configuration exist in the database. It runs at startup so configured sources are ready for the scheduled sync job.

**Data flow**: It receives configured SourceEntry objects. It opens a workspace transaction, finds the workspace and main agent, computes each source’s stable id, inserts missing source rows, and grants the main agent access. Existing active rows are left in place; removed rows are not revived.

**Call relations**: Startup code calls this outside the regular sync polling loop. It uses source_row_id to avoid creating duplicates and writes source_grant rows so the main agent can read the source.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `_rescheduled`  (lines 440–451)

```
def _rescheduled(claimed: ClaimedSource, when: datetime | sa.Case[datetime]) -> sa.Case[datetime]
```

**Purpose**: Builds the database expression that decides a source’s next sync time after a run finishes. It protects manual or event-driven resync requests that arrived while the source was already being synced.

**Data flow**: It receives a ClaimedSource and a proposed next time. It returns a SQL case expression: use the proposed time only if next_sync_at has not been moved forward since the claim was taken; otherwise keep the newer requested time.

**Call relations**: SyncDriver._write, SyncDriver._release, and SyncDriver._skip use this when freeing a claim. It keeps completion cleanup from accidentally burying a fresh resync request.

*Call graph*: called by 3 (_release, _skip, _write); 1 external calls (case).


##### `_stream_tags`  (lines 454–459)

```
def _stream_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: Builds metric tags that identify the provider and stream for a sync outcome. Tags are small labels used by monitoring systems to group events and counters.

**Data flow**: It receives a ClaimedSource, reads its backend name, extracts the stream value from config when present, and returns a provider/stream dictionary.

**Call relations**: SyncDriver.run, SyncDriver._report_ok, SyncDriver._report_failed, SyncDriver._report_parked, and _check_tags use these tags when logging or emitting metrics.

*Call graph*: calls 1 internal fn (_config_value); called by 5 (_report_failed, _report_ok, _report_parked, run, _check_tags).


##### `_check_tags`  (lines 462–470)

```
def _check_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: Builds service-check tags that identify one specific source row. This prevents two sources for the same provider stream from overwriting each other’s health status.

**Data flow**: It receives a ClaimedSource, starts with _stream_tags, adds the source_id as a string, and returns the combined tag dictionary.

**Call relations**: SyncDriver._report_ok and SyncDriver._report_failed use this for service checks. It calls _stream_tags so metrics and checks describe streams consistently, while checks also include row identity.

*Call graph*: calls 1 internal fn (_stream_tags); called by 2 (_report_failed, _report_ok).


##### `_config_value`  (lines 473–475)

```
def _config_value(source: ClaimedSource, key: str) -> str
```

**Purpose**: Safely reads a string value from a source config mapping. It returns an empty string if the key is missing or not a string.

**Data flow**: It receives a ClaimedSource and a config key. It looks up the key in source.config and returns the value only when it is a string; otherwise it returns "".

**Call relations**: _stream_tags uses it to find stream names, and reporting methods use it for account ids. It keeps telemetry formatting simple and safe when configs differ by backend.

*Call graph*: called by 3 (_report_failed, _report_ok, _stream_tags).


##### `_readers_remain`  (lines 495–521)

```
def _readers_remain() -> sa.ColumnElement[bool]
```

**Purpose**: Creates a database condition that says a source still has at least one live reader, or has no grants at all. This avoids syncing content that no active agent can use.

**Data flow**: It builds SQL subqueries over source_grant and agent rows. The returned condition is true when there are no grants, or when at least one granted agent is not archived.

**Call relations**: SyncDriver.candidate_workspaces and SyncDriver._claim_due include this condition when looking for due work. It prevents unnecessary provider calls, blob writes, and indexing for unreachable feeds.

*Call graph*: called by 2 (_claim_due, candidate_workspaces); 5 external calls (and_, exists, literal, or_, select).


##### `SyncDriver.candidate_workspaces`  (lines 543–564)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds workspaces that currently have at least one source due to sync. This lets the scheduler avoid opening per-workspace work when nothing is ready.

**Data flow**: It reads the current time, opens an owner-level transaction, queries source rows that are due, not removed, not actively claimed, and still readable, then returns distinct workspace ids.

**Call relations**: A dispatcher can call this before binding work to a workspace. It uses _readers_remain to filter out sources whose only readers are archived.

*Call graph*: calls 1 internal fn (_readers_remain); 4 external calls (now, or_, select, owner_tx).


##### `SyncDriver.run`  (lines 566–585)

```
async def run(self) -> None
```

**Purpose**: Runs one sync pass for due sources in the current workspace. It claims sources, fetches content, commits successful results, and records or reschedules failures.

**Data flow**: It creates a unique claim id, asks _claim_due for source rows, and processes each one. A normal fetch goes through _fetch and _commit; a StreamSkipped goes to _skip; other exceptions are classified, reported, and released with backoff.

**Call relations**: This is the main driver method used by the scheduled source-sync job. It coordinates the lower-level helpers without letting one broken source stop the rest of the batch.

*Call graph*: calls 8 internal fn (_claim_due, _commit, _error_backoff, _fetch, _release, _report_failed, _skip, _stream_tags); 4 external calls (suppress, now, log, uuid4).


##### `SyncDriver._claim_due`  (lines 587–638)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: Claims a limited batch of source rows that are ready to sync. Claiming is a lease, meaning it marks the row so another worker does not sync the same source at the same time.

**Data flow**: It reads the current time, selects due rows that are not removed and not held by a valid claim, optionally uses database row locking on PostgreSQL, writes claimed_by and claim_expires_at, and returns ClaimedSource objects.

**Call relations**: SyncDriver.run calls this at the start of a pass. It uses _readers_remain so sources without live readers are not claimed.

*Call graph*: calls 1 internal fn (_readers_remain); called by 1 (run); 7 external calls (__init__, now, timedelta, or_, select, update, workspace_tx).


##### `SyncDriver._fetch`  (lines 640–659)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: Calls the correct backend for a claimed source and returns its SyncResult. It also prepares the authentication context the backend may need to reach its provider.

**Data flow**: It receives a ClaimedSource, looks up the backend, validates the stored config using the backend’s config_model, resolves an optional self user id, binds source credentials if available, and awaits backend.fetch. The output is the backend’s SyncResult.

**Call relations**: SyncDriver.run calls this before committing anything. It is the bridge between generic core sync logic and provider-specific backend code.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 661–740)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: Compares fetched pages with existing page rows and decides what must be written, metadata-only updated, or tombstoned. It is the main reconciliation step after a successful fetch.

**Data flow**: It receives a claimed source and SyncResult, loads prior pages, assigns stable page ids, compares digests and browse metadata, writes changed bodies to blob storage, builds delete lists, and then calls _write. After the database write, it reports success.

**Call relations**: SyncDriver.run calls this after _fetch succeeds. It uses _prior_pages to know current state, page_id_for for stable ids, _write for database changes, and _report_ok for telemetry.

*Call graph*: calls 4 internal fn (_prior_pages, _report_ok, _write, page_id_for); called by 1 (run); 3 external calls (__init__, __init__, uuid5).


##### `SyncDriver._prior_pages`  (lines 742–786)

```
async def _prior_pages(self, source_id: UUID) -> tuple[dict[UUID, tuple[str, bool, PageBrowse]], dict[str, tuple[str, bool, PageBrowse]]]
```

**Purpose**: Loads the existing page state for a source before reconciliation. This lets the driver detect unchanged pages, renamed identities, existing tombstones, and metadata changes.

**Data flow**: It receives a source id, queries page rows for that source, and builds two lookup tables: one by page id and one by source_identity. Each entry carries digest, tombstone state, and browse metadata.

**Call relations**: SyncDriver._commit calls this before examining fetched pages. The returned lookups guide whether the driver writes a new blob, updates only metadata, or reuses an existing row.

*Call graph*: called by 1 (_commit); 3 external calls (__init__, select, workspace_tx).


##### `SyncDriver._write`  (lines 788–916)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[ChangedPage], metadata: list[PageBrowse], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> int
```

**Purpose**: Persists one successful sync result into the database and releases the source claim. It writes changed pages, metadata updates, tombstones deleted pages, updates the source cursor, and schedules the next normal run.

**Data flow**: It receives prepared changed pages, metadata updates, fetched ids, delete ids, the next cursor, and whether the result was a full snapshot. Inside a workspace transaction it checks the claim still has authority, updates or inserts page rows, tombstones explicit deletes and missing snapshot pages, resets error/refusal state, clears the claim, and returns how many rows were tombstoned.

**Call relations**: SyncDriver._commit calls this after it has written changed bodies to blob storage. It uses _rescheduled so a resync request made during the run is not lost.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (_commit); 6 external calls (now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._report_ok`  (lines 918–941)

```
async def _report_ok(self, source: ClaimedSource, fetched: int, written: int, tombstoned: int, dropped: int) -> None
```

**Purpose**: Records a successful source sync in logs and service checks. It says how many pages were fetched, written, tombstoned, and dropped by the backend.

**Data flow**: It receives counts and the source, builds tags, writes a source_sync.ok log, and emits an OK service check. Each telemetry action is protected so telemetry problems do not break the sync.

**Call relations**: SyncDriver._commit calls this after _write succeeds. It uses _stream_tags, _check_tags, and _config_value to label the event clearly.

*Call graph*: calls 3 internal fn (_check_tags, _config_value, _stream_tags); called by 1 (_commit); 3 external calls (suppress, emit_service_check, log).


##### `SyncDriver._error_backoff`  (lines 943–951)

```
def _error_backoff(self, source: ClaimedSource, now: datetime) -> tuple[int, datetime]
```

**Purpose**: Calculates how long to wait before retrying a source after a failure. The delay doubles with each consecutive error, up to a cap.

**Data flow**: It receives the source and current time, increments the source’s consecutive error count, computes a bounded exponential backoff delay, and returns both the new count and the next sync time.

**Call relations**: SyncDriver.run calls this when _fetch or _commit raises an ordinary exception. The result is passed to _report_failed and _release so the log and database agree.

*Call graph*: called by 1 (run); 1 external calls (timedelta).


##### `SyncDriver._report_failed`  (lines 953–1011)

```
async def _report_failed(self, source: ClaimedSource, error: Exception, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: Records a failed source sync without leaking sensitive provider data. It logs the failure, emits a failure counter, and marks the source service check as critical.

**Data flow**: It receives the source, exception, cursor-reset flag, error count, and next retry time. It classifies the exception, builds a safe provider_fault string for known cases, emits log and metric records, and sends a critical service check.

**Call relations**: SyncDriver.run calls this after computing backoff for a failed source. It uses response_fault for HTTP errors and validation_fault for validation errors, plus shared tag helpers for telemetry labels.

*Call graph*: calls 5 internal fn (_check_tags, _config_value, _stream_tags, response_fault, validation_fault); called by 1 (run); 5 external calls (suppress, isoformat, emit_metric, emit_service_check, log_error).


##### `SyncDriver._release`  (lines 1013–1039)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: Frees a claimed source after a failed run and records its retry state. This ensures one failed source does not remain stuck as claimed forever.

**Data flow**: It receives the source, whether to clear the cursor, the new error count, and next retry time. In a workspace transaction it updates the source row, optionally clears the cursor, sets next_sync_at using _rescheduled, stores the error count, and clears the claim.

**Call relations**: SyncDriver.run calls this after _report_failed. A successful run does not use it because SyncDriver._write performs the release and resets counters.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (run); 2 external calls (update, workspace_tx).


##### `SyncDriver._skip`  (lines 1041–1106)

```
async def _skip(self, source: ClaimedSource, reason: str, *, awaits_grant: bool) -> None
```

**Purpose**: Reschedules a source whose backend deliberately skipped the stream instead of failing it. It keeps existing pages untouched and slows repeated refusals by parking the source when needed.

**Data flow**: It receives the source, refusal reason, and awaits_grant flag. It increments the stored refusal count, decides whether the source should park, sets the next sync time to a normal interval or a longer hold, clears errors and the claim, and records park metadata. If the row was parked, it reports that separately.

**Call relations**: SyncDriver.run calls this when it catches StreamSkipped. It uses _rescheduled to preserve newer resync requests and calls _report_parked only after the database update confirms the park.

*Call graph*: calls 2 internal fn (_report_parked, _rescheduled); called by 1 (run); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `SyncDriver._report_parked`  (lines 1108–1133)

```
async def _report_parked(self, source: ClaimedSource, reason: str, refusals: int) -> None
```

**Purpose**: Records that a refused source stream has been parked after repeated skips. A park is a warning, not an alert, because an operator usually cannot fix a missing grant or provider refusal.

**Data flow**: It receives the source, reason, and refusal count. It builds stream tags, writes a warning log, and emits a parked counter; telemetry failures are suppressed.

**Call relations**: SyncDriver._skip calls this after successfully writing a parked state. It uses _stream_tags so the warning and metric are grouped by provider and stream.

*Call graph*: calls 1 internal fn (_stream_tags); called by 1 (_skip); 3 external calls (suppress, emit_metric, warn).


##### `PageFeed.pages_changed_since`  (lines 1173–1173)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Defines the interface an indexer uses to read page changes after a cursor. The cursor is a bookmark that lets the indexer resume without rereading everything.

**Data flow**: As a protocol method, it promises to accept an optional cursor and limit, then return a PageBatch containing ordered changes and the next cursor. The concrete implementation supplies the database and blob-reading behavior.

**Call relations**: CorePageFeed.pages_changed_since implements this contract. Extension contexts can expose a PageFeed so downstream indexers depend on the interface rather than the core implementation.


##### `page_cursor`  (lines 1176–1185)

```
def page_cursor(cursor: object) -> tuple[int, UUID]
```

**Purpose**: Parses and validates a page-feed cursor. The cursor must contain a revision number and page id separated by a vertical bar.

**Data flow**: It receives an object, verifies it is a string, splits it into revision and UUID text, checks the revision is numeric, parses the UUID, and returns both pieces. Bad input raises ValueError.

**Call relations**: CorePageFeed.pages_changed_since calls this when a reader supplies a cursor. The parsed values become the database boundary for reading only later page changes.

*Call graph*: called by 1 (pages_changed_since); 1 external calls (UUID).


##### `CorePageFeed.pages_changed_since`  (lines 1197–1255)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Reads changed pages from the core database in a stable order and includes each live page’s body. Indexers use this to keep their own derived indexes up to date.

**Data flow**: It receives an optional cursor and requested limit, caps the limit, builds a query ordered by revision and page id, and applies the cursor boundary if present. It loads rows, fetches each non-tombstoned body from blob storage, turns rows into PageChange objects, and returns a PageBatch with a next cursor when rows were found.

**Call relations**: This is the concrete PageFeed implementation used by downstream indexing code. It calls page_cursor to resume correctly and reads both the page table and blob store to provide complete change records.

*Call graph*: calls 1 internal fn (page_cursor); 7 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx).

## 📊 State Registers Touched

- `reg-extension-catalog` — The installed extension and pack catalog that says which extra tools, routes, agents, skills, jobs, and backends are available.
- `reg-workspace-records` — The saved workspace records that identify each customer space and hold its limits, setup state, balance settings, and routing boundaries.
- `reg-credentials-and-grants` — The encrypted secrets, account connections, and grants that say which member or agent may use an outside service.
- `reg-egress-network-policy` — The outbound network permission state that decides which external hosts, proxies, and secret injections are allowed for a workspace or agent.
- `reg-audience-visibility` — The saved visibility and audience rules that decide who may see a conversation, transcript, agent, source, artifact, or object.
- `reg-source-page-sync-state` — The source and page records that remember connected feeds, cursors, backoff, deletes, ownership, grants, and the latest synced content.
- `reg-index-memory-store` — The searchable index and long-term memory store built from synced pages, embeddings, recalled facts, and deduplicated notes.
- `reg-extension-data-store` — The per-workspace extension storage area where optional features save their own small durable JSON state.
- `reg-service-connection-pools` — Process-local shared connection/client pools for database, Redis/pubsub, HTTP/provider calls, and other long-lived service clients reused by requests, workers, tools, and jobs.
- `reg-background-runner-handles` — Process-local async task handles, wakeup queues, and scheduler loop state for live background workers distinct from their durable job records.
- `reg-backend-provider-registry` — Process-local registry mapping provider names to active backend implementations for models, search, embeddings, memory, connectors, browser access, auth, billing, and feature services.
- `reg-source-trigger-wakeup-queue` — Durable wakeup records created from source/page changes so background runners can later admit agent work without losing or duplicating change-triggered starts.
