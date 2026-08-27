# CRM, Support, and Business Workflow Providers  `stage-16.7`

This stage is shared behind-the-scenes support for syncing business tools into the system. Each provider is like an adapter plug: it knows one outside service, talks to that service’s API, and reshapes the replies into the common record format used by the rest of the project.

The CRM adapters cover customer and sales data. Airtable discovers bases and tables, then reads their records. Attio reads companies, people, deals, tasks, notes, meetings, and calls. HubSpot handles a wide range of CRM, marketing, conversation, consent, list, analytics, and custom-object data. Salesforce reads standard sales and support objects such as accounts, contacts, opportunities, and cases.

The workflow and response adapters bring in scheduling and form data. Calendly reads users, event types, groups, scheduled events, and invitees. Typeform reads forms, responses, workspaces, themes, images, and webhooks.

The support adapters cover help-desk systems. Freshdesk, Intercom, and Zendesk each know how to authenticate, request pages of results, and turn tickets, conversations, users, organizations, articles, and related details into steady streams for storage and search.

## Files in this stage

### Business Databases
Readers for flexible business databases that expose structured operational records for syncing.

### `extensions/sources/ufo_ext_sources/providers/airtable.py`

`io_transport` · `sync run`

Airtable data is not offered as one simple list. It is more like a building directory: first you must find the buildings, then the rooms inside each building, then the files inside each room. This connector does that walking for the rest of the system.

It defines three readable streams: bases, tables, and records. A base is an Airtable workspace-like container. A table belongs to a base. A record is a row inside a table. The connector starts by calling Airtable's metadata API to list bases. For each base, it asks Airtable for the tables in that base. For each table, it reads records in pages of up to 100 items, following Airtable's `offset` token when there are more records to fetch.

As it reads nested data, it adds helpful context such as `base_id`, `base_name`, `table_id`, and `table_name`. That matters because a record by itself does not fully explain where it came from. The file also reshapes records slightly before the rest of the system sees them, for example by adding useful API URLs and normalizing Airtable's `createdTime` field into `created_at`.

There is no write support here. This is deliberately a read-only source connector.

#### Function details

##### `AirtableConnector._bases`  (lines 39–41)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Airtable bases the connected account can see. This is the starting point for every deeper Airtable sync, because tables and records are found through bases.

**Data flow**: It receives an HTTP client that is already ready to talk to Airtable. It asks Airtable for `/meta/bases`, extracts the `bases` list from the response, and returns that list as plain record dictionaries.

**Call relations**: During pagination, `AirtableConnector.paginate` calls this first when it needs the `bases` stream. It also calls it before syncing tables or records, because those streams depend on knowing which bases exist.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 43–50)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Fetches all tables inside one Airtable base. It also labels each table with the base it came from, so later readers do not lose that parent-child connection.

**Data flow**: It receives an HTTP client and one base record. It reads the base's `id`; if the id is missing or invalid, it returns an empty list. Otherwise it asks Airtable for that base's tables, extracts the `tables` list, adds the base id and base name to each table, and returns the enriched table list.

**Call relations**: After `AirtableConnector.paginate` has loaded bases, it calls this for each base when building the `tables` stream. It also calls this while syncing records, because records can only be fetched after the connector knows which tables exist in each base.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 52–70)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the records from one specific Airtable table, page by page. It adds base and table context to every record batch so each row can be traced back to its source table.

**Data flow**: It receives an HTTP client, a base id, and a table record. It checks that the table has a usable id. If not, it stops without yielding anything. If the table id is valid, it requests records from Airtable in pages of 100, following Airtable's `offset` cursor when more pages exist. For each non-empty page, it adds `base_id`, `table_id`, and `table_name`, then yields that page onward.

**Call relations**: Once `AirtableConnector.paginate` has found a base and its tables, it calls this for each table while producing the `records` stream. This function is the final step in the discovery chain: bases lead to tables, and tables lead to records.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 72–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main traffic director for Airtable syncing. Depending on which stream is requested, it decides whether to return bases, tables, or table records, and yields them in batches.

**Data flow**: It receives an HTTP client, a stream description, and a cursor value. For the `bases` stream, it fetches bases and yields them as one batch if any exist. For the `tables` stream, it fetches bases, then gathers tables from each base and yields them in batches around the configured page size. For the `records` stream, it walks bases, then tables, then yields each page of records. If the requested stream is unknown, it raises a skip signal instead of pretending it can sync it.

**Call relations**: The broader source-sync framework calls this when it wants Airtable data. This method then calls `_bases`, `_tables_for_base`, and `_records_for_table` in the right order, handing each stage the information discovered by the previous one.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 103–125)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Prepares raw Airtable items for storage or indexing by making their shape more consistent and useful. It keeps the original data but adds or normalizes a few fields that downstream code can rely on.

**Data flow**: It receives one record dictionary and the stream it belongs to. For bases, it adds a direct metadata API URL. For tables, it adds the table API URL using the stored base id and table id. For records, it makes sure `fields` is a dictionary, exposes the Airtable id, and copies `createdTime` into `created_at`. It returns the adjusted dictionary without changing the original stream choice.

**Call relations**: After pages have been produced by `paginate`, the surrounding connector framework can use this function to turn each raw Airtable item into the cleaner form expected by the rest of the system.


### CRM and Sales Records
Readers for CRM and sales platforms that collect companies, people, deals, opportunities, activities, and related business objects.

### `extensions/sources/ufo_ext_sources/providers/attio.py`

`io_transport` · `source sync, while reading Attio data`

Attio’s API does not return every kind of data in the same shape. Company, person, and deal records hide their useful fields inside nested “value cells.” Tasks, notes, meetings, and call recordings each use different endpoints and paging styles. This file is the adapter that smooths all of that out.

Think of it like a translator at a warehouse receiving boxes from one supplier. Some boxes have labels inside other boxes, some arrive in batches counted by offset, and some arrive with a “next page” token. The connector opens each box, finds the real label, and repacks the contents in a consistent way.

The main class, `AttioConnector`, lists the streams it can read and knows which Attio endpoint to call for each one. Its paging code fetches full snapshots, because Attio does not provide one common “changed since” field for all streams. Its flattening code lifts nested IDs like `record_id`, `task_id`, and `meeting_id` to the top level, so the system has a stable key for each item. It also turns Attio’s nested attribute cells into everyday values such as names, emails, domains, phone numbers, dates, and transcript text.

If an Attio workspace has disabled a standard object, or the connected account lacks permission for a stream, this connector skips that stream rather than failing the whole sync.

#### Function details

##### `_records_stream`  (lines 35–43)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Builds the standard description for an Attio object stream, such as companies, people, or deals. The system uses that description to know the stream name, where it comes from in Attio, and which field uniquely identifies each record.

**Data flow**: It receives a friendly stream name, an Attio object slug, and whether the stream is canonical. It fills those into a `StreamSpec`, including `record_id` as the primary key and `delete_missing=True` so each sync is treated as a full snapshot. It returns that stream specification.

**Call relations**: This helper is used when the file defines the list of Attio streams. It creates the object-based stream entries by calling `StreamSpec.__init__`, so the connector starts with a clear menu of what it can read.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 70–71)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: Safely pulls one named ID out of a nested dictionary. It prevents crashes when Attio sends an unexpected shape instead of the dictionary the code hoped for.

**Data flow**: It receives any value and the key to look for. If the value is a dictionary, it returns the matching entry; otherwise it returns `None`. It does not change anything.

**Call relations**: `AttioConnector._value_primitive` calls this when Attio stores useful IDs under nested `id` objects, such as option IDs or status IDs. It is a small safety guard inside the larger flattening process.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 80–81)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: Creates the request body used to ask Attio for one page of object records. It keeps the page size and offset format in one place.

**Data flow**: It receives an offset number, meaning how many records have already been fetched. It returns a dictionary with the fixed page limit and that offset. The returned dictionary is sent to Attio’s records query endpoint.

**Call relations**: `AttioConnector.paginate` calls this while reading object streams such as companies, people, and deals. Each time another page is needed, this function supplies the small JSON body for the next request.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._value_primitive`  (lines 84–127)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: Turns one Attio value cell into the simplest useful value. Attio stores different field types under different keys, so this function finds the human-usable value no matter whether it is text, a select option, an email, a phone number, a currency amount, a reference, or a location.

**Data flow**: It receives one dictionary from Attio. It checks the known places where Attio may put the real value, sometimes using `_nested_id` to reach an ID inside another dictionary. It returns a plain value such as a string, number, ID-like string, joined address, or `None` if nothing useful is found.

**Call relations**: This is the core decoder used by the flattening helpers. When records are being prepared for storage, the connector repeatedly asks this function to translate Attio’s nested cells into ordinary values.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 130–145)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: Reduces one Attio field cell to a single usable value, or sometimes a short list for multi-select options. It hides the awkward difference between fields that arrive as one object and fields that arrive as a list of objects.

**Data flow**: It receives a cell that may be a list, a dictionary, or an already-simple value. For lists, it converts each item to a primitive value and removes empty results; for dictionaries, it uses `_value_primitive`; otherwise it keeps the value as-is. It returns the simplified value or `None`.

**Call relations**: This helper sits inside the record-flattening path. It is used when an Attio attribute should usually become one top-level field rather than a preserved list.


##### `AttioConnector._flatten_list_cell`  (lines 148–155)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: Turns an Attio field cell into a clean list of values. It is used for fields where keeping all entries matters, such as domains, categories, email addresses, and phone numbers.

**Data flow**: It receives a cell that may already be a list or may be a single value. It converts each usable item into a plain value, removes empty results, and returns a list. If there is no usable value, it returns an empty list.

**Call relations**: This function supports `_flatten_values`, which decides that certain Attio fields should stay as lists. It keeps multi-value contact details from being accidentally collapsed too early.


##### `AttioConnector._flatten_values`  (lines 158–192)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts Attio’s `values` block into normal top-level fields. This is where most company, person, and deal attributes become readable data.

**Data flow**: It receives the nested `values` dictionary from an Attio record. For each attribute, it chooses either list-style flattening or single-value flattening, then adds convenience fields like `email`, `phone`, `domain`, `first_name`, and `last_name` when they can be derived. It returns a new flat dictionary.

**Call relations**: This function is used by `_flatten_record` as part of turning object records into the common shape expected by the sync system. It gathers many small decoded cells into one practical record.


##### `AttioConnector._flatten_record`  (lines 195–209)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns a standard Attio object record, such as a company or person, into a flat record with a clear primary key. Without this, the record ID would remain buried inside Attio’s nested `id` object.

**Data flow**: It receives the raw Attio record and the stream description. It copies important identity and timestamp fields to the top level, optionally fills a cursor field if the stream asks for one, then merges in the flattened attributes from `values`. It returns the cleaned record dictionary.

**Call relations**: `AttioConnector.flatten` calls this for the object-style streams. It is the bridge between Attio’s standard object format and the normalized records the rest of the source sync expects.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 212–216)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `task_id` to an Attio task record. This gives the sync system a stable key for storing and replacing tasks.

**Data flow**: It receives a raw task record. It copies the record, looks inside the nested `id` field when needed, writes the found value to `task_id`, and returns the copy. The original input is not intentionally rewritten.

**Call relations**: `AttioConnector.flatten` calls this when the current stream is `tasks`. It is the task-specific version of the ID-lifting work done for other Attio resources.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 219–223)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `note_id` to an Attio note record. This makes notes identifiable in the same simple way as other synced rows.

**Data flow**: It receives a raw note record. It copies all existing fields, extracts the note ID from the nested `id` shape if necessary, stores it as `note_id`, and returns the copied record.

**Call relations**: `AttioConnector.flatten` calls this for the `notes` stream. It keeps note records compatible with the common stream definition, which expects a top-level primary key.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 226–230)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `meeting_id` to an Attio meeting record. This gives meetings the stable identifier needed for full-snapshot syncing.

**Data flow**: It receives a raw meeting record. It copies the record, extracts the meeting ID from the nested `id` field or uses the ID directly if it is already a string, writes `meeting_id`, and returns the copy.

**Call relations**: `AttioConnector.flatten` calls this when processing the `meetings` stream. The call recording code also relies on meeting IDs earlier in the fetching process, but this function prepares the meeting rows themselves for output.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 233–254)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares a call recording record for storage by adding its top-level ID and readable transcript text. It also fills `recording_url` from `web_url` when Attio did not provide a direct recording URL.

**Data flow**: It receives a raw call recording record, possibly already enriched with transcript data. It copies the record, extracts `call_recording_id`, sets a fallback recording URL if needed, and joins transcript segments into a plain multiline `transcript_text`. It returns the enriched copy.

**Call relations**: `AttioConnector.flatten` calls this for the `call_recordings` stream after pagination has fetched recordings and, when available, their transcripts. It turns detailed recording data into a searchable page-like record.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 256–265)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening routine for the stream currently being synced. Different Attio resources have different shapes, so one universal flattening step would not be correct.

**Data flow**: It receives a raw record and the stream specification that says what kind of record it is. It checks the stream name and sends the record to the task, note, meeting, call recording, or standard object flattener. It returns the normalized record produced by that helper.

**Call relations**: The broader source framework calls this after records are fetched. This method then delegates to `_flatten_task`, `_flatten_note`, `_flatten_meeting`, `_flatten_call_recording`, or `_flatten_record`, so each stream gets the right cleanup.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 267–321)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches all pages for one Attio stream. It knows which Attio endpoint and paging method each stream requires.

**Data flow**: It receives an HTTP client, a stream specification, and an optional cursor value. For tasks and notes, it reads offset-based pages; for meetings and call recordings, it reads cursor-based pages; for standard objects, it posts query bodies with increasing offsets. It yields lists of raw records page by page, and may raise `StreamSkipped` when Attio says a stream is unavailable or unauthorized.

**Call relations**: This is the main read loop used by the source framework for each stream. It calls `_paginate_simple`, `_paginate_cursor`, `_paginate_call_recordings`, `_build_query_body`, and the error-checking helpers to route each stream through the right Attio behavior.

*Call graph*: calls 8 internal fn (__init__, _build_query_body, _is_object_disabled, _is_scope_unauthorized, _paginate_call_recordings, _paginate_cursor, _paginate_simple, _scope_skip_reason).


##### `AttioConnector._paginate_simple`  (lines 323–330)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads pages from Attio endpoints that use `limit` and `offset` query parameters. In this file, that fits tasks and notes.

**Data flow**: It receives an HTTP client, an endpoint path, and a page size. It asks the base REST connector for offset-based pages and yields each list of records as it arrives. It does not flatten records itself.

**Call relations**: `AttioConnector.paginate` calls this for `/v2/tasks` and `/v2/notes`. It hands the low-level offset paging work to the shared REST connector and passes each page back to the main pagination flow.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._paginate_cursor`  (lines 332–350)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads pages from Attio endpoints that use a cursor, which is a token saying where the next page starts. This is used for meetings and meeting call recordings.

