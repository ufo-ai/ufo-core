# CRM, marketing, advertising, and customer data source providers  `stage-14.2.7`

This stage is shared behind-the-scenes support for bringing outside customer and marketing data into the system. Each file is a connector, like a custom plug for a different service. The plug knows how to ask that service for data, handle its page-by-page replies, and reshape the answers into records the rest of the system can store, search, and resume syncing later.

ActiveCampaign, HubSpot, Salesforce, Attio, Apollo, and Intercom cover customer relationship data such as contacts, companies, deals, tasks, conversations, tickets, notes, and accounts. Klaviyo and Mailchimp bring in marketing data such as profiles, audiences, campaigns, lists, events, subscribers, and reports. Facebook Ads and Google Ads pull advertising structures and performance numbers, from accounts and campaigns down to ads and daily metrics. Instagram reads business pages, posts, stories, and analytics through Meta’s API. Typeform imports forms, responses, workspaces, themes, images, and webhooks.

Together, these connectors turn many different outside APIs, each with its own rules, into one steady source-sync flow.

## Files in this stage

### Customer relationship foundations
Connectors for CRM and customer/contact databases that provide core people, company, deal, and activity records.

### `extensions/sources/ufo_ext_sources/providers/active_campaign.py`

`io_transport` · `source sync`

ActiveCampaign exposes many kinds of data: contacts, lists, campaigns, deals, accounts, custom fields, tags, users, and more. This file is the connector for reading those objects through ActiveCampaign’s version 3 web API. Without it, the wider sync system would not know which ActiveCampaign data streams exist, what each stream is called in the API, how to authenticate, or how to fetch large result sets a page at a time.

The file first defines a catalog of streams. A stream is one kind of record to copy, such as “contacts” or “deals.” For each one, the connector records practical details like its main ID field and which date field can be used as a cursor. A cursor is a saved “last seen” value, like a bookmark, so later runs can ask for only newer or changed records when ActiveCampaign supports that.

ActiveCampaign returns list results in envelopes, meaning the records are nested under a key such as `contacts` or `campaignMessages`, with paging controlled by `limit` and `offset`. The connector hides those details. It chooses the correct API path, adds an incremental date filter when possible, and keeps requesting pages until there are no more full pages.

Authentication is also ActiveCampaign-specific. Instead of a usual bearer token, direct API keys must be sent as an `Api-Token` header. If access is refused, the connector raises a clear stream-skip error rather than letting the whole sync fail unclearly.

#### Function details

##### `_stream`  (lines 99–115)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard description of one ActiveCampaign stream, such as contacts or campaigns. This keeps the long stream list readable and makes sure each stream gets consistent metadata, like its primary key and date fields.

**Data flow**: It receives the stream name and optional details such as the API object name, primary key, cursor field, and whether it is a core stream. It fills in defaults where details are not supplied, decides whether the cursor field also counts as an updated-at field, and returns a `StreamSpec`, which is the system’s compact record of how to sync that stream.

**Call relations**: This helper is used while the module is being loaded to build the ActiveCampaign stream catalog. It hands each finished stream description to `StreamSpec`, so the connector can later expose one consistent list of readable ActiveCampaign resources.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 178–183)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to ActiveCampaign, with the right kind of authentication. It exists because ActiveCampaign expects direct API keys in an `Api-Token` header, not in the more common bearer-token format.

**Data flow**: It receives a base URL and a resolved credential. If the credential already includes a custom transport, such as a broker or proxy, it leaves that path alone and delegates to the parent connector. Otherwise, it reads the credential’s key, wraps it into an `Api-Token` header, and asks the parent connector to create the actual asynchronous HTTP client. If no key is present, it raises an error immediately.

**Call relations**: The wider REST connector setup calls this when a sync run needs a client for the tenant’s ActiveCampaign API host. This method only adjusts the authentication shape, then hands client construction back to the shared REST connector machinery.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 186–187)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Finds the exact ActiveCampaign API path name and response key for a stream. This matters because some streams use different casing or names in the API than they do inside this project.

**Data flow**: It receives a stream description. It looks up the stream’s internal name in the file’s mapping of special ActiveCampaign names. If there is a match, it returns the API URL segment and the JSON envelope key; if not, it safely uses the stream name for both.

**Call relations**: The pagination method calls this before making requests. It gives pagination the two names it needs: where to send the request and where to find the records inside ActiveCampaign’s response.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 189–213)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches records for one ActiveCampaign stream, one page at a time. It adds incremental filters when ActiveCampaign supports them, and turns authentication or permission refusals into a controlled skipped-stream result.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous run. It resolves the stream’s API path and response key, builds request parameters with a page size of 100, and, when possible, adds a filter asking for records changed after the cursor. It then yields each page of records from the shared offset-page reader. If ActiveCampaign returns 401 or 403, it raises `StreamSkipped` with a clear explanation; other HTTP errors are passed upward unchanged.

**Call relations**: During a sync, the connector framework calls this for each selected ActiveCampaign stream. It first asks `_resolve_stream_segment` how ActiveCampaign names that stream, then relies on the parent REST paging helper to do the repeated network requests. The pages it yields are what the rest of the sync pipeline consumes and stores.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### `extensions/sources/ufo_ext_sources/providers/apollo.py`

`io_transport` · `source sync polling`

Apollo does not offer a simple “give me everything changed since yesterday” option, and it does not send webhooks when contacts or accounts change. So this connector has to poll Apollo: it asks Apollo for search results page by page, newest first, and stops once it reaches records the system has already seen.

The file defines two readable streams: contacts and accounts. Each stream says what Apollo object it represents, which field uniquely identifies a record, and which timestamp is used as the checkpoint, also called a watermark. A watermark is like a bookmark in time: on the next run, the connector only keeps records created after that bookmark.

Apollo also has a special authentication rule. It expects the API key in an `X-Api-Key` header, not in the usual bearer-token `Authorization` header. This connector adjusts the HTTP client so Apollo receives the key in the right place.

During syncing, the connector posts to Apollo’s search endpoints with a page number, page size, and newest-first sort order. It yields only records above the saved checkpoint. If a page contains any older record, the connector stops because later pages will be older too. If Apollo refuses access with a 401 or 403 status, the connector marks that stream as skipped instead of crashing the whole source sync.

#### Function details

##### `ApolloConnector._make_client`  (lines 63–71)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Apollo. It fixes the authentication header so Apollo gets the API key in the format it accepts.

**Data flow**: It receives a base API address and a resolved credential. It first lets the shared REST connector build a normal client, then, if the credential contains a bearer-style key, it removes the standard `Authorization` header and places that same key into Apollo’s required `X-Api-Key` header. It returns the adjusted client, ready to make Apollo requests.

**Call relations**: This is part of the connector setup before any pages are fetched. The broader REST source machinery calls it when it needs a client for Apollo, and the returned client is later used by the pagination code to make search requests.


##### `ApolloConnector.paginate`  (lines 73–106)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Apollo stream, such as contacts or accounts, page by page. It is designed to resume from a saved checkpoint, so repeat syncs do not reread the whole Apollo database unless they need to.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value that represents the last saved creation time. It looks up the right Apollo search endpoint, asks for records newest first, turns the response field into a safe list, filters that list through `ApolloConnector._above`, and yields any records that are newer than the cursor. It stops when Apollo returns no records, when a page contains older records, or when Apollo says there are no more pages. If Apollo refuses access with 401 or 403, it raises `StreamSkipped` with a clear message; other HTTP errors continue upward as real failures.

**Call relations**: The sync engine calls this when it wants records for a configured Apollo stream. Inside the loop it relies on `list_or_empty` to safely read result arrays, `get_path` to read Apollo’s nested page count, and `ApolloConnector._above` to decide what is still new enough to sync. When access is refused, it hands control back to the sync system by raising `StreamSkipped`, meaning this stream cannot be read with the current key.

*Call graph*: calls 2 internal fn (__init__, _above); 2 external calls (get_path, list_or_empty).


##### `ApolloConnector._above`  (lines 109–118)

```
def _above(records: list[dict[str, Any]], cursor: str | None) -> list[dict[str, Any]]
```

**Purpose**: This keeps only records that are newer than the saved checkpoint. It is the small filter that makes incremental Apollo syncing possible even though Apollo itself cannot filter by modification time.

**Data flow**: It receives a list of Apollo records and an optional cursor string. If there is no cursor, it returns all records because this is a first run or a full read. If there is a cursor, it checks each record’s `created_at` value and returns only records whose timestamp text is greater than the cursor. It does not change the original records.

