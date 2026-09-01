# Structured Apps, Documents, Forms, Scheduling, and Support Connectors  `stage-12.1.7`

This stage is a set of behind-the-scenes connectors that let the system read from structured business tools during a sync. A connector is like an adapter plug: each outside service has its own shape, but the rest of the system expects a steady stream of simple records it can store and search.

Airtable reads bases, tables, and records from spreadsheet-like databases. Calendly brings in scheduling data such as users, event types, bookings, invitees, groups, and memberships. DocuSign reads envelopes and templates, finds the correct regional service address, and gives documents useful titles. PandaDoc reads documents, templates, and contacts without changing anything in PandaDoc. Typeform gathers forms, responses, workspaces, themes, images, and webhook settings. Freshdesk, Intercom, and Zendesk cover customer support systems, turning tickets, conversations, contacts, companies, agents, help articles, forums, and activity logs into the same kind of syncable pages. Together, these files translate many different APIs, or web service interfaces, into one common flow for later recall.

## Files in this stage

### Structured Data and Scheduling
Connectors that turn database-style business records and scheduling objects into syncable streams.

### `extensions/sources/ufo_ext_sources/providers/airtable.py`

`io_transport` · `source sync`

Airtable data is not offered as one simple list. It is more like a filing cabinet: first there are bases, inside each base are tables, and inside each table are records. This connector walks that cabinet in order. It first asks Airtable for all bases, then asks for the tables in each base, then reads the records in each table.

The file defines three streams: bases, tables, and records. A stream is a named kind of data the sync system can ask for. The connector uses Airtable’s web API through an HTTP client, with authentication provided by the shared source framework. For records, Airtable sends results in chunks and gives back an offset token to fetch the next chunk. The connector follows those tokens until the table is fully read.

A key detail is that tables and records are stamped with extra context, such as the base ID or table ID they came from. Without that, a record would be like a loose page with no folder label; later code could see the row but not know where it belonged. This file is read-only. It has no path for writing changes back to Airtable.

#### Function details

##### `AirtableConnector._bases`  (lines 39–41)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Airtable bases available to the authenticated account. A base is Airtable’s top-level container, similar to a workbook or project space.

**Data flow**: It receives an HTTP client that can make authenticated requests. It asks Airtable’s metadata endpoint for bases, pulls the list named "bases" out of the response, and returns that list as plain dictionaries.

**Call relations**: The main pagination method calls this first whenever it needs to sync bases, tables, or records. It relies on the shared record-extraction helper to pick the useful list out of Airtable’s response.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 43–50)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Fetches the tables that belong to one Airtable base. It also labels each table with the base it came from, so the table is not separated from its parent.

**Data flow**: It receives an HTTP client and one base record. It reads the base ID; if the ID is missing or invalid, it returns an empty list. Otherwise it asks Airtable for that base’s tables, extracts the table list, adds base information such as base ID and base name to each table, and returns the enriched table list.

**Call relations**: The main pagination method calls this after discovering bases. It uses the shared helpers to extract table records from the response and attach context before the data is passed onward.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 52–70)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the records inside one Airtable table, one page at a time. It adds the base and table labels needed to identify where each record came from.

**Data flow**: It receives an HTTP client, a base ID, and a table description. It checks that the table has a usable ID. If so, it repeatedly asks Airtable for record pages using Airtable’s offset token for the next page. For every non-empty page, it adds base ID, table ID, and table name to each record, then yields that page to the caller.

**Call relations**: The main pagination method calls this after it has found a base and one of its tables. It delegates the repeated page fetching to the shared cursor-page reader, then adds Airtable-specific context before handing records back.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 72–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the main read path for each Airtable stream. Depending on whether the caller asks for bases, tables, or records, it walks the right part of Airtable’s hierarchy and yields pages of data.

**Data flow**: It receives an HTTP client, a stream description, and a cursor value. It checks the stream name. For bases, it fetches bases and yields them. For tables, it fetches bases, then gathers tables from each base into pages of about 100 items. For records, it fetches bases, then tables, then yields record pages from every table. If the stream name is unknown, it raises a skip signal instead of pretending it can sync it.

**Call relations**: This is the method the broader source-sync framework calls when it wants Airtable data. It coordinates the helper methods in the right order: bases first, then tables, then records. If asked for a stream this connector does not implement, it hands back a clear StreamSkipped result.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 103–125)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes Airtable records into a shape that is easier for the rest of the system to store and display. It adds useful direct fields, such as API URLs or a consistent created-at value.

**Data flow**: It receives one Airtable record and the stream it belongs to. For bases, it keeps the record and adds a direct metadata API URL. For tables, it adds the table API URL using the stored base ID. For records, it pulls out the fields dictionary safely and renames Airtable’s creation time into a clearer created_at field. It returns the adjusted dictionary without changing the original stream choice.

**Call relations**: After pages are fetched by the pagination flow, the source framework can call this to prepare each item for downstream use. It does not fetch more data; it only reshapes what has already been read.


### `extensions/sources/ufo_ext_sources/providers/calendly.py`

`io_transport` · `source sync`

This connector is the bridge between Calendly and the project’s source-sync system. Calendly stores data behind a web API, so the system needs a careful reader that knows which API routes to call, how to follow pages of results, and how to keep each record tied to the right organization.

The file defines the Calendly streams first. A stream is a named kind of data to sync, like scheduled events or event invitees. Most Calendly data here is organization-scoped, so the connector first asks Calendly who the current user is, then reads that user’s current organization. Without that step, requests for organization data would not know which Calendly workspace to look inside.

Calendly sends large lists in pages. This file follows Calendly’s `next_page_token`, much like turning pages in a book until there are no more. Some streams also use a cursor, which is a saved “last seen” value, so future syncs can fetch only newer or changed records.

Invitees need special treatment: Calendly exposes them under each scheduled event, so the connector first lists events, extracts each event’s ID from its URI, then fetches invitees for that event. Finally, `flatten` reshapes some records so important fields like name, email, title, and location are easy for the rest of the system to use.

#### Function details

##### `_uuid_from_uri`  (lines 61–64)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: This helper pulls the final ID-like part out of a Calendly URI. It is used when the connector needs the event UUID from a full scheduled-event URI before asking Calendly for that event’s invitees.

**Data flow**: It receives any value that might be a URI. If the value is not a non-empty string, it returns nothing. If it is a string, it trims any trailing slash, splits on the last slash, and returns the final piece.