**Data flow**: It receives an HTTP client, endpoint path, page size, and optional extra parameters. It asks the shared REST connector to follow `pagination.next_cursor` from one response to the next. It yields each page of records.

**Call relations**: `AttioConnector.paginate` uses this directly for meetings, and `_paginate_call_recordings` uses it both to walk meetings and to list recordings under each meeting. It provides the common cursor-reading machinery for those newer Attio endpoints.

*Call graph*: called by 2 (_paginate_call_recordings, paginate).


##### `AttioConnector._paginate_call_recordings`  (lines 352–388)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the call recordings stream by first walking through meetings, then fetching recordings for each meeting. This is needed because Attio exposes recordings under their parent meeting rather than as one simple global list.

**Data flow**: It receives an HTTP client. It pages through meetings, extracts each meeting ID, copies meeting context such as title and start/end time onto each recording, computes a duration when possible, then fetches each recording transcript when a recording ID is available. It yields pages of enriched recording records.

**Call relations**: `AttioConnector.paginate` calls this for the `call_recordings` stream. Inside, it calls `_paginate_cursor`, `_meeting_id`, `_datetime_of`, `_duration_seconds`, `_call_recording_id`, and `_fetch_transcript` to fan out from meetings to recordings and attach transcript data.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (paginate).


##### `AttioConnector._fetch_transcript`  (lines 390–402)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: Fetches the transcript for one call recording. If Attio says the transcript is not ready or not found, it quietly returns no transcript instead of stopping the sync.

**Data flow**: It receives an HTTP client, a meeting ID, and a recording ID. It builds the transcript endpoint path and sends a GET request. If the response contains a dictionary under `data`, it returns that dictionary; if Attio returns 404 or 409, it returns `None`; other HTTP errors are raised.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this after it has found a recording ID. The transcript it returns is attached to the recording before the page is yielded, so later flattening can produce `transcript_text`.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 405–409)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a meeting ID from Attio’s flexible ID shape. It accepts either the nested dictionary form or a direct string.

**Data flow**: It receives a meeting record. It looks at the record’s `id` field; if it is a dictionary, it returns `id.meeting_id`, and if it is already a string, it returns that string. Otherwise it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this while walking meeting pages. A valid meeting ID is required before the connector can ask Attio for that meeting’s call recordings.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 412–416)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a call recording ID from Attio’s flexible ID shape. This ID is needed to request the recording’s transcript.

**Data flow**: It receives a call recording record. It checks whether `id` is a dictionary containing `call_recording_id` or a direct string. It returns the found ID, or `None` if no usable ID is present.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this for each recording. When it returns an ID, the connector can hand that ID to `_fetch_transcript`.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 419–423)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: Pulls a usable date or date-time string out of Attio’s meeting time shape. Attio may send timed meetings as `datetime` or all-day meetings as `date`.

**Data flow**: It receives any value. If the value is a dictionary, it returns the `datetime` entry when present, otherwise the `date` entry. If the shape is not recognized, it returns `None`.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this when copying parent meeting timing onto call recordings. Those extracted times are also used to estimate recording duration.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 426–436)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: Calculates a simple duration in seconds from start and end ISO date-time strings. If either time is missing or cannot be parsed, it returns no duration.

**Data flow**: It receives optional start and end strings. It converts them to Python date-time objects, accepting a trailing `Z` as UTC time, subtracts start from end, and clamps negative results to zero. It returns the number of seconds as a float, or `None` if calculation is not possible.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this after extracting meeting start and end times. The resulting duration is attached to recordings when available.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 439–448)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes Attio’s specific error for a disabled standard object. This lets the connector skip, for example, deals or companies when that object is not enabled in the workspace.

**Data flow**: It receives an HTTP error. It first checks for status code 400, then tries to read the JSON body and looks for `code` equal to `standard_object_disabled`. It returns `True` when that exact condition is found, otherwise `False`.

**Call relations**: `AttioConnector.paginate` calls this when a standard object records query fails. If it returns true, pagination raises `StreamSkipped` instead of treating the situation as a fatal sync error.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._is_scope_unauthorized`  (lines 451–460)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes Attio’s specific error for a missing OAuth permission scope. An OAuth scope is a named permission granted by the connected Attio account.

**Data flow**: It receives an HTTP error. It checks for status code 403, tries to parse the response as JSON, and looks for `code` equal to `unauthorized`. It returns a boolean answer and does not change the error.

**Call relations**: `AttioConnector.paginate` calls this around streams that may require extra permissions, such as meetings and call recordings. When it returns true, the stream is skipped with a clearer reason.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._scope_skip_reason`  (lines 463–469)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a readable explanation for skipping a stream because the OAuth grant lacks a required permission. This makes the sync outcome easier to understand than a raw HTTP error.

**Data flow**: It receives an HTTP error. It tries to read the JSON response and pull out Attio’s `message`; if that is unavailable, it uses a generic fallback. It returns a sentence explaining that a required OAuth scope is missing.

**Call relations**: `AttioConnector.paginate` calls this after `_is_scope_unauthorized` identifies a permission problem. Its returned text is passed into `StreamSkipped` so the skipped stream has a useful human-facing reason.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/hubspot.py`

`io_transport` · `source sync / stream pagination`

HubSpot exposes its data through many different web API shapes. Some records live behind the CRM search API, some use simple list endpoints, and others require walking from a parent item to its children. This file is the adapter that hides that mess. It is like a translator at a busy airport: no matter which counter a passenger comes from, the translator gives the rest of the system the same kind of readable ticket.

The file first defines every HubSpot stream the connector can sync, such as contacts, companies, deals, forms, emails, pipelines, lists, analytics reports, and custom objects. Each stream says what kind of HubSpot object it reads, which field identifies a record, and which timestamp can be used as a cursor for incremental syncs.

The `HubSpotConnector` then decides how to fetch each stream. Normal CRM objects are searched page by page, sorted by last modified time. Deleted or archived records are checked separately so the local store can remove them. Product API streams use their own routes. Special streams, such as campaign assets, associations, consent states, and sequence enrollments, are built by walking related records and creating synthetic rows.

A key theme is normalization. HubSpot often nests data inside `properties`, uses different ID names, or returns timestamps in different formats. This connector flattens those rows into a predictable shape. It also treats missing permissions as skipped streams instead of failed syncs, because many HubSpot accounts simply do not include every product area.

#### Function details

##### `_normalize_epoch_millis`  (lines 248–255)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Converts a HubSpot timestamp written as milliseconds since 1970 into a standard date-time string. It leaves booleans and already non-time-looking values alone so unrelated fields are not accidentally changed.

**Data flow**: It receives any value. If the value is a number, or a digit-only string, it treats it as milliseconds from the Unix epoch and returns an ISO-formatted UTC time. Otherwise it returns the original value unchanged.

**Call relations**: Product API flattening and analytics view shaping call this when HubSpot sends dates in raw millisecond form. It gives later records a consistent date format before they leave the connector.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 258–267)

```
def _stream(name: str, *, object_type: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a standard CRM stream description for objects fetched through HubSpot's CRM search API. It avoids repeating the same stream setup for contacts, companies, deals, and similar objects.

**Data flow**: It receives a stream name, HubSpot object type, and whether the stream is canonical. It returns a `StreamSpec`, which is a small description telling the sync runner how to identify and incrementally read that stream.

**Call relations**: This helper is used at module load time to define many stream constants. Those constants are collected into `ALL_STREAMS`, which `HubSpotConnector` exposes to the source runner.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 270–289)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='createdAt', updated_at_field: str | None='updatedAt', pagi
```

**Purpose**: Creates a stream description for HubSpot data that does not use the normal CRM search API. These product API streams include things like forms, workflows, files, analytics, and conversations.

**Data flow**: It receives names for the stream, ID field, time fields, and optional pagination rules. It returns a `StreamSpec` marked as non-canonical because these records come from product-specific APIs.

**Call relations**: This helper builds the many product stream constants at import time. Later, `_paginate_unchecked` recognizes these streams and sends them to `_paginate_product_api`.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 292–304)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Builds a reusable pagination rule for HubSpot endpoints that return `results` plus a `paging.next.after` cursor. This keeps simple product API streams from needing custom pagination code.

**Data flow**: It receives an API path. It returns a `Pagination` object that says where records live in the response, where the next-page cursor lives, and which query parameters to use.

**Call relations**: Several product stream definitions use this helper. When those streams run, `_paginate_unchecked` can hand them to the base connector's strategy-based pagination.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 307–317)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Creates a stream description for relationship tables, such as deal-to-contact links. These streams are synthetic because HubSpot returns the relationship inside parent records rather than as a normal object list.

**Data flow**: It receives a stream name and parent object type. It returns a `StreamSpec` with no cursor, because HubSpot does not provide modification times for these relationships.

**Call relations**: The junction stream constants created here are later recognized by `_paginate_unchecked`, which sends them to `_paginate_junction` for full refresh-style reading.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 622–652)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the JSON request body needed to search HubSpot CRM objects. It includes all requested properties, a page size, sort order, and an optional cursor filter for incremental syncs.

**Data flow**: It receives a stream description, property names, the last saved cursor, and an optional page cursor. It returns a dictionary that HubSpot's search endpoint can accept.

**Call relations**: `_paginate_unchecked` uses it for standard CRM streams, and `_paginate_custom_object_records` uses it for custom objects. It is the common request builder for search-based pagination.

*Call graph*: called by 2 (_paginate_custom_object_records, _paginate_unchecked).


##### `HubSpotConnector._flatten`  (lines 655–666)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a normal HubSpot CRM record into a flat row. HubSpot puts most useful fields inside a `properties` bag, and this function lifts those fields to the top level.

**Data flow**: It receives one raw CRM record. It copies the ID, timestamps, archived flag, and all property fields into one plain dictionary, then returns that dictionary.

**Call relations**: `flatten` calls this for regular CRM object streams. The result is what downstream storage sees instead of HubSpot's nested envelope.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 669–692)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records from HubSpot product APIs, which do not all use the same shape. It fills in missing IDs, lifts nested properties, expands form submission values, and fixes a few timestamp formats.

**Data flow**: It receives a product API record and its stream description. It copies the record, promotes `objectId`, `properties`, and `values` fields where needed, normalizes selected timestamps, and returns the flatter row.

**Call relations**: `flatten` uses this for product API streams. It calls `_normalize_epoch_millis` for streams that need millisecond timestamps turned into readable date strings.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 694–701)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening behavior for a stream. Some streams are already flat, some are custom rows, some are product API rows, and normal CRM rows need property lifting.

**Data flow**: It receives a raw record and stream description. Based on the stream name, it either returns the record unchanged, sends it to `_flatten_product_api`, or sends it to `_flatten`.

**Call relations**: The source framework calls this after pages are fetched. It hands regular CRM rows to `_flatten` and product rows to `_flatten_product_api` so all streams leave the connector in a predictable shape.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector._list_properties`  (lines 703–711)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which fields exist for a CRM object type. This matters because HubSpot's search API only returns properties that are explicitly requested.

