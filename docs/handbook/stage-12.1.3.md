# CRM, Sales, Marketing, and Advertising Connectors  `stage-12.1.3`

This stage is a set of behind-the-scenes connectors that let the system bring in customer, sales, marketing, and advertising data from outside services. Each connector knows how to talk to one provider’s web API, meaning the provider’s online doorway for requesting data, and how to turn the answers into steady “pages” of records the rest of the sync system can store and search.

ActiveCampaign, HubSpot, Attio, Salesforce, Apollo, Klaviyo, and Mailchimp cover CRM and marketing records such as contacts, companies, deals, campaigns, lists, events, email activity, tasks, notes, and conversations. Salesforce also tracks deleted records so the local copy can be cleaned up. Facebook Ads, Google Ads, and Instagram cover advertising and social data, including accounts, campaigns, ads, media, stories, and performance numbers.

Together, these files act like adapters for different plug shapes. Each outside platform has its own layout and paging style, but these connectors translate them into a common stream of clean records for the larger system to recall later.

## Files in this stage

### CRM and Sales Readers
Provider readers for CRM and sales-engagement platforms that expose customer, company, deal, task, and contact data as syncable records.

### `extensions/sources/ufo_ext_sources/providers/active_campaign.py`

`io_transport` · `source sync`

ActiveCampaign exposes many kinds of data: contacts, lists, campaigns, deals, accounts, tags, custom fields, webhooks, users, and more. This file turns those outside API resources into named streams the rest of the UFO source-sync system can ask for in a consistent way.

The file first describes each stream with a small helper, including its name, its main ID field, and which date field can be used to notice newer changes. It also records ActiveCampaign’s sometimes unusual API names, such as using `campaignMessages` in the URL for the local stream called `campaign_messages`.

The main class, `ActiveCampaignConnector`, is the adapter between UFO and ActiveCampaign. It creates an HTTP client with the right authentication header. ActiveCampaign expects an `Api-Token` header, not the more common bearer-token style, so this class translates the stored credential into the shape ActiveCampaign wants.

When syncing, it asks ActiveCampaign for records in pages of 100. This is like reading a long report 100 rows at a time instead of trying to carry the whole stack at once. For streams where ActiveCampaign supports it, the connector also sends a “changed after this time” filter so repeated syncs can avoid rereading old data. If ActiveCampaign refuses access with a 401 or 403 response, the stream is skipped with a clear message instead of crashing the whole idea of source syncing.

#### Function details

##### `_stream`  (lines 98–114)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a `StreamSpec`, which is the system’s description of one kind of ActiveCampaign data that can be synced. It keeps the long stream list readable by filling in common defaults, such as using `id` as the main record key and `cdate` as the creation-time field.

**Data flow**: It receives a stream name plus optional details like the ActiveCampaign object name, primary key, cursor field, and whether the stream is considered canonical. It combines those inputs with standard defaults, decides whether the cursor field also counts as an updated-time field, and returns a ready-to-use stream description.

**Call relations**: This function is used while the module is being loaded to build the `ACTIVECAMPAIGN_STREAMS` list. Each returned stream description is later attached to `ActiveCampaignConnector`, so the wider sync system knows which ActiveCampaign resources this connector can read.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 176–181)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the web client used to talk to ActiveCampaign with the correct authentication. Its main job is to turn the stored API key into ActiveCampaign’s required `Api-Token` header.

**Data flow**: It receives a base URL and a credential. If the credential already supplies a custom transport, it leaves that setup alone and lets the parent connector create the client. Otherwise, it checks for a direct API key, wraps that key in an `Api-Token` header, and returns an HTTP client configured by the parent connector. If no usable key is present, it raises an error before any network call is attempted.

**Call relations**: The broader REST connector flow calls this when it is preparing to sync. This method customizes only the ActiveCampaign-specific authentication detail, then hands client creation back to the shared REST connector machinery.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 184–185)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function translates the system’s stream name into the exact URL segment and response field name ActiveCampaign uses. It exists because some names are written differently locally than they are in ActiveCampaign’s API.

**Data flow**: It receives a stream description. It looks up that stream’s name in the ActiveCampaign name map. If there is a special mapping, it returns the mapped URL segment and response envelope key; otherwise, it uses the stream name for both.

**Call relations**: The pagination function calls this before making requests. It gives `paginate` the precise path to request and the precise field to read records from in ActiveCampaign’s response.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 187–211)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one ActiveCampaign stream in pages and yields each page of records to the sync system. It also applies incremental filters when ActiveCampaign supports them, so later runs can ask mainly for data changed after the previous cursor.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value such as a timestamp from a previous sync. It resolves the ActiveCampaign URL and response key, builds request parameters with a page size of 100, and adds a “changed after” filter when possible. It then asks the shared REST paging helper for offset-based pages and yields each list of records. If ActiveCampaign answers with 401 or 403, it turns that refusal into a `StreamSkipped` error with a helpful explanation; other HTTP errors are passed upward unchanged.

**Call relations**: During a source sync, the REST connector framework calls this to fetch records for a specific stream. It first uses `_resolve_stream_segment` to understand ActiveCampaign’s naming, then delegates the repeated page fetching to the shared offset-pagination helper. If access is refused, it signals the larger sync flow to skip that stream rather than treating the response like normal data.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### `extensions/sources/ufo_ext_sources/providers/apollo.py`

`io_transport` · `source sync polling`

Apollo does not offer a simple “give me everything changed since yesterday” endpoint for these CRM records. It also does not send useful change webhooks for contacts or accounts. So this connector must poll Apollo directly: it asks for contacts or accounts, page by page, newest first, and stops once it reaches records that are not newer than the last successful sync point. That saved sync point is the cursor, also called a watermark, like a bookmark showing how far the previous run got.

The file defines two supported streams: contacts and accounts. For each one it knows the Apollo search endpoint to call, the response field where records live, and the Apollo field used to sort newest-first. The connector sends POST requests with page number, page size, and sort options. It keeps only records whose creation time is newer than the cursor. If an entire page is old, or even partly old, the connector stops because Apollo is already sorted newest-first, so later pages will be older too.

Authentication has one important twist: Apollo expects the API key in an X-Api-Key header, not the usual Authorization bearer header. The connector rewrites the HTTP client headers when it receives a direct API key. If Apollo rejects the request with 401 or 403, the stream is skipped with a clear message, because some Apollo keys cannot access all CRM data.

#### Function details

##### `ApolloConnector._make_client`  (lines 61–69)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to Apollo and fixes the authentication header to match Apollo’s rules. Someone would rely on it so a normal stored API key is sent as X-Api-Key instead of as a bearer token, which Apollo refuses.

**Data flow**: It starts with a base URL and a resolved credential. It first asks the general REST connector to build a standard HTTP client. If the credential contains a bearer-style key, it removes the normal Authorization header and puts that key into Apollo’s X-Api-Key header. It returns the adjusted client, ready to make Apollo API requests.

**Call relations**: This is the connector’s setup step before any Apollo requests are sent. The wider REST connector machinery calls on it when it needs an HTTP client, and the returned client is then used by the paging code to make search requests against Apollo.


##### `ApolloConnector.paginate`  (lines 71–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one Apollo stream, such as contacts or accounts, page by page. It yields batches of records that are newer than the saved cursor, and stops as soon as it reaches older data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing the newest record already seen in a previous run. It looks up the correct Apollo search endpoint and response key for that stream, then repeatedly sends a POST request asking for the next page sorted newest-first. From each response, it turns the record field into a safe list, filters that list through ApolloConnector._above, yields any still-new records, checks Apollo’s total page count, and decides whether to continue or stop. If Apollo returns 401 or 403, it changes that low-level HTTP refusal into a StreamSkipped message explaining that the key is invalid or lacks master access.

**Call relations**: During a sync, the source framework calls this function to get Apollo data in batches. Inside the loop it delegates the cursor comparison to ApolloConnector._above, uses list_or_empty so a missing or malformed record list does not crash the loop, and uses get_path to read pagination.total_pages from the response. If Apollo refuses access, it raises StreamSkipped so the broader sync can skip this stream cleanly instead of treating it like an unexpected system failure.

*Call graph*: calls 2 internal fn (__init__, _above); 2 external calls (get_path, list_or_empty).


##### `ApolloConnector._above`  (lines 107–116)

```
def _above(records: list[dict[str, Any]], cursor: str | None) -> list[dict[str, Any]]
```

**Purpose**: This helper keeps only records that are newer than the current cursor. It is the small rule that makes incremental syncing possible even though Apollo itself cannot filter by modification time.

**Data flow**: It receives a list of Apollo records and an optional cursor string. If there is no cursor, it returns the full list, which is what a first sync needs. If there is a cursor, it checks each record’s created_at value and keeps only records whose created_at string is newer than the cursor. The result is a shorter list of records that should still be imported.

**Call relations**: ApolloConnector.paginate calls this after each Apollo page is downloaded. Its result tells paginate both what to yield and when to stop: if some records on a newest-first page are not above the cursor, later pages will be older too, so the walk can end.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/attio.py`

`io_transport` · `source sync`

Attio’s API does not present every kind of data in the same shape. Companies, people, and deals are fetched from object-specific record endpoints. Tasks and notes have their own workspace endpoints. Meetings and call recordings use cursor-based paging, and call recordings need an extra request to fetch transcript text. This connector is the adapter that hides those differences.

The file first defines the Attio streams the system can sync. Each stream says what it is called, where it comes from in Attio, what field uniquely identifies each row, and that the sync should treat every run as a full snapshot. That matters because Attio does not provide one consistent “last changed” field for all streams.

The most important work is flattening. Attio records store their identity inside an `id` object and most useful fields inside nested `values` cells. The rest of the system needs ordinary top-level fields like `record_id`, `email`, or `transcript_text`. The connector lifts those values out, like unpacking labeled boxes before putting items on a shelf.

It also knows when to skip a stream safely. If an Attio workspace has disabled a standard object, or the OAuth grant lacks permission for meetings or recordings, it raises `StreamSkipped` instead of failing the whole sync.

#### Function details

##### `_records_stream`  (lines 35–43)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates the standard stream definition for Attio object records such as companies, people, and deals. It saves repeated setup by filling in the shared choices: `record_id` as the unique key and full-snapshot syncing.

**Data flow**: It receives a stream name, an Attio object slug, and whether the stream is canonical. It packages those into a `StreamSpec`, which is the system’s description of how to sync one kind of source data.

**Call relations**: This helper is used while building the file-level `ATTIO_STREAMS` list. It hands the finished stream definitions to the connector class so the broader source runtime knows which Attio streams exist.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 70–71)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: Safely pulls one named ID value out of a nested dictionary. It avoids errors when Attio returns a value in an unexpected shape.

**Data flow**: It receives any value and a key name. If the value is a dictionary, it returns the value stored under that key; otherwise it returns nothing.

**Call relations**: It is called by `AttioConnector._value_primitive` when an Attio option or status stores its useful identifier inside another `id` object.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 80–81)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: Builds the request body used to ask Attio for one page of object records. It keeps the paging request format in one small place.

**Data flow**: It receives an offset, meaning how many records have already been read. It returns a JSON-ready dictionary with Attio’s page limit and that offset.

**Call relations**: `AttioConnector.paginate` calls this before each object-record query so each request asks for the next slice of data.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._value_primitive`  (lines 84–127)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: Turns one Attio value-cell into the simplest useful value, such as text, a number, an email address, a phone number, an option title, or a referenced record ID. This is needed because different Attio field types store their real value under different names.

**Data flow**: It receives one Attio value-cell as a dictionary. It checks known Attio shapes in order and returns a plain value, such as a string, number, formatted reference, location text, or nothing if it cannot find a useful value.

**Call relations**: This is the basic unpacking tool used by the flattening helpers. When option or status IDs are nested, it calls `_nested_id` to extract them safely.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 130–145)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: Simplifies one Attio attribute cell into a single usable value. It knows how to deal with cells that are lists, single dictionaries, or already-simple values.

**Data flow**: It receives one cell from Attio’s `values` object. It converts any nested value entries into primitives, removes empty results, and returns either the most useful value, a list for multi-select options, or nothing.

**Call relations**: This helper is used as part of the record-flattening path, especially through `AttioConnector._flatten_values`, to turn Attio’s nested attribute storage into normal fields.


##### `AttioConnector._flatten_list_cell`  (lines 148–155)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: Turns a cell into a clean list of simple values. It is used for fields where several values are expected, such as email addresses, phone numbers, domains, or categories.

**Data flow**: It receives a cell that may or may not already be a list. It extracts primitive values, drops empty ones, and returns a list every time.