**Call relations**: The invitee-sync flow calls this while walking through scheduled events. Once it extracts the event UUID, the connector can build the Calendly URL for that event’s invitees.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 72–75)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: This asks Calendly for the authenticated account’s user profile. The connector uses that profile to find the current organization before reading organization-level data.

**Data flow**: It receives an HTTP client that already knows how to talk to Calendly. It sends a request to `/users/me`, looks inside the response for the `resource` object, and returns that object if it is a dictionary. If the response is not shaped as expected, it returns an empty dictionary.

**Call relations**: The organization stream helper calls this before making organization-scoped requests. The main `paginate` method also calls it directly for the `api_user` stream, where the current user record itself is the data being synced.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 77–90)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Calendly list endpoint one page at a time. It hides the repetitive work of following Calendly’s page token so other methods can simply loop over batches of records.

**Data flow**: It receives an HTTP client, an API path, and optional request parameters. It asks the shared REST paging helper to read records from the response’s `collection` field, use `pagination.next_page_token` to find the next page, request up to 100 records at a time, and yield each page as a list of records.

**Call relations**: Organization-level streams use this after they have chosen the organization parameter. The invitee flow also uses it to fetch invitees for each scheduled event.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 92–108)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Calendly collection that belongs to the user’s current organization. It is the common path for streams like event types, groups, memberships, and scheduled events.

**Data flow**: It starts with an HTTP client, an API path, and optionally a saved cursor plus the Calendly parameter name that should receive that cursor. It first fetches the current user, takes the `current_organization` URI, and stops the stream if no valid organization is available. It then builds request parameters with that organization and optional cursor, reads paged results, adds organization context to each page, and yields those enriched pages.

**Call relations**: The main `paginate` method calls this for most Calendly streams. The invitee flow calls it first to get scheduled events, because invitees are found underneath individual events. It relies on `_current_user`, `_paginate_collection`, and `with_context` to fetch records and attach the organization they came from.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 110–128)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads invitees for scheduled events. Calendly does not provide invitees as one simple organization-wide list here, so the connector must first find events and then ask for invitees event by event.

**Data flow**: It receives an HTTP client and an optional cursor. It reads scheduled events through the organization stream, extracts each event UUID from its URI, and skips events whose URI cannot produce a UUID. For each valid event, it pages through that event’s invitees. If a cursor exists, it keeps only invitees whose `created_at` value is newer than the cursor. It then yields non-empty invitee pages with added context showing which scheduled event they belong to.

**Call relations**: The main `paginate` method calls this when syncing the `event_invitees` stream. This method combines `_org_stream`, `_uuid_from_uri`, `_paginate_collection`, and `with_context` to turn many per-event API calls into one stream of invitee records.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 130–162)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main routing point for reading Calendly streams. Given a stream name, it chooses the correct Calendly API path and cursor behavior, then yields pages of records for that stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. For `api_user`, it fetches the current user and yields that single record. For organization-backed streams, it delegates to `_org_stream`, adding Calendly’s incremental parameters where needed, such as `updated_since` for event types or `min_start_time` for scheduled events. For invitees, it delegates to `_invitees`. If the stream is unknown, it stops that stream with a clear skipped-stream message.

**Call relations**: The broader source-sync framework calls this to get Calendly records page by page. It then hands work to `_current_user`, `_org_stream`, or `_invitees` depending on which stream the framework requested.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 164–204)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes raw Calendly records into friendlier records for the rest of the system. It keeps the original data but promotes important fields, such as name, email, title, start time, end time, and location, to predictable places.

**Data flow**: It receives one Calendly record and the stream it belongs to. Depending on the stream, it copies the record and adds or rewrites useful fields. For organization memberships, it pulls name and email out of the nested `user` object and leaves that nested user object out, so later user profile changes do not make the membership record look changed. For scheduled events, it maps Calendly’s event name and times into title, start, and end fields and simplifies the location when possible. It returns the reshaped record.

**Call relations**: After `paginate` has supplied raw records, the sync framework can call this before storing or indexing them. It uses `dict_or_empty` only for the membership case, so a missing or malformed nested user still becomes a safe empty dictionary rather than causing the sync to fail.

*Call graph*: 1 external calls (dict_or_empty).


### Documents and Forms
Connectors for e-signature, document workflow, and form platforms that expose documents, templates, forms, and responses as searchable records.

### `extensions/sources/ufo_ext_sources/providers/docusign.py`

`io_transport` · `during scheduled source sync`

DocuSign data is not all served from one fixed web address. A user first authenticates through DocuSign’s shared identity service, then DocuSign tells the connector which regional account address to use for real data. This file exists to make that two-step process safe and predictable.

The connector defines two streams of data: envelopes, which are sent signing packets, and templates, which are reusable envelope setups. For each sync run, it asks DocuSign who the authenticated user is and which account should be read. If there is exactly one usable account, it uses that. If there are several, it only proceeds when DocuSign marks one as the default. This avoids silently importing the wrong company’s documents.

Once it has the account-specific API address, it reads records in pages of 100. Envelopes can be synced incrementally using their status-changed time, so later runs can ask only for newer changes. A first run starts from a very old fixed date so it does not accidentally miss history. Templates are listed without a cursor. If DocuSign refuses access, the stream is skipped with a clear explanation instead of crashing the whole idea of syncing other sources.

One small but important finishing touch is rendering envelope titles. DocuSign stores the human-facing envelope name as `emailSubject`, so this file uses that as the title users will recognize.

#### Function details

##### `DocuSignConnector._account_base`  (lines 79–109)

```
async def _account_base(self, client: httpx.AsyncClient) -> str
```

**Purpose**: This function finds the exact DocuSign account API address that should be used for data requests. It protects the system from guessing when one login can see multiple DocuSign accounts.

**Data flow**: It receives an HTTP client that can make authenticated requests. It asks DocuSign’s user-info endpoint for the accounts attached to the login, filters out accounts missing an account ID or base address, then chooses the only account or the single account marked as default. It returns a full REST API base URL for that account, or raises a clear fault if there is no safe account choice.

**Call relations**: Before `DocuSignConnector.paginate` can list envelopes or templates, it calls this function to learn where those account-specific requests must go. If the account list is unusable or ambiguous, this function stops the flow by raising `StreamFault`; otherwise it hands back the base address that pagination will build on.

*Call graph*: calls 1 internal fn (__init__); called by 1 (paginate); 1 external calls (list_or_empty).


