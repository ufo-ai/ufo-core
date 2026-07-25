# CRM, sales, support, and customer-success source connectors  `stage-12.4`

This stage is part of the system’s data intake work. It sits behind the scenes when a sync runs, reaching out to customer-facing tools and reshaping their data into one common stream of records that the rest of the product can store, search, and recall.

Each file is like an adapter for a different outside service. The Attio connector reads companies, people, deals, tasks, notes, meetings, and call recordings, including transcripts when they exist. The HubSpot connector covers a wide range of CRM, marketing, analytics, conversation, list, association, custom-object, and deletion data. The Salesforce connector reads common sales records such as accounts, contacts, opportunities, and cases through Salesforce’s web API, meaning its online data access interface. Freshdesk brings in support tickets, contacts, agents, help articles, and forum content. Zendesk does the same for support, help-center, and community data, including comments and votes. Intercom reads conversations, contacts, companies, teams, tags, and activity logs. Together, these adapters turn many different service formats into the same sync-friendly shape.

## Files in this stage

### CRM and sales pipelines
Connectors that extract customer records, relationships, deals, opportunities, activities, and sales-oriented CRM objects.

### `extensions/sources/ufo_ext_sources/attio.py`

`io_transport` · `source sync / data ingestion`

Attio’s API does not return every kind of data in the same shape. Company, person, and deal records come from one style of endpoint. Tasks and notes come from another. Meetings and call recordings use cursor-based paging, and call recordings need an extra request to fetch their transcripts. This file is the adapter that hides those differences.

The connector defines the Attio streams the system can sync and marks them as full snapshots. That means each sync reads the whole stream and uses the record’s ID to decide what still exists and what has disappeared. This matters because Attio does not provide one reliable “last changed” field across all these resources.

A large part of the file is about flattening. Attio stores most fields inside nested “value cells,” like a filing cabinet where every field is wrapped in its own folder. The rest of the system needs simple top-level fields such as record_id, email, domain, or transcript_text. The flattening helpers open those folders, pick the useful value, and create a simpler record.

The file also knows when to skip a stream instead of failing the whole sync. For example, if a workspace has disabled a standard object, or the OAuth grant is missing permission for meetings, the connector raises StreamSkipped so the rest of the sync can continue safely.

#### Function details

##### `_records_stream`  (lines 35–43)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a standard stream description for Attio object records such as companies, people, and deals. It gives the rest of the sync system the stream name, where to read it from, and which field should identify each row.

**Data flow**: It receives a friendly stream name and an Attio object slug. It builds a StreamSpec with record_id as the primary key, no cursor field, and delete_missing turned on. The result is a reusable stream definition placed into the connector’s stream list.

**Call relations**: This helper is used while the module is being loaded to build the Attio stream catalog. It hands its settings to StreamSpec, which is the common object the broader source framework understands.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 70–71)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: Safely pulls one named ID out of a nested dictionary. It prevents the connector from crashing when Attio returns an unexpected shape.

**Data flow**: It receives any value and a key name. If the value is a dictionary, it returns the value stored under that key. If not, it returns None.

**Call relations**: AttioConnector._value_primitive calls this when an option or status has its useful ID tucked inside another id object. It is a small safety helper inside the value-cleaning path.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 80–81)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: Builds the request body used to ask Attio for a page of object records. It keeps the paging request format in one place.

**Data flow**: It receives an offset, meaning how many records have already been read. It returns a dictionary with Attio’s page limit and that offset. Nothing else is changed.

**Call relations**: AttioConnector.paginate calls this for company, person, and deal style streams before sending a records query to Attio.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._value_primitive`  (lines 84–127)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: Turns one Attio value cell into the plain value people expect, such as text, a number, an email address, a selected option name, or a referenced record ID. This is needed because Attio stores different field types under different key names.

**Data flow**: It receives one value-cell dictionary from Attio. It checks which kind of information is present, chooses the most natural simple value, and returns that value. For references, it prefixes the target ID with attio:; for locations, it joins address parts into one readable string; if it cannot find a useful value, it returns None.

**Call relations**: This is the main low-level cleaner used by the flattening helpers. When an option or status stores its identifier inside a nested id object, it calls _nested_id to retrieve it safely.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 130–145)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: Simplifies one Attio field cell into a single useful value, or sometimes a list for multi-select options. It acts like opening one wrapped field and taking out the readable contents.

**Data flow**: It receives a cell that may be a list, a dictionary, or an already-simple value. For lists, it converts each item to a primitive value and drops empty results. Usually it returns the first useful value, but for multiple option selections it keeps the full list. For dictionaries, it delegates to the primitive extractor. The output is a simpler value or None.

**Call relations**: This helper sits in the flattening pipeline used when normal Attio records are converted into system-friendly records. It relies on AttioConnector._value_primitive for the actual field-type decisions.


##### `AttioConnector._flatten_list_cell`  (lines 148–155)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: Simplifies a field that should stay as a list, such as domains, categories, email addresses, or phone numbers. It preserves multiple useful values instead of reducing everything to one.

**Data flow**: It receives a cell from Attio. If the cell is not a list, it flattens it as one value and wraps that value in a list. If it is already a list, it extracts primitive values from each item and removes empty ones. The result is always a list.

**Call relations**: AttioConnector._flatten_values uses this for fields where keeping all entries matters. It shares the same primitive extraction rules as the rest of the flattening code.


##### `AttioConnector._flatten_values`  (lines 158–192)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns Attio’s nested values object into ordinary top-level fields. This is where fields like email_addresses, phone_numbers, domains, first_name, last_name, and name become easy for the rest of the system to use.

**Data flow**: It receives the values dictionary from an Attio record. It walks each field, flattens it either as a single value or a list, and stores the result under the field’s slug. It also creates convenient shortcut fields such as email from the first email address and domain from the first domain. It returns a cleaned dictionary of fields.

**Call relations**: This function is part of normal record flattening. AttioConnector._flatten_record uses its output to combine Attio’s identity fields with the cleaned business fields.


##### `AttioConnector._flatten_record`  (lines 195–207)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Converts a standard Attio object record, such as a company or person, into the flat shape required by the sync system. Most importantly, it lifts record_id out of Attio’s nested id block.

**Data flow**: It receives a raw Attio record and the stream definition being synced. It reads record_id, object_id, and workspace_id from the nested id section, optionally copies a cursor-like timestamp if the stream asks for one, then adds the flattened field values. It returns one clean record dictionary.

**Call relations**: AttioConnector.flatten calls this for object streams that are not tasks, notes, meetings, or call recordings. It is the main path for companies, people, and deals.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 210–214)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts an Attio task into a record with a top-level task_id. This gives the sync system a stable key for identifying the task.

**Data flow**: It receives a raw task dictionary. It copies the record, reads task_id from the nested id field when needed, adds task_id at the top level, and returns the copy.

**Call relations**: AttioConnector.flatten calls this when the current stream is tasks. It is a simpler version of the standard record flattener because task fields are not stored in the same values wrapper.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 217–221)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts an Attio note into a record with a top-level note_id. This makes notes identifiable by the rest of the sync pipeline.

**Data flow**: It receives a raw note dictionary. It copies the note, extracts note_id from the nested id block if necessary, adds note_id at the top level, and returns the copy.

**Call relations**: AttioConnector.flatten calls this for the notes stream. It exists because notes use a different API shape from standard object records.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 224–228)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts an Attio meeting into a record with a top-level meeting_id. This lets meetings be stored and updated consistently.

**Data flow**: It receives a raw meeting dictionary. It copies the meeting, extracts meeting_id from the nested id field when present, adds meeting_id at the top level, and returns the copy.

**Call relations**: AttioConnector.flatten calls this for the meetings stream. It is also conceptually connected to call recording sync, because recordings are fetched under individual meetings.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 231–252)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts an Attio call recording into a searchable record, including a top-level call_recording_id and readable transcript text. This is what turns transcript segments into one useful text field.

**Data flow**: It receives a raw call recording dictionary. It copies the record, lifts call_recording_id from the nested id field, fills recording_url from web_url if needed, and joins transcript segments into transcript_text with speaker names when available. It returns the enriched copy.

**Call relations**: AttioConnector.flatten calls this for the call_recordings stream. It expects transcript data that may have been attached earlier by AttioConnector._paginate_call_recordings.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 254–263)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening method for the stream currently being synced. It is the public cleanup step that turns Attio’s raw API rows into records the rest of the product can use.

**Data flow**: It receives one raw record and its stream definition. It checks the stream name, sends the record to the matching task, note, meeting, call recording, or standard record flattener, and returns the cleaned record.

**Call relations**: The source framework calls this after pages of raw Attio data are read. It hands off to AttioConnector._flatten_task, AttioConnector._flatten_note, AttioConnector._flatten_meeting, AttioConnector._flatten_call_recording, or AttioConnector._flatten_record depending on the stream.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 265–319)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all pages for one Attio stream, using the paging style that stream requires. This is the main doorway from the sync system into Attio’s API.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. For tasks and notes it uses simple offset paging. For meetings it uses cursor paging. For call recordings it walks meetings first, then their recordings. For standard objects it posts query bodies with limit and offset. It yields batches of raw records and may raise StreamSkipped when a stream should be skipped rather than treated as a fatal error.

**Call relations**: This method coordinates the lower-level paging helpers: AttioConnector._paginate_simple, AttioConnector._paginate_cursor, AttioConnector._paginate_call_recordings, and AttioConnector._build_query_body. When Attio reports a disabled object or missing OAuth permission, it checks AttioConnector._is_object_disabled or AttioConnector._is_scope_unauthorized and uses StreamSkipped with AttioConnector._scope_skip_reason where appropriate.

*Call graph*: calls 8 internal fn (__init__, _build_query_body, _is_object_disabled, _is_scope_unauthorized, _paginate_call_recordings, _paginate_cursor, _paginate_simple, _scope_skip_reason).


##### `AttioConnector._paginate_simple`  (lines 321–328)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads offset-paged Attio endpoints such as tasks and notes. Offset paging means asking for records after a numbered starting point, like reading a book page by page.

**Data flow**: It receives an HTTP client, an API path, and a page size. It asks the shared REST helper for pages found under the data field and yields each page unchanged.

**Call relations**: AttioConnector.paginate calls this for the tasks and notes streams. It delegates the repeated HTTP GET and offset bookkeeping to the inherited REST connector helper.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._paginate_cursor`  (lines 330–348)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads cursor-paged Attio endpoints such as meetings and call recordings. A cursor is a token from the server that says where to continue next.

