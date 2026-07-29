# CRM, sales, and support source connectors  `stage-12.7`

This stage is the set of “adapters” that let the system bring in customer and support data from outside services. It is part of the main sync work: when the system needs fresh records, these connectors log in to each service, ask for data through that service’s API, and reshape the replies into standard records that the rest of the system can store, search, and recall.

Each file speaks one service’s language. The Attio connector reads companies, people, deals, tasks, notes, meetings, and call recordings. HubSpot covers a wide range, including CRM records, marketing content, conversations, analytics, custom objects, links between records, and deletion markers. Salesforce reads common CRM items like accounts, contacts, opportunities, and tasks, without writing anything back. Freshdesk handles support objects and its different page-by-page result formats. Intercom gathers conversations, contacts, companies, tickets, admins, tags, and related details. Zendesk reads tickets, users, comments, help articles, and community posts. Together they act like translators feeding one common indexing pipeline.

## Files in this stage

### CRM and Sales Records
Connectors that ingest customer relationship and sales data from CRM platforms into searchable synced records.

### `extensions/sources/ufo_ext_sources/attio.py`

`io_transport` · `during Attio source sync`

Attio’s API does not return every kind of data in the same shape. Companies, people, and deals come from object-specific record queries. Tasks and notes use simpler workspace endpoints. Meetings and call recordings use cursor-style paging, where the server gives a token for the next page. This file hides those differences behind one connector so the rest of the system can ask for “the Attio streams” without caring how Attio organizes them.

The most important work here is flattening. Attio records often bury their useful identity inside an `id` object and bury field values inside arrays of small “value cells.” Without flattening, a company record might not have a clear top-level `record_id`, and a name, email, domain, or status might be wrapped in several layers. This connector pulls those pieces up into ordinary fields, like turning a stack of nested envelopes into a readable form.

The connector only reads; it does not write back to Attio. Every stream is treated as a full snapshot, meaning missing records can be deleted downstream because Attio does not offer one consistent “changed since last time” field. It also knows when to skip a stream safely, such as when a workspace has disabled a standard object or the OAuth permission grant lacks a needed scope.

#### Function details

##### `_records_stream`  (lines 35–43)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates the stream description used for Attio object records such as companies, people, and deals. A stream description tells the sync engine what the stream is called, what Attio object it reads from, and which field uniquely identifies each record.

**Data flow**: It receives a friendly stream name, an Attio object slug, and whether the stream is canonical. It fills in a `StreamSpec` with a `record_id` primary key and marks the stream as a full snapshot where missing records should be removed downstream. The result is a ready-to-use stream definition.

**Call relations**: This helper is used while the file defines the Attio stream list. It hands off the finished stream description to `StreamSpec`, which the base connector later uses when deciding what to fetch and how to store it.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 70–71)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: Safely pulls one named identifier out of a nested dictionary. It is used when Attio stores an ID inside another object rather than directly as a plain string.

**Data flow**: It receives any value and a key name. If the value is a dictionary, it returns the value under that key; otherwise it returns nothing. It does not change anything.

**Call relations**: It supports `AttioConnector._value_primitive`, which sometimes needs to recover IDs from nested Attio option or status objects when a human-readable title is not available.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 80–81)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: Builds the request body used when asking Attio for a page of standard object records. It keeps the page size fixed and moves through results by offset.

**Data flow**: It receives an offset, which means how many records have already been skipped. It returns a small dictionary containing Attio’s page limit and that offset. Nothing else is changed.

**Call relations**: `AttioConnector.paginate` calls this each time it requests the next page for object-based streams like companies, people, and deals.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._value_primitive`  (lines 84–127)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: Turns one Attio value cell into the simplest useful value, such as text, a number, an email address, a phone number, a status title, or a linked record ID. This is the basic translator between Attio’s nested API format and ordinary fields.

**Data flow**: It receives one dictionary from Attio. It checks the different places Attio may put the real value, such as `value`, `option.title`, `status.title`, `email_address`, `phone_number`, `domain`, or location parts. It returns one plain value, such as a string, number, joined address, linked-record label, or nothing if it cannot find a useful value.

**Call relations**: Flattening helpers call this whenever they need to simplify a cell. When an option or status contains only a nested ID, it calls `_nested_id` to pull that ID out safely.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 130–145)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: Simplifies one Attio field cell into a single useful value when possible. It understands both one-value fields and list-shaped fields.

**Data flow**: It receives a cell that may be a list, dictionary, or already-simple value. For lists, it converts each item to a primitive value and removes empty results; most lists become their first useful value, while multi-select option lists stay as a list. For dictionaries, it delegates to the value translator. It returns the simplified value or nothing.

**Call relations**: `AttioConnector._flatten_values` uses this for most Attio attributes while building a clean top-level record.


##### `AttioConnector._flatten_list_cell`  (lines 148–155)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: Simplifies an Attio field that should remain a list, such as multiple emails, phone numbers, domains, or categories. It keeps all useful entries instead of choosing only the first one.

**Data flow**: It receives a cell. If the cell is not a list, it flattens it as a single value and wraps that value in a list if present. If it is a list, it converts each item into a primitive value and removes empty entries. The result is always a list.

**Call relations**: `AttioConnector._flatten_values` uses this for known multi-value fields so downstream code can see all emails or domains, not just one.


##### `AttioConnector._flatten_values`  (lines 158–192)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns Attio’s nested `values` block into ordinary top-level fields. This is where names, domains, categories, emails, and phone numbers become easy to read and search.

**Data flow**: It receives the `values` dictionary from an Attio record. It walks each attribute, flattens list-style fields as lists and other fields as single values, then adds helpful shortcut fields such as `first_name`, `last_name`, `email`, `phone`, `domain`, and `category`. It returns a new flat dictionary.

**Call relations**: `AttioConnector._flatten_record` calls this after it has pulled out the record identity and timestamps. It relies on `_flatten_cell` and `_flatten_list_cell` as its smaller translation tools.


##### `AttioConnector._flatten_record`  (lines 195–209)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Converts a standard Attio object record, such as a company, person, or deal, into the flat shape expected by the rest of the sync system. It makes sure the record has a clear top-level `record_id`.

**Data flow**: It receives the raw Attio record and the stream definition. It pulls IDs and timestamps from the outer record, optionally copies a cursor field if the stream uses one, then merges in the flattened attribute values. It returns one clean record dictionary.

**Call relations**: `AttioConnector.flatten` calls this for all object-record streams that are not tasks, notes, meetings, or call recordings.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 212–216)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `task_id` to an Attio task record. This gives the sync system a stable field to use as the task’s unique identifier.

**Data flow**: It receives a raw task record, copies it, reads the task ID from the nested `id` object when needed, and writes that value into `task_id`. It returns the copied and slightly enriched record.

**Call relations**: `AttioConnector.flatten` calls this when the current stream is `tasks`.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 219–223)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `note_id` to an Attio note record. This makes notes identifiable in the same simple way as other synced records.

**Data flow**: It receives a raw note record, copies it, reads the note ID from the nested `id` object when needed, and writes that value into `note_id`. It returns the updated copy.

**Call relations**: `AttioConnector.flatten` calls this when the current stream is `notes`.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 226–230)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `meeting_id` to an Attio meeting record. This gives meetings a clear unique key for storage and later lookup.

**Data flow**: It receives a raw meeting record, copies it, extracts the meeting ID from the nested `id` object if present, and stores it as `meeting_id`. It returns the updated record copy.

**Call relations**: `AttioConnector.flatten` calls this when the current stream is `meetings`.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 233–254)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares a call recording record for search and storage by adding a top-level recording ID and turning transcript segments into readable text. It also uses the web page URL as a fallback recording URL when needed.

**Data flow**: It receives a raw call recording record, copies it, extracts `call_recording_id`, fills `recording_url` from `web_url` if no recording URL is present, and joins transcript segments into a single `transcript_text` string with speaker names when available. It returns the enriched copy.

**Call relations**: `AttioConnector.flatten` calls this when the current stream is `call_recordings`. Transcript data may have been attached earlier by `_paginate_call_recordings` after it fetched each recording’s transcript.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 256–265)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening routine for the stream being synced. It is the connector’s single public translation point from raw Attio records to clean records.

**Data flow**: It receives a raw record and its stream description. It checks the stream name and sends the record to the matching helper for tasks, notes, meetings, call recordings, or standard object records. It returns the flattened record.

**Call relations**: The base `RestConnector` flow calls this after pages of records have been fetched. This function then hands the record to one of the specialized flatteners so every stream gets the right ID and field shape.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 267–321)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches all pages of data for one Attio stream, using the paging style that stream requires. It is the main reader for Attio API data.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor value. For tasks and notes it reads offset-paged GET endpoints. For meetings it reads cursor-paged results. For call recordings it fans out through meetings and recordings. For standard objects it POSTs query bodies with increasing offsets. It yields one list of records at a time, and may raise `StreamSkipped` when Attio says a stream cannot be read because the object is disabled or a permission scope is missing.

**Call relations**: The sync engine calls this to pull records. It delegates to `_paginate_simple`, `_paginate_cursor`, `_paginate_call_recordings`, and `_build_query_body`, and uses the error-check helpers to decide whether a failed request should stop the stream or simply skip it.

*Call graph*: calls 8 internal fn (__init__, _build_query_body, _is_object_disabled, _is_scope_unauthorized, _paginate_call_recordings, _paginate_cursor, _paginate_simple, _scope_skip_reason).


##### `AttioConnector._paginate_simple`  (lines 323–330)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use simple offset paging, currently tasks and notes. Offset paging means each request asks for a fixed number of records after skipping a certain number already seen.

**Data flow**: It receives an HTTP client, an endpoint path, and a page size. It asks the base connector to walk pages from that endpoint and yields each returned page of records. It does not reshape the records itself.

**Call relations**: `AttioConnector.paginate` calls this for the `tasks` and `notes` streams. The lower-level page walking is handed off to the shared REST connector helper.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._paginate_cursor`  (lines 332–350)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use cursor paging, currently meetings and per-meeting call recordings. Cursor paging means the server returns a token that points to the next page.