**Call relations**: This helper is called by `ApolloConnector.paginate` after each page comes back from Apollo. Its result tells `paginate` both what to yield and when to stop: if some records on the page are not above the cursor, then the connector has reached already-seen data and should not continue into older pages.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/attio.py`

`io_transport` · `source sync`

Attio’s API does not return every kind of data in the same shape. Companies, people, and deals come from one style of endpoint. Tasks and notes come from another. Meetings and call recordings use cursor-based paging, where the API gives a “next page” marker instead of a numeric offset. This file hides those differences behind one connector called AttioConnector.

The connector’s job has two main parts. First, it fetches every page of data from Attio. Because Attio does not provide one reliable “last changed” field for all streams, each stream is treated as a full snapshot: the system rereads everything and removes local rows that disappeared upstream. Second, it flattens Attio’s records. Attio stores identifiers inside an id object and most fields inside arrays of “value cells.” Those cells may hold text, numbers, emails, selected options, linked records, locations, and more. Without flattening, the rest of UFO would not have a clear top-level primary key or easy fields to index.

The file also makes failure behavior friendly. If an Attio standard object is disabled, or the OAuth grant is missing permission, the stream is skipped instead of crashing the whole sync. Think of it like a careful librarian: it visits each shelf Attio exposes, copies the books it can read, labels them clearly, and politely skips locked rooms.

#### Function details

##### `_records_stream`  (lines 36–44)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates the stream description used for Attio object records such as companies, people, and deals. A stream description tells the sync system what the stream is called, where it comes from in Attio, and which field uniquely identifies each row.

**Data flow**: It receives a stream name, an Attio object slug, and whether the stream is canonical. It builds a StreamSpec with record_id as the primary key, no incremental cursor, and delete_missing turned on. The result is a ready-to-use stream definition.

**Call relations**: This helper is used while the file defines ATTIO_STREAMS. It calls StreamSpec.__init__ to create the shared description that the connector later uses when deciding how to page and flatten Attio records.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 71–72)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: Safely pulls one id value out of a nested dictionary. It exists because Attio often wraps identifiers inside small id objects.

**Data flow**: It receives any value and the key to look for. If the value is a dictionary, it returns value[key] when present; otherwise it returns None. It does not change anything.

**Call relations**: AttioConnector._value_primitive calls this when an option or status does not have a readable title and the connector needs to fall back to a stored id.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 82–83)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: Builds the request body for Attio’s object-record query endpoint. It sets the page size and the offset, which is the number of records already skipped.

**Data flow**: It receives an offset number. It returns a dictionary containing Attio’s maximum supported record-query limit and that offset. The returned body is sent to Attio in a POST request.

**Call relations**: AttioConnector._paginate_records calls this before each object-record request so every page asks for the right slice of records.

*Call graph*: called by 1 (_paginate_records).


##### `AttioConnector._value_primitive`  (lines 86–129)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: Turns one Attio value cell into the plain value people expect, such as a string, number, email address, domain, selected option title, linked record id, or formatted location. This is the heart of making Attio’s flexible field format usable.

**Data flow**: It receives one value-cell dictionary from Attio. It checks the known places Attio may store the real value, such as value, option.title, status.title, email_address, phone_number, currency_value, or location parts. It returns one simple value, or None if it cannot find one.

**Call relations**: Flattening helpers rely on this function when reducing Attio’s nested field data. When option or status data only has an id, it calls _nested_id to extract that fallback id.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 132–147)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: Simplifies one Attio field cell into a single usable value. It knows how to deal with either a list of value cells, one dictionary cell, or an already-simple value.

**Data flow**: It receives one field cell. If the cell is a list, it extracts primitive values and removes blanks; for multi-select options it may keep the list, otherwise it returns the first value. If the cell is a dictionary, it extracts its primitive value. The output is a simple value, a list for certain multi-select fields, or None.

**Call relations**: This function is part of the flattening path used when records are prepared for storage. It delegates the detailed cell interpretation to AttioConnector._value_primitive.


##### `AttioConnector._flatten_list_cell`  (lines 150–157)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: Turns a field into a clean list of values. It is used for Attio fields where keeping every value matters, such as emails, phone numbers, domains, and categories.

**Data flow**: It receives either a list-style Attio cell or a single cell. It extracts simple values, drops missing ones, and always returns a list. A missing or empty value becomes an empty list.

**Call relations**: AttioConnector._flatten_values uses this for fields that should not be collapsed to just one value.


##### `AttioConnector._flatten_values`  (lines 160–194)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Converts the whole Attio values block into ordinary top-level fields. It also adds convenient shortcut fields like email, phone, domain, category, first_name, and last_name.

**Data flow**: It receives the values dictionary from an Attio record. For each attribute, it flattens the cell into either a simple value or a list. It then derives common easy-to-read fields from name, domains, categories, email addresses, and phone numbers. It returns a flat dictionary of attributes.

**Call relations**: This is used by the record-flattening flow so standard and custom object records become usable rows rather than nested Attio API shapes.


##### `AttioConnector._flatten_record`  (lines 197–211)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Flattens a standard or custom Attio object record, such as a company, person, or deal. It lifts important identity and timestamp fields to the top level and adds flattened attributes.

**Data flow**: It receives one raw Attio record and the stream description. It reads id.record_id, object_id, workspace_id, created_at, updated_at, and any cursor-like field if configured. It then merges in flattened values from the record’s values block. The result is one plain dictionary with record_id as the key field.

**Call relations**: AttioConnector.flatten calls this for streams that are not tasks, notes, meetings, or call recordings. It is the default flattening path for Attio object streams.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 214–218)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares a task record by making its task_id available at the top level. This gives the sync system a clear primary key for tasks.

**Data flow**: It receives a raw task record. It copies the record, reads id.task_id when the id is nested, and writes that value as task_id. It returns the copied and slightly enriched task row.

**Call relations**: AttioConnector.flatten calls this when the current stream is tasks.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 221–225)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares a note record by making its note_id available at the top level. This gives notes the same clear identity shape as other synced rows.

**Data flow**: It receives a raw note record. It copies the record, reads id.note_id when available, and writes that value as note_id. It returns the copied note row.

**Call relations**: AttioConnector.flatten calls this when the current stream is notes.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 228–232)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares a meeting record by lifting its meeting_id to the top level. This makes meetings easy for the rest of the system to identify and store.

**Data flow**: It receives a raw meeting record. It copies the record, reads id.meeting_id when the id is nested, and writes that value as meeting_id. It returns the copied meeting row.

**Call relations**: AttioConnector.flatten calls this when the current stream is meetings.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 235–256)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Prepares a call recording record by lifting its call_recording_id and turning transcript segments into readable text. It also falls back to a web URL when no recording URL is present.

**Data flow**: It receives a raw call recording record. It copies the record, extracts id.call_recording_id, fills recording_url from web_url if needed, and joins transcript segments into transcript_text with speaker names when available. It returns the enriched recording row.

**Call relations**: AttioConnector.flatten calls this when the current stream is call_recordings. The transcript data it formats may have been attached earlier by AttioConnector._paginate_call_recordings.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 258–267)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening method for the current Attio stream. It is the connector’s public cleanup step before records are handed to the rest of UFO.

**Data flow**: It receives one raw record and the stream it came from. It checks the stream name and sends the record to the matching flattener for tasks, notes, meetings, call recordings, or ordinary object records. It returns one plain dictionary ready for storage or indexing.

**Call relations**: The broader source sync calls this after records are fetched. It calls AttioConnector._flatten_task, _flatten_note, _flatten_meeting, _flatten_call_recording, or _flatten_record depending on the stream.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 269–278)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Streams pages of records from Attio for one stream and turns certain permission errors into a clean skip. This keeps one unavailable stream from stopping the whole sync.

**Data flow**: It receives an HTTP client, a stream description, and an unused cursor value. It asks _pages for page batches and yields them onward. If Attio returns a missing-scope authorization error, it raises StreamSkipped with a readable reason; other errors still bubble up.

**Call relations**: The source sync uses this as the main page reader. It calls AttioConnector._pages for the actual endpoint choice, AttioConnector._is_scope_unauthorized to recognize OAuth permission failures, and AttioConnector._scope_skip_reason to explain the skip.

*Call graph*: calls 4 internal fn (__init__, _is_scope_unauthorized, _pages, _scope_skip_reason).


##### `AttioConnector._pages`  (lines 280–293)

```
def _pages(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses which paging strategy to use for a stream. Attio has several endpoint styles, so this function is the switchboard that sends each stream to the right reader.

**Data flow**: It receives an HTTP client and a stream description. Based on the stream name, it returns an async iterator for simple offset paging, cursor paging, call-recording fan-out, or object-record querying. Nothing is fetched here until the returned iterator is consumed.

**Call relations**: AttioConnector.paginate calls this first. It hands tasks and notes to AttioConnector._paginate_simple, meetings to AttioConnector._paginate_cursor, call_recordings to AttioConnector._paginate_call_recordings, and object streams to AttioConnector._paginate_records.

*Call graph*: calls 4 internal fn (_paginate_call_recordings, _paginate_cursor, _paginate_records, _paginate_simple); called by 1 (paginate).


##### `AttioConnector._paginate_records`  (lines 295–316)

```
async def _paginate_records(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads standard and custom Attio object records, such as companies, people, and deals, using Attio’s records query endpoint. It walks through offset-based pages until no more records remain.

**Data flow**: It receives an HTTP client and a stream. It builds a POST body with the current offset, requests a page, yields the data list, and advances the offset by the number of records returned. If the object is disabled in Attio, it raises StreamSkipped instead of treating that as a fatal error.

**Call relations**: AttioConnector._pages calls this for ordinary object streams. It calls AttioConnector._build_query_body for each request and AttioConnector._is_object_disabled when Attio rejects the object query.

*Call graph*: calls 3 internal fn (__init__, _build_query_body, _is_object_disabled); called by 1 (_pages).


##### `AttioConnector._paginate_simple`  (lines 318–325)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use simple limit-and-offset paging, currently tasks and notes. Offset paging means asking for records 0-499, then 500-999, and so on.

**Data flow**: It receives an HTTP client, an endpoint path, and a page size. It uses the base REST connector’s offset-page helper to request pages from the data field and yields each page as a list of records.

**Call relations**: AttioConnector._pages calls this for the tasks and notes streams. It relies on shared REST connector behavior for the actual repeated GET requests.

*Call graph*: called by 1 (_pages).


##### `AttioConnector._paginate_cursor`  (lines 327–345)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Attio endpoints that use cursor paging, currently meetings and meeting call recordings. A cursor is a token from the API that says where the next page starts.

**Data flow**: It receives an HTTP client, endpoint path, page size, and optional query parameters. It asks the shared REST connector to read records from data and follow pagination.next_cursor until there are no more pages. It yields each page of records.

**Call relations**: AttioConnector._pages calls this for meetings. AttioConnector._paginate_call_recordings also calls it to list meetings first and then list recordings for each meeting.

*Call graph*: called by 2 (_pages, _paginate_call_recordings).


##### `AttioConnector._paginate_call_recordings`  (lines 347–383)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the call_recordings stream by first walking meetings, then fetching recordings under each meeting, and then attaching transcript and meeting context. This is needed because Attio exposes recordings as children of meetings rather than one global list.

**Data flow**: It receives an HTTP client. It pages through meetings, extracts each meeting id, title, start time, end time, and duration, then pages through that meeting’s recordings. For each recording, it adds parent meeting fields and tries to fetch the transcript. It yields recording pages enriched with that extra context.

**Call relations**: AttioConnector._pages calls this for the call_recordings stream. Inside, it calls AttioConnector._paginate_cursor for meetings and recordings, _meeting_id and _call_recording_id to read nested ids, _datetime_of and _duration_seconds to normalize timing, and _fetch_transcript to retrieve transcript details.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (_pages).


##### `AttioConnector._fetch_transcript`  (lines 385–397)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: Fetches the transcript for one call recording when Attio has it ready. If the transcript is not available yet, it quietly returns None.

**Data flow**: It receives an HTTP client plus a meeting id and recording id. It requests the transcript endpoint. A 404 or 409 response becomes None, while other HTTP errors are re-raised. If the response contains a dictionary under data, that dictionary is returned.

**Call relations**: AttioConnector._paginate_call_recordings calls this for each recording that has an id, so transcript segments can later be flattened into readable transcript_text.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 400–404)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a meeting id from Attio’s possible id shapes. This avoids duplicating id-shape checks in the call-recording flow.

**Data flow**: It receives a meeting dictionary. If meeting.id is a dictionary, it returns id.meeting_id; if meeting.id is already a string, it returns that string. Otherwise it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this while walking meetings, because recordings can only be requested when the parent meeting id is known.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 407–411)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: Extracts a call recording id from Attio’s possible id shapes. This gives the connector the identifier needed to fetch a recording’s transcript.

**Data flow**: It receives a recording dictionary. If recording.id is a dictionary, it returns id.call_recording_id; if recording.id is already a string, it returns that string. Otherwise it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this before asking AttioConnector._fetch_transcript for transcript data.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 414–418)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: Normalizes Attio’s meeting time object into one string. Attio may describe a timed meeting with datetime or an all-day meeting with date.

**Data flow**: It receives a time-shaped value. If it is a dictionary, it returns the datetime value when present, otherwise the date value. If the input is not the expected shape, it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this for meeting start and end values before copying them onto recordings and calculating duration.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 421–431)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: Calculates an approximate meeting duration in seconds from two ISO 8601 time strings. ISO 8601 is the common machine-readable date-time format, like 2024-01-01T10:00:00Z.

**Data flow**: It receives optional start and end strings. If either is missing or cannot be parsed, it returns None. Otherwise it parses both times, subtracts start from end, clamps negative results to zero, and returns the number of seconds.

**Call relations**: AttioConnector._paginate_call_recordings calls this after normalizing meeting start and end times. It uses datetime.datetime.fromisoformat to parse the time strings.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 434–443)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes Attio’s specific error for a disabled standard object. This lets the connector skip, for example, deals or companies if that object is not enabled in a workspace.

**Data flow**: It receives an HTTP error. It checks for status code 400, tries to read the JSON response body, and returns True only when the body code is standard_object_disabled. Otherwise it returns False.

**Call relations**: AttioConnector._paginate_records calls this when an object-record request fails. A True result is turned into StreamSkipped with a clear message.

*Call graph*: called by 1 (_paginate_records).


##### `AttioConnector._is_scope_unauthorized`  (lines 446–455)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes Attio’s specific error for a missing OAuth scope. An OAuth scope is a permission granted to an app, such as permission to read meetings or notes.

**Data flow**: It receives an HTTP error. It checks for status code 403, tries to parse the JSON body, and returns True only when the body code is unauthorized. Otherwise it returns False.

**Call relations**: AttioConnector.paginate calls this when page reading fails. If it returns True, the stream is skipped instead of letting the missing permission crash the sync.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._scope_skip_reason`  (lines 458–464)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a readable explanation for why a stream was skipped due to missing OAuth permission. It includes Attio’s message when available.

**Data flow**: It receives an HTTP error. It tries to parse the response body and read its message field. It returns a sentence explaining that the OAuth grant is missing a required scope, falling back to a generic note if no message is available.

**Call relations**: AttioConnector.paginate calls this after AttioConnector._is_scope_unauthorized recognizes a missing-scope error. The returned text is passed into StreamSkipped.

*Call graph*: called by 1 (paginate).


### Paid advertising analytics
Connectors for paid media platforms that sync advertising account structure and campaign performance data.

### `extensions/sources/ufo_ext_sources/providers/facebook_ads.py`

`io_transport` · `source sync`

This connector is the system's read-only bridge to Facebook Ads. Without it, the system would not know which Facebook API addresses to call, how to page through long result lists, how to split data by ad account, or how to resume from the last synced point instead of rereading everything.

The file defines the Facebook Ads streams the system can fetch: ad accounts, campaigns, ad sets, ads, and ad-level daily insights. A stream is a named kind of data, with rules such as its main ID field and, for some streams, the date field used as a bookmark.

The connector first asks Facebook for the grant's ad accounts. For account-specific data, it then visits each account and asks for that account's campaigns, ad sets, ads, or insights. This is like first getting a list of stores in a chain, then visiting each store to collect its local sales records.

Facebook returns large results in pages. The connector follows Facebook's own “next page” link until there are no more pages. For campaigns, ad sets, and ads, it filters out records older than the saved cursor. For insights, it asks for daily ad metrics from the cursor date, or the last 90 days on a first run, and creates a stable synthetic ID for each row. If Facebook rejects access with an authorization error, the stream is skipped cleanly instead of crashing the whole sync.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 78–91)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one Facebook API collection page after another. It hides the repetitive work of following Facebook's paging links so the rest of the connector can simply receive batches of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It calls the API, reads the JSON response, pulls the list under the response's data field, yields that list when it is not empty, then follows the response's next-page URL if Facebook provides one. The output is an async stream of record batches; it does not store anything itself.

**Call relations**: This is the low-level page reader used by the account lookup, account child streams, and insights reader. Those higher-level functions decide what Facebook object to request, while this function repeatedly fetches pages and uses records_at to pull out the actual list of records.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 93–98)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Gets all ad accounts available to the current Facebook credential. Other streams need this first because campaigns, ads, and insights are requested separately for each ad account.

**Data flow**: It starts with a fixed list of ad account fields, then asks _paged to read /me/adaccounts from Facebook. It gathers every returned page into one list and returns that full list of account dictionaries.

**Call relations**: paginate calls this directly when syncing the ad_accounts stream. _account_children and _insights also call it first so they know which accounts to visit before requesting account-specific data.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 100–125)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads account-owned objects such as campaigns, ad sets, and ads. It also applies the saved cursor so repeat syncs only pass along records that changed after the last checkpoint.

**Data flow**: It receives the stream being synced and an optional cursor value. It chooses the right Facebook fields for that stream, loads all ad accounts, then requests the matching child collection under each account. For each returned page, it optionally filters records whose cursor field is not newer than the saved cursor, adds account context such as ad account ID and name, and yields the enriched page.

**Call relations**: paginate uses this when the requested stream is campaigns, ad_sets, or ads. It relies on _accounts to find the accounts, _paged to read each account's pages, and with_context to attach account information so downstream records still show where they came from.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 127–170)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads daily ad performance metrics, such as impressions, clicks, spend, and reach. It builds each insight row into a stable record by adding an ID made from the account, campaign, ad set, ad, and date.

**Data flow**: It receives an HTTP client and an optional cursor. It builds Facebook insight query parameters: if a cursor exists, it requests data from that date through today; otherwise it requests the last 90 days. It then loads all ad accounts, requests each account's insights pages, adds an ad_account_id, creates a repeatable ID for every row, and yields non-empty batches.

**Call relations**: paginate calls this for the ads_insights stream. This function uses _accounts to find every account, _paged to walk through Facebook's paginated insight responses, json.dumps to format the date range parameter, and the current UTC date to set the end of a cursor-based sync window.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 172–196)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct reading path for each Facebook Ads stream. It is the main entry point the source framework calls when it wants records for a stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. Based on the stream name, it delegates to _accounts, _account_children, or _insights and yields the batches they produce. If the stream is unknown, or if Facebook refuses access with a 401 or 403 status, it raises StreamSkipped so the system can skip that stream cleanly.

**Call relations**: The wider source syncing framework calls this during a sync. It acts as the traffic director: ad_accounts goes to _accounts, campaigns/ad_sets/ads go to _account_children, and ads_insights goes to _insights. It also converts Facebook permission failures into StreamSkipped, while letting other HTTP errors continue upward.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 198–206)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records just before they are handed onward, with a small special case for campaigns. For campaign records, it makes important fields easier and more consistent for downstream users.

**Data flow**: It receives one record and its stream description. If the stream is campaigns, it returns a copy with a simplified status field that prefers effective_status, plus a created_at field copied from Facebook's created_time. For all other streams, it returns the record unchanged.

**Call relations**: This fits into the connector's output cleanup step after records have been fetched. Unlike paginate, it does not call other helpers; it simply shapes individual records so later storage or recall code sees a more consistent campaign record.


### `extensions/sources/ufo_ext_sources/providers/googleads.py`

`io_transport` · `source sync`

Google Ads is not a simple “download my data” service. To read from it, the system must send Google Ads Query Language queries, which are SQL-like requests such as “select these campaign fields from campaigns.” It must also include two kinds of permission: an OAuth credential for the advertiser account, and a Google Ads developer token that proves this application is allowed to use the Ads API. This file is the bridge between UFO’s generic source-sync machinery and those Google Ads rules.

