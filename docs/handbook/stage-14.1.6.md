# CRM, sales, advertising, and marketing connectors  `stage-14.1.6`

This stage is shared behind-the-scenes support for bringing business data into the system. It does not run the product by itself. Instead, it acts like a set of adapters, each one shaped to fit a different outside service. An adapter calls that service’s web API, meaning its online doorway for data, then turns the answers into a steady stream of records the rest of the sync system can store and search.

ActiveCampaign, Klaviyo, Mailchimp, HubSpot, and Typeform cover marketing, email, forms, and customer activity. Apollo, Attio, and Salesforce cover sales and CRM data such as contacts, companies, deals, tasks, notes, and opportunities. Calendly brings in scheduling data like event types, bookings, and invitees. Facebook Ads reads ad accounts, campaigns, ads, and daily performance. Instagram reads business accounts, posts, stories, and analytics through Facebook’s Graph API.

Together, these files hide the quirks of each service, such as paging through long result lists, and present everything as consistent source records.

## Files in this stage

### CRM and sales records
Defines connectors for customer-management and sales platforms that expose contacts, companies, deals, tasks, and related CRM objects.

### `extensions/sources/ufo_ext_sources/providers/active_campaign.py`

`io_transport` · `source sync`

ActiveCampaign exposes many kinds of data: contacts, lists, campaigns, deals, accounts, tags, users, webhooks, and more. This file turns those remote API resources into named “streams,” meaning repeatable feeds of records that the rest of the system can sync.

The main problem it solves is that ActiveCampaign’s API is broad but fairly regular. Most list endpoints return data in a wrapper like “contacts: [...]” plus paging information, and they use limit-and-offset paging, like reading a long book 100 lines at a time. This connector records the small differences between streams, such as the URL name ActiveCampaign expects and the field that shows when a record changed.

It also handles authentication. ActiveCampaign does not use the usual “Bearer” authorization header; it expects an “Api-Token” header. The connector adapts the stored credential into that shape unless a broker-provided transport is already being used.

During a sync, the connector asks for pages of records. If a previous cursor is available, it adds an “updated after this time” filter for streams where ActiveCampaign supports that. If ActiveCampaign rejects access with a 401 or 403 status, the stream is skipped with a clear message instead of crashing the whole sync unexpectedly.

#### Function details

##### `_stream`  (lines 98–114)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one ActiveCampaign resource. A stream description tells the sync system the stream’s name, where records come from, which field identifies a record, and which date field can be used to notice changes.

**Data flow**: It receives a stream name and optional details such as the ActiveCampaign source object name, primary key, cursor field, and whether it is a main recommended stream. It fills in sensible defaults, decides whether the cursor field also counts as the updated-at field, and returns a StreamSpec object that the connector can advertise to the rest of the system.

**Call relations**: This function is used while the file is loaded to build the ActiveCampaign stream list. It hands each finished StreamSpec to the module-level ACTIVECAMPAIGN_STREAMS list, which the connector later exposes as its available data feeds.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 176–181)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to ActiveCampaign. Its important job is to put the API key into the header name ActiveCampaign expects: Api-Token.

**Data flow**: It receives a base URL and a Credential. If the credential already contains a special transport, it leaves that path alone and lets the parent connector create the client. Otherwise, it checks for a direct API key, wraps that key into headers as Api-Token, and returns an asynchronous HTTP client ready to make requests. If no key is present, it raises an error because the connector cannot authenticate.

**Call relations**: The broader RestConnector machinery calls this when it needs a network client for a sync run. This method customizes the standard client-building path just enough for ActiveCampaign’s authentication style, then delegates the actual client creation back to the parent connector.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 184–185)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This small lookup converts the system’s stream name into the exact URL segment and response wrapper key ActiveCampaign uses. It matters because some resources use names like campaignMessages instead of the project’s snake_case name campaign_messages.

**Data flow**: It receives a StreamSpec. It looks up the stream name in the ActiveCampaign path map. If there is a custom mapping, it returns the mapped URL segment and response envelope key; otherwise it returns the stream name for both.

**Call relations**: The paginate method calls this before making requests. It gives paginate the API path to request and the key to read from the JSON response, so paging can work the same way across many different streams.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 187–211)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one ActiveCampaign stream page by page and yields batches of records. It is the core read loop for pulling data from the ActiveCampaign API.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It resolves the correct API path, starts with a page size of 100, and adds an “updated after” filter when the stream supports server-side incremental filtering. It then asks the parent paging helper for offset-based pages and yields each list of records. If ActiveCampaign responds with 401 or 403, it turns that into a StreamSkipped error with a clear explanation; other HTTP errors are raised normally.

**Call relations**: The sync runtime calls paginate when it wants records for a particular stream. Paginate first calls _resolve_stream_segment to translate the stream into ActiveCampaign’s API naming, then hands the actual page fetching to the shared offset-page helper from the parent connector. When access is refused, it raises StreamSkipped so the larger sync flow can treat that stream as unavailable rather than mistaking it for normal data.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### `extensions/sources/ufo_ext_sources/providers/apollo.py`

`io_transport` · `source sync polling`

Apollo does not offer a simple “give me everything changed since yesterday” endpoint, and it does not send webhooks when CRM records change. Because of that, this connector must poll Apollo: it asks for pages of contacts or accounts, newest first, and stops once it reaches records that are not newer than the last successful sync point, called a watermark.

The file defines two readable streams: contacts and accounts. For each stream, it knows which Apollo search endpoint to call, which field in the response contains the records, and which Apollo field should be used to sort newest first. Think of it like reading a stack of mail where the newest letters are on top: on later runs, the connector only needs to read down until it reaches mail it has already seen.

Apollo also has a special authentication detail. Instead of accepting the common “Bearer” authorization header, it expects the API key in an `X-Api-Key` header. The connector adjusts the HTTP client for that. If Apollo refuses access with 401 or 403, the connector marks that stream as skipped rather than treating the whole system as broken, because some Apollo keys cannot access all API areas.

#### Function details

##### `ApolloConnector._make_client`  (lines 61–69)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Apollo. Its main job is to put the API key where Apollo expects it: in the `X-Api-Key` header instead of the usual bearer authorization header.

**Data flow**: It receives a base URL and a resolved credential. It first asks the shared REST connector code to build a normal HTTP client. If the credential contains a bearer-style key, it removes the standard `Authorization` header and adds that key as `X-Api-Key`. It returns the adjusted client, ready to make Apollo requests.

**Call relations**: This is used during connector setup, before any Apollo pages are fetched. It builds on the shared REST connector behavior, then applies Apollo’s special authentication rule so later calls made by `ApolloConnector.paginate` are accepted by Apollo.


##### `ApolloConnector.paginate`  (lines 71–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Apollo stream, such as contacts or accounts, page by page. It yields only records that are newer than the last saved cursor, so repeated syncs avoid rereading the whole CRM when possible.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that marks the newest record already seen. It looks up the correct Apollo search endpoint, sends POST requests with page number, page size, and newest-first sorting, then pulls the record list out of each response. It filters those records through `ApolloConnector._above`, yields the records that are newer than the cursor, and stops when a page is empty, when it sees older records, or when Apollo says there are no more pages. If Apollo returns 401 or 403, it turns that refusal into a `StreamSkipped` error with a clear explanation.

**Call relations**: This is the connector’s main polling loop for Apollo data. During each page fetch, it calls the shared helpers `list_or_empty` to safely read a list from the response and `get_path` to read Apollo’s nested pagination count. It calls `ApolloConnector._above` to decide which records are still worth syncing. If Apollo refuses access, it raises `StreamSkipped`, telling the larger source-sync flow to skip that stream instead of continuing blindly.

*Call graph*: calls 2 internal fn (__init__, _above); 2 external calls (get_path, list_or_empty).


##### `ApolloConnector._above`  (lines 107–116)

```
def _above(records: list[dict[str, Any]], cursor: str | None) -> list[dict[str, Any]]
```

**Purpose**: This keeps only records newer than the sync cursor. It exists because Apollo cannot filter by time on the server side, so the connector must do that comparison itself after each page arrives.

**Data flow**: It receives a list of Apollo records and an optional cursor string. If there is no cursor, meaning this is a first sync or a full read, it returns all records. If there is a cursor, it checks each record’s `created_at` value and returns only records whose timestamp string is newer than the cursor.

**Call relations**: This helper is called by `ApolloConnector.paginate` after every Apollo search response. Its result tells the pagination loop both what to yield and when to stop: if a page contains records that are not above the cursor, the loop knows it has reached previously synced data.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/attio.py`

`io_transport` · `source sync`

Attio’s API does not return every kind of data in the same shape. Regular CRM objects, like companies and people, live under object-specific record endpoints. Tasks and notes have their own workspace-wide endpoints. Meetings and call recordings use cursor-based paging, and recordings need an extra request to fetch their transcript. This connector is the adapter that hides those differences.

The most important job here is flattening. Attio puts record IDs inside nested `id` objects and stores each field as a list of “value cells” rather than as simple values. Without flattening, the system would not have a clear primary key such as `record_id`, `task_id`, or `call_recording_id`, and fields like email or company domain would be hard to use. The connector lifts IDs to the top level and turns Attio-specific field shapes into plain strings, numbers, lists, and dates.

For reading, the connector pages through each stream until Attio has no more rows. It uses offset paging for older endpoints and cursor paging for newer ones. Call recordings are special: it first walks meetings, then asks each meeting for its recordings, then fetches transcripts where available. If a standard object is disabled or the OAuth grant lacks a needed permission, it skips that stream instead of failing the whole sync.

#### Function details

##### `_records_stream`  (lines 35–43)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates the stream description for a normal Attio CRM object, such as companies, people, or deals. A stream description tells the sync system what the stream is called, where it comes from, and which field identifies each row.

**Data flow**: It receives a friendly stream name, an Attio object slug, and whether the stream should be treated as canonical. It builds a `StreamSpec`, which is a small recipe for syncing that object, with `record_id` as the primary key and full-snapshot behavior enabled. The result is used in the file’s list of supported Attio streams.

**Call relations**: This helper is used while the module is being loaded to define the standard object streams. It hands the finished stream recipe to `StreamSpec.__init__`, which stores the details the connector later follows.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 70–71)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: Safely pulls one named ID out of a nested dictionary. It exists because Attio often wraps IDs inside small objects rather than returning a plain string directly.

**Data flow**: It receives any value and a key name. If the value is a dictionary, it returns the value at that key; otherwise it returns `None`. Nothing outside the input is changed.

**Call relations**: It is called by `AttioConnector._value_primitive` when that function is trying to extract a useful fallback ID from option or status objects.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 80–81)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: Builds the request body used when asking Attio for a page of object records. It keeps paging requests consistent and capped at Attio’s supported page size.

**Data flow**: It receives an offset, meaning how many records have already been read. It returns a small dictionary containing the fixed limit and that offset. The caller sends this dictionary as JSON to Attio.

**Call relations**: `AttioConnector.paginate` calls this each time it needs the next page from a standard object records endpoint.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._value_primitive`  (lines 84–127)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: Turns one Attio value cell into the simplest useful value. For example, it can turn a selected option into its title, an email cell into an email address, or a record reference into an `attio:<id>` string.

**Data flow**: It receives one dictionary-shaped value cell from Attio. It checks which kind of data the cell contains, picks the human-usable value, and returns that value. If it cannot find a meaningful value, it returns `None`.

**Call relations**: This is the basic translation tool used by the flattening helpers. When option or status objects only expose nested IDs, it calls `_nested_id` to pull those IDs out safely.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 130–145)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: Reduces one Attio field cell into a single plain value when possible. This is like opening a package and keeping the useful item inside instead of storing all the wrapping.

**Data flow**: It receives a cell that may be a list, a dictionary, or an already-simple value. Lists are converted item by item using `_value_primitive`, empty results become `None`, and most multi-value cells keep only the first useful value. Multi-select option lists are preserved as a list.

**Call relations**: It is part of the flattening path for normal Attio object records. Higher-level flattening code uses it when a field is expected to behave like a single value.


##### `AttioConnector._flatten_list_cell`  (lines 148–155)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: Turns an Attio field cell into a clean list of useful values. It is used for fields that naturally have many values, such as email addresses or phone numbers.

**Data flow**: It receives a cell that may already be a list or may be a single value. It extracts simple values from each item, removes missing values, and returns a list. If there is nothing useful, it returns an empty list.

**Call relations**: It supports the broader value-flattening process for fields where keeping all values matters, rather than choosing only the first one.


##### `AttioConnector._flatten_values`  (lines 158–192)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts Attio’s nested `values` block into a normal dictionary of fields. This is where raw Attio attributes become usable properties like `email`, `domain`, `first_name`, and `phone`.

**Data flow**: It receives the raw values dictionary from an Attio record. For each field, it chooses either list-style or single-value flattening, then adds helpful shortcut fields such as the first email or first domain. It returns a plain dictionary ready to merge into the final record.

