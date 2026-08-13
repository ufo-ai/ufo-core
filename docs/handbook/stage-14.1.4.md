# CRM, Sales, and Customer Support Source Connectors  `stage-14.1.4`

This stage is the customer-data intake area of the system. It runs during sync work, when the project reaches out to outside services and brings back records that can be stored, searched, and reused later. Each source file is like an adapter plug for a different service. Attio reads companies, people, deals, tasks, notes, meetings, and call recordings, flattening Attio’s nested replies into simpler records. HubSpot covers a wide range of sales and marketing data, including contacts, companies, deals, assets, analytics, and links between records. Salesforce reads objects such as Accounts and Contacts, including signs that records were deleted, but it never writes changes back. Freshdesk focuses on helpdesk data and knows how to sign in and move through Freshdesk’s different page-by-page result formats. Intercom brings in conversations, contacts, companies, tickets, tags, and activity logs. Zendesk imports support tickets, users, organizations, help articles, and community posts. Together, these connectors translate many customer-facing tools into one steady record stream.

## Files in this stage

### CRM and Sales Platforms
Connectors that sync customer relationship, sales pipeline, account, contact, deal, and related business records from CRM systems.

### `extensions/sources/ufo_ext_sources/attio.py`

`io_transport` · `during Attio source sync`

Attio’s API does not return one simple, uniform shape for all data. Companies, people, and deals are fetched one way; tasks and notes another way; meetings and call recordings use a newer paging style. This connector is the adapter that knows those differences, like a travel plug that lets the same machine work with different wall sockets.

The file defines which Attio streams exist and what field uniquely identifies each item. It then provides an AttioConnector class that can ask Attio for pages of data, detect when a stream should be skipped instead of treated as a failure, and reshape each returned item into a flatter form.

The flattening is especially important. Attio stores most fields inside nested "value cells", where the useful value might be under keys like value, option.title, email_address, phone_number, or target_record_id. Without flattening, records would not have a clear top-level ID or easy-to-read fields. For call recordings, the connector also walks through meetings, fetches each recording, asks for its transcript when available, and combines transcript segments into readable text.

This connector only reads from Attio. It does not write changes back.

#### Function details

##### `_records_stream`  (lines 35–43)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates the standard description for an Attio object stream such as companies, people, or deals. The system uses this description to know the stream name, the Attio object slug, the primary key, and that missing records should be deleted during a full snapshot.

**Data flow**: It receives a friendly stream name, an Attio object slug, and whether the stream is canonical. It packages those choices into a StreamSpec with record_id as the unique identifier and no incremental cursor. The result is a reusable stream definition.

**Call relations**: This helper is used while the file is loaded to build the Attio stream list. It hands the details to StreamSpec so the wider sync framework can treat these Attio objects consistently.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 70–71)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: Safely pulls a named ID out of a nested dictionary. It avoids errors when Attio gives a missing or unexpected shape.

**Data flow**: It receives any value and the name of an ID field. If the value is a dictionary, it returns that field; otherwise it returns nothing. The input is unchanged.

**Call relations**: AttioConnector._value_primitive calls this when an option or status has an ID wrapped inside another object. It is a small safety helper used during field flattening.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 80–81)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: Builds the request body used to ask Attio for one page of object records. It sets the page size and where Attio should start reading.

**Data flow**: It receives an offset number. It returns a dictionary containing the fixed record limit and that offset. Nothing else is changed.

**Call relations**: AttioConnector.paginate calls this each time it requests another page from the standard object records endpoint.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._value_primitive`  (lines 84–127)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: Extracts the most useful plain value from one Attio value cell. Attio stores different field types in different places, so this function finds the human-readable value wherever Attio put it.

**Data flow**: It receives one dictionary from Attio. It checks for known shapes such as text values, select options, statuses, email addresses, phone numbers, domains, money amounts, references, actors, and locations. It returns a simple value such as a string, number, ID-like label, address text, or nothing if it cannot recognize a useful value.

**Call relations**: This is the core translator used by the flattening helpers. When it needs an ID nested inside an option or status, it calls _nested_id.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 130–145)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: Turns one Attio field cell into a single plain value when that is the natural shape. It also preserves multiple select-style choices when there is more than one option.

**Data flow**: It receives a cell that may be a list, dictionary, or already-simple value. For lists, it extracts primitives from each item and removes empty values; usually it returns the first useful value, but for multi-option cells it returns the list. For dictionaries, it extracts one primitive. For simple values, it returns the value as-is.

**Call relations**: This is used by AttioConnector._flatten_values to simplify most record attributes before records are handed back to the sync system.


##### `AttioConnector._flatten_list_cell`  (lines 148–155)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: Turns an Attio field cell into a list of plain values. It is used for fields where keeping all values matters, such as emails, phone numbers, domains, or categories.

**Data flow**: It receives a cell that may or may not already be a list. If it is not a list, it flattens it once and wraps the result in a list when present. If it is a list, it extracts a primitive from each item and removes empty values. The output is always a list.

**Call relations**: AttioConnector._flatten_values uses this for known multi-value fields so the connector does not accidentally drop extra emails, domains, categories, or phone numbers.


##### `AttioConnector._flatten_values`  (lines 158–192)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts Attio’s nested attributes dictionary into ordinary top-level fields. This makes records easier for the rest of the system to store, display, and search.

**Data flow**: It receives Attio’s values dictionary, where each attribute slug points to one or more value cells. It flattens each cell, keeps selected fields as lists, and adds convenient shortcut fields such as first_name, last_name, domain, category, email, and phone when possible. It returns a new flat dictionary.

**Call relations**: AttioConnector._flatten_record uses this after it has lifted the record identity fields. It is the main cleanup step for companies, people, deals, and other object records.


##### `AttioConnector._flatten_record`  (lines 195–209)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns a standard Attio object record into the flat record shape expected by the sync framework. It gives the record a clear top-level record_id and adds flattened attributes.

**Data flow**: It receives one Attio record and its stream description. It reads the nested id object, copies identity and timestamp fields to the top level, optionally fills a cursor field if the stream defines one, then merges in flattened values. It returns a new flat record.

**Call relations**: AttioConnector.flatten calls this for object streams that are not tasks, notes, meetings, or call recordings.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 212–216)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level task_id to an Attio task. This gives the task a clear unique key for storage and deduplication.

**Data flow**: It receives a task record, copies it, reads task_id from the nested id when present, and writes task_id at the top level. It returns the copied and enriched task.

**Call relations**: AttioConnector.flatten calls this whenever the current stream is tasks.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 219–223)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level note_id to an Attio note. This makes notes fit the same storage expectations as other synced records.

**Data flow**: It receives a note record, copies it, reads note_id from the nested id when present, and writes note_id at the top level. It returns the copied and enriched note.

**Call relations**: AttioConnector.flatten calls this whenever the current stream is notes.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 226–230)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level meeting_id to an Attio meeting. This makes meetings identifiable in the same simple way as other synced items.

**Data flow**: It receives a meeting record, copies it, reads meeting_id from the nested id when present, and writes meeting_id at the top level. It returns the copied and enriched meeting.

**Call relations**: AttioConnector.flatten calls this whenever the current stream is meetings.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 233–254)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares an Attio call recording for storage by adding its top-level ID, filling in a usable recording URL when possible, and turning transcript segments into readable text.

**Data flow**: It receives a call recording record, copies it, reads call_recording_id from the nested id, and writes it at the top level. If there is no recording_url but there is a web_url, it uses that as the recording URL. If transcript segments are present, it joins speaker names and speech into transcript_text. It returns the enriched recording.

**Call relations**: AttioConnector.flatten calls this for the call_recordings stream, after pagination may already have fetched transcript data for each recording.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 256–265)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening routine for the current Attio stream. It is the connector’s public reshaping step before records leave the connector.

**Data flow**: It receives a raw Attio record and the stream it came from. It checks the stream name and sends the record to the task, note, meeting, call recording, or standard object flattener. The output is a cleaner record with a top-level primary key.

**Call relations**: The sync framework calls this as records are read. It delegates to AttioConnector._flatten_task, AttioConnector._flatten_note, AttioConnector._flatten_meeting, AttioConnector._flatten_call_recording, or AttioConnector._flatten_record depending on the stream.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 267–321)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches all pages for one Attio stream, using the paging style that stream requires. It also turns certain expected Attio errors into a clean "skip this stream" signal.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. For tasks and notes, it reads offset-based GET pages. For meetings, it reads cursor-based pages. For call recordings, it walks meetings and their recordings. For standard objects, it repeatedly posts query bodies with increasing offsets. It yields lists of raw records page by page, or raises StreamSkipped for disabled objects or missing OAuth permission scopes.

**Call relations**: This is the main reading path used by the sync framework. It calls the smaller pagination helpers, builds object query bodies, checks known error shapes, and uses StreamSkipped when Attio says a stream cannot be read in the current workspace or authorization grant.

*Call graph*: calls 8 internal fn (__init__, _build_query_body, _is_object_disabled, _is_scope_unauthorized, _paginate_call_recordings, _paginate_cursor, _paginate_simple, _scope_skip_reason).


##### `AttioConnector._paginate_simple`  (lines 323–330)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use simple offset paging. Offset paging means asking for records starting at position 0, then 500, then 1000, and so on.

**Data flow**: It receives an HTTP client, an endpoint path, and a page size. It asks the shared REST helper for pages under the response’s data field and yields each page unchanged. It does not flatten the records itself.

**Call relations**: AttioConnector.paginate calls this for tasks and notes because those Attio endpoints use limit and offset parameters.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._paginate_cursor`  (lines 332–350)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use cursor paging. A cursor is a token from the server that says where the next page starts.

**Data flow**: It receives an HTTP client, endpoint path, page size, and optional query parameters. It asks the shared REST helper to read records from data and follow pagination.next_cursor until there is no next page. It yields each page of records.

**Call relations**: AttioConnector.paginate calls this for meetings. AttioConnector._paginate_call_recordings also calls it to walk meetings and then recordings under each meeting.