The file defines the streams this connector can read: customers, campaigns, ad groups, ads, campaign metrics, and customer-client relationships. Think of each stream like a separate shelf in a filing cabinet. The connector first asks Google which customer accounts are accessible. Then, for each customer account, it runs the right query and tags every returned row with that customer id, so later steps know where the row came from.

Google Ads returns nested objects, such as a campaign object inside a result row. The `flatten` step pulls out the most important fields into a flatter shape, including stable ids used for storing and updating records. If the developer token is missing, or Google refuses access with an authorization error, the connector skips the stream instead of crashing the whole sync. This matters because a user may connect OAuth successfully but still lack Google Ads API approval.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 73–82)

```
def _developer_token(self) -> str
```

**Purpose**: This function finds the Google Ads developer token required on every Google Ads API request. OAuth alone is not enough for Google Ads, so without this token the connector cannot safely ask the API for data.

**Data flow**: It reads two environment variables, first `UFO_GOOGLE_ADS_DEVELOPER_TOKEN` and then `GOOGLE_ADS_DEVELOPER_TOKEN`. If one is present, it returns that token as text. If neither exists, it raises `StreamSkipped`, which tells the sync runner to skip this Google Ads stream instead of treating it as an unexpected crash.

**Call relations**: When the connector is building its HTTP client, `GoogleAdsConnector._make_client` calls this function to fetch the token before any Google Ads requests are sent. If the token is missing, the skip signal starts here and prevents later API calls that Google would reject anyway.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 84–92)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to Google Ads and adds the special headers Google requires. In plain terms, it prepares the “letterhead” that every request must carry so Google knows both who is asking and which approved developer is making the request.

**Data flow**: It receives a base API address and an OAuth credential. It asks the parent connector to create the basic authenticated HTTP client, then adds the developer token header from `GoogleAdsConnector._developer_token`. It also optionally reads a login customer id from the environment, removes dashes from it, and adds it as another header. The result is a ready-to-use `httpx.AsyncClient` for Google Ads requests.

**Call relations**: This is part of the connector setup before pagination begins. It depends on `GoogleAdsConnector._developer_token` for the required developer token; once the client is prepared, later functions use that client to list customers and run Google Ads queries.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 94–101)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This function asks Google Ads which customer accounts the current credential can access. The rest of the connector needs these ids because Google Ads queries must be run separately under each customer account.

**Data flow**: It takes the prepared HTTP client, sends a request to Google Ads’ accessible-customers endpoint, and reads the returned resource names. For each name shaped like `customers/123`, it extracts just the final id, such as `123`. It returns a list of customer id strings.

**Call relations**: `GoogleAdsConnector._query_each_customer` calls this first so it knows which accounts to query. The ids returned here become the loop that drives all stream reads for campaigns, ads, metrics, and the other Google Ads resources.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 103–122)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: This function sends one Google Ads query for one customer account and collects the result rows. It is the low-level read operation behind every stream in this connector.

**Data flow**: It receives the HTTP client, a customer id, and a Google Ads Query Language query. It posts that query to the customer-specific `searchStream` endpoint. Google responds with batches of results, so the function walks through those batches, keeps only dictionary-like result rows, and returns them as a list.

**Call relations**: `GoogleAdsConnector._query_each_customer` calls this once per accessible customer id. Higher-level stream logic in `GoogleAdsConnector.paginate` chooses the query text, while this function focuses on sending that query and unpacking Google’s batched response.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 124–132)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function runs the same query across every accessible Google Ads customer account. It also labels each returned row with the customer id it came from, which is important because the same kind of campaign or ad data can exist under many accounts.

**Data flow**: It receives an HTTP client and a query string. First it gets the list of customer ids from `GoogleAdsConnector._customer_ids`. Then, for each id, it calls `GoogleAdsConnector._search_stream`. If rows come back, it adds `customer_id` to each row and yields that group as a page of records.

**Call relations**: `GoogleAdsConnector.paginate` uses this helper for every supported stream after choosing the correct query. This function is the middle layer: it does not decide what to ask Google for, but it decides to ask every accessible customer and hands back pages to the sync runner.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 134–202)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function is the main read path for each Google Ads stream. Given a stream name, it chooses the correct Google Ads query, runs it across all accessible customers, and yields pages of records for the sync system to process.

**Data flow**: It receives the HTTP client, a stream description, and an optional cursor, which is a saved position from a previous sync. It matches the stream name to a query for customers, campaigns, ad groups, ads, campaign metrics, or customer clients. For campaign metrics, it uses the cursor date if available; otherwise it asks for roughly the last 90 days. It sends the chosen query through `GoogleAdsConnector._query_each_customer` and yields each returned page. If the stream is unknown, it raises `StreamSkipped`. If Google replies with a 401 or 403 refusal, it also raises `StreamSkipped` with a clearer explanation.

**Call relations**: The source-sync framework calls this when it wants records for a particular stream. This function then delegates the repeated per-customer work to `GoogleAdsConnector._query_each_customer`. It is also the place where authorization failures are translated into a controlled skip, so one refused Google Ads stream does not look like an ordinary programming error.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 204–235)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes Google Ads’ nested response rows into flatter records that are easier for the rest of the system to store, compare, and recall. It also creates or exposes the key fields used to identify records.

**Data flow**: It receives one raw record and the stream it belongs to. For customer records, it pulls out the customer id and display name. For campaign records, it pulls out the campaign resource name, name, status, and start date. For campaign metrics, it combines the customer id, campaign id, and date into a stable metric id, and copies out common numbers like impressions, clicks, and cost. For streams that do not need special reshaping, it returns the record unchanged.

**Call relations**: After `GoogleAdsConnector.paginate` yields raw rows from Google Ads, the broader source framework can call this function to normalize each row. It uses `dict_or_empty` so missing nested objects are treated as empty dictionaries rather than causing avoidable failures.

*Call graph*: 1 external calls (dict_or_empty).


### HubSpot CRM and marketing
The broad HubSpot connector covers CRM, marketing, analytics, associations, lists, and custom object data.

### `extensions/sources/ufo_ext_sources/providers/hubspot.py`

`io_transport` · `source sync / request handling`

HubSpot is not one simple database. Different areas of HubSpot use different web API shapes, different pagination styles, and different names for dates and identifiers. This connector is the translation layer. Without it, the system would not know where to fetch HubSpot pages, how to continue from the last sync, how to notice deleted CRM records, or how to skip data that an account is not allowed to read.

The file first declares all the HubSpot “streams,” meaning named feeds of records such as contacts, companies, forms, campaign assets, and consent states. A stream says what object it reads, what field uniquely identifies a record, and what date can be used as a bookmark for incremental syncing.

The HubSpotConnector then routes each stream to the right fetching method. Normal CRM objects use HubSpot's search API. Product areas, such as conversations or files, use their own list endpoints. Some streams are assembled by walking parent records and asking for child data, like form submissions for each form or messages for each conversation thread. Custom objects are discovered first by reading their schemas, then synced one object type at a time.

A key theme is normalization: HubSpot often nests useful fields under properties or returns values as name/value pairs. This connector flattens those into ordinary records. It also treats missing permissions as a skipped stream rather than a failed run, which matters because HubSpot features vary by account tier and OAuth scope.

#### Function details

##### `_normalize_epoch_millis`  (lines 249–256)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Turns a HubSpot timestamp expressed as milliseconds since 1970 into a readable ISO date string. It deliberately leaves booleans and already-normal values alone so it does not accidentally rewrite unrelated fields.

**Data flow**: It receives any value. If the value is a number, or a string made only of digits, it treats it as milliseconds since the Unix epoch and converts it to a UTC date-time string; otherwise it returns the original value unchanged.

**Call relations**: It is used when product API records contain older timestamp formats, especially in flattened product rows and analytics view rows.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 259–274)

```
def _stream(name: str, *, object_type: str, canonical: bool=True, modified_property: str='hs_lastmodifieddate') -> StreamSpec
```

**Purpose**: Creates a stream description for normal HubSpot CRM objects. A stream description tells the sync runner what the stream is called, what HubSpot object it reads, which field is the unique ID, and which date field acts as the bookmark.

**Data flow**: It receives a stream name, HubSpot object type, and a few options. It produces a StreamSpec object that the connector later uses to decide how to fetch and store that stream.

**Call relations**: It is used while the module is loaded to define CRM-style streams such as companies, contacts, deals, tickets, and many activity or commerce objects.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 277–296)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='createdAt', updated_at_field: str | None='updatedAt', pagi
```

**Purpose**: Creates a stream description for HubSpot product APIs that are not standard CRM object searches. These streams are marked as non-canonical because they are supporting records rather than the main normalized CRM objects.

**Data flow**: It receives the stream name, source object, key fields, date fields, and optional pagination rule. It returns a StreamSpec that captures those choices for later syncing.

**Call relations**: It is used during stream declarations for owners, workflows, forms, conversations, analytics, associations, sequences, and other product-specific feeds.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 299–311)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Builds the standard pagination recipe for HubSpot GET list endpoints. Pagination means asking for records page by page instead of trying to fetch everything at once.

**Data flow**: It receives an API path. It returns a Pagination object that says records live under results, the next-page cursor lives under paging.next.after, and future requests should send that cursor as the after query parameter.

**Call relations**: It is used when declaring flat product API streams that all share HubSpot's common list response shape.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 314–324)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Creates a stream description for a synthetic relationship table, such as deal-to-contact links. These streams do not come from their own HubSpot object; they are built by reading associations embedded on parent records.

**Data flow**: It receives the junction stream name and the parent object type. It returns a StreamSpec with no cursor field, because HubSpot does not provide a modified time for these relationships.

**Call relations**: It is used at module load time to define relationship streams like deal_contacts and ticket_companies, which are later fetched through the junction paginator.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 630–660)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the request body for HubSpot's CRM search API. It asks HubSpot for all known properties, sorted by the bookmark field, and optionally only records changed since the last cursor.

**Data flow**: It receives a stream, the list of property names to request, the current cursor, and a page cursor called after. It returns a JSON-ready dictionary that HubSpot's search endpoint understands.

**Call relations**: CRM object pagination and custom object pagination call this before each search request so both paths use the same incremental-sync rules.

*Call graph*: called by 2 (_paginate_crm_object, _paginate_custom_object_records).


##### `HubSpotConnector._flatten`  (lines 663–674)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a standard CRM object response into a simpler flat record. HubSpot wraps most object fields inside properties; this lifts them beside id and timestamps.

**Data flow**: It receives one HubSpot record. It starts with id, createdAt, updatedAt, and archived, then copies all fields from properties onto the top level and returns the combined record.

**Call relations**: The public flatten method calls this for ordinary CRM object streams after records have been fetched.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 677–700)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records from HubSpot APIs that do not look exactly like CRM search results. It smooths over shapes such as objectId, properties bags, and form submission values.

**Data flow**: It receives a product API record and the stream description. It copies the record, fills in id from objectId when needed, lifts properties and values into top-level fields, normalizes a few timestamp formats, and returns the flat record.

**Call relations**: The public flatten method sends product API streams here; this helper uses _normalize_epoch_millis for fields that HubSpot returns as epoch milliseconds.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 702–709)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening rule for each HubSpot stream. This keeps the rest of the system from needing to know which HubSpot APIs wrap fields differently.

**Data flow**: It receives a raw record and its stream. Junction and custom-object rows are already shaped and pass through; product API rows go through product normalization; standard CRM rows go through CRM flattening.

**Call relations**: The sync framework calls this after pages are fetched. It delegates to _flatten or _flatten_product_api depending on the stream.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector.record_identity`  (lines 711–724)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Computes the stable identity for a record, with special handling for consent states. Consent records need a compound identity because the same contact can have many subscription or unsubscribe statuses.

**Data flow**: It receives a flat record and stream. For most streams it uses the base connector's identity logic; for consent states it combines contact, subscription or status kind, and business unit into one stable string.

**Call relations**: The sync framework uses this when deciding whether a row is new, changed, or the same as a previous row.


##### `HubSpotConnector._list_properties`  (lines 726–734)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which fields exist for a CRM object type. HubSpot search only returns properties that are explicitly requested, so this prevents the connector from hard-coding a limited field list.

**Data flow**: It receives an HTTP client and a HubSpot object name. It requests that object's property metadata and returns the property names that HubSpot reports.

**Call relations**: _paginate_crm_object calls this before searching so each CRM object sync asks for every available property.

*Call graph*: called by 1 (_paginate_crm_object).


##### `HubSpotConnector.paginate`  (lines 736–749)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the safe outer paging method for a stream. It fetches pages, but turns certain permission failures into a clean skipped-stream signal instead of crashing the whole HubSpot sync.

**Data flow**: It receives an HTTP client, stream, and optional cursor. It yields pages from _paginate_unchecked; if HubSpot says the stream is unauthorized or unavailable, it raises StreamSkipped with a readable reason.

**Call relations**: The source runner calls this to read a stream. It delegates the real fetching to _paginate_unchecked and uses _is_stream_unavailable plus _stream_skip_reason when HubSpot refuses access.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 751–778)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Routes each stream to the correct paging strategy. It is like a switchboard that sends CRM objects, product APIs, custom objects, and relationship streams down different paths.

**Data flow**: It receives a stream and cursor. Depending on the stream's configuration or name, it yields pages from a declared pagination strategy, junction pagination, custom object pagination, product API pagination, or CRM search pagination.

**Call relations**: paginate calls this after setting up error handling. This method hands off to _paginate_crm_object, _paginate_custom_objects, _paginate_junction, or _paginate_product_api.