**Call relations**: It is the main field-cleaning step used while flattening standard object records. It relies on the lower-level cell flatteners to translate individual Attio value cells.


##### `AttioConnector._flatten_record`  (lines 195–209)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Flattens a standard Attio object record, such as a company, person, or deal. It gives the record a top-level `record_id` and turns its nested attributes into simple fields.

**Data flow**: It receives one raw Attio record and the stream description. It reads the nested identity, copies creation and update timestamps, optionally copies a cursor field if the stream has one, then merges in the flattened attribute values. It returns a clean dictionary for storage.

**Call relations**: `AttioConnector.flatten` calls this when the stream is not one of the special streams like tasks, notes, meetings, or call recordings.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 212–216)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a clear top-level `task_id` to a raw Attio task. This lets the sync system identify the task reliably.

**Data flow**: It receives a task record. It copies the whole record, reads `task_id` from the nested `id` object when needed, places it at the top level, and returns the copied record.

**Call relations**: `AttioConnector.flatten` calls this for the `tasks` stream, so tasks get the primary key shape expected by the rest of the system.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 219–223)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a clear top-level `note_id` to a raw Attio note. This makes notes safe to store and update by ID.

**Data flow**: It receives a note record. It copies the record, extracts `note_id` from the nested ID shape when present, writes it at the top level, and returns the result.

**Call relations**: `AttioConnector.flatten` calls this for the `notes` stream before records leave the connector.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 226–230)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a clear top-level `meeting_id` to a raw Attio meeting. This normalizes meetings into the same primary-key style as other streams.

**Data flow**: It receives a meeting record. It copies the record, extracts `meeting_id` from the nested ID when necessary, adds it to the top level, and returns the updated copy.

**Call relations**: `AttioConnector.flatten` calls this for the `meetings` stream.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 233–254)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Cleans up one call recording record so it has a top-level recording ID, a usable recording URL, and readable transcript text when transcript segments are present.

**Data flow**: It receives a raw call recording. It copies it, extracts `call_recording_id`, fills `recording_url` from `web_url` if needed, and joins transcript segments into one text field with speaker names where available. It returns the enriched recording record.

**Call relations**: `AttioConnector.flatten` calls this for the `call_recordings` stream after pagination has already gathered recording and transcript data.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 256–265)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening method for each Attio stream. It is the connector’s main cleanup doorway before raw API records become stored records.

**Data flow**: It receives one raw record and the stream it came from. It checks the stream name, sends the record to the matching specialized flattener, and returns the normalized dictionary. For standard CRM objects, it falls back to `_flatten_record`.

**Call relations**: The wider source-sync framework calls this after records are read. This function then delegates to `_flatten_task`, `_flatten_note`, `_flatten_meeting`, `_flatten_call_recording`, or `_flatten_record` depending on the stream.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 267–293)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads pages of records from Attio for one stream. It hides whether the stream uses object-record queries or one of Attio’s special endpoints.

**Data flow**: It receives an HTTP client, a stream description, and an unused cursor argument. For special streams, it hands off to `_paginate_named`. For standard objects, it repeatedly posts query bodies with increasing offsets, yields each page of records, and stops when Attio returns no records or a short final page. If Attio says a standard object is disabled, it raises `StreamSkipped` so that stream can be skipped cleanly.

**Call relations**: This is the main page-reading method used by the sync framework. It calls `_build_query_body` for standard object requests, `_is_object_disabled` when Attio returns an error, and `_paginate_named` for tasks, notes, meetings, and call recordings.

*Call graph*: calls 4 internal fn (__init__, _build_query_body, _is_object_disabled, _paginate_named).


##### `AttioConnector._paginate_named`  (lines 295–321)

```
async def _paginate_named(self, client: httpx.AsyncClient, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Routes named non-object streams to the correct paging method. Tasks, notes, meetings, and call recordings each need different Attio API behavior.

**Data flow**: It receives an HTTP client and a stream name. For tasks and notes, it uses offset paging. For meetings, it uses cursor paging. For call recordings, it starts the special meeting-by-meeting recording flow. If Attio rejects the request because the OAuth grant is missing a required permission, it raises `StreamSkipped` with a readable reason.

**Call relations**: `AttioConnector.paginate` calls this whenever the stream is one of Attio’s special named resources. It delegates to `_paginate_simple`, `_paginate_cursor`, or `_paginate_call_recordings`, and uses `_is_scope_unauthorized` plus `_scope_skip_reason` to turn permission failures into stream skips.

*Call graph*: calls 6 internal fn (__init__, _is_scope_unauthorized, _paginate_call_recordings, _paginate_cursor, _paginate_simple, _scope_skip_reason); called by 1 (paginate).


##### `AttioConnector._paginate_simple`  (lines 323–330)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads an offset-paged Attio endpoint such as tasks or notes. Offset paging means asking for records starting at position 0, then 500, then 1000, and so on.

**Data flow**: It receives an HTTP client, an API path, and a page size. It asks the shared REST helper for pages under the response’s `data` field and yields each list of records unchanged.

**Call relations**: `AttioConnector._paginate_named` calls this for tasks and notes because those endpoints use simple limit-and-offset paging.

*Call graph*: called by 1 (_paginate_named).


##### `AttioConnector._paginate_cursor`  (lines 332–350)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a cursor-paged Attio endpoint, such as meetings or meeting call recordings. A cursor is like a bookmark returned by the API that says where the next page begins.

**Data flow**: It receives an HTTP client, path, page size, and optional query parameters. It asks the shared REST helper to read records from `data`, follow `pagination.next_cursor`, and yield each page. It does not flatten or enrich the records itself.

**Call relations**: `AttioConnector._paginate_named` calls this for meetings. `AttioConnector._paginate_call_recordings` also calls it first to list meetings and then to list recordings for each meeting.

*Call graph*: called by 2 (_paginate_call_recordings, _paginate_named).


##### `AttioConnector._paginate_call_recordings`  (lines 352–388)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the call recordings stream by walking through meetings first, then reading recordings for each meeting. This is necessary because Attio exposes recordings under individual meetings rather than as one global list.

**Data flow**: It receives an HTTP client. It pages through meetings, extracts each meeting ID, title, start and end times, calculates duration when possible, then pages through that meeting’s recordings. Each recording is stamped with parent meeting information, and if a recording ID exists, it fetches the transcript and attaches it. It yields pages of enriched recording records.

**Call relations**: `AttioConnector._paginate_named` calls this for the `call_recordings` stream. Inside the flow it calls `_paginate_cursor`, `_meeting_id`, `_datetime_of`, `_duration_seconds`, `_call_recording_id`, and `_fetch_transcript` to assemble each recording with its meeting context.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (_paginate_named).


##### `AttioConnector._fetch_transcript`  (lines 390–402)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: Fetches the transcript for one Attio call recording. If the transcript is not ready or not found, it quietly returns nothing instead of breaking the sync.

**Data flow**: It receives an HTTP client, a meeting ID, and a recording ID. It calls Attio’s transcript endpoint, returns the response’s `data` object if it is a dictionary, returns `None` for 404 or 409 responses, and re-raises other HTTP errors.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this after it finds a recording ID, so transcript data can be attached before the recording is yielded.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 405–409)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: Extracts the usable meeting ID from Attio’s meeting identity shape. It accepts both nested ID dictionaries and plain string IDs.

**Data flow**: It receives a meeting record. If `id` is a dictionary, it returns `id.meeting_id`; if `id` is already a string, it returns that string; otherwise it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this while walking meeting pages, because it needs the meeting ID to ask Attio for that meeting’s recordings.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 412–416)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: Extracts the usable call recording ID from Attio’s recording identity shape. It protects the transcript-fetching step from malformed or missing IDs.

**Data flow**: It receives a call recording record. If `id` is a dictionary, it returns `id.call_recording_id`; if `id` is a string, it returns that string; otherwise it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this before trying to fetch a transcript for a recording.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 419–423)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: Pulls a date or date-time string out of Attio’s meeting time shape. Attio may represent timed events with `datetime` and all-day events with `date`.

**Data flow**: It receives a time-shaped value. If it is a dictionary, it returns the `datetime` value when present, otherwise the `date` value. If the shape is not a dictionary, it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this for meeting start and end values before adding them to recordings and before calculating duration.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 426–436)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: Calculates a rough meeting duration in seconds from start and end time strings. It returns nothing when the inputs are missing or cannot be parsed as dates.

**Data flow**: It receives optional start and end strings. If both exist, it parses them as ISO 8601 date-time values, subtracts start from end, clamps negative durations to zero, and returns the number of seconds. If parsing fails, it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this after extracting meeting start and end times. It uses Python’s `datetime.fromisoformat` parser to turn strings into date-time objects.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 439–448)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes the specific Attio error that means a standard CRM object is disabled in the workspace. This lets the connector skip that stream instead of treating it as a fatal problem.

**Data flow**: It receives an HTTP status error. It checks for status code 400, tries to read the JSON response body, and returns `True` only when the body has code `standard_object_disabled`. Otherwise it returns `False`.

**Call relations**: `AttioConnector.paginate` calls this when a standard object query fails. If it returns true, `paginate` raises `StreamSkipped` with a clear message.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._is_scope_unauthorized`  (lines 451–460)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes the Attio error that means the OAuth permission grant is missing a required scope. A scope is a named permission, such as permission to read meetings.

**Data flow**: It receives an HTTP status error. It checks for status code 403, tries to parse the JSON response, and returns `True` only when the response code is `unauthorized`. Otherwise it returns `False`.

**Call relations**: `AttioConnector._paginate_named` calls this when a named stream fails. If the failure is a missing permission, the stream is skipped rather than crashing the entire sync.

*Call graph*: called by 1 (_paginate_named).


##### `AttioConnector._scope_skip_reason`  (lines 463–469)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a human-readable explanation for skipping a stream because of missing OAuth permissions. It tries to include Attio’s own message when available.

**Data flow**: It receives an HTTP status error. It tries to parse the JSON response, reads its `message` field if present, and returns a sentence explaining that the OAuth grant lacks a required scope. If no message is available, it uses a generic fallback.

**Call relations**: `AttioConnector._paginate_named` calls this after `_is_scope_unauthorized` identifies a missing-scope error, then passes the returned text into `StreamSkipped`.

*Call graph*: called by 1 (_paginate_named).


### Scheduling and paid advertising
Reads event-scheduling data and paid advertising account, campaign, ad, and performance records into syncable streams.

### `extensions/sources/ufo_ext_sources/providers/calendly.py`

`io_transport` · `during Calendly source sync`

This connector is the bridge between UFO and Calendly’s web API. Calendly keeps data behind organization-based API endpoints, so the connector first asks Calendly who the authenticated user is and which organization they belong to. Without that step, most collection requests would not know which organization to read from and the sync would fail or return incomplete data.

The file defines the Calendly streams the system can sync. A stream is a named kind of data, like scheduled events or event invitees. For each stream, the connector knows which Calendly endpoint to call, which field uniquely identifies each record, and which field can be used as a progress marker for incremental syncs.

Most Calendly endpoints return data in pages, like a long list split across several screens. The connector follows Calendly’s next-page token until all pages are read. For event invitees, it first reads scheduled events, extracts each event’s ID from its URI, then asks Calendly for invitees to that specific event. This is like checking each meeting on a calendar, then opening its guest list.

If Calendly refuses access with a permission error, the connector skips that stream instead of crashing the whole sync. Finally, `flatten` reshapes records into friendlier fields, such as copying a scheduled event’s `name` into `title`.

#### Function details

##### `_uuid_from_uri`  (lines 63–66)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: Pulls the final ID-like part out of a Calendly URI. This is needed because some Calendly endpoints use full URIs in records, while other endpoints require just the short event identifier.

**Data flow**: It receives any value that might be a URI. If the value is not a non-empty string, it returns nothing. If it is a string, it trims any trailing slash, takes the text after the last slash, and returns that as the extracted identifier.

**Call relations**: When the connector is reading invitees, `CalendlyConnector._invitees` uses this helper to turn each scheduled event URI into the event ID needed to call Calendly’s invitee endpoint.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 74–77)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: Asks Calendly for the authenticated account’s user record. The connector needs this record mainly to discover the account’s current organization.

**Data flow**: It receives an HTTP client that is already prepared to talk to Calendly. It sends a request to `/users/me`, looks inside the response for the `resource` object, and returns that object if it is a dictionary. If the response shape is not as expected, it returns an empty dictionary.

**Call relations**: `CalendlyConnector.paginate` calls this directly for the `api_user` stream. `CalendlyConnector._org_stream` also calls it before reading organization-scoped streams, because those streams need the current organization URI.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 79–92)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a Calendly list endpoint one page at a time. It hides the details of Calendly’s pagination so the rest of the connector can simply loop over batches of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST connector machinery to fetch pages from Calendly, looking for records under `collection` and the next-page marker under `pagination.next_page_token`. It yields each page as a list of record dictionaries.