**Call relations**: It supports `AttioConnector._flatten_values`, which uses it for Attio attributes that should remain lists instead of being reduced to one value.


##### `AttioConnector._flatten_values`  (lines 158–192)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts Attio’s nested `values` block into ordinary top-level fields. This is where most CRM attributes become usable by the rest of the system.

**Data flow**: It receives the dictionary of Attio attribute cells. It flattens each cell, keeps list-like fields as lists, adds helpful aliases such as `email`, `phone`, `domain`, and `category`, and splits name data into first, last, and full name when available.

**Call relations**: It is part of the main object-record flattening path. `AttioConnector._flatten_record` calls it after creating the basic identity fields for a company, person, or deal.


##### `AttioConnector._flatten_record`  (lines 195–209)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns a standard Attio object record, such as a company, person, or deal, into a flat row with a top-level primary key. Without this, the sync system would not have a clear `record_id` to identify the row.

**Data flow**: It receives a raw Attio record and its stream definition. It pulls IDs and timestamps from the record, optionally copies a cursor field if the stream has one, then merges in the flattened attribute values.

**Call relations**: `AttioConnector.flatten` calls this for streams that are not tasks, notes, meetings, or call recordings. It relies on `AttioConnector._flatten_values` for the nested CRM attributes.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 212–216)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `task_id` to an Attio task record. This makes tasks identifiable in the same simple way as other synced rows.

**Data flow**: It receives a raw task record, copies it, reads the task ID from the nested `id` object when present, and returns the copied record with `task_id` added.

**Call relations**: `AttioConnector.flatten` calls this when the active stream is `tasks`, before the row is handed back to the source runtime.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 219–223)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `note_id` to an Attio note record. This gives the sync system a stable key for each note.

**Data flow**: It receives a raw note record, copies it, extracts the note ID from the nested `id` object when possible, and returns the enriched copy.

**Call relations**: `AttioConnector.flatten` calls this for the `notes` stream so notes match the primary-key shape declared in their stream definition.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 226–230)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a top-level `meeting_id` to an Attio meeting record. This makes meetings easy to store, compare, and recall.

**Data flow**: It receives a raw meeting record, copies it, extracts the meeting ID from the nested `id` object or uses the ID directly, and returns the updated record.

**Call relations**: `AttioConnector.flatten` calls this when processing the `meetings` stream.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 233–254)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares an Attio call recording for storage by adding `call_recording_id`, filling in a recording URL fallback, and joining transcript segments into readable text. This turns a set of spoken fragments into a single searchable transcript field.

**Data flow**: It receives a raw call recording, copies it, extracts the recording ID, uses `web_url` as `recording_url` if needed, and builds `transcript_text` from speaker-and-speech transcript segments when they exist.

**Call relations**: `AttioConnector.flatten` calls this for the `call_recordings` stream. It expects transcript data may already have been attached earlier by `AttioConnector._paginate_call_recordings`.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 256–265)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening method for the stream currently being synced. It is the connector’s single public flattening doorway.

**Data flow**: It receives a raw Attio record and the stream definition. It checks the stream name, sends the record to the matching specialized flattener, and returns the cleaned row.

**Call relations**: The source runtime calls this after pages of records are fetched. It delegates to the task, note, meeting, call-recording, or standard-record flatteners so each Attio data type is shaped correctly.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 267–293)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches all pages for one Attio stream. It understands that standard CRM objects use one paging style, while tasks, notes, meetings, and call recordings use other endpoint families.

**Data flow**: It receives an HTTP client, a stream definition, and an unused cursor value. For named special streams it delegates to `_paginate_named`; for object records it repeatedly posts query bodies with increasing offsets and yields each page of records until Attio has no more.

**Call relations**: This is the main page-fetching method used by the source runtime. It calls `_build_query_body` for object queries, `_paginate_named` for special endpoints, and `_is_object_disabled` to turn disabled Attio objects into `StreamSkipped` instead of a hard failure.

*Call graph*: calls 4 internal fn (__init__, _build_query_body, _is_object_disabled, _paginate_named).


##### `AttioConnector._paginate_named`  (lines 295–321)

```
async def _paginate_named(self, client: httpx.AsyncClient, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Routes non-standard Attio streams to the correct paging method. Tasks and notes use simple offset paging, meetings use cursor paging, and call recordings require a meeting-by-meeting fan-out.

**Data flow**: It receives an HTTP client and a stream name. It yields pages from the matching helper, and if Attio reports a missing OAuth permission, it raises a skip message instead of stopping the whole sync.

**Call relations**: `AttioConnector.paginate` calls this for tasks, notes, meetings, and call recordings. It hands work to `_paginate_simple`, `_paginate_cursor`, or `_paginate_call_recordings`, and uses `_is_scope_unauthorized` plus `_scope_skip_reason` for permission-related skips.

*Call graph*: calls 6 internal fn (__init__, _is_scope_unauthorized, _paginate_call_recordings, _paginate_cursor, _paginate_simple, _scope_skip_reason); called by 1 (paginate).


##### `AttioConnector._paginate_simple`  (lines 323–330)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads offset-paged Attio endpoints such as tasks and notes. Offset paging means each request asks for records after a numeric starting point.

**Data flow**: It receives an HTTP client, an endpoint path, and a page size. It asks the shared REST connector paging helper for pages under the `data` field and yields each page unchanged.

**Call relations**: `AttioConnector._paginate_named` calls this for `/v2/tasks` and `/v2/notes`, keeping those simple endpoints separate from cursor-based ones.

*Call graph*: called by 1 (_paginate_named).


##### `AttioConnector._paginate_cursor`  (lines 332–350)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads cursor-paged Attio endpoints such as meetings and meeting call recordings. A cursor is a token from the server that says where the next page starts.

**Data flow**: It receives an HTTP client, endpoint path, page size, and optional query parameters. It calls the shared cursor paging helper, which follows `pagination.next_cursor`, and yields each returned page of records.

**Call relations**: `AttioConnector._paginate_named` uses this for meetings. `AttioConnector._paginate_call_recordings` also uses it first to walk meetings and then to list recordings under each meeting.

*Call graph*: called by 2 (_paginate_call_recordings, _paginate_named).


##### `AttioConnector._paginate_call_recordings`  (lines 352–388)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the call recording stream by walking through meetings, then fetching recordings for each meeting, then fetching each recording’s transcript when available. This is necessary because Attio exposes recordings underneath meetings rather than as one simple global list.

**Data flow**: It receives an HTTP client. It reads meeting pages, extracts each meeting ID, copies meeting context such as title and start/end time onto each recording, calculates duration when possible, fetches transcript data for each recording, and yields pages of enriched recordings.

**Call relations**: `AttioConnector._paginate_named` calls this for the `call_recordings` stream. It uses `_paginate_cursor` for meetings and recordings, `_meeting_id` and `_call_recording_id` to find IDs, `_datetime_of` and `_duration_seconds` for time details, and `_fetch_transcript` for transcript content.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (_paginate_named).


##### `AttioConnector._fetch_transcript`  (lines 390–402)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: Fetches the transcript for one meeting call recording. It treats “not ready” or “not found” responses as normal missing data rather than sync-breaking errors.

**Data flow**: It receives an HTTP client, meeting ID, and recording ID. It requests the transcript endpoint, returns the `data` dictionary when present, returns nothing for 404 or 409 responses, and re-raises other HTTP errors.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this while enriching each recording. The returned transcript is later turned into searchable text by `AttioConnector._flatten_call_recording`.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 405–409)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a meeting’s ID from the shapes Attio may return. It keeps the call-recording fan-out from depending on one exact ID layout.

**Data flow**: It receives a meeting record. If the record has a nested `id.meeting_id`, it returns that; if the ID is already a string, it returns the string; otherwise it returns nothing.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this before requesting recordings for a meeting. If no meeting ID can be found, that meeting is skipped.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 412–416)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a call recording’s ID from the shapes Attio may return. This ID is needed to ask Attio for the recording’s transcript.

**Data flow**: It receives a recording record. It returns the nested `id.call_recording_id`, a direct string ID, or nothing if neither is available.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this for each recording before calling `_fetch_transcript`.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 419–423)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: Pulls a usable date or date-time string out of Attio’s meeting time shape. Attio may store timed events as `datetime` and all-day events as `date`.

**Data flow**: It receives a time-shaped value. If it is a dictionary, it returns the `datetime` value or falls back to `date`; otherwise it returns nothing.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this to copy meeting start and end times onto each related call recording.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 426–436)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: Calculates a rough meeting duration in seconds from start and end timestamps. It returns nothing when the inputs are missing or cannot be parsed safely.

**Data flow**: It receives optional start and end strings. It parses them as ISO 8601 date-times, subtracts start from end, clamps negative results to zero, and returns the number of seconds.

**Call relations**: `AttioConnector._paginate_call_recordings` calls this after extracting meeting start and end values. The result is added to recordings when available.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 439–448)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes Attio’s specific error for a disabled standard object. This lets the sync skip, for example, deals or companies if that workspace has not enabled them.

**Data flow**: It receives an HTTP error. It checks for status code 400, tries to read the JSON body, and returns true only when the body code is `standard_object_disabled`.

**Call relations**: `AttioConnector.paginate` calls this when an object-record query fails. If it returns true, `paginate` raises `StreamSkipped` with a clear reason.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._is_scope_unauthorized`  (lines 451–460)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes Attio’s error for a missing OAuth permission. OAuth is the login-and-permission grant that lets this system read a user’s Attio data.

**Data flow**: It receives an HTTP error. It checks for status code 403, reads the JSON body if possible, and returns true only when Attio’s error code is `unauthorized`.

**Call relations**: `AttioConnector._paginate_named` calls this when named streams fail. If the problem is missing permission, the stream is skipped with a helpful message.

*Call graph*: called by 1 (_paginate_named).


##### `AttioConnector._scope_skip_reason`  (lines 463–469)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a human-readable reason for skipping a stream because the OAuth grant lacks a required permission. It includes Attio’s own message when one is available.

**Data flow**: It receives an HTTP error, tries to read its JSON body, pulls out the `message` field if present, and returns a clear explanatory string.

**Call relations**: `AttioConnector._paginate_named` calls this after `_is_scope_unauthorized` identifies a missing-permission error, then uses the returned text when raising `StreamSkipped`.

*Call graph*: called by 1 (_paginate_named).


### Paid Advertising Readers
Provider readers for paid advertising platforms that stream account, campaign, ad, and performance data from Meta and Google Ads APIs.

### `extensions/sources/ufo_ext_sources/providers/facebook_ads.py`

`io_transport` · `during source sync`

This connector is the bridge between UFO and Facebook Ads. Without it, the system would not know which Facebook Ads API addresses to call, how to move through paged results, or how to split Facebook's account-based data into the separate streams used by the rest of the product.

The file defines a set of streams: ad accounts, campaigns, ad sets, ads, and ad insights. A stream is a named kind of record the sync system can fetch. Most Facebook Ads data lives under an ad account, so the connector first asks Facebook for the user's ad accounts. It then visits each account and asks for that account's campaigns, ad sets, ads, or daily insight rows.

Facebook returns data in pages, like a long report split across many sheets. The `_paged` helper keeps following Facebook's `paging.next` link until there are no more sheets to read. For campaigns, ad sets, and ads, the connector can skip older records by comparing their `updated_time` with the saved cursor, which is a bookmark from the last sync. For insights, it asks for daily ad-level performance either since the saved date or, on a first run, for the last 90 days.

This is a read-only source connector. It fetches and lightly reshapes data, but it does not write anything back to Facebook.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 74–87)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector's page reader for Facebook's API. It keeps requesting data until Facebook says there is no next page, so callers can work with batches of records instead of worrying about pagination themselves.

**Data flow**: It starts with an API path and optional query parameters. It sends a request, reads the JSON response, pulls the list stored under `data`, yields that list if it is not empty, then follows the `paging.next` URL for the next round. The output is an asynchronous stream of record batches; it does not combine them into one big list.

**Call relations**: The account, child-object, and insight readers all call this when they need Facebook data. It relies on the shared `records_at` helper to safely pick records out of the response body, then hands each page back to the higher-level reader that knows what kind of Facebook object is being fetched.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 89–94)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches all Facebook ad accounts available to the current credential. The rest of the connector depends on this because campaigns, ads, and insights are requested one ad account at a time.

**Data flow**: It asks `_paged` to read `/me/adaccounts` with a fixed list of useful account fields, such as name, currency, time zone, and creation time. It collects every page into one list. The result is a list of ad account records.

