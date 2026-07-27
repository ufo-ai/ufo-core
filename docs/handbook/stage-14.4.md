# CRM, sales, and customer support source connectors  `stage-14.4`

This stage is the set of “adapters” that let the system bring in customer and sales data from outside services. It sits at the edge of the sync engine: these files talk to vendor APIs, meaning the web doors those services provide, then reshape the answers into standard records the rest of the code can store, search, and track.

Each connector knows the habits of one service. Attio reads companies, people, deals, tasks, notes, meetings, and call recordings. HubSpot covers a wide range, from CRM objects and deleted records to marketing assets, conversations, lists, permissions, and links between objects. Salesforce reads common business records such as accounts, contacts, opportunities, and cases, without writing anything back. Freshdesk focuses on helpdesk data and carefully follows its different page-by-page formats. Intercom turns conversations, contacts, companies, tickets, admins, and tags into one common stream. Zendesk does the same for tickets, users, organizations, help articles, and community posts. Together, they act like translators for customer-facing systems.

## Files in this stage

### CRM and sales platforms
Connectors for relationship and sales systems that sync companies, people, deals, opportunities, cases, marketing assets, analytics, and related CRM records.

### `extensions/sources/ufo_ext_sources/attio.py`

`io_transport` · `source sync`

Attio’s API does not present every kind of data in the same shape. Companies, people, and deals are read through one records endpoint. Tasks and notes use simpler workspace endpoints. Meetings and call recordings use cursor-style paging, where the server gives back a “next page” token. This file hides those differences behind one connector, AttioConnector, so the rest of the system can ask for a stream of records without knowing Attio’s quirks.

The most important work here is flattening. Attio often stores useful fields inside nested id objects and arrays of “value cells,” where the real value may be called value, email_address, phone_number, option.title, target_record_id, or something else. Without flattening, a person or company might not even have a clear top-level record_id. This connector lifts those identifiers and turns nested fields into simple values like names, emails, domains, dates, and transcript text.

The connector only reads from Attio. It never writes back. It also treats every stream as a full snapshot, meaning missing records can be deleted locally because Attio does not provide one shared “last changed” field for reliable incremental syncs. If Attio says a standard object is disabled, or an OAuth permission is missing, the connector skips that stream instead of failing the whole sync.

#### Function details

##### `_records_stream`  (lines 35–43)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates the stream description for Attio record-based objects such as companies, people, and deals. A stream description tells the sync system what the stream is called, what Attio object it reads, and which field uniquely identifies each row.

**Data flow**: It receives a friendly stream name, an Attio object slug, and whether the stream is canonical. It builds a StreamSpec with record_id as the primary key, no incremental cursor, and delete_missing turned on. The result is a ready-to-use stream definition.

**Call relations**: This helper is used while the file defines ATTIO_STREAMS. It hands the StreamSpec constructor the exact settings needed for Attio’s standard object record endpoints.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 70–71)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: Safely pulls a named id out of a nested dictionary. It is a small guard against Attio values that may or may not be shaped like an object.

**Data flow**: It receives any value and a key name. If the value is a dictionary, it returns value[key] when present. If the value is not a dictionary, it returns None.

**Call relations**: AttioConnector._value_primitive calls this when a choice or status field stores its id inside another id object. It keeps that larger conversion code simple and safe.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 80–81)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: Builds the request body used when asking Attio for one page of standard object records. It keeps the page size and offset format in one place.

**Data flow**: It receives an offset, meaning how many records have already been read. It returns a small dictionary with the fixed Attio page limit and that offset. Nothing else is changed.

**Call relations**: AttioConnector.paginate calls this each time it asks the standard records endpoint for another page of companies, people, or deals.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._value_primitive`  (lines 84–127)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: Turns one Attio value cell into the simplest useful Python value. For example, it can turn a select option into its title, an email cell into an email address, or a reference into a stable Attio-style id.

**Data flow**: It receives one dictionary representing an Attio field value. It checks the known places where Attio stores the meaningful data, such as value, option, status, email_address, phone_number, domain, currency_value, target_record_id, actor information, or location parts. It returns a simple value such as a string, number, or None when nothing useful is found.

**Call relations**: This is the core converter used by the flattening helpers. It calls _nested_id when Attio hides ids inside nested id dictionaries.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 130–145)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: Converts one Attio field cell into a single clean value where possible. It understands that a cell may be a list of value cells, one dictionary, or an already-simple value.

**Data flow**: It receives a cell from Attio. If it is a list, it converts each item to a primitive value and drops empty results. Usually it returns the first useful value, but for multi-select options it preserves the list. If it is a dictionary, it converts that dictionary. Otherwise it returns the value as-is.

**Call relations**: This helper is part of the flattening chain used by AttioConnector._flatten_values. It relies on AttioConnector._value_primitive to understand Attio’s many field shapes.


##### `AttioConnector._flatten_list_cell`  (lines 148–155)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: Converts an Attio field into a clean list of values. It is used for fields where keeping all entries matters, such as email addresses, phone numbers, domains, and categories.

**Data flow**: It receives a cell that may be a list or a single value. If it is not a list, it flattens it and wraps the result in a list unless it is empty. If it is a list, it converts each item and removes None values. The output is always a list.

**Call relations**: AttioConnector._flatten_values uses this for known multi-value fields so important extra emails, phone numbers, domains, and categories are not accidentally thrown away.


##### `AttioConnector._flatten_values`  (lines 158–192)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns Attio’s nested values object into a normal dictionary of useful fields. This is what makes records readable to the rest of the system.

**Data flow**: It receives the values dictionary from an Attio record. For each field slug, it flattens the cell into either a simple value or a list, depending on the field. It also adds convenient fields such as first_name, last_name, name, domain, category, email, and phone when those can be derived. The output is a flat dictionary.

**Call relations**: AttioConnector._flatten_record calls this after lifting record identity fields. It uses AttioConnector._flatten_cell and AttioConnector._flatten_list_cell as its smaller tools.


##### `AttioConnector._flatten_record`  (lines 195–209)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Flattens a standard Attio object record, such as a company, person, or deal. It gives the record a clear top-level record_id and turns its attributes into ordinary fields.

**Data flow**: It receives the raw record and the stream definition. It reads id information, created and updated timestamps, and any cursor field if one is configured. Then it flattens the nested values object and merges those fields into the result. The output is a clean dictionary ready for storage.

**Call relations**: AttioConnector.flatten calls this for all streams except tasks, notes, meetings, and call recordings. It delegates the attribute cleanup to AttioConnector._flatten_values.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 212–216)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a simple top-level task_id to a raw Attio task. This makes tasks easy for the sync system to identify and update consistently.

**Data flow**: It receives a raw task dictionary. It copies the task, reads id.task_id when the id is nested, or uses id directly when it is already simple, then stores that as task_id. The output is the copied task with task_id added.

**Call relations**: AttioConnector.flatten calls this whenever the current stream is tasks.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 219–223)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a simple top-level note_id to a raw Attio note. This gives the system a stable key for each note.

**Data flow**: It receives a raw note dictionary. It copies the note, extracts note_id from the nested id object when needed, and returns the note with note_id added. The original input is not directly rewritten by this function.

**Call relations**: AttioConnector.flatten calls this whenever the current stream is notes.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 226–230)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a simple top-level meeting_id to a raw Attio meeting. This makes meeting rows match the primary key expected by the stream definition.

**Data flow**: It receives a raw meeting dictionary. It copies the meeting, extracts id.meeting_id or uses a simple string id, and returns the meeting with meeting_id added.

**Call relations**: AttioConnector.flatten calls this whenever the current stream is meetings.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 233–254)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Cleans up a call recording row by adding call_recording_id and combining transcript segments into readable transcript text. It also uses the web URL as a fallback recording URL when needed.

**Data flow**: It receives a raw call recording dictionary. It copies the record, extracts its call recording id, fills recording_url from web_url if necessary, and walks transcript segments to produce lines like “Speaker: words spoken.” It returns the enriched recording dictionary.

**Call relations**: AttioConnector.flatten calls this for the call_recordings stream. The transcript it formats may have been fetched earlier by AttioConnector._paginate_call_recordings.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 256–265)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening routine for the kind of Attio record being synced. It is the public cleanup step that normalizes raw API rows before the rest of the system sees them.

**Data flow**: It receives a raw record and its stream definition. It checks the stream name and sends the record to the matching specialized flattener for tasks, notes, meetings, call recordings, or standard records. It returns the cleaned dictionary from that helper.

**Call relations**: The base sync machinery calls this after pages of Attio data have been fetched. It hands off to AttioConnector._flatten_task, _flatten_note, _flatten_meeting, _flatten_call_recording, or _flatten_record depending on the stream.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 267–321)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches all pages for one Attio stream, using the correct Attio endpoint and paging style for that stream. This is the main reading loop for the connector.

**Data flow**: It receives an HTTP client, a stream definition, and an unused cursor value. For tasks and notes, it reads offset-based GET pages. For meetings, it reads cursor-based pages. For call recordings, it first walks meetings and then recordings. For standard objects, it posts query bodies with increasing offsets. It yields lists of raw records page by page, and may raise StreamSkipped when Attio says a stream cannot be read.

**Call relations**: The sync framework calls this to collect records. It calls the smaller pagination helpers, builds standard object query bodies with AttioConnector._build_query_body, and uses the error-checking helpers to decide when to skip instead of fail.

*Call graph*: calls 8 internal fn (__init__, _build_query_body, _is_object_disabled, _is_scope_unauthorized, _paginate_call_recordings, _paginate_cursor, _paginate_simple, _scope_skip_reason).


##### `AttioConnector._paginate_simple`  (lines 323–330)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple Attio endpoints that use limit and offset paging. This is used for tasks and notes.

**Data flow**: It receives an HTTP client, an endpoint path, and a page size. It asks the shared REST connector helper for pages whose records live under the data field. It yields each page of records unchanged.

**Call relations**: AttioConnector.paginate calls this for the tasks and notes streams. It relies on the inherited offset paging helper supplied by RestConnector.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._paginate_cursor`  (lines 332–350)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use cursor paging. A cursor is a token from the server that means “start the next request after this point.”