**Data flow**: It receives an HTTP client and an object type. It calls HubSpot's properties endpoint, filters the response to valid property names, and returns a list of strings.

**Call relations**: `_paginate_unchecked` calls this before searching a standard CRM stream. The returned list is passed into `_build_search_body` so the sync captures every available field.

*Call graph*: called by 1 (_paginate_unchecked).


##### `HubSpotConnector.paginate`  (lines 713–726)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the public page-reading method for the connector. It wraps the real pagination work with friendly error handling for streams the current HubSpot account cannot access.

**Data flow**: It receives an HTTP client, a stream, and an optional cursor. It yields pages from `_paginate_unchecked`; if HubSpot returns an authentication or permission-style failure, it raises `StreamSkipped` with a clear reason.

**Call relations**: The sync runner calls this when it wants records for a stream. It delegates fetching to `_paginate_unchecked`, uses `_is_stream_unavailable` to recognize permission limits, and `_stream_skip_reason` to explain skips.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 728–778)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Routes each stream to the correct fetching strategy. HubSpot has many API styles, so this method is the main traffic controller.

**Data flow**: It receives a stream and cursor. It checks whether the stream has built-in pagination, is a junction stream, is custom objects, is a product API stream, or is a normal CRM search stream, then yields pages from the matching path.

**Call relations**: `paginate` calls this after setting up skip handling. It may call `_paginate_junction`, `_paginate_custom_objects`, `_paginate_product_api`, `_list_properties`, `_build_search_body`, and `_paginate_archived_ids` depending on the stream.

*Call graph*: calls 6 internal fn (_build_search_body, _list_properties, _paginate_archived_ids, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._is_stream_unavailable`  (lines 781–804)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects when HubSpot is saying, "this account is not allowed to read this stream," rather than reporting a real connector bug. This helps the sync skip unavailable streams cleanly.

**Data flow**: It receives an HTTP error. It checks for status code 403 and looks for permission-related words in the response message, then returns true or false.

**Call relations**: `paginate` uses it for top-level stream skips. Archived sweeps and custom archived sweeps also use it so permission-limited cleanup requests do not fail the whole stream.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 807–816)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a human-readable explanation for why a HubSpot stream was skipped. It includes HubSpot's own message when available.

**Data flow**: It receives the stream name and an HTTP error. It tries to read the response JSON, extracts the message if present, and returns a sentence describing the unavailable stream.

**Call relations**: `paginate` calls this right before raising `StreamSkipped`. The message becomes part of the sync result rather than being treated as a crash.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 818–853)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived or deleted CRM records after a normal search. HubSpot's search endpoint omits archived records, so this extra sweep lets the local system remove records that disappeared upstream.

**Data flow**: It receives an HTTP client and stream. It walks the object's list endpoint with `archived=true`, collects record IDs, and yields `StreamPage` objects containing delete markers.

**Call relations**: `_paginate_unchecked` calls this after standard CRM search pagination. It uses `_is_archived_sweep_unsupported` and `_is_stream_unavailable` to quietly stop when HubSpot cannot provide archived data.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 855–949)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Dispatches product API streams to their special readers. Product APIs cover many HubSpot areas and often need custom rules rather than the CRM search API.

**Data flow**: It receives a stream and cursor. It checks the stream name, calls the matching paginator for special streams, or falls back to a simple GET collection path when one is declared.

**Call relations**: `_paginate_unchecked` calls this for product API streams. It hands off to specialized methods such as `_paginate_analytics_reports`, `_paginate_associations`, `_paginate_email_events`, `_paginate_form_submissions`, and many others.

*Call graph*: calls 21 internal fn (_paginate_analytics_reports, _paginate_analytics_views, _paginate_association_labels, _paginate_associations, _paginate_campaign_assets, _paginate_consent_states, _paginate_conversation_messages, _paginate_email_events, _paginate_event_occurrences, _paginate_event_types (+11 more)); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._paginate_get_collection`  (lines 951–979)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a common HubSpot list endpoint shape: records under `results` and the next page under `paging.next.after`. Many product APIs fit this pattern.

**Data flow**: It receives an HTTP client, path, page limit, and optional extra query parameters. It repeatedly requests pages, normalizes `objectId` into `id` when needed, and yields lists of records until no next cursor remains.

**Call relations**: Many specialized paginators reuse this helper instead of duplicating page-walking code. `_paginate_product_api` also uses it directly for simple product streams.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_product_api, _paginate_sequences, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 981–1016)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Reads all custom object types defined in a HubSpot account. Custom objects are account-specific, so the connector must discover their schemas before it can read records.

**Data flow**: It fetches custom object schemas, extracts each object's type ID and property names, searches records for each object type, and then sweeps archived IDs for deletion markers.

**Call relations**: `_paginate_unchecked` calls this for the `custom_objects` stream. It depends on `_custom_object_schemas`, `_schema_object_type_id`, `_schema_property_names`, `_paginate_custom_object_records`, and `_paginate_custom_object_archived_ids`.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 1018–1020)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of custom object definitions from HubSpot. These definitions describe account-specific object types and their fields.

**Data flow**: It receives an HTTP client, calls the HubSpot schema endpoint, keeps only dictionary-shaped rows, and returns them as a list.

**Call relations**: `_paginate_custom_objects` uses this before reading custom object records. `_association_object_types` also uses it so associations can include custom object types.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 1023–1028)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Finds the best identifier for a custom object schema. HubSpot may provide several possible name fields, and this chooses the first usable one.

**Data flow**: It receives a schema dictionary. It checks `objectTypeId`, `fullyQualifiedName`, and `name` in order, returning the first non-empty string or `None`.

**Call relations**: `_paginate_custom_objects`, `_custom_object_row`, and `_association_object_types` call this whenever they need a stable custom object type ID.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1031–1046)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Collects every property name worth requesting for a custom object. This includes normal schema properties plus display fields used for readable titles.

**Data flow**: It receives a schema dictionary. It walks property definitions and display-property settings, avoids duplicates, and returns a list of names.

**Call relations**: `_paginate_custom_objects` calls this before building search requests for each custom object type.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1048–1083)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Searches one custom object type and yields its records. It also avoids repeating records that sit exactly on an incremental cursor boundary.

**Data flow**: It receives a custom stream description, schema, property names, and cursor. It builds search bodies, posts them to HubSpot, filters duplicate boundary IDs, converts records with `_custom_object_row`, and yields record pages.

**Call relations**: `_paginate_custom_objects` calls this for each discovered schema. It reuses `_build_search_body` and `_custom_object_row`.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1085–1124)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Turns one raw custom object record into a useful row with both data and context about its custom object type. It gives the row a globally unique ID by combining object type and record ID.

**Data flow**: It receives a raw record and its schema. It extracts properties, labels, display fields, and timestamps, then returns a flattened row or `None` if essential IDs are missing.

**Call relations**: `_paginate_custom_object_records` calls this for every custom object record. It uses `_schema_object_type_id` to identify which custom object type the record belongs to.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1126–1156)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived custom object records so local copies can be removed. It mirrors the archived sweep used for standard CRM objects.

**Data flow**: It receives an object type ID. It walks the archived list endpoint, prefixes each deleted record ID with the object type ID, and yields delete markers in `StreamPage` objects.

**Call relations**: `_paginate_custom_objects` calls this after reading active records for each custom object type. It uses `_is_archived_sweep_unsupported` and `_is_stream_unavailable` to stop gracefully when HubSpot cannot provide archived data.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1158–1177)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a distinct list of owner teams from the owner records. HubSpot exposes team details inside owners rather than as a standalone stream here.

**Data flow**: It reads owners through `_paginate_get_collection`, extracts each embedded team, deduplicates by team ID, and yields one page of unique team rows.

**Call relations**: `_paginate_product_api` calls this for the `owner_teams` stream. It depends on the regular owners endpoint as its source of team information.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_lists`  (lines 1179–1208)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot contact or object lists through the lists search endpoint. It also flattens extra list metadata into the main row.

**Data flow**: It posts search requests with an offset, turns `listId` into `id`, merges `additionalProperties`, yields pages, and advances until HubSpot says there are no more lists.

**Call relations**: `_paginate_product_api` calls this for the `lists` stream. `_paginate_list_memberships` also calls it before walking the members of each list.

*Call graph*: called by 2 (_paginate_list_memberships, _paginate_product_api).


##### `HubSpotConnector._paginate_site_search`  (lines 1210–1230)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads CMS search results for a specific content type, such as knowledge articles. It uses offset-based paging instead of HubSpot's usual `after` cursor.

**Data flow**: It receives a content type, repeatedly calls the site-search endpoint with an offset, yields dictionary-shaped result rows, and stops when the offset reaches the total count.

**Call relations**: `_paginate_product_api` calls this for knowledge articles. It is a small adapter for the CMS search API's paging style.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1232–1256)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds marketing assets attached to each campaign. HubSpot requires first listing campaigns, then asking for each type of asset under each campaign.

**Data flow**: It reads campaign pages, extracts each campaign ID and name, loops over known campaign asset types, and yields pages from `_paginate_campaign_asset_type`.

**Call relations**: `_paginate_product_api` calls this for the `campaign_assets` stream. It uses `_paginate_get_collection` for campaigns and delegates asset-type detail work to `_paginate_campaign_asset_type`.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1258–1293)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one asset type for one campaign and turns each asset into a stable, contextual row. It adds the campaign ID, campaign name, asset type, and a unique combined ID.

**Data flow**: It receives a campaign ID, campaign name, and asset type. It pages through that campaign's asset endpoint, builds enriched rows, yields non-empty pages, and ignores forbidden or missing asset-type endpoints.

**Call relations**: `_paginate_campaign_assets` calls this once for every campaign and known asset type. It reuses `_paginate_get_collection` for the actual page walking.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1295–1301)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields saved analytics view definitions. These views can later be used as filters for analytics reports.

**Data flow**: It asks `_analytics_view_rows` for normalized view rows and yields them if any exist.

**Call relations**: `_paginate_product_api` calls this for the `analytics_views` stream. `_analytics_view_rows` does the actual fetching and shaping.

*Call graph*: calls 1 internal fn (_analytics_view_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_view_rows`  (lines 1303–1334)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches and normalizes HubSpot analytics views. It gives each view an ID, name, filters, report kind, and cleaned creation/update fields.