##### `DocuSignConnector.paginate`  (lines 111–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads DocuSign envelopes or templates in batches. It is the main data-fetching loop for this connector.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value that marks where an incremental sync should resume. It first asks `_account_base` for the right account API address, then repeatedly requests pages of up to 100 records. For envelopes, it adds a starting date, asks DocuSign to include recipients and custom fields, and requests oldest-to-newest ordering. Each non-empty page is yielded to the caller, and the loop stops when DocuSign returns fewer than 100 records. If DocuSign replies with a permission refusal, it changes that into a skipped stream message; other HTTP errors keep bubbling up.

**Call relations**: The broader source-sync system calls this method when it wants records from a DocuSign stream. This method depends on `_account_base` to choose the correct regional account endpoint, uses `list_or_empty` so missing or malformed list fields become safe empty lists, and hands each page of records back to the sync engine for storage and rendering.

*Call graph*: calls 2 internal fn (__init__, _account_base); 1 external calls (list_or_empty).


##### `DocuSignConnector.render`  (lines 145–154)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns a raw DocuSign record into a title and text body that the rest of the system can store and show. It gives envelopes a friendly title based on the subject recipients saw.

**Data flow**: It receives one DocuSign record and the stream it came from. If the record is an envelope with a non-empty `emailSubject`, it uses that subject as the title and builds a text page containing the full record as sorted JSON. For templates, or envelopes without a usable subject, it falls back to the standard rendering behavior from the parent connector.

**Call relations**: After `paginate` has supplied records, the source framework can call this method to prepare them for recall or display. This method does not fetch more data; it only reshapes one already-fetched record, using `json.dumps` to include the raw record contents in a readable stored page.

*Call graph*: 1 external calls (dumps).


### `extensions/sources/ufo_ext_sources/providers/pandadoc.py`

`io_transport` · `sync request handling`

This connector is the system’s bridge to PandaDoc. PandaDoc exposes its data through a web API, and this file knows which API paths to call, how to authenticate, how to page through long lists, and how to recover when some records cannot be opened.

The main job is to sync three kinds of PandaDoc objects: documents, templates, and contacts. Documents get special treatment because the list endpoint only returns a thin summary. For each document, the connector makes a second request to fetch fuller details, such as fields, tokens, pricing, and recipients. This is like first getting a library catalog entry, then opening the actual book record for the useful details.

For documents, syncing can be incremental. That means the connector can ask PandaDoc for only documents changed since the last saved point, using the document’s modified date. Templates and contacts are fetched as ordinary paged lists.

The connector also protects the wider sync run from common access problems. If PandaDoc refuses an entire stream because the key or grant lacks permission, the stream is skipped with a clear message. If one listed document cannot be opened, the connector keeps the basic list row instead of failing the whole run.

#### Function details

##### `_stream`  (lines 37–51)