**Data flow**: It receives an HTTP client, an API path, a page size, and optional request parameters. It asks the shared REST helper for pages under data and feeds Attio’s pagination.next_cursor value back into later requests. It yields each page of records.

**Call relations**: AttioConnector.paginate calls this for meetings. AttioConnector._paginate_call_recordings also calls it first to list meetings and then to list recordings for each meeting.

*Call graph*: called by 2 (_paginate_call_recordings, paginate).


##### `AttioConnector._paginate_call_recordings`  (lines 350–386)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the call recording stream by first finding meetings, then finding recordings under each meeting, then adding transcript data when available. This is needed because Attio does not expose recordings as one simple global list.

**Data flow**: It receives an HTTP client. It pages through meetings, extracts each meeting ID, title, start, and end time, calculates a duration when possible, then pages through that meeting’s recordings. For each recording it adds parent meeting context and, when it can identify the recording, fetches its transcript and attaches transcript fields. It yields pages of enriched recording records.

**Call relations**: AttioConnector.paginate calls this for the call_recordings stream. Inside, it uses AttioConnector._paginate_cursor for both meetings and recordings, AttioConnector._meeting_id and AttioConnector._call_recording_id to find IDs, AttioConnector._datetime_of and AttioConnector._duration_seconds for timing, and AttioConnector._fetch_transcript for transcript details.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (paginate).


##### `AttioConnector._fetch_transcript`  (lines 388–400)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: Fetches the transcript for one call recording when Attio has it ready. If the transcript is not ready or not found, it quietly returns None so the sync can continue.

**Data flow**: It receives an HTTP client, a meeting ID, and a recording ID. It builds the transcript API path and sends a GET request. If Attio replies with 404 or 409, it returns None. Otherwise it returns the data object if it is a dictionary, or None if the response shape is not useful.

**Call relations**: AttioConnector._paginate_call_recordings calls this for each recording that has an identifiable recording ID. The returned transcript is attached to the recording before the page is yielded.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 403–407)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a meeting ID from Attio’s possible meeting ID shapes. It keeps the call recording flow from depending on one exact response format.

**Data flow**: It receives a meeting dictionary. If meeting.id is a dictionary, it returns id.meeting_id. If meeting.id is already a string, it returns that string. Otherwise it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this while walking meetings. Without a meeting ID, that meeting is skipped because recordings cannot be requested for it.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 410–414)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a call recording ID from Attio’s possible recording ID shapes. This ID is needed to request the recording’s transcript.

**Data flow**: It receives a recording dictionary. If rec.id is a dictionary, it returns id.call_recording_id. If rec.id is already a string, it returns that string. Otherwise it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this for each recording. When it returns an ID, the flow can call AttioConnector._fetch_transcript.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 417–421)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: Pulls a usable date or date-time string out of Attio’s meeting time object. It supports both timed meetings and all-day meetings.

**Data flow**: It receives a time-shaped value from Attio. If it is a dictionary, it returns the datetime field first, or the date field if datetime is absent. If the input is not a dictionary, it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this for meeting start and end values before adding those values to recordings and before calculating duration.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 424–434)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: Calculates a rough meeting duration in seconds from start and end strings. It avoids failing the sync if the dates are missing or malformed.

**Data flow**: It receives optional start and end strings. If either is missing, it returns None. Otherwise it tries to parse them as ISO 8601 dates, including strings ending in Z for UTC time. If parsing works, it returns the non-negative difference in seconds; if parsing fails, it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this after extracting meeting start and end values. It uses datetime.fromisoformat to do the date parsing.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 437–446)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes the specific Attio error that means a standard object, such as companies or deals, is disabled in the workspace. This lets the connector skip that stream instead of treating it as a broken sync.

**Data flow**: It receives an HTTP error. It first checks for status code 400, then tries to read the JSON response body. It returns true only when the body has code set to standard_object_disabled; otherwise it returns false.

**Call relations**: AttioConnector.paginate calls this when a standard object query fails. If it returns true, paginate raises StreamSkipped with a clear reason.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._is_scope_unauthorized`  (lines 449–458)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes the Attio error that means the OAuth token is missing a required permission. OAuth is the permission grant that lets this system read data from Attio on a user’s behalf.

**Data flow**: It receives an HTTP error. It checks for status code 403, tries to parse the response JSON, and returns true only if the response code is unauthorized. If the response is not JSON or does not match, it returns false.

**Call relations**: AttioConnector.paginate calls this around meetings and call recordings. If the error is a missing permission, paginate turns it into StreamSkipped rather than stopping every stream.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._scope_skip_reason`  (lines 461–467)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: Creates a human-readable explanation for skipping a stream because the OAuth grant lacks a required scope. This makes sync logs easier to understand.

**Data flow**: It receives an HTTP error. It tries to read the JSON body and pull out Attio’s message. It returns a sentence explaining that a required OAuth scope is missing, using the upstream message when available.

**Call relations**: AttioConnector.paginate calls this after AttioConnector._is_scope_unauthorized identifies a missing-permission error. The returned text is passed into StreamSkipped.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/hubspot.py`

`io_transport` · `source sync and pagination`

HubSpot has many APIs, and they do not all return data in the same shape. This file is the adapter that makes them feel like one source. It defines the HubSpot streams the sync runner can ask for, such as contacts, companies, deals, forms, email events, pipelines, and custom objects. Then `HubSpotConnector` knows which HubSpot endpoint to call for each stream, how to page through long result sets, and how to turn HubSpot’s nested responses into flat rows the rest of the system can store.

The main flow is like a librarian collecting books from many departments. For normal CRM objects, it first asks HubSpot which fields exist, searches records in modification-time order, and avoids duplicating records that sit exactly on the saved cursor boundary. After that it performs a separate sweep for archived records, so deleted HubSpot objects become tombstones instead of silently lingering. For product APIs that do not support CRM search, it uses stream-specific walkers.

The connector is careful about partial access. If HubSpot says a particular object is unavailable because of account tier or missing permission, this file marks only that stream as skipped rather than failing the whole sync. It also contains no write path and no stored token; it only reads through the HTTP client provided by the runner.

#### Function details

##### `_normalize_epoch_millis`  (lines 248–255)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Turns HubSpot timestamps written as milliseconds since 1970 into readable UTC date-time strings. It leaves booleans and already-readable values alone so unrelated fields are not accidentally changed.

**Data flow**: Input is any value. If it is a number, or a string made only of digits, it is treated as milliseconds since the Unix epoch and converted to an ISO timestamp; otherwise the same value comes back unchanged.

**Call relations**: This is used when product-style records carry dates in raw millisecond form, especially by `HubSpotConnector._flatten_product_api` and `HubSpotConnector._analytics_view_rows`, before those records are handed to the rest of the sync.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 258–265)

```
def _stream(name: str, *, object_type: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a standard HubSpot CRM stream definition. It records the object type, primary key, cursor field, and whether the stream is considered a canonical core object.

**Data flow**: Input is a stream name and HubSpot object type. It produces a `StreamSpec`, which is the small description the sync runner uses to know what to read and how to track progress.

**Call relations**: This helper is used while the module is loaded to declare CRM streams such as companies, contacts, deals, tasks, and many non-canonical CRM objects.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 268–283)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, pagination: Pagination | None=None) -> StreamSpec
```

**Purpose**: Creates a stream definition for HubSpot product APIs, which are APIs outside the standard CRM search surface. These streams often have different keys, cursors, or pagination rules.

**Data flow**: Input is a stream name, source object name, and optional key, cursor, and pagination details. Output is a non-canonical `StreamSpec` ready for the connector’s routing logic.

**Call relations**: This helper is used at module load time to define streams like owners, workflows, forms, conversations, analytics reports, and email events.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 286–298)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Builds a reusable pagination recipe for HubSpot GET endpoints that return `results` plus a `paging.next.after` cursor. It prevents each simple product stream from needing to restate the same paging rules.

**Data flow**: Input is an API path. Output is a `Pagination` object describing where records live in the response, where the next cursor lives, and which query parameters control cursor and page size.

**Call relations**: This is used when defining product API streams that can be read through the generic pagination strategy instead of custom code.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 301–311)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Creates a stream definition for a relationship table, such as deal-to-contact links. These streams do not represent HubSpot objects themselves; they represent pairs of related objects.

**Data flow**: Input is the stream name and the parent object type to walk. Output is a `StreamSpec` with no cursor, because HubSpot does not expose reliable modified times for these relationship rows.

**Call relations**: This helper is used at module load time to define synthetic relationship streams later read by `HubSpotConnector._paginate_junction`.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 595–625)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the JSON body for HubSpot’s CRM search API. It asks for all known properties, sorts by the cursor field, and optionally filters to records changed since the saved cursor.

**Data flow**: Input is a stream, property names, a saved cursor, and a page cursor called `after`. Output is a dictionary sent as the POST body to HubSpot search.

**Call relations**: The normal CRM pagination path and the custom-object record path call this whenever they need the next search page.

*Call graph*: called by 2 (_paginate_custom_object_records, _paginate_unchecked).


##### `HubSpotConnector._flatten`  (lines 628–639)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a normal HubSpot CRM record into a simpler flat record. HubSpot puts most fields inside `properties`; this moves them up beside the id and timestamps.

**Data flow**: Input is one CRM record. Output is a dictionary with `id`, creation and update timestamps, archived status, and all property values at the top level.

**Call relations**: `HubSpotConnector.flatten` calls this for ordinary CRM streams before rows leave the connector.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 642–665)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records from HubSpot product APIs, whose shapes vary more than CRM records. It lifts useful nested fields and creates an `id` when HubSpot only provides `objectId`.

**Data flow**: Input is a product API record and its stream definition. It copies the record, promotes `properties` and form-submission `values`, fixes some date fields, and returns the flatter row.

**Call relations**: `HubSpotConnector.flatten` uses this for product API streams after the stream-specific paginator has fetched records.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 667–674)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening rule for a record based on its stream. This gives the rest of the system one predictable row shape even though HubSpot sends many different shapes.

**Data flow**: Input is a raw record and stream. Junction and custom-object rows pass through, product API rows go through product normalization, and normal CRM rows go through CRM flattening; the output is the row to store.

**Call relations**: The sync framework calls this after pages are fetched. It delegates to `_flatten` or `_flatten_product_api` when needed.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector._list_properties`  (lines 676–684)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which fields exist for a CRM object type. This matters because HubSpot search only returns properties that are explicitly requested.

