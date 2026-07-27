# CRM, sales, and customer-support connectors  `stage-13.1.5`

This stage is part of the shared data-gathering layer. It connects the system to tools that teams use to manage customers, sales, and support. Each connector knows how to talk to one outside service, ask for the right records, handle login, follow paged results, and reshape the replies into a common form the rest of the system can store, search, and sync.

The Attio connector reads companies, people, deals, tasks, notes, meetings, and call recordings. HubSpot covers a wide range of CRM and marketing data, including standard records, custom objects, relationships, email events, analytics, and consent information. Salesforce reads accounts, contacts, opportunities, tasks, and related objects, and also detects records deleted since the last sync. Freshdesk gathers support tickets, contacts, conversations, knowledge-base articles, and forum content. Intercom reads conversations, contacts, companies, admins, tags, and activity logs. Zendesk brings in tickets, users, organizations, Help Center articles, and community posts.

Together, these files act like adapters for different plug shapes, making many platforms feed one steady sync pipeline.

## Files in this stage

### CRM and sales platforms
Connectors that ingest customer, company, deal, opportunity, task, activity, and related CRM records from sales-focused systems.

### `extensions/sources/ufo_ext_sources/attio.py`

`io_transport` · `sync data fetching and record normalization`

Attio’s API does not return every kind of information in the same shape. Company and people records hide their useful fields inside nested “value cells”; tasks and notes use one style of paging; meetings and call recordings use another; transcripts must be fetched separately for each recording. This file is the adapter that smooths all of that into a consistent stream of records.

The connector defines which Attio streams exist and how each one should be identified, for example by `record_id`, `task_id`, or `call_recording_id`. During a sync, it asks Attio for pages of data, using the correct endpoint and paging method for each stream. If Attio says a standard object is disabled, or the OAuth permission grant is missing a needed scope, the connector skips that stream instead of failing the whole sync.

The most important work is flattening. Attio often stores a field like an email address, company domain, status, or linked record inside a small nested object. The flattening helpers pull out the human-useful value and place it at the top level. For call recordings, the connector also joins transcript segments into readable text. Without this file, Attio data would arrive incomplete, hard to identify, and too nested for the rest of the system to use reliably.

#### Function details

##### `_records_stream`  (lines 35–43)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a standard stream description for Attio object records such as companies, people, and deals. This tells the sync system what the stream is called, which Attio object it reads from, and which field uniquely identifies each record.

**Data flow**: It receives a friendly stream name, an Attio object slug, and whether the stream is canonical. It packages those choices into a `StreamSpec`, including the primary key `record_id` and the rule that missing records should be treated as deleted. The result is a stream definition used later by the connector.

**Call relations**: This helper is used while the file builds the list of Attio streams. It hands its result to `StreamSpec.__init__`, which creates the stream metadata consumed by the connector during sync.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 70–71)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: Safely pulls one named ID out of a nested dictionary. It exists because Attio sometimes wraps IDs inside small objects instead of returning them directly.

**Data flow**: It receives any value and the key to look for. If the value is a dictionary, it returns the matching entry; otherwise it returns nothing. It does not change anything.

**Call relations**: It is called by `AttioConnector._value_primitive` when that function needs a fallback ID from nested option or status data.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 80–81)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: Builds the request body used when asking Attio for a page of standard object records. It sets the page size and where the page should start.

**Data flow**: It receives an offset, meaning how many records have already been read. It returns a small dictionary with Attio’s record query limit and that offset. Nothing else is changed.

**Call relations**: `AttioConnector.paginate` calls this each time it requests another page from a standard object endpoint.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._value_primitive`  (lines 84–127)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: Turns one Attio value cell into the simplest useful value, such as text, an email address, a phone number, a status title, a currency amount, or a linked record ID. This is the key translator from Attio’s internal field shapes to ordinary data.

**Data flow**: It receives one dictionary representing a field value from Attio. It checks the possible places Attio may store the real value, including nested option and status objects, reference IDs, actor IDs, and location parts. It returns one plain value, such as a string, number, or `None` if no useful value is found.

**Call relations**: This function is used by the flattening helpers to clean individual field values. When an option or status only has an ID object, it calls `_nested_id` to pull out the usable ID.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 130–145)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: Converts one Attio field cell into a single simple value whenever possible. It knows how to deal with cells that are lists, dictionaries, or already plain values.

**Data flow**: It receives one cell from an Attio record. If the cell is a list, it extracts simple values from each item, removes empty results, and usually returns the first useful value; for multi-select options it keeps the list. If the cell is a dictionary, it extracts one primitive value. The output is a cleaner value ready to store.

**Call relations**: This helper is part of the record-flattening path used by `AttioConnector._flatten_values`, which prepares Attio object records for the rest of the sync system.


##### `AttioConnector._flatten_list_cell`  (lines 148–155)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: Converts an Attio field cell into a list of simple values. It is used for fields where keeping all values matters, such as email addresses or phone numbers.

**Data flow**: It receives a cell that may be a list or a single value. It extracts the useful primitive values, removes empty ones, and always returns a list. The result may be an empty list if there was nothing useful to keep.

**Call relations**: This helper is used inside `AttioConnector._flatten_values` for known multi-value fields such as domains, categories, email addresses, and phone numbers.


##### `AttioConnector._flatten_values`  (lines 158–192)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Flattens all custom and standard attributes inside an Attio object record. It turns Attio’s nested `values` block into ordinary top-level fields.

**Data flow**: It receives the record’s `values` dictionary. For each field, it chooses either list-style flattening or single-value flattening, then adds a plain field to the output. It also creates convenient shortcuts such as `email`, `phone`, `domain`, `category`, `first_name`, and `last_name` when those can be derived.

**Call relations**: This function is used by `AttioConnector._flatten_record` after that function has already lifted the record’s identity fields. Together they turn a standard Attio object record into a usable sync record.


##### `AttioConnector._flatten_record`  (lines 195–209)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns a standard Attio object record, such as a company or person, into a flat record with a clear primary key. This makes the record easy for the rest of the system to store and compare.

**Data flow**: It receives the raw Attio record and the stream definition. It pulls `record_id`, `object_id`, and `workspace_id` out of the nested ID block, copies creation and update times, optionally copies a cursor field, and then adds flattened attribute values. It returns a new flat dictionary.

**Call relations**: `AttioConnector.flatten` calls this for streams that are not tasks, notes, meetings, or call recordings. It relies on `AttioConnector._flatten_values` to clean the nested Attio attributes.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 212–216)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `task_id` to a raw Attio task. This gives the sync system a reliable way to identify the task.

**Data flow**: It receives a raw task record. It copies the record, reads the task ID from the nested `id` field when needed, and writes that value as `task_id`. It returns the copied and enriched task.

**Call relations**: `AttioConnector.flatten` calls this when the current stream is `tasks`.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 219–223)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `note_id` to a raw Attio note. This makes notes match the primary key expected by the stream definition.

**Data flow**: It receives a raw note record. It copies the record, extracts the note ID from the nested `id` block when present, and stores it as `note_id`. It returns the updated copy.

**Call relations**: `AttioConnector.flatten` calls this when the current stream is `notes`.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 226–230)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `meeting_id` to a raw Attio meeting. This lets meetings be stored and updated consistently across sync runs.

**Data flow**: It receives a raw meeting record. It copies the meeting, extracts the nested meeting ID if necessary, and writes it as `meeting_id`. It returns the updated meeting copy.

**Call relations**: `AttioConnector.flatten` calls this when the current stream is `meetings`.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 233–254)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares a call recording record for storage by adding its main ID, choosing a usable recording URL, and turning transcript segments into readable text. This makes recordings searchable and easier to display.

**Data flow**: It receives a raw call recording. It copies the record, extracts `call_recording_id`, falls back from `recording_url` to `web_url` if needed, then walks transcript segments and joins speaker names and speech into `transcript_text`. It returns the enriched recording.

**Call relations**: `AttioConnector.flatten` calls this for the `call_recordings` stream after pagination has already collected recordings and, when available, attached transcript data.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 256–265)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening routine for the current Attio stream. It is the main doorway between raw Attio API responses and normalized records.

**Data flow**: It receives one raw record and the stream it came from. It checks the stream name, sends the record to the matching helper for tasks, notes, meetings, call recordings, or standard object records, and returns the cleaned record. It does not fetch more data.

**Call relations**: The broader sync framework calls this after records have been paged from Attio. It hands work off to `AttioConnector._flatten_task`, `_flatten_note`, `_flatten_meeting`, `_flatten_call_recording`, or `_flatten_record` depending on the stream.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 267–321)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches all records for one Attio stream, one page at a time. It hides the fact that different Attio resources use different endpoints and paging styles.

**Data flow**: It receives an HTTP client, a stream definition, and an unused cursor value. For tasks and notes it uses simple offset paging; for meetings and call recordings it uses cursor paging; for standard objects it posts query bodies with offsets. It yields lists of raw records and may raise `StreamSkipped` when a stream cannot be read because it is disabled or lacks permission.

**Call relations**: This is the connector’s main read loop for Attio streams. It delegates paging to `AttioConnector._paginate_simple`, `_paginate_cursor`, and `_paginate_call_recordings`, builds standard-object query bodies with `_build_query_body`, and uses the error helpers to decide when to skip instead of fail.

*Call graph*: calls 8 internal fn (__init__, _build_query_body, _is_object_disabled, _is_scope_unauthorized, _paginate_call_recordings, _paginate_cursor, _paginate_simple, _scope_skip_reason).


##### `AttioConnector._paginate_simple`  (lines 323–330)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use straightforward offset paging, such as tasks and notes. Offset paging means asking for records starting at a numbered position, like reading pages in a book.

**Data flow**: It receives an HTTP client, an API path, and a page size. It asks the shared REST helper for pages from that path and yields each page of records found under the `data` field. It does not reshape the records.

**Call relations**: `AttioConnector.paginate` calls this for the `tasks` and `notes` streams.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._paginate_cursor`  (lines 332–350)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use cursor paging, such as meetings and call recordings. A cursor is a token from the server that says where to continue next.

