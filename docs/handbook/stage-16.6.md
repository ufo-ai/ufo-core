# Customer support, CRM, marketing, ads, and observability source connectors  `stage-16.6`

This stage is a set of read-only “source connectors.” A connector is an adapter that knows how to talk to another company’s web service through its API, the doorway that software uses to request data. These files sit behind the scenes during syncing. They do not run the product’s main work themselves, and they do not change outside systems. They fetch pages of records and reshape them into a common form the rest of the codebase can store, search, or recall.

The CRM connectors cover ActiveCampaign, Attio, HubSpot, and Salesforce, pulling contacts, companies, deals, tasks, cases, and related activity. The advertising and social connectors read Facebook Ads, Google Ads, and Instagram accounts, campaigns, posts, stories, and performance metrics. Freshdesk, Intercom, and Zendesk bring in support tickets, users, conversations, help articles, and community data. Klaviyo and Mailchimp read marketing audiences, campaigns, events, and reports. Typeform collects forms and responses. PagerDuty brings in incidents, services, schedules, and on-call data. Sentry reads error-tracking projects, issues, events, and releases. Together they act like many intake pipes feeding one shared sync system.

## Files in this stage

### CRM and sales records
Connectors for reading customer, company, deal, contact, and sales activity data from CRM-oriented platforms.

### `extensions/sources/ufo_ext_sources/active_campaign.py`

`io_transport` · `during source sync, while reading ActiveCampaign streams`

ActiveCampaign has many kinds of data: contacts, lists, campaigns, deals, accounts, tags, custom fields, and more. This file is the connector for reading those objects through ActiveCampaign's version 3 web API. Without it, the larger system would not know which ActiveCampaign collections exist, what URL to call for each one, how to authenticate, or how to move through long result lists page by page.

The file starts by defining the available streams. A stream is one kind of record to sync, such as contacts or campaigns. Each stream says what its main ID field is, which timestamp can be used to continue later from where a previous sync stopped, and whether it is one of the main built-in objects.

ActiveCampaign returns lists in a repeated shape: a named envelope containing records, plus paging information. The connector maps each stream name to the URL segment and response key ActiveCampaign expects. Most names match, but some use camelCase, so the map avoids guessing.

When syncing, the connector builds an HTTP client with ActiveCampaign's required `Api-Token` header. Then it requests records in pages of 100. If the stream supports server-side incremental filtering, it asks ActiveCampaign for only records changed after the saved cursor. If ActiveCampaign rejects a stream with a permission or authentication error, the connector skips that stream with a clear message rather than stopping the whole sync unnecessarily.

#### Function details

##### `_stream`  (lines 98–114)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates one stream definition for an ActiveCampaign object. It keeps the long stream list readable by filling in common defaults, such as using `id` as the main key and `cdate` as the creation time field.

**Data flow**: It takes a stream name and optional details like the source object name, primary key, cursor field, and whether the stream is canonical. It combines those with ActiveCampaign-specific defaults, decides whether the cursor field should also count as the updated-at field, and returns a `StreamSpec`, which is the system's description of one syncable collection.

**Call relations**: This function is used while the module is loaded to build the ActiveCampaign stream catalog. It hands each prepared set of stream facts to `StreamSpec`, so the rest of the connector can later use those stream definitions during sync.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 176–181)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method creates the HTTP client used to talk to ActiveCampaign, with the authentication format ActiveCampaign expects. ActiveCampaign uses an `Api-Token` header rather than the more common bearer-token style.

**Data flow**: It receives a base URL and a resolved credential. If the credential already has a special transport, such as a broker or proxy connection, it leaves that path alone and asks the parent connector to build the client. Otherwise it reads the API key from the credential's bearer value, wraps it into an `Api-Token` header, and returns an asynchronous HTTP client ready to make requests. If no key is present, it raises an error immediately instead of making failing network calls.

**Call relations**: The broader REST connector setup calls this when it needs a client for a sync run. This method adapts the generic credential shape into ActiveCampaign's specific authentication shape, then delegates the actual client construction back to the shared REST connector machinery.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 184–185)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This small lookup method finds the ActiveCampaign URL path and response envelope name for a given stream. It matters because some stream names in this project use snake_case, while ActiveCampaign's API sometimes expects camelCase names.

**Data flow**: It receives a stream definition. It looks up that stream's name in the path mapping. If a special mapping exists, it returns the mapped URL segment and response key; if not, it falls back to using the stream name for both.

**Call relations**: The pagination method calls this before making requests. It gives pagination the exact path to call and the exact key to read from the JSON response, so the network-reading loop does not need to know about ActiveCampaign's naming quirks.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 187–211)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one ActiveCampaign stream page by page and yields batches of records. It is the main read path for the connector.

**Data flow**: It receives an HTTP client, a stream definition, and an optional saved cursor from a previous sync. It resolves the stream's API path, starts with a page size of 100, and, when supported, adds a filter asking ActiveCampaign for records changed after the cursor. It then asks the shared REST paging helper to fetch offset-based pages and yields each page of records. If ActiveCampaign responds with 401 or 403, meaning unauthorized or forbidden, it turns that into a `StreamSkipped` error with a clear explanation; other HTTP errors are allowed to continue upward.

**Call relations**: During a sync, the surrounding REST connector calls this method for each stream it wants to read. `paginate` first calls `_resolve_stream_segment` to translate the stream into ActiveCampaign API terms, then hands the actual repeated page fetching to the shared offset-page helper. If the API refuses access to a stream, it creates a `StreamSkipped` signal so the sync system can treat that stream as unavailable rather than as ordinary data.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### `extensions/sources/ufo_ext_sources/attio.py`

`io_transport` · `source sync`

Attio’s API does not return every kind of data in the same shape. Companies, people, and deals are fetched one way; tasks and notes another way; meetings and call recordings use a newer paging style; transcripts for call recordings require extra requests. This file is the adapter that hides those differences.

The connector lists the streams it can read, such as companies or meetings, and marks them as full snapshots. A full snapshot means each sync asks Attio for the whole stream and later removes local rows that disappeared upstream. That matters because Attio does not provide one shared “last changed” field that could safely support incremental syncing.

The most important job here is flattening. Attio often stores a record’s real ID inside a nested `id` object and stores field values inside arrays of small “value cells.” Without flattening, the system would not have a clear top-level key like `record_id`, `task_id`, or `call_recording_id`. The connector pulls those IDs up, turns value cells into simple text, numbers, dates, or lists, and adds helpful fields like `email`, `domain`, or joined transcript text.

It also knows when to skip a stream instead of failing the whole sync, such as when an Attio workspace has disabled a standard object or when the OAuth permission grant is missing a required scope.

#### Function details

##### `_records_stream`  (lines 35–43)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates the standard description for an Attio object stream, such as companies, people, or deals. This description tells the sync system what the stream is called, what Attio object it reads, and which field uniquely identifies each record.

**Data flow**: It takes a friendly stream name, an Attio object slug, and whether the stream is canonical. It fills in a `StreamSpec`, including `record_id` as the primary key and `delete_missing` as true, then returns that stream description.

**Call relations**: This helper is used while building the file’s list of Attio streams. It hands stream metadata to `StreamSpec`, which the connector later uses when choosing API paths and flattening records.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 70–71)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: Safely pulls one named ID out of a nested dictionary. It exists because Attio sometimes hides useful identifiers inside an `id` object.

**Data flow**: It receives any value and a key name. If the value is a dictionary, it returns the value under that key; otherwise it returns nothing.

**Call relations**: It is called from `AttioConnector._value_primitive` when an Attio option or status has no display title and the connector needs to fall back to its internal ID.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 80–81)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: Builds the request body used when asking Attio for a page of object records. It keeps the page size consistent with Attio’s limit.

**Data flow**: It receives an offset, meaning how many records have already been read. It returns a small dictionary with the fixed limit and that offset.

**Call relations**: `AttioConnector.paginate` calls this each time it requests another page from the standard object records endpoint.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._value_primitive`  (lines 84–127)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: Turns one Attio value cell into the simplest useful value, such as text, a number, an email address, a domain, a selected option title, or a referenced record ID. This is the core translator between Attio’s detailed API shape and plain records.

**Data flow**: It receives one value-cell dictionary from Attio. It checks the known places where Attio may store the real value, including `value`, `option`, `status`, `email_address`, `phone_number`, location parts, and record references. It returns a simple value, or nothing if it cannot find one.

**Call relations**: Flattening helpers rely on this function whenever they need to simplify Attio field values. When an option or status needs an ID fallback, it calls `_nested_id`.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 130–145)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: Simplifies one Attio field cell into a single usable value, or sometimes a list for multi-select options. It keeps normal fields easy to store and search.

**Data flow**: It receives a cell that may be a list, a dictionary, or an already-simple value. For lists, it extracts primitives from each item and removes empty values; for dictionaries, it extracts one primitive; for other values, it returns them as-is.

**Call relations**: This helper is part of the record-flattening pipeline. `AttioConnector._flatten_values` uses it for most fields that should become one value rather than a list.


##### `AttioConnector._flatten_list_cell`  (lines 148–155)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: Simplifies an Attio field cell into a list of useful values. It is used for fields where multiple values are expected, such as email addresses or phone numbers.

**Data flow**: It receives a cell that may already be a list or may be a single value. It extracts simple values, drops empty ones, and returns a list every time.

**Call relations**: This helper supports `AttioConnector._flatten_values` for fields that should stay as lists instead of being reduced to just one value.


##### `AttioConnector._flatten_values`  (lines 158–192)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Walks through all the Attio attributes on a record and turns them into normal top-level fields. It also creates convenient shortcut fields like `email`, `phone`, `domain`, and separate first and last names.

**Data flow**: It receives Attio’s `values` dictionary, where each field is stored as a nested cell. It flattens each cell, keeps certain multi-value fields as lists, and then derives friendly first-value shortcuts. It returns a plain dictionary of fields.

**Call relations**: This function is used by `AttioConnector._flatten_record` to convert standard and custom Attio object records into the shape expected by the rest of the sync system.


##### `AttioConnector._flatten_record`  (lines 195–209)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Flattens a standard Attio object record, such as a company, person, or deal. It pulls the record identity to the top level and adds all flattened attribute values.

**Data flow**: It receives a raw Attio record and the stream description. It reads nested ID fields, creation and update times, optionally copies a cursor field if one exists, then merges in the flattened `values` attributes. It returns one plain record dictionary.

**Call relations**: `AttioConnector.flatten` calls this for streams that are not tasks, notes, meetings, or call recordings. It delegates the attribute cleanup to `AttioConnector._flatten_values`.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 212–216)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares an Attio task record by adding a clear top-level `task_id`. This gives the sync system a stable key for storing and replacing tasks.

**Data flow**: It receives a raw task record, copies it, reads the task ID from the nested `id` object when present, and writes that value as `task_id`. It returns the copied and updated record.

**Call relations**: `AttioConnector.flatten` calls this when the current stream is `tasks`.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 219–223)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares an Attio note record by adding a clear top-level `note_id`. This makes notes identifiable in the same straightforward way as other synced rows.

**Data flow**: It receives a raw note record, copies it, reads the note ID from the nested `id` object when present, and writes that value as `note_id`. It returns the copied and updated record.

**Call relations**: `AttioConnector.flatten` calls this when the current stream is `notes`.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 226–230)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares an Attio meeting record by adding a top-level `meeting_id`. This lets meetings be stored and updated by a stable key.

**Data flow**: It receives a raw meeting record, copies it, reads the meeting ID from the nested `id` object when present, and writes that value as `meeting_id`. It returns the copied and updated record.

**Call relations**: `AttioConnector.flatten` calls this when the current stream is `meetings`.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 233–254)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares an Attio call recording record by adding a top-level `call_recording_id` and turning transcript segments into readable text. It also falls back to a web URL when no direct recording URL is present.

**Data flow**: It receives a raw call recording, copies it, extracts the recording ID, fills `recording_url` from `web_url` when needed, and joins transcript segments into a newline-separated `transcript_text`. It returns the enriched record.

**Call relations**: `AttioConnector.flatten` calls this for the `call_recordings` stream, after pagination has already attached meeting context and fetched transcript data where available.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 256–265)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening method for the stream currently being synced. It is the single doorway the wider connector framework can use without knowing Attio’s many record shapes.

**Data flow**: It receives one raw Attio record and its stream description. It checks the stream name and sends the record to the matching specialized flattener, or to the general object-record flattener. It returns a plain dictionary ready for storage.

**Call relations**: The sync framework calls this after records are fetched. It hands work off to `_flatten_task`, `_flatten_note`, `_flatten_meeting`, `_flatten_call_recording`, or `_flatten_record` depending on the stream.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 267–321)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches all pages for one Attio stream, using the correct Attio API style for that stream. This is where the connector decides whether to use offset paging, cursor paging, object queries, or the special call-recording fan-out.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. Based on the stream name, it requests pages from the right endpoint and yields lists of raw records. If Attio says a stream cannot be read because an object is disabled or a permission is missing, it raises `StreamSkipped` so the sync can continue with other streams.

**Call relations**: This is the main read path for the connector. It calls `_paginate_simple` for tasks and notes, `_paginate_cursor` for meetings, `_paginate_call_recordings` for call recordings, and `_build_query_body` plus direct POST requests for standard objects. It uses `_is_object_disabled`, `_is_scope_unauthorized`, and `_scope_skip_reason` to turn expected Attio limitations into clean stream skips.

*Call graph*: calls 8 internal fn (__init__, _build_query_body, _is_object_disabled, _is_scope_unauthorized, _paginate_call_recordings, _paginate_cursor, _paginate_simple, _scope_skip_reason).


##### `AttioConnector._paginate_simple`  (lines 323–330)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use basic offset paging, where each request says how many items to return and how many to skip. It is used for tasks and notes.

**Data flow**: It receives an HTTP client, an endpoint path, and a page size. It asks the base REST connector for offset-based pages from the `data` field and yields each page of records.

**Call relations**: `AttioConnector.paginate` calls this for `/v2/tasks` and `/v2/notes`, keeping their simpler paging style separate from other stream types.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._paginate_cursor`  (lines 332–350)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use cursor paging, where the response gives a token for the next page. A cursor is like a bookmark saying where to continue reading.

**Data flow**: It receives an HTTP client, endpoint path, page size, and optional query parameters. It asks the base REST connector to keep following `pagination.next_cursor` and yields each page from the response’s `data` field.

**Call relations**: `AttioConnector.paginate` uses this for meetings. `AttioConnector._paginate_call_recordings` also uses it first to walk meetings and then to list recordings for each meeting.

*Call graph*: called by 2 (_paginate_call_recordings, paginate).


##### `AttioConnector._paginate_call_recordings`  (lines 352–388)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Collects call recordings by first walking through meetings, then fetching recordings under each meeting, and then fetching transcripts for each recording. This extra work is needed because Attio exposes recordings as children of meetings rather than as one simple global list.

**Data flow**: It receives an HTTP client. It pages through meetings, extracts each meeting ID, copies useful meeting context such as title and start/end time onto each recording, computes duration when possible, fetches each recording transcript when available, and yields pages of enriched recording records.

**Call relations**: `AttioConnector.paginate` calls this for the `call_recordings` stream. It relies on `_paginate_cursor` for both meetings and recording lists, `_meeting_id` and `_call_recording_id` for IDs, `_datetime_of` and `_duration_seconds` for timing details, and `_fetch_transcript` for transcript content.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (paginate).


##### `AttioConnector._fetch_transcript`  (lines 390–402)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: Fetches the transcript for one call recording when Attio has it ready. If the transcript is not ready or not found, it treats that as normal and returns nothing instead of failing the sync.

**Data flow**: It receives an HTTP client plus a meeting ID and recording ID. It requests the transcript endpoint, returns the `data` object if it is a dictionary, returns nothing for 404 or 409 responses, and re-raises other HTTP errors.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this while enriching each recording. The returned transcript is later turned into readable `transcript_text` by `AttioConnector._flatten_call_recording`.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 405–409)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a meeting ID from the different ID shapes Attio may return. It keeps the call-recording fan-out from breaking when IDs are nested or already plain strings.

**Data flow**: It receives a meeting record. If `id` is a dictionary, it returns `id.meeting_id`; if `id` is already a string, it returns that string; otherwise it returns nothing.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this before listing recordings for a meeting. If no meeting ID is found, that meeting is skipped.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 412–416)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a call recording ID from the different ID shapes Attio may return. The connector needs this ID to request the recording’s transcript.