**Data flow**: Input is an HTTP client and a HubSpot object name. It reads the object’s property metadata from HubSpot and returns a list of property names.

**Call relations**: `HubSpotConnector._paginate_unchecked` calls this before searching a normal CRM object so the sync can request every available field.

*Call graph*: called by 1 (_paginate_unchecked).


##### `HubSpotConnector.paginate`  (lines 686–699)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the safe public pagination entry for a stream. It yields records, but converts certain HubSpot permission errors into a skipped-stream result instead of a full sync failure.

**Data flow**: Input is an HTTP client, stream definition, and optional saved cursor. It yields pages from `_paginate_unchecked`; if HubSpot says the stream is inaccessible, it raises `StreamSkipped` with a clear reason.

**Call relations**: The source runner calls this during sync. It wraps `_paginate_unchecked` and uses `_is_stream_unavailable` plus `_stream_skip_reason` to decide whether an error is recoverable.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 701–753)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Routes each stream to the correct reading strategy. It is the central traffic director for HubSpot pagination.

**Data flow**: Input is a client, stream, and optional cursor. Depending on the stream, it uses a declared pagination strategy, a junction walker, custom-object logic, product API logic, or normal CRM search; it yields pages and deletion pages.

**Call relations**: `HubSpotConnector.paginate` calls this. It hands work to helpers such as `_paginate_product_api`, `_paginate_custom_objects`, `_paginate_junction`, and `_paginate_archived_ids`.

*Call graph*: calls 6 internal fn (_build_search_body, _list_properties, _paginate_archived_ids, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._is_stream_unavailable`  (lines 756–779)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether a HubSpot error means this account cannot access one stream. This keeps missing permissions from looking like broken networking or bad code.

**Data flow**: Input is an HTTP status error. It checks for a 403 response and looks for permission-related wording in HubSpot’s message, then returns true or false.

**Call relations**: `paginate` and archived-sweep helpers use this when deciding whether to skip or silently stop a stream-specific extra step.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 782–791)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Creates a human-readable explanation for why a HubSpot stream was skipped. It includes HubSpot’s own message when available.

**Data flow**: Input is a stream name and HTTP error. It reads the response body if possible and returns a short reason string.

**Call relations**: `HubSpotConnector.paginate` uses this right before raising `StreamSkipped`.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 793–828)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived, meaning deleted or hidden, CRM object ids after the normal search finishes. This lets the destination remove records that no longer exist as active objects in HubSpot.

**Data flow**: Input is a client and stream. It pages through HubSpot’s archived list endpoint and yields `StreamPage` deletion markers containing record ids.

**Call relations**: `_paginate_unchecked` calls this after normal CRM search. It uses `_is_archived_sweep_unsupported` and `_is_stream_unavailable` to quietly stop when HubSpot does not support the sweep.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 830–924)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct custom reader for product API streams. HubSpot product APIs differ enough that many need their own small route.

**Data flow**: Input is a client, stream, and optional cursor. It dispatches to the matching paginator for owners, lists, forms, conversations, analytics, events, associations, sequences, pipelines, or a generic GET collection.

**Call relations**: `_paginate_unchecked` calls this for product API streams. It then hands off to many specialized helpers that know each endpoint’s shape.

*Call graph*: calls 21 internal fn (_paginate_analytics_reports, _paginate_analytics_views, _paginate_association_labels, _paginate_associations, _paginate_campaign_assets, _paginate_consent_states, _paginate_conversation_messages, _paginate_email_events, _paginate_event_occurrences, _paginate_event_types (+11 more)); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._paginate_get_collection`  (lines 926–954)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a common HubSpot collection endpoint that returns records under `results` and pages with `after`. It is the reusable worker for many simple product APIs.

**Data flow**: Input is a client, path, optional limit, and optional extra query parameters. It repeatedly GETs pages, fixes missing ids from `objectId`, and yields lists of records until no next cursor remains.

**Call relations**: Many stream-specific paginators call this when their endpoint follows HubSpot’s standard collection pattern.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_product_api, _paginate_sequences, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 956–989)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Reads all custom object types defined in the HubSpot account. Custom objects are account-specific, so the connector must discover their schemas before it can fetch their records.

**Data flow**: Input is a client and optional cursor. It loads custom object schemas, builds a temporary stream for each object type, yields its records, and then yields tombstones for archived records.

**Call relations**: `_paginate_unchecked` calls this for the `custom_objects` stream. It uses schema helpers, `_paginate_custom_object_records`, and `_paginate_custom_object_archived_ids`.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 991–993)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of custom object schemas from HubSpot. A schema describes what a custom object type is called and what properties it has.

**Data flow**: Input is a client. It GETs HubSpot’s schema endpoint and returns only dictionary-shaped schema rows.

**Call relations**: Custom-object syncing and association discovery both call this to learn account-specific object types.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 996–1001)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Extracts the best object type identifier from a custom object schema. HubSpot may name this identifier in more than one field.

**Data flow**: Input is one schema dictionary. It checks known id-like fields in order and returns the first non-empty string, or `None` if none exists.

**Call relations**: Custom-object pagination, custom-object row building, and association object-type discovery use this to refer to custom objects consistently.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1004–1019)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Collects all useful property names from a custom object schema. This ensures search requests ask HubSpot for the fields needed to build meaningful records.

**Data flow**: Input is a schema. It gathers property names, primary display property, and secondary display properties without duplicates, then returns the name list.

**Call relations**: `_paginate_custom_objects` calls this before searching records for each custom object type.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1021–1056)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Searches records for one custom object type. It uses the same cursor-boundary deduplication idea as normal CRM search.

**Data flow**: Input is a client, temporary stream, schema, property list, and cursor. It posts search requests, skips duplicate boundary records, converts each raw record into a custom-object row, and yields pages.

**Call relations**: `_paginate_custom_objects` calls this for each discovered custom object schema. It relies on `_build_search_body` and `_custom_object_row`.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1058–1097)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Builds a useful flat row for one custom object record. It adds stable ids, display labels, titles, and schema context so custom objects are understandable later.

**Data flow**: Input is a raw record and its schema. If both object type and record id exist, it returns a row combining properties, record metadata, object labels, title fields, and timestamps; otherwise it returns `None`.

**Call relations**: `_paginate_custom_object_records` calls this for each custom object returned by HubSpot.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1099–1129)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived records for one custom object type. This lets deleted custom object records become tombstones too.

**Data flow**: Input is a client and custom object type id. It pages through archived records and yields deletion markers whose ids include both object type and record id.

**Call relations**: `_paginate_custom_objects` calls this after reading active records. It stops quietly for unsupported archived sweeps or unavailable streams.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1131–1150)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Derives team records from owner records. HubSpot exposes teams nested under owners, so this builds a separate team stream by collecting those nested values.

**Data flow**: Input is a client. It reads owners, extracts each team, deduplicates by team id, and yields one page of team rows.

**Call relations**: `_paginate_product_api` calls this for the `owner_teams` stream, using `_paginate_get_collection` to read owners.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_lists`  (lines 1152–1181)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot CRM lists through the lists search endpoint. It also promotes extra list properties into the main row.

**Data flow**: Input is a client. It posts list-search requests using offset pagination, adds an `id` from `listId`, merges `additionalProperties`, and yields pages until HubSpot reports no more results.

**Call relations**: `_paginate_product_api` uses this for the `lists` stream, and `_paginate_list_memberships` uses it to know which lists need membership lookups.

*Call graph*: called by 2 (_paginate_list_memberships, _paginate_product_api).


##### `HubSpotConnector._paginate_site_search`  (lines 1183–1203)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads CMS search results for a specific content type, such as knowledge articles. It supports endpoints that page by numeric offset instead of cursor.

**Data flow**: Input is a client and content type. It GETs site-search pages, yields dictionary records, and advances the offset until all results are read.

**Call relations**: `_paginate_product_api` calls this for knowledge articles.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1205–1229)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads assets attached to marketing campaigns. A campaign can contain many asset types, so this walks campaigns first and then fans out by asset type.

**Data flow**: Input is a client. It reads campaigns, extracts each campaign id and name, then asks `_paginate_campaign_asset_type` for every known campaign asset type.

**Call relations**: `_paginate_product_api` calls this for the `campaign_assets` stream. It uses `_paginate_get_collection` for the campaign list.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1231–1266)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one kind of asset for one campaign. It adds campaign context and a stable composite id to each asset row.

**Data flow**: Input is a client, campaign id, campaign name, and asset type. It pages that campaign’s asset endpoint, builds rows with asset id, type, kind, campaign id, campaign name, and metrics, and yields them.

**Call relations**: `_paginate_campaign_assets` calls this repeatedly. It ignores 403 and 404 responses because some campaign asset combinations are simply unavailable.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1268–1274)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields HubSpot analytics views as stream pages. Analytics views are saved filters or reporting views used later for report queries.

**Data flow**: Input is a client. It asks `_analytics_view_rows` for normalized view rows and yields them if any exist.

**Call relations**: `_paginate_product_api` calls this for the `analytics_views` stream.

*Call graph*: calls 1 internal fn (_analytics_view_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_view_rows`  (lines 1276–1307)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches and normalizes analytics view definitions. It gives each view a stable id, readable name, filters, and normalized dates.