**Data flow**: It receives an HTTP client, an API path, a page size, and optional query parameters. It asks the shared REST helper for pages, reading records from `data` and following `pagination.next_cursor` until there are no more pages. It yields each page unchanged.

**Call relations**: `AttioConnector.paginate` calls this for meetings, and `AttioConnector._paginate_call_recordings` uses it both to list meetings and to list recordings for each meeting.

*Call graph*: called by 2 (_paginate_call_recordings, paginate).


##### `AttioConnector._paginate_call_recordings`  (lines 352–388)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Collects call recordings by first walking through meetings, then reading recordings under each meeting. It also attaches meeting context and fetches transcripts when available.

**Data flow**: It receives an HTTP client. It pages through meetings, extracts each meeting ID, title, start time, end time, and duration, then pages through that meeting’s recordings. For each recording it adds parent meeting details, fetches transcript data if a recording ID exists, and yields pages of enriched recording records.

**Call relations**: `AttioConnector.paginate` calls this for the `call_recordings` stream. Inside the flow it uses `_paginate_cursor` for both meeting and recording lists, `_meeting_id` and `_call_recording_id` to find IDs, `_datetime_of` and `_duration_seconds` for timing details, and `_fetch_transcript` for transcript text.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (paginate).


##### `AttioConnector._fetch_transcript`  (lines 390–402)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: Fetches the transcript for one call recording. If Attio says the transcript is not found or not ready yet, it treats that as no transcript rather than a fatal error.

**Data flow**: It receives an HTTP client plus a meeting ID and recording ID. It builds the transcript endpoint path and performs a GET request. If the response contains a dictionary under `data`, it returns that dictionary; if the transcript is unavailable with a 404 or 409 response, it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this while enriching each recording. The transcript it returns is later flattened by `AttioConnector._flatten_call_recording` into readable `transcript_text`.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 405–409)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a meeting’s ID from the shape Attio returned. It accepts both nested ID objects and plain string IDs.

**Data flow**: It receives a meeting dictionary. If `id` is a dictionary, it returns `id.meeting_id`; if `id` is already a string, it returns that. If neither shape is usable, it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this before requesting recordings for a meeting. Without a meeting ID, that meeting is skipped for recording lookup.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 412–416)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a call recording’s ID from the shape Attio returned. This ID is needed to fetch the recording transcript.

**Data flow**: It receives a call recording dictionary. If `id` is a dictionary, it returns `id.call_recording_id`; if `id` is already a string, it returns that. Otherwise it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this for each recording. When an ID is found, the flow continues to `AttioConnector._fetch_transcript`.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 419–423)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: Pulls a usable date or date-time string from Attio’s meeting time shape. Attio may send timed meetings as `datetime` or all-day meetings as `date`.

**Data flow**: It receives any value that might describe a meeting start or end. If it is a dictionary, it returns the `datetime` value first, or the `date` value if no datetime exists. If the shape is not recognized, it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this while copying meeting start and end times onto recordings.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 426–436)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: Calculates a rough meeting duration in seconds from start and end time strings. It avoids crashing if either time is missing or cannot be understood.

**Data flow**: It receives optional start and end strings. If both exist, it parses them as ISO 8601 date-time values, computes the difference, and returns the duration in seconds, never below zero. If parsing fails or a value is missing, it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this after extracting meeting start and end times. It uses `datetime.datetime.fromisoformat` to parse the time strings.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 439–448)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects the specific Attio error that means a standard object, such as deals, is not enabled in the workspace. This lets the sync skip that stream cleanly.

**Data flow**: It receives an HTTP status error. It first checks for status code 400, then tries to read the JSON response body. It returns `true` only when the body contains the code `standard_object_disabled`; otherwise it returns `false`.

**Call relations**: `AttioConnector.paginate` calls this when a standard object query fails. If it returns true, pagination raises `StreamSkipped` with a clear reason.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._is_scope_unauthorized`  (lines 451–460)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects when Attio refused a request because the OAuth grant is missing a required permission scope. OAuth is the permission system that lets this connector access Attio on a user’s behalf.

**Data flow**: It receives an HTTP status error. It checks for status code 403 and then looks for a JSON response code of `unauthorized`. It returns `true` for that exact case and `false` for other errors.

**Call relations**: `AttioConnector.paginate` calls this around meetings and call recordings. If the missing-scope case is detected, pagination uses `_scope_skip_reason` and raises `StreamSkipped`.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._scope_skip_reason`  (lines 463–469)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a readable explanation for skipping a stream because an OAuth permission is missing. This gives operators a useful clue instead of a vague failure.

**Data flow**: It receives an HTTP status error. It tries to read the response JSON and extract its message. It returns a sentence saying the OAuth grant is missing a required scope, including Attio’s message when available.

**Call relations**: `AttioConnector.paginate` calls this after `_is_scope_unauthorized` confirms the cause. The returned text becomes the reason attached to `StreamSkipped`.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/hubspot.py`

`io_transport` · `during HubSpot sync stream reading`

HubSpot is not one simple database. Different HubSpot features expose data through different web API shapes, pagination styles, field names, and permission rules. This file is the adapter that hides that mess from the rest of the system. Think of it like a universal plug: HubSpot has many socket shapes, and this connector turns them into one steady stream of records.

The connector declares all the HubSpot “streams” it knows about, such as contacts, deals, owners, lists, forms, campaigns, files, associations, and custom objects. For normal CRM objects, it asks HubSpot what fields exist, searches records in modified-time order, and uses a cursor so later runs can pick up where the last one stopped. Because HubSpot’s boundary filter is inclusive, it carefully avoids sending the same boundary record twice. It also does an extra pass for archived records so deletions become tombstones instead of silently disappearing.

For product areas that do not use the CRM search API, the file contains many smaller walkers. Some read simple list endpoints. Others fan out: for example, read campaigns, then read each campaign’s assets; read forms, then read each form’s submissions. It also normalizes records by lifting nested fields into top-level fields, making later indexing simpler. If HubSpot says a stream is unavailable because the account lacks a feature or permission, the connector marks that stream as skipped rather than failing the whole sync.

#### Function details

##### `_normalize_epoch_millis`  (lines 248–255)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Turns HubSpot timestamps written as milliseconds since 1970 into a readable ISO date string. It leaves booleans and already-normal values alone so it does not accidentally rewrite unrelated data.

**Data flow**: It receives one value. If the value is a number, or a digit-only string, it treats it as milliseconds from the Unix epoch and converts it to a UTC timestamp string. Otherwise it returns the original value unchanged.

**Call relations**: It is used when product-style HubSpot records need cleanup, especially knowledge articles, email events, and analytics views, before those records are handed back to the sync pipeline.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 258–267)

```
def _stream(name: str, *, object_type: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a standard HubSpot CRM stream definition. A stream definition tells the sync system what object to read, what field identifies a record, and what timestamp is used for incremental updates.

**Data flow**: It receives a stream name, HubSpot object type, and whether the stream is canonical. It returns a StreamSpec with HubSpot’s usual CRM fields: id as the key and hs_lastmodifieddate as the cursor.

**Call relations**: It is used at module load time to define many CRM object streams such as companies, contacts, deals, tasks, tickets, and commerce objects.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 270–289)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='createdAt', updated_at_field: str | None='updatedAt', pagi
```

**Purpose**: Creates a stream definition for HubSpot product APIs that do not follow the normal CRM search shape. These streams often have different keys, timestamp fields, or pagination rules.

**Data flow**: It receives the stream name, source object name, key fields, timestamp field names, and optional pagination instructions. It returns a StreamSpec marked as non-canonical because these are supporting product surfaces rather than core CRM records.

**Call relations**: It is used while declaring streams like owners, workflows, forms, marketing emails, analytics reports, associations, consent states, and sequences.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 292–304)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Builds a reusable pagination recipe for HubSpot GET endpoints that return records in a results list and a next cursor. Pagination means fetching a large result set one page at a time.

**Data flow**: It receives an API path. It returns a Pagination object that says where records live in the response, where the next cursor lives, and which query parameters control cursor and page size.

**Call relations**: It is used when defining flat product API streams whose paging behavior can be handled by the shared RestConnector strategy.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 307–317)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Creates a stream definition for synthetic relationship tables, such as deal-to-contact links. These streams do not represent HubSpot objects themselves; they represent pairs of linked records.

**Data flow**: It receives a stream name and parent object type. It returns a StreamSpec with no cursor because HubSpot does not expose modification times for these association rows.

**Call relations**: It is used at module load time to define junction streams like deal_contacts, ticket_companies, and task_contacts.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 622–652)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the JSON request body for HubSpot’s CRM search API. This is the request that asks HubSpot for records, sorted by update time and optionally filtered from a saved cursor.

**Data flow**: It takes the stream definition, the full list of properties to request, an optional cursor, and an optional page token. It returns a dictionary that HubSpot accepts as a search request body.

**Call relations**: The main CRM pagination path and the custom-object pagination path call this before each search request, so they can read records in a predictable incremental order.

*Call graph*: called by 2 (_paginate_custom_object_records, _paginate_unchecked).


##### `HubSpotConnector._flatten`  (lines 655–666)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a normal HubSpot CRM record into a flatter record that is easier for the rest of the system to index. HubSpot stores most fields inside a properties bag; this lifts them up.

**Data flow**: It receives one raw CRM record. It copies id, createdAt, updatedAt, and archived status, then merges any properties into the top level. It returns the flattened dictionary.

**Call relations**: The public flatten method calls this for ordinary CRM object streams after pages have been fetched.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 669–692)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records from HubSpot product APIs, which often use different shapes from CRM records. It makes object IDs, property bags, form submission values, and a few timestamp fields look more consistent.

**Data flow**: It receives one product API record and the stream definition. It copies the record, fills id from objectId when needed, lifts properties and values arrays into top-level fields, normalizes selected timestamps, and returns the result.

**Call relations**: The public flatten method calls this for product API streams. It also uses _normalize_epoch_millis when HubSpot supplies timestamps as millisecond numbers.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 694–701)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening rule for each kind of HubSpot stream. This gives the rest of the sync system one consistent place to ask, “make this record usable.”

**Data flow**: It receives a raw record and the stream it came from. Junction and custom-object rows are already shaped, product API rows go through product normalization, and normal CRM records go through CRM flattening. It returns the normalized record.

**Call relations**: The broader source framework calls this after records are paginated. It delegates to _flatten or _flatten_product_api depending on the stream.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector._list_properties`  (lines 703–711)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which fields exist for a CRM object type. This matters because HubSpot’s search API only returns fields that are explicitly requested.

