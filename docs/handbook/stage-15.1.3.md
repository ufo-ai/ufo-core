# CRM, sales, and customer-support sources  `stage-15.1.3`

This stage is a set of read-only connectors for tools used by sales teams, marketers, and support teams. It is behind-the-scenes support for the main sync work: when the product needs customer or conversation data, these files know how to ask each outside service for it and turn the answers into a common stream of records.

Each connector is like an adapter for a different plug shape. Apollo reads contacts and accounts from its search APIs. Attio reads CRM data such as companies, people, deals, tasks, notes, meetings, and call recordings. Freshdesk reads support records and carefully follows its different page-by-page result formats. HubSpot covers a wide range of CRM and marketing data, including contacts, companies, deals, conversations, analytics, and custom objects. Intercom reads conversations, contacts, companies, tickets, teams, tags, and related details. Salesforce reads common business objects like accounts, contacts, opportunities, and cases. Zendesk reads tickets, users, organizations, Help Center articles, and community posts. Together, they make many outside systems look consistent to the rest of the product.

## Files in this stage

### Sales and relationship CRMs
Prospecting and CRM connectors establish streams for contacts, accounts, companies, people, deals, tasks, and related relationship records.

### `extensions/sources/ufo_ext_sources/providers/apollo.py`

`io_transport` · `source sync polling`

Apollo does not offer a useful “tell me what changed” webhook for CRM records, and its search API cannot ask directly for “records changed since this time.” This connector works around that by walking Apollo’s contacts and accounts pages from newest to oldest. Think of it like checking a stack of papers sorted with the newest on top: once you find a paper older than the bookmark from last time, you can stop looking.

The file defines two streams, contacts and accounts, with the fields the sync system needs to identify each record and track progress. The ApolloConnector then customizes the normal REST connector behavior in two important ways. First, Apollo expects the API key in an X-Api-Key header, not the more common Authorization bearer header, so the connector moves the key into the right place. Second, its paginate method posts to Apollo’s search endpoints page by page, keeps only records newer than the saved cursor, and stops when it reaches older data or the last available page.

If Apollo refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of treating the whole run as a mysterious crash. This matters because some Apollo keys are not powerful enough to read every API surface.

#### Function details

##### `ApolloConnector._make_client`  (lines 61–69)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Apollo. It makes sure the API key is sent in Apollo’s required X-Api-Key header instead of the usual bearer-token Authorization header.

**Data flow**: It receives a base API address and a resolved credential. It first asks the shared REST connector to build a normal HTTP client. If the credential contains a bearer-style key, it removes the default Authorization header and places that key into X-Api-Key. It returns the adjusted client, ready to make Apollo requests.

**Call relations**: This is part of the connector setup before any Apollo pages are read. The broader REST connector machinery calls on it when creating the client, and the returned client is later used by pagination to send search requests to Apollo.


##### `ApolloConnector.paginate`  (lines 71–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Apollo stream, such as contacts or accounts, a page at a time. It returns batches of records that are newer than the last saved sync position, so incremental syncs do not have to reread the whole CRM.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is the saved “last seen” creation time from a previous run. It chooses the correct Apollo search endpoint for that stream, sends POST requests with page number, page size, and newest-first sorting, then pulls the record list out of the response. It filters those records through ApolloConnector._above, yields any records that are still newer than the cursor, and stops when it reaches an empty page, a page containing older records, or Apollo’s final page. If Apollo responds with 401 or 403, it turns that refusal into a StreamSkipped message explaining that the key may be invalid or not a master key.

**Call relations**: This is the main read loop for Apollo sync. During a source run, the shared sync framework asks the connector to paginate each configured stream. Inside the loop it uses list_or_empty to safely treat missing or non-list response data as an empty list, get_path to read Apollo’s nested pagination.total_pages value, and ApolloConnector._above to decide which records are still worth syncing.

*Call graph*: calls 2 internal fn (__init__, _above); 2 external calls (get_path, list_or_empty).


##### `ApolloConnector._above`  (lines 107–116)

```
def _above(records: list[dict[str, Any]], cursor: str | None) -> list[dict[str, Any]]
```

**Purpose**: This filters a page of Apollo records down to only the records newer than the saved cursor. It is the small rule that lets Apollo syncing stop early even though Apollo itself cannot filter by time.

**Data flow**: It receives a list of records and an optional cursor string. If there is no cursor, meaning this is a first full sync, it returns all records unchanged. If there is a cursor, it keeps only records whose created_at value is a string and is greater than that cursor, then returns that smaller list.

**Call relations**: ApolloConnector.paginate calls this for every page it receives from Apollo. The size of the returned list tells paginate not only what to yield, but also whether the page has crossed the saved watermark and the connector can stop walking older pages.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/attio.py`

`io_transport` · `source sync`

Attio stores customer data in several different shapes. Regular CRM objects such as companies, people, and deals come from one kind of API endpoint. Tasks and notes come from another. Meetings and call recordings use cursor-style paging, and call recordings require an extra trip to fetch transcripts. This connector hides those differences so the rest of the system can simply ask for pages of records.

The most important job here is flattening. Attio often puts the useful ID inside an `id` object and puts each field inside a nested list called `values`. That is like receiving a contact card where every line is sealed in its own envelope. The connector opens those envelopes, chooses the useful value, and writes it onto the top-level record. Without this step, the system would not reliably know the record’s primary ID, name, email, domain, transcript text, and so on.

The connector only reads from Attio. It does not write changes back. It treats every stream as a full snapshot, meaning missing records can be deleted downstream. It also gracefully skips streams when Attio says a standard object is disabled or the OAuth token is missing a required permission.

#### Function details

##### `_records_stream`  (lines 35–43)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates the standard description for an Attio object stream, such as companies, people, or deals. It makes sure each of those streams uses `record_id` as its main identifier and is treated as a full snapshot.

**Data flow**: It receives a stream name and the Attio object slug to read. It builds a StreamSpec with the source object, primary key, snapshot behavior, and whether the stream is canonical. The result is a reusable stream definition used by the connector.

**Call relations**: At file load time, this helper is used to build the Attio stream list. It hands its settings to StreamSpec so the broader source framework knows what each CRM object stream is called and how to identify records.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 70–71)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: This small helper safely pulls one ID field out of a nested dictionary. It avoids crashes when Attio returns a value in an unexpected shape.

**Data flow**: It receives any value and the key to look for. If the value is a dictionary, it returns the requested entry; otherwise it returns nothing. The output is either the nested ID value or null.

**Call relations**: The primitive-value picker uses this when Attio gives option or status IDs in nested objects. It is a guardrail inside the flattening path.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 80–81)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: This builds the request body used when asking Attio for a page of CRM object records. It sets the page size and the starting offset.

**Data flow**: It receives an offset number. It returns a small dictionary containing Attio’s record-query limit and that offset. It does not change any stored state.

**Call relations**: The main pagination method calls this before each standard object request. The returned body is sent to Attio’s records query endpoint.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._value_primitive`  (lines 84–127)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: This chooses the plain useful value from one Attio value cell. Attio stores different field types under different keys, so this function translates many shapes into simple values such as text, email, phone number, selected option title, linked record ID, or address.

**Data flow**: It receives one dictionary representing a value cell from Attio. It checks known field shapes in order, extracts the most human-useful primitive value, and returns that value. If it cannot recognize a useful value, it returns null.

**Call relations**: The cell-flattening helpers depend on this function whenever they open Attio’s nested field values. It calls _nested_id when an option or status has no title but does have a nested ID.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 130–145)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: This turns one Attio field cell into a single clean value, or sometimes a list when multiple selected options should be preserved. It is the basic envelope-opener for ordinary attributes.

**Data flow**: It receives a cell that may be a list, a dictionary, or an already-simple value. For lists and dictionaries, it extracts primitive values and drops empty ones. It returns null, one primitive value, a list of option values, or the original simple value.

**Call relations**: The value-flattening step uses this for most Attio fields. It relies on the primitive-value picker to understand the many field formats Attio can return.


##### `AttioConnector._flatten_list_cell`  (lines 148–155)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: This turns an Attio field into a clean list of values. It is used for fields where keeping all entries matters, such as email addresses, phone numbers, domains, and categories.

**Data flow**: It receives a cell that may or may not already be a list. It extracts useful primitive values, removes nulls, and returns a list. If there is no useful value, it returns an empty list.

**Call relations**: The broader value-flattening step calls this for known multi-value fields. It shares the same primitive extraction rules as ordinary cell flattening.


##### `AttioConnector._flatten_values`  (lines 158–192)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This walks through all of a CRM record’s Attio attributes and turns them into ordinary top-level fields. It also creates convenient shortcut fields like `email`, `phone`, `domain`, and first and last name.

**Data flow**: It receives the record’s `values` dictionary from Attio. For each attribute, it flattens the cell into a simple value or list, then adds helpful derived fields from names, domains, categories, emails, and phone numbers. It returns a new flat dictionary of attributes.

**Call relations**: Standard CRM record flattening calls this after it has lifted the record IDs and timestamps. It brings together _flatten_cell and _flatten_list_cell so downstream code sees easy-to-use fields instead of Attio’s nested structure.


##### `AttioConnector._flatten_record`  (lines 195–209)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This turns a standard Attio CRM record, such as a company or person, into the flat form the rest of the system expects. It makes the main ID and workspace information visible at the top level.

**Data flow**: It receives the raw Attio record and the stream definition. It pulls IDs and timestamps out of the raw record, optionally copies a cursor field if one is configured, then adds the flattened attribute values. It returns one clean record dictionary.

**Call relations**: The public flatten method sends standard object records here when the stream is not tasks, notes, meetings, or call recordings. This is the main path for companies, people, and deals.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 212–216)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This gives an Attio task a clear top-level `task_id`. That lets the sync system identify the task reliably.

**Data flow**: It receives a raw task record. It copies the record, extracts `task_id` from the nested `id` object when needed, and returns the copied record with `task_id` added.

**Call relations**: The public flatten method calls this when processing the tasks stream. It is a task-specific version of ID lifting.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 219–223)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This gives an Attio note a clear top-level `note_id`. That makes notes safe to store and compare during sync.

**Data flow**: It receives a raw note record. It copies the record, extracts `note_id` from the nested `id` object when needed, and returns the copied record with `note_id` added.

**Call relations**: The public flatten method calls this for the notes stream. It keeps note records in the same easy-to-identify style as other Attio data.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 226–230)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This gives an Attio meeting a clear top-level `meeting_id`. That ID is needed both for storing meetings and for finding their call recordings.

**Data flow**: It receives a raw meeting record. It copies the record, extracts `meeting_id` from the nested `id` object when needed, and returns the copied record with `meeting_id` added.

**Call relations**: The public flatten method calls this for the meetings stream. The call-recording pagination path also depends on meeting IDs, though it extracts them with a smaller helper before flattening.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 233–254)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This prepares a call recording record for recall by lifting its ID, choosing a usable recording URL, and turning transcript segments into readable text. This is what makes call recordings searchable as conversation content.

**Data flow**: It receives a raw call recording. It copies the record, adds `call_recording_id`, fills `recording_url` from `web_url` if needed, and joins transcript speaker lines into `transcript_text`. It returns the enriched recording record.

**Call relations**: The public flatten method calls this for the call_recordings stream. Earlier pagination may already have attached the transcript, and this function turns that transcript into a single readable field.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 256–265)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This is the connector’s central cleanup switchboard. It sends each raw Attio record to the right flattening routine based on which stream it came from.