*Call graph*: called by 2 (_paginate_call_recordings, paginate).


##### `AttioConnector._paginate_call_recordings`  (lines 352–388)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the call recording stream by first finding meetings, then finding recordings inside each meeting, and then fetching transcripts for those recordings when available. This is needed because Attio does not expose call recordings as one simple workspace-wide list here.

**Data flow**: It receives an HTTP client. It pages through meetings, extracts each meeting ID, title, start time, end time, and duration, then pages through that meeting’s call recordings. Each recording is stamped with parent meeting context. If a recording ID is available, it fetches the transcript and adds transcript data when present. It yields pages of enriched recording records.

**Call relations**: AttioConnector.paginate calls this for the call_recordings stream. Inside, it relies on AttioConnector._paginate_cursor for both meetings and recordings, uses ID and date helpers to interpret each item, and calls AttioConnector._fetch_transcript for per-recording transcript details.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (paginate).


##### `AttioConnector._fetch_transcript`  (lines 390–402)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: Fetches the transcript for a single call recording. If Attio says the transcript is not found or not ready yet, it treats that as normal and returns nothing.

**Data flow**: It receives an HTTP client plus a meeting ID and recording ID. It builds the transcript endpoint path and sends a GET request. If Attio returns 404 or 409, it returns None; for other HTTP errors, it lets the error continue. If the response contains a dictionary under data, it returns that dictionary.

**Call relations**: AttioConnector._paginate_call_recordings calls this after it finds a recording ID, so transcript text can travel along with the recording record before flattening.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 405–409)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a meeting’s ID from Attio’s nested or plain ID shape. This keeps call recording pagination from breaking when Attio returns either format.

**Data flow**: It receives a meeting record. If id is a dictionary, it returns id.meeting_id. If id is already a string, it returns that string. Otherwise it returns nothing.

**Call relations**: AttioConnector._paginate_call_recordings uses this before requesting recordings under a meeting. Without a meeting ID, that meeting is skipped.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 412–416)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a call recording’s ID from Attio’s nested or plain ID shape. The ID is needed to ask Attio for the recording transcript.

**Data flow**: It receives a call recording record. If id is a dictionary, it returns id.call_recording_id. If id is already a string, it returns that string. Otherwise it returns nothing.

**Call relations**: AttioConnector._paginate_call_recordings uses this after listing recordings for a meeting. When an ID is found, it can hand that ID to AttioConnector._fetch_transcript.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 419–423)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: Pulls a usable date or date-time string out of Attio’s meeting time shape. Attio may represent timed meetings and all-day meetings differently.

**Data flow**: It receives any value that might be Attio’s time object. If it is a dictionary, it returns datetime when present, otherwise date when present. If the shape is not recognized, it returns nothing.

**Call relations**: AttioConnector._paginate_call_recordings uses this for meeting start and end fields before copying that context onto each recording.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 426–436)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: Estimates how long a meeting lasted, in seconds, from its start and end strings. It returns nothing if either time is missing or cannot be parsed.

**Data flow**: It receives start and end strings. It converts ISO 8601 date-time text into datetime objects, subtracts start from end, clamps negative results to zero, and returns the number of seconds. If parsing fails, it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this after reading meeting start and end times. The resulting duration is copied onto call recordings when available.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 439–448)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes Attio’s specific error for a standard object that is disabled in the workspace. This lets the sync skip that stream instead of failing the whole run.

**Data flow**: It receives an HTTP error. It checks that the status is 400, tries to read the JSON body, and looks for code equal to standard_object_disabled. It returns true or false.

**Call relations**: AttioConnector.paginate calls this when a standard object query fails. If it returns true, paginate raises StreamSkipped with a clear reason.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._is_scope_unauthorized`  (lines 451–460)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes Attio’s specific error for a missing OAuth permission scope. An OAuth scope is a permission granted to an app, such as permission to read meetings.

**Data flow**: It receives an HTTP error. It checks that the status is 403, tries to read the JSON body, and looks for code equal to unauthorized. It returns true or false.

**Call relations**: AttioConnector.paginate calls this around meeting and call recording reads. If the permission is missing, paginate turns the error into StreamSkipped instead of treating it as an unexpected crash.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._scope_skip_reason`  (lines 463–469)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a clear explanation for why a stream was skipped because of missing OAuth permissions. It includes Attio’s own message when one is available.

**Data flow**: It receives an HTTP error, tries to read its JSON body, and looks for a message field. It returns a human-readable sentence explaining that the OAuth grant is missing a required scope.

**Call relations**: AttioConnector.paginate calls this after AttioConnector._is_scope_unauthorized confirms the error type. The returned text is passed into StreamSkipped so the sync result explains what happened.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/hubspot.py`

`io_transport` · `sync request handling`

HubSpot stores customer data across many different API shapes. Some records, like contacts and companies, use HubSpot’s CRM search API. Others, like workflows, forms, email events, analytics, and sequences, each have their own endpoint and paging style. This connector is the project’s adapter for all of that variety.

The file first declares many “streams,” which are named categories of data to sync. A stream says what HubSpot object it reads, what field uniquely identifies each record, and which timestamp can be used as a cursor for incremental syncs. A cursor is like a bookmark: the next run starts from the last seen update time instead of rereading everything.

The main class, `HubSpotConnector`, then decides how to fetch each stream. Standard CRM objects are searched in timestamp order, with duplicate boundary records removed because HubSpot’s “greater than or equal” filter can repeat the last item from the previous run. Deleted or archived CRM records are swept afterward and emitted as tombstones so downstream storage can remove them. Product-specific streams use custom walkers. The file also flattens HubSpot’s nested response shapes into simple top-level fields, making later indexing and recall easier.

An important behavior is graceful skipping: if HubSpot says a stream is unavailable because the account lacks a product tier or permission, the connector marks that stream as skipped rather than failing the whole run.

#### Function details

##### `_normalize_epoch_millis`  (lines 248–255)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Converts a HubSpot timestamp written as milliseconds since 1970 into a readable ISO date string. It leaves booleans and already non-millisecond-looking values alone, so ordinary fields are not accidentally changed.

**Data flow**: It receives any value. If the value is a number, or a string made only of digits, it treats it as milliseconds from the Unix epoch and returns an ISO timestamp in UTC; otherwise it returns the original value unchanged.

**Call relations**: This helper is used while flattening product API rows and while shaping analytics view rows, because those HubSpot surfaces may use raw millisecond timestamps where the rest of the system expects readable dates.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 258–267)

```
def _stream(name: str, *, object_type: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a standard CRM stream description for HubSpot objects such as contacts, companies, deals, and tasks. It gives the sync engine the basic facts it needs: name, object type, unique id field, and update-time cursor.

**Data flow**: It receives a stream name, a HubSpot object type, and whether the stream is canonical. It returns a `StreamSpec`, which is a small description object used later to decide how to fetch and store that stream.

**Call relations**: This setup helper is used at file load time to define many CRM object streams. Those stream definitions are collected into `ALL_STREAMS`, which the connector exposes to the sync runner.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 270–289)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='createdAt', updated_at_field: str | None='updatedAt', pagi
```

**Purpose**: Creates a stream description for HubSpot data that does not follow the normal CRM search API. These streams cover product-specific areas like owners, workflows, forms, analytics, files, and conversations.

**Data flow**: It receives naming, key, timestamp, and optional pagination details. It returns a `StreamSpec` marked as non-canonical, because these records come from special HubSpot product APIs rather than the main CRM object model.

**Call relations**: This helper is used when the module defines product API streams. Later, `HubSpotConnector._paginate_product_api` looks at those stream names and sends each one to the right fetching routine.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 292–304)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Builds the standard paging recipe for HubSpot list endpoints that return `results` and a `paging.next.after` cursor. This avoids repeating the same pagination settings for many simple GET endpoints.

**Data flow**: It receives an API path and returns a `Pagination` object describing where records live in the response, where the next-page cursor lives, and which query parameters control cursor and page size.

**Call relations**: This helper is used in stream declarations for flat product API endpoints. When `_paginate_unchecked` sees a stream with a pagination strategy, it lets the base connector use this recipe.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 307–317)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Creates a stream description for relationship rows, such as deal-to-contact or ticket-to-company links. These streams do not represent HubSpot objects themselves; they represent pairs of connected objects.

**Data flow**: It receives a stream name and the parent HubSpot object type. It returns a `StreamSpec` with no cursor, because HubSpot does not expose a modification time for these simple association rows.

**Call relations**: This helper defines junction streams at module load time. Later, `_paginate_unchecked` recognizes these stream names and sends them to `_paginate_junction` for full-refresh fetching.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 622–652)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the JSON body used for HubSpot CRM search requests. It asks HubSpot for all known properties, sorts records by update time, and optionally starts from a saved cursor.

**Data flow**: It receives a stream description, a list of property names, an optional cursor, and an optional page cursor called `after`. It returns a dictionary ready to send in a POST request to HubSpot’s search API.

**Call relations**: Standard CRM pagination and custom object pagination both call this helper before posting to HubSpot. It centralizes the search shape so cursor filtering and paging stay consistent.

*Call graph*: called by 2 (_paginate_custom_object_records, _paginate_unchecked).


##### `HubSpotConnector._flatten`  (lines 655–666)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a normal HubSpot CRM record into a simpler flat record. HubSpot nests most useful fields under `properties`; this lifts them up so later code can read fields directly.

**Data flow**: It receives one CRM record. It copies the id, creation time, update time, archived flag, and every key from the nested `properties` dictionary into one top-level dictionary.

**Call relations**: `HubSpotConnector.flatten` calls this for ordinary CRM object streams. It is the standard cleanup step after records are fetched.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 669–692)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records from HubSpot product APIs, whose shapes vary more than CRM records. It handles common oddities such as `objectId`, nested `properties`, form submission `values`, and millisecond timestamps.

**Data flow**: It receives a raw product API record and its stream description. It returns a copy with a reliable `id` where possible, lifted properties and form values, and normalized dates for specific streams.

**Call relations**: `HubSpotConnector.flatten` calls this for product API streams. It uses `_normalize_epoch_millis` where HubSpot returns dates as millisecond numbers.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 694–701)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening behavior for each stream. Some streams are already synthetic or flat, while others need CRM-style or product-API cleanup.

**Data flow**: It receives a raw record and its stream. Junction and custom object rows pass through unchanged, product API rows go through `_flatten_product_api`, and ordinary CRM rows go through `_flatten`.

**Call relations**: This is the connector’s public record-shaping hook used after pages are fetched. It hands records to the correct specialized flattener based on the stream name.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector._list_properties`  (lines 703–711)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which fields exist for a CRM object type. HubSpot search only returns properties that are explicitly requested, so this lets the connector fetch everything the account exposes.