**Data flow**: It receives an HTTP client, endpoint path, page size, and optional query parameters. It asks the shared REST connector helper to read records from data and find the next cursor at pagination.next_cursor. It yields each page until no next cursor remains.

**Call relations**: AttioConnector.paginate uses this for meetings. AttioConnector._paginate_call_recordings also uses it to walk meetings and then each meeting’s recordings.

*Call graph*: called by 2 (_paginate_call_recordings, paginate).


##### `AttioConnector._paginate_call_recordings`  (lines 352–388)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the call recordings stream by first finding meetings, then finding each meeting’s recordings, and finally fetching each recording’s transcript when available. This matters because Attio exposes recordings underneath meetings rather than as one flat list.

**Data flow**: It receives an HTTP client. It pages through meetings, extracts each meeting id, title, start and end time, and duration, then pages through that meeting’s call recordings. For each recording, it adds parent meeting context and, when there is a recording id, requests the transcript and attaches transcript data. It yields pages of enriched recording rows.

**Call relations**: AttioConnector.paginate calls this for the call_recordings stream. Inside, it calls AttioConnector._paginate_cursor for both meetings and recordings, _meeting_id and _call_recording_id to find ids, _datetime_of and _duration_seconds for timing fields, and _fetch_transcript for transcript details.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (paginate).


##### `AttioConnector._fetch_transcript`  (lines 390–402)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: Fetches the transcript for one call recording. If Attio says the transcript is missing or not ready, it quietly returns None instead of treating that as a fatal error.

**Data flow**: It receives an HTTP client, a meeting id, and a recording id. It builds the transcript endpoint path and sends a GET request. If Attio returns 404 or 409, it returns None. Otherwise it returns the data object when it is a dictionary, or None if the response shape is unexpected.

**Call relations**: AttioConnector._paginate_call_recordings calls this after it finds a recording id. The returned transcript is later formatted by AttioConnector._flatten_call_recording.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 405–409)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a meeting id from Attio’s meeting shape. It protects the rest of the code from having to care whether the id is nested or already a string.

**Data flow**: It receives a meeting dictionary. If meeting.id is a dictionary, it returns id.meeting_id. If meeting.id is a string, it returns that string. Otherwise it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this before listing recordings for a meeting. If no id can be found, that meeting is skipped.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 412–416)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a call recording id from Attio’s recording shape. This gives the connector the id needed to request the transcript endpoint.

**Data flow**: It receives a recording dictionary. If recording.id is a dictionary, it returns id.call_recording_id. If recording.id is a string, it returns that string. Otherwise it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this for each recording. When it finds an id, the connector can pass that id to AttioConnector._fetch_transcript.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 419–423)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: Pulls a usable date or datetime string out of Attio’s meeting time object. Attio may store timed meetings as datetime plus timezone, or all-day meetings as date.

**Data flow**: It receives any value. If the value is not a dictionary, it returns None. If it is a dictionary, it returns the datetime field first, or the date field if datetime is missing.

**Call relations**: AttioConnector._paginate_call_recordings calls this for meeting start and end fields before adding that context to recordings.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 426–436)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: Computes an approximate meeting duration in seconds from start and end time strings. It returns None when the inputs are missing or cannot be understood as dates.

**Data flow**: It receives start and end strings. If either is missing, it returns None. Otherwise it parses both ISO 8601 timestamps, treats trailing Z as UTC, subtracts start from end, and returns the number of seconds, never below zero. If parsing fails, it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this after extracting meeting start and end values. It uses datetime.fromisoformat from Python’s standard datetime library to parse the strings.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 439–448)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Checks whether an Attio error means a standard object, such as deals or companies, is disabled in the workspace. This lets the sync skip that stream instead of crashing.

**Data flow**: It receives an HTTP error. If the status code is not 400, it returns False. If the response body cannot be read as JSON, it returns False. Otherwise it returns True only when the JSON code is standard_object_disabled.

**Call relations**: AttioConnector.paginate calls this when a standard object records query fails. If it returns True, paginate raises StreamSkipped with a clear reason.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._is_scope_unauthorized`  (lines 451–460)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Checks whether an Attio error means the OAuth connection is missing a required permission. OAuth is the permission grant that lets this app read data from Attio on a user’s behalf.

**Data flow**: It receives an HTTP error. If the status code is not 403, it returns False. If the body is not valid JSON, it returns False. Otherwise it returns True only when the JSON code is unauthorized.

**Call relations**: AttioConnector.paginate calls this around meetings and call recordings. If a permission is missing, paginate raises StreamSkipped rather than stopping the whole sync.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._scope_skip_reason`  (lines 463–469)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a human-readable explanation for skipping a stream because an OAuth permission is missing. It includes Attio’s message when one is available.

**Data flow**: It receives an HTTP error. It tries to read the JSON response and extract a message. It returns a sentence explaining that the OAuth grant is missing a required scope, falling back to a generic note if no message is available.

**Call relations**: AttioConnector.paginate calls this after AttioConnector._is_scope_unauthorized confirms the kind of error. The returned text is passed into StreamSkipped so logs or callers can understand what happened.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/hubspot.py`

`io_transport` · `sync run data fetching`

HubSpot has many APIs, and they do not all return data in the same shape. This file is the adapter that makes HubSpot look like one readable source to the rest of the project. It defines every HubSpot “stream,” meaning a named kind of data such as contacts, deals, forms, campaigns, or associations. For normal CRM objects it asks HubSpot what properties exist, searches records in update-time order, and uses a cursor so later syncs can resume from the last seen timestamp. Because HubSpot’s boundary filter is inclusive, it remembers IDs at the cursor boundary so the same record is not emitted twice. It also does a separate sweep for archived records, so deletions become tombstones instead of silently disappearing. For HubSpot areas that are not exposed through CRM search, it uses each product API’s own listing pattern and normalizes the result. “Normalize” here means flattening nested fields so callers see simple top-level keys. Some HubSpot accounts cannot access certain objects because of subscription tier or OAuth permission scope. In those cases this connector marks just that stream as skipped rather than failing the whole sync. Like a universal travel plug, this file converts many HubSpot outlet shapes into one shape the rest of the system can use.

#### Function details

##### `_normalize_epoch_millis`  (lines 248–255)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Converts HubSpot timestamps written as milliseconds since 1970 into readable ISO date strings. It leaves booleans and already-readable values alone so accidental conversions do not corrupt data.

**Data flow**: It receives any value. If the value is a number, or a string containing only digits, it treats it as milliseconds and turns it into a UTC timestamp string; otherwise it returns the original value unchanged.

**Call relations**: The HubSpotConnector uses this when flattening product API rows and when preparing analytics view rows, because those APIs sometimes use raw millisecond timestamps instead of normal date strings.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 258–267)

```
def _stream(name: str, *, object_type: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates the standard description for a HubSpot CRM object stream, such as contacts or deals. This tells the sync engine which ID, cursor, and timestamp fields to use.

**Data flow**: It receives a stream name, HubSpot object type, and whether the stream is canonical. It returns a StreamSpec configured around HubSpot CRM search fields like id and hs_lastmodifieddate.

**Call relations**: It is used at module load time to define many CRM object stream constants that HubSpotConnector later exposes through its streams list.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 270–289)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='createdAt', updated_at_field: str | None='updatedAt', pagi
```

**Purpose**: Creates a stream description for HubSpot product APIs that are not normal CRM search objects. These streams often have different IDs, timestamps, or pagination rules.

**Data flow**: It receives naming, key, timestamp, and optional pagination details. It returns a StreamSpec marked non-canonical so the rest of the system knows it is a supporting HubSpot surface.

**Call relations**: It is used while this file defines streams such as owners, workflows, forms, files, analytics reports, and association data.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 292–304)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Builds the common pagination recipe for HubSpot GET list endpoints. Pagination means repeatedly asking for the next page until there are no more results.

**Data flow**: It receives an API path. It returns a Pagination object that knows to read records from results, find the next cursor at paging.next.after, and send that cursor back as after.

**Call relations**: Product stream definitions use this helper so simple list-style HubSpot APIs can be paged by the shared RestConnector strategy instead of custom code.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 307–317)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Creates a stream description for a relationship table, such as deal-to-contact links. These are synthetic rows made from HubSpot association data, not standalone HubSpot objects.

**Data flow**: It receives a stream name and parent object type. It returns a StreamSpec with no cursor because HubSpot does not provide modification times for these relationship rows.

**Call relations**: It is used at module load time to define simple junction streams, which HubSpotConnector later fills by walking parent objects with association data included.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 622–652)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the JSON request body used for HubSpot CRM search. It asks HubSpot for all known properties, sorted by the cursor field, optionally starting from a saved cursor.

**Data flow**: It receives a stream, property names, an optional cursor, and an optional page cursor called after. It produces a dictionary that HubSpot’s search endpoint accepts, including filters and paging when needed.

**Call relations**: The main CRM pagination path and the custom object record pagination path call this before making each search request.

*Call graph*: called by 2 (_paginate_custom_object_records, _paginate_unchecked).


##### `HubSpotConnector._flatten`  (lines 655–666)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a normal HubSpot CRM record into a simpler flat dictionary. HubSpot wraps most useful fields inside properties, and this lifts them to the top level.

**Data flow**: It receives one HubSpot record. It copies id, createdAt, updatedAt, archived, then merges in every key from properties, returning the combined row.

**Call relations**: The public flatten method calls this for ordinary CRM streams after records have been fetched.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 669–692)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes rows from HubSpot product APIs, whose shapes vary more than CRM objects. It makes IDs and form-style name/value fields easy for the rest of the system to read.

**Data flow**: It receives one product API record and its stream. It copies the record, fills id from objectId when needed, lifts properties and values entries to top-level keys, normalizes a few timestamp fields, and returns the flat row.

**Call relations**: The public flatten method sends product API streams here. This helper also uses _normalize_epoch_millis for APIs that return timestamps as milliseconds.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 694–701)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening rule for each HubSpot stream. It is the final cleanup step before fetched data becomes a standard record.

**Data flow**: It receives a raw record and stream. Junction and custom object rows pass through unchanged, product API rows go through product normalization, and ordinary CRM rows go through CRM flattening.

**Call relations**: The broader connector framework calls this after pagination yields raw HubSpot records; it delegates to _flatten or _flatten_product_api as appropriate.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector._list_properties`  (lines 703–711)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which properties exist for a CRM object type. This avoids hard-coding fields and lets the sync capture custom account fields.