**Data flow**: It receives a raw record and its stream definition. It checks the stream name, delegates to the matching task, note, meeting, call-recording, or standard-record flattener, and returns the cleaned record.

**Call relations**: The source framework calls this after records are fetched. It hands off to the specialized flattening functions so every stream ends up with the right primary key and readable fields.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 267–293)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches pages of records from Attio for a requested stream. It knows that standard CRM objects use one API style, while tasks, notes, meetings, and call recordings use separate paths.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor that this connector does not use for standard snapshots. For named special streams, it delegates to _paginate_named. For standard objects, it repeatedly posts query bodies with increasing offsets, yields each non-empty page, and stops when Attio returns fewer than the page limit. If Attio says a standard object is disabled, it raises a skip signal instead of failing the whole sync.

**Call relations**: This is the main record-fetching entry used by the source framework. It calls _build_query_body for standard object requests, _paginate_named for special streams, and _is_object_disabled to decide whether a stream should be skipped.

*Call graph*: calls 4 internal fn (__init__, _build_query_body, _is_object_disabled, _paginate_named).


##### `AttioConnector._paginate_named`  (lines 295–321)

```
async def _paginate_named(self, client: httpx.AsyncClient, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This routes the non-standard Attio streams to the right paging method. Tasks and notes use simple offset pages, while meetings and call recordings use cursor pages and may require permission checks.

**Data flow**: It receives an HTTP client and a stream name. It yields pages from the matching helper: simple offset paging for tasks or notes, cursor paging for meetings, or the call-recording fan-out process. If Attio returns a missing-permission error for meetings or recordings, it raises a clear stream-skip message.

**Call relations**: The main paginate method calls this for tasks, notes, meetings, and call recordings. It calls _paginate_simple, _paginate_cursor, or _paginate_call_recordings, and uses _is_scope_unauthorized plus _scope_skip_reason to turn certain API errors into skipped streams.

*Call graph*: calls 6 internal fn (__init__, _is_scope_unauthorized, _paginate_call_recordings, _paginate_cursor, _paginate_simple, _scope_skip_reason); called by 1 (paginate).


##### `AttioConnector._paginate_simple`  (lines 323–330)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Attio endpoints that use simple offset paging, where each request asks for records starting at a numbered position. It is used for tasks and notes.

**Data flow**: It receives an HTTP client, an API path, and a page size. It asks the base REST connector to fetch offset-based pages from the `data` field and yields each page it gets back.

**Call relations**: _paginate_named calls this when the stream is tasks or notes. It delegates the low-level paging mechanics to the shared REST connector.

*Call graph*: called by 1 (_paginate_named).


##### `AttioConnector._paginate_cursor`  (lines 332–350)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Attio endpoints that use cursor paging, where the API returns a token for the next page instead of a numeric offset. It is used for meetings and call recordings.

**Data flow**: It receives an HTTP client, an API path, a page size, and optional query parameters. It asks the base REST connector to fetch records from `data`, follow `pagination.next_cursor`, and yield each page until no next cursor remains.

**Call relations**: _paginate_named uses this for meetings, and _paginate_call_recordings uses it both to list meetings and to list recordings under each meeting. It relies on shared REST paging support for the actual HTTP loop.

*Call graph*: called by 2 (_paginate_call_recordings, _paginate_named).


##### `AttioConnector._paginate_call_recordings`  (lines 352–388)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers call recordings by first walking through every meeting, then asking Attio for recordings attached to each meeting. It also tries to fetch each recording’s transcript and stamps meeting context onto the recording.

**Data flow**: It receives an HTTP client. It pages through meetings, extracts each meeting ID, title, start time, end time, and duration, then pages through that meeting’s call recordings. For each recording, it adds parent meeting information and, when possible, fetches transcript data. It yields pages of enriched recording records.

**Call relations**: _paginate_named calls this when the requested stream is call_recordings. It uses _paginate_cursor for both meeting and recording lists, _meeting_id and _call_recording_id to find IDs, _datetime_of and _duration_seconds for timing fields, and _fetch_transcript for transcript content.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (_paginate_named).


##### `AttioConnector._fetch_transcript`  (lines 390–402)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: This asks Attio for the transcript of one call recording. If the transcript is not ready or not found, it quietly returns nothing instead of stopping the sync.

**Data flow**: It receives an HTTP client, a meeting ID, and a recording ID. It builds the transcript endpoint path and performs a GET request. If Attio responds with 404 or 409, it returns null; otherwise it returns the transcript body from the response when it is a dictionary.

**Call relations**: _paginate_call_recordings calls this for each recording that has an ID. Its result is attached to the recording before the record later reaches the call-recording flattener.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 405–409)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: This safely extracts a meeting ID from an Attio meeting record. It supports both nested ID objects and plain string IDs.

**Data flow**: It receives a meeting dictionary. If `id` is a dictionary, it returns `id.meeting_id`; if `id` is already a string, it returns that string. Otherwise it returns null.

**Call relations**: _paginate_call_recordings uses this while walking meeting pages. Without a meeting ID, that meeting cannot be used to fetch attached recordings, so it is skipped.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 412–416)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: This safely extracts a call recording ID from an Attio recording record. It supports the shapes Attio may use for IDs.

**Data flow**: It receives a recording dictionary. If `id` is a dictionary, it returns `id.call_recording_id`; if `id` is already a string, it returns that string. Otherwise it returns null.

**Call relations**: _paginate_call_recordings uses this before trying to fetch a transcript. A transcript request can only be made when this helper finds a recording ID.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 419–423)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: This extracts a usable date or date-time string from Attio’s meeting time shape. It handles both timed meetings and all-day meetings.

**Data flow**: It receives a value that may be a dictionary with `datetime`, `timezone`, or `date` fields. If it is a dictionary, it returns the `datetime` value first, or the `date` value if no datetime exists. Otherwise it returns null.

**Call relations**: _paginate_call_recordings calls this for meeting start and end values. Those extracted strings are added to recordings and are also used to estimate duration.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 426–436)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: This estimates how long a meeting lasted in seconds from its start and end strings. If the dates are missing or malformed, it gives up safely.

**Data flow**: It receives optional start and end strings. It parses them as ISO 8601 date-time values, treating a trailing `Z` as UTC, subtracts start from end, and returns the non-negative duration in seconds. If parsing fails or either value is missing, it returns null.

**Call relations**: _paginate_call_recordings calls this after extracting meeting times. The resulting duration is copied onto call recording records when it can be calculated.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 439–448)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: This checks whether an Attio error means a standard CRM object is disabled in the workspace. That lets the connector skip unavailable streams instead of treating them as broken.

**Data flow**: It receives an HTTP status error. It only considers 400 responses, tries to read the JSON response body, and returns true when the body code is `standard_object_disabled`. Otherwise it returns false.

**Call relations**: The main paginate method calls this when a standard object query fails. If it returns true, paginate raises StreamSkipped with a clear reason.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._is_scope_unauthorized`  (lines 451–460)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: This checks whether an Attio error means the OAuth token is missing a required permission scope. A scope is a named permission granted to an app, such as access to meetings.

**Data flow**: It receives an HTTP status error. It only considers 403 responses, tries to read the JSON body, and returns true when the body code is `unauthorized`. Otherwise it returns false.

**Call relations**: _paginate_named calls this around meetings and call-recording pagination. If it detects a missing scope, the stream is skipped with a helpful message rather than crashing the whole sync.

*Call graph*: called by 1 (_paginate_named).


##### `AttioConnector._scope_skip_reason`  (lines 463–469)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: This builds a human-readable explanation for skipping a stream because the OAuth grant is missing a required permission. It includes Attio’s own message when available.

**Data flow**: It receives an HTTP status error. It tries to read the JSON body and pull out the `message` field. It returns a sentence explaining that a required OAuth scope is missing, falling back to a generic note if no message is available.

**Call relations**: _paginate_named calls this after _is_scope_unauthorized identifies a missing-permission error. The returned text is passed into StreamSkipped so operators can understand what permission problem occurred.

*Call graph*: called by 1 (_paginate_named).


### Support desk ingestion
The Freshdesk connector handles support-platform authentication, object selection, and paging for customer-service records.

### `extensions/sources/ufo_ext_sources/providers/freshdesk.py`

`io_transport` · `source sync`

Freshdesk exposes many kinds of data: tickets, conversations, contacts, companies, agents, knowledge-base articles, forum posts, settings, and more. This connector is the map and travel guide for reading all of them through Freshdesk’s REST API, which is a web interface where the system asks for JSON records over HTTP.

The file starts by defining the available streams. A stream is one category of records to sync, such as tickets or companies. Most streams are simple: call one Freshdesk URL and follow the “next page” link until there are no more pages. Some streams are more like a filing cabinet. For example, conversations live under tickets, articles live under folders, and folders live under categories. For those, the connector first lists the parent items, then visits each child URL.

Authentication is also handled here. Freshdesk expects HTTP Basic authentication, using the API key as the username and a dummy password. If the system is using a brokered or proxied connection instead, this file respects that transport instead of replacing it.

A key detail is tickets: Freshdesk uses numbered pages for them and refuses requests past page 300. This connector stops at that ceiling and relies on the caller to use smaller time windows when needed. If Freshdesk rejects access with 401 or 403, the stream is skipped with a clear message instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 56–70)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one Freshdesk object type. It keeps the long stream list readable by filling in common defaults like the source object name and primary key.

**Data flow**: It receives a stream name plus optional details such as the API object name, primary key, cursor field, and whether the stream is considered canonical. It packages those choices into a StreamSpec object, which the rest of the connector uses as the instruction card for syncing that stream.

