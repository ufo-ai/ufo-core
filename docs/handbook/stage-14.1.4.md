# CRM and customer support connectors  `stage-14.1.4`

This stage is the set of “adapters” that lets the system read customer and support data from outside services during a sync. A sync is the repeated work of asking another product for its latest records, then shaping those records so this system can store and search them in a consistent way.

Each file is one adapter for a different service. Attio brings in CRM items such as companies, people, deals, tasks, notes, meetings, and call recordings. HubSpot handles a wider mix, including contacts, companies, deals, emails, forms, campaigns, analytics, and custom objects, and smooths over HubSpot’s many API formats. Salesforce reads core sales records like accounts, contacts, opportunities, and tasks. Freshdesk brings in helpdesk data such as tickets, conversations, agents, contacts, companies, articles, forums, and settings. Intercom reads customer conversations, contacts, companies, teams, tags, tickets, and activity logs. Zendesk reads support tickets, users, organizations, Help Center and community content, plus admin data. Together, these connectors act like translators between many customer platforms and one shared search system.

## Files in this stage

### CRM and sales records
Connectors that normalize relationship, sales, account, contact, deal, opportunity, task, and related CRM records.

### `extensions/sources/ufo_ext_sources/attio.py`

`io_transport` · `sync run / source data fetching`

Attio’s API does not present all data in one simple shape. Some things, like companies and people, are fetched through object-specific record-query endpoints. Tasks and notes use their own workspace-wide endpoints. Meetings and call recordings use newer cursor-based paging, where the API gives back a “next page” token instead of a numeric offset. This file hides those differences behind one connector so the rest of the system can ask for a stream of records without caring how Attio serves them.

The most important work here is flattening. Attio records often store their real identifier inside an `id` object, and most fields inside nested `values` cells. That is like receiving a filing cabinet where every label is inside a sealed envelope: before the rest of the system can file or compare the record, this connector opens the envelopes and puts the useful labels on the outside. It creates top-level keys such as `record_id`, `task_id`, `meeting_id`, and `call_recording_id`.

The connector only reads; it does not write back to Attio. It treats each stream as a full snapshot, meaning missing records can be deleted downstream. It also knows when to skip a stream cleanly, such as when an Attio standard object is disabled or the OAuth permission grant is missing a required scope.

#### Function details

##### `_records_stream`  (lines 35–43)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates the standard description for an Attio object stream, such as companies, people, or deals. This tells the sync system what the stream is called, what Attio object it reads, and which field uniquely identifies each record.

**Data flow**: It receives a friendly stream name, an Attio object slug, and whether the stream is canonical. It builds a `StreamSpec`, which is a small recipe the rest of the connector uses to know how to fetch and store that stream. The result is a configured stream description with `record_id` as its primary key and full-snapshot behavior enabled.

**Call relations**: This helper is used while the file defines the list of Attio streams. It hands off to `StreamSpec.__init__` to create the stream recipe that `AttioConnector` later uses during pagination and flattening.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 70–71)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: Safely pulls one named identifier out of a nested dictionary. It is used when Attio puts useful IDs inside another `id` object.

**Data flow**: It receives any value and the key to look for. If the value is a dictionary, it returns the value under that key; otherwise it returns `None`. It never changes the input.

**Call relations**: It supports `AttioConnector._value_primitive`, which sometimes needs to extract fallback IDs from nested Attio objects such as select options or statuses.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 80–81)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: Builds the request body used when asking Attio for a page of object records. It keeps the page size consistent and sets the current offset.

**Data flow**: It receives an offset number, meaning how many records have already been read. It returns a small dictionary containing the fixed page limit and that offset. Nothing else is changed.

**Call relations**: `AttioConnector.paginate` calls this while walking through object streams like companies or people. The returned body is sent to Attio’s record-query endpoint.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._value_primitive`  (lines 84–127)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: Turns one Attio value-cell into the plain value a person would expect, such as text, a number, an email address, a selected option title, or a formatted location. This is the core translator from Attio’s nested field format into simple data.

**Data flow**: It receives a dictionary representing one Attio field value. It checks the shapes Attio commonly uses and returns the most natural plain value: for example `value`, `option.title`, `email_address`, `phone_number`, `currency_value`, or a joined address string. If it cannot find a useful value, it returns `None`.

**Call relations**: Flattening helpers use this function whenever they need to simplify Attio field cells. When an option or status needs an ID fallback, it calls `_nested_id` to safely pull that ID out.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 130–145)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: Simplifies one Attio field cell into either a single useful value, a list of selected option values, or `None`. It handles the fact that Attio may wrap even simple fields in lists.

**Data flow**: It receives a cell that may be a list, dictionary, or already-simple value. For lists, it converts each item to a primitive and removes empty results; most lists are reduced to their first useful value, while multi-select option lists stay as lists. For dictionaries, it delegates to the primitive extractor. The output is a simpler value ready for a flattened record.

**Call relations**: This is part of the flattening pipeline used by `AttioConnector._flatten_values`, which walks all fields in a record.


##### `AttioConnector._flatten_list_cell`  (lines 148–155)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: Simplifies a field that should remain a list, such as domains, categories, email addresses, or phone numbers. It preserves all useful values instead of picking only the first one.

**Data flow**: It receives a cell that may or may not already be a list. If it is not a list, it flattens it and wraps the result in a one-item list, or returns an empty list for no value. If it is a list, it converts each item to a primitive and drops empty values. The output is always a list.

**Call relations**: `AttioConnector._flatten_values` calls this for fields where the connector wants to keep multiple entries rather than collapse them to one.


##### `AttioConnector._flatten_values`  (lines 158–192)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts Attio’s nested `values` block into normal top-level fields. It also adds convenient shortcut fields like `email`, `phone`, `domain`, and `category` from the first item in their lists.

**Data flow**: It receives the dictionary of Attio attribute values. For each field, it chooses either list-flattening or single-cell flattening, then stores the result under the field’s slug. It also extracts first name, last name, and full name from Attio’s name shape when present, and creates first-item shortcuts for common list fields. The output is a flat dictionary of usable fields.

**Call relations**: `AttioConnector._flatten_record` uses this after it has lifted record identity fields. Together they turn a raw Attio object record into the shape the sync system can store and compare.


##### `AttioConnector._flatten_record`  (lines 195–209)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Flattens standard Attio object records, such as companies, people, and deals. It puts the hidden record identity and nested field values into a normal top-level dictionary.

**Data flow**: It receives a raw Attio record and the stream description. It extracts `record_id`, `object_id`, `workspace_id`, creation time, and update time from the outer record and nested `id`. If the stream has a cursor field, it fills that too. Then it adds the flattened attribute values. The result is a record with a usable primary key and plain fields.

**Call relations**: `AttioConnector.flatten` calls this for streams that are not tasks, notes, meetings, or call recordings. It depends on `AttioConnector._flatten_values` to simplify the nested Attio attributes.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 212–216)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `task_id` to an Attio task record. This gives the sync system a stable key for tasks.

**Data flow**: It receives a raw task record, copies it, looks inside its `id` value, and stores the task identifier as `task_id`. The output is the original task data plus this easier-to-use ID field.

**Call relations**: `AttioConnector.flatten` calls this when the current stream is `tasks`, because task records use a different ID name than standard object records.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 219–223)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `note_id` to an Attio note record. This makes notes identifiable in the same simple way as other synced records.

**Data flow**: It receives a raw note record, copies it, extracts the note identifier from the nested `id` shape when needed, and writes it as `note_id`. The output is the note record with a clear primary key field.

**Call relations**: `AttioConnector.flatten` calls this for the `notes` stream, because notes have their own ID shape.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 226–230)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `meeting_id` to an Attio meeting record. This gives meetings the stable identifier needed for snapshot syncing.

**Data flow**: It receives a raw meeting record, copies it, extracts the meeting identifier from `id`, and stores it as `meeting_id`. The output keeps the original meeting data and adds the easier-to-find ID.

**Call relations**: `AttioConnector.flatten` calls this for the `meetings` stream. Call-recording pagination also reads meeting IDs separately when it fans out from meetings to recordings.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 233–254)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares a call recording record for storage by adding `call_recording_id`, filling in a recording URL when possible, and turning transcript segments into readable text. This makes recordings searchable and useful instead of leaving the transcript buried in pieces.

**Data flow**: It receives a raw call recording record and copies it. It extracts the recording ID from the nested `id`, uses `web_url` as a fallback `recording_url` if needed, and reads transcript segments. For each segment, it combines the speaker name, when available, with the spoken text, then joins all segments into `transcript_text`. The output is an enriched recording record.

**Call relations**: `AttioConnector.flatten` calls this for the `call_recordings` stream. It expects transcript data may already have been attached earlier by `AttioConnector._paginate_call_recordings`.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 256–265)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening routine for the current Attio stream. It is the single public-style flattening entry point used by the connector framework after raw records are fetched.

**Data flow**: It receives one raw Attio record and the stream it came from. It checks the stream name and routes the record to the matching helper for tasks, notes, meetings, call recordings, or standard object records. It returns the flattened record that downstream storage can identify and search.

**Call relations**: This function sits between fetching and storing. After `paginate` yields raw Attio pages, the connector framework can call `flatten`, which then hands each record to `_flatten_task`, `_flatten_note`, `_flatten_meeting`, `_flatten_call_recording`, or `_flatten_record`.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 267–321)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches records from Attio page by page for whichever stream is being synced. It hides the fact that different Attio resources use different endpoint paths and paging styles.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. For tasks and notes, it uses simple offset-based GET paging. For meetings and call recordings, it uses cursor-based paging, and call recordings require a meeting-by-meeting fan-out. For standard objects, it POSTs query bodies with increasing offsets. It yields lists of raw records and raises `StreamSkipped` when a stream should be skipped cleanly because an object is disabled or permissions are missing.

**Call relations**: This is the main fetching routine called by the connector framework during a sync. It delegates to `_paginate_simple`, `_paginate_cursor`, `_paginate_call_recordings`, `_build_query_body`, and error-check helpers so each Attio endpoint family is treated correctly.

*Call graph*: calls 8 internal fn (__init__, _build_query_body, _is_object_disabled, _is_scope_unauthorized, _paginate_call_recordings, _paginate_cursor, _paginate_simple, _scope_skip_reason).


##### `AttioConnector._paginate_simple`  (lines 323–330)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use ordinary offset paging, specifically tasks and notes. Offset paging means asking for records starting after a certain count, like turning pages in a book by page number.

**Data flow**: It receives an HTTP client, an endpoint path, and a page size. It asks the shared REST connector helper to fetch pages from the `data` field using `limit` and offset parameters. It yields each page of records as it arrives.

**Call relations**: `AttioConnector.paginate` calls this for the `tasks` and `notes` streams. It relies on the base REST connector’s paging helper to do the repeated HTTP requests.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._paginate_cursor`  (lines 332–350)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use cursor paging, where the server returns a token for the next page. This is used for meetings and call recordings.