**Data flow**: It receives an HTTP client and object type. It calls HubSpot’s properties endpoint, extracts valid property names from the response, and returns them as a list.

**Call relations**: _paginate_unchecked calls this before CRM search so _build_search_body can request every available field.

*Call graph*: called by 1 (_paginate_unchecked).


##### `HubSpotConnector.paginate`  (lines 713–726)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the safe public pagination entry for a stream. It fetches pages, but turns permission-related HubSpot failures into a skipped stream instead of a failed sync.

**Data flow**: It receives an HTTP client, stream, and optional cursor. It yields pages from _paginate_unchecked; if HubSpot returns unauthorized or unavailable-stream errors, it raises StreamSkipped with a readable reason.

**Call relations**: The sync runner calls this to read a stream. It relies on _is_stream_unavailable and _stream_skip_reason to distinguish expected access limits from real errors.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 728–780)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Routes each stream to the correct fetching strategy and implements the normal CRM search loop. It is called “unchecked” because permission handling is wrapped by paginate.

**Data flow**: It receives a stream and optional cursor. Depending on the stream, it delegates to strategy pagination, junction fetching, custom object fetching, product API fetching, or CRM search; CRM search yields result pages and then deletion tombstones.

**Call relations**: paginate calls this for the actual work. It hands off to many stream-specific helpers and uses _build_search_body, _list_properties, and _paginate_archived_ids for ordinary CRM objects.

*Call graph*: calls 6 internal fn (_build_search_body, _list_properties, _paginate_archived_ids, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._is_stream_unavailable`  (lines 783–806)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether a HubSpot error means this account simply cannot access one stream. This prevents expected permission gaps from breaking the entire run.

**Data flow**: It receives an HTTP status error. It checks for a 403 response and scans the JSON message for permission or scope wording, returning true only for those cases.

**Call relations**: paginate, archived sweeps, and custom archived sweeps use this to convert account capability limits into skips or quiet returns.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 809–818)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds the human-readable explanation recorded when a HubSpot stream is skipped. It includes HubSpot’s own message when available.

**Data flow**: It receives the stream name and HTTP error. It reads the response message if possible and returns a sentence explaining that the stream is unavailable for this account.

**Call relations**: paginate calls this right before raising StreamSkipped, so the sync result can explain the skipped stream clearly.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 820–855)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived CRM object IDs so deleted HubSpot records become tombstones. Without this, the local store could keep records that HubSpot has removed.

**Data flow**: It receives a client and stream. It walks HubSpot’s archived list endpoint page by page, extracts record IDs, and yields StreamPage objects containing deletes.

**Call relations**: _paginate_unchecked calls this after normal CRM search. It uses _is_archived_sweep_unsupported and _is_stream_unavailable to quietly stop when HubSpot cannot provide archived IDs.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 857–951)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Dispatches non-CRM streams to the special code needed for each HubSpot product API. Product APIs cover areas like forms, campaigns, analytics, emails, conversations, and associations.

**Data flow**: It receives a stream and cursor. It chooses a stream-specific helper when needed, uses generic collection pagination for simple paths, or raises an error if a declared stream has no known fetch path.

**Call relations**: _paginate_unchecked calls this whenever a stream belongs to the product API group. It is the switchboard for many specialized pagination helpers.

*Call graph*: calls 21 internal fn (_paginate_analytics_reports, _paginate_analytics_views, _paginate_association_labels, _paginate_associations, _paginate_campaign_assets, _paginate_consent_states, _paginate_conversation_messages, _paginate_email_events, _paginate_event_occurrences, _paginate_event_types (+11 more)); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._paginate_get_collection`  (lines 953–981)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks a standard HubSpot list endpoint that returns results and a next cursor. It is the reusable page turner for many simple product APIs.

**Data flow**: It receives a client, path, limit, and optional extra query parameters. It repeatedly calls the endpoint, normalizes objectId into id when needed, yields non-empty result pages, and stops when no next cursor appears.

**Call relations**: Many product helpers call this, including owner teams, campaign assets, form submissions, conversations, sequences, and generic product stream fetching.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_product_api, _paginate_sequences, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 983–1018)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Fetches records for all custom HubSpot object types defined in the account. Custom objects are account-specific, so the connector must discover their schemas first.

**Data flow**: It gets custom object schemas, builds a search stream for each schema, collects property names, yields matching records, and then yields tombstones for archived custom records.

**Call relations**: _paginate_unchecked calls this for the custom_objects stream. It coordinates schema discovery, record pagination, row shaping, and archived-ID sweeping.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 1020–1022)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Reads the custom object definitions available in the HubSpot account. A schema describes what a custom object is called and which properties it has.

**Data flow**: It receives an HTTP client, calls the custom object schema endpoint, filters the results to dictionaries, and returns the list.

**Call relations**: Custom object pagination calls this before fetching records. Association discovery also calls it so custom object types can be included in relationship scans.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 1025–1030)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Finds the best usable object type identifier inside a custom object schema. HubSpot may expose this under different field names.

**Data flow**: It receives a schema dictionary. It checks objectTypeId, fullyQualifiedName, and name in order, returning the first non-empty string or None.

**Call relations**: Custom object pagination, custom row shaping, and association object-type discovery call this whenever they need the API identifier for a schema.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1033–1048)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Collects all property names worth requesting for a custom object. It includes normal properties and display properties used for titles.

**Data flow**: It receives a schema. It walks declared properties, the primary display property, and secondary display properties, returning a de-duplicated list of names.

**Call relations**: _paginate_custom_objects calls this before searching custom object records so HubSpot returns the fields needed for useful rows.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1050–1085)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Searches records for one custom object type. It uses cursor-based incremental syncing and avoids duplicate records at the cursor boundary.

**Data flow**: It receives a custom object stream, schema, property list, and cursor. It posts search requests page by page, filters duplicate boundary records, converts each record with _custom_object_row, and yields pages.

**Call relations**: _paginate_custom_objects calls this once per discovered schema. It uses _build_search_body to create HubSpot search requests and _custom_object_row to produce stable output rows.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1087–1126)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Turns one custom object record into a readable, stable row. It adds object labels, title fields, and a namespaced ID so records from different custom object types cannot collide.

**Data flow**: It receives a raw record and schema. It extracts properties, display labels, and timestamps, then returns a combined row with id formatted as object_type_id:record_id, or None if required IDs are missing.

**Call relations**: _paginate_custom_object_records calls this for each custom record after HubSpot search returns it. It uses _schema_object_type_id to identify the object type.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1128–1158)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived records for one custom object type and emits deletion tombstones. This keeps local custom object data in step with HubSpot deletions.

**Data flow**: It receives a client and custom object type ID. It walks the archived list endpoint, prefixes each deleted ID with the object type, and yields StreamPage delete batches.

**Call relations**: _paginate_custom_objects calls this after each custom object’s active records. It uses the same unsupported and unavailable checks as the normal archived sweep.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1160–1179)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a unique list of owner teams from owner records. HubSpot exposes teams nested under owners rather than as a simple standalone list here.

**Data flow**: It reads owners through _paginate_get_collection, inspects each owner’s teams, de-duplicates by team ID, and yields one page of team rows if any exist.

**Call relations**: _paginate_product_api calls this for the owner_teams stream. It depends on the generic collection paginator for the underlying owners request.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_lists`  (lines 1181–1210)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches HubSpot contact or object lists from the list search API. It also flattens extra list properties into the main row.

**Data flow**: It posts search requests with an offset, converts listId into id, merges additionalProperties when present, yields pages, and advances until HubSpot says there are no more lists.

**Call relations**: _paginate_product_api calls this for lists. _paginate_list_memberships also calls it first so memberships can be fetched list by list.

*Call graph*: called by 2 (_paginate_list_memberships, _paginate_product_api).


##### `HubSpotConnector._paginate_site_search`  (lines 1212–1232)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches CMS search results for a specific content type, such as knowledge articles. It uses offset-style paging rather than cursor paging.

**Data flow**: It receives a content type. It repeatedly calls HubSpot site search with limit and offset, yields dictionary results, and stops when the next offset reaches the reported total.

**Call relations**: _paginate_product_api calls this for knowledge_articles, because that stream uses HubSpot’s site search endpoint.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1234–1258)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches marketing assets attached to campaigns. It first lists campaigns, then asks for each supported asset type under each campaign.

**Data flow**: It reads campaigns, extracts campaign IDs and names, loops over known asset types, and yields pages produced by _paginate_campaign_asset_type.

**Call relations**: _paginate_product_api calls this for the campaign_assets stream. It uses _paginate_get_collection for campaigns and delegates per asset type to a focused helper.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1260–1295)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one kind of asset for one campaign and turns each asset into a uniquely identified row. Missing or forbidden asset endpoints are treated as absent, not fatal.

**Data flow**: It receives campaign details and an asset type. It lists assets, builds rows with campaign context, asset kind, stable IDs, and metrics, then yields pages; 403 or 404 responses simply end that asset type.

**Call relations**: _paginate_campaign_assets calls this for every campaign and known asset type. It relies on _paginate_get_collection for the actual paging.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1297–1303)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields HubSpot analytics views, which are saved filters or views used by reports. It wraps the row-building helper in pagination form.

**Data flow**: It asks _analytics_view_rows for all view rows. If any rows exist, it yields them as one page.

**Call relations**: _paginate_product_api calls this for analytics_views. _analytics_view_rows does the actual HTTP request and normalization.

*Call graph*: calls 1 internal fn (_analytics_view_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_view_rows`  (lines 1305–1336)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Reads and normalizes analytics view definitions. It gives each view an ID, name, kind, filters, and normalized timestamps.