**Call relations**: This function is used while the file is being loaded to build the Freshdesk stream catalog. Its main handoff is to StreamSpec, which stores the stream metadata for the connector class to expose later.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector.record_identity`  (lines 110–113)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This function decides the stable identity for a Freshdesk record. It gives the settings record a fixed identity because Freshdesk settings are a single page rather than a normal list of items with separate IDs.

**Data flow**: It receives one record and the stream it came from. If the stream is settings, it returns the constant key used for the helpdesk settings page; otherwise it lets the general REST connector decide the identity in the usual way.

**Call relations**: The broader source-sync machinery asks the connector for record identities when storing or comparing records. This method only steps in for the special settings stream and otherwise passes the decision back to the parent connector behavior.


##### `FreshdeskConnector.record_ref`  (lines 115–119)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This function chooses a human-useful reference value for a record. For Freshdesk settings, it uses the primary language as the reference because the settings page has no ordinary object name.

**Data flow**: It receives a record and its stream. For non-settings streams, it delegates to the normal REST connector logic. For settings, it reads the primary_language field and returns it as text if it is a string or number; if that field is not useful, it returns nothing.

**Call relations**: The sync system uses this when it wants a friendly label or reference for a stored record. Like record_identity, this method exists mainly to make the unusual single settings page fit into the normal record model.


##### `FreshdeskConnector._make_client`  (lines 121–136)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function builds the HTTP client used to talk to Freshdesk. It applies the correct Freshdesk base URL, timeouts, JSON headers, and authentication style.

**Data flow**: It receives a base URL and a credential. It trims the URL, prepares request headers, and sets connection and read timeouts. If the credential already carries a custom transport, it builds a client around that; if it carries a direct API key, it turns that key into Freshdesk Basic authentication. If neither is available, it raises an error because it cannot safely contact Freshdesk.

**Call relations**: The connector setup path calls this before pagination begins. It hands back an httpx AsyncClient, which is the network tool later used by paginate and the lower-level page walkers to fetch API responses.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 139–149)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: This function builds the query options for one page of Freshdesk tickets. It makes sure tickets are requested in a consistent order and, when possible, only after a saved update cursor.

**Data flow**: It receives an optional cursor and a page number. It creates a parameter dictionary asking for 100 tickets, sorted by updated_at in ascending order, with useful extra ticket details included. If a cursor is present, it adds updated_since so Freshdesk returns tickets updated after that point.

**Call relations**: FreshdeskConnector._paginate_tickets calls this each time it asks for another numbered ticket page. The returned parameters become the request options for the Freshdesk tickets endpoint.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 151–178)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway for reading pages from a Freshdesk stream. Given a stream such as tickets or contacts, it chooses the right paging method and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. First it asks whether the stream needs special traversal, such as tickets, conversations, or nested knowledge-base/forum data. If so, it yields those special pages. If not, it finds the simple API path, handles the single settings page if needed, or follows normal link-header pagination. If Freshdesk replies with 401 or 403, it turns that refusal into a StreamSkipped error with an explanatory message.

**Call relations**: The source runtime calls this when it wants records for a stream. This function delegates special cases to FreshdeskConnector._special_pages, ordinary list streams to FreshdeskConnector._paginate_link_header, and refusal reporting to StreamSkipped.

*Call graph*: calls 3 internal fn (__init__, _paginate_link_header, _special_pages).


##### `FreshdeskConnector._special_pages`  (lines 180–221)

```
def _special_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]] | None
```

**Purpose**: This function is the traffic director for streams that cannot be read from one simple URL. It recognizes Freshdesk resources that require ticket-based, two-level, or three-level walking.

**Data flow**: It receives the HTTP client, stream name, and optional cursor. For tickets and conversations, it returns their dedicated paginators. For child resources such as canned responses, forums, topics, and comments, it chooses the parent and child URL pattern and returns a two-level paginator. For solution articles, it returns a three-level paginator that walks categories, folders, and then articles. If the stream is not special, it returns nothing.

**Call relations**: FreshdeskConnector.paginate calls this before trying the simple-path table. Depending on the stream name, it hands the work to FreshdeskConnector._paginate_tickets, FreshdeskConnector._paginate_conversations, FreshdeskConnector._paginate_two_level, or FreshdeskConnector._paginate_three_level.

*Call graph*: calls 4 internal fn (_paginate_conversations, _paginate_three_level, _paginate_tickets, _paginate_two_level); called by 1 (paginate).


##### `FreshdeskConnector._paginate_link_header`  (lines 223–230)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function follows Freshdesk’s normal “next page” links for list endpoints. It is used for the many resources where Freshdesk tells the client where the next batch is through an HTTP Link header.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST connector helper to fetch pages with a page size of 100, then yields each page of records as it arrives.

**Call relations**: FreshdeskConnector.paginate uses this for ordinary streams. The nested walkers also use it whenever they need to list parents or children, so FreshdeskConnector._paginate_conversations, FreshdeskConnector._paginate_two_level, and FreshdeskConnector._paginate_three_level all rely on it as their basic page-fetching tool.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 232–249)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Freshdesk tickets using Freshdesk’s ticket-specific numbered paging rules. It supports incremental sync by asking for tickets updated after a cursor.

**Data flow**: It receives an HTTP client and an optional cursor. Starting at page 1, it builds ticket parameters, fetches the ticket endpoint, extracts the records, and yields them. It stops when there are no records, when a page is shorter than the maximum page size, or when it reaches Freshdesk’s 300-page ceiling.

**Call relations**: FreshdeskConnector._special_pages chooses this when the requested stream is tickets. FreshdeskConnector._paginate_conversations also calls it first because conversations are found by walking through tickets. For each request, it uses FreshdeskConnector._build_tickets_params to prepare the query options.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, _special_pages).


##### `FreshdeskConnector._paginate_conversations`  (lines 251–267)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads ticket conversations by first finding the tickets and then fetching the conversation list for each ticket. It turns a parent-child API shape into a flat stream of conversation records.

**Data flow**: It receives an HTTP client and optional cursor. It pages through tickets admitted by that cursor, takes each ticket ID, fetches that ticket’s conversations, and stamps each conversation with ticket_id if it is missing. It then yields conversation pages for the sync system to store.

**Call relations**: FreshdeskConnector._special_pages returns this paginator for the conversations stream. Internally it depends on FreshdeskConnector._paginate_tickets to find ticket IDs and FreshdeskConnector._paginate_link_header to walk each ticket’s conversation pages.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (_special_pages).


##### `FreshdeskConnector._paginate_two_level`  (lines 269–280)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Freshdesk resources that live one level below a parent resource. An everyday example is opening each folder in a cabinet and copying the papers inside.

**Data flow**: It receives an HTTP client, a parent API path, and a child URL template. It pages through all parents, reads each parent ID, fills that ID into the child URL, then pages through and yields the child records. Parents without usable IDs are skipped.

**Call relations**: FreshdeskConnector._special_pages uses this for streams such as canned responses, solution folders, discussion forums, topics, and comments. The function uses FreshdeskConnector._paginate_link_header both to list the parents and to list each parent’s children.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (_special_pages).


##### `FreshdeskConnector._paginate_three_level`  (lines 282–305)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Freshdesk resources that sit two levels below a root resource. In this file it is used for solution articles, which are found by walking categories, then folders, then articles.

**Data flow**: It receives an HTTP client plus URL patterns for the root, middle, and leaf levels. It lists root records, extracts each root ID, lists the middle records under it, extracts each middle ID, then lists and yields the leaf records. Any category or folder without an ID is skipped because the next URL cannot be built.

**Call relations**: FreshdeskConnector._special_pages chooses this for solution_articles. At every level of the walk, it relies on FreshdeskConnector._paginate_link_header to fetch all pages before moving deeper into the tree.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (_special_pages).


### Customer engagement hubs
HubSpot and Intercom provide broad customer, conversation, marketing, ticket, and engagement streams with varied API pagination patterns.

### `extensions/sources/ufo_ext_sources/providers/hubspot.py`

`io_transport` · `during HubSpot source sync`

HubSpot is a large product with many different API shapes. Some data comes from the CRM search API, some from newer product APIs, and some must be discovered by walking from one object to another. This file is the adapter that hides that variety. Without it, the system would not know which HubSpot endpoints to call, how to page through results, how to continue from a previous sync, or how to represent deleted records.

The file first declares the streams: each stream is a kind of HubSpot thing to sync, such as contacts, owners, workflows, forms, associations, or pipelines. A stream definition says which HubSpot object it comes from, which field identifies a record, and which timestamp can be used as a cursor. A cursor is like a bookmark: it lets the next run start near where the last one stopped.

The `HubSpotConnector` then does the work. It chooses the right paging method for each stream, calls HubSpot, normalizes records into flat dictionaries, and yields pages of rows. For CRM objects it also performs an archived-record sweep so deleted HubSpot records become tombstones. If HubSpot says an account lacks permission for one optional stream, the connector marks that stream as skipped instead of failing the whole sync.

#### Function details

##### `_normalize_epoch_millis`  (lines 247–254)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Converts a HubSpot timestamp written as milliseconds since 1970 into a readable UTC date-time string. It leaves booleans and already non-time-looking values alone so they are not accidentally changed.

**Data flow**: A value comes in, possibly a number or digit-only string. If it looks like milliseconds, the function turns it into an ISO-formatted UTC timestamp; otherwise the original value comes back unchanged.

**Call relations**: This is used when product-style HubSpot rows use old numeric time formats. `_flatten_product_api` uses it for knowledge articles and email events, and `_analytics_view_rows` uses it for analytics view dates.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 257–266)

```
def _stream(name: str, *, object_type: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Builds a standard CRM object stream definition. It saves repeated setup for HubSpot objects that all use the CRM search pattern.

**Data flow**: A stream name and HubSpot object type go in. A `StreamSpec`, which is the system’s description of how to identify and timestamp records, comes out.

**Call relations**: This helper is used while the module defines streams such as companies, contacts, deals, and tickets. Those stream definitions are later listed on `HubSpotConnector` so the runner knows what can be synced.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 269–288)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='createdAt', updated_at_field: str | None='updatedAt', pagi
```

**Purpose**: Builds a stream definition for HubSpot APIs that are not regular CRM objects. These streams often have different primary keys, timestamps, or pagination rules.

**Data flow**: The caller provides the stream name, source object, key fields, optional cursor fields, and optional pagination instructions. The function returns a `StreamSpec` marked as non-canonical because it is not one of the core CRM object streams.

**Call relations**: This is used at module setup time for streams such as owners, workflows, forms, files, analytics reports, and email events. Later pagination code uses those specs to choose the right fetching path.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 291–303)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Creates a reusable pagination recipe for HubSpot endpoints that return `results` plus a `paging.next.after` cursor. Pagination means fetching one page at a time until there are no more pages.

**Data flow**: An API path goes in. A `Pagination` object comes out, telling the base connector where records live in the response and where to find the next-page cursor.

**Call relations**: Stream definitions use this helper for simple product API list endpoints. When `_paginate_unchecked` sees a stream with this pagination recipe, it can hand the work to the base strategy-based paginator.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 306–316)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Builds a stream definition for relationship rows, such as deal-to-contact links. These are synthetic records made by the connector rather than first-class HubSpot objects.

**Data flow**: A junction stream name and its parent object go in. A `StreamSpec` comes out with no cursor, because HubSpot does not expose a reliable modified time for these links.

**Call relations**: The module uses this while defining relationship streams. `_paginate_unchecked` later recognizes these stream names and sends them to `_paginate_junction`.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 621–651)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Creates the JSON body used to search HubSpot CRM objects. It asks HubSpot for all known properties, sorts by the cursor field, and optionally starts from the saved cursor.

**Data flow**: A stream description, property names, an incremental cursor, and a page cursor go in. A request body dictionary comes out, ready to send to HubSpot’s CRM search endpoint.

**Call relations**: `_paginate_crm_object` uses this for normal CRM streams, and `_paginate_custom_object_records` uses it for custom objects. It is the shared request builder for search-based syncing.

*Call graph*: called by 2 (_paginate_crm_object, _paginate_custom_object_records).


##### `HubSpotConnector._flatten`  (lines 654–665)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a CRM object response into a simpler record where important fields are at the top level. This makes later indexing code read records consistently instead of digging through HubSpot’s nested `properties` bag.

**Data flow**: A HubSpot CRM record goes in. The function copies `id`, creation and update times, archive status, and all fields under `properties` into one flat dictionary.

**Call relations**: `flatten` calls this for ordinary CRM object streams after pages have been fetched. It is the normalizing step between HubSpot’s envelope format and the system’s row format.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 668–691)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records from HubSpot product APIs, which do not all look alike. It lifts nested property bags and form-style name/value lists into top-level fields.

**Data flow**: A product API record and its stream description go in. The function copies the record, creates an `id` from `objectId` when needed, lifts nested fields, fixes special date formats for some streams, and returns the flat record.

**Call relations**: `flatten` calls this for product API streams. It uses `_normalize_epoch_millis` when HubSpot returns timestamps as millisecond numbers.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 693–700)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right record-normalizing method for each HubSpot stream. It keeps the rest of the source system from needing to know which HubSpot API shape produced the row.

**Data flow**: A raw record and its stream go in. Junction and custom object rows pass through as already shaped; product API rows go through `_flatten_product_api`; regular CRM rows go through `_flatten`.

**Call relations**: The source framework calls this after records are fetched. It hands work to the correct flattening helper based on the stream name.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector.record_identity`  (lines 702–715)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Returns the stable identity for a record, with special handling for consent states. Consent records need a compound identity because HubSpot may not provide one field that uniquely names the contact, subscription, and business unit together.

