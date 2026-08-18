# CRM, sales, and customer support source connectors  `stage-12.1.6`

This stage is part of the system’s data intake work. It connects to outside customer and support tools, reads their records through each service’s API, and reshapes them into a common form the rest of the product can store, search, and analyze. An API is a doorway a service provides so software can ask for data in an organized way.

Each file is like an adapter for a different machine. The Attio, HubSpot, and Salesforce connectors focus on customer relationship and sales data, such as companies, people, accounts, contacts, deals, tasks, notes, and deletion markers. HubSpot also covers marketing, analytics, associations between records, and product-specific data. The Freshdesk, Intercom, and Zendesk connectors focus on support data, including tickets, conversations, users, companies, agents or admins, help articles, forums, tags, and related setup records. Together, they hide the differences between these services’ paging and record formats, producing steady streams of clean records for the shared sync pipeline.

## Files in this stage

### CRM and sales connectors
Connectors for CRM and sales platforms that normalize companies, contacts, deals, accounts, associations, and deletion markers.

### `extensions/sources/ufo_ext_sources/attio.py`

`io_transport` · `source sync`

Attio’s API does not return every kind of data in the same shape. Companies, people, and deals come from object-specific record queries. Tasks and notes use different workspace-wide endpoints. Meetings and call recordings use cursor-based paging, where the server gives a token for the next page. This file hides those differences behind one connector called AttioConnector.

The connector’s job has two halves. First, it fetches all rows for each stream. A stream is one type of synced data, such as “people” or “meetings.” Because Attio does not provide one reliable “changed since” field for every stream, these streams are treated as full snapshots: each run reads everything and later removes records that disappeared upstream.

Second, it reshapes Attio’s nested data into simple top-level fields. Attio stores record IDs inside an id object and attributes inside arrays of “value cells.” Without flattening, a company or person may not even have a clear top-level primary key. The flattening code pulls IDs up, picks useful values such as email addresses or domains, and builds readable call transcript text from transcript segments.

The file also knows when not to fail the whole sync. If an Attio workspace has disabled a standard object, or if the OAuth permission grant is missing a scope, it skips that stream with a clear reason instead of crashing everything.

#### Function details

##### `_records_stream`  (lines 35–43)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates the standard description for an Attio object stream, such as companies, people, or deals. This tells the sync system what the stream is called, which Attio object it reads, and which field uniquely identifies each record.

**Data flow**: It receives a stream name, an Attio object slug, and whether the stream is canonical. It builds a StreamSpec with record_id as the primary key, no cursor field, and delete_missing turned on. The result is a stream definition used later by the connector.

**Call relations**: At file load time, this helper is used to build the list of Attio streams. It hands its information to StreamSpec so the broader source framework knows how to treat each object stream.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 70–71)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: Safely pulls a named ID out of a nested dictionary. It is a small guardrail for Attio fields that sometimes contain an id object and sometimes do not.

**Data flow**: It receives any value and a key name. If the value is a dictionary, it returns the dictionary entry for that key; otherwise it returns None. Nothing outside the return value is changed.

**Call relations**: AttioConnector._value_primitive calls this when it needs a fallback ID from nested option or status data. It keeps that larger value-conversion function from repeating the same safety check.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 80–81)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: Builds the JSON body used when asking Attio for a page of standard object records. It sets the page size and where the page should start.

**Data flow**: It receives an offset number. It returns a dictionary containing the fixed record limit and that offset. The returned body is sent to Attio in a POST request.

**Call relations**: AttioConnector.paginate calls this while walking through companies, people, deals, and other object-record streams. Each call prepares the next request body for Attio’s offset-based paging.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._value_primitive`  (lines 84–127)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: Turns one Attio value cell into the simplest useful value, such as text, a number, an email address, a domain, a selected option title, or a referenced record ID. This is the heart of making Attio’s nested attribute format readable.

**Data flow**: It receives one dictionary representing an Attio value cell. It checks the known places Attio may store the real value, including value, option, status, email_address, phone_number, domain, currency_value, target_record_id, location parts, and actor references. It returns one plain value, or None if it cannot find one.

**Call relations**: This function is used by the flattening helpers that process cells and lists of cells. When option or status IDs are nested inside another object, it calls _nested_id to safely pull them out.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 130–145)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: Converts one Attio attribute cell into a simple value for a normal output record. It decides whether to return the first meaningful value, a list of selected options, or None.

**Data flow**: It receives a cell that may be a list, a dictionary, or an already-simple value. For lists, it converts each item with AttioConnector._value_primitive and removes empty results. For dictionary cells, it converts the one cell. It returns a simplified value.

**Call relations**: This is part of the flattening pipeline used when AttioConnector._flatten_values reshapes an object record. It relies on the same primitive conversion rules as other flattening helpers.


##### `AttioConnector._flatten_list_cell`  (lines 148–155)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: Converts an Attio attribute into a list of simple values. It is used for fields where keeping all values matters, such as multiple email addresses, phone numbers, domains, or categories.

**Data flow**: It receives either a list-like Attio cell or a single cell. If the input is not a list, it flattens it and wraps the result in a one-item list, unless the value is empty. If the input is a list, it converts each item and removes empty values. It returns a list.

**Call relations**: AttioConnector._flatten_values calls this for known multi-value fields. It works beside AttioConnector._flatten_cell, which is used for fields that should usually collapse to one value.


##### `AttioConnector._flatten_values`  (lines 158–192)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Walks through all attributes on an Attio object record and turns them into normal top-level fields. It also creates convenient shortcut fields such as email, phone, domain, category, first_name, and last_name.

**Data flow**: It receives the record’s values dictionary from Attio. For each attribute slug, it chooses either list-style flattening or single-value flattening. It then adds special name handling and copies the first domain, category, email address, and phone number into simpler singular fields. It returns a new flat dictionary of attributes.

**Call relations**: AttioConnector._flatten_record uses this to turn the attributes of companies, people, deals, and other object records into the shape expected by the rest of the system.


##### `AttioConnector._flatten_record`  (lines 195–209)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Flattens a standard Attio object record, such as a company, person, or deal. It pulls identity and timestamps to the top level, then adds simplified attributes.

**Data flow**: It receives a raw Attio record and the stream definition. It reads the nested id object, created and updated timestamps, and any cursor field if one exists. It then flattens the record’s values section and merges those fields in. It returns one clean dictionary with a top-level record_id.

**Call relations**: AttioConnector.flatten calls this for streams that are not tasks, notes, meetings, or call recordings. It is the main path for Attio object-record data.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 212–216)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level task_id to an Attio task record. This makes each task easy for the sync system to identify.

**Data flow**: It receives a raw task record. It copies the record, reads task_id from the nested id object when present, and writes that value to the top level. It returns the copied and updated task record.

**Call relations**: AttioConnector.flatten calls this when processing the tasks stream. It is a smaller flattening path because tasks already arrive in a simpler shape than object records.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 219–223)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level note_id to an Attio note record. This gives the sync system a stable key for notes.

**Data flow**: It receives a raw note record. It copies the record, reads note_id from the nested id object when possible, and places it at the top level. It returns the updated copy.

**Call relations**: AttioConnector.flatten calls this for the notes stream. It keeps note-specific ID cleanup separate from the more complex object-record flattening.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 226–230)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level meeting_id to an Attio meeting record. This makes meetings identifiable in the same simple way as other synced rows.

**Data flow**: It receives a raw meeting record. It copies the record, extracts meeting_id from the nested id object if available, and stores it as meeting_id. It returns the updated meeting dictionary.

**Call relations**: AttioConnector.flatten calls this for the meetings stream. Meeting records are also used indirectly by call recording pagination to discover each meeting’s recordings.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 233–254)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Cleans up an Attio call recording record. It exposes the recording ID, fills in a usable recording URL when possible, and turns transcript segments into readable transcript text.

**Data flow**: It receives a raw call recording record. It copies the record, extracts call_recording_id from the nested id object, uses web_url as recording_url if needed, and reads transcript segments. For each segment, it combines the speaker name and speech into lines of text. It returns the enriched recording record.

**Call relations**: AttioConnector.flatten calls this for the call_recordings stream. It often receives records that were already enriched by AttioConnector._paginate_call_recordings with meeting context and fetched transcript data.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 256–265)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening routine for each Attio stream. It is the public cleanup step that converts raw API records into records the rest of the source framework can store.

**Data flow**: It receives a raw record and its stream definition. It checks the stream name and sends the record to the matching flattening helper for tasks, notes, meetings, call recordings, or standard object records. It returns the flattened dictionary.

**Call relations**: The source framework calls this after records are fetched. This function then delegates to AttioConnector._flatten_task, AttioConnector._flatten_note, AttioConnector._flatten_meeting, AttioConnector._flatten_call_recording, or AttioConnector._flatten_record depending on the stream.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 267–321)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches pages of records from Attio for whichever stream is being synced. It hides the fact that different Attio resources use different endpoints and paging styles.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor that this connector does not use for incremental sync. It chooses the correct Attio endpoint and paging method, yields lists of raw records page by page, and may raise StreamSkipped when a stream cannot be read because of a disabled object or missing OAuth permission.

**Call relations**: The source framework calls this to read data. It calls AttioConnector._paginate_simple for tasks and notes, AttioConnector._paginate_cursor for meetings, AttioConnector._paginate_call_recordings for call recordings, and AttioConnector._build_query_body for standard object-record queries. It uses AttioConnector._is_object_disabled, AttioConnector._is_scope_unauthorized, and AttioConnector._scope_skip_reason to turn expected Attio errors into clear skipped-stream outcomes.

*Call graph*: calls 8 internal fn (__init__, _build_query_body, _is_object_disabled, _is_scope_unauthorized, _paginate_call_recordings, _paginate_cursor, _paginate_simple, _scope_skip_reason).


##### `AttioConnector._paginate_simple`  (lines 323–330)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use simple offset paging, specifically tasks and notes. Offset paging is like saying “give me 500 rows starting at row 1000.”

**Data flow**: It receives an HTTP client, an endpoint path, and a page size. It asks the shared REST helper to fetch pages from the data field using limit and offset parameters. It yields each page of records as it arrives.

**Call relations**: AttioConnector.paginate calls this for the tasks and notes streams. It delegates the low-level repeated GET requests to the base REST connector’s offset-page helper.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._paginate_cursor`  (lines 332–350)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use cursor paging, such as meetings and call recordings. A cursor is a token from the server that means “continue from here.”