**Data flow**: It receives an HTTP client and object type name. It calls HubSpot’s properties endpoint, extracts property names from the response, and returns them as a list of strings.

**Call relations**: The normal CRM pagination path calls this before searching a stream, so it can request every available field rather than relying on a hand-written list.

*Call graph*: called by 1 (_paginate_unchecked).


##### `HubSpotConnector.paginate`  (lines 713–726)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the connector’s safe entry point for reading one stream. It turns some HubSpot permission failures into a clean skipped-stream result instead of crashing the whole run.

**Data flow**: It receives an HTTP client, a stream definition, and an optional saved cursor. It yields pages from the unchecked paginator. If HubSpot returns an authorization or stream-unavailable error, it raises StreamSkipped with a human-readable reason.

**Call relations**: The source framework calls this when syncing each stream. It wraps _paginate_unchecked and consults _is_stream_unavailable and _stream_skip_reason when HubSpot refuses access.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 728–780)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Routes a stream to the correct HubSpot reading strategy. It is the main dispatcher that decides whether a stream is standard CRM search, custom objects, product APIs, or synthetic junction data.

**Data flow**: It receives a stream and optional cursor. It checks the stream type, then yields pages from the matching helper. For normal CRM streams, it lists properties, repeatedly searches HubSpot, removes duplicate cursor-boundary records, and finally yields archived-record tombstones.

**Call relations**: paginate calls this for real work. This function hands off to helpers such as _paginate_product_api, _paginate_custom_objects, _paginate_junction, _list_properties, _build_search_body, and _paginate_archived_ids.

*Call graph*: calls 6 internal fn (_build_search_body, _list_properties, _paginate_archived_ids, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._is_stream_unavailable`  (lines 783–806)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether a HubSpot error means “this account cannot access this stream” rather than “the sync is broken.” It looks for permission-related messages in 403 responses.

**Data flow**: It receives an HTTP error. It checks the status code and response body message. It returns true when the message matches known missing-permission or missing-scope wording.

**Call relations**: paginate uses it to skip unavailable streams. Archived sweeps also use it so optional deletion checks do not fail a stream when HubSpot blocks that object.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 809–818)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Creates the message stored when a HubSpot stream is skipped. The message names the stream and includes HubSpot’s explanation when available.

**Data flow**: It receives a stream name and HTTP error. It reads the JSON error body if possible and builds a readable string. The output is used as the StreamSkipped reason.

**Call relations**: paginate calls this only after deciding that a stream should be skipped rather than treated as a fatal sync error.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 820–855)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds CRM records that HubSpot has archived or deleted so the local system can tombstone them. Without this, deleted HubSpot records could stay searchable forever.

**Data flow**: It receives a client and stream. It pages through the object list endpoint with archived=true, collects record IDs, and yields StreamPage objects containing delete IDs. If HubSpot does not support the archived sweep for that object, it quietly stops.

**Call relations**: _paginate_unchecked calls this after normal CRM search pages. It uses _is_archived_sweep_unsupported and _is_stream_unavailable to tell harmless unsupported cases from real failures.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 857–951)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Dispatches product API streams to their special readers. HubSpot product areas are inconsistent, so this function is the traffic director for all non-standard streams.

**Data flow**: It receives a product stream and cursor. It compares the stream name to known special cases and yields pages from the matching helper. If the stream has only a simple URL, it uses the generic collection paginator.

**Call relations**: _paginate_unchecked calls this for product API streams. It hands off to many focused helpers, such as analytics, campaigns, events, associations, lists, sequences, forms, pipelines, and conversations.

*Call graph*: calls 21 internal fn (_paginate_analytics_reports, _paginate_analytics_views, _paginate_association_labels, _paginate_associations, _paginate_campaign_assets, _paginate_consent_states, _paginate_conversation_messages, _paginate_email_events, _paginate_event_occurrences, _paginate_event_types (+11 more)); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._paginate_get_collection`  (lines 953–981)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a simple HubSpot collection endpoint that uses limit and after query parameters. It is the shared loop for many straightforward product API lists.

**Data flow**: It receives an API path, optional limit, and optional extra parameters. It repeatedly calls the endpoint, extracts dictionary records from results, fills id from objectId when needed, yields non-empty pages, and follows the next cursor until done.

**Call relations**: Many specialized product helpers call this when their endpoint has HubSpot’s common results-plus-paging shape.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_product_api, _paginate_sequences, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 983–1018)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Reads all custom object records defined in a HubSpot account. Custom objects are account-specific, so the connector must first discover their schemas before it can read records.

**Data flow**: It fetches custom object schemas, finds each object type ID and property list, builds a temporary search stream, yields that object’s records, then yields tombstones for archived custom object records.

**Call relations**: _paginate_unchecked calls this for the custom_objects stream. It coordinates schema discovery, record pagination, custom row shaping, and archived-ID sweeps.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 1020–1022)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the definitions of custom object types available in the HubSpot account. A schema is a description of an object type and its fields.

**Data flow**: It receives an HTTP client, calls HubSpot’s custom schema endpoint, filters the results to dictionaries, and returns the list.

**Call relations**: Custom-object pagination uses it to know what to read. Association discovery also uses it so custom object types can be included in relationship scans.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 1025–1030)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Finds the usable object type identifier inside a custom object schema. HubSpot may expose this under a few different field names.

**Data flow**: It receives one schema dictionary. It checks objectTypeId, fullyQualifiedName, and name in order, returning the first non-empty string or None if none exists.

**Call relations**: Custom-object pagination, custom-object row building, and association object-type discovery call this whenever they need the API name of a custom object.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1033–1048)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Builds the list of custom object fields that should be requested from HubSpot. It includes normal properties plus display fields used to label records.

**Data flow**: It receives a schema. It collects unique property names, the primary display property, and secondary display properties, then returns that list.

**Call relations**: _paginate_custom_objects calls this before searching each custom object type, because HubSpot search only returns requested properties.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1050–1085)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Searches records for one custom object type, with cursor-based incremental behavior. It also avoids duplicate records at the inclusive cursor boundary.

**Data flow**: It receives a stream, schema, requested properties, and optional cursor. It repeatedly builds a search body, posts it to HubSpot, filters duplicate boundary records, converts raw records into custom object rows, and yields pages.

**Call relations**: _paginate_custom_objects calls this once per discovered custom object type. It uses _build_search_body for requests and _custom_object_row for output shaping.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1087–1126)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Turns one raw custom object record into a rich, searchable row. It adds object metadata so records from different custom object types do not collide.

**Data flow**: It receives a raw record and its schema. It extracts the object type, record ID, labels, display fields, timestamps, and properties, then returns a flattened row with a composite id like objectType:recordId. If required IDs are missing, it returns None.

**Call relations**: _paginate_custom_object_records calls this for each raw custom object returned by HubSpot.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1128–1158)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived records for one custom object type and emits tombstones for them. The IDs include the object type so they match the custom object row IDs.

**Data flow**: It receives an object type ID. It pages through HubSpot’s archived records for that custom object, builds delete IDs in objectType:recordId form, and yields StreamPage delete batches. Unsupported sweeps are ignored.

**Call relations**: _paginate_custom_objects calls this after reading live records for each custom object type. It uses the same error checks as the standard archived sweep.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1160–1179)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a deduplicated list of owner teams from owner records. HubSpot exposes teams nested under owners rather than as a simple standalone list here.

**Data flow**: It reads owners, inspects each owner’s teams list, stores each team by ID to avoid duplicates, and yields one page of unique teams.

**Call relations**: _paginate_product_api calls this for the owner_teams stream. It uses _paginate_get_collection to read owners.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_lists`  (lines 1181–1210)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot CRM lists through the list search API. Lists use offset-based paging rather than the more common after cursor.

**Data flow**: It posts search requests with a count and offset, converts listId to id, lifts additionalProperties into the top level, yields pages, and advances the offset until HubSpot says there are no more lists.

**Call relations**: _paginate_product_api calls this for the lists stream. List membership pagination also calls it so it can visit each list’s members.

*Call graph*: called by 2 (_paginate_list_memberships, _paginate_product_api).


##### `HubSpotConnector._paginate_site_search`  (lines 1212–1232)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads CMS search results for a chosen content type, such as knowledge articles. CMS means content management system, HubSpot’s website/content area.

**Data flow**: It receives a content type. It calls the site search endpoint with limit and offset, yields dictionary results, and advances until it reaches the reported total.

**Call relations**: _paginate_product_api calls this when reading knowledge_articles.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1234–1258)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads assets attached to HubSpot marketing campaigns. Since assets are nested under campaigns and split by asset type, it fans out across both.

**Data flow**: It first pages through campaigns. For each campaign with an ID, it loops over known campaign asset types and yields the asset pages returned for that campaign and type.

**Call relations**: _paginate_product_api calls this for campaign_assets. It uses _paginate_get_collection to read campaigns and _paginate_campaign_asset_type to read each nested asset list.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1260–1295)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one asset type for one campaign and adds enough context to make each asset record unique and understandable.

**Data flow**: It receives a campaign ID, campaign name, and asset type. It reads the campaign asset endpoint, builds rows with composite IDs, campaign fields, asset kind, and metrics, then yields pages. Permission or missing-endpoint errors for a type are skipped.

**Call relations**: _paginate_campaign_assets calls this repeatedly, once per campaign and asset type. It relies on _paginate_get_collection for normal paging.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1297–1303)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields HubSpot analytics views as one stream page. Analytics views are saved filters or reporting views used to slice analytics data.

**Data flow**: It asks _analytics_view_rows for normalized rows and yields them if any exist.

**Call relations**: _paginate_product_api calls this for analytics_views. The heavier analytics report reader also reuses _analytics_view_rows.

*Call graph*: calls 1 internal fn (_analytics_view_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_view_rows`  (lines 1305–1336)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches and normalizes analytics view definitions. It gives each view a stable id, name, filter block, and timestamps.