*Call graph*: calls 4 internal fn (_paginate_crm_object, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._paginate_crm_object`  (lines 780–807)

```
async def _paginate_crm_object(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Reads one standard HubSpot CRM object stream through the search API. It supports incremental syncing and also follows up with deleted-record detection.

**Data flow**: It receives a stream and last cursor. It lists all properties, repeatedly posts search requests, filters out duplicate records at the inclusive cursor boundary, yields record pages, then yields tombstone pages for archived records.

**Call relations**: _paginate_unchecked calls this for regular CRM objects. It relies on _list_properties, _build_search_body, and _paginate_archived_ids.

*Call graph*: calls 3 internal fn (_build_search_body, _list_properties, _paginate_archived_ids); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._is_stream_unavailable`  (lines 810–833)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes HubSpot errors that mean an account cannot access a particular stream. This separates normal product-tier or permission limits from real connector failures.

**Data flow**: It receives an HTTP error. It checks for a 403 response and scans the error message for permission-related phrases, returning true only when the stream should be skipped.

**Call relations**: paginate uses it for top-level stream skipping, and archived sweeps use it to stop quietly when the account cannot read that object.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 836–845)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds the human-readable reason recorded when a stream is skipped. It includes HubSpot's own message when available.

**Data flow**: It receives the stream name and HTTP error. It tries to read the JSON error body, extracts the message, and returns a sentence explaining that the stream is unavailable for this account.

**Call relations**: paginate calls this after _is_stream_unavailable decides the error should become StreamSkipped.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 847–882)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds CRM records that HubSpot has archived or deleted so the local copy can be tombstoned. HubSpot's search endpoint does not include archived records, so this extra sweep is needed.

**Data flow**: It repeatedly calls the CRM list endpoint with archived=true, collects record IDs, and yields StreamPage objects containing deletes. If HubSpot says archived paging is unsupported or unavailable, it stops quietly.

**Call relations**: _paginate_crm_object calls this after normal search pages. It uses _is_archived_sweep_unsupported and _is_stream_unavailable to decide when to ignore an archived sweep failure.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_crm_object); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 884–892)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Starts pagination for non-CRM HubSpot product streams. It is a thin bridge between the general stream router and the product-specific dispatcher.

**Data flow**: It receives a stream and cursor. It asks _product_pages for the right async page source and yields each page it produces.

**Call relations**: _paginate_unchecked calls this for streams listed as product API streams.

*Call graph*: calls 1 internal fn (_product_pages); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._product_pages`  (lines 894–929)

```
def _product_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the special paginator for a product API stream, or falls back to a simple GET collection. This keeps one table of exceptions for HubSpot APIs that need custom walking.

**Data flow**: It receives an HTTP client, stream name, and optional cursor. It returns an async iterator from a named special paginator, or from _paginate_get_collection using the stream's configured path.

**Call relations**: _paginate_product_api calls this. It points streams such as lists, analytics reports, associations, consent states, sequences, and form submissions to their custom routines.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api); 1 external calls (partial).


##### `HubSpotConnector._paginate_get_collection`  (lines 931–959)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a common HubSpot list endpoint that returns results plus a next cursor. It is the reusable worker for many product API streams.

**Data flow**: It receives a path, page size, and optional extra query parameters. It GETs each page, ensures objectId can become id when needed, yields records, and follows paging.next.after until there is no next page.

**Call relations**: Many product-specific paginators call this for their inner loops, including owners, campaign assets, forms, conversations, sequences, and the default product stream fallback.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_sequences, _product_pages, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 961–996)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Discovers and reads all custom object types defined in the HubSpot account. Custom objects are account-specific, so the connector cannot declare their exact types ahead of time.

**Data flow**: It fetches custom object schemas, extracts each schema's object type and properties, creates a temporary search stream, yields that object's records, then yields tombstones for archived records of that custom type.

**Call relations**: _paginate_unchecked calls this for the custom_objects stream. It coordinates _custom_object_schemas, schema helpers, record pagination, and archived-ID pagination.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 998–1000)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches HubSpot's definitions for custom objects. A schema describes what the custom object is called and which properties it has.

**Data flow**: It receives an HTTP client, calls the custom object schema endpoint, and returns only dictionary-shaped schema rows.

**Call relations**: _paginate_custom_objects uses it to discover custom object streams, and association logic uses it so custom objects can be included in relationship discovery.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 1003–1008)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Finds the best usable object type identifier inside a custom object schema. HubSpot may expose this under several possible field names.

**Data flow**: It receives a schema dictionary. It checks objectTypeId, fullyQualifiedName, and name in order, returning the first non-empty string or None.

**Call relations**: Custom object pagination and association object discovery call this whenever they need the API identifier for a schema.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1011–1026)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Collects all property names worth requesting for a custom object. It includes normal properties plus display fields so records are useful to humans.

**Data flow**: It receives a schema, walks its properties list, primary display property, and secondary display properties, and returns a de-duplicated list of names.

**Call relations**: _paginate_custom_objects calls this before searching records for each custom object type.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1028–1063)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Reads records for one custom object type through HubSpot's CRM search API. It applies the same incremental boundary de-duplication used for standard CRM objects.

**Data flow**: It receives a temporary stream, schema, property list, and cursor. It posts search requests page by page, skips duplicate records exactly on the cursor boundary, converts each raw record into a custom object row, and yields pages.

**Call relations**: _paginate_custom_objects calls this for each discovered schema. It uses _build_search_body and _custom_object_row.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1065–1104)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Builds a readable, stable row for one custom object record. It adds object metadata so records from different custom object types can live safely in one stream.

**Data flow**: It receives a raw record and schema. It combines properties, record ID, object type ID, labels, title fields, timestamps, and archive status into one flat dictionary, or returns None if required IDs are missing.

**Call relations**: _paginate_custom_object_records calls this for each custom object returned by search. It uses _schema_object_type_id to form stable IDs.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1106–1136)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived records for one custom object type so the local store can remove or tombstone them. This mirrors the archived sweep used for standard CRM objects.

**Data flow**: It receives an object type ID. It pages through the archived list endpoint and yields StreamPage delete entries using IDs prefixed with the custom object type.

**Call relations**: _paginate_custom_objects calls this after reading active records for each custom object type. It uses the same unavailable and unsupported checks as the standard archived sweep.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1138–1157)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a stream of owner teams by reading owners, because HubSpot exposes teams nested under owner records. It de-duplicates teams seen through multiple owners.

**Data flow**: It reads all owners, looks at each owner's teams list, stores each team by ID, and finally yields the unique team records.

**Call relations**: The product stream dispatcher selects this for the owner_teams stream, and it reuses _paginate_get_collection to fetch owners.

*Call graph*: calls 1 internal fn (_paginate_get_collection).


##### `HubSpotConnector._paginate_lists`  (lines 1159–1188)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot contact or object lists through the lists search endpoint. Lists use an offset-style paging model rather than the common after cursor.

**Data flow**: It posts search requests with a count and offset, adds a string id from listId, lifts additionalProperties onto the record, yields each page, and advances until hasMore is false.

**Call relations**: The product dispatcher uses this for lists, and _paginate_list_memberships calls it before fetching members of each list.

*Call graph*: called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_site_search`  (lines 1190–1210)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads CMS content through HubSpot's site search API for a specific content type. For example, knowledge articles are found this way.

**Data flow**: It receives a content type, requests pages with limit and offset, yields result rows, and stops when the calculated next offset reaches the reported total.

**Call relations**: The product stream dispatcher uses this through a pre-filled content_type for knowledge_articles.


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1212–1236)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds campaign asset records by first reading campaigns, then asking HubSpot for many kinds of assets attached to each campaign. This turns a nested marketing concept into a flat stream.

**Data flow**: It pages through campaigns, extracts each campaign's ID and name, then for every supported asset type calls the asset-type paginator and yields its pages.

**Call relations**: The product dispatcher selects this for campaign_assets. It uses _paginate_get_collection for campaigns and _paginate_campaign_asset_type for the per-type fan-out.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1238–1273)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one asset type for one campaign and stamps each asset with campaign context. It ignores missing or forbidden asset categories because not every account or campaign supports every type.

**Data flow**: It receives campaign ID, campaign name, and asset type. It pages through the campaign asset endpoint, creates stable IDs combining campaign, type, and asset ID, adds asset kind and metrics, and yields pages.

**Call relations**: _paginate_campaign_assets calls this repeatedly, once per campaign and asset type. It reuses _paginate_get_collection for the actual page walking.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1275–1281)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields the account's analytics views as a stream. Analytics views are saved filter sets used when querying reports.

**Data flow**: It asks _analytics_view_rows for normalized rows, then yields them as one page if any exist.

**Call relations**: The product dispatcher selects this for analytics_views, and the same row builder is reused by analytics report pagination.

*Call graph*: calls 1 internal fn (_analytics_view_rows).


##### `HubSpotConnector._analytics_view_rows`  (lines 1283–1314)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches and normalizes HubSpot analytics view definitions. It gives each view a stable id, readable name, filters, and normalized dates.

**Data flow**: It calls the analytics views endpoint, accepts either a list or results-shaped response, filters valid rows, derives IDs and names, normalizes created dates, and returns a list of view records.

**Call relations**: _paginate_analytics_views uses it to emit analytics view records, and _paginate_analytics_reports uses it to query reports for each saved view.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1316–1346)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Generates many analytics report queries across HubSpot's report families, subjects, time periods, and saved views. This broad fan-out captures totals and breakdowns in a searchable form.

**Data flow**: It calculates the report date window, fetches analytics views, creates an all-traffic filter plus one filter per view, then yields pages from each individual report query combination.

**Call relations**: The product dispatcher selects this for analytics_reports. It coordinates _analytics_report_window, _analytics_view_rows, and _paginate_analytics_report_query.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_window`  (lines 1349–1350)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range for analytics reports. The connector asks from a fixed early start date through today's UTC date.

**Data flow**: It takes no input. It returns a pair of strings in HubSpot's YYYYMMDD format: the fixed start date and the current date.

**Call relations**: _paginate_analytics_reports calls this once before launching its report query fan-out.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1352–1403)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Runs one concrete analytics report query and pages through its breakdown rows. Some combinations are not supported by HubSpot, so those are skipped cleanly.

**Data flow**: It receives report family, subject, time period, optional view filter, and date range. It GETs report pages, converts each response into rows, yields them, and advances by the number of breakdowns until all are read.

**Call relations**: _paginate_analytics_reports calls this for each report combination. It delegates row shaping to _analytics_report_rows.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1406–1481)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Converts a HubSpot analytics report response into flat records. It creates one totals row and one row per breakdown value.

**Data flow**: It receives the raw report response plus context such as subject, time period, view, dates, and offset. It builds stable IDs, names, metrics, filters, and normalized dates, then returns a list of report rows.

**Call relations**: _paginate_analytics_report_query calls this after each HubSpot report response. The rows it creates are then yielded as analytics report pages.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1484–1488)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Builds a safe, stable ID for an analytics report row. It replaces characters that would make the ID ambiguous, such as slashes and colons.

**Data flow**: It receives any number of ID parts. It stringifies them, substitutes safe characters, joins them with colons, and prefixes the result with analytics_report.

**Call relations**: Analytics report row construction uses this whenever it needs an ID for totals or breakdown records.


##### `HubSpotConnector._analytics_report_date`  (lines 1491–1492)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Converts HubSpot's compact analytics date format into a normal date string. This makes report dates easier for downstream systems and people to read.

**Data flow**: It receives a string like YYYYMMDD and returns YYYY-MM-DD by slicing the year, month, and day parts.

**Call relations**: Analytics report row construction uses this for start_date and end_date fields.


##### `HubSpotConnector._paginate_event_types`  (lines 1494–1507)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot event type definitions. Event types describe categories of custom behavioral events.

**Data flow**: It calls the event types endpoint, accepts either a list or results-shaped response, adds an id from id or fullyQualifiedName when possible, and yields the rows.

**Call relations**: The product stream dispatcher selects this for the event_types stream.


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1509–1526)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual event occurrence records, optionally starting after the last synced occurrence time. This is the event stream's incremental path.

**Data flow**: It receives an optional cursor. If present, it sends it as occurredAfter, fetches events, filters dictionary rows, and yields them; a 404 means the feature is unavailable and produces no rows.

**Call relations**: The product stream dispatcher selects this for event_occurrences and passes the sync cursor into it.


##### `HubSpotConnector._paginate_email_events`  (lines 1528–1551)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot email event records, such as opens or clicks, using HubSpot's older offset-based endpoint. It can start from the last synced timestamp.

**Data flow**: It turns the cursor into a millisecond timestamp when possible, requests events with a large limit, yields event rows, and follows offset while HubSpot says there are more pages.

**Call relations**: The product stream dispatcher selects this for email_events. It calls _email_event_start_timestamp to translate the cursor.

*Call graph*: calls 1 internal fn (_email_event_start_timestamp).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1554–1563)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts an email-event cursor into the millisecond timestamp expected by HubSpot's email events API. It accepts either an existing numeric timestamp or an ISO date string.

**Data flow**: It receives a cursor string or None. It returns None for missing or unparseable values, returns an integer unchanged from digit strings, or parses an ISO date and converts it to milliseconds.

**Call relations**: _paginate_email_events calls this before building each request's startTimestamp parameter.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._paginate_association_labels`  (lines 1565–1581)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the labels that describe relationships between HubSpot object types. Labels explain what kind of association exists, not just that two records are linked.

**Data flow**: It walks object-type pairs that have labels, converts each label into a normalized row with from/to object context, and yields pages.

**Call relations**: The product dispatcher selects this for association_labels. It uses _association_pairs_with_labels and _association_label_row.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels).


##### `HubSpotConnector._paginate_associations`  (lines 1583–1598)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual associations between HubSpot objects, such as a company linked to a contact. It discovers valid object-type pairs, then batch-reads relationships for source record IDs.

**Data flow**: It loops through pairs that have labels, pages through IDs of the source object type, sends those IDs to the batch association API, and yields normalized relationship rows.

**Call relations**: The product dispatcher selects this for associations. It uses _association_pairs_with_labels, _paginate_crm_object_id_pages, and _paginate_association_batch.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages).


##### `HubSpotConnector._paginate_association_batch`  (lines 1600–1627)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads associations for a batch of source records, including follow-up pages for records with many associations. This handles HubSpot's nested paging inside batch results.

**Data flow**: It receives source and target object types plus input IDs. It posts to the batch read endpoint, yields normalized association rows, then replaces pending inputs with any per-record next cursors until none remain.

**Call relations**: _paginate_associations calls this for each page of source IDs. It uses _association_rows to shape results and _next_association_inputs to continue deep pages.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1630–1644)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Finds which source records still have more association pages to read. HubSpot can return a next cursor separately for each source record in a batch.

**Data flow**: It receives a batch association response. For each result with a source ID and paging.next.after cursor, it returns a new input dictionary containing that ID and after value.

**Call relations**: _paginate_association_batch calls this after each batch response to know whether another batch request is needed.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1646–1659)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Discovers object-type pairs that actually have association labels. This avoids wasting work on pairs HubSpot does not support or the account cannot access.

**Data flow**: It asks for the list of object types, tries every from/to combination, fetches labels for each pair, and yields only pairs with at least one label.

**Call relations**: Both association label pagination and association record pagination call this as their discovery step.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1661–1674)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the object-type list used for association discovery. It starts with known HubSpot standard objects and adds any custom object types in the account.

**Data flow**: It begins with a fixed list of standard object names. It tries to fetch custom schemas, extracts custom object type IDs, appends new ones, and returns the full list.