**Data flow**: It receives an HTTP client and a HubSpot object type. It reads HubSpot’s properties endpoint and returns a list of property names found in the response.

**Call relations**: `_paginate_unchecked` calls this before searching ordinary CRM objects. The returned names are passed into `_build_search_body` so search results include all available fields.

*Call graph*: called by 1 (_paginate_unchecked).


##### `HubSpotConnector.paginate`  (lines 713–726)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Provides the main page-by-page reading interface for one HubSpot stream. It also turns certain permission errors into a clean skipped-stream result instead of a failed sync.

**Data flow**: It receives an HTTP client, a stream, and an optional cursor. It yields pages from `_paginate_unchecked`; if HubSpot returns an authorization or unavailable-stream error, it raises `StreamSkipped` with a readable reason.

**Call relations**: The sync runner calls this when it wants records for a stream. This method delegates real fetching to `_paginate_unchecked`, then uses `_is_stream_unavailable` and `_stream_skip_reason` to decide whether a failed request should become a skip.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 728–778)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the fetching strategy for a stream and performs the default CRM search flow. It is the connector’s main traffic director.

**Data flow**: It receives a stream and cursor. Depending on the stream, it yields pages from a declared pagination strategy, a junction walker, custom object logic, product API logic, or standard CRM search; for standard CRM streams it also yields deletion tombstones from archived records.

**Call relations**: `paginate` calls this after wrapping it with error handling. This method delegates to `_paginate_junction`, `_paginate_custom_objects`, `_paginate_product_api`, `_list_properties`, `_build_search_body`, and `_paginate_archived_ids` as needed.

*Call graph*: calls 6 internal fn (_build_search_body, _list_properties, _paginate_archived_ids, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._is_stream_unavailable`  (lines 781–804)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes HubSpot errors that mean this account cannot access a particular stream. This prevents a missing product feature or OAuth scope from stopping the entire sync.

**Data flow**: It receives an HTTP error. It checks for a 403 response and looks for permission-related wording in the response message, returning true only when the stream appears unavailable rather than temporarily broken.

**Call relations**: `paginate`, `_paginate_archived_ids`, and `_paginate_custom_object_archived_ids` use this to decide whether to skip or silently stop optional parts instead of raising the original error.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 807–816)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a clear human-readable explanation for why a HubSpot stream was skipped. The message includes HubSpot’s own reason when available.

**Data flow**: It receives a stream name and an HTTP error. It tries to read the error body, extracts the message, and returns a sentence suitable for the sync run’s skip record.

**Call relations**: `paginate` calls this when `_is_stream_unavailable` says the stream should be skipped. The result is passed into `StreamSkipped`.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 818–853)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived or deleted CRM records after a normal search. HubSpot search omits archived records, so this extra sweep lets the downstream system remove records that disappeared in HubSpot.

**Data flow**: It receives a stream and HTTP client. It pages through the CRM list endpoint with `archived=true`, collects record ids, and yields `StreamPage` objects containing delete markers instead of normal records.

**Call relations**: `_paginate_unchecked` runs this after standard CRM search. It uses `_is_archived_sweep_unsupported` and `_is_stream_unavailable` to quietly stop when HubSpot cannot provide archived data for that object.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 855–949)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Routes each product-specific HubSpot stream to its correct fetcher. These streams do not all share one API pattern, so this method acts like a switchboard.

**Data flow**: It receives a product stream and optional cursor. It checks the stream name and yields pages from the specialized method for owners, lists, analytics, events, associations, consent states, sequences, form submissions, messages, pipelines, or a generic GET collection.

**Call relations**: `_paginate_unchecked` calls this for streams listed as product API streams. This method hands work off to many focused pagination helpers so each HubSpot API quirk stays isolated.

*Call graph*: calls 21 internal fn (_paginate_analytics_reports, _paginate_analytics_views, _paginate_association_labels, _paginate_associations, _paginate_campaign_assets, _paginate_consent_states, _paginate_conversation_messages, _paginate_email_events, _paginate_event_occurrences, _paginate_event_types (+11 more)); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._paginate_get_collection`  (lines 951–979)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Pages through a common HubSpot GET endpoint that returns records under `results`. It is the reusable walker for many simple product API lists.

**Data flow**: It receives a path, page size, and optional extra query parameters. It repeatedly sends GET requests, yields cleaned record lists, and follows HubSpot’s `paging.next.after` cursor until no next page remains.

**Call relations**: Many specialized paginators call this when their endpoint follows HubSpot’s standard list shape, including campaign assets, owner teams, forms, conversations, sequences, and generic product API streams.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_product_api, _paginate_sequences, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 981–1016)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Fetches records for all custom object types defined in the HubSpot account. Custom objects are user-created object kinds, so the connector must discover their schemas first.

**Data flow**: It reads custom object schemas, extracts each object type and its properties, builds a temporary stream description for that object, yields its records, and then yields tombstones for archived custom records.

**Call relations**: `_paginate_unchecked` calls this for the `custom_objects` stream. It delegates schema reading, property extraction, record paging, row shaping, and archived-id sweeping to smaller helpers.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 1018–1020)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Reads the list of custom object schemas from HubSpot. A schema describes a custom object type and its available properties.

**Data flow**: It receives an HTTP client, calls HubSpot’s custom object schema endpoint, and returns only response rows that are dictionaries.

**Call relations**: `_paginate_custom_objects` uses this to discover custom objects. `_association_object_types` also uses it so association syncing can include custom object types.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 1023–1028)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Finds the usable object type identifier inside a custom object schema. HubSpot may expose this under several possible field names.

**Data flow**: It receives a schema dictionary. It checks likely identifier fields in order and returns the first non-empty string, or `None` if no identifier is found.

**Call relations**: `_paginate_custom_objects`, `_custom_object_row`, and `_association_object_types` call this whenever they need the stable HubSpot name for a custom object type.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1031–1046)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Collects the property names that should be requested for a custom object. It includes declared properties and display properties so useful titles are not missed.

**Data flow**: It receives a schema. It scans the schema’s property list, primary display property, and secondary display properties, returning a de-duplicated list of names.

**Call relations**: `_paginate_custom_objects` calls this before searching custom object records. The resulting names are passed into `_build_search_body`.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1048–1083)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Searches one custom object type and yields its records. It follows the same incremental timestamp idea as standard CRM objects, including duplicate boundary protection.

**Data flow**: It receives a temporary stream, schema, property list, and optional cursor. It posts search requests, skips repeated boundary records, converts each raw record into a custom object row, and yields non-empty pages.

**Call relations**: `_paginate_custom_objects` calls this for each discovered custom object type. It uses `_build_search_body` to form requests and `_custom_object_row` to create records that include schema context.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1085–1124)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Turns one custom object record into a useful flat row with extra context about the custom object type. This makes otherwise opaque custom records understandable in downstream search.

**Data flow**: It receives a raw record and its schema. If both object type and record id exist, it returns a row with a compound id, title fields, labels, copied properties, timestamps, and archive state; otherwise it returns `None`.

**Call relations**: `_paginate_custom_object_records` calls this while processing search results. It uses `_schema_object_type_id` to build stable ids.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1126–1156)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived custom object records and emits delete markers for them. This mirrors the archived sweep used for standard CRM objects.

**Data flow**: It receives a custom object type id. It pages through that object’s list endpoint with `archived=true`, builds compound delete ids like `objectType:recordId`, and yields them in `StreamPage` delete pages.

**Call relations**: `_paginate_custom_objects` calls this after syncing active records for each custom object type. It uses the same unavailable-stream and unsupported-sweep checks as the normal archived sweep.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1158–1177)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a stream of HubSpot owner teams from the owner records. HubSpot exposes teams nested inside owners rather than as a simple standalone list.

**Data flow**: It reads owners through `_paginate_get_collection`, extracts each nested team, de-duplicates by team id, and yields one page of unique team rows.

**Call relations**: `_paginate_product_api` calls this for the `owner_teams` stream. It relies on the generic collection pager to fetch owners first.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_lists`  (lines 1179–1208)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches HubSpot lists using their search endpoint. Lists use offset-based paging rather than the usual `after` cursor.

**Data flow**: It posts a search request with a count and offset, turns each list id into a normal `id`, lifts any additional properties into the row, yields pages, and advances the offset until HubSpot says there are no more.

**Call relations**: `_paginate_product_api` calls this for the `lists` stream. `_paginate_list_memberships` also calls it first so it can walk memberships for each list.

*Call graph*: called by 2 (_paginate_list_memberships, _paginate_product_api).


##### `HubSpotConnector._paginate_site_search`  (lines 1210–1230)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches CMS search results for a specific content type, such as knowledge articles. It handles HubSpot’s offset-and-total paging style.

**Data flow**: It receives a content type, repeatedly calls the site search endpoint with limit and offset, yields dictionary rows, and stops when the next offset reaches the reported total.

**Call relations**: `_paginate_product_api` calls this for knowledge articles. It hides the CMS search paging details from the stream router.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1232–1256)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds assets attached to HubSpot marketing campaigns. It first lists campaigns, then checks many possible asset categories for each campaign.

**Data flow**: It reads campaign pages, extracts each campaign id and name, loops through known campaign asset types, and yields pages produced by `_paginate_campaign_asset_type`.

**Call relations**: `_paginate_product_api` calls this for the `campaign_assets` stream. It uses `_paginate_get_collection` to get campaigns and delegates per-type asset reads to `_paginate_campaign_asset_type`.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1258–1293)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one kind of asset for one campaign and turns each asset into a row with campaign context. This creates stable ids for assets that are only meaningful inside a campaign.

**Data flow**: It receives a campaign id, campaign name, and asset type. It pages through that asset endpoint, builds rows with compound ids, asset kind, campaign fields, and metrics, and ignores 403 or 404 responses for unavailable asset categories.

**Call relations**: `_paginate_campaign_assets` calls this inside its campaign-and-asset-type loop. It uses `_paginate_get_collection` for the actual paging.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1295–1301)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields the configured HubSpot analytics views. Analytics views are saved filters that can be used to slice reports.

**Data flow**: It calls `_analytics_view_rows`, then yields the returned rows as a page if any exist.

**Call relations**: `_paginate_product_api` calls this for the `analytics_views` stream. It keeps the public paginator thin and leaves row shaping to `_analytics_view_rows`.

*Call graph*: calls 1 internal fn (_analytics_view_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_view_rows`  (lines 1303–1334)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Reads and normalizes HubSpot analytics view definitions. It makes sure each view has a stable id, name, filter object, and readable timestamps.