**Data flow**: It calls the analytics views endpoint, accepts either a list response or a results wrapper, skips invalid rows, fills missing names from other label fields, normalizes created time, and returns rows.

**Call relations**: Both analytics view syncing and analytics report syncing call this, because reports can be requested for each analytics view.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1338–1368)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds many analytics report queries across report subjects, time periods, and analytics views. This turns HubSpot’s report matrix into rows the sync can store.

**Data flow**: It calculates a date window, loads analytics views, creates an all-views filter plus per-view filters, loops through report families, subjects, time periods, and filters, and yields rows from each query.

**Call relations**: _paginate_product_api calls this for analytics_reports. It coordinates _analytics_report_window, _analytics_view_rows, and _paginate_analytics_report_query.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_report_window`  (lines 1371–1372)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range used for analytics report pulls. It starts from a fixed early date and ends today in UTC.

**Data flow**: It takes no input. It returns a start date string and the current UTC date string in HubSpot’s YYYYMMDD format.

**Call relations**: _paginate_analytics_reports calls this once before issuing its report queries.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1374–1425)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Runs one analytics report query and pages through its breakdown rows. It tolerates report combinations HubSpot does not support.

**Data flow**: It receives report family, subject, time period, optional view filter, date range, and client. It calls the report endpoint with offset paging, converts each response with _analytics_report_rows, yields rows, and stops at the end or on 400/404.

**Call relations**: _paginate_analytics_reports calls this inside its nested report loops. It delegates row shaping to _analytics_report_rows.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1428–1503)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Turns one HubSpot analytics report response into stable rows. It creates both overall totals and per-breakdown records.

**Data flow**: It receives the raw report response plus context such as subject, time period, view, dates, and offset. It builds IDs, names, metadata, metrics, and formatted dates, returning a list of rows.

**Call relations**: _paginate_analytics_report_query calls this after each report response. It uses the connector’s report ID and date helpers to create consistent values.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1506–1510)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Creates a safe, stable ID for an analytics report row. Stable IDs let repeated syncs update the same row instead of creating duplicates.

**Data flow**: It receives any number of ID parts. It converts them to strings, replaces characters that would be awkward inside an ID, joins them, and prefixes analytics_report:.

**Call relations**: _analytics_report_rows uses this when creating totals and breakdown rows for report output.


##### `HubSpotConnector._analytics_report_date`  (lines 1513–1514)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Reformats HubSpot report dates into a more standard readable form. It changes YYYYMMDD into YYYY-MM-DD.

**Data flow**: It receives an eight-character date string. It slices it into year, month, and day parts and returns them joined with hyphens.

**Call relations**: _analytics_report_rows uses this to store report start and end dates in a consistent format.


##### `HubSpotConnector._paginate_event_types`  (lines 1516–1535)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches HubSpot event type definitions. These describe kinds of events before individual event occurrences are read.

**Data flow**: It calls the event types endpoint, accepts either a list or results wrapper, chooses a usable ID from several possible fields or the row index, and yields the rows.

**Call relations**: _paginate_product_api calls this for the event_types stream.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1537–1559)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches individual HubSpot event occurrences, optionally starting after a cursor. It creates IDs when HubSpot does not provide them.

**Data flow**: It builds query parameters from the cursor, calls the events endpoint, skips invalid rows, fills missing IDs with _synthetic_event_id, and yields one page if rows exist.

**Call relations**: _paginate_product_api calls this for event_occurrences. It uses _synthetic_event_id so every emitted event can be upserted reliably.

*Call graph*: calls 1 internal fn (_synthetic_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._synthetic_event_id`  (lines 1562–1572)

```
def _synthetic_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Creates a fallback ID for an event occurrence. It combines important event fields with a short content hash to make collisions unlikely.

**Data flow**: It receives an event row and its index. It joins event type, object type, object ID, occurrence time or index, and a stable hash, replacing colons inside parts.

**Call relations**: _paginate_event_occurrences calls this only when HubSpot does not include an event ID.

*Call graph*: called by 1 (_paginate_event_occurrences).


##### `HubSpotConnector._paginate_email_events`  (lines 1574–1602)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches public email events such as sends, opens, or clicks. It supports incremental fetching by converting the cursor into HubSpot’s start timestamp format.

**Data flow**: It receives a cursor, converts it if possible, pages through email events using offset tokens, fills missing IDs with _synthetic_email_event_id, yields pages, and stops when HubSpot reports no more data.

**Call relations**: _paginate_product_api calls this for email_events. It relies on _email_event_start_timestamp and _synthetic_email_event_id.

*Call graph*: calls 2 internal fn (_email_event_start_timestamp, _synthetic_email_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1605–1614)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts an email event cursor into the millisecond timestamp HubSpot expects. It accepts either an existing numeric timestamp or an ISO date string.

**Data flow**: It receives an optional cursor string. It returns None for no cursor or unparseable text, returns the integer directly for numeric text, or parses an ISO timestamp and converts it to milliseconds.

**Call relations**: _paginate_email_events calls this before requesting email events.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._synthetic_email_event_id`  (lines 1617–1627)

```
def _synthetic_email_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Creates a fallback ID for an email event when HubSpot does not provide one. It uses event details plus a short hash of the whole payload.

**Data flow**: It receives an email event row and index. It joins created time, recipient, event type, campaign ID, and stable payload hash into one colon-safe string.

**Call relations**: _paginate_email_events calls this for email rows without IDs.

*Call graph*: called by 1 (_paginate_email_events).


##### `HubSpotConnector._stable_payload_hash`  (lines 1630–1632)

```
def _stable_payload_hash(row: dict[str, Any]) -> str
```

**Purpose**: Creates a repeatable short fingerprint for a row. This is useful when HubSpot omits IDs but the same payload should produce the same fallback ID.

**Data flow**: It receives a dictionary, serializes it to sorted JSON, hashes it with SHA-256, and returns the first 16 hex characters.

**Call relations**: Synthetic ID helpers use this as the last piece of their generated IDs, even though the call graph records no direct callers here.

*Call graph*: 2 external calls (sha256, dumps).


##### `HubSpotConnector._paginate_association_labels`  (lines 1634–1650)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches labels that describe relationships between HubSpot object types, such as what kind of link connects one record to another.

**Data flow**: It walks object-type pairs that have labels, converts each raw label into a standard row, and yields pages of label rows.

**Call relations**: _paginate_product_api calls this for association_labels. It depends on _association_pairs_with_labels and _association_label_row.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_associations`  (lines 1652–1667)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches actual relationship rows between HubSpot records across object-type pairs. This answers questions like which contacts are associated with which deals.

**Data flow**: It finds object-type pairs with labels, pages through source object IDs, sends batch association reads, and yields flattened association rows.