**Data flow**: It receives an HTTP client, an endpoint path, a page size, and optional query parameters. It repeatedly asks the shared REST helper for pages from the data field and follows pagination.next_cursor for the next page. It yields each page of records.

**Call relations**: AttioConnector.paginate calls this for meetings. AttioConnector._paginate_call_recordings also calls it first to list meetings and then to list recordings for each meeting.

*Call graph*: called by 2 (_paginate_call_recordings, paginate).


##### `AttioConnector._paginate_call_recordings`  (lines 352–388)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the call recordings stream by walking through meetings first, then fetching each meeting’s recordings and transcript data. This is needed because Attio exposes recordings underneath individual meetings.

**Data flow**: It receives an HTTP client. It pages through meetings, extracts each meeting ID, title, start and end time, and computes a duration when possible. For every meeting, it pages through that meeting’s call recordings, adds parent meeting context to each recording, fetches the transcript for each recording when available, and yields pages of enriched recording records.

**Call relations**: AttioConnector.paginate calls this for the call_recordings stream. Inside, it calls AttioConnector._paginate_cursor to list meetings and recordings, AttioConnector._meeting_id and AttioConnector._call_recording_id to read IDs, AttioConnector._datetime_of and AttioConnector._duration_seconds to prepare meeting timing fields, and AttioConnector._fetch_transcript to add transcript content.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (paginate).


##### `AttioConnector._fetch_transcript`  (lines 390–402)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: Fetches the transcript for one call recording. If the transcript is not ready or not found, it quietly returns None instead of failing the entire recording sync.

**Data flow**: It receives an HTTP client, a meeting ID, and a recording ID. It builds the transcript endpoint path and performs a GET request. If Attio returns 404 or 409, it returns None; otherwise it returns the data object from the response when that object is a dictionary.

**Call relations**: AttioConnector._paginate_call_recordings calls this for each recording that has an ID. The returned transcript data is attached to the recording before the page is yielded.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 405–409)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: Extracts the usable meeting ID from an Attio meeting object. It handles both nested ID dictionaries and plain string IDs.

**Data flow**: It receives a meeting dictionary. It looks at the id field; if id is a dictionary, it returns id.meeting_id, and if id is already a string, it returns that string. If no usable ID exists, it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this while looping through meetings. Without a meeting ID, that meeting is skipped because the connector cannot ask Attio for its recordings.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 412–416)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: Extracts the usable call recording ID from an Attio recording object. It supports both nested and plain string ID shapes.

**Data flow**: It receives a recording dictionary. It checks the id field, returns id.call_recording_id when id is a dictionary, returns id itself when it is a string, or returns None when no valid ID is present.

**Call relations**: AttioConnector._paginate_call_recordings calls this before trying to fetch a transcript. A transcript request can only be made when this function finds a recording ID.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 419–423)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: Pulls a usable date or date-time string out of Attio’s meeting time shape. It supports both timed meetings and all-day meetings.

**Data flow**: It receives a value that may be a dictionary like {datetime, timezone} or {date}. If it is a dictionary, it returns datetime first, then date. If the shape is not recognized, it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this for meeting start and end fields. The extracted times are copied onto recordings and used to estimate duration.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 426–436)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: Computes an approximate meeting duration in seconds from start and end timestamps. It returns None when the times are missing or cannot be parsed.

**Data flow**: It receives start and end strings. It converts ISO 8601 date-time text into datetime objects, treating a trailing Z as UTC time. If parsing works, it subtracts start from end and returns the non-negative number of seconds; otherwise it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this after extracting meeting start and end values. The resulting duration is added to each recording from that meeting when available.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 439–448)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects the specific Attio error meaning a standard object is disabled in the workspace. This lets the connector skip that stream instead of treating it as an unexpected failure.

**Data flow**: It receives an HTTP error. It first checks for status code 400, then tries to read the response body as JSON. It returns true only when the body is a dictionary with code set to standard_object_disabled.

**Call relations**: AttioConnector.paginate calls this when a standard object-record query fails. If it returns true, paginate raises StreamSkipped with a clear disabled-object message.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._is_scope_unauthorized`  (lines 451–460)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects the Attio error meaning the OAuth grant is missing a required permission scope. OAuth is the permission system where a user or workspace grants this connector limited access.

**Data flow**: It receives an HTTP error. It checks for status code 403, then tries to parse the response JSON. It returns true only when the response code is unauthorized.

**Call relations**: AttioConnector.paginate calls this for meetings and call recordings when Attio rejects a request. If the error matches, paginate raises StreamSkipped rather than failing the whole sync.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._scope_skip_reason`  (lines 463–469)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a human-readable explanation for skipping a stream because of missing OAuth permission. It includes Attio’s own message when available.

**Data flow**: It receives an HTTP error. It tries to parse the JSON response and read the message field. It returns a sentence explaining that the OAuth grant is missing a required scope, with either Attio’s message or a fallback note.

**Call relations**: AttioConnector.paginate calls this after AttioConnector._is_scope_unauthorized confirms the permission problem. The returned text becomes the reason attached to StreamSkipped.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/hubspot.py`

`io_transport` · `during HubSpot source sync`

HubSpot stores business data in many different shapes. A contact or company comes from one CRM search API, while owners, lists, workflows, forms, email events, analytics reports, and conversations each have their own endpoints and paging rules. This file is the adapter that makes all of those surfaces look like one set of named streams the rest of the system can sync.

The file first declares stream definitions: what each stream is called, which HubSpot object or API it reads, which field uniquely identifies a row, and which timestamp can be used as a cursor for incremental syncs. Then HubSpotConnector chooses the right reading method for each stream. For normal CRM objects, it asks HubSpot which properties exist, searches in timestamp order, avoids re-emitting duplicate records at the cursor boundary, and then performs a separate sweep for archived IDs so deletions become tombstones. For product APIs, it uses specialized walkers when HubSpot does not offer one standard list shape.

A key theme is normalization. HubSpot often wraps useful fields inside properties or values. This connector flattens those nested shapes so downstream code can read fields in a simple, top-level way. If HubSpot says a stream is unavailable because the account lacks a product tier or permission, the connector marks that stream as skipped instead of failing the whole sync.

#### Function details

##### `_normalize_epoch_millis`  (lines 248–255)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Converts HubSpot timestamps written as milliseconds since 1970 into readable ISO date strings. It leaves booleans and already-normal values alone so accidental conversions do not corrupt data.

**Data flow**: It receives any value. If the value is a number, or a string made only of digits, it treats it as milliseconds since the Unix epoch and returns a UTC timestamp string; otherwise it returns the original value unchanged.

**Call relations**: This is used when HubSpot returns dates in a raw numeric style, especially for knowledge article dates, email event creation times, and analytics view creation times.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 258–267)

```
def _stream(name: str, *, object_type: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Builds a standard HubSpot CRM stream definition. It is used for object types like contacts, companies, deals, tasks, and many other CRM objects that follow the same search pattern.

**Data flow**: It receives a stream name, the HubSpot object type, and whether the stream is canonical. It returns a StreamSpec that tells the sync system which ID and timestamp fields to use.

**Call relations**: This helper is used at module setup time to declare many of the CRM object streams that HubSpotConnector exposes.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 270–289)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='createdAt', updated_at_field: str | None='updatedAt', pagi
```

**Purpose**: Builds a stream definition for HubSpot data that comes from product-specific APIs rather than the main CRM search API. These streams often have different ID fields, timestamp fields, or pagination rules.

**Data flow**: It receives naming, key, timestamp, and optional pagination details. It returns a non-canonical StreamSpec that the connector later uses to choose the correct product API reader.

**Call relations**: This helper is used when the file declares streams such as owners, workflows, forms, analytics reports, and conversation messages.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 292–304)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Describes HubSpot’s common list-page format for GET endpoints. It lets generic pagination code follow HubSpot’s next cursor without each stream rewriting the same rule.

**Data flow**: It receives an API path. It returns a Pagination object that says records live in results, the next cursor lives in paging.next.after, and the next request should send that cursor as after.

**Call relations**: Product API stream definitions use this helper when their endpoint has HubSpot’s standard results-plus-next-cursor shape.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 307–317)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Builds a stream definition for relationship rows, such as deal-to-contact or ticket-to-company links. These are synthetic streams because HubSpot exposes them as associations inside another object’s response.

**Data flow**: It receives the stream name and the parent object type. It returns a StreamSpec with no cursor, because HubSpot does not provide modification timestamps for these association rows.

**Call relations**: The module uses this at setup time to define junction streams, and HubSpotConnector later routes those streams to its junction pagination method.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 622–652)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Creates the request body for HubSpot’s CRM search endpoint. It tells HubSpot which fields to return, how many rows to return, how to sort them, and where to continue from.

**Data flow**: It receives a stream definition, the property names to request, an optional sync cursor, and an optional page cursor. It returns a JSON-ready dictionary for a POST search request.

**Call relations**: Normal CRM pagination and custom object pagination both call this before sending a search request to HubSpot.

*Call graph*: called by 2 (_paginate_custom_object_records, _paginate_unchecked).


##### `HubSpotConnector._flatten`  (lines 655–666)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a standard CRM object response into a simpler flat row. HubSpot puts most user-facing fields inside a properties bag, and this function lifts them up.

**Data flow**: It receives one HubSpot CRM record. It copies id, creation time, update time, archived status, and every property into one top-level dictionary.

**Call relations**: The public flatten method calls this for regular CRM streams after records have been fetched.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 669–692)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes rows from HubSpot product APIs, which do not all use the same shape. It makes those rows look more like the flat CRM rows used elsewhere.

**Data flow**: It receives one product API record and its stream definition. It copies top-level fields, promotes objectId to id when needed, lifts nested properties and values entries, and fixes a few timestamp formats.

**Call relations**: The public flatten method calls this for product API streams, and it uses _normalize_epoch_millis for fields HubSpot sends as epoch milliseconds.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 694–701)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses how to clean up a fetched HubSpot record before the rest of the sync system sees it. It keeps special synthetic rows as-is and flattens HubSpot-shaped records when needed.

**Data flow**: It receives a raw record and the stream it came from. It returns either the original record, a flat CRM row, or a normalized product API row.

**Call relations**: The sync framework calls this after pagination yields records, and this method delegates to the right flattening helper based on stream name.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector._list_properties`  (lines 703–711)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which fields exist for a CRM object type. This matters because HubSpot search only returns fields that are explicitly requested.