**Data flow**: It receives a call recording record. If `id` is a dictionary, it returns `id.call_recording_id`; if `id` is already a string, it returns that string; otherwise it returns nothing.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this for each recording before deciding whether it can fetch a transcript.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 419–423)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: Pulls a usable date or date-time string from Attio’s meeting time shape. Attio may represent timed meetings with `datetime` or all-day meetings with `date`.

**Data flow**: It receives a time-shaped value. If it is a dictionary, it returns the `datetime` value or, if that is missing, the `date` value. For anything else, it returns nothing.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this to copy meeting start and end times onto each recording.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 426–436)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: Calculates a rough meeting duration in seconds from start and end time strings. It returns nothing when the times are missing or cannot be parsed safely.

**Data flow**: It receives optional start and end strings. It parses them as ISO 8601 timestamps, computes the difference in seconds, prevents negative durations by flooring at zero, and returns the number. If parsing fails, it returns nothing.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this after extracting meeting start and end times, so each call recording can inherit a useful duration when available.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 439–448)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes the specific Attio error that means a standard object, such as deals, is disabled in the workspace. This lets the connector skip that stream instead of treating it as a broken sync.

**Data flow**: It receives an HTTP error. It checks for status code 400, tries to read the JSON response body, and returns true only when the body’s code is `standard_object_disabled`.

**Call relations**: `AttioConnector.paginate` uses this when a standard object records query fails. If it returns true, pagination raises `StreamSkipped` with a clear explanation.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._is_scope_unauthorized`  (lines 451–460)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes the specific Attio error that means the OAuth grant is missing a needed permission scope. OAuth is the permission system where a user grants an app limited access.

**Data flow**: It receives an HTTP error. It checks for status code 403, tries to read the JSON response body, and returns true only when the body’s code is `unauthorized`.

**Call relations**: `AttioConnector.paginate` uses this around meetings and call recordings. If the permission is missing, pagination raises `StreamSkipped` rather than stopping the entire connector.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._scope_skip_reason`  (lines 463–469)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a human-readable explanation for skipping a stream because the OAuth grant lacks a required scope. It includes Attio’s own message when available.

**Data flow**: It receives an HTTP error, tries to read its JSON body, extracts the `message` field if present, and returns a short explanatory string. If no message is available, it falls back to a generic note.

**Call relations**: `AttioConnector.paginate` calls this after `_is_scope_unauthorized` confirms the error type, then passes the message into `StreamSkipped`.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/hubspot.py`

`io_transport` · `during HubSpot source sync runs`

HubSpot exposes data through many separate web APIs, and they do not all return records in the same shape. This file is the adapter between that messy outside world and the project’s simpler source-sync system. Without it, HubSpot data would either not be imported, or each HubSpot object type would need custom handling elsewhere.

The file first declares many stream definitions. A stream is one kind of HubSpot data, such as contacts, forms, pipelines, email events, or list memberships. Each stream says what its stable ID is, whether it has a time field for incremental syncing, and how paging works when HubSpot returns results in chunks.

The `HubSpotConnector` then decides how to fetch each stream. Standard CRM objects use HubSpot’s search API, including an incremental cursor so later runs can ask only for records changed since the last run. Deleted CRM records are checked separately, because HubSpot’s search endpoint does not include archived records. Product APIs, like owners or marketing emails, use their own endpoints. Some streams are built by walking parent records, for example turning a campaign’s assets or a conversation’s messages into separate rows.

A key theme is normalization: HubSpot often nests useful fields inside `properties`, `values`, or product-specific wrappers. This connector flattens those into plain records with predictable IDs. It also treats missing permissions as skipped streams rather than whole-sync failures, which matters because HubSpot accounts vary by product tier and OAuth scope.

#### Function details

##### `_normalize_epoch_millis`  (lines 248–255)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Converts a HubSpot timestamp written as milliseconds since 1970 into a readable ISO date string. It leaves booleans and already non-timestamp values alone so accidental conversion does not corrupt data.

**Data flow**: It receives any value. If the value is a number, or a string made only of digits, it treats it as milliseconds and turns it into a UTC date-time string; otherwise it returns the original value unchanged.

**Call relations**: Product and analytics normalization call this when HubSpot sends dates as raw millisecond numbers. It hands back a cleaner value that those row-building functions place into the outgoing record.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 258–267)

```
def _stream(name: str, *, object_type: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a standard stream description for CRM-style HubSpot objects. It gives the sync system the names of the ID field, cursor field, and created/updated date fields.

**Data flow**: It receives a stream name, a HubSpot object type, and whether the stream is canonical. It returns a `StreamSpec`, which is the project’s small instruction card for syncing that object type.

**Call relations**: This helper is used at module load time to define many CRM streams such as contacts, companies, deals, and tickets. Those stream definitions are later collected into the connector’s stream list.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 270–289)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='createdAt', updated_at_field: str | None='updatedAt', pagi
```

**Purpose**: Creates a stream description for HubSpot product APIs that are not shaped like normal CRM search results. These include owners, workflows, campaigns, files, analytics, and similar areas.

**Data flow**: It receives metadata such as stream name, source object, primary key, date fields, and optional pagination rules. It returns a `StreamSpec` marked as non-canonical because these are supporting product records rather than core CRM objects.

**Call relations**: This helper is used while the file defines product API streams. The resulting specs guide `HubSpotConnector._paginate_product_api` when choosing the right endpoint or special walker.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 292–304)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Builds a reusable pagination rule for HubSpot list endpoints that return `results` plus a `paging.next.after` cursor. This avoids repeating the same paging setup for many product APIs.

**Data flow**: It receives an API path. It returns a `Pagination` object that tells the base connector where records live in the response, where the next-page cursor lives, and which query parameters control cursor and page size.

**Call relations**: Several stream definitions use this during module setup. Later, streams with this pagination rule can be paged by the base REST connector strategy instead of custom code.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 307–317)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Creates a stream description for relationship tables, such as deal-to-contact or ticket-to-company links. These are synthetic streams because HubSpot does not expose them as normal records.

**Data flow**: It receives a junction stream name and the parent object type. It returns a `StreamSpec` with no cursor because association links do not provide a modification timestamp.

**Call relations**: The module uses this to define simple relationship streams. During syncing, `HubSpotConnector._paginate_unchecked` recognizes these streams and sends them to `HubSpotConnector._paginate_junction`.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 622–652)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the JSON request body for HubSpot’s CRM search API. It includes which properties to return, how many records to fetch, how to sort, and where to resume.

**Data flow**: It receives a stream definition, property names, an incremental cursor, and a page cursor. It produces a dictionary sent as the POST body to HubSpot, optionally filtering records to those changed at or after the saved cursor.

**Call relations**: The normal CRM paginator and the custom-object paginator both call this before making search requests. It is the shared recipe that keeps incremental CRM searches consistent.

*Call graph*: called by 2 (_paginate_custom_object_records, _paginate_unchecked).


##### `HubSpotConnector._flatten`  (lines 655–666)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a normal HubSpot CRM record into a simpler flat record. HubSpot wraps business fields inside `properties`; this lifts them to the top level.

**Data flow**: It receives one HubSpot record. It copies the ID, created and updated timestamps, archived flag, and all fields inside `properties` into one plain dictionary.

**Call relations**: `HubSpotConnector.flatten` calls this for standard CRM streams. The flattened output is what the rest of the sync pipeline sees instead of HubSpot’s nested envelope.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 669–692)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records from HubSpot product APIs, which often differ from CRM records. It fills in missing IDs, lifts nested property bags, and handles special date shapes.

**Data flow**: It receives a product API record and its stream definition. It copies the record, may turn `objectId` into `id`, lifts `properties` and `values` fields, and normalizes special timestamps for knowledge articles and email events.

**Call relations**: `HubSpotConnector.flatten` calls this for product API streams. It also uses `_normalize_epoch_millis` when particular HubSpot APIs return time as milliseconds.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 694–701)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening rule for each kind of HubSpot stream. This is the connector’s final cleanup step before records leave the source layer.

**Data flow**: It receives a raw record and its stream definition. Junction and custom-object rows are already shaped correctly, product API rows go through product normalization, and ordinary CRM rows go through CRM flattening.

**Call relations**: The broader source framework calls this after pages are fetched. It delegates to `_flatten` or `_flatten_product_api` so each stream family is cleaned in the right way.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector._list_properties`  (lines 703–711)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which fields exist for a CRM object type. This matters because HubSpot’s search API only returns properties that are explicitly requested.

**Data flow**: It receives an HTTP client and a HubSpot object type. It calls HubSpot’s properties endpoint, filters the response to valid property names, and returns those names as a list.

**Call relations**: `HubSpotConnector._paginate_unchecked` calls this before searching standard CRM objects. The returned names are fed into `_build_search_body` so the sync captures all available fields.

*Call graph*: called by 1 (_paginate_unchecked).


##### `HubSpotConnector.paginate`  (lines 713–726)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the safe public paging entry for a HubSpot stream. It fetches pages but turns permission-related failures into a skipped stream instead of a broken sync run.

**Data flow**: It receives an HTTP client, a stream, and an optional saved cursor. It yields pages from `_paginate_unchecked`; if HubSpot returns authentication or stream-permission errors, it raises `StreamSkipped` with a readable reason.

**Call relations**: The source runner calls this when it wants records for a stream. It delegates all real fetching to `_paginate_unchecked` and uses `_is_stream_unavailable` plus `_stream_skip_reason` to decide when a failure should be recorded as a skip.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 728–778)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Routes each stream to the correct fetching method, then pages through normal CRM search when no special method is needed. It is the main traffic director inside the connector.

**Data flow**: It receives a stream and cursor. Depending on the stream, it either uses a declared pagination strategy, a junction walker, a custom-object walker, a product-API walker, or the standard CRM search loop; after standard CRM search it also yields archived-record tombstones.

**Call relations**: `HubSpotConnector.paginate` calls this after adding error handling around it. This function calls helpers such as `_paginate_product_api`, `_paginate_custom_objects`, `_paginate_junction`, `_list_properties`, `_build_search_body`, and `_paginate_archived_ids` as needed.

*Call graph*: calls 6 internal fn (_build_search_body, _list_properties, _paginate_archived_ids, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._is_stream_unavailable`  (lines 781–804)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects when HubSpot is saying this account is not allowed to read a particular stream. That is different from the API being down or the connector being broken.

**Data flow**: It receives an HTTP error. It checks for a 403 response and scans HubSpot’s message for permission or scope wording, returning true only when the stream appears unavailable to this account.

**Call relations**: `paginate` uses this to convert permission failures into skipped streams. Archived sweeps and custom-object archived sweeps also use it so optional inaccessible parts do not stop the run.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 807–816)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a clear human-readable explanation for why a HubSpot stream was skipped. It includes HubSpot’s own message when available.

**Data flow**: It receives the stream name and HTTP error. It tries to read the JSON response body and returns a sentence naming the stream and the upstream reason.

**Call relations**: `HubSpotConnector.paginate` calls this only after deciding a stream should be skipped. The result becomes the message attached to `StreamSkipped`.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 818–853)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds CRM records that HubSpot has archived or deleted so the local copy can be tombstoned. HubSpot’s search API misses these, so this extra sweep prevents stale data from lingering.

**Data flow**: It receives a client and stream. It pages through the object list endpoint with `archived=true`, collects record IDs, and yields `StreamPage` objects containing deletes instead of normal records.

**Call relations**: `_paginate_unchecked` runs this after standard CRM search. It uses `_is_archived_sweep_unsupported` and `_is_stream_unavailable` to silently stop when HubSpot does not support or allow the archived sweep.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 855–949)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the special fetching routine for product API streams. HubSpot product areas have many different endpoints, so one generic loop is not enough.

**Data flow**: It receives a stream and cursor. It checks the stream name and delegates to the matching paginator, or falls back to a known GET collection path; if no path exists, it raises an implementation error.

**Call relations**: `_paginate_unchecked` calls this for product API streams. It hands work to many specialized paginators, including analytics, associations, list memberships, sequences, events, form submissions, conversation messages, pipelines, and generic GET collection paging.

*Call graph*: calls 21 internal fn (_paginate_analytics_reports, _paginate_analytics_views, _paginate_association_labels, _paginate_associations, _paginate_campaign_assets, _paginate_consent_states, _paginate_conversation_messages, _paginate_email_events, _paginate_event_occurrences, _paginate_event_types (+11 more)); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._paginate_get_collection`  (lines 951–979)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Pages through HubSpot endpoints that follow the common `results` plus `paging.next.after` pattern. It is the simple reusable list reader.

**Data flow**: It receives a path, optional page size, and extra query parameters. It repeatedly calls the endpoint, normalizes `objectId` into `id` when needed, yields non-empty result pages, and stops when there is no next cursor.

**Call relations**: Many product-specific paginators call this instead of rewriting the same paging loop. It supports owner teams, campaign assets, form submissions, conversation messages, sequences, and generic product API streams.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_product_api, _paginate_sequences, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 981–1016)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Syncs HubSpot custom objects, whose object types are defined by each HubSpot account. It first discovers the account’s custom schemas, then reads records for each schema.

**Data flow**: It receives a cursor. It fetches custom object schemas, extracts each object type and property list, builds a temporary stream definition, yields matching records, and then yields archived tombstones for that custom object type.

**Call relations**: `_paginate_unchecked` calls this for the `custom_objects` stream. It relies on schema helpers, `_paginate_custom_object_records`, and `_paginate_custom_object_archived_ids`.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 1018–1020)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of custom object schemas available in the HubSpot account. A schema describes a custom object type and its fields.

**Data flow**: It receives an HTTP client, calls HubSpot’s CRM schema endpoint, and returns only dictionary-like schema rows from the response.

**Call relations**: `_paginate_custom_objects` uses this to discover what to sync. `_association_object_types` also uses it so associations involving custom objects can be considered.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 1023–1028)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Finds the best usable object type identifier inside a custom object schema. HubSpot may expose this under different field names.

**Data flow**: It receives a schema dictionary. It checks several possible keys and returns the first non-empty string, or `None` if none exists.

**Call relations**: `_paginate_custom_objects`, `_custom_object_row`, and `_association_object_types` use this whenever they need the stable object type name for custom-object API calls or IDs.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1031–1046)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Collects all useful property names from a custom object schema. This ensures custom object searches request the fields that actually exist.

**Data flow**: It receives a schema. It gathers names from the schema’s property list and display-property settings, removes duplicates, and returns the ordered list.

**Call relations**: `_paginate_custom_objects` calls this before searching records for each custom object type. The result is passed into `_paginate_custom_object_records`.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1048–1083)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Reads records for one specific custom object type through HubSpot’s search API. It applies the same incremental cursor idea used for normal CRM objects.

**Data flow**: It receives a temporary stream spec, schema, property list, and cursor. It pages through search results, skips duplicate boundary records caused by inclusive cursor filtering, turns raw records into custom-object rows, and yields pages.

**Call relations**: `_paginate_custom_objects` calls this for each discovered custom schema. It uses `_build_search_body` for the search request and `_custom_object_row` to shape each output record.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1085–1124)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Turns one raw custom object record into a self-describing row. It includes both the record’s properties and context about what custom object type it belongs to.

**Data flow**: It receives a raw record and its schema. It builds an ID combining object type and record ID, copies properties, adds display labels and title fields, and returns `None` if required IDs are missing.

**Call relations**: `_paginate_custom_object_records` calls this for each custom object search result. It uses `_schema_object_type_id` so the row can be tied back to the correct custom object type.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1126–1156)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived custom object records so they can be deleted from the local synced view. This mirrors the archived sweep for standard CRM objects.

**Data flow**: It receives a custom object type ID. It pages HubSpot’s archived list endpoint, builds delete IDs in the same `object_type:record_id` format used for live custom-object rows, and yields `StreamPage` delete batches.

**Call relations**: `_paginate_custom_objects` calls this after reading live records for each custom object type. It uses `_is_archived_sweep_unsupported` and `_is_stream_unavailable` to stop gracefully when HubSpot cannot provide the sweep.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1158–1177)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a stream of owner teams from the owner records HubSpot returns. HubSpot does not expose teams here as a separate flat list, so this extracts them.

**Data flow**: It reads all owners, looks inside each owner’s `teams` list, deduplicates teams by ID, and yields one page of unique team records.