**Call relations**: _association_pairs_with_labels calls this before trying object-type pairs. It uses _custom_object_schemas, _schema_object_type_id, and optional-error detection.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1676–1692)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches relationship labels for one source object type and one target object type. If the pair is not supported, it returns an empty list.

**Data flow**: It receives from/to object type names, calls the labels endpoint, and returns dictionary rows from results. Optional 400, 404, or permission failures become an empty result.

**Call relations**: _association_pairs_with_labels calls this for every candidate object-type pair.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1695–1712)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Normalizes one association label into a flat record with a stable ID. It preserves HubSpot's label fields while adding clear from/to context.

**Data flow**: It receives a label and the source and target object types. It derives type ID, category, display label, creates a compound id, and returns the enriched row.

**Call relations**: _paginate_association_labels calls this for each label found during association pair discovery.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1714–1726)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Yields pages of CRM object IDs only. This is useful when another API needs IDs as input rather than full records.

**Data flow**: It receives an object type. It pages through that object's list endpoint asking only for hs_object_id, extracts non-empty IDs, and yields them as strings.

**Call relations**: Association pagination uses it to supply source IDs, and sequence enrollment pagination uses it to walk contacts.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1728–1758)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple pages from a CRM object's list endpoint with selected properties. This is a lighter alternative to the search API when only IDs or a few fields are needed.

**Data flow**: It receives an object type and property tuple. It GETs list pages with limit, properties, and after cursor, yields dictionary rows, and stops on no next cursor or optional unavailability.

**Call relations**: _paginate_crm_object_id_pages and _paginate_contact_identity_pages call this for helper lookups.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1761–1795)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Converts a batch association API response into one flat row per relationship type. A single linked pair can have multiple association types, so it may emit multiple rows.

**Data flow**: It receives raw batch data and source/target object names. It walks each source result, each target record, and each association type, then creates normalized rows for valid pairs.

**Call relations**: _paginate_association_batch calls this after each batch API response.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1798–1825)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one normalized association record. It captures the two record IDs, the two object types, the association category, label, and a stable compound ID.

**Data flow**: It receives one association type plus source and target details. It derives type and category fields, builds a unique id, and returns a dictionary describing that relationship.

**Call relations**: _association_rows uses this as the row factory for each source-target association it finds.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1828–1831)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes errors that are acceptable when probing optional HubSpot combinations. Some object pairs, endpoints, or features simply do not exist for an account.

**Data flow**: It receives an HTTP error. It returns true for 400 or 404, or for permission-style unavailable stream errors recognized by _is_stream_unavailable.

**Call relations**: Many fan-out helpers call this so one missing optional endpoint does not stop the entire stream.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1833–1847)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds records showing which contacts or objects belong to each HubSpot list. It first discovers lists, then walks memberships for each list.

**Data flow**: It pages through lists, extracts each list ID, calls the membership paginator for that list, and yields the resulting membership pages.

**Call relations**: The product dispatcher selects this for list_memberships. It coordinates _paginate_lists and _paginate_memberships_for_list.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1849–1892)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads membership rows for one HubSpot list and enriches them with list context. This makes each member row understandable without looking up the list separately.

**Data flow**: It receives a list record and list ID. It pages through the list memberships endpoint, builds IDs from list and record IDs, adds list name and object type, yields pages, and skips optional failures.

**Call relations**: _paginate_list_memberships calls this for every list discovered by _paginate_lists.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1894–1907)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot communication subscription definitions. These define the kinds of email subscriptions or preferences a contact can have.

**Data flow**: It fetches the definitions endpoint, accepts either results or subscriptionDefinitions arrays, assigns each row an id from available fields or its position, and yields the rows.

**Call relations**: The product dispatcher selects this for subscription_definitions.


##### `HubSpotConnector._paginate_consent_states`  (lines 1909–1922)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds contact communication preference records by checking each contact's email address. It gathers both per-subscription statuses and unsubscribe-all statuses.

**Data flow**: It pages through contacts with emails, skips contacts without usable emails, requests consent status rows and unsubscribe-all rows for each email, combines them, and yields pages.

**Call relations**: The product dispatcher selects this for consent_states. It uses _paginate_contact_identity_pages, _consent_status_rows, and _unsubscribe_all_rows.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1924–1940)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads contact IDs and email addresses for consent lookups. It normalizes email whether HubSpot places it on the record or inside properties.

**Data flow**: It pages through contacts asking for the email property, extracts email from either top level or properties, and yields contact records with a consistent email field.

**Call relations**: _paginate_consent_states calls this as the starting point for communication preference checks.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 1942–1963)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches subscription-specific communication preference statuses for one email address. It returns rows ready to be shaped as consent records.

**Data flow**: It receives a contact and email, URL-escapes the email, calls the statuses endpoint for EMAIL channel, converts each result through _consent_row, and returns the list.

**Call relations**: _paginate_consent_states calls this for each contact email. It uses _is_optional_pair_unavailable to ignore missing preference data.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 1965–1989)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches unsubscribe-all communication preference statuses for one email address. This captures a broad opt-out that is separate from individual subscription types.

**Data flow**: It receives a contact and email, URL-escapes the email, calls the unsubscribe-all endpoint, converts results through _consent_row with the unsubscribe_all kind, and returns them.

**Call relations**: _paginate_consent_states calls this alongside _consent_status_rows for each contact email.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 1992–2023)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Normalizes one communication preference result into a stable consent record. It adds contact, email, purpose, status, legal basis, source, and timestamp fields.

**Data flow**: It receives a raw preference row, contact, email, and status kind. It builds a compound id from email, subscription/status, and business unit, derives friendly fields, and returns the enriched row.

**Call relations**: _consent_status_rows and _unsubscribe_all_rows both use this to produce the same output shape.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2025–2052)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sales sequences for each HubSpot user that can own them. Because sequences are requested per user, the connector first maps owners to user IDs.

**Data flow**: It gets user rows, then for each user calls the sequences endpoint with userId, adds owner context to each sequence, yields pages, and skips users or endpoints that are unavailable.

**Call relations**: The product dispatcher selects this for sequences. It uses _sequence_user_rows and _paginate_get_collection.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows).


##### `HubSpotConnector._sequence_user_rows`  (lines 2054–2077)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the list of HubSpot user IDs needed to query sequences. It derives users from owner records and avoids duplicates.

**Data flow**: It pages through owners, extracts userId, owner id, and owner email, keeps each userId only once, and yields one page of user rows.

**Call relations**: _paginate_sequences calls this before fetching user-specific sequence lists.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2079–2097)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sequence enrollment data for contacts. An enrollment says a contact is or was enrolled in a sales sequence.

**Data flow**: It pages through contact IDs, calls the enrollment endpoint for each contact, converts the response into rows, and yields pages; optional missing data for a contact is skipped.

**Call relations**: The product dispatcher selects this for sequence_enrollments. It uses _paginate_crm_object_id_pages and _sequence_enrollment_rows.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2100–2114)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes sequence enrollment responses into rows with stable IDs and contact context. It handles responses that are either a list under results or a single object.

**Data flow**: It receives raw enrollment data and contact ID. It chooses rows from results or the data itself, assigns an id from id or contact/sequence/index fallback, adds contact_id, and returns the list.

**Call relations**: _paginate_sequence_enrollments calls this after each contact-specific enrollment request.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2116–2145)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads submissions for every HubSpot form. HubSpot exposes submissions under each form, so this stream fans out from forms to submissions.

**Data flow**: It pages through forms, extracts each form ID, pages through that form's submissions with a smaller limit, builds a stable submission id, adds form ID and name, and yields pages.

**Call relations**: The product dispatcher selects this for form_submissions. It reuses _paginate_get_collection for both forms and submissions.

*Call graph*: calls 1 internal fn (_paginate_get_collection).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2147–2162)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages inside every conversation thread. This turns threaded conversation data into message rows that include their thread ID.

**Data flow**: It pages through conversation threads, extracts each thread ID, pages through the thread's messages endpoint, adds thread_id to each message, and yields pages.

**Call relations**: The product dispatcher selects this for conversation_messages. It reuses _paginate_get_collection for threads and messages.

*Call graph*: calls 1 internal fn (_paginate_get_collection).


##### `HubSpotConnector._paginate_pipelines`  (lines 2164–2171)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot pipelines for object types that support them, currently deals and tickets. Pipelines describe the high-level workflow stages for those records.

**Data flow**: It loops over supported pipeline object types, asks for normalized pipeline rows for each, and yields non-empty pages.

**Call relations**: The product dispatcher selects this for pipelines. It delegates per-object work to _pipeline_rows_for_object_type.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2173–2223)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads stages inside deal and ticket pipelines. Stages are the steps within a pipeline, such as open, closed, or archived states.

**Data flow**: It fetches raw pipelines for each supported object type, walks each pipeline's stages, derives status and closed flags from metadata, builds stable IDs, and yields stage rows.

**Call relations**: The product dispatcher selects this for pipeline_stages. It uses _raw_pipelines_for_object_type to get the source pipeline data.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2225–2248)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes pipelines for one object type. It stamps each pipeline with object kind and a stable ID that includes the object type.

**Data flow**: It receives an object type, fetches raw pipelines, skips rows without IDs, adds pipeline_id, object_kind, name, and active/archived status, and returns the list.

**Call relations**: _paginate_pipelines calls this for each supported object type. It relies on _raw_pipelines_for_object_type.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2250–2261)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches the raw HubSpot pipeline records for one object type. It treats forbidden or missing pipeline endpoints as empty because some accounts may not have them.

**Data flow**: It receives an object type, calls the CRM pipelines endpoint, returns dictionary rows from results, or returns an empty list for 403 and 404 responses.

**Call relations**: _pipeline_rows_for_object_type and _paginate_pipeline_stages both call this as their raw data source.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2264–2268)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects HubSpot's specific error for object types that cannot page through deleted records. This lets the connector skip only that cleanup step instead of failing the stream.

**Data flow**: It receives an HTTP error. It checks for status 400 and looks for HubSpot's “paging through deleted objects is not yet supported” message.

**Call relations**: Both standard and custom archived-ID sweeps call this when their archived lookup fails.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2271–2279)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Extracts the message field from a HubSpot JSON error response. It is a small helper for interpreting upstream API errors.

**Data flow**: It receives an HTTP error, tries to parse the response as JSON, and returns the message string if present; otherwise it returns None.

**Call relations**: _is_archived_sweep_unsupported uses this helper to inspect HubSpot's error wording.


##### `HubSpotConnector._paginate_junction`  (lines 2281–2328)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds simple relationship rows from HubSpot's inline associations on parent object list responses. These junction streams connect records such as deals to contacts or tickets to companies.

**Data flow**: It receives a parent object and target object. It lists parent records with associations requested, walks each embedded association, creates one row per parent-target pair, and follows the after cursor until done.

**Call relations**: _paginate_unchecked calls this for predefined junction streams. These streams have no incremental cursor because HubSpot does not expose modification times for the links.

*Call graph*: called by 1 (_paginate_unchecked).


### Social and support engagement
Connectors for social presence and customer messaging systems that capture audience interactions and support context.

### `extensions/sources/ufo_ext_sources/providers/instagram.py`

`io_transport` · `source sync run`

Instagram business data is reached through Facebook Pages, not by starting directly at Instagram. This file is the map for that journey. It first asks Facebook for the Pages the current credential can access. From each Page, it finds the linked Instagram business account. From each account, it can then read media, stories, and several kinds of insight data, which are analytics numbers such as reach, impressions, or replies.

The connector is read-only. It does not publish media or change Instagram; it only fetches data. It also does not store the access token itself. The wider source-running system supplies an authenticated HTTP client, like giving this connector a temporary key at run time.

A key job here is walking Facebook Graph API pagination. The API returns a list of records and, when there is more to fetch, a full “next page” URL. The connector follows those links until there are no more pages. For media, stories, and user insights, it supports incremental syncing by comparing timestamps with a saved cursor, or watermark, so old records are not read again.

The file also treats permission problems carefully. If a whole stream is refused because the token lacks access, the stream is marked as skipped rather than crashing the entire run. But if a single media or story object refuses insight data, that one object is skipped and the rest continue.

#### Function details

##### `InstagramConnector._paged`  (lines 94–111)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Follows a Facebook Graph API collection across all of its pages. Someone uses this when an endpoint may return too many records for one response and must be read page by page.

**Data flow**: It starts with an API path and optional query parameters. It asks the API for that page, reads the response body, pulls out the list under the usual `data` field, and yields that list if it is not empty. If the response contains a `paging.next` link, it uses that as the next request and repeats until there is no next link.

**Call relations**: This is the low-level page-turner for the connector. `_pages` uses it to walk `/me/accounts`, and `_account_collection` uses it to walk each Instagram account’s media or stories. It relies on `records_at` to safely find the API’s `data` list.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 113–118)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches all Facebook Pages available to the current credential, including any linked Instagram business account information. This is the starting point because Instagram business access comes through Pages.

**Data flow**: It builds a request asking for Page IDs, names, and linked Instagram account details. It sends that request through `_paged`, gathers every returned page record into one list, and returns that full list.

**Call relations**: This function sits near the root of the connector’s data tree. `_instagram_accounts` calls it to discover Instagram accounts, and `_root_pages` calls it when the requested stream is the Pages stream.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, _root_pages).


##### `InstagramConnector._instagram_accounts`  (lines 120–130)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Extracts the Instagram business accounts linked to the accessible Facebook Pages. It also remembers which Page each Instagram account came from, so later records can be tied back to their Page context.

**Data flow**: It asks `_pages` for all Facebook Pages. For each Page, it looks for an `instagram_business_account` object with an ID. It copies that account data, adds the Page ID and Page name, removes duplicates by account ID, and returns the unique accounts as a list.

**Call relations**: This is the bridge from Facebook Pages to Instagram account data. `_account_collection` uses it before reading account media or stories, `_user_insights` uses it before reading account-level analytics, and `_root_pages` uses it when the Instagram accounts stream is requested.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _root_pages, _user_insights).


##### `InstagramConnector._account_collection`  (lines 132–153)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a collection that belongs to each Instagram business account, such as media posts or stories. It can also filter out older records using a saved cursor timestamp.

**Data flow**: It receives a collection name, a field list, and optional cursor information. It first gets all Instagram accounts, then requests the chosen collection for each account through `_paged`. If a cursor is present, it keeps only records whose cursor field is newer. Before yielding records, it adds the Instagram account ID to each one so downstream code knows where the record came from.