**Call relations**: The main `paginate` method uses this directly for the `ad_accounts` stream. The campaign/ad-set/ad readers and the insights reader also call it first, because they need account IDs before they can request account-specific data.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 96–121)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches account-owned objects: campaigns, ad sets, or ads. It also attaches the ad account identity to each returned record, so later parts of the system know which account the item came from.

**Data flow**: It receives a stream description and an optional cursor bookmark. First it fetches all ad accounts. For each valid account ID, it requests the matching Facebook collection with the fields needed for that stream. If a cursor is present, it filters out records whose cursor field is not newer than that bookmark. It then adds account context, such as ad account ID and name, and yields the remaining records in batches.

**Call relations**: The top-level `paginate` method calls this for the `campaigns`, `ad_sets`, and `ads` streams. Inside, it uses `_accounts` to find where to look, `_paged` to read each Facebook endpoint, and `with_context` to enrich the records before handing them back to the sync engine.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 123–166)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches daily ad performance rows, such as impressions, clicks, spend, reach, and cost metrics. It is used for the reporting-style `ads_insights` stream, where each row represents one ad on one day.

**Data flow**: It builds a Facebook insights request with ad-level reporting, one-day time slices, and a page size. If a cursor exists, it asks for data from that cursor date through today; otherwise it asks for the last 90 days. For each ad account, it reads insight pages and adds a stable synthetic `id` made from the account, campaign, ad set, ad, and date. It also adds the ad account ID, then yields the rows in batches.

**Call relations**: The main `paginate` method calls this when the selected stream is `ads_insights`. This function first uses `_accounts` to find account IDs, then `_paged` to walk Facebook's paginated insights responses. It uses `json.dumps` to format the date range Facebook expects and the current UTC date to decide the end of a cursor-based window.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 168–184)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector's dispatcher for reading a chosen stream. Given a stream name, it routes the sync engine to the right fetching routine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. If the stream is `ad_accounts`, it returns the account list. If it is one of the account child streams, it yields batches from `_account_children`. If it is `ads_insights`, it yields batches from `_insights`. If the stream name is unknown, it raises `StreamSkipped` to clearly say that this connector does not implement that stream.

**Call relations**: The broader source-sync framework calls this when it wants records for a Facebook Ads stream. `paginate` does not fetch all stream types itself; it chooses the correct specialist helper and passes its batches back to the framework.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 186–194)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This lightly reshapes records before they leave the connector. Its special case makes campaign records easier for the rest of the system to read by normalizing a few field names and values.

**Data flow**: It receives one record and its stream description. For campaign records, it returns a copy that keeps the original data but sets `status` to the effective status when available and copies `created_time` into `created_at`. For all other streams, it returns the record unchanged.

**Call relations**: This is called by the connector framework after records are fetched, as part of preparing them for storage or downstream use. It does not call other local helpers; it is the final small cleanup step for campaign-shaped data.


### `extensions/sources/ufo_ext_sources/providers/googleads.py`

`io_transport` · `source sync`

Google Ads does not expose this data as simple web pages. Instead, it uses GAQL, the Google Ads Query Language, where the connector sends SQL-like questions such as “SELECT campaign.name FROM campaign” to Google’s API. This file is the adapter that knows which questions to ask, how to ask them, and how to reshape the answers into records UFO can store.

A key detail is that Google Ads needs two kinds of permission. OAuth proves which advertiser account the user authorized, while a separate developer token proves this software is allowed to use the Google Ads API. This connector reads that token from environment variables. If it is missing, or if Google refuses access, the stream is skipped instead of crashing the whole sync.

The connector first asks Google which customer accounts are accessible. Then, for each requested stream, it builds the right GAQL query and runs it separately for every customer account. Each returned row is stamped with the customer id, like writing the account number on every receipt before filing it. Finally, `flatten` pulls important nested Google fields up to predictable top-level keys, so the rest of the system can identify and update records consistently.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 71–80)

```
def _developer_token(self) -> str
```

**Purpose**: This function finds the required Google Ads developer token. Google Ads OAuth alone is not enough, so without this token the connector cannot safely make API requests.

**Data flow**: It reads environment variables named `UFO_GOOGLE_ADS_DEVELOPER_TOKEN` and `GOOGLE_ADS_DEVELOPER_TOKEN`. If one is present, it returns that token. If neither is present, it raises `StreamSkipped`, which tells the sync system to skip Google Ads rather than treat the missing token as a hard failure.

**Call relations**: It is used by `GoogleAdsConnector._make_client` while preparing the HTTP client. That means every Google Ads request is set up with the developer-token header before any stream tries to read data.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function prepares the web client used to talk to Google Ads. It adds the Google Ads-specific headers that ordinary OAuth setup does not provide.

**Data flow**: It starts with the base HTTP client created by the parent REST connector, using the given base URL and credential. It adds the developer token header from `_developer_token`. If a login customer id is present in the environment, it removes any dashes and adds that as another header. It returns the configured async HTTP client.

**Call relations**: This is the connector’s setup point before requests go out to Google Ads. It calls `_developer_token` so later calls such as listing customers and running GAQL searches have the required Google Ads authorization details attached.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 92–99)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This function asks Google Ads which customer accounts the current credential can access. The rest of the connector needs this list because Google Ads queries are run per customer account.

**Data flow**: It sends a request to Google’s accessible-customers endpoint and reads the returned resource names. For entries shaped like `customers/1234567890`, it keeps only the numeric id part. It returns a list of customer id strings.

**Call relations**: `GoogleAdsConnector._query_each_customer` calls this first, before running any stream query. The customer ids it returns become the account-by-account work list for all the data streams.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 101–120)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: This function runs one GAQL query for one Google Ads customer account and collects the rows Google returns. It is the direct bridge between a stream’s question and Google’s streamed answer.

**Data flow**: It receives an HTTP client, a customer id, and a GAQL query string. It posts that query to the customer’s `googleAds:searchStream` endpoint. Google may return several batches, so the function walks through those batches, gathers the `results` rows, ignores malformed pieces, and returns a plain list of row dictionaries.

**Call relations**: `GoogleAdsConnector._query_each_customer` calls this once for each accessible customer id. This function does the actual per-account API call, while `_query_each_customer` decides how to repeat that call across accounts.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 122–130)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function runs the same Google Ads query across every accessible customer account. It turns one stream query into a sequence of result pages, one page per customer that returned data.

**Data flow**: It receives an HTTP client and a GAQL query. First it gets the accessible customer ids from `_customer_ids`. Then it calls `_search_stream` for each customer. If rows come back, it adds the `customer_id` onto every row and yields that list as a page of records.

**Call relations**: `GoogleAdsConnector.paginate` uses this helper after choosing the right query for a stream. `_query_each_customer` hides the repeated “ask every customer account” pattern so each stream definition can stay focused on what data it wants.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 132–200)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function decides what Google Ads query to run for each stream and yields pages of raw records. It is the main read path for syncing Google Ads data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that marks how far a previous sync got. Based on the stream name, it builds the matching GAQL query for customers, campaigns, ad groups, ads, campaign metrics, or customer clients. For metrics, it uses the cursor date when available, otherwise it looks back 90 days. It then yields pages produced by `_query_each_customer`. If the stream is unknown, or Google refuses access with an authorization status, it raises `StreamSkipped`; other HTTP errors are allowed to surface.

**Call relations**: The sync engine calls this when it needs records for a Google Ads stream. `paginate` chooses the query, delegates the repeated per-customer work to `_query_each_customer`, and passes pages onward to the rest of the source-sync pipeline.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 202–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function turns Google Ads’ nested response objects into flatter records with stable top-level fields. That makes it easier for UFO to identify, store, and update each record.

**Data flow**: It receives one raw record and the stream it belongs to. For customer records, it pulls out an `id` and readable name. For campaigns, it pulls out the campaign resource name, name, status, and start date. For campaign metrics, it builds a synthetic id from customer, campaign, and date, then lifts key numbers such as impressions, clicks, and cost. For other streams, it returns the record unchanged.

**Call relations**: After `paginate` has yielded raw rows from Google Ads, the sync flow can call `flatten` to normalize each row. It uses `dict_or_empty` so missing nested sections are treated as empty dictionaries instead of causing errors.

*Call graph*: 1 external calls (dict_or_empty).


### Marketing and Social Engagement Readers
Provider readers for broad marketing, social, and email automation systems that normalize assets, audiences, messages, analytics, and engagement activity.

### `extensions/sources/ufo_ext_sources/providers/hubspot.py`

`io_transport` · `source sync`

HubSpot is not one simple database. Different parts of HubSpot expose data through different web API endpoints, with different pagination styles, different field names, and different permission rules. This connector is the adapter that makes all of those surfaces look like a set of named streams.

At the top, the file declares the streams the system can sync, such as contacts, companies, tickets, workflows, files, email events, associations, and custom objects. A stream is like a labeled conveyor belt: it says what kind of records will arrive, which field identifies a record, and which timestamp can be used for incremental syncing.

The `HubSpotConnector` then decides how to fetch each stream. Standard CRM objects use HubSpot’s search API, including a cursor so later runs can ask only for changed records. Because HubSpot’s boundary filter is inclusive, the connector remembers IDs at the cursor edge and skips duplicates. It also does an extra sweep for archived records so deletions become tombstones instead of silently disappearing.

Other HubSpot product APIs need special walks: campaigns fan out into assets, lists fan out into memberships, contacts fan out into consent states, and associations are read in batches. Throughout, the connector flattens nested HubSpot records into simple dictionaries and treats missing product permissions as skipped streams rather than failed syncs.

#### Function details

##### `_normalize_epoch_millis`  (lines 247–254)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Converts HubSpot timestamps stored as milliseconds since 1970 into readable UTC date-time strings. It leaves booleans and already non-numeric values alone so real data is not accidentally changed.

**Data flow**: A value comes in, possibly a number or numeric string. If it is an epoch-millisecond timestamp, the function turns it into an ISO formatted UTC time; otherwise it returns the original value unchanged.

**Call relations**: This helper is used when product API records contain timestamp fields in HubSpot’s older numeric style. Analytics view rows and flattened product API rows call it before records are handed back to the sync pipeline.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 257–266)

```
def _stream(name: str, *, object_type: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates the standard description for a CRM object stream, such as contacts or deals. It gives the rest of the connector the object name, primary key, and timestamp fields to use.

**Data flow**: A stream name and HubSpot object type go in. A `StreamSpec`, meaning a small stream definition object, comes out with consistent CRM defaults.

**Call relations**: This is used at file load time to build the many CRM stream constants. Those constants are collected into `ALL_STREAMS`, which the connector advertises to the runner.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 269–288)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='createdAt', updated_at_field: str | None='updatedAt', pagi
```

**Purpose**: Creates a stream description for HubSpot data that does not use the normal CRM search API. These streams often have their own endpoint and may not be canonical CRM objects.

**Data flow**: The caller supplies names, key fields, timestamp fields, and optional pagination rules. The function packages those details into a `StreamSpec` for later syncing.

**Call relations**: This builds stream constants for product APIs such as owners, workflows, files, email events, and analytics. Later, `HubSpotConnector._paginate_unchecked` recognizes these stream names and sends them through product-specific pagination.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 291–303)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Builds the standard pagination recipe for HubSpot GET collection endpoints. Pagination means fetching a large result set page by page instead of all at once.

**Data flow**: A URL path goes in. A `Pagination` object comes out describing where records live in the response, where HubSpot puts the next cursor, and which query parameters control page size and continuation.

**Call relations**: Several product API stream definitions use this helper when their endpoints all share HubSpot’s common `results` plus `paging.next.after` shape.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 306–316)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Defines a synthetic stream for relationships between two HubSpot objects, such as deals linked to contacts. These rows do not exist as first-class HubSpot objects, so the connector creates them from association data.

**Data flow**: A stream name and parent object type go in. A non-canonical `StreamSpec` comes out with no cursor, because HubSpot does not expose update times for these relationship rows.

**Call relations**: This builds the small junction stream constants near the stream declarations. Later, `HubSpotConnector._paginate_unchecked` routes these streams to `_paginate_junction`.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 621–651)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the JSON request body used for HubSpot CRM search calls. It asks HubSpot for selected properties, sorted by the cursor field, and optionally limited to records changed since the last sync.

**Data flow**: A stream definition, list of property names, saved cursor, and page cursor go in. The function returns a dictionary that can be sent as the POST body to HubSpot’s search endpoint.

**Call relations**: CRM object pagination and custom object pagination both call this right before making a search request. It centralizes the rules for incremental search so the two flows stay consistent.

*Call graph*: called by 2 (_paginate_crm_object, _paginate_custom_object_records).


##### `HubSpotConnector._flatten`  (lines 654–665)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a normal HubSpot CRM record into a flatter, easier-to-store record. HubSpot often nests user fields under `properties`; this lifts them to the top level.