**Data flow**: Input is a client. It reads HubSpot analytics views, accepts either list or object response shapes, skips invalid rows, and returns normalized dictionaries.

**Call relations**: Both analytics view pagination and analytics report pagination use this, because reports are queried once for all data and again for each available view.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1309–1339)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs many HubSpot analytics report queries to produce report rows. It covers different report subjects, time periods, and analytics views.

**Data flow**: Input is a client. It builds a date window, reads analytics views, then loops through report families, subjects, time periods, and view filters, yielding rows from each query.

**Call relations**: `_paginate_product_api` calls this for the `analytics_reports` stream. It delegates each actual endpoint call to `_paginate_analytics_report_query`.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_report_window`  (lines 1342–1343)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range used for analytics reports. The range starts at a fixed old date and ends today in UTC.

**Data flow**: No input. It returns a pair of strings formatted as `YYYYMMDD`: the configured start date and today’s date.

**Call relations**: `_paginate_analytics_reports` calls this before issuing report queries.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1345–1396)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Reads one analytics report endpoint for one subject, time period, and optional view. It also handles report combinations HubSpot does not support.

**Data flow**: Input is a client plus report family, subject, time period, optional view, and date range. It GETs pages with offsets, converts each response into rows, yields them, and stops when all breakdowns are read or the report is unsupported.

**Call relations**: `_paginate_analytics_reports` calls this inside its report-combination loops. It uses `_analytics_report_rows` to shape each response.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1399–1474)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Turns one raw analytics report response into rows suitable for storage. It separates overall totals from detailed breakdown rows.

**Data flow**: Input is the response data and report context. It creates a totals row when present, creates one row per breakdown, adds ids, names, metrics, filters, and formatted dates, then returns the list.

**Call relations**: `_paginate_analytics_report_query` calls this after every report response.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1477–1481)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Builds a stable id for an analytics report row from its identifying parts. It cleans characters that would make the id ambiguous.

**Data flow**: Input is any number of id parts. It converts them to strings, replaces `/` and `:` inside parts, joins them with colons, and prefixes the result with `analytics_report:`.

**Call relations**: `_analytics_report_rows` uses this when creating totals and breakdown rows.


##### `HubSpotConnector._analytics_report_date`  (lines 1484–1485)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Converts HubSpot report dates from compact `YYYYMMDD` form into normal `YYYY-MM-DD` form.

**Data flow**: Input is an eight-character date string. Output is the same date with hyphens inserted.

**Call relations**: `_analytics_report_rows` uses this to make report start and end dates easier to read.


##### `HubSpotConnector._paginate_event_types`  (lines 1487–1506)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the definitions of HubSpot event types. These describe kinds of events that can appear in the events stream.

**Data flow**: Input is a client. It reads the event-types endpoint, accepts list or object response shapes, assigns a stable id to each row, and yields one page if any rows exist.

**Call relations**: `_paginate_product_api` calls this for the `event_types` stream.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1508–1530)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual HubSpot event occurrences, optionally starting after a saved cursor. It creates an id when HubSpot does not provide one.

**Data flow**: Input is a client and optional cursor. It sends the cursor as `occurredAfter`, reads event rows, fills missing ids with `_synthetic_event_id`, and yields them.

**Call relations**: `_paginate_product_api` calls this for the `event_occurrences` stream.

*Call graph*: calls 1 internal fn (_synthetic_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._synthetic_event_id`  (lines 1533–1543)

```
def _synthetic_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Creates a stable fallback id for an event occurrence. This prevents otherwise-idless events from changing identity between syncs.

**Data flow**: Input is an event row and its position in the page. It combines event type, object type, object id, occurrence time or index, and a payload hash into one id string.

**Call relations**: `_paginate_event_occurrences` calls this only when HubSpot does not supply an event id.

*Call graph*: called by 1 (_paginate_event_occurrences).


##### `HubSpotConnector._paginate_email_events`  (lines 1545–1573)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads marketing email events, such as opens or clicks, from HubSpot’s email events API. It supports cursor-based incremental starts by converting saved times into HubSpot’s expected timestamp.

**Data flow**: Input is a client and optional cursor. It requests pages with a limit and optional start timestamp, creates fallback ids where needed, yields pages, and follows HubSpot’s offset until no more pages remain.

**Call relations**: `_paginate_product_api` calls this for the `email_events` stream. It uses `_email_event_start_timestamp` and `_synthetic_email_event_id`.

*Call graph*: calls 2 internal fn (_email_event_start_timestamp, _synthetic_email_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1576–1585)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts an email-event cursor into the millisecond timestamp HubSpot expects. It accepts either an existing numeric timestamp or an ISO date-time string.

**Data flow**: Input is a cursor string or `None`. It returns `None`, the integer value of a numeric string, or milliseconds parsed from an ISO date-time; invalid dates return `None`.

**Call relations**: `_paginate_email_events` calls this before each request.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._synthetic_email_event_id`  (lines 1588–1598)

```
def _synthetic_email_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Creates a stable fallback id for an email event. It uses the event’s timing, recipient, type, campaign, and payload hash.

**Data flow**: Input is an email event row and its page index. Output is a colon-separated id string with unsafe colons inside parts replaced.

**Call relations**: `_paginate_email_events` calls this when HubSpot does not provide an id.

*Call graph*: called by 1 (_paginate_email_events).


##### `HubSpotConnector._stable_payload_hash`  (lines 1601–1603)

```
def _stable_payload_hash(row: dict[str, Any]) -> str
```

**Purpose**: Creates a short stable fingerprint of a record’s full contents. It is used to make synthetic ids less likely to collide.

**Data flow**: Input is a dictionary. It serializes the dictionary in sorted-key order, hashes it with SHA-256, and returns the first 16 hex characters.

**Call relations**: Synthetic id helpers use this as the final distinguishing piece for records that lack HubSpot ids.

*Call graph*: 2 external calls (sha256, dumps).


##### `HubSpotConnector._paginate_association_labels`  (lines 1605–1621)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads labels that describe relationships between HubSpot object types, such as the named kind of link between a deal and a company.

**Data flow**: Input is a client. It walks object-type pairs that have labels, converts each label into a row with source and target object context, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the association-label stream. It uses `_association_pairs_with_labels` and `_association_label_row`.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_associations`  (lines 1623–1638)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual association links between HubSpot objects. It first finds which object-type pairs can have labels, then batches through source record ids to read their targets.

**Data flow**: Input is a client. For each pair of object types, it pages source object ids, sends batch association reads, and yields flat relationship rows.

**Call relations**: `_paginate_product_api` calls this for the broad `associations` stream. It relies on `_association_pairs_with_labels`, `_paginate_crm_object_id_pages`, and `_paginate_association_batch`.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_association_batch`  (lines 1640–1667)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads association results for a batch of source records. It also follows per-record paging when one source record has more associated targets than fit in one response.

**Data flow**: Input is source and target object types plus batch inputs. It posts the batch request, converts results into rows, yields them, then builds the next pending inputs from embedded paging cursors.

**Call relations**: `_paginate_associations` calls this after collecting source ids. It uses `_association_rows`, `_next_association_inputs`, and optional-pair error handling.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1670–1684)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Finds follow-up association batch inputs for records that have more association pages. This is needed because HubSpot can page each source record separately inside one batch response.

**Data flow**: Input is a batch association response. It looks for each result’s source id and next `after` cursor, returning new input dictionaries for only those that need another request.

**Call relations**: `_paginate_association_batch` calls this after processing a batch response.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1686–1699)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Discovers object-type pairs that have association labels. Only pairs with labels are yielded, which avoids doing expensive association reads for unsupported pairs.

**Data flow**: Input is a client. It gets all known object types, asks HubSpot for labels for every from/to pair, and yields pairs whose label list is not empty.

**Call relations**: Both association-label and association-link pagination use this as their discovery step.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1701–1714)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the list of HubSpot object types to consider for associations. It starts with known standard objects and adds account-specific custom objects.

**Data flow**: Input is a client. It returns standard object type names plus any custom object ids found in schemas, skipping optional schema failures when appropriate.

**Call relations**: `_association_pairs_with_labels` calls this before checking pair labels.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1716–1732)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches association labels for one source object type and one target object type. Missing or unsupported pairs return an empty list rather than failing discovery.

**Data flow**: Input is a client and two object type names. It GETs the pair’s labels endpoint and returns valid label dictionaries, or an empty list for optional unavailable pairs.

**Call relations**: `_association_pairs_with_labels` calls this for every from/to pair it tests.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1735–1752)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Turns one raw association label into a stable row. It adds the source object type, target object type, type id, category, and display label.

**Data flow**: Input is a label dictionary plus from/to object types. Output is a row with a composite id and normalized label fields.

**Call relations**: `_paginate_association_labels` calls this while building association-label pages.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1754–1766)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Reads only ids for a CRM object type. This is a lightweight way to prepare batch lookups that do not need full object data.

**Data flow**: Input is a client and object type. It reads CRM object pages with just `hs_object_id`, extracts ids as strings, and yields id lists.

**Call relations**: Association pagination and sequence-enrollment pagination call this when they need to fan out from object ids.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1768–1798)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple pages from HubSpot’s CRM object list endpoint. It is used when search is not needed and only a few properties are required.

**Data flow**: Input is a client, object type, and property names. It GETs pages, yields dictionary rows, follows `after`, and stops quietly for optional unavailable object types.

**Call relations**: `_paginate_crm_object_id_pages` and `_paginate_contact_identity_pages` call this as their lower-level CRM list reader.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1801–1835)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Converts a batch association response into flat relationship rows. It expands one source record with many target records and possibly many association types into separate rows.

**Data flow**: Input is response data plus source and target object type names. It walks each result, extracts source and target ids, expands association types, and returns row dictionaries.

**Call relations**: `_paginate_association_batch` calls this after every successful batch read.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1838–1865)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one normalized association row. It records both sides of the relationship and the label or type metadata if HubSpot provided it.

**Data flow**: Input is association type metadata, source and target object details, and a fallback index. Output is a row with a stable composite id, object ids, category, label, and raw association type list.

**Call relations**: `_association_rows` uses this for each individual relationship it expands.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1868–1871)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether an error means an optional object pair or endpoint is simply unavailable. This lets broad discovery continue across many HubSpot product features.

**Data flow**: Input is an HTTP error. It returns true for 400 or 404 responses, or for permission-style unavailable streams; otherwise false.

**Call relations**: Association, list-membership, consent, CRM-list, sequence, and campaign-related helpers use this when probing optional HubSpot surfaces.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1873–1887)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads which records belong to HubSpot lists. It first reads the lists themselves, then walks memberships for each list.

**Data flow**: Input is a client. It iterates list records, extracts each list id, and yields membership pages returned by `_paginate_memberships_for_list`.

**Call relations**: `_paginate_product_api` calls this for the `list_memberships` stream, and it depends on `_paginate_lists` for the parent list set.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1889–1932)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads membership rows for one HubSpot list. Each row connects a list to one member record and carries list context.

**Data flow**: Input is a client, the list record, and the list id. It pages the memberships endpoint, builds composite ids from list id and record id, adds list metadata, and yields pages.

**Call relations**: `_paginate_list_memberships` calls this once per list. It treats optional unavailable endpoints as empty.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1934–1947)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot communication subscription definitions. These describe the types of email or communication preferences users can consent to or unsubscribe from.

**Data flow**: Input is a client. It reads the definitions endpoint, accepts either common response field, assigns ids when needed, and yields one page of rows.

**Call relations**: `_paginate_product_api` calls this for the `subscription_definitions` stream.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_consent_states`  (lines 1949–1962)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads communication consent and unsubscribe state for contacts with email addresses. This connects contact identities to their current subscription preferences.