**Data flow**: It receives an HTTP client, endpoint path, page size, and optional query parameters. It asks the shared REST connector helper to read records from `data` and find the next cursor at `pagination.next_cursor`. It yields each page until Attio stops returning a next cursor.

**Call relations**: `AttioConnector.paginate` uses this directly for meetings, and `AttioConnector._paginate_call_recordings` uses it both to list meetings and to list recordings inside each meeting.

*Call graph*: called by 2 (_paginate_call_recordings, paginate).


##### `AttioConnector._paginate_call_recordings`  (lines 352–388)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches call recordings by first walking through every meeting, then asking Attio for recordings attached to each meeting. It enriches each recording with meeting context and transcript data when available.

**Data flow**: It receives an HTTP client. It pages through meetings, extracts each meeting ID, title, start and end times, and computes a duration when possible. For each meeting, it pages through that meeting’s recordings, adds parent meeting fields to each recording, and fetches the transcript for each recording ID. It yields pages of enriched recording records.

**Call relations**: `AttioConnector.paginate` calls this for the `call_recordings` stream. Inside, it uses `_paginate_cursor` for both levels of paging, `_meeting_id` and `_call_recording_id` to find IDs, `_datetime_of` and `_duration_seconds` for timing, and `_fetch_transcript` to attach transcript content.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (paginate).


##### `AttioConnector._fetch_transcript`  (lines 390–402)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: Fetches the transcript for one call recording. If the transcript is not ready or not found, it treats that as normal and returns no transcript instead of failing the whole sync.

**Data flow**: It receives an HTTP client, meeting ID, and recording ID. It builds the transcript endpoint path and sends a GET request. If Attio replies with 404 or 409, it returns `None`; otherwise unexpected HTTP errors are raised. On success, it returns the `data` dictionary from Attio if it is shaped as expected.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this while enriching each recording. The transcript it returns is later converted into readable `transcript_text` by `_flatten_call_recording`.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 405–409)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a meeting ID from Attio’s meeting record shape. It accepts both nested ID objects and plain string IDs.

**Data flow**: It receives a meeting dictionary. If `id` is a dictionary, it returns `id.meeting_id`; if `id` is already a string, it returns that string. If neither form is present, it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` uses this before asking Attio for recordings under a meeting. Without a meeting ID, that meeting is skipped for recording fan-out.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 412–416)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a call recording ID from Attio’s recording record shape. It handles both nested and plain ID forms.

**Data flow**: It receives a recording dictionary. If `id` is a dictionary, it returns `id.call_recording_id`; if `id` is a string, it returns that. Otherwise it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` uses this before fetching a transcript. If no recording ID is available, the recording can still be yielded, but no transcript request is made.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 419–423)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: Pulls a usable date or date-time string out of Attio’s meeting time shape. Attio may represent a timed event with `datetime` or an all-day event with `date`.

**Data flow**: It receives any value. If the value is a dictionary, it returns the `datetime` field if present, otherwise the `date` field. If the input is not the expected shape, it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` uses this to copy meeting start and end information onto related recordings.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 426–436)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: Calculates a rough meeting duration in seconds from start and end timestamps. It is careful to return no duration if the dates are missing or cannot be parsed.

**Data flow**: It receives optional start and end strings. If either is missing, it returns `None`. Otherwise it parses both as ISO 8601 date-time strings, including timestamps ending in `Z`, and subtracts start from end. The output is a non-negative number of seconds, or `None` if parsing fails.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this after extracting meeting times. The resulting duration is attached to call recordings when it can be computed.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 439–448)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes Attio’s specific error for a standard object that is disabled in the workspace. This lets the connector skip that stream instead of treating it as a broken sync.

**Data flow**: It receives an HTTP error. It first checks for status code 400, then tries to read the response as JSON. If the response body has code `standard_object_disabled`, it returns `true`; otherwise it returns `false`.

**Call relations**: `AttioConnector.paginate` calls this when a standard object query fails. If it returns true, `paginate` raises `StreamSkipped` with a clear reason.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._is_scope_unauthorized`  (lines 451–460)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes Attio’s error for a missing OAuth scope, meaning the user granted the connector too few permissions. This avoids failing the entire connector when only one stream is not allowed.

**Data flow**: It receives an HTTP error. It checks for status code 403, tries to parse the JSON response, and looks for code `unauthorized`. It returns `true` only for that specific permission problem.

**Call relations**: `AttioConnector.paginate` calls this around meetings and call recordings requests. When it returns true, `paginate` uses `_scope_skip_reason` and raises `StreamSkipped`.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._scope_skip_reason`  (lines 463–469)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a human-readable explanation for skipping a stream because the OAuth grant is missing a required permission. OAuth is the permission system that lets a user authorize this connector without sharing their password.

**Data flow**: It receives an HTTP error and tries to read the JSON response. If Attio included a message, that message is included in the returned reason; otherwise it falls back to a generic note to check the upstream response. The output is a short string suitable for `StreamSkipped`.

**Call relations**: `AttioConnector.paginate` calls this after `_is_scope_unauthorized` confirms the error is a missing-scope problem. The reason is passed into `StreamSkipped` so the sync can report a useful skip message.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/hubspot.py`

`io_transport` · `during source sync, while reading and paginating HubSpot streams`

HubSpot is a large product with many separate APIs. Some records come from the CRM search API, others from marketing, analytics, conversations, files, lists, or automation endpoints. This connector is the adapter that makes all of those look like one familiar set of streams. It is like a travel plug: HubSpot has many socket shapes, and this file converts them into one shape the sync engine understands.

The file first defines the streams that can be synced and the basic rules for each one: its name, its HubSpot object type, its main id field, and which timestamp can be used as a cursor for incremental syncs. For normal CRM objects, it asks HubSpot which properties exist, searches records in timestamp order, skips duplicate records at the cursor boundary, and then does a separate sweep for archived records so deletions become tombstones. For product APIs that do not use CRM search, it has custom walkers for each awkward area, such as campaign assets, list memberships, consent states, sequences, analytics reports, and associations.

A key job here is normalization. HubSpot often nests useful fields inside `properties` or `values`; this file lifts those into top-level fields so downstream code does not need to know HubSpot’s quirks. If HubSpot says a stream is unavailable because the account lacks a product tier or permission, the connector records the stream as skipped instead of failing the whole sync.

#### Function details

##### `_normalize_epoch_millis`  (lines 248–255)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Converts HubSpot timestamps stored as milliseconds since 1970 into readable ISO date strings. It leaves booleans and non-timestamp-looking values alone so ordinary fields are not accidentally changed.

**Data flow**: It receives any value. If the value is a number, or a string made only of digits, it treats it as milliseconds since the Unix epoch and returns an ISO timestamp in UTC; otherwise it returns the original value unchanged.

**Call relations**: This helper is used when flattening product API rows and when building analytics view rows, because those HubSpot endpoints may return dates as raw millisecond numbers.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 258–267)

```
def _stream(name: str, *, object_type: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a standard stream description for HubSpot CRM objects that can be searched by last modified time. It saves repeated boilerplate for objects like contacts, companies, deals, and tasks.

**Data flow**: It takes a stream name, HubSpot object type, and whether the stream is canonical. It returns a `StreamSpec`, which is the sync engine’s small instruction card for how to identify and update records in that stream.

**Call relations**: This is used at file load time to define many CRM-style streams. Those stream definitions are later read by `HubSpotConnector` when a sync asks for data.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 270–289)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='createdAt', updated_at_field: str | None='updatedAt', pagi
```

**Purpose**: Creates a stream description for HubSpot product APIs that do not behave like normal CRM search. These streams often need their own paging rules and may use different created or updated fields.

**Data flow**: It receives stream metadata such as name, source object, id field, cursor field, timestamp fields, and optional pagination instructions. It returns a `StreamSpec` marked as non-canonical because these are supporting product surfaces rather than core CRM objects.

**Call relations**: This helper is used while defining streams such as owners, workflows, forms, files, analytics, consent states, and sequences. The connector later routes these streams through product-specific pagination code.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 292–304)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Builds a reusable paging recipe for HubSpot endpoints that return `results` plus a `paging.next.after` cursor. This keeps the connector from rewriting the same paging setup for many simple list endpoints.

**Data flow**: It takes an API path. It returns a `Pagination` object that says where records live in the response, where the next cursor is, and which query parameters control cursor and page size.

**Call relations**: Product API stream definitions use this helper when a HubSpot endpoint follows the common GET collection shape. Later, `_paginate_unchecked` can hand those streams to the generic strategy paginator.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 307–317)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Creates a stream description for synthetic relationship tables, such as deal-to-contact or ticket-to-company links. These streams are not HubSpot objects themselves; they are rows made from associations between objects.

**Data flow**: It takes a synthetic stream name and a parent object type. It returns a `StreamSpec` with no cursor, because HubSpot does not expose modification times for these relationship rows.

**Call relations**: The junction stream definitions are used by `_paginate_unchecked`, which recognizes them and sends them to `_paginate_junction` for full refresh-style association walking.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 622–652)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the JSON body used to search CRM object records in HubSpot. It asks for all known properties, sorts records by the cursor field, and optionally filters to records changed since the last sync.

**Data flow**: It receives a stream definition, a list of property names, an optional saved cursor, and an optional page cursor. It returns a request body that HubSpot’s search API can understand.

**Call relations**: Both `_paginate_unchecked` and `_paginate_custom_object_records` call this before sending search requests, so standard CRM objects and custom objects use the same incremental-search shape.

*Call graph*: called by 2 (_paginate_custom_object_records, _paginate_unchecked).