**Call relations**: _paginate_product_api calls this for associations. It coordinates pair discovery, ID paging, and _paginate_association_batch.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_association_batch`  (lines 1669–1696)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads associations for a batch of source records, including follow-up pages for records with many associations. Optional unsupported pairs are skipped.

**Data flow**: It receives source and target object types plus input IDs. It posts a batch read, converts results with _association_rows, yields rows, then replaces pending inputs with any per-record next-page requests.

**Call relations**: _paginate_associations calls this after collecting source IDs. It uses _next_association_inputs to continue paged association results.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1699–1713)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Finds which association batch inputs need another page. Some individual source records can have more associated targets than fit in one batch response.

**Data flow**: It receives a batch response. It inspects each result for a from ID and paging.next.after cursor, returning new input dictionaries with id and after.

**Call relations**: _paginate_association_batch calls this after each batch response to decide whether to loop again.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1715–1728)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Discovers object-type pairs that have association labels. Only pairs with labels are useful for the label and association scans.

**Data flow**: It gets all candidate object types, checks every from/to combination for labels, and yields the pair plus its labels when any exist.

**Call relations**: Both association label pagination and full association pagination call this. It uses _association_object_types and _association_labels_for_pair.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1730–1743)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the list of HubSpot object types to check for associations. It starts with known standard objects and adds custom objects if the account exposes them.

**Data flow**: It begins with a fixed list, tries to fetch custom schemas, extracts custom object IDs, appends new ones, and returns the combined list.

**Call relations**: _association_pairs_with_labels calls this before scanning from/to pairs. It uses custom schema helpers and optional-pair error handling.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1745–1761)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches relationship labels for one source object type and one target object type. Unsupported pairs simply return no labels.

**Data flow**: It receives from and to object types, calls HubSpot’s labels endpoint, returns valid result dictionaries, or returns an empty list for optional unavailable errors.

**Call relations**: _association_pairs_with_labels calls this for each candidate pair.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1764–1781)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Turns one raw association label into a standard row with a stable ID and clear from/to fields.

**Data flow**: It receives a label plus source and target object types. It extracts type ID, category, and display label, then returns a row combining the original label with normalized fields.

**Call relations**: _paginate_association_labels calls this for every label returned by pair discovery.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1783–1795)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Pages through CRM objects and yields only their IDs. This is a lightweight way to feed other APIs that need record IDs.

**Data flow**: It receives an object type. It calls _paginate_crm_object_pages asking only for hs_object_id, extracts IDs from each page, and yields lists of strings.

**Call relations**: Association syncing and sequence enrollment syncing call this when they need contact or source object IDs.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1797–1827)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks a CRM object list endpoint with selected properties. This is used when a full search stream is unnecessary and simple pages are enough.

**Data flow**: It receives an object type and property names. It calls the list endpoint with limit, properties, and after cursor, yields valid record dictionaries, and stops when no next cursor exists.

**Call relations**: _paginate_crm_object_id_pages and _paginate_contact_identity_pages call this. It treats optional unavailable object types as empty.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1830–1864)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Converts a batch association response into flat relationship rows. It expands multiple association types between the same two records into separate rows.

**Data flow**: It receives raw batch data and source/target object types. It walks each from record and its to records, skips incomplete entries, and creates rows through _association_row.

**Call relations**: _paginate_association_batch calls this after each batch read response.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1867–1894)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one flat association row between two HubSpot records. The row includes IDs, object types, label details, and a stable compound primary key.

**Data flow**: It receives association type metadata, source and target types, source and target IDs, and a fallback index. It extracts category, label, and type ID, then returns one relationship dictionary.

**Call relations**: _association_rows uses this as its row factory, although the call graph lists no direct caller.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1897–1900)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether an error means an optional HubSpot pair or endpoint is simply not available. This lets broad discovery scans continue across unsupported combinations.

**Data flow**: It receives an HTTP error. It returns true for 400 or 404 responses, or for permission-style stream unavailability, and false otherwise.

**Call relations**: Many helpers use this while scanning associations, memberships, consent, sequences, and CRM object pages so one unsupported branch does not stop the whole stream.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1902–1916)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the records that belong to each HubSpot list. It turns list membership into its own stream of rows.

**Data flow**: It first pages through lists, extracts each list ID, then calls _paginate_memberships_for_list and yields those membership pages.

**Call relations**: _paginate_product_api calls this for list_memberships. It depends on _paginate_lists to discover the parent lists.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1918–1961)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches memberships for one HubSpot list and adds list context to every row. This makes each membership understandable without looking up the list separately.

**Data flow**: It receives a list record and list ID. It pages through the memberships endpoint, builds rows with id list_id:record_id plus list name and object type, and yields pages.

**Call relations**: _paginate_list_memberships calls this for each list. It skips optional unavailable membership endpoints.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1963–1976)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches HubSpot communication subscription definitions. These describe the categories people can subscribe or unsubscribe from.

**Data flow**: It calls the definitions endpoint, accepts either results or subscriptionDefinitions, chooses an ID from several possible fields or the index, and yields rows.

**Call relations**: _paginate_product_api calls this for subscription_definitions.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_consent_states`  (lines 1978–1991)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches email consent status for contacts. It combines per-subscription status and global unsubscribe-all status into one stream.

**Data flow**: It pages through contact identities, uses each contact email to request subscription statuses and unsubscribe-all statuses, combines the resulting rows, and yields pages.

**Call relations**: _paginate_product_api calls this for consent_states. It relies on contact identity pages plus _consent_status_rows and _unsubscribe_all_rows.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1993–2009)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches contact IDs and email addresses for later consent lookups. It normalizes email whether HubSpot returns it at the top level or inside properties.

**Data flow**: It calls _paginate_crm_object_pages for contacts with the email property, copies each record, fills an email field, and yields pages.

**Call relations**: _paginate_consent_states calls this before making communication-preference requests.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 2011–2032)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches subscription-specific consent rows for one email address. It returns no rows when the endpoint is optional and unavailable.

**Data flow**: It receives a contact and email, URL-escapes the email, calls the statuses endpoint, converts each result with _consent_row, and returns the list.

**Call relations**: _paginate_consent_states calls this for each contact email. It uses _is_optional_pair_unavailable for tolerable errors.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 2034–2058)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches global unsubscribe-all consent rows for one email address. This captures whether a contact opted out of all email communication.

**Data flow**: It receives a contact and email, URL-escapes the email, calls the unsubscribe-all endpoint, converts each result with _consent_row, and returns the list.

**Call relations**: _paginate_consent_states calls this alongside _consent_status_rows for each contact email.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 2061–2091)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Turns one raw communication-preference status into a consistent consent row. It adds contact identity, subject email, purpose, status, legal basis, and timestamps.

**Data flow**: It receives a raw status row, contact, email, and status kind. It chooses ID parts from subscription and business unit fields, fills normalized consent fields, and returns the row.

**Call relations**: _consent_status_rows and _unsubscribe_all_rows call this to shape their API responses the same way.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2093–2120)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches sales sequences for each HubSpot user discovered through owners. Sequences are requested per user rather than all at once.

**Data flow**: It gets user rows from owners, calls the sequences endpoint with each userId, adds owner context to sequence rows, yields pages, and skips optional unavailable responses.

**Call relations**: _paginate_product_api calls this for sequences. It uses _sequence_user_rows to discover users and _paginate_get_collection to page sequence results.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_user_rows`  (lines 2122–2145)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a unique list of sequence user IDs from HubSpot owners. It keeps owner ID and email as context.

**Data flow**: It pages through owners, extracts userId values, de-duplicates them, records owner_id and owner_email, and yields one page if any users are found.

**Call relations**: _paginate_sequences calls this before requesting per-user sequences.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2147–2165)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches sequence enrollment information for contacts. An enrollment shows whether or how a contact is in a sales sequence.

**Data flow**: It pages through contact IDs, calls the enrollment endpoint for each contact, converts responses with _sequence_enrollment_rows, accumulates rows, and yields pages.

**Call relations**: _paginate_product_api calls this for sequence_enrollments. It uses _paginate_crm_object_id_pages to find contacts and skips optional unavailable contact responses.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2168–2182)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes sequence enrollment responses for one contact. It supports both list-style responses and single-object responses.

**Data flow**: It receives raw data and a contact ID. It treats results as rows when present or wraps the whole response as one row, fills an ID and contact_id, and returns valid rows.

**Call relations**: _paginate_sequence_enrollments calls this after each contact enrollment request.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2184–2213)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches submissions for every HubSpot form. It first discovers forms, then reads submissions under each form.

**Data flow**: It pages through forms, extracts form IDs, pages through each form’s submissions with a smaller limit, builds stable row IDs from conversionId or form/time/index, adds form context, and yields pages.

**Call relations**: _paginate_product_api calls this for form_submissions. It uses _paginate_get_collection for both forms and submissions.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2215–2230)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages inside HubSpot conversation threads. Threads and messages are separate API levels, so this walks both.

**Data flow**: It pages through conversation threads, extracts each thread ID, pages through that thread’s messages, adds thread_id to every message, and yields pages.

**Call relations**: _paginate_product_api calls this for conversation_messages. It uses _paginate_get_collection for both thread and message listing.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipelines`  (lines 2232–2239)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches deal and ticket pipelines. Pipelines describe the stages records move through, like a sales process or support workflow.

**Data flow**: It loops over supported pipeline object types, builds rows for each object type, and yields non-empty pages.

**Call relations**: _paginate_product_api calls this for pipelines. It delegates per-object work to _pipeline_rows_for_object_type.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2241–2291)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches stages inside deal and ticket pipelines. It adds status, ordering, closure, and pipeline context to each stage row.

**Data flow**: It loops over pipeline object types, reads raw pipelines, walks their stages, builds compound IDs, extracts metadata such as probability and closed state, and yields pages.

**Call relations**: _paginate_product_api calls this for pipeline_stages. It gets source pipeline data from _raw_pipelines_for_object_type.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2293–2316)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Turns raw pipelines for one object type into normalized pipeline rows. It marks pipelines active or archived based on HubSpot flags.

**Data flow**: It receives an object type, reads its raw pipelines, skips rows without IDs, and returns rows with compound IDs, source pipeline ID, object kind, name, and status.

**Call relations**: _paginate_pipelines calls this for deals and tickets. It uses _raw_pipelines_for_object_type for the API read.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2318–2329)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Reads raw HubSpot pipeline data for one object type. Missing or forbidden pipeline endpoints produce an empty list.

**Data flow**: It receives an object type, calls the pipeline endpoint, returns valid result dictionaries, or returns an empty list for 403 or 404.

**Call relations**: Pipeline and pipeline-stage helpers call this as their shared data source.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2332–2336)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects HubSpot’s specific error for unsupported paging through deleted objects. This lets deletion sweeps stop quietly when HubSpot cannot provide them.

**Data flow**: It receives an HTTP error. It checks for status 400 and looks for HubSpot’s known message about paging through deleted objects, returning true only in that case.

**Call relations**: Both normal and custom archived-ID sweep helpers call this while handling errors.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2339–2347)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Extracts the message field from a HubSpot error response when possible. It is a small helper for interpreting upstream errors.

**Data flow**: It receives an HTTP error. It tries to parse JSON, checks for a dictionary body, and returns the message as text or None.

**Call relations**: _is_archived_sweep_unsupported uses this helper to inspect HubSpot’s error text, even though the call inventory lists no caller.


##### `HubSpotConnector._paginate_junction`  (lines 2349–2396)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds simple relationship rows for predefined parent-target pairs such as deal contacts or ticket companies. It does a full refresh because HubSpot does not expose update timestamps for these links.

**Data flow**: It receives parent and target object names. It pages through parent objects with associations included, extracts each parent ID and target ID, builds one row per pair, and yields pages until no next cursor remains.

**Call relations**: _paginate_unchecked calls this for streams listed in the junction map. The rows pass through flatten unchanged because they are already in the desired shape.

*Call graph*: called by 1 (_paginate_unchecked).


### `extensions/sources/ufo_ext_sources/salesforce.py`