**Call relations**: `_stream_pages` calls this when the run asks for the media or stories stream. It depends on `_instagram_accounts` to know which accounts to visit, `_paged` to follow API pagination, and `with_context` to attach account context to each returned record.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (_stream_pages); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 155–189)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches analytics for individual objects, such as each media item or story. It turns per-object insight responses into records with stable IDs and a link back to the original object.

**Data flow**: It receives an async stream of object pages and a list of insight metrics to request. For each object with a valid ID, it calls that object’s `/insights` endpoint. If the API says that one object cannot provide insights because of common permission or availability errors, it skips that object. For each insight returned, it adds a generated ID, the parent object ID, and the stream name, then yields batches of insight records.

**Call relations**: `_insight_pages` calls this after arranging a source stream of media or stories. It uses `records_at` to read the `data` list from each insight response. Its forgiving behavior keeps one bad object from stopping the whole insight stream.

*Call graph*: called by 1 (_insight_pages); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 191–221)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads account-level Instagram analytics, such as impressions, reach, and profile views, broken down by day. It supports incremental syncing by ignoring insight values that are not newer than the saved cursor.

**Data flow**: It starts by getting all Instagram business accounts. For each account, it asks the API for daily insight metrics. Each metric contains a list of dated values, so the function walks those values, checks each `end_time` against the cursor, and turns each kept value into a record with a unique ID, metric name, and account ID. It yields the records in batches when there is anything new.

**Call relations**: `_stream_pages` calls this when the requested stream is user insights. It depends on `_instagram_accounts` to find accounts, `records_at` to read insight groups, and `list_or_empty` to safely treat missing or malformed value lists as empty.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (_stream_pages); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 223–239)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the public stream-reading entry point expected by the source framework. It yields pages of records for a requested stream and turns broad permission refusals into a clean “stream skipped” result.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It asks `_stream_pages` for the matching record pages and yields them onward. If the API returns an authentication or permission refusal, it raises `StreamSkipped` with a clear message instead of letting the run treat it as a hard failure.

**Call relations**: The source framework calls this to read streams, and `_insight_pages` also calls it internally to reuse the media or stories stream as the input for insight fetching. It delegates stream-specific choices to `_stream_pages` and wraps that work with permission-error handling.

*Call graph*: calls 2 internal fn (__init__, _stream_pages); called by 1 (_insight_pages).


##### `InstagramConnector._stream_pages`  (lines 241–275)

```
def _stream_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right reader for a stream name. It is the connector’s dispatcher: given “media”, “stories”, “user_insights”, and so on, it routes the request to the helper that knows how to fetch that kind of data.

**Data flow**: It receives the stream name and cursor. For root streams, it returns `_root_pages`; for media and stories, it returns `_account_collection` with the correct API path and field list; for media and story insights, it returns `_insight_pages`; for user insights, it returns `_user_insights`. If the stream name is unknown, it raises `StreamSkipped` to say this connector does not implement it.

**Call relations**: `paginate` calls this during normal stream reads. It ties together all the specialized helpers: `_root_pages`, `_account_collection`, `_insight_pages`, and `_user_insights`. It is the decision point that keeps the rest of the connector organized.

*Call graph*: calls 5 internal fn (__init__, _account_collection, _insight_pages, _root_pages, _user_insights); called by 1 (paginate).


##### `InstagramConnector._root_pages`  (lines 277–284)

```
async def _root_pages(self, client: httpx.AsyncClient, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Returns the top-level records that do not need to be read account-by-account: Facebook Pages or linked Instagram accounts. It wraps those lists in the same page-yielding shape used by the other stream readers.

**Data flow**: It receives a stream name. If the name is `pages`, it fetches Pages through `_pages`; otherwise it fetches Instagram accounts through `_instagram_accounts`. If the resulting list has records, it yields that list as one batch.

**Call relations**: `_stream_pages` calls this for the Pages and Instagram accounts streams. It reuses `_pages` and `_instagram_accounts`, then presents their results in the async page format expected by `paginate`.

*Call graph*: calls 2 internal fn (_instagram_accounts, _pages); called by 1 (_stream_pages).


##### `InstagramConnector._insight_pages`  (lines 286–295)

```
def _insight_pages(self, client: httpx.AsyncClient, source: str, metrics: str, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds an insight stream from another stream, such as media or stories. It first reads the source objects, then asks for analytics on each object.

**Data flow**: It receives the source stream name, the metrics to request, and the output insight stream name. It finds the matching stream definition, starts reading that source stream through `paginate` with no cursor, and passes those source objects into `_object_insights`. The result is an async stream of insight record batches.

**Call relations**: `_stream_pages` calls this for media insights and story insights. It calls `paginate` to reuse the existing media or stories reader, then hands those objects to `_object_insights` so each object’s analytics can be fetched.

*Call graph*: calls 2 internal fn (_object_insights, paginate); called by 1 (_stream_pages).


### `extensions/sources/ufo_ext_sources/providers/intercom.py`

`io_transport` · `source sync / request handling`

Intercom is a customer-support platform, and its API does not expose every kind of data in the same way. Some records are found through search requests, some through a scrolling company endpoint, some through simple list endpoints, and some only by first reading a parent record and then asking for its children. This file hides those differences behind one connector, so the rest of the project can ask for “the contacts stream” or “the conversation parts stream” without knowing Intercom’s rules.

The file first defines the Intercom streams the system knows about. A stream is a named kind of data, with details such as its main ID field and, when possible, a cursor field used for incremental syncing. A cursor is like a bookmark: it lets the next run continue from records updated after the previous run.

The `IntercomConnector` then adds Intercom-specific behavior. It creates an authenticated HTTP client, adds the required Intercom API version header, chooses the right paging method for each stream, and translates permission failures into a clean “stream skipped” result instead of crashing the whole run. It also flattens a few nested Intercom fields, such as conversation source details or contact company IDs, so later SQL-style processing can read them more easily. This connector is read-only; it fetches data from Intercom but does not write anything back.

#### Function details

##### `_stream`  (lines 51–65)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a small description object for one Intercom stream. This saves repetition when listing all the kinds of Intercom data the connector can read.

**Data flow**: It receives a stream name and optional details such as the Intercom object name, primary key, cursor field, and whether the stream is canonical. It packages those choices into a `StreamSpec`, which is the system’s standard description of a readable data stream.

**Call relations**: This helper is used while the module is loaded to build the `INTERCOM_STREAMS` list. That list is then attached to `IntercomConnector`, so the rest of the source framework knows which Intercom streams are available.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector.record_identity`  (lines 101–105)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Chooses the stable identity value for a record. Attribute records are a special case because Intercom may identify them by `id` or by `full_name`, so this method gives them a usable identity.

**Data flow**: It receives one record and the stream it came from. For normal streams, it lets the base connector use the usual identity rule; for company and contact attributes, it reads `id` first, then `full_name`, and returns it as text if it is usable.

**Call relations**: The broader source framework calls this when it needs to name or deduplicate records. This method only steps in for Intercom attribute streams and otherwise hands the decision back to the shared REST connector behavior.


##### `IntercomConnector.record_ref`  (lines 107–111)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Chooses a human-friendly reference label for certain Intercom records. For tags, teams, and attributes, the record name is more useful to a person than an internal ID.

**Data flow**: It receives a record and its stream. For tags, teams, and attribute streams, it reads the `name` field and returns it as text when possible; for all other streams, it uses the base connector’s normal reference behavior.

**Call relations**: The source framework uses this when it wants a readable label for a record. This method customizes that label only for streams where Intercom’s `name` field is the clearest reference.


##### `IntercomConnector._make_client`  (lines 113–116)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Intercom and adds the Intercom API version header. Without that header, Intercom may interpret requests using a different API version than this connector expects.

**Data flow**: It receives a base URL and credential. It asks the parent REST connector to create the authenticated HTTP client, then adds `Intercom-Version: 2.11` to every request made by that client, and returns the prepared client.

**Call relations**: This runs during connector setup, before any stream pages are requested. It relies on the shared REST connector for authentication setup, then adds the Intercom-specific requirement.


##### `IntercomConnector._build_search_body`  (lines 119–149)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the request body for Intercom search endpoints. It sets page size, sorting, and the “only records after this cursor” filter used for incremental syncing.

**Data flow**: It receives the stream, the saved cursor bookmark, and an optional `starting_after` token for the next page. It creates a JSON body with pagination settings, ascending sort by the cursor field, and a query that asks Intercom for records newer than the cursor. The result is a dictionary ready to send in a POST request.

**Call relations**: The search paginator uses this for conversations, contacts, and tickets. The conversation-parts paginator also uses it first to find conversations, then fetches each conversation’s parts separately.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 152–157)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: Safely picks the first object from a list-like nested Intercom field. It exists because some useful IDs are buried inside arrays inside envelope objects.

**Data flow**: It receives any value. If the value is a non-empty list and its first item is a dictionary, it returns that dictionary; otherwise it returns nothing.

**Call relations**: The flattening helpers use this when pulling the first contact from a conversation or the first company from a contact. It keeps those helpers from repeating the same safety checks.


##### `IntercomConnector._flatten_conversation`  (lines 160–177)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes selected nested conversation fields easier to read later. It lifts source details and the first requester contact ID into simple top-level keys.

**Data flow**: It receives one conversation record. It copies the record, reads nested `source` fields such as type, subject, and body, and adds them as flat keys like `source__subject`. It also looks for the first contact inside the conversation’s contacts envelope and adds that contact’s ID as `requester_id`.

**Call relations**: The general `flatten` method calls this only for the conversations stream. Its output is passed onward as the cleaned-up version of the record for later storage or transformation.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 180–189)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes the author of a conversation part easy to query. It turns nested author information into simple `author_type` and `author_id` fields.

**Data flow**: It receives one conversation-part record. It copies the record, reads the nested `author` object if present, and adds the author’s type and ID as top-level fields. It leaves any existing `conversation_id` in place.

**Call relations**: The general `flatten` method calls this for conversation parts after the paginator has already stamped each part with its parent conversation ID. The flattened record then continues through the normal sync pipeline.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 192–200)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a contact’s first associated company ID as a simple field. This helps later processing treat that company as the contact’s organization.

**Data flow**: It receives one contact record. It copies the record, looks inside the nested companies envelope, takes the first company if present, and writes its `id` or `company_id` into a top-level `org_id` field.

**Call relations**: The general `flatten` method calls this only for contacts. It uses `_first` to safely read the nested company list.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 202–210)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Applies Intercom-specific cleanup to records before the rest of the system sees them. It only changes streams where Intercom nests important values too deeply for convenient downstream use.

**Data flow**: It receives a raw record and its stream description. If the stream is conversations, conversation parts, or contacts, it sends the record to the matching flattening helper; otherwise it returns the record unchanged.

**Call relations**: This sits between fetching records from Intercom and handing them to the rest of the sync system. It routes each supported stream to `_flatten_conversation`, `_flatten_conversation_part`, or `_flatten_contact` as needed.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 212–228)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Produces pages of records for one stream and turns Intercom permission refusals into a clean skipped-stream signal. This prevents one missing API scope from stopping the whole sync.

**Data flow**: It receives an HTTP client, a stream, and the current cursor bookmark. It asks `_stream_pages` for pages and yields each page onward. If Intercom responds with HTTP 401 or 403, meaning unauthorized or forbidden, it raises `StreamSkipped` with an explanation; other HTTP errors are re-raised.

**Call relations**: The source framework calls this when it is time to read a stream. This method wraps the actual stream-specific pagination chosen by `_stream_pages` and adds the connector’s error policy around it.

*Call graph*: calls 2 internal fn (__init__, _stream_pages).


##### `IntercomConnector._stream_pages`  (lines 230–248)

```
def _stream_pages(self, client: httpx.AsyncClient, stream: StreamSpec, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct Intercom paging strategy for a stream. This is the connector’s traffic director, because Intercom uses different API shapes for different data types.

**Data flow**: It receives the client, stream, and cursor. It checks the stream name and returns the matching asynchronous page generator: search, scroll, list, attributes, conversation parts, company segments, or activity logs. If no strategy exists, it raises an error.

**Call relations**: `paginate` calls this first for every stream. It then hands the work to one of the specialized paginator methods, each of which knows one Intercom endpoint style.

*Call graph*: calls 7 internal fn (_paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search); called by 1 (paginate).


##### `IntercomConnector._paginate_search`  (lines 250–270)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads streams that use Intercom’s search API, such as conversations, contacts, and tickets. It keeps asking for the next page until Intercom stops providing a next-page token.

**Data flow**: It receives the client, stream, and cursor. For each loop, it builds a search body, sends a POST request to the stream’s search endpoint, extracts the records from the correct response key, yields them if present, then reads `starting_after` from the response to continue paging.

**Call relations**: `_stream_pages` uses this for streams listed as search streams. It relies on `_build_search_body` to format each request correctly.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (_stream_pages).


##### `IntercomConnector._paginate_scroll`  (lines 272–286)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads companies through Intercom’s scroll API. A scroll API is like following a temporary bookmark that Intercom gives back after each batch.

**Data flow**: It starts with no scroll token, calls `/companies/scroll`, yields the returned company records, then repeats using the returned `scroll_param`. It stops when no records or no next scroll token are available.

**Call relations**: `_stream_pages` uses this for the companies stream. Other code does not need to understand the scroll token; this method hides that detail.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_list`  (lines 288–300)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple one-response Intercom list streams, such as admins, tags, teams, and segments. These streams do not need cursor-based paging here.

**Data flow**: It receives the client and stream, calls the stream’s list endpoint, then looks for records under either the stream name or a generic `data` key. If it finds a non-empty list, it yields that list once.

**Call relations**: `_stream_pages` sends simple list streams here. This method is the shortest path because there is no next-page loop to follow.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_attributes`  (lines 302–311)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Intercom data attributes for either companies or contacts. These describe custom fields configured in Intercom.

**Data flow**: It receives the client and stream, translates the stream name into the Intercom model name, calls `/data_attributes` with that model as a parameter, filters the response to dictionary records, and yields them if any exist.

**Call relations**: `_stream_pages` uses this for company and contact attribute streams. It shares one endpoint but changes the `model` parameter depending on which kind of attributes are requested.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_conversation_parts`  (lines 313–345)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the individual messages or events inside conversations. Intercom does not return all of these directly in the search result, so this method first finds conversations and then fetches each conversation’s detail.

**Data flow**: It receives the client and cursor. It searches conversations page by page using `_build_search_body`; for each conversation ID found, it calls the conversation detail endpoint, extracts its `conversation_parts`, stamps each part with the parent `conversation_id`, and yields the parts. It continues until the conversation search has no next-page token.