##### `HubSpotConnector._flatten`  (lines 655–666)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a normal CRM API record into a flat dictionary. This matters because HubSpot puts most business fields inside a nested `properties` object, while the rest of the system expects fields at the top level.

**Data flow**: It receives one HubSpot CRM record. It copies the id, created time, updated time, archived flag, and all fields from `properties` into one flat output dictionary.

**Call relations**: `flatten` calls this for ordinary CRM streams after pagination has produced raw HubSpot records.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 669–692)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes rows from HubSpot product APIs, which often use different shapes than CRM records. It lifts ids, property bags, form-submission values, and certain timestamp formats into a common flat form.

**Data flow**: It receives a product API record and the stream it belongs to. It copies the record, fills in `id` from `objectId` when needed, lifts nested `properties` and `values`, normalizes selected dates, and returns the cleaned row.

**Call relations**: `flatten` calls this for product API streams. It uses `_normalize_epoch_millis` for streams whose timestamps arrive as raw milliseconds.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 694–701)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening rule for each stream. It keeps downstream storage from having to know whether a record came from CRM search, a product API, a custom object, or a synthetic relationship stream.

**Data flow**: It receives a raw record and its stream definition. It returns the record unchanged for streams that are already shaped correctly, uses product API flattening for product streams, and uses CRM flattening for ordinary CRM records.

**Call relations**: The sync framework calls this after pages are read. It delegates to `_flatten` or `_flatten_product_api` depending on the stream.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector._list_properties`  (lines 703–711)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which fields exist for a CRM object type. This is important because HubSpot search only returns properties explicitly requested.

**Data flow**: It receives an HTTP client and a HubSpot object type. It calls the properties endpoint, reads the returned property names, and returns them as a list of strings.

**Call relations**: `_paginate_unchecked` calls this before searching normal CRM streams so the connector can request every available field instead of a hand-picked subset.

*Call graph*: called by 1 (_paginate_unchecked).


##### `HubSpotConnector.paginate`  (lines 713–726)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the safe public paging entry for the connector. It reads pages from HubSpot and turns permission-related failures into skipped streams instead of sync failures.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. It yields pages from `_paginate_unchecked`; if HubSpot returns an authentication or stream-permission error, it raises `StreamSkipped` with a human-readable reason.

**Call relations**: The sync engine calls this when it wants records for one stream. It wraps `_paginate_unchecked` and uses `_is_stream_unavailable` and `_stream_skip_reason` to decide whether an error should be treated as a skip.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 728–778)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Routes a stream to the correct paging method and performs the normal CRM search loop when no special route is needed. It is the connector’s central traffic director.

**Data flow**: It receives a stream and cursor. It either uses a declared pagination strategy, calls a special paginator for product APIs, custom objects, or junctions, or searches the CRM API page by page and finally yields archived-id tombstones.

**Call relations**: `paginate` calls this after adding error handling. This method calls helpers such as `_list_properties`, `_build_search_body`, `_paginate_product_api`, `_paginate_custom_objects`, `_paginate_junction`, and `_paginate_archived_ids` depending on the stream.

*Call graph*: calls 6 internal fn (_build_search_body, _list_properties, _paginate_archived_ids, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._is_stream_unavailable`  (lines 781–804)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether a HubSpot error means the account is not allowed to read a particular stream. This separates expected permission limits from real outages or bugs.

**Data flow**: It receives an HTTP status error. It checks for a 403 response and scans the response message for permission or scope wording, returning true only when the stream appears unavailable to this account.

**Call relations**: `paginate`, `_paginate_archived_ids`, and `_paginate_custom_object_archived_ids` use this to avoid failing an entire sync when HubSpot blocks one object type.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 807–816)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds the message recorded when a HubSpot stream is skipped. It includes the stream name and HubSpot’s own explanation when available.

**Data flow**: It receives a stream name and an HTTP error. It tries to read the response body’s message and returns a plain text reason string.

**Call relations**: `paginate` calls this just before raising `StreamSkipped`, so the sync report can explain why data was not read.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 818–853)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds deleted or archived CRM records after the main search has finished. HubSpot search omits archived records, so this sweep lets the destination remove records that disappeared upstream.

**Data flow**: It receives a client and stream. It pages through the object list endpoint with `archived=true`, collects record ids, and yields `StreamPage` objects that contain delete tombstones instead of normal rows.

**Call relations**: `_paginate_unchecked` calls this after normal CRM pagination. It uses `_is_archived_sweep_unsupported` and `_is_stream_unavailable` to silently stop when HubSpot cannot support the archived sweep.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 855–949)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Sends each non-CRM HubSpot product stream to the special reader it needs. Product APIs differ widely, so this method acts as a dispatch table.

**Data flow**: It receives a client, stream, and optional cursor. Based on the stream name, it yields pages from the matching specialized paginator, or falls back to a simple GET collection path when the endpoint is straightforward.

**Call relations**: `_paginate_unchecked` calls this for streams listed as product APIs. It hands off to many stream-specific methods such as analytics, associations, lists, sequences, email events, pipelines, form submissions, and generic GET collection paging.

*Call graph*: calls 21 internal fn (_paginate_analytics_reports, _paginate_analytics_views, _paginate_association_labels, _paginate_associations, _paginate_campaign_assets, _paginate_consent_states, _paginate_conversation_messages, _paginate_email_events, _paginate_event_occurrences, _paginate_event_types (+11 more)); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._paginate_get_collection`  (lines 951–979)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a standard HubSpot list endpoint that returns records under `results` and uses an `after` cursor for the next page. It is the common worker for simple product API endpoints.

**Data flow**: It receives a client, path, optional page size, and optional extra query parameters. It repeatedly GETs pages, normalizes `objectId` into `id` when needed, yields non-empty result lists, and stops when HubSpot gives no next cursor.

**Call relations**: Many specialized paginators call this when their inner endpoint has the normal collection shape, including owner teams, campaign assets, form submissions, conversations, sequences, and the generic product API fallback.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_product_api, _paginate_sequences, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 981–1016)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Reads all custom object types defined in the HubSpot account. Custom objects are account-specific, so the connector first discovers their schemas and then syncs records for each schema.

**Data flow**: It receives a client and cursor. It loads custom object schemas, derives object type ids and property names, searches records for each custom object type, and then yields tombstones for archived custom records.

**Call relations**: `_paginate_unchecked` calls this for the `custom_objects` stream. It coordinates `_custom_object_schemas`, `_schema_object_type_id`, `_schema_property_names`, `_paginate_custom_object_records`, and `_paginate_custom_object_archived_ids`.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 1018–1020)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the custom object definitions available in the HubSpot account. These definitions tell the connector what custom object types exist and what fields they have.

**Data flow**: It receives an HTTP client. It calls the CRM schema endpoint and returns only result entries that are dictionaries.

**Call relations**: `_paginate_custom_objects` uses this to discover custom streams, and `_association_object_types` uses it so custom objects can be included in association discovery.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 1023–1028)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Finds the best usable object type identifier from a custom object schema. HubSpot may expose the identifier under a few different field names.

**Data flow**: It receives a schema dictionary. It checks `objectTypeId`, `fullyQualifiedName`, and `name` in order, returning the first non-empty string or `None` if none is available.

**Call relations**: Custom object pagination and association object discovery call this whenever they need the API id for a schema. `_custom_object_row` also uses it when building stable row ids.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1031–1046)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Extracts all useful property names from a custom object schema. This lets search requests ask HubSpot for every field, including display fields.

**Data flow**: It receives a schema. It collects unique property names from the schema’s property list, primary display property, and secondary display properties, then returns them as a list.

**Call relations**: `_paginate_custom_objects` calls this before searching records for each custom object type.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1048–1083)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Searches records for one custom object type. It mirrors normal CRM incremental search but also reshapes each record with custom-object metadata.

**Data flow**: It receives a client, temporary stream definition, schema, property list, and cursor. It posts search requests page by page, skips duplicate records at the inclusive cursor boundary, converts each record with `_custom_object_row`, and yields rows.

**Call relations**: `_paginate_custom_objects` calls this for each discovered schema. It uses `_build_search_body` to make the search request and `_custom_object_row` to produce destination-ready records.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1085–1124)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Turns one custom object record into a stable, searchable row with helpful labels. It adds object type information so records from different custom objects do not collide.

**Data flow**: It receives a raw record and its schema. It reads the schema id, record id, properties, labels, and display fields, then returns a flat row with an id like `objectType:recordId`; if required ids are missing, it returns `None`.

**Call relations**: `_paginate_custom_object_records` calls this for each custom object result. It calls `_schema_object_type_id` to identify the custom object type.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1126–1156)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived records for one custom object type and yields delete tombstones. This keeps custom object data in sync when records are removed in HubSpot.

**Data flow**: It receives a client and custom object type id. It pages through that object’s archived list endpoint, builds delete ids in the same `objectType:recordId` form used for live rows, and yields `StreamPage` delete pages.

**Call relations**: `_paginate_custom_objects` calls this after reading live custom object records. It uses the same unavailable-stream and unsupported-archive checks as the normal archived sweep.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1158–1177)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a stream of owner teams from the owner records. HubSpot exposes teams nested under owners, so this method extracts and deduplicates them.

**Data flow**: It receives a client. It pages owners, reads each owner’s `teams`, stores one row per unique team id, and yields the collected team rows.

**Call relations**: `_paginate_product_api` calls this for the `owner_teams` stream. It relies on `_paginate_get_collection` to read owners.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_lists`  (lines 1179–1208)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot CRM lists through the list search API. It also flattens additional list properties so list metadata is easy to store.

**Data flow**: It receives a client. It posts list search requests using an offset, turns `listId` into `id`, merges `additionalProperties` into each row, yields pages, and advances until HubSpot reports no more results.

**Call relations**: `_paginate_product_api` calls this for the `lists` stream, and `_paginate_list_memberships` calls it before walking members of each list.

*Call graph*: called by 2 (_paginate_list_memberships, _paginate_product_api).


##### `HubSpotConnector._paginate_site_search`  (lines 1210–1230)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads CMS search results for a given content type, such as knowledge articles. It uses offset-based paging rather than HubSpot’s usual `after` cursor.