**Call relations**: `CalendlyConnector._org_stream` uses this for normal organization collections such as groups or events. `CalendlyConnector._invitees` uses it again for each scheduled event’s invitee list.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 94–110)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a Calendly collection that belongs to the user’s current organization. It also adds organization context to each record page so downstream code knows where the records came from.

**Data flow**: It receives an HTTP client, an endpoint path, and optionally a saved cursor plus the Calendly query parameter that should receive that cursor. It first fetches the current user, extracts `current_organization`, and stops the stream if Calendly did not provide one. It then builds request parameters with the organization and optional cursor, fetches paged records, adds the organization value to each record, and yields those enriched pages.

**Call relations**: `CalendlyConnector.paginate` uses this for event types, groups, organization memberships, and scheduled events. `CalendlyConnector._invitees` uses it to get the scheduled events that it must later inspect for invitees. It relies on `_current_user`, `_paginate_collection`, and `with_context`, and it raises `StreamSkipped` when the required organization is missing.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 112–130)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads invitees for all scheduled events in the organization. Calendly does not expose invitees as one simple organization-wide list here, so the connector must visit each event and then fetch that event’s invitees.

**Data flow**: It receives an HTTP client and an optional cursor. It first reads scheduled events through `_org_stream`. For each event, it extracts the event UUID from the event URI. If an ID is available, it fetches that event’s invitees page by page. When a cursor is present, it keeps only invitees whose `created_at` value is newer than the cursor. It yields non-empty invitee pages, with extra context showing which scheduled event they belong to.

**Call relations**: `CalendlyConnector.paginate` calls this when syncing the `event_invitees` stream. This function depends on `_org_stream` to find events, `_uuid_from_uri` to get usable event IDs, `_paginate_collection` to read invitee pages, and `with_context` to attach event information to the invitee records.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 132–172)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right Calendly reading strategy for each stream. This is the main dispatch point the source-sync framework calls when it wants records from Calendly.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing where a previous sync left off. It checks the stream name and routes the request: current user is fetched directly, organization streams go through `_org_stream`, and invitees go through `_invitees`. It yields pages of records. If the stream is unknown, or if Calendly refuses access with a 401 or 403 status, it raises `StreamSkipped` so the sync can continue with other streams.

**Call relations**: The broader sync framework calls this function for each declared Calendly stream. It hands work to `_current_user`, `_org_stream`, or `_invitees` depending on the stream. It also turns permission failures into skipped streams, which keeps one missing Calendly permission from stopping the entire connector.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 174–214)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Reshapes Calendly records into a friendlier and more consistent form for storage and search. It keeps the original data but adds common fields like `title`, `email`, `start_at`, or `api_url` where useful.

**Data flow**: It receives one Calendly record and the stream it belongs to. Based on the stream name, it copies or renames important fields into standard places. For organization memberships, it pulls the member name and email out of the nested `user` object, then leaves that nested object out so unrelated profile changes do not make the membership look changed. It returns the adjusted record.

**Call relations**: After `paginate` has yielded raw records, the source framework can call this function before writing records onward. It uses `dict_or_empty` when reading nested user data so missing or malformed user objects do not cause failures.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/facebook_ads.py`

`io_transport` · `source sync`

This connector is the project’s read-only bridge to Facebook Ads. Without it, the system would not know which Facebook API URLs to call, how to walk through pages of results, or how to split Facebook Ads data into useful streams such as campaigns and ad insights.

The file defines the Facebook Ads streams the system can sync. A stream is a named kind of data, like “campaigns” or “ads_insights,” with details such as its main identifier and which date field can be used as a cursor. A cursor is a saved “last seen” value that lets later syncs avoid re-reading old data.

The main class, FacebookAdsConnector, talks to Facebook’s Graph API using an OAuth bearer token supplied elsewhere by the platform. It first lists the ad accounts available to the connected Facebook grant. Then, for account-specific data, it asks each ad account for its campaigns, ad sets, ads, or daily insight rows. This is like checking every mailbox in a building before collecting the letters inside each one.

Facebook returns data in pages, so the connector follows Facebook’s `paging.next` links until there is no next page. For campaigns, ad sets, and ads, it filters out records older than the cursor. For insights, it requests daily ad-level metrics from the cursor date, or the last 90 days on a first run. If Facebook refuses access with a permission error, the connector skips that stream rather than crashing the whole sync.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 76–89)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads one Facebook API collection that may be split across multiple pages. It hides the repetitive work of following Facebook’s “next page” link and yielding each batch of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It calls the raw GET request method, reads the JSON response, pulls the list under the `data` field, and yields that list when it is not empty. If Facebook provides a `paging.next` URL, it uses that as the next request and clears the original query parameters because the next URL already contains them.

**Call relations**: This is the shared page-reading engine used by `_accounts`, `_account_children`, and `_insights`. Those higher-level functions decide what kind of Facebook Ads data they want, while `_paged` does the mechanical work of stepping through Facebook’s paginated responses and using `records_at` to find the returned records.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 91–96)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function gets the Facebook ad accounts available to the current credential. Other streams depend on this because campaigns, ads, and insights are all requested under a specific ad account.

**Data flow**: It starts with a fixed list of ad account fields, such as account ID, name, currency, time zone, and business details. It asks `_paged` to read `/me/adaccounts`, gathers every page into one list, and returns that complete list of account records.

**Call relations**: This function is the first stop for most of the connector’s work. `paginate` uses it directly for the `ad_accounts` stream, while `_account_children` and `_insights` call it first so they know which account IDs to query next.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 98–123)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads account-owned objects: campaigns, ad sets, or ads. It exists because those objects are not fetched once globally; they must be requested separately for each ad account.

**Data flow**: It receives a stream description and an optional cursor. It chooses the right Facebook fields for that stream, gets all ad accounts, and for each valid account ID reads the account’s matching collection. If a cursor is present, it keeps only records whose cursor field is newer than that saved value. Before yielding a page, it adds useful account context, such as the ad account ID and name, to every record.

**Call relations**: `paginate` calls this when the requested stream is `campaigns`, `ad_sets`, or `ads`. Inside, it relies on `_accounts` to find the accounts, `_paged` to fetch each account’s pages, and `with_context` to attach account information so later parts of the system can tell where each record came from.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 125–168)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads daily Facebook Ads performance metrics, such as impressions, clicks, spend, reach, and click-through rate. It turns Facebook’s insight rows into stable records the sync system can store and update.

**Data flow**: It builds a Facebook insights request for ad-level daily rows. If a cursor is available, it asks from that cursor date through today; otherwise it asks for the last 90 days. It then gets every ad account, reads each account’s insights pages, and transforms each row by adding an `id` made from the account, campaign, ad set, ad, and date. It also adds the ad account ID before yielding the rows.

**Call relations**: `paginate` calls this for the `ads_insights` stream. The function uses `_accounts` to know which accounts to inspect, `_paged` to walk through Facebook’s insight pages, `json.dumps` to format Facebook’s requested date range, and the current UTC date to set the end of cursor-based sync windows.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 170–194)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main dispatcher for reading a stream. Given a stream name, it chooses the correct reading path and yields pages of records to the wider sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. For ad accounts, it returns the account list. For campaigns, ad sets, and ads, it delegates to `_account_children`. For ads insights, it delegates to `_insights`. If the stream is unknown, it raises `StreamSkipped`. If Facebook returns a 401 or 403 permission refusal, it converts that into `StreamSkipped` with a clear message; other HTTP errors are allowed to keep failing normally.

**Call relations**: The source framework calls `paginate` when it wants records for one Facebook Ads stream. This function coordinates the lower-level readers and acts as the safety valve for unsupported streams or missing Facebook permissions, so one refused stream can be skipped cleanly instead of breaking the whole connector run.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 196–204)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function lightly normalizes records after they are read, especially campaign records. It makes campaign data easier for the rest of the system to use by choosing consistent field names and values.

**Data flow**: It receives one record and its stream description. If the stream is `campaigns`, it returns a copy of the record with a normalized `status` that prefers Facebook’s `effective_status`, plus a `created_at` value copied from `created_time`. For all other streams, it returns the record unchanged.

**Call relations**: This function is used after pagination by the broader connector framework when records are being prepared for storage. Unlike the fetching functions, it does not call out to Facebook; it simply reshapes each record so downstream code sees a cleaner version.


### HubSpot and social presence
Handles broad HubSpot CRM and marketing objects alongside Instagram business content and analytics sourced through Facebook APIs.

### `extensions/sources/ufo_ext_sources/providers/hubspot.py`

`io_transport` · `sync run`

HubSpot is not one simple database. Its customer records, marketing assets, analytics reports, consent settings, and sales tools live behind many different API endpoints, each with its own paging style and field names. This file is the bridge between that uneven outside world and this project’s steady internal sync format. Without it, HubSpot data would either not sync at all or would arrive in shapes too inconsistent to use reliably.

The file first declares the streams, meaning the named sets of data to fetch: contacts, companies, deals, lists, campaigns, conversations, custom objects, and more. Then HubSpotConnector decides how each stream should be read. Standard CRM objects use HubSpot’s search API, with an incremental cursor so later runs can fetch only records changed since the last sync. Product areas that do not support that search API use their own list or fan-out endpoints.

A lot of the file is careful translation work. HubSpot often wraps fields inside a properties object, or uses special forms like values arrays. The connector flattens those into top-level fields. It also looks for archived records and emits delete markers, so removed HubSpot records can disappear downstream too. If HubSpot says a stream is unavailable because the account lacks a product tier or permission, the connector marks just that stream as skipped instead of failing the whole run.

#### Function details

##### `_normalize_epoch_millis`  (lines 248–255)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Turns HubSpot timestamps stored as milliseconds since 1970 into readable ISO date strings. It leaves booleans and already-normal values alone so unrelated fields are not damaged.

**Data flow**: It receives any value. If the value is a number, or a string containing only digits, it treats it as milliseconds since the Unix epoch and converts it to a UTC timestamp string; otherwise it returns the original value.

**Call relations**: Analytics view rows and product API flattening call this when HubSpot returns dates in raw millisecond form, so later records have consistent date fields.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 258–273)

```
def _stream(name: str, *, object_type: str, canonical: bool=True, modified_property: str='hs_lastmodifieddate') -> StreamSpec
```

**Purpose**: Creates a stream description for a normal HubSpot CRM object, such as contacts or deals. A stream description tells the sync runner the object name, primary key, and timestamp fields to use.

**Data flow**: It receives a stream name, HubSpot object type, and a few options. It produces a StreamSpec configured for CRM search-style syncing.

**Call relations**: This is used while the module is loaded to define many CRM streams before HubSpotConnector exposes them to the runner.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 276–295)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='createdAt', updated_at_field: str | None='updatedAt', pagi
```

**Purpose**: Creates a stream description for HubSpot product APIs that are not plain CRM objects. These streams often have different keys, timestamp names, or pagination rules.

**Data flow**: It receives the stream’s name, source object, key fields, timestamp fields, and optional pagination setup. It returns a non-canonical StreamSpec ready for the connector’s product API paths.

**Call relations**: The module uses it at startup to declare owners, workflows, campaigns, analytics, consent, sequences, and similar streams.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 298–310)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Builds the common paging recipe for HubSpot GET endpoints that return results plus a next cursor. A cursor is like a bookmark telling the next request where to continue.

**Data flow**: It receives an API path. It returns a Pagination object that says where records live in the response, where the next cursor lives, and which query parameters carry cursor and page size.

**Call relations**: Several product API stream definitions use this helper so they can rely on the shared REST pagination machinery instead of custom code.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 313–323)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Creates a stream description for relationship tables, such as deal-to-contact links. These are synthetic streams: they are built from associations rather than from a single HubSpot object.

**Data flow**: It receives a stream name and the parent object to walk. It returns a StreamSpec without a cursor, because HubSpot does not expose modification timestamps for these association rows.

**Call relations**: The module uses it while defining junction streams that HubSpotConnector later routes to _paginate_junction.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 628–658)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the request body for HubSpot’s CRM search endpoint. It asks HubSpot for selected properties, sorted by the cursor field, and optionally filtered to records changed after the saved cursor.

**Data flow**: It receives a stream definition, property names, an incremental cursor, and a page cursor. It returns a JSON-ready dictionary for the POST search request.

**Call relations**: _paginate_crm_object and _paginate_custom_object_records use this before each search request so standard and custom objects follow the same incremental pattern.

*Call graph*: called by 2 (_paginate_crm_object, _paginate_custom_object_records).


##### `HubSpotConnector._flatten`  (lines 661–672)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a standard HubSpot CRM record into a flatter record that is easier for the rest of the system to store. HubSpot’s properties bag is lifted up beside id and timestamps.

**Data flow**: It receives one HubSpot record. It copies id, createdAt, updatedAt, and archived, then adds every field found inside properties, and returns the combined dictionary.