**Data flow**: It receives an HTTP client and an object type like contacts or deals. It fetches the object’s property definitions and returns just the property names.

**Call relations**: _paginate_unchecked calls this before searching a normal CRM object stream so the connector can request every available field.

*Call graph*: called by 1 (_paginate_unchecked).


##### `HubSpotConnector.paginate`  (lines 713–726)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the safe outer pagination entry for a HubSpot stream. It reads pages, but turns certain permission or availability errors into a skipped stream instead of a failed run.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. It yields pages from _paginate_unchecked, or raises StreamSkipped when HubSpot says this account cannot read that stream.

**Call relations**: The source runner calls this to read a stream. It delegates real reading to _paginate_unchecked and uses _is_stream_unavailable plus _stream_skip_reason to explain skips.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 728–778)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Routes each stream to the correct HubSpot reading strategy. It is the connector’s main traffic director.

**Data flow**: It receives a client, stream, and optional cursor. Depending on the stream, it yields pages from a generic pagination strategy, junction reader, custom object reader, product API reader, or CRM search loop with archived-ID cleanup.

**Call relations**: paginate calls this after wrapping it in error handling. This function hands off to many specialized pagination helpers when a stream needs custom behavior.

*Call graph*: calls 6 internal fn (_build_search_body, _list_properties, _paginate_archived_ids, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._is_stream_unavailable`  (lines 781–804)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether a HubSpot error means a stream is not available for this account rather than truly broken. This keeps missing permissions or product-tier limits from stopping the whole sync.

**Data flow**: It receives an HTTP error. It checks for a 403 response and scans the response message for permission-related wording, returning true or false.

**Call relations**: paginate uses this to skip streams, and archived sweep helpers use it to quietly stop optional cleanup work when HubSpot blocks it.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 807–816)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a human-readable explanation for why a HubSpot stream was skipped. It includes HubSpot’s own message when available.

**Data flow**: It receives a stream name and an HTTP error. It reads the response body if possible and returns a sentence describing the unavailable stream.

**Call relations**: paginate calls this right before raising StreamSkipped so the run report records a useful reason.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 818–853)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds CRM records that HubSpot has archived or deleted so the local copy can mark them as gone. The search API does not include these records, so this separate sweep is needed.

**Data flow**: It receives a client and stream. It pages through the object list endpoint with archived=true and yields StreamPage objects containing delete IDs.

**Call relations**: _paginate_unchecked calls this after normal CRM search pages finish. It uses availability checks to stop quietly when HubSpot does not support this sweep.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 855–949)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Selects the correct reader for product API streams. HubSpot’s non-CRM APIs differ widely, so one generic method is not enough.

**Data flow**: It receives a client, stream, and optional cursor. It branches by stream name, yields pages from the matching specialized helper, or uses a generic collection reader when the endpoint is simple.

**Call relations**: _paginate_unchecked calls this for product API streams, and this function fans out to helpers for lists, forms, analytics, events, associations, sequences, pipelines, and more.

*Call graph*: calls 21 internal fn (_paginate_analytics_reports, _paginate_analytics_views, _paginate_association_labels, _paginate_associations, _paginate_campaign_assets, _paginate_consent_states, _paginate_conversation_messages, _paginate_email_events, _paginate_event_occurrences, _paginate_event_types (+11 more)); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._paginate_get_collection`  (lines 951–979)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a HubSpot collection endpoint that uses the common results plus paging.next.after format. It is the reusable walker for many simple list APIs.

**Data flow**: It receives a client, path, optional page size, and optional extra query parameters. It repeatedly sends GET requests, yields result lists, and follows the after cursor until there is no next page.

**Call relations**: Many product-specific helpers call this when their underlying endpoint follows HubSpot’s standard list pattern.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_product_api, _paginate_sequences, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 981–1016)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Reads all custom object records defined in a HubSpot account. Custom objects are account-specific, so the connector must first discover their schemas before it can read records.

**Data flow**: It fetches custom object schemas, extracts each object type and properties, then yields record pages and archived-ID tombstones for each discovered custom object type.

**Call relations**: _paginate_unchecked calls this for the custom_objects stream. It coordinates schema helpers, record pagination, and archived cleanup.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 1018–1020)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of custom object schemas from HubSpot. A schema describes a custom object type and its fields.

**Data flow**: It receives an HTTP client, calls the custom object schema endpoint, and returns only dictionary-shaped schema rows.

**Call relations**: Custom object pagination uses this to discover what to sync, and association discovery uses it so custom object types can be included in relationship scans.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 1023–1028)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Finds the best identifier for a custom object schema. HubSpot may provide this identifier under different field names.

**Data flow**: It receives a schema dictionary. It checks objectTypeId, fullyQualifiedName, and name in order, returning the first non-empty string or None.

**Call relations**: Custom object syncing and association object-type discovery call this whenever they need the API-safe object type name.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1031–1046)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Collects all useful property names from a custom object schema. This ensures the search request asks HubSpot for display fields as well as ordinary properties.

**Data flow**: It receives a schema. It gathers unique names from the properties list, the primary display property, and secondary display properties, then returns them as a list.

**Call relations**: _paginate_custom_objects calls this before searching each custom object type.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1048–1083)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Searches records for one specific custom object type. It follows HubSpot’s search paging and avoids duplicate records at the incremental cursor boundary.

**Data flow**: It receives a client, a temporary stream definition, schema details, requested properties, and an optional cursor. It sends search requests, converts each record into a custom object row, and yields pages.

**Call relations**: _paginate_custom_objects calls this once per discovered custom object schema, and this function uses _build_search_body and _custom_object_row.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1085–1124)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Turns one custom object record into a clear, searchable row. It adds context like object type, label, display title, and original properties.

**Data flow**: It receives a raw record and its schema. It combines record properties with schema labels and display-field settings, returning a normalized row or None if required IDs are missing.

**Call relations**: _paginate_custom_object_records calls this for each raw custom object returned by HubSpot.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1126–1156)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived custom object records so local copies can be tombstoned. Custom object delete IDs need the object type included to stay unique.

**Data flow**: It receives a client and custom object type ID. It pages through archived records and yields StreamPage delete entries shaped as objectTypeId:recordId.

**Call relations**: _paginate_custom_objects calls this after reading live records for each custom object type.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1158–1177)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a distinct list of owner teams from the teams embedded inside owner records. HubSpot does not expose this stream as a simple separate list here.

**Data flow**: It reads owners, inspects each owner’s teams, deduplicates teams by ID, and yields one page of unique team rows.

**Call relations**: _paginate_product_api calls this for the owner_teams stream, and it uses the generic collection reader to fetch owners.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_lists`  (lines 1179–1208)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot lists through the list search endpoint. Lists use an offset-style search response rather than the usual after cursor.

**Data flow**: It posts search requests with a count and offset, flattens additionalProperties into each row, fills in id from listId, and yields pages until HubSpot says there are no more.

**Call relations**: _paginate_product_api calls this for the lists stream, and list membership syncing calls it to know which lists to walk.

*Call graph*: called by 2 (_paginate_list_memberships, _paginate_product_api).


##### `HubSpotConnector._paginate_site_search`  (lines 1210–1230)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads CMS search results for a specific content type, such as knowledge articles. It uses HubSpot’s offset and total count style.

**Data flow**: It receives a content type, requests pages with limit and offset, yields dictionary rows, and stops when the next offset reaches the total.

**Call relations**: _paginate_product_api calls this for knowledge_articles.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1232–1256)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads assets attached to marketing campaigns. A campaign can contain many asset types, so this walks campaigns first and then fans out by asset type.

**Data flow**: It reads campaign pages, extracts each campaign ID and name, then asks for every supported asset type under that campaign and yields the resulting asset pages.

**Call relations**: _paginate_product_api calls this for campaign_assets, and it delegates each campaign/type pair to _paginate_campaign_asset_type.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1258–1293)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one asset type for one campaign and turns each asset into a uniquely identified row. It tolerates missing or blocked asset types because not every account has every marketing feature.

**Data flow**: It receives a campaign ID, campaign name, and asset type. It fetches assets, adds campaign context and a synthetic ID, yields pages, and ignores 403 or 404 responses.

**Call relations**: _paginate_campaign_assets calls this repeatedly while walking all campaign asset types.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1295–1301)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields the analytics view definitions configured in HubSpot. A view is like a saved filter for analytics reporting.

**Data flow**: It asks _analytics_view_rows for all view rows and yields them as a page if any exist.

**Call relations**: _paginate_product_api calls this for analytics_views.

*Call graph*: calls 1 internal fn (_analytics_view_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_view_rows`  (lines 1303–1334)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches and normalizes HubSpot analytics views. It gives each view a stable ID, readable name, filters, and normalized timestamps.