**Data flow**: It calls the analytics views endpoint, accepts either a raw list or a results list, skips malformed rows, chooses an ID and name, normalizes created time when needed, and returns a list of rows.

**Call relations**: It supports both the analytics_views stream and analytics_reports, where reports are queried once for all traffic and once per analytics view.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1338–1368)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs many HubSpot analytics report queries and turns their results into stream pages. It covers multiple report families, subjects, time periods, and analytics views.

**Data flow**: It calculates the report date window, fetches analytics views, builds a list of view filters including “all,” then loops through report categories and yields rows from each query.

**Call relations**: _paginate_product_api calls this for analytics_reports. It uses _analytics_report_window, _analytics_view_rows, and _paginate_analytics_report_query.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_report_window`  (lines 1371–1372)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range used for analytics reports. It starts from a fixed old date and ends at today in UTC.

**Data flow**: It takes no input. It returns a pair of strings in HubSpot’s YYYYMMDD format: the configured start date and the current date.

**Call relations**: _paginate_analytics_reports calls this before issuing report queries.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1374–1425)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Runs one specific analytics report query and pages through its breakdown rows. A breakdown is a grouped slice of report data, such as by source or geography.

**Data flow**: It receives the report family, subject, time period, optional view filter, date range, and client. It calls HubSpot with offset paging, converts each response to rows, yields them, and stops on unsupported report combinations or when all rows are read.

**Call relations**: _paginate_analytics_reports calls this many times. It passes each response to _analytics_report_rows for shaping.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1428–1503)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Turns one analytics report response into normal records. It creates both a totals row and separate breakdown rows when present.

**Data flow**: It receives raw report data plus context such as subject, time period, view, date range, and offset. It builds stable IDs, names, metric dictionaries, filter metadata, and readable dates, then returns a list of rows.

**Call relations**: _paginate_analytics_report_query calls this after every HubSpot report response. It uses the analytics report ID and date helpers to keep row IDs and date fields consistent.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1506–1510)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Builds a stable ID for an analytics report row from several identifying parts. It removes characters that would make the ID ambiguous.

**Data flow**: It receives any number of parts. It converts each part to text, replaces slashes and colons, joins them with colons, and prefixes the result with analytics_report.

**Call relations**: Analytics report row construction uses this so totals and breakdown rows can be upserted reliably across sync runs.


##### `HubSpotConnector._analytics_report_date`  (lines 1513–1514)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Formats HubSpot’s compact report date string into a normal date. For example, YYYYMMDD becomes YYYY-MM-DD.

**Data flow**: It receives an eight-character date string and returns the same date with hyphens inserted.

**Call relations**: Analytics report row construction uses this when setting start_date and end_date fields.


##### `HubSpotConnector._paginate_event_types`  (lines 1516–1535)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the catalog of HubSpot event types. Event types describe the kinds of behavioral or tracking events that may occur.

**Data flow**: It calls the event types endpoint, accepts either a list or results field, chooses an ID from several possible fields or the row index, and yields the rows if any exist.

**Call relations**: _paginate_product_api calls this for the event_types stream.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1537–1559)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual HubSpot event occurrences, optionally starting after a cursor. If HubSpot does not provide an ID for an event, it creates one.

**Data flow**: It receives an optional cursor. It sends occurredAfter when present, reads events, skips malformed rows, fills missing IDs with _synthetic_event_id, and yields one page. A missing endpoint is treated as empty.

**Call relations**: _paginate_product_api calls this for event_occurrences. It delegates ID creation to _synthetic_event_id when needed.

*Call graph*: calls 1 internal fn (_synthetic_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._synthetic_event_id`  (lines 1562–1572)

```
def _synthetic_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Creates a stable fallback ID for an event occurrence that lacks one. This prevents otherwise valid events from being unusable in an upsert-based sync.

**Data flow**: It receives an event row and its position in the page. It combines event type, object type, object ID, occurrence time or index, and a payload hash into a colon-separated ID.

**Call relations**: _paginate_event_occurrences calls this for events without HubSpot-supplied IDs. It relies on the stable payload hash helper for uniqueness.

*Call graph*: called by 1 (_paginate_event_occurrences).


##### `HubSpotConnector._paginate_email_events`  (lines 1574–1602)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot marketing email events, such as sends, opens, or clicks. It supports cursor-based starts by translating the cursor into HubSpot’s millisecond timestamp parameter.

**Data flow**: It builds request parameters with a large limit, optional start timestamp, and offset. It reads events pages, fills missing IDs with synthetic IDs, yields rows, and follows HubSpot’s offset until there are no more pages.

**Call relations**: _paginate_product_api calls this for email_events. It uses _email_event_start_timestamp and _synthetic_email_event_id.

*Call graph*: calls 2 internal fn (_email_event_start_timestamp, _synthetic_email_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1605–1614)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts a saved email-event cursor into the timestamp format HubSpot expects. HubSpot accepts milliseconds for startTimestamp.

**Data flow**: It receives a cursor string. If it is digits, it returns it as an integer. Otherwise it tries to parse it as an ISO date string and returns milliseconds since 1970. If parsing fails, it returns None.

**Call relations**: _paginate_email_events calls this before making email event requests.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._synthetic_email_event_id`  (lines 1617–1627)

```
def _synthetic_email_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Creates a stable fallback ID for an email event that does not include one. It combines timing, recipient, event type, campaign, and payload hash.

**Data flow**: It receives an email event row and page index. It builds a colon-separated text ID from available identifying fields, replacing embedded colons to avoid ambiguity.

**Call relations**: _paginate_email_events calls this whenever a HubSpot email event lacks an id.

*Call graph*: called by 1 (_paginate_email_events).


##### `HubSpotConnector._stable_payload_hash`  (lines 1630–1632)

```
def _stable_payload_hash(row: dict[str, Any]) -> str
```

**Purpose**: Creates a short, repeatable fingerprint of a record’s contents. This helps synthetic IDs stay stable without storing the whole payload in the ID.

**Data flow**: It receives a dictionary, serializes it as sorted JSON, hashes it with SHA-256, and returns the first 16 hex characters.

**Call relations**: Synthetic event ID helpers use this idea to reduce collisions when HubSpot does not provide reliable IDs.

*Call graph*: 2 external calls (sha256, dumps).


##### `HubSpotConnector._paginate_association_labels`  (lines 1634–1650)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the labels that describe relationship types between HubSpot object types. A label might explain that one company is a billing company or that one contact has a special role.

**Data flow**: It walks every object-type pair that has labels, converts each raw label into a normalized row, and yields pages of label rows.

**Call relations**: _paginate_product_api calls this for association_labels. It depends on _association_pairs_with_labels and _association_label_row.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_associations`  (lines 1652–1667)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual relationships between HubSpot records across object types. These are the links that say, for example, this deal is associated with this contact.

**Data flow**: It discovers object-type pairs with labels, pages through source object IDs, sends batches of IDs to HubSpot’s association read API, and yields normalized relationship rows.