**Call relations**: flatten calls this for ordinary CRM streams after pages have been fetched.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 675–698)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records from HubSpot APIs outside the standard CRM search shape. It handles special cases like objectId fields, properties bags, form values arrays, and date formats.

**Data flow**: It receives a raw product API record and its stream. It copies the record, fills in an id when possible, lifts nested properties and values, fixes selected date fields, and returns the normalized record.

**Call relations**: flatten calls this for product API streams, and this helper calls _normalize_epoch_millis where HubSpot returns millisecond dates.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 700–707)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening rule for each stream. This keeps downstream records consistent while respecting streams that are already custom-built.

**Data flow**: It receives a raw record and stream description. It returns the record unchanged for junction and custom object streams, uses product API flattening for product streams, and uses CRM flattening for standard CRM objects.

**Call relations**: The wider sync framework calls this after records are fetched; it delegates to _flatten or _flatten_product_api depending on the stream.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector.record_identity`  (lines 709–722)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Finds the stable identity for a record, with special handling for consent states. Consent records need a compound identity because one contact can have multiple subscription statuses and business units.

**Data flow**: It receives a record and stream. For most streams it uses the parent connector’s identity logic; for consent states it combines contact id, subscription or status kind, and business unit into one identity string, or returns nothing if required parts are missing.

**Call relations**: The sync framework uses this when deciding whether an incoming record updates an existing one. Its special consent logic matches the custom rows built by _consent_row.


##### `HubSpotConnector._list_properties`  (lines 724–732)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which fields exist for a CRM object. This avoids hard-coding property names and lets the sync include account-specific custom fields.

**Data flow**: It receives an HTTP client and object name. It calls HubSpot’s properties endpoint, filters the response to valid property names, and returns those names as a list.

**Call relations**: _paginate_crm_object calls this before searching so the search request can ask for every available property.

*Call graph*: called by 1 (_paginate_crm_object).


##### `HubSpotConnector.paginate`  (lines 734–747)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Public paging entry for this connector. It fetches pages for a stream and turns permission-related HubSpot errors into skipped streams instead of full sync failures.

**Data flow**: It receives an HTTP client, stream, and saved cursor. It yields pages from _paginate_unchecked; if HubSpot returns unauthorized or stream-unavailable errors, it raises StreamSkipped with a readable reason.

**Call relations**: The sync runner calls this for each stream. It delegates the real route choice to _paginate_unchecked and uses _is_stream_unavailable and _stream_skip_reason for graceful skipping.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 749–776)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Routes each stream to the correct paging method. It is the connector’s traffic director.

**Data flow**: It receives a stream and cursor. It checks whether the stream has a generic pagination strategy, is a junction stream, custom object stream, product API stream, or normal CRM object, then yields pages from the matching helper.

**Call relations**: paginate calls this inside its error wrapper. It hands off to CRM, product, custom object, or junction paginators.

*Call graph*: calls 4 internal fn (_paginate_crm_object, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._paginate_crm_object`  (lines 778–805)

```
async def _paginate_crm_object(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Reads standard HubSpot CRM objects through the search API, incrementally when a cursor is available. It also follows up with archived-id checks so deletes are captured.

**Data flow**: It receives a client, stream, and cursor. It gets all property names, repeatedly posts search requests, removes duplicate records on the inclusive cursor boundary, yields record pages, then yields delete pages from archived records.

**Call relations**: _paginate_unchecked calls this for ordinary CRM streams. It uses _list_properties, _build_search_body, and _paginate_archived_ids.

*Call graph*: calls 3 internal fn (_build_search_body, _list_properties, _paginate_archived_ids); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._is_stream_unavailable`  (lines 808–831)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether a HubSpot error means the account simply cannot access this stream. This separates missing permissions from real connector failures.

**Data flow**: It receives an HTTP error. It checks for status 403, reads the JSON message if possible, and returns true only when the message matches known permission or scope wording.

**Call relations**: paginate and archived sweep helpers use this to skip unavailable streams or stop optional delete checks safely.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 834–843)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a human-readable explanation for why a HubSpot stream was skipped. It includes HubSpot’s own message when available.

**Data flow**: It receives a stream name and HTTP error. It tries to read the response message and returns a sentence describing the unavailable stream.

**Call relations**: paginate uses this when converting a HubSpot permission problem into StreamSkipped.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 845–880)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived, meaning deleted or hidden, CRM object ids after normal search results. This lets downstream storage remove records HubSpot no longer considers active.

**Data flow**: It receives a client and stream. It walks the object list endpoint with archived=true, collects ids, and yields StreamPage delete markers; unsupported or unavailable sweeps quietly stop.

**Call relations**: _paginate_crm_object calls this after active records. It relies on _is_archived_sweep_unsupported and _is_stream_unavailable to avoid failing on HubSpot limitations.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_crm_object); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 882–890)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Starts paging for non-CRM HubSpot product streams. It is a thin wrapper that keeps product API routing separate from CRM object routing.

**Data flow**: It receives a client, stream, and cursor. It asks _product_pages for the stream-specific page iterator and yields each page it produces.

**Call relations**: _paginate_unchecked calls this for product API streams; it delegates stream-specific details to _product_pages.

*Call graph*: calls 1 internal fn (_product_pages); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._product_pages`  (lines 892–927)

```
def _product_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct product API paginator for a named stream. Some streams can use a simple endpoint; others need special fan-out logic.

**Data flow**: It receives a client, stream name, and cursor. It looks up a custom paginator when needed, otherwise finds a declared GET path and returns the generic collection paginator; if no route exists, it raises an error.

**Call relations**: _paginate_product_api calls this. It connects names such as form_submissions, analytics_reports, associations, and pipelines to their specialized helpers.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api); 1 external calls (partial).


##### `HubSpotConnector._paginate_get_collection`  (lines 929–957)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks a common HubSpot list endpoint that returns records under results and a next cursor. This is the reusable workhorse for many product APIs.

**Data flow**: It receives a client, path, optional limit, and extra query parameters. It repeatedly GETs pages, normalizes objectId into id when needed, yields non-empty result lists, and follows paging.next.after until done.

**Call relations**: Many specialized paginators call this so they do not each reimplement the same cursor loop.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_sequences, _product_pages, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 959–994)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Reads all custom object types defined in the HubSpot account. Custom objects are account-specific, so the connector must discover their schemas before fetching records.

**Data flow**: It receives a client and cursor. It loads schemas, builds a temporary StreamSpec for each object type, fetches its records, then fetches archived ids for that object type.

**Call relations**: _paginate_unchecked calls this for the custom_objects stream. It uses schema helpers, _paginate_custom_object_records, and _paginate_custom_object_archived_ids.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 996–998)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches HubSpot’s definitions for account-specific custom objects. These schemas tell the connector what object types and fields exist.

**Data flow**: It receives a client, calls the schemas endpoint, keeps only dictionary rows, and returns them as a list.

**Call relations**: _paginate_custom_objects uses this to discover custom streams, and _association_object_types uses it so custom objects can be included in association discovery.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 1001–1006)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Extracts the usable object type identifier from a custom object schema. HubSpot may provide this under several different field names.

**Data flow**: It receives one schema dictionary. It checks objectTypeId, fullyQualifiedName, then name, and returns the first non-empty string found, or nothing.

**Call relations**: Custom object pagination, custom object row building, and association object discovery call this to refer to custom object types consistently.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1009–1024)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Builds the list of properties to request for a custom object. It includes ordinary properties plus display properties that help make records understandable.

**Data flow**: It receives a schema. It gathers unique property names from the schema’s properties, primary display property, and secondary display properties, then returns the list.

**Call relations**: _paginate_custom_objects calls this before searching each custom object type.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1026–1061)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Fetches records for one custom object type through the CRM search API. It applies the same cursor-boundary de-duplication used for normal CRM objects.

**Data flow**: It receives a client, temporary stream, schema, requested properties, and cursor. It posts search requests, skips duplicate boundary records, converts each raw record into a custom object row, and yields pages.

**Call relations**: _paginate_custom_objects calls this for each discovered schema. It uses _build_search_body and _custom_object_row.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1063–1102)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Turns a raw custom object record into a useful, self-describing row. It adds the object type, readable labels, display title, and a globally unique id.

**Data flow**: It receives a raw record and its schema. If required ids are present, it combines properties, schema labels, timestamps, display fields, and metadata into one flat row; otherwise it returns nothing.

**Call relations**: _paginate_custom_object_records calls this for every custom object record after fetching it.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1104–1134)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived records for a custom object type and emits delete markers. It prefixes ids with the object type so they match the custom object row ids.

**Data flow**: It receives a client and custom object type id. It pages through archived records, builds delete ids like objectType:recordId, yields StreamPage delete markers, and stops on unsupported or unavailable cases.

**Call relations**: _paginate_custom_objects calls this after active records for each custom schema.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1136–1155)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a unique list of owner teams from owner records. HubSpot exposes teams nested inside owners, so this stream is assembled from that nested data.

**Data flow**: It receives a client, reads owners through _paginate_get_collection, collects team dictionaries by id to remove duplicates, and yields the final team list.

**Call relations**: _product_pages selects this paginator for the owner_teams stream.

*Call graph*: calls 1 internal fn (_paginate_get_collection).


##### `HubSpotConnector._paginate_lists`  (lines 1157–1186)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot lists through their search endpoint. It also flattens HubSpot’s additionalProperties into the main list record.

**Data flow**: It receives a client. It posts list search requests with an offset, assigns id from listId when available, merges extra properties, yields pages, and advances until HubSpot says there are no more.

**Call relations**: _product_pages uses this for the lists stream, and _paginate_list_memberships calls it to find lists before reading their members.

*Call graph*: called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_site_search`  (lines 1188–1208)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads CMS search results for one content type, such as knowledge articles. It uses offset paging rather than cursor paging.

**Data flow**: It receives a client and content type. It repeatedly calls the site search endpoint with limit and offset, yields dictionary results, and stops when the offset reaches the reported total.

**Call relations**: _product_pages uses this through a partial function for knowledge_articles.


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1210–1234)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds marketing assets attached to each campaign. It first lists campaigns, then asks HubSpot for many asset types under each campaign.

**Data flow**: It receives a client. It pages through campaigns, extracts each campaign id and name, then for every known asset type yields pages from _paginate_campaign_asset_type.

**Call relations**: _product_pages selects this for campaign_assets. It uses _paginate_get_collection for campaigns and delegates each asset-type request.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1236–1271)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one kind of asset for one campaign and turns each asset into a stable row. It tolerates missing asset endpoints when HubSpot returns 403 or 404.

**Data flow**: It receives a client, campaign id, campaign name, and asset type. It pages the campaign asset endpoint, builds ids that include campaign, type, and asset id, adds campaign context and metrics, and yields pages.

**Call relations**: _paginate_campaign_assets calls this inside its campaign-and-asset-type fan-out.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1273–1279)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields HubSpot analytics views as a stream page. Analytics views are saved filters or reporting views used by analytics reports.

**Data flow**: It receives a client, asks _analytics_view_rows for normalized rows, and yields them if any exist.

**Call relations**: _product_pages selects this for analytics_views, and it relies on _analytics_view_rows for the actual normalization.

*Call graph*: calls 1 internal fn (_analytics_view_rows).


##### `HubSpotConnector._analytics_view_rows`  (lines 1281–1312)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches and normalizes analytics view definitions. It makes sure every view has an id, name, filters, and readable dates where possible.

**Data flow**: It receives a client, calls the analytics views endpoint, accepts either list or results-shaped responses, normalizes each row, and returns a list.

**Call relations**: _paginate_analytics_views uses it directly, and _paginate_analytics_reports uses it to run reports for each available view.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1314–1344)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Generates many analytics report rows by querying combinations of report subjects, time periods, and analytics views. It is a broad fan-out over HubSpot’s analytics reporting API.

**Data flow**: It receives a client. It chooses a date window, loads analytics views, then loops through report families, subjects, time periods, and view filters, yielding rows from each report query.

**Call relations**: _product_pages selects this for analytics_reports. It uses _analytics_report_window, _analytics_view_rows, and _paginate_analytics_report_query.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_window`  (lines 1347–1348)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range for analytics report extraction. The start is fixed far in the past, and the end is today in UTC.

**Data flow**: It takes no input. It returns two strings in HubSpot’s YYYYMMDD format: the configured start date and today’s date.

**Call relations**: _paginate_analytics_reports calls this before building analytics report queries.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1350–1401)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Runs one analytics report query and pages through its breakdown rows. It skips unsupported combinations that HubSpot reports as bad request or not found.

**Data flow**: It receives report type details, optional analytics view, and date range. It GETs the report endpoint with offset paging, converts the response through _analytics_report_rows, yields rows, and advances until all breakdowns are read.