**Data flow**: A record and stream description go in. For most streams, the inherited identity logic is used; for consent states, the function combines contact, subscription or status kind, and business unit into one string, or returns nothing if key parts are missing.

**Call relations**: The sync system uses this when deciding whether a record is new, updated, or the same as a previous one. This override prevents consent rows from colliding with one another.


##### `HubSpotConnector._list_properties`  (lines 717–725)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which fields exist for a CRM object type. HubSpot search only returns fields that are requested, so this lets the connector request everything the account exposes.

**Data flow**: An HTTP client and object type go in. The function calls HubSpot’s properties endpoint and returns a list of property names.

**Call relations**: `_paginate_crm_object` calls this before building search requests. The result is passed into `_build_search_body` so each search page includes all available fields.

*Call graph*: called by 1 (_paginate_crm_object).


##### `HubSpotConnector.paginate`  (lines 727–740)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Provides the public paging entry for the connector and turns some HubSpot permission errors into skipped streams. This keeps one unavailable HubSpot feature from failing the entire sync.

**Data flow**: An HTTP client, stream, and saved cursor go in. The function yields pages from `_paginate_unchecked`; if HubSpot returns an authentication or stream-permission error, it raises `StreamSkipped` with a human-readable reason.

**Call relations**: The source runner calls this when it wants records for a stream. It delegates the real fetching to `_paginate_unchecked` and uses `_is_stream_unavailable` plus `_stream_skip_reason` for graceful skip behavior.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 742–769)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Routes each stream to the correct fetching method. It is the connector’s traffic director.

**Data flow**: A stream and cursor go in. Depending on the stream definition or name, it yields pages from a strategy paginator, junction walker, custom object walker, product API paginator, or CRM object search.

**Call relations**: `paginate` calls this inside error handling. It hands off to `_paginate_crm_object`, `_paginate_custom_objects`, `_paginate_junction`, or `_paginate_product_api` depending on what kind of HubSpot data is being read.

*Call graph*: calls 4 internal fn (_paginate_crm_object, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._paginate_crm_object`  (lines 771–798)

```
async def _paginate_crm_object(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Fetches regular HubSpot CRM objects through the search API. It supports incremental syncing and then checks for archived records so deletions are noticed.

**Data flow**: An HTTP client, stream, and cursor go in. The function lists available properties, repeatedly posts search requests, filters duplicate records at the inclusive cursor boundary, yields record pages, and finally yields delete tombstones from `_paginate_archived_ids`.

**Call relations**: `_paginate_unchecked` sends ordinary CRM streams here. It uses `_list_properties`, `_build_search_body`, and `_paginate_archived_ids` to cover both active and deleted records.

*Call graph*: calls 3 internal fn (_build_search_body, _list_properties, _paginate_archived_ids); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._is_stream_unavailable`  (lines 801–824)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects when HubSpot is saying a particular stream is not allowed for this account, usually because of product tier or missing OAuth scope. OAuth scope means a granted permission for an app.

**Data flow**: An HTTP error goes in. The function checks for status 403 and looks for known permission phrases in the JSON message, returning true only for those stream-level availability problems.

**Call relations**: `paginate` uses this to skip unavailable streams. Archived sweeps also use it so optional deletion checks do not fail a stream when HubSpot blocks access.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 827–836)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a clear explanation when a HubSpot stream is skipped. The message includes HubSpot’s own reason when available.

**Data flow**: A stream name and HTTP error go in. The function reads the response message if it can and returns a sentence explaining that the stream is unavailable for this account.

**Call relations**: `paginate` calls this right before raising `StreamSkipped`. It turns a raw API error into a runner-facing skip reason.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 838–873)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived, meaning deleted or hidden, CRM object IDs after the active-record search finishes. This lets the destination remove records that no longer exist in HubSpot.

**Data flow**: An HTTP client and stream go in. The function walks HubSpot’s object list endpoint with `archived=true`, collects IDs, and yields `StreamPage` objects containing deletes; unsupported or unavailable sweeps quietly stop.

**Call relations**: `_paginate_crm_object` calls this after yielding active records. It uses `_is_archived_sweep_unsupported` and `_is_stream_unavailable` to decide when an archived sweep should be ignored rather than treated as fatal.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_crm_object); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 875–883)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Starts pagination for non-CRM HubSpot product streams. It is a small wrapper that keeps product API handling separate from regular CRM search.

**Data flow**: An HTTP client, product stream, and cursor go in. It asks `_product_pages` for the right async page source and yields each page from it.

**Call relations**: `_paginate_unchecked` sends product API streams here. This function then delegates to `_product_pages`, which chooses the exact endpoint-specific walker.

*Call graph*: calls 1 internal fn (_product_pages); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._product_pages`  (lines 885–920)

```
def _product_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct product API paginator for a stream name. Some product streams are simple lists, while others require fan-out, such as fetching messages for each conversation thread.

**Data flow**: An HTTP client, stream name, and optional cursor go in. The function returns an async iterator from a specialized paginator when needed, otherwise uses a known GET collection path, or raises an error if no path is known.

**Call relations**: `_paginate_product_api` calls this. It points streams such as campaign assets, associations, email events, forms, pipelines, and sequence enrollments to their dedicated routines, and simple streams to `_paginate_get_collection`.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api); 1 external calls (partial).


##### `HubSpotConnector._paginate_get_collection`  (lines 922–950)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks a standard HubSpot list endpoint that returns `results` and an `after` cursor. This is the common paging loop for many simpler product APIs.

**Data flow**: An HTTP client, API path, page limit, and optional extra parameters go in. It repeatedly calls the endpoint, normalizes `objectId` into `id` when needed, yields non-empty pages, and stops when HubSpot has no next cursor.

**Call relations**: Many specialized paginators reuse this instead of rewriting the same paging code, including owner teams, campaign assets, forms, conversations, sequences, and simple product streams chosen by `_product_pages`.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_sequences, _product_pages, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 952–987)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Syncs HubSpot custom objects, which are user-defined object types. It first discovers the object schemas, then reads records for each discovered type.

**Data flow**: An HTTP client and cursor go in. The function fetches schemas, extracts each custom object’s type and properties, builds a temporary stream spec, yields active record pages, and then yields archived IDs for that object type.

**Call relations**: `_paginate_unchecked` calls this for the custom object stream. It coordinates `_custom_object_schemas`, schema helpers, `_paginate_custom_object_records`, and `_paginate_custom_object_archived_ids`.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 989–991)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of custom object definitions from HubSpot. A schema describes what a custom object type is called and which fields it has.

**Data flow**: An HTTP client goes in. The function calls HubSpot’s schema endpoint and returns only result rows that are dictionaries.

**Call relations**: `_paginate_custom_objects` uses this to know which custom object types to sync. `_association_object_types` also uses it so associations can include custom object types.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 994–999)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Finds the best usable type identifier from a custom object schema. HubSpot may name that identifier in a few different fields.

**Data flow**: A schema dictionary goes in. The function checks known identifier fields in order and returns the first non-empty string, or nothing if none exists.

**Call relations**: `_paginate_custom_objects`, `_custom_object_row`, and `_association_object_types` call this when they need a stable object type name for requests or record IDs.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1002–1017)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Collects all property names that should be requested for a custom object. It includes normal properties and display properties used to label records.

**Data flow**: A schema dictionary goes in. The function walks its property list and display-property fields, avoids duplicates, and returns a list of names.

**Call relations**: `_paginate_custom_objects` calls this before searching records for each custom object type. The returned names feed into `_paginate_custom_object_records`.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1019–1054)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Fetches records for one custom object type through HubSpot’s CRM search API. It applies the same incremental cursor and boundary deduping pattern used for standard CRM objects.

**Data flow**: An HTTP client, temporary stream, schema, property list, and cursor go in. It posts search requests page by page, skips duplicate boundary records, converts each record with `_custom_object_row`, and yields pages.

**Call relations**: `_paginate_custom_objects` calls this for each discovered custom object schema. It uses `_build_search_body` to create each search request and `_custom_object_row` to shape rows for the shared custom-object stream.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1056–1095)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Turns one raw custom object record into a useful row with labels, titles, and a globally unique ID. This makes many different custom object types fit into one stream.

**Data flow**: A raw record and its schema go in. The function combines schema metadata, record properties, display fields, timestamps, and archive status into one dictionary, or returns nothing if the object type or record ID is missing.

**Call relations**: `_paginate_custom_object_records` calls this for each fetched record. It uses `_schema_object_type_id` so the generated row ID includes the custom object type.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1097–1127)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds deleted records for one custom object type. It creates delete markers using the same custom-object ID format used for active rows.

**Data flow**: An HTTP client and custom object type ID go in. The function walks the archived object list endpoint, prefixes each deleted record ID with the object type, yields delete pages, and stops if HubSpot does not support or allow the sweep.

**Call relations**: `_paginate_custom_objects` calls this after active records for each type. It uses `_is_archived_sweep_unsupported` and `_is_stream_unavailable` for graceful exits.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1129–1148)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Extracts owner team records from the owners API. HubSpot returns teams nested under owners, so this function turns them into their own stream.

**Data flow**: An HTTP client goes in. The function pages through owners, gathers unique team IDs from each owner’s `teams` list, and yields one page of distinct team rows.

**Call relations**: `_product_pages` selects this paginator for the `owner_teams` stream. It reuses `_paginate_get_collection` to read owners first.

*Call graph*: calls 1 internal fn (_paginate_get_collection).


##### `HubSpotConnector._paginate_lists`  (lines 1150–1179)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot CRM lists using the list search endpoint. Lists use offset-based paging instead of the usual `after` cursor.

**Data flow**: An HTTP client goes in. The function posts list search requests, assigns each list a string `id`, lifts extra properties into the row, yields pages, and advances until `hasMore` is false.

**Call relations**: `_product_pages` can use this for the lists stream, and `_paginate_list_memberships` calls it to discover which lists need membership walks.

*Call graph*: called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_site_search`  (lines 1181–1201)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads content from HubSpot’s site search endpoint for a chosen content type. In this file it is used for knowledge articles.

**Data flow**: An HTTP client and content type go in. The function requests pages using limit and offset, yields dictionary rows, and stops when the reported total has been reached.

**Call relations**: `_product_pages` binds this with the knowledge article content type. It provides the stream-specific walk for `knowledge_articles`.


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1203–1227)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds assets attached to HubSpot marketing campaigns. It first lists campaigns, then asks HubSpot for each supported asset type under each campaign.

**Data flow**: An HTTP client goes in. The function pages through campaigns, extracts campaign IDs and names, loops over known campaign asset types, and yields pages from `_paginate_campaign_asset_type`.

**Call relations**: `_product_pages` selects this for the `campaign_assets` stream. It uses `_paginate_get_collection` for campaigns and delegates per-campaign asset fetching to `_paginate_campaign_asset_type`.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1229–1264)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one type of asset for one campaign and shapes each asset into a stable campaign-asset row. Missing asset APIs for a campaign are ignored when HubSpot returns 403 or 404.