**Data flow**: It receives a client, reads the analytics views endpoint, accepts either a list or results wrapper, and returns cleaned rows.

**Call relations**: _paginate_analytics_views uses this directly, and _paginate_analytics_reports uses it to run reports for each view filter.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1336–1366)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a broad set of analytics report rows across report subjects, time periods, and analytics views. This turns many possible report queries into one stream.

**Data flow**: It computes the reporting date window, fetches analytics views, then loops through report families, subjects, time periods, and view filters, yielding pages from each query.

**Call relations**: _paginate_product_api calls this for analytics_reports, and this function delegates each concrete report request to _paginate_analytics_report_query.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_report_window`  (lines 1369–1370)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range used for analytics report pulls. It starts from a fixed early date and ends at today in UTC.

**Data flow**: It takes no input. It returns a pair of strings in HubSpot’s YYYYMMDD format: the fixed start date and the current date.

**Call relations**: _paginate_analytics_reports calls this before issuing report queries.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1372–1423)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Runs one analytics report query and pages through its breakdown rows. It skips report combinations HubSpot says are invalid or unavailable.

**Data flow**: It receives report family, subject, time period, optional view filter, and date range. It sends GET requests with offset paging, converts responses into rows, and yields them until the report is exhausted.

**Call relations**: _paginate_analytics_reports calls this for each report combination, and this function uses _analytics_report_rows to shape the returned data.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1426–1501)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Converts one analytics report response into syncable rows. It creates one totals row when present and separate rows for each breakdown.

**Data flow**: It receives HubSpot report data plus context about the query. It builds stable IDs, names, metrics, filter details, and date fields, then returns a list of rows.

**Call relations**: _paginate_analytics_report_query calls this after each report response.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1504–1508)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Creates a safe stable ID for an analytics report row. It replaces characters that would make the ID ambiguous.

**Data flow**: It receives any number of ID parts. It stringifies them, substitutes safe separators, joins them, and prefixes the result with analytics_report:.

**Call relations**: Analytics report row creation uses this when building totals and breakdown rows.


##### `HubSpotConnector._analytics_report_date`  (lines 1511–1512)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Formats HubSpot report dates from compact YYYYMMDD form into a standard YYYY-MM-DD string.

**Data flow**: It receives an eight-character date string and returns the same date with hyphens inserted.

**Call relations**: Analytics report row creation uses this when setting start_date and end_date.


##### `HubSpotConnector._paginate_event_types`  (lines 1514–1533)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the list of HubSpot event types. It assigns an ID even when HubSpot uses another identifying field.

**Data flow**: It fetches event types, accepts either a bare list or a results wrapper, chooses an ID from several possible fields, and yields the rows.

**Call relations**: _paginate_product_api calls this for event_types.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1535–1557)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads individual HubSpot event occurrences. It can start after a cursor so incremental syncs avoid rereading old events.

**Data flow**: It sends occurredAfter when a cursor exists, fetches events, assigns a real or synthetic ID to each row, and yields one page of rows.

**Call relations**: _paginate_product_api calls this for event_occurrences, and it uses _synthetic_event_id when HubSpot does not provide an ID.

*Call graph*: calls 1 internal fn (_synthetic_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._synthetic_event_id`  (lines 1560–1570)

```
def _synthetic_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Builds a stable fallback ID for an event occurrence that lacks its own ID. The ID combines event details with a hash of the whole payload.

**Data flow**: It receives an event row and its position in the response. It combines event type, object type, object ID, timestamp or index, and a payload hash into one string.

**Call relations**: _paginate_event_occurrences calls this only for rows without a HubSpot-provided ID.

*Call graph*: called by 1 (_paginate_event_occurrences).


##### `HubSpotConnector._paginate_email_events`  (lines 1572–1600)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot email tracking events, such as opens or clicks. It uses HubSpot’s offset-based paging and can start from an incremental cursor.

**Data flow**: It converts the cursor into a start timestamp, repeatedly requests email event pages, assigns IDs when missing, yields rows, and follows offset until hasMore is false.

**Call relations**: _paginate_product_api calls this for email_events. It uses _email_event_start_timestamp and _synthetic_email_event_id.

*Call graph*: calls 2 internal fn (_email_event_start_timestamp, _synthetic_email_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1603–1612)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts an email event cursor into the millisecond timestamp HubSpot expects. It accepts either a numeric string or an ISO date string.

**Data flow**: It receives an optional cursor. It returns None for no cursor or an unparseable cursor, an integer for numeric cursors, or an integer timestamp parsed from an ISO date.

**Call relations**: _paginate_email_events calls this before requesting email events.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._synthetic_email_event_id`  (lines 1615–1625)

```
def _synthetic_email_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Builds a stable fallback ID for an email event that does not include one. It combines the event’s time, recipient, type, campaign, and payload hash.

**Data flow**: It receives an email event row and its index. It returns a colon-separated string with unsafe colons in parts replaced.

**Call relations**: _paginate_email_events calls this for rows that lack a HubSpot event ID.

*Call graph*: called by 1 (_paginate_email_events).


##### `HubSpotConnector._stable_payload_hash`  (lines 1628–1630)

```
def _stable_payload_hash(row: dict[str, Any]) -> str
```

**Purpose**: Creates a short repeatable fingerprint of a row. This helps synthetic IDs stay stable even when HubSpot omits an ID.

**Data flow**: It receives a dictionary, serializes it in a sorted and compact JSON form, hashes it with SHA-256, and returns the first 16 hex characters.

**Call relations**: Synthetic ID builders use this as the final distinguishing piece for otherwise similar event rows.

*Call graph*: 2 external calls (sha256, dumps).


##### `HubSpotConnector._paginate_association_labels`  (lines 1632–1648)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the labels that describe relationships between HubSpot object types. A label explains what kind of association exists, such as a named relationship type.

**Data flow**: It walks object-type pairs that have labels, converts each label into a normalized row, and yields pages.

**Call relations**: _paginate_product_api calls this for association_labels. It uses _association_pairs_with_labels and _association_label_row.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_associations`  (lines 1650–1665)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual links between HubSpot records across object types. For example, it can find which contacts are associated with which companies or deals.

**Data flow**: It discovers object-type pairs with labels, pages through source record IDs, sends batch association reads, and yields normalized association rows.