**Data flow**: A raw CRM record goes in. The result keeps the ID, timestamps, archived flag, and all property fields in one flat dictionary.

**Call relations**: `HubSpotConnector.flatten` calls this for ordinary CRM streams after pages have been fetched. The flattened form is what downstream storage sees.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 668–691)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records from HubSpot product APIs, which do not all look the same. It fills in missing IDs, lifts nested `properties`, expands form submission values, and fixes a few timestamp formats.

**Data flow**: A raw product API record and its stream definition go in. The function returns a copy with important fields promoted to top-level keys and special timestamp fields converted where needed.

**Call relations**: `HubSpotConnector.flatten` calls this for product API streams. It uses `_normalize_epoch_millis` for fields that arrive as numeric millisecond timestamps.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 693–700)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening rule for each stream. Some streams are already custom-shaped and must be left alone, while CRM and product API streams need different normalization.

**Data flow**: A raw record and stream definition go in. The method either returns the record unchanged, runs CRM flattening, or runs product API flattening.

**Call relations**: This method is the connector’s public normalization hook used by the base source framework after records are fetched. It hands ordinary CRM records to `_flatten` and product API records to `_flatten_product_api`.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector.record_identity`  (lines 702–715)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Computes the stable identity for a record, with special rules for consent states. Consent rows need a compound identity because one contact can have many subscriptions and business units.

**Data flow**: A record and stream definition go in. For most streams it delegates to the base connector’s normal identity logic; for consent states it combines contact, subscription or status kind, and business unit into one string.

**Call relations**: The sync framework calls this when deciding whether a fetched row represents the same item as a previously stored row. Its special consent behavior prevents separate consent facts from overwriting each other.


##### `HubSpotConnector._list_properties`  (lines 717–725)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which properties exist for a CRM object type. This lets the connector request all available fields without hard-coding each account’s custom schema.

**Data flow**: An HTTP client and object type go in. The method calls HubSpot’s properties endpoint and returns a list of property names found in the response.

**Call relations**: `_paginate_crm_object` calls this before searching a CRM stream. The resulting property list is passed into `_build_search_body` so HubSpot includes those fields in search results.

*Call graph*: called by 1 (_paginate_crm_object).


##### `HubSpotConnector.paginate`  (lines 727–740)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main page-producing entry point for a stream. It wraps the real pagination work with error handling that turns unavailable streams into clean skips.

**Data flow**: An HTTP client, stream definition, and optional cursor go in. It yields pages of records or delete markers; if HubSpot says the stream cannot be read because of permissions, it raises `StreamSkipped` instead of failing the whole run.

**Call relations**: The source runner calls this when it wants data from a HubSpot stream. It delegates to `_paginate_unchecked`, asks `_is_stream_unavailable` whether certain errors are skippable, and uses `_stream_skip_reason` to explain the skip.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 742–769)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Routes each stream to the correct fetching strategy. HubSpot has many API styles, so this method is the traffic director.

**Data flow**: A stream and cursor come in. Depending on the stream, it yields pages from a declared pagination strategy, a junction walk, custom object logic, product API logic, or normal CRM object search.

**Call relations**: `paginate` calls this after setting up error handling. It hands work to `_paginate_crm_object`, `_paginate_custom_objects`, `_paginate_junction`, or `_paginate_product_api` based on stream metadata and stream name.

*Call graph*: calls 4 internal fn (_paginate_crm_object, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._paginate_crm_object`  (lines 771–798)

```
async def _paginate_crm_object(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Fetches ordinary CRM object records such as companies, contacts, deals, and tasks. It supports incremental syncing and follows with a deletion sweep.

**Data flow**: The HTTP client, stream definition, and saved cursor go in. The method lists available properties, repeatedly searches HubSpot, removes duplicate boundary records, yields changed records, then yields tombstones for archived IDs.

**Call relations**: `_paginate_unchecked` sends normal CRM streams here. This method uses `_list_properties`, `_build_search_body`, and `_paginate_archived_ids` as its main helpers.

*Call graph*: calls 3 internal fn (_build_search_body, _list_properties, _paginate_archived_ids); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._is_stream_unavailable`  (lines 801–824)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether a HubSpot error means the current account is not allowed to read a stream. This keeps missing permissions from looking like broken infrastructure.

**Data flow**: An HTTP status error goes in. The method checks for a 403 response and looks for permission-related wording in the response body, returning true or false.

**Call relations**: `paginate` uses this to skip whole streams. Archived sweeps for normal and custom objects also use it to quietly stop when HubSpot denies that optional read.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 827–836)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a human-readable explanation for why a stream was skipped. It includes HubSpot’s own message when available.

**Data flow**: A stream name and HTTP error go in. The method extracts the response message if possible and returns a sentence explaining that the stream is unavailable for this account.

**Call relations**: `paginate` calls this when `_is_stream_unavailable` or a 401 causes a skip. The resulting text is stored by the runner as the reason for the skipped stream.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 838–873)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds CRM records that HubSpot has archived, so the local system can delete or tombstone them too. The search API does not include archived records, so this separate pass is necessary.

**Data flow**: A client and stream go in. The method walks HubSpot’s list endpoint with `archived=true`, collects IDs, and yields `StreamPage` objects containing delete markers.

**Call relations**: `_paginate_crm_object` calls this after finishing live search results. It uses `_is_archived_sweep_unsupported` and `_is_stream_unavailable` to ignore optional sweeps HubSpot does not allow.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_crm_object); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 875–883)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches streams that belong to HubSpot product APIs rather than the normal CRM object search API. It is a small bridge to the stream-specific product paginator.

**Data flow**: A client, stream, and cursor go in. It asks `_product_pages` for the correct async page iterator and yields each page it produces.

**Call relations**: `_paginate_unchecked` calls this for product API stream names. `_product_pages` does the real routing to specialized or generic product endpoint walkers.

*Call graph*: calls 1 internal fn (_product_pages); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._product_pages`  (lines 885–920)

```
def _product_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right product API paginator for a named stream. Some streams need special fan-out logic, while simple streams can use a generic GET collection walker.

**Data flow**: A client, stream name, and cursor go in. The method returns an async iterator from a specialized paginator, a partially applied paginator with the cursor, or `_paginate_get_collection` for simple endpoints.

**Call relations**: `_paginate_product_api` calls this for all product API streams. It connects names like `campaign_assets`, `email_events`, and `pipelines` to their dedicated methods, and falls back to `_paginate_get_collection` when a path is declared.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api); 1 external calls (partial).


##### `HubSpotConnector._paginate_get_collection`  (lines 922–950)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks a standard HubSpot GET endpoint that returns records in `results` and uses an `after` cursor for the next page. This avoids rewriting the same pagination loop many times.

**Data flow**: A client, path, optional limit, and extra query parameters go in. The method repeatedly GETs pages, normalizes `objectId` into `id` when needed, and yields non-empty record lists until no next cursor remains.

**Call relations**: Many specialized product paginators call this as their basic page reader, including owner teams, campaign assets, forms, conversations, sequences, and default product streams selected by `_product_pages`.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_sequences, _product_pages, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 952–987)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Fetches records for all custom object types defined in a HubSpot account. Custom objects vary by account, so the connector discovers their schemas before reading records.

**Data flow**: A client and cursor go in. The method loads custom object schemas, builds a temporary stream definition for each schema, fetches its records, then performs an archived-ID sweep for that custom object type.

**Call relations**: `_paginate_unchecked` calls this for the `custom_objects` stream. It relies on `_custom_object_schemas`, `_schema_object_type_id`, `_schema_property_names`, `_paginate_custom_object_records`, and `_paginate_custom_object_archived_ids`.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 989–991)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Retrieves the list of custom object definitions from HubSpot. These schemas describe which custom object types exist and what fields they have.

**Data flow**: An HTTP client goes in. The method calls the custom schema endpoint and returns only dictionary-shaped schema rows.

**Call relations**: `_paginate_custom_objects` uses this to discover custom object streams. `_association_object_types` also uses it so association syncing includes custom object types where possible.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 994–999)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Finds the best usable object type identifier inside a custom object schema. HubSpot can expose this identifier under several field names.

**Data flow**: A schema dictionary goes in. The method checks known identifier keys in order and returns the first non-empty string, or `None` if none exist.

**Call relations**: Custom object pagination uses this to know which HubSpot object endpoint to query. Association type discovery and custom row building also call it to label records correctly.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1002–1017)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Builds the property name list to request for a custom object type. It includes declared properties and display fields so records have useful titles.

**Data flow**: A schema dictionary goes in. The method collects unique property names from schema fields, primary display property, and secondary display properties, then returns the list.

**Call relations**: `_paginate_custom_objects` calls this before searching each custom object type. The resulting names are passed into `_paginate_custom_object_records` and then into `_build_search_body`.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1019–1054)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Searches one custom object type and yields its records in the connector’s unified custom object shape. It also avoids duplicate records at the incremental cursor boundary.

**Data flow**: A client, temporary stream, schema, property list, and cursor go in. The method posts search requests, filters duplicate boundary IDs, converts raw HubSpot records with `_custom_object_row`, and yields pages.

**Call relations**: `_paginate_custom_objects` calls this once per custom object schema. It shares `_build_search_body` with normal CRM search and uses `_custom_object_row` to enrich each record with schema context.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1056–1095)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Turns one raw custom object record into a self-describing row. It adds the object type, labels, display title, original record ID, and flattened properties.

**Data flow**: A raw record and its schema go in. If both object type and record ID are present, the method returns a dictionary with a compound ID and helpful display metadata; otherwise it returns `None`.

**Call relations**: `_paginate_custom_object_records` calls this for every custom object record it receives. It calls `_schema_object_type_id` to keep object type identification consistent.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1097–1127)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived records for one custom object type, so deleted custom records are reflected locally. It mirrors the archived sweep used for built-in CRM objects.

**Data flow**: A client and custom object type ID go in. The method pages through archived records and yields delete markers with compound custom object IDs.

**Call relations**: `_paginate_custom_objects` calls this after reading live records for each schema. It uses `_is_archived_sweep_unsupported` and `_is_stream_unavailable` to stop quietly when HubSpot cannot provide the sweep.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1129–1148)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Extracts unique team records from the owner records HubSpot returns. HubSpot exposes teams nested under owners, so this method turns them into their own stream.

**Data flow**: A client goes in. The method reads owners through `_paginate_get_collection`, gathers team dictionaries by ID to remove duplicates, and yields the unique teams.

**Call relations**: `_product_pages` selects this paginator for the `owner_teams` stream. It depends on the generic owner collection reader rather than a separate team endpoint.

*Call graph*: calls 1 internal fn (_paginate_get_collection).


##### `HubSpotConnector._paginate_lists`  (lines 1150–1179)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot lists using the list search API. It also flattens extra list properties into the main record.

**Data flow**: A client goes in. The method posts list search requests with an offset, adds a string `id` from `listId`, merges `additionalProperties`, and yields pages until HubSpot says there are no more.

**Call relations**: `_product_pages` uses this for the lists stream, and `_paginate_list_memberships` calls it first so it can later fetch the members of each list.