**Data flow**: It receives an HTTP client, endpoint path, page size, and optional query parameters. It asks the base connector to keep following `pagination.next_cursor` and yields each page of records from `data`. It passes through the records unchanged.

**Call relations**: `AttioConnector.paginate` calls this for meetings. `_paginate_call_recordings` also calls it first to list meetings and then to list recordings for each meeting.

*Call graph*: called by 2 (_paginate_call_recordings, paginate).


##### `AttioConnector._paginate_call_recordings`  (lines 352–388)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the call recordings stream by walking through meetings, then fetching each meeting’s recordings, then attaching transcript data when available. This is needed because Attio exposes recordings under their parent meeting rather than as one simple global list.

**Data flow**: It receives an HTTP client. It pages through meetings, extracts each meeting ID and timing information, then pages through that meeting’s call recordings. For each recording it adds parent meeting context, estimates duration when possible, fetches the transcript by recording ID, and attaches transcript fields if the transcript exists. It yields pages of enriched recording records.

**Call relations**: `AttioConnector.paginate` calls this for the `call_recordings` stream. Inside, it uses `_paginate_cursor` for both meetings and recordings, `_meeting_id` and `_call_recording_id` to find IDs, `_datetime_of` and `_duration_seconds` for timing, and `_fetch_transcript` for transcript details.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (paginate).


##### `AttioConnector._fetch_transcript`  (lines 390–402)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: Fetches the transcript for one call recording. It treats “not found” or “not ready yet” as a normal absence rather than a fatal error.

**Data flow**: It receives an HTTP client, meeting ID, and recording ID. It builds the transcript endpoint path and performs a GET request. If Attio returns 404 or 409, it returns nothing; otherwise it returns the transcript data object when present, or nothing if the response is not in the expected shape.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this for each recording that has an ID, so the later flattening step can turn transcript segments into searchable text.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 405–409)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a meeting’s ID from Attio’s possible ID shapes. This keeps the call recording fan-out from breaking when the ID is nested.

**Data flow**: It receives a meeting record. If `id` is a dictionary, it returns `id.meeting_id`; if `id` is already a string, it returns that string. Otherwise it returns nothing.

**Call relations**: `AttioConnector._paginate_call_recordings` uses this before asking Attio for recordings under a meeting. If no meeting ID can be found, that meeting is skipped for recording lookup.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 412–416)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a call recording’s ID from Attio’s possible ID shapes. This gives the connector the exact value needed to fetch that recording’s transcript.

**Data flow**: It receives a recording record. If `id` is a dictionary, it returns `id.call_recording_id`; if `id` is already a string, it returns that string. Otherwise it returns nothing.

**Call relations**: `AttioConnector._paginate_call_recordings` uses this after listing recordings. When an ID is found, it hands that ID to `_fetch_transcript`.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 419–423)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: Pulls a usable date or date-time string out of Attio’s meeting time shape. Attio may represent timed meetings and all-day meetings differently.

**Data flow**: It receives a value that may be a dictionary containing `datetime`, `timezone`, or `date`. If the value is a dictionary, it returns the `datetime` value first, otherwise the `date` value. If the shape is not usable, it returns nothing.

**Call relations**: `AttioConnector._paginate_call_recordings` uses this to copy meeting start and end times onto each recording and to prepare inputs for duration calculation.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 426–436)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: Calculates an approximate meeting duration in seconds from start and end timestamps. It avoids failing the sync if the timestamps are missing or cannot be parsed.

**Data flow**: It receives optional start and end strings. If either is missing, it returns nothing. Otherwise it parses the strings as ISO 8601 times, converts a trailing `Z` into an explicit UTC offset, subtracts start from end, and returns the non-negative number of seconds. If parsing fails, it returns nothing.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this after extracting meeting times, then copies the duration onto recordings when it can be calculated.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 439–448)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes the Attio error that means a standard object, such as companies or deals, is disabled in the workspace. This lets the connector skip that stream instead of treating the whole sync as broken.

**Data flow**: It receives an HTTP error. It first checks for status code 400, then tries to read the response as JSON, and finally checks whether the error code is `standard_object_disabled`. It returns true or false.

**Call relations**: `AttioConnector.paginate` calls this when an object record query fails. If it returns true, `paginate` raises `StreamSkipped` with a clear reason.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._is_scope_unauthorized`  (lines 451–460)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes the Attio error that means the OAuth grant is missing a needed permission scope. OAuth is the permission system that lets this connector access Attio on behalf of a user or workspace.

**Data flow**: It receives an HTTP error. It checks for status code 403, tries to parse the response body as JSON, and looks for Attio’s `unauthorized` code. It returns true if this exact missing-permission condition is detected.

**Call relations**: `AttioConnector.paginate` calls this when meeting or call recording requests fail. If the missing scope is detected, `paginate` skips that stream using the message built by `_scope_skip_reason`.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._scope_skip_reason`  (lines 463–469)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a human-readable explanation for why a stream was skipped because of missing OAuth permissions. This helps operators understand what needs to be fixed.

**Data flow**: It receives an HTTP error, tries to read the response JSON, and pulls out Attio’s message when present. It returns a sentence saying the OAuth grant is missing a required scope, with Attio’s message or a fallback note.

**Call relations**: `AttioConnector.paginate` calls this after `_is_scope_unauthorized` confirms the error is a missing-scope problem. The returned text is passed into `StreamSkipped`.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/hubspot.py`

`io_transport` · `during HubSpot sync runs`

HubSpot does not offer one simple doorway for all account data. Contacts and deals come from one search API, lists and owners come from other list APIs, analytics has its own report shape, and relationships between records need separate calls. This file is the adapter that hides that mess from the rest of the system. Think of it like a warehouse receiving desk: trucks arrive with boxes in different shapes, and this code repacks them into the same labeled carton format.

The file first declares many stream definitions. A stream is one category of data, such as contacts, forms, campaign assets, or list memberships. Each stream says what field is its unique ID, what field can be used as a cursor for incremental syncing, and whether it is a core canonical record.

The `HubSpotConnector` then decides how to fetch each stream. Standard CRM objects use HubSpot search, including incremental filters based on the last-seen modification time. Product APIs use special endpoint walkers. Custom objects are discovered from schemas first, because each account can define different object types. The connector also checks archived records so deleted HubSpot objects become tombstones instead of silently staying alive. If HubSpot says a stream is unavailable because of account tier or missing permission, the connector marks only that stream as skipped rather than failing the whole run.

#### Function details

##### `_normalize_epoch_millis`  (lines 248–255)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Converts HubSpot timestamps given as milliseconds since 1970 into readable ISO date strings. It leaves booleans and already-normal values alone so fields are not accidentally changed.

**Data flow**: It receives any value. If the value is a number or a numeric string, it treats it as milliseconds, converts it to a UTC date-time string, and returns that; otherwise it returns the original value unchanged.

**Call relations**: It is used when product-style records, such as knowledge articles or analytics views, use raw millisecond timestamps while the rest of the connector expects readable date strings.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 258–267)

```
def _stream(name: str, *, object_type: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a standard HubSpot CRM stream definition. It saves repeated setup for common objects that all use HubSpot's CRM search shape.

**Data flow**: It receives a stream name, HubSpot object type, and whether the stream is canonical. It produces a `StreamSpec`, which is the system's recipe for syncing that object type.

**Call relations**: It is used during module loading to define streams such as companies, contacts, deals, tasks, tickets, and many other CRM-style objects.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 270–289)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='createdAt', updated_at_field: str | None='updatedAt', pagi
```

**Purpose**: Creates a stream definition for HubSpot product APIs that do not behave like normal CRM search objects. These streams often have different IDs, cursors, timestamps, or pagination rules.

**Data flow**: It receives stream metadata such as name, source object, primary key, timestamp fields, and optional pagination instructions. It returns a non-canonical `StreamSpec` for the runner.

**Call relations**: It is used during module loading for streams such as owners, workflows, forms, files, analytics reports, email events, sequences, and other non-CRM surfaces.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 292–304)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Builds a reusable pagination recipe for HubSpot endpoints that return `results` plus a `paging.next.after` cursor. Pagination means asking for the next page of records until there are no more.

**Data flow**: It receives an API path and returns a `Pagination` object that tells the base connector where records live, where the next cursor lives, and which query parameters to use.

**Call relations**: It supports product API stream definitions that can be fetched by the base connector's generic pagination strategy instead of custom code.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 307–317)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Creates a stream definition for a relationship table, such as deal-to-contact links. These are synthetic rows made by the connector, not standalone HubSpot objects.

**Data flow**: It receives a stream name and parent object type. It returns a `StreamSpec` with no cursor, because HubSpot does not expose modification times for these relationship rows.

**Call relations**: It is used during module setup to define streams like `deal_contacts`, `ticket_companies`, and `task_contacts`, which are later filled by `_paginate_junction`.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 622–652)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the JSON body used to search CRM objects in HubSpot. It includes the fields to fetch, the page size, the sort order, and optional incremental-sync filters.

**Data flow**: It receives a stream definition, property names, the saved cursor, and HubSpot's page cursor. It returns a request body that HubSpot's search API understands.

**Call relations**: The standard CRM object walker and the custom-object walker call it whenever they need the next search page.

*Call graph*: called by 2 (_paginate_custom_object_records, _paginate_unchecked).


##### `HubSpotConnector._flatten`  (lines 655–666)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a normal HubSpot CRM object envelope into a flat record. This makes nested `properties` fields look like ordinary top-level fields.

**Data flow**: It receives one raw CRM record. It copies the ID, timestamps, archived flag, and every property into one dictionary, then returns that flatter record.

**Call relations**: `flatten` calls it for standard CRM streams after pages have been fetched.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 669–692)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records from HubSpot product APIs, which do not all use the same shape. It makes them look more like the flat records used elsewhere.

**Data flow**: It receives a raw product API record and its stream definition. It fills in an ID from `objectId` when needed, lifts nested `properties` and form `values`, fixes selected timestamps, and returns the normalized record.

**Call relations**: `flatten` calls it for product API streams before the records are handed back to the source framework.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 694–701)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening rule for each HubSpot stream. Some streams are already flat, while others need CRM-style or product-API-style cleanup.

**Data flow**: It receives a raw record and its stream. It checks the stream name, either returns the record unchanged or delegates to the correct helper, and returns the final record.

**Call relations**: The source framework calls this after records are fetched so downstream storage sees a consistent shape.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector._list_properties`  (lines 703–711)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which fields exist for a CRM object type. This lets the connector fetch all available properties without hard-coding every field name.