```
def _stream(name: str, *, cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates the description of one PandaDoc stream, such as documents, templates, or contacts. A stream description tells the rest of the sync system what the records are called, which field identifies each record, and which date fields can be used for tracking changes.

**Data flow**: It takes a stream name, an optional cursor field for incremental syncing, and a flag saying whether this is a main, standard stream. It fills those choices into a StreamSpec object, using `id` as the record’s unique key and PandaDoc’s `date_created` and `date_modified` fields as the creation and update timestamps. The result is a ready-to-use stream definition.

**Call relations**: This function is used while the file is being loaded to build the `PANDADOC_STREAMS` list. It hands each stream definition to the connector class, so the broader source framework knows which PandaDoc collections can be synced.

*Call graph*: 1 external calls (__init__).


##### `PandaDocConnector._make_client`  (lines 66–72)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function prepares the HTTP client used to talk to PandaDoc. Its important job is to put member-provided PandaDoc API keys into the special authorization format PandaDoc expects.

**Data flow**: It receives a base API address and a credential. First it asks the parent REST connector to build the normal web client. If the credential contains a bearer-style key, this function rewrites the authorization header as `API-Key <key>`, because PandaDoc uses that wording instead of the more common bearer-token style. It returns the configured client.

**Call relations**: The source framework calls this when it is setting up a PandaDoc sync. After this client is created, other connector methods use it to fetch lists and document details from PandaDoc.


##### `PandaDocConnector.paginate`  (lines 74–103)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one PandaDoc stream page by page. It is the main reader for documents, templates, and contacts, and it yields batches of records back to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. It chooses the correct PandaDoc list endpoint, then requests pages of up to 100 records. For documents, it also asks PandaDoc to sort by modification date and, when a cursor is available, to return only documents changed after that point. Each response’s `results` value is turned into a list; document rows are enriched by fetching their details one by one. It yields each non-empty batch, stops when a short page shows there is no more data, and raises a clear skip signal if PandaDoc refuses access with a 401 or 403 status.

**Call relations**: The core sync process calls this when it wants records for a PandaDoc stream. During document syncing it calls `PandaDocConnector._details` for each listed document so the records are useful, not just summaries. If access is denied for the stream, it creates a `StreamSkipped` error so the larger run can continue appropriately instead of crashing without context.

*Call graph*: calls 2 internal fn (__init__, _details); 1 external calls (list_or_empty).


##### `PandaDocConnector._details`  (lines 105–117)

```
async def _details(self, client: httpx.AsyncClient, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This function expands a single PandaDoc document summary into a fuller document record. It adds the information that PandaDoc only provides from the document details endpoint.

**Data flow**: It receives the web client and one document record from the list endpoint. If the record has no usable string `id`, it returns the record unchanged. Otherwise it asks PandaDoc for `/public/v1/documents/{id}/details`. If that succeeds, it combines the original list row and the details response into one record. If PandaDoc says this particular document is forbidden or missing, it returns the original list row so one bad document does not stop the whole sync.

**Call relations**: `PandaDocConnector.paginate` calls this while processing the documents stream. It hands back either an enriched document or the original summary, allowing pagination to keep yielding complete batches even when some individual documents cannot be opened.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/typeform.py`

`io_transport` · `source sync run`

Typeform is an online form service, and its data is spread across several API endpoints. This connector is the bridge between Typeform’s API and UFO’s source-sync runner. Without it, the system would not know which Typeform endpoints to call, how to move through Typeform’s pages of results, or how to attach useful form context to things like responses and webhooks.

The file defines the available Typeform streams first: forms, responses, workspaces, images, themes, and webhooks. A stream is one category of data the sync system can pull. The main class, TypeformConnector, inherits shared REST behavior from RestConnector, meaning it uses common machinery for authenticated HTTP requests while adding Typeform-specific rules.

Most Typeform collections are fetched page by page using numbered pages. Responses are different: they are fetched separately for each form and use a cursor token, which is like a bookmark saying “continue after this point.” Webhooks are also fetched per form. The connector supports incremental syncing where possible, meaning it can ask for only records newer than a saved cursor instead of rereading everything.

If Typeform refuses access with a 401 or 403 status, the connector does not crash the whole run. It marks that stream as skipped, usually meaning the credential is missing permission or is invalid.

#### Function details

##### `TypeformConnector.record_ref`  (lines 52–56)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This function chooses a stable human-facing reference for a Typeform record. For most streams it uses the normal reference logic, but for webhooks it uses the webhook tag because that is the meaningful identifier Typeform exposes there.

**Data flow**: It receives one record and the stream it came from. If the stream is not webhooks, it passes the record to the parent connector’s usual reference logic. If it is webhooks, it reads the record’s tag field and returns it as text when it is a string or number; otherwise it returns nothing.

**Call relations**: The wider sync system calls this when it needs a reference for a synced record. This function only special-cases webhooks, then otherwise hands responsibility back to the shared RestConnector behavior.


##### `TypeformConnector.paginate`  (lines 58–85)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a Typeform stream. Given a requested stream, it chooses the right fetching method and yields batches of records back to the sync runner.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching helper, and passes each returned page of records onward. If the stream is unknown, or if Typeform refuses access with a 401 or 403 response, it raises StreamSkipped so the runner can skip that stream cleanly instead of treating it as a full system failure.

**Call relations**: The sync runner calls this when it wants data for one Typeform stream. It delegates to _forms, _responses, _paged_items, or _webhooks depending on the stream, acting like a traffic controller that sends each request down the correct road.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 87–105)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Typeform endpoints that use ordinary numbered pages, such as forms, workspaces, images, and themes. It keeps asking for the next page until Typeform says there are no more results.

**Data flow**: It receives an HTTP client, an API path, and optional query settings. It starts at page 1, adds Typeform’s page and page_size parameters, fetches the page, pulls the list of records out of the response’s items field, and yields that list when it is not empty. It stops when it reaches Typeform’s reported page count, or when a short page suggests the end has been reached.

**Call relations**: paginate calls this directly for simple streams. _forms also calls it as its base fetcher, then adds form-specific filtering. It uses records_at to safely extract the records from Typeform’s response envelope.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 107–114)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches Typeform forms and optionally filters them for incremental syncs. It is important because forms are both a stream of their own and the starting point for fetching responses and webhooks.

**Data flow**: It receives an HTTP client and an optional cursor. It asks _paged_items for pages from the /forms endpoint. If a cursor is present, it keeps only forms whose last_updated_at value is newer than that cursor. It then yields any remaining forms in batches.

**Call relations**: paginate calls this when syncing the forms stream. _responses and _webhooks also call it first because Typeform responses and webhooks must be requested form by form, so the connector needs the list of forms before it can fetch those child records.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 116–138)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches submitted responses for every Typeform form. It also adds the form’s id and title to each response so later readers know which form the response belongs to.

**Data flow**: It receives an HTTP client and an optional cursor. First it fetches all forms without filtering them by form update time. For each valid form id, it builds request parameters; when a cursor is available, it sends it as Typeform’s since value so only newer responses are requested. It then walks through cursor-based response pages, where Typeform provides a next_page_token as a bookmark. Each batch of response records is returned with extra context fields: form_id and form_title.

**Call relations**: paginate calls this for the responses stream. This function depends on _forms to discover the forms to visit, then relies on the shared cursor-page fetching behavior from the base connector. Before yielding results, it uses with_context to attach parent-form information to each response.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 140–149)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches the configured webhooks for every Typeform form. Like responses, webhooks live under individual forms, so the connector must walk through the forms first.

**Data flow**: It receives an HTTP client. It fetches all forms, skips any form without a usable string id, then requests /forms/{form_id}/webhooks for each one. From each API response it extracts the items list, and when records exist it adds the form_id and form_title to each webhook before yielding them.

**Call relations**: paginate calls this for the webhooks stream. It calls _forms to find the forms, uses records_at to pull webhook records from Typeform’s response, and uses with_context so each webhook record carries the form it came from.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).


### Customer Support Platforms
Connectors that normalize helpdesk, customer messaging, ticketing, knowledge-base, and community data into syncable records.

### `extensions/sources/ufo_ext_sources/providers/freshdesk.py`

`io_transport` · `source sync`

Freshdesk exposes its data through a web API, but not every kind of data is fetched the same way. This file is the adapter that knows those rules. Without it, the system would not know which Freshdesk web addresses to call, how to authenticate, how to follow Freshdesk’s different paging styles, or how to walk nested objects like ticket conversations and knowledge-base articles.

The file defines the list of Freshdesk streams the system can read. A stream is one category of records, like “tickets” or “companies.” For simple streams, it maps the stream name to one Freshdesk API path and follows Freshdesk’s “next page” links. Tickets are special: they use numbered pages, can be fetched incrementally using an “updated since” time, and must stop before Freshdesk’s 300-page limit. Conversations are also special because they live under tickets, so the connector first reads tickets and then asks Freshdesk for each ticket’s conversations.

Some Freshdesk areas are tree-shaped, like a filing cabinet: categories contain folders, and folders contain articles. This file walks those trees level by level. It also creates the HTTP client with Freshdesk’s expected Basic authentication, using the API key as the username. If Freshdesk refuses access with a 401 or 403 response, the stream is skipped with a clear explanation instead of crashing the whole sync unnecessarily.

#### Function details

##### `_stream`  (lines 56–70)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This small helper creates a stream description for one kind of Freshdesk data. It keeps the long stream list readable by filling in common defaults like the record key and source object name.

**Data flow**: It receives a stream name plus optional details such as the Freshdesk source name, primary key, cursor field, and whether it is a main stream. It packages those values into a StreamSpec object, which is the system’s standard description of a readable stream.

**Call relations**: It is used while the file is loaded to build the Freshdesk stream catalog. Each call creates one entry that FreshdeskConnector later advertises as something it can sync.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector.record_identity`  (lines 110–113)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This decides the stable identity for a Freshdesk record. It gives the Freshdesk settings record a fixed identity because settings come back as one object rather than as a normal list item with its own record id.

**Data flow**: It receives one record and the stream it came from. If the stream is settings, it returns the constant key "helpdesk"; otherwise it falls back to the normal identity logic from the shared REST connector.

**Call relations**: The broader sync system calls this when it needs to know how to name or deduplicate a record. This method only steps in for the special settings stream and lets the base connector handle all regular streams.


##### `FreshdeskConnector.record_ref`  (lines 115–119)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This chooses a human-friendly reference for a Freshdesk record. For settings, it uses the primary language when available, because the settings object has no ordinary title-like field here.

**Data flow**: It receives a record and its stream. For non-settings streams, it delegates to the shared connector behavior. For settings, it reads the record’s primary_language value and returns it as text if it is a string or number; otherwise it returns nothing.

**Call relations**: The sync system uses this when it wants a readable label for a stored record. Like record_identity, this method only customizes the unusual settings stream and leaves normal records to the shared REST behavior.


##### `FreshdeskConnector._make_client`  (lines 121–136)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the web client used to talk to a Freshdesk tenant. It sets timeouts, JSON headers, the tenant base address, and the authentication method Freshdesk expects.

**Data flow**: It receives a base URL and a Credential object. It trims the base URL, prepares headers and time limits, then either uses a provided custom transport or creates Basic authentication with the API key as the username and "X" as the password. It returns an httpx AsyncClient ready to make Freshdesk API calls, or raises an error if no usable credential is present.

**Call relations**: The shared connector framework calls this before making requests. It hands back the configured HTTP client that paginate and the lower-level request helpers use for all Freshdesk reads.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 139–149)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: This builds the query options Freshdesk needs when fetching ticket pages. It includes page size, page number, sort order, extra included ticket details, and optionally an incremental sync cursor.