**Data flow**: It calls the analytics views endpoint, accepts either list-shaped or object-shaped responses, filters to dictionary rows, chooses an id and name, normalizes created time when needed, and returns a list of rows.

**Call relations**: `_paginate_analytics_views` uses this to emit the analytics view stream. `_paginate_analytics_reports` also uses it so reports can be queried both for all data and for each saved view.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1336–1366)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds many analytics report queries across report subjects, time periods, and analytics views. It turns HubSpot’s report API into a stream of report rows.

**Data flow**: It chooses a date window, loads analytics views, creates filter choices for all data plus each view, then loops through report families, subjects, time periods, and filters, yielding pages from `_paginate_analytics_report_query`.

**Call relations**: `_paginate_product_api` calls this for the `analytics_reports` stream. It uses `_analytics_report_window`, `_analytics_view_rows`, and `_paginate_analytics_report_query` to organize the report fan-out.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_report_window`  (lines 1369–1370)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range used for analytics reports. It starts at a fixed early date and ends at today in UTC.

**Data flow**: It reads the current UTC date and returns two strings in HubSpot’s compact `YYYYMMDD` format: the fixed start date and today’s end date.

**Call relations**: `_paginate_analytics_reports` calls this before launching report queries so every report uses the same window.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1372–1423)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Runs one HubSpot analytics report query and pages through its breakdown rows. It tolerates report combinations that HubSpot does not support.

**Data flow**: It receives report family, subject, time period, optional analytics view, and date window. It calls the report endpoint, converts the response to rows, yields them, and advances by offset until all breakdowns are read; 400 and 404 responses end that query quietly.

**Call relations**: `_paginate_analytics_reports` calls this many times as it fans out across report combinations. It delegates response shaping to `_analytics_report_rows`.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1426–1501)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Turns one analytics report response into normal stream rows. It creates separate rows for totals and for each breakdown entry.

**Data flow**: It receives a report response plus context such as subject, time period, view, and dates. It returns rows with ids, names, report metadata, filters, date range, and metric values.

**Call relations**: `_paginate_analytics_report_query` calls this after each report API response. Its rows are then yielded to the sync runner as the analytics report stream.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1504–1508)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Creates a stable id string for an analytics report row. It cleans characters that would make compound ids ambiguous.

**Data flow**: It receives any number of id parts. It converts each part to text, replaces slashes and colons, joins them with colons, and prefixes the result with `analytics_report:`.

**Call relations**: This helper supports analytics report row construction so totals and breakdown rows can be upserted consistently across sync runs.


##### `HubSpotConnector._analytics_report_date`  (lines 1511–1512)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Reformats a compact HubSpot report date into a normal dashed date. This makes report date fields easier to read and compare.

**Data flow**: It receives a string like `20260131` and returns `2026-01-31` by slicing the year, month, and day parts.

**Call relations**: This helper supports analytics report row construction, where the report window needs to be stored in a readable form.


##### `HubSpotConnector._paginate_event_types`  (lines 1514–1533)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the event type definitions known to HubSpot. Event types describe categories of event occurrences.

**Data flow**: It calls the event types endpoint, accepts either a list or `results` response, chooses a stable id from available fields or the row index, and yields the rows if any exist.

**Call relations**: `_paginate_product_api` calls this for the `event_types` stream.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1535–1557)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches actual HubSpot event occurrences, optionally starting after a saved cursor. It creates synthetic ids when HubSpot does not provide one.

**Data flow**: It receives an optional cursor, sends it as `occurredAfter` when present, reads event rows, assigns each row an id from HubSpot or `_synthetic_event_id`, and yields one page.

**Call relations**: `_paginate_product_api` calls this for the `event_occurrences` stream. It uses `_synthetic_event_id` to keep records stable when the API response lacks ids.

*Call graph*: calls 1 internal fn (_synthetic_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._synthetic_event_id`  (lines 1560–1570)

```
def _synthetic_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Builds a fallback id for an event occurrence. It combines meaningful event fields with a stable hash so repeated syncs identify the same event the same way.

**Data flow**: It receives an event row and its position in the response. It joins event type, object type, object id, occurred time or index, and a payload hash into one colon-separated id.

**Call relations**: `_paginate_event_occurrences` calls this only when an event row has no id from HubSpot.

*Call graph*: called by 1 (_paginate_event_occurrences).


##### `HubSpotConnector._paginate_email_events`  (lines 1572–1600)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches HubSpot email event records, such as email opens or clicks. It supports incremental starts by converting the saved cursor into HubSpot’s expected millisecond timestamp.

**Data flow**: It receives an optional cursor. It repeatedly calls the email events endpoint with a limit, start timestamp, and offset, assigns ids from HubSpot or `_synthetic_email_event_id`, yields pages, and stops when HubSpot has no more events.

**Call relations**: `_paginate_product_api` calls this for the `email_events` stream. It uses `_email_event_start_timestamp` for cursor conversion and `_synthetic_email_event_id` for missing ids.

*Call graph*: calls 2 internal fn (_email_event_start_timestamp, _synthetic_email_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1603–1612)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts an email event cursor into the millisecond timestamp format HubSpot expects. It accepts either an already-numeric cursor or an ISO date string.

**Data flow**: It receives a cursor string or `None`. It returns `None` for no cursor or an unparseable value, returns the integer directly for digit strings, or parses an ISO timestamp and converts it to milliseconds.

**Call relations**: `_paginate_email_events` calls this before making requests so incremental email event syncs can start at the right time.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._synthetic_email_event_id`  (lines 1615–1625)

```
def _synthetic_email_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Builds a fallback id for an email event when HubSpot does not provide one. It uses event content plus a stable hash to reduce collisions.

**Data flow**: It receives an email event row and its index. It joins created time, recipient, event type, campaign id, and a payload hash into a colon-separated id string.

**Call relations**: `_paginate_email_events` calls this for email event rows that lack ids.

*Call graph*: called by 1 (_paginate_email_events).


##### `HubSpotConnector._stable_payload_hash`  (lines 1628–1630)

```
def _stable_payload_hash(row: dict[str, Any]) -> str
```

**Purpose**: Creates a short stable fingerprint for a record. This is useful when an API response lacks an id but the whole payload can help identify it.

**Data flow**: It receives a dictionary, serializes it with sorted keys, hashes that text with SHA-256, and returns the first 16 hex characters.

**Call relations**: Synthetic id helpers use this kind of fingerprint so fallback ids stay consistent across runs when the payload is the same.

*Call graph*: 2 external calls (sha256, dumps).


##### `HubSpotConnector._paginate_association_labels`  (lines 1632–1648)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches labels that describe relationships between HubSpot object types. A label might distinguish, for example, one kind of company-contact relationship from another.

**Data flow**: It walks object-type pairs that have labels, converts each raw label into a normalized label row, and yields pages of those rows.

**Call relations**: `_paginate_product_api` calls this for the association label stream. It uses `_association_pairs_with_labels` to discover valid pairs and `_association_label_row` to shape each label.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_associations`  (lines 1650–1665)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches actual association records between HubSpot objects. These rows say that one specific record is linked to another specific record.

**Data flow**: It discovers object-type pairs with labels, pages through ids for the source object type, sends those ids in batch association reads, and yields the resulting relationship rows.