**Data flow**: It receives an HTTP client and a HubSpot object type. It calls the properties endpoint, extracts property names from the response, and returns them as a list.

**Call relations**: The standard CRM pagination path calls it before searching an object type, because HubSpot search only returns properties explicitly requested.

*Call graph*: called by 1 (_paginate_unchecked).


##### `HubSpotConnector.paginate`  (lines 713–726)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Provides the safe public page generator for a stream. It catches permission-style failures and turns them into skipped streams instead of failed syncs.

**Data flow**: It receives an HTTP client, stream definition, and optional cursor. It yields pages from the internal paginator, but if HubSpot says the stream cannot be read, it raises `StreamSkipped` with a human-readable reason.

**Call relations**: The source runner calls this for each stream. It delegates real fetching to `_paginate_unchecked` and uses the skip helpers only when HubSpot returns an error.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 728–778)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the correct fetching path for a stream and yields its pages. It is the main traffic director inside the connector.

**Data flow**: It receives a stream and cursor. Depending on the stream, it uses generic pagination, junction fetching, custom-object fetching, product-API fetching, or standard CRM search; standard CRM streams also get an archived-record sweep afterward.

**Call relations**: `paginate` calls it after setting up error handling. It hands off to many specialized paginators when a stream needs custom behavior.

*Call graph*: calls 6 internal fn (_build_search_body, _list_properties, _paginate_archived_ids, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._is_stream_unavailable`  (lines 781–804)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether a HubSpot error means this one stream is not available to the account. This is different from a broken connection or bug.

**Data flow**: It receives an HTTP error. It checks for status 403 and looks for permission-related wording in the response message, returning true or false.

**Call relations**: `paginate` and archived sweeps use it to decide whether to skip quietly or let the error fail the run.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 807–816)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds the message recorded when a HubSpot stream is skipped. The message includes the stream name and HubSpot's own explanation when available.

**Data flow**: It receives the stream name and HTTP error. It reads the response message if possible and returns one readable sentence.

**Call relations**: `paginate` uses it when converting a permission-style HubSpot error into `StreamSkipped`.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 818–853)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived, meaning deleted or hidden, CRM object IDs and emits tombstones for them. Without this, removed HubSpot records could remain searchable forever.

**Data flow**: It receives a client and stream. It pages through HubSpot's archived list endpoint, collects record IDs, and yields `StreamPage` objects containing delete markers.

**Call relations**: The standard CRM path calls it after normal search pages. It stops quietly if HubSpot does not support archived paging for that object.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 855–949)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Routes non-CRM streams to the special fetcher they need. HubSpot product APIs are inconsistent, so one generic method is not enough.

**Data flow**: It receives a client, stream, and cursor. It checks the stream name, delegates to a stream-specific paginator when needed, or falls back to a declared GET endpoint.

**Call relations**: `_paginate_unchecked` calls it for product API streams. It is the central switchboard for owners, lists, analytics, events, associations, sequences, forms, conversations, pipelines, and more.

*Call graph*: calls 21 internal fn (_paginate_analytics_reports, _paginate_analytics_views, _paginate_association_labels, _paginate_associations, _paginate_campaign_assets, _paginate_consent_states, _paginate_conversation_messages, _paginate_email_events, _paginate_event_occurrences, _paginate_event_types (+11 more)); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._paginate_get_collection`  (lines 951–979)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Pages through HubSpot endpoints that use the common `results` plus `after` pattern. It is the connector's reusable simple-list reader.

**Data flow**: It receives a path, optional limit, and extra query parameters. It repeatedly calls the endpoint, yields valid record dictionaries, and follows the next cursor until done.

**Call relations**: Many specialized paginators use it as their basic building block instead of rewriting the same paging loop.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_product_api, _paginate_sequences, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 981–1016)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Syncs HubSpot custom objects, which are account-defined record types. It first discovers the account's custom schemas, then reads each type's records.

**Data flow**: It receives a client and cursor. It loads schemas, builds a temporary search stream for each schema, yields active records, then yields archived tombstones for that custom object type.

**Call relations**: `_paginate_unchecked` calls it for the `custom_objects` stream. It coordinates schema helpers, record pagination, and archived-ID pagination.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 1018–1020)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of custom object definitions available in the HubSpot account.

**Data flow**: It receives a client, calls the custom object schema endpoint, filters the response down to dictionary rows, and returns them.

**Call relations**: Custom-object syncing uses it to know what to read. Association syncing also uses it so custom object types can participate in relationship discovery.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 1023–1028)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Finds the usable object type identifier inside a custom object schema. HubSpot may expose this identifier under more than one field name.

**Data flow**: It receives a schema dictionary. It checks likely identifier keys in order and returns the first non-empty string, or `null` if none exists.

**Call relations**: Custom-object and association flows use it whenever they need the API name for a custom object type.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1031–1046)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Collects all property names that should be requested for a custom object. It includes normal fields and display fields used for readable titles.

**Data flow**: It receives a schema. It walks the schema's property list and display-property fields, removes duplicates, and returns a list of names.

**Call relations**: `_paginate_custom_objects` uses it before searching custom-object records, because HubSpot search needs requested property names up front.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1048–1083)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Reads records for one custom object type through HubSpot search. It also avoids repeating records that sit exactly on the saved cursor boundary.

**Data flow**: It receives a client, temporary stream definition, schema, property list, and cursor. It posts search requests page by page, converts each record to a custom-object row, and yields non-empty pages.

**Call relations**: `_paginate_custom_objects` calls it once per discovered schema. It uses `_build_search_body` for the request and `_custom_object_row` for record shaping.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1085–1124)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Turns one raw custom-object record into a self-describing flat row. It adds labels and display titles so custom records are understandable outside HubSpot.

**Data flow**: It receives a raw record and its schema. It combines properties, record ID, object type ID, object label, title fields, timestamps, and archive status into one dictionary, or returns `null` if required IDs are missing.

**Call relations**: The custom-object record paginator calls it for every fetched custom record.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1126–1156)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived custom-object records and emits delete markers with IDs that match the custom-object row format.

**Data flow**: It receives a client and custom object type ID. It pages through archived records, prefixes each record ID with its object type, and yields tombstone pages.

**Call relations**: `_paginate_custom_objects` calls it after reading active records for each custom object type.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1158–1177)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a stream of owner teams by looking inside owner records. HubSpot exposes teams nested under owners rather than as a clean separate list here.

**Data flow**: It receives a client, reads all owners, extracts unique team objects from them, and yields one page of team rows.

**Call relations**: `_paginate_product_api` calls it for the `owner_teams` stream, using `_paginate_get_collection` to read owners.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_lists`  (lines 1179–1208)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot CRM lists using the lists search endpoint. Lists use offset paging rather than the usual `after` cursor.

**Data flow**: It receives a client. It posts list-search requests with an offset, flattens additional properties into each list record, adds a string ID, and yields pages until HubSpot says there are no more.

**Call relations**: `_paginate_product_api` uses it for the `lists` stream, and list-membership syncing uses it to discover which lists need membership calls.

*Call graph*: called by 2 (_paginate_list_memberships, _paginate_product_api).


##### `HubSpotConnector._paginate_site_search`  (lines 1210–1230)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads CMS search results for a chosen content type, such as knowledge articles. It handles HubSpot's offset-based site search pagination.

**Data flow**: It receives a client and content type. It calls the site search endpoint with limit and offset, yields dictionary results, and stops when the offset reaches the total count.

**Call relations**: `_paginate_product_api` calls it for knowledge articles.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1232–1256)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads marketing assets attached to HubSpot campaigns. It first finds campaigns, then fans out across all supported asset types for each campaign.

**Data flow**: It receives a client. It pages through campaigns, extracts each campaign ID and name, then asks `_paginate_campaign_asset_type` for every supported asset kind and yields those pages.

**Call relations**: `_paginate_product_api` calls it for the `campaign_assets` stream.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1258–1293)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one asset type for one campaign and turns each asset into a row tied back to that campaign.

**Data flow**: It receives a campaign ID, campaign name, and asset type. It pages through the campaign asset endpoint, creates stable IDs that include campaign and asset type, adds helpful labels and metrics, and yields rows.

**Call relations**: `_paginate_campaign_assets` calls it repeatedly. It ignores 403 or 404 for unavailable asset categories but raises unexpected errors.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1295–1301)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields the HubSpot analytics views available in the account. An analytics view is a saved filter or reporting view.

**Data flow**: It receives a client, asks `_analytics_view_rows` for normalized rows, and yields them if any exist.

**Call relations**: `_paginate_product_api` calls it for the `analytics_views` stream.

*Call graph*: calls 1 internal fn (_analytics_view_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_view_rows`  (lines 1303–1334)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches and normalizes analytics view definitions. It makes sure every row has an ID, name, report kind, filters, and readable timestamps.