**Call relations**: `_paginate_product_api` calls this for the `owner_teams` stream. It depends on `_paginate_get_collection` to read the owner pages.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_lists`  (lines 1179–1208)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot CRM lists using HubSpot’s search-style lists endpoint. It also flattens extra list properties into the main row.

**Data flow**: It sends repeated POST requests with an offset, converts `listId` into `id`, merges `additionalProperties` when present, yields pages, and advances until HubSpot says there are no more lists.

**Call relations**: `_paginate_product_api` uses this for the `lists` stream. `_paginate_list_memberships` also calls it first so it knows which lists need membership walks.

*Call graph*: called by 2 (_paginate_list_memberships, _paginate_product_api).


##### `HubSpotConnector._paginate_site_search`  (lines 1210–1230)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads CMS search results for a chosen content type, such as knowledge articles. It handles offset-based paging rather than cursor-based paging.

**Data flow**: It receives a content type, repeatedly calls the site-search endpoint with limit and offset, yields dictionary results, and stops once the next offset reaches the reported total.

**Call relations**: `_paginate_product_api` calls this for the knowledge-article stream. The returned rows are later normalized by the product API flattening path.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1232–1256)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds assets attached to HubSpot marketing campaigns. It starts with campaigns, then checks each supported asset type under each campaign.

**Data flow**: It reads campaign pages, extracts each campaign ID and name, then asks `_paginate_campaign_asset_type` for every known asset type and yields the resulting asset pages.

**Call relations**: `_paginate_product_api` calls this for the `campaign_assets` stream. It uses `_paginate_get_collection` to list campaigns and delegates per-type asset fetching to `_paginate_campaign_asset_type`.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1258–1293)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one kind of asset for one campaign and turns it into a stable row. The stable ID includes campaign, asset type, and asset ID.

**Data flow**: It receives a campaign ID, campaign name, and asset type. It pages the matching endpoint, adds fields such as `asset_kind`, `campaign_id`, and `metrics`, yields pages, and ignores 403 or 404 responses for unavailable asset types.

**Call relations**: `_paginate_campaign_assets` calls this inside its campaign-and-asset-type loop. It uses `_paginate_get_collection` for the actual paging.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1295–1301)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields HubSpot analytics view definitions as one page. Analytics views act like saved filters for analytics reports.

**Data flow**: It asks `_analytics_view_rows` to fetch and normalize the views. If any rows come back, it yields them.

**Call relations**: `_paginate_product_api` calls this for the `analytics_views` stream. It is a thin wrapper around `_analytics_view_rows`.

*Call graph*: calls 1 internal fn (_analytics_view_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_view_rows`  (lines 1303–1334)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches and normalizes analytics views. It gives each view a stable ID, readable name, filters, and normalized timestamps.

**Data flow**: It calls HubSpot’s analytics views endpoint, accepts either a list response or a `results` response, filters invalid rows, builds normalized rows, and returns them as a list.

**Call relations**: `_paginate_analytics_views` uses this to emit analytics view records. `_paginate_analytics_reports` also uses it so reports can be fetched both for all traffic and for each analytics view.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1336–1366)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds many analytics report queries across report subjects, time periods, and analytics views. It fans out because HubSpot’s analytics API is split by report shape.

**Data flow**: It chooses a date window, loads analytics views, creates an all-traffic filter plus per-view filters, then loops through report families, subjects, periods, and views, yielding pages from each query.

**Call relations**: `_paginate_product_api` calls this for the `analytics_reports` stream. It uses `_analytics_report_window`, `_analytics_view_rows`, and `_paginate_analytics_report_query`.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_report_window`  (lines 1369–1370)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range used for analytics report pulls. It starts from a fixed early date and ends at today in UTC.

**Data flow**: It reads the current UTC date, formats it as `YYYYMMDD`, and returns a start-date and end-date pair.

**Call relations**: `_paginate_analytics_reports` calls this before launching report queries. The resulting dates are passed into each analytics report request.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1372–1423)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Runs one analytics report query and pages through its breakdown rows. Some report combinations are not supported by HubSpot, so it skips expected 400 or 404 responses.

**Data flow**: It receives report family, subject, period, optional analytics view, and date range. It calls HubSpot with offset paging, converts the response into rows, yields them, and advances until all breakdowns are read.

**Call relations**: `_paginate_analytics_reports` calls this for each report combination. It delegates response shaping to `_analytics_report_rows`.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1426–1501)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Turns one analytics report response into searchable rows. It creates one row for totals and separate rows for each breakdown value.

**Data flow**: It receives the raw report response plus report context. It copies metrics, attaches IDs, names, filters, date range, report family, subject, and time period, then returns a list of rows.

**Call relations**: `_paginate_analytics_report_query` calls this after each HubSpot report response. The rows it returns are yielded as the `analytics_reports` stream.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1504–1508)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Creates a stable ID string for an analytics report row. It cleans separator characters so the ID remains predictable.

**Data flow**: It receives any number of ID parts. It converts each part to text, replaces problematic `/` and `:` characters, uses `none` for missing parts, and joins them under an `analytics_report:` prefix.

**Call relations**: This is a local helper for analytics report row creation. It does not call other project helpers; it simply returns the ID string needed by report rows.


##### `HubSpotConnector._analytics_report_date`  (lines 1511–1512)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Formats HubSpot analytics report dates from compact `YYYYMMDD` text into normal `YYYY-MM-DD` text.

**Data flow**: It receives an eight-character date string and inserts dashes between year, month, and day.

**Call relations**: This helper supports analytics report row creation by making start and end dates easier to read. It has no handoff to other project functions.


##### `HubSpotConnector._paginate_event_types`  (lines 1514–1533)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot event type definitions. Event types describe categories of events that can later appear as occurrences.

**Data flow**: It calls the event-types endpoint, accepts either list or `results` response shape, assigns each row an ID from available identifying fields or its position, and yields the rows if any exist.

**Call relations**: `_paginate_product_api` calls this for the `event_types` stream. It is self-contained apart from the HTTP request.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1535–1557)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual HubSpot event occurrences, optionally from a saved cursor. It creates synthetic IDs when HubSpot does not provide one.

**Data flow**: It receives an optional cursor and sends it as `occurredAfter`. It reads event rows, ensures every row has an `id`, yields them, and treats a 404 as an empty stream.

**Call relations**: `_paginate_product_api` calls this for the `event_occurrences` stream. It calls `_synthetic_event_id` only for rows missing a HubSpot ID.

*Call graph*: calls 1 internal fn (_synthetic_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._synthetic_event_id`  (lines 1560–1570)

```
def _synthetic_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Builds a stable fallback ID for an event occurrence. This prevents events without HubSpot IDs from collapsing into duplicates.

**Data flow**: It receives an event row and its page index. It combines event type, object type, object ID, occurrence time or index, and a stable hash of the payload into one colon-separated string.

**Call relations**: `_paginate_event_occurrences` calls this when a row lacks its own ID. It relies on the connector’s stable payload hash helper to reduce accidental collisions.

*Call graph*: called by 1 (_paginate_event_occurrences).


##### `HubSpotConnector._paginate_email_events`  (lines 1572–1600)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads marketing email events from HubSpot’s older email events API. It supports incremental starts and offset paging.

**Data flow**: It converts the cursor to a start timestamp when possible, requests event pages with a large limit, assigns IDs or synthetic IDs, yields pages, and follows HubSpot’s offset until there are no more events.

**Call relations**: `_paginate_product_api` calls this for the `email_events` stream. It uses `_email_event_start_timestamp` for cursor conversion and `_synthetic_email_event_id` for missing IDs.

*Call graph*: calls 2 internal fn (_email_event_start_timestamp, _synthetic_email_event_id); called by 1 (_paginate_product_api).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1603–1612)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts an email-event cursor into the millisecond timestamp format that HubSpot’s email API expects.

**Data flow**: It receives a cursor string. If it is already digits, it returns that number; if it is an ISO date-time string, it parses it and returns milliseconds since 1970; otherwise it returns `None`.

**Call relations**: `_paginate_email_events` calls this before making requests. The returned number becomes HubSpot’s `startTimestamp` query parameter.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._synthetic_email_event_id`  (lines 1615–1625)

```
def _synthetic_email_event_id(row: dict[str, Any], idx: int) -> str
```

**Purpose**: Builds a stable fallback ID for an email event when HubSpot does not provide one. It uses enough event details to distinguish similar events.

**Data flow**: It receives an email event row and index. It combines created time, recipient, event type, campaign ID, and a stable hash into one colon-separated ID string.

**Call relations**: `_paginate_email_events` calls this for rows with no ID. It depends on the stable payload hash helper to make the fallback ID less likely to collide.

*Call graph*: called by 1 (_paginate_email_events).


##### `HubSpotConnector._stable_payload_hash`  (lines 1628–1630)

```
def _stable_payload_hash(row: dict[str, Any]) -> str
```

**Purpose**: Creates a short repeatable hash of a row’s full contents. This is useful when HubSpot omits an ID but the connector still needs a stable identifier.

**Data flow**: It receives a dictionary, serializes it with sorted keys, hashes the text with SHA-256, and returns the first 16 hex characters.

**Call relations**: Synthetic ID helpers use this kind of value to distinguish otherwise similar records. It does not call project-specific helpers.

*Call graph*: 2 external calls (sha256, dumps).


##### `HubSpotConnector._paginate_association_labels`  (lines 1632–1648)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the labels HubSpot defines for relationships between object types, such as different kinds of contact-to-company associations.

**Data flow**: It walks object-type pairs that have labels, converts each label into a normalized row with source and target object types, and yields non-empty pages.

**Call relations**: `_paginate_product_api` calls this for the `association_labels` stream. It gets pairs from `_association_pairs_with_labels` and shapes each label with `_association_label_row`.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_associations`  (lines 1650–1665)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual association links between HubSpot objects. It fans out across object-type pairs and source record IDs.

**Data flow**: It finds labeled object-type pairs, pages through IDs for the source object type, sends batches to HubSpot’s association read endpoint, and yields normalized relationship rows.

**Call relations**: `_paginate_product_api` calls this for the `associations` stream. It uses `_association_pairs_with_labels`, `_paginate_crm_object_id_pages`, and `_paginate_association_batch`.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_association_batch`  (lines 1667–1694)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads association links for a batch of source records, including additional pages for records with many associations.

**Data flow**: It receives source and target object types plus input IDs. It posts the batch read request, turns the response into rows, yields them, then builds any follow-up inputs needed for per-record next cursors.

**Call relations**: `_paginate_associations` calls this after collecting source IDs. It uses `_association_rows` for row shaping, `_next_association_inputs` for continuation, and `_is_optional_pair_unavailable` to ignore unsupported pairs.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1697–1711)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Finds follow-up requests needed when HubSpot says a source record has more associated targets than fit in one response.

**Data flow**: It receives a batch association response. It scans each result for a source record ID and a next cursor, then returns new input dictionaries containing `id` and `after`.

**Call relations**: `_paginate_association_batch` calls this after each batch response. The returned inputs become the next loop’s pending work.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1713–1726)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Discovers object-type pairs that actually have association labels. This limits later association work to pairs HubSpot recognizes.

**Data flow**: It gets all candidate object types, tries every from-and-to combination, asks HubSpot for labels, and yields only pairs with at least one label.

**Call relations**: `_paginate_association_labels` and `_paginate_associations` both call this. It uses `_association_object_types` to build candidates and `_association_labels_for_pair` to test each pair.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1728–1741)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the list of HubSpot object types to consider for associations. It includes standard object types and any custom object types available in the account.

**Data flow**: It starts with a fixed list of known standard object types. It tries to fetch custom object schemas, extracts their object type IDs, appends new ones, and returns the combined list.

**Call relations**: `_association_pairs_with_labels` calls this before testing object-type pairs. It uses `_custom_object_schemas`, `_schema_object_type_id`, and `_is_optional_pair_unavailable` for graceful fallback.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1743–1759)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches association label definitions for one source object type and one target object type. Unsupported pairs return an empty list.

**Data flow**: It receives two object type names, calls HubSpot’s labels endpoint, returns valid label rows, or returns an empty list for optional unavailable pairs.

**Call relations**: `_association_pairs_with_labels` calls this for each candidate pair. It uses `_is_optional_pair_unavailable` to decide whether an error just means the pair is not supported.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1762–1779)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Turns one raw association label into a normalized record. It adds a stable ID and clear source/target fields.

**Data flow**: It receives a label plus source and target object types. It extracts type ID, category, and display label, then returns a row containing both original and normalized fields.

**Call relations**: `_paginate_association_labels` calls this for every label returned by `_association_pairs_with_labels`.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1781–1793)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Reads CRM object pages and reduces them to ID lists. This is useful for APIs that need source record IDs as inputs.

**Data flow**: It receives an object type. It pages through CRM object records requesting only `hs_object_id`, extracts non-empty IDs, and yields ID batches.

**Call relations**: `_paginate_associations` uses this to feed association batch reads. `_paginate_sequence_enrollments` uses it to check sequence enrollments for contacts.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1795–1825)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads basic CRM object list pages with selected properties. It is a lightweight alternative to the full search-based stream sync.

**Data flow**: It receives an object type and a tuple of property names. It pages the CRM object list endpoint, yields dictionary records, and stops gracefully for optional unavailable object types.

**Call relations**: `_paginate_crm_object_id_pages` and `_paginate_contact_identity_pages` call this. It uses `_is_optional_pair_unavailable` to ignore unsupported or inaccessible optional objects.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1828–1862)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Converts a HubSpot association batch response into one row per relationship type between two records.

**Data flow**: It receives raw association response data and object type context. It finds each source record, each target record, and each association type, then builds normalized rows for valid links.

**Call relations**: `_paginate_association_batch` calls this after each batch read. It uses the association-row builder to create the final row shape.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1865–1892)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one normalized association relationship row. The row names both sides of the relationship and the kind of association when HubSpot provides it.

**Data flow**: It receives association type details, source and target object types, source and target record IDs, and a fallback index. It returns a dictionary with a stable composite ID and relationship metadata.

**Call relations**: This is the low-level row builder used by association response normalization. It does not need to fetch data; it only shapes one relationship.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1895–1898)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether an error should be treated as an unsupported optional combination rather than a fatal failure. This is common when checking many possible HubSpot object pairs.

**Data flow**: It receives an HTTP error. It returns true for 400 or 404 responses, or for permission-style 403 responses recognized by `_is_stream_unavailable`.

**Call relations**: Many optional fan-out walkers call this, including association, memberships, consent, sequence, and CRM object helper flows. It centralizes the choice to skip unavailable optional pieces.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1900–1914)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads which records belong to each HubSpot list. It first finds lists, then walks the members of each list.

**Data flow**: It pages through lists, extracts each list ID, calls the per-list membership paginator, and yields membership pages.

**Call relations**: `_paginate_product_api` calls this for the `list_memberships` stream. It depends on `_paginate_lists` and `_paginate_memberships_for_list`.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1916–1959)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads memberships for one HubSpot list and adds list context to each membership row.

**Data flow**: It receives the list record and list ID. It pages the memberships endpoint, builds rows with a composite ID, list name, object type, and processing type, then yields pages until no cursor remains.

**Call relations**: `_paginate_list_memberships` calls this for each list. It uses `_is_optional_pair_unavailable` to ignore lists whose memberships cannot be read.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1961–1974)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot communication subscription definitions. These describe the kinds of email subscriptions or preferences a contact may have.

**Data flow**: It calls the definitions endpoint, accepts either `results` or `subscriptionDefinitions`, assigns each row an ID, and yields the rows if any exist.