**Call relations**: `_paginate_product_api` calls this for the associations stream. It uses `_association_pairs_with_labels`, `_paginate_crm_object_id_pages`, and `_paginate_association_batch` to break a very large relationship graph into manageable requests.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_association_batch`  (lines 1667–1694)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads associations for a batch of source record ids. It also follows per-record paging when a single source record has more linked targets than one response can hold.

**Data flow**: It receives source and target object types plus input ids. It posts a batch read, turns the response into rows, yields them, then builds the next set of paged inputs until no more remain.

**Call relations**: `_paginate_associations` calls this after collecting a page of source ids. It uses `_association_rows` to shape results, `_next_association_inputs` to continue long association lists, and `_is_optional_pair_unavailable` to skip unsupported pairs.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1697–1711)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Finds which association batch inputs need another page. Some source records have their own `after` cursor inside the batch response.

**Data flow**: It receives a batch association response. For each result with a source id and a next-page cursor, it returns a new input containing both the id and that cursor.

**Call relations**: `_paginate_association_batch` calls this after each batch response to decide whether another batch request is needed.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1713–1726)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Discovers object-type pairs that have association labels. These are the pairs worth reading for label and association streams.

**Data flow**: It gets the list of object types, tries every from/to combination, asks HubSpot for labels for that pair, and yields only pairs that return labels.

**Call relations**: `_paginate_association_labels` and `_paginate_associations` both call this as their discovery step. It depends on `_association_object_types` and `_association_labels_for_pair`.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1728–1741)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the list of HubSpot object types to consider for associations. It includes known standard object types and any custom object types the account defines.

**Data flow**: It starts with a fixed list of standard HubSpot object names, tries to read custom object schemas, adds their object type ids when new, and returns the combined list.

**Call relations**: `_association_pairs_with_labels` calls this before trying object-type pairs. It uses `_custom_object_schemas`, `_schema_object_type_id`, and `_is_optional_pair_unavailable` for optional custom schema access.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1743–1759)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches association labels for one source object type and one target object type. Unsupported pairs are treated as empty instead of fatal.

**Data flow**: It receives a from-type and to-type, calls the labels endpoint, returns dictionary rows from `results`, or returns an empty list when the pair is not available.

**Call relations**: `_association_pairs_with_labels` calls this for each possible object-type pair. It uses `_is_optional_pair_unavailable` to distinguish unsupported pairs from real errors.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1762–1779)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Normalizes one association label into a stable stream row. It adds the source type, target type, type id, category, and display label in predictable fields.

**Data flow**: It receives a raw label and the two object types it connects. It returns a dictionary with a compound id and normalized association metadata.

**Call relations**: `_paginate_association_labels` calls this for every label returned by `_association_pairs_with_labels`.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1781–1793)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Yields pages of record ids for a CRM object type. This is useful when another API needs ids as input, rather than full records.

**Data flow**: It receives an object type, asks `_paginate_crm_object_pages` for pages containing `hs_object_id`, extracts each record id as text, and yields non-empty id lists.

**Call relations**: `_paginate_associations` uses this to feed batch association reads. `_paginate_sequence_enrollments` uses it to check enrollments for each contact.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1795–1825)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Pages through the normal CRM list endpoint for an object type with selected properties. This is a lighter read than the full search flow.

**Data flow**: It receives an object type and property names. It repeatedly calls the CRM objects endpoint, yields dictionary records from `results`, follows `paging.next.after`, and stops if the object type is optional and unavailable.

**Call relations**: `_paginate_crm_object_id_pages` and `_paginate_contact_identity_pages` call this when they need simple pages of CRM records. It uses `_is_optional_pair_unavailable` to skip unsupported object reads.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1828–1862)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Converts a batch association response into flat relationship rows. It expands each source-to-target link and each association type into its own row.

**Data flow**: It receives response data plus source and target object type names. It loops through results, reads the source id, target ids, and association type details, and returns a list of normalized association rows.

**Call relations**: `_paginate_association_batch` calls this after each batch read response. It creates rows through the association-row shaping logic.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1865–1892)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one normalized relationship row between two HubSpot records. The row has a compound id that includes both records and the association type.

**Data flow**: It receives association type metadata, source and target object types, source and target record ids, and a fallback index. It returns a dictionary describing the link and its type, label, category, and stable id.

**Call relations**: This helper is part of association row shaping. It is used when expanding raw association responses into rows suitable for syncing.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1895–1898)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether an error means an optional HubSpot object pair or feature is simply unavailable. This keeps broad discovery loops from failing on unsupported combinations.

**Data flow**: It receives an HTTP error. It returns true for 400 or 404 responses, or for permission-style unavailable-stream errors recognized by `_is_stream_unavailable`.

**Call relations**: Many optional walkers call this, including association, list membership, consent, sequence, and lightweight CRM page readers. It acts as the common safety check for exploratory API calls.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1900–1914)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches which records belong to each HubSpot list. It first discovers lists, then walks the memberships for every list.

**Data flow**: It reads list pages, extracts each list id, and yields membership pages from `_paginate_memberships_for_list` for each valid list.

**Call relations**: `_paginate_product_api` calls this for the `list_memberships` stream. It depends on `_paginate_lists` for the parent list records and `_paginate_memberships_for_list` for member rows.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1916–1959)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the members of one HubSpot list and adds list context to each membership row. This makes each row useful without looking up the parent list later.

**Data flow**: It receives a list record and list id. It pages through the memberships endpoint, builds rows with compound ids, list id, list name, object type, and processing type, then yields pages until no next cursor remains.

**Call relations**: `_paginate_list_memberships` calls this for every list. It uses `_is_optional_pair_unavailable` to skip lists whose memberships cannot be read.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1961–1974)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches HubSpot communication subscription definitions. These define the kinds of email or communication preferences a contact can have.

**Data flow**: It calls the definitions endpoint, accepts either `results` or `subscriptionDefinitions`, assigns a stable id from available fields or index, and yields the rows if any exist.

**Call relations**: `_paginate_product_api` calls this for the `subscription_definitions` stream.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_consent_states`  (lines 1976–1989)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches email consent status for contacts. It walks contacts with email addresses, then asks HubSpot for both subscription-specific status and global unsubscribe status.

**Data flow**: It reads pages of contact identities, skips contacts without an email, gathers consent rows and unsubscribe-all rows for each email, and yields combined pages.

**Call relations**: `_paginate_product_api` calls this for the `consent_states` stream. It relies on `_paginate_contact_identity_pages`, `_consent_status_rows`, and `_unsubscribe_all_rows`.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1991–2007)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads contact records with their email addresses. Consent APIs are keyed by email, so this provides the bridge from contact ids to emails.

**Data flow**: It pages through contacts requesting the `email` property, pulls the email either from the top level or nested properties, and yields pages with an `email` field added.

**Call relations**: `_paginate_consent_states` calls this before looking up communication preferences. It uses `_paginate_crm_object_pages` for the lightweight contact read.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 2009–2030)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches subscription-level consent statuses for one contact email. These rows show whether the contact has opted in or out of particular communication types.

**Data flow**: It receives a contact and email, URL-escapes the email, calls the statuses endpoint, converts each returned status with `_consent_row`, and returns the list; unavailable cases return an empty list.

**Call relations**: `_paginate_consent_states` calls this for each contact email. It uses `_is_optional_pair_unavailable` for optional failures and `_consent_row` for row shaping.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 2032–2056)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches global unsubscribe status for one contact email. This captures the broad “unsubscribe from all” preference separately from individual subscription types.

**Data flow**: It receives a contact and email, URL-escapes the email, calls the unsubscribe-all endpoint, converts each returned row with `_consent_row`, and returns the list; unavailable cases return an empty list.

**Call relations**: `_paginate_consent_states` calls this alongside `_consent_status_rows`. It uses the same optional-error handling and consent row shaping.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 2059–2089)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Normalizes one communication preference row into a stable consent-state record. It adds contact, email, purpose, status, legal basis, source, and timestamp fields.

**Data flow**: It receives a raw consent row, contact, email, and status kind. It builds a compound id from email, subscription or status kind, and business unit, then returns a flat row with meaningful consent fields.

**Call relations**: `_consent_status_rows` and `_unsubscribe_all_rows` call this to keep both consent sources in the same record shape.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2091–2118)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches sales sequences for each HubSpot user discovered through owners. Sequence access is user-scoped, so the connector must fan out by user id.

**Data flow**: It reads sequence user rows, calls the sequences endpoint with each user id, adds owner context to returned rows, yields pages, and skips users or endpoints that are unavailable.

**Call relations**: `_paginate_product_api` calls this for the `sequences` stream. It uses `_sequence_user_rows`, `_paginate_get_collection`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_user_rows`  (lines 2120–2143)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the list of HubSpot user ids that can be used to query sequences. It derives these user ids from owner records.

**Data flow**: It pages through owners, extracts unique `userId` values, attaches owner id and email when present, and yields one page of user rows.

**Call relations**: `_paginate_sequences` calls this before querying sequences. It uses `_paginate_get_collection` to read owners.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2145–2163)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches sequence enrollment information for contacts. It checks each contact id against HubSpot’s enrollment endpoint.

**Data flow**: It pages through contact ids, calls the enrollment endpoint for each contact, converts responses into rows, and yields pages; optional unavailable responses for individual contacts are skipped.

**Call relations**: `_paginate_product_api` calls this for the `sequence_enrollments` stream. It uses `_paginate_crm_object_id_pages`, `_sequence_enrollment_rows`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2166–2180)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes sequence enrollment responses for one contact. HubSpot may return a list of results or a single object, so this function accepts both.

**Data flow**: It receives response data and a contact id. It treats `results` as the row list when present, otherwise treats the whole response as one row, assigns ids, adds the contact id, and returns the rows.

**Call relations**: `_paginate_sequence_enrollments` calls this after each contact enrollment request.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2182–2211)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches submissions for every HubSpot form. It first lists forms, then reads each form’s submissions.

**Data flow**: It pages through forms, extracts each form id, pages through that form’s submissions with a smaller limit, assigns each submission an id, adds form id and form name, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the `form_submissions` stream. It uses `_paginate_get_collection` both for forms and for submissions.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2213–2228)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages inside HubSpot conversation threads. Threads are listed first, then each thread’s messages are read separately.

**Data flow**: It pages through conversation threads, extracts each thread id, pages through that thread’s messages, adds `thread_id` to each message, and yields non-empty pages.

**Call relations**: `_paginate_product_api` calls this for the `conversation_messages` stream. It uses `_paginate_get_collection` for both thread and message endpoints.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipelines`  (lines 2230–2237)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches deal and ticket pipelines. Pipelines describe the high-level process stages for those object types.

**Data flow**: It loops over supported pipeline object types, asks `_pipeline_rows_for_object_type` for each, and yields a page when rows exist.

**Call relations**: `_paginate_product_api` calls this for the `pipelines` stream. It delegates object-specific shaping to `_pipeline_rows_for_object_type`.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2239–2289)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the stages inside deal and ticket pipelines. It turns nested stage data into standalone rows with pipeline context.

**Data flow**: It reads raw pipelines for each supported object type, loops through each pipeline’s stages, builds compound stage ids, adds status, probability, display order, and closed-state fields, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the `pipeline_stages` stream. It uses `_raw_pipelines_for_object_type` to get the raw nested pipeline data.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2291–2314)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes pipeline rows for one object type, such as deals or tickets. It adds object type context and a stable compound id.

**Data flow**: It receives an object type, reads raw pipelines, skips rows without ids, and returns rows with id, pipeline id, object kind, name, and active/archived status.