**Data flow**: It receives a client, calls HubSpot's analytics views endpoint, handles list-shaped or object-shaped responses, filters invalid rows, and returns normalized rows.

**Call relations**: Analytics view syncing yields its result directly. Analytics report syncing also calls it so reports can be queried for each saved view.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1336–1366)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a broad set of analytics report queries and yields their results. It covers multiple report subjects, time periods, and analytics views.

**Data flow**: It receives a client. It chooses a date window, loads analytics views, creates an all-views plus per-view query list, then calls `_paginate_analytics_report_query` for every combination.

**Call relations**: `_paginate_product_api` calls it for the `analytics_reports` stream.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_report_window`  (lines 1369–1370)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range used for analytics report syncs. It starts at a fixed old date and ends today in UTC.

**Data flow**: It reads the current UTC date and returns two strings in HubSpot's compact `YYYYMMDD` format.

**Call relations**: `_paginate_analytics_reports` calls it before constructing report queries.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1372–1423)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Runs one analytics report query and follows its offset pagination. Some report combinations are not supported by HubSpot, so it skips harmless 400 or 404 responses.

**Data flow**: It receives report family, subject, time period, optional view filter, and date range. It calls the report endpoint, converts the response into rows, yields them, and advances offset while more breakdowns exist.

**Call relations**: `_paginate_analytics_reports` calls it for each planned report combination. It uses `_analytics_report_rows` to shape HubSpot's response.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1426–1501)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Turns one analytics report response into rows suitable for storage. It separates overall totals from individual breakdown lines.

**Data flow**: It receives raw report data plus query context. It creates one totals row when present, creates one row per breakdown, assigns stable IDs, formats dates, and returns the row list.

**Call relations**: `_paginate_analytics_report_query` calls it after each HubSpot report response.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1504–1508)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Creates a stable, readable ID for an analytics report row. It removes characters that would make IDs ambiguous.

**Data flow**: It receives any number of ID parts. It stringifies them, replaces slashes and colons, joins them with colons, and prefixes the result with `analytics_report:`.

**Call relations**: Analytics report row building uses it to identify totals and breakdown rows consistently across runs.


##### `HubSpotConnector._analytics_report_date`  (lines 1511–1512)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Converts HubSpot's compact analytics date strings into normal dashed dates.

**Data flow**: It receives a string like `20260129` and returns `2026-01-29`.

**Call relations**: Analytics report row building uses it for the start and end dates included in each report row.


##### `HubSpotConnector._paginate_event_types`  (lines 1514–1533)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the definitions of event types available in HubSpot. These describe kinds of events, not individual occurrences.

**Data flow**: It receives a client, fetches event types, accepts either list-shaped or `results` responses, assigns an ID from the best available field, and yields one page.

**Call relations**: `_paginate_product_api` calls it for the `event_types` stream.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1535–1557)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads individual HubSpot event occurrences, optionally only after a saved cursor. It creates IDs when HubSpot does not provide them.

**Data flow**: It receives a client and cursor. It sends `occurredAfter` when a cursor exists, reads event rows, assigns real or synthetic IDs, and yields them; a 404 simply means no endpoint/data to read.

**Call relations**: `_paginate_product_api` calls it for the `event_occurrences` stream, and it uses `_synthetic_event_id` when needed.

*Call graph*: calls 1 internal fn (_synthetic_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._synthetic_event_id`  (lines 1560–1570)

```
def _synthetic_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Creates a repeatable ID for an event occurrence that lacks one from HubSpot. Repeatable IDs are important so the same event updates instead of duplicating.

**Data flow**: It receives an event row and its page index. It combines event type, object type, object ID, time, and a short payload hash into a colon-separated string.

**Call relations**: Event occurrence pagination calls it only for rows without an ID.

*Call graph*: called by 1 (_paginate_event_occurrences).


##### `HubSpotConnector._paginate_email_events`  (lines 1572–1600)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads marketing email events from HubSpot's older email events API. It supports incremental syncing by converting the cursor into a start timestamp.

**Data flow**: It receives a client and cursor. It builds request parameters, follows offset pagination, assigns real or synthetic IDs to events, yields pages, and stops when HubSpot reports no more data.

**Call relations**: `_paginate_product_api` calls it for the `email_events` stream. It relies on timestamp and synthetic-ID helpers.

*Call graph*: calls 2 internal fn (_email_event_start_timestamp, _synthetic_email_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1603–1612)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts the saved email-event cursor into the millisecond timestamp HubSpot expects. It accepts either an already-numeric cursor or an ISO date string.

**Data flow**: It receives a cursor string or `null`. It returns `null` for no cursor or unparseable text, returns the integer directly for numeric text, or parses a date string into milliseconds.

**Call relations**: Email event pagination calls it before each request so incremental syncs start at the right time.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._synthetic_email_event_id`  (lines 1615–1625)

```
def _synthetic_email_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Creates a stable ID for an email event when HubSpot does not supply one.

**Data flow**: It receives an email event row and index. It combines creation time, recipient, event type, campaign ID, and a short payload hash into one safe string.

**Call relations**: Email event pagination uses it for rows missing IDs.

*Call graph*: called by 1 (_paginate_email_events).


##### `HubSpotConnector._stable_payload_hash`  (lines 1628–1630)

```
def _stable_payload_hash(row: dict[str, Any]) -> str
```

**Purpose**: Creates a short fingerprint of a record's full contents. This helps make synthetic IDs stable while still distinguishing similar records.

**Data flow**: It receives a dictionary, serializes it with sorted keys, hashes that text with SHA-256, and returns the first 16 hexadecimal characters.

**Call relations**: Synthetic event ID helpers use it as the final uniqueness piece when HubSpot does not provide IDs.

*Call graph*: 2 external calls (sha256, dumps).


##### `HubSpotConnector._paginate_association_labels`  (lines 1632–1648)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the labels HubSpot uses to describe relationships between object types. For example, a company-contact link may have a specific relationship label.

**Data flow**: It receives a client. It walks object-type pairs that have labels, converts each label into a normalized row, and yields pages.

**Call relations**: `_paginate_product_api` calls it for the `association_labels` stream. It depends on pair discovery and label-row shaping helpers.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_associations`  (lines 1650–1665)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual relationship rows between HubSpot records across object types. This answers questions like which contacts belong to which deals.

**Data flow**: It receives a client. For each object-type pair with labels, it pages through source object IDs, sends batch association reads, and yields normalized relationship rows.