**Data flow**: It receives a client and content type. It repeatedly calls the site-search endpoint with limit and offset, yields dictionary rows, and stops when the offset reaches the reported total.

**Call relations**: `_paginate_product_api` calls this for knowledge articles.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1232–1256)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds marketing assets attached to each campaign. HubSpot stores these behind per-campaign, per-asset-type endpoints, so the connector fans out across both.

**Data flow**: It receives a client. It first pages campaigns, then for each campaign and each known asset type, it asks `_paginate_campaign_asset_type` for attached assets and yields those pages.

**Call relations**: `_paginate_product_api` calls this for the `campaign_assets` stream. It uses `_paginate_get_collection` to read campaigns and delegates each asset type to `_paginate_campaign_asset_type`.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1258–1293)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one type of asset for one campaign and adds context that ties every asset back to its campaign. It tolerates missing or forbidden asset types because not every account has every marketing feature.

**Data flow**: It receives a client, campaign id, campaign name, and asset type. It pages that asset endpoint, builds stable ids including campaign and asset type, adds asset kind and campaign fields, yields pages, and returns quietly on 403 or 404.

**Call relations**: `_paginate_campaign_assets` calls this inside its campaign-and-type fan-out. It uses `_paginate_get_collection` for the actual page loop.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1295–1301)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields saved analytics views as a stream. Analytics views are filters that can be used to scope report results.

**Data flow**: It receives a client, asks `_analytics_view_rows` for normalized rows, and yields them if any exist.

**Call relations**: `_paginate_product_api` calls this for the `analytics_views` stream. The detailed row-building work is done by `_analytics_view_rows`.

*Call graph*: calls 1 internal fn (_analytics_view_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_view_rows`  (lines 1303–1334)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches and normalizes HubSpot analytics views. It makes sure each view has an id, readable name, filter information, and consistent timestamp fields.

**Data flow**: It receives a client. It calls the analytics views endpoint, accepts either a list or a `results` response, filters valid dictionaries, normalizes ids and date fields, and returns a list of rows.

**Call relations**: `_paginate_analytics_views` uses this to emit the views stream, and `_paginate_analytics_reports` uses it to query reports for each available view.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1336–1366)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Generates analytics report rows across many report subjects, time periods, and optional analytics views. It turns HubSpot’s report matrix into a stream of individual rows.

**Data flow**: It receives a client. It chooses a date window, loads analytics views, builds a list of filters including an all-traffic view, then queries every supported report family, subject, period, and view combination.

**Call relations**: `_paginate_product_api` calls this for the `analytics_reports` stream. It uses `_analytics_report_window`, `_analytics_view_rows`, and `_paginate_analytics_report_query`.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_report_window`  (lines 1369–1370)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range for analytics report queries. It starts from a fixed old date and ends at today in UTC.

**Data flow**: It takes no input. It returns a pair of strings in HubSpot’s `YYYYMMDD` format: the fixed start date and the current date.

**Call relations**: `_paginate_analytics_reports` calls this before sending report queries.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1372–1423)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Runs one analytics report query and pages through its breakdown rows. It also ignores report combinations HubSpot says are invalid or missing.

**Data flow**: It receives a client plus report family, subject, time period, optional view, and date range. It GETs report pages, converts each response into rows with `_analytics_report_rows`, yields them, and advances by offset until done.

**Call relations**: `_paginate_analytics_reports` calls this for each report combination it wants to try. It delegates response shaping to `_analytics_report_rows`.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1426–1501)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Turns one HubSpot analytics report response into destination rows. It creates a row for totals and separate rows for breakdown values.

**Data flow**: It receives report data and context such as subject, time period, view, dates, and offset. It builds stable ids, labels, metric dictionaries, filters, and formatted dates, then returns a list of report rows.

**Call relations**: `_paginate_analytics_report_query` calls this after each HubSpot report response. It is the point where raw report JSON becomes stream records.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1504–1508)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Builds a stable id for an analytics report row. Stable ids are needed so repeated syncs update the same report row rather than creating duplicates.

**Data flow**: It receives several id parts. It converts them to strings, replaces characters that would confuse the colon-separated id, and returns one `analytics_report:` id string.

**Call relations**: This helper supports analytics report row creation, where totals and breakdown rows need predictable ids.


##### `HubSpotConnector._analytics_report_date`  (lines 1511–1512)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Converts HubSpot analytics dates from `YYYYMMDD` into the clearer `YYYY-MM-DD` form. This makes report rows easier to read and compare.

**Data flow**: It receives an eight-character date string. It slices it into year, month, and day and returns a dashed date string.

**Call relations**: This helper is used while analytics report rows are assembled for output.


##### `HubSpotConnector._paginate_event_types`  (lines 1514–1533)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the event type definitions available in HubSpot. These describe categories of events that may appear in event occurrence data.

**Data flow**: It receives a client. It calls the event-types endpoint, accepts either a list response or a `results` response, assigns a string id from available fields or the row index, and yields the rows.

**Call relations**: `_paginate_product_api` calls this for the `event_types` stream.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1535–1557)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads individual event occurrences, optionally only after a saved cursor time. It creates ids for events when HubSpot does not provide one.

**Data flow**: It receives a client and cursor. It passes the cursor as `occurredAfter` when present, reads event results, fills missing ids with `_synthetic_event_id`, and yields one page of rows.

**Call relations**: `_paginate_product_api` calls this for the `event_occurrences` stream. It delegates fallback id creation to `_synthetic_event_id`.

*Call graph*: calls 1 internal fn (_synthetic_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._synthetic_event_id`  (lines 1560–1570)

```
def _synthetic_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Creates a fallback id for an event occurrence that lacks a HubSpot id. It combines identifying event fields with a hash of the full payload to reduce collisions.

**Data flow**: It receives an event row and its index in the page. It joins event type, object type, object id, occurrence time or index, and a stable payload hash into one id string.

**Call relations**: `_paginate_event_occurrences` calls this only when HubSpot does not supply an event id.

*Call graph*: called by 1 (_paginate_event_occurrences).


##### `HubSpotConnector._paginate_email_events`  (lines 1572–1600)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads email activity events from HubSpot’s email events API. It supports cursor-based incremental reads by translating saved cursor values into HubSpot’s millisecond timestamp parameter.

**Data flow**: It receives a client and cursor. It builds query parameters, pages by offset while `hasMore` is true, fills missing ids with `_synthetic_email_event_id`, and yields event rows.

**Call relations**: `_paginate_product_api` calls this for the `email_events` stream. It uses `_email_event_start_timestamp` for cursor conversion and `_synthetic_email_event_id` for fallback ids.

*Call graph*: calls 2 internal fn (_email_event_start_timestamp, _synthetic_email_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1603–1612)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts an email event cursor into the millisecond timestamp HubSpot expects. It accepts either an already-numeric cursor or an ISO date string.

**Data flow**: It receives an optional cursor string. If absent it returns `None`; if all digits it returns the integer; if it is an ISO date it parses it and returns milliseconds since 1970; otherwise it returns `None`.

**Call relations**: `_paginate_email_events` calls this while building the request parameters for incremental email event reads.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._synthetic_email_event_id`  (lines 1615–1625)

```
def _synthetic_email_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Creates a fallback id for an email event when HubSpot does not provide one. It uses the event’s time, recipient, type, campaign, and payload hash.

**Data flow**: It receives an email event row and its index. It joins the best available identifying fields into a colon-separated string, replacing colons inside parts to keep the id safe.

**Call relations**: `_paginate_email_events` calls this for email events missing an id.

*Call graph*: called by 1 (_paginate_email_events).


##### `HubSpotConnector._stable_payload_hash`  (lines 1628–1630)

```
def _stable_payload_hash(row: dict[str, Any]) -> str
```

**Purpose**: Creates a short, repeatable fingerprint for a JSON-like row. This helps synthetic ids remain stable even when no official id exists.

**Data flow**: It receives a dictionary, serializes it with sorted keys, hashes the encoded text with SHA-256, and returns the first 16 hex characters.

**Call relations**: Synthetic id helpers use this kind of fingerprint so fallback ids are less likely to collide for similar events.

*Call graph*: 2 external calls (sha256, dumps).


##### `HubSpotConnector._paginate_association_labels`  (lines 1632–1648)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the labels HubSpot defines for relationships between object types. A label explains what a connection means, such as a contact being associated with a company in a particular way.

**Data flow**: It receives a client. It walks object-type pairs that have labels, converts each label into a flat row with `_association_label_row`, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the association-label stream. It depends on `_association_pairs_with_labels` to discover valid pairs.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_associations`  (lines 1650–1665)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual relationships between HubSpot objects across supported object-type pairs. It batches parent ids to use HubSpot’s association batch API.

**Data flow**: It receives a client. For each object-type pair with labels, it pages source object ids, sends them to `_paginate_association_batch`, and yields relationship rows.

**Call relations**: `_paginate_product_api` calls this for the associations stream. It uses `_association_pairs_with_labels`, `_paginate_crm_object_id_pages`, and `_paginate_association_batch`.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_association_batch`  (lines 1667–1694)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads associations for a batch of source object ids. It also follows per-record association paging when one source object has more links than fit in a single response.

**Data flow**: It receives object types and a list of input ids. It posts to HubSpot’s batch read endpoint, converts results with `_association_rows`, yields rows, then builds the next pending inputs from response paging until no more remain.

**Call relations**: `_paginate_associations` calls this for each page of source ids. It uses `_is_optional_pair_unavailable` to ignore unsupported pairs, `_association_rows` to shape results, and `_next_association_inputs` to continue long association lists.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1697–1711)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Finds which association batch inputs need another request. HubSpot can page associations separately for each source record, and this helper prepares those follow-up inputs.

**Data flow**: It receives a batch association response. It inspects each result for a source id and next `after` cursor, then returns a new list of input dictionaries containing both.

**Call relations**: `_paginate_association_batch` calls this after each batch response to know whether more association pages are needed.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1713–1726)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Discovers object-type pairs that have association labels. The presence of labels is used as a sign that a relationship pair is meaningful and readable.

**Data flow**: It receives a client. It gets all object types, checks every from/to combination for labels, and yields only pairs with non-empty label lists.