*Call graph*: called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_site_search`  (lines 1181–1201)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads content items from HubSpot’s site search endpoint for a chosen content type. In this file it is used for knowledge articles.

**Data flow**: A client and content type go in. The method sends offset-based search requests, yields dictionary result rows, and stops when it reaches the reported total.

**Call relations**: `_product_pages` connects the `knowledge_articles` stream to this method with the content type already filled in.


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1203–1227)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds assets attached to HubSpot marketing campaigns. It first reads campaigns, then asks for many possible asset types under each campaign.

**Data flow**: A client goes in. The method pages through campaigns, extracts each campaign’s ID and name, then yields asset pages returned by `_paginate_campaign_asset_type` for every known asset type.

**Call relations**: `_product_pages` routes the `campaign_assets` stream here. It uses `_paginate_get_collection` for campaigns and delegates per-type asset reads to `_paginate_campaign_asset_type`.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1229–1264)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one kind of asset for one campaign and adds campaign context to each asset row. If HubSpot says that asset type is unavailable, it quietly skips it.

**Data flow**: A client, campaign ID, campaign name, and asset type go in. The method pages through the campaign asset endpoint, creates stable compound IDs, adds asset kind and campaign fields, and yields pages.

**Call relations**: `_paginate_campaign_assets` calls this inside its campaign-and-asset-type loop. It uses `_paginate_get_collection` for the actual page walking.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1266–1272)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields HubSpot analytics views as a single page. Analytics views are saved filters or report views used later to query analytics reports.

**Data flow**: A client goes in. The method asks `_analytics_view_rows` to build normalized rows and yields them if any exist.

**Call relations**: `_product_pages` selects this for the `analytics_views` stream. It is a thin wrapper around `_analytics_view_rows`, which is also reused by analytics report syncing.

*Call graph*: calls 1 internal fn (_analytics_view_rows).


##### `HubSpotConnector._analytics_view_rows`  (lines 1274–1305)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches and normalizes analytics view definitions. It makes sure each row has an ID, name, filter details, and normalized created time.

**Data flow**: A client goes in. The method reads `/analytics/v2/views`, accepts either list or wrapped response shapes, filters invalid rows, and returns normalized dictionaries.

**Call relations**: `_paginate_analytics_views` yields these rows directly. `_paginate_analytics_reports` also calls this to run reports both for all traffic and for each available analytics view.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1307–1337)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs a broad set of HubSpot analytics report queries and yields them as records. It covers many subjects, time periods, and analytics views.

**Data flow**: A client goes in. The method picks a report date window, loads analytics views, builds all combinations of report family, subject, time period, and view filter, then yields pages from `_paginate_analytics_report_query`.

**Call relations**: `_product_pages` routes the `analytics_reports` stream here. It uses `_analytics_report_window`, `_analytics_view_rows`, and `_paginate_analytics_report_query` to break a large report space into smaller calls.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_window`  (lines 1340–1341)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range used for analytics report requests. The start is a fixed old date and the end is today in UTC.

**Data flow**: No input is needed. The method returns two strings in `YYYYMMDD` format: the configured historical start and the current date.

**Call relations**: `_paginate_analytics_reports` calls this before making report queries so every report combination uses the same time window.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1343–1394)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Runs one specific analytics report query and paginates through its breakdown rows. It skips report combinations HubSpot rejects as invalid or missing.

**Data flow**: A client plus report family, subject, time period, optional view, and date range go in. The method sends report requests with an offset, converts each response using `_analytics_report_rows`, and yields pages until all breakdowns are read.

**Call relations**: `_paginate_analytics_reports` calls this for every report combination. It hands each raw response to `_analytics_report_rows` so totals and breakdowns become normal records.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1397–1472)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Converts one HubSpot analytics report response into stored rows. It creates one totals row when present and one row per breakdown item.

**Data flow**: A report response and context fields go in. The function builds dictionaries with stable IDs, names, report metadata, filters, metrics, and human-readable date strings.

**Call relations**: `_paginate_analytics_report_query` calls this for every report response. It uses the class’s report ID and date formatting helpers to make consistent row fields.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1475–1479)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Builds a stable record ID for an analytics report row from several identifying parts. It sanitizes characters that would make IDs awkward.

**Data flow**: Any number of ID parts go in. The method turns them into strings, replaces slashes and colons, substitutes `none` for missing parts, and joins them behind an `analytics_report:` prefix.

**Call relations**: `_analytics_report_rows` uses this helper when creating totals and breakdown rows. Stable IDs let repeated syncs update the same report rows instead of creating duplicates.


##### `HubSpotConnector._analytics_report_date`  (lines 1482–1483)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Turns HubSpot report dates from compact `YYYYMMDD` text into the clearer `YYYY-MM-DD` form.

**Data flow**: An eight-character date string goes in. The method slices it into year, month, and day and returns a dashed date string.

**Call relations**: `_analytics_report_rows` uses this when adding start and end dates to report records.


##### `HubSpotConnector._paginate_event_types`  (lines 1485–1498)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot event type definitions. These describe kinds of events that can later have occurrences.

**Data flow**: A client goes in. The method accepts either a list response or a wrapped `results` response, ensures rows are dictionaries, adds an ID from available fields when possible, and yields them.

**Call relations**: `_product_pages` selects this paginator for the `event_types` stream.


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1500–1517)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot event occurrence records, optionally starting after the previous cursor. These are actual instances of tracked events.

**Data flow**: A client and optional cursor go in. The method sends `occurredAfter` when a cursor exists, reads event results, and yields valid dictionary rows; a 404 is treated as no data.

**Call relations**: `_product_pages` routes the `event_occurrences` stream here and passes the sync cursor into it.


##### `HubSpotConnector._paginate_email_events`  (lines 1519–1542)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads marketing email event records with HubSpot’s offset-based email events API. It can start from a saved cursor converted into the timestamp HubSpot expects.

**Data flow**: A client and optional cursor go in. The method builds request parameters, converts the cursor with `_email_event_start_timestamp`, yields event pages, and follows HubSpot’s offset until there are no more pages.

**Call relations**: `_product_pages` selects this for the `email_events` stream. It uses `_email_event_start_timestamp` to bridge the system’s cursor format to HubSpot’s millisecond timestamp parameter.

*Call graph*: calls 1 internal fn (_email_event_start_timestamp).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1545–1554)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts an email event cursor into the millisecond timestamp HubSpot’s email events API expects. It accepts either an already numeric cursor or an ISO date-time string.

**Data flow**: A cursor string or `None` goes in. The method returns `None`, an integer parsed directly from digits, or an integer timestamp parsed from a date-time string.