**Call relations**: _paginate_product_api calls this for associations. It coordinates label discovery, object ID paging, and batch association reads.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_association_batch`  (lines 1667–1694)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads associations for a batch of source records. It also handles per-record continuation when HubSpot has more associated targets for a source record.

**Data flow**: It receives source and target object types plus input IDs. It posts a batch read, yields association rows, then replaces the pending inputs with any next-page inputs returned by HubSpot.

**Call relations**: _paginate_associations calls this after collecting a page of source record IDs. It uses _association_rows and _next_association_inputs.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1697–1711)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Finds follow-up association requests needed for records whose association list continues onto another page.

**Data flow**: It receives a batch association response. It returns new input dictionaries containing the same source record ID plus HubSpot’s after cursor.

**Call relations**: _paginate_association_batch calls this after each batch response to decide whether more batch reads are needed.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1713–1726)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Discovers object-type pairs that have association labels. It avoids wasting work on pairs HubSpot says do not exist or are unavailable.

**Data flow**: It gets all relevant object types, checks every from/to pair for labels, and yields only pairs with at least one label.

**Call relations**: Both association label syncing and association record syncing call this as their starting point.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1728–1741)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the list of object types to consider for association scans. It includes known HubSpot standard objects and any custom objects in the account.

**Data flow**: It starts with a built-in list of standard object types, fetches custom schemas if allowed, extracts their object type IDs, and returns the combined list.

**Call relations**: _association_pairs_with_labels calls this before checking from/to combinations.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1743–1759)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches relationship labels for one pair of object types. If HubSpot says the pair is optional or unavailable, it returns an empty list.

**Data flow**: It receives from and to object type names, calls the labels endpoint, and returns dictionary rows from the results.

**Call relations**: _association_pairs_with_labels calls this for each candidate pair.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1762–1779)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Normalizes one association label into a row with a stable ID and clear from/to object context.

**Data flow**: It receives a raw label and the source and target object types. It copies the label fields and adds ID, association type, category, label text, and object-type fields.

**Call relations**: _paginate_association_labels calls this for each label discovered by _association_pairs_with_labels.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1781–1793)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Reads pages of CRM object IDs. It is a lightweight helper for tasks that need IDs, not full records.

**Data flow**: It receives an object type, asks _paginate_crm_object_pages for records with hs_object_id, extracts each record’s id, and yields ID lists.

**Call relations**: Association syncing and sequence enrollment syncing call this before making per-record or batch follow-up requests.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1795–1825)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple pages from the CRM object list endpoint. This is useful when the connector only needs a few properties and not full search behavior.

**Data flow**: It receives an object type and requested property names. It GETs pages, yields dictionary records, and follows HubSpot’s after cursor until done.

**Call relations**: _paginate_crm_object_id_pages and contact identity pagination use this shared reader.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1828–1862)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Turns a HubSpot batch association response into flat relationship rows. Each row represents one source record linked to one target record, with one association type.

**Data flow**: It receives raw batch data and object-type context. It walks results, target records, and association types, building normalized rows for valid links.

**Call relations**: _paginate_association_batch calls this after each batch response.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1865–1892)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one normalized association row. It gives the relationship a stable ID and records both sides of the link.

**Data flow**: It receives one association type plus source and target object IDs. It returns a dictionary containing from/to IDs, category, label, relationship type, and original association type details.

**Call relations**: _association_rows uses this as the row builder for each association it finds.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1895–1898)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether a failed request should be treated as an absent optional relationship or feature. This lets broad discovery continue even when some object pairs or APIs are not supported.

**Data flow**: It receives an HTTP error. It returns true for 400 or 404 responses, or for permission-style 403 responses detected by _is_stream_unavailable.

**Call relations**: Many fan-out helpers call this when probing optional HubSpot endpoints, including associations, memberships, consent, sequences, and simple CRM object pages.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1900–1914)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads which records belong to each HubSpot list. It first discovers lists, then walks memberships for each list.

**Data flow**: It receives a client, iterates through list pages, extracts each list ID, and yields membership pages from _paginate_memberships_for_list.

**Call relations**: _paginate_product_api calls this for list_memberships, and it depends on _paginate_lists for the parent list catalog.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1916–1959)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the members of one HubSpot list. It adds list context so each membership row is understandable by itself.

**Data flow**: It receives a list record and list ID. It pages through that list’s memberships, builds rows with IDs like listId:recordId, and yields pages.

**Call relations**: _paginate_list_memberships calls this once for each list it discovers.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1961–1974)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot email subscription definitions. These describe the categories or types of communication a contact may consent to.

**Data flow**: It fetches the definitions endpoint, accepts either results or subscriptionDefinitions, assigns an ID to each row, and yields the rows.

**Call relations**: _paginate_product_api calls this for subscription_definitions.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_consent_states`  (lines 1976–1989)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads email consent and unsubscribe status for contacts. It uses contact email addresses to ask HubSpot’s communication preferences APIs for each person’s state.

**Data flow**: It pages through contacts with emails, calls status and unsubscribe-all endpoints for each email, combines the returned rows, and yields pages.

**Call relations**: _paginate_product_api calls this for consent_states. It coordinates contact identity pages, _consent_status_rows, and _unsubscribe_all_rows.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1991–2007)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads contact IDs and email addresses for consent lookups. It normalizes email whether HubSpot places it at the top level or inside properties.

**Data flow**: It uses _paginate_crm_object_pages to fetch contacts with the email property, then yields rows that always include an email field.

**Call relations**: _paginate_consent_states calls this before making communication preference requests.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 2009–2030)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches subscription-specific consent statuses for one email address. It returns empty results when HubSpot says the lookup is unavailable.

**Data flow**: It receives a contact and email, safely URL-encodes the email, requests EMAIL channel statuses, and converts each result with _consent_row.

**Call relations**: _paginate_consent_states calls this for each contact email.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 2032–2056)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches the global unsubscribe status for one email address. This captures whether a contact has opted out of all email communication.

**Data flow**: It receives a contact and email, URL-encodes the email, calls the unsubscribe-all endpoint, and converts each result into a consent row.

**Call relations**: _paginate_consent_states calls this alongside _consent_status_rows for each contact email.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 2059–2089)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Normalizes one consent or unsubscribe response into a clear row. It adds contact context and consistent fields for purpose, status, legal basis, and capture time.

**Data flow**: It receives the raw status row, contact, email, and status kind. It returns a row with a stable ID and standardized consent fields.

**Call relations**: _consent_status_rows and _unsubscribe_all_rows both call this to shape their API responses.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2091–2118)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sales sequences for each HubSpot user who appears through owner records. HubSpot scopes sequence queries by user ID.

**Data flow**: It gets sequence-capable user rows, requests sequences for each user ID, adds owner context to each row, and yields pages.

**Call relations**: _paginate_product_api calls this for sequences. It uses _sequence_user_rows and the generic collection reader.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_user_rows`  (lines 2120–2143)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a unique list of HubSpot user IDs from owner records. This gives sequence syncing the user IDs it needs.

**Data flow**: It reads owners, extracts userId, owner ID, and owner email, deduplicates by userId, and yields one page of user rows.

**Call relations**: _paginate_sequences calls this before querying sequences per user.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2145–2163)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sequence enrollment data for contacts. It checks contacts one by one because the endpoint is contact-specific.

**Data flow**: It pages through contact IDs, requests each contact’s sequence enrollments, converts responses into rows, and yields pages.

**Call relations**: _paginate_product_api calls this for sequence_enrollments, and it uses _paginate_crm_object_id_pages plus _sequence_enrollment_rows.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2166–2180)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes sequence enrollment responses for one contact. It handles either a results list or a single response object.

**Data flow**: It receives response data and a contact ID. It assigns each row an ID, adds contact_id, and returns the list of rows.

**Call relations**: _paginate_sequence_enrollments calls this after each contact-specific enrollment request.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2182–2211)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads submissions for every HubSpot form. It first discovers forms, then walks each form’s submission endpoint.

**Data flow**: It reads forms, extracts each form ID, fetches submission pages, assigns each submission an ID, adds form ID and form name, and yields pages.

**Call relations**: _paginate_product_api calls this for form_submissions, and it uses _paginate_get_collection for both form discovery and submission pages.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2213–2228)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages inside HubSpot conversation threads. Threads are the parent records, and messages are fetched per thread.

**Data flow**: It reads conversation threads, extracts each thread ID, fetches messages for that thread, adds thread_id to each message, and yields pages.

**Call relations**: _paginate_product_api calls this for conversation_messages, using _paginate_get_collection for both levels.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipelines`  (lines 2230–2237)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads pipeline definitions for supported HubSpot object types. Pipelines describe stages for deals and tickets.

**Data flow**: It loops through supported pipeline object types, asks for normalized pipeline rows for each, and yields any non-empty page.

**Call relations**: _paginate_product_api calls this for pipelines, and it delegates per-object work to _pipeline_rows_for_object_type.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2239–2289)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the stages inside deal and ticket pipelines. It turns nested stage data into standalone rows.

**Data flow**: It fetches raw pipelines for each supported object type, walks each pipeline’s stages, computes status and closed flags, and yields normalized stage rows.

**Call relations**: _paginate_product_api calls this for pipeline_stages, and it uses _raw_pipelines_for_object_type to fetch the source pipeline data.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2291–2314)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes pipeline definitions for one object type. It adds object context and a consistent active or archived status.

**Data flow**: It receives an object type, fetches raw pipelines, builds rows with IDs like objectType:pipelineId, and returns the rows.

**Call relations**: _paginate_pipelines calls this once for each supported pipeline object type.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2316–2327)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches raw pipeline data for one HubSpot object type. It treats missing or forbidden pipeline endpoints as simply empty.

**Data flow**: It receives an object type, calls the CRM pipelines endpoint, returns dictionary rows from results, or returns an empty list for 403 and 404.

**Call relations**: Pipeline and pipeline-stage normalization both call this before shaping the data.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2330–2334)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects a specific HubSpot error meaning archived-object paging is not supported for that object. This prevents cleanup from failing streams that otherwise synced correctly.

**Data flow**: It receives an HTTP error. It checks for a 400 response with HubSpot’s deleted-object paging message and returns true or false.

**Call relations**: Archived-ID sweep helpers call this when their archived=true list request fails.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2337–2345)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Extracts HubSpot’s message field from an HTTP error response. It is a small helper for interpreting upstream errors.

**Data flow**: It receives an HTTP error, tries to parse the JSON body, and returns the message string if one exists; otherwise it returns None.

**Call relations**: _is_archived_sweep_unsupported uses this to inspect HubSpot’s error text.


##### `HubSpotConnector._paginate_junction`  (lines 2347–2394)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple relationship streams such as deal_contacts and ticket_companies. It walks parent records with inline associations and emits one row per parent-target pair.

**Data flow**: It receives parent and target object names. It pages through parent objects with an associations query, extracts associated target IDs, builds rows with parent and target ID fields, and yields pages.

**Call relations**: _paginate_unchecked calls this for junction streams. These streams full-refresh because HubSpot does not provide association modification timestamps.

*Call graph*: called by 1 (_paginate_unchecked).


### `extensions/sources/ufo_ext_sources/salesforce.py`

`io_transport` · `during Salesforce source sync`

Salesforce stores customer data in many object types, called SObjects, such as Account, Contact, Opportunity, and Case. This file defines which of those objects the system can sync, and how to ask Salesforce for them safely and incrementally.

The connector first asks Salesforce to describe an object, which means Salesforce returns the list of fields that object currently has in that customer’s organization. This matters because Salesforce setups can be customized; the code does not rely on a fixed, hand-written list of columns. It then builds a SOQL query, which is Salesforce’s SQL-like search language, to fetch records in small pages ordered by `SystemModstamp`, the timestamp used as the sync cursor.