**Call relations**: `_paginate_association_labels` and `_paginate_associations` both call this to decide which relationship pairs to process. It uses `_association_object_types` and `_association_labels_for_pair`.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1728–1741)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the list of HubSpot object types to consider for association discovery. It includes standard object types and any custom object types available in the account.

**Data flow**: It receives a client. It starts with a built-in list, tries to fetch custom schemas, adds each custom object type id if not already present, and returns the combined list.

**Call relations**: `_association_pairs_with_labels` calls this before trying object-type combinations. It uses `_custom_object_schemas`, `_schema_object_type_id`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1743–1759)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches relationship labels for one from-object and to-object pair. If HubSpot says the pair is unsupported, it returns an empty list instead of failing.

**Data flow**: It receives a client and two object type names. It calls the labels endpoint and returns dictionary rows from `results`; for optional unavailable pairs, it returns an empty list.

**Call relations**: `_association_pairs_with_labels` calls this for each possible object-type pair. It uses `_is_optional_pair_unavailable` to tolerate unsupported combinations.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1762–1779)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Turns one association label into a flat stream row with a stable id and clear from/to object fields. This makes relationship metadata searchable like ordinary records.

**Data flow**: It receives a label plus source and target object types. It extracts type id, category, and display label, then returns a row with those values and a composed id.

**Call relations**: `_paginate_association_labels` calls this for every label found by `_association_pairs_with_labels`.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1781–1793)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Reads pages of CRM object ids only. This is a lightweight way to gather ids for later fan-out calls, such as association or sequence enrollment lookups.

**Data flow**: It receives a client and object type. It pages CRM objects requesting only `hs_object_id`, extracts record ids as strings, and yields id lists.

**Call relations**: `_paginate_associations` uses this to batch association reads, and `_paginate_sequence_enrollments` uses it to visit contacts. It relies on `_paginate_crm_object_pages`.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1795–1825)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads basic CRM object list pages without using the search API. It is used when the connector just needs ids or a small set of properties.

**Data flow**: It receives a client, object type, and property names. It GETs list pages with those properties, yields dictionary records, and follows the `after` cursor until finished.

**Call relations**: `_paginate_crm_object_id_pages` and `_paginate_contact_identity_pages` call this. It uses `_is_optional_pair_unavailable` to return quietly when an optional object type cannot be listed.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1828–1862)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Converts a HubSpot association batch response into one flat row per relationship type between two records. It handles cases where one target record has multiple association types.

**Data flow**: It receives batch response data and the from/to object types. It loops through source records, target records, and association types, and builds rows using `_association_row`.

**Call relations**: `_paginate_association_batch` calls this after each batch read response.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1865–1892)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one normalized association row for a single source record, target record, and relationship type. The row includes both ids and human-readable relationship information when available.

**Data flow**: It receives association type data, object types, source id, target id, and a fallback index. It creates a stable id and returns fields such as association category, label, type id, and relationship type.

**Call relations**: This helper is used by association row construction to produce the final relationship record shape.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1895–1898)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether an error from an optional fan-out endpoint should be ignored. Many HubSpot object pairs or feature endpoints simply do not exist for a given account.

**Data flow**: It receives an HTTP error. It returns true for 400 or 404 responses, or when `_is_stream_unavailable` recognizes a permission-style 403.

**Call relations**: Association, list membership, consent, sequence, CRM object, and other fan-out readers call this so missing optional surfaces do not break the whole stream.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1900–1914)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads which records belong to each HubSpot list. Lists are first discovered, then each list’s memberships are walked separately.

**Data flow**: It receives a client. It pages lists, extracts each list id, calls `_paginate_memberships_for_list`, and yields membership pages.

**Call relations**: `_paginate_product_api` calls this for the `list_memberships` stream. It depends on `_paginate_lists` and `_paginate_memberships_for_list`.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1916–1959)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads member records for one HubSpot list and attaches list context to each membership row. This turns a nested list-members endpoint into standalone records.

**Data flow**: It receives a client, the list record, and list id. It pages memberships, skips rows without record ids, builds ids from list id and record id, adds list name and object type details, and yields pages.

**Call relations**: `_paginate_list_memberships` calls this for each list. It uses `_is_optional_pair_unavailable` when a list’s membership endpoint cannot be read.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1961–1974)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the subscription preference definitions configured in HubSpot. These define the types of communication a contact can subscribe or unsubscribe from.

**Data flow**: It receives a client. It calls the definitions endpoint, accepts either `results` or `subscriptionDefinitions`, assigns each row an id from available fields or index, and yields the rows.

**Call relations**: `_paginate_product_api` calls this for the `subscription_definitions` stream.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_consent_states`  (lines 1976–1989)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads email consent status for contacts. It visits contacts with email addresses and asks HubSpot for both subscription-specific statuses and unsubscribe-all status.

**Data flow**: It receives a client. It pages contact identities, skips contacts without email, collects rows from `_consent_status_rows` and `_unsubscribe_all_rows`, and yields non-empty pages.

**Call relations**: `_paginate_product_api` calls this for the `consent_states` stream. It coordinates `_paginate_contact_identity_pages`, `_consent_status_rows`, and `_unsubscribe_all_rows`.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1991–2007)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads contact records with enough identity information to check communication preferences. The main field it needs is email.

**Data flow**: It receives a client. It pages contact CRM records requesting the email property, lifts the email from either the top level or nested properties, and yields contact rows.

**Call relations**: `_paginate_consent_states` calls this before checking each contact’s consent state. It uses `_paginate_crm_object_pages`.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 2009–2030)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches subscription-level email consent statuses for one contact email. It returns rows describing whether that email is subscribed or unsubscribed to specific communication types.

**Data flow**: It receives a client, contact row, and email address. It URL-encodes the email, calls the statuses endpoint, converts each result with `_consent_row`, and returns the list.

**Call relations**: `_paginate_consent_states` calls this for each contact email. It uses `_is_optional_pair_unavailable` for missing preference endpoints and `_consent_row` for row shaping.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 2032–2056)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches unsubscribe-all status for one contact email. This is separate from individual subscription types and means the contact opted out broadly.

**Data flow**: It receives a client, contact row, and email address. It URL-encodes the email, calls the unsubscribe-all endpoint, converts each result with `_consent_row`, and returns the rows.

**Call relations**: `_paginate_consent_states` calls this alongside `_consent_status_rows`. It uses `_is_optional_pair_unavailable` and `_consent_row`.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 2059–2089)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Builds one normalized consent-state row. It combines HubSpot’s preference data with contact id and email so each consent record stands on its own.

**Data flow**: It receives a raw preference row, contact, email, and status kind. It creates a stable id, copies status details, adds contact and subject email fields, chooses purpose and subscription type, and returns the row.

**Call relations**: `_consent_status_rows` and `_unsubscribe_all_rows` call this after their HubSpot API requests.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2091–2118)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sales sequences for each HubSpot user that can own sequences. HubSpot requires a user id, so the connector first derives users from owners.

**Data flow**: It receives a client. It gets user rows, queries the sequences endpoint for each user id, adds owner context to each sequence row, and yields pages while skipping optional unavailable users or endpoints.

**Call relations**: `_paginate_product_api` calls this for the `sequences` stream. It uses `_sequence_user_rows`, `_paginate_get_collection`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_user_rows`  (lines 2120–2143)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a unique list of HubSpot user ids from owner records. These user ids are needed to query sequence APIs.

**Data flow**: It receives a client. It pages owners, collects unique `userId` values, attaches owner id and email, and yields one page of user rows.

**Call relations**: `_paginate_sequences` calls this before querying sequences. It reads owners through `_paginate_get_collection`.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2145–2163)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sequence enrollment information for contacts. It visits contacts one page at a time and asks HubSpot whether each contact is enrolled in sequences.

**Data flow**: It receives a client. It pages contact ids, calls the contact enrollment endpoint for each id, converts responses with `_sequence_enrollment_rows`, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the `sequence_enrollments` stream. It uses `_paginate_crm_object_id_pages`, `_sequence_enrollment_rows`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2166–2180)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes a sequence enrollment response for one contact. HubSpot may return either a list under `results` or a single object, so this helper accepts both.

**Data flow**: It receives response data and the contact id. It chooses the raw rows, assigns each a stable id from HubSpot’s id or contact plus sequence id, adds `contact_id`, and returns the rows.

**Call relations**: `_paginate_sequence_enrollments` calls this after each contact enrollment request.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2182–2211)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads submissions for every HubSpot form. The forms endpoint lists the forms, then each form has its own submissions endpoint.

**Data flow**: It receives a client. It pages forms, skips forms without ids, pages submissions for each form, builds stable submission ids, adds form id and form name, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the `form_submissions` stream. It uses `_paginate_get_collection` for both forms and submissions.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2213–2228)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages inside HubSpot conversation threads. Threads are listed first, then messages are fetched per thread.