**Call relations**: `_paginate_product_api` calls it for the `associations` stream. It coordinates pair discovery, object ID paging, and batch association reads.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_association_batch`  (lines 1667–1694)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads associations for a batch of source records and handles per-record association pagination. Some HubSpot association responses need follow-up requests for the same source ID.

**Data flow**: It receives object types and input IDs. It posts a batch-read request, converts the response into rows, yields them, then builds any follow-up inputs from nested paging cursors.

**Call relations**: `_paginate_associations` calls it for each source-ID page. It uses helpers to detect unavailable pairs, shape rows, and find next inputs.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1697–1711)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Finds follow-up association requests needed for records whose relationship list continues onto another page.

**Data flow**: It receives a batch association response. It looks inside each result for the source record ID and next `after` cursor, then returns a new list of inputs.

**Call relations**: Association batch pagination calls it after each batch response to decide whether more association reads are needed.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1713–1726)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Discovers object-type pairs that actually have association labels. This avoids trying to read every possible pair when HubSpot says no relationship exists.

**Data flow**: It receives a client. It gets all standard and custom object types, checks labels for each from-to combination, and yields only pairs with labels.

**Call relations**: Both association-label syncing and association-row syncing use it as their starting point.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1728–1741)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the list of HubSpot object types to consider for associations. It includes standard types and any account-specific custom object types.

**Data flow**: It receives a client. It starts with built-in object type names, tries to load custom object schemas, extracts their type IDs, and returns the combined list.

**Call relations**: Association pair discovery calls it before checking pair labels.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1743–1759)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches relationship labels for one source object type and one target object type.

**Data flow**: It receives two object type names. It calls HubSpot's labels endpoint and returns valid label dictionaries, or an empty list when that optional pair is unavailable.

**Call relations**: Association pair discovery calls it for each possible object-type pair.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1762–1779)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Normalizes one association label into a row with a stable ID and clear from/to object fields.

**Data flow**: It receives a raw label and the two object types. It extracts type ID, category, and display label, adds from/to metadata, and returns one dictionary.

**Call relations**: Association-label pagination calls it for each label returned by HubSpot.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1781–1793)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Reads CRM object pages and reduces them to just record IDs. This is useful for later fan-out calls that only need IDs.

**Data flow**: It receives an object type. It asks `_paginate_crm_object_pages` for records with `hs_object_id`, extracts non-null IDs, and yields ID lists.

**Call relations**: Association syncing and sequence-enrollment syncing use it to get batches of records to query further.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1795–1825)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Pages through the basic HubSpot CRM list endpoint for an object type. This is separate from search and is used for lightweight ID or identity scans.

**Data flow**: It receives an object type and requested properties. It calls the list endpoint, yields dictionary records, follows `after` pagination, and skips optional unavailable object types.

**Call relations**: ID-page and contact-identity helpers call it as their shared low-level CRM list reader.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1828–1862)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Turns a HubSpot batch association response into flat relationship rows. It handles multiple association types for the same pair of records.

**Data flow**: It receives raw association data and the from/to object types. It walks each source record, each target record, and each association type, returning normalized rows.

**Call relations**: Association batch pagination calls it after each batch-read response.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1865–1892)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one normalized relationship row between two HubSpot records.

**Data flow**: It receives association type details plus from/to object IDs. It creates a stable ID, records both sides of the relationship, stores category and label details, and returns the row.

**Call relations**: The association response shaper uses it for each discovered record-to-record link.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1895–1898)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether an error means an optional HubSpot pair or endpoint simply is not available. This prevents expected gaps from breaking a whole sync.

**Data flow**: It receives an HTTP error. It returns true for 400 or 404, or when the broader permission checker says the stream is unavailable.

**Call relations**: Many fan-out flows use it around optional endpoints, including associations, list memberships, consent status, sequences, and CRM object paging.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1900–1914)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads which records belong to each HubSpot list. It first discovers lists, then reads memberships for every list.

**Data flow**: It receives a client. It pages through lists, extracts each list ID, calls the per-list membership paginator, and yields membership pages.

**Call relations**: `_paginate_product_api` calls it for the `list_memberships` stream.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1916–1959)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads membership rows for one HubSpot list and adds list context to every member.

**Data flow**: It receives a list record and list ID. It pages through that list's memberships, creates IDs from list ID and record ID, adds list name and object type details, and yields rows.

**Call relations**: List-membership pagination calls it once for each list.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1961–1974)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot communication subscription definitions. These describe the kinds of email preferences a contact can opt into or out of.

**Data flow**: It receives a client, fetches definitions, accepts possible response field names, assigns IDs, and yields one page if rows exist.

**Call relations**: `_paginate_product_api` calls it for the `subscription_definitions` stream.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_consent_states`  (lines 1976–1989)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads email consent and unsubscribe status for contacts with email addresses. This captures communication preferences tied to each contact.

**Data flow**: It receives a client. It pages through contact identities, then for each email asks for subscription statuses and unsubscribe-all statuses, combines the rows, and yields pages.

**Call relations**: `_paginate_product_api` calls it for the `consent_states` stream. It coordinates contact identity paging and two consent endpoints.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1991–2007)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads contact IDs and email addresses in pages. Consent lookups need an email address, not just a contact ID.

**Data flow**: It receives a client. It uses the CRM object page helper to request contact email properties, lifts the email into a top-level field, and yields pages.

**Call relations**: Consent-state pagination calls it before querying communication preference endpoints.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 2009–2030)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches normal subscription consent statuses for one email address.

**Data flow**: It receives a contact row and email. It URL-escapes the email, calls the statuses endpoint, converts each result into a consent row, and returns the list; unavailable optional responses return an empty list.

**Call relations**: Consent-state pagination calls it for each contact email.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 2032–2056)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches the global unsubscribe-all status for one email address.

**Data flow**: It receives a contact row and email. It calls the unsubscribe-all endpoint, converts each result into a consent row marked as unsubscribe-all, and returns the list; optional unavailable responses become an empty list.

**Call relations**: Consent-state pagination calls it alongside normal subscription status lookup.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 2059–2089)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Normalizes one communication-preference response into a clear consent-state row.

**Data flow**: It receives a raw preference row, contact, email, and status kind. It builds a stable ID, adds contact and email fields, names the purpose and subscription type, copies legal basis and source, and returns the row.

**Call relations**: Both consent-status and unsubscribe-all helpers use it to make their rows match the same shape.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2091–2118)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sales sequences for each HubSpot user connected to an owner. Sequences are queried per user rather than from one global list.

**Data flow**: It receives a client. It discovers users from owners, then for each user pages through sequence records and adds owner context before yielding rows.

**Call relations**: `_paginate_product_api` calls it for the `sequences` stream. It depends on `_sequence_user_rows` and generic collection pagination.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_user_rows`  (lines 2120–2143)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the list of HubSpot user IDs that should be checked for sequences. It derives them from owner records.

**Data flow**: It receives a client, reads owners, extracts unique `userId` values, keeps owner ID and email as context, and yields one page.

**Call relations**: Sequence pagination calls it before making per-user sequence API requests.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2145–2163)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sequence enrollment information for contacts. It checks each contact ID because HubSpot exposes enrollments through a per-contact endpoint.

**Data flow**: It receives a client. It pages through contact IDs, calls the enrollment endpoint for each contact, converts responses into rows, and yields accumulated pages.

**Call relations**: `_paginate_product_api` calls it for the `sequence_enrollments` stream. It uses ID paging and `_sequence_enrollment_rows`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2166–2180)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes sequence enrollment responses for one contact.

**Data flow**: It receives raw enrollment data and a contact ID. It accepts either a `results` list or a single object, assigns IDs, adds the contact ID, and returns rows.

**Call relations**: Sequence-enrollment pagination calls it after each per-contact API response.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2182–2211)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads submissions for every HubSpot form. Forms are listed first, then each form's submissions are read separately.

**Data flow**: It receives a client. It pages through forms, builds a submissions endpoint for each form, pages through submissions, assigns IDs, adds form ID and name, and yields rows.

**Call relations**: `_paginate_product_api` calls it for the `form_submissions` stream.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2213–2228)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages inside HubSpot conversation threads. Threads are listed first, then messages are fetched per thread.

**Data flow**: It receives a client. It pages through conversation threads, calls each thread's messages endpoint, adds the thread ID to every message, and yields message pages.

**Call relations**: `_paginate_product_api` calls it for the `conversation_messages` stream.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipelines`  (lines 2230–2237)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads deal and ticket pipelines. Pipelines describe the stages records move through, like sales or support workflows.

**Data flow**: It receives a client. For each supported object type, it asks for normalized pipeline rows and yields them if present.

**Call relations**: `_paginate_product_api` calls it for the `pipelines` stream, and it relies on `_pipeline_rows_for_object_type`.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2239–2289)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the stages inside deal and ticket pipelines and normalizes useful stage metadata.

**Data flow**: It receives a client. It fetches raw pipelines for each object type, walks their stage lists, builds stable stage IDs, adds pipeline and object context, status, probability, order, and closed-state details, then yields rows.

**Call relations**: `_paginate_product_api` calls it for the `pipeline_stages` stream.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2291–2314)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes pipeline records for one HubSpot object type, such as deals or tickets.

**Data flow**: It receives a client and object type. It fetches raw pipelines, creates IDs that include the object type, copies names and status, and returns rows.

**Call relations**: Pipeline pagination calls it once for each supported pipeline object type.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2316–2327)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches raw pipeline data for one HubSpot object type. It treats forbidden or missing pipeline endpoints as simply empty.

**Data flow**: It receives a client and object type. It calls HubSpot's pipeline endpoint, returns valid result dictionaries, or returns an empty list for 403 and 404.

**Call relations**: Pipeline and pipeline-stage normalizers call it before shaping rows.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2330–2334)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects HubSpot's specific error for object types that cannot page through deleted records. This lets the connector skip only the deletion sweep.

**Data flow**: It receives an HTTP error. It checks for status 400 and a known message about deleted-object paging, returning true only for that case.

**Call relations**: Archived-ID paginators for standard and custom objects use it when deciding whether to stop quietly.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2337–2345)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Extracts HubSpot's text message from an HTTP error response when possible.

**Data flow**: It receives an HTTP error. It tries to parse JSON, checks for a `message` field, and returns that text or `null`.

**Call relations**: Archived-sweep error detection uses it to recognize HubSpot's unsupported-deleted-paging message.


##### `HubSpotConnector._paginate_junction`  (lines 2347–2394)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds simple relationship rows for specific parent-to-target pairs, such as deal-to-contact. These rows come from associations embedded in the parent object's list response.

**Data flow**: It receives parent and target object names. It pages through parent records with associations included, extracts each parent ID and target ID pair, creates a stable row ID, and yields rows until no pages remain.

**Call relations**: `_paginate_unchecked` calls it for the configured junction streams. It is a lighter relationship path for common pairs where full association metadata is not needed.

*Call graph*: called by 1 (_paginate_unchecked).


### `extensions/sources/ufo_ext_sources/salesforce.py`

`io_transport` · `during source sync`

Salesforce stores customer data in named record types called SObjects, such as Account or Contact. This file defines which Salesforce objects the product knows how to sync, then teaches the sync engine how to ask Salesforce for those records through Salesforce’s REST API, which is a web-based interface for reading data.