If the system has synced before, it only asks for records changed after the saved cursor. It also separately asks Salesforce for records that were hard-deleted since that cursor. Those deleted IDs are returned as a tombstone page, like a notice saying “this record used to exist, but remove it now.”

The connector follows Salesforce pagination links until all records are read. If Salesforce refuses access with a 401 or 403 response, it skips that stream with a clear message instead of pretending the stream is empty. The file expects the real Salesforce instance URL and credentials to be supplied by the surrounding runner.

#### Function details

##### `_stream`  (lines 29–38)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Salesforce stream, such as accounts or contacts. It records the Salesforce object name, the primary key, and which timestamp field should be used to continue a sync from where it left off.

**Data flow**: It receives a friendly stream name, a Salesforce SObject name, and whether the stream is considered canonical. It packages those details into a `StreamSpec`, which is the system’s small instruction card for syncing that object.

**Call relations**: This helper is used while the file is being loaded to build the `SALESFORCE_STREAMS` list. Each entry it creates is later used by `SalesforceConnector.paginate` to know what Salesforce object to query and what cursor field to use.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 78–82)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: This function builds the Salesforce query used to fetch records for one stream. It includes all discovered fields, optionally filters to records newer than the saved cursor, and orders results so the sync can move forward predictably.

**Data flow**: It takes a stream description, a list of field names, and an optional cursor timestamp. It turns them into one SOQL query string, such as selecting fields from an object with a `WHERE` clause when a previous sync cursor exists.

**Call relations**: `SalesforceConnector.paginate` calls this after it has learned the object’s fields. The query it returns is then sent to Salesforce’s query endpoint to retrieve records page by page.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 84–90)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: This function asks Salesforce what fields exist on a particular object. It avoids hard-coding Salesforce schemas, which is important because each Salesforce organization can add or change fields.

**Data flow**: It receives an HTTP client and an SObject name. It calls Salesforce’s describe endpoint, reads the returned field list, keeps valid field names, and returns them as a list of strings.

**Call relations**: `SalesforceConnector.paginate` calls this before building the SOQL query. The field list it returns is handed to `SalesforceConnector._build_soql` so the connector fetches all fields Salesforce exposes for that object.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 92–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main read loop for a Salesforce stream. It fetches changed records in pages, follows Salesforce’s next-page links, and, on incremental syncs, also reports records that were deleted.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It discovers fields, builds a query, requests records from Salesforce, yields each non-empty batch, follows `nextRecordsUrl` until Salesforce says the query is done, and then may yield a deletion page. If Salesforce rejects access with 401 or 403, it turns that into a clear stream-skipped error.

**Call relations**: The broader sync runner calls this when it wants data for one Salesforce stream. Inside, it relies on `_describe_fields` to learn columns, `_build_soql` to create the query, and `_deleted_page` to collect deletion tombstones after the normal record pages are finished.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 121–139)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: This function asks Salesforce which records were hard-deleted during an incremental sync window. It turns those deleted record IDs into a tombstone page so downstream storage can remove or mark them correctly.

**Data flow**: It receives an HTTP client, a stream description, and the previous cursor timestamp. It chooses the current time as the end of the window, calls Salesforce’s deleted-record endpoint, extracts deleted IDs, picks the next cursor from Salesforce’s response when available, and returns a `StreamPage` containing deletes and the next cursor.

**Call relations**: `SalesforceConnector.paginate` calls this only when there is already a cursor, meaning the sync is incremental rather than a first full snapshot. The page it returns is yielded back to the sync runner alongside normal record pages.

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 141–144)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function removes Salesforce’s extra `attributes` wrapper from each record. That wrapper describes Salesforce metadata, not the actual business fields users usually want to sync.

**Data flow**: It receives one Salesforce record and the stream it belongs to. If the record contains an `attributes` key, it returns a copy without that key; otherwise it returns the record unchanged.

**Call relations**: The base connector flow calls this when preparing raw Salesforce records for the rest of the system. It keeps synced pages focused on real field values rather than Salesforce’s transport metadata.


### Support and helpdesk connectors
Connectors for customer support and helpdesk platforms that normalize tickets, conversations, users, companies, knowledge content, and community records.

### `extensions/sources/ufo_ext_sources/freshdesk.py`

`io_transport` · `source sync`

Freshdesk stores helpdesk data behind many web API endpoints. This connector is the bridge between those endpoints and the rest of the system's source-sync machinery. Without it, the system would not know which Freshdesk objects exist, how to authenticate, or how to walk through Freshdesk's paged results without missing records.

The file first defines the Freshdesk streams: these are the named kinds of records the system can recall later, such as tickets, contacts, solution articles, and discussion comments. Most streams map directly to one Freshdesk URL. Some are nested, like conversations inside tickets or articles inside solution folders, so they need extra walking.

The `FreshdeskConnector` builds an HTTP client using Freshdesk's expected authentication: the API key is sent as the Basic Auth username, with a dummy password. It then chooses the right paging strategy for each stream. Some Freshdesk endpoints use a standard `Link` header, which is like a “next page” signpost. Tickets are different: they use page numbers and can be filtered by `updated_since` for incremental syncing. Nested content is fetched by first reading parent records, then using each parent ID to fetch its children.

If Freshdesk rejects a request with an authorization error, the connector turns that into a skipped stream rather than pretending the sync succeeded.

#### Function details

##### `_stream`  (lines 55–69)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a small description of one Freshdesk stream, such as what it is called, what its primary ID field is, and whether it supports incremental syncing. It is a convenience helper so the long stream list stays readable and consistent.

**Data flow**: It receives a stream name plus optional details like the Freshdesk source object name, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults, then produces a `StreamSpec`, which is the system's standard description of a readable source stream.

**Call relations**: This helper is used while the module is being loaded to build `FRESHDESK_STREAMS`. It hands each stream description to `StreamSpec`, which the wider source framework later reads when deciding what Freshdesk data can be synced.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector._make_client`  (lines 109–124)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the web client used to talk to a specific Freshdesk account. It sets timeouts, JSON headers, the base Freshdesk URL, and the correct authentication method.

**Data flow**: It receives a base URL and a resolved credential. If the credential already includes a custom transport, it keeps that transport intact. If the credential contains a direct API key, it wraps that key in Freshdesk's Basic Auth format. The result is an `httpx.AsyncClient`, an asynchronous HTTP client ready to make requests.

**Call relations**: The connector framework calls this when it is preparing to sync Freshdesk. This function relies on `httpx.Timeout`, `httpx.BasicAuth`, and `httpx.AsyncClient` to create the actual HTTP machinery used later by `paginate` and its helper methods.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 127–137)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: Builds the query settings for one page of Freshdesk tickets. These settings tell Freshdesk how many tickets to return, which page to return, and how to sort and filter them.

**Data flow**: It receives an optional cursor, which is the last known update time from a previous sync, and a page number. It creates a dictionary of request parameters for the tickets endpoint. If a cursor is present, it adds `updated_since` so Freshdesk only returns newer or changed tickets.

**Call relations**: `_paginate_tickets` calls this every time it asks Freshdesk for another ticket page. The helper keeps the ticket-specific request rules in one place, so the pagination loop can focus on when to stop.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 139–213)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses how to read pages for each Freshdesk stream. It is the main traffic director that knows which streams are simple, which are nested, and which need special Freshdesk rules.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. Based on the stream name, it sends the work to the correct pagination helper. As those helpers return batches of records, it yields those batches onward. If Freshdesk returns a 401 or 403 refusal, it changes that into a `StreamSkipped` error with a clear explanation.

**Call relations**: The wider source-sync framework calls this when it wants records for a Freshdesk stream. This method then hands off to `_paginate_tickets`, `_paginate_conversations`, `_paginate_two_level`, `_paginate_three_level`, or `_paginate_link_header`, depending on the shape of the Freshdesk endpoint.

*Call graph*: calls 6 internal fn (__init__, _paginate_conversations, _paginate_link_header, _paginate_three_level, _paginate_tickets, _paginate_two_level).


##### `FreshdeskConnector._paginate_link_header`  (lines 215–222)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads endpoints that use a standard web pagination style where each response points to the next page in a `Link` header. A `Link` header is like a note from the server saying, “go here next.”

**Data flow**: It receives an HTTP client, a resource path, and optional request parameters. It asks the shared REST connector helper to fetch pages with the configured page size, then yields each list of records as it arrives.

**Call relations**: `paginate` uses this for simple Freshdesk streams. The nested pagination helpers also use it whenever they need to walk through parent or child endpoints. It delegates the low-level `Link` header walking to the base connector's `_get_link_header_pages` behavior.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 224–241)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Freshdesk tickets using Freshdesk's ticket-specific page-number system. It supports incremental syncing by asking only for tickets updated since a cursor time.

**Data flow**: It starts at page 1 and repeatedly builds ticket request parameters with `_build_tickets_params`. It fetches a page from `/api/v2/tickets`, turns the response into a list of ticket records, and yields that list. It stops when there are no records, when a page is shorter than the maximum page size, or when it reaches Freshdesk's 300-page limit.

**Call relations**: `paginate` calls this when the requested stream is `tickets`. `_paginate_conversations` also calls it first, because conversations are found by walking through tickets and then asking for each ticket's conversation list.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, paginate).


##### `FreshdeskConnector._paginate_conversations`  (lines 243–259)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads ticket conversations by first finding the relevant tickets, then fetching the conversation thread for each ticket. This is needed because conversations live underneath tickets rather than in one flat endpoint.

**Data flow**: It receives an HTTP client and optional cursor. It uses `_paginate_tickets` to get ticket batches, extracts each ticket ID, then calls `_paginate_link_header` on that ticket's conversations URL. Before yielding each conversation page, it makes sure every conversation record has the related `ticket_id` stamped on it.

**Call relations**: `paginate` calls this for the `conversations` stream. It depends on `_paginate_tickets` to decide which tickets are in scope, and on `_paginate_link_header` to walk through each ticket's conversation pages.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (paginate).


##### `FreshdeskConnector._paginate_two_level`  (lines 261–272)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Freshdesk data that has a parent-and-child shape, such as folders containing responses or forums containing topics. It is a reusable walker for two-level trees.

**Data flow**: It receives a parent endpoint path and a child endpoint template containing an `{id}` placeholder. It fetches pages of parent records, extracts each parent's ID, fills that ID into the child path, and yields each page of child records.

**Call relations**: `paginate` calls this for several nested streams, including canned responses, solution folders, discussion forums, discussion topics, and discussion comments. It uses `_paginate_link_header` for both the parent walk and each child walk.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


##### `FreshdeskConnector._paginate_three_level`  (lines 274–297)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Freshdesk data that is three levels deep, such as solution articles inside folders inside solution categories. It is the tree-walker for deeper knowledge-base structures.

**Data flow**: It receives paths for the root level, middle level, and leaf level. It fetches root records, extracts each root ID to fetch middle records, then extracts each middle ID to fetch leaf records. The output is pages of the leaf records, such as articles.

**Call relations**: `paginate` calls this for `solution_articles`. It repeatedly uses `_paginate_link_header` at each level of the tree, turning category pages into folder requests and folder pages into article requests.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/intercom.py`