**Call relations**: _paginate_analytics_reports calls this for every report combination it wants to collect.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1404–1479)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Turns one analytics report response into stored rows for totals and breakdowns. A breakdown is a split of metrics by something like source, page, or location.

**Data flow**: It receives raw report data plus report context. It creates a totals row on the first page when available, creates one row per breakdown, adds ids, names, metrics, filters, and formatted dates, then returns the rows.

**Call relations**: _paginate_analytics_report_query calls this after each HubSpot analytics response.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1482–1486)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Builds a safe stable id for an analytics report row. It removes characters that would make the id ambiguous.

**Data flow**: It receives any number of id parts. It converts them to strings, replaces slashes and colons, substitutes none for missing parts, joins them, and prefixes analytics_report:.

**Call relations**: _analytics_report_rows uses this when creating total and breakdown report records.


##### `HubSpotConnector._analytics_report_date`  (lines 1489–1490)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Formats HubSpot analytics dates from YYYYMMDD into the more familiar YYYY-MM-DD form.

**Data flow**: It receives an eight-character date string. It slices year, month, and day and returns them with dashes.

**Call relations**: _analytics_report_rows uses this for report start and end dates.


##### `HubSpotConnector._paginate_event_types`  (lines 1492–1505)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot event type definitions. These describe the kinds of events that can later appear as event occurrences.

**Data flow**: It receives a client, calls the event types endpoint, accepts list or results-shaped responses, ensures rows have string ids when possible, and yields one page.

**Call relations**: _product_pages selects this for the event_types stream.


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1507–1524)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual HubSpot event occurrences, optionally starting after the last synced event time. It skips quietly if the endpoint is absent.

**Data flow**: It receives a client and cursor. It sends occurredAfter when a cursor exists, collects dictionary results, and yields them as one page.

**Call relations**: _product_pages selects this for event_occurrences and passes the stream cursor into it.


##### `HubSpotConnector._paginate_email_events`  (lines 1526–1549)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot marketing email events with offset paging. It can start from a previous cursor by translating that cursor to HubSpot’s millisecond timestamp format.

**Data flow**: It receives a client and cursor. It builds query parameters, repeatedly calls the email events endpoint, yields event rows, and follows offset until hasMore is false or the offset stops changing.

**Call relations**: _product_pages selects this for email_events. It uses _email_event_start_timestamp to prepare the incremental start.

*Call graph*: calls 1 internal fn (_email_event_start_timestamp).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1552–1561)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts an email event cursor into the millisecond timestamp HubSpot expects. It accepts either an existing numeric timestamp or an ISO date string.

**Data flow**: It receives an optional cursor string. It returns nothing for missing or unparseable cursors, returns the integer directly for digit strings, or parses an ISO timestamp and converts it to milliseconds.

**Call relations**: _paginate_email_events calls this before requesting email event pages.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._paginate_association_labels`  (lines 1563–1579)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the labels HubSpot uses to describe relationships between object types. For example, a company-contact association may have a particular label or category.

**Data flow**: It receives a client. It walks all object-type pairs that have labels, converts each label into a normalized row, and yields pages.

**Call relations**: _product_pages selects this for association_labels. It uses _association_pairs_with_labels and _association_label_row.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels).


##### `HubSpotConnector._paginate_associations`  (lines 1581–1596)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual relationships between HubSpot object records across object-type pairs. This produces rows like one contact linked to one deal.

**Data flow**: It receives a client. It discovers object-type pairs with labels, pages ids for the source object type, batch-reads associations for those ids, and yields normalized relationship rows.

**Call relations**: _product_pages selects this for associations. It coordinates _association_pairs_with_labels, _paginate_crm_object_id_pages, and _paginate_association_batch.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages).


##### `HubSpotConnector._paginate_association_batch`  (lines 1598–1625)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Batch-reads associations for a group of source record ids. It also follows per-record association paging when HubSpot says one source record has more linked targets.

**Data flow**: It receives source and target object types plus input ids. It posts a batch read request, converts results to rows, yields them, then builds the next pending inputs from response paging until none remain.

**Call relations**: _paginate_associations calls this for each page of source ids. It uses _association_rows, _next_association_inputs, and _is_optional_pair_unavailable.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1628–1642)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Finds follow-up association batch inputs for records whose association lists continue onto another page.

**Data flow**: It receives one batch association response. For each result with a source id and next after cursor, it returns an input dictionary containing that id and after cursor.

**Call relations**: _paginate_association_batch calls this after each batch response to continue reading long association lists.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1644–1657)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Discovers object-type pairs that actually have association labels. This avoids querying associations for pairs HubSpot says do not exist.

**Data flow**: It receives a client. It gets known standard and custom object types, tries every from/to combination, fetches labels for the pair, and yields only pairs with labels.

**Call relations**: _paginate_association_labels and _paginate_associations both use this as their discovery step.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1659–1672)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the list of HubSpot object types to consider for associations. It starts with known standard types and adds custom object types from the account.

**Data flow**: It receives a client. It copies the standard type list, fetches custom object schemas when available, extracts object type ids, and returns the combined unique list.

**Call relations**: _association_pairs_with_labels calls this before testing object-type pairs.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1674–1690)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches association labels for one source object type and one target object type. Missing or unsupported pairs become an empty list.

**Data flow**: It receives a client and two object type names. It calls HubSpot’s labels endpoint and returns valid label dictionaries, or returns an empty list for optional unavailable pairs.

**Call relations**: _association_pairs_with_labels calls this for each possible object-type pair.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1693–1710)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Normalizes one association label into a stable row. It adds source type, target type, category, readable label, and a compound id.

**Data flow**: It receives a raw label and from/to object types. It extracts type id, category, and label text, then returns a dictionary with normalized field names.

**Call relations**: _paginate_association_labels calls this while turning discovered labels into stream records.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1712–1724)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Pages through a CRM object type and returns only record ids. This is useful when another API needs ids as input.

**Data flow**: It receives a client and object type. It calls _paginate_crm_object_pages asking only for hs_object_id, extracts ids from each page, and yields lists of string ids.

**Call relations**: _paginate_associations uses this before batch association reads, and _paginate_sequence_enrollments uses it before checking contacts.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1726–1756)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks a CRM object list endpoint for a small set of properties. This is a lightweight alternative to full CRM search when only ids or simple identity fields are needed.

**Data flow**: It receives a client, object type, and property names. It GETs pages with limit and after cursor, yields dictionary records, and stops on optional unavailable object types.

**Call relations**: _paginate_crm_object_id_pages and _paginate_contact_identity_pages use this helper.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1759–1793)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Converts a batch association response into one row per relationship and association type. Some linked records can have multiple association type labels, so this may produce several rows.

**Data flow**: It receives raw association data plus source and target object types. It walks each result, each target record, and each association type, then builds normalized rows.

**Call relations**: _paginate_association_batch calls this after each batch read response.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1796–1823)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one normalized association record. The id combines both object types, both record ids, category, and association type so the relationship is stable.

**Data flow**: It receives one association type dictionary and source/target details. It extracts type id, category, and label, then returns a relationship row with normalized fields.

**Call relations**: _association_rows uses this for each individual source-to-target association it finds.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1826–1829)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether an error is acceptable for optional pair-style requests. Many HubSpot pairings or feature endpoints simply do not exist for some accounts.

**Data flow**: It receives an HTTP error. It returns true for 400 or 404 responses, or when _is_stream_unavailable identifies a permission-related 403.

**Call relations**: Association, consent, membership, sequence, and lightweight CRM helpers use this to skip unsupported optional calls without stopping the whole sync.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1831–1845)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads which records belong to each HubSpot list. It first discovers lists, then walks the membership endpoint for each list.

**Data flow**: It receives a client. It iterates through list records, extracts each list id, and yields membership pages from _paginate_memberships_for_list.

**Call relations**: _product_pages selects this for list_memberships. It depends on _paginate_lists for the parent list discovery.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1847–1890)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the members of one HubSpot list and adds list context to each membership row.

**Data flow**: It receives a client, list record, and list id. It pages the memberships endpoint, builds ids from list id and record id, adds list name and object type information, and yields pages.

**Call relations**: _paginate_list_memberships calls this once for each list.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1892–1905)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot communication subscription definitions. These define the subscription types that consent states can refer to.

**Data flow**: It receives a client. It calls the definitions endpoint, accepts either results or subscriptionDefinitions response shapes, assigns ids, and yields one page.

**Call relations**: _product_pages selects this for subscription_definitions.


##### `HubSpotConnector._paginate_consent_states`  (lines 1907–1920)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads email consent and unsubscribe information for contacts. It uses contact emails as the lookup key because HubSpot’s communication preference endpoints are email-based.

**Data flow**: It receives a client. It pages contacts with emails, then for each valid email fetches subscription status rows and unsubscribe-all rows, combines them into pages, and yields them.

**Call relations**: _product_pages selects this for consent_states. It coordinates _paginate_contact_identity_pages, _consent_status_rows, and _unsubscribe_all_rows.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1922–1938)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads contact ids and email addresses for consent lookups. It makes sure email is available at the top level even if HubSpot returned it inside properties.

**Data flow**: It receives a client. It pages contact records requesting email, extracts email from either the record or properties, and yields contact identity pages.

**Call relations**: _paginate_consent_states calls this before making communication preference requests.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 1940–1961)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches subscription-specific consent statuses for one contact email. These show whether the contact is opted in or out of particular email subscription types.

**Data flow**: It receives a client, contact row, and email. It URL-escapes the email, calls the statuses endpoint, converts each result with _consent_row, and returns the rows.

**Call relations**: _paginate_consent_states calls this for each contact email. It uses _is_optional_pair_unavailable to ignore absent optional responses.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 1963–1987)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches the global unsubscribe-all state for one contact email. This is separate from individual subscription statuses.

**Data flow**: It receives a client, contact row, and email. It URL-escapes the email, calls the unsubscribe-all endpoint, converts each result with _consent_row, and returns the rows.

**Call relations**: _paginate_consent_states calls this alongside _consent_status_rows for each contact email.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 1990–2021)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Normalizes one communication preference result into a consent state row. It adds contact id, email, purpose, status, legal basis, source, and stable id fields.

**Data flow**: It receives a raw preference row, contact, email, and status kind. It chooses subscription and business-unit identity parts, derives readable purpose fields, copies timestamps, and returns the combined row.

**Call relations**: _consent_status_rows and _unsubscribe_all_rows call this to produce records that record_identity can later identify consistently.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2023–2050)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sales sequences for each HubSpot user connected to an owner. HubSpot requires a user id filter, so the connector first discovers those users.

**Data flow**: It receives a client. It gets sequence user rows, then for each user calls the sequences endpoint with userId, adds owner context to each sequence, and yields pages.

**Call relations**: _product_pages selects this for sequences. It uses _sequence_user_rows and _paginate_get_collection.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows).


##### `HubSpotConnector._sequence_user_rows`  (lines 2052–2075)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the list of HubSpot users who can own sequences, based on owner records. It removes duplicate user ids.

**Data flow**: It receives a client. It pages owners, extracts userId, owner id, and email, keeps each user only once, and yields the collected user rows.

**Call relations**: _paginate_sequences calls this before requesting sequences by user id.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2077–2095)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sequence enrollment data for contacts. It checks each contact id because the HubSpot endpoint is contact-specific.

**Data flow**: It receives a client. It pages contact ids, calls the enrollment endpoint for each contact, converts responses with _sequence_enrollment_rows, and yields pages.

**Call relations**: _product_pages selects this for sequence_enrollments. It uses _paginate_crm_object_id_pages and _sequence_enrollment_rows.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2098–2112)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes sequence enrollment responses for one contact. It handles both list-shaped and single-object responses.

**Data flow**: It receives raw enrollment data and a contact id. It chooses result rows, assigns an id from the response or contact plus sequence, adds contact_id, and returns the rows.

**Call relations**: _paginate_sequence_enrollments calls this after each contact enrollment lookup.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2114–2143)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads submissions for every HubSpot form. It first lists forms, then walks each form’s submissions endpoint.

**Data flow**: It receives a client. It pages forms, extracts each form id, pages submissions with a smaller limit, creates stable ids from conversion id or form/time/index, adds form context, and yields pages.

**Call relations**: _product_pages selects this for form_submissions. It uses _paginate_get_collection for both form discovery and submission paging.

*Call graph*: calls 1 internal fn (_paginate_get_collection).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2145–2160)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages inside HubSpot conversation threads. Threads are the parent records; messages are fetched per thread.

**Data flow**: It receives a client. It pages conversation threads, extracts each thread id, pages that thread’s messages, adds thread_id to each message, and yields pages.

**Call relations**: _product_pages selects this for conversation_messages. It relies on _paginate_get_collection for both levels.

*Call graph*: calls 1 internal fn (_paginate_get_collection).


##### `HubSpotConnector._paginate_pipelines`  (lines 2162–2169)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sales and support pipelines for deals and tickets. Pipelines organize records into stages.

**Data flow**: It receives a client. It loops over supported pipeline object types, gets normalized rows for each type, and yields non-empty pages.

**Call relations**: _product_pages selects this for pipelines. It delegates object-specific normalization to _pipeline_rows_for_object_type.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2171–2221)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the stages inside deal and ticket pipelines. It turns nested stage data into standalone rows with status and closure information.

**Data flow**: It receives a client. For each pipeline object type, it fetches raw pipelines, walks their stages, builds compound ids, extracts metadata such as probability and closed state, and yields pages.

**Call relations**: _product_pages selects this for pipeline_stages. It uses _raw_pipelines_for_object_type to get the source data.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2223–2246)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes pipeline records for one object type, such as deals or tickets. It gives each pipeline an id that includes the object kind.

**Data flow**: It receives a client and object type. It fetches raw pipelines, skips rows without ids, adds object_kind, pipeline_id, name, and active or archived status, then returns the rows.

**Call relations**: _paginate_pipelines calls this for each supported pipeline object type.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2248–2259)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches raw pipeline data for one HubSpot object type. It treats missing or forbidden pipeline endpoints as empty instead of fatal.

**Data flow**: It receives a client and object type. It GETs the pipelines endpoint, returns dictionary results, or returns an empty list for 403 and 404 responses.

**Call relations**: _pipeline_rows_for_object_type and _paginate_pipeline_stages both call this before normalizing pipeline or stage records.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2262–2266)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects a specific HubSpot limitation: some objects cannot be paged through when archived=true. This lets the connector skip only that delete sweep.

**Data flow**: It receives an HTTP error. It checks for status 400, reads the upstream message, and returns true if it contains HubSpot’s known deleted-object paging warning.

**Call relations**: _paginate_archived_ids and _paginate_custom_object_archived_ids use this when deciding whether to stop an archived-record pass quietly.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2269–2277)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Extracts HubSpot’s message field from an HTTP error response. It is a small helper for interpreting API errors.

**Data flow**: It receives an HTTP error. It tries to parse JSON, checks for a dictionary message field, and returns that message as text or nothing.

**Call relations**: _is_archived_sweep_unsupported uses this to recognize HubSpot’s archived paging limitation.


##### `HubSpotConnector._paginate_junction`  (lines 2279–2326)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds simple relationship streams for selected parent-to-target pairs, such as deals to contacts. It reads associations embedded in parent object list responses.

**Data flow**: It receives a client, parent object, and target object. It pages parent records with associations requested, extracts each parent id and associated target id, creates one row per pair, and yields pages until no next cursor remains.

**Call relations**: _paginate_unchecked calls this for predefined junction streams. These streams full-refresh because HubSpot does not provide association modification timestamps.

*Call graph*: called by 1 (_paginate_unchecked).


### `extensions/sources/ufo_ext_sources/providers/instagram.py`

`io_transport` · `source sync, while reading Instagram streams from the Facebook Graph API`

Instagram business data is reached through Facebook Pages, so this connector starts there. It asks Facebook for the Pages available to the current grant, finds each Page's linked Instagram business account, then uses those accounts as the doorway to fetch media, stories, and insight metrics. Think of it like entering an office building through the front desk: the Page is the front desk, and the Instagram account is the department you actually want.

The file defines several streams, which are named groups of records the sync system can collect, such as pages, media, stories, and user insights. Some streams are incremental, meaning they use a saved timestamp watermark so the next run only keeps newer records instead of rereading everything.

The connector follows Facebook Graph API pagination, where each response contains a list of records plus a link to the next page. It also adds helpful context, such as the Instagram account ID, onto child records so later parts of the system know where each item came from.

A key behavior is its error handling. If Facebook refuses access to a whole stream because the token is invalid or missing permission, the stream is marked as skipped rather than crashing the entire run. But if an individual media or story insight cannot be read, the connector quietly skips just that object and continues.

#### Function details

##### `InstagramConnector._paged`  (lines 92–109)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one paginated Facebook Graph API collection from start to finish. It hides the repeated work of following each “next page” link and yields batches of records as they arrive.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the API for that path, looks inside the JSON response for the `data` list, yields that list when it is not empty, then follows the response's `paging.next` link until there are no more pages. It does not return one final list; instead, it streams batches outward over time.

**Call relations**: This is the low-level page-turning helper. `_pages` uses it to walk `/me/accounts`, and `_account_collection` uses it to walk each Instagram account's media or stories collection.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 111–116)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the Facebook Pages available to the current connection, including any linked Instagram business account information. This is the starting point for almost everything else in the connector.

**Data flow**: It sends a request for `/me/accounts` with fields for the Page and its linked Instagram business account. It collects all batches from `_paged` into one list and returns that list of Page records.

**Call relations**: This function is called when the system needs the root `pages` stream through `_root_pages`. It is also used by `_instagram_accounts`, because Instagram accounts are discovered from the Pages.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, _root_pages).


##### `InstagramConnector._instagram_accounts`  (lines 118–128)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Builds the list of Instagram business accounts linked to the available Facebook Pages. It also attaches the Page ID and Page name so each Instagram account can be traced back to its Page.

**Data flow**: It starts with the Page records returned by `_pages`. For each Page, it looks for an `instagram_business_account` object with an ID, copies that account's fields, adds `page_id` and `page_name`, and deduplicates accounts by account ID. It returns a list of unique Instagram account records.

**Call relations**: This is the bridge from Facebook Pages to Instagram data. `_root_pages` uses it for the `instagram_accounts` stream, while `_account_collection` and `_user_insights` use it before fetching account-level media, stories, or analytics.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _root_pages, _user_insights).


##### `InstagramConnector._account_collection`  (lines 130–151)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches a collection, such as media or stories, for every discovered Instagram business account. It can also apply a timestamp cursor so only newer items are passed along.

**Data flow**: It receives the collection name, requested fields, and optional cursor information. It first gets all Instagram accounts, then for each valid account ID it pages through that account's chosen collection. If a cursor is present, it drops records whose cursor field is not newer than the saved value. Before yielding a batch, it adds the Instagram account ID to each record.

**Call relations**: `_stream_pages` calls this when the requested stream is `media` or `stories`. It depends on `_instagram_accounts` to know which accounts to visit, `_paged` to walk each API collection, and `with_context` to label returned records with their parent account.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (_stream_pages); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 153–187)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches insight metrics for individual objects, such as posts or stories. It turns each metric into a record that clearly points back to the object it describes.

**Data flow**: It receives an async stream of source objects, such as media batches, plus a list of metric names and the target stream name. For each object with a valid ID, it calls that object's `/insights` endpoint. It converts returned insight entries into records with a generated ID, the parent object ID, and the stream name. If Facebook says a single object's insights are unavailable with certain expected error codes, it skips that object and keeps going.

**Call relations**: `_insight_pages` uses this to create the `media_insights` and `story_insights` streams. It consumes object batches produced by `paginate`, reads each object's insight data, and yields insight batches back to the sync runner.

*Call graph*: called by 1 (_insight_pages); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 189–219)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches daily account-level analytics for each Instagram business account, such as impressions, reach, and profile views. These are different from post or story insights because they describe the whole account over time.

**Data flow**: It starts by getting all Instagram accounts. For each account, it requests daily insight metrics from the API, then walks through each metric's `values` list. Each value becomes a row with a stable generated ID, the metric name, and the Instagram account ID. If a cursor is present, older or already-seen values are skipped.

**Call relations**: `_stream_pages` calls this for the `user_insights` stream. It relies on `_instagram_accounts` to choose accounts and uses helper functions to safely read API response lists before yielding rows to the sync system.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (_stream_pages); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 221–237)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector's public stream reader. The sync runner asks it for records from one stream, and it yields batches while translating permission failures into a clean “skipped stream” result.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It asks `_stream_pages` to produce the right batches for that stream and yields them onward. If Facebook responds with an authorization refusal, it raises `StreamSkipped` so the run records that this stream could not be accessed; other HTTP errors are allowed to fail normally.

**Call relations**: The wider source-sync framework calls this method to read a stream. `_insight_pages` also calls it internally to fetch media or story objects before reading their per-object insights.

*Call graph*: calls 2 internal fn (__init__, _stream_pages); called by 1 (_insight_pages).


##### `InstagramConnector._stream_pages`  (lines 239–273)

```
def _stream_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct reading path for a stream name. It is the connector's dispatcher: given `media`, `stories`, `pages`, or an insights stream, it routes the request to the helper that knows how to fetch that kind of data.