A key point is that the connector does not hard-code every field on every Salesforce object. Instead, before syncing an object, it asks Salesforce to “describe” that object and list its available fields. This matters because different Salesforce organizations can have custom fields. The connector then builds a SOQL query, which is Salesforce’s SQL-like search language, to fetch records ordered by SystemModstamp, the timestamp used as the sync cursor. A cursor is like a bookmark: after one run, the next run can ask only for records changed after that point.

The connector follows Salesforce pagination links until there are no more records. On incremental runs, it also asks Salesforce for records deleted since the last cursor and emits a tombstone page, meaning “this record used to exist, now remove it.” If Salesforce refuses access with an authorization error, the stream is skipped with a clear message rather than failing mysteriously. Finally, each returned record is flattened by removing Salesforce’s metadata envelope called attributes, leaving just the useful field data.

#### Function details

##### `_stream`  (lines 29–38)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Salesforce stream, such as accounts or contacts. It records the Salesforce object name, the primary key field, and the timestamp fields used for incremental syncing.

**Data flow**: It receives a friendly stream name, a Salesforce SObject name, and whether the stream is considered canonical. It fills in the shared Salesforce defaults, such as Id as the primary key and SystemModstamp as the cursor field, then returns a StreamSpec object that the sync engine can use later.

**Call relations**: This function is used while the module is being loaded to build the Salesforce stream list. It hands each completed StreamSpec to the connector class through SALESFORCE_STREAMS, so later sync runs know which Salesforce objects are available.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 78–82)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: This builds the Salesforce query used to fetch records for one stream. It includes all known fields, optionally filters to records newer than the saved cursor, orders them oldest-to-newest, and limits the page size.

**Data flow**: It takes a stream description, a list of field names discovered from Salesforce, and an optional cursor. It turns those into a SOQL text query like “select these fields from this object after this timestamp,” then returns that query string for the API request.

**Call relations**: The paginate method calls this after it has asked Salesforce what fields exist. The resulting query is then sent to Salesforce’s query endpoint so the sync can retrieve the next batch of records.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 84–90)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: This asks Salesforce which fields exist on a given object. It avoids assuming a fixed schema, which is important because Salesforce installations often include custom fields.

**Data flow**: It receives an HTTP client and a Salesforce object name. It calls Salesforce’s describe endpoint, reads the returned field list, keeps only valid field names, and returns those names as a simple list of strings.

**Call relations**: The paginate method calls this at the start of a stream sync. Its output feeds directly into _build_soql, so the next API call can request every field Salesforce says is available.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 92–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main read loop for a Salesforce stream. It fetches records page by page, follows Salesforce’s next-page links, and on incremental runs also reports deleted records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. It first discovers the object’s fields, builds a SOQL query, sends that query to Salesforce, yields each non-empty batch of records, follows nextRecordsUrl until Salesforce says the query is done, and then, if a cursor existed, asks for deletes since that cursor and yields a delete page when needed. If Salesforce returns a 401 or 403 refusal, it changes that low-level web error into a StreamSkipped message explaining that access is not allowed.

**Call relations**: This method is the connector’s central handoff point to the wider sync engine: the engine asks it for pages, and it yields raw record pages or deletion pages. Inside that flow it calls _describe_fields to learn the schema, _build_soql to create the query, and _deleted_page to include hard-deleted records.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 121–139)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: This asks Salesforce which records were hard-deleted between the previous cursor and now. It packages those missing record IDs as tombstones so the rest of the system can remove or mark old copies correctly.

**Data flow**: It receives an HTTP client, a stream description, and the previous cursor timestamp. It chooses the current time as the end of the deletion window, calls Salesforce’s deleted-records endpoint, extracts deleted record IDs, chooses the next cursor from Salesforce’s latest covered date when available, and returns a StreamPage containing deletes and the next cursor. If there is nothing meaningful to report, it can return nothing.

**Call relations**: The paginate method calls this only after normal record fetching and only when there is already a cursor, meaning the run is incremental. The StreamPage it creates is handed back to the sync engine alongside normal record pages, but it means “delete these IDs” rather than “store these records.”

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 141–144)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This cleans up a Salesforce record before the rest of the system sees it. Salesforce includes an attributes wrapper with API metadata, and this function removes that wrapper so only the actual record fields remain.

**Data flow**: It receives one Salesforce record and its stream description. If the record contains an attributes key, it returns a new dictionary with that key removed; otherwise, it returns the original record unchanged.

**Call relations**: This fits into the connector’s normal record-cleaning step after records have been fetched. It does not call other project functions here; it simply shapes Salesforce’s response into the flatter form expected by downstream sync and storage code.


### Support and Customer Conversations
Connectors that read help desk, ticketing, and customer conversation data from support platforms.

### `extensions/sources/ufo_ext_sources/freshdesk.py`

`io_transport` · `source sync / request handling`

Freshdesk exposes helpdesk data through a web API: tickets, conversations, contacts, companies, agents, knowledge-base articles, forum topics, and several admin settings. This file is the read-only bridge between that API and the rest of the UFO source-sync system. Without it, the system would not know where Freshdesk keeps each kind of record, how to authenticate, or how to keep asking for the next batch of records until a stream is complete.

The file first defines the Freshdesk streams: named collections such as tickets, contacts, and solution articles. A stream is like a labeled conveyor belt of records. Some are simple one-page-after-another lists. Others are nested: for example, conversations live under tickets, and solution articles live under folders, which live under categories.

The FreshdeskConnector builds an HTTP client using the tenant’s Freshdesk domain and an API key. It then routes each requested stream to the right paging strategy. Tickets use Freshdesk’s numbered pages and can start from an updated-since cursor for incremental syncs. Most simple resources use the API’s “next page” link header. Nested resources first fetch parent records, then use each parent id to fetch children. If Freshdesk rejects access with a 401 or 403 response, the connector marks that stream as skipped instead of pretending the sync succeeded. There is deliberately no write path here; this connector only reads Freshdesk data.

#### Function details

##### `_stream`  (lines 55–69)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a StreamSpec, which is the system’s small description of one Freshdesk collection to sync. It lets the file list many streams without repeating the same setup details each time.

**Data flow**: It receives a stream name plus optional details such as the Freshdesk source path name, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults, then returns a StreamSpec object that the connector can later advertise and use.

**Call relations**: This helper is used while the file is loaded to build the Freshdesk stream list. It hands the finished stream descriptions to StreamSpec so the broader source framework knows what Freshdesk data is available.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector._make_client`  (lines 109–124)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Freshdesk. It applies the Freshdesk base URL, JSON headers, timeouts, and authentication method.

**Data flow**: It receives a base URL and a resolved credential. It trims the URL, prepares request headers and timeout limits, then either uses a provided custom transport or creates Basic authentication from the API key. It returns an httpx AsyncClient ready to make Freshdesk requests; if there is no usable credential, it raises an error.

**Call relations**: The parent RestConnector calls this when a sync needs a network client. This function delegates the low-level connection pieces to httpx, including AsyncClient, Timeout, and BasicAuth, then the paging functions use that client for actual API calls.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 127–137)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: Builds the query settings for one page of Freshdesk tickets. This keeps ticket paging consistent, including page size, ordering, included details, and optional incremental filtering.

**Data flow**: It receives an optional cursor timestamp and a page number. It creates a parameter dictionary asking for up to 100 tickets, ordered by updated time from oldest to newest, with selected extra ticket details included. If a cursor is present, it adds Freshdesk’s updated_since filter. The result is passed into the ticket API request.

**Call relations**: FreshdeskConnector._paginate_tickets calls this before each ticket request. It does not make network calls itself; it only prepares the request parameters that _paginate_tickets will send.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 139–213)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct way to fetch pages for the requested Freshdesk stream. It is the main dispatcher that turns a stream name like tickets or solution_articles into the right sequence of API calls.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, then yields lists of records from the matching paging helper. For simple streams it looks up the REST path and follows link-header pagination. If Freshdesk refuses access with 401 or 403, it converts that failure into StreamSkipped so the sync can clearly report that this stream was not allowed.

**Call relations**: The source framework calls this when it wants records for a stream. This function then hands off to _paginate_tickets, _paginate_conversations, _paginate_two_level, _paginate_three_level, or _paginate_link_header depending on the stream’s shape.

*Call graph*: calls 6 internal fn (__init__, _paginate_conversations, _paginate_link_header, _paginate_three_level, _paginate_tickets, _paginate_two_level).


##### `FreshdeskConnector._paginate_link_header`  (lines 215–222)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches resources that use Freshdesk’s standard “next page” link style. A link header is a response header that tells the client where the next page of results lives.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It starts with a page size of 100, follows each next-page link provided by the API, and yields each page as a list of records.

**Call relations**: FreshdeskConnector.paginate uses this for simple streams. The nested paging helpers also call it whenever they need to fetch a parent list or a child list. The actual link-following work is delegated to the inherited _get_link_header_pages helper.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 224–241)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Freshdesk tickets using the special numbered-page style required by the tickets endpoint. It supports incremental sync by asking only for tickets updated after a cursor timestamp.

**Data flow**: It receives an HTTP client and an optional cursor. Starting at page 1, it builds ticket query parameters, requests a page of tickets, yields the records, and then moves to the next page. It stops when Freshdesk returns no records, returns fewer than the page limit, or reaches Freshdesk’s 300-page ceiling.

**Call relations**: FreshdeskConnector.paginate calls this for the tickets stream. FreshdeskConnector._paginate_conversations also calls it first, because conversations are fetched by walking through tickets and then asking for each ticket’s conversations.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, paginate).


##### `FreshdeskConnector._paginate_conversations`  (lines 243–259)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches ticket conversations by first finding the relevant tickets, then asking Freshdesk for the conversations under each ticket. This is needed because conversations are not read as one flat global list here.

**Data flow**: It receives an HTTP client and an optional cursor. It uses _paginate_tickets to get ticket pages, reads each ticket id, then follows link-header pagination for that ticket’s conversation endpoint. Before yielding each conversation page, it makes sure each conversation record has the ticket_id attached.

**Call relations**: FreshdeskConnector.paginate calls this for the conversations stream. This helper depends on _paginate_tickets to find parent tickets and _paginate_link_header to fetch each ticket’s child conversation pages.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (paginate).


##### `FreshdeskConnector._paginate_two_level`  (lines 261–272)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches nested Freshdesk data where child records live directly under parent records. Examples include folders under categories, responses under response folders, forums under discussion categories, and comments under topics.

**Data flow**: It receives an HTTP client, a parent API path, and a child path template containing a parent id placeholder. It fetches parent pages, reads each parent id, builds the child path for that id, then yields each child page returned by Freshdesk.

**Call relations**: FreshdeskConnector.paginate calls this for several two-step streams. This helper uses _paginate_link_header for both the parent list and each child list, so it can reuse the standard Freshdesk next-page behavior.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


##### `FreshdeskConnector._paginate_three_level`  (lines 274–297)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches nested Freshdesk data that is three levels deep. In this file it is used for solution articles, which sit under folders, which sit under solution categories.

**Data flow**: It receives an HTTP client plus paths for the root level, middle level, and leaf level. It fetches root records, reads each root id, fetches middle records under that root, reads each middle id, then fetches and yields the leaf pages under each middle record.

**Call relations**: FreshdeskConnector.paginate calls this for streams that require a three-step tree walk. It relies on _paginate_link_header at every level so each category, folder, and final article list can span multiple API pages.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/intercom.py`