`io_transport` · `during Intercom source sync`

Intercom does not expose all of its data in one simple shape. Some records are fetched through a search endpoint, some through a scrolling company endpoint, some through one-off list endpoints, and some only appear after first fetching a parent record. This file is the adapter that hides those differences from the rest of UFO.

It defines the Intercom streams UFO knows about, including their names, main identifier fields, and cursor fields. A cursor is the saved “last seen” value used to continue an incremental sync without rereading everything. For Intercom, that is usually an updated_at timestamp in Unix seconds.

The IntercomConnector builds an authenticated HTTP client, adds the required Intercom-Version header, and then chooses the right pagination method for each stream. For example, conversations, contacts, and tickets use Intercom’s search API; companies use the scroll API; admins and tags use simple list calls. Detail streams, like conversation parts and company segments, first walk through parent conversations or companies and then fetch each child list.

The file also flattens a few nested fields into easier top-level fields. This is like taking important notes out of envelopes so later database queries do not need to open each envelope again. If Intercom refuses access with a permission error, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 52–66)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one kind of Intercom data. A stream description tells UFO what the stream is called, what object it represents in Intercom, which field uniquely identifies a record, and which field is used for incremental syncing.

**Data flow**: It receives a stream name plus optional settings such as source object, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults, then returns a StreamSpec object that the connector later uses as its map for syncing that stream.

**Call relations**: This helper is used while the module is loaded to build the Intercom stream list. It hands its settings to StreamSpec so the rest of the connector can treat each Intercom resource in a consistent way.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector._make_client`  (lines 103–106)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Intercom and adds the Intercom API version header. Without that header, Intercom may interpret requests using a different API version than this connector expects.

**Data flow**: It receives the base URL and a resolved credential. It asks the parent RestConnector to create the normal authenticated client, adds Intercom-Version: 2.11 to the client headers, and returns the ready-to-use client.

**Call relations**: The base connector calls this when setting up API access. After this method returns, all later pagination methods use the same client to make Intercom requests with the correct authentication and version.


##### `IntercomConnector._build_search_body`  (lines 109–139)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: This builds the JSON request body for Intercom search endpoints. It tells Intercom how many records to return, how to sort them, where to continue within a page sequence, and which records are newer than the saved cursor.

**Data flow**: It receives a stream description, the saved cursor from a previous sync, and an optional starting_after token for the next search page. It creates a request body with pagination, sorting, and a query filter; if the cursor looks like a number, it sends it as a number because Intercom expects that. The result is a dictionary ready to send in a POST request.

**Call relations**: Both _paginate_search and _paginate_conversation_parts call this before posting to Intercom’s search API. It is the shared recipe that keeps normal search streams and conversation-part parent lookups using the same cursor logic.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 142–147)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: This small helper safely picks the first dictionary from a list-like value. It is used when Intercom nests related records, such as contacts or companies, inside a list.

**Data flow**: It receives any value. If the value is a non-empty list and its first item is a dictionary, it returns that dictionary; otherwise it returns None. It does not change the input.

**Call relations**: The flattening helpers use this when they need just the first related contact or company. It keeps those helpers from repeating the same safety checks.


##### `IntercomConnector._flatten_conversation`  (lines 150–167)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes selected nested conversation fields easier to use later. It copies the source type, subject, body, and first requester contact ID onto top-level keys.

**Data flow**: It receives one conversation record. It copies the record, looks inside its source object and contacts envelope, and adds flat keys such as source__type, source__subject, source__body, and requester_id when the data is present. It returns the enriched copy.

**Call relations**: The main flatten method calls this only for the conversations stream. Its output is then passed back to the broader sync flow as a simpler record shape for storage and later querying.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 170–179)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes the author information on a conversation part easier to query. It pulls the nested author type and author ID onto top-level fields.

**Data flow**: It receives one conversation-part record. It copies the record, looks for an author dictionary, and adds author_type and author_id if available. It keeps any existing conversation_id untouched and returns the copy.

**Call relations**: The main flatten method calls this for conversation_parts records. Those records usually come from _paginate_conversation_parts, which stamps each part with its parent conversation ID before flattening happens.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 182–190)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This extracts the first company linked to a contact and exposes it as org_id. That gives later transforms a simple way to connect a contact to an organization.

**Data flow**: It receives one contact record. It copies the record, looks inside the nested companies list, and if the first company has an id or company_id, it adds that value as org_id. It returns the enriched copy.

**Call relations**: The main flatten method calls this only for contacts. It relies on _first to safely inspect the nested companies list.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 192–206)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This prepares Intercom records for UFO’s storage layer by simplifying certain nested fields and normalizing cursor values. It is the final cleanup step for records after they are fetched.

**Data flow**: It receives a raw record and the stream it belongs to. Depending on the stream name, it sends the record through the conversation, conversation-part, or contact flattening helper. If the stream has a cursor field and that value is an integer, it converts it to a decimal string so UFO’s string-based watermark can advance correctly. It returns the cleaned record.

**Call relations**: The broader RestConnector flow calls this after pagination yields records. Inside this method, stream-specific work is handed off to _flatten_conversation, _flatten_conversation_part, or _flatten_contact.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 208–252)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the traffic director for reading Intercom streams. Given a stream, it chooses the correct Intercom API paging method and yields pages of records.

**Data flow**: It receives an HTTP client, a stream description, and the saved cursor. It checks the stream name, delegates to the matching pagination helper, and yields each page that helper produces. If Intercom responds with 401 or 403, meaning access is refused or missing permissions, it raises StreamSkipped so the run records the skip instead of treating it as a full failure.

**Call relations**: The main sync machinery calls paginate when it needs records for a stream. This method then calls one of the specialized helpers: search, scroll, list, attributes, conversation parts, company segments, or activity logs.

*Call graph*: calls 8 internal fn (__init__, _paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search).


##### `IntercomConnector._paginate_search`  (lines 254–274)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams that use Intercom’s search API, such as conversations, contacts, and tickets. It keeps requesting pages until Intercom says there is no next page.

**Data flow**: It receives the HTTP client, stream description, and saved cursor. For each loop, it builds a search body, posts it to the stream’s search path, extracts the record list from the right response key, yields that list if it is not empty, and follows the next starting_after token if one is present. It stops when no next token remains.

**Call relations**: paginate calls this for streams listed in the search-path map. This helper calls _build_search_body each time it needs the next Intercom search request.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_scroll`  (lines 276–290)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads companies through Intercom’s scroll API. The scroll API works like asking for the next drawer of files using a token returned with the previous drawer.

**Data flow**: It starts with no scroll token. It sends a GET request to /companies/scroll, including the scroll_param token after the first page, yields the returned company records, then stores the next scroll_param. It stops when there are no records or no next token.

**Call relations**: paginate calls this when the requested stream is companies. It returns company pages directly to paginate, which passes them back to the sync flow.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_list`  (lines 292–304)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads simple Intercom list endpoints, such as admins, tags, teams, and segments. These endpoints return their records in one response rather than a long page sequence.

**Data flow**: It receives the HTTP client and stream description. It looks up the endpoint path, sends one GET request, checks whether the response contains a list under the stream name or under data, and yields that list if it has records. Then it finishes.

**Call relations**: paginate calls this for streams in the list-path map. It is the simplest pagination helper because there is no next-page token to follow.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_attributes`  (lines 306–315)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Intercom data attribute definitions for companies or contacts. These are metadata fields that describe custom data stored on those objects.

**Data flow**: It receives the HTTP client and stream description. It maps the stream name to the Intercom model name, sends a GET request to /data_attributes with that model as a parameter, extracts the data list, and yields it if non-empty.