**Data flow**: It receives the stream name and optional cursor. It compares the name against the supported streams and returns an async iterator from the matching helper. For unsupported names, it raises `StreamSkipped` to say this connector does not implement that stream.

**Call relations**: `paginate` calls this whenever a stream needs to be read. It hands root streams to `_root_pages`, media and stories to `_account_collection`, object insight streams to `_insight_pages`, and account-level analytics to `_user_insights`.

*Call graph*: calls 5 internal fn (__init__, _account_collection, _insight_pages, _root_pages, _user_insights); called by 1 (paginate).


##### `InstagramConnector._root_pages`  (lines 275–282)

```
async def _root_pages(self, client: httpx.AsyncClient, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields the top-level records for either Facebook Pages or linked Instagram accounts. It wraps simple list-producing helpers in the batch format expected by the rest of the sync system.

**Data flow**: It receives the requested root stream name. If the name is `pages`, it fetches Page records; otherwise it fetches Instagram account records. If any records exist, it yields them as one batch.

**Call relations**: `_stream_pages` uses this for the `pages` and `instagram_accounts` streams. It delegates the actual API reading to `_pages` or `_instagram_accounts` and adapts their returned lists into stream batches.

*Call graph*: calls 2 internal fn (_instagram_accounts, _pages); called by 1 (_stream_pages).


##### `InstagramConnector._insight_pages`  (lines 284–293)

```
def _insight_pages(self, client: httpx.AsyncClient, source: str, metrics: str, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds an insight stream from another object stream, such as turning media records into media insight records. It connects the source objects to the per-object insight reader.

**Data flow**: It receives the source stream name, the metric names to request, and the output insight stream name. It finds the stream definition for the source, calls `paginate` to read those source objects without a cursor, and passes that object stream into `_object_insights`. The output is an async iterator of insight batches.

**Call relations**: `_stream_pages` calls this for `media_insights` and `story_insights`. It acts as the middle step between general stream pagination and the specialized `_object_insights` function that reads metrics for each individual object.

*Call graph*: calls 2 internal fn (_object_insights, paginate); called by 1 (_stream_pages).


### Email marketing platforms
Turns customer marketing, audience, campaign, event, report, and email activity APIs into consistent sync records.

### `extensions/sources/ufo_ext_sources/providers/klaviyo.py`

`io_transport` · `source sync and pagination`

Klaviyo exposes its data through a web API, but the raw responses are shaped for Klaviyo, not for this project. This file is the adapter between the two worlds. It defines which Klaviyo resources can be synced, how to authenticate, how to ask for only new or changed records, how to follow Klaviyo’s next-page links, and how to reshape each record into a simpler form.

The connector works like a careful librarian copying books from one library catalog into another. First it lists the shelves it knows about: profiles, campaigns, events, flows, catalog records, and more. For streams that can be synced incrementally, it asks Klaviyo for records sorted by a time field, so the next run can continue from the last seen time instead of starting over.

Klaviyo returns records wrapped in layers such as attributes and relationships. The `flatten` method unwraps useful fields so later code can read plain keys like `updated`, `email_consent`, `subject_line`, or `metric_id`. Events get extra help: when Klaviyo includes metric details, the connector copies the metric name onto the event.

If Klaviyo refuses access to a stream because the API key lacks permission, the connector marks that stream as skipped instead of crashing the whole sync. This file is read-only; it never writes back to Klaviyo.

#### Function details

##### `_stream`  (lines 36–54)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', created_at_field: str='created', updated_at_field: str | None='updated', canonical:
```

**Purpose**: This helper creates a standard description of one Klaviyo data stream, such as profiles or campaigns. It saves repeated setup code by filling in common defaults like the primary key and timestamp fields.

**Data flow**: It receives a stream name and optional details such as the Klaviyo API object name, cursor field, and created or updated timestamp fields. It combines those values with defaults, then returns a `StreamSpec`, which is the project’s small recipe for how that stream should be synced.

**Call relations**: This helper is used while the file is being loaded to build the `KLAVIYO_STREAMS` list. That list is later attached to `KlaviyoConnector`, so the rest of the source framework knows which Klaviyo resources this connector can read.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 113–121)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function prepares the web client used to talk to Klaviyo. It adds Klaviyo’s required API version header and, when a direct private key is available, adds Klaviyo’s special authorization header.

**Data flow**: It receives the base Klaviyo URL and a credential. It first asks the parent REST connector to create the basic HTTP client, then adds a pinned `revision` header and possibly an `Authorization` header containing the Klaviyo API key. It returns the ready-to-use client.

**Call relations**: The source framework calls this when setting up the connector’s connection to Klaviyo. After this point, pagination and API reads use the configured client so every request carries the headers Klaviyo expects.


##### `KlaviyoConnector._next_path`  (lines 124–135)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This function converts Klaviyo’s full next-page URL into the path and query form this connector’s base web client needs. It is what lets the connector keep walking through paged results.

**Data flow**: It receives Klaviyo’s `links.next` value, which may be a full URL or may be missing. If there is no usable link, it returns `None`. If there is one, it parses the URL, keeps only the path and query string, and returns that smaller request path.

**Call relations**: During `KlaviyoConnector.paginate`, each API response may point to the next page. `paginate` hands that link to `_next_path`; if a path comes back, the loop fetches another page, and if `None` comes back, pagination stops.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 138–143)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: This function chooses the Klaviyo timestamp field that should be used for incremental syncing. Different Klaviyo resources use different field names, so this keeps that choice in one place.

**Data flow**: It receives a stream description. For events it returns `datetime`; for campaigns, forms, and images it returns `updated_at`; for most other streams it returns `updated`.

**Call relations**: This helper supports the query-building logic in `_initial_query`. It ensures that when the connector asks Klaviyo for sorted or filtered records, it uses the field name Klaviyo expects for that specific stream.


##### `KlaviyoConnector._initial_query`  (lines 146–160)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function builds the query parameters for the first request to a Klaviyo stream. It sets the page size, adds incremental filtering when there is a saved cursor, and requests a few extra details for streams that need them.

**Data flow**: It receives a stream description and an optional cursor value from a previous sync. It creates a parameter dictionary with the page size. If the stream supports cursor-based syncing, it adds sorting and, when a cursor exists, a filter asking Klaviyo for records at or after that time. For profiles it asks for subscription details, and for events it asks Klaviyo to include metric information. It returns the parameter dictionary.

**Call relations**: `KlaviyoConnector.paginate` calls this before fetching the first page of a stream. Later pages do not reuse these parameters because Klaviyo’s `links.next` already contains the needed paging information.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 163–174)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: This small helper safely pulls an ID out of Klaviyo’s nested relationship structure. It avoids errors when Klaviyo leaves a relationship out or returns it in an unexpected shape.

**Data flow**: It receives a relationships object and the relationship name to look for, such as `profile`, `metric`, or `list`. It checks each nested level before reading from it. If it finds an ID, it returns it as text; otherwise it returns `None`.

**Call relations**: `KlaviyoConnector.flatten` uses this when turning Klaviyo records into simpler records. It is especially useful for events, which refer to profiles and metrics, and for segments that may refer back to a parent list.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 176–206)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes one raw Klaviyo record into the flatter record format the rest of the system expects. It makes important fields easy to read and places cursor fields at the top level so incremental syncing can advance correctly.

**Data flow**: It receives a raw Klaviyo record and the stream it came from. It starts a new dictionary with the record ID and resource type, copies top-level values from Klaviyo’s `attributes`, removes profile counts from lists and segments, and then adds stream-specific useful fields. Profiles get email consent information, campaigns get subject and sender information, events get linked profile and metric IDs, and segments may get their parent list ID. It returns the flattened dictionary.

**Call relations**: The broader REST source flow calls `flatten` after records have been fetched. Inside this function, profile-specific work is handed to `_flatten_profile`, campaign-specific work is handed to `_flatten_campaign`, and relationship IDs are read through `_lift_relationship_id`.

*Call graph*: calls 3 internal fn (_flatten_campaign, _flatten_profile, _lift_relationship_id).


##### `KlaviyoConnector._flatten_profile`  (lines 209–223)

```
def _flatten_profile(flat: dict[str, Any], attrs: Any) -> None
```

**Purpose**: This function extracts email marketing consent and suppression information from a Klaviyo profile. That makes privacy and subscription state visible without forcing later code to understand Klaviyo’s deeply nested profile shape.

**Data flow**: It receives the flat record being built and the original attributes section from Klaviyo. It looks under subscriptions, then email, then marketing. If consent is present, it writes `email_consent`. If suppression information is present, it writes a simple `email_suppression` value, using the first suppression reason or code when Klaviyo sends a list.

**Call relations**: `KlaviyoConnector.flatten` calls this only for the profiles stream. It enriches the flattened profile record in place, then returns control to `flatten`, which returns the completed record.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector._flatten_campaign`  (lines 226–244)

```
def _flatten_campaign(flat: dict[str, Any], attrs: Any) -> None
```

**Purpose**: This function extracts campaign email details that are useful for recall, such as the subject line, sender name, sender email, and primary audience list. These details may live in more than one place in Klaviyo’s response, so the function checks the nested campaign audience settings first and then falls back to simpler fields.

**Data flow**: It receives the flat campaign record being built and the original attributes from Klaviyo. It looks for nested audience message settings and copies subject, sender label, and sender email when found. It also looks for the first included audience list and saves it as `primary_list_id`. Finally, it fills missing subject or sender fields from direct attributes if available.

**Call relations**: `KlaviyoConnector.flatten` calls this for campaign records. It modifies the flat campaign dictionary in place so the final returned record contains human-useful campaign context.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.paginate`  (lines 246–299)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous function reads all pages for one Klaviyo stream. It starts at the stream’s API endpoint, follows Klaviyo’s next-page links until there are no more, and yields each page of records to the sync process.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a prior sync. It builds the first request path and query, fetches a page, extracts the records, and yields them when present. For events, it also reads included metric records and copies metric names onto the event attributes. After each page, it reads `links.next`, converts it with `_next_path`, and repeats until no next path remains. If Klaviyo returns a forbidden or unauthorized status, it raises `StreamSkipped` so that stream is recorded as skipped rather than stopping the whole run.

**Call relations**: The source framework calls `paginate` when it needs records from a Klaviyo stream. `paginate` relies on `_initial_query` to form the first request and `_next_path` to continue through later pages. When access is refused, it hands control back to the framework through `StreamSkipped`, signaling that the stream could not be read because the granted API key lacks the needed scope.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/providers/mailchimp.py`

`io_transport` · `source sync / request handling`

Mailchimp stores useful marketing data in several places, and many of those places are nested. For example, members belong to an audience list, interests belong to an interest category inside a list, and email activity belongs to a campaign report. This file is the map and walking guide for collecting all of that data safely and predictably.

It defines the Mailchimp streams the system can read, including what each stream is called, which field identifies a record, and which date field can be used for incremental syncing. Incremental syncing means asking Mailchimp only for records changed since a known time, rather than starting over every run.

The main class, `MailchimpConnector`, is a read-only connector built on the shared REST connector. It knows Mailchimp's paging style: requests use `count` and `offset`, and responses wrap records under names like `lists`, `members`, or `reports`. For simple resources, it walks pages directly. For nested resources, it first gathers parent IDs, then visits each child endpoint. Like checking every folder in a filing cabinet, it opens each parent container before collecting the records inside.

A few details make the synced data easier to use later. Child records are stamped with their parent ID, unsubscribe records get a combined identity from campaign and email, and email activity is split into one row per action because Mailchimp groups several actions under one recipient.

#### Function details

##### `_stream`  (lines 62–80)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str='created_at', updated_at_field: str | None='updated_at', canonical
```

**Purpose**: This helper creates a `StreamSpec`, which is the system's small description card for one Mailchimp data stream. It keeps the stream definitions short and consistent.

**Data flow**: It receives a stream name and optional details such as the Mailchimp object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults when details are not provided, then returns a `StreamSpec` object that the connector uses later to know how to sync that stream.

**Call relations**: This is used while building the file-level `MAILCHIMP_STREAMS` list. It hands each completed stream description to the rest of the connector setup, so the base source framework can discover what Mailchimp streams exist.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector.record_identity`  (lines 148–155)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This chooses the stable identity for a record. It has special behavior for unsubscribe records because Mailchimp identifies them best by combining the campaign and email IDs.

**Data flow**: It receives one record and the stream description. For most streams, it lets the shared REST connector decide the identity. For `unsubscribes`, it reads `campaign_id` and `email_id`; if either is missing it returns nothing, otherwise it returns a combined value like `campaign:email`.

**Call relations**: The wider sync process calls this when it needs to know whether a record is new, changed, or already known. It usually delegates upward to the base connector, but for unsubscribes it supplies Mailchimp-specific identity logic.


##### `MailchimpConnector.flatten`  (lines 157–163)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This lightly reshapes records before they leave the connector. For list and segment members, it creates a standard `created_at` value from Mailchimp's signup or opt-in timestamps.

**Data flow**: It receives a record and its stream description. If the record is from `list_members` or `segment_members`, it copies the record and adds `created_at` from `timestamp_signup`, falling back to `timestamp_opt`. Other records pass through unchanged.

**Call relations**: The base sync flow calls this after records are fetched, before they are stored or passed onward. It does not call other helper functions; it simply makes member records line up better with the system's expected time fields.


##### `MailchimpConnector._data_field`  (lines 166–167)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This tells the connector which JSON field contains the actual list of records in a Mailchimp response. Mailchimp wraps different resources under different names, such as `members` or `campaigns`.

**Data flow**: It receives a stream description. It looks up the stream name in the Mailchimp field map and returns the matching response field; if no special field is listed, it uses the stream name itself.

**Call relations**: The pagination helpers call this before fetching pages, so they know where to look inside each Mailchimp response. It feeds `_paginate_top_level`, `_paginate_per_list`, and `_paginate_per_report`.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 170–177)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This turns a saved cursor value into the Mailchimp query parameter that asks for only newer or changed records. A cursor is the remembered point in time from the last sync.