**Data flow**: It calls the analytics views endpoint, accepts either a list response or a `results` response, filters valid rows, fills fallback IDs and names, normalizes millisecond creation times, and returns a list.

**Call relations**: `_paginate_analytics_views` uses this to emit view rows. `_paginate_analytics_reports` also uses it to run reports for each saved view as well as for all traffic.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1336–1366)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs many analytics report queries across report families, subjects, time periods, and analytics views. This creates a broad snapshot of HubSpot analytics data.

**Data flow**: It computes a start and end date, fetches analytics views, builds filter choices, loops over report combinations, and yields pages from `_paginate_analytics_report_query`.

**Call relations**: `_paginate_product_api` calls this for the `analytics_reports` stream. It uses `_analytics_report_window`, `_analytics_view_rows`, and `_paginate_analytics_report_query`.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_report_window`  (lines 1369–1370)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range used for analytics report queries. The range starts from a fixed old date and ends today in UTC.

**Data flow**: It takes no input. It returns two strings in HubSpot's compact `YYYYMMDD` date format: the fixed start date and the current UTC date.

**Call relations**: `_paginate_analytics_reports` calls this before issuing report queries. The returned dates are passed into `_paginate_analytics_report_query`.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1372–1423)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Runs one specific analytics report query and pages through its breakdown rows. Some combinations are not supported by HubSpot, and those are skipped.

**Data flow**: It receives report family, subject, time period, optional view filter, and date range. It calls the report endpoint with offsets, converts the response with `_analytics_report_rows`, yields rows, and advances until all breakdowns are read.

**Call relations**: `_paginate_analytics_reports` calls this for each report combination. It delegates row shaping to `_analytics_report_rows` and treats 400 or 404 responses as unsupported combinations.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1426–1501)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Turns one analytics report response into rows the rest of the system can store. It creates one totals row and one row per breakdown item.

**Data flow**: It receives the raw report data plus report context such as subject, time period, view, date range, and offset. It builds stable IDs, names, metrics, filters, and formatted dates, then returns a list of rows.

**Call relations**: `_paginate_analytics_report_query` calls this after each HubSpot response. It uses the report ID and date formatting helpers to make consistent output rows.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1504–1508)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Builds a stable ID for an analytics report row from several identifying pieces. It sanitizes separators so the ID remains predictable.

**Data flow**: It receives any number of parts. It converts them to strings, replaces troublesome slash and colon characters, joins them with colons, and prefixes the result with `analytics_report:`.

**Call relations**: Report row shaping uses this helper when creating totals and breakdown rows. Stable IDs let repeated syncs update the same report rows instead of creating duplicates.


##### `HubSpotConnector._analytics_report_date`  (lines 1511–1512)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Converts HubSpot's compact analytics date string into a readable date. For example, it turns `20260131` into `2026-01-31`.

**Data flow**: It receives an eight-character date string. It slices out year, month, and day and returns them joined with hyphens.

**Call relations**: Analytics report row creation uses this so stored report rows contain easier-to-read start and end dates.


##### `HubSpotConnector._paginate_event_types`  (lines 1514–1533)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the available event type definitions from HubSpot. These describe the kinds of events that may appear in event occurrence data.

**Data flow**: It calls the event types endpoint, handles either list or `results` response shapes, gives each row a usable ID, and yields the rows if any exist.

**Call relations**: `_paginate_product_api` calls this for the `event_types` stream. It is separate from event occurrences, which are the actual happened events.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1535–1557)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads individual event occurrences from HubSpot, optionally starting after the last saved cursor. It creates synthetic IDs when HubSpot does not provide one.

**Data flow**: It receives an optional cursor. It sends it as `occurredAfter`, reads event results, assigns each row an ID from HubSpot or `_synthetic_event_id`, and yields one page.

**Call relations**: `_paginate_product_api` calls this for the `event_occurrences` stream. It uses `_synthetic_event_id` to keep records identifiable even when HubSpot omits IDs.

*Call graph*: calls 1 internal fn (_synthetic_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._synthetic_event_id`  (lines 1560–1570)

```
def _synthetic_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Creates a stable fallback ID for an event occurrence. This prevents anonymous event rows from changing identity between syncs.

**Data flow**: It receives an event row and its position in the page. It combines event type, object type, object ID, time, and a payload hash into a colon-separated string.

**Call relations**: `_paginate_event_occurrences` calls this whenever an event row has no native ID. It relies on the stable payload hash helper for uniqueness.

*Call graph*: called by 1 (_paginate_event_occurrences).


##### `HubSpotConnector._paginate_email_events`  (lines 1572–1600)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot email event history, such as sends, opens, or clicks. It supports incremental fetching by translating the saved cursor into HubSpot's expected start timestamp.

**Data flow**: It receives an optional cursor, builds request parameters with a limit and optional start timestamp, follows offset paging, assigns fallback IDs when needed, and yields event rows.

**Call relations**: `_paginate_product_api` calls this for the `email_events` stream. It uses `_email_event_start_timestamp` and `_synthetic_email_event_id`.

*Call graph*: calls 2 internal fn (_email_event_start_timestamp, _synthetic_email_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1603–1612)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts a saved email-event cursor into the millisecond timestamp HubSpot's email events API expects. It accepts either a raw numeric cursor or an ISO date-time string.

**Data flow**: It receives a cursor string or `None`. It returns `None` for no cursor, an integer for numeric cursors, or parses an ISO time and returns milliseconds; invalid date strings also return `None`.

**Call relations**: `_paginate_email_events` calls this before making each request. It bridges the project's cursor format and HubSpot's API parameter format.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._synthetic_email_event_id`  (lines 1615–1625)

```
def _synthetic_email_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Creates a stable fallback ID for an email event when HubSpot does not include one. It uses key event details plus a short hash of the full row.

**Data flow**: It receives an email event row and page index. It combines created time, recipient, event type, campaign ID, and payload hash into a safe string ID.

**Call relations**: `_paginate_email_events` calls this for email event rows missing IDs. It uses the stable payload hash helper to reduce collisions.

*Call graph*: called by 1 (_paginate_email_events).


##### `HubSpotConnector._stable_payload_hash`  (lines 1628–1630)

```
def _stable_payload_hash(row: dict[str, Any]) -> str
```

**Purpose**: Creates a short, repeatable fingerprint for a record's full contents. This is useful when HubSpot gives no ID but the row still needs a stable identity.

**Data flow**: It receives a dictionary, serializes it to JSON with sorted keys, hashes it with SHA-256, and returns the first 16 hex characters.

**Call relations**: Synthetic event ID helpers use this when building fallback IDs. The hash makes IDs less likely to collide when visible fields are similar.

*Call graph*: 2 external calls (sha256, dumps).


##### `HubSpotConnector._paginate_association_labels`  (lines 1632–1648)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the labels that describe relationships between HubSpot object types. A label might explain what kind of link connects two records.

**Data flow**: It walks all object-type pairs that have labels, converts each label with `_association_label_row`, and yields pages of label rows.

**Call relations**: `_paginate_product_api` calls this for the `association_labels` stream. It gets candidate pairs from `_association_pairs_with_labels`.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_associations`  (lines 1650–1665)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual links between HubSpot records across object types, such as a contact linked to a company. It works pair by pair and batches source record IDs.

**Data flow**: It finds object-type pairs with labels, pages through IDs for the source object type, sends batch association reads, and yields normalized association rows.

**Call relations**: `_paginate_product_api` calls this for the `associations` stream. It uses `_association_pairs_with_labels`, `_paginate_crm_object_id_pages`, and `_paginate_association_batch`.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_association_batch`  (lines 1667–1694)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads associations for a batch of source records between two object types. It also follows per-record paging when a source record has many targets.

**Data flow**: It receives source and target object types plus input IDs. It posts a batch read request, converts the response with `_association_rows`, yields rows, then asks `_next_association_inputs` whether more pages are needed.

**Call relations**: `_paginate_associations` calls this for each page of source IDs. It uses `_is_optional_pair_unavailable` to skip unsupported object pairs.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1697–1711)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Finds follow-up association requests for records whose association results were paged. HubSpot can return a separate `after` cursor per source record.

**Data flow**: It receives a batch association response. It scans each result for a source record ID and next cursor, then returns new input dictionaries containing `id` and `after`.

**Call relations**: `_paginate_association_batch` calls this after each batch response. The returned inputs become the next batch request when more association pages exist.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1713–1726)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Finds object-type pairs that actually have association labels. This avoids trying to read relationship data for pairs HubSpot says do not exist.

**Data flow**: It fetches all known object types, tries each from-to combination, asks for labels, and yields only pairs with at least one label.

**Call relations**: `_paginate_association_labels` and `_paginate_associations` both call this. It depends on `_association_object_types` and `_association_labels_for_pair`.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1728–1741)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the list of object types to consider for associations. It starts with standard HubSpot object types and adds custom object types from the account.

**Data flow**: It receives an HTTP client. It copies the known standard object type list, tries to fetch custom schemas, extracts custom object IDs, and returns the combined list.

**Call relations**: `_association_pairs_with_labels` calls this before checking pair labels. It uses `_custom_object_schemas`, `_schema_object_type_id`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1743–1759)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches relationship labels for one pair of object types. Unsupported pairs are treated as empty rather than fatal.

**Data flow**: It receives source and target object type names. It calls HubSpot's labels endpoint, returns dictionary-shaped label rows, or returns an empty list for optional unavailable pairs.

**Call relations**: `_association_pairs_with_labels` calls this for each possible object-type pair. It uses `_is_optional_pair_unavailable` to ignore unsupported combinations.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1762–1779)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Normalizes one association label into a row with a stable ID and clear source and target object type fields.

**Data flow**: It receives a label plus from/to object type names. It extracts type ID, category, and display label, builds a combined ID, and returns an enriched row.

**Call relations**: `_paginate_association_labels` calls this for each label returned by `_association_pairs_with_labels`.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1781–1793)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Produces pages of CRM record IDs for a given object type. It is a lightweight reader used when another endpoint only needs IDs.

**Data flow**: It asks `_paginate_crm_object_pages` for pages containing `hs_object_id`, extracts each record's `id`, converts IDs to strings, and yields non-empty ID pages.

**Call relations**: `_paginate_associations` uses this to feed batch association reads. `_paginate_sequence_enrollments` uses it to check enrollments for contacts.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1795–1825)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks the basic CRM object list endpoint for a given object type and small set of properties. This is used for support tasks that do not need full search behavior.

**Data flow**: It receives an object type and property names. It requests list pages with an `after` cursor, yields valid result rows, and stops when no next cursor remains.

**Call relations**: `_paginate_crm_object_id_pages` and `_paginate_contact_identity_pages` call this. It uses `_is_optional_pair_unavailable` to skip object types or endpoints that are not available.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1828–1862)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Converts a HubSpot batch association response into one row per relationship. If one target has multiple association types, it creates one row per type.

**Data flow**: It receives raw association data and from/to object type names. It walks each source result, each target record, and each association type, then returns normalized rows.

**Call relations**: `_paginate_association_batch` calls this after each batch read. It delegates the final row shape to `_association_row`.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1865–1892)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one normalized association row connecting one source record to one target record. The row includes both IDs, object types, category, label, and a stable combined ID.

**Data flow**: It receives an association type dictionary plus source and target context. It extracts type ID, category, and label, builds an ID from all relationship parts, and returns the row.

**Call relations**: `_association_rows` uses this for each individual relationship it finds in a batch association response.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1895–1898)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether an error from an optional pair-style request should be ignored. Many HubSpot object combinations or product features simply do not exist for an account.

**Data flow**: It receives an HTTP error. It returns true for 400 or 404 responses, or when `_is_stream_unavailable` recognizes a permission limitation.

**Call relations**: Association, list membership, consent, sequence, and CRM helper methods call this to skip unsupported optional branches without failing the whole sync.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1900–1914)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads which records belong to each HubSpot list. It first discovers lists, then walks memberships for every list.

**Data flow**: It pages through lists with `_paginate_lists`, extracts each list ID, calls `_paginate_memberships_for_list`, and yields membership pages.

**Call relations**: `_paginate_product_api` calls this for the `list_memberships` stream. It combines list metadata with member rows through `_paginate_memberships_for_list`.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1916–1959)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads members for one HubSpot list and adds helpful list context to each membership row.

**Data flow**: It receives a list record and list ID. It pages through the memberships endpoint, builds rows with combined IDs and list metadata, yields pages, and stops on unsupported optional errors.

**Call relations**: `_paginate_list_memberships` calls this for each list. It uses `_is_optional_pair_unavailable` to skip lists whose memberships cannot be read.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1961–1974)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the subscription preference definitions configured in HubSpot. These describe the kinds of email subscriptions or communication preferences available.

**Data flow**: It calls the communication preference definitions endpoint, accepts either `results` or `subscriptionDefinitions`, assigns an ID to each valid row, and yields the rows.

**Call relations**: `_paginate_product_api` calls this for the `subscription_definitions` stream. Consent state rows can then be interpreted against these definitions.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_consent_states`  (lines 1976–1989)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads email consent and unsubscribe status for contacts. It uses contact email addresses because HubSpot's communication preference endpoints are email-based.