`io_transport` · `during source sync, while fetching Intercom records`

Intercom does not expose all of its data in one simple shape. Some records are fetched through a search endpoint, some through a scrolling endpoint, some through one-off list endpoints, and some only appear after first fetching a parent record. This file is the adapter that hides those differences from the rest of the project.

It defines the Intercom streams the system knows about, including what each stream is called, what field uniquely identifies a record, and which timestamp can be used as a cursor. A cursor is a saved “last seen” value, like a bookmark, so the next sync can ask only for newer records.

The main class, IntercomConnector, builds an authenticated HTTP client, adds Intercom’s required API version header, and chooses the right paging method for each stream. For example, conversations and contacts use Intercom’s search API, companies use Intercom’s scroll API, and conversation parts are found by first searching conversations and then opening each conversation to collect its parts.

The file also lightly reshapes some nested Intercom records. It copies useful buried fields, such as a conversation’s source subject or a contact’s first company id, onto simple top-level keys. That makes later database or SQL work easier. If Intercom refuses access because the token lacks permission, the stream is marked as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 52–66)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a StreamSpec, which is the system’s description of one kind of Intercom data to sync. It saves repeated setup code so each stream can be declared in a short, readable way.

**Data flow**: It receives a stream name and optional details such as the Intercom object name, primary key, cursor field, and whether it is a main canonical stream. It fills in sensible defaults, then returns a StreamSpec object the connector can later use when syncing.

**Call relations**: This helper is used while the file is loaded to build INTERCOM_STREAMS. Those stream definitions are then attached to IntercomConnector so the wider source-sync framework knows what Intercom datasets are available.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector._make_client`  (lines 103–106)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Intercom and adds the Intercom API version header. Without this header, Intercom may interpret requests using a different API version or reject behavior the connector expects.

**Data flow**: It receives the Intercom base URL and a Credential containing authentication information. It asks the parent REST connector to create the authenticated client, adds the Intercom-Version header, and returns the ready-to-use client.

**Call relations**: This fits into the connector setup phase. The base RestConnector supplies the general client-building behavior, and this Intercom-specific override adds the one header that every later paging method relies on.


##### `IntercomConnector._build_search_body`  (lines 109–139)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the JSON request body for Intercom search endpoints. It tells Intercom how many records to return, how to sort them, where to continue within a page sequence, and which records are newer than the saved cursor.

**Data flow**: It receives a stream definition, the saved cursor from a previous sync, and an optional starting_after token from Intercom. It creates a request body with pagination, sorting, and a query filter. If the cursor looks like a number, it sends it as a number because Intercom expects timestamps that way. The result is a dictionary ready to send in a POST request.

**Call relations**: The search pagination methods call this each time they need another page. _paginate_search uses it for top-level search streams, and _paginate_conversation_parts uses it to find conversations before fetching their parts.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 142–147)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: Safely pulls the first dictionary out of a list-like value. It is a small guard against Intercom returning missing, empty, or oddly shaped nested data.

**Data flow**: It receives any value. If the value is a non-empty list and its first item is a dictionary, it returns that dictionary. Otherwise it returns None, meaning there was no usable first record.

**Call relations**: This is a helper for flattening records. The conversation and contact flattening code use it when they need the first associated contact or company without risking errors on empty or unexpected data.


##### `IntercomConnector._flatten_conversation`  (lines 150–167)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes important nested conversation fields easier to use later. It lifts details such as the message source type, subject, body, and requester id onto simple top-level fields.

**Data flow**: It receives one conversation record from Intercom. It copies the record, looks inside nested source and contacts sections, and adds flat keys when those nested values exist. It returns the copied record with those extra easy-to-read fields.

**Call relations**: The public flatten method calls this only for the conversations stream. Its output is then passed onward to the common sync machinery, which can store or transform flatter records more easily.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 170–179)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes the author of a conversation part easier to query. Intercom nests the author information, so this function copies the author type and id to top-level fields.

**Data flow**: It receives one conversation part record. It copies the record, looks for an author dictionary, and adds author_type and author_id if present. It leaves the conversation_id alone because that is added earlier by the paginator. It returns the updated copy.

**Call relations**: The public flatten method calls this for conversation_parts records. Those records are produced by _paginate_conversation_parts, which first stamps them with the parent conversation id.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 182–190)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a simple organization id field to contact records based on the first associated company. This helps later code connect a person to a company without digging through nested Intercom data.

**Data flow**: It receives one contact record. It copies the record, looks inside the nested companies section, picks the first company if available, and writes its id or company_id into org_id. It returns the copied record with that added field when possible.

**Call relations**: The public flatten method calls this only for the contacts stream. It uses _first to safely inspect the first company entry.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 192–206)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Prepares each Intercom record for storage or later transformation. It applies stream-specific flattening and converts integer cursor timestamps into strings so the wider sync system can store and compare them as watermarks.

**Data flow**: It receives a raw record and the stream definition it belongs to. Depending on the stream name, it may call a specialized flattening helper. Then, if the stream has a cursor field and that field is an integer, it returns a copy where that cursor value is a decimal string. Otherwise it returns the record as-is or with only the flattening changes.

**Call relations**: This is called after records have been fetched from Intercom. It hands off to _flatten_conversation, _flatten_conversation_part, or _flatten_contact for the streams that need extra shaping, then returns the cleaned record to the general sync pipeline.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 208–252)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct way to fetch pages for each Intercom stream. It is the traffic director that turns many Intercom API styles into one common flow of record batches.

**Data flow**: It receives an HTTP client, a stream definition, and an optional saved cursor. It checks the stream name, calls the matching pagination helper, and yields each list of records produced by that helper. If Intercom responds with a permission refusal, it converts that into a StreamSkipped signal so the run can record a skipped stream instead of failing everything.

**Call relations**: The wider RestConnector sync process calls this when it needs records for a stream. paginate then delegates to _paginate_search, _paginate_scroll, _paginate_list, _paginate_attributes, _paginate_conversation_parts, _paginate_company_segments, or _paginate_activity_logs depending on what Intercom endpoint shape that stream requires.

*Call graph*: calls 8 internal fn (__init__, _paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search).


##### `IntercomConnector._paginate_search`  (lines 254–274)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches streams that use Intercom’s search API, such as conversations, contacts, and tickets. It keeps asking for the next page until Intercom says there are no more results.

**Data flow**: It receives the HTTP client, the stream definition, and the saved cursor. It builds a search request body, sends a POST request to the stream’s search path, yields any records found, reads Intercom’s next starting_after token, and repeats until that token is missing.

**Call relations**: paginate calls this for streams listed in the search-path table. It relies on _build_search_body to create each request and then yields record batches back to paginate, which passes them into the normal sync flow.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_scroll`  (lines 276–290)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches companies using Intercom’s scroll API. A scroll API is like being given a temporary ticket for the next slice of a long list.

**Data flow**: It starts with no scroll token, calls /companies/scroll, yields the returned company records, then saves Intercom’s scroll_param for the next request. It repeats until there are no records or no next scroll token.

**Call relations**: paginate calls this for the companies stream. The batches it yields are returned to the shared sync machinery just like pages from any other Intercom stream.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_list`  (lines 292–304)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches simple list-style streams that Intercom returns in one response, such as admins, tags, teams, and segments. These streams do not need cursor paging in this connector.

**Data flow**: It receives the HTTP client and stream definition, looks up the endpoint path, sends one GET request, and checks likely response keys for a list of records. If it finds a non-empty list, it yields that list once.

**Call relations**: paginate calls this for streams in the list-path table. Unlike the search or scroll helpers, this usually finishes after one response.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_attributes`  (lines 306–315)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Intercom data attribute definitions for companies or contacts. These are metadata records that describe custom fields rather than normal customer records.