**Data flow**: It receives a stream description and an optional cursor string. If there is no cursor, no cursor field, or Mailchimp has no matching filter for that field, it returns an empty set of parameters. Otherwise it returns a small dictionary such as `{since_last_changed: cursor}`.

**Call relations**: Pagination helpers call this when they can ask Mailchimp to filter on the server side. It is used by top-level, per-list, per-report, and segment-member pagination before those helpers hand parameters to the lower-level page fetcher.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 179–235)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for Mailchimp syncing. Given a stream name, it chooses the right walking strategy for that part of the Mailchimp API.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks which family the stream belongs to: simple top-level resources, children of lists, deeper list children, children of reports, or email activity. It then yields pages of records from the matching helper. If Mailchimp returns a 401 or 403 refusal, it turns that into a skipped stream with a clear message.

**Call relations**: The source framework calls this when it wants records for a Mailchimp stream. This function delegates to the specialized pagination helpers and shields the wider sync from permission failures by raising `StreamSkipped` for refused streams.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 237–248)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads simple Mailchimp collections that live directly at one endpoint, such as lists, campaigns, automations, and reports.

**Data flow**: It receives the HTTP client, stream description, endpoint path, and optional cursor. It finds the response field to read, builds any cursor filter, and asks the shared offset-page fetcher for pages using Mailchimp's `count` page size parameter. It yields each page of records.

**Call relations**: `paginate` calls this for top-level streams. This helper prepares Mailchimp-specific details, then relies on the base REST connector's offset paging machinery to do the repeated requests.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 250–267)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the reusable page walker for nested Mailchimp endpoints. It is used whenever records live underneath a parent object, such as list members under a list or unsubscribes under a report.

**Data flow**: It receives the HTTP client, an endpoint path, the response field that contains records, and optional base query parameters. It calls the shared offset-page fetcher with Mailchimp's page size and `count` parameter, then yields each resulting page.

**Call relations**: The more specific nested pagination helpers call this after they have built the correct endpoint path. It keeps all child-endpoint paging behavior in one place.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 269–277)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: This fetches IDs from a paged Mailchimp collection. It is a small helper for discovering parent objects before walking their children.

**Data flow**: It receives an endpoint path and the response field containing rows. It pages through that collection, checks each row, and yields the `id` value as text when one is present.

**Call relations**: `_list_ids` and `_report_ids` call this to avoid duplicating the same ID-gathering pattern. Those parent IDs are then used by nested pagination helpers.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 279–281)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This produces the IDs of all Mailchimp audience lists. Many other streams need these list IDs before they can fetch list-specific data.

**Data flow**: It receives the HTTP client. It asks `_ids` to walk the `/3.0/lists` endpoint and read IDs from the `lists` response field, then yields each list ID.

**Call relations**: Nested list flows call this first, including per-list records, interests, and segment members. It is the connector's way of finding every audience list before opening each list's related collections.

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 283–285)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This produces the IDs of all Mailchimp campaign reports. Report IDs are needed before fetching report-specific details such as unsubscribes and email activity.

**Data flow**: It receives the HTTP client. It asks `_ids` to walk the `/3.0/reports` endpoint and read IDs from the `reports` response field, then yields each report ID.