**Data flow**: Input is a client. It pages contacts with emails, asks for subscription status rows and unsubscribe-all rows for each email, and yields combined pages.

**Call relations**: `_paginate_product_api` calls this for `consent_states`. It relies on `_paginate_contact_identity_pages`, `_consent_status_rows`, and `_unsubscribe_all_rows`.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1964–1980)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads contact records with their email addresses. It normalizes email whether HubSpot places it at the top level or inside `properties`.

**Data flow**: Input is a client. It reads contact pages with the `email` property, attaches a top-level email field to each row, and yields pages.

**Call relations**: `_paginate_consent_states` calls this before making per-email consent requests.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 1982–2003)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Reads subscription-specific consent statuses for one contact email. It asks HubSpot what that email’s status is for email-channel subscriptions.

**Data flow**: Input is a client, contact row, and email. It URL-escapes the email, GETs the statuses endpoint, converts each result with `_consent_row`, and returns the rows.

**Call relations**: `_paginate_consent_states` calls this for each contact email. Optional unavailable errors become an empty result.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 2005–2029)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Reads unsubscribe-all status for one contact email. This captures the broader case where a person opted out of all email communication.

**Data flow**: Input is a client, contact row, and email. It URL-escapes the email, calls the unsubscribe-all endpoint, converts results with `_consent_row`, and returns them.

**Call relations**: `_paginate_consent_states` calls this alongside `_consent_status_rows` for each contact email.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 2032–2062)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Builds one normalized consent-state row. It makes subscription and unsubscribe-all results look consistent.

**Data flow**: Input is a raw consent row, contact, email, and status kind. Output is a row with a stable id, contact id, subject email, purpose, subscription type, status, legal basis, source, and timestamps.

**Call relations**: `_consent_status_rows` and `_unsubscribe_all_rows` call this after receiving HubSpot consent responses.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2064–2091)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sales sequences for HubSpot users. Because sequences are requested by user id, it first derives users from owners.

**Data flow**: Input is a client. It gets sequence-capable user rows, requests sequences for each user id, adds owner context to each sequence, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the `sequences` stream. It uses `_sequence_user_rows` and `_paginate_get_collection`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_user_rows`  (lines 2093–2116)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the list of HubSpot user ids needed to query sales sequences. It derives those user ids from owner records and removes duplicates.

**Data flow**: Input is a client. It reads owners, collects unique `userId` values with owner id and email context, and yields one page of user rows.

**Call relations**: `_paginate_sequences` calls this before requesting sequences per user.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2118–2136)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sequence enrollment information for contacts. It fans out from contact ids because HubSpot exposes enrollments per contact.

**Data flow**: Input is a client. It pages contact ids, calls the enrollment endpoint for each contact, converts responses into rows, and yields pages.

**Call relations**: `_paginate_product_api` calls this for `sequence_enrollments`. It uses `_paginate_crm_object_id_pages` and `_sequence_enrollment_rows`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2139–2153)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes enrollment responses for one contact. It handles either a list of results or a single object response.

**Data flow**: Input is response data and contact id. It creates rows with ids from HubSpot when present or from contact and sequence information when not, and adds the contact id.

**Call relations**: `_paginate_sequence_enrollments` calls this after each per-contact enrollment request.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2155–2184)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads submissions for HubSpot forms. It first discovers forms, then reads submission pages for each form.

**Data flow**: Input is a client. It pages forms, extracts form ids, pages submissions for each form, creates stable submission ids, adds form id and form name, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the `form_submissions` stream, using `_paginate_get_collection` for both forms and submissions.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2186–2201)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages inside HubSpot conversation threads. Threads are read first because messages live under a thread-specific endpoint.

**Data flow**: Input is a client. It pages conversation threads, then pages messages for each thread and adds the thread id to every message row.

**Call relations**: `_paginate_product_api` calls this for the `conversation_messages` stream.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipelines`  (lines 2203–2210)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads deal and ticket pipelines as normalized rows. Pipelines define the high-level workflow containers for those object types.

**Data flow**: Input is a client. It asks for pipeline rows for deals and tickets and yields a page for each object type when rows exist.

**Call relations**: `_paginate_product_api` calls this for the `pipelines` stream. It delegates object-specific shaping to `_pipeline_rows_for_object_type`.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2212–2262)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads stages inside deal and ticket pipelines. Stages are the steps within each pipeline, such as open, closed, won, or ticket states.

**Data flow**: Input is a client. It fetches raw pipelines for each supported object type, expands their `stages`, adds stable ids and status fields, and yields stage rows.

**Call relations**: `_paginate_product_api` calls this for the `pipeline_stages` stream. It uses `_raw_pipelines_for_object_type` for the source data.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2264–2287)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes pipelines for one object type. It adds object-kind context and an active-or-archived status.

**Data flow**: Input is a client and object type such as deals or tickets. It reads raw pipelines, skips rows without ids, and returns rows with composite ids, names, and status.

**Call relations**: `_paginate_pipelines` calls this for each supported pipeline object type.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2289–2300)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches raw pipeline data for one HubSpot object type. It treats missing or forbidden pipeline endpoints as no data.

**Data flow**: Input is a client and object type. It GETs the pipeline endpoint and returns valid result dictionaries, or an empty list for 403 and 404 responses.

**Call relations**: Pipeline and pipeline-stage pagination call this as their shared low-level reader.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2303–2307)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes the specific HubSpot error that says paging through deleted objects is not supported. This lets the connector skip only the tombstone sweep when HubSpot cannot provide it.

**Data flow**: Input is an HTTP error. It checks for status 400 and looks for HubSpot’s known unsupported-deleted-objects message, returning true or false.

**Call relations**: Archived-id sweep functions use this when deciding whether to stop quietly or re-raise an error.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2310–2318)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Safely extracts HubSpot’s message string from an error response. It avoids crashing if the response body is not JSON or has an unexpected shape.

**Data flow**: Input is an HTTP error. It tries to parse JSON, reads the `message` field when present, and returns a string or `None`.

**Call relations**: `_is_archived_sweep_unsupported` uses this to inspect HubSpot’s specific error text.


##### `HubSpotConnector._paginate_junction`  (lines 2320–2367)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple relationship streams, such as deal-contact or ticket-company links, from inline associations on parent object pages. These are synthetic rows made from pairs of object ids.

**Data flow**: Input is a client, parent object type, and target object type. It pages parent objects with requested associations, emits one row per parent-target pair, and stops when no next page remains.

**Call relations**: `_paginate_unchecked` calls this for configured junction streams. The rows pass through `flatten` unchanged because they are already flat.

*Call graph*: called by 1 (_paginate_unchecked).


### `extensions/sources/ufo_ext_sources/salesforce.py`

`io_transport` · `source sync`

Salesforce stores business data in objects called SObjects, such as Account or Contact. This connector is the bridge between those Salesforce objects and this project’s source-sync system. Without it, the system would not know which Salesforce objects to read, how to ask Salesforce for them, or how to notice records that were deleted.

The file first defines the list of Salesforce streams the project cares about. Each stream says: “read this Salesforce object, use Id as the stable record key, and use SystemModstamp as the change timestamp.” That timestamp acts like a bookmark, so later syncs can ask only for records changed after the last run instead of rereading everything.

When a stream is synced, the connector first asks Salesforce to describe the object’s fields. This avoids hard-coding a schema; it reads whatever fields the customer’s Salesforce organization exposes. It then builds a SOQL query, which is Salesforce’s SQL-like query language, and follows Salesforce’s paginated results until there are no more pages. If this is an incremental sync with an existing cursor, it also asks Salesforce for records deleted since that cursor and emits a special tombstone page so downstream storage can remove or mark those vanished records.