**Call relations**: `_stream_pages` uses this for the conversation_parts stream. It combines the search paging logic with extra detail requests so downstream code receives conversation parts as their own stream.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (_stream_pages).


##### `IntercomConnector._paginate_company_segments`  (lines 347–371)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the segments attached to each company. Since segments are reached through each company, this method first scrolls through companies and then asks for every company’s segments.

**Data flow**: It calls the company scroll endpoint, loops over returned companies, and for each company with an ID calls `/companies/{id}/segments`. It stamps each segment with `company_id`, yields segment batches when present, then follows the next `scroll_param` until the company scroll ends.

**Call relations**: `_stream_pages` uses this for the company_segments stream. It fans out from parent company records into child segment records, much like checking every folder for the papers inside.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_activity_logs`  (lines 373–395)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads admin activity logs, optionally starting after a saved creation-time cursor. It follows Intercom’s next-page links until there are no more logs.

**Data flow**: It receives the client and cursor. If a cursor exists, it sends it as `created_at_after` on the first request to `/admins/activity_logs`. It yields any `activity_logs` found, then follows the response’s next-page link, stripping the base URL if Intercom returns a full URL.

**Call relations**: `_stream_pages` uses this for the activity_logs stream. This method owns the activity-log-specific paging style so the main paginator can treat it like any other stream.

*Call graph*: called by 1 (_stream_pages).


### Email marketing automation
Connectors for marketing automation and email platforms that sync audiences, profiles, campaigns, events, and reports.

### `extensions/sources/ufo_ext_sources/providers/klaviyo.py`

`io_transport` · `source sync`

Klaviyo exposes its data through a web API, but the raw shape is not very convenient for this project. Records arrive wrapped in layers like attributes, relationships, and links. This connector is the adapter that knows Klaviyo’s rules: how to authenticate, which resources can be read, how to ask for only new or changed records, how to follow pages of results, and how to flatten each record into a simpler form.

The file first defines the list of Klaviyo streams, such as profiles, lists, campaigns, events, catalog items, and webhooks. A stream is one kind of object the sync can read. Some streams are incremental, meaning the connector can resume from a saved “watermark” instead of reading everything every time.

The main class, KlaviyoConnector, builds an HTTP client with Klaviyo’s required private-key authorization header and pinned API revision. When reading, it starts at the right API path, adds page size and cursor filters, then follows Klaviyo’s next-page links until there are no more. Think of it like reading a long document by following “next page” buttons.

Before records leave the connector, flatten turns nested Klaviyo fields into direct fields. It also extracts useful details, such as profile email consent, campaign subject lines, event metric names, and related profile or list IDs. If Klaviyo refuses access to a stream because the key lacks permission, the connector marks that stream as skipped rather than crashing the whole sync.

#### Function details

##### `_stream`  (lines 37–55)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', created_at_field: str='created', updated_at_field: str | None='updated', canonical:
```

**Purpose**: This helper creates a StreamSpec, which is the system’s description of one Klaviyo resource to sync. It keeps the long stream list readable by filling in common defaults like the primary key and timestamp fields.

**Data flow**: It receives a friendly stream name and optional details such as the Klaviyo API object name, cursor field, and timestamp fields. It combines those choices with defaults, then returns a StreamSpec object that the connector later uses to know where and how to read that stream.

**Call relations**: This is used while the module is loaded to build KLAVIYO_STREAMS. It hands its choices into StreamSpec.__init__, so the rest of the connector can treat each Klaviyo resource in a consistent way.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 115–123)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the web client used to talk to Klaviyo and adds the Klaviyo-specific headers that every request needs. In plain terms, it prepares the “letterhead” for all API calls so Klaviyo recognizes the request and the API version.

**Data flow**: It receives a base URL and a credential. It asks the parent RestConnector to create the basic HTTP client, then adds Klaviyo’s revision header. If the credential contains a private key, it also adds the Authorization header in Klaviyo’s required format. It returns the ready-to-use HTTP client.

**Call relations**: This fits into the connector startup path, when the base source framework needs a client for requests. It builds on the parent connector’s client creation and then layers on the Klaviyo-specific authentication and versioning rules.


##### `KlaviyoConnector._next_path`  (lines 126–137)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This converts Klaviyo’s full next-page URL into just the path and query that this connector’s HTTP client should request next. It is needed because Klaviyo gives an absolute link, while the client is already bound to the base Klaviyo host.

**Data flow**: It receives a possible next-page link. If the link is empty or has no path, it returns nothing. Otherwise, it parses the URL, keeps the path, adds the query string if present, and returns that shorter request path.

**Call relations**: paginate calls this after each page of API results. It turns the links.next value from Klaviyo into the next path that paginate can request in its loop.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 140–145)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: This chooses the Klaviyo timestamp field that should be used to resume an incremental sync for a stream. Different Klaviyo resources use different field names for “when this changed,” so this keeps that rule in one place.

**Data flow**: It receives a stream description. If the stream is events, it returns datetime. If the stream is one of the resources that use updated_at, it returns updated_at. Otherwise, it returns updated.

**Call relations**: This supports building the first request for an incremental stream. _initial_query uses this choice when it creates filter and sort parameters for Klaviyo.


##### `KlaviyoConnector._initial_query`  (lines 148–162)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the query parameters for the first API request for a stream. It decides page size, ordering, optional cursor filtering, and a few stream-specific extras like asking profiles for subscription details.

**Data flow**: It receives a stream and the saved cursor value, if there is one. It starts with the standard page size. If the stream supports a cursor, it adds a sort field, and if a cursor value exists it adds a Klaviyo filter meaning “only records at or after this timestamp.” For profiles it asks for subscription fields; for events it asks Klaviyo to include metric information. It returns the parameter dictionary for the first request.

**Call relations**: paginate calls this once before the first page request. After that, pagination follows Klaviyo’s own next links, so this query is not reused on later pages.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 165–176)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: This safely pulls the ID of a related Klaviyo object out of a nested relationships block. It prevents missing or oddly shaped relationship data from causing errors.

**Data flow**: It receives a relationships value and the relationship name to look for. It checks each nested level carefully: the relationship, its data object, and the id. If an ID is found, it returns it as text; otherwise it returns nothing.

**Call relations**: flatten uses this when it wants useful related IDs, such as an event’s profile ID, an event’s metric ID, or a segment’s parent list ID. It acts like a careful helper that reaches into Klaviyo’s nested record shape without assuming every field exists.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 178–208)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This turns a raw Klaviyo record into the simpler flat record shape used by the rest of the system. It makes important fields easy to search, store, and use for watermarks.

**Data flow**: It receives one Klaviyo record and its stream description. It starts a new dictionary with the record ID and resource type, then copies fields from attributes up to the top level. For lists and segments, it removes profile_count so changing membership counts do not make the record look changed. Depending on the stream, it adds profile consent details, campaign message details, event relationship IDs, event message references, or a segment’s parent list ID. It returns the flattened record.

**Call relations**: This is the main record-shaping step after pages are fetched. It calls _flatten_profile, _flatten_campaign, and _lift_relationship_id as needed, so each special Klaviyo record type gets the extra fields that make it useful later.

*Call graph*: calls 3 internal fn (_flatten_campaign, _flatten_profile, _lift_relationship_id).


##### `KlaviyoConnector._flatten_profile`  (lines 211–225)

```
def _flatten_profile(flat: dict[str, Any], attrs: Any) -> None
```

**Purpose**: This extracts email marketing consent and suppression information from a Klaviyo profile. Those details are buried inside the profile’s subscription data, but they are important for understanding whether a person can receive marketing email.

**Data flow**: It receives the flat output dictionary being built and the original attributes block. It looks for subscriptions, then email, then marketing. If it finds consent, it writes email_consent. If it finds suppression information, it writes email_suppression using the reason, code, or text available. It changes the flat dictionary in place and returns nothing.

**Call relations**: flatten calls this only for profile records. It is a focused helper so the main flatten function does not have to contain all the nested profile-specific lookup steps.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector._flatten_campaign`  (lines 228–246)

```
def _flatten_campaign(flat: dict[str, Any], attrs: Any) -> None
```

**Purpose**: This extracts human-useful campaign details, especially the subject line and sender information. These fields can be nested under audience message settings or appear directly on the campaign, so this function checks both places.

**Data flow**: It receives the flat output dictionary being built and the original attributes block. It looks inside audiences.message_settings for subject, sender label, and sender email, and inside audiences.included for the primary list ID. It then fills in fallback subject and sender values from top-level campaign attributes if they were not already set. It updates the flat dictionary in place and returns nothing.

**Call relations**: flatten calls this only for campaign records. It concentrates the campaign-specific cleanup so campaign records leave the connector with obvious fields like subject_line, from_label, from_email, and primary_list_id.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.paginate`  (lines 248–301)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Klaviyo stream page by page and yields batches of raw records. It also enriches event records with metric names when Klaviyo includes those related metric objects.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It starts at the stream’s API path and builds the first query with _initial_query. For each page, it requests data, extracts the records, optionally reads included metric objects for events and writes metric_name into event attributes, then yields the page’s records if any exist. It reads the links.next value, converts it with _next_path, and repeats until there is no next page. If Klaviyo returns 401 or 403, it raises StreamSkipped so the stream is recorded as unavailable rather than failing everything.

**Call relations**: This is the connector’s main read loop for Klaviyo streams. It calls _initial_query to prepare the first request, uses the inherited _get method to fetch each page, calls _next_path to keep following Klaviyo pagination, and raises StreamSkipped when Klaviyo says the credential lacks permission for that stream.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/providers/mailchimp.py`

`io_transport` · `source sync`

Mailchimp stores useful marketing data in several layers. Some things, like lists and campaigns, can be fetched directly. Other things live underneath a parent object: members belong to a list, interests belong to a list’s interest category, and email activity belongs to a campaign report. This file is the map and tour guide for walking through all of those paths safely.

At the top, it defines the Mailchimp streams the system knows about and the fields used to identify records and resume later syncs. A stream is a named feed of records, like “list_members” or “email_activity.” Many streams also have a cursor field, which is a timestamp used like a bookmark so later runs can ask Mailchimp only for newer or changed data.

The main class, MailchimpConnector, plugs into the project’s generic REST connector. Its paginate method acts like a dispatcher: it looks at the stream name and chooses the right route through Mailchimp’s API. Simple streams use direct offset paging. Nested streams first fetch parent IDs, then fetch children under each parent. For email activity, Mailchimp returns one recipient with a list of actions, so this file splits those actions into separate rows and creates stable IDs for them.

If Mailchimp rejects access with an authorization error, the stream is skipped with a clear message instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 63–81)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str='created_at', updated_at_field: str | None='updated_at', canonical
```

**Purpose**: Creates a StreamSpec, which is the system’s description of one Mailchimp data feed. It keeps the stream definitions shorter and consistent.

**Data flow**: It takes a stream name plus optional details such as the Mailchimp source object, primary key, cursor field, and timestamp fields. It fills in sensible defaults where details are missing, then returns a StreamSpec object that the connector later uses during syncing.