**Call relations**: `_paginate_pipelines` calls this for each supported pipeline object type. It depends on `_raw_pipelines_for_object_type` for the API read.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2316–2327)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Reads raw pipeline data for one HubSpot object type. It treats missing or forbidden pipeline endpoints as simply having no pipelines.

**Data flow**: It receives an object type, calls the CRM pipelines endpoint, returns dictionary rows from `results`, or returns an empty list for 403 and 404 responses.

**Call relations**: `_pipeline_rows_for_object_type` and `_paginate_pipeline_stages` call this as their shared raw data source.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2330–2334)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes the specific HubSpot error that says deleted-object paging is not supported. This lets the connector skip only the archived sweep without failing the active-record sync.

**Data flow**: It receives an HTTP error. It checks for a 400 response and looks for HubSpot’s known message about paging through deleted objects, returning true only for that case.

**Call relations**: `_paginate_archived_ids` and `_paginate_custom_object_archived_ids` call this when archived-record requests fail.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2337–2345)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Extracts HubSpot’s error message from an HTTP error response. It is a small helper for error classification.

**Data flow**: It receives an HTTP error, tries to parse the response body as JSON, and returns the `message` field as text when present; otherwise it returns `None`.

**Call relations**: This supports archived-sweep error detection by giving `_is_archived_sweep_unsupported` the upstream message text to inspect.


##### `HubSpotConnector._paginate_junction`  (lines 2347–2394)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches simple relationship streams like deal-contact or ticket-company links using HubSpot’s inline association listing. These streams are full-refreshed because HubSpot does not expose update timestamps for them.

**Data flow**: It receives a parent object type and target object type. It pages through parent records while asking HubSpot to include target associations, then emits one row per parent-target pair with a compound id and relationship type.

**Call relations**: `_paginate_unchecked` calls this for the synthetic junction streams. It is separate from the broader association APIs because these common links can be read cheaply from parent list endpoints.

*Call graph*: called by 1 (_paginate_unchecked).


### `extensions/sources/ufo_ext_sources/salesforce.py`

`io_transport` · `during Salesforce source sync`

Salesforce stores customer data in many object types, called SObjects, such as Account, Contact, Opportunity, and Case. This file defines a connector that knows which of those objects are worth syncing and how to ask Salesforce for them safely.

The connector does not hard-code every field on every Salesforce object. Instead, before reading an object, it asks Salesforce to describe that object and list the fields available in the current organization. This matters because Salesforce installations are often customized. The connector then builds a SOQL query, which is Salesforce’s SQL-like search language, to fetch records in order by their SystemModstamp, a timestamp Salesforce updates when a record changes.

For a first sync, it reads records in pages. For later syncs, it uses a cursor, meaning “the last point we successfully reached,” so it only asks for records changed after that point. It also asks Salesforce for records deleted since the cursor and returns those as tombstones, which are simple notices saying “this record is gone.”

If Salesforce refuses access with a 401 or 403 status, the connector marks that stream as skipped instead of pretending the data is empty. It also removes Salesforce’s metadata wrapper named attributes from each record, leaving only the useful fields.

#### Function details

##### `_stream`  (lines 29–38)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates the standard description for one Salesforce stream, such as accounts or contacts. A stream description tells the sync system what Salesforce object to read, what field is the record ID, and what timestamp should be used to continue from the last sync.

**Data flow**: It takes a friendly stream name, the Salesforce object name, and whether the stream is considered canonical. It fills in the common Salesforce fields like Id, CreatedDate, and SystemModstamp, then returns a StreamSpec object that the connector can later use while syncing.

**Call relations**: This helper is used while the file is being loaded to build the SALESFORCE_STREAMS list. It hands each completed stream definition to StreamSpec so the rest of the connector can treat all Salesforce objects in a consistent way.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 78–82)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: This function builds the Salesforce query used to fetch records for one stream. It includes all discovered fields, optionally filters to records newer than the cursor, and asks Salesforce to return results in oldest-to-newest order.

**Data flow**: It receives a stream description, a list of field names, and possibly a cursor from the previous sync. It turns those into one SOQL text query, including SELECT, FROM, optional WHERE, ORDER BY, and a page size limit, then returns that query string.

**Call relations**: The paginate method calls this after it has learned the object’s fields. The resulting query is then sent to Salesforce’s query endpoint so records can be fetched page by page.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 84–90)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: This function asks Salesforce what fields exist on a particular object. It avoids assuming that every Salesforce organization has the same schema, which is important because many Salesforce setups add custom fields.

**Data flow**: It receives an HTTP client and a Salesforce object name. It calls Salesforce’s describe endpoint, reads the returned field list, keeps valid field names, and returns them as a list of strings.

**Call relations**: The paginate method calls this before building its query. The field list it returns is passed into _build_soql so the connector can request the full shape of records exposed by that Salesforce organization.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 92–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main read loop for a Salesforce stream. It fetches changed records from Salesforce in pages and, when doing an incremental sync, also fetches records that were deleted since the last cursor.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It first discovers the object’s fields, builds a SOQL query, calls Salesforce’s query endpoint, yields each non-empty batch of records, follows Salesforce’s next page link until finished, and then, if a cursor was supplied, yields a deletion page when deleted records are found. If Salesforce responds with a permission or authentication refusal, it raises StreamSkipped so the system knows the stream could not be read.

**Call relations**: This method is the center of the connector’s syncing work. It calls _describe_fields to learn the schema, _build_soql to prepare the query, and _deleted_page to capture deletions after normal record paging. It is the method the surrounding sync framework relies on when it wants Salesforce data for a specific stream.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 121–139)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: This function asks Salesforce which records were hard-deleted during a time window. It turns those deleted IDs into a tombstone page so the rest of the system can remove or mark records that no longer exist upstream.

**Data flow**: It receives an HTTP client, a stream description, and the previous cursor. It sets the end of the window to the current UTC time, calls Salesforce’s deleted-records endpoint, gathers deleted record IDs, chooses the next cursor from Salesforce’s latest covered date when available, and returns a StreamPage containing the deletes and next cursor.

**Call relations**: The paginate method calls this after reading normal changed records, but only when there is already a cursor. It creates a StreamPage so the sync system can advance its cursor and process deletions alongside record updates.

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 141–144)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function cleans up each Salesforce record before the rest of the system sees it. Salesforce includes an attributes field with API metadata, and this function removes that wrapper so only the actual record fields remain.

**Data flow**: It receives one Salesforce record and its stream description. If the record contains attributes, it returns a new dictionary without that key; otherwise, it returns the original record unchanged.

**Call relations**: This is part of the connector’s record-shaping step after records have been fetched. It does not call other helper functions here; it simply prepares Salesforce data so downstream pages contain business fields rather than Salesforce transport metadata.


### Support and Conversation Platforms
Connectors that sync helpdesk tickets, customer conversations, support users, help center content, and related operational support data.

### `extensions/sources/ufo_ext_sources/freshdesk.py`

`io_transport` · `source sync / Freshdesk data fetching`

Freshdesk exposes many kinds of data: tickets, conversations, contacts, companies, agents, help articles, forum posts, and admin settings. This file is the read-only connector for that world. Without it, the system would not know where Freshdesk data lives, how to ask for it, or how to keep asking until all pages have been collected.

The file first defines the list of available “streams,” where a stream means one kind of record to sync, such as tickets or companies. It also maps many stream names to their Freshdesk REST API paths. REST API means a web interface where the connector asks Freshdesk for JSON data over HTTP.

The `FreshdeskConnector` then provides the real behavior. It builds an HTTP client using Freshdesk’s API-key style authentication, where the key is sent like a username with a dummy password. It chooses the right paging method for each stream. Simple streams follow Freshdesk’s `Link` header, which is like a “next page is over here” sign. Tickets use numbered pages and can be limited by an update cursor, so later syncs can fetch only changed tickets. Nested data, such as conversations inside tickets or articles inside solution folders, is fetched by first reading the parent records and then asking for each child list. If Freshdesk rejects access with an authorization error, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 55–69)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a `StreamSpec`, which is the small description the sync system uses to know one Freshdesk record type exists. It fills in common defaults so the long stream list stays readable.

**Data flow**: It receives a stream name plus optional details such as the Freshdesk object name, primary key, cursor field, and whether it is a main canonical stream. It substitutes sensible defaults when values are missing, then returns a `StreamSpec` object describing that stream.