**Call relations**: _paginate_product_api calls this for associations. It uses _association_pairs_with_labels, _paginate_crm_object_id_pages, and _paginate_association_batch.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_association_batch`  (lines 1669–1696)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads associations for a batch of source record IDs. It also follows per-record paging when one source record has many associated targets.

**Data flow**: It receives from/to object types and input IDs. It posts a batch read request, converts the response into rows, yields them, then builds another pending batch for any records with next cursors. Optional unsupported pairs return quietly.

**Call relations**: _paginate_associations calls this for each batch of source IDs. It uses _association_rows, _next_association_inputs, and _is_optional_pair_unavailable.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1699–1713)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Finds follow-up association batch inputs when HubSpot says a source record has more association results. This keeps large relationship lists from being cut off.

**Data flow**: It receives a batch association response. For each result with a source ID and next cursor, it creates an input containing that ID and after cursor. It returns the list of follow-up inputs.

**Call relations**: _paginate_association_batch calls this after each batch response to decide whether another batch request is needed.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1715–1728)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Discovers object-type pairs that actually have association labels. This avoids trying to read every possible relationship blindly.

**Data flow**: It gathers known object types, then checks each from/to pair for labels. When labels exist, it yields the pair and its labels.

**Call relations**: Both association label pagination and association row pagination call this. It uses _association_object_types and _association_labels_for_pair.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1730–1743)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the list of HubSpot object types to consider for associations. It includes standard object types and any account-specific custom object types.

**Data flow**: It starts with a fixed list of standard object types. It tries to fetch custom object schemas, extracts their object type IDs, appends new ones, and returns the combined list.

**Call relations**: _association_pairs_with_labels calls this before scanning object-type pairs. It uses custom schema helpers and optional-pair error handling.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1745–1761)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches relationship labels for one from-object and to-object pair. If HubSpot says the pair is not valid or unavailable, it returns no labels.

**Data flow**: It receives two object type names, calls the labels endpoint, filters valid dictionary rows, and returns them. Optional pair errors become an empty list.

**Call relations**: _association_pairs_with_labels calls this for each candidate pair.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1764–1781)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Normalizes one association label into a record with a stable ID and clear from/to fields.

**Data flow**: It receives a raw label plus from and to object types. It extracts type ID, category, and display label, then returns a row with a composite ID and normalized field names.

**Call relations**: _paginate_association_labels calls this for every label returned by association pair discovery.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1783–1795)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Reads only IDs for a CRM object type, page by page. This is useful when another API needs record IDs as input.

**Data flow**: It receives an object type. It asks _paginate_crm_object_pages for pages containing hs_object_id, extracts each record id as text, and yields non-empty ID pages.

**Call relations**: Association pagination uses this to build batch inputs. Sequence enrollment pagination uses it to visit contacts.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1797–1827)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads basic pages from HubSpot’s CRM object list endpoint. Unlike search, this is a simple list read with selected properties.

**Data flow**: It receives an object type and property names. It calls the list endpoint with limit, properties, and after cursor, yields dictionary records, and follows paging until complete. Optional unavailable objects stop quietly.

**Call relations**: _paginate_crm_object_id_pages and _paginate_contact_identity_pages call this as a reusable low-level CRM list reader.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1830–1864)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Converts a batch association API response into flat relationship records. It handles cases where one target has multiple association types.

**Data flow**: It receives raw response data and the from/to object type names. It loops through source records, target records, and association type entries, then returns a list of normalized association rows.

**Call relations**: _paginate_association_batch calls this after each batch read response.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1867–1894)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one normalized relationship row between two HubSpot records. The row has a stable ID and clear fields for both sides of the link.

**Data flow**: It receives association type details, object types, record IDs, and a fallback index. It extracts type, category, and label, then returns a dictionary describing the relationship.

**Call relations**: _association_rows uses this while expanding HubSpot’s nested association response into one row per relationship type.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1897–1900)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes errors that mean an optional HubSpot pair or endpoint is unavailable. This prevents optional cross-object scans from failing the whole sync.

**Data flow**: It receives an HTTP error. It returns true for 400 or 404 responses, or for permission-style stream-unavailable errors; otherwise false.

**Call relations**: Many fan-out helpers call this when reading optional associations, list memberships, consent rows, sequences, and CRM object pages.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1902–1916)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads which records belong to each HubSpot list. A list by itself is useful, but memberships reveal the actual contacts or objects included.

**Data flow**: It pages through lists, extracts each list ID, then yields membership pages for that list.

**Call relations**: _paginate_product_api calls this for list_memberships. It coordinates _paginate_lists and _paginate_memberships_for_list.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1918–1961)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the members of one HubSpot list and attaches list context to each membership row.

**Data flow**: It receives the list record and list ID. It pages through the list memberships endpoint, builds rows with composite IDs, list name, object type, and processing type, and yields pages. Optional errors are skipped.

**Call relations**: _paginate_list_memberships calls this once per list.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1963–1976)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot communication subscription definitions. These describe the kinds of email subscriptions or preferences a contact can have.

**Data flow**: It calls the communication preferences definitions endpoint, accepts either results or subscriptionDefinitions, assigns each row an ID, and yields the rows if any exist.

**Call relations**: _paginate_product_api calls this for subscription_definitions.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_consent_states`  (lines 1978–1991)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads email consent and unsubscribe status for contacts. This is privacy and communication-preference data, not just ordinary CRM profile data.

**Data flow**: It pages through contact identities, uses each contact email to fetch subscription statuses and unsubscribe-all statuses, combines those rows, and yields pages.

**Call relations**: _paginate_product_api calls this for consent_states. It uses _paginate_contact_identity_pages, _consent_status_rows, and _unsubscribe_all_rows.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1993–2009)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads contact records with their email addresses so consent endpoints can be queried by email.

**Data flow**: It reads contact pages requesting the email property, copies email from either the top level or properties bag, and yields pages of contact dictionaries with an email field.

**Call relations**: _paginate_consent_states calls this before asking HubSpot for consent status per email address.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 2011–2032)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Reads normal subscription consent statuses for one contact email. It answers whether the contact is opted in or out for specific subscription types.

**Data flow**: It receives a contact and email, URL-escapes the email, calls the statuses endpoint, turns each result into a normalized consent row, and returns the list. Optional unavailable responses return an empty list.

**Call relations**: _paginate_consent_states calls this for each contact email. It uses _consent_row to shape each result.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 2034–2058)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Reads the global unsubscribe-all status for one contact email. This is separate from individual subscription types.

**Data flow**: It receives a contact and email, URL-escapes the email, calls the unsubscribe-all endpoint, converts results into normalized consent rows, and returns them. Optional unavailable responses return an empty list.

**Call relations**: _paginate_consent_states calls this alongside _consent_status_rows for each contact email.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 2061–2091)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Turns one raw consent status into a consistent record. It adds contact identity, email, purpose, status, legal basis, source, and timestamp fields.

**Data flow**: It receives a raw status row, contact, email, and status kind. It builds a stable ID from email, subscription or status kind, and business unit, then returns an enriched row.

**Call relations**: _consent_status_rows and _unsubscribe_all_rows both call this so subscription statuses and global unsubscribe statuses share one output shape.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2093–2120)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sales sequences for each HubSpot user discovered through owners. A sequence is an automated sales outreach flow.

**Data flow**: It gets user rows from owners, then for each user calls the sequences endpoint with userId. It adds owner context to each sequence row and yields pages. Optional unavailable users or endpoints are skipped.

**Call relations**: _paginate_product_api calls this for sequences. It uses _sequence_user_rows, _paginate_get_collection, and optional error handling.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_user_rows`  (lines 2122–2145)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the list of HubSpot user IDs needed to query sequences. HubSpot owners can point to users, and sequences are queried by user ID.

**Data flow**: It reads owners, extracts unique userId values, attaches owner ID and email, and yields one page of user rows.

**Call relations**: _paginate_sequences calls this before reading sequences per user. It uses _paginate_get_collection to read owners.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2147–2165)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sequence enrollment data for contacts. This shows which contacts are enrolled in sales sequences.

**Data flow**: It pages through contact IDs, calls the enrollment endpoint for each contact, converts responses into rows, and yields pages. Missing or unavailable enrollment data for a contact is skipped.

**Call relations**: _paginate_product_api calls this for sequence_enrollments. It uses _paginate_crm_object_id_pages, _sequence_enrollment_rows, and optional error handling.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2168–2182)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes sequence enrollment responses for one contact. It handles both list-shaped and single-object responses.

**Data flow**: It receives raw response data and a contact ID. It chooses the results list if present, otherwise treats the response itself as one row, assigns each row an ID, adds contact_id, and returns the rows.

**Call relations**: _paginate_sequence_enrollments calls this after fetching enrollment data for a contact.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2184–2213)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads submissions for every HubSpot form. Since submissions live under each form, this first discovers forms and then reads their submission pages.

**Data flow**: It pages through forms, extracts each form ID, reads submissions for that form with a smaller limit, assigns each submission an ID, adds form ID and form name, and yields pages.

**Call relations**: _paginate_product_api calls this for form_submissions. It uses _paginate_get_collection for both form listing and submission listing.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2215–2230)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages inside HubSpot conversation threads. Threads are the parent records; messages are fetched from each thread’s nested endpoint.

**Data flow**: It pages through conversation threads, extracts each thread ID, reads messages for that thread, adds thread_id to each message, and yields pages.

**Call relations**: _paginate_product_api calls this for conversation_messages. It uses _paginate_get_collection for both thread and message pages.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipelines`  (lines 2232–2239)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads deal and ticket pipelines. Pipelines describe the stages records move through, such as sales deal stages or support ticket stages.

**Data flow**: It loops over supported pipeline object types, asks for normalized pipeline rows for each, and yields non-empty pages.

**Call relations**: _paginate_product_api calls this for pipelines. It delegates per-object work to _pipeline_rows_for_object_type.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2241–2291)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads stages inside deal and ticket pipelines and turns them into standalone records. This makes each stage searchable and linkable to its parent pipeline.

**Data flow**: It fetches raw pipelines for each supported object type, loops through their stages, extracts IDs and metadata, computes status and closed state, and yields stage rows with composite IDs.

**Call relations**: _paginate_product_api calls this for pipeline_stages. It uses _raw_pipelines_for_object_type to get the source pipeline data.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2293–2316)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes pipelines for one object type, such as deals or tickets. It adds an object-specific ID so pipelines from different object types do not collide.

**Data flow**: It receives an object type, fetches raw pipelines, skips rows without IDs, adds pipeline_id, object_kind, name, and active-or-archived status, then returns the rows.

**Call relations**: _paginate_pipelines calls this for each supported pipeline object type. It uses _raw_pipelines_for_object_type for the HTTP read.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2318–2329)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches raw pipeline records for one HubSpot object type. It treats forbidden or missing pipeline endpoints as empty because some accounts do not have every pipeline type.

**Data flow**: It receives an object type, calls the CRM pipelines endpoint, returns dictionary rows from results, or returns an empty list on 403 or 404.

**Call relations**: Pipeline and pipeline-stage normalizers call this as their low-level reader.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2332–2336)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects a specific HubSpot response meaning archived-record paging is not supported for an object type. This lets the connector skip only that cleanup pass.

**Data flow**: It receives an HTTP error. It checks for status 400 and looks for HubSpot’s “paging through deleted objects is not yet supported” message. It returns true only for that case.

**Call relations**: Standard and custom archived-ID paginators call this when their archived sweep request fails.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2339–2347)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Extracts HubSpot’s message field from an HTTP error response when possible. It is a small helper for interpreting upstream errors.