**Call relations**: `_paginate_email_events` calls this before making each email event request so incremental syncing can start at the right time.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._paginate_association_labels`  (lines 1556–1572)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the labels HubSpot uses for relationships between object types. Labels explain what an association means, such as a contact’s role in a deal.

**Data flow**: A client goes in. The method discovers object-type pairs with labels, converts each label into a normalized row, and yields non-empty pages.

**Call relations**: `_product_pages` routes the `association_labels` stream here. It depends on `_association_pairs_with_labels` for discovery and `_association_label_row` for row shaping.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels).


##### `HubSpotConnector._paginate_associations`  (lines 1574–1589)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual relationship rows between HubSpot object records. It discovers valid object-type pairs, lists source record IDs, then asks HubSpot for their associated target records in batches.

**Data flow**: A client goes in. The method loops over labeled object-type pairs, pages source object IDs, batches them into read requests, and yields association rows.

**Call relations**: `_product_pages` routes the `associations` stream here. It uses `_association_pairs_with_labels`, `_paginate_crm_object_id_pages`, and `_paginate_association_batch` to turn many pairwise API calls into one stream.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages).


##### `HubSpotConnector._paginate_association_batch`  (lines 1591–1618)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads associations for a batch of source record IDs and follows per-record continuation cursors. Some source records may have more associations than fit in one response.

**Data flow**: A client, source object type, target object type, and input IDs go in. The method posts a batch read request, yields normalized association rows, then builds the next pending inputs from response paging until finished.

**Call relations**: `_paginate_associations` calls this after it has source IDs. This method uses `_association_rows` to shape results, `_next_association_inputs` to continue paged records, and `_is_optional_pair_unavailable` to skip unsupported pairs.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1621–1635)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Builds the next batch request inputs for association reads that are not finished yet. HubSpot can return a separate next cursor for each source record.

**Data flow**: A batch association response goes in. The function extracts source record IDs that have `paging.next.after` values and returns input dictionaries containing those IDs and cursors.

**Call relations**: `_paginate_association_batch` calls this after each batch response. The returned inputs become the next loop’s pending work.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1637–1650)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Discovers object-type pairs that actually have association labels. This avoids trying to sync every possible pair when HubSpot says no relationship exists.

**Data flow**: A client goes in. The method gets all object types, checks labels for each from/to pair, and yields only pairs with labels.

**Call relations**: `_paginate_association_labels` uses this to emit label rows. `_paginate_associations` uses the same discovered pairs to fetch actual record-to-record associations.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1652–1665)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the list of object types to consider for association syncing. It starts with known standard HubSpot object types and adds custom object types if schemas are available.

**Data flow**: A client goes in. The method copies the standard object type list, tries to fetch custom schemas, extracts custom type IDs, and returns the combined list.

**Call relations**: `_association_pairs_with_labels` calls this before testing pair labels. It uses `_custom_object_schemas`, `_schema_object_type_id`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1667–1683)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches association labels for one source object type and one target object type. If HubSpot says the pair is invalid or unavailable, it returns no labels.

**Data flow**: A client plus from-object and to-object types go in. The method calls the pair’s labels endpoint and returns valid label dictionaries, or an empty list for optional unavailable pairs.

**Call relations**: `_association_pairs_with_labels` calls this for each possible pair. It uses `_is_optional_pair_unavailable` to distinguish harmless unsupported pairs from real errors.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1686–1703)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Normalizes one association label into a stable row. It adds source and target object types plus a stable ID built from the pair, category, and type ID.

**Data flow**: A raw label and its from/to object types go in. The function returns a dictionary with original label fields plus normalized association metadata.

**Call relations**: `_paginate_association_labels` calls this for each label discovered by `_association_pairs_with_labels`.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1705–1717)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Yields pages of record IDs for a CRM object type. This is useful when another API needs IDs as input, not full records.

**Data flow**: A client and object type go in. The method reads CRM object pages requesting only `hs_object_id`, extracts string IDs, and yields non-empty ID lists.

**Call relations**: `_paginate_associations` uses this to build batch association inputs. `_paginate_sequence_enrollments` uses it to walk contacts before fetching their sequence enrollment data.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1719–1749)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks the simple CRM object list endpoint for selected properties. This is a lighter alternative to search when the caller only needs basic fields or IDs.

**Data flow**: A client, object type, and requested property names go in. The method follows `after` pagination, yields dictionary records, and stops quietly for optional unavailable object types.

**Call relations**: `_paginate_crm_object_id_pages` and `_paginate_contact_identity_pages` call this. It uses `_is_optional_pair_unavailable` to skip unsupported reads.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1752–1786)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Converts a HubSpot batch association response into flat relationship rows. One source record may point to many target records, and each link may have one or more association types.

**Data flow**: A raw batch response plus source and target object types go in. The function loops through results, target records, and association types, returning one normalized row per relationship type.

**Call relations**: `_paginate_association_batch` calls this after each batch API response. It uses the class’s single-row shaping logic to create stable relationship records.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1789–1816)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one flat association record for a single source-to-target relationship. It includes IDs, object types, category, label, and a stable compound record ID.

**Data flow**: Association type details and source/target identifiers go in. The function returns a dictionary describing that one relationship in a consistent shape.

**Call relations**: `_association_rows` uses this while unpacking HubSpot batch association responses.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1819–1822)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Identifies errors that mean an optional HubSpot relationship or endpoint is simply unavailable. This prevents optional fan-out reads from breaking the entire stream.

**Data flow**: An HTTP error goes in. The method returns true for 400 or 404 responses, or for permission errors recognized by `_is_stream_unavailable`.

**Call relations**: Many fan-out methods call this when probing optional APIs, including association labels, association batches, list memberships, consent reads, sequences, and CRM object pages.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1824–1838)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads which records belong to each HubSpot list. Since memberships are reached through individual lists, it first fetches lists and then fans out to each list’s members.

**Data flow**: A client goes in. The method pages through lists, extracts each list ID, then yields membership pages from `_paginate_memberships_for_list`.

**Call relations**: `_product_pages` routes the `list_memberships` stream here. It depends on `_paginate_lists` to discover list IDs and `_paginate_memberships_for_list` to read members.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1840–1883)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the membership rows for one HubSpot list and adds list context to each member. This turns a nested endpoint into standalone records.

**Data flow**: A client, list record, and list ID go in. The method pages through the list membership endpoint, builds compound IDs from list ID and record ID, adds list name and object type, and yields pages.

**Call relations**: `_paginate_list_memberships` calls this for each list. It uses `_is_optional_pair_unavailable` so deleted or inaccessible lists do not stop the whole membership sync.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1885–1898)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot communication subscription definitions. These define the kinds of email subscriptions or preferences a contact can have.

**Data flow**: A client goes in. The method reads the definitions endpoint, accepts either `results` or `subscriptionDefinitions`, assigns each row an ID, and yields the rows.

**Call relations**: `_product_pages` selects this for the `subscription_definitions` stream.


##### `HubSpotConnector._paginate_consent_states`  (lines 1900–1913)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads email consent and unsubscribe status for contacts. It walks contacts with email addresses and asks HubSpot for both subscription-specific status and unsubscribe-all status.

**Data flow**: A client goes in. The method pages through contacts with emails, calls the consent and unsubscribe helpers for each email, combines their rows, and yields pages.

**Call relations**: `_product_pages` routes the `consent_states` stream here. It uses `_paginate_contact_identity_pages`, `_consent_status_rows`, and `_unsubscribe_all_rows`.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1915–1931)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields contact records with their email address made easy to access. Consent APIs are keyed by email, so this prepares contacts for consent lookup.

**Data flow**: A client goes in. The method reads contact pages requesting the `email` property, pulls email from either top-level or nested properties, and yields contact rows with an `email` field.

**Call relations**: `_paginate_consent_states` calls this before asking for each contact’s communication preferences. It uses `_paginate_crm_object_pages` for the actual contact listing.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 1933–1954)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches subscription-level email consent rows for one contact email. These rows say whether the contact is subscribed, unsubscribed, or otherwise restricted for each subscription type.

**Data flow**: A client, contact record, and email address go in. The method URL-escapes the email, calls HubSpot’s status endpoint, converts each returned row with `_consent_row`, and returns the list.

**Call relations**: `_paginate_consent_states` calls this for each contact email. It uses `_is_optional_pair_unavailable` to skip unavailable preference endpoints and `_consent_row` to normalize results.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 1956–1980)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches the global unsubscribe-all status for one contact email. This is separate from subscription-specific consent and needs its own endpoint.

**Data flow**: A client, contact record, and email address go in. The method URL-escapes the email, calls the unsubscribe-all endpoint, converts result rows with `_consent_row`, and returns them.

**Call relations**: `_paginate_consent_states` calls this alongside `_consent_status_rows` for each contact. It shares optional-error handling and row shaping with the subscription status path.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 1983–2014)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Normalizes one raw consent response into a stable consent state record. It adds contact ID, subject email, purpose, subscription type, status, legal basis, source, and captured time.

**Data flow**: A raw consent row, contact, email, and status kind go in. The function builds a dictionary with original fields plus normalized identifiers and display fields.

**Call relations**: `_consent_status_rows` and `_unsubscribe_all_rows` both call this so both consent kinds have the same stored shape.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2016–2043)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sales sequences for users who can own sequences. HubSpot requires a user ID filter, so the connector first discovers users from owners.

**Data flow**: A client goes in. The method gets user rows from `_sequence_user_rows`, fetches sequences for each user, adds owner context to each sequence, and yields pages.

**Call relations**: `_product_pages` routes the `sequences` stream here. It uses `_sequence_user_rows`, `_paginate_get_collection`, and `_is_optional_pair_unavailable` to continue past users whose sequence endpoint is unavailable.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows).


##### `HubSpotConnector._sequence_user_rows`  (lines 2045–2068)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the list of unique HubSpot user IDs from owner records. Sequence APIs use user IDs, while other CRM APIs often expose owner IDs.

**Data flow**: A client goes in. The method pages through owners, collects unique `userId` values, attaches owner ID and email, and yields the collected user rows.

**Call relations**: `_paginate_sequences` calls this before fetching sequences per user. It uses `_paginate_get_collection` to read owners.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2070–2088)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sequence enrollment information for contacts. It walks contact IDs and asks HubSpot whether each contact is enrolled in sequences.

**Data flow**: A client goes in. The method pages contact IDs, fetches enrollment data for each contact, normalizes rows with `_sequence_enrollment_rows`, and yields pages.

**Call relations**: `_product_pages` selects this for `sequence_enrollments`. It uses `_paginate_crm_object_id_pages`, `_sequence_enrollment_rows`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2091–2105)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes HubSpot sequence enrollment responses for one contact. It handles both wrapped result lists and single-object responses.

**Data flow**: Raw enrollment data and a contact ID go in. The function turns the response into a list of dictionaries, assigns stable IDs, and adds the contact ID to each row.

**Call relations**: `_paginate_sequence_enrollments` calls this after each contact-specific enrollment request.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2107–2136)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads submissions for every HubSpot form. Form submissions are reached by first listing forms, then requesting submissions for each form.

**Data flow**: A client goes in. The method pages through forms, extracts each form ID, reads submissions with a smaller page limit, adds form ID and form name, and yields submission pages.

**Call relations**: `_product_pages` routes the `form_submissions` stream here. It uses `_paginate_get_collection` for both the form list and each form’s submissions.

*Call graph*: calls 1 internal fn (_paginate_get_collection).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2138–2153)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages inside HubSpot conversation threads. Threads are fetched first, then each thread’s messages are fetched separately.

**Data flow**: A client goes in. The method pages through conversation threads, fetches messages for each thread ID, adds `thread_id` to each message, and yields pages.

**Call relations**: `_product_pages` selects this for `conversation_messages`. It uses `_paginate_get_collection` for both threads and messages.

*Call graph*: calls 1 internal fn (_paginate_get_collection).


##### `HubSpotConnector._paginate_pipelines`  (lines 2155–2162)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads deal and ticket pipeline definitions. Pipelines describe the high-level process stages used for deals or tickets.

**Data flow**: A client goes in. The method asks for pipeline rows for each supported object type and yields non-empty pages.

**Call relations**: `_product_pages` routes the `pipelines` stream here. It delegates object-specific shaping to `_pipeline_rows_for_object_type`.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2164–2214)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads individual stages inside deal and ticket pipelines. It turns nested stages into standalone records with pipeline context.

**Data flow**: A client goes in. For each pipeline object type, the method loads raw pipelines, loops through their stages, computes status and closure fields, and yields stage rows.

**Call relations**: `_product_pages` selects this for the `pipeline_stages` stream. It uses `_raw_pipelines_for_object_type` to get the nested pipeline data.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2216–2239)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes pipeline definitions for one object type, such as deals or tickets. It creates stable IDs that include the object kind.

**Data flow**: A client and object type go in. The method loads raw pipelines, skips ones without IDs, adds normalized ID, pipeline ID, object kind, display name, and active/archived status, then returns the rows.

**Call relations**: `_paginate_pipelines` calls this for each supported pipeline object type. It relies on `_raw_pipelines_for_object_type` for the API read.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2241–2252)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches raw pipeline data for one HubSpot object type. It treats missing or forbidden pipeline endpoints as simply having no pipelines.

**Data flow**: A client and object type go in. The method calls HubSpot’s pipeline endpoint and returns dictionary rows from `results`, or an empty list for 403 and 404 responses.

**Call relations**: `_pipeline_rows_for_object_type` uses this for pipeline records, and `_paginate_pipeline_stages` uses it to access nested stages.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2255–2259)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects the specific HubSpot error that says deleted-object pagination is not supported. This lets the connector skip only that optional cleanup step.

**Data flow**: An HTTP error goes in. The method checks for status 400 and looks for HubSpot’s known message about paging through deleted objects.

**Call relations**: `_paginate_archived_ids` and `_paginate_custom_object_archived_ids` call this when their archived sweeps fail. It uses `_upstream_message` to read HubSpot’s message text.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2262–2270)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Extracts the `message` field from a HubSpot error response if one exists. It is a small helper for clearer error decisions.

**Data flow**: An HTTP status error goes in. The method tries to parse the response body as JSON and returns the message string, or `None` if it cannot.

**Call relations**: _is_archived_sweep_unsupported uses this to recognize HubSpot’s deleted-object pagination limitation.


##### `HubSpotConnector._paginate_junction`  (lines 2272–2319)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds relationship rows for a small set of common parent-to-target links, such as deal-to-contact. It reads associations embedded in the parent object list endpoint.

**Data flow**: A client, parent object type, and target object type go in. The method pages through parent records with the requested associations included, emits one flat row per parent-target pair, and stops when there is no next page.

**Call relations**: `_paginate_unchecked` calls this for the synthetic junction streams created by `_junction`. These streams full-refresh because HubSpot does not provide modification timestamps for these associations.

*Call graph*: called by 1 (_paginate_unchecked).


### `extensions/sources/ufo_ext_sources/providers/instagram.py`

`io_transport` · `source sync run`

Instagram business accounts are reached through Facebook Pages, so this connector starts by asking Facebook for the Pages the current grant can access. From each Page it pulls the linked Instagram business account, then uses those account IDs to read posts, stories, and analytics-style “insights” such as reach, impressions, replies, or video views. Think of it like entering a building through the front desk: the Page is the front desk, and the Instagram account is the room you can reach from there.

The file defines several stream descriptions, such as pages, instagram_accounts, media, stories, and user_insights. A stream is one kind of record the sync system can ask for. Some streams are incremental, meaning they use a saved timestamp cursor so the next run only reads newer items.

The connector uses the Facebook Graph API’s paging style, where each response has a data list and sometimes a paging.next URL for the next batch. It follows those links until there is nothing left. If an individual media or story insight cannot be read because Facebook refuses that one object, it skips that object and keeps going. But if Facebook refuses the whole account walk because of missing permission or an invalid token, it raises a clean “stream skipped” signal instead of treating the run as a broken crash.

#### Function details

##### `InstagramConnector._paged`  (lines 92–109)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the reusable page-turner for Facebook Graph API list responses. It asks for one API page, yields the records found in its data section, then follows Facebook’s next-page link until there are no more pages.

**Data flow**: It receives an HTTP client, an API path or next-page URL, and optional query parameters. It repeatedly sends a GET request, reads the JSON response, extracts the list under data, and yields that list when it is not empty. After the first request, it stops reusing the original parameters because Facebook’s next URL already contains what is needed.

**Call relations**: _pages uses this to walk the user’s Facebook Pages. _account_collection uses it to walk per-account collections like media and stories. It relies on records_at to safely pull the data list out of the API response.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 111–116)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches all Facebook Pages visible to the current grant, including each Page’s linked Instagram business account if one exists. It is the starting point for nearly every other Instagram read in this connector.

**Data flow**: It starts with an empty list and asks /me/accounts for Page fields plus embedded Instagram account fields. As _paged returns batches, it appends them into one list. The result is a complete list of Page records gathered from all API pages.

**Call relations**: _instagram_accounts calls this to discover Instagram accounts behind Pages. _root_pages calls it when the requested stream is pages. It depends on _paged to do the actual Graph API pagination.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, _root_pages).


##### `InstagramConnector._instagram_accounts`  (lines 118–128)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This extracts the Instagram business accounts linked from the accessible Facebook Pages. It also attaches the Page ID and Page name so later records can be traced back to where the account came from.

**Data flow**: It reads the Page list from _pages, checks each Page for an instagram_business_account object with an ID, and stores those accounts by ID to avoid duplicates. It enriches each account with page_id and page_name, then returns the unique accounts as a list.

**Call relations**: _account_collection calls this before reading account-specific items like media and stories. _user_insights uses it before reading account-level metrics. _root_pages uses it when the requested stream is instagram_accounts.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _root_pages, _user_insights).


##### `InstagramConnector._account_collection`  (lines 130–151)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a collection that belongs to each Instagram account, such as media or stories. It also applies the saved cursor so old items can be ignored on later sync runs.

**Data flow**: It first gets all Instagram accounts, then for each valid account ID it calls the requested account path, such as /{account_id}/media or /{account_id}/stories. For each returned batch, it optionally filters out records whose cursor field is not newer than the stored cursor. Before yielding records, it adds the instagram_account_id to each one so the system knows which account produced it.

**Call relations**: _stream_pages calls this when the media or stories stream is requested. It uses _instagram_accounts to find accounts, _paged to read each collection, and with_context to attach the account ID to each returned record.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (_stream_pages); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 153–187)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads insight metrics for individual objects, such as a media post or a story. It turns Facebook’s metric rows into records with stable IDs tied back to the original object.

**Data flow**: It receives an async stream of object batches, such as media records. For each object with an ID, it asks /{object_id}/insights for the requested metrics. Each insight row is copied into a new record with an ID made from the object ID and metric name, plus parent_external_id and stream_name fields. If Facebook says a single object’s insights are unavailable with common refusal statuses, that object is skipped and the rest continue.

**Call relations**: _insight_pages calls this after choosing which source objects and metrics to use. It uses records_at to pull the data list from each insight response, and it deliberately lets unexpected HTTP errors bubble up instead of hiding them.

*Call graph*: called by 1 (_insight_pages); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 189–219)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads daily account-level insight values for each Instagram business account, such as impressions, reach, and profile views. It produces one record per account, metric, and day.

**Data flow**: It starts by loading all Instagram accounts. For each account, it asks the Graph API for daily insight metrics. It walks each metric’s values list, skips malformed values, and applies the cursor to keep only values newer than the stored end_time. It returns batches of rows with a generated ID, metric name, end time, value data, and the Instagram account ID.

**Call relations**: _stream_pages calls this when the user_insights stream is requested. It uses _instagram_accounts to know which accounts to query, records_at to read metric records, and list_or_empty to safely treat missing values as an empty list.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (_stream_pages); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 221–237)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry the wider source-sync system uses to ask this connector for records from a stream. It wraps the lower-level stream logic with friendly error handling for permission problems.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It asks _stream_pages for batches and yields them onward unchanged. If Facebook returns a whole-stream refusal such as unauthorized or forbidden, it turns that into StreamSkipped so the run records a skipped stream instead of a hard failure.

**Call relations**: The sync runner calls this style of method through the RestConnector contract. Inside this file, _insight_pages also calls paginate to reuse the normal media or stories walk before fetching their insights. paginate delegates the actual stream choice to _stream_pages.

*Call graph*: calls 2 internal fn (__init__, _stream_pages); called by 1 (_insight_pages).


##### `InstagramConnector._stream_pages`  (lines 239–273)

```
def _stream_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the router that decides which helper should produce records for a requested stream name. It maps names like media, stories, media_insights, and user_insights to the correct reading path.