**Data flow**: It receives a cursor value and a page number. It creates a dictionary of request parameters for the tickets endpoint, adding updated_since only when a cursor is available. The result is passed directly into the ticket API request.

**Call relations**: FreshdeskConnector._paginate_tickets calls this once per ticket page. It keeps the ticket paging loop focused on fetching records while this helper takes care of the exact Freshdesk request shape.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 151–178)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway for reading pages of records from any Freshdesk stream. It chooses the right paging method for the requested stream and turns Freshdesk responses into batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor for incremental reads. It first checks whether the stream needs special treatment, such as tickets or nested resources. If not, it looks up the stream’s simple API path, fetches settings as a single object when needed, or follows link-header pagination for ordinary list endpoints. It yields lists of records as they arrive. If Freshdesk returns 401 or 403, it converts that refusal into a StreamSkipped error with a clear message.

**Call relations**: The sync engine calls paginate when it is time to read one Freshdesk stream. This method delegates special cases to FreshdeskConnector._special_pages and ordinary list-style streams to FreshdeskConnector._paginate_link_header, then yields pages back to the caller.

*Call graph*: calls 3 internal fn (__init__, _paginate_link_header, _special_pages).


##### `FreshdeskConnector._special_pages`  (lines 180–221)

```
def _special_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]] | None
```

**Purpose**: This decides whether a stream needs a custom walking plan instead of the simple one-path paging flow. It covers tickets, conversations, canned responses, solution articles, and discussion/forum trees.

**Data flow**: It receives the HTTP client, stream name, and cursor. It compares the stream name against known special cases. For tickets or conversations it returns the matching paginator. For two-level structures it returns a parent-then-child walker. For solution articles it returns a three-level walker. If the stream is not special, it returns nothing.

**Call relations**: FreshdeskConnector.paginate calls this before trying the simple stream path. This function is the dispatcher that hands special streams off to FreshdeskConnector._paginate_tickets, FreshdeskConnector._paginate_conversations, FreshdeskConnector._paginate_two_level, or FreshdeskConnector._paginate_three_level.

*Call graph*: calls 4 internal fn (_paginate_conversations, _paginate_three_level, _paginate_tickets, _paginate_two_level); called by 1 (paginate).


##### `FreshdeskConnector._paginate_link_header`  (lines 223–230)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Freshdesk endpoints that point to the next page using a web-standard Link header. A Link header is like a “next page is over here” note attached to the response.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST helper to fetch pages with a page size of 100, then yields each list of records it gets back.

**Call relations**: FreshdeskConnector.paginate uses this for ordinary list streams. The nested paginators also use it whenever they need to read parent or child lists, so it acts as the common page-walking tool for most Freshdesk endpoints.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 232–249)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Freshdesk tickets using Freshdesk’s ticket-specific numbered paging rules. It supports incremental syncing by asking only for tickets updated after a cursor time.

**Data flow**: It receives an HTTP client and optional cursor. Starting at page 1, it builds request parameters, fetches a ticket page, extracts the records, and yields them. It stops when there are no records, when a page is not full, or when it reaches Freshdesk’s 300-page ceiling.

**Call relations**: FreshdeskConnector._special_pages returns this paginator for the tickets stream. FreshdeskConnector._paginate_conversations also calls it first, because conversations must be discovered by walking through tickets.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, _special_pages).


##### `FreshdeskConnector._paginate_conversations`  (lines 251–267)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads ticket conversations, which Freshdesk stores underneath each ticket rather than as one global list. It first finds tickets, then fetches the conversations for each ticket.

**Data flow**: It receives an HTTP client and optional cursor. It reads ticket pages through FreshdeskConnector._paginate_tickets. For each ticket with an id, it requests that ticket’s conversation pages, adds the ticket_id to each conversation if missing, and yields the conversation pages.

**Call relations**: FreshdeskConnector._special_pages returns this paginator for the conversations stream. Inside, it relies on FreshdeskConnector._paginate_tickets to find the parent tickets and FreshdeskConnector._paginate_link_header to read each ticket’s conversation list.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (_special_pages).


##### `FreshdeskConnector._paginate_two_level`  (lines 269–280)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks Freshdesk data that is arranged in two levels: a parent list and then a child list under each parent. Examples include folders to responses, categories to forums, and topics to comments.

**Data flow**: It receives an HTTP client, a parent API path, and a child-path template containing a parent id placeholder. It reads parent pages, takes each parent id, fills that id into the child path, reads the child pages, and yields those child records.

**Call relations**: FreshdeskConnector._special_pages uses this for several nested streams. This function relies on FreshdeskConnector._paginate_link_header for both the parent lists and the child lists.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (_special_pages).


##### `FreshdeskConnector._paginate_three_level`  (lines 282–305)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks Freshdesk data arranged in three levels, specifically the solution knowledge-base path from categories to folders to articles. It is like opening a cabinet, then a drawer, then reading the files inside.