**Data flow**: It receives an HTTP error. It tries to parse the response as JSON, checks for a dictionary body, and returns the message as text or None.

**Call relations**: _is_archived_sweep_unsupported uses this to inspect HubSpot’s error text.


##### `HubSpotConnector._paginate_junction`  (lines 2349–2396)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple relationship streams by asking HubSpot to include associations while listing parent objects. Each output row is one parent-to-target pair.

**Data flow**: It receives a parent object type and target object type. It pages through the parent object list endpoint with associations requested, extracts target IDs from each parent’s association block, builds composite relationship IDs, and yields pages.

**Call relations**: _paginate_unchecked calls this for synthetic junction streams such as deal_contacts and task_companies. These streams full-refresh because HubSpot does not expose association update cursors.

*Call graph*: called by 1 (_paginate_unchecked).


### `extensions/sources/ufo_ext_sources/salesforce.py`

`io_transport` · `sync run`

Salesforce stores customer relationship data in named objects, often called SObjects, such as Account or Contact. This file is the read-only connector for those objects. Its job is to ask Salesforce what fields each object has, fetch records in time order, and pass them back in a standard shape that the rest of the project understands.

The file defines a list of Salesforce streams, one for each object the system knows how to sync. Each stream says the Salesforce object name, the record ID field, and the timestamp field used as a cursor. A cursor is like a bookmark: after one sync finishes, the next sync can start from the last known update time instead of rereading everything.

The main connector, SalesforceConnector, uses Salesforce's REST API. Before querying an object, it asks Salesforce to describe that object so it can learn the real field list for this specific Salesforce organization. That matters because Salesforce setups often differ from company to company. It then builds a SOQL query, which is Salesforce's SQL-like query language, and pages through results until Salesforce says there are no more.

On later syncs, it also asks Salesforce for records deleted since the previous cursor. Those are returned as tombstones, meaning “this ID used to exist, now remove it.” If Salesforce refuses access with a 401 or 403 response, the stream is skipped with a clear error instead of pretending the data is empty.

#### Function details

##### `_stream`  (lines 29–38)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates a StreamSpec, which is the small description the sync system needs for one Salesforce object. It records the stream's friendly name, the Salesforce object behind it, and the fields used to identify and order records.

**Data flow**: It takes a stream name, a Salesforce SObject name, and whether the stream is considered canonical. It fills in the standard Salesforce fields for ID, creation time, and update cursor, then returns a StreamSpec that other parts of the connector can use.

**Call relations**: This function is used while the file is loaded to build the SALESFORCE_STREAMS list. Each call produces one stream definition, which the SalesforceConnector later exposes as the set of Salesforce objects it can read.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 78–82)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: This function builds the Salesforce query text used to fetch records for one stream. It includes all known fields, optionally starts after a saved cursor, and orders records by update time so syncing can resume safely.

**Data flow**: It receives a stream description, a list of field names, and possibly a cursor timestamp. It joins the fields into a SELECT query, adds a WHERE clause if there is a cursor, adds an ORDER BY clause when the stream has a cursor field, limits the page size, and returns the finished SOQL query string.

**Call relations**: paginate calls this after _describe_fields has discovered the available fields. The returned query is then sent to Salesforce's query endpoint to begin reading records.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 84–90)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: This function asks Salesforce what fields exist on a particular object. That lets the connector sync whatever fields this Salesforce organization exposes instead of relying on a fixed, hand-written schema.

**Data flow**: It receives an HTTP client and a Salesforce object name. It sends a describe request to Salesforce, reads the returned field list, keeps only valid field names, converts them to strings, and returns the list.

**Call relations**: paginate calls this before building the query for a stream. Its output becomes the field list passed into _build_soql, so the later record fetch asks Salesforce for the full available object shape.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 92–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main reader for a Salesforce stream. It fetches updated records page by page, follows Salesforce's continuation links, and on incremental runs also emits delete tombstones for records that disappeared.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. First it asks Salesforce for the stream's fields, builds a SOQL query, and sends it to the query API. For each response, it yields any records found, then follows Salesforce's nextRecordsUrl until the response says the query is done. If there was a cursor, it then asks for deleted records since that cursor and yields a StreamPage containing those deleted IDs if needed. If Salesforce returns 401 or 403, it turns that refusal into a StreamSkipped error with an explanatory message; other HTTP errors are allowed to rise normally.

**Call relations**: The broader sync runner calls paginate when it wants data from a Salesforce stream. paginate coordinates the helper functions in this file: it uses _describe_fields to learn the schema, _build_soql to create the query, and _deleted_page to add deletion information after normal records have been read.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 121–139)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: This function checks Salesforce for records that were hard-deleted since the last cursor. It turns those missing records into a standard delete page so the rest of the system can remove or mark them correctly.

**Data flow**: It receives an HTTP client, a stream description, and the previous cursor timestamp. It sets the current time as the end of the deletion window, asks Salesforce's deleted-record endpoint for IDs deleted between the cursor and that end time, collects valid deleted IDs, chooses the next cursor from Salesforce's latest covered date or the end time, and returns a StreamPage containing the deleted IDs and next cursor. If there is nothing meaningful to report, it can return None.

**Call relations**: paginate calls this after reading normal updated records, but only when a previous cursor exists. The StreamPage it returns is yielded back to the sync runner, which can then treat those IDs as deletions rather than ordinary records.

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 141–144)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function removes Salesforce's metadata wrapper from each record. Salesforce includes an attributes field that describes the API object, but the sync output usually wants just the business fields, such as Id, Name, or SystemModstamp.

**Data flow**: It receives one Salesforce record and its stream description. If the record contains an attributes key, it returns a copy without that key. If there is no such key, it returns the record unchanged.

**Call relations**: This method is part of the connector interface inherited from RestConnector. After paginate yields records, the surrounding sync machinery can call flatten to clean each Salesforce record before storing or exposing it.


### Support and service desks
Connectors that ingest tickets, conversations, contacts, knowledge-base content, community content, and support activity from customer-service platforms.

### `extensions/sources/ufo_ext_sources/freshdesk.py`

`io_transport` · `during source sync, when Freshdesk streams are read`

Freshdesk exposes its data through a web API, but not every kind of data is laid out the same way. Some lists use ordinary “next page” links. Tickets use numbered pages and have a hard Freshdesk limit. Conversations belong under tickets. Knowledge-base and forum content sit in trees, like folders inside categories and articles inside folders. This file is the adapter that hides those differences from the rest of the system.

The main class, FreshdeskConnector, is a read-only connector. It builds an HTTP client with Freshdesk’s expected authentication, then offers a single pagination entry point. Given a stream name such as “tickets” or “solution_articles,” it chooses the correct walking pattern and yields batches of records.

A useful analogy is a librarian collecting documents from several filing cabinets. Some cabinets have a simple “next drawer” label. Some require checking every folder inside every category. Tickets need special care because Freshdesk will not let the reader go beyond 300 pages at once. If Freshdesk says the key is invalid or lacks permission, the connector marks that stream as skipped instead of pretending the sync succeeded. Without this file, the wider system would know that Freshdesk exists, but it would not know how to reliably collect Freshdesk records.

#### Function details

##### `_stream`  (lines 55–69)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a stream description for one Freshdesk data type. A stream description tells the sync system what the stream is called, what Freshdesk object it comes from, what field identifies a record, and whether it has a cursor for incremental reads.

**Data flow**: It receives a stream name and optional details such as the Freshdesk source object, primary key, cursor field, and whether it is a main supported stream. It fills in sensible defaults when details are missing, then returns a StreamSpec object that the connector can advertise to the rest of the system.