**Data flow**: It receives a client. It pages conversation threads, skips threads without ids, pages messages for each thread, adds `thread_id` to every message, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the `conversation_messages` stream. It uses `_paginate_get_collection` for both thread and message endpoints.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipelines`  (lines 2230–2237)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads deal and ticket pipelines. Pipelines define the high-level process stages for those object types.

**Data flow**: It receives a client. It asks `_pipeline_rows_for_object_type` for each supported pipeline object type and yields any returned rows.

**Call relations**: `_paginate_product_api` calls this for the `pipelines` stream. It delegates object-specific work to `_pipeline_rows_for_object_type`.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2239–2289)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the stages inside deal and ticket pipelines. It adds fields that make stage status, order, probability, and closure easier to understand.

**Data flow**: It receives a client. For each pipeline object type, it loads raw pipelines, loops through their stages, builds stable stage ids, derives status and closure values, and yields stage rows.

**Call relations**: `_paginate_product_api` calls this for the `pipeline_stages` stream. It uses `_raw_pipelines_for_object_type` to get the source pipeline data.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2291–2314)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes pipeline records for one object type, such as deals or tickets. It creates ids that include the object kind so different pipeline namespaces do not collide.

**Data flow**: It receives a client and object type. It loads raw pipelines, skips rows without ids, adds a composed id, pipeline id, object kind, display name, and active-or-archived status, then returns the rows.

**Call relations**: `_paginate_pipelines` calls this for each supported object type. It gets raw data through `_raw_pipelines_for_object_type`.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2316–2327)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches raw pipeline definitions for one HubSpot object type. It treats forbidden or missing pipeline endpoints as simply empty.

**Data flow**: It receives a client and object type. It calls the pipelines endpoint, returns dictionary rows from `results`, returns an empty list on 403 or 404, and re-raises other errors.

**Call relations**: `_paginate_pipeline_stages` and `_pipeline_rows_for_object_type` call this before normalizing pipelines or stages.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2330–2334)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects a specific HubSpot error meaning archived-record paging is not supported for that object. This lets the sync continue without delete detection for that unsupported case.

**Data flow**: It receives an HTTP error. It checks for status 400 and looks for HubSpot’s message about paging through deleted objects not being supported.

**Call relations**: `_paginate_archived_ids` and `_paginate_custom_object_archived_ids` call this when their archived sweeps fail.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2337–2345)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Extracts HubSpot’s error message from an HTTP error response when possible. It is a small helper for interpreting upstream failures.

**Data flow**: It receives an HTTP error. It tries to parse the response as JSON, checks for a dictionary `message`, and returns it as a string or `None`.

**Call relations**: This supports error-classification helpers, especially the archived-sweep unsupported check.


##### `HubSpotConnector._paginate_junction`  (lines 2347–2394)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds synthetic relationship rows for simple parent-to-target links such as deal contacts or ticket companies. HubSpot returns these associations embedded in parent object list responses.

**Data flow**: It receives a client, parent object type, and target object type. It pages parent objects with the target associations included, extracts each parent id and target id pair, builds a flat row for the relationship, and yields pages until no next cursor remains.

**Call relations**: `_paginate_unchecked` calls this for junction streams. Because HubSpot does not provide association modification timestamps here, this method re-walks the relationships and relies on stable ids for idempotent upserts.

*Call graph*: called by 1 (_paginate_unchecked).


### `extensions/sources/ufo_ext_sources/salesforce.py`

`io_transport` · `source sync`

Salesforce is a customer relationship management system, and each company’s Salesforce setup can expose a different set of fields. This connector avoids guessing those fields. Before reading a Salesforce object, it asks Salesforce for that object’s field list, then builds a SOQL query, which is Salesforce’s SQL-like search language, to fetch all available columns.

The file defines a catalogue of Salesforce streams, such as accounts, contacts, opportunities, users, cases, and campaigns. Each stream says which Salesforce object to read, which field is the unique ID, and which timestamp field is used as the sync cursor. A cursor is like a bookmark: after one sync finishes, the next sync can ask only for records changed after that point.

The main class, `SalesforceConnector`, reads records in pages of 200. It follows Salesforce’s `nextRecordsUrl` links until there are no more results. On later syncs, it also asks Salesforce which records were hard-deleted since the previous cursor, so the system can create delete markers instead of silently keeping vanished records. If Salesforce rejects access with a permission or authentication error, the stream is skipped with a clear message. This file only reads from Salesforce; it does not write changes back.

#### Function details

##### `_stream`  (lines 29–38)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Salesforce stream. It gives the sync engine the important facts it needs, such as the Salesforce object name, the record ID field, and the timestamp fields used for ordering and incremental updates.

**Data flow**: It receives a friendly stream name, a Salesforce object name, and whether the stream is considered canonical. It fills in the shared Salesforce defaults, such as `Id` as the primary key and `SystemModstamp` as the update cursor, and returns a `StreamSpec` object that the connector can later use.

**Call relations**: This helper is used while the file is loaded to build the `SALESFORCE_STREAMS` list. Those stream descriptions are then attached to `SalesforceConnector`, so the wider source framework knows which Salesforce objects this connector can read.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 78–82)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: This function builds the Salesforce query used to fetch records from one object. It is responsible for asking for the right fields, applying the cursor when this is an incremental sync, and sorting results in cursor order.

**Data flow**: It receives a stream description, a list of field names, and an optional cursor value. It joins the fields into a `SELECT` query, adds a `WHERE` clause if there is a previous cursor, adds an `ORDER BY` clause when the stream has a cursor field, limits the page size, and returns the final SOQL query string.

**Call relations**: `paginate` calls this after `_describe_fields` has discovered the object’s fields. The returned query is then sent to Salesforce’s query endpoint to begin reading records.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 84–90)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: This function asks Salesforce what fields exist on a given object. That matters because Salesforce organizations can customize their data, so a fixed hard-coded field list would miss information or break.

**Data flow**: It receives an HTTP client and a Salesforce object name. It calls the Salesforce describe endpoint, reads the returned field metadata, keeps only entries that look like real field records with names, and returns a plain list of field-name strings.

**Call relations**: `paginate` calls this at the start of reading each stream. Its output feeds into `_build_soql`, allowing the query to request the fields that this Salesforce organization actually exposes.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 92–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main reading loop for a Salesforce stream. It fetches changed records page by page, follows Salesforce’s continuation links, and, on incremental syncs, also reports records that were deleted.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. First it discovers the available fields, then builds and sends a SOQL query. Each response may contain records and possibly a link to the next page; it yields each non-empty batch of records until Salesforce says the query is done. If a cursor was provided, it then asks `_deleted_page` for deletions since that cursor and yields that delete page if one exists. If Salesforce returns a 401 or 403 refusal, it turns that into a clear `StreamSkipped` error; other HTTP errors are allowed to continue upward.

**Call relations**: The source framework calls `paginate` when it wants data for one Salesforce stream. Inside that flow, `paginate` coordinates `_describe_fields`, `_build_soql`, repeated REST reads, and `_deleted_page`. It is the function that connects the general sync engine to Salesforce’s specific paging and deletion behavior.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 121–139)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: This function asks Salesforce which records were permanently deleted during an incremental sync window. It lets the system record tombstones, meaning markers that say a record should be treated as gone.

**Data flow**: It receives an HTTP client, a stream description, and the previous cursor. It chooses the current time as the end of the deletion window, calls Salesforce’s deleted-records endpoint, extracts deleted record IDs, and chooses the next cursor from Salesforce’s `latestDateCovered` value when available. It returns a `StreamPage` containing the deleted IDs and next cursor, or nothing if there is no useful page to emit.

**Call relations**: `paginate` calls this only after normal record paging and only when there was already a cursor. The result is handed back to the sync engine as a special page so deletions are processed alongside ordinary record updates.

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 141–144)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function cleans up each Salesforce record before the system stores it. Salesforce includes an `attributes` wrapper with API metadata, and this connector removes that wrapper so only the business fields remain.

**Data flow**: It receives one record dictionary and its stream description. If the record contains an `attributes` key, it returns a copy without that key. If there is no such key, it returns the record unchanged.

**Call relations**: The broader connector framework uses `flatten` when preparing fetched records for downstream storage or indexing. Unlike `paginate`, it does not call other helpers here; it is the final small cleanup step after records have been read.


### Support and conversations
Connectors that ingest helpdesk tickets, customer conversations, knowledge content, community content, and support administration records.

### `extensions/sources/ufo_ext_sources/freshdesk.py`

`io_transport` · `data sync`

Freshdesk exposes many kinds of records through its REST API, which means the system must know which web address to call, how to sign in, and how to keep asking for the next page of results until nothing is left. This file is that guidebook for Freshdesk.

It defines the list of Freshdesk “streams,” where a stream is one type of data the sync system can collect, such as tickets or contacts. Most streams are simple: call one Freshdesk endpoint and follow Freshdesk’s “next page” link. Some are more like a family tree. For example, conversations live under tickets, solution articles live under solution folders, and folders live under categories. The connector walks those parent-child paths step by step.

Authentication is done with Freshdesk’s API key using HTTP Basic auth, where the key is the username and a dummy password is supplied. If the credential instead provides a prebuilt transport, the connector uses that unchanged, which lets another part of the system proxy or broker the request.

The most important special case is tickets. Freshdesk uses numbered pages for tickets and has a hard 300-page ceiling, so the connector stops before going beyond that limit. If Freshdesk rejects access with 401 or 403, the stream is skipped with a clear message instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 55–69)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a StreamSpec, which is the system’s description of one Freshdesk data type to read. It keeps the stream list compact and consistent by filling in common defaults like the source object name and primary key.

**Data flow**: It receives a stream name plus optional details such as the Freshdesk object name, the primary key field, a cursor field for incremental sync, and whether the stream is considered canonical. It fills in any missing defaults, then returns a StreamSpec object that the rest of the sync system can use.

**Call relations**: This helper is used while the module is loaded to build FRESHDESK_STREAMS. It hands each finished StreamSpec to the FreshdeskConnector through its streams_list class setting.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector._make_client`  (lines 109–124)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Freshdesk. It sets the Freshdesk base address, request timeouts, JSON headers, and the right authentication method.

**Data flow**: It receives a base URL and a Credential. It trims the URL, prepares headers, and builds a timeout. If the credential already contains a custom transport, it returns a client using that transport. If the credential contains an API key, it returns a client using HTTP Basic authentication with that key. If neither is present, it raises an error because it cannot safely call Freshdesk.

**Call relations**: The wider RestConnector machinery calls this when it is ready to open a Freshdesk connection. This function hands back an httpx AsyncClient, which later pagination functions use to make the actual web requests.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 127–137)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: This builds the query options for fetching one page of Freshdesk tickets. It keeps ticket requests consistent, including page size, sort order, included details, and optional incremental filtering.

**Data flow**: It receives an optional cursor and a page number. It creates a dictionary asking Freshdesk for up to 100 tickets, sorted by updated time from oldest to newest, with extra ticket details included. If a cursor is present, it adds an updated_since filter so only newer or changed tickets are requested. The dictionary is returned to be used in the HTTP request.

**Call relations**: FreshdeskConnector._paginate_tickets calls this for every ticket page it requests. It is the small parameter-building step before the connector asks Freshdesk for the next batch of ticket records.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 139–213)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a Freshdesk stream. Given a stream name, it chooses the correct paging strategy and yields batches of records back to the sync system.

**Data flow**: It receives an HTTP client, a StreamSpec, and an optional cursor. It checks the stream name and sends the work to the right helper: special ticket paging, conversation fetching under tickets, two-level or three-level tree walking, or normal link-header paging. It yields lists of record dictionaries as they arrive. If Freshdesk replies with 401 or 403, it turns that into a StreamSkipped message so the caller knows this stream cannot be read with the current permission.