**Data flow**: It pages through contacts with emails, asks for subscription statuses and unsubscribe-all statuses for each email, combines the returned rows, and yields non-empty pages.

**Call relations**: `_paginate_product_api` calls this for the `consent_states` stream. It uses `_paginate_contact_identity_pages`, `_consent_status_rows`, and `_unsubscribe_all_rows`.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1991–2007)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Produces contact rows that include an email address. This is a lightweight feed for consent lookups.

**Data flow**: It reads contact pages with the `email` property, copies the email from either the top level or properties bag, and yields pages of contact identity rows.

**Call relations**: `_paginate_consent_states` calls this before asking HubSpot for each contact's communication preferences. It uses `_paginate_crm_object_pages`.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 2009–2030)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Reads subscription-specific consent status for one email address. It returns rows such as opted in, opted out, or related status details.

**Data flow**: It receives a contact row and email. It URL-escapes the email, calls the status endpoint, converts each result with `_consent_row`, and returns the list.

**Call relations**: `_paginate_consent_states` calls this for each contact email. It uses `_is_optional_pair_unavailable` to ignore missing optional preference data.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 2032–2056)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Reads the global unsubscribe status for one email address. This captures whether the person has opted out of all email communication.

**Data flow**: It receives a contact row and email. It URL-escapes the email, calls the unsubscribe-all endpoint, converts returned rows with `_consent_row`, and returns them.

**Call relations**: `_paginate_consent_states` calls this alongside `_consent_status_rows`. Both feed the same normalized consent stream.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 2059–2089)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Normalizes one communication preference record into a consistent consent row. It adds contact ID, subject email, purpose, status, legal basis, and timestamps.

**Data flow**: It receives a raw preference row, contact context, email, and status kind. It builds a stable ID from email, subscription or status kind, and business unit, then returns the enriched row.

**Call relations**: `_consent_status_rows` and `_unsubscribe_all_rows` both call this so the two HubSpot preference endpoints produce the same row shape.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2091–2118)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sales sequences available to HubSpot users. HubSpot requires a user ID filter, so the connector first discovers users through owners.

**Data flow**: It gets user rows from `_sequence_user_rows`, requests sequence pages for each user ID, adds owner context to each sequence row, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the `sequences` stream. It uses `_paginate_get_collection` for the sequence endpoint and `_is_optional_pair_unavailable` for inaccessible users or products.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_user_rows`  (lines 2120–2143)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a unique list of HubSpot user IDs from owner records. Sales sequence APIs are keyed by user ID, while owner records contain the mapping.

**Data flow**: It reads owners through `_paginate_get_collection`, extracts unique `userId` values, keeps owner ID and email context, and yields one page of user rows.

**Call relations**: `_paginate_sequences` calls this before querying sequences per user. It depends on the owners endpoint as the source of user identity.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2145–2163)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sales sequence enrollment data for contacts. It checks each contact ID because the endpoint is contact-specific.

**Data flow**: It pages through contact IDs, calls the enrollment endpoint for each contact, converts results with `_sequence_enrollment_rows`, and yields accumulated pages.

**Call relations**: `_paginate_product_api` calls this for the `sequence_enrollments` stream. It uses `_paginate_crm_object_id_pages`, `_sequence_enrollment_rows`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2166–2180)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes sequence enrollment responses for one contact. It handles both list-style and single-object response shapes.

**Data flow**: It receives raw enrollment data and the contact ID. It treats `results` as the row list when present, otherwise wraps the response as one row, assigns IDs, adds `contact_id`, and returns the rows.

**Call relations**: `_paginate_sequence_enrollments` calls this after each contact-specific enrollment request.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2182–2211)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads submissions for every HubSpot form. HubSpot exposes submissions under each form, so the connector must first list forms.

**Data flow**: It pages through forms, extracts each form ID, pages through that form's submissions, assigns each submission an ID, adds form ID and form name, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the `form_submissions` stream. It reuses `_paginate_get_collection` for both forms and submissions.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2213–2228)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages inside HubSpot conversation threads. Threads are listed first, then messages are fetched per thread.

**Data flow**: It pages through conversation threads, extracts each thread ID, pages through that thread's messages, adds `thread_id` to each message, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the `conversation_messages` stream. It reuses `_paginate_get_collection` for both thread and message endpoints.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipelines`  (lines 2230–2237)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads deal and ticket pipelines as normalized rows. Pipelines define the stages that records move through.

**Data flow**: It loops over supported pipeline object types, asks `_pipeline_rows_for_object_type` for rows, and yields non-empty pages.

**Call relations**: `_paginate_product_api` calls this for the `pipelines` stream. It delegates object-specific fetching and shaping to `_pipeline_rows_for_object_type`.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2239–2289)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads stages inside deal and ticket pipelines. It enriches each stage with pipeline context, status, probability, order, and closed/open information.

**Data flow**: It loops over deal and ticket pipeline types, fetches raw pipelines, walks each pipeline's stages, builds stable combined IDs, normalizes metadata, and yields stage pages.

**Call relations**: `_paginate_product_api` calls this for the `pipeline_stages` stream. It uses `_raw_pipelines_for_object_type` to get the source pipeline data.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2291–2314)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Builds normalized pipeline rows for one object type, such as deals or tickets. It adds object kind and active/archived status.

**Data flow**: It receives an object type, fetches raw pipelines, skips rows without IDs, creates combined IDs like `deals:default`, and returns the shaped rows.

**Call relations**: `_paginate_pipelines` calls this for each supported object type. It uses `_raw_pipelines_for_object_type` for the HubSpot request.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2316–2327)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches raw pipeline definitions for one HubSpot object type. Missing or forbidden pipeline endpoints are treated as empty.

**Data flow**: It receives an object type, calls the CRM pipelines endpoint, returns dictionary-shaped results, or returns an empty list for 403 and 404 responses.

**Call relations**: `_pipeline_rows_for_object_type` and `_paginate_pipeline_stages` call this. It is the low-level pipeline reader used by both pipeline streams.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2330–2334)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects a specific HubSpot limitation: some endpoints do not support paging through deleted objects. When that happens, the connector can stop the cleanup sweep quietly.

**Data flow**: It receives an HTTP error. It checks for status code 400 and looks for HubSpot's known message about deleted-object paging, then returns true or false.

**Call relations**: `_paginate_archived_ids` and `_paginate_custom_object_archived_ids` call this when archived sweeps fail. It relies on `_upstream_message` to read HubSpot's error text.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2337–2345)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Extracts HubSpot's `message` field from an HTTP error response when possible. It is a small helper for interpreting upstream errors.

**Data flow**: It receives an HTTP error, tries to parse the response as JSON, checks for a dictionary-shaped body, and returns the message string or `None`.

**Call relations**: `_is_archived_sweep_unsupported` uses this to recognize HubSpot's deleted-object paging limitation.


##### `HubSpotConnector._paginate_junction`  (lines 2347–2394)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds simple relationship rows for selected parent-child pairs, such as deal contacts or ticket companies. It reads associations embedded in parent object list responses.

**Data flow**: It receives a parent object and target object. It pages through parent records with `associations` requested, extracts target IDs from each parent, builds rows with parent and target ID fields, and yields them.

**Call relations**: `_paginate_unchecked` calls this for junction streams. These streams have no cursor, so the method full-refreshes the relationships and relies on stable IDs for idempotent upserts.

*Call graph*: called by 1 (_paginate_unchecked).


### `extensions/sources/ufo_ext_sources/providers/salesforce.py`

`io_transport` · `source sync`

Salesforce is not one single database with the same shape everywhere. Each customer’s Salesforce organization can expose different fields. This connector solves that by first asking Salesforce what fields exist for each object, instead of relying on a fixed hand-written schema. It then builds a SOQL query, which is Salesforce’s SQL-like query language, to fetch records in time order using SystemModstamp as the cursor. A cursor is like a bookmark: it lets the next sync continue from the last known change instead of reading everything again.

The file defines a list of Salesforce streams, such as accounts, contacts, tasks, and products. Each stream says which Salesforce object to read, which field is the main ID, and which timestamp marks updates. During a sync, SalesforceConnector describes the object, queries records in pages, follows Salesforce’s next-page link when more results exist, and yields each batch to the wider source system.

It also checks for deleted records after a cursor exists. That matters because a record that was hard-deleted will no longer appear in normal query results. The connector asks Salesforce’s deleted-record endpoint and emits a tombstone page, which is a page saying “these IDs should be considered gone.” If Salesforce refuses access with a 401 or 403 response, the stream is skipped with a clear explanation instead of crashing unclearly.

#### Function details

##### `_stream`  (lines 29–38)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a standard description of one Salesforce stream, such as accounts or contacts. It saves repeated setup by filling in the usual Salesforce fields for ID, creation time, and update cursor.

**Data flow**: It receives a friendly stream name, the Salesforce object name, and whether the stream is considered canonical. It puts those details into a StreamSpec along with standard Salesforce field names like Id, CreatedDate, and SystemModstamp. The result is a stream definition used later by the connector.

**Call relations**: This helper is used while the file is loaded to build the SALESFORCE_STREAMS list. It hands each stream definition to StreamSpec.__init__, which creates the structured object the rest of the source framework understands.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 78–82)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: Builds the Salesforce query used to fetch records for one stream. It includes all discovered fields and, when a cursor exists, asks only for records changed after that cursor.

**Data flow**: It receives a stream definition, a list of field names, and an optional cursor. It joins the fields into a SELECT query, adds a WHERE clause if there is a cursor, orders by the cursor field, and limits the page size. It returns the finished SOQL query string.