**Data flow**: It receives an HTTP client plus paths for the root level, middle level, and leaf level. It reads root categories, uses each category id to fetch folders, uses each folder id to fetch articles, and yields the article pages it finds. Items without ids are skipped because the next path cannot be built without an id.

**Call relations**: FreshdeskConnector._special_pages returns this paginator for solution_articles. It uses FreshdeskConnector._paginate_link_header at every level so the same next-page behavior is reused throughout the tree walk.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (_special_pages).


### `extensions/sources/ufo_ext_sources/providers/intercom.py`

`io_transport` · `during source sync runs`

Intercom does not offer one simple way to fetch everything. Some data is searched with a POST request and a cursor, some is fetched by a scrolling company endpoint, some comes back as one plain list, and some is hidden behind parent objects such as conversation parts inside conversations. This file is the adapter that knows those differences so the rest of UFO can ask for a stream and receive batches of records without caring how Intercom exposes them.

At the top, the file defines the Intercom streams UFO can sync and basic facts about each stream, such as its name, main identifier, and cursor field. A cursor is the “bookmark” used for incremental sync, so future runs can ask only for records updated after the last saved point.

`IntercomConnector` then provides the real behavior. It creates an HTTP client with the required Intercom API version header, chooses the right pagination method for each stream, and converts refusal errors like HTTP 401 or 403 into a clean “stream skipped” result. It also flattens a few nested Intercom fields, like conversation source details or contact company IDs, so later SQL-style transforms can read them easily. The connector is read-only; it pulls data from Intercom but does not write anything back.

#### Function details

##### `_stream`  (lines 52–66)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates a stream description for one kind of Intercom data. A stream description tells UFO what the stream is called, what Intercom object it represents, which field identifies records, and whether it has a cursor for incremental syncing.

**Data flow**: It receives a stream name plus optional details like source object, primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults, then returns a `StreamSpec`, which is UFO’s small recipe for syncing that stream.

