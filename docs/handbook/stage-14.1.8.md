# Horizontal Data, Scheduling, and Form Source Connectors  `stage-14.1.8`

This stage is a set of behind-the-scenes connectors that bring in common business data from outside services. It is not the main work of the product by itself. Instead, it feeds the rest of the system with clean, repeatable records that can be stored, searched, and reused later.

The Airtable connector reads from Airtable, a spreadsheet-like database tool. It asks Airtable’s web API, meaning its online data access doorway, what bases, tables, and records exist. It then breaks that data into streamable pages so the system can process it in manageable chunks.

The Calendly connector reads scheduling data. It collects users, event types, meetings, and invitees, then reshapes Calendly’s responses into a common record format.

The Typeform connector reads form and intake data. It gathers forms, responses, workspaces, themes, images, and webhook settings, which are rules for sending updates when something happens.

Together, these connectors act like import adapters. Each speaks a different outside service’s language, then hands the system data in a familiar shape.

## Files in this stage

### Horizontal Source Connectors
General-purpose connectors that stream structured business data, scheduling records, and form/intake responses into the system.

### `extensions/sources/ufo_ext_sources/airtable.py`

`io_transport` · `during Airtable source sync`

Airtable data is organized like a set of workspaces: a base contains tables, and each table contains records. There is no single Airtable endpoint that simply says “give me everything,” so this connector has to walk the structure in order. First it asks Airtable for the list of bases. Then, for each base, it asks for that base’s tables. Finally, for each table, it asks for the records inside it.

This file exists so new Airtable tables can be picked up automatically during the next sync, without someone editing configuration by hand. It also adds helpful context to each item it reads. For example, a table is marked with the base it came from, and a record is marked with both its base and table. That context matters later, because a record ID alone is not enough to understand where the record belongs.

The connector reads only; it does not create or update Airtable data. It uses Airtable’s paged record API, where Airtable returns a batch of records plus an “offset” token that means “ask again from here.” Think of it like reading a long book with a bookmark: each request reads one chunk, and the bookmark tells the connector where to continue.

#### Function details

##### `AirtableConnector._bases`  (lines 39–41)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Airtable for the list of bases the authenticated user can access. A base is Airtable’s top-level container, similar to a workbook that contains multiple tables.

**Data flow**: It receives an HTTP client that already knows how to make web requests. It sends a request to Airtable’s metadata endpoint for bases, extracts the list stored under the “bases” field, and returns that list as plain dictionaries.

**Call relations**: The main pagination flow calls this whenever it needs to start from the top of the Airtable structure. It relies on the shared records_at helper to pull the useful list out of Airtable’s response before handing those bases back to paginate.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 43–50)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This asks Airtable for the tables inside one specific base. It also labels each table with the base it came from, so later steps do not lose that parent information.

**Data flow**: It receives an HTTP client and one base record. It reads the base’s ID; if the ID is missing or unusable, it returns an empty list. Otherwise it requests that base’s table metadata, extracts the table list, adds the base ID and base name to each table, and returns the enriched table records.

**Call relations**: The pagination flow calls this after it has found bases. It uses records_at to find the table list in Airtable’s response and with_context to attach base details before the tables are passed onward, either as table results or as the next step toward reading records.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 52–70)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the actual rows, or records, from one Airtable table. It yields them in batches because a table may contain more records than Airtable will return in one response.

**Data flow**: It receives an HTTP client, a base ID, and a table record. It checks that the table has a valid ID. If so, it repeatedly asks Airtable for pages of records from that table, following Airtable’s offset bookmark between pages. For each non-empty batch, it adds the base ID, table ID, and table name to every record, then yields that batch to the caller.

**Call relations**: The pagination flow calls this after it has discovered a base and one of its tables. This function uses the connector’s shared cursor-paging machinery to do the repeated web requests, then uses with_context so downstream code can tell exactly which table each record came from.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 72–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for Airtable syncs. Given a requested stream — bases, tables, or records — it decides which Airtable API calls are needed and yields the results in pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. For the bases stream, it fetches bases and yields them once. For the tables stream, it fetches all bases, then fetches tables for each base and groups them into batches of about 100. For the records stream, it walks bases, then tables, then record pages, yielding each batch as it arrives. If asked for an unknown stream, it raises a StreamSkipped signal to say this connector does not implement it.

**Call relations**: The wider source-sync system calls this when it wants data from Airtable. paginate then calls _bases, _tables_for_base, and _records_for_table in the correct order, like moving through nested folders. If the stream name is not one of the known Airtable streams, it hands back a clear skip signal instead of pretending it can read it.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 103–125)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes Airtable objects into a cleaner, more consistent form before the rest of the system stores or displays them. It adds useful fields such as API URLs and normalizes record fields.

**Data flow**: It receives one Airtable record and the stream it belongs to. For bases, it keeps the original data and adds a direct metadata API URL. For tables, it adds the table’s API URL using the saved base ID. For records, it copies the Airtable ID, renames createdTime to created_at, and makes sure fields is a dictionary even if Airtable sent something unexpected. It returns the adjusted record without changing the original stream choice.