**Data flow**: It receives the HTTP client and stream definition, maps the stream name to the Intercom model name, calls /data_attributes with that model as a parameter, and yields the returned data list if it is not empty.

**Call relations**: paginate calls this for company_attributes and contact_attributes. It converts the project’s stream names into the model names Intercom expects before handing records back to the sync flow.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_conversation_parts`  (lines 317–349)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches individual messages or events inside conversations. Intercom does not return these as a plain top-level list, so the connector first finds conversations and then opens each one to collect its parts.

**Data flow**: It receives the HTTP client and saved cursor. It searches conversations using the same cursor logic as the conversations stream, then for each conversation id it calls the conversation detail endpoint. It extracts the nested conversation_parts list, adds the parent conversation_id to each part when possible, yields any parts found, and continues through search pages until there is no next token.

**Call relations**: paginate calls this for the conversation_parts stream. It uses _build_search_body to page through parent conversations, then performs extra GET requests for each conversation so that the child records can be synced as their own stream.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_company_segments`  (lines 351–375)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the segments attached to each company. Since segments are reached through each company, this function walks through companies first and then asks Intercom for each company’s segment list.

**Data flow**: It scrolls through companies using /companies/scroll. For each company with an id, it calls /companies/{id}/segments, extracts the returned segment records, adds company_id to each segment when possible, and yields those segment lists. It continues until the company scroll has no more records or no next scroll token.

**Call relations**: paginate calls this for the company_segments stream. It builds on the same company-scrolling pattern as _paginate_scroll, but fans out into extra requests for child segment records.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_activity_logs`  (lines 377–399)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches admin activity log records, optionally only after a saved created_at cursor. It follows Intercom’s next-page links until the log pages are exhausted.

**Data flow**: It starts with the /admins/activity_logs path. If a cursor is present, it sends it as created_at_after on the first request. For each response, it yields any activity_logs records, reads the next page link, converts a full URL into a relative path if needed, and keeps going until no next link remains.

**Call relations**: paginate calls this for the activity_logs stream. It is separate from the other pagination helpers because this endpoint uses next links rather than the search API’s starting_after token or the company scroll token.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/zendesk.py`

`io_transport` · `source sync`

Zendesk exposes many kinds of data through its web API, and each kind is not fetched in exactly the same way. This file is the connector that knows those differences. It defines the list of Zendesk streams the system can sync, such as tickets, users, organizations, ticket comments, audit logs, article votes, and community posts. A stream is one category of records to pull.

The main class, ZendeskConnector, works like a librarian who knows which shelf to visit for each topic. For high-volume data like tickets and users, it uses Zendesk's incremental cursor export, which means it asks for records changed since a saved point in time and follows Zendesk's “next” links until there is nothing left. For ordinary streams, it uses standard page-by-page API links. Some streams need special treatment: ticket comments are hidden inside ticket event records, so the connector extracts only the comment events and adds the ticket ID; user identities require first listing users and then asking for each user’s identities.

The connector also smooths out Zendesk quirks. Ticket records can include related users, so it copies requester, submitter, and assignee email addresses onto each ticket when Zendesk provides them. If Zendesk refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of treating the whole sync as a mysterious crash.

#### Function details

##### `_stream`  (lines 58–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a StreamSpec, which is the small description the sync system uses to know what a Zendesk stream is called, where it comes from, and which fields identify or order its records. It keeps the long stream list readable by avoiding repeated setup code.

**Data flow**: It receives a friendly stream name plus optional details like the Zendesk API object name, primary key, and timestamp fields. It fills in sensible defaults when details are not supplied, then returns a StreamSpec object that the rest of the connector can use during syncing.

**Call relations**: It is used while building the module-level Zendesk stream list. Its only handoff is to StreamSpec creation, turning compact stream declarations into the structured stream descriptions consumed later by ZendeskConnector.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 142–167)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: This helper copies useful data from related records that Zendesk sends alongside the main records. In practice, it adds user email addresses onto ticket records so later users of the synced data do not have to perform extra lookups.

**Data flow**: It receives the main records, the full API page, and instructions for which related array and ID fields to match. It builds a lookup table from the related records, then walks through each main record and fills missing target fields, such as requester_email, when it can match an ID to a related user with an email address. It changes the records in place and returns nothing.

**Call relations**: It is called during incremental cursor pagination for streams that request sideloaded data, currently tickets. The pagination code fetches a page from Zendesk, then asks this helper to enrich the records before yielding them onward.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 176–177)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This method decides which JSON field in a Zendesk response contains the records for a stream. It exists because some Zendesk endpoints use names that do not exactly match this connector’s stream names.

**Data flow**: It receives a stream description. It checks a table of special cases, such as account_attributes mapping to attributes, and otherwise uses the stream name itself. It returns the response field name to read.

**Call relations**: The default pagination path calls this before reading each page. It gives _paginate_default the correct key to pull records from, so standard Zendesk endpoints can share one paging routine despite small naming differences.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 180–192)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: This method turns the connector’s saved cursor into a Unix timestamp, which is a number of seconds since January 1, 1970. Zendesk’s incremental APIs need that numeric form to ask for records changed after a point in time.

**Data flow**: It receives a cursor that may be missing, already numeric, or written as an ISO date string. If it is missing or unreadable, it returns 0, meaning the beginning of Unix time. If it is a number, it returns that number. If it is a date string, it parses it, assumes UTC when no timezone is present, and returns the matching timestamp.

**Call relations**: The incremental pagination methods call this when building their first Zendesk URL. It lets ticket, user, organization, metric event, ticket comment, and user identity syncs all start from the correct saved time.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 195–204)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: This method converts Zendesk’s full next-page URL into just the path and query part the connector needs for its HTTP client. It lets the connector follow Zendesk pagination links safely without rebuilding them by hand.

**Data flow**: It receives a next-page URL or nothing. If there is no URL, or the URL has no path, it returns nothing. Otherwise it parses the URL, keeps the path and query string, and returns that shorter API path.

**Call relations**: All pagination routines use this after each page is fetched. Zendesk returns fields like next_page or after_url; this method turns those into the next request path until pagination is complete.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 206–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a Zendesk stream. Given a stream, it chooses the right paging strategy and yields batches of records to the wider sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks the stream name, then delegates to the special ticket comment reader, the user identity reader, the incremental cursor reader, or the default page reader. It yields each batch of records produced by that chosen reader. If Zendesk answers with 401 or 403, it turns that refusal into a StreamSkipped error with a clear message.

**Call relations**: The base source framework calls this when it wants records for a Zendesk stream. This method acts as the dispatcher: it does not fetch most records itself, but sends the work to the pagination method that matches Zendesk’s API shape for that stream.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 232–253)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads high-volume Zendesk streams using Zendesk’s cursor-based incremental export. That means it starts from a saved time and keeps following Zendesk’s cursor links until the export says the stream is finished.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It converts the cursor to a Unix timestamp, builds the incremental export URL, and repeatedly fetches pages. From each response it takes the stream records, optionally enriches them with sideloaded user emails, yields non-empty batches, and then follows after_url or next_page until Zendesk reports end_of_stream.

**Call relations**: paginate calls this for tickets, users, organizations, and ticket_metric_events. It relies on _cursor_to_unix to start at the right time, _apply_sideload to enrich ticket data when needed, and _next_page_path to move through the API’s pagination chain.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 255–265)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads ordinary Zendesk endpoints that use simple next-page pagination. It is the shared path for streams that do not need the incremental export or special extraction logic.

**Data flow**: It receives an HTTP client and a stream description. It builds the first API path from the stream’s source object, finds the correct response field name, fetches each page, yields any records it finds, and follows the next_page link until there are no more pages.

**Call relations**: paginate calls this when a stream has no special paging needs. It uses _data_field to know where records live in the response and _next_page_path to continue from page to page.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 267–299)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method extracts ticket comments from Zendesk’s ticket event feed. Zendesk does not expose these comments in the same simple shape as many other objects, so this method turns nested comment events into normal rows.

**Data flow**: It receives an HTTP client and an optional cursor. It starts from the cursor time, fetches ticket event pages with comment events included, and scans each event’s child events. When it finds a child event whose type is Comment, it copies that child, adds the parent ticket_id, normalizes a numeric created_at value into an ISO date string when needed, and collects it. It yields batches of extracted comments and follows Zendesk’s cursor links until the stream ends.

**Call relations**: paginate calls this only for the ticket_comments stream. It uses _cursor_to_unix to choose the starting point and _next_page_path to follow after_url or next_page links through the event feed.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate); 1 external calls (fromtimestamp).


##### `ZendeskConnector._paginate_user_identities`  (lines 301–326)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads identity records for users, such as login or email identities, by first finding users and then asking Zendesk for each user’s identities. It exists because identities are reached through each individual user rather than as one flat global list.

**Data flow**: It receives an HTTP client and an optional cursor. It fetches changed users from Zendesk’s incremental user export, skips malformed users or users without IDs, then for each user fetches that user’s identities page by page. It yields identity batches when found, then moves to the next user page until Zendesk reports the user stream is finished.

**Call relations**: paginate calls this for the users_identities stream. It uses _cursor_to_unix to start from the saved sync point and _next_page_path both for identity pagination and for moving through the incremental user list.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).