`io_transport` · `source sync`

Salesforce stores customer data in many object types, called SObjects, and different Salesforce organizations can expose different fields. This file avoids hard-coding those fields. Before reading a stream, it asks Salesforce what fields exist on that object, then builds a Salesforce query that selects all of them. It reads records in update-time order, using SystemModstamp as a cursor so later syncs can continue from where the last one stopped instead of downloading everything again.

The main class, SalesforceConnector, is a REST connector: it talks to Salesforce over HTTP. For each supported stream, such as accounts or contacts, the file defines the Salesforce object name, primary key, creation time field, and update cursor field. During pagination, it sends a SOQL query, which is Salesforce’s SQL-like query language, follows Salesforce’s next-page links, and yields batches of records.

One important detail is deletion tracking. Once there is already a cursor, the connector also asks Salesforce which records were hard-deleted since that time. It returns those as a special tombstone page, meaning “these IDs should now be considered gone.” Another small cleanup step removes Salesforce’s metadata wrapper called attributes, so downstream code sees mostly the actual business fields. If Salesforce refuses access with a permission or authentication error, the stream is skipped with a clear message rather than crashing the whole sync unnecessarily.

#### Function details

##### `_stream`  (lines 29–38)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Salesforce stream, such as accounts or contacts. It gives the sync system the key facts it needs: what Salesforce object to read, which field identifies each record, and which timestamp field marks updates.

**Data flow**: It takes a friendly stream name, a Salesforce SObject name, and whether the stream is considered canonical. It fills in the shared Salesforce defaults, such as Id as the primary key and SystemModstamp as the update cursor, then returns a StreamSpec object that the connector can later use.

**Call relations**: This helper is used while the file is being loaded to build the SALESFORCE_STREAMS list. Those stream descriptions are then exposed through SalesforceConnector.streams_list so the wider sync runner knows which Salesforce objects this connector can read.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 78–82)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: This function builds the Salesforce query used to fetch records for one stream. It includes all known fields, optionally filters to records newer than the saved cursor, and asks Salesforce to return results in update order.

**Data flow**: It receives a stream description, a list of field names discovered from Salesforce, and possibly a cursor from a previous sync. It turns those into a SOQL query string like “select these columns from this object, only after this timestamp, ordered by timestamp, with a page limit.” The output is that query text.

**Call relations**: SalesforceConnector.paginate calls this after it has discovered the available fields. The query it returns is then sent to Salesforce’s query endpoint to begin reading records.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 84–90)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: This function asks Salesforce what fields exist on a particular object. This matters because Salesforce organizations can customize their objects, so the connector should not rely on a fixed built-in schema.

**Data flow**: It receives an HTTP client and a Salesforce object name. It requests that object’s describe endpoint, reads the returned field list, keeps only valid field names, and returns them as a list of strings.

**Call relations**: SalesforceConnector.paginate calls this before building the query. Its result feeds directly into SalesforceConnector._build_soql so the later record query includes whatever fields that Salesforce organization exposes.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 92–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main read loop for a Salesforce stream. It downloads records page by page, follows Salesforce’s next-page links, and, on incremental syncs, also reports records that were deleted.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing the last synced update time. It first discovers fields, builds a query, sends that query to Salesforce, yields each non-empty batch of records, and keeps following nextRecordsUrl until Salesforce says the query is done. If a cursor was provided, it then asks for deleted records since that cursor and may yield a tombstone page. If Salesforce responds with a permission or authentication refusal, it turns that into a StreamSkipped error with an explanatory message; other HTTP errors are allowed to bubble up.

**Call relations**: The sync runner calls this when it wants records for one Salesforce stream. Inside, it coordinates the smaller helpers: SalesforceConnector._describe_fields discovers columns, SalesforceConnector._build_soql creates the query, and SalesforceConnector._deleted_page adds deletion information after normal records have been read.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 121–139)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: This function asks Salesforce which records were permanently deleted during an incremental sync window. It packages those deleted IDs as a special page so downstream storage can remove or mark those records without needing a full rescan.

**Data flow**: It receives an HTTP client, a stream description, and the previous cursor as the start time. It chooses the current UTC time as the end of the window, calls Salesforce’s deleted-records endpoint, extracts deleted record IDs, and chooses the next cursor from Salesforce’s latest covered date when available. It returns a StreamPage containing deleted IDs and the next cursor, or nothing if there is no useful deletion page.

**Call relations**: SalesforceConnector.paginate calls this only after normal record pages have been read and only when there is already a cursor. The StreamPage it creates is handed back to the sync runner alongside ordinary record batches, but it means “delete these records” instead of “upsert these records.”

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 141–144)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function cleans one Salesforce record before the rest of the system sees it. It removes Salesforce’s attributes metadata envelope, leaving the actual field values.

**Data flow**: It receives a record dictionary and the stream description. If the record contains an attributes key, it returns a new dictionary with that key removed; otherwise it returns the original record unchanged. It does not change the stream description.

**Call relations**: This method is part of the connector shape expected by the source framework. After records are fetched by pagination, the framework can use flatten to normalize each Salesforce record before storing or indexing it.


### Support and conversation platforms
Connectors for helpdesk and customer communication systems that sync tickets, conversations, contacts, organizations, knowledge content, and related support records.

### `extensions/sources/ufo_ext_sources/freshdesk.py`

`io_transport` · `during Freshdesk data sync`

Freshdesk exposes many kinds of data: tickets, conversations, contacts, companies, agents, help-center articles, forum topics, and admin settings. This file is the read-only connector for that API. Without it, the system would not know where Freshdesk data lives, how to ask for it, or how to keep fetching the next batch when Freshdesk splits results across many pages.

The file first defines the available streams, where a “stream” means one kind of record the sync system can recall later, such as tickets or companies. Some streams are simple: they map directly to one REST API path. Others are nested, like ticket conversations, solution articles, or discussion comments. For those, the connector first fetches a parent object, then uses its id to fetch the child records. An everyday comparison is checking a filing cabinet: first find each folder, then open that folder to collect the papers inside.

Freshdesk uses different pagination methods for different endpoints. Most endpoints use a standard “next link” in the HTTP response headers. Tickets use numbered pages and an optional updated-since cursor for incremental syncs. Nested streams combine these patterns. The connector also turns Freshdesk permission failures into a clear “stream skipped” signal, so one missing grant does not look like a mysterious crash.

#### Function details

##### `_stream`  (lines 55–69)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a stream description for one kind of Freshdesk data, such as tickets or contacts. The rest of the sync system uses this description to know the stream’s name, source object, primary id field, and whether it supports cursor-based incremental reading.

**Data flow**: It receives a stream name and optional details such as source object, primary key, cursor field, and canonical flag. It fills in sensible defaults when details are not provided, then returns a StreamSpec object that represents that Freshdesk stream.

**Call relations**: This helper is used while the file builds the Freshdesk stream list. It hands the completed stream descriptions to StreamSpec.__init__, which produces the objects later used by FreshdeskConnector.paginate to decide what to fetch.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector._make_client`  (lines 109–124)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to a specific Freshdesk tenant. It sets timeouts, JSON headers, and authentication so later requests can focus on fetching records.

**Data flow**: It takes a base URL and a resolved credential. It trims any trailing slash from the URL, prepares standard JSON request headers, then either reuses a provided transport from the credential or creates Basic authentication using the Freshdesk API key as the username and "X" as the password. It returns an httpx AsyncClient ready to make requests, or raises an error if no usable authentication is present.

**Call relations**: The broader connector framework calls this when it is time to contact Freshdesk. This function delegates the low-level client setup to httpx.Timeout, httpx.BasicAuth, and httpx.AsyncClient, then the returned client is used by paginate and the helper pagination methods.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 127–137)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: Creates the query settings for fetching one page of Freshdesk tickets. It keeps ticket reads ordered by update time and adds the incremental cursor when one is available.

**Data flow**: It receives an optional cursor and a page number. It builds a parameter dictionary with page size, page number, ascending updated-time ordering, and requested included ticket details. If a cursor was provided, it adds it as Freshdesk’s updated_since value. The result is a dictionary ready to send with the ticket API request.

**Call relations**: FreshdeskConnector._paginate_tickets calls this before each ticket request. It is the small preparation step that makes ticket pagination consistent and cursor-aware.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 139–213)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct fetching strategy for each Freshdesk stream. It is the main traffic director for reads: simple streams go one way, ticket streams another, and nested help-center or forum streams use parent-child walks.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, then yields batches of records from the matching helper method. If the stream is not specially nested, it looks up the simple API path and follows Freshdesk’s link-header pagination. If Freshdesk responds with 401 or 403, meaning authentication or permission was refused, it turns that into StreamSkipped; other HTTP errors are allowed to continue upward.

**Call relations**: The sync framework calls this when it wants records for a Freshdesk stream. Depending on the stream, it hands work to _paginate_tickets, _paginate_conversations, _paginate_two_level, _paginate_three_level, or _paginate_link_header, then passes each returned page back to the caller.

*Call graph*: calls 6 internal fn (__init__, _paginate_conversations, _paginate_link_header, _paginate_three_level, _paginate_tickets, _paginate_two_level).


##### `FreshdeskConnector._paginate_link_header`  (lines 215–222)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches endpoints that use the common web pattern where each response points to the next page in an HTTP Link header. This avoids callers having to know the details of that paging convention.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the inherited REST helper to fetch pages with a page size of 100, then yields each page of records as it arrives.

**Call relations**: FreshdeskConnector.paginate uses this for simple streams. The nested pagination helpers also call it whenever they need to fetch parent, middle, or child pages. It relies on the base connector’s _get_link_header_pages method for the actual HTTP loop.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 224–241)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Freshdesk tickets using Freshdesk’s special numbered-page API. It supports incremental sync by starting from an updated_since cursor and stops before Freshdesk’s documented 300-page limit.

**Data flow**: It receives an HTTP client and an optional cursor. Starting at page 1, it builds ticket query parameters, requests the ticket endpoint, extracts the returned records, and yields them as a page. It stops when there are no records, when a page is smaller than the maximum page size, or when the page ceiling is reached.

**Call relations**: FreshdeskConnector.paginate calls this for the tickets stream. FreshdeskConnector._paginate_conversations also calls it first, because conversations are discovered by walking through tickets and then asking for each ticket’s conversation list. This function calls _build_tickets_params before each request.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, paginate).


##### `FreshdeskConnector._paginate_conversations`  (lines 243–259)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads ticket conversations by first finding tickets and then fetching the conversations attached to each ticket. This is needed because conversations are not fetched as one flat global list here; they live under individual tickets.

**Data flow**: It receives an HTTP client and optional cursor. It gets ticket pages from _paginate_tickets, takes each ticket id, requests that ticket’s conversations through link-header pagination, and stamps each conversation with the ticket_id if it is not already present. It yields pages of conversation records.

**Call relations**: FreshdeskConnector.paginate calls this for the conversations stream. It chains together _paginate_tickets and _paginate_link_header: tickets supply the parent ids, and link-header paging retrieves the child conversation pages.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (paginate).


##### `FreshdeskConnector._paginate_two_level`  (lines 261–272)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches data that has a parent-child shape, such as categories containing forums or folders containing responses. It is a reusable walker for Freshdesk structures that are two levels deep.

**Data flow**: It receives an HTTP client, a parent API path, and a child path template containing a parent id placeholder. It fetches parent pages, pulls each parent id, formats the child path with that id, then yields each child page found under that parent. Parents without an id are skipped.

**Call relations**: FreshdeskConnector.paginate calls this for several nested streams, including canned responses, solution folders, discussion forums, discussion topics, and discussion comments. It uses _paginate_link_header for both the parent list and each child list.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


##### `FreshdeskConnector._paginate_three_level`  (lines 274–297)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches data that sits three levels down in Freshdesk’s tree-shaped APIs, such as solution articles inside folders inside categories. It prevents each stream from needing its own custom nested loop.

**Data flow**: It receives an HTTP client plus paths for the root, middle, and leaf levels. It fetches root records, extracts each root id, fetches the middle records under that root, extracts each middle id, then fetches and yields the final leaf pages. Any record without the needed id is skipped.

**Call relations**: FreshdeskConnector.paginate calls this for streams that need a three-step walk, especially solution articles. It repeatedly uses _paginate_link_header at each level so the same next-page behavior works for categories, folders, and final records.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/intercom.py`