One important detail is that Salesforce organizations live on different host names, so this connector has no fixed base URL. It expects the runner to provide the resolved Salesforce instance URL and authentication. If Salesforce refuses access with a 401 or 403 response, the stream is skipped with a clear permission error instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 29–36)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates one stream definition for a Salesforce object. It gives the sync system the stream name, the Salesforce object name, the primary key field, and the timestamp field used as the sync bookmark.

**Data flow**: It receives a friendly stream name, a Salesforce SObject name, and whether the stream is considered canonical. It packages those values together with the fixed primary key Id and cursor field SystemModstamp. The result is a StreamSpec object that the connector later uses to know what to query.

**Call relations**: This helper is used while the module is loaded to build the Salesforce stream list. It hands each stream definition to StreamSpec, and those definitions are later used by SalesforceConnector.paginate when real sync work begins.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 76–80)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: This function builds the Salesforce query text used to fetch records for one stream. It includes only changed records when a cursor is available, which keeps later syncs smaller and faster.

**Data flow**: It takes the stream definition, the field names discovered from Salesforce, and an optional cursor value. It joins the fields into a SELECT clause, adds a WHERE clause if there is a previous cursor, orders by the cursor field, and limits the page size. It returns one SOQL query string ready to send to Salesforce.

**Call relations**: SalesforceConnector.paginate calls this after it has learned the available fields. The returned query becomes the q parameter for Salesforce’s query endpoint, which starts the page-by-page read.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 82–88)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: This function asks Salesforce what fields exist on a given object. That lets the connector adapt to each Salesforce organization instead of relying on a hand-written list of fields.

**Data flow**: It receives an HTTP client and a Salesforce object name. It sends a request to Salesforce’s describe endpoint, reads the fields list from the response, keeps valid field names, and returns them as strings. It does not change local state.

**Call relations**: SalesforceConnector.paginate calls this before building the query. Its field list is passed into SalesforceConnector._build_soql so the query can request all exposed columns for that object.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 90–117)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main read loop for a Salesforce stream. It fetches records page by page, follows Salesforce’s next-page links, and adds delete information when doing an incremental sync.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor from a previous sync. It first discovers the object’s fields, builds a SOQL query, sends the query to Salesforce, yields any records it receives, and follows nextRecordsUrl until Salesforce says the query is done. If there was a cursor, it then asks for records deleted since that cursor and yields a StreamPage containing delete tombstones when needed. If Salesforce returns a permission or authentication refusal, it turns that into a StreamSkipped error with a useful message.

**Call relations**: This function is the connector’s main contribution to the sync runner. During a stream sync, the runner calls it to obtain batches of records. Inside, it relies on _describe_fields to learn columns, _build_soql to create the query, and _deleted_page to report hard-deleted records after the normal record pages are finished.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 119–137)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: This function asks Salesforce which records were hard-deleted during a time window. It turns those deleted record IDs into a tombstone page so the rest of the system can remember that the records disappeared.

**Data flow**: It receives an HTTP client, a stream definition, and the previous cursor time. It sets the window end to the current UTC time, calls Salesforce’s deleted-records endpoint, extracts deleted record IDs, and chooses the next cursor from Salesforce’s latest covered date or the window end. It returns a StreamPage with delete IDs and the next cursor, or nothing if there is no meaningful page.

**Call relations**: SalesforceConnector.paginate calls this only when there is already a cursor, meaning the sync is incremental. The StreamPage it returns is yielded after ordinary record pages, giving downstream sync code both updates and deletions for the same time window.

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 139–142)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function removes Salesforce’s metadata wrapper from each record before the record is stored or processed further. It keeps the business fields and drops the special attributes field that Salesforce adds to API responses.

**Data flow**: It receives one Salesforce record and the stream definition. If the record contains an attributes key, it returns a copy without that key. If there is no such key, it returns the original record unchanged.

**Call relations**: This fits into the connector cleanup step after records have been fetched. It does not call other project functions here; it simply shapes Salesforce API output into cleaner field data for the broader sync pipeline.


### Support desks and help centers
Connectors that stream tickets, users, organizations, knowledge-base content, forums, communities, and related support artifacts.

### `extensions/sources/ufo_ext_sources/freshdesk.py`

`io_transport` · `source sync / API fetching`

Freshdesk exposes helpdesk data through a web API, but not all parts of that API are shaped the same way. Some lists use ordinary “next page” links, tickets use page numbers, and nested content like conversations or knowledge-base articles must be found by first walking through their parent objects. This file is the adapter that hides those differences.

It defines the Freshdesk streams the system knows about, such as tickets, companies, contacts, solution articles, and discussion comments. A stream is a named kind of record that can be synced. The FreshdeskConnector then creates an HTTP client using Freshdesk’s API-key authentication, chooses the right fetching strategy for each stream, and yields batches of plain record dictionaries.

The main idea is like collecting files from a filing cabinet. For simple drawers, it reads one page after another. For tickets, it follows Freshdesk’s special numbered-page rules and can start from an “updated since” cursor so old data is not reread unnecessarily. For nested areas, it first lists parents, then opens each parent to collect children. If Freshdesk says the key is invalid or not allowed, the stream is skipped with a clear message instead of crashing the whole sync unexpectedly.

#### Function details

##### `_stream`  (lines 55–69)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a StreamSpec, which is the small description the sync system uses to know what a Freshdesk record type is called, what its source name is, which field identifies each record, and whether it supports cursor-based syncing.

**Data flow**: It receives a stream name and optional details such as the Freshdesk source object, primary key, cursor field, and whether it is a main stream. It fills in sensible defaults, then returns a StreamSpec object that can be placed in the connector’s stream list.

**Call relations**: This helper is used while the file is loaded to build FRESHDESK_STREAMS. It hands each stream definition to StreamSpec.__init__, so the rest of the connector can later route sync requests by stream name.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector._make_client`  (lines 109–124)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to a specific Freshdesk tenant. It also applies the correct authentication method: either a provided transport from a credential broker, or Freshdesk’s documented API-key-as-username Basic Auth.

**Data flow**: It receives a base URL and a resolved Credential. It trims the URL, prepares JSON headers and timeout settings, then returns an httpx.AsyncClient. If the credential has a custom transport, that transport is used as-is. If it has a direct API key, the key is sent through Basic Auth with "X" as the password. If neither is present, it raises an error because it cannot safely contact Freshdesk.

**Call relations**: The wider RestConnector flow calls this when a sync run needs a network client. This function delegates the low-level pieces to httpx.Timeout, httpx.BasicAuth, and httpx.AsyncClient, then the pagination methods use the returned client for actual API requests.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 127–137)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: Builds the query parameters Freshdesk expects when fetching ticket pages. It keeps ticket syncing consistent by always requesting tickets in updated-time order and by adding an incremental cursor when one is available.

**Data flow**: It receives an optional cursor timestamp and a page number. It creates a parameter dictionary with page size, page number, sort order, and requested included ticket details. If a cursor was supplied, it adds it as updated_since. The finished dictionary is returned to the ticket fetcher.

**Call relations**: FreshdeskConnector._paginate_tickets calls this before each ticket request. The returned parameters are then passed into the API call so ticket pagination follows Freshdesk’s special rules.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 139–213)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct paging strategy for the requested Freshdesk stream and yields batches of records. This is the main routing point that turns many different Freshdesk API shapes into one common stream interface.

**Data flow**: It receives an HTTP client, a StreamSpec, and an optional cursor. It checks the stream name, then sends the request to the matching helper: ticket paging, conversation paging, two-level nested paging, three-level nested paging, or simple link-header paging. It yields each page of records it gets back. If Freshdesk returns 401 or 403, meaning unauthorized or forbidden, it raises StreamSkipped with a readable explanation.

**Call relations**: The sync framework calls this when it wants records for a stream. This function then calls FreshdeskConnector._paginate_tickets, FreshdeskConnector._paginate_conversations, FreshdeskConnector._paginate_two_level, FreshdeskConnector._paginate_three_level, or FreshdeskConnector._paginate_link_header depending on the stream. It is the dispatcher that keeps the rest of the system from needing to know Freshdesk’s API quirks.

*Call graph*: calls 6 internal fn (__init__, _paginate_conversations, _paginate_link_header, _paginate_three_level, _paginate_tickets, _paginate_two_level).


##### `FreshdeskConnector._paginate_link_header`  (lines 215–222)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches ordinary Freshdesk list endpoints that advertise the next page through an HTTP Link header. A Link header is a response header that points to related pages, such as “next”.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST connector machinery to follow Link headers with a page size of 100, then yields each page of records as it arrives.

**Call relations**: FreshdeskConnector.paginate uses this for simple streams. The nested pagination helpers also use it whenever they need to list parents or children. It relies on the base RestConnector’s _get_link_header_pages behavior to do the actual page-to-page walking.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 224–241)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Freshdesk tickets using Freshdesk’s special page-number system. It supports incremental syncing by starting from a cursor timestamp and stops before hitting Freshdesk’s documented page ceiling.

**Data flow**: It receives an HTTP client and an optional cursor. Starting at page 1, it builds ticket parameters, requests /api/v2/tickets, normalizes the response into a list of records, and yields that list. It stops when there are no records, when the page is shorter than the maximum page size, or when the 300-page ceiling is reached.

**Call relations**: FreshdeskConnector.paginate calls this for the tickets stream, and FreshdeskConnector._paginate_conversations calls it first so it can discover ticket IDs before fetching each ticket’s conversations. It calls FreshdeskConnector._build_tickets_params for each request.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, paginate).


##### `FreshdeskConnector._paginate_conversations`  (lines 243–259)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches conversations attached to tickets. Freshdesk conversations are not listed as one flat global stream here, so this function first finds tickets and then opens each ticket’s conversation list.

**Data flow**: It receives an HTTP client and an optional cursor. It pages through tickets admitted by that cursor, reads each ticket ID, then fetches that ticket’s conversations. Before yielding a conversation page, it makes sure each conversation record includes the ticket_id, so downstream code can tell which ticket it belongs to.

**Call relations**: FreshdeskConnector.paginate calls this for the conversations stream. This function depends on FreshdeskConnector._paginate_tickets to find tickets and FreshdeskConnector._paginate_link_header to page through the conversations for each ticket.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (paginate).


##### `FreshdeskConnector._paginate_two_level`  (lines 261–272)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Freshdesk data that lives one level below a parent object, such as responses inside canned-response folders or forums inside discussion categories.

**Data flow**: It receives an HTTP client, a parent API path, and a child path template containing a parent ID placeholder. It pages through all parents, takes each parent’s id, formats the child path for that id, then yields every child page found there. Parents without an id are skipped.

**Call relations**: FreshdeskConnector.paginate calls this for several nested streams, including canned responses, solution folders, discussion forums, discussion topics, and discussion comments. This helper uses FreshdeskConnector._paginate_link_header for both the parent listing and each child listing.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


##### `FreshdeskConnector._paginate_three_level`  (lines 274–297)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Freshdesk data that is buried two levels below a root object, such as solution articles inside folders inside solution categories.

**Data flow**: It receives an HTTP client plus path templates for the root, middle, and leaf levels. It lists root records, extracts each root id, lists middle records under that root, extracts each middle id, then lists and yields the final leaf records. Any record missing the needed id is skipped.

**Call relations**: FreshdeskConnector.paginate calls this for the solution_articles stream. It repeatedly uses FreshdeskConnector._paginate_link_header to walk each level of the tree, turning Freshdesk’s category → folder → article structure into a simple stream of article pages.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/zendesk.py`