**Data flow**: An HTTP client, campaign ID, campaign name, and asset type go in. It pages through the campaign asset endpoint, builds rows with compound IDs, asset kind, campaign metadata, and metrics, then yields non-empty pages.

**Call relations**: `_paginate_campaign_assets` calls this inside its campaign and asset-type loops. It relies on `_paginate_get_collection` for the actual page walking.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1266–1272)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields HubSpot analytics views as a stream. Analytics views are saved filters or reporting views.

**Data flow**: An HTTP client goes in. The function asks `_analytics_view_rows` for normalized rows and yields them if any exist.

**Call relations**: `_product_pages` selects this for the analytics views stream. It is a thin paging wrapper around `_analytics_view_rows`.

*Call graph*: calls 1 internal fn (_analytics_view_rows).


##### `HubSpotConnector._analytics_view_rows`  (lines 1274–1305)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches and normalizes HubSpot analytics view definitions. It gives each view a reliable ID, readable name, filter data, and cleaned timestamps.

**Data flow**: An HTTP client goes in. The function calls the analytics views endpoint, accepts either list or result-wrapper responses, filters to dictionary rows, fills in standard fields, normalizes creation time, and returns a list.

**Call relations**: `_paginate_analytics_views` uses this to emit the analytics view stream. `_paginate_analytics_reports` also uses it so reports can be queried both globally and per view.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1307–1337)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a broad set of analytics report rows by querying many report subjects, time periods, and analytics views. It turns HubSpot’s report API into a stream of reusable records.

**Data flow**: An HTTP client goes in. The function chooses a date window, fetches analytics views, builds global and per-view filters, loops through report families, subjects, and periods, and yields pages from `_paginate_analytics_report_query`.

**Call relations**: `_product_pages` selects this for the analytics reports stream. It coordinates `_analytics_report_window`, `_analytics_view_rows`, and `_paginate_analytics_report_query`.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_window`  (lines 1340–1341)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range for analytics report queries. It starts from a fixed early date and ends at today in UTC.

**Data flow**: No input is needed. The function returns a pair of strings in `YYYYMMDD` format: the start date and the current UTC date.

**Call relations**: `_paginate_analytics_reports` calls this before issuing report queries. The resulting dates are passed into every analytics report request.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1343–1394)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Runs one analytics report query and pages through its breakdown rows. Some report combinations are not supported by HubSpot, and those are skipped.

**Data flow**: An HTTP client plus report family, subject, time period, optional view filter, and date range go in. The function calls HubSpot with offset paging, converts each response with `_analytics_report_rows`, yields rows, and stops when all breakdowns are read or the query is unsupported.

**Call relations**: `_paginate_analytics_reports` calls this repeatedly for each report combination. It hands raw report responses to `_analytics_report_rows` for shaping.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1397–1472)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Converts one HubSpot analytics report response into flat records. It creates both total rows and breakdown rows with stable IDs and readable metadata.

**Data flow**: A report response and its query context go in. The function pulls totals and breakdowns, builds metric dictionaries, adds report labels, date ranges, filters, and IDs, then returns the list of rows.

**Call relations**: `_paginate_analytics_report_query` calls this after each API response. It uses the connector’s analytics ID and date helpers to keep row values consistent.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1475–1479)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Builds a stable ID for an analytics report row from its identifying parts. It also removes characters that would make IDs awkward.

**Data flow**: Any number of ID parts go in. The function stringifies them, replaces slashes and colons, substitutes `none` for missing parts, joins them, and prefixes the result with `analytics_report:`.

**Call relations**: `_analytics_report_rows` uses this when making total and breakdown rows. Stable IDs let repeated syncs update the same report rows rather than creating duplicates.


##### `HubSpotConnector._analytics_report_date`  (lines 1482–1483)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Converts HubSpot report dates from compact `YYYYMMDD` strings into `YYYY-MM-DD`. This makes dates easier for people and downstream systems to read.

**Data flow**: A date string goes in. The function slices it into year, month, and day and returns the dashed form.

**Call relations**: `_analytics_report_rows` uses this while building analytics report records. It is a small formatting helper for report output.


##### `HubSpotConnector._paginate_event_types`  (lines 1485–1498)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot event type definitions. Event types describe categories of events that can later have occurrences.

**Data flow**: An HTTP client goes in. The function calls the event types endpoint, accepts list or wrapped responses, ensures rows are dictionaries, adds a string `id` when possible, and yields one page.

**Call relations**: `_product_pages` selects this for the event types stream. It stands alone because the endpoint does not need the generic paging loop.


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1500–1517)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot event occurrences, optionally starting after a saved cursor. Occurrences are individual event instances.

**Data flow**: An HTTP client and optional cursor go in. The function sends `occurredAfter` when a cursor exists, reads result rows, yields them, and treats a 404 as an unavailable event surface.

**Call relations**: `_product_pages` selects this for the event occurrences stream, binding in the cursor. It is the stream-specific fetcher for event data.


##### `HubSpotConnector._paginate_email_events`  (lines 1519–1542)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads marketing email events with HubSpot’s offset-style email events API. It can start from a cursor converted into a millisecond timestamp.

**Data flow**: An HTTP client and optional cursor go in. The function converts the cursor with `_email_event_start_timestamp`, requests pages with limit and offset, yields event rows, and stops when HubSpot says there are no more pages.

**Call relations**: `_product_pages` selects this for the email events stream. It delegates only the cursor conversion to `_email_event_start_timestamp`.

*Call graph*: calls 1 internal fn (_email_event_start_timestamp).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1545–1554)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts an email event cursor into the millisecond timestamp format expected by HubSpot’s email event API. It accepts either an existing numeric timestamp or an ISO date-time string.

**Data flow**: A cursor string or nothing goes in. The function returns `None` for no cursor or unparseable values, returns the integer directly for digit strings, or parses a date-time and converts it to milliseconds.

**Call relations**: `_paginate_email_events` calls this before making requests. It lets the stream use readable timestamps while still speaking HubSpot’s older API format.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._paginate_association_labels`  (lines 1556–1572)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the labels HubSpot uses for relationships between object types. Labels explain what a connection means, such as a default association or a named relationship.

**Data flow**: An HTTP client goes in. The function walks all object-type pairs that have labels, converts each label with `_association_label_row`, and yields pages.

**Call relations**: `_product_pages` selects this for the association labels stream. It relies on `_association_pairs_with_labels` to discover useful pairs.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels).


##### `HubSpotConnector._paginate_associations`  (lines 1574–1589)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads actual relationship records between HubSpot objects, such as one contact linked to one company. It discovers valid object-type pairs first, then batch-reads associations.

**Data flow**: An HTTP client goes in. The function loops through object pairs with labels, pages through source object IDs, sends batches to HubSpot, and yields association rows.

**Call relations**: `_product_pages` selects this for the associations stream. It coordinates `_association_pairs_with_labels`, `_paginate_crm_object_id_pages`, and `_paginate_association_batch`.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages).


##### `HubSpotConnector._paginate_association_batch`  (lines 1591–1618)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads associations for a batch of source records and follows any per-record association paging. Some HubSpot associations have more linked records than fit in one batch response.

**Data flow**: An HTTP client, source and target object types, and input IDs go in. The function posts a batch read, converts results to rows, yields them, then builds the next batch inputs from paging cursors until none remain.

**Call relations**: `_paginate_associations` calls this for each page of source IDs. It uses `_association_rows` to shape results, `_next_association_inputs` to continue, and `_is_optional_pair_unavailable` to skip unsupported pairs.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1621–1635)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Finds which association batch inputs need another page. HubSpot can return a separate next cursor for each source record.

**Data flow**: A batch association response goes in. The function scans each result for a source ID and next `after` cursor, then returns new input dictionaries for the follow-up batch request.

**Call relations**: `_paginate_association_batch` calls this after each batch response. It keeps association paging going until every source record has been fully read.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1637–1650)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Discovers object-type pairs that actually have association labels. This narrows association work to pairs HubSpot says are meaningful for the account.

**Data flow**: An HTTP client goes in. The function gets all possible object types, checks every from-to pair for labels, and yields only pairs with non-empty label lists.

**Call relations**: `_paginate_association_labels` and `_paginate_associations` both call this. It uses `_association_object_types` and `_association_labels_for_pair` as its discovery steps.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1652–1665)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the list of HubSpot object types to consider when reading associations. It combines known standard object types with any custom object types for the account.

**Data flow**: An HTTP client goes in. The function starts with a built-in list, tries to fetch custom object schemas, adds their type IDs, and returns the combined list.

**Call relations**: `_association_pairs_with_labels` calls this before testing pairs. It uses `_custom_object_schemas`, `_schema_object_type_id`, and `_is_optional_pair_unavailable` to include custom objects when possible.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1667–1683)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches association labels for one source object type and one target object type. If the pair is not supported, it returns an empty list.

**Data flow**: An HTTP client and two object type names go in. The function calls HubSpot’s labels endpoint, filters result rows to dictionaries, and returns them, or returns an empty list for optional unavailable pairs.

**Call relations**: `_association_pairs_with_labels` calls this while scanning possible pairs. It uses `_is_optional_pair_unavailable` to distinguish harmless unsupported pairs from real failures.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1686–1703)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Normalizes one association label into a stable row. It records the two object types, HubSpot’s label/category fields, and a compound ID.

**Data flow**: A raw label plus source and target object type names go in. The function returns a dictionary with standardized association fields and an ID built from the pair and label identity.

**Call relations**: `_paginate_association_labels` calls this for every label discovered by `_association_pairs_with_labels`.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1705–1717)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Yields pages of CRM record IDs for a given object type. It is useful when another API needs only IDs, not full records.

**Data flow**: An HTTP client and object type go in. The function pages through minimal CRM object records and extracts non-empty IDs as strings.

**Call relations**: `_paginate_associations` uses this to gather source IDs for association batch reads. `_paginate_sequence_enrollments` uses it to gather contact IDs.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1719–1749)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks HubSpot’s basic CRM object list endpoint for selected properties. This is lighter than the full search path when only a small field set is needed.

**Data flow**: An HTTP client, object type, and property names go in. The function requests pages, yields dictionary records, follows the `after` cursor, and returns quietly for optional unavailable objects.

**Call relations**: `_paginate_crm_object_id_pages` and `_paginate_contact_identity_pages` call this. It uses `_is_optional_pair_unavailable` to skip object types that the account cannot access.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1752–1786)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Converts HubSpot’s nested batch association response into flat relationship rows. Each output row represents one source record connected to one target record with one association type.

**Data flow**: A batch response and the two object type names go in. The function walks result entries, target records, and association types, then returns rows built by `_association_row`.

**Call relations**: `_paginate_association_batch` calls this after each batch read. It delegates row construction to `_association_row` for consistent IDs and fields.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1789–1816)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one normalized association row. It creates a stable ID from the source, target, category, and type information.

**Data flow**: Association type data, source and target object names, source and target record IDs, and a fallback index go in. The function returns a flat dictionary describing that relationship.

**Call relations**: `_association_rows` uses this for every individual relationship found inside a batch response. The resulting row is yielded by `_paginate_association_batch`.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1819–1822)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether an API error should be treated as an optional missing feature rather than a hard failure. This is common when probing many HubSpot object pairs or add-on products.

**Data flow**: An HTTP error goes in. The function returns true for 400 or 404 responses, or for permission-style unavailable-stream errors detected by `_is_stream_unavailable`.