**Call relations**: This helper is used while the file is loaded to build the `INTERCOM_STREAMS` list. That list becomes the catalog that `IntercomConnector` advertises to the wider source-sync system.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector.record_identity`  (lines 101–105)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Chooses the stable identity value for a record. For most streams it uses the normal base connector behavior, but Intercom attribute records need special handling because their useful identity may be either `id` or `full_name`.

**Data flow**: It receives one record and the stream it came from. If the stream is an attribute stream, it looks for `id` first and then `full_name`, returning that value as text when possible. Otherwise it passes the decision to the parent connector.

**Call relations**: The sync framework calls this when it needs to name or deduplicate records. This method only steps in for Intercom’s attribute streams; all other streams continue through the shared `RestConnector` identity logic.


##### `IntercomConnector.record_ref`  (lines 107–111)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Chooses a human-friendly reference label for some Intercom records. Tags, teams, and attribute records are best recognized by their `name`, so this method uses that when available.

**Data flow**: It receives a record and its stream. For tags, teams, and attribute streams, it reads the `name` field and returns it as text if it is a string or number. For all other streams, it leaves the choice to the parent connector.

**Call relations**: The wider sync system uses record references when it wants a readable label rather than just an internal ID. This method customizes that label for Intercom streams whose names are more useful to people.


##### `IntercomConnector._make_client`  (lines 113–116)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to Intercom and adds the required Intercom API version header. Without this header, Intercom may answer using a different API shape than this connector expects.

**Data flow**: It receives the base URL and resolved credential. It asks the parent connector to build the authenticated HTTP client, adds `Intercom-Version: 2.11` to its headers, and returns the ready-to-use client.

**Call relations**: This runs as part of connector setup before pagination begins. The returned client is then passed through the paging methods that make GET and POST requests to Intercom.


##### `IntercomConnector._build_search_body`  (lines 119–149)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the request body for Intercom’s search endpoints. It includes page size, sorting, a page cursor for moving through results, and a filter that asks for records newer than the saved sync cursor.

**Data flow**: It receives the stream being searched, the saved sync cursor, and Intercom’s `starting_after` page marker. It creates a JSON body with pagination and ascending sort order, converts numeric cursor text back into a number when possible, and returns the body for a POST request.

**Call relations**: Search-based paginators call this before each request. `IntercomConnector._paginate_search` uses it for normal search streams, and `IntercomConnector._paginate_conversation_parts` uses it to find parent conversations before fetching their parts.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 152–157)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: Safely picks the first dictionary from a list-like nested Intercom field. It is a small guard against missing, empty, or oddly shaped data.

**Data flow**: It receives any value. If that value is a non-empty list and its first item is a dictionary, it returns that dictionary. Otherwise it returns nothing.

**Call relations**: Flattening helpers use this when Intercom wraps related objects in lists, such as a conversation’s contacts or a contact’s companies. It keeps those helpers simple and avoids assuming the API response is always perfectly shaped.


##### `IntercomConnector._flatten_conversation`  (lines 160–177)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes important nested conversation fields easier to read later. It lifts source details and the first requester contact ID onto simple top-level keys.

**Data flow**: It receives one conversation record. It copies the record, then, if `source` is a dictionary, adds flat fields for source type, subject, and body. If contacts are present, it finds the first contact and adds its ID as `requester_id`. The original nested data is kept too.

**Call relations**: This is called by `IntercomConnector.flatten` only for the conversations stream. It prepares conversation records for later processing that expects easy top-level fields rather than deeply nested envelopes.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 180–189)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Makes a conversation part’s author easy to inspect. Intercom nests the author object, and this helper exposes the author type and ID as top-level fields.

**Data flow**: It receives one conversation part record. It copies the record, reads the nested `author` object when present, and adds `author_type` and `author_id`. It leaves any existing `conversation_id` alone.

**Call relations**: This is called by `IntercomConnector.flatten` for conversation part records. It works after the conversation-part paginator has already stamped each part with the parent conversation ID.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 192–200)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds a contact’s first associated company ID as a simple `org_id` field. This helps later transforms connect people to organizations without digging through Intercom’s nested company wrapper.

**Data flow**: It receives one contact record. It copies the record, looks inside the nested companies data, finds the first company dictionary if one exists, and writes that company’s `id` or `company_id` to `org_id`.

**Call relations**: This is called by `IntercomConnector.flatten` for contact records. It uses the shared `_first` helper to safely read the first company.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 202–216)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records after they are fetched from Intercom. It exposes a few useful nested fields as plain top-level fields and converts integer cursor values to strings so UFO’s watermark system can store them.

**Data flow**: It receives a record and its stream. Depending on the stream name, it passes the record through the matching flattening helper for conversations, conversation parts, or contacts. Then, if the stream has a cursor field and that value is an integer, it returns a copy with that cursor value changed to text; otherwise it returns the record as-is.

**Call relations**: The sync flow calls this after pages are retrieved and before records are stored or transformed. It delegates stream-specific reshaping to `_flatten_conversation`, `_flatten_conversation_part`, and `_flatten_contact`.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 218–234)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the public paging loop for Intercom streams. It yields batches of records and turns Intercom permission refusals into a clean skipped-stream signal instead of crashing the whole run.

**Data flow**: It receives an HTTP client, a stream description, and the saved cursor. It asks `_stream_pages` for the right page iterator and yields each page it produces. If Intercom responds with HTTP 401 or 403, it raises `StreamSkipped` with a message explaining that the grant likely lacks the needed scope.

**Call relations**: The source-sync framework calls this when it wants records for a stream. This method wraps the lower-level pagination methods with consistent error behavior for permission problems.

*Call graph*: calls 2 internal fn (__init__, _stream_pages).


##### `IntercomConnector._stream_pages`  (lines 236–254)

```
def _stream_pages(self, client: httpx.AsyncClient, stream: StreamSpec, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct paging strategy for a stream. Intercom uses several different API patterns, so this function acts like a switchboard that sends each stream to the matching fetcher.

**Data flow**: It receives the HTTP client, stream, and cursor. It checks the stream name against known groups: search streams, company scroll, plain lists, attributes, conversation parts, company segments, and activity logs. It returns the async page iterator for the selected strategy, or raises an error if the stream has no known strategy.

**Call relations**: `IntercomConnector.paginate` calls this before yielding pages. From here, control passes to one of the specialized pagination methods that knows the exact Intercom endpoint and response shape.

*Call graph*: calls 7 internal fn (_paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search); called by 1 (paginate).


##### `IntercomConnector._paginate_search`  (lines 256–276)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads streams that use Intercom’s search API, such as conversations, contacts, and tickets. It keeps asking for the next page until Intercom says there is no next `starting_after` marker.

**Data flow**: It receives the HTTP client, the stream, and the saved cursor. For each loop, it builds a search request body, posts it to the stream’s search path, extracts the list of records from the correct response key, yields that list when non-empty, and then reads the next page marker.

**Call relations**: `_stream_pages` selects this for search-based streams. It relies on `_build_search_body` to make each request match both the saved sync cursor and Intercom’s page-by-page cursor.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (_stream_pages).


##### `IntercomConnector._paginate_scroll`  (lines 278–292)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads companies using Intercom’s scroll API. A scroll API is like being given a ticket for the next window of results; each response may include a `scroll_param` to continue.

**Data flow**: It starts with no scroll parameter, calls `/companies/scroll`, yields the returned company records, and then repeats with the returned `scroll_param`. It stops when there are no records or no next scroll parameter.

**Call relations**: `_stream_pages` uses this for the companies stream. Company-related substreams, such as company segments, have their own paginator because they must fetch extra child data for each company.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_list`  (lines 294–306)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads small Intercom streams that come back as a single list, such as admins, tags, teams, and segments. These endpoints do not need multi-page cursor logic here.

**Data flow**: It receives the HTTP client and stream, calls the stream’s list endpoint, then looks for a list under either the stream name or `data`. If it finds a non-empty list, it yields it once and stops.

**Call relations**: `_stream_pages` chooses this for plain list streams. It is the simplest pagination path because Intercom returns the whole stream in one response shape.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_attributes`  (lines 308–317)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Intercom data attribute definitions for companies or contacts. These are metadata fields that describe what custom attributes can exist on those objects.

**Data flow**: It receives the HTTP client and stream. It maps the stream name to the Intercom model name, calls `/data_attributes` with that model as a parameter, filters the returned `data` list down to dictionary records, and yields them if any exist.

**Call relations**: `_stream_pages` selects this for company and contact attribute streams. The resulting records later use custom identity and reference logic because attribute records do not behave exactly like normal Intercom objects.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_conversation_parts`  (lines 319–351)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the individual messages or events inside conversations. Intercom exposes these as child data, so the connector first finds conversations and then fetches each conversation’s details to collect its parts.

**Data flow**: It receives the HTTP client and saved cursor. It searches conversations page by page using the conversation stream’s cursor, then for each conversation with an ID, calls its detail endpoint. It extracts `conversation_parts`, stamps each part with the parent `conversation_id` when missing, yields any parts found, and continues until there is no next search page.

**Call relations**: `_stream_pages` chooses this for the conversation_parts stream. It uses `_build_search_body` to page through parent conversations, then performs extra GET requests so child records can be synced as their own stream.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (_stream_pages).


##### `IntercomConnector._paginate_company_segments`  (lines 353–377)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the segments attached to each company. Because these segment memberships are reached through each company, the function first scrolls through companies and then asks Intercom for the segments of each one.

**Data flow**: It scrolls through `/companies/scroll`, reads each company ID, calls `/companies/{id}/segments`, and stamps each returned segment with `company_id` when missing. It yields segment lists as they are found and continues with the next company scroll page until Intercom stops providing more.

**Call relations**: `_stream_pages` selects this for the company_segments stream. It combines the company scroll pattern with per-company child requests, similar in spirit to the conversation-parts flow.

*Call graph*: called by 1 (_stream_pages).


##### `IntercomConnector._paginate_activity_logs`  (lines 379–401)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads admin activity logs, optionally starting after a saved creation time. It follows Intercom’s next-page links until there are no more log pages.

**Data flow**: It receives the HTTP client and cursor. If a cursor exists, it sends it as `created_at_after` on the first request to `/admins/activity_logs`. For each response, it yields any `activity_logs`, then reads the next-page link and converts a full URL into a path if needed before continuing.

**Call relations**: `_stream_pages` uses this for the activity_logs stream. Unlike search streams, it follows URL-style next links from the response rather than building `starting_after` search bodies.

*Call graph*: called by 1 (_stream_pages).


### `extensions/sources/ufo_ext_sources/providers/zendesk.py`

`io_transport` · `source sync`

Zendesk exposes many different lists of information, but they do not all use the same style of API paging. This file hides those differences behind one connector, so the rest of the project can simply ask for a stream like “tickets” or “articles” and receive records in batches.

The file first defines the Zendesk streams the system knows about. A stream is a named feed of records, with details like its main ID field and which timestamp should be used to continue from a previous sync. Some high-volume streams, such as tickets and users, use Zendesk’s incremental export API. That means the connector starts from a saved time cursor and asks only for records changed since then. Other streams use ordinary page-by-page links.

A few streams need special treatment. Ticket records can ask Zendesk to include related users in the same response, and this file copies user emails onto the ticket records so they are easier to use later. Ticket comments are not fetched from a simple comments endpoint; they are extracted from ticket event records. User identities require first reading users, then asking Zendesk for each user’s identities.

If Zendesk refuses access with a permission or authentication error, the connector marks that stream as skipped instead of pretending it succeeded. The connector only reads data; it does not write back to Zendesk.

#### Function details

##### `_stream`  (lines 58–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a stream description for one kind of Zendesk data. It keeps the long list of supported Zendesk streams readable by filling in common defaults, such as using “id” as the main record key and “updated_at” as the usual progress timestamp.

**Data flow**: It receives a stream name plus optional details, such as the Zendesk API object name or cursor field. It combines those choices with safe defaults and returns a StreamSpec, which is the project’s small recipe for how to sync that stream.

**Call relations**: The file uses this helper while building the Zendesk stream list. It hands the finished stream recipe to StreamSpec so the shared source framework can later know what each stream is called and how progress should be tracked.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 142–167)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: This helper enriches records with related data that Zendesk sent alongside the main records. In this file, it is used to copy requester, submitter, and assignee email addresses from sideloaded user records onto ticket records.

**Data flow**: It receives the main records, the full API response page, and instructions for which related objects to match. It builds a quick lookup table from the related records, finds matching IDs on each main record, and writes the matching email into the target field when it is missing.

**Call relations**: The incremental ticket pagination flow calls this after downloading a page that includes both tickets and users. It does not fetch anything itself; it only reshapes the data already returned by Zendesk before the connector yields the records onward.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 176–177)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This helper decides which field in a Zendesk API response contains the records for a stream. Most streams use their own stream name, but a few Zendesk endpoints use different names such as “audits” or “policies.”

**Data flow**: It receives a stream description. It checks whether that stream has a known response-field override, and returns either the override or the stream’s own name.

**Call relations**: The default pagination path calls this before reading records from a response page. This keeps Zendesk’s naming quirks in one place instead of spreading special cases through the paging loop.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 180–192)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: This helper turns a saved sync cursor into a Unix timestamp, which is the number of seconds since January 1, 1970. Zendesk’s incremental export endpoints need that number to know where to start.

**Data flow**: It receives a cursor that may be empty, already numeric, or an ISO-style date string. It returns 0 when there is no usable cursor, returns the number directly for numeric text, or parses the date and converts it into seconds.

**Call relations**: The incremental ticket/user/organization-style pagination, ticket comment extraction, and user identity sync all call this when building their first Zendesk request. It prepares the starting point before those functions begin following pages.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 195–204)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: This helper turns Zendesk’s next-page URL into just the path and query string needed for the connector’s HTTP request method. It is like taking a full mailing address and keeping only the street-and-house part because the city is already known.

**Data flow**: It receives a next-page URL or nothing. If the URL is usable, it parses it, keeps the path, adds the query string if present, and returns that shorter request path; otherwise it returns nothing.

**Call relations**: Every pagination method calls this after reading a Zendesk page. It tells the loop where to go next, whether the page link came from a normal next_page field or an incremental after_url field.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 206–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway the rest of the source framework uses to read one Zendesk stream. It chooses the correct paging strategy for the requested stream and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks the stream name, delegates to the right specialized pagination method, and passes each batch back to the caller. If Zendesk responds with a permission or authentication refusal, it raises StreamSkipped so the sync can report that this stream could not be read.

**Call relations**: The shared RestConnector framework calls this when it needs records from Zendesk. This function then routes ticket comments, user identities, incremental cursor streams, and ordinary streams to their matching helper methods.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 232–253)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Zendesk streams that support incremental cursor export, such as tickets, users, organizations, and ticket metric events. It is built for high-volume data where a sync should resume from the last known time instead of starting over.

**Data flow**: It receives an HTTP client, a stream description, and a saved cursor. It converts the cursor into a start timestamp, builds the first incremental API path, downloads pages, extracts the records, optionally enriches tickets with sideloaded user emails, yields non-empty batches, and follows Zendesk’s next cursor link until the stream ends.

**Call relations**: The main paginate method calls this for streams listed as incremental cursor streams. Inside the loop it relies on _cursor_to_unix to choose the starting time, _apply_sideload to enrich ticket records when needed, and _next_page_path to continue through Zendesk’s page links.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 255–265)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Zendesk streams that use ordinary page-by-page listing endpoints. It is the simple path for data where Zendesk returns a list of records plus a next_page link.

**Data flow**: It receives an HTTP client and a stream description. It builds the first API path, figures out which response field contains the records, downloads each page, yields non-empty batches, and follows next_page until there are no more pages.

**Call relations**: The main paginate method calls this when a stream does not need one of the special flows. It asks _data_field how to find records in each response and _next_page_path how to move to the next page.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 267–299)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads ticket comments even though Zendesk exposes them through ticket events rather than a plain comments list. It filters the event feed down to comment events and turns each comment into its own record.

**Data flow**: It receives an HTTP client and an optional saved cursor. It starts the incremental ticket events feed from that cursor, looks through each event’s child events, keeps only children marked as comments, adds the parent ticket ID, normalizes numeric creation times into readable timestamp strings, yields comment batches, and follows the next page until the event stream ends.

**Call relations**: The main paginate method calls this only for the ticket_comments stream. It uses _cursor_to_unix to start at the right time and _next_page_path to keep following Zendesk’s incremental paging links.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate); 1 external calls (fromtimestamp).


##### `ZendeskConnector._paginate_user_identities`  (lines 301–326)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads the identities attached to Zendesk users, such as login or contact identities. Zendesk requires a per-user request for these, so the connector first walks through changed users and then asks for each user’s identities.

**Data flow**: It receives an HTTP client and an optional saved cursor. It reads users from Zendesk’s incremental user export, skips invalid user entries, requests each user’s identities page by page, yields any identity records found, and then moves to the next page of users until the user stream ends.

**Call relations**: The main paginate method calls this for the users_identities stream. It uses _cursor_to_unix to choose the user starting point and _next_page_path both for identity pagination and for continuing through pages of users.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).