`io_transport` · `during source sync`

Intercom exposes useful customer-support data, but it does not offer all of it in one simple format. Some endpoints use search pages, some use a scrolling cursor, some return one plain list, and some records must be fetched by first reading a parent item and then asking for its children. This file hides those differences behind one connector.

The connector declares the Intercom streams UFO can read. A stream is a named kind of data, such as conversations or contacts, with details like its main ID field and the field used to resume an incremental sync. When a sync runs, IntercomConnector creates an HTTP client, adds the Intercom API version header, and chooses the right pagination method for the requested stream.

The file also reshapes a few records before the rest of UFO sees them. Intercom often nests useful values inside envelopes, like a conversation source or a contact’s companies. The flattening helpers copy those values to simple top-level fields so later database or SQL steps can use them easily. For incremental syncs, Intercom uses Unix-second integers like updated_at, while this adapter stores cursor values as strings, so the connector converts those cursor numbers to decimal strings after reading them.

#### Function details

##### `_stream`  (lines 52–66)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a StreamSpec, which is the connector’s small description card for one kind of Intercom data. It saves repeated setup code when defining many Intercom streams.

**Data flow**: It receives a stream name and optional details such as the Intercom object name, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults, then creates and returns a StreamSpec object with those choices.

**Call relations**: This helper is used while the file defines the list of available Intercom streams. Its direct handoff is to StreamSpec.__init__, which turns the plain settings into the standard stream object the rest of the connector framework understands.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector._make_client`  (lines 103–106)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to Intercom and adds the required Intercom API version header. Without this, Intercom might answer using a different API version or reject version-sensitive requests.

**Data flow**: It receives a base URL and a credential. It first lets the parent RestConnector build the normal authenticated client, then adds the Intercom-Version header, and returns that ready-to-use client.

**Call relations**: This is part of the connector setup path. The base REST connector supplies the normal client creation behavior, and this method adds the Intercom-specific finishing touch before any pagination method makes requests.


##### `IntercomConnector._build_search_body`  (lines 109–139)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the request body for Intercom search endpoints. It tells Intercom how many records to return, how to sort them, where to continue within a page sequence, and what cursor value to filter after.

**Data flow**: It receives the stream description, the last saved sync cursor, and an optional page cursor called starting_after. It turns these into a JSON-ready dictionary for Intercom, converting numeric cursor strings back into numbers when possible because Intercom expects numbers for timestamp searches. The result is the body sent in a POST request.

**Call relations**: The search pagination methods call this whenever they need another page. IntercomConnector._paginate_search uses it for main search streams like conversations, contacts, and tickets, and IntercomConnector._paginate_conversation_parts uses it to find conversations before fetching their parts.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 142–147)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: Safely picks the first dictionary from a list-like nested Intercom field. It is used when Intercom wraps related records, such as contacts or companies, inside a list.

**Data flow**: It receives any value. If the value is a non-empty list and its first item is a dictionary, it returns that dictionary. Otherwise, it returns nothing.

**Call relations**: This helper supports the flattening code. The conversation and contact flatteners use it to pull one useful related record out of Intercom’s nested envelopes without crashing on missing or unexpected shapes.


##### `IntercomConnector._flatten_conversation`  (lines 150–167)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes conversation records easier to use by copying important nested fields to simple top-level keys. In particular, it exposes the conversation source details and the first requester contact ID.

**Data flow**: It receives one conversation record. It copies the record, looks inside the nested source object for type, subject, and body, and looks inside the nested contacts list for the first contact ID. It returns the enriched copy without changing the original record in place.

**Call relations**: IntercomConnector.flatten calls this only for the conversations stream. It prepares conversation records for later processing, where flat fields are much easier to query than deeply nested Intercom JSON.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 170–179)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes a conversation part easier to query by copying author details to top-level fields. A conversation part is an individual message or event inside a conversation.

**Data flow**: It receives one conversation-part record. It copies the record, reads the nested author object if present, and writes author_type and author_id onto the copy. It leaves conversation_id alone because the paginator adds that earlier.

**Call relations**: IntercomConnector.flatten calls this for the conversation_parts stream. The paginator first stamps each part with its parent conversation ID, and this helper then exposes author information in the same flat style.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 182–190)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds an easy-to-find organization ID to a contact record. Intercom may bury a contact’s associated company inside a nested companies envelope.

**Data flow**: It receives one contact record. It copies the record, looks for the first company under the nested companies field, and writes that company’s id or company_id into org_id. It returns the copied record with this extra field when available.

**Call relations**: IntercomConnector.flatten calls this for the contacts stream. It uses the same small _first helper pattern as conversation flattening, so later transforms can read org_id directly.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 192–206)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records after they are read from Intercom. It flattens selected nested fields and converts integer cursor values into strings so UFO’s watermark tracking can store them.

**Data flow**: It receives one record and the stream it came from. Depending on the stream name, it sends the record through the conversation, conversation-part, or contact flattener. Then, if the stream has a cursor field and that field is an integer, it returns a copy where that cursor value is a string; otherwise it returns the record as-is.

**Call relations**: This is the common cleanup step after pagination produces raw Intercom records. It calls IntercomConnector._flatten_conversation, IntercomConnector._flatten_conversation_part, or IntercomConnector._flatten_contact when the stream needs special reshaping.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 208–252)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right way to read pages for each Intercom stream. It is the traffic director that hides Intercom’s many pagination styles behind one common async stream of record batches.

**Data flow**: It receives an HTTP client, a stream description, and the last cursor value. It checks the stream name, delegates to the matching pagination helper, and yields each list of records that helper produces. If Intercom returns a permission refusal, it turns that into StreamSkipped so the sync can record a clean skip instead of failing the whole run.

**Call relations**: This is the main dispatcher for reading Intercom data. It hands off to IntercomConnector._paginate_search, _paginate_scroll, _paginate_list, _paginate_attributes, _paginate_conversation_parts, _paginate_company_segments, or _paginate_activity_logs depending on the stream, and it creates StreamSkipped for 401 or 403 responses.

*Call graph*: calls 8 internal fn (__init__, _paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search).


##### `IntercomConnector._paginate_search`  (lines 254–274)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads streams that use Intercom’s search API, such as conversations, contacts, and tickets. It keeps asking for the next search page until Intercom says there are no more.

**Data flow**: It receives the client, stream, and saved cursor. It builds a search request body, posts it to the stream’s search path, extracts the record list from the correct response key, and yields non-empty batches. It then reads Intercom’s next starting_after token and repeats until that token is missing.

**Call relations**: IntercomConnector.paginate calls this for streams listed in the search-path table. This method calls IntercomConnector._build_search_body before each request so the request contains the current cursor and page-continuation token.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_scroll`  (lines 276–290)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads companies using Intercom’s scroll API. A scroll API is like paging through a long list with a bookmark token returned by each response.

**Data flow**: It starts with no scroll token, requests /companies/scroll, yields the returned company records, then saves the returned scroll_param token. It repeats with that token until there are no records or no next token.