**Call relations**: `_paginate_product_api` calls this for the `subscription_definitions` stream. It is a direct endpoint reader with light normalization.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_consent_states`  (lines 1976–1989)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads email consent states for contacts. It checks both normal subscription statuses and unsubscribe-all status for each contact email.

**Data flow**: It pages through contact identities, skips contacts without email, requests subscription status rows and unsubscribe-all rows, combines them, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the `consent_states` stream. It uses `_paginate_contact_identity_pages`, `_consent_status_rows`, and `_unsubscribe_all_rows`.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1991–2007)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads contact records with their email addresses. This supplies the email identifiers needed for consent preference endpoints.

**Data flow**: It pages contact records requesting the `email` property, extracts email from either the top level or `properties`, and yields contact rows with a consistent `email` field.

**Call relations**: `_paginate_consent_states` calls this before checking communication preferences. It uses `_paginate_crm_object_pages` to read the contact pages.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 2009–2030)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches subscription-specific communication preference statuses for one contact email.

**Data flow**: It receives a contact and email, URL-escapes the email, calls the statuses endpoint for the EMAIL channel, converts each result into a consent row, and returns the list.

**Call relations**: `_paginate_consent_states` calls this for each contact email. It uses `_consent_row` to shape rows and `_is_optional_pair_unavailable` to ignore unavailable preference data.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 2032–2056)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches the unsubscribe-all communication preference status for one contact email. This captures broad opt-out state in addition to subscription-specific state.

**Data flow**: It receives a contact and email, URL-escapes the email, calls the unsubscribe-all endpoint for the EMAIL channel, converts each result into a consent row, and returns the list.

**Call relations**: `_paginate_consent_states` calls this alongside `_consent_status_rows`. It uses `_consent_row` and `_is_optional_pair_unavailable` in the same way.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 2059–2089)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Normalizes one communication preference response into a consent-state record. It gives the row a stable ID and clear fields for contact, email, purpose, status, and source.

**Data flow**: It receives a raw preference row, contact, email, and status kind. It builds a composite ID from email, subscription or status kind, and business unit, then returns a row with original and normalized consent fields.

**Call relations**: `_consent_status_rows` and `_unsubscribe_all_rows` call this to keep both preference shapes consistent.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2091–2118)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sales sequences for each HubSpot user found through owners. Sequences are user-scoped in this API, so the connector must first discover user IDs.

**Data flow**: It gets user rows from owners, calls the sequences endpoint with each user ID, attaches owner context to each returned sequence, yields pages, and skips optional unavailable user scopes.

**Call relations**: `_paginate_product_api` calls this for the `sequences` stream. It uses `_sequence_user_rows`, `_paginate_get_collection`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_user_rows`  (lines 2120–2143)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a unique list of HubSpot user IDs from owner records. These user IDs are needed to query user-scoped sequence APIs.

**Data flow**: It reads owner pages, extracts each owner’s `userId`, deduplicates them, and returns rows containing user ID plus owner ID and owner email.

**Call relations**: `_paginate_sequences` calls this before fetching sequences. It uses `_paginate_get_collection` to read owners.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2145–2163)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sequence enrollment data for contacts. It checks each contact ID against the sequence enrollment endpoint.

**Data flow**: It pages through contact IDs, requests enrollment data for each contact, converts responses into rows, accumulates a page, and yields it when non-empty.

**Call relations**: `_paginate_product_api` calls this for the `sequence_enrollments` stream. It uses `_paginate_crm_object_id_pages`, `_sequence_enrollment_rows`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2166–2180)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes a sequence enrollment response for one contact. It handles both list-style and single-object responses.

**Data flow**: It receives raw response data and a contact ID. It treats `results` as the row list when present, otherwise wraps the whole response as one row, assigns each row an ID, adds `contact_id`, and returns the rows.

**Call relations**: `_paginate_sequence_enrollments` calls this after each contact enrollment lookup. It only shapes data; it does not make network calls.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2182–2211)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads submissions for every HubSpot form. Form submissions live under each form, so this walks forms first.

**Data flow**: It pages forms, extracts each form ID, pages that form’s submissions, assigns each submission a conversion-based or fallback ID, adds form ID and form name, and yields pages.

**Call relations**: `_paginate_product_api` calls this for the `form_submissions` stream. It uses `_paginate_get_collection` for both forms and submissions.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2213–2228)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages inside HubSpot conversation threads. Threads are read first, then each thread’s messages are fetched.

**Data flow**: It pages conversation threads, extracts each thread ID, pages messages for that thread, adds `thread_id` to each message, and yields non-empty message pages.

**Call relations**: `_paginate_product_api` calls this for the `conversation_messages` stream. It uses `_paginate_get_collection` for both thread and message endpoints.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipelines`  (lines 2230–2237)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads deal and ticket pipelines as normalized pipeline rows. Pipelines define the stages that deals or tickets move through.

**Data flow**: It loops through supported pipeline object types, asks for pipeline rows for each, and yields any non-empty pages.

**Call relations**: `_paginate_product_api` calls this for the `pipelines` stream. It delegates per-object-type shaping to `_pipeline_rows_for_object_type`.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2239–2289)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads pipeline stages for deal and ticket pipelines. It turns nested stage lists into separate rows with clear pipeline and object-type context.

**Data flow**: It loops through pipeline object types, fetches raw pipelines, then for each stage builds a row with stage ID, pipeline ID, object kind, name, status, probability, order, and closed-state information.

**Call relations**: `_paginate_product_api` calls this for the `pipeline_stages` stream. It uses `_raw_pipelines_for_object_type` to get the source pipeline data.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2291–2314)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes raw pipelines for one object type, such as deals or tickets. It adds IDs and status fields that are consistent across object kinds.

**Data flow**: It receives an object type, fetches raw pipelines, skips entries without IDs, and returns rows with composite IDs, source pipeline IDs, object kind, name, and active or archived status.

**Call relations**: `_paginate_pipelines` calls this for each supported object type. It depends on `_raw_pipelines_for_object_type` for the HubSpot API read.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2316–2327)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches raw pipeline definitions from HubSpot for one object type. It quietly returns no rows when the account cannot access that pipeline area.

**Data flow**: It receives an object type, calls the pipelines endpoint, returns dictionary rows from `results`, and turns 403 or 404 responses into an empty list.

**Call relations**: `_pipeline_rows_for_object_type` and `_paginate_pipeline_stages` call this before shaping pipeline or stage records.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2330–2334)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects a specific HubSpot response meaning archived-object paging is not supported for that object type. This lets the sync continue without delete detection for that stream.

**Data flow**: It receives an HTTP error. It checks for status 400 and looks in HubSpot’s message for the known unsupported archived-paging text.

**Call relations**: `_paginate_archived_ids` and `_paginate_custom_object_archived_ids` call this when archived sweeps fail. It relies on `_upstream_message` to read HubSpot’s error message.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2337–2345)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Extracts HubSpot’s `message` field from an HTTP error response when possible. It is a small helper for interpreting upstream errors.

**Data flow**: It receives an HTTP error. It tries to parse the response as JSON, checks for a dictionary body, and returns the message as text or `None`.

**Call relations**: Archived-sweep error detection uses this to inspect HubSpot’s explanation. It does not make network calls or change state.


##### `HubSpotConnector._paginate_junction`  (lines 2347–2394)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds simple relationship streams like deal-to-contact by reading associations embedded in parent object list responses. Each output row represents one parent-target pair.

**Data flow**: It receives a parent object and target object. It pages the parent object list with `associations` requested, extracts target IDs from each parent’s association block, builds composite relationship rows, and stops when no next cursor remains.

**Call relations**: `_paginate_unchecked` calls this for configured junction streams. It is separate from the broader association API flow because these few relationship streams can be built directly from parent records.

*Call graph*: called by 1 (_paginate_unchecked).


### `extensions/sources/ufo_ext_sources/salesforce.py`

`io_transport` · `active during source sync when Salesforce streams are being read`

Salesforce stores customer data in objects, called SObjects, and each organization can expose a different set of fields. This file avoids guessing those fields ahead of time. For each supported Salesforce object, it first asks Salesforce what fields exist, then builds a Salesforce query to fetch records in update-time order. That lets the sync pick up where it left off by using a cursor, which is just a saved timestamp marking the last seen change.

The main class, SalesforceConnector, is a REST connector, meaning it talks to Salesforce over HTTP. It uses Salesforce’s query API to fetch records in pages of up to 200. If Salesforce says there are more results, it follows the next-results link until the stream is done. After a previous cursor exists, it also asks Salesforce for records deleted since that time. Those deleted IDs are returned as a special tombstone page, so the rest of the system can forget records that disappeared without needing to rescan everything.

A small cleanup step removes Salesforce’s metadata wrapper named attributes, leaving only the actual field values. If Salesforce refuses access with an authorization error, the stream is skipped with a clear message instead of failing mysteriously. The connector expects the real Salesforce instance URL and credentials to be supplied by the surrounding runner.

#### Function details

##### `_stream`  (lines 29–38)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a standard description of one Salesforce stream, such as accounts or contacts. This description tells the sync system which Salesforce object to read, which field identifies each record, and which timestamp field should be used to resume later.

**Data flow**: It receives a friendly stream name, the Salesforce object name, and whether the stream is considered canonical. It fills in the common Salesforce details, such as Id as the primary key and SystemModstamp as the update cursor, and returns a StreamSpec object the connector can later use.

**Call relations**: This helper is used while the file is loaded to build the SALESFORCE_STREAMS list. Each call produces one stream recipe, and those recipes are attached to SalesforceConnector so the wider sync system knows which Salesforce objects are available.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 78–82)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: Builds the Salesforce query text used to fetch records for one stream. SOQL is Salesforce’s query language, similar in spirit to SQL, and this function creates a safe, consistent query shape for incremental syncing.

**Data flow**: It receives the stream description, the field names discovered from Salesforce, and an optional saved cursor timestamp. It joins the fields into a SELECT query, adds a timestamp filter when a cursor exists, orders results by that timestamp, limits the page size, and returns the final query string.

**Call relations**: paginate calls this after it has learned the object’s fields. The returned query is then sent to Salesforce’s query endpoint to retrieve the next batch of records.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 84–90)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: Asks Salesforce which fields exist on a specific object. This matters because different Salesforce organizations can customize their objects, so hard-coding a field list would miss data or break in some accounts.

**Data flow**: It receives an HTTP client and a Salesforce object name. It calls Salesforce’s describe endpoint, reads the fields section of the response, keeps valid field names, and returns them as a list of strings.

**Call relations**: paginate calls this at the start of reading a stream. Its result feeds into _build_soql, so the later query includes the real fields available in that Salesforce organization.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 92–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Reads one Salesforce stream page by page. It is the main pulling loop: it fetches current or changed records, follows Salesforce pagination links, and then checks for deleted records when doing an incremental sync.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous run. It discovers fields, builds a SOQL query, sends requests to Salesforce, yields each non-empty batch of records, follows nextRecordsUrl links until Salesforce says the query is done, and finally may yield a delete-tombstone page for records removed since the cursor. If Salesforce returns 401 or 403, it turns that into a StreamSkipped message explaining that access was refused.

**Call relations**: This is the connector’s central read operation. It calls _describe_fields to learn what to ask for, _build_soql to form the query, and _deleted_page to report hard deletes. When Salesforce refuses access, it creates a StreamSkipped error so the surrounding sync runner can skip that stream cleanly.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 121–139)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: Finds Salesforce records that were hard-deleted since the last saved cursor. This prevents the local synced copy from keeping records that no longer exist in Salesforce.

**Data flow**: It receives an HTTP client, a stream description, and the previous cursor timestamp. It chooses the current time as the end of the deletion window, calls Salesforce’s deleted-records endpoint, extracts deleted record IDs, chooses the next cursor from Salesforce’s latest covered time when available, and returns a StreamPage containing delete markers and the next cursor.

**Call relations**: paginate calls this only after it has finished fetching changed records and only when there is an existing cursor. The StreamPage it returns is handed back to the sync system as a tombstone page, meaning a page whose purpose is to say which records should be removed.

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 141–144)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Removes Salesforce’s extra attributes wrapper from a record before the record is stored or indexed. This keeps the synced data focused on the object’s actual fields.

**Data flow**: It receives one Salesforce record and its stream description. If the record contains an attributes key, it returns a copy without that key; otherwise it returns the record unchanged.

**Call relations**: This is a connector cleanup hook used after records are fetched. It sits after paginate in the practical flow: paginate yields raw Salesforce records, and flatten shapes each record into the simpler form expected by the rest of the system.


### Advertising and social analytics
Connectors for pulling ad account structures, campaign entities, social content, and performance metrics from advertising and social APIs.

### `extensions/sources/ufo_ext_sources/facebook_ads.py`

`io_transport` · `during source sync when Facebook Ads streams are read`

This connector is the bridge between UFO and Facebook Ads. Without it, the system would not know which Facebook Ads web addresses to call, how to page through long result lists, or how to turn Facebook's responses into consistent records.

The file defines the available Facebook Ads streams first. A stream is a kind of data the system can sync, such as campaigns or ad insights. Each stream says which field identifies a record, and which date field can be used as a cursor. A cursor is a saved marker, like a bookmark, that lets a later sync ask only for data newer than the last successful run.

The FacebookAdsConnector then does the actual reading. It starts by asking Facebook for the user's ad accounts. Most other data belongs under an ad account, so the connector loops through every account and asks for that account's campaigns, ad sets, ads, or insights. Long Facebook responses are fetched page by page by following Facebook's own `paging.next` link, much like clicking “next page” until there are no more pages.

For campaigns, ad sets, and ads, the connector filters out records older than the cursor. For insights, it asks for daily ad-level metrics either since the cursor date or for the last 90 days on a first run. It also creates a stable id for each insight row, because Facebook's metric rows do not naturally come with one.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 74–87)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared page-reader for Facebook Graph API list responses. It keeps asking Facebook for the next page of results until Facebook stops giving a next-page link.

**Data flow**: It receives an HTTP client, an API path or full next-page URL, and optional query parameters. It calls the raw GET helper, reads the JSON response, extracts the list stored under `data`, yields that list when it is not empty, then follows the `paging.next` URL for the next round. The output is an async stream of record batches, not one giant list.

**Call relations**: The account, child-object, and insights readers all rely on this function whenever they need to walk through Facebook's paginated API. It uses `records_at` to safely pull the `data` array out of each response before handing each page back to its caller.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 89–94)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function fetches all ad accounts available to the authenticated Facebook user. These accounts are the starting points for almost every other Facebook Ads request.

**Data flow**: It starts with a fixed list of account fields to request, then asks `_paged` for `/me/adaccounts`. As each page arrives, it adds those account records to one list. It returns the complete list of ad account dictionaries.

**Call relations**: The top-level `paginate` method uses this directly for the `ad_accounts` stream. The campaign, ad set, ad, and insights readers also call it first, because they must know which ad accounts to query before they can fetch account-specific data.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 96–121)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches account-owned objects such as campaigns, ad sets, and ads. It is used when the same kind of object must be collected separately from each ad account.

**Data flow**: It receives the stream to read and an optional cursor. It chooses the right Facebook fields for that stream, fetches all ad accounts, then loops through the accounts one by one. For each valid account id, it requests the account's objects page by page. If a cursor is present, it keeps only records whose cursor field is newer than that saved value. Before yielding a page, it adds context such as the ad account id and name, so the records still show where they came from.

**Call relations**: `paginate` calls this when the requested stream is `campaigns`, `ad_sets`, or `ads`. This function depends on `_accounts` to discover the accounts, `_paged` to read each Facebook list, and `with_context` to attach account information to each returned record batch.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 123–166)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches daily ad performance metrics, such as impressions, clicks, spend, reach, and click-through rate. It turns Facebook's daily report rows into stable records the sync system can remember.

**Data flow**: It builds the fields and reporting options for an ad-level daily insights request. If a cursor exists, it asks Facebook for rows from that cursor date through today; otherwise it asks for the last 90 days. It then fetches all ad accounts and requests each account's insights pages. For every row, it creates an `id` by joining the account, campaign, ad set, ad, and date pieces, and adds the ad account id. It yields non-empty batches of these enriched rows.

**Call relations**: `paginate` calls this for the `ads_insights` stream. This function uses `_accounts` to know which accounts to report on, `_paged` to follow Facebook's result pages, `json.dumps` to format the date range for Facebook, and the current UTC date when building an incremental request.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 168–184)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector's main dispatcher for reading a stream. Given a stream name, it chooses the correct reader and yields batches of Facebook Ads records.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. For `ad_accounts`, it returns the account list. For campaigns, ad sets, and ads, it delegates to `_account_children`. For ad insights, it delegates to `_insights`. If someone asks for a stream this connector does not implement, it raises a skip signal instead of pretending it succeeded.

**Call relations**: The broader source-sync framework calls this method when it needs records from a Facebook Ads stream. `paginate` then routes that request to the narrower helper that knows how to read that specific kind of Facebook data.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 186–194)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function makes selected records a little more convenient and consistent after they have been read. At present, it customizes campaign records by normalizing a few commonly used fields.

**Data flow**: It receives one record and its stream definition. If the stream is `campaigns`, it returns a copy of the record with `status` set to the effective status when available and `created_at` copied from Facebook's `created_time`. For all other streams, it returns the record unchanged.

**Call relations**: The sync framework can call this after records are fetched, before they are stored or exposed in the system's standard shape. It does not call other project helpers; it is a final cleanup step for records that need it.


### `extensions/sources/ufo_ext_sources/googleads.py`

`io_transport` · `during source sync, when Google Ads streams are fetched and normalized`

Google Ads has its own query language, called GAQL, which is like SQL for asking Google Ads for specific fields. This connector translates the system’s standard stream requests into GAQL queries, sends them to the Google Ads API, and reshapes the replies into flat records the rest of the system can store and recall.

A key complication is that one signed-in advertiser may have access to several Google Ads customer accounts. So the connector first asks Google which customer IDs are accessible, then runs the same query for each customer. It stamps the customer ID onto every returned row so later code knows where each record came from.

Google Ads also requires a developer token, separate from normal OAuth login. Think of OAuth as the user’s permission slip, and the developer token as Google approving this software to use the Ads API. If the token is missing, or Google refuses access with an authorization error, this file skips the affected stream rather than crashing the whole sync.

Finally, Google’s API often returns nested objects, like a campaign object inside a row. The `flatten` method pulls out the fields the sync system needs most, such as IDs, names, dates, and metrics.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 71–80)

```
def _developer_token(self) -> str
```

**Purpose**: Finds the Google Ads developer token needed for every API request. Someone would use this before talking to Google Ads because OAuth alone is not enough for this API.

**Data flow**: It reads environment variables named `UFO_GOOGLE_ADS_DEVELOPER_TOKEN` or `GOOGLE_ADS_DEVELOPER_TOKEN`. If one is present, it returns that token as text. If neither is present, it raises `StreamSkipped`, which tells the sync runner to skip Google Ads instead of failing unexpectedly.

**Call relations**: When the HTTP client is being prepared, `_make_client` calls this function so the outgoing requests can include Google’s required developer-token header. If this function cannot find the token, the skip signal travels back up before any Google Ads query is attempted.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to call Google Ads and adds the special headers Google requires. This is the setup step that turns a normal authenticated web client into one accepted by the Google Ads API.

**Data flow**: It receives a base URL and an OAuth credential from the surrounding connector framework. It creates the base client, adds the developer token from `_developer_token`, and optionally adds a login customer ID from the environment after removing dashes. It returns the prepared async HTTP client.

**Call relations**: This is part of the connector setup flow before stream fetching begins. It depends on `_developer_token` to supply the required Google Ads approval token, then hands the finished client to later fetch steps such as customer discovery and GAQL searches.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 92–99)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Asks Google Ads which customer accounts the current credential can access. This matters because every actual data query must be run under a specific customer ID.

**Data flow**: It takes an authenticated HTTP client, calls the Google Ads endpoint that lists accessible customers, and reads resource names like `customers/1234567890`. It keeps only valid customer resource names and returns just the ID parts as a list of strings.

**Call relations**: `_query_each_customer` calls this first, before running any stream query. The list it returns becomes the route map for the rest of the sync: each ID is used to send the same GAQL query to that customer’s account.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 101–120)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: Runs one GAQL query for one Google Ads customer and collects the returned rows. It hides the details of Google’s streamed batch response so the rest of the connector can work with a simple list of records.

**Data flow**: It receives an HTTP client, a customer ID, and a GAQL query string. It posts the query to that customer’s `searchStream` endpoint, reads the response JSON, walks through the returned batches, and gathers each result row that is shaped like a dictionary. It returns a list of rows.

**Call relations**: `_query_each_customer` calls this once for each accessible customer ID. This function supplies the raw Google Ads rows that are then tagged with their customer ID and yielded upward to `paginate`.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 122–130)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs the same Google Ads query across every customer account the credential can reach. It is the bridge between “one query” and “all accounts this advertiser can see.”

**Data flow**: It receives an HTTP client and a GAQL query. First it gets customer IDs from `_customer_ids`; then for each customer it calls `_search_stream`. If rows come back, it adds `customer_id` to each row and yields that group as a page of results.

**Call relations**: `paginate` uses this helper for every supported stream, such as campaigns or metrics. It coordinates `_customer_ids` and `_search_stream`, then hands pages of customer-tagged rows back to the stream sync flow.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 132–200)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right Google Ads query for each stream and yields pages of records for the sync system. This is the main read path for customers, campaigns, ad groups, ads, metrics, and customer-client links.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing where a previous sync left off. Based on the stream name, it builds a GAQL `SELECT` query, then uses `_query_each_customer` to run it across accessible accounts and yield pages of rows. For campaign metrics, it uses the cursor date if available, otherwise it asks for roughly the last 90 days. If the stream is unknown, or Google refuses access with a 401 or 403 status, it raises `StreamSkipped`; other HTTP errors are allowed to continue upward.

**Call relations**: This is the method the connector framework calls when it needs records for a stream. It delegates the repeated per-customer work to `_query_each_customer`, and it uses `StreamSkipped` to tell the larger sync that a stream should be ignored when it is unsupported or not authorized.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 202–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns Google Ads’ nested response rows into flatter records with the key fields the sync system expects. This makes later storage and lookup simpler and more consistent.

**Data flow**: It receives one raw record and the stream it belongs to. For customer, campaign, and campaign-metrics streams, it safely pulls nested pieces like `customer`, `campaign`, `segments`, and `metrics` into top-level fields such as `id`, `resource_name`, `name`, `date`, `campaign_id`, `impressions`, `clicks`, and `cost_micros`. If the stream does not need special shaping, it returns the original record unchanged.

**Call relations**: After `paginate` has yielded raw rows from Google Ads, the broader source sync flow can call `flatten` to normalize each row. It relies on `dict_or_empty` so missing or malformed nested objects do not cause simple field extraction to crash.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/instagram.py`