**Call relations**: Many optional fan-out routines call this, including association, list membership, consent, sequence, and lightweight CRM object walks. It keeps exploratory reads from breaking the whole sync.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1824–1838)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads which records belong to each HubSpot list. It first discovers lists, then walks memberships for each list.

**Data flow**: An HTTP client goes in. The function pages through lists, extracts each list ID, and yields membership pages from `_paginate_memberships_for_list`.

**Call relations**: `_product_pages` selects this for the list memberships stream. It connects `_paginate_lists` with `_paginate_memberships_for_list`.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1840–1883)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads membership rows for one HubSpot list. Each row links a list to a record in that list.

**Data flow**: An HTTP client, list record, and list ID go in. The function pages through the list memberships endpoint, builds rows with compound IDs and list metadata, yields pages, and stops if the membership endpoint is unavailable for that list.

**Call relations**: `_paginate_list_memberships` calls this for each discovered list. It uses `_is_optional_pair_unavailable` to ignore unsupported list membership reads.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1885–1898)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads HubSpot communication subscription definitions. These describe the categories people can subscribe or unsubscribe from.

**Data flow**: An HTTP client goes in. The function calls the definitions endpoint, accepts either of HubSpot’s likely result keys, assigns each row an ID, and yields rows if present.

**Call relations**: `_product_pages` selects this for the subscription definitions stream. It is a standalone product API reader.


##### `HubSpotConnector._paginate_consent_states`  (lines 1900–1913)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads email consent and unsubscribe status for contacts. It turns per-contact communication preferences into stream rows.

**Data flow**: An HTTP client goes in. The function pages through contact IDs and emails, skips contacts without email, fetches subscription statuses and unsubscribe-all statuses, combines them into pages, and yields them.

**Call relations**: `_product_pages` selects this for consent states. It coordinates `_paginate_contact_identity_pages`, `_consent_status_rows`, and `_unsubscribe_all_rows`.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1915–1931)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches lightweight contact records containing IDs and emails. Consent APIs require an email address, so this prepares the contact list for consent lookups.

**Data flow**: An HTTP client goes in. The function pages through contacts with the email property, lifts the email to the top level, and yields contact pages.

**Call relations**: `_paginate_consent_states` calls this before asking HubSpot for each contact’s communication preferences. It reuses `_paginate_crm_object_pages`.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 1933–1954)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches subscription-specific consent statuses for one contact email. These rows say whether the contact is subscribed or unsubscribed for particular communication types.

**Data flow**: An HTTP client, contact row, and email go in. The function safely URL-encodes the email, calls the statuses endpoint, converts each result with `_consent_row`, and returns the list, or an empty list if the endpoint is unavailable.

**Call relations**: `_paginate_consent_states` calls this for each contact with an email. It uses `_consent_row` for row shaping and `_is_optional_pair_unavailable` for graceful skips.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 1956–1980)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches unsubscribe-all status for one contact email. This captures the broad preference where someone opts out of all email.

**Data flow**: An HTTP client, contact row, and email go in. The function URL-encodes the email, calls the unsubscribe-all endpoint, converts results with `_consent_row`, and returns rows or an empty list for optional unavailable responses.

**Call relations**: `_paginate_consent_states` calls this alongside `_consent_status_rows`. Both feed normalized consent records into the same stream.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 1983–2014)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Builds one normalized consent-state row from a HubSpot communication preference response. It adds contact identity, email, purpose, status, source, and timestamps.

**Data flow**: A raw consent row, contact, email, and status kind go in. The function creates a compound ID, fills human-meaningful fields, preserves original data, and returns the completed row.

**Call relations**: `_consent_status_rows` and `_unsubscribe_all_rows` call this for every consent result. `record_identity` later gives consent rows a stable deduping identity.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2016–2043)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sales sequences for each HubSpot user found through owners. Sequences are fetched per user because the API is user-scoped.

**Data flow**: An HTTP client goes in. The function gets user rows, requests sequences for each user ID, adds owner context to each sequence row, yields pages, and skips users whose sequence endpoint is unavailable.

**Call relations**: `_product_pages` selects this for the sequences stream. It uses `_sequence_user_rows`, `_paginate_get_collection`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows).


##### `HubSpotConnector._sequence_user_rows`  (lines 2045–2068)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a list of unique HubSpot user IDs from owner records. This prepares the connector to query user-scoped sequence APIs.

**Data flow**: An HTTP client goes in. The function pages through owners, keeps each unique `userId` once, attaches owner ID and email, and yields one page of user rows.

**Call relations**: `_paginate_sequences` calls this before reading sequences. It reuses `_paginate_get_collection` to fetch owners.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2070–2088)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sequence enrollment information for contacts. An enrollment says a contact is or was enrolled in a sales sequence.

**Data flow**: An HTTP client goes in. The function pages through contact IDs, calls the sequence enrollment endpoint for each contact, converts responses with `_sequence_enrollment_rows`, and yields pages.

**Call relations**: `_product_pages` selects this for sequence enrollments. It uses `_paginate_crm_object_id_pages`, `_sequence_enrollment_rows`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2091–2105)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes sequence enrollment responses for one contact. It handles both wrapped result lists and single-object responses.

**Data flow**: A response dictionary and contact ID go in. The function chooses the raw rows, assigns each row an ID when missing, adds the contact ID, and returns the list.

**Call relations**: `_paginate_sequence_enrollments` calls this after each contact enrollment request. The returned rows become pages for the sequence enrollments stream.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2107–2136)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads submissions for every HubSpot form. It first lists forms, then fetches submissions under each form.

**Data flow**: An HTTP client goes in. The function pages through forms, finds each form ID, pages through its submissions, assigns each submission a stable ID, adds form ID and name, and yields pages.

**Call relations**: `_product_pages` selects this for form submissions. It uses `_paginate_get_collection` both for listing forms and reading submissions.

*Call graph*: calls 1 internal fn (_paginate_get_collection).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2138–2153)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages inside HubSpot conversation threads. Threads and messages are separate API levels, so the connector walks from thread to messages.

**Data flow**: An HTTP client goes in. The function pages through conversation threads, then pages through messages for each thread, adds the thread ID to each message, and yields pages.

**Call relations**: `_product_pages` selects this for conversation messages. It reuses `_paginate_get_collection` for both thread and message list endpoints.

*Call graph*: calls 1 internal fn (_paginate_get_collection).


##### `HubSpotConnector._paginate_pipelines`  (lines 2155–2162)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads pipeline definitions for deals and tickets. Pipelines describe the stages used to track work.

**Data flow**: An HTTP client goes in. The function asks `_pipeline_rows_for_object_type` for each supported object type and yields non-empty pages.

**Call relations**: `_product_pages` selects this for the pipelines stream. It delegates object-specific shaping to `_pipeline_rows_for_object_type`.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2164–2214)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the stages inside deal and ticket pipelines. It turns nested stage definitions into their own rows.

**Data flow**: An HTTP client goes in. The function fetches raw pipelines for each object type, walks their `stages`, builds compound IDs and fields like status, probability, display order, and closed state, then yields pages.

**Call relations**: `_product_pages` selects this for pipeline stages. It uses `_raw_pipelines_for_object_type` to get the nested source data.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2216–2239)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes pipeline rows for one HubSpot object type, such as deals or tickets. It adds an object kind and active/archived status.

**Data flow**: An HTTP client and object type go in. The function fetches raw pipelines, skips rows without IDs, builds compound IDs and readable names, and returns the list.

**Call relations**: `_paginate_pipelines` calls this for each supported pipeline object type. It relies on `_raw_pipelines_for_object_type` for the API read.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2241–2252)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches raw pipeline data for one object type from HubSpot. It treats missing or forbidden pipeline endpoints as empty.

**Data flow**: An HTTP client and object type go in. The function calls the pipelines endpoint, returns dictionary rows from `results`, returns an empty list for 403 or 404, and re-raises other errors.

**Call relations**: `_pipeline_rows_for_object_type` and `_paginate_pipeline_stages` both call this. It centralizes the raw pipeline API request.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2255–2259)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects a specific HubSpot error meaning archived-object paging is not supported for that object. This lets the connector skip only the deletion sweep while keeping active records.

**Data flow**: An HTTP error goes in. The function checks for status 400 and looks for HubSpot’s known archived paging message, returning true if it matches.

**Call relations**: `_paginate_archived_ids` and `_paginate_custom_object_archived_ids` call this when their archived sweeps fail. It uses `_upstream_message` to read HubSpot’s message.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2262–2270)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Safely extracts the `message` field from a HubSpot error response. It returns nothing if the response is not usable JSON or has no message.

**Data flow**: An HTTP error goes in. The function tries to parse the response body as JSON, checks it is a dictionary, and returns the message as text when present.

**Call relations**: `_is_archived_sweep_unsupported` uses this to recognize HubSpot’s archived paging limitation. It is a small helper for interpreting upstream errors.


##### `HubSpotConnector._paginate_junction`  (lines 2272–2319)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds simple relationship streams for selected parent-target pairs, such as deals to contacts. It reads associations embedded in the parent object list response.

**Data flow**: An HTTP client, parent object name, and target object name go in. The function pages through parent records with `associations` requested, turns each embedded target link into a flat row with a compound ID, and yields pages until no next cursor remains.

**Call relations**: `_paginate_unchecked` calls this for junction streams. These rows are full-refreshed rather than cursor-based because HubSpot does not expose modification timestamps for these relationships.

*Call graph*: called by 1 (_paginate_unchecked).


### `extensions/sources/ufo_ext_sources/providers/intercom.py`

`io_transport` · `source sync runs`

Intercom does not expose all of its data in one simple way. Some records are found through a search endpoint, some through a scrolling company endpoint, some through simple list endpoints, and some must be fetched by first reading a parent record and then asking for its children. This file is the adapter that hides those differences from the rest of the project.

Think of it like a tour guide for a large building with several kinds of doors. The rest of the system asks for “contacts” or “conversation parts”; this connector knows which Intercom door to use, what request to send, how to ask for the next page, and when there are no more records.

The file defines the Intercom streams the system can read, adds the required Intercom API version header, and translates each stream into pages of dictionaries. It also flattens a few nested Intercom fields into simpler top-level fields, so later database or analysis steps do not need to dig through deeply nested JSON. For incremental syncing, it treats Intercom’s time cursor fields, such as updated_at, carefully: Intercom sends them as Unix-second integers, but this sync layer stores cursors as strings, so the connector converts them when needed.

If Intercom refuses access with a permission error, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 52–66)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates a description of one Intercom stream, such as its name, source object, primary key, and cursor field. It keeps the stream list concise and consistent.

**Data flow**: It takes a stream name and optional details like the source object name, primary key, cursor field, and whether it is a main canonical stream. It fills in sensible defaults, then returns a StreamSpec object that the connector uses later to know how to sync that stream.