**Call relations**: The base sync framework calls this when it wants records for a particular Freshdesk stream. This function then delegates to FreshdeskConnector._paginate_tickets, FreshdeskConnector._paginate_conversations, FreshdeskConnector._paginate_two_level, FreshdeskConnector._paginate_three_level, or FreshdeskConnector._paginate_link_header, depending on the stream shape.

*Call graph*: calls 6 internal fn (__init__, _paginate_conversations, _paginate_link_header, _paginate_three_level, _paginate_tickets, _paginate_two_level).


##### `FreshdeskConnector._paginate_link_header`  (lines 215–222)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Freshdesk endpoints that use a standard “Link” header to point to the next page. A Link header is like a note on the response saying, “go here next for more results.”

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It starts at that path with a page size of 100, follows each next-page link supplied by Freshdesk, and yields each page as a list of records.

**Call relations**: FreshdeskConnector.paginate uses this for simple streams. The nested walkers also use it whenever they need to list parents, children, or leaves in a Freshdesk tree.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 224–241)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads ticket records using Freshdesk’s special numbered-page system. It also respects Freshdesk’s 300-page limit so the connector does not ask for pages the API will reject.

**Data flow**: It receives an HTTP client and an optional cursor. Starting at page 1, it builds ticket request parameters, asks Freshdesk for that page, and turns the response into a list of records. It yields each non-empty page, then stops when the page is short, empty, or would pass the 300-page ceiling.

**Call relations**: FreshdeskConnector.paginate calls this when the requested stream is tickets. FreshdeskConnector._paginate_conversations also calls it first, because conversations are fetched by walking through the tickets that are visible for the current cursor.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, paginate).


##### `FreshdeskConnector._paginate_conversations`  (lines 243–259)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads conversations by first finding tickets, then asking Freshdesk for the conversations attached to each ticket. It adds the ticket ID to each conversation when needed so the relationship is preserved.

**Data flow**: It receives an HTTP client and an optional cursor. It gets pages of tickets, extracts each ticket’s id, then calls the ticket-conversations endpoint for that id. For every conversation record returned, it makes sure ticket_id is present. It yields conversation pages back to the caller.

**Call relations**: FreshdeskConnector.paginate calls this for the conversations stream. Internally it relies on FreshdeskConnector._paginate_tickets to find ticket IDs and FreshdeskConnector._paginate_link_header to page through each ticket’s conversation list.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (paginate).


##### `FreshdeskConnector._paginate_two_level`  (lines 261–272)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks Freshdesk data shaped as parent records with child records underneath them. Examples include folders with canned responses, categories with forums, or topics with comments.

**Data flow**: It receives an HTTP client, a parent API path, and a child path template containing a placeholder for the parent id. It lists parent records, takes each parent id, fills that id into the child path, and yields the child pages returned from Freshdesk. Parents without an id are ignored because their child path cannot be built.

**Call relations**: FreshdeskConnector.paginate calls this for streams that have exactly one parent-child step. This helper uses FreshdeskConnector._paginate_link_header both to read the parent list and to read each child list.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


##### `FreshdeskConnector._paginate_three_level`  (lines 274–297)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks Freshdesk data shaped as a three-step tree. Its main use here is solution articles, which are found under folders, which are found under categories.

**Data flow**: It receives an HTTP client, a root path, a middle path template, and a leaf path template. It lists root records, extracts each root id, lists the middle records under that root, extracts each middle id, then lists and yields the leaf records under that middle item. Any record without an id is skipped because the next URL cannot be formed.

**Call relations**: FreshdeskConnector.paginate calls this for streams that require two parent lookups before the final records can be read. It depends on FreshdeskConnector._paginate_link_header at every level of the tree.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/intercom.py`

`io_transport` · `source sync, while reading Intercom streams`

Intercom stores customer support data behind a web API, but not all parts of that API behave the same way. Some endpoints are searched with a POST request and a cursor, some are scrolled through with a special scroll token, and some return a simple list in one response. This file is the adapter that hides those differences.

The central class, IntercomConnector, describes which Intercom streams exist and how to fetch each one. When the sync engine asks for a stream, paginate chooses the right fetching method. It is like a travel guide that knows which ticket you need for each train line.

The file also prepares records so later database or reporting steps can use them more easily. Intercom often nests useful values inside smaller objects, such as a conversation source, an author, or a contact’s companies. The flatten methods copy selected nested values into top-level fields. It also converts Intercom’s timestamp cursor from an integer into a string because the wider sync framework stores watermarks as strings.

Authentication is handled by the base REST connector, but this file adds the required Intercom API version header. If Intercom refuses access because the credential lacks permission, the connector marks that stream as skipped instead of crashing the whole run.

#### Function details

##### `_stream`  (lines 52–66)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a stream description for one kind of Intercom data. A stream description tells the sync system what the stream is called, what object it represents, which field uniquely identifies records, and which field is used for incremental syncing.

**Data flow**: It receives a stream name plus optional details such as the source object name, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults when details are missing, then returns a StreamSpec object that the connector uses later to know how to sync that stream.

**Call relations**: This helper is used while building the file’s list of Intercom streams. Each created StreamSpec becomes part of the connector’s catalog, so later sync steps can ask for streams by name.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector._make_client`  (lines 103–106)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Intercom and adds the Intercom API version header. This matters because Intercom can change API behavior between versions, so the connector pins the version it expects.

**Data flow**: It receives the API base URL and a credential. It asks the parent REST connector to create the authenticated client, then adds an Intercom-Version header to every future request, and returns that prepared client.

**Call relations**: The wider REST connector setup calls this when it needs a client for Intercom. After this point, all pagination methods use the returned client to make requests with the correct authentication and version header.


##### `IntercomConnector._build_search_body`  (lines 109–139)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the request body for Intercom endpoints that use the search API. It tells Intercom how many records to return, how to continue from the previous page, and how to fetch only records newer than the saved cursor.

**Data flow**: It receives the stream being searched, the saved cursor from the last sync, and an optional page cursor called starting_after. It creates a JSON body with pagination, sorting, and a query filter. If the saved cursor looks like a number, it sends it as a number because Intercom expects that for timestamp filters. The result is a dictionary ready to send in a POST request.

**Call relations**: The search pagination paths call this before each request. Conversation-parts pagination also uses it because it first searches conversations before fetching each conversation’s parts.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 142–147)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: Safely picks the first dictionary from a list-like value. It is used when Intercom wraps related records in arrays but the connector only needs the first related item.

**Data flow**: It receives any value. If that value is a non-empty list and its first item is a dictionary, it returns that first dictionary. Otherwise, it returns nothing.

**Call relations**: The flattening helpers use this small utility when pulling a first contact or first company out of Intercom’s nested relationship fields.


##### `IntercomConnector._flatten_conversation`  (lines 150–167)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes conversation records easier to query by copying a few useful nested values to top-level fields. In particular, it surfaces the message source details and the first requester contact ID.

**Data flow**: It receives one conversation record. It copies the record, looks inside source for type, subject, and body, and writes those as flat fields. It also looks inside contacts for the first contact and writes that contact’s id as requester_id. It returns the enriched copy without changing the original record directly.

**Call relations**: The general flatten method calls this only for the conversations stream. Its output is then passed back to the sync pipeline as the cleaner version of the record.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 170–179)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes conversation-part records easier to use by copying author information to top-level fields. A conversation part is one message or event inside a larger conversation.

**Data flow**: It receives one conversation-part record. It copies the record, looks for an author object, and if present adds author_type and author_id fields. It keeps the conversation_id field intact if it was added earlier by the paginator. It returns the enriched copy.

**Call relations**: The general flatten method calls this for the conversation_parts stream. The conversation-parts paginator adds conversation_id before records arrive here, and this helper adds author details afterward.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 182–190)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes contact records easier to connect to companies by copying the first associated company ID into a simple org_id field.

**Data flow**: It receives one contact record. It copies the record, looks inside its companies wrapper, picks the first company if available, and writes that company’s id or company_id as org_id. It returns the enriched copy.

**Call relations**: The general flatten method calls this for the contacts stream. The result gives later transforms a straightforward company link without needing to understand Intercom’s nested shape.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 192–206)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes individual Intercom records after they are fetched. It exposes important nested fields as simple top-level fields and converts timestamp cursors into strings so the sync framework can store them.

**Data flow**: It receives a record and the stream it belongs to. Depending on the stream name, it sends the record through the matching flattening helper. Then, if the stream has a cursor field and that field is an integer, it returns a copy where that cursor value is written as a decimal string. Otherwise, it returns the record as-is or with the flattening changes.

**Call relations**: The sync framework calls this after records are paged in. It delegates stream-specific cleanup to the conversation, conversation-part, and contact flattening helpers, then hands the normalized record back to the rest of the pipeline.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 208–252)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right way to read pages for each Intercom stream. This is needed because Intercom uses several different pagination patterns depending on the endpoint.

**Data flow**: It receives an HTTP client, a stream description, and the saved cursor. It checks the stream name and routes the request to the matching pagination method: search, scroll, simple list, attributes, conversation parts, company segments, or activity logs. It yields pages of records as they arrive. If Intercom responds with an authorization refusal, it turns that into a skipped stream notice.

**Call relations**: This is the main paging entry used by the base sync system for Intercom streams. It acts as the dispatcher and hands off the actual API work to the specialized _paginate_* methods.

*Call graph*: calls 8 internal fn (__init__, _paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search).


##### `IntercomConnector._paginate_search`  (lines 254–274)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads streams that use Intercom’s search API, such as conversations, contacts, and tickets. It keeps asking for the next page until Intercom says there are no more results.

**Data flow**: It receives the HTTP client, the stream to search, and the saved cursor. For each loop, it builds a search body, sends a POST request, extracts the records from the response, yields them if any exist, then reads the next starting_after token for the following page. When there is no next token, it stops.