`io_transport` · `during source sync`

Instagram business accounts are reached through Facebook Pages, so this connector starts at the grant's Facebook Pages and fans outward from there. Think of it like entering a building through the front desk: first it asks for the Pages, then finds the Instagram business account attached to each Page, then visits that account's media, stories, and statistics.

The file defines the streams this source can produce: pages, Instagram accounts, media, media insights, stories, story insights, and user-level insights. A stream is one kind of record the sync system can ask for. The connector does not store an access token itself; the wider runner supplies an authenticated HTTP client.

Most Facebook Graph API lists arrive in pages: a response contains a `data` list and sometimes a `paging.next` URL for the next batch. The helper `_paged` follows those links. Media and stories can be synced incrementally by comparing their timestamp with a saved cursor, which is a watermark saying “we already saw records up to here.”

The connector is careful about permissions. If a whole account walk is refused, it reports the stream as skipped rather than crashing the full run. If a single media or story insight cannot be read, it silently skips that object and keeps going. This matters because Instagram permissions can vary from account to account and object to object.

#### Function details

##### `InstagramConnector._paged`  (lines 92–109)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads a Facebook Graph API collection that may span multiple response pages. It follows the API's `paging.next` link until there are no more results.

**Data flow**: It receives an HTTP client, a starting API path, and optional query parameters. It requests that path, reads the JSON response, extracts the list under `data`, yields that list if it is not empty, then moves to the next page URL if the response provides one. The output is an asynchronous stream of record batches.

**Call relations**: Lower-level collection readers use this whenever they need to walk a Graph API list. `_pages` uses it to read `/me/accounts`, and `_account_collection` uses it to read each Instagram account's media or stories.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 111–116)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function fetches the Facebook Pages available to the current grant, including each Page's linked Instagram business account if one exists. It is the starting point for almost everything else in this connector.

**Data flow**: It builds a request asking for Page IDs, names, and embedded Instagram business account details. It uses `_paged` to collect all result pages from `/me/accounts`, combines the batches into one list, and returns that list of Page records.

**Call relations**: The public `paginate` method calls this when the requested stream is `pages`. `_instagram_accounts` also calls it because Instagram business accounts are discovered through Facebook Pages.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, paginate).


##### `InstagramConnector._instagram_accounts`  (lines 118–128)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function extracts the Instagram business accounts attached to the available Facebook Pages. It also adds the Page ID and Page name so each Instagram account can be traced back to its Facebook Page.

**Data flow**: It starts by calling `_pages` to get all Pages. For each Page, it looks for an `instagram_business_account` object with an ID. It stores accounts by ID to avoid duplicates, adds `page_id` and `page_name`, and returns the unique accounts as a list.

**Call relations**: The public `paginate` method calls it directly for the `instagram_accounts` stream. `_account_collection` and `_user_insights` call it first because they need account IDs before they can ask Instagram for media, stories, or account-level metrics.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _user_insights, paginate).


##### `InstagramConnector._account_collection`  (lines 130–151)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads a collection that belongs to each Instagram account, such as media posts or stories. It also applies incremental filtering so old records do not need to be processed again.

**Data flow**: It receives the collection name, the fields to request, and an optional cursor plus cursor field. It first gets Instagram accounts, then asks the Graph API for the chosen collection under each account ID. If a cursor is present, it keeps only records whose cursor field is newer than that saved value. Before yielding a batch, it adds the Instagram account ID to each record for context.

**Call relations**: The public `paginate` method uses this for the `media` and `stories` streams. It relies on `_instagram_accounts` to find account IDs and `_paged` to walk API pages, then uses `with_context` from the source SDK to attach account context to each record.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 153–187)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads insight metrics for individual media or story objects. These are per-object statistics such as reach, impressions, replies, or video views.

**Data flow**: It receives an asynchronous stream of media or story batches, plus a list of metric names and the destination stream name. For each object with an ID, it calls that object's `/insights` endpoint. It turns each returned insight into a record with a stable ID made from the object ID and metric name, and includes the parent object ID. If one object's insight endpoint returns a common permission-or-missing error, it skips that object and continues.

**Call relations**: The public `paginate` method uses this for `media_insights` and `story_insights`. In those cases, `paginate` first creates a media or stories stream, then hands that stream into `_object_insights` so each object can be expanded into metric records.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 189–219)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads daily account-level Instagram metrics, such as impressions, reach, and profile views. These are about the account as a whole, not a single post or story.

**Data flow**: It starts with the known Instagram accounts. For each account ID, it requests daily insights from the account's `/insights` endpoint. It walks each metric's `values` list, skips values at or before the saved cursor, and creates one output record per account, metric, and end time. The result is yielded in batches.

**Call relations**: The public `paginate` method calls this when the requested stream is `user_insights`. It depends on `_instagram_accounts` to know which accounts to query, and uses SDK helpers to safely read `data` and normalize the metric `values` list.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (paginate); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 221–299)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector's main dispatcher. The sync system asks it for one stream, and it chooses the right reading path for that stream.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional cursor. Based on the stream name, it calls the appropriate helper and yields batches of records. For media, stories, and user insights, it passes the cursor along so older data can be skipped. If the stream is unknown, it reports the stream as skipped. If Instagram refuses access with an authorization-style error, it converts that into a skip message instead of treating it as a hard failure.

**Call relations**: This is the method the broader source-sync runner calls to retrieve records. It delegates to `_pages`, `_instagram_accounts`, `_account_collection`, `_object_insights`, and `_user_insights` depending on the requested stream, and raises `StreamSkipped` when the run should record a skipped stream rather than crash.

*Call graph*: calls 6 internal fn (__init__, _account_collection, _instagram_accounts, _object_insights, _pages, _user_insights).


### Support and helpdesk systems
Connectors for syncing tickets, conversations, users, knowledge-base content, and related service-desk records.

### `extensions/sources/ufo_ext_sources/freshdesk.py`

`io_transport` · `source sync / API reading`

Freshdesk exposes many kinds of records, but they do not all come out of the API in the same way. This file is the adapter that knows those differences. Without it, the larger system would not know which Freshdesk URL to call, how to sign in, how to move through pages of results, or how to follow parent-child structures like tickets to conversations or categories to articles.

The file first defines the streams the connector can read, such as tickets, contacts, companies, solution articles, and discussion comments. A stream is simply one named kind of data the sync system can ask for. For simple streams, the connector maps the stream name directly to a Freshdesk API path.

The main class, FreshdeskConnector, creates an HTTP client using Freshdesk’s API key as basic authentication. It then chooses the right paging strategy for each stream. Some Freshdesk endpoints use normal “next page” links in response headers. Tickets use numbered pages and can be filtered by an updated-since cursor for incremental syncing. Other data is nested, like “get all folders for each category” or “get all conversations for each ticket,” so the connector first reads the parent records and then asks Freshdesk for each child list. If Freshdesk rejects access with 401 or 403, the stream is skipped with a clear explanation instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 55–69)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a StreamSpec, which is the project’s small description of one readable Freshdesk data stream. It keeps the stream list concise by filling in common defaults like using the stream name as the API object name and using id as the primary key.

**Data flow**: It receives a stream name and optional details such as the Freshdesk object name, primary key, cursor field, and whether the stream is considered canonical. It combines those choices with defaults and returns a StreamSpec object that the connector later exposes to the sync system.

**Call relations**: This helper is used while the module is loaded to build the FRESHDESK_STREAMS list. It hands each finished StreamSpec to the connector class through streams_list, so the rest of the system can discover what Freshdesk data is available.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector._make_client`  (lines 109–124)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to a particular Freshdesk tenant. It also applies authentication, timeouts, and standard JSON headers so later code can focus on asking for data.

**Data flow**: It receives a base URL and a resolved credential. It trims the URL, prepares time limits and headers, then either uses a provided transport from an authentication broker or builds basic authentication from the API key. It returns an httpx.AsyncClient ready to make Freshdesk API requests; if no usable credential is present, it raises an error.

**Call relations**: The broader RestConnector machinery calls this when a sync run needs a client. The client it returns is then passed into paginate and the lower-level pagination helpers so they can fetch records from Freshdesk.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 127–137)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: This builds the query parameters Freshdesk expects when reading tickets. It makes sure ticket pages are ordered by update time, limited to the standard page size, and optionally restricted to records changed after a saved cursor.

**Data flow**: It receives an optional cursor and a page number. It creates a dictionary containing per-page count, page number, sorting choices, and requested included fields. If a cursor is present, it adds updated_since. The result is a ready-to-send parameter dictionary for the tickets API.

**Call relations**: FreshdeskConnector._paginate_tickets calls this once for each ticket page it asks Freshdesk for. It is the small parameter-building step inside the larger ticket paging loop.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 139–213)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a Freshdesk stream. Given a stream name, it chooses the correct way to page through Freshdesk’s API and yields batches of records back to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, then delegates to the right helper: tickets, conversations, two-level trees, three-level trees, or ordinary link-header pagination. It yields lists of record dictionaries as they arrive. If Freshdesk denies access with 401 or 403, it turns that into a StreamSkipped message explaining that the key or permissions are not enough.

**Call relations**: The sync framework calls paginate when it wants records for one stream. paginate then calls FreshdeskConnector._paginate_tickets, FreshdeskConnector._paginate_conversations, FreshdeskConnector._paginate_two_level, FreshdeskConnector._paginate_three_level, or FreshdeskConnector._paginate_link_header depending on the stream, acting like a traffic director for all Freshdesk reads.

*Call graph*: calls 6 internal fn (__init__, _paginate_conversations, _paginate_link_header, _paginate_three_level, _paginate_tickets, _paginate_two_level).


##### `FreshdeskConnector._paginate_link_header`  (lines 215–222)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Freshdesk endpoints that use standard HTTP Link headers to point to the next page. A Link header is like a “next aisle this way” sign attached to each API response.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the base REST helper to walk pages of size 100 using the response’s next-link information. It yields each page as a list of records.

**Call relations**: paginate uses this for simple streams, and the nested pagination helpers use it whenever they need to read parent or child lists. It delegates the detailed link-following work to the inherited _get_link_header_pages helper.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 224–241)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Freshdesk tickets using Freshdesk’s special numbered-page API. It supports incremental syncing by asking only for tickets updated after a cursor when one is available.

**Data flow**: It receives an HTTP client and optional cursor. Starting at page 1, it builds ticket query parameters, requests the ticket endpoint, extracts the list of records, and yields that list. It stops when there are no records, when a page is not full, or when it reaches Freshdesk’s 300-page ceiling.

**Call relations**: paginate calls this directly for the tickets stream. FreshdeskConnector._paginate_conversations also calls it first, because conversations must be discovered by walking through the tickets that contain them. It calls FreshdeskConnector._build_tickets_params for each page request.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, paginate).


##### `FreshdeskConnector._paginate_conversations`  (lines 243–259)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads conversations by first finding tickets, then fetching the conversations attached to each ticket. This is needed because Freshdesk conversations are reached through a ticket-specific URL rather than one simple global list.

**Data flow**: It receives an HTTP client and optional cursor. It reads ticket pages, takes each ticket id, requests that ticket’s conversations, adds the ticket_id to each conversation if it is missing, and yields the conversation pages. Tickets without an id are ignored because there is no safe child URL to call.

**Call relations**: paginate calls this for the conversations stream. It depends on FreshdeskConnector._paginate_tickets to find the tickets and FreshdeskConnector._paginate_link_header to walk each ticket’s conversation pages.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (paginate).


##### `FreshdeskConnector._paginate_two_level`  (lines 261–272)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads data arranged as a parent list with one child list under each parent. Examples include folders under solution categories or comments under discussion topics.

**Data flow**: It receives an HTTP client, a parent API path, and a child path template containing an id placeholder. It reads each page of parents, pulls the id from each parent record, formats the child URL with that id, and yields the child pages it finds. Parent records without an id are skipped.

**Call relations**: paginate calls this for streams such as canned responses, solution folders, discussion forums, discussion topics, and discussion comments. It uses FreshdeskConnector._paginate_link_header for both the parent pages and each child list.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


##### `FreshdeskConnector._paginate_three_level`  (lines 274–297)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads data arranged in three layers, such as solution categories, then folders, then articles. It is used when the desired records live two steps below the top-level Freshdesk list.

**Data flow**: It receives an HTTP client plus paths for the root level, middle level, and leaf level. It reads root records, extracts each root id to fetch middle records, then extracts each middle id to fetch leaf records. It yields the leaf pages, skipping any record that does not have the id needed for the next step.

**Call relations**: paginate calls this for solution_articles. Internally it repeatedly uses FreshdeskConnector._paginate_link_header to read each level of the tree before handing the final article pages back to the caller.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/intercom.py`