**Call relations**: paginate calls this after _describe_fields has found the available Salesforce columns. The resulting query is sent to Salesforce’s query endpoint so the connector can begin reading records.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 84–90)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: Asks Salesforce what fields exist on a given object. This lets the connector work across Salesforce organizations with different custom or enabled fields.

**Data flow**: It receives an HTTP client and a Salesforce object name. It calls Salesforce’s describe endpoint, reads the fields list from the response, keeps valid field names, and returns them as strings. The connector then has the full set of columns it can request.

**Call relations**: paginate calls this before building the query. Its output feeds into _build_soql, so the sync can request the fields Salesforce says are actually available.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 92–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Reads one Salesforce stream page by page and yields the results to the sync system. It also adds a deleted-record page when doing an incremental sync, so removals are not missed.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. It first gets the object’s fields, builds a SOQL query, calls Salesforce’s query API, yields any records returned, and follows nextRecordsUrl until Salesforce says the query is done. If a cursor was supplied, it then asks for records deleted since that cursor and yields a tombstone page if needed. If Salesforce responds with 401 or 403, it turns that into StreamSkipped with a human-readable reason; other HTTP errors continue upward.

**Call relations**: This is the main reading loop the source framework uses for Salesforce streams. It calls _describe_fields to learn columns, _build_soql to create the query, and _deleted_page to capture hard deletes. When access is refused, it constructs StreamSkipped so the broader sync can skip that stream cleanly.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 121–139)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: Finds Salesforce records that were hard-deleted since the last cursor and packages their IDs as delete tombstones. This prevents the local copy from keeping records that no longer exist in Salesforce.

**Data flow**: It receives an HTTP client, a stream definition, and the previous cursor. It chooses the current UTC time as the end of the deletion window, calls Salesforce’s deleted-record endpoint, extracts deleted record IDs, and chooses the next cursor from Salesforce’s latestDateCovered or the window end. It returns a StreamPage containing delete IDs and the next cursor, or nothing if there is truly no page to report.

**Call relations**: paginate calls this only after normal record fetching and only when there is an existing cursor. It creates a StreamPage that the rest of the sync system can treat as a deletion update rather than a normal record batch.

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 141–144)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Removes Salesforce’s metadata wrapper from a record before the record is stored or processed further. The useful business fields remain, while the extra attributes envelope is dropped.

**Data flow**: It receives one Salesforce record and its stream definition. If the record contains an attributes key, it returns a copy without that key. If there is no such key, it returns the record unchanged.

**Call relations**: After paginate yields Salesforce records, the source framework can call flatten as part of normal record cleanup. This keeps downstream data focused on actual Salesforce fields rather than Salesforce’s transport metadata.


### Scheduling and Forms
Readers for scheduling and form-response systems that turn bookings, invitees, forms, and submissions into searchable records.

### `extensions/sources/ufo_ext_sources/providers/calendly.py`

`io_transport` · `sync/request handling`

This connector is the read-only bridge between Calendly and the project. Calendly organizes most data under an organization, so the connector first asks Calendly who the current user is and which organization they belong to. Without that organization value, most Calendly lists cannot be fetched, so the stream is skipped rather than returning misleading empty data.

The file defines the Calendly streams the system knows about: the API user, event types, groups, organization memberships, scheduled events, and event invitees. For list-style data, Calendly returns results in pages, like a book split into chapters. The connector keeps asking for the next page using Calendly’s `next_page_token` until there are no more pages.

Some streams support incremental syncing, which means “only fetch things newer than what we already saw.” Event types use Calendly’s `updated_since` filter. Scheduled events use `min_start_time`. Invitees are fetched by first listing scheduled events, then asking for invitees for each event, and finally filtering invitees by their `created_at` time when a previous cursor exists.

At the end, `flatten` reshapes certain records so important fields like name, email, title, start time, and location are easy for the rest of the system to use. The connector does not write anything back to Calendly.

#### Function details

##### `_uuid_from_uri`  (lines 61–64)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: This helper pulls the final identifier out of a Calendly URI. Calendly often gives object references as full web-style strings, but some API calls need just the last ID part.

**Data flow**: It receives any value. If the value is a non-empty string, it trims a trailing slash if present and returns the text after the final slash. If the input is missing or not a string, it returns nothing.

**Call relations**: The invitee-fetching flow uses this when it has a scheduled event URI and needs the event’s short ID before calling Calendly’s invitees endpoint for that event.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 72–75)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: This asks Calendly for the account connected to the current credential. The connector needs this user record both as its own stream and to discover the user’s current organization.

**Data flow**: It takes an HTTP client that is already prepared to talk to Calendly. It requests `/users/me`, looks for the `resource` object in Calendly’s response, and returns that object if it is a dictionary. If the response shape is not what is expected, it returns an empty dictionary.

**Call relations**: Organization-based streams call this first so they know which Calendly organization to read from. The main `paginate` method also calls it directly when syncing the `api_user` stream.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 77–90)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Calendly list endpoint page by page. It hides the repeated work of asking for the next page token and using the standard Calendly page size.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the base REST connector to fetch pages from the response’s `collection` field, following `pagination.next_page_token` and sending that token back as `page_token`. It yields each page as a list of record dictionaries.

**Call relations**: Organization streams use this after adding the organization parameter. The invitee flow also uses it to read invitees for each scheduled event.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 92–108)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Calendly collection that belongs to the current user’s organization. It also optionally adds a cursor filter so repeated syncs can avoid rereading older data.

**Data flow**: It starts with an HTTP client, an API path, and optional cursor information. It fetches the current user, extracts `current_organization`, and stops the stream with `StreamSkipped` if Calendly did not provide one. It then adds the organization to the request parameters, adds the cursor parameter when present, reads pages from Calendly, and adds organization context to every record before yielding the page.

**Call relations**: The main `paginate` method relies on this for event types, groups, organization memberships, and scheduled events. The invitee flow also uses it first to discover scheduled events before fetching their invitees.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 110–128)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches invitees for scheduled events. Calendly does not expose invitees as one simple organization-wide list here, so the connector first finds events, then visits each event’s invitee list.

**Data flow**: It receives an HTTP client and an optional saved cursor. It reads scheduled events for the organization, extracts each event’s ID from its URI, and skips events whose URI cannot be understood. For each valid event, it fetches invitee pages. If a cursor exists, it keeps only invitees whose `created_at` value is newer than that cursor. It yields non-empty invitee pages with extra context showing which scheduled event they came from.

**Call relations**: The main `paginate` method calls this for the `event_invitees` stream. Inside, it depends on `_org_stream` to get scheduled events, `_uuid_from_uri` to form the invitee API path, `_paginate_collection` to read invitee pages, and `with_context` to attach event information to the returned records.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 130–162)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s traffic director for reading Calendly streams. Given a stream name, it chooses the right Calendly API path and the right incremental filter, then yields pages of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. For `api_user`, it fetches the current user and yields it as a one-record page. For organization-based streams, it delegates to `_org_stream` with the proper endpoint and cursor parameter when needed. For invitees, it delegates to `_invitees`. If the stream name is unknown, it raises `StreamSkipped` to say this connector does not implement it.

**Call relations**: The broader source syncing framework calls this when it wants records from a specific Calendly stream. This method then hands work to `_current_user`, `_org_stream`, or `_invitees` depending on what kind of Calendly data is being synced.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 164–204)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes Calendly records into a friendlier form for storage and search. It keeps the original data but copies or extracts important fields into predictable top-level names.

**Data flow**: It receives one Calendly record and the stream it belongs to. For user and invitee records, it makes sure name, email, and creation time are easy to find. For organization memberships, it pulls the member’s name and email out of the nested `user` object and removes that nested object from the returned record. For scheduled events, it exposes title, description, start and end times, and a simple location value. For event types, it exposes the display name, API URL, and creation time. Streams without special rules are returned unchanged.

**Call relations**: After records are fetched by `paginate`, the syncing framework can call this to normalize them before saving them. It uses `dict_or_empty` for organization memberships so a missing or malformed nested user object does not break the flattening step.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/typeform.py`

`io_transport` · `source sync`

Typeform is an online form service, and its API does not return everything in one simple answer. Some data comes in numbered pages, some data must be fetched form by form, and some data can only be fetched after first discovering which forms exist. This file hides those details behind a single connector called TypeformConnector.

The connector is read-only. It does not create or change anything in Typeform. When the sync runner asks for a stream, such as "forms" or "responses", the connector chooses the right path through the Typeform API. For ordinary collections, it walks through Typeform's numbered pages until there are no more. For responses, it first lists every form, then asks Typeform for the responses belonging to each form. It also adds helpful context, such as the form ID and title, to each response so the record still makes sense later.

The file also supports incremental syncing. That means it can use a saved timestamp-like cursor to ask only for newer data, instead of rereading everything every time. If Typeform refuses access with an authorization error, the connector marks that stream as skipped rather than crashing the whole sync.

#### Function details

##### `TypeformConnector.paginate`  (lines 52–79)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway the sync system uses to read a Typeform stream. It looks at which stream was requested and sends the work to the helper that knows how to fetch that kind of Typeform data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching helper, and yields batches of records back to the caller. If Typeform says the credentials are missing permission or invalid, it turns that into a clear "stream skipped" result instead of letting the error stop everything.

**Call relations**: The wider sync runner calls this method when it wants records from Typeform. From there, it hands off to _forms, _responses, _paged_items, or _webhooks depending on the requested stream. If the stream is not implemented, or Typeform refuses access, it raises StreamSkipped so the runner can continue safely with other work.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 81–99)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Typeform endpoints that return a simple list of items spread across numbered pages. It is the connector's reusable page-turning routine, like flipping through a catalog until the last page.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It adds a page number and page size, asks Typeform for that page, pulls the records out of the returned "items" field, and yields them as a batch. It keeps increasing the page number until Typeform reports the last page, or until a short page suggests there is nothing more.

**Call relations**: paginate uses this directly for streams such as workspaces, images, and themes. _forms also uses it to fetch the raw list of forms before applying any form-specific filtering.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 101–108)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches Typeform forms and, when asked, filters out forms that have not changed since the last sync. It is the source of form information for both the forms stream and other streams that need to know which forms exist.

**Data flow**: It receives an HTTP client and an optional cursor. It asks _paged_items to read all pages from the /forms endpoint. If a cursor is present, it keeps only forms whose last_updated_at value is newer than that cursor, then yields the remaining forms in batches.

**Call relations**: paginate calls this when the requested stream is forms. _responses and _webhooks also call it first, because they must know each form's ID before they can fetch that form's responses or webhooks.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 110–132)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches submitted answers from Typeform forms. Because Typeform responses live under each individual form, it first finds the forms and then reads responses for each one.

**Data flow**: It receives an HTTP client and an optional cursor. It reads all forms, skips any form without a usable ID, and then requests that form's responses. If a cursor is present, it sends it as a "since" filter so Typeform can return newer responses only. Each response batch is enriched with the form ID and form title before being yielded.

**Call relations**: paginate calls this for the responses stream. This helper calls _forms to discover the forms first, then uses with_context to attach form details to the response records so downstream code can understand where each response came from.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 134–143)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches webhook definitions for each Typeform form. A webhook is a saved instruction telling Typeform to notify another system when something happens, such as a new response.

**Data flow**: It receives an HTTP client. It reads all forms, skips any form without a usable ID, asks Typeform for that form's webhooks, extracts the returned items, and adds the form ID and title to each webhook record before yielding it.

**Call relations**: paginate calls this for the webhooks stream. Like _responses, it starts with _forms because webhooks are fetched per form, then uses records_at to pull the list from Typeform's response and with_context to preserve which form each webhook belongs to.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).


### Support Desk Channels
Readers for customer support platforms that normalize tickets, conversations, contacts, help content, and related support metadata.

### `extensions/sources/ufo_ext_sources/providers/freshdesk.py`

`io_transport` · `source sync, while fetching Freshdesk streams`

Freshdesk exposes many kinds of helpdesk data: tickets, ticket conversations, contacts, companies, agents, knowledge-base articles, forum discussions, and admin settings. This file turns those Freshdesk API endpoints into named streams that the larger sync system can pull from.

The main class, `FreshdeskConnector`, is read-only. It builds an HTTP client using a Freshdesk API key, then fetches records page by page. Freshdesk does not use one single paging pattern everywhere, so this file is like a guidebook for several routes through the same building. Most resources use a standard “next page” link in the response headers. Tickets use numbered pages and can be filtered by an `updated_since` time so later syncs only read changed tickets. Some resources are nested: conversations belong to tickets, solution articles belong under categories and folders, and forum comments belong under topics. The connector first fetches the parent items, then uses their IDs to fetch the children.

A key safety behavior is that Freshdesk may reject a stream with HTTP 401 or 403 when the API key is invalid or lacks permission. Instead of crashing the whole source, this connector turns that into `StreamSkipped`, meaning “this particular stream cannot be read with the current access.”

#### Function details

##### `_stream`  (lines 55–69)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a small stream definition for one Freshdesk resource. A stream definition tells the rest of the system the stream’s name, the Freshdesk object it maps to, its unique record key, and whether it has a time-based cursor for incremental syncing.

**Data flow**: It receives a stream name plus optional details such as a source object name, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults, then returns a `StreamSpec`, which is the system’s compact description of a readable data stream.

**Call relations**: This helper is used while building the module-level Freshdesk stream list. Each call produces one entry that `FreshdeskConnector` later advertises as something it can sync.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector._make_client`  (lines 109–124)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to a Freshdesk tenant. It applies the correct base URL, timeouts, JSON headers, and authentication method.