**Call relations**: This helper is used while building the file’s Freshdesk stream catalog. It hands each finished stream description to the connector through `FRESHDESK_STREAMS`, so the rest of the system can later ask the connector to sync those streams.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector._make_client`  (lines 109–124)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to one Freshdesk tenant. It adds the right base address, timeouts, JSON headers, and authentication.

**Data flow**: It receives a base URL and a resolved credential. It trims the URL, prepares request time limits and headers, then either uses a supplied proxy transport unchanged or turns the credential’s API key into Freshdesk Basic authentication. It returns an asynchronous HTTP client ready to make requests, or raises an error if no usable authentication is present.

**Call relations**: The broader REST connector machinery calls this when it is preparing to read Freshdesk. This function hands back the network client that later pagination functions use for every API request.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 127–137)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: Builds the query options for one page of Freshdesk tickets. It makes ticket reads consistent, sorted by update time, and optionally limited to tickets updated after a cursor.

**Data flow**: It receives an optional cursor and a page number. It creates a dictionary asking Freshdesk for up to 100 tickets, in ascending update order, including useful extra ticket details. If a cursor is present, it adds `updated_since` so Freshdesk returns only newer records. The dictionary is returned for use in the HTTP request.

**Call relations**: `FreshdeskConnector._paginate_tickets` calls this each time it asks for the next ticket page. It is a small helper that keeps the ticket paging loop focused on fetching and stopping rules.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 139–213)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct way to fetch pages for a requested Freshdesk stream. This is the connector’s main dispatch point for reading data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name and sends the work to the matching paging routine: tickets, conversations, nested help-center/forum structures, canned responses, or ordinary link-header pagination. It yields lists of records page by page. If Freshdesk responds with 401 or 403, meaning the key is invalid or lacks permission, it turns that into a skipped stream notice.

**Call relations**: The sync framework calls this when it wants records for a particular stream. `paginate` then calls the specialized helpers in this file and passes their pages back upward to the framework.

*Call graph*: calls 6 internal fn (__init__, _paginate_conversations, _paginate_link_header, _paginate_three_level, _paginate_tickets, _paginate_two_level).


##### `FreshdeskConnector._paginate_link_header`  (lines 215–222)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a simple Freshdesk endpoint that uses a `Link` header to point to the next page. This covers most non-nested Freshdesk resources.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It starts at that path with the standard page size, follows Freshdesk’s next-page links, and yields each page of records as it arrives.

**Call relations**: `FreshdeskConnector.paginate` uses this for ordinary streams. The nested pagination helpers also use it as their basic building block whenever they need to fetch a parent list or a child list.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 224–241)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Freshdesk tickets using numbered pages, which Freshdesk treats differently from many other resources. It supports incremental syncing by using an update cursor.

**Data flow**: It receives an HTTP client and an optional cursor. Starting at page 1, it builds ticket query parameters, requests the ticket page, extracts the records, and yields them. It stops when Freshdesk returns no records, when a page is not full, or when it reaches Freshdesk’s 300-page ceiling.

**Call relations**: `FreshdeskConnector.paginate` calls this for the `tickets` stream. `FreshdeskConnector._paginate_conversations` also calls it first, because conversations are fetched ticket by ticket.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, paginate).


##### `FreshdeskConnector._paginate_conversations`  (lines 243–259)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches conversations by first finding tickets, then asking Freshdesk for the conversations attached to each ticket. This is needed because conversations are not read as one flat global list here.

**Data flow**: It receives an HTTP client and an optional ticket cursor. It reads ticket pages, takes each ticket ID, requests that ticket’s conversation pages, and adds the ticket ID to each conversation record if it is missing. It yields conversation pages after they have been tied back to their ticket.

**Call relations**: `FreshdeskConnector.paginate` calls this for the `conversations` stream. It relies on `_paginate_tickets` to find the parent tickets and `_paginate_link_header` to walk each ticket’s conversation pages.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (paginate).


##### `FreshdeskConnector._paginate_two_level`  (lines 261–272)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches data shaped like parent records with child records underneath them, such as categories with forums or folders with responses. It is a reusable helper for two-step Freshdesk trees.

**Data flow**: It receives an HTTP client, a parent API path, and a child path pattern containing the parent ID. It fetches parent pages, reads each parent’s ID, fills that ID into the child path, then fetches and yields the child pages. Parents without usable IDs are skipped.

**Call relations**: `FreshdeskConnector.paginate` calls this for several nested streams, including canned responses, solution folders, discussion forums, topics, and comments. It uses `_paginate_link_header` for both the parent walk and each child walk.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


##### `FreshdeskConnector._paginate_three_level`  (lines 274–297)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches data buried three levels deep, such as solution articles inside folders inside solution categories. It is the same idea as opening a filing cabinet, then a drawer, then the folders inside.

**Data flow**: It receives an HTTP client plus paths for the root level, middle level, and leaf level. It fetches root records, uses each root ID to fetch middle records, then uses each middle ID to fetch final leaf records. It yields only the leaf pages, skipping any parent or middle records that do not have IDs.

**Call relations**: `FreshdeskConnector.paginate` calls this for the `solution_articles` stream. Internally it repeatedly uses `_paginate_link_header`, because each level still uses Freshdesk’s ordinary next-page link style.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/intercom.py`

`io_transport` · `source sync`

Intercom does not expose all of its data in one simple way. Some resources use a search endpoint with a cursor, some use a scrolling company endpoint, some return one plain list, and some are nested inside another object. This file is the adapter that hides those differences. It is like a travel guide that knows which ticket booth to use for each destination.

The file defines the Intercom streams UFO can sync, including their names, primary keys, and cursor fields. A cursor is a saved marker, usually a timestamp, that lets the next run ask only for records newer than the last run. The connector adds the required Intercom API version header to every request, then chooses the right pagination method for each stream.

It also reshapes a few records before they leave the connector. For example, Intercom conversations contain nested source and contact information; this file copies important nested values into simple top-level fields so later SQL-style transforms can use them easily. For cursor fields stored as integers by Intercom, it converts them to strings because UFO’s watermark system expects string cursors.

If Intercom refuses access with an authorization error, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 52–66)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a small description of one Intercom data stream, such as conversations or contacts. This description tells the sync system what object to ask for, what field identifies a record, and what field can be used as the incremental cursor.

**Data flow**: It receives a stream name and optional settings like source object, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults when settings are not provided, then returns a StreamSpec object that the connector later uses as a recipe for syncing that stream.

**Call relations**: This helper is used while building the file-level INTERCOM_STREAMS list. It hands each stream definition to StreamSpec so the rest of the connector can treat all streams in a consistent way.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector._make_client`  (lines 103–106)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Intercom and adds the Intercom API version header. Without this header, Intercom may interpret requests using a different API version than this connector expects.

**Data flow**: It receives the base URL and resolved credential. It asks the parent RestConnector to create the authenticated client, adds the Intercom-Version header, and returns the ready-to-use async HTTP client.

**Call relations**: This fits into the setup step inherited from RestConnector. The base connector creates the general client, and this method adds the Intercom-specific requirement before any pagination method sends requests.


##### `IntercomConnector._build_search_body`  (lines 109–139)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the JSON request body for Intercom search endpoints. It tells Intercom how many records to return, how to sort them, where to continue within a page sequence, and which records are newer than the saved cursor.

**Data flow**: It receives a stream description, the saved cursor from the previous sync, and an optional starting_after token for moving through Intercom’s pages. It creates a dictionary with pagination, sorting, and a query filter. If the cursor looks like a number, it sends it as a number because Intercom rejects it as a string in this filter. The result is the body sent in a POST request.

**Call relations**: The search paginator calls this for normal search streams like conversations, contacts, and tickets. The conversation-parts paginator also calls it first, because it must search conversations before fetching each conversation’s nested parts.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 142–147)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: Safely picks the first dictionary from a list-like value. It is a small guard against Intercom returning missing, empty, or oddly shaped nested lists.

**Data flow**: It receives any value. If the value is a non-empty list and its first item is a dictionary, it returns that dictionary. Otherwise it returns nothing.

**Call relations**: This helper supports the flattening methods that need the first related contact or company from Intercom’s nested envelopes. It keeps those methods from repeating the same safety checks.


##### `IntercomConnector._flatten_conversation`  (lines 150–167)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Copies important nested conversation details into simple top-level fields. This makes later querying and transformation easier because consumers do not have to dig through Intercom’s nested source and contacts structures.

**Data flow**: It receives one conversation record. It copies the record, then, when present, lifts source type, subject, and body into fields like source__type. It also looks for the first associated contact and stores that contact id as requester_id. It returns the enriched copy without modifying the original record in place.

**Call relations**: The main flatten method calls this only for the conversations stream. It uses the shared _first helper to safely choose the first contact from Intercom’s nested contact list.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 170–179)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Copies a conversation part’s author information into simple fields. A conversation part is one message or event inside a larger Intercom conversation.

**Data flow**: It receives one conversation-part record. It copies the record, reads the nested author object if it exists, and adds author_type and author_id at the top level. It keeps any conversation_id already added by the paginator. It returns the enriched record.

**Call relations**: The main flatten method calls this for the conversation_parts stream. The conversation-parts paginator adds the parent conversation_id before records reach this flattening step.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 182–190)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Copies a contact’s first associated company id into a simple org_id field. This helps later steps connect people to organizations without understanding Intercom’s nested company envelope.

**Data flow**: It receives one contact record. It copies the record, looks inside the nested companies list, takes the first company if present, and stores its id or company_id as org_id. It returns the enriched copy.

**Call relations**: The main flatten method calls this for the contacts stream. Like conversation flattening, it uses _first to avoid errors when the nested company list is missing or empty.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 192–206)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Prepares an Intercom record for UFO’s storage and watermark system. It flattens selected nested fields and converts integer cursor timestamps into strings.

**Data flow**: It receives a raw record and its stream description. Depending on the stream name, it sends the record through the matching flattening helper for conversations, conversation parts, or contacts. Then, if the stream has a cursor field and the record’s cursor value is an integer, it returns a copy with that cursor value converted to text. Otherwise it returns the record as-is or with only the flattening changes.

**Call relations**: This is the shared cleanup step after records have been fetched by pagination. It delegates stream-specific reshaping to _flatten_conversation, _flatten_conversation_part, or _flatten_contact, then applies the cursor conversion needed by the broader sync adapter.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 208–252)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct way to fetch pages for each Intercom stream. This is necessary because Intercom uses several different API styles instead of one uniform pagination system.

**Data flow**: It receives an HTTP client, a stream description, and the saved cursor. It checks the stream name and routes the request to the matching paginator: search, company scroll, plain list, attributes, conversation parts, company segments, or activity logs. It yields pages of records as they arrive. If Intercom responds with a permission refusal, it turns that into a StreamSkipped error so the run can record the skip cleanly.

**Call relations**: This is the main dispatcher used by the source sync flow. It hands work to the specialized pagination methods and passes their yielded pages back upward. When Intercom returns 401 or 403, it creates StreamSkipped instead of letting that authorization failure stop unrelated streams.

*Call graph*: calls 8 internal fn (__init__, _paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search).


##### `IntercomConnector._paginate_search`  (lines 254–274)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches streams that use Intercom’s search API, such as conversations, contacts, and tickets. It keeps asking for the next page until Intercom says there is no next cursor.

**Data flow**: It receives the HTTP client, stream description, and saved cursor. It looks up the correct search path and record key, builds a search body, sends a POST request, yields any records returned, then reads Intercom’s starting_after token for the next page. When there is no next token, it stops.

**Call relations**: The main paginate dispatcher calls this for streams listed in the search-path map. It calls _build_search_body before each POST request so cursor filtering and page continuation are encoded correctly.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_scroll`  (lines 276–290)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches companies through Intercom’s scroll API. A scroll API is like asking for the next batch using a temporary ticket that the previous response gives you.