**Call relations**: Report-based pagination helpers call this before visiting child endpoints. It supplies the parent IDs used by `_paginate_per_report` and `_paginate_email_activity`.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 287–308)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads collections that exist once per audience list, such as members, segments, tags, and interest categories. It also adds the parent `list_id` to child records so their origin is not lost.

**Data flow**: It receives the HTTP client, stream description, child path name, optional cursor, and the parent field to stamp. It gets the right response field and cursor parameters, loops through every list ID, builds that list's child endpoint, fetches pages from it, adds `list_id` to each record when requested, and yields the pages.

**Call relations**: `paginate` calls this for streams that are direct children of lists. It uses `_list_ids` to find parents, `_paginate_child` to fetch child pages, and URL quoting to safely place Mailchimp IDs inside endpoint paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 310–332)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads interests, which are nested two levels deep: inside interest categories, which are inside audience lists. It adds both the list ID and category ID to each interest record.

**Data flow**: It receives the HTTP client, stream description, and optional cursor. It loops through each list, fetches that list's interest categories, then for each category fetches its interests. Each interest record is stamped with `list_id` and `category_id`, then pages of interests are yielded.

**Call relations**: `paginate` calls this only for the `interests` stream. It uses `_list_ids` to find lists and `_paginate_child` twice: first for categories, then for interests inside each category.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 334–356)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads members inside each segment of each audience list. It preserves context by adding both the list ID and segment ID to every member record.

**Data flow**: It receives the HTTP client, stream description, and optional cursor. It builds cursor parameters if possible, loops through every list, fetches that list's segments, then fetches members for each segment. Each member record gets `list_id` and `segment_id`, and pages are yielded.

**Call relations**: `paginate` calls this for the `segment_members` stream. It combines `_list_ids` for parent discovery, `_paginate_child` for both segment and member pages, and URL quoting to safely build nested Mailchimp paths.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 358–378)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads collections that live under each campaign report, such as unsubscribes. It adds the parent campaign or report ID to each child record so the child can be traced back.

**Data flow**: It receives the HTTP client, stream description, report child path, optional cursor, and parent field to stamp. It finds the data field and cursor parameters, loops through report IDs, builds each report child endpoint, fetches pages, stamps the parent ID when requested, and yields the pages.

**Call relations**: `paginate` calls this for report child streams such as `unsubscribes`. It uses `_report_ids` to find parent reports, `_paginate_child` to fetch records, and `_data_field` and `_cursor_params` to match Mailchimp's response and filtering rules.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 380–412)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads email activity from each campaign report and turns Mailchimp's grouped activity lists into separate event records. That makes each open, click, or other action easier to store and compare over time.

**Data flow**: It receives the HTTP client and optional cursor. It builds a `since` filter if a cursor exists, loops through report IDs, fetches each report's email activity, and then splits each recipient's `activity` array into individual rows. Each row keeps the recipient details, gets the campaign ID, and receives a synthesized stable ID made from email ID, action, and timestamp. It yields only non-empty exploded pages.

**Call relations**: `paginate` calls this for the `email_activity` stream. It uses `_report_ids` to find reports and `_paginate_child` to fetch Mailchimp's grouped records, then performs the Mailchimp-specific reshaping itself before handing rows back to the sync flow.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


### Enterprise CRM and forms
Adds standard Salesforce CRM records and Typeform form-response data as recallable source pages.

### `extensions/sources/ufo_ext_sources/providers/salesforce.py`

`io_transport` · `source sync`

Salesforce stores each kind of business object, such as an Account or Contact, as an “SObject.” This file defines a Salesforce connector that reads those objects through Salesforce’s REST API, which is a web interface for asking Salesforce for data. Rather than keeping a fixed list of fields, it first asks Salesforce to describe each object. That matters because different Salesforce organizations can expose different fields, including custom ones.

The connector then builds a SOQL query. SOQL is Salesforce’s query language, similar in spirit to asking, “give me these columns from this table.” It reads records in small batches, ordered by Salesforce’s update timestamp, so later syncs can continue from the last known point instead of rereading everything.

A notable detail is deletion tracking. If the connector already has a cursor, it also asks Salesforce which records were hard-deleted since that time. It returns those as tombstones, meaning “this record used to exist, but should now be treated as deleted.” This is like leaving a forwarding notice after removing a file, so downstream systems know not to keep stale data.

The connector only reads. It does not write back to Salesforce. It also does not store credentials itself; another part of the runner supplies the authorized HTTP client. If Salesforce refuses access with a permission or authentication error, the stream is skipped with a clear reason.

#### Function details

##### `_stream`  (lines 29–38)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Salesforce stream, such as accounts or contacts. It gives the sync system the object name, the record ID field, and the timestamp fields used to track changes.

**Data flow**: It receives a friendly stream name, the Salesforce SObject name, and whether that stream is considered canonical. It fills in the common Salesforce conventions, such as Id as the primary key and SystemModstamp as the update cursor. It returns a StreamSpec object that the connector later uses to know what to query.

**Call relations**: This helper is used while the file is loaded to build the Salesforce stream list. It hands each completed StreamSpec to the connector class through that list, so later sync runs know which Salesforce objects are available.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 78–82)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: This function builds the Salesforce query text used to fetch records for one stream. It includes the fields to read, the object to read from, and, when available, a cursor so only newer changes are requested.

**Data flow**: It receives a stream description, a list of field names discovered from Salesforce, and an optional cursor timestamp. It turns those into one SOQL query string with selected columns, an optional “changed after this cursor” condition, sorting by the cursor field, and a fixed page limit. The output is plain query text sent to Salesforce.

**Call relations**: The paginate method calls this after it has asked Salesforce what fields exist. The resulting query is then passed into Salesforce’s query endpoint so the connector can start reading records in update order.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 84–90)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: This function asks Salesforce which fields exist on a specific SObject. It lets the connector adapt to each Salesforce organization instead of relying on a hard-coded schema.

**Data flow**: It receives an authorized HTTP client and a Salesforce object name. It calls Salesforce’s describe endpoint, reads the returned field list, keeps valid field names, and returns them as strings. Badly shaped or nameless field entries are ignored.

**Call relations**: The paginate method calls this before building a query. Its field list feeds directly into _build_soql, which means every sync uses the fields Salesforce says are currently available.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 92–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main read loop for a Salesforce stream. It fetches records page by page and, on incremental runs, also reports records that were deleted since the last cursor.

**Data flow**: It receives an authorized HTTP client, a stream description, and an optional cursor. First it discovers the stream’s fields, then builds a SOQL query, then repeatedly calls Salesforce’s query API. Each batch of records is yielded to the caller. If Salesforce says there are more pages, it follows the next page URL. After normal records are read, if a cursor was provided, it asks for deleted records and may yield a special StreamPage containing delete tombstones and the next cursor.

**Call relations**: This method is the connector’s central worker during a sync. It calls _describe_fields to learn the available columns, _build_soql to create the query, and _deleted_page to capture deletions. If Salesforce responds with an authentication or permission refusal, it raises StreamSkipped so the wider sync runner can skip that stream cleanly instead of crashing the whole job.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 121–139)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: This function asks Salesforce which records in a stream were hard-deleted during a time window. It packages those missing record IDs as tombstones so downstream storage can remove or hide stale records.

**Data flow**: It receives an authorized HTTP client, a stream description, and the previous cursor timestamp. It sets the end of the window to the current UTC time, asks Salesforce’s deleted-records endpoint for deletions between the cursor and that end time, extracts deleted record IDs, and chooses the next cursor from Salesforce’s covered date or the window end. It returns a StreamPage containing the deleted IDs and next cursor, or nothing if there is no useful page.

**Call relations**: The paginate method calls this after reading normal records, but only when there is already a cursor. Its StreamPage is yielded back into the same sync flow as normal pages, allowing the rest of the system to process deletions alongside updates.

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 141–144)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function cleans up each Salesforce record before the rest of the system sees it. It removes Salesforce’s metadata wrapper called attributes, leaving only the actual field values.

**Data flow**: It receives one record dictionary and the stream it came from. If the record contains an attributes entry, it returns a new dictionary without that entry. If there is no such entry, it returns the original record unchanged.

**Call relations**: This fits into the connector’s normal record-cleaning step after records are fetched. It does not call other project functions here; it simply shapes Salesforce’s response into a cleaner form for downstream sync and indexing.


### `extensions/sources/ufo_ext_sources/providers/typeform.py`

`io_transport` · `source sync`

This connector is the bridge between UFO and Typeform. Typeform is an online form service, and its API returns data in separate groups, or “streams,” such as forms and form responses. Without this file, UFO would not know which Typeform endpoints to call, how to move through paginated results, or how to attach useful form context to responses and webhooks.

The file defines the Typeform streams first, including each stream’s name, main identifier, and date fields used for incremental syncing. Incremental syncing means “only fetch things newer than the last successful run” instead of rereading everything every time.

The main class, TypeformConnector, reads from Typeform using an HTTP client. Most Typeform list endpoints use page numbers, like turning pages in a catalog. The connector keeps asking for page 1, page 2, and so on until Typeform says there are no more pages. Responses are different: they belong to individual forms, so the connector first lists all forms, then asks Typeform for the responses for each form. It also stamps each response with the form ID and title, so later readers know where the response came from.

If Typeform rejects access with a “not allowed” response, the connector skips that stream rather than crashing the whole sync. This matters because a user’s Typeform token may not have permission for every kind of data.

#### Function details

##### `TypeformConnector.record_ref`  (lines 52–56)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This chooses the human-meaningful reference used for a Typeform record. Webhooks are special because their useful reference is the webhook tag, while other streams use the normal reference behavior from the shared REST connector.

**Data flow**: It receives one record and the stream it belongs to. If the stream is webhooks, it looks for the record’s tag and turns it into text when possible; otherwise it leaves the decision to the standard connector behavior. The result is either a string reference or nothing if no safe reference can be made.

**Call relations**: The wider sync system calls this when it needs a stable label for a record. This method only changes the story for webhooks; for every other Typeform stream, it hands the choice back to the parent REST connector.


##### `TypeformConnector.paginate`  (lines 58–85)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the dispatcher that knows how to fetch each Typeform stream. Given a stream name, it sends the work to the right helper and yields batches of records back to the sync runner.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. It checks the stream name, calls the matching helper, and passes each returned page of records onward. If the stream is unknown, or if Typeform refuses access with a 401 or 403 status, it raises StreamSkipped so the runner can skip that stream cleanly.

**Call relations**: The source runner asks paginate for records during a Typeform sync. paginate then calls _forms for forms, _responses for form answers, _paged_items for simple list-style streams, and _webhooks for webhooks. It is the traffic director that keeps stream-specific details out of the runner.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 87–105)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Typeform endpoints that use ordinary page-number pagination. It keeps fetching pages until it has reached the last page or the endpoint appears to have run out of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It adds a page number and page size, asks Typeform for that page, extracts the list under the items field, and yields the records when there are any. It then decides whether to stop based on Typeform’s page_count value or, if that is missing, whether the page was smaller than the usual page size.

**Call relations**: paginate uses this directly for streams like workspaces, images, and themes. _forms also uses it to fetch the forms list before applying any date filtering. It relies on records_at to pull the actual list of records out of Typeform’s response envelope.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 107–114)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches Typeform forms and optionally filters them for incremental syncs. It is the source of form information used both as its own stream and as the starting point for response and webhook fan-out.

**Data flow**: It receives an HTTP client and an optional cursor date or timestamp. It asks _paged_items for all form pages, then, when a cursor is present, keeps only forms whose last_updated_at value is newer than that cursor. It yields only non-empty batches of forms.

**Call relations**: paginate calls this when syncing the forms stream. _responses and _webhooks also call it because those records are tied to specific forms; they need the form list first so they know which form-specific Typeform endpoints to visit.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 116–138)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches submitted answers for every Typeform form. Because responses live under each form, it first finds the forms, then walks through each form’s response pages.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all forms, skips any form without a usable string ID, and builds request parameters. If a cursor exists, it sends it as Typeform’s since parameter so only newer responses are requested. For each response page, it adds form_id and form_title to every item, then yields those enriched response records.

**Call relations**: paginate calls this when the requested stream is responses. _responses calls _forms to discover which form endpoints to query, then uses the shared cursor-based paging behavior from the REST connector. Before yielding data, it calls with_context so downstream storage can tell which form each response belongs to.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 140–149)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches the webhooks configured for each Typeform form. It adds form details to each webhook so the record is understandable outside the narrow API endpoint it came from.

**Data flow**: It receives an HTTP client. It first gets all forms, skips forms without a usable string ID, then requests the webhooks endpoint for each form. It extracts the list of webhook records from the items field, adds form_id and form_title to each record, and yields the enriched records when any exist.

**Call relations**: paginate calls this when syncing the webhooks stream. Like _responses, it depends on _forms because Typeform webhooks are fetched form by form. It uses records_at to pull records from the API response and with_context to attach the form information before handing records back to the sync flow.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).