**Data flow**: It receives the stream name and cursor. For root streams it returns _root_pages; for media and stories it returns _account_collection with the right API path, fields, and cursor field; for insight streams it returns the matching insight helper; for user insights it returns _user_insights. If the stream name is unknown, it raises StreamSkipped with a clear message.

**Call relations**: paginate calls this for every requested stream. It is the central switchboard that hands work to _root_pages, _account_collection, _insight_pages, or _user_insights depending on what the sync runner asked to read.

*Call graph*: calls 5 internal fn (__init__, _account_collection, _insight_pages, _root_pages, _user_insights); called by 1 (paginate).


##### `InstagramConnector._root_pages`  (lines 275–282)

```
async def _root_pages(self, client: httpx.AsyncClient, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This returns the simple top-level streams that do not need per-account collection walking: Facebook Pages and Instagram accounts. It packages the full list as a single yielded batch if there is anything to return.

**Data flow**: It receives the stream name. If the name is pages, it fetches Pages with _pages; otherwise it fetches linked Instagram accounts with _instagram_accounts. If the resulting list is non-empty, it yields that list once.

**Call relations**: _stream_pages calls this for the pages and instagram_accounts streams. It stands between the router and the discovery helpers so those root streams have the same async batch shape as all other streams.

*Call graph*: calls 2 internal fn (_instagram_accounts, _pages); called by 1 (_stream_pages).


##### `InstagramConnector._insight_pages`  (lines 284–293)

```
def _insight_pages(self, client: httpx.AsyncClient, source: str, metrics: str, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This prepares the flow for per-object insight streams, such as media insights or story insights. It first finds the source stream to read objects from, then asks _object_insights to fetch metrics for each object.

**Data flow**: It receives the source stream name, metric list, and output insight stream name. It looks up the matching source stream description, calls paginate for that source with no cursor, and passes the resulting object batches into _object_insights. The output is an async stream of insight record batches.

**Call relations**: _stream_pages calls this for media_insights and story_insights. This helper calls paginate so it can reuse the normal media or stories collection logic, then hands those objects to _object_insights to read their metric details.

*Call graph*: calls 2 internal fn (_object_insights, paginate); called by 1 (_stream_pages).


### `extensions/sources/ufo_ext_sources/providers/klaviyo.py`

`io_transport` · `source sync and request handling`

Klaviyo exposes its data through a web API, but the data arrives in a nested shape and uses Klaviyo-specific rules for authentication, paging, and filtering. This file hides those details behind a connector, so the rest of the system can simply ask for streams of records.

At the top, the file defines every Klaviyo “stream” the system can read. A stream is one kind of thing to sync, such as profiles or campaigns. Each stream says what field identifies a record, what date field can be used to resume from the last sync, and whether it is a main resource.

The `KlaviyoConnector` then does three main jobs. First, it creates an HTTP client with the headers Klaviyo expects, including the API revision and private-key authorization format. Second, it builds the first request for a stream, including page size, sorting, and optional “only records changed since this cursor” filtering. Third, it follows Klaviyo’s `links.next` pagination links until there are no more pages.

Klaviyo records are shaped like envelopes: the useful fields live under `attributes`, while IDs and relationships sit elsewhere. The connector flattens those envelopes into simpler records. It also pulls out important nested details, such as profile email consent, campaign subject lines, event metric names, and related profile or metric IDs. If Klaviyo refuses access to a stream because the API key lacks permission, the connector marks that stream as skipped instead of failing the whole sync.

#### Function details

##### `_stream`  (lines 36–54)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', created_at_field: str='created', updated_at_field: str | None='updated', canonical:
```

**Purpose**: This helper creates a stream description for one Klaviyo resource. It keeps the long list of Klaviyo streams readable by filling in common defaults, such as using `id` as the record key and `updated` as the usual change-tracking field.

**Data flow**: It receives a stream name and optional details such as the Klaviyo API object name, primary key, and date fields. It combines those inputs with sensible defaults, then returns a `StreamSpec`, which is the system’s small instruction card for syncing that resource.

**Call relations**: The file uses this helper while building `KLAVIYO_STREAMS`. Each call hands its settings to `StreamSpec.__init__`, so the connector later has a complete catalog of Klaviyo resources it can read.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 113–121)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method prepares the web client used to talk to Klaviyo. It adds Klaviyo’s required API revision header and, when a direct private key is available, formats the authorization header the way Klaviyo expects.

**Data flow**: It receives a base URL and a credential. It first asks the parent REST connector to build the normal HTTP client, then adds the `revision` header and possibly an `Authorization` header. It returns the same client, now ready for Klaviyo requests.

**Call relations**: This fits into connector setup before any Klaviyo API calls are made. The parent connector supplies the base client, and this method adds the Klaviyo-specific pieces needed for successful requests.


##### `KlaviyoConnector._next_path`  (lines 124–135)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This method turns Klaviyo’s full next-page URL into the path and query string the already-configured client can request. It is like taking a full street address and keeping only the part needed once you are already in the right city.

**Data flow**: It receives a `links.next` value, which may be a full URL or may be missing. If there is no usable URL path, it returns `None`. Otherwise it parses the URL, keeps the path, appends the query string if present, and returns that shorter request path.

**Call relations**: `paginate` calls this after each page of results. The returned path becomes the next request target; `None` tells `paginate` that there are no more pages.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 138–143)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: This method decides which timestamp field should be used to resume syncing a particular stream. Klaviyo is not fully consistent: most resources use `updated`, but some use `updated_at`, and events use `datetime`.

**Data flow**: It receives a stream description. It checks the stream name against the special groups for event time and `updated_at` resources. It returns the exact field name that should be used for sorting and incremental filtering.

**Call relations**: This supports request-building for incremental syncs. `_initial_query` uses its answer when constructing Klaviyo filter and sort parameters.


##### `KlaviyoConnector._initial_query`  (lines 146–160)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This method builds the query parameters for the first API request for a stream. It sets the page size, chooses the right sort order, and adds a “changed since this point” filter when there is a saved cursor.

**Data flow**: It receives a stream description and an optional cursor value from a previous sync. It creates a parameter dictionary, adds incremental filtering and sorting when the stream supports it, and adds special options for profiles and events. It returns the query parameters for the first page request.

**Call relations**: `paginate` calls this before fetching the first page. Later pages do not use these parameters because Klaviyo supplies a complete `links.next` URL for continuing the same query.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 163–174)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: This helper safely extracts the ID of a related Klaviyo object from a nested `relationships` block. It is defensive because Klaviyo may omit relationships or leave them empty.

**Data flow**: It receives a relationships value and the name of the relationship to read. It checks each expected layer, looks for `relationships[key].data.id`, converts the ID to text if present, and returns it. If anything is missing or shaped differently, it returns `None`.

**Call relations**: `flatten` uses this whenever a simple top-level related ID is useful, such as an event’s profile ID, an event’s metric ID, or a segment’s parent list ID.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 176–206)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method converts one nested Klaviyo record into a simpler flat record that the rest of the system can store, search, and use for incremental progress. It pulls `attributes` up to the top level and adds a few high-value relationship fields.

**Data flow**: It receives a raw Klaviyo record and its stream description. It starts a new dictionary with the record ID and resource type, copies all fields from `attributes`, removes list and segment profile counts so membership changes do not make those records look changed, then adds stream-specific extras. It returns the flattened record.

**Call relations**: This is the main cleanup step after pages are fetched. For profile-specific email consent details it calls `_flatten_profile`; for campaign sender and subject details it calls `_flatten_campaign`; for relationship IDs it calls `_lift_relationship_id`.

*Call graph*: calls 3 internal fn (_flatten_campaign, _flatten_profile, _lift_relationship_id).


##### `KlaviyoConnector._flatten_profile`  (lines 209–223)

```
def _flatten_profile(flat: dict[str, Any], attrs: Any) -> None
```

**Purpose**: This helper pulls email marketing consent and suppression details out of a profile’s nested subscription data. These fields are important because they explain whether and why a person can receive marketing email.

**Data flow**: It receives the flat output record being built and the original attributes block. It looks under subscriptions, then email, then marketing. If consent or suppression information is present, it writes simple fields such as `email_consent` and `email_suppression` into the flat record. It changes the passed-in dictionary and returns nothing.

**Call relations**: `flatten` calls this only for the `profiles` stream. It adds human-useful profile details that would otherwise remain buried in Klaviyo’s nested API shape.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector._flatten_campaign`  (lines 226–244)

```
def _flatten_campaign(flat: dict[str, Any], attrs: Any) -> None
```

**Purpose**: This helper pulls campaign email details into easy top-level fields, such as subject line, sender label, sender email, and the main included list. These are often the facts someone wants when recalling or searching campaign records.

**Data flow**: It receives the flat output record being built and the campaign attributes. It looks inside the campaign audience settings for message details and included lists, then writes simple fields into the flat record. If those nested values are missing, it falls back to direct campaign fields when available. It changes the passed-in dictionary and returns nothing.

**Call relations**: `flatten` calls this only for the `campaigns` stream. It turns campaign-specific nested data into ordinary fields alongside the rest of the flattened record.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.paginate`  (lines 246–299)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous method reads a Klaviyo stream page by page. It is responsible for making the web requests, following Klaviyo’s next-page links, enriching event records with metric names, and gracefully skipping streams the API key is not allowed to read.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It builds the first request path and query, fetches a page, extracts records, optionally matches included event metrics to event records, yields each non-empty page, and then follows `links.next` until no next path remains. If Klaviyo returns 401 or 403, it raises `StreamSkipped`; other HTTP errors are re-raised.

**Call relations**: This is the connector’s main reading loop. It calls `_initial_query` to start correctly and `_next_path` to continue through pages. When access is refused, it creates a `StreamSkipped` error so the larger sync run can record that this one stream was skipped instead of treating it like an ordinary data page.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/providers/mailchimp.py`

`io_transport` · `source sync run`

Mailchimp stores useful marketing data in several places. Some things, like campaigns or reports, are direct lists. Other things are tucked under a parent, such as members inside an audience list, interests inside an interest category, or unsubscribes inside a report. This file is the map and walking guide for all of those routes.

At the top, it defines the Mailchimp streams the system knows about and the key fields used to identify and update records. The MailchimpConnector then uses the shared RestConnector base class to make HTTP requests. HTTP is the web protocol used to talk to APIs.

The main job is pagination: Mailchimp returns records in chunks, not all at once, so the connector repeatedly asks for pages using count and offset values. For nested data, it first fetches parent IDs, then asks for each child collection. Think of it like checking every folder in a filing cabinet: first find the drawer labels, then open each drawer and read the papers inside.

The connector also adds missing context, such as list_id, segment_id, category_id, or campaign_id, so child records still make sense after they are separated from their parent API response. For email activity, Mailchimp groups many actions under one recipient, so this file splits those actions into separate rows and creates stable IDs for them. If Mailchimp refuses access with an authorization error, the stream is skipped with a clear explanation instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 62–80)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str='created_at', updated_at_field: str | None='updated_at', canonical
```

**Purpose**: This helper creates a StreamSpec, which is the small description the sync system uses to know what a Mailchimp stream is called, what its main ID field is, and which date field can be used for incremental syncing. It keeps the stream list compact and consistent.

**Data flow**: It receives a stream name plus optional details such as the source object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults where details are not provided, then returns a StreamSpec object that describes that stream to the rest of the system.

**Call relations**: This function is used while the file is being loaded to build the MAILCHIMP_STREAMS list. It hands the finished stream descriptions to StreamSpec.__init__, and the MailchimpConnector later exposes that list as the set of streams it can read.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector.record_identity`  (lines 148–155)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This decides the unique identity for a record. Most streams can use the normal identity logic from the base connector, but unsubscribes need a special combined key because the same email identifier can matter in the context of a specific campaign.

**Data flow**: It receives one record and the stream it belongs to. If the stream is not unsubscribes, it delegates to the parent connector. If it is unsubscribes, it reads campaign_id and email_id from the record and combines them into one identity string; if either part is missing, it returns nothing.