`io_transport` · `source sync`

Zendesk exposes its data through many web API endpoints, and those endpoints do not all behave the same way. This file is the translator between Zendesk’s API shape and the project’s standard source connector shape. Without it, the system would not know which Zendesk pages exist, where to fetch them, how to move through multiple pages of results, or how to recover useful details like ticket requester emails.

The file first defines the list of Zendesk streams, where each stream is one kind of thing to read, such as tickets, users, article comments, or community posts. Think of these streams like labeled conveyor belts: each belt carries one kind of Zendesk record.

The `ZendeskConnector` then decides how each belt should be loaded. Some large Zendesk objects use an incremental cursor API, which means Zendesk can return only records changed after a certain time. Simpler objects use normal page-by-page links. Ticket comments are special because they are buried inside ticket event records, so the connector extracts only the comment events and adds the ticket ID. User identities are also special because the connector first reads users, then asks Zendesk for each user’s identities.

The connector is read-only. It does not store credentials itself; it relies on the wider runner and authentication layer to supply access. If Zendesk refuses access with an authorization error, the stream is skipped with a clear message instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 58–72)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Zendesk stream, such as its name, Zendesk API object, primary key, and timestamp field. It keeps the long stream list readable and consistent.

**Data flow**: It receives basic stream settings, such as a public stream name and optional source object name. It fills in sensible defaults, then creates and returns a `StreamSpec`, which is the project’s standard record describing how a stream should be synced.

**Call relations**: This helper is used while building the file’s `ZENDESK_STREAMS` list. It hands each completed stream description to `StreamSpec`, so the connector later has a ready-made catalog of Zendesk data types to offer.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 133–158)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: This function enriches records using related data that Zendesk returned in the same API response. In practice, it adds useful email fields to ticket records by looking up included user records.

**Data flow**: It receives the main records, the full API page, and instructions for which ID fields should be matched to which included records. It builds a quick lookup table from the included side data, then fills missing target fields, such as requester or assignee email, on the original records. It changes the records in place and returns nothing.

**Call relations**: The incremental ticket reader calls this after Zendesk returns tickets with sideloaded users. It acts like a clerk matching ticket forms to a nearby address book, then writing the matching emails directly onto the ticket records before they are yielded to the sync system.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 167–168)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This function decides which field in a Zendesk JSON response contains the actual list of records for a stream. Most streams use their own name, but a few Zendesk endpoints use different names.

**Data flow**: It receives a stream description. It checks whether that stream has a special response-field override, and returns either the override or the stream name.

**Call relations**: The default page reader calls this before reading records from a Zendesk response. This keeps endpoint quirks in one small place instead of scattering special cases throughout the pagination loop.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 171–183)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: This function turns the saved sync position into the Unix timestamp format Zendesk expects. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor, which may be missing, already numeric, or an ISO date string such as `2024-01-01T00:00:00Z`. If the cursor is empty or cannot be parsed, it returns `0`, meaning start from the beginning. If it is valid, it returns the matching timestamp as an integer.

**Call relations**: All incremental readers call this before asking Zendesk for changed records. It is the bridge between the system’s stored cursor format and Zendesk’s `start_time` query parameter.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 186–195)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: This function converts Zendesk’s full next-page URL into just the path and query part needed by the connector’s request helper. It also safely stops pagination when there is no usable next page.

**Data flow**: It receives a next-page URL or nothing. If there is no URL or no path, it returns `None`. Otherwise it parses the URL, keeps the path and query string, and returns that smaller request path.

**Call relations**: Every pagination method uses this after each API response. Zendesk often gives a full URL for the next page, while the connector’s lower-level request method works with paths, so this function adapts one format to the other.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 197–221)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading a Zendesk stream. It chooses the right paging strategy for each kind of Zendesk data and turns authorization refusals into a clear skipped-stream signal.

**Data flow**: It receives an HTTP client, a stream description, and the current cursor. It checks the stream name, delegates to the matching pagination method, and yields batches of records as they arrive. If Zendesk responds with 401 or 403, meaning unauthorized or forbidden, it raises `StreamSkipped` with an explanatory message; other HTTP errors are allowed to keep failing normally.

**Call relations**: The wider sync framework calls this when it wants records from a Zendesk stream. `paginate` then routes special streams like ticket comments and user identities to their custom readers, routes large incremental streams to the cursor reader, and sends all remaining streams to the default reader.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 223–244)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads high-volume Zendesk streams using Zendesk’s incremental cursor export API. That API is designed for syncing changes over time without rereading everything on every run.

**Data flow**: It receives an HTTP client, a stream description, and a cursor. It turns the cursor into a Zendesk `start_time`, builds the first request path, repeatedly fetches pages, extracts the records, optionally enriches tickets with sideloaded user emails, yields non-empty batches, and follows Zendesk’s next-page links until Zendesk says the stream has ended.

**Call relations**: `paginate` calls this for large streams such as tickets, users, organizations, and ticket metric events. Inside the loop it relies on `_cursor_to_unix` to start at the right time, `_apply_sideload` to enrich tickets, and `_next_page_path` to move to the next API page.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 246–256)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads ordinary Zendesk endpoints that use simple page-by-page navigation. It is the general fallback for streams that do not need a special incremental or nested-data strategy.

**Data flow**: It receives an HTTP client and a stream description. It builds the first `/api/v2/...json` request with a page size, fetches each page, extracts the list of records from the correct response field, yields any records found, and follows `next_page` until there are no more pages.

**Call relations**: `paginate` calls this for most standard Zendesk streams. It asks `_data_field` which JSON field contains the records and asks `_next_page_path` how to turn Zendesk’s next-page URL into the next request path.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 258–284)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads ticket comments even though Zendesk does not expose them here as a simple flat list. It pulls them out of ticket event pages and turns each comment event into its own record.

**Data flow**: It receives an HTTP client and a cursor. It starts from the matching incremental ticket events URL, fetches event pages, looks through each event’s child events, keeps only child events whose type is `Comment`, copies each comment, adds the parent ticket ID, fills `created_at` from the event timestamp when needed, yields comment batches, and follows page links until the stream ends.

**Call relations**: `paginate` calls this only for the `ticket_comments` stream. It uses `_cursor_to_unix` to start at the right point in time and `_next_page_path` to follow Zendesk’s continuation links.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_user_identities`  (lines 286–311)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads the identity records attached to Zendesk users, such as email or login identities. Zendesk requires this as a two-step process: find users first, then fetch identities for each user.

**Data flow**: It receives an HTTP client and a cursor. It pages through incrementally changed users, skips malformed users or users without an ID, then for each valid user it requests that user’s identities page by page. It yields identity batches when found, then continues through both identity pages and user pages until Zendesk reports the user stream has ended.

**Call relations**: `paginate` calls this only for the `users_identities` stream. It uses `_cursor_to_unix` to choose which users to inspect and `_next_page_path` both for user pagination and for each user’s identity pagination.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).


### Customer conversations
Connectors focused on customer messaging, conversation histories, contacts, teams, tags, and activity logs.

### `extensions/sources/ufo_ext_sources/intercom.py`

`io_transport` · `sync-time API reading`

Intercom does not expose all of its data in one simple way. Some records are found through search requests, some through a scrolling company feed, some through plain list endpoints, and some are nested inside parent records. This file is the adapter that hides those differences from the rest of UFO.

It defines the Intercom streams UFO can read, including their names, primary keys, and cursor fields. A cursor is the saved “last seen” value used to continue a later sync instead of starting over. For most Intercom search streams, that cursor is an `updated_at` Unix timestamp, meaning a number of seconds since 1970.

The `IntercomConnector` builds an authenticated HTTP client, adds the Intercom API version header, chooses the right pagination method for each stream, and yields lists of records. It also flattens a few nested fields into easier-to-query top-level fields. For example, a conversation’s nested source subject becomes `source__subject`, and a contact’s first company becomes `org_id`.

A notable behavior is permission handling. If Intercom refuses a stream with HTTP 401 or 403, this connector marks that stream as skipped instead of crashing the whole sync. This matters because Intercom accounts often grant access to some objects but not others.

#### Function details

##### `_stream`  (lines 52–66)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: This small helper creates a stream description for one Intercom object type. A stream description tells UFO what the stream is called, what Intercom object it represents, which field uniquely identifies records, and which field can be used as the sync cursor.

**Data flow**: It receives a stream name and optional details such as the source object name, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults, then creates and returns a `StreamSpec`, which is the shared UFO object used to describe a readable stream.

**Call relations**: This helper is used while the file is loaded to build `INTERCOM_STREAMS`. Those stream descriptions are later read by `IntercomConnector` so the connector knows what Intercom data it can sync.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector._make_client`  (lines 103–106)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Intercom. Its special job is to add the `Intercom-Version` header, which tells Intercom which version of its API responses this connector expects.