**Call relations**: It is used while building the file’s Intercom stream list. Its only direct handoff is to StreamSpec.__init__, which creates the stream description object consumed by the connector.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector.record_identity`  (lines 101–105)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This decides what stable identity should represent a record when the system stores or refers to it. Attribute streams need special treatment because their useful identifier can be either an id or a full_name.

**Data flow**: It receives one Intercom record and the stream it came from. For normal streams, it lets the parent connector decide; for company_attributes and contact_attributes, it looks for id first, then full_name, and returns it as text if it is usable.

**Call relations**: The broader source framework calls this when it needs a record’s identity. This method only steps in for Intercom attribute records; all other streams are handed back to the base RestConnector behavior.


##### `IntercomConnector.record_ref`  (lines 107–111)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This chooses a human-readable reference for records where the name is more useful than an id, such as tags, teams, and attributes. That makes stored pages easier to recognize later.

**Data flow**: It receives a record and its stream. For tags, teams, company_attributes, and contact_attributes, it reads the record’s name and returns it as text; for other streams, it delegates to the parent connector’s normal reference logic.

**Call relations**: The source framework asks for this when it wants a display-style reference for a synced record. This method provides Intercom-specific naming for a few streams and otherwise relies on the shared RestConnector behavior.


##### `IntercomConnector._make_client`  (lines 113–116)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Intercom and adds the Intercom API version header. Without that header, Intercom may interpret requests using a different API version.

**Data flow**: It receives the base URL and credential. It asks the parent connector to create an authenticated httpx.AsyncClient, adds Intercom-Version: 2.11 to the client headers, and returns the ready-to-use client.

**Call relations**: The source framework uses this during connector setup. It builds on the base RestConnector client creation, then adds the Intercom-specific requirement before any pagination methods make requests.


##### `IntercomConnector._build_search_body`  (lines 119–149)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: This builds the JSON request body for Intercom search endpoints. It tells Intercom how many records to return, where to continue from, and which records are newer than the saved cursor.

**Data flow**: It takes a stream, the saved cursor from a previous sync, and an optional page cursor called starting_after. It creates a body with pagination, sorting, and a query filter; if the saved cursor looks like a number, it sends it as a number because Intercom expects that for time fields. The result is a dictionary ready to send in a POST request.

**Call relations**: IntercomConnector._paginate_search uses it for regular search streams like conversations, contacts, and tickets. IntercomConnector._paginate_conversation_parts also uses it to find the parent conversations before fetching their parts.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 152–157)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: This small helper safely picks the first dictionary from a list. It is used when Intercom wraps related records, such as contacts or companies, inside nested lists.

**Data flow**: It receives any value. If the value is a non-empty list and its first item is a dictionary, it returns that first dictionary; otherwise it returns nothing.

**Call relations**: It supports the flattening helpers that need the first related contact or company. Those helpers use it to avoid errors when Intercom sends missing, empty, or unexpectedly shaped data.


##### `IntercomConnector._flatten_conversation`  (lines 160–177)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes conversation records easier for later steps to use by copying important nested fields to simple top-level keys. It pulls out source details and the first requester contact id.

**Data flow**: It receives one conversation dictionary and copies it. If the conversation has a source object, it adds source__type, source__subject, and source__body. If it has a contacts wrapper with at least one contact, it adds requester_id from that first contact. It returns the enriched copy without changing the original record directly.

**Call relations**: IntercomConnector.flatten calls this only for the conversations stream. Its output then continues through the normal flatten path, including possible cursor conversion.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 180–189)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes a conversation part record easier to query by lifting the author’s type and id into top-level fields. It leaves the conversation_id alone because pagination adds that parent id earlier.

**Data flow**: It receives one conversation part dictionary and copies it. If the record has an author object, it adds author_type and author_id from that object. It returns the copied record with those extra simple fields.

**Call relations**: IntercomConnector.flatten calls this for the conversation_parts stream. The records it receives often came from IntercomConnector._paginate_conversation_parts, which stamps each part with its parent conversation id before flattening happens.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 192–200)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes contact records easier to connect to companies by copying the first associated company id to org_id. That saves later code from having to understand Intercom’s nested company wrapper.

**Data flow**: It receives one contact dictionary and copies it. If the contact has a companies wrapper with at least one company, it reads that company’s id or company_id and stores it as org_id. It returns the copied record.

**Call relations**: IntercomConnector.flatten calls this only for the contacts stream. Its result then goes through the shared cursor conversion step if the stream has a cursor field.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 202–216)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This is the connector’s final cleanup step for each record before the rest of the sync system sees it. It flattens selected nested Intercom fields and converts integer cursor values into strings for the sync watermark system.

**Data flow**: It receives a raw record and the stream it belongs to. Depending on the stream name, it sends the record through the conversation, conversation part, or contact flattening helper. Then, if the stream has a cursor field and that field is an integer, it returns a copy where that cursor is stored as text; otherwise it returns the record as-is.

**Call relations**: The source framework calls this after records are fetched. It coordinates IntercomConnector._flatten_conversation, IntercomConnector._flatten_conversation_part, and IntercomConnector._flatten_contact, then hands a cleaner record back to the shared sync pipeline.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 218–234)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main record-reading entry for a stream. It yields pages of Intercom records and turns permission refusals into a clean “stream skipped” result instead of a hard failure.

**Data flow**: It receives an HTTP client, a stream description, and the saved cursor. It asks IntercomConnector._stream_pages for pages and yields each page onward. If Intercom returns HTTP 401 or 403, meaning unauthorized or forbidden, it raises StreamSkipped with a clear message; other HTTP errors are re-raised.

**Call relations**: The source runtime calls this when it wants to sync one Intercom stream. This method delegates the actual paging choice to IntercomConnector._stream_pages and uses StreamSkipped.__init__ when Intercom says the connected account lacks permission.

*Call graph*: calls 2 internal fn (__init__, _stream_pages).


##### `IntercomConnector._stream_pages`  (lines 236–254)

```
def _stream_pages(self, client: httpx.AsyncClient, stream: StreamSpec, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This chooses the correct paging strategy for a stream. Intercom has several different API shapes, so this function acts like a dispatcher that sends each stream to the right reader.

**Data flow**: It receives the HTTP client, stream, and cursor. It checks the stream name against known groups: search endpoints, company scroll, simple list endpoints, data attributes, conversation parts, company segments, and activity logs. It returns the matching asynchronous page iterator, or raises an error if no strategy exists.

**Call relations**: IntercomConnector.paginate calls this before any records are read. Depending on the stream, it hands control to IntercomConnector._paginate_search, _paginate_scroll, _paginate_list, _paginate_attributes, _paginate_conversation_parts, _paginate_company_segments, or _paginate_activity_logs.

*Call graph*: calls 7 internal fn (_paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search); called by 1 (paginate).


##### `IntercomConnector._paginate_search`  (lines 256–276)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams that use Intercom’s search API, such as conversations, contacts, and tickets. It keeps asking for the next search page until Intercom says there is no next page.

**Data flow**: It receives the HTTP client, stream, and saved cursor. For each loop, it builds a search body with IntercomConnector._build_search_body, sends a POST request to the right search path, extracts the records from the stream-specific response key, yields them if present, and updates starting_after from the response’s next-page information. When there is no next cursor, it stops.

**Call relations**: IntercomConnector._stream_pages calls this for streams listed in the search-path table. It relies on IntercomConnector._build_search_body to keep pagination and incremental filtering consistent.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (_stream_pages).


##### `IntercomConnector._paginate_scroll`  (lines 278–292)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads companies using Intercom’s scroll API, which is a special paging style for walking through company records. It follows the scroll token returned by Intercom until the data runs out.

**Data flow**: It starts without a scroll_param. Each time through the loop, it sends a GET request to /companies/scroll, including the scroll_param after the first page. It extracts the data list, yields it if non-empty, then reads the next scroll_param. It stops when there are no records or no next scroll token.

**Call relations**: IntercomConnector._stream_pages calls this for the companies stream. It is separate from the search paginator because Intercom does not use the same search cursor shape for company scrolling.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_list`  (lines 294–306)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads simple list-style Intercom endpoints, such as admins, tags, teams, and segments. These endpoints return one response rather than a multi-page search or scroll flow.

**Data flow**: It receives the client and stream, looks up the stream’s endpoint path, and sends one GET request. It then looks for a list under either the stream name or data. If it finds a non-empty list, it yields that list once and then finishes.

**Call relations**: IntercomConnector._stream_pages calls this for streams in the list-path table. It does not call any other connector paging helpers because these endpoints are simple one-shot reads.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_attributes`  (lines 308–317)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Intercom data attribute definitions for companies or contacts. These are metadata fields that describe what custom data can exist on those objects.

**Data flow**: It receives the client and stream, maps the stream name to the Intercom model name, and sends a GET request to /data_attributes with that model as a parameter. It filters the returned data so only dictionary records remain, yields them if any exist, and then stops.

**Call relations**: IntercomConnector._stream_pages calls this for company_attributes and contact_attributes. The mapping table decides whether the request asks Intercom for company attributes or contact attributes.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_conversation_parts`  (lines 319–351)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the individual messages or events inside conversations. Intercom does not expose these as a simple independent search stream here, so the connector first searches conversations and then fetches each conversation’s detail page.

**Data flow**: It receives the client and saved cursor. It finds the conversations stream definition, builds a search request with IntercomConnector._build_search_body, and retrieves conversations page by page. For each conversation with an id, it sends a GET request for that conversation’s details, extracts conversation_parts, stamps each part with conversation_id if missing, and yields the parts. It advances through conversation search pages using starting_after until none remains.

**Call relations**: IntercomConnector._stream_pages calls this for the conversation_parts stream. It uses IntercomConnector._build_search_body because it begins by searching parent conversations with the same cursor rules as the conversations stream.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (_stream_pages).


##### `IntercomConnector._paginate_company_segments`  (lines 353–377)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the segment memberships for each company. It first walks through companies, then asks Intercom which segments each company belongs to.

**Data flow**: It starts with no scroll_param and repeatedly reads /companies/scroll. For every company with an id, it sends a GET request to /companies/{id}/segments, extracts the returned segment records, stamps each segment with company_id if missing, and yields the segment list when present. It follows Intercom’s scroll_param until there are no more companies or no next token.

**Call relations**: IntercomConnector._stream_pages calls this for the company_segments stream. It combines the company scroll style with per-company detail requests because segment records are reached through their parent company.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_activity_logs`  (lines 379–401)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads admin activity logs, optionally starting after a saved created_at cursor. It follows Intercom’s next-page links until there are no more logs.

**Data flow**: It receives the client and cursor. If a cursor exists, it sends it as created_at_after on the first request to /admins/activity_logs. For each response, it yields the activity_logs list if present, then looks for a pages.next value. If the next value is a full URL, it strips the base Intercom URL so the client can request the path; if there is no next link, it stops.

**Call relations**: IntercomConnector._stream_pages calls this for the activity_logs stream. It is its own paginator because activity logs use next links rather than the search API’s starting_after body field or the company scroll token.

*Call graph*: called by 1 (_stream_pages).


### Enterprise CRM and help centers
Salesforce and Zendesk round out the stage with enterprise CRM objects, cases, tickets, users, organizations, and knowledge/community content.

### `extensions/sources/ufo_ext_sources/providers/salesforce.py`

`io_transport` · `active during Salesforce source sync runs`

Salesforce stores customer data in objects called SObjects, which are like database tables for things such as Account or Contact. This file defines a read-only connector for those objects. Without it, the system would not know which Salesforce records to ask for, how to page through large result sets, or how to notice records that were deleted after a previous sync.

The file starts by listing the Salesforce streams the system understands. Each stream says the Salesforce object name, the record ID field, and the timestamp field used as a cursor. A cursor is a bookmark: it lets the next sync ask only for records changed after the last successful point.

The main class, SalesforceConnector, talks to Salesforce through its REST API. Before querying an object, it asks Salesforce to describe that object, so it can discover the available fields instead of relying on a hard-coded schema. It then builds a SOQL query, which is Salesforce’s query language, and follows Salesforce’s pagination links until all matching records have been read.

If this is not the first sync, it also asks Salesforce for records deleted since the cursor and emits those as tombstones, meaning “this record used to exist, but should now be treated as gone.” If Salesforce refuses access with a permission or authentication error, the stream is skipped with a clear message rather than silently failing.

#### Function details

##### `_stream`  (lines 29–38)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates the description of one Salesforce stream, such as accounts or contacts. It saves repeated setup by filling in the standard Salesforce fields used for identity and change tracking.

**Data flow**: It receives a friendly stream name, the Salesforce object name, and whether the stream is considered canonical. It packages those details with standard fields like Id, CreatedDate, and SystemModstamp into a StreamSpec. The result is a small stream definition used later by the connector.

**Call relations**: At file load time, the Salesforce stream list calls this helper many times to build the catalog of Salesforce objects. Each call creates a StreamSpec, which the connector exposes through its streams_list.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 78–82)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: This builds the Salesforce query text used to fetch records for one stream. It includes all discovered fields, optionally filters by the sync cursor, and orders results so progress is stable.