**Call relations**: After paginate has produced raw Airtable items, the connector framework can call flatten to prepare each item for downstream use. Unlike the paging functions, this does not make web requests; it is the cleanup step that makes Airtable data easier for the rest of the system to understand.


### `extensions/sources/ufo_ext_sources/calendly.py`

`io_transport` · `during Calendly source sync`

Calendly is organized around an account’s current organization, so this connector starts by asking Calendly who the signed-in user is and which organization they belong to. Once it has that organization, it can fetch organization-wide lists such as event types, groups, memberships, and scheduled events. Think of it like first checking which office building your badge belongs to, then reading the correct notice boards inside that building.

The file defines the streams, meaning the named sets of records this connector can read. Some streams support incremental syncing: instead of rereading everything every time, they ask Calendly only for records updated or created after a saved point in time. That makes repeated syncs faster and avoids unnecessary work.

Calendly returns large lists in pages, so the connector follows Calendly’s “next page” token until there are no more pages. Invitees need extra work: Calendly exposes them under each scheduled event, so the connector first reads events, extracts each event’s ID from its URI, then asks for invitees for that event.

The file also reshapes some records before they leave the connector. For example, scheduled events get friendly fields like title, start_at, and end_at, and organization memberships copy out a member’s name and email while removing the nested user object so unrelated profile changes do not make the membership record look changed.

#### Function details

##### `_uuid_from_uri`  (lines 61–64)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: This helper pulls the final ID-like piece out of a Calendly URI. It is used when the connector needs the short event identifier that Calendly requires for invitee API calls.

**Data flow**: It receives any value that might be a URI. If the value is not a non-empty string, it returns nothing. If it is a string, it trims any trailing slash, takes the text after the final slash, and returns that as the extracted ID.

**Call relations**: When invitees are being fetched, CalendlyConnector._invitees reads each scheduled event’s URI and asks this helper to turn it into the event ID needed for the next API request.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 72–75)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: This asks Calendly for the account connected to the current credentials. The connector needs this because the user response tells it which Calendly organization to sync.

**Data flow**: It receives an HTTP client that already knows how to make requests. It calls Calendly’s /users/me endpoint, looks for the resource object in the response, and returns that object if it is a dictionary-like record. If the response is missing or shaped unexpectedly, it returns an empty record.

**Call relations**: CalendlyConnector.paginate uses this directly for the api_user stream. CalendlyConnector._org_stream uses it before organization-wide streams so those streams know which organization parameter to send to Calendly.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 77–90)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Calendly list endpoint page by page. It hides the repeated work of following Calendly’s next-page token so other methods can simply loop over batches of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST connector machinery to fetch records from the response’s collection field, follow pagination.next_page_token, send that token back as page_token, and request pages of 100 records. It yields each page as a list of records.

**Call relations**: CalendlyConnector._org_stream uses this for organization-scoped lists. CalendlyConnector._invitees also uses it for the per-event invitee lists after it has found each scheduled event.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 92–108)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Calendly collection that belongs to the current organization. It makes sure every request includes the organization URI, because these Calendly endpoints need that context.

**Data flow**: It receives an HTTP client, an API path, and optional cursor information. It first reads the current user, extracts current_organization, and stops the stream with a clear skip message if Calendly does not provide one. It builds request parameters with the organization and, when present, the saved cursor value. It then yields paged records and adds organization context to each page before passing it on.

**Call relations**: CalendlyConnector.paginate calls this for event types, groups, memberships, and scheduled events. CalendlyConnector._invitees calls it first to get scheduled events before asking for each event’s invitees. It relies on CalendlyConnector._current_user, CalendlyConnector._paginate_collection, and with_context to produce organization-aware pages.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 110–128)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads invitees for scheduled Calendly events. Calendly does not provide invitees as one organization-wide list, so this function walks through scheduled events first and then fetches invitees for each event.

**Data flow**: It receives an HTTP client and an optional saved cursor. It gets scheduled events from the organization stream, extracts each event’s UUID from its URI, and skips events whose URI cannot be understood. For each valid event, it requests that event’s invitee pages. If a cursor exists, it keeps only invitees whose created_at value is later than the cursor. It yields non-empty invitee pages with context showing which scheduled event they came from.

**Call relations**: CalendlyConnector.paginate calls this when the requested stream is event_invitees. This function builds on CalendlyConnector._org_stream for events, _uuid_from_uri for event IDs, CalendlyConnector._paginate_collection for invitee pages, and with_context to attach the parent event information.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 130–162)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher that decides how to fetch each Calendly stream. The sync framework asks it for records from a named stream, and it chooses the right Calendly endpoint and filtering behavior.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. For api_user, it returns the current user as a single-record page. For organization-based streams, it delegates to the organization stream helper, adding the right cursor parameter where Calendly supports incremental reads. For event_invitees, it delegates to the invitee-specific flow. If the stream name is unknown, it marks that stream as skipped.