**Data flow**: It receives a base URL and a resolved credential. It asks the parent REST connector to create the authenticated HTTP client, adds the Intercom API version header to that client, and returns the modified client.

**Call relations**: This runs during connector setup, before any pages are requested. The rest of the pagination methods depend on the returned client so their requests are authenticated and use the expected Intercom API version.


##### `IntercomConnector._build_search_body`  (lines 109–139)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: This builds the JSON body used for Intercom search endpoints. It tells Intercom how many records to return, where to continue within a multi-page result, and which records are newer than the saved cursor.

**Data flow**: It receives a stream description, the saved cursor from the last sync, and an optional `starting_after` page token from Intercom. It creates a request body with a page size, ascending sort order, optional page continuation token, and a query like “cursor field is greater than this value.” If the cursor looks like a number, it sends it as a number because Intercom rejects a numeric timestamp sent as text. The result is a dictionary ready to send as JSON.

**Call relations**: `IntercomConnector._paginate_search` uses this for normal search streams such as conversations, contacts, and tickets. `IntercomConnector._paginate_conversation_parts` also uses it first to find the parent conversations whose parts need to be fetched.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 142–147)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: This picks the first dictionary record out of a list-like nested Intercom field. It exists because Intercom often wraps related objects, such as contacts or companies, inside small nested lists.

**Data flow**: It receives any value. If the value is a non-empty list and its first item is a dictionary, it returns that first dictionary. Otherwise it returns `None`, meaning there is no usable first record.

**Call relations**: This is a local helper for the flattening code. The conversation and contact flattening steps use it when they need one representative related object, such as the first contact on a conversation or the first company on a contact.


##### `IntercomConnector._flatten_conversation`  (lines 150–167)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes important nested conversation fields easier for later database queries to use. It copies selected nested source fields and the requester contact ID onto top-level keys.

**Data flow**: It receives one conversation record from Intercom. It makes a shallow copy, reads `source.type`, `source.subject`, and `source.body` if they exist, and writes them as `source__type`, `source__subject`, and `source__body`. It also looks for the first nested contact and writes that contact’s ID as `requester_id`. It returns the enriched copy without changing the original record in place.

**Call relations**: `IntercomConnector.flatten` calls this only for the `conversations` stream. The flattened result then continues through the normal UFO sync path with fields that are easier to transform or query.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 170–179)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes a conversation part’s author easier to read later. Conversation parts can be replies, notes, or other pieces inside a conversation, and their author is nested inside an `author` object.

**Data flow**: It receives one conversation part record. It copies the record, checks whether `author` is a dictionary, and if so writes `author_type` and `author_id` as top-level fields. It leaves `conversation_id` alone, because the pagination step has already stamped that onto the part. It returns the copied and enriched record.

**Call relations**: `IntercomConnector.flatten` calls this for the `conversation_parts` stream. The records usually come from `IntercomConnector._paginate_conversation_parts`, which first fetches parent conversations and then their nested parts.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 182–190)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This adds a contact’s first associated company as a simple top-level organization ID. That makes contact-to-company joins easier later.

**Data flow**: It receives one contact record. It copies the record, looks inside the nested `companies.companies` list, and if there is a first company, writes its `id` or `company_id` as `org_id`. It returns the copied record with that extra field when available.

**Call relations**: `IntercomConnector.flatten` calls this only for the `contacts` stream. It prepares contact records from the Intercom API for later storage and transformation.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 192–206)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This is the connector’s final cleanup step for individual records before UFO stores or processes them. It flattens selected nested fields and converts integer cursor values into strings so UFO’s watermark system can remember them.

**Data flow**: It receives one Intercom record and the stream description for that record. Depending on the stream name, it sends the record through the conversation, conversation-part, or contact flattening helper. Then, if the stream has a cursor field and that field is an integer, it returns a copy where the cursor value is written as decimal text. If no special change is needed, it returns the record as-is.

**Call relations**: The broader REST connector calls this after records have been fetched. Inside this file, it hands stream-specific work to `IntercomConnector._flatten_conversation`, `IntercomConnector._flatten_conversation_part`, or `IntercomConnector._flatten_contact`.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 208–252)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the dispatcher that chooses how to read each Intercom stream. It is like a traffic director: depending on the stream name, it sends the sync to the right pagination method.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional saved cursor. It checks the stream name and delegates to the matching pagination method, yielding each page of records that method produces. If Intercom returns HTTP 401 or 403, it turns that refusal into `StreamSkipped`, so the run records that this stream was not allowed instead of treating it as an unexpected crash. Other HTTP errors are re-raised.

**Call relations**: The sync engine calls this when it needs pages for an Intercom stream. This method then calls `_paginate_search`, `_paginate_scroll`, `_paginate_list`, `_paginate_attributes`, `_paginate_conversation_parts`, `_paginate_company_segments`, or `_paginate_activity_logs` depending on what kind of Intercom endpoint the stream uses.

*Call graph*: calls 8 internal fn (__init__, _paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search).


##### `IntercomConnector._paginate_search`  (lines 254–274)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams that use Intercom’s search API, such as conversations, contacts, and tickets. It keeps asking for the next search page until Intercom says there is no next page.

**Data flow**: It receives an HTTP client, a stream description, and the saved cursor. It looks up the correct search path and response key, builds a search request body, sends a POST request, extracts the list of records, and yields that list when it is not empty. It reads Intercom’s `starting_after` token from the response and repeats until no token remains.

**Call relations**: `IntercomConnector.paginate` calls this for search-based streams. Each loop uses `IntercomConnector._build_search_body` to create the request body with the current cursor and page token.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_scroll`  (lines 276–290)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads companies through Intercom’s scroll API. A scroll API is like paging through a long catalogue using a bookmark token returned by the previous page.

**Data flow**: It receives an HTTP client. It starts without a scroll token, requests `/companies/scroll`, yields the returned company records if there are any, then stores the returned `scroll_param` token for the next request. It stops when Intercom returns no records or no next scroll token.

**Call relations**: `IntercomConnector.paginate` calls this for the `companies` stream. It hides the scroll-token details so the caller simply receives pages of company records.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_list`  (lines 292–304)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads simple Intercom list endpoints, such as admins, tags, teams, and segments. These endpoints return their available records in one response rather than a long cursor-driven sequence.

**Data flow**: It receives an HTTP client and a stream description. It looks up the endpoint path, sends one GET request, then checks whether the response contains a list under the stream name or under `data`. If it finds a non-empty list, it yields that list once; either way, it then finishes.

**Call relations**: `IntercomConnector.paginate` calls this for streams listed in the connector’s plain-list map. Unlike the search and scroll methods, it does not loop over multiple pages.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_attributes`  (lines 306–315)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Intercom data attribute definitions for companies or contacts. These are metadata records that describe custom fields, not the company or contact records themselves.

**Data flow**: It receives an HTTP client and a stream description. It maps the stream name to the Intercom model name, sends a GET request to `/data_attributes` with that model as a parameter, extracts the `data` list, and yields it if it contains records.

**Call relations**: `IntercomConnector.paginate` calls this for `company_attributes` and `contact_attributes`. It provides the same page-of-records shape as the other pagination methods, even though the API call is a single request.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_conversation_parts`  (lines 317–349)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the pieces inside conversations, such as replies and notes. Intercom does not fetch these as a normal top-level search stream here, so the connector first finds conversations and then opens each one to collect its parts.

**Data flow**: It receives an HTTP client and an optional saved cursor. It searches conversations using the normal conversation cursor, then for each returned conversation with an ID, it requests that conversation’s detail endpoint. From the detail response it extracts `conversation_parts.conversation_parts`, stamps each part with the parent `conversation_id` if missing, and yields the parts when any are found. It follows the conversation search `starting_after` token until there are no more conversation pages.

**Call relations**: `IntercomConnector.paginate` calls this for the `conversation_parts` stream. Inside its loop, it uses `IntercomConnector._build_search_body` to page through parent conversations before making detail requests for each conversation.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_company_segments`  (lines 351–375)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the segment memberships for companies. Because segments are attached to each company, it first scrolls through companies and then asks Intercom for the segments of each company.

**Data flow**: It receives an HTTP client. It pages through `/companies/scroll` using `scroll_param`, and for each company with an ID, sends a request to `/companies/{id}/segments`. It extracts the returned segment list, stamps each segment with the parent `company_id` if missing, and yields those segment records. It stops when the company scroll feed ends.

**Call relations**: `IntercomConnector.paginate` calls this for the `company_segments` stream. It combines the company scroll pattern with per-company detail calls so downstream code can see company-segment links as their own records.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_activity_logs`  (lines 377–399)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Intercom admin activity logs, optionally starting after a saved creation-time cursor. Activity logs use their own endpoint and pagination style.

**Data flow**: It receives an HTTP client and an optional cursor. If a cursor is present, it sends it as `created_at_after` on the first request to `/admins/activity_logs`. It yields any `activity_logs` records from each response, then follows the response’s next-page path. If Intercom returns a full URL for the next page, it strips off the base URL so the client can request the relative path. It stops when there is no next page.

**Call relations**: `IntercomConnector.paginate` calls this for the `activity_logs` stream. This method owns the activity-log-specific cursor parameter and next-link handling, while `paginate` only decides that this is the right method for that stream.

*Call graph*: called by 1 (paginate).