**Data flow**: It receives a Freshdesk base URL and a resolved credential. If the credential already contains a custom transport, it preserves that transport. Otherwise, if it has a direct API key, it creates Freshdesk’s required HTTP Basic authentication using the key as the username and `X` as the password. It returns an asynchronous HTTP client ready to make requests.

**Call relations**: The wider connector framework calls this when it is time to open a connection to Freshdesk. The client it returns is then passed into `paginate` and the lower-level paging helpers so they can make API calls.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 127–137)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: Prepares the query settings for one page of Freshdesk tickets. This keeps ticket fetching consistent, including page size, sorting order, included details, and optional incremental filtering.

**Data flow**: It receives an optional cursor time and a page number. It builds a dictionary of request parameters: 100 records per page, sorted by update time from oldest to newest, with extra ticket details included. If a cursor is present, it adds `updated_since` so Freshdesk only returns tickets updated after that point.

**Call relations**: `_paginate_tickets` calls this before each ticket request. The result becomes the query string sent to Freshdesk’s ticket endpoint.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 139–213)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right fetching strategy for each Freshdesk stream. It is the main dispatcher that turns a requested stream name into the correct sequence of API calls.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. It checks the stream name, then delegates to the matching paging helper: tickets, conversations, nested two-level resources, nested three-level resources, or ordinary link-header pagination. It yields lists of records as pages. If Freshdesk refuses access with a 401 or 403 response, it converts that into a skipped stream message.

**Call relations**: The sync framework calls this whenever it wants records for one Freshdesk stream. `paginate` then calls the more specialized helper methods and passes their record pages back up to the framework.

*Call graph*: calls 6 internal fn (__init__, _paginate_conversations, _paginate_link_header, _paginate_three_level, _paginate_tickets, _paginate_two_level).


##### `FreshdeskConnector._paginate_link_header`  (lines 215–222)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Freshdesk resources that use a standard “next page” link in the HTTP response headers. This is the common paging path for many Freshdesk endpoints.

**Data flow**: It receives an HTTP client, an API path, and optional request parameters. It asks the base REST connector to follow Freshdesk’s link headers with a page size of 100, then yields each returned page of records unchanged.

**Call relations**: `paginate` uses this for simple streams. The nested helpers also use it whenever they need to fetch parent or child pages, so it acts as the shared paging tool for most non-ticket endpoints.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 224–241)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Freshdesk tickets using Freshdesk’s ticket-specific numbered paging rules. It also supports incremental syncing by requesting tickets updated after a stored cursor time.

**Data flow**: It starts at page 1 and repeatedly builds ticket request parameters, fetches the ticket page, and yields the records. It stops when Freshdesk returns no records, when the page is shorter than the maximum page size, or when it reaches Freshdesk’s 300-page ticket limit.

**Call relations**: `paginate` calls this for the `tickets` stream. `_paginate_conversations` also calls it first, because conversations are found by walking through the tickets that may have changed.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, paginate).


##### `FreshdeskConnector._paginate_conversations`  (lines 243–259)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches ticket conversations by first finding the relevant tickets, then asking Freshdesk for the conversations under each ticket. This is needed because conversations are not fetched as one flat global list here.

**Data flow**: It receives an HTTP client and optional cursor. It reads ticket pages through `_paginate_tickets`, takes each ticket ID, fetches that ticket’s conversation pages, and adds the `ticket_id` to each conversation if it is not already present. It yields each conversation page after that small safety stamp.

**Call relations**: `paginate` calls this for the `conversations` stream. It depends on `_paginate_tickets` to discover ticket IDs and on `_paginate_link_header` to walk the conversation pages for each ticket.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (paginate).


##### `FreshdeskConnector._paginate_two_level`  (lines 261–272)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches resources arranged as parent items with child lists underneath them. Examples include folders with responses, categories with forums, forums with topics, and topics with comments.

**Data flow**: It receives a parent API path and a child path pattern. It reads pages of parents, extracts each parent’s ID, fills that ID into the child path, then yields the child pages returned from Freshdesk. Parents without usable IDs are skipped.

**Call relations**: `paginate` calls this for several nested streams that have exactly one parent-child step. It uses `_paginate_link_header` for both the parent list and each child list.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


##### `FreshdeskConnector._paginate_three_level`  (lines 274–297)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches resources buried under two layers of parents. In this file, it is used for solution articles, which live under solution categories and then solution folders.

**Data flow**: It receives a root path, a middle-level path pattern, and a leaf-level path pattern. It reads root records, extracts each root ID, reads the middle records below it, extracts each middle ID, then reads and yields the final leaf pages. Any item without an ID is ignored because the next URL cannot be built.

**Call relations**: `paginate` calls this when a stream needs a three-step tree walk. The method repeatedly uses `_paginate_link_header` to move from root pages to middle pages to final record pages.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/intercom.py`

`io_transport` · `during Intercom source sync`

Intercom does not expose all of its data in one simple way. Some data is found through a search endpoint, some through a scrolling company endpoint, some through one-shot list endpoints, and some only by first reading a parent item and then asking for its children. This file is the adapter that hides those differences from the rest of the project.

It defines the Intercom streams the system knows about, such as conversations, contacts, companies, conversation parts, company segments, and activity logs. Each stream says what kind of Intercom object it represents, what field uniquely identifies a record, and what field can be used as a time cursor for incremental syncing.

The main class, `IntercomConnector`, builds an authenticated HTTP client, adds the required Intercom API version header, and chooses the right paging method for each stream. Think of it like a tour guide who knows which door to use for each room in a large building. Search-based streams get POST requests with cursor filters. Companies use Intercom’s scroll token. Lists are fetched once. Child streams fetch parent records first, then ask Intercom for the related children.

The file also flattens a few nested Intercom fields into easier top-level fields. That matters because later SQL-style processing can work with simple column names more easily than deeply nested JSON. If Intercom refuses access with a 401 or 403 response, the stream is marked as skipped rather than crashing the whole run.

#### Function details

##### `_stream`  (lines 52–66)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a stream description for one kind of Intercom data. A stream description tells the sync system what the stream is called, which Intercom object it maps to, what field identifies records, and which field can be used to resume later.

**Data flow**: It receives a stream name and optional settings such as the source object name, primary key, cursor field, and whether it is a standard stream. It fills in sensible defaults, then returns a `StreamSpec`, which is the project’s small record describing how to sync that stream.

**Call relations**: This helper is used while the file is being loaded to build the `INTERCOM_STREAMS` list. It hands each finished stream description to `StreamSpec.__init__`, so the connector can later advertise all supported Intercom streams.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector._make_client`  (lines 103–106)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Intercom and adds the Intercom API version header. Without this header, Intercom may answer using a different API version than the connector expects.

**Data flow**: It receives a base URL and a credential. It asks the parent REST connector to create the authenticated client, adds `Intercom-Version: 2.11` to the client’s headers, and returns that ready-to-use client.

**Call relations**: This is part of connector setup. The broader REST connector machinery calls it when preparing network access, and all later pagination methods use the returned client for their Intercom requests.


##### `IntercomConnector._build_search_body`  (lines 109–139)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the request body for Intercom endpoints that use the search API. It tells Intercom how many records to return, where to continue within a page sequence, and which updated records to include.

**Data flow**: It receives a stream description, the saved cursor from the previous sync, and an optional `starting_after` token from Intercom. It creates a JSON body with pagination settings, sorting by the cursor field, and a query like “updated_at is greater than this cursor.” It returns that body for a POST request.

**Call relations**: `IntercomConnector._paginate_search` uses this for normal search streams such as conversations, contacts, and tickets. `IntercomConnector._paginate_conversation_parts` also uses it to find conversations before fetching their parts.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 142–147)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: Safely picks the first dictionary-shaped item from a list. It is used when Intercom wraps related objects, such as contacts or companies, inside nested lists.

**Data flow**: It receives any value. If the value is a non-empty list and its first item is a dictionary, it returns that first dictionary. Otherwise, it returns nothing.

**Call relations**: This helper supports the flattening functions. Those functions use it when they need one related object, such as the first contact on a conversation or the first company on a contact.


##### `IntercomConnector._flatten_conversation`  (lines 150–167)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes important nested conversation fields easier to use later. It copies source details and the first requester contact ID onto simple top-level keys.

**Data flow**: It receives one Intercom conversation record. It copies the record, looks inside the nested `source` object for type, subject, and body, and places those values under flat names like `source__subject`. It also looks for the first related contact and saves that contact’s ID as `requester_id`. It returns the enriched copy.

**Call relations**: `IntercomConnector.flatten` calls this only for the `conversations` stream. It relies on `_first` to safely read the first related contact from Intercom’s nested contact envelope.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 170–179)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes the author of a conversation part easy to query. Instead of requiring later code to dig into an `author` object, it exposes the author type and ID as simple fields.

**Data flow**: It receives one conversation-part record. It copies the record, checks whether there is an `author` dictionary, and if so adds `author_type` and `author_id`. It returns the copied record with those extra fields.

**Call relations**: `IntercomConnector.flatten` calls this for the `conversation_parts` stream. The pagination step has already added the parent `conversation_id`, and this function deliberately leaves that value alone.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 182–190)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a simple organization ID to contact records when Intercom includes related company information. This makes contact-to-company relationships easier for later processing.

**Data flow**: It receives one contact record. It copies the record, looks inside the nested `companies` envelope, takes the first related company if present, and writes its ID to `org_id`. It returns the copied record with that added field.

**Call relations**: `IntercomConnector.flatten` calls this only for the `contacts` stream. It uses `_first` to avoid errors when the company list is missing, empty, or not shaped as expected.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 192–206)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Prepares raw Intercom records for the rest of the sync system by making selected nested fields flat and by converting numeric cursor values to strings. The cursor conversion matters because this adapter stores watermarks as strings.

**Data flow**: It receives a record and the stream it came from. Depending on the stream name, it sends the record through the matching flattening helper for conversations, conversation parts, or contacts. If the stream has a cursor field and that field is an integer, it returns a copy where that cursor value is written as a decimal string; otherwise it returns the record as-is or with only the flattening changes.