**Call relations**: IntercomConnector.paginate calls this for the companies stream. Unlike the search streams, this helper does not use a timestamp cursor; it follows Intercom’s scroll_param from page to page.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_list`  (lines 292–304)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple Intercom list endpoints, such as admins, tags, teams, and segments. These endpoints return their data in a single response rather than through a multi-page search loop.

**Data flow**: It receives the client and stream, chooses the endpoint path for that stream, and makes one GET request. It looks for a list under either the stream name or data, yields it if it is non-empty, and then stops.

**Call relations**: IntercomConnector.paginate calls this for streams in the list-path table. It is the simplest pagination branch because there is no follow-up token to pass along.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_attributes`  (lines 306–315)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Intercom data attribute definitions for contacts or companies. These are metadata records that describe custom fields rather than customer records themselves.

**Data flow**: It receives the client and stream, maps the stream name to the Intercom model name, and requests /data_attributes with that model as a parameter. It extracts the data list and yields it if there are records.

**Call relations**: IntercomConnector.paginate calls this for company_attributes and contact_attributes. The stream name determines whether the helper asks Intercom for company or contact attribute definitions.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_conversation_parts`  (lines 317–349)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the messages or events inside conversations. Intercom does not get these as a standalone search stream here, so the connector first finds conversations and then fetches each conversation’s details.

**Data flow**: It receives the client and saved cursor. It searches conversations page by page, then for each conversation with an ID it requests /conversations/{id}. From the detail response it extracts conversation parts, stamps each part with its parent conversation_id, yields the parts, and then continues to the next conversation search page.

**Call relations**: IntercomConnector.paginate calls this for the conversation_parts stream. This method calls IntercomConnector._build_search_body to page through parent conversations, then performs extra detail GET requests so child records can be emitted as their own stream.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_company_segments`  (lines 351–375)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the segments attached to each company. It first scrolls through companies, then asks Intercom which segments each company belongs to.

**Data flow**: It receives the client and starts reading companies through /companies/scroll. For each company with an ID, it requests /companies/{id}/segments, stamps each returned segment with company_id, and yields the segment records. It follows scroll_param until there are no more company pages.

**Call relations**: IntercomConnector.paginate calls this for the company_segments stream. It is a substream reader: companies are the parent records, and their segment memberships are emitted as child records.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_activity_logs`  (lines 377–399)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads admin activity logs, optionally only after a saved created_at cursor. It follows Intercom’s next-page links until the activity log feed is exhausted.

**Data flow**: It receives the client and optional cursor. If a cursor exists, it sends it as created_at_after on the first request to /admins/activity_logs. It yields activity_logs from each response, then follows the pages.next URL or path until no next link remains.

**Call relations**: IntercomConnector.paginate calls this for the activity_logs stream. This helper has its own paging style because Intercom returns the next location as a link rather than the search API’s starting_after token or the company scroll token.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/zendesk.py`

`io_transport` · `sync run / source data fetching`

Zendesk exposes its data through many web API endpoints, and those endpoints do not all behave the same way. This file is the adapter that hides those differences. Without it, the system would not know which Zendesk URLs to call, how to move from one page of results to the next, or how to pull special records like ticket comments and user identities.

The file first defines a catalog of Zendesk “streams.” A stream is one kind of thing to fetch, like tickets or articles. Each stream records the Zendesk object name, its main ID field, and which date field can be used for incremental syncing, meaning “only fetch things changed since last time.”

The main class, `ZendeskConnector`, is a read-only connector. It uses a shared REST connector base class for the actual HTTP requests, while this file decides the Zendesk-specific paths and paging rules. Some large Zendesk objects use cursor-based incremental export, like following numbered tickets at a deli counter. Other objects use ordinary `next_page` links. Ticket comments are not fetched from a simple comments endpoint; they are extracted from ticket event records. User identities are fetched by first listing users, then asking Zendesk for each user’s identities.

If Zendesk rejects access with a permission or authentication error, the connector marks that stream as skipped instead of pretending the data is empty.

#### Function details

##### `_stream`  (lines 58–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates one stream definition for a Zendesk data type. It keeps the long stream list readable by filling in common defaults, such as using `id` as the main identifier and `updated_at` as the usual change-tracking field.

**Data flow**: It receives a stream name plus optional details like the Zendesk source path, primary key, date fields, and whether the stream is considered canonical. It fills in missing values with sensible defaults, builds a `StreamSpec` object, and returns that object for the connector’s stream catalog.

**Call relations**: This helper is used while the file is loaded to build `ZENDESK_STREAMS`. It hands each completed stream definition to `StreamSpec`, so later the connector can decide how to fetch each type of Zendesk data.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 142–167)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: This helper adds useful related information that Zendesk sent alongside the main records. In practice, it lets ticket records gain requester, submitter, and assignee email addresses from sideloaded user data.

**Data flow**: It receives a list of main records, the full API page returned by Zendesk, and instructions for matching fields. It builds a quick lookup table from the related records in the page, finds matching related users for each ticket, and writes email fields back onto the ticket records. It changes the records in place and does not return a separate value.

**Call relations**: The incremental ticket fetcher calls this after downloading a page that includes both tickets and users. It enriches the ticket rows before they are yielded to the rest of the sync pipeline.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 176–177)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This method decides which field inside a Zendesk API response contains the records for a stream. Most streams use their own name, but a few Zendesk endpoints use different names like `audits`, `policies`, or `definitions`.

**Data flow**: It receives a stream definition. It checks a small override table for special cases; if there is no special case, it returns the stream’s name. The returned string is used as the key for reading records out of the JSON response.

**Call relations**: The default paginator calls this before reading each normal paged response. It lets one generic paginator work with many Zendesk endpoints whose response shapes are slightly different.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 180–192)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: This method converts the stored sync cursor into the Unix timestamp format Zendesk expects. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor value that may be missing, already numeric, or an ISO-style date string. If the value is missing or unreadable, it returns `0`, meaning start from the beginning. If it can parse the value, it returns the matching timestamp as an integer.

**Call relations**: The special incremental paginators call this when building their first Zendesk URL. It turns the project’s saved progress marker into the `start_time` query value Zendesk needs.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 195–204)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: This method turns Zendesk’s full next-page URL into just the path and query string needed by the connector’s HTTP client. It is a small safety step that keeps paging requests tied to the configured Zendesk host.

**Data flow**: It receives a `next_page` or `after_url` value from Zendesk. If the value is empty or has no path, it returns nothing. Otherwise, it parses the URL, keeps the path and query part, and returns that shorter path for the next request.

**Call relations**: All pagination methods call this after each page. It is the common bridge from Zendesk’s “here is the next URL” response to the connector’s “request this path next” loop.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 206–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading one Zendesk stream. It chooses the right paging strategy for the requested stream and turns Zendesk responses into batches of records.

**Data flow**: It receives an HTTP client, a stream definition, and an optional saved cursor. It checks the stream name, sends the work to the matching paginator, and yields each batch of records produced there. If Zendesk returns a 401 or 403 refusal, it raises `StreamSkipped`, which tells the system that this stream cannot be read with the current permissions.

**Call relations**: The wider source-sync framework calls this when it wants records for a particular Zendesk stream. This method then hands off to the ticket-comments paginator, user-identities paginator, cursor-based incremental paginator, or ordinary paginator depending on the stream.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 232–253)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads Zendesk’s high-volume incremental export streams, such as tickets, users, organizations, and ticket metric events. It is built for syncing large objects efficiently by asking only for records after a given starting time.

**Data flow**: It receives an HTTP client, a stream definition, and a cursor. It converts the cursor to a timestamp, builds the Zendesk incremental export URL, downloads each page, extracts the records, optionally enriches tickets with sideloaded user emails, and yields non-empty batches. It follows Zendesk’s next cursor URL until Zendesk says the stream has ended.

**Call relations**: `ZendeskConnector.paginate` calls this for streams listed as incremental cursor streams. It uses `_cursor_to_unix` to start at the right time, `_apply_sideload` to improve ticket records when needed, and `_next_page_path` to continue through Zendesk’s pages.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 255–265)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads Zendesk streams that use ordinary page-by-page API results. It is the simple path for streams that do not need special incremental-export behavior.

**Data flow**: It receives an HTTP client and a stream definition. It builds the first `/api/v2/...` URL, asks `_data_field` which response field contains the records, downloads each page, yields any records found, and follows the `next_page` link until there are no more pages.

**Call relations**: `ZendeskConnector.paginate` uses this as the fallback for most streams. It relies on `_data_field` for response-shape differences and `_next_page_path` for moving to the next Zendesk page.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 267–299)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method creates a ticket-comments stream from Zendesk ticket events. Zendesk exposes comments as child events inside ticket-event records, so this method pulls those comment entries out and turns them into standalone rows.

**Data flow**: It receives an HTTP client and an optional cursor. It converts the cursor into a start timestamp, requests incremental ticket events with comment events included, scans each ticket event for child events whose type is `Comment`, copies each comment, adds the parent `ticket_id`, normalizes numeric creation times into readable UTC timestamps, and yields batches of comments. It keeps following Zendesk’s next page until the event stream ends.

**Call relations**: `ZendeskConnector.paginate` calls this only for the `ticket_comments` stream. It uses `_cursor_to_unix` to start from the last sync point and `_next_page_path` to keep walking through Zendesk’s event feed.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate); 1 external calls (fromtimestamp).


##### `ZendeskConnector._paginate_user_identities`  (lines 301–326)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads identity records for Zendesk users, such as login identities or email identities. Zendesk requires a two-step process: first find users, then ask for identities for each user.

**Data flow**: It receives an HTTP client and an optional cursor. It incrementally downloads users changed since the cursor, skips malformed users or users without an ID, then for each user requests that user’s identities page by page. Whenever identities are found, it yields them as record batches. It continues through all users and all identity pages until Zendesk reports the user stream is finished.

**Call relations**: `ZendeskConnector.paginate` calls this for the `users_identities` stream. It uses `_cursor_to_unix` for the user listing start time and `_next_page_path` for both user pagination and identity pagination.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).