`io_transport` · `source sync`

Intercom is a customer support platform, and its API does not expose every kind of data in the same way. Some records are found through a search endpoint, some through a scrolling company endpoint, some through one-off list endpoints, and some must be fetched by first reading a parent record and then asking for its children. This file is the adapter that hides those differences from the rest of UFO.

The central class, IntercomConnector, is a read-only connector built on the shared RestConnector base. It declares which Intercom streams exist, adds the Intercom API version header to every HTTP client, and chooses the right paging method for each stream. Paging means repeatedly asking Intercom for the next batch of records until there are no more, like turning pages in a catalog.

The file also lightly reshapes some records. Intercom often nests useful fields inside envelopes such as source, author, contacts, or companies. The flattening helpers copy important nested values onto top-level keys so later SQL-style transforms can use them easily. It also converts integer cursor fields such as updated_at into strings, because UFO’s watermark system stores cursor values as strings. If Intercom refuses access with a 401 or 403 response, the connector reports the stream as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 52–66)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates a StreamSpec, which is the small description UFO uses to know what an Intercom stream is called, what object it comes from, what field identifies each record, and what field can be used for incremental syncing.

**Data flow**: It receives a stream name and optional details such as source object, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults when details are not provided, then returns a StreamSpec object that the connector later uses when syncing.

**Call relations**: This function is used while the module is being loaded to build the INTERCOM_STREAMS list. That list becomes IntercomConnector.streams_list, so the wider source framework can discover which Intercom data sets are available.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector._make_client`  (lines 103–106)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Intercom and adds the required Intercom API version header. Without this header, Intercom might answer using a different API version than this connector expects.

**Data flow**: It receives the base API URL and a Credential containing access information. It asks the parent RestConnector to build the normal authenticated client, adds the Intercom-Version header, and returns the prepared client.

**Call relations**: The shared connector framework calls this when it is setting up network access for Intercom. After this, all pagination functions use the returned client for their GET and POST requests.


##### `IntercomConnector._build_search_body`  (lines 109–139)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: This builds the request body for Intercom search endpoints, such as conversations, contacts, and tickets. It tells Intercom how many records to return, where to continue from inside a result set, and which updated records to include.

**Data flow**: It receives the stream being searched, the saved sync cursor, and Intercom’s page cursor called starting_after. It creates a JSON body with pagination, sorting, and a query that asks for records whose cursor field is greater than the saved cursor. The result is a dictionary ready to send in a POST request.

**Call relations**: IntercomConnector._paginate_search uses this for normal search-based streams. IntercomConnector._paginate_conversation_parts also uses it first to find conversations before fetching their parts.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 142–147)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: This small helper safely picks the first dictionary from a list. It is used when Intercom wraps related records, such as contacts or companies, inside nested lists.

**Data flow**: It receives any value. If the value is a non-empty list and its first item is a dictionary, it returns that dictionary. Otherwise it returns nothing.

**Call relations**: The flattening helpers use this when they want a single related record from an Intercom envelope. It keeps those helpers from repeating the same safety checks.


##### `IntercomConnector._flatten_conversation`  (lines 150–167)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes conversation records easier to use by copying important nested fields onto top-level keys. For example, it exposes the conversation source and the first requester contact ID.

**Data flow**: It receives one conversation record. It copies the record, looks inside the source object for type, subject, and body, and looks inside contacts for the first contact ID. It returns the copied record with extra flat fields added when those values exist.

**Call relations**: IntercomConnector.flatten calls this only for the conversations stream. Its output is then passed back to the shared sync machinery as the cleaner version of the record.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 170–179)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes conversation part records easier to query by exposing author information as simple top-level fields. A conversation part is an individual message or event inside a larger conversation.

**Data flow**: It receives one conversation part record. It copies the record, checks whether the author field is a nested object, and if so adds author_type and author_id. It returns the copied and enriched record.

**Call relations**: IntercomConnector.flatten calls this for the conversation_parts stream. The paginator for conversation parts adds conversation_id before records reach this step, and this function deliberately keeps that value unchanged.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 182–190)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes contact records easier to connect to companies by copying the first associated company ID into a simple org_id field.

**Data flow**: It receives one contact record. It copies the record, looks for a nested companies envelope, picks the first company if present, and stores its id or company_id as org_id. It returns the copied record with that extra field when available.

**Call relations**: IntercomConnector.flatten calls this for the contacts stream. The resulting flat org_id helps later transforms link people to organizations without digging through nested JSON.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 192–206)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This is the connector’s main record-cleanup step. It makes selected Intercom records flatter and converts integer cursor values into strings so UFO’s incremental sync watermark can store them.

**Data flow**: It receives a raw Intercom record and the StreamSpec that says which stream it came from. Depending on the stream name, it sends the record through the right flattening helper. Then, if the stream has a cursor field and that value is an integer, it returns a copy where the cursor is the same number written as text. Otherwise it returns the record as-is.

**Call relations**: The shared sync flow calls this after records are read from Intercom and before they are saved or passed onward. It hands off to IntercomConnector._flatten_conversation, IntercomConnector._flatten_conversation_part, or IntercomConnector._flatten_contact for stream-specific cleanup.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 208–252)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the traffic director for reading Intercom streams. Given a stream, it chooses the correct paging strategy for that stream’s API shape and yields batches of records.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional saved cursor. It checks the stream name, delegates to the matching private pagination function, and yields each list of records that function produces. If Intercom replies with a 401 or 403 refusal, it turns that into a StreamSkipped error so the run records the stream as skipped.

**Call relations**: The broader RestConnector sync process calls this whenever it needs records for an Intercom stream. This function then calls one of the specialized paginators: search, scroll, list, attributes, conversation parts, company segments, or activity logs.

*Call graph*: calls 8 internal fn (__init__, _paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search).


##### `IntercomConnector._paginate_search`  (lines 254–274)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams that use Intercom’s search API, such as conversations, contacts, and tickets. It keeps asking for the next search page until Intercom says there is no next cursor.

**Data flow**: It receives the HTTP client, stream description, and saved cursor. For each loop, it builds a search body, sends a POST request, pulls the record list from the response, yields it if non-empty, then reads Intercom’s starting_after value for the next page. When there is no next page token, it stops.

**Call relations**: IntercomConnector.paginate calls this for streams listed in the search path map. It relies on IntercomConnector._build_search_body to format each POST request correctly.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_scroll`  (lines 276–290)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads companies through Intercom’s scroll API, which is a cursor-based way to walk through a large company list. It is like being handed a bookmark after each batch so the next request can continue in the right place.

**Data flow**: It receives the HTTP client. It starts without a scroll parameter, requests /companies/scroll, yields the returned company records, then uses the response’s scroll_param for the next request. It stops when no records or no next scroll parameter are returned.

**Call relations**: IntercomConnector.paginate calls this for the companies stream. The records it yields then continue through the shared sync pipeline and may later be flattened or stored by the base framework.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_list`  (lines 292–304)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads simple Intercom list endpoints such as admins, tags, teams, and segments. These endpoints return their records in one response rather than through a long paging loop.

**Data flow**: It receives the HTTP client and stream description. It chooses the endpoint path for that stream, sends a GET request, then looks for a list under either the stream name or data. If it finds a non-empty list, it yields that one batch.

**Call relations**: IntercomConnector.paginate calls this for streams in the list path map. It is the simplest pagination path because there is no follow-up page token to chase.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_attributes`  (lines 306–315)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Intercom data attribute definitions for companies or contacts. These are metadata fields that describe what custom information Intercom can store for those objects.

**Data flow**: It receives the HTTP client and stream description. It translates the stream name into the model Intercom expects, asks /data_attributes with that model as a query parameter, and yields the response’s data list if it contains records.

**Call relations**: IntercomConnector.paginate calls this for company_attributes and contact_attributes. It lets those metadata streams fit the same batch-yielding pattern as normal record streams.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_conversation_parts`  (lines 317–349)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the individual parts inside conversations, such as replies or notes. Intercom does not provide these as a standalone search stream, so the connector first finds conversations and then fetches each conversation’s details.

**Data flow**: It receives the HTTP client and saved cursor. It searches conversations page by page, then for each conversation with an ID it requests /conversations/{id}. It pulls out the conversation_parts list, stamps each part with the parent conversation_id if missing, and yields the parts when there are any. It continues until the conversation search has no next page.

**Call relations**: IntercomConnector.paginate calls this for the conversation_parts stream. It uses IntercomConnector._build_search_body to page through parent conversations before making detail requests for their child parts.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_company_segments`  (lines 351–375)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the segment memberships for each company. Because segments are attached to companies, the connector first scrolls through companies and then asks Intercom for each company’s segments.

**Data flow**: It receives the HTTP client. It pages through /companies/scroll using scroll_param, and for each company with an ID it requests /companies/{id}/segments. It adds the company_id to each segment record if missing, yields any segment batches, and stops when the company scroll has no more records or no next scroll token.

**Call relations**: IntercomConnector.paginate calls this for the company_segments stream. It is a substream paginator: it fans out from parent company records into related child segment records.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_activity_logs`  (lines 377–399)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads admin activity logs from Intercom, optionally starting after a saved created_at cursor. Activity logs describe actions taken by admins inside Intercom.

**Data flow**: It receives the HTTP client and optional cursor. If a cursor exists, it sends it as created_at_after on the first request to /admins/activity_logs. For each response, it yields any activity_logs records, then follows the next page link from the pages section. If the next link is a full URL, it trims it down to a path before the next request. It stops when there is no next link.

**Call relations**: IntercomConnector.paginate calls this for the activity_logs stream. This paginator handles Intercom’s next-link style of paging, which differs from both search cursors and company scroll cursors.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/zendesk.py`

`io_transport` · `during source sync, while reading Zendesk API pages`

Zendesk exposes its data through many web API endpoints, and those endpoints do not all behave the same way. This file is the adapter that makes them look consistent to the rest of the project. It defines the list of Zendesk streams the system can read, such as tickets, comments, users, schedules, audit logs, articles, and votes. A stream is one category of records to sync.

The main class, ZendeskConnector, is a read-only connector. It does not create or update Zendesk data. Its job is to fetch records page by page from Zendesk and yield batches of plain dictionaries. Some Zendesk objects support incremental export, meaning the connector can ask for records changed since a saved time instead of rereading everything. Other objects use ordinary page links. A few streams need special treatment: ticket comments are buried inside ticket event records, and user identities must be fetched by first reading users and then asking Zendesk for each user’s identities.

The file also smooths over Zendesk quirks. For tickets, Zendesk can return related user records alongside the ticket records; this connector copies user emails onto the ticket when possible, like attaching a name tag to a package before passing it along. If Zendesk refuses access with a permission or authentication error, the connector reports that the stream should be skipped instead of pretending the data is empty.

#### Function details

##### `_stream`  (lines 58–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: Creates a StreamSpec, which is the small description the sync system uses to know what a Zendesk stream is called, where it comes from, and which fields mark identity and time. It keeps the long stream list readable by avoiding repeated setup code.

**Data flow**: It receives a stream name and optional details such as the Zendesk source path, primary key, and time fields. It fills in sensible defaults when details are not supplied, then returns a StreamSpec object that represents one readable Zendesk data category.

**Call relations**: This helper is used while building the file’s Zendesk stream list. It hands each finished StreamSpec to the connector configuration so later pagination code knows which Zendesk endpoint and fields to use.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 142–167)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: Adds useful information from related records that Zendesk returned alongside the main records. In this file, it is used to copy requester, submitter, and assignee email addresses from sideloaded user records onto ticket records.

**Data flow**: It receives the main records, the full API page, and instructions for which related records to match. It builds a quick lookup table from the sideloaded arrays, finds matching related entries by ID, and writes email fields into the main records when they are missing. It changes the given records in place and returns nothing.

**Call relations**: The incremental cursor paginator calls this when it is reading tickets with included users. The paginator fetches a Zendesk page, this helper enriches the ticket rows, and then the paginator yields the improved records to the rest of the sync.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 176–177)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: Decides which field inside a Zendesk API response contains the list of records for a stream. Most streams use their own name, but some Zendesk endpoints use different response names.

**Data flow**: It receives a StreamSpec. It checks a small override table for special cases, such as account_attributes using attributes, and returns the response field name that should be read.

**Call relations**: The default paginator calls this before reading ordinary paged endpoints. It gives the paginator the correct key to pull records out of each JSON response.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 180–192)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: Converts the saved sync cursor into the Unix timestamp format Zendesk expects for incremental exports. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor that may be missing, already numeric, or written as a date-time string. Missing or unreadable values become 0, numeric strings become integers, and valid date-time strings are parsed and converted to seconds. The result is an integer timestamp.

**Call relations**: The incremental ticket, user, organization, ticket event, and identity flows call this when building their starting API URL. It turns the system’s remembered position into the form Zendesk needs.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 195–204)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: Turns Zendesk’s full next-page URL into a path that the connector can request through its configured Zendesk base URL. This avoids mixing tenant-specific hostnames into the connector’s internal request paths.

**Data flow**: It receives a next-page URL or nothing. If there is no usable path, it returns nothing. Otherwise it extracts the URL path and query string, then returns them as a relative API path.

**Call relations**: All pagination methods use this after each API response. Zendesk tells the connector where the next page is; this helper cleans that link into the form used for the next request.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 206–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right paging strategy for a Zendesk stream and yields batches of records. It is the main doorway the rest of the sync system uses to read Zendesk data.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. Based on the stream name, it delegates to the special ticket comment flow, the user identity flow, the incremental cursor flow, or the ordinary page-by-page flow. It yields each batch produced by that chosen flow. If Zendesk replies with an authentication or permission refusal, it raises StreamSkipped so the system knows that stream could not be read with the current grant.

**Call relations**: The wider RestConnector machinery calls this when it wants records for a stream. This method acts like a traffic director, sending each stream to the helper that knows Zendesk’s shape for that kind of data.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 232–253)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads high-volume Zendesk objects through Zendesk’s incremental cursor export, which is designed for syncing changes over time. This is used for streams such as tickets, users, organizations, and ticket metric events.

**Data flow**: It receives an HTTP client, a stream description, and the saved cursor. It builds a Zendesk incremental export URL using the cursor timestamp, fetches one page, pulls out the records, optionally enriches ticket records with sideloaded user emails, and yields non-empty batches. It follows Zendesk’s after_url or next_page until Zendesk says the stream has ended.

**Call relations**: The main paginate method calls this for streams listed as incremental cursor streams. It relies on _cursor_to_unix to start at the right time, _apply_sideload to improve ticket records when users are included, and _next_page_path to move from one page to the next.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 255–265)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Zendesk streams that use the normal page-by-page API style. This covers many lower-volume or administrative endpoints.