**Call relations**: paginate calls this for company_attributes and contact_attributes. It turns two UFO streams into the single Intercom data-attributes endpoint by changing the model parameter.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_conversation_parts`  (lines 317–349)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the individual messages or events inside conversations. Intercom does not return all of these as a standalone search stream, so the connector first finds conversations and then fetches each conversation’s details.

**Data flow**: It receives the HTTP client and saved cursor. It searches conversations page by page, using the same cursor logic as the conversations stream. For each conversation with an ID, it fetches /conversations/{id}, extracts its conversation_parts list, stamps each part with the parent conversation_id if missing, and yields the parts. It follows the search starting_after token until there are no more conversation pages.

**Call relations**: paginate calls this when syncing conversation_parts. This helper calls _build_search_body to find the parent conversations, then performs detail GET requests for each parent before yielding child records.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_company_segments`  (lines 351–375)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the segments attached to each company. Because those segment records are reached through each company, the connector walks companies first and then asks Intercom for every company’s segment list.

**Data flow**: It scrolls through companies using /companies/scroll. For each company with an ID, it sends a GET request to /companies/{id}/segments, extracts the segment list, adds company_id to each segment when possible, and yields non-empty segment pages. It continues with the next scroll token until the company scroll ends.

**Call relations**: paginate calls this for the company_segments stream. It combines the company scroll pattern with per-company detail requests so downstream storage receives segment records linked back to their company.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_activity_logs`  (lines 377–399)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads admin activity logs, optionally starting after a saved created_at cursor. It follows Intercom’s next-page links until the log stream is exhausted.

**Data flow**: It receives the HTTP client and saved cursor. If a cursor exists, it sends it as created_at_after on the first request to /admins/activity_logs. For each response, it yields the activity_logs list if present, then reads the pages.next value. If that next value is a full URL, it converts it to a path for the existing client; if there is no usable next value, it stops.

**Call relations**: paginate calls this for the activity_logs stream. It is separate from the generic search helper because this endpoint uses URL-style next links and a created_at_after parameter rather than the search API body.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/zendesk.py`

`io_transport` · `source sync pagination`

Zendesk has many different API endpoints, and they do not all page through data in the same way. This file is the adapter that hides those differences. It defines the Zendesk streams the system can sync, then chooses the right way to walk through each stream.

For large, frequently changing data like tickets and users, it uses Zendesk’s incremental cursor export. In plain terms, that means it asks, “Give me everything changed since this time,” then follows Zendesk’s “next” link until Zendesk says there is nothing more. For ordinary list endpoints, it follows normal next-page links. Two streams need special treatment: ticket comments are buried inside ticket event records, so this file pulls the comment events out and adds the ticket ID; user identities are fetched by first listing users and then asking Zendesk for each user’s identities.

The connector also smooths over small Zendesk quirks. Some endpoints return their records under names that differ from the stream name, so it maps those names correctly. Tickets can include related users in the same response, and this file copies requester, submitter, and assignee emails onto the ticket records for easier use later. If Zendesk refuses access with a 401 or 403 error, the stream is skipped with a clear message instead of crashing unclearly.

#### Function details

##### `_stream`  (lines 58–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a stream description for one kind of Zendesk data. A stream description tells the sync system what the stream is called, where it comes from in Zendesk, and which fields identify and order its records.

**Data flow**: It receives a stream name plus optional details such as the Zendesk source path, primary key, time fields, and whether the stream is considered canonical. It fills in sensible defaults when details are not supplied, then returns a StreamSpec object that the connector later uses during syncing.

**Call relations**: This function is used while building the file’s ZENDESK_STREAMS list. Each call hands its settings to StreamSpec so the rest of the connector has a consistent recipe for every Zendesk stream.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 142–167)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: This helper copies useful information from related records that Zendesk returned alongside the main records. In this file, it is used to add user email addresses onto ticket records, so later users do not have to join those records themselves.

**Data flow**: It receives the main records, the full Zendesk response page, and instructions for which related array and fields to use. It first builds a quick lookup table from the related items, like a phone book keyed by user ID. Then it walks each main record, finds matching related entries, and writes missing target fields such as requester_email onto the record in place. It does not return a new list; it changes the records it was given.

**Call relations**: ZendeskConnector._paginate_incremental_cursor calls this when syncing a stream that asked Zendesk to include related data. The paginated API response arrives first, this helper enriches the records, and then the paginator yields those improved records onward.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 176–177)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This method decides which JSON field in a Zendesk response contains the actual records for a stream. It exists because some Zendesk endpoints use names that do not exactly match this project’s stream names.

**Data flow**: It receives a StreamSpec. It checks whether that stream has a special response field name in the override table. If so, it returns that special name; otherwise, it returns the stream’s own name.

**Call relations**: ZendeskConnector._paginate_default calls this before reading each ordinary Zendesk list response. The result tells that paginator where to look inside the JSON data for the rows it should yield.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 180–192)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: This method turns the saved sync cursor into the Unix timestamp format Zendesk expects. A Unix timestamp is a number of seconds since January 1, 1970, used by many APIs to represent time.

**Data flow**: It receives a cursor that may be missing, already numeric, or written as an ISO date-time string. If there is no usable cursor, it returns 0, meaning start from the beginning. If the cursor is numeric, it returns that number. If it is a date string, it parses it, assumes UTC when no timezone is included, and returns the equivalent timestamp in seconds.

**Call relations**: The incremental paginators call this before building their first Zendesk request. ZendeskConnector._paginate_incremental_cursor, ZendeskConnector._paginate_ticket_comments, and ZendeskConnector._paginate_user_identities all need this conversion so they can ask Zendesk for records changed since the previous sync point.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 195–204)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: This method turns Zendesk’s next-page URL into the path form used by the connector’s request helper. It keeps the route and query string but removes the scheme and host.

**Data flow**: It receives a next-page URL or nothing. If the value is empty or has no path, it returns nothing. Otherwise, it parses the URL, keeps the path, adds the query string if present, and returns that path for the next request.

**Call relations**: All pagination methods use this after each API response. Zendesk gives back links such as after_url or next_page; this method converts those links into the form that the connector can pass back into its internal GET request method.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 206–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading a Zendesk stream. It chooses the correct pagination strategy for the requested stream and turns Zendesk API responses into batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name and forwards the work to the specialized paginator for ticket comments, user identities, incremental cursor streams, or ordinary page-based streams. It yields each batch it receives. If Zendesk refuses access with a 401 or 403 status, it raises StreamSkipped with a clear explanation; other errors are allowed to bubble up.

**Call relations**: The broader source-sync framework calls this when it wants records for one Zendesk stream. This method then delegates to ZendeskConnector._paginate_ticket_comments, ZendeskConnector._paginate_user_identities, ZendeskConnector._paginate_incremental_cursor, or ZendeskConnector._paginate_default depending on the stream.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 232–253)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads high-volume Zendesk objects using Zendesk’s incremental cursor API. It is meant for data like tickets and users, where the sync should continue from a saved time rather than reread everything unnecessarily.

**Data flow**: It receives an HTTP client, a stream description, and a cursor. It converts the cursor into a timestamp, builds the first incremental Zendesk API path, and repeatedly fetches pages. From each response, it pulls out the record list. For tickets, it may also copy related user emails into each ticket. It yields non-empty batches, stops when Zendesk says the stream has ended, or follows the next cursor link to continue.

**Call relations**: ZendeskConnector.paginate calls this for streams listed as incremental cursor streams. Inside its loop, it relies on ZendeskConnector._cursor_to_unix to start at the right time, _apply_sideload to enrich records when Zendesk supplied related users, and ZendeskConnector._next_page_path to follow Zendesk’s continuation links.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 255–265)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads ordinary Zendesk list endpoints that use standard next-page links. It is the simple path for streams that do not need special incremental or nested-record logic.

**Data flow**: It receives an HTTP client and a stream description. It builds an initial API path from the stream’s Zendesk source object, fetches each page, finds the array of records under the correct JSON field, yields any records it finds, and follows next_page until there is no next page.

**Call relations**: ZendeskConnector.paginate calls this for streams that are not special cases. It uses ZendeskConnector._data_field to know where records live in each response and ZendeskConnector._next_page_path to turn Zendesk’s next-page URL into the next request path.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 267–299)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method extracts ticket comments from Zendesk’s ticket event feed. Zendesk does not expose this stream here as a plain list of comments, so the method digs comments out of nested event data and turns them into normal rows.

**Data flow**: It receives an HTTP client and an optional cursor. It starts from the cursor time and asks Zendesk for ticket events with comment events included. For each ticket event, it looks through child events, keeps only those marked as Comment, copies the comment data, adds the parent ticket_id, and normalizes numeric created_at timestamps into readable UTC date-time strings. It yields batches of comments, stops at end_of_stream, or follows the next link.

**Call relations**: ZendeskConnector.paginate calls this only for the ticket_comments stream. This method uses ZendeskConnector._cursor_to_unix to begin at the right point in time and ZendeskConnector._next_page_path to continue through Zendesk’s event feed.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate); 1 external calls (fromtimestamp).


##### `ZendeskConnector._paginate_user_identities`  (lines 301–326)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads user identity records, such as email or login identities, by visiting each changed user and then fetching that user’s identities. It exists because Zendesk exposes identities under individual users rather than as one simple global stream.

**Data flow**: It receives an HTTP client and an optional cursor. It first pages through users changed since the cursor time. For each user with an ID, it builds a user-specific identities request, follows any identity pages for that user, and yields batches of identities when present. After all users on a page are processed, it stops at end_of_stream or follows the next user page.

**Call relations**: ZendeskConnector.paginate calls this for the users_identities stream. It uses ZendeskConnector._cursor_to_unix to decide which changed users to inspect and ZendeskConnector._next_page_path both for identity pagination and for moving through the incremental user feed.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).