**Data flow**: It receives a stream definition, a list of field names, and an optional cursor. It joins the fields into a SELECT query, adds a WHERE clause if there is a previous cursor, adds ordering by the cursor field, and limits the page size. It returns the completed SOQL query string.

**Call relations**: paginate calls this after it has discovered the object’s fields. The returned query is then sent to Salesforce’s query endpoint to begin reading records.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 84–90)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: This asks Salesforce what fields exist on a given object. That matters because Salesforce organizations can customize objects, so the connector should read what the organization actually exposes instead of assuming a fixed layout.

**Data flow**: It receives an HTTP client and a Salesforce object name. It calls Salesforce’s describe endpoint, reads the returned field list, keeps entries that look valid, and returns just the field names as strings.

**Call relations**: paginate calls this before building the query. Its field list is handed to _build_soql so the query includes the object’s real columns.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 92–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main reader for one Salesforce stream. It fetches records page by page, follows Salesforce’s next-page links, and, on incremental syncs, also reports deleted records.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor from a previous run. It first discovers fields, builds a SOQL query, sends the query to Salesforce, yields each non-empty batch of records, and follows nextRecordsUrl until Salesforce says the query is done. If a cursor was supplied, it then asks for deletions since that cursor and may yield a StreamPage containing delete tombstones. If Salesforce returns a 401 or 403 refusal, it turns that into a StreamSkipped error with a human-readable reason; other HTTP errors continue upward.

**Call relations**: This function is the connector’s central flow for reading data. It calls _describe_fields to learn the schema, _build_soql to prepare the query, and _deleted_page to capture removals after normal records have been read. When permission is denied, it creates StreamSkipped so the wider sync runner can skip that stream cleanly.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 121–139)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: This checks Salesforce for records that were hard-deleted since the last cursor. It lets the system remove or mark missing records without doing a full fresh copy of the entire object.

**Data flow**: It receives an HTTP client, a stream definition, and the previous cursor time. It chooses the current time as the end of the deletion window, calls Salesforce’s deleted-records endpoint, extracts deleted record IDs, and chooses the next cursor from Salesforce’s latest covered date or from the chosen end time. It returns a StreamPage containing the deleted IDs and next cursor, or None when there is nothing useful to return.

**Call relations**: paginate calls this only after it has finished reading changed records and only when a prior cursor exists. The StreamPage it creates is yielded back to the sync runner as a tombstone page.

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 141–144)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This removes Salesforce’s metadata wrapper from each record. Salesforce includes an attributes field describing the API object, but the rest of the sync wants the actual business fields.

**Data flow**: It receives one record dictionary and its stream definition. If the record contains an attributes key, it returns a copy without that key; otherwise it returns the original record unchanged. The output is the cleaned record that downstream code can treat as ordinary field data.

**Call relations**: No direct caller is shown in the provided call facts, but this method is a connector hook used as part of record preparation. It sits after records are fetched and before they are stored or passed further through the source-sync pipeline.


### `extensions/sources/ufo_ext_sources/providers/zendesk.py`

`io_transport` · `source sync / request handling`

Zendesk exposes its data through many web API endpoints, and those endpoints do not all behave the same way. This file is the adapter that hides those differences. To the rest of the system, Zendesk looks like a set of named streams, such as “tickets” or “articles,” each producing batches of records.

The file first defines the list of Zendesk streams and the basic details for each one: where it comes from, what field acts as its ID, and what date field can be used as a cursor. A cursor is like a bookmark: it tells the next sync where the last one stopped.

The `ZendeskConnector` then chooses the right reading method for each stream. Large, frequently changing objects use Zendesk’s incremental export API, which starts from a timestamp and follows “next” links until Zendesk says the stream is finished. Simpler objects use normal page-by-page reading. Two streams need special treatment: ticket comments are buried inside ticket event data, and user identities require asking for identities separately for each user.

If Zendesk rejects a request because the credential is invalid or lacks permission, this connector marks that stream as skipped instead of pretending the data is empty. It only reads data; there is no write or update path here.

#### Function details

##### `_stream`  (lines 58–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a standard description of one Zendesk stream. It keeps the long stream list readable by filling in common defaults, such as using `id` as the main identifier and `updated_at` as the usual change-tracking field.

**Data flow**: It receives a stream name plus optional details like the Zendesk API object name, primary key, cursor field, and whether the stream is considered canonical. It combines those values with sensible defaults and returns a `StreamSpec`, which is the system’s compact recipe for reading that stream.

**Call relations**: This function is used while the file is being loaded to build `ZENDESK_STREAMS`. Each call hands its stream recipe to `StreamSpec`, and the resulting list is attached to `ZendeskConnector` so the broader source runner knows what Zendesk data is available.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 142–167)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: This helper copies useful information from extra records Zendesk sends alongside the main records. In this file, it is used to attach user email addresses to ticket records when Zendesk returns users together with tickets.

**Data flow**: It receives the main records, the full API page, and instructions for how to match an ID on a record to an item in a sideloaded list. It builds a lookup table, finds matching related records, and writes fields such as requester, submitter, or assignee email directly onto each ticket record. It changes the records in place and returns nothing.

**Call relations**: `ZendeskConnector._paginate_incremental_cursor` calls this after downloading an incremental ticket page that includes sideloaded users. The helper enriches the ticket records before they are yielded to the rest of the sync process.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 176–177)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This small helper decides which JSON field in a Zendesk response contains the actual list of records for a stream. Most streams use their own name, but some Zendesk endpoints use a different label.

**Data flow**: It receives a stream description. It checks a table of special cases, such as `ticket_audits` using `audits`, and returns the response field name that should be read.

**Call relations**: `ZendeskConnector._paginate_default` calls this before reading ordinary paginated endpoints. This lets default pagination work even when Zendesk’s response name does not exactly match the system’s stream name.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 180–192)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: This helper turns the system’s saved cursor into the Unix timestamp format Zendesk’s incremental API expects. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor that may be missing, already numeric, or written as an ISO date string. Missing or unreadable values become `0`, numeric text becomes an integer, and date text is parsed and converted to seconds. The result is always an integer start time.

**Call relations**: The incremental pagination methods call this when building their first Zendesk request. It gives `ZendeskConnector._paginate_incremental_cursor`, `ZendeskConnector._paginate_ticket_comments`, and `ZendeskConnector._paginate_user_identities` a safe start point for “only give me changes since then” requests.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 195–204)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: This helper turns Zendesk’s full next-page URL into just the path and query string needed by the connector’s request method. It is a small safety step that keeps paging requests relative to the configured Zendesk base URL.

**Data flow**: It receives a `next_page` or `after_url` value from Zendesk. If the value is empty or has no path, it returns nothing. Otherwise, it extracts the URL path and keeps the query string, producing a request path such as `/api/v2/...?...`.

**Call relations**: All pagination methods call this after each page to find the next page to request. It is the common bridge between Zendesk’s paging links and the connector’s `_get` calls.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 206–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a Zendesk stream. It looks at the stream name and chooses the correct paging strategy, then yields batches of records back to the source runner.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It sends the work to the special ticket comments reader, the user identities reader, the incremental cursor reader, or the default page reader. It yields each batch those helpers produce. If Zendesk responds with 401 or 403, meaning unauthorized or forbidden, it raises `StreamSkipped` with a clear explanation.

**Call relations**: The source framework calls this when it wants records for a Zendesk stream. `paginate` then delegates to the right private pagination method and passes each resulting page onward. It is also where permission failures are translated into a controlled “skip this stream” outcome.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 232–253)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads high-volume Zendesk streams through the incremental cursor API. It is used for data that can be large and changes often, such as tickets, users, organizations, and ticket metric events.

**Data flow**: It receives an HTTP client, a stream description, and a cursor. It converts the cursor to a Zendesk start time, builds the first incremental API path, downloads each page, extracts records, optionally enriches tickets with sideloaded user emails, and yields non-empty batches. After each page, it follows Zendesk’s `after_url` or `next_page` until Zendesk says the stream has ended.

**Call relations**: `ZendeskConnector.paginate` calls this for streams listed as incremental cursor streams. Inside the loop it relies on `_cursor_to_unix` to start in the right place, `_apply_sideload` to improve ticket records when needed, and `_next_page_path` to continue through Zendesk’s paging links.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 255–265)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads Zendesk endpoints that use ordinary page-by-page navigation. It is the fallback for streams that do not need the special incremental or fan-out behavior.

**Data flow**: It receives an HTTP client and a stream description. It builds the first `/api/v2/...json` request, asks `_data_field` which response field contains the records, downloads pages, yields non-empty record batches, and follows `next_page` until there are no more pages.

**Call relations**: `ZendeskConnector.paginate` calls this when no special stream rule applies. It uses `_data_field` to read the right list from each response and `_next_page_path` to move from one Zendesk page to the next.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 267–299)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method extracts ticket comments from Zendesk’s ticket event feed. Zendesk does not read comments here as a simple top-level list, so the connector has to pull comment child events out of larger ticket event records.

**Data flow**: It receives an HTTP client and an optional cursor. It starts the incremental ticket events request from that cursor, asks Zendesk to include comment events, then walks through each event’s child events. For each child event whose type is `Comment`, it copies the comment, adds the parent `ticket_id`, normalizes numeric creation times into readable timestamp strings, and yields batches of comments. It follows paging links until the event stream ends.

**Call relations**: `ZendeskConnector.paginate` calls this only for the `ticket_comments` stream. The method uses `_cursor_to_unix` to start from the saved sync point and `_next_page_path` to keep following Zendesk’s event pages.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate); 1 external calls (fromtimestamp).


##### `ZendeskConnector._paginate_user_identities`  (lines 301–326)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads identity records for users, such as login or email identity entries. Zendesk exposes identities under each individual user, so the connector first reads users and then asks for identities user by user.

**Data flow**: It receives an HTTP client and an optional cursor. It reads changed users from the incremental users API, skips malformed user entries, and for each valid user ID requests `/users/{id}/identities.json`. It yields any identity batches it finds, follows identity pages for each user, and then moves to the next page of users until Zendesk says the user stream is finished.

**Call relations**: `ZendeskConnector.paginate` calls this for the `users_identities` stream. It uses `_cursor_to_unix` to choose which users to inspect and `_next_page_path` for both the outer user paging and the inner identity paging.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).