**Call relations**: This helper is used while the file is being loaded to build the Freshdesk stream list. It hands those stream definitions to FreshdeskConnector through the class-level streams_list.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector._make_client`  (lines 109–124)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Freshdesk. It sets timeouts, JSON headers, and the correct authentication method before any API requests are made.

**Data flow**: It receives a Freshdesk base URL and a resolved credential. It trims the URL, prepares headers and timeout settings, then either uses a provided proxy transport unchanged or turns the Freshdesk API key into HTTP Basic authentication, where the key is the username and "X" is the password. It returns an asynchronous HTTP client ready to make requests. If the credential has no usable authentication, it raises an error.

**Call relations**: The connector framework calls this when it needs a client for a Freshdesk sync. The client it returns is later passed into pagination methods that fetch tickets, contacts, articles, forum content, and other streams.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 127–137)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: Prepares the query settings for one page of Freshdesk tickets. It includes the page size, page number, sort order, extra ticket details to include, and optionally the incremental starting point.

**Data flow**: It receives an optional cursor, meaning the last known update time, and a page number. It builds a dictionary of request parameters. If a cursor is present, it adds Freshdesk’s updated_since filter. The result is passed directly to the ticket API request.

**Call relations**: FreshdeskConnector._paginate_tickets calls this before each ticket request. It keeps the ticket-specific request rules in one small place so the ticket pager can focus on fetching and stopping at the right time.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 139–213)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right fetching strategy for a requested Freshdesk stream. This is the main doorway the rest of the sync system uses to read pages of Freshdesk records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, then sends the work to the correct helper: ticket paging, conversation paging, two-level tree walking, three-level tree walking, or normal link-header paging. It yields lists of records as they arrive. If Freshdesk returns a refusal such as unauthorized or forbidden, it converts that into a clear skipped-stream signal.

**Call relations**: The connector framework calls this whenever it wants records for one Freshdesk stream. This function then delegates to FreshdeskConnector._paginate_tickets, FreshdeskConnector._paginate_conversations, FreshdeskConnector._paginate_link_header, FreshdeskConnector._paginate_two_level, or FreshdeskConnector._paginate_three_level depending on the stream shape.

*Call graph*: calls 6 internal fn (__init__, _paginate_conversations, _paginate_link_header, _paginate_three_level, _paginate_tickets, _paginate_two_level).


##### `FreshdeskConnector._paginate_link_header`  (lines 215–222)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a Freshdesk endpoint that uses standard “next page” web links. This is the common path for simple Freshdesk lists such as agents, companies, groups, and many admin objects.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST connector machinery to fetch pages of up to 100 records and follow the next-page link in the response headers. It yields each page of records back to its caller.

**Call relations**: FreshdeskConnector.paginate uses this directly for simple streams. The nested pagers also use it as their basic building block when they need to fetch parents, children, or leaves in a tree.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 224–241)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Freshdesk tickets using the special ticket pagination rules. Tickets are important enough to have their own path because Freshdesk uses numbered pages and limits how far a single request window can go.

**Data flow**: It starts at page 1 and repeatedly builds ticket request parameters from the cursor and current page number. It requests tickets, turns the response into a list of records, yields non-empty pages, and stops when the page is short, empty, or reaches Freshdesk’s 300-page ceiling. The cursor narrows the results to tickets updated since a known time.

**Call relations**: FreshdeskConnector.paginate calls this for the tickets stream. FreshdeskConnector._paginate_conversations also calls it first, because conversations are fetched by walking through the tickets that match the same cursor.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, paginate).


##### `FreshdeskConnector._paginate_conversations`  (lines 243–259)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches ticket conversations by first finding tickets, then reading the conversations under each ticket. This is needed because Freshdesk does not provide conversations as one flat top-level list in the same way as simple resources.

**Data flow**: It receives a client and optional cursor. It pages through tickets allowed by that cursor, takes each ticket id, fetches that ticket’s conversation pages, and makes sure every conversation record contains the ticket_id. It yields conversation pages as they are collected.

**Call relations**: FreshdeskConnector.paginate calls this when the requested stream is conversations. This function depends on FreshdeskConnector._paginate_tickets to find the ticket ids and FreshdeskConnector._paginate_link_header to follow conversation pages for each ticket.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (paginate).


##### `FreshdeskConnector._paginate_two_level`  (lines 261–272)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks Freshdesk data that has a parent-and-child shape. Examples include folders with canned responses, categories with forums, forums with topics, and topics with comments.

**Data flow**: It receives a parent API path and a child path pattern containing an id placeholder. It fetches pages of parent records, reads each parent id, formats the child path with that id, then fetches and yields the child pages. Parents without an id are skipped because there is no safe child URL to request.

**Call relations**: FreshdeskConnector.paginate calls this for several nested streams. This function uses FreshdeskConnector._paginate_link_header for both the parent list and each child list, so it reuses the normal next-page behavior while adding the parent-to-child walk.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


##### `FreshdeskConnector._paginate_three_level`  (lines 274–297)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks Freshdesk data arranged in three layers, such as solution categories, then folders, then articles. It exists for parts of Freshdesk where the records people want are buried two levels below the top.

**Data flow**: It receives a root path, a middle path pattern, and a leaf path pattern. It fetches root records, uses each root id to fetch middle records, uses each middle id to fetch leaf records, and yields the leaf pages. Any record without the needed id is skipped because it cannot be used to form the next request.

**Call relations**: FreshdeskConnector.paginate calls this for solution articles. It uses FreshdeskConnector._paginate_link_header at every level, turning ordinary page following into a deeper tree walk.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/intercom.py`

`io_transport` · `during Intercom source sync`

Intercom does not expose all of its data in one simple way. Some records are fetched with a search request, some use a scrolling cursor, some are returned as one plain list, and some are child records that must be fetched by first reading their parent. This file is the adapter that knows those rules.

At startup, it defines the Intercom streams the system can sync. A stream is a named kind of data, like contacts or tickets, with details such as its unique key and the field used to continue an incremental sync. The IntercomConnector then adds Intercom-specific behavior on top of the shared REST connector: it creates an HTTP client with the required Intercom API version header, chooses the right paging method for each stream, and converts permission failures into a clean skipped-stream result instead of crashing the whole run.

The connector also lightly reshapes some records. Intercom often nests useful values inside envelopes, like a conversation's source or a contact's first company. The flattening methods copy those values onto simple top-level fields so later database queries do not have to dig through nested JSON. One important detail is that Intercom timestamps are integers, but this sync framework tracks cursor values as strings, so timestamp cursors are converted to decimal strings after records are read.

#### Function details

##### `_stream`  (lines 52–66)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a StreamSpec, which is the small description the sync system uses to know what an Intercom data stream is called, what its source object is, what identifies each record, and whether it has a cursor for incremental syncing.

**Data flow**: It receives a stream name and optional details such as source object, primary key, cursor field, and whether it is canonical. It fills in sensible defaults, then returns a StreamSpec object that the connector later uses when deciding how to fetch and flatten that stream.

**Call relations**: This helper is used while the module builds the INTERCOM_STREAMS list. It hands each finished StreamSpec to the connector class through streams_list, so later sync code can ask the connector what Intercom streams are available.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector._make_client`  (lines 103–106)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Intercom and adds the Intercom API version header required for predictable responses.

**Data flow**: It receives the Intercom base URL and a credential. It asks the parent REST connector to create the authenticated HTTP client, adds the Intercom-Version header, and returns that prepared client.

**Call relations**: The shared connector setup calls this when it needs a client for an Intercom sync. After this function returns, all later paging methods use the same client to make GET and POST requests with the correct version header already attached.


##### `IntercomConnector._build_search_body`  (lines 109–139)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the JSON body for Intercom search endpoints, including page size, sorting, and the incremental cursor filter.

**Data flow**: It receives the stream being searched, the last saved cursor, and an optional Intercom page cursor called starting_after. It creates a request body that asks for up to 150 records, sorted oldest to newest by the cursor field, and filtered to records newer than the saved cursor. It returns that body ready to send in a POST request.

**Call relations**: The search paginator and the conversation-parts paginator call this before each Intercom search request. It is the shared recipe that keeps normal search streams and conversation child-fetching in sync with the same cursor behavior.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 142–147)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: Safely picks the first dictionary from a list-like Intercom field. It is used when a record contains a nested list and the connector only needs the first related object.

**Data flow**: It receives any value. If the value is a non-empty list and its first item is a dictionary, it returns that first dictionary; otherwise it returns nothing.

**Call relations**: The flattening helpers use this when pulling out the first contact from a conversation or the first company from a contact. It keeps those helpers from assuming Intercom always returns the exact nested shape expected.


##### `IntercomConnector._flatten_conversation`  (lines 150–167)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes important nested conversation fields easier to query by copying them onto top-level keys.

**Data flow**: It receives one conversation record. It copies the record, then, if present, pulls source type, subject, and body out of the nested source object and adds them as simple fields. It also looks for the first associated contact and adds that contact's id as requester_id. The result is the same conversation with extra convenient fields.

**Call relations**: IntercomConnector.flatten calls this only for the conversations stream. It prepares conversation records for later storage and SQL-style transforms that work better with flat field names.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 170–179)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes the author of a conversation part easier to use by copying the author's type and id onto top-level fields.

**Data flow**: It receives one conversation-part record. It copies the record, reads the nested author object if it exists, and adds author_type and author_id. It leaves conversation_id alone because that value was already stamped onto the part while fetching it.

**Call relations**: IntercomConnector.flatten calls this for the conversation_parts stream. The conversation-parts paginator first attaches the parent conversation id, then this function adds author details before the record moves on.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 182–190)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a contact's first associated company id as a simple org_id field.

**Data flow**: It receives one contact record. It copies the record, looks inside the nested companies envelope, chooses the first company if there is one, and stores that company's id or company_id as org_id. It returns the enriched contact record.

**Call relations**: IntercomConnector.flatten calls this for the contacts stream. It turns Intercom's nested company relationship into a simple field that later transforms can read directly.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 192–206)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Applies Intercom-specific cleanup to each record after it is fetched, and converts integer cursor values to strings so the sync watermark can advance.

**Data flow**: It receives a raw record and the stream it came from. Depending on the stream name, it may call a specialized flattening helper for conversations, conversation parts, or contacts. Then, if the stream has a cursor field and that value is an integer, it returns a copy with that cursor converted to a string; otherwise it returns the record as-is or with only the flattening changes.

**Call relations**: The broader sync system calls this after pages are read. It delegates the record-shaping details to _flatten_conversation, _flatten_conversation_part, and _flatten_contact, then hands back records in the shape expected by downstream storage and cursor tracking.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 208–252)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct way to fetch pages for each Intercom stream. It is the traffic director for Intercom's several API styles.

**Data flow**: It receives an HTTP client, a stream description, and the saved cursor for incremental sync. It checks the stream name and sends the work to the matching paginator: search, scroll, list, attributes, conversation parts, company segments, or activity logs. It yields each page of records as it arrives. If Intercom replies with a permission refusal, it turns that into a StreamSkipped signal so the run records the skip cleanly.

**Call relations**: The sync engine calls this when it wants records from a stream. This function then calls the specific pagination helper for that stream and passes each page back upward. It also protects the larger sync run from failing just because one Intercom scope is missing.

*Call graph*: calls 8 internal fn (__init__, _paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search).


##### `IntercomConnector._paginate_search`  (lines 254–274)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Intercom streams that use the search API, such as conversations, contacts, and tickets.

**Data flow**: It receives the HTTP client, stream, and saved cursor. For each loop, it builds a search request body, posts it to the correct search endpoint, extracts the records from the response, and yields them as a page. If Intercom provides a next-page cursor, it uses that cursor for the next request; otherwise it stops.

**Call relations**: IntercomConnector.paginate calls this for streams listed in the search-path table. It relies on _build_search_body to create each request, then hands pages of search results back to paginate and onward to the sync engine.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_scroll`  (lines 276–290)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads companies from Intercom's scroll API, which is like being given a bookmark for the next batch of companies.