**Data flow**: It receives an HTTP client and a stream description. It builds the first API path, finds the correct response field for that stream, fetches each page, yields any records found, and follows Zendesk’s next_page link until there are no more pages.

**Call relations**: The main paginate method calls this when a stream does not need special incremental or nested fetching. It uses _data_field to know where records are inside the response and _next_page_path to continue through the result pages.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 267–299)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Extracts ticket comments from Zendesk’s ticket events feed. Zendesk does not provide these comments in the same simple shape as many other objects, so this function digs them out and turns them into regular comment records.

**Data flow**: It receives an HTTP client and an optional cursor. It asks Zendesk for incremental ticket events including comment events, then scans each event’s child events. When a child event is a comment, it copies that child into a new record, adds the parent ticket_id, normalizes numeric creation times into readable timestamp strings, and yields batches of comments. It follows page links until the event stream ends.

**Call relations**: The main paginate method calls this only for the ticket_comments stream. It uses _cursor_to_unix to choose the starting time, datetime conversion to clean up numeric timestamps, and _next_page_path to keep moving through Zendesk’s event pages.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate); 1 external calls (fromtimestamp).


##### `ZendeskConnector._paginate_user_identities`  (lines 301–326)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads identity records for users, such as alternate login or contact identities, by first finding changed users and then fetching each user’s identities. This is needed because Zendesk exposes identities under individual user URLs rather than as one flat global stream.

**Data flow**: It receives an HTTP client and an optional cursor. It pages through incremental users from the cursor time, skips invalid user entries, and for each user with an ID requests that user’s identities. It yields identity batches when found, follows identity page links for each user, and then follows the user export page links until Zendesk says the user stream is finished.

**Call relations**: The main paginate method calls this for the users_identities stream. It uses _cursor_to_unix to start from the saved sync point and _next_page_path both for user pages and for each user’s identity pages.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).


### Marketing and forms
Connectors for reading marketing audiences, campaigns, events, email activity, forms, and response data.

### `extensions/sources/ufo_ext_sources/klaviyo.py`

`io_transport` · `data sync / API pagination`

Klaviyo’s API returns many kinds of marketing data, but it wraps each item in a nested JSON shape and uses its own rules for paging, filtering, dates, and permissions. This file is the adapter that makes Klaviyo look like the project’s standard source connector.

It first defines the Klaviyo streams: the named collections the system can read, such as profiles, lists, segments, campaigns, flows, events, templates, tags, coupons, catalog records, images, webhooks, and accounts. Each stream says what its main ID is and which date field should be used to continue an incremental sync. An incremental sync means “only ask for records changed since the last saved point,” like resuming a book from a bookmark instead of starting over.

The `KlaviyoConnector` then sets up Klaviyo-specific HTTP details: the base API address, the required API revision header, and Klaviyo’s private-key authorization format. Its pagination code follows Klaviyo’s `links.next` URLs until there are no more pages. Its flattening code turns nested API records into simpler dictionaries, lifting useful fields like campaign subject lines, profile email consent, event metric names, and relationship IDs to the top level. If Klaviyo refuses access to a stream because the key lacks permission, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 36–54)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', created_at_field: str='created', updated_at_field: str | None='updated', canonical:
```

**Purpose**: This helper creates a standard stream description for one Klaviyo resource. It keeps the long stream list readable by filling in common defaults, such as using `id` as the main identifier and `updated` as the usual change-tracking date.

**Data flow**: It receives a stream name plus optional details like the API object name, date fields, and whether the stream is considered canonical. It packages those choices into a `StreamSpec`, which is the project’s small description object for a syncable collection, and returns it for the stream list.

**Call relations**: The file uses this helper while building the Klaviyo stream catalog. Instead of writing each `StreamSpec` by hand, the stream list calls `_stream` repeatedly so each Klaviyo collection is registered in the same shape.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 113–121)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client so every request speaks Klaviyo’s expected language. Klaviyo requires a pinned API revision header, and when a private key is available directly, it must be sent as `Klaviyo-API-Key`, not as a normal bearer token.

**Data flow**: It receives the API base URL and a credential. It asks the parent REST connector to create the basic client, then adds Klaviyo’s revision header and, when present, the private API key authorization header. It returns the ready-to-use client.

**Call relations**: This is part of connector setup before requests are sent. The broader REST connector supplies the base client, and this method adds Klaviyo-specific headers so later pagination and fetch calls are accepted by Klaviyo.


##### `KlaviyoConnector._next_path`  (lines 124–135)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This turns Klaviyo’s next-page link into the kind of path the existing HTTP client can request. Klaviyo gives a full absolute URL, but the client is already tied to the base Klaviyo address, so it only needs the path and query string.

**Data flow**: It receives a `links.next` value, which may be missing or empty. If there is a usable URL, it parses it, keeps the path and any query parameters, and returns that shorter request path. If there is no next page, it returns `None`.

**Call relations**: During pagination, `KlaviyoConnector.paginate` reads the next-page link from each API response and hands it to `_next_path`. The returned path becomes the next request target, or `None` tells the loop to stop.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 138–143)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: This chooses the correct date field to use when asking Klaviyo for records in time order. Most Klaviyo resources use `updated`, but a few use `updated_at`, and events use `datetime`.

**Data flow**: It receives a stream description. It checks the stream name against the small groups that need special cursor fields, then returns the field name Klaviyo expects for filtering and sorting.

**Call relations**: This supports the first-page query builder. When `KlaviyoConnector._initial_query` needs to construct a filter or sort order, it uses this method so each stream gets the right timestamp field.


##### `KlaviyoConnector._initial_query`  (lines 146–160)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the query parameters for the first request to a Klaviyo stream. It sets the page size, adds incremental sync filtering when there is a saved cursor, and asks for a few useful extras on special streams.

**Data flow**: It receives a stream description and an optional cursor value from a previous sync. It creates a parameter dictionary with `page[size]`, then adds a Klaviyo filter such as “greater than or equal to this date” when appropriate, plus sorting by the same date field. For profiles it requests subscription details, and for events it requests included metric records. It returns the parameter dictionary for the first API call.

**Call relations**: At the start of `KlaviyoConnector.paginate`, this method supplies the query parameters for the first page. After that, pagination follows Klaviyo’s own next-page URLs, so these initial parameters are not reused.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 163–174)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: This safely pulls an ID out of Klaviyo’s nested relationship format. Klaviyo may omit relationship data, so this helper avoids errors when parts are missing.

**Data flow**: It receives a relationships object and the relationship name to look for, such as `profile` or `metric`. It checks each nested layer before reading `data.id`. If an ID is present, it returns it as text; otherwise it returns `None`.

**Call relations**: The flattening step uses this helper when turning nested Klaviyo records into simpler records. `KlaviyoConnector.flatten` calls it for relationships like an event’s profile, an event’s metric, or a segment’s parent list.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 176–242)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This converts one Klaviyo API record from Klaviyo’s nested JSON shape into a flatter record that is easier for the rest of the system to store, search, and use for incremental sync. It also pulls out a few high-value details that would otherwise stay buried.

**Data flow**: It receives one raw Klaviyo record and the stream it came from. It starts a new flat dictionary with the record ID and type, copies fields from `attributes` to the top level, removes list and segment profile counts so membership changes do not make those records look changed, and then adds stream-specific fields. For example, it extracts profile email consent, campaign subject and sender details, event profile and metric IDs, event message references, and a segment’s parent list ID. It returns the flattened record and does not modify external state.

**Call relations**: This function is the bridge between raw API responses and the project’s standard record shape. When it needs relationship IDs, it hands the nested relationship block to `_lift_relationship_id` rather than repeating the defensive checks itself.

*Call graph*: calls 1 internal fn (_lift_relationship_id).


##### `KlaviyoConnector.paginate`  (lines 244–297)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Klaviyo stream page by page. It starts at the stream’s API endpoint, yields batches of raw records, follows Klaviyo’s next-page links, and turns permission refusals into a clean stream skip.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the last sync. It builds the first query, requests a page, extracts the records, and yields them as a list when any are present. For events, it also uses included metric records to attach a readable metric name to each event’s attributes. After each page, it reads `links.next`, converts it to a request path, and continues until no next page remains. If Klaviyo returns a 401 or 403 refusal, it raises `StreamSkipped`; other HTTP errors continue upward.

**Call relations**: This is the main read loop for Klaviyo data. It calls `_initial_query` to prepare the first request, uses the inherited REST fetching method to get each page, calls `_next_path` to move to the following page, and raises `StreamSkipped` when a stream is inaccessible because the credential lacks the needed scope.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/mailchimp.py`

`io_transport` · `during source sync, when Mailchimp streams are read`

Mailchimp stores useful data in several layers. Some things, like campaigns and audiences, can be fetched directly. Other things only make sense inside a parent, such as members inside an audience list, interests inside an interest category, or email activity inside a campaign report. This file is the map and tour guide for walking that structure safely.

It defines the Mailchimp streams the product can sync, including their main identifier and the date field used for incremental syncing. Incremental syncing means “only ask for records changed since the last successful run,” which avoids rereading everything every time. The connector also knows Mailchimp’s paging style: ask for a fixed number of records with a count and offset, then keep going until the page is short.

The main class, MailchimpConnector, decides which walking pattern each stream needs. For a top-level stream it simply pages through one endpoint. For list-based streams it first fetches all list IDs, then fetches children for each list. For report-based streams it first fetches report IDs, then fetches report children. Email activity has one extra step: Mailchimp groups many actions under one recipient, so this file splits those actions into separate rows and invents a stable ID for each one. If Mailchimp rejects access with a permission or authentication error, the stream is skipped with a clear reason instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 62–80)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str='created_at', updated_at_field: str | None='updated_at', canonical
```

**Purpose**: This helper builds a StreamSpec, which is the small description the sync system uses to know what a Mailchimp stream is called, what its main ID field is, and which time fields matter. It keeps the long stream list readable and consistent.

**Data flow**: It receives a stream name plus optional details like source object name, primary key, cursor field, created time field, updated time field, and whether the stream is canonical. It fills in sensible defaults where details are missing, then returns a StreamSpec object with those settings.

**Call relations**: This helper is used while the module is loaded to create the Mailchimp stream catalog. It hands the completed StreamSpec objects to the connector through MAILCHIMP_STREAMS, so later sync code can ask for each stream by name.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector.flatten`  (lines 148–154)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function lightly normalizes records after they come from Mailchimp. For member records, it creates a common created_at value from Mailchimp’s signup or opt-in timestamps so downstream code has a predictable field to use.

**Data flow**: It receives one Mailchimp record and the stream description. If the stream is list_members or segment_members, it copies the record and adds created_at from timestamp_signup, falling back to timestamp_opt. For all other streams, it returns the record unchanged.

**Call relations**: The broader connector framework calls this after records have been fetched. It does not fetch anything itself; it prepares the row shape that later storage or processing expects.


##### `MailchimpConnector._data_field`  (lines 157–158)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This function tells the connector where Mailchimp puts the actual list of records inside a JSON response. Mailchimp often wraps data under names like lists, members, or emails instead of returning a bare array.

**Data flow**: It receives a stream description, looks up the stream name in the file’s data-field map, and returns the matching JSON key. If there is no special mapping, it returns the stream name itself.

**Call relations**: Pagination helpers call this before reading top-level, per-list, or per-report pages. It gives those helpers the exact response field to pull records from.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 161–168)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function turns a saved sync cursor into the Mailchimp query parameter needed for incremental reads. In plain terms, it asks Mailchimp, “only send me things since this time,” when Mailchimp supports that for the stream.

**Data flow**: It receives a stream description and an optional cursor value. If there is no cursor, no cursor field, or no Mailchimp parameter for that field, it returns an empty parameter set. Otherwise it returns a small dictionary such as since_last_changed mapped to the cursor timestamp.

**Call relations**: The top-level, per-list, segment-member, and per-report pagination paths call this before making requests. Its output is passed into the HTTP paging helper so Mailchimp can filter records on the server side.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 170–226)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading a Mailchimp stream. Given a stream name, it chooses the right route through Mailchimp’s API and yields pages of records to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, sends control to the matching pagination helper, and yields each page that helper produces. If Mailchimp returns 401 or 403, meaning unauthorized or forbidden, it converts that into a StreamSkipped error with a clear message; other HTTP errors are allowed to continue upward.

**Call relations**: The connector framework calls this when it wants records for a stream. This function then hands off to the specific helper for top-level streams, list children, interests, segment members, report children, or email activity.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 228–239)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads simple Mailchimp resources that live at one direct API path, such as lists, campaigns, automations, or reports. It keeps requesting pages until the shared paging helper has no more full pages to return.

**Data flow**: It receives the HTTP client, stream description, API path, and optional cursor. It finds the right JSON data field and cursor query parameters, then asks the base connector’s offset paging helper for pages of up to 500 records. It yields each page unchanged.

**Call relations**: MailchimpConnector.paginate calls this for top-level streams. It relies on _data_field and _cursor_params to prepare the request details before handing the actual page walking to the base REST connector.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 241–258)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the generic page reader for nested Mailchimp endpoints, such as members inside a list or unsubscribes inside a report. It avoids repeating the same offset-paging code in every parent-child walker.

**Data flow**: It receives the HTTP client, endpoint path, the response field that contains records, and optional query parameters. It calls the base offset paging helper with Mailchimp’s page size and count parameter, then yields each page of child records.

**Call relations**: The more specialized helpers call this after they have built the correct nested path. It is the common bridge between those helpers and the lower-level REST paging machinery.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 260–268)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: This function reads IDs from a paged Mailchimp collection. It is used when the connector must first discover parent objects before it can fetch their children.

**Data flow**: It receives an HTTP client, an API path, and the JSON field containing records. It pages through that collection, inspects each row, and yields the row’s id as text when an ID is present.

**Call relations**: _list_ids and _report_ids use this as their shared worker. Those parent-ID streams then feed list-based and report-based pagination flows.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 270–272)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This function lists all Mailchimp audience list IDs. Those IDs are needed because many Mailchimp resources can only be fetched by first naming the list they belong to.

**Data flow**: It receives an HTTP client, asks _ids to page through the /3.0/lists endpoint, and yields each list ID it finds.

**Call relations**: The per-list, interests, and segment-members walkers call this before fetching nested data. It supplies the parent list ID used to build each child endpoint path.

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 274–276)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This function lists all Mailchimp report IDs. Report IDs are the entry point for reading report-specific children like unsubscribes and email activity.

**Data flow**: It receives an HTTP client, asks _ids to page through the /3.0/reports endpoint, and yields each report ID it finds.

**Call relations**: The per-report and email-activity walkers call this first. Each yielded report ID becomes the parent value used to build the next API request.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 278–299)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads child collections that belong directly to each audience list, such as list members, segments, tags, or interest categories. It also marks each returned row with the list it came from when requested.

**Data flow**: It receives the HTTP client, stream description, child path name, optional cursor, and the parent field to stamp onto rows. It gets all list IDs, builds a nested path for each list, fetches pages from that path, optionally adds list_id to each record, and yields the pages.

**Call relations**: MailchimpConnector.paginate calls this for list-based streams. It uses _list_ids to discover parents, _data_field and _cursor_params to prepare page reading, and _paginate_child to fetch the actual child pages.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 301–323)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads interests, which sit two levels deep in Mailchimp: first under a list, then under an interest category. It preserves that context by adding both the list ID and category ID to each interest row.

**Data flow**: It receives the HTTP client, stream description, and optional cursor. It loops through every list, fetches that list’s interest categories, then for each category fetches its interests. Each interest row is stamped with list_id and category_id before the page is yielded.

**Call relations**: MailchimpConnector.paginate calls this when the requested stream is interests. It gets parent list IDs from _list_ids and uses _paginate_child for both the category pages and the interest pages.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 325–347)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads members inside each segment of each audience list. It is needed because Mailchimp does not expose all segment members from one single flat endpoint.

**Data flow**: It receives the HTTP client, stream description, and optional cursor. It prepares any cursor filter, loops through list IDs, fetches segments for each list, then fetches members for each segment. Each member row is stamped with list_id and segment_id before the page is yielded.

**Call relations**: MailchimpConnector.paginate calls this for the segment_members stream. It combines _list_ids for parent discovery, _cursor_params for incremental filtering, and _paginate_child for the segment and member API calls.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 349–369)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads child collections that belong to campaign reports, such as unsubscribes. It adds the campaign or report context to each returned row so the row is not disconnected from its parent.