**Call relations**: paginate calls this for search-based streams. This method relies on _build_search_body to create each request body before it sends the request to Intercom.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_scroll`  (lines 276–290)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads companies using Intercom’s scroll API. A scroll API is like turning pages with a special bookmark token returned by the server.

**Data flow**: It starts with no scroll token and sends a GET request to the companies scroll endpoint. It yields each batch of company records, then uses the returned scroll_param as the bookmark for the next request. It stops when there are no records or no next scroll token.

**Call relations**: paginate calls this for the companies stream. The pages it yields go back through the normal sync flow for flattening and storage.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_list`  (lines 292–304)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads streams that Intercom returns as a simple list, such as admins, tags, teams, and segments. These endpoints do not need repeated paging in this connector.

**Data flow**: It receives the HTTP client and stream description, chooses the endpoint path for that stream, and sends one GET request. It looks for a list under either the stream’s name or a generic data key. If it finds a non-empty list, it yields that list once, then stops.

**Call relations**: paginate calls this for simple list streams. Unlike search or scroll pagination, this method does not loop through multiple pages.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_attributes`  (lines 306–315)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Intercom data-attribute definitions for companies or contacts. These are metadata fields that describe custom attributes in Intercom.

**Data flow**: It receives the HTTP client and stream description. It maps the stream name to the Intercom model name, asks the data attributes endpoint for that model, extracts the data list, and yields it if it is not empty.

**Call relations**: paginate calls this for company_attributes and contact_attributes. It is a specialized one-request reader for Intercom’s attributes endpoint.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_conversation_parts`  (lines 317–349)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the individual parts inside conversations. Intercom does not provide this as a simple top-level stream here, so the connector first finds conversations, then fetches each conversation’s details to get its parts.

**Data flow**: It receives the HTTP client and saved cursor. It searches conversations page by page using the same updated-at cursor logic as the conversations stream. For each conversation with an id, it fetches the full conversation detail, extracts its conversation_parts list, stamps each part with the parent conversation_id, and yields the parts. It continues until the conversation search has no next page.

**Call relations**: paginate calls this for the conversation_parts stream. It uses _build_search_body to page through parent conversations, then makes detail requests for each parent so child records can be emitted as their own stream.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_company_segments`  (lines 351–375)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the segments attached to each company. Since segments are reached through each company, the connector scrolls through companies and then asks Intercom for every company’s segments.

**Data flow**: It starts scrolling through companies with an optional scroll token. For each company page, it skips companies without an id, fetches that company’s segments, adds the company_id to each segment record, and yields segment batches when present. It advances using the returned scroll_param until there are no more companies or no next token.

**Call relations**: paginate calls this for the company_segments stream. It builds on the same company scroll pattern used by _paginate_scroll, but adds per-company follow-up requests to collect child segment records.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_activity_logs`  (lines 377–399)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads admin activity log records, optionally only after a saved creation-time cursor. Activity logs use their own next-page link style, so this method follows those links until they end.

**Data flow**: It receives the HTTP client and optional cursor. If a cursor exists, it sends it as created_at_after on the first request. It fetches activity log pages, yields any records found, then checks the response for a next page link. If the next link is a full URL, it trims it down to a path the client can use. It stops when there is no next link.

**Call relations**: paginate calls this for the activity_logs stream. It is separate from the other paginators because Intercom returns activity-log pagination as next links rather than search cursors or scroll tokens.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/zendesk.py`

`io_transport` · `during source sync when reading Zendesk API pages`

Zendesk exposes its data through many web API endpoints, and those endpoints do not all behave the same way. This file is the adapter that hides those differences from the rest of the system. Think of it like a tour guide who knows which door to use for each room in a large building, and how to keep walking until every room has been visited.

The file first defines the list of Zendesk “streams,” meaning named collections of records the system can sync, such as tickets, users, articles, or groups. Some streams have special names or fields in Zendesk’s responses, so the connector records those differences up front.

The main class, `ZendeskConnector`, reads pages of records from Zendesk. High-volume data such as tickets and users uses Zendesk’s incremental cursor export, which asks for records changed since a starting time and follows a cursor until Zendesk says the stream is finished. Simpler resources use ordinary page-by-page links. A few streams need extra shaping: ticket comments are extracted from ticket event data, and user identities are fetched by first listing users and then asking for each user’s identities.

If Zendesk refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of treating the whole sync as a mystery failure. The base URL depends on the customer’s Zendesk subdomain, so it must be supplied elsewhere before the connector can run.

#### Function details

##### `_stream`  (lines 58–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a description of one Zendesk stream, such as tickets or articles. It gives the rest of the system the important labels for that stream: what Zendesk object to request, which field identifies a record, and which timestamp can be used for incremental syncing.

**Data flow**: It receives a stream name and optional details like the source object name, primary key, and timestamp fields. It fills in sensible defaults when details are not provided, then returns a `StreamSpec`, which is the system’s compact recipe for syncing that stream.

**Call relations**: This helper is used while building the module-level `ZENDESK_STREAMS` list. The connector later exposes that list so the broader source-sync machinery knows which Zendesk collections are available.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 142–167)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: This function enriches records with related data that Zendesk returned alongside them. In this file, it is used to copy user email addresses onto ticket records, so a ticket can directly show requester, submitter, or assignee email without another lookup.

**Data flow**: It receives the main records, the full API page, and instructions for how to match related records. It builds a lookup table from the related arrays in the page, then scans each main record, finds the referenced related item by ID, and writes the chosen email field onto the record when possible. It changes the record dictionaries in place and returns nothing.

**Call relations**: The incremental ticket pagination path calls this after fetching a page that includes sideloaded users. It runs before the page is yielded, so downstream code receives tickets that already contain the extra email fields.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 176–177)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This method decides which key in Zendesk’s JSON response contains the actual list of records for a stream. Most streams use their own name, but some Zendesk endpoints use a different word, such as `policies` for SLA policies.

**Data flow**: It receives a stream description. It checks a small override table for streams with unusual response keys, and otherwise falls back to the stream’s own name. It returns the string key to read from the API response.

**Call relations**: The default pagination path calls this before reading each page. That lets one generic page-reading loop work for many Zendesk endpoints even when their response field names differ.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 180–192)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: This method converts a saved sync position into the Unix time format Zendesk expects. Unix time means the number of seconds since January 1, 1970, and Zendesk uses it for incremental export starting points.

**Data flow**: It receives a cursor value, which may be missing, already numeric text, or an ISO-style date string. Missing or unparseable values become `0`, numeric strings become integers, and valid date strings become Unix timestamps. The result is an integer start time for an API request.

**Call relations**: The incremental ticket/user/organization paths, ticket comment extraction, and user identity fetching all call this before building their first Zendesk request. It ensures all of those flows speak Zendesk’s expected time format.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 195–204)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: This method turns Zendesk’s “next page” URL into just the path and query part needed by the connector’s HTTP helper. It keeps pagination moving without depending on the full absolute URL returned by Zendesk.

**Data flow**: It receives a possible next-page URL. If the value is empty or has no path, it returns nothing. Otherwise it parses the URL, keeps the path, adds the query string if present, and returns that as the next request path.

**Call relations**: Every pagination style uses this after reading a page. Zendesk often returns full URLs for `next_page` or `after_url`, and this helper standardizes them before the next request is made.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 206–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher that chooses how to read a particular Zendesk stream. Different Zendesk resources need different paging strategies, and this method routes each stream to the correct one.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing where the previous sync stopped. It checks the stream name, delegates to the special comment, identity, incremental, or default pagination method, and yields batches of records from whichever method is appropriate. If Zendesk rejects access with a permission or authentication error, it raises `StreamSkipped` with a clear explanation.

**Call relations**: The broader sync framework calls this when it wants records for a Zendesk stream. This method then hands off to the specialized pagination helpers and passes their record batches back to the caller.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 232–253)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads high-volume Zendesk streams using Zendesk’s incremental cursor API. It is designed for large or frequently changing data, where asking for “everything changed since this time” is safer and faster than walking ordinary pages from the beginning.

**Data flow**: It receives an HTTP client, a stream description, and a cursor. It converts the cursor into a Unix start time, builds the incremental export URL, fetches pages, extracts the records, optionally enriches ticket records with sideloaded user emails, and yields non-empty batches. It follows Zendesk’s cursor links until Zendesk reports the end of the stream.

**Call relations**: `ZendeskConnector.paginate` calls this for streams like tickets, users, organizations, and ticket metric events. It relies on `_cursor_to_unix` to start at the right time, `_apply_sideload` to enrich tickets, and `_next_page_path` to continue from page to page.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 255–265)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads ordinary Zendesk endpoints that use standard page-by-page navigation. It is the simple path for resources that do not need Zendesk’s incremental cursor export or special reshaping.

**Data flow**: It receives an HTTP client and a stream description. It builds the first API path, chooses the response field that contains records, fetches each page, yields any records found, and follows the next-page link until there are no more pages.

**Call relations**: `ZendeskConnector.paginate` calls this when a stream has no special case. It uses `_data_field` to read the correct list from Zendesk’s response and `_next_page_path` to move through the pages.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 267–299)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method creates a ticket comments stream from Zendesk’s ticket event feed. Zendesk does not read comments here as a plain top-level list, so the connector digs into ticket events and pulls out only the child events that are actual comments.

**Data flow**: It receives an HTTP client and a cursor. It converts the cursor to a Unix start time, requests incremental ticket events with comment events included, scans each event’s child events, keeps only comment entries, adds the parent ticket ID, normalizes numeric timestamps into readable date strings, and yields batches of comments. It follows cursor links until Zendesk says the stream is complete.

**Call relations**: `ZendeskConnector.paginate` calls this only for the `ticket_comments` stream. It uses `_cursor_to_unix` for the starting point and `_next_page_path` to continue through Zendesk’s event pages.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate); 1 external calls (fromtimestamp).


##### `ZendeskConnector._paginate_user_identities`  (lines 301–326)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads user identity records, such as email or login identities, by first finding users and then asking Zendesk for each user’s identities. It exists because identities are nested under individual users rather than exposed as one simple global stream.

**Data flow**: It receives an HTTP client and a cursor. It fetches incrementally changed users starting at that cursor, skips invalid user entries, then for each valid user ID requests that user’s identities page by page. Each non-empty batch of identities is yielded. After all users on a page are processed, it moves to the next user page until the incremental user stream ends.

**Call relations**: `ZendeskConnector.paginate` calls this for the `users_identities` stream. It uses `_cursor_to_unix` to choose the first user page and `_next_page_path` for both the user list pagination and each user’s identity pagination.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).