**Call relations**: This is the common cleanup step after records are fetched. It calls `_flatten_conversation`, `_flatten_conversation_part`, or `_flatten_contact` when those stream-specific adjustments are needed.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 208–252)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct way to page through Intercom data for each stream. This is the central traffic director that hides Intercom’s many paging styles from the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and the current cursor. It checks the stream name and delegates to the matching pagination method. As those methods produce lists of records, it yields those lists onward. If Intercom answers with 401 or 403, meaning the credential is not allowed to read that stream, it raises `StreamSkipped` so the run can record a skip instead of failing everything.

**Call relations**: The sync engine calls this when it wants pages for a stream. It hands search streams to `_paginate_search`, companies to `_paginate_scroll`, list streams to `_paginate_list`, attribute streams to `_paginate_attributes`, conversation parts to `_paginate_conversation_parts`, company segments to `_paginate_company_segments`, and activity logs to `_paginate_activity_logs`.

*Call graph*: calls 8 internal fn (__init__, _paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search).


##### `IntercomConnector._paginate_search`  (lines 254–274)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads streams that use Intercom’s search API, such as conversations, contacts, and tickets. It keeps asking for the next page until Intercom says there are no more pages.

**Data flow**: It receives the HTTP client, stream description, and saved cursor. For each loop, it builds a search request body, sends a POST request to the right search path, pulls records out of the correct response key, and yields them as a page. It then reads Intercom’s `starting_after` token and either continues with that token or stops.

**Call relations**: `IntercomConnector.paginate` calls this for streams listed in the search-path table. This function calls `_build_search_body` each time it needs the next Intercom search request.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_scroll`  (lines 276–290)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads companies through Intercom’s scroll API. A scroll API is like being given a bookmark after each batch so you can continue from the same place next time.

**Data flow**: It receives the HTTP client. It starts without a scroll token, requests `/companies/scroll`, yields the returned company records, then repeats using the returned `scroll_param`. It stops when Intercom returns no records or no next scroll token.

**Call relations**: `IntercomConnector.paginate` calls this for the `companies` stream. The pages it yields are then treated like normal company record batches by the rest of the sync flow.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_list`  (lines 292–304)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple Intercom list endpoints that return all their records in one response, such as admins, tags, teams, and segments. These streams do not need multi-page cursor logic here.

**Data flow**: It receives the HTTP client and stream description. It looks up the right endpoint, performs one GET request, checks whether records are under the stream name or under `data`, and yields the list if it is present and non-empty.

**Call relations**: `IntercomConnector.paginate` calls this for streams listed in the list-path table. It does not call further pagination helpers because these endpoints are treated as single-response lists.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_attributes`  (lines 306–315)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Intercom data attributes for companies or contacts. Data attributes are custom fields defined in Intercom for a certain model, such as company or contact.

**Data flow**: It receives the HTTP client and stream description. It maps the stream name to the Intercom model name, requests `/data_attributes` with that model as a parameter, extracts the `data` list, and yields it if there are records.

**Call relations**: `IntercomConnector.paginate` calls this for `company_attributes` and `contact_attributes`. It supplies attribute definitions as ordinary record pages to the surrounding sync process.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_conversation_parts`  (lines 317–349)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the messages or events inside conversations. Intercom does not provide these as a simple top-level stream here, so this function first finds conversations and then fetches each conversation’s detailed parts.

**Data flow**: It receives the HTTP client and saved cursor. It searches conversations page by page using the same cursor logic as the conversations stream. For each conversation with an ID, it fetches `/conversations/{id}`, extracts the nested conversation parts, stamps each part with the parent `conversation_id`, and yields the parts when any exist. It continues through search pages until there is no next token.

**Call relations**: `IntercomConnector.paginate` calls this for the `conversation_parts` stream. It calls `_build_search_body` to find the parent conversations, then performs extra detail requests so child records can be synced as their own stream.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_company_segments`  (lines 351–375)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the segments attached to each company. Because these segment memberships are found under individual companies, the function walks through companies first and then asks for each company’s segments.

**Data flow**: It receives the HTTP client. It scrolls through companies using `/companies/scroll`, and for every company with an ID it requests `/companies/{id}/segments`. It adds the parent `company_id` to each segment record and yields segment lists when present. It stops when the company scroll has no records or no next token.

**Call relations**: `IntercomConnector.paginate` calls this for the `company_segments` stream. It combines company scrolling with per-company segment requests so downstream code can see company-segment links as standalone records.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_activity_logs`  (lines 377–399)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads admin activity logs from Intercom, optionally starting after a saved creation-time cursor. These logs use their own next-page link style rather than the search or scroll styles.

**Data flow**: It receives the HTTP client and optional cursor. If a cursor is present, it sends it as `created_at_after` on the first request to `/admins/activity_logs`. For each response, it yields any `activity_logs`, then follows the `pages.next` link if Intercom provides one. If the next link is a full URL, it strips the base URL so the client can request the path.

**Call relations**: `IntercomConnector.paginate` calls this for the `activity_logs` stream. It owns the special next-link loop for that stream and yields record pages back to the same outer sync flow as the other paginators.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/zendesk.py`

`io_transport` · `source sync`

Zendesk exposes different kinds of data through different web API patterns. Some large objects, like tickets and users, use an incremental feed: the connector asks for everything changed since a time and follows a “next” link until Zendesk says there is no more. Other objects use ordinary page-by-page lists. A few need special treatment, such as ticket comments, which are hidden inside ticket event records, and user identities, which require a separate lookup for each user.

This file gathers those differences behind one connector class, `ZendeskConnector`. The rest of the system can simply ask for a named stream, and this file chooses the right way to fetch it. The list `ZENDESK_STREAMS` describes the available Zendesk streams and their key fields, so the sync runner knows what can be read and how to track progress.

The code also adds small conveniences that make the captured data more useful. For tickets, Zendesk can return related users in the same response; `_apply_sideload` copies requester, submitter, and assignee email addresses onto each ticket. If Zendesk refuses access with a permission or authentication error, the connector raises `StreamSkipped`, meaning this particular stream should be skipped rather than crashing the whole run.

#### Function details

##### `_stream`  (lines 58–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a description of one Zendesk data stream, such as tickets or users. It keeps the large stream list readable by filling in common defaults like the primary key and timestamp fields.

**Data flow**: It receives a stream name and optional details, such as the Zendesk API object name or which field acts as the update cursor. It combines those details with defaults and returns a `StreamSpec`, which is a small record describing how the sync system should treat that stream.

**Call relations**: This helper is used while the file is loaded to build `ZENDESK_STREAMS`. Each call hands a ready-made stream description to the connector class so later sync runs know which Zendesk objects are available.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 142–167)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: This function enriches records with information that Zendesk returned alongside them. In practice, it copies user email addresses from a side list onto ticket records so each ticket is easier to understand on its own.

**Data flow**: It receives the main records, the full Zendesk response page, and instructions for which ID fields should be matched. It builds a lookup table from the side-loaded data, then walks each record and fills missing target fields such as requester email. It changes the records in place and returns nothing.

**Call relations**: The incremental ticket fetcher calls this after downloading a page that includes both tickets and related users. It acts like a clerk matching ticket forms to a separate address book before the ticket batch is passed onward.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 176–177)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This function decides which field in a Zendesk API response contains the records for a stream. Most streams use their own name, but some Zendesk endpoints use different names like `audits` or `policies`.

**Data flow**: It receives a stream description. It checks the override table for a special response field name; if none exists, it uses the stream name itself. It returns the field name to read from the response JSON.

**Call relations**: The default paginator uses this before reading each ordinary Zendesk page. This keeps endpoint quirks in one place instead of scattering special cases through the paging loop.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 180–192)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: This function converts a saved sync position into the Unix timestamp format Zendesk expects. A Unix timestamp is a count of seconds since January 1, 1970.

**Data flow**: It receives a cursor value, which may be missing, already numeric, or an ISO date string such as `2024-01-01T00:00:00Z`. Missing or unrecognized values become `0`, meaning start from the beginning. Valid dates are parsed, given UTC time if needed, and returned as an integer timestamp.

**Call relations**: The incremental ticket/user/organization fetcher, the ticket comment fetcher, and the user identity fetcher call this when building their first request. It translates the system’s stored progress into Zendesk’s required `start_time` parameter.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 195–204)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: This function turns Zendesk’s full next-page URL into just the path and query string needed for the connector’s HTTP helper. It is a small safety step that keeps requests relative to the configured Zendesk tenant.

**Data flow**: It receives a `next_page` or `after_url` string from Zendesk. If the value is empty or has no path, it returns nothing. Otherwise it parses the URL, keeps the path and query text, and returns that relative request path.

**Call relations**: Every paging method uses this after reading a page. Zendesk tells the connector where to go next, and this helper reshapes that pointer into the form used by the shared request method.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 206–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry point for reading one Zendesk stream. It chooses the right paging strategy for the requested stream and yields batches of records as they arrive.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing where the last sync stopped. Based on the stream name, it delegates to the special ticket comment reader, the user identity reader, an incremental cursor reader, or the ordinary page reader. It yields lists of records, and if Zendesk returns a permission refusal, it turns that into `StreamSkipped`.

**Call relations**: The wider sync runner calls this when it wants data from a Zendesk stream. `paginate` is the dispatcher: it sends each stream to the correct internal method and protects the run from expected 401 or 403 access failures by marking only that stream as skipped.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 232–253)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads high-volume Zendesk streams using Zendesk’s incremental cursor API. This is used for data where asking for every page from the beginning would be too slow or wasteful.

**Data flow**: It receives an HTTP client, a stream description, and a cursor. It converts the cursor to a start time, builds the first incremental request, downloads each page, optionally enriches tickets with side-loaded user emails, yields any records found, and follows Zendesk’s next link until the stream ends.

**Call relations**: `paginate` sends streams like tickets, users, organizations, and ticket metric events here. This method relies on `_cursor_to_unix` to start in the right place, `_apply_sideload` to improve ticket records, and `_next_page_path` to move through Zendesk’s cursor pages.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 255–265)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Zendesk streams that use the normal page-by-page API style. It is the general path for many admin, Help Center, and community resources.

**Data flow**: It receives an HTTP client and a stream description. It builds the first `/api/v2/...` request with a page size, finds the response field that contains the records, yields non-empty record batches, and follows `next_page` links until there are no more.

**Call relations**: `paginate` uses this for streams that do not need a special incremental or fan-out flow. It asks `_data_field` where records live in each response and `_next_page_path` how to request the next page.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 267–299)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function extracts ticket comments from Zendesk’s ticket event feed. Zendesk does not expose these comments in the same simple shape as many other resources, so this method turns nested comment events into standalone rows.

**Data flow**: It receives an HTTP client and a cursor. It requests incremental ticket events with comment events included, scans each event’s child events, keeps only children whose event type is `Comment`, copies them into new records, adds the parent ticket ID, normalizes numeric creation times into readable UTC date strings, and yields batches of comments.

**Call relations**: `paginate` calls this only for the `ticket_comments` stream. It uses `_cursor_to_unix` to begin at the right time and `_next_page_path` to keep following Zendesk’s event feed until the end.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate); 1 external calls (fromtimestamp).


##### `ZendeskConnector._paginate_user_identities`  (lines 301–326)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads identity records for users, such as login or contact identities, by first finding changed users and then asking Zendesk for each user’s identities. It exists because identities are not returned as one simple global list.

**Data flow**: It receives an HTTP client and a cursor. It pages through incrementally changed users, skips malformed users or users without an ID, then for each valid user requests that user’s identities page by page. Whenever identities are found, it yields them as a batch.

**Call relations**: `paginate` routes the `users_identities` stream here. This method uses `_cursor_to_unix` to decide which users to inspect and `_next_page_path` for both the user list and each user’s identity pages.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).