**Call relations**: This is the connector’s central read path. The broader source-sync system calls it for each configured stream, and it hands the work to CalendlyConnector._current_user, CalendlyConnector._org_stream, or CalendlyConnector._invitees depending on what kind of Calendly data is being read.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 164–204)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes Calendly records into friendlier, more stable records before the rest of the system stores them. It keeps the original data but adds common fields such as name, email, title, start_at, and end_at where useful.

**Data flow**: It receives one Calendly record and the stream it came from. Depending on the stream, it copies selected values into easier-to-use field names, extracts nested membership user details, simplifies scheduled event location data, or leaves the record unchanged. The result is a new dictionary-like record ready for storage or indexing.

**Call relations**: After records are fetched through CalendlyConnector.paginate and related helpers, the sync framework can call this to normalize each record. For organization memberships it uses dict_or_empty so a missing or malformed nested user object does not break the flattening step.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/typeform.py`

`io_transport` · `source sync`

Typeform is an online form service, and its API returns data in small chunks rather than all at once. This file is the Typeform “reader” for the project. Its job is to ask Typeform for each supported kind of data, follow Typeform’s paging rules, and yield batches of plain records for the sync system.

The central class, TypeformConnector, inherits from a shared RestConnector, which supplies common web-request behavior. This connector defines which Typeform streams exist and how to read each one. For simple collections like forms, workspaces, images, and themes, it uses page numbers: page 1, page 2, and so on, until Typeform says there are no more pages. For responses, the work is more involved. Typeform responses belong to a form, so the connector first lists forms, then asks for responses for each form. It also adds the form id and title onto each response, so later readers know where that response came from.

The file also supports incremental syncing. For example, when reading forms it can skip forms whose last update is not newer than the saved cursor. For responses, it passes the cursor to Typeform as a “since” value. If Typeform refuses access with a 401 or 403 error, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `TypeformConnector.paginate`  (lines 52–79)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a Typeform stream. Given a requested stream, it chooses the right helper to fetch that kind of Typeform data and yields batches of records back to the sync runner.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that marks the last synced point. It checks the stream name, calls the matching reader, and passes through each page of records it gets back. If Typeform rejects the request because the key is bad or missing permission, it turns that into a clear “stream skipped” result; for other web errors, it lets the error continue upward.

**Call relations**: The sync framework calls this when it wants records from Typeform. It hands form reads to TypeformConnector._forms, response reads to TypeformConnector._responses, simple page-numbered collections to TypeformConnector._paged_items, and webhook reads to TypeformConnector._webhooks. If a stream is unknown or forbidden, it raises StreamSkipped so the larger sync can move on safely.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 81–99)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Typeform endpoints that use ordinary page numbers. It keeps asking for the next page until Typeform says the collection is finished.

**Data flow**: It receives an HTTP client, an API path such as /forms, and optional query parameters. It adds a page number and page size, makes the request, pulls the list found under the “items” field, and yields that list when it is not empty. It stops when it reaches Typeform’s reported page count, or when a short page suggests there is nothing more to fetch.

**Call relations**: TypeformConnector.paginate uses this directly for simple streams like workspaces, images, and themes. TypeformConnector._forms also uses it as its lower-level page reader before applying form-specific cursor filtering. It relies on records_at to safely extract the list of records from Typeform’s response body.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 101–108)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Typeform forms and optionally filters them to only forms updated after a saved cursor. It is the shared starting point for anything that needs to know which forms exist.

**Data flow**: It receives an HTTP client and an optional cursor string. It asks TypeformConnector._paged_items for pages from /forms, then, if a cursor was supplied, keeps only forms whose last_updated_at value is newer than that cursor. It yields each remaining non-empty page of forms.

**Call relations**: TypeformConnector.paginate calls this when the requested stream is forms. TypeformConnector._responses and TypeformConnector._webhooks also call it because both responses and webhooks are attached to individual forms, so they must first discover the forms to visit.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 110–132)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads submitted responses for every Typeform form. It matters because responses are not one global list in Typeform; they must be fetched form by form.

**Data flow**: It first reads all forms without filtering them by cursor, then looks at each form’s id. For each valid form id, it builds request parameters, adding a “since” value when an incremental cursor is available. It then reads cursor-based response pages, where Typeform returns a token for the next page, and yields the response records with the form id and form title attached.

**Call relations**: TypeformConnector.paginate calls this for the responses stream. This function depends on TypeformConnector._forms to know which forms to inspect, then uses the inherited cursor-page reader from the REST connector to walk through each form’s responses. Before yielding results, it calls with_context so downstream code can tell which form each response belongs to.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 134–143)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads the webhooks configured for each Typeform form. Webhooks are fetched per form, so this function visits every form and asks Typeform for that form’s webhook list.

**Data flow**: It receives an HTTP client, reads all forms, and skips any form that does not have a usable string id. For each valid form, it requests /forms/{form_id}/webhooks, extracts the list under “items”, and yields those webhook records with the form id and form title added as context.

**Call relations**: TypeformConnector.paginate calls this when the webhooks stream is requested. It uses TypeformConnector._forms to discover forms, records_at to pull webhook records out of Typeform’s response, and with_context to label each webhook with the form it came from.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).