**Call relations**: The sync system calls this when it needs a stable record key. This method only steps in for unsubscribes; all other streams follow the general RestConnector behavior.


##### `MailchimpConnector.flatten`  (lines 157–163)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This lightly reshapes records after they are fetched. For member records, it creates a common created_at value from Mailchimp’s signup or opt-in timestamps so downstream code can treat them more consistently.

**Data flow**: It receives a record and its stream description. If the record is from list_members or segment_members, it copies the record and adds created_at from timestamp_signup, falling back to timestamp_opt. For every other stream, it returns the record unchanged.

**Call relations**: This fits into the base connector’s record-cleaning step after pagination has produced raw Mailchimp records. It does not call other local helpers; it simply prepares member records for the common data shape expected later.


##### `MailchimpConnector._data_field`  (lines 166–167)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This tells the connector which JSON field inside a Mailchimp response contains the actual list of records. Mailchimp uses names like lists, members, categories, or emails, so the connector needs this lookup to pull records from the right place.

**Data flow**: It receives a stream description. It checks the file’s stream-to-response-field map and returns the matching Mailchimp response key, or the stream name itself if no special key is listed.

**Call relations**: Pagination helpers call this before reading top-level, per-list, or per-report pages. It gives those helpers the correct field name to pass into the lower-level page reader.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 170–177)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the Mailchimp query parameter used for incremental syncing, meaning 'only give me records changed since this saved point.' It hides Mailchimp’s different names for different date filters.

**Data flow**: It receives a stream description and an optional cursor value, usually a timestamp from the last sync. If there is no cursor or the stream has no cursor field, it returns an empty parameter set. If Mailchimp supports filtering on that field, it returns the correct since-style parameter with the cursor value.

**Call relations**: Several pagination helpers call this before asking Mailchimp for pages. It supplies the filtering parameters that are then handed to the shared offset-page fetching logic.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 179–235)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading a Mailchimp stream. Given a stream name, it chooses the right walking pattern: direct pages, children under lists, children under reports, or deeper nested paths.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching pagination helper, and yields each page of records produced by that helper. If Mailchimp returns a 401 or 403 refusal, it turns that into a StreamSkipped error with a human-readable reason; other HTTP errors continue upward.

**Call relations**: The broader sync engine calls this to fetch records for a stream. This method then hands off to _paginate_top_level, _paginate_per_list, _paginate_interests, _paginate_segment_members, _paginate_per_report, or _paginate_email_activity depending on the stream.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 237–248)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Mailchimp resources that live directly at a top-level API path, such as lists, campaigns, automations, and reports. These are the simplest streams because they do not require visiting a parent object first.

**Data flow**: It receives an HTTP client, a stream description, an API path, and an optional cursor. It asks _data_field where records live in the response and _cursor_params whether to add a since filter, then repeatedly fetches offset-based pages and yields each page.

**Call relations**: The main paginate method calls this for top-level streams. This helper prepares the Mailchimp-specific details and relies on the base connector’s offset-page reader to do the repeated HTTP requests.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 250–267)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the reusable page reader for nested Mailchimp endpoints. It is used whenever records live under a parent path, such as members under a list or unsubscribes under a report.

**Data flow**: It receives an HTTP client, an API path, the response field that contains records, and optional base query parameters. It repeatedly requests pages from that path using Mailchimp’s count and offset style, then yields each list of records.

**Call relations**: Higher-level helpers call this after they have built the correct nested URL. It is the shared worker used by per-list, interests, segment-members, per-report, and email-activity pagination.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 269–277)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: This extracts IDs from a paged Mailchimp collection. It is a small helper for discovering parent objects before fetching their child records.

**Data flow**: It receives an HTTP client, an API path, and the response field containing records. It reads all pages from that collection, looks at each row, and yields the row’s id value as text when one is present.

**Call relations**: _list_ids and _report_ids call this to avoid repeating the same ID-scanning logic. Those ID streams then drive the deeper pagination walks.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 279–281)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This yields the IDs of all Mailchimp audience lists. Those IDs are needed because many useful resources are stored underneath a specific list.

**Data flow**: It receives an HTTP client. It asks _ids to read the /3.0/lists collection and yields each list ID that _ids finds.

**Call relations**: Per-list pagination, interest pagination, and segment-member pagination call this first. Each list ID it yields becomes part of a child API path.

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 283–285)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This yields the IDs of all Mailchimp campaign reports. Report IDs are needed to fetch report-specific data like unsubscribes and email activity.

**Data flow**: It receives an HTTP client. It asks _ids to read the /3.0/reports collection and yields each report ID that _ids finds.

**Call relations**: Per-report pagination and email-activity pagination call this before reading report children. Each report ID becomes the parent part of a nested Mailchimp URL.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 287–308)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads collections that exist under every Mailchimp audience list, such as members, segments, tags, or interest categories. It also adds the parent list ID to each child record so the record still says which list it came from.

**Data flow**: It receives an HTTP client, a stream description, the child path name, an optional cursor, and the name of a parent field to stamp onto records. It finds all list IDs, builds a child URL for each list, reads all pages from that child endpoint, adds list_id or another requested parent field to each record, and yields the pages.

**Call relations**: The main paginate method calls this for several per-list streams. It uses _list_ids to discover parents, _data_field and _cursor_params to prepare the request, _paginate_child to fetch records, and URL quoting to safely place IDs into paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 310–332)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Mailchimp interests, which are nested two levels deep: first under a list, then under an interest category. It preserves both the list and category context on each interest record.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It loops through list IDs, fetches interest categories for each list, then fetches interests for each category. For every interest record, it adds list_id and category_id when they are missing, then yields the page.

**Call relations**: The main paginate method calls this only for the interests stream. It uses _list_ids to find lists, _paginate_child to fetch both category and interest pages, and URL quoting to safely build nested Mailchimp paths.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 334–356)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads members who belong to each segment inside each Mailchimp list. Because the data is nested, it adds both list_id and segment_id to every member record.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It builds cursor filter parameters when possible, finds each list, fetches that list’s segments, then fetches members for each segment. It stamps each member with its parent list and segment IDs and yields the resulting pages.

**Call relations**: The main paginate method calls this for the segment_members stream. It combines _list_ids, _cursor_params, _paginate_child, and URL quoting to walk the list → segment → members path.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 358–378)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads collections that live under each Mailchimp report, such as unsubscribes. It adds the parent campaign or report context to each child record.

**Data flow**: It receives an HTTP client, a stream description, a child path, an optional cursor, and an optional field name for the parent ID. It finds all report IDs, builds a report-child URL for each one, reads all pages from that endpoint, stamps the parent ID onto each record when requested, and yields the pages.

**Call relations**: The main paginate method calls this for report-based child streams. It uses _report_ids to discover reports, _data_field and _cursor_params to shape the request, _paginate_child to read pages, and URL quoting to build safe paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 380–412)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads email activity from each report and turns Mailchimp’s grouped activity arrays into separate action records. That matters because each open, click, bounce, or similar action needs its own stable row for syncing.

**Data flow**: It receives an HTTP client and an optional cursor. It creates a since filter when a cursor is present, loops through report IDs, reads email-activity pages, and for each recipient record copies the shared recipient fields. It then combines those fields with each individual activity item, adds campaign_id, creates a stable synthetic id when Mailchimp does not provide one, and yields only pages that contain exploded activity rows.

**Call relations**: The main paginate method calls this for the email_activity stream. It uses _report_ids to visit every report, _paginate_child to fetch each report’s email activity, and URL quoting to safely place report IDs into request paths.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


### Enterprise CRM Synchronization
Provider reader for Salesforce CRM objects, including standard record syncing and deleted-record cleanup.

### `extensions/sources/ufo_ext_sources/providers/salesforce.py`

`io_transport` · `source sync`

Salesforce stores customer data in many object types, called SObjects, such as Account or Contact. This file defines a Salesforce connector that knows which of those objects to read and how to ask Salesforce for them through its REST API, which is Salesforce’s web-based way for programs to request data.

The connector does not keep a Salesforce password or token itself. It expects the surrounding runner to provide an authenticated HTTP client. For each stream, it first asks Salesforce to describe the object, meaning “tell me all the fields this organization exposes.” That matters because Salesforce installations can be customized heavily; hard-coding fields would miss customer-specific data or break when fields differ.

It then builds a SOQL query. SOQL is Salesforce’s query language, similar in spirit to SQL. The query selects all discovered fields, orders records by SystemModstamp, and, when there is a saved cursor from a previous run, only asks for newer records. Results are read in pages of up to 200 records, following Salesforce’s next-page link until there are no more.

After an incremental sync, it also asks Salesforce for records deleted since the cursor. Those are returned as tombstones, like a note saying “this item used to exist, now remove it.” If Salesforce refuses access with a 401 or 403 error, the stream is skipped with a clear reason instead of silently failing.

#### Function details

##### `_stream`  (lines 29–38)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates the description for one Salesforce data stream, such as accounts or contacts. It gives the rest of the sync system the object name, record ID field, date fields, and whether the stream is considered one of the main standard streams.

**Data flow**: It receives a friendly stream name, the Salesforce object name, and an optional flag saying whether it is canonical. It fills those into a StreamSpec, always using Id as the primary key and SystemModstamp as the cursor field. The result is a compact stream definition used later by the connector.

**Call relations**: At file load time, this helper is used to build the SALESFORCE_STREAMS list. Each call hands its settings to StreamSpec.__init__, which creates the structured stream description consumed by SalesforceConnector.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 78–82)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: This function writes the Salesforce query used to fetch records for one stream. It adds the saved cursor when present, so repeated syncs can ask only for records changed after the last checkpoint.

**Data flow**: It receives a stream description, a list of field names discovered from Salesforce, and an optional cursor timestamp. It joins the fields into a SELECT query, adds a WHERE clause when a cursor exists, sorts by the cursor field, and limits the page size. It returns the finished SOQL query string.

**Call relations**: SalesforceConnector.paginate calls this after it has learned the object’s fields. The returned query is then sent to Salesforce’s query endpoint to start reading records.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 84–90)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: This function asks Salesforce what fields exist on a particular object. It avoids relying on a fixed schema, which is important because each Salesforce organization can add or hide fields.

**Data flow**: It receives an authenticated HTTP client and a Salesforce object name. It calls Salesforce’s describe endpoint, reads the fields section of the response, keeps only valid field names, and returns them as a list of strings.

**Call relations**: SalesforceConnector.paginate calls this before building the query. Its output becomes the field list passed into SalesforceConnector._build_soql.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 92–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main reader for a Salesforce stream. It fetches records page by page and, on incremental runs, also reports records that were deleted since the last cursor.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. First it asks Salesforce for the stream’s fields, builds a SOQL query, and sends that query to Salesforce. It yields each non-empty batch of records, follows Salesforce’s next-page URL until the query is done, and then, if a cursor was supplied, yields a delete-tombstone page when deleted records are found. If Salesforce replies with an access refusal, it turns that into a StreamSkipped error with a human-readable message.

**Call relations**: This function is the connector’s main handoff point to the sync runner. Inside its flow it calls _describe_fields to learn the shape of the object, _build_soql to create the query, and _deleted_page to cover deletions after normal records are read. When access is denied, it creates a StreamSkipped exception so the broader source-sync machinery can skip that stream cleanly.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 121–139)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: This function checks Salesforce for records that were hard-deleted after the last sync point. It packages those missing IDs into a page the rest of the system can use to remove local copies.

**Data flow**: It receives an HTTP client, a stream description, and the previous cursor timestamp. It chooses the current time as the end of the deletion-check window, asks Salesforce’s deleted-records endpoint for IDs removed between the cursor and that end time, and reads Salesforce’s latest covered timestamp when available. It returns a StreamPage containing deleted IDs and the next cursor, or nothing if there is no useful page to return.

**Call relations**: SalesforceConnector.paginate calls this after it finishes fetching changed records, but only when there was already a cursor. This function uses datetime.now to mark the end of the deletion window and StreamPage.__init__ to create the tombstone page that paginate yields onward.

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 141–144)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function cleans up one Salesforce record before the rest of the system stores or indexes it. Salesforce wraps records with an attributes section that describes API metadata, and this method removes that wrapper so only actual field values remain.

**Data flow**: It receives one record dictionary and the stream it came from. If the record contains an attributes key, it returns a new dictionary with every other key kept and attributes removed. If there is no attributes key, it returns the original record unchanged.

**Call relations**: This is a connector hook used after records have been read. It does not call other listed functions; it simply prepares each fetched Salesforce record for the broader RestConnector-style sync pipeline.