**Data flow**: It starts with no scroll_param, requests /companies/scroll, yields the returned company records, then saves the response’s scroll_param for the next request. If a response contains no records or no next scroll_param, it stops.

**Call relations**: The main paginate dispatcher calls this for the companies stream. It uses the base connector’s GET helper to perform each request and yields company pages back to the sync flow.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_list`  (lines 292–304)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches simple Intercom streams that return a single list response, such as admins, tags, teams, or segments. These streams do not need multi-page cursor logic here.

**Data flow**: It receives the HTTP client and stream description. It looks up the endpoint path, sends one GET request, then checks for a list under either the stream name or data. If it finds a non-empty list, it yields that list once and stops.

**Call relations**: The main paginate dispatcher calls this for streams listed in the plain-list path map. It is the simplest pagination branch because there is no follow-up token to hand off.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_attributes`  (lines 306–315)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Intercom data attributes for either companies or contacts. Data attributes are custom fields that describe what extra properties Intercom can store for those objects.

**Data flow**: It receives the HTTP client and stream description. It maps the stream name to an Intercom model name, requests /data_attributes with that model as a parameter, and yields the returned data list if it is not empty.

**Call relations**: The main paginate dispatcher calls this for company_attributes and contact_attributes. It uses the stream-to-model map to turn UFO’s stream name into the parameter Intercom expects.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_conversation_parts`  (lines 317–349)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the messages or events inside conversations. Intercom does not return these parts directly from the search page, so this method first finds conversations and then fetches each conversation in detail.

**Data flow**: It receives the HTTP client and saved cursor. It searches conversations using the same cursor logic as the conversations stream, then loops through each conversation id. For each id, it requests the detailed conversation, extracts its conversation_parts list, adds the parent conversation_id to each part when missing, and yields those parts. It follows search pagination until no starting_after token remains.

**Call relations**: The main paginate dispatcher calls this for the conversation_parts stream. It calls _build_search_body to search parent conversations, then uses GET requests for each conversation detail before yielding the child records.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_company_segments`  (lines 351–375)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the segment memberships for each company. It first scrolls through companies, then asks Intercom which segments each company belongs to.

**Data flow**: It receives the HTTP client. It requests company batches through /companies/scroll, then for each company with an id, requests /companies/{id}/segments. It adds company_id to each returned segment record when missing and yields segment lists. It continues using scroll_param until there are no more companies or no next scroll token.

**Call relations**: The main paginate dispatcher calls this for the company_segments stream. It combines the company scroll endpoint with the per-company segments endpoint so downstream consumers get direct company-to-segment records.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_activity_logs`  (lines 377–399)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches admin activity logs, optionally starting after a saved created_at cursor. These logs use their own endpoint and pagination links rather than the search or scroll patterns.

**Data flow**: It receives the HTTP client and optional cursor. If a cursor exists, it sends it as created_at_after on the first request. It requests /admins/activity_logs, yields any activity_logs records, then follows the next page link from the response. If Intercom gives an absolute URL, it strips the base URL so the connector can request the relative path. It stops when there is no next link.

**Call relations**: The main paginate dispatcher calls this for the activity_logs stream. This method owns the activity-log-specific paging loop and passes each page of log records back to the sync flow.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/zendesk.py`

`io_transport` · `source sync`

Zendesk exposes its data through many web API endpoints, and those endpoints do not all behave the same way. This file is the adapter that hides those differences. Without it, the system would not know where to ask Zendesk for tickets, comments, users, articles, or admin settings, nor how to keep paging through large result sets.

The file first defines the list of Zendesk “streams,” where a stream means one kind of thing to sync, such as tickets or groups. Most streams are ordinary paged lists: ask Zendesk for one page, read the records, then follow Zendesk’s next-page link. Some high-volume streams use Zendesk’s incremental cursor export, which starts from a saved time and keeps following cursor links until Zendesk says the stream is done. This is like resuming a long audiobook from the last timestamp instead of starting again at chapter one.

A few streams need special treatment. Ticket comments are buried inside ticket event records, so this connector extracts only comment events and adds the ticket ID. User identities are fetched by first listing users, then asking for each user’s identities. Tickets can also arrive with related user records “sideloaded,” meaning Zendesk includes extra user data in the same response; this file copies requester, submitter, and assignee emails onto each ticket when possible.

If Zendesk refuses access with an authorization error, the connector marks that stream as skipped instead of pretending the data is empty.

#### Function details

##### `_stream`  (lines 58–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: Creates a small description of one Zendesk data stream, such as tickets or users. This keeps the long stream list compact and consistent.

**Data flow**: It receives a stream name and optional details like the Zendesk endpoint name, primary key, and timestamp fields. It fills in sensible defaults when details are not provided, then returns a StreamSpec object that the connector can use later to fetch that stream.

**Call relations**: This helper is used while the file is being loaded to build the Zendesk stream catalog. It hands the completed stream descriptions to StreamSpec, which is the shared source framework’s way of representing a syncable collection.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 142–167)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: Adds useful related information from a Zendesk response onto the main records. In practice, it copies user email addresses from sideloaded user data onto ticket records.

**Data flow**: It receives the main records, the full API page, and instructions for which fields should be matched. It builds a lookup table from the extra arrays in the page, matches each record’s ID fields to those related records, and writes email fields back onto the original records when found.

**Call relations**: The incremental ticket reader calls this after fetching a page that includes both tickets and users. It enriches records before they are yielded to the rest of the sync flow, so later code can use ticket email fields directly without performing another lookup.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 176–177)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: Finds the JSON field name where Zendesk puts records for a stream. Most streams use their stream name, but a few Zendesk endpoints use different names.

**Data flow**: It receives a StreamSpec. It checks a small override table for special cases, and returns either the override or the stream’s own name.

**Call relations**: The default paginator calls this before reading a normal Zendesk page. It tells that paginator which key to pull from the response body so it can extract the actual list of records.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 180–192)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: Converts a saved sync cursor into the Unix timestamp format Zendesk expects. A Unix timestamp is a number of seconds since the start of 1970 in UTC time.

**Data flow**: It receives a cursor that may be empty, already numeric, or an ISO-style date string. Empty or unreadable values become 0, numeric text becomes an integer, and date text is parsed into seconds since 1970.

**Call relations**: The incremental paginators call this when building their starting URL. It lets ticket, user, organization, metric-event, comment, and identity syncs resume from a previous point instead of fetching all history every time.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 195–204)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: Turns Zendesk’s next-page URL into the path form this connector uses for follow-up requests. It keeps the endpoint path and query string, but drops the website domain.

**Data flow**: It receives a next-page URL or nothing. If there is no usable path, it returns nothing; otherwise it parses the URL and returns a path such as `/api/v2/...?...`.

**Call relations**: Every pagination method uses this when Zendesk points to the next batch of data. It is the small bridge between Zendesk’s full links and the connector’s request helper, which expects a path.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 206–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right paging strategy for a Zendesk stream and yields batches of records. It is the main doorway the source framework uses to read Zendesk data.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. Based on the stream name, it delegates to the special ticket-comment reader, the user-identity reader, the incremental cursor reader, or the default page reader, then passes each batch onward. If Zendesk returns a 401 or 403 refusal, it raises StreamSkipped with a clear message.

**Call relations**: The broader sync runner calls this for each stream. This method then routes the work to the appropriate private paginator and translates authorization failures into a skip signal that the source framework understands.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 232–253)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads high-volume Zendesk streams using Zendesk’s incremental cursor API. This is used for large datasets where the connector should resume from a timestamp and move forward efficiently.

**Data flow**: It receives an HTTP client, a stream description, and a cursor. It converts the cursor to a Unix timestamp, builds the incremental export URL, fetches each page, optionally enriches tickets with sideloaded user emails, yields non-empty record batches, and follows Zendesk’s cursor links until the end of the stream.

**Call relations**: The main paginate method calls this for streams listed as incremental cursor streams. It uses _cursor_to_unix to choose the start point, _apply_sideload when ticket pages include user data, and _next_page_path to keep moving through Zendesk’s after-url or next-page links.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 255–265)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads ordinary Zendesk list endpoints that use simple next-page pagination. This covers many admin, Help Center, and community resources.

**Data flow**: It receives an HTTP client and a stream description. It builds the first API path, figures out which JSON field contains records, fetches a page, yields the records if any exist, and repeats with the next-page link until there is no next page.

**Call relations**: The main paginate method calls this when a stream does not need special handling. It relies on _data_field to read the right response key and _next_page_path to follow Zendesk’s pagination links.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 267–299)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Extracts ticket comments from Zendesk’s ticket event feed. Zendesk does not provide these comments in the same simple shape as many other streams, so this function reshapes them into standalone comment records.

**Data flow**: It receives an HTTP client and a cursor. It starts from the cursor time, fetches ticket event pages with comment events included, scans each event’s child events, keeps only child events whose type is Comment, adds the parent ticket ID, normalizes numeric creation times into readable UTC timestamps, and yields batches of comments.

**Call relations**: The main paginate method calls this only for the ticket_comments stream. It uses _cursor_to_unix to start from the right time and _next_page_path to continue through Zendesk’s event pages.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate); 1 external calls (fromtimestamp).


##### `ZendeskConnector._paginate_user_identities`  (lines 301–326)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads identity records for Zendesk users, such as login or contact identities, by visiting each updated user and then fetching that user’s identities.

**Data flow**: It receives an HTTP client and a cursor. It incrementally fetches users from the cursor time, skips invalid user entries, then for each valid user ID it requests that user’s identities page by page and yields any identity records it finds. It continues until the user export reaches its end.

**Call relations**: The main paginate method calls this for the users_identities stream. It uses _cursor_to_unix to decide which users to inspect and _next_page_path both for identity pages and for the outer user pagination loop.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).