**Data flow**: It receives the HTTP client, stream description, child path name, optional cursor, and parent field to stamp. It gets report IDs, builds each report-child endpoint, fetches pages from it, optionally adds the parent campaign_id field to each row, and yields those pages.

**Call relations**: MailchimpConnector.paginate calls this for report-based child streams. It relies on _report_ids to discover parents, _data_field and _cursor_params to prepare the reads, and _paginate_child to walk the paged endpoint.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 371–403)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads campaign email activity and turns Mailchimp’s grouped recipient activity into one row per action. That matters because clicks, opens, bounces, and similar actions need stable individual rows for reliable syncing.

**Data flow**: It receives the HTTP client and optional cursor. It builds a since parameter when a cursor is present, loops through report IDs, fetches email-activity pages for each report, and then splits each recipient’s activity array into separate records. Each output row keeps the recipient details, includes campaign_id, includes the action details, and gets a synthesized ID made from email ID, action, and timestamp when Mailchimp does not provide one.

**Call relations**: MailchimpConnector.paginate calls this for the email_activity stream. It gets report IDs from _report_ids, fetches grouped activity with _paginate_child, then performs the extra reshaping step before yielding pages to the sync engine.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


### `extensions/sources/ufo_ext_sources/typeform.py`

`io_transport` · `source sync run`

Typeform stores form-building data behind a web API, and that API returns large lists in chunks rather than all at once. This connector is the adapter that knows Typeform’s rules: which API paths to call, how to move through pages, how to fetch responses for each form, and how to skip data the current credentials are not allowed to see.

The file defines the Typeform streams the system can read. A stream is one kind of thing to sync, such as forms or responses. The main class, TypeformConnector, inherits shared REST-API behavior from RestConnector, then fills in the Typeform-specific details.

Most streams use ordinary numbered pages: ask for page 1, then page 2, and stop when Typeform says there are no more. Responses are different. They belong to individual forms, so the connector first lists all forms, then asks Typeform for each form’s responses. It also adds the form id and title onto each response, like attaching a label to a folder so the response is still understandable later.

If Typeform refuses a request with a 401 or 403 status, meaning the token is invalid or lacks permission, the connector marks that stream as skipped instead of crashing the whole sync. This file only reads from Typeform; it does not create or update anything there.

#### Function details

##### `TypeformConnector.paginate`  (lines 52–79)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway the sync runner uses to ask for one Typeform stream. It chooses the right fetching method for forms, responses, workspaces, images, themes, or webhooks, and reports unsupported or unauthorized streams as skipped.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value that means “only data newer than this.” It checks the stream name, delegates to the matching helper, and yields batches of records as they arrive. If Typeform rejects the request with an authorization error, it turns that into a clear skip message; other errors are allowed to continue upward.

**Call relations**: The wider source system calls this when it wants records for a Typeform stream. From there, this function sends the work to _forms, _responses, _paged_items, or _webhooks depending on what is being synced. If nothing in this file knows how to read the requested stream, it raises StreamSkipped so the runner can move on cleanly.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 81–99)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Typeform endpoints that return ordinary numbered pages of items. It is the reusable “turn the pages until the book ends” routine for streams like forms, workspaces, images, and themes.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It repeatedly adds a page number and page size, makes a GET request, extracts the list found under the items field, and yields that list when it is not empty. It stops when Typeform’s page count says the last page has been reached, or when a short page suggests there is no more data.

**Call relations**: paginate calls this directly for simple list-style streams. _forms also calls it so form listing gets the same paging behavior before applying any form-specific filtering. It relies on records_at to safely pull the item list out of Typeform’s response body.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 101–108)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches Typeform forms and optionally filters them for incremental syncs. An incremental sync means the system only wants forms updated after a saved point in time.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It asks _paged_items for all pages from the /forms endpoint, then, if a cursor was supplied, keeps only forms whose last_updated_at value is newer than that cursor. It yields only non-empty batches of forms.

**Call relations**: paginate uses this when the requested stream is forms. _responses and _webhooks also use it first because both of those streams are reached through forms: they need to know which form ids exist before they can fetch responses or webhooks for each form.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 110–132)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches submitted responses for every Typeform form. It preserves the form context on each response, so a response can later be traced back to the form it came from.

**Data flow**: It receives an HTTP client and an optional cursor. First it loads all forms, ignoring the cursor for the form list itself so no form is missed. For each form with a usable id, it asks the form-specific responses endpoint for response pages. If a cursor was supplied, it sends it as Typeform’s since parameter to request newer responses. Each batch of responses is returned with extra form_id and form_title fields added.

**Call relations**: paginate calls this when syncing the responses stream. This function depends on _forms to discover the forms to visit, then uses the shared cursor-page request helper from the base connector to follow Typeform’s next-page tokens. It uses with_context to attach form details before yielding records back to paginate and the sync runner.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 134–143)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches webhook definitions for every Typeform form. Webhooks are callbacks Typeform can send to another service when something happens, such as a form submission.

**Data flow**: It receives an HTTP client. It first loads all forms, then for each form with a valid id it calls that form’s webhooks endpoint. It extracts the returned items and, when any exist, adds the form id and title to each webhook record before yielding the batch.

**Call relations**: paginate calls this when syncing the webhooks stream. Like _responses, it starts with _forms because Typeform webhooks are nested under individual forms. It uses records_at to read the item list from the API response and with_context to label each webhook with the form it belongs to.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).


### Incidents and observability
Connectors for syncing operational incident, on-call, error-tracking, release, and project data.

### `extensions/sources/ufo_ext_sources/pagerduty.py`

`io_transport` · `source sync / API pagination`

PagerDuty is an incident response service, so its data is often useful when someone wants to recall what happened, who was involved, and what service was affected. This connector is the read-only bridge from PagerDuty into the larger system.

The file defines the PagerDuty streams the system knows about: users, teams, services, incidents, incident notes, escalation policies, schedules, and on-calls. Each stream describes where the records live in PagerDuty’s API response and what field uniquely identifies each record. Some streams also say which timestamp can be used as a cursor, meaning a bookmark for “only fetch things newer than this.”

The main class, `PagerDutyConnector`, builds an HTTP client for PagerDuty’s API and sets the special `Accept` header PagerDuty requires for its version 2 API. It then fetches records page by page. PagerDuty uses offset-based pagination, like reading a long list in chunks of 100 and asking “is there more?” after each chunk.

Incidents get special treatment because they can be read incrementally by `updated_at`. Incident notes are even more special: PagerDuty exposes them under each incident, so the connector first reads incidents, then asks for notes for each one. If PagerDuty refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 74–77)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to PagerDuty and adds the PagerDuty-specific API version header. Someone would use it when starting a PagerDuty sync so every request is formatted the way PagerDuty expects.

**Data flow**: It receives a base URL and a credential object supplied by the surrounding authentication system. It first lets the shared REST connector create the normal client, then adds an `Accept` header that says the connector wants PagerDuty API version 2 responses. It returns the prepared asynchronous HTTP client.

**Call relations**: This is part of the setup path inherited from the generic REST connector. Once the client is prepared here, the pagination functions use that same client to make actual PagerDuty API requests.


##### `PagerDutyConnector._offset_pages`  (lines 79–99)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one ordinary PagerDuty stream page by page using PagerDuty’s offset-and-limit paging style. It also applies an optional cursor filter so old records can be skipped.

**Data flow**: It receives an HTTP client, a stream description, optional query parameters, and an optional cursor timestamp or string. It asks the base REST connector for chunks of records from the stream’s API path, with a page size of 100. If a cursor and cursor field are present, it keeps only records whose cursor field is newer than the saved cursor. It yields each non-empty page of records.

**Call relations**: This is the common paging helper for most PagerDuty streams. `PagerDutyConnector._incidents` calls it after adding incident-specific sorting and since parameters, and `PagerDutyConnector.paginate` calls it directly for users, teams, services, escalation policies, schedules, and on-calls.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 101–113)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads PagerDuty incidents in a safe incremental order, oldest updated records first. It is used when syncing incidents directly and also when incident notes need to discover which incidents to inspect.

**Data flow**: It receives an HTTP client and an optional cursor. It builds PagerDuty query parameters that sort incidents by `updated_at` ascending. If a cursor exists, it also sends that cursor as PagerDuty’s `since` value. It then delegates the actual page fetching and extra cursor filtering to `_offset_pages`, and yields each page of incident records.

**Call relations**: This function sits between the general paging helper and the higher-level stream dispatcher. `PagerDutyConnector.paginate` calls it for the incidents stream, while `PagerDutyConnector._incident_notes` calls it to find incidents before requesting their notes.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 115–128)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches notes attached to PagerDuty incidents. PagerDuty does not provide these as one simple global list, so the connector must visit incidents first and then fetch notes for each incident.

**Data flow**: It receives an HTTP client and an optional cursor. It first reads incidents without an incident cursor, then looks at each incident’s ID. For each valid incident ID, it requests `/incidents/{incident_id}/notes`, extracts the `notes` list from the response, and optionally keeps only notes whose `created_at` value is newer than the cursor. Before yielding notes, it adds the incident ID as context so each note keeps a link back to the incident it came from.

**Call relations**: This is called by `PagerDutyConnector.paginate` when the selected stream is `incident_notes`. It relies on `_incidents` to find incidents, uses `records_at` to pull the notes list out of PagerDuty’s response, and uses `with_context` to attach the parent incident ID before handing the notes back to the sync system.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 130–164)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main router for reading PagerDuty streams. Given a stream name, it chooses the correct fetching strategy and yields record pages to the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. For incidents, it sends the work to `_incidents`. For incident notes, it sends the work to `_incident_notes`. For the simpler PagerDuty streams, it sends the work to `_offset_pages`. If the stream is unknown, it raises `StreamSkipped`, meaning this stream should be recorded as not run rather than treated like a broken sync. If PagerDuty rejects the request with HTTP 401 or 403, it converts that refusal into `StreamSkipped` with a clear message; other HTTP errors are allowed to continue upward as real failures.

**Call relations**: This is the entry point the generic source runner uses when it wants records for a PagerDuty stream. It coordinates the helper functions and decides whether a problem is a harmless skipped stream, an access-scope refusal, or an error that should stop the run.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/sentry.py`

`io_transport` · `source sync / request handling`

Sentry’s API is arranged like a tree: first there are organizations, then projects inside those organizations, then items such as issues and events inside projects. This connector walks that tree in the right order so each record arrives with enough context, such as which organization or project it came from. Without this file, the wider sync system would not know which Sentry API paths to call, how to follow Sentry’s pages of results, or how to skip cleanly when a token lacks permission.

The file starts by defining the Sentry streams the system can recall later: organizations, members, projects, issues, events, and releases. A stream is one category of records to sync. The main class, `SentryConnector`, plugs into the project’s generic REST connector framework. Its central `paginate` method chooses the right helper for the requested stream.

Sentry returns long lists in pages. Like a book that says “continued on next page,” Sentry puts the next-page marker in an HTTP `Link` header. `_sentry_next_cursor` reads that marker, and `_paged_list` keeps requesting pages until there are no more. For project- or organization-specific data, helpers first fetch the needed parent records, then fetch child records and stamp them with context. If Sentry refuses access with 401 or 403, the connector reports the stream as skipped instead of crashing the whole sync.

#### Function details

##### `_sentry_next_cursor`  (lines 76–81)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This small helper looks at Sentry’s response headers and finds the cursor for the next page of results. A cursor is a token from the server that means “start the next request after this point.”

**Data flow**: It receives HTTP headers from a Sentry response. It reads the `link` or `Link` header, searches for the part that says there is a next page with real results, and pulls out the cursor text. It returns that cursor string, or `None` when there is no next page to fetch.

**Call relations**: After `_paged_list` receives a page from Sentry, it calls `_sentry_next_cursor` to decide whether to keep going. If this helper returns a cursor, `_paged_list` asks Sentry for another page; if it returns nothing, the paging loop ends.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.paginate`  (lines 89–128)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main entry for reading one Sentry stream. It decides which Sentry data category was requested and sends the work to the matching helper.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the appropriate helper, and yields batches of records back to the sync system. For some streams it also applies cursor filtering so only newer records are returned. If Sentry says access is unauthorized or forbidden, it turns that into a clean `StreamSkipped` result instead of letting the whole run fail.

**Call relations**: The wider REST source framework calls this method when it needs Sentry records. `paginate` then branches to `_organizations`, `_projects`, `_members`, `_issues`, `_events`, or `_releases`. If the stream name is not implemented, or if Sentry refuses access with 401 or 403, it raises `StreamSkipped` so the caller can move on safely.

*Call graph*: calls 7 internal fn (__init__, _events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._paged_list`  (lines 130–147)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper repeatedly calls one Sentry list endpoint until all pages have been read. It is the shared paging engine used by the rest of the connector.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It sends a request, reads the JSON response, keeps only list items that are dictionary-like records, and yields those records as a batch. Then it asks `_sentry_next_cursor` whether Sentry has another page; if so, it includes that cursor in the next request and repeats.

**Call relations**: All stream-specific helpers call `_paged_list` so they do not each need to know Sentry’s paging rules. `_organizations`, `_projects`, `_members`, `_issues`, `_events`, and `_releases` supply the endpoint path and any filters; `_paged_list` supplies the repeated network reads and next-page logic.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 149–153)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper fetches all organizations visible to the current Sentry grant. Organizations are the top-level containers needed before member and release data can be fetched.

**Data flow**: It starts with an empty list. It asks `_paged_list` for every page from `/organizations/`, adds each page’s records to the list, and returns the complete organization list.

**Call relations**: `paginate` calls this directly when syncing the organizations stream. `_members` and `_releases` also call it first because their Sentry API paths require an organization slug.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, paginate).


##### `SentryConnector._projects`  (lines 155–159)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper fetches all projects visible to the current Sentry grant. Projects are needed before project-level issues and events can be fetched.

**Data flow**: It starts with an empty list. It asks `_paged_list` for every page from `/projects/`, adds each page’s records to the list, and returns the complete project list.

**Call relations**: `paginate` calls this directly when syncing the projects stream. `_issues` and `_events` call it first because their API paths need both the organization slug and the project slug.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, paginate).


##### `SentryConnector._members`  (lines 161–167)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches members for every visible Sentry organization. It adds the organization slug to each member record so the record still makes sense after it leaves Sentry’s nested API.

**Data flow**: It first gets all organizations from `_organizations`. For each organization with a valid slug, it reads pages from that organization’s members endpoint. Before yielding each page, it uses `with_context` to add `organization_slug` to every member record.

**Call relations**: `paginate` calls `_members` for the members stream. `_members` depends on `_organizations` to know which organization member endpoints to visit, and it uses `_paged_list` to read each endpoint page by page.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 169–183)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches issue records for every visible Sentry project. Issues are Sentry’s grouped error reports, and this helper can ask Sentry for only issues seen after the last sync cursor.

**Data flow**: It gets all projects from `_projects`. For each project that has a usable organization slug and project slug, it builds the project issues endpoint. If a cursor was provided, it adds a Sentry search query such as `lastSeen:>cursor` so Sentry filters older issues out. It yields each page after adding `organization_slug` and `project_slug` to every record.

**Call relations**: `paginate` calls `_issues` for the issues stream. `_issues` uses `_projects` to find the project endpoints, `_paged_list` to read them, and `with_context` to preserve where each issue came from.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 185–199)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches event records for every visible Sentry project. Events are individual occurrences, and this helper can request only events newer than the last cursor.

**Data flow**: It gets all projects from `_projects`. For each project with valid organization and project slugs, it builds the project events endpoint. If a cursor was provided, it adds a Sentry query such as `event.timestamp:>cursor` to limit the results. It yields each page after adding the organization and project slugs to every event record.

**Call relations**: `paginate` calls `_events` for the events stream. Like `_issues`, it first relies on `_projects`, then uses `_paged_list` for the API pages, and finally uses `with_context` so downstream code knows each event’s source project.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 201–212)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches release records for every visible Sentry organization. A release represents a version of software known to Sentry.

**Data flow**: It first gets all organizations from `_organizations`. For each organization with a valid slug, it reads pages from that organization’s releases endpoint. If a cursor was provided, it keeps only releases whose `dateCreated` value is newer than that cursor. It yields non-empty pages after adding `organization_slug` to every release record.

**Call relations**: `paginate` calls `_releases` for the releases stream. `_releases` uses `_organizations` to discover which organization endpoints to visit, `_paged_list` to read those endpoints, and `with_context` to attach the organization identity to each release.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).