**Call relations**: This helper is used while the module is loaded to build the MAILCHIMP_STREAMS list. It hands its settings to StreamSpec.__init__, which creates the standard stream description understood by the source framework.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector.record_identity`  (lines 150–157)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Builds the unique identity for a Mailchimp record. It has special logic for unsubscribes because Mailchimp’s unsubscribe IDs are only unique when paired with the campaign they came from.

**Data flow**: It receives one record and the stream it belongs to. For most streams, it lets the base REST connector decide the identity. For unsubscribes, it reads campaign_id and email_id from the record and combines them into one stable key; if either part is missing, it returns no identity.

**Call relations**: The wider sync framework calls this when it needs to know whether two records are the same record across runs. This method only steps in for the unsubscribe stream; all other streams continue through the inherited connector behavior.


##### `MailchimpConnector.flatten`  (lines 159–165)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records before they are saved or passed onward. For member records, it creates a standard created_at value from Mailchimp’s signup or opt-in timestamps.

**Data flow**: It receives a raw record and its stream. If the stream is list_members or segment_members, it copies the record and adds created_at using timestamp_signup first, or timestamp_opt if signup time is absent. Other records pass through unchanged.

**Call relations**: The base sync process calls this as part of preparing each record. It helps member streams look more like the other streams that already have standard creation-time fields.


##### `MailchimpConnector._data_field`  (lines 168–169)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: Finds the JSON field where Mailchimp places the actual list of records for a stream. This matters because Mailchimp wraps records under names like lists, members, or emails instead of returning a bare array.

**Data flow**: It receives a stream description, looks up the stream name in the local data-field map, and returns the wrapper field name. If the stream is not in the map, it falls back to the stream name itself.

**Call relations**: The pagination helpers call this before requesting pages, so they know where to look inside each Mailchimp response. It supports top-level, per-list, and per-report paging paths.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 172–179)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Turns the system’s saved sync bookmark into the query parameter name Mailchimp expects. This lets a sync ask Mailchimp for records changed after a known time when the API supports it.

**Data flow**: It receives a stream and an optional cursor value. If either the cursor or the stream’s cursor field is missing, it returns an empty parameter set. If Mailchimp supports filtering on that cursor field, it returns a one-item dictionary such as since_last_changed mapped to the cursor timestamp.

**Call relations**: Several pagination paths call this before fetching data. It feeds incremental-sync parameters into top-level, per-list, segment-member, and per-report requests.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 181–237)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct fetching strategy for each Mailchimp stream and yields pages of records. It is the main routing point between the generic sync system and Mailchimp’s specific API shape.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. It checks the stream name, calls the matching pagination helper, and yields each page that helper produces. If Mailchimp returns a permission or authentication refusal, it converts that into a StreamSkipped error with a human-readable explanation.

**Call relations**: The source framework calls paginate when it wants records for a stream. This method then hands off to specialized helpers for top-level resources, per-list children, interests, segment members, report children, or email activity.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 239–250)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches simple Mailchimp resources that are available directly, such as lists, campaigns, automations, and reports. These can be read page by page without first finding a parent object.

**Data flow**: It receives an HTTP client, stream, API path, and optional cursor. It chooses the right response field, adds any cursor query parameter, asks the base REST helper for offset-based pages, and yields each page of records.

**Call relations**: paginate calls this for top-level streams. This helper uses _data_field to locate records inside Mailchimp’s JSON and _cursor_params to add incremental filtering when possible.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 252–269)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches a nested Mailchimp collection using the same offset paging pattern. It is a reusable helper for child endpoints such as members under a list or email activity under a report.

**Data flow**: It receives an HTTP client, a path, the JSON field containing records, and optional base query parameters. It repeatedly asks the base REST connector for pages using Mailchimp’s count and offset style, then yields each page unchanged.

**Call relations**: The deeper pagination methods call this after they have built the correct child endpoint path. It keeps the repeated paging mechanics in one place so per-list, per-report, interests, segment members, and email activity do not each duplicate the same loop.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 271–279)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: Reads a collection and yields just the IDs from its records. It is used when later requests need parent IDs before they can fetch child records.

**Data flow**: It receives an HTTP client, an API path, and the response field containing records. It pages through that collection, checks each row for an id, converts valid IDs to text, and yields them one by one.

**Call relations**: _list_ids and _report_ids call this to get the parent IDs they need. Those parent IDs then drive the fan-out requests for list-based and report-based streams.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 281–283)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Provides all Mailchimp audience/list IDs. Child streams use these IDs to fetch data that belongs to each list.

**Data flow**: It receives an HTTP client and calls _ids on the Mailchimp lists endpoint. As _ids finds list IDs, this function yields them onward unchanged.

**Call relations**: Per-list pagination, interests pagination, and segment-member pagination call this first. It supplies the list_id values they insert into nested endpoint URLs.

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 285–287)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Provides all Mailchimp report IDs. Report-based streams need these IDs before they can fetch unsubscribes or email activity for each campaign report.

**Data flow**: It receives an HTTP client and calls _ids on the Mailchimp reports endpoint. Each valid report ID found by _ids is yielded onward as text.

**Call relations**: Per-report pagination and email-activity pagination call this before fetching report children. It supplies the report or campaign identifier used in those nested URLs.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 289–310)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches records that live directly under each Mailchimp list, such as list members, segments, tags, and interest categories. It also adds the parent list_id to each child row when requested, so the child record can be traced back to its audience.

**Data flow**: It receives an HTTP client, stream, child path name, optional cursor, and an optional parent-field name. It gets the correct data field and cursor parameters, loops through every list ID, builds the child endpoint for that list, fetches pages from it, stamps each row with the list ID if needed, and yields the pages.

**Call relations**: paginate calls this for several list-based streams. It depends on _list_ids to find parents, _paginate_child to read each nested collection, _data_field to locate records in the response, _cursor_params for incremental filtering, and URL quoting to safely place list IDs inside paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 312–334)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches interests, which are nested two levels deep: first under a list, then under an interest category. This is needed because Mailchimp does not expose all interests as one flat top-level collection.

**Data flow**: It receives an HTTP client, stream, and cursor value, though the cursor is not used in this path. It loops through list IDs, fetches each list’s interest categories, then fetches interests under each category. For each interest row, it adds list_id and category_id if they are not already present, then yields the page.

**Call relations**: paginate calls this for the interests stream. The method uses _list_ids to start from every list, _paginate_child for both category and interest pages, and URL quoting when building safe nested Mailchimp paths.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 336–358)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches members inside each segment of each list. This walks a two-step parent chain so every returned member can be tied back to both a list and a segment.

**Data flow**: It receives an HTTP client, stream, and optional cursor. It builds cursor parameters if possible, loops through list IDs, fetches segments for each list, then fetches members for each segment. It adds list_id and segment_id to each member row and yields the member pages.

**Call relations**: paginate calls this for the segment_members stream. It uses _list_ids to find lists, _paginate_child to fetch segments and members, _cursor_params to request only newer member changes when possible, and URL quoting for IDs placed in endpoint paths.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 360–380)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches records that live under campaign reports, such as unsubscribes. It also records which campaign report each child row came from.

**Data flow**: It receives an HTTP client, stream, child path, optional cursor, and optional parent-field name. It finds the response data field and cursor parameters, loops through report IDs, fetches the requested child collection under each report, stamps rows with the parent campaign_id when requested, and yields the pages.

**Call relations**: paginate calls this for report-child streams like unsubscribes. It uses _report_ids to find parent reports, _paginate_child to read each report’s nested collection, _data_field to locate records in responses, _cursor_params for incremental filtering, and URL quoting for safe paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 382–414)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches email activity from each campaign report and reshapes it into one row per action. Mailchimp groups many actions under one recipient, but the sync system needs separate stable records for each action.

**Data flow**: It receives an HTTP client and optional cursor. If a cursor exists, it sends it as Mailchimp’s since parameter. It loops through report IDs, fetches email-activity pages, separates each recipient’s activity list into individual action rows, copies recipient-level fields onto each action, adds campaign_id, creates a stable synthetic id when Mailchimp did not provide one, and yields only non-empty exploded pages.

**Call relations**: paginate calls this for the email_activity stream. It uses _report_ids to visit each report and _paginate_child to fetch each report’s email activity, then performs the extra reshaping step before handing records back to the sync framework.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


### Sales and form capture
Connectors for downstream CRM records and form-response data used to capture customer and lead information.

### `extensions/sources/ufo_ext_sources/providers/salesforce.py`

`io_transport` · `source sync`

Salesforce stores each customer organization on its own web host and exposes data through a REST API, which is a web interface that programs can call. This file defines a read-only connector for that API. Its job is to ask Salesforce what fields exist on each object, query those fields in small batches, and pass the records onward in a standard shape the UFO source runtime understands.

The file starts by defining the list of Salesforce objects this connector knows about. Each stream has a human-friendly stream name, the Salesforce object name, a primary key, and a timestamp field used as a bookmark. That bookmark lets later syncs ask only for records changed after the last successful run, instead of downloading everything again.

When a sync runs, the connector first calls Salesforce's “describe” endpoint to discover the fields for the object. It then builds a SOQL query, which is Salesforce's SQL-like query language, and follows Salesforce's pagination links until all matching records are read. If this is not the first sync, it also asks Salesforce for records that were hard-deleted since the previous bookmark and returns them as delete tombstones, so the rest of the system can remove vanished records too.

If Salesforce refuses access with an authorization error, the connector skips that stream with a clear message instead of crashing the whole source run. It deliberately does not write anything back to Salesforce.

#### Function details

##### `_stream`  (lines 30–39)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates the standard description for one Salesforce stream. It keeps the stream definitions short and consistent by filling in common details like the record ID field and the timestamp used for syncing changes.

**Data flow**: It receives a stream name, a Salesforce object name, and whether the stream is considered canonical. It packages those values together with fixed Salesforce fields such as Id, CreatedDate, and SystemModstamp. The result is a StreamSpec object that tells the sync runtime how to identify and bookmark records from that Salesforce object.

**Call relations**: This helper is used while the file is being loaded to build the Salesforce stream list. It hands each finished stream description to StreamSpec, which is the shared source-runtime type used by the connector later during pagination.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 80–84)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: This function builds the Salesforce query string used to fetch records for one stream. It includes all known fields and, when a previous sync bookmark exists, limits the query to records changed after that bookmark.

**Data flow**: It receives the stream description, the list of field names discovered from Salesforce, and an optional cursor value from the previous sync. It joins the fields into a SELECT query, adds a WHERE clause if there is a cursor, sorts by the cursor field so records arrive in time order, and caps the batch size. It returns the finished SOQL text that can be sent to Salesforce.

**Call relations**: The pagination flow calls this after it has discovered the object's fields. The returned query is then sent to Salesforce's query endpoint so the connector can start reading records.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 86–92)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: This function asks Salesforce what fields exist on a particular object. That matters because different Salesforce organizations can add or remove fields, so the connector should not rely on a hard-coded schema.

**Data flow**: It receives an HTTP client and a Salesforce object name. It calls Salesforce's describe endpoint, reads the field entries from the response, keeps only valid field names, and returns those names as a list of strings.

**Call relations**: The pagination flow calls this before building its query. Its field list is handed to _build_soql, allowing the connector to request whatever columns the current Salesforce organization actually exposes.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 94–121)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main read loop for a Salesforce stream. It fetches records page by page, follows Salesforce's next-page links, and also reports deleted records when doing an incremental sync.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. It discovers the object's fields, builds a Salesforce query, sends that query, yields each non-empty batch of records, and follows nextRecordsUrl until Salesforce says the query is done. If a cursor was provided, it then asks for records deleted since that cursor and yields a StreamPage containing delete tombstones and the next bookmark. If Salesforce responds with 401 or 403, it changes that low-level web error into a StreamSkipped message explaining that access was refused.

**Call relations**: This is the function the source runtime relies on when it wants data from Salesforce. It coordinates _describe_fields, _build_soql, and _deleted_page: first learn the shape of the data, then query live records, then report deletions. It hands record batches and delete pages back to the sync runtime as they become available.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 123–141)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: This function finds Salesforce records that were hard-deleted since the previous sync. It turns those missing record IDs into a tombstone page, which tells the rest of the system to treat those records as removed.

**Data flow**: It receives an HTTP client, a stream description, and the previous cursor. It chooses the current time as the end of the deletion-check window, calls Salesforce's deleted-records endpoint, extracts deleted record IDs, and chooses the next cursor from Salesforce's latest covered date when available. It returns a StreamPage containing the deleted IDs and next cursor, or nothing if there is no useful deletion information.

**Call relations**: The main paginate function calls this after reading changed live records, but only when there is already a cursor. It constructs a StreamPage for the sync runtime so deletion information travels through the same paging system as normal records.

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 143–146)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function cleans up each Salesforce record before the rest of the system sees it. Salesforce includes an attributes wrapper with API metadata, and this function removes that wrapper so only real field values remain.

**Data flow**: It receives one Salesforce record and its stream description. If the record contains an attributes key, it returns a copy without that key. If there is no such wrapper, it returns the record unchanged.

**Call relations**: This is a standard connector hook used by the source runtime when preparing individual records. It does not call other project functions here; it simply shapes Salesforce's raw response into the cleaner record format expected downstream.


### `extensions/sources/ufo_ext_sources/providers/typeform.py`

`io_transport` · `source sync runs`

Typeform is an outside service, so its data has to be fetched over its web API in the shape Typeform provides. This file is the adapter between Typeform’s API and UFO’s source-sync system. Without it, UFO would not know which Typeform endpoints to call, how to move through Typeform’s pages of results, or how to attach useful context like which form a response came from.

The file defines the available Typeform streams: forms, responses, workspaces, images, themes, and webhooks. A stream is simply one kind of data the sync runner can ask for. The main class, TypeformConnector, inherits common REST API behavior from RestConnector, such as making HTTP requests and cursor-based paging.

Most Typeform lists are fetched page by page, like reading a book one page at a time until the last page. Responses and webhooks are different: Typeform stores them under each form, so the connector first fetches all forms, then visits each form to collect its responses or webhooks. For responses, it can use a cursor, which is a saved “last seen” marker, so later syncs only ask for newer submissions.

If Typeform rejects a request with an authorization error, the connector does not crash the whole sync. It marks that stream as skipped, usually meaning the token is invalid or lacks permission.

#### Function details

##### `TypeformConnector.record_ref`  (lines 54–58)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This chooses a stable human-readable reference for a Typeform record. For most streams it uses the normal inherited behavior, but for webhooks it uses the webhook tag because Typeform webhooks are better identified that way.

**Data flow**: It receives one Typeform record and the stream it belongs to. If the stream is not webhooks, it passes the record to the parent connector’s usual reference logic. If it is webhooks, it reads the record’s tag field and returns it as text when possible; otherwise it returns nothing.

**Call relations**: This is part of the connector’s record-shaping step. When the wider source system needs a reference for a synced record, this method gives webhooks a Typeform-specific reference while leaving all other streams to the shared REST connector behavior.


##### `TypeformConnector.paginate`  (lines 60–87)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main router for reading a requested Typeform stream. It decides which helper should fetch the data and turns Typeform authorization failures into a clean “skip this stream” signal.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching fetch routine, and yields each batch of records as it arrives. If the stream is unknown, or Typeform refuses access with a 401 or 403 response, it raises StreamSkipped so the sync runner can move on safely.

**Call relations**: The sync runner calls this when it wants records for one Typeform stream. It hands forms to TypeformConnector._forms, responses to TypeformConnector._responses, simple page-based streams to TypeformConnector._paged_items, and webhooks to TypeformConnector._webhooks. If none of those apply, it stops that stream with StreamSkipped.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 89–107)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Typeform endpoints that return a normal list of items split across numbered pages. It hides the repeated work of asking for page 1, then page 2, and so on.

**Data flow**: It receives an HTTP client, an API path such as /forms, and optional query parameters. It adds the page number and page size, requests data from Typeform, extracts the items list, and yields each non-empty batch. It stops when Typeform says the last page has been reached, or when a short page shows there is probably no more data.

**Call relations**: TypeformConnector.paginate uses this directly for workspaces, images, and themes. TypeformConnector._forms also uses it to fetch form pages before applying form-specific filtering. The helper relies on records_at to pull the item list out of Typeform’s response body.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 109–116)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches Typeform forms and, when given a cursor, keeps only forms updated after that saved point. It is used both as its own stream and as the starting point for form-specific data like responses and webhooks.

**Data flow**: It receives an HTTP client and an optional cursor. It asks TypeformConnector._paged_items for all form pages from /forms. If a cursor is present, it filters out forms whose last_updated_at value is not newer than the cursor, then yields the remaining forms in batches.

**Call relations**: TypeformConnector.paginate calls this when the requested stream is forms. TypeformConnector._responses and TypeformConnector._webhooks call it with no cursor because they need the full list of forms before they can visit each form’s nested endpoints.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 118–140)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches submitted answers for every Typeform form. It adds the form’s id and title to each response so a response is not separated from the form it belongs to.

**Data flow**: It receives an HTTP client and an optional cursor. First it gets all forms through TypeformConnector._forms. For each valid form id, it requests that form’s responses using Typeform’s cursor-style paging, where a next-page token is fed back into the next request. If a cursor was provided, it sends it as a since filter. Each yielded response batch is enriched with form_id and form_title before leaving the function.

**Call relations**: TypeformConnector.paginate calls this for the responses stream. Inside, it depends on TypeformConnector._forms to discover which form response endpoints exist, and it uses with_context to attach form details before handing records back to the caller.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 142–151)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches webhook definitions for every Typeform form. A webhook is a saved instruction telling Typeform to call another service when something happens, so each one needs the form context attached.

**Data flow**: It receives an HTTP client. It gets all forms through TypeformConnector._forms, skips any form without a usable id, then requests /forms/{form_id}/webhooks for each form. It extracts the items list from the response and, when records exist, adds form_id and form_title before yielding them.

**Call relations**: TypeformConnector.paginate calls this for the webhooks stream. This function uses TypeformConnector._forms to find all forms, records_at to extract webhook records from Typeform’s response, and with_context to keep each webhook tied to the form it belongs to.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).