**Data flow**: It starts without a scroll token, sends a GET request to the companies scroll endpoint, yields the returned company records, then repeats using the scroll_param returned by Intercom. It stops when there are no records or no next scroll token.

**Call relations**: IntercomConnector.paginate calls this for the companies stream. It hides the scroll-token loop so the rest of the sync only sees normal pages of company records.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_list`  (lines 292–304)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple Intercom streams that come back as one list, such as admins, tags, teams, and segments.

**Data flow**: It receives the client and stream, looks up the stream's endpoint, sends one GET request, and checks common response keys for a list of records. If it finds a non-empty list, it yields that list once and then finishes.

**Call relations**: IntercomConnector.paginate calls this for streams in the list-path table. It is the simplest pagination path because these endpoints do not require repeated page requests here.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_attributes`  (lines 306–315)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Intercom data attribute definitions for companies or contacts.

**Data flow**: It receives the client and stream, maps the stream name to the Intercom model name, and requests /data_attributes with that model as a parameter. It extracts the data list and yields it if any attribute records are present.

**Call relations**: IntercomConnector.paginate calls this for company_attributes and contact_attributes. It converts the two stream names into the model parameter Intercom expects.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_conversation_parts`  (lines 317–349)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the individual messages or events inside conversations. These are child records, so the connector first finds conversations and then fetches each conversation's details.

**Data flow**: It searches conversations using the saved cursor and normal search paging. For each conversation with an id, it requests that conversation's detail endpoint, extracts its conversation_parts list, stamps each part with the parent conversation_id, and yields the parts. It follows Intercom's search next-page cursor until there are no more conversations to inspect.

**Call relations**: IntercomConnector.paginate calls this for the conversation_parts stream. This function calls _build_search_body to page through parent conversations, then uses detail requests to fan out into the child parts that are not available as a standalone search stream.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_company_segments`  (lines 351–375)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the segments attached to each company. Like conversation parts, these are child records that require fetching parent companies first.

**Data flow**: It scrolls through companies using Intercom's company scroll endpoint. For each company with an id, it requests that company's segments endpoint, extracts the segment list, stamps each segment with company_id, and yields the segments. It keeps scrolling until Intercom stops returning companies or a next scroll token.

**Call relations**: IntercomConnector.paginate calls this for the company_segments stream. It combines the company scroll flow with per-company segment requests so downstream code receives ordinary pages of segment records.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_activity_logs`  (lines 377–399)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads admin activity logs, optionally starting after a saved created_at cursor.

**Data flow**: It builds request parameters with created_at_after when a cursor is available, then requests the activity log endpoint. It yields any activity log records returned. If Intercom provides a next-page URL or path, it follows it, stripping the base URL when needed, and continues until there is no next page.

**Call relations**: IntercomConnector.paginate calls this for the activity_logs stream. It turns Intercom's next-link style paging into the same page-by-page stream used by the rest of the connector.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/zendesk.py`

`io_transport` · `source sync`

Zendesk exposes many kinds of data through web API endpoints, and they do not all page through results in the same way. This file is the adapter that hides those differences. It lists the Zendesk streams the system can read, describes important fields like each record’s ID and update time, and chooses the right paging method for each stream.

The main class, ZendeskConnector, is read-only. It does not create or update anything in Zendesk. For large, frequently changing objects like tickets and users, it uses Zendesk’s incremental cursor export, which means “give me everything changed since this time, and tell me where to continue.” For simpler endpoints, it follows ordinary next-page links. Two streams need special treatment: ticket comments are buried inside ticket event records, so the connector pulls comment events out into their own rows; user identities require first listing users, then asking Zendesk for each user’s identities.

A small but important detail is sideloading. For tickets, Zendesk can return related users in the same response. This file uses that extra data to attach requester, submitter, and assignee email addresses directly to each ticket, like adding labels to boxes before passing them along. If Zendesk refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of treating the whole sync as a mysterious crash.

#### Function details

##### `_stream`  (lines 58–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a StreamSpec, which is the system’s small description card for one Zendesk data stream. It keeps the long stream list readable by filling in common defaults such as the primary key and timestamp fields.

**Data flow**: It receives a stream name and optional details, such as the Zendesk endpoint name or which field tracks updates. It combines those details with sensible defaults, then returns a StreamSpec object that the connector later uses to know what to request and how to interpret records.

**Call relations**: This function is used while the module is being loaded to build the ZENDESK_STREAMS list. It hands each completed stream description to StreamSpec, which is the shared source framework type used by the connector.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 142–167)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: This helper copies useful information from related records that Zendesk returned alongside the main records. In this file, it is used to add user email addresses onto ticket records when Zendesk includes user data in the same response.

**Data flow**: It receives the main records, the whole API page, and instructions describing which related records to look up. It builds a quick lookup table from the sideloaded arrays, matches each main record’s ID fields to those related records, and fills in target fields such as requester_email when an email is available. It changes the records in place and does not return a separate value.

**Call relations**: The incremental ticket paging flow calls this after fetching a page from Zendesk. It enriches the records before they are yielded to the rest of the sync pipeline, so later code receives tickets that already include the extra email fields.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 176–177)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This method decides which top-level JSON field contains the records for a stream. Most Zendesk responses use the stream name, but a few endpoints use different words, such as policies or audits.

**Data flow**: It receives a StreamSpec. It checks a table of exceptions first; if the stream is not in that table, it uses the stream’s own name. The result is a string key used to pull records out of Zendesk’s response body.

**Call relations**: The default paging method calls this before reading ordinary next-page API responses. It lets one generic paging loop work across many Zendesk endpoints even when their response field names differ.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 180–192)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: This method converts the system’s saved cursor into the Unix timestamp format Zendesk expects. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor that may be empty, already numeric, or an ISO-style date string. Empty or unparseable values become 0, numeric strings become integers, and date strings are parsed into seconds. The result is an integer start time for Zendesk incremental API calls.

**Call relations**: The incremental paging methods call this when they build their first request. It makes sure tickets, comments, users, organizations, metric events, and user identities all start from a Zendesk-compatible point in time.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 195–204)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: This method turns Zendesk’s full next-page URL into just the path and query string that this connector needs for the next request. It is a small safety step that keeps pagination working with the connector’s configured base URL.

**Data flow**: It receives a next-page URL, or nothing. If there is no usable path, it returns nothing. Otherwise it extracts the URL path and keeps the query string if one exists, returning a relative path such as /api/v2/users.json?page=2.

**Call relations**: Every paging loop uses this after reading Zendesk’s after_url or next_page value. It provides the next request path, or tells the loop to stop by returning no path.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 206–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main dispatcher for reading one Zendesk stream. It chooses the right paging strategy for the stream and turns permission refusals into a clear “skip this stream” signal.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing where the last sync left off. It selects a specialized paginator for ticket comments, user identities, incremental cursor streams, or regular streams. As each selected paginator produces lists of records, this method yields them onward. If Zendesk responds with 401 or 403, it raises StreamSkipped with a readable explanation.

**Call relations**: The source framework calls this when it wants records for a particular Zendesk stream. This method then hands control to one of the private paging methods and passes their output back to the framework.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 232–253)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads high-volume Zendesk streams using Zendesk’s incremental cursor export. It is built for data that changes often, so a sync can resume from a saved time instead of starting over.

**Data flow**: It receives an HTTP client, a stream description, and a cursor. It converts the cursor to a Unix timestamp, builds the incremental export URL, then repeatedly fetches pages. It pulls records from the expected response field, optionally enriches tickets with sideloaded user emails, yields non-empty record batches, and follows Zendesk’s next cursor link until Zendesk says the stream has ended.

**Call relations**: ZendeskConnector.paginate calls this for streams such as tickets, users, organizations, and ticket metric events. Inside the loop it relies on _cursor_to_unix to start in the right place, _apply_sideload to enrich tickets, and _next_page_path to move to the next page.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 255–265)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads ordinary Zendesk endpoints that use simple next-page pagination. It is the fallback for streams that do not need incremental cursor logic or special reshaping.

**Data flow**: It receives an HTTP client and a stream description. It builds the first API path from the stream’s source object, asks _data_field which response field contains records, fetches each page, yields any records found, and follows the next_page link until there is no next page.

**Call relations**: ZendeskConnector.paginate calls this for the many standard Zendesk streams. It uses _data_field to understand each response shape and _next_page_path to continue through pages.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 267–299)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method creates a clean ticket_comments stream even though Zendesk stores comments inside ticket event data. It extracts only comment child events and adds the ticket ID so each comment can stand on its own.

**Data flow**: It receives an HTTP client and a cursor. It converts the cursor to a Unix timestamp, requests incremental ticket events with comment events included, then scans each event’s child_events. When it finds a child event whose type is Comment, it copies it, adds the parent ticket_id, normalizes numeric created_at timestamps into readable UTC date strings, collects comments into a batch, and yields that batch. It keeps following cursor links until the event stream ends.

**Call relations**: ZendeskConnector.paginate calls this only for the ticket_comments stream. It uses _cursor_to_unix to start from the saved point and _next_page_path to continue through Zendesk’s event pages.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate); 1 external calls (fromtimestamp).


##### `ZendeskConnector._paginate_user_identities`  (lines 301–326)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads user identity records, such as email or login identities, which Zendesk exposes under each individual user rather than as one global list. It first finds changed users, then asks for identities user by user.

**Data flow**: It receives an HTTP client and a cursor. It converts the cursor to a Unix timestamp and pages through incremental users. For each valid user with an ID, it requests that user’s identities, yields any identity records found, and follows identity next-page links if needed. After all users on a page are processed, it follows the users cursor until Zendesk says there are no more changed users.

**Call relations**: ZendeskConnector.paginate calls this for the users_identities stream. It depends on _cursor_to_unix for the initial user export request and _next_page_path for both user pagination and per-user identity pagination.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).
