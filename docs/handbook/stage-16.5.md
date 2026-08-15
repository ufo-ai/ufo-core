# Productivity, collaboration, and work-management source connectors  `stage-16.5`

This stage is a set of read-only “source connectors.” They run during sync, when the system gathers outside work information and turns it into searchable pages. Each connector knows one service’s layout and API, meaning the web doorway the service provides for reading data.

Airtable reads bases, tables, and records. Asana, ClickUp, Jira, Linear, monday.com, and Wrike read project spaces, tasks or issues, comments, users, boards, goals, and related work details. Calendly and Google Calendar read scheduling data, including events, invitees, memberships, and attendees. Confluence, Google Docs, Google Drive, Google Meet, Google Sheets, and Notion turn documents, pages, files, meeting notes, spreadsheets, comments, and revisions into plain readable text. GitHub reads repositories, issues, commits, users, and other development records. Gmail and Outlook read mail, contacts, calendars, folders, and change feeds so later syncs can fetch only updates. Slack and Microsoft Teams read people, channels, chats, messages, and threads.

Together, these files act like translators at many office doors: they only look inside, collect allowed records, and reshape them into the system’s common page format.

## Files in this stage

### Project databases and scheduling
These connectors start the stage with structured productivity sources, calendar booking data, and nested project-management records.

### `extensions/sources/ufo_ext_sources/airtable.py`

`io_transport` · `during Airtable source sync`

Airtable is organized in layers: a base contains tables, and tables contain records. There is no single “give me everything” endpoint, so this connector acts like a careful librarian walking shelf by shelf. First it asks Airtable for the list of bases. Then, for each base, it asks for the tables inside it. Finally, for each table, it reads records in pages of up to 100 items at a time.

The file defines three streams of data: bases, tables, and records. A stream is simply one kind of thing the sync system can read. Bases are treated as the main, canonical stream. Tables and records are discovered from those bases.

As it reads, the connector adds helpful context. A table is tagged with the base it came from. A record is tagged with its base and table. This matters because record IDs alone are not enough for a downstream reader to understand where the record lives in Airtable.

The connector only reads. It does not create or update Airtable data. Authentication and low-level HTTP behavior come from the shared REST connector it inherits from, while this file supplies the Airtable-specific routes, paging rules, and record shaping.

#### Function details

##### `AirtableConnector._bases`  (lines 39–41)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Airtable for the list of bases the connected account can access. A base is Airtable’s top-level container, similar to a workbook that contains multiple sheets.

**Data flow**: It receives an HTTP client that is already ready to talk to Airtable. It calls Airtable’s metadata endpoint for bases, pulls the actual list out of the response, and returns that list as ordinary dictionaries.

**Call relations**: The main pagination flow calls this whenever it needs to start from Airtable’s top level. It relies on the shared record-extraction helper to pick the bases list out of Airtable’s response shape.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 43–50)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This finds the tables inside one Airtable base. It also labels each returned table with the base it came from, so later steps do not lose that context.

**Data flow**: It receives an HTTP client and one base record. It reads the base ID from that record; if the ID is missing or unusable, it returns an empty list. Otherwise it asks Airtable for that base’s tables, extracts the table list, adds the base ID and base name to each table, and returns the enriched list.

**Call relations**: The main pagination flow calls this after discovering bases. It uses the shared record-extraction helper to read Airtable’s table list and the shared context helper to attach origin information.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 52–70)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the records from one Airtable table, one page at a time. It is built as an asynchronous iterator, meaning it can yield pages as they arrive instead of waiting for the whole table to be downloaded.

**Data flow**: It receives an HTTP client, a base ID, and a table record. It reads the table ID; if the table ID is missing or invalid, it stops. Otherwise it repeatedly asks Airtable for record pages using Airtable’s offset token for paging. Each non-empty page is tagged with the base ID, table ID, and table name before being yielded onward.

**Call relations**: The records branch of the main pagination flow calls this for every discovered table. It depends on the inherited cursor-paging helper to follow Airtable’s offset tokens, and it uses the context helper so every record keeps its Airtable location.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 72–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading route for the connector. Given a requested stream, it decides whether to return bases, tables, or records, and then walks Airtable’s hierarchy in the right order.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. For the bases stream, it fetches bases and yields them as one page. For the tables stream, it fetches bases, gathers their tables, and yields batches when they reach the page size. For the records stream, it fetches bases, then tables, then yields record pages from each table. If asked for a stream this connector does not know, it raises a skip signal instead of pretending it can read it.

**Call relations**: This is the function the shared sync machinery calls to get Airtable data. It coordinates the helper methods in order: bases first, then tables for each base, then records for each table. When the stream name is unknown, it hands control back by raising the standard stream-skipped exception.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 103–125)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes Airtable objects into a cleaner form for the rest of the system. It adds useful URLs for bases and tables, and normalizes record fields so downstream code sees a predictable shape.

**Data flow**: It receives one Airtable record and the stream it belongs to. For a base, it keeps the original data and adds a stable API URL. For a table, it adds the table API URL using the stored base ID. For a record, it copies the record ID, turns Airtable’s creation timestamp into a clearer created_at field, and ensures fields is a dictionary. It returns the reshaped record without changing the original stream flow.

**Call relations**: After pages are read by the pagination path, the broader source framework can call this to prepare each item for storage or indexing. Unlike the pagination helpers, it does not fetch more data; it only tidies one already-fetched object.


### `extensions/sources/ufo_ext_sources/asana.py`

`io_transport` · `during source sync`

Asana stores work-tracking information behind a web API, and it does not send everything at once. Instead, it returns one page at a time, with the actual records inside a `data` list and a `next_page` pointer when more records are waiting. This file is the Asana-specific adapter that knows those rules.

The file first defines the Asana streams the system can read. A stream is one kind of Asana object, such as `tasks`, `projects`, or `users`. Each stream says what field uniquely identifies a record (`gid`) and, for some streams, what time field can be used to pick up only changed records on later syncs. Tasks and projects can use Asana’s `modified_since` filter, so they can be synced incrementally. Most other streams are fetched fully each time because Asana does not offer the same change filter for them.

The `AsanaConnector` then provides the actual reading behavior. It builds requests to Asana’s API, asks for up to 100 records per page, yields any records it receives, and follows Asana’s offset token until there are no more pages. It does not write anything back to Asana, and it does not keep an API token itself; authentication is supplied by the wider runner through its proxy system.

#### Function details

##### `_stream`  (lines 24–38)

```
def _stream(name: str, *, cursor_field: str | None=None, updated_at_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard stream description for one kind of Asana object. This keeps the stream list short and consistent, so each Asana collection is described the same way.

**Data flow**: It receives a stream name plus optional fields that describe how to track changes and whether the stream is a main, important one. It fills in the shared Asana defaults, especially that records are identified by `gid`, and returns a `StreamSpec` object that the connector can later use when fetching data.

**Call relations**: This helper is used while building the file’s Asana stream catalog. It hands its settings to `StreamSpec`, which is the shared source-system object that tells the rest of the connector framework what each stream is called and how it should be tracked.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 74–90)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Asana stream page by page and yields batches of records. Someone uses it when the system needs to sync a collection such as tasks, projects, or users from Asana.

**Data flow**: It takes an HTTP client, a stream description, and an optional saved cursor from a previous sync. It builds an Asana API request with a page limit of 100, adds `modified_since` only for streams where Asana supports incremental changes, then repeatedly fetches pages. From each response it pulls the `data` list, turns missing or invalid data into an empty list, yields non-empty batches, and follows `next_page.offset` until Asana says there are no more pages.

**Call relations**: The broader REST connector framework calls this method when it is time to read records for a stream. Inside the loop, it relies on the inherited `_get` request method to contact Asana, and it uses `ufo.sdk.sources.list_or_empty` to safely normalize the response’s `data` field before handing records back to the sync pipeline.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/calendly.py`

`io_transport` · `source sync`

Calendly stores scheduling data behind a web API, and most of the useful records belong to an organization. This connector is the bridge between that API and the rest of the sync system. Without it, the project would not know which Calendly endpoints to call, how to page through long result lists, or how to shape Calendly’s responses into records that can be stored and searched later.

The connector first asks Calendly who the authenticated user is by reading `/users/me`. From that response it finds the user’s current organization. That organization value is then added to most later requests, like writing the correct company name on a folder before filing papers into it. If the account does not reveal an organization, organization-level streams are skipped rather than guessed.

Long Calendly lists are read page by page using Calendly’s `next_page_token`. Some streams also use a saved cursor, meaning a remembered timestamp, so later syncs can ask only for newer or relevant records. Invitees are a special case: Calendly exposes them under each scheduled event, so the connector first reads events, extracts each event’s ID from its URI, and then reads that event’s invitees.

Finally, `flatten` lightly reshapes records so important fields such as name, email, title, times, and location are easy for the rest of the system to use.

#### Function details

##### `_uuid_from_uri`  (lines 61–64)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: This small helper pulls the final ID-like piece out of a Calendly URI. It is used when the connector needs the event ID from a full event link before calling invitee endpoints.

**Data flow**: It receives any value that might be a URI. If the value is not a non-empty string, it returns nothing. If it is a string, it removes a trailing slash if present, splits on the last slash, and returns the final piece.

**Call relations**: The invitee-reading flow calls this while walking through scheduled events. The result becomes the event identifier used to ask Calendly for invitees for that specific event.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 72–75)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: This asks Calendly for the account tied to the current credential. The connector uses that user record both as its own stream and as the way to discover the current organization.

**Data flow**: It receives an HTTP client that already knows how to make requests. It calls `/users/me`, looks for the `resource` object in the response, and returns that object if it is a dictionary. If the response shape is not as expected, it returns an empty dictionary.

**Call relations**: The organization-stream helper calls this before reading organization-scoped data. The main `paginate` method also calls it directly when syncing the `api_user` stream.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 77–90)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Calendly list endpoint one page at a time. It hides the repeated work of following Calendly’s next-page token so callers can simply receive batches of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST connector paging helper to read records from the response’s `collection` field, follow `pagination.next_page_token`, send that token back as `page_token`, and request up to 100 records at a time. It yields each page as a list of dictionaries.

**Call relations**: Organization streams use this to read event types, groups, memberships, and scheduled events. The invitee flow also uses it after it has built the per-event invitee path.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 92–108)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the common path for Calendly data that belongs to an organization. It finds the current organization, adds it to the request, optionally adds an incremental cursor, and then yields pages with organization context attached.

**Data flow**: It receives an HTTP client, an endpoint path, and optionally a cursor plus the Calendly parameter name that should carry that cursor. It reads the current user, extracts `current_organization`, and stops the stream with `StreamSkipped` if there is no usable organization. Otherwise it builds query parameters, reads paged results, adds the organization value to every record as context, and yields the pages.

**Call relations**: The main `paginate` dispatcher calls this for most Calendly streams. The invitee flow also calls it first to discover scheduled events before reading the invitees under each event.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 110–128)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads event invitees, which Calendly exposes underneath each scheduled event rather than as one simple organization-wide list. It gathers invitees event by event and adds information about which scheduled event they came from.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It first reads scheduled events through the organization stream, pulls each event’s UUID from its URI, and skips events that do not have a usable ID. For each event, it pages through `/scheduled_events/{event_uuid}/invitees`. If a cursor is present, it keeps only invitees whose `created_at` value is newer than that cursor. It yields non-empty invitee pages with the parent event URI and UUID attached.

**Call relations**: The main `paginate` method calls this for the `event_invitees` stream. Inside, it relies on `_org_stream` to find events, `_uuid_from_uri` to extract event IDs, `_paginate_collection` to read invitee pages, and `with_context` to preserve the parent event connection.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 130–162)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main routing function for reading Calendly streams. Given a stream name, it chooses the right Calendly endpoint and the right cursor behavior.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. For `api_user`, it returns the current user as a one-record page. For organization streams, it delegates to `_org_stream`, sometimes passing the cursor as Calendly’s `updated_since` or `min_start_time` parameter. For invitees, it delegates to `_invitees`. If the stream name is unknown, it raises `StreamSkipped` so the sync system knows this connector cannot read it.

**Call relations**: The wider source-sync engine calls this when it wants records for a specific Calendly stream. This function then hands the work to `_current_user`, `_org_stream`, or `_invitees`, depending on what kind of Calendly data is being requested.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 164–204)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes raw Calendly records into a friendlier form for storage and later recall. It keeps the original data but copies important nested or differently named fields into predictable top-level names.

**Data flow**: It receives one Calendly record and the stream it belongs to. Depending on the stream, it adds fields such as `name`, `email`, `created_at`, `title`, `description`, `start_at`, `end_at`, `location`, or `api_url`. For organization memberships, it pulls name and email out of the nested `user` object and removes that nested user object from the output so later user profile changes do not make the membership record look changed. It returns the reshaped dictionary.

**Call relations**: After `paginate` has produced raw pages, the broader connector machinery can call this before storing records. It uses `dict_or_empty` for membership records so missing or malformed nested user data becomes a safe empty dictionary instead of causing an error.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/clickup.py`

`io_transport` · `source sync`

ClickUp does not present all work items in one simple list. Its data is arranged like a set of boxes inside boxes: a team contains spaces, a space contains folders and lists, and lists contain tasks, comments, and custom fields. This connector exists to open those boxes in the right order so nothing important is missed.

The file defines the ClickUp streams the system knows how to sync, such as teams, users, tasks, and list comments. The ClickUpConnector then uses ClickUp’s REST API, which means it asks ClickUp for data over normal web requests. It starts at teams, then uses the discovered team IDs to find spaces, uses space IDs to find folders, and uses folder or space IDs to find lists. Once it reaches lists, it can read the leaf data: tasks, list comments, and list custom fields.

As it reads child records, it adds useful parent information, such as the list ID or team ID. That is like writing the room number on every item taken from a building, so later readers know where it came from. It also supports cursor-based syncing for some streams, meaning it can skip records older than the last successful sync. This keeps repeat syncs smaller and faster. The connector is read-only; it fetches ClickUp data but does not create or update anything in ClickUp.

#### Function details

##### `ClickUpConnector._teams`  (lines 58–60)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the ClickUp teams available to the authenticated account. This is the starting point for most other reads, because ClickUp’s spaces, goals, and users are found through teams.

**Data flow**: It receives an HTTP client that can make web requests. It asks ClickUp for the /team endpoint, then pulls the teams list out of ClickUp’s response. It returns a plain list of team records.

**Call relations**: This is the first step in the hierarchy. _spaces calls it so it can look up spaces under each team, and paginate calls it directly when the requested stream is teams, users, or goals.

*Call graph*: called by 2 (_spaces, paginate); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 62–70)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived spaces under all ClickUp teams. Spaces are the next level down from teams and are needed before the connector can discover folders and some lists.

**Data flow**: It starts by asking _teams for team records. For each team with a usable ID, it requests that team’s spaces from ClickUp, extracts the space records, and adds the parent team_id to each one. It returns one combined list of spaces.

**Call relations**: This builds on _teams. _folders uses it to find folders inside spaces, _lists uses it to find lists that live directly under spaces, and paginate uses it when the spaces stream is requested.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 72–82)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived folders inside ClickUp spaces. Folders matter because many ClickUp lists live inside them.

**Data flow**: It receives an HTTP client, calls _spaces to get all known spaces, and then asks ClickUp for folders inside each valid space. It extracts the folder records and adds the parent space_id to each folder before returning the combined list.

**Call relations**: This is the bridge between spaces and folder-based lists. _lists calls it to find lists inside folders, and paginate calls it directly when syncing the folders stream.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 84–100)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Collects all non-archived lists the connector can find, whether they are inside folders or directly inside spaces. Lists are important because tasks, comments, and custom fields are read from lists.

**Data flow**: It first calls _folders, then asks ClickUp for lists inside each folder and tags those records with folder_id. It then calls _spaces and asks for lists that are directly under each space, tagging those with space_id. It returns one combined list of list records.

**Call relations**: This is the gateway to the leaf data. _tasks calls it before reading tasks, _list_child_stream calls it before reading comments or custom fields, and paginate calls it directly when syncing the lists stream.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _tasks, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 102–127)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads tasks from every discovered ClickUp list, including closed tasks and subtasks. It can also skip tasks that have not changed since a saved cursor, which helps incremental syncs avoid rereading old data.

**Data flow**: It receives an HTTP client and an optional cursor value, which represents the last seen update time. It calls _lists, then for each valid list asks ClickUp for task pages one page at a time. It adds list_id and list_name to each task, filters out tasks whose date_updated is not newer than the cursor, and yields each non-empty batch of tasks. It stops paging a list when ClickUp returns no tasks for the page.

**Call relations**: paginate hands control to this function when the tasks stream is requested. This function depends on _lists to know where tasks live, and it uses the shared record-extraction and context helpers to turn ClickUp responses into useful batches.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 129–148)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads list-level child data, specifically list comments or list custom fields, from every discovered ClickUp list. It is shared code for two similar streams so the connector does not duplicate the same walk through lists.

**Data flow**: It receives an HTTP client, the stream being synced, and an optional cursor. It calls _lists, chooses the correct ClickUp endpoint for either comments or fields, extracts the right records from the response, and adds list_id and list_name. If the stream has a cursor field and a cursor was provided, it keeps only records newer than that cursor. It yields each non-empty set of records.

**Call relations**: paginate calls this when the requested stream is list_comments or list_custom_fields. This function relies on _lists to discover the lists first, then hands back batches of child records for the sync flow to consume.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 150–204)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main dispatcher for reading a ClickUp stream. Given a stream name, it decides which helper should fetch the data and yields the results in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. For simple hierarchy streams, it calls the matching helper and yields the list if it is not empty. For users, it pulls members embedded inside team records and deduplicates them by user ID. For tasks, comments, and custom fields, it delegates to streaming helpers that may yield multiple batches. For goals, it reads goals under each team and adds team_id. If the stream name is unknown, it raises StreamSkipped to tell the sync system this stream is not implemented.

**Call relations**: This is the connector’s central read path. The broader REST source framework calls paginate to get records for a chosen stream, and paginate then calls _teams, _spaces, _folders, _lists, _tasks, or _list_child_stream as needed. It is the place where the flat idea of 'sync this stream' is translated into ClickUp’s nested API calls.

*Call graph*: calls 7 internal fn (__init__, _folders, _list_child_stream, _lists, _spaces, _tasks, _teams); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 206–240)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes ClickUp records into easier-to-use shapes after they have been fetched. It gives common fields consistent names, such as name, created_at, status, author, and parent_external_id.

**Data flow**: It receives one record and the stream it belongs to. Depending on the stream, it copies the original record and adds or rewrites a few useful fields: users get a name and created_at, spaces/folders/lists get an API URL, tasks get a plain status and created_at, and comments get body, author, created_at, and a parent list reference. It returns the cleaned-up record without changing the original input in place.

**Call relations**: After paginate has produced raw ClickUp records, the connector’s normal processing can call flatten to make them friendlier for storage and recall. For comment records, it calls dict_or_empty so a missing or malformed user field does not break author extraction.

*Call graph*: 1 external calls (dict_or_empty).


### Atlassian and repository content
These connectors read collaboration knowledge and development artifacts from Confluence and GitHub into searchable source pages.

### `extensions/sources/ufo_ext_sources/confluence.py`

`io_transport` · `source sync runs`

Confluence stores wiki pages as structured HTML-like text, not as simple sentences. If the system saved that raw format directly, people would later see tags, macros, and markup instead of the page content they actually recognize. This connector solves that by fetching Confluence records and reshaping them into clean, searchable prose.

The connector first asks Atlassian which Confluence sites the user’s authorization can reach. One authorization can cover several sites, so every request is made separately for each site. It then walks through each collection in pages of results, like reading a long index one screen at a time. For streams that can be synced incrementally, such as pages and comments, Confluence does not provide a true “only changes since this time” filter. So the connector reads pages of results and locally keeps only records newer than the saved cursor, which is the last-seen timestamp.

Before records are stored, `flatten` gives them consistent fields such as title, body, URL, author, and created time. It also prefixes most IDs with the Confluence site ID so two different sites cannot accidentally produce the same record reference. Finally, `render` uses `_StorageTextExtractor` to strip Confluence storage-format XHTML down to readable text, preserving line breaks around paragraphs, headings, tables, and list items.

#### Function details

##### `_body_text`  (lines 114–116)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: This small helper finds the readable body HTML stored inside a Confluence record. It prefers the storage-format body, and falls back to the view-format body if needed.

**Data flow**: It receives one record as a dictionary-like object. It looks inside nested fields for `body.storage.value` first, then `body.view.value`; if it finds a non-empty string, it returns that string. If the body is missing or not text, it returns nothing.

**Call relations**: When `ConfluenceConnector.flatten` is preparing pages, blog posts, or comments, it calls this helper to pull out the body content before the record is shaped into the system’s standard fields.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 124–150)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Confluence streams. It decides which Confluence API path to use, loops through every reachable Confluence site, and yields batches of records for the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It maps the stream name to the right Confluence endpoint, asks for the sites available to the authorization, then reads each site’s records in pages. Each outgoing batch is tagged with the site’s cloud ID and URL. If Confluence refuses access because the grant is missing permission, it turns that into a skipped stream instead of a hard failure.

**Call relations**: The broader source framework calls this when it wants records for a stream. Inside, it asks `_sites` which Confluence sites are available, delegates the page-by-page API reading to `_offset_results`, and wraps each batch with site context using `with_context`. If a stream is unknown or access is refused, it raises `StreamSkipped` so the run can report a clean skip.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 152–156)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Confluence sites the current authorization can access. That matters because all later Confluence API calls must be scoped to a specific site ID.

**Data flow**: It receives an HTTP client, calls Atlassian’s accessible-resources endpoint, reads the JSON response, and returns it as a list. If the response is missing or not shaped like a list, it safely returns an empty list.

**Call relations**: `paginate` calls this before reading any stream data. The returned site entries provide the `cloud_id` that `paginate` uses to build each site-specific Confluence API path.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 158–182)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: This walks through a Confluence collection one page of API results at a time. It also performs local cursor filtering for streams where only newer records should be kept.

**Data flow**: It receives an HTTP client, an API path, optional request parameters, an optional cursor, and the name of the cursor field. It repeatedly requests results using `start` and `limit`, extracts the `results` list, optionally removes records whose cursor value is not newer than the saved cursor, and yields non-empty batches. It stops when there are no records to yield or Confluence no longer provides a next-page link.

**Call relations**: `paginate` uses this for each site and stream after it has built the correct API path. This helper performs the repeated API calls and hands each usable batch back to `paginate`, which then adds site context before yielding it to the rest of the sync.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 184–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes raw Confluence records into the fields the rest of the system expects. It makes pages, blog posts, comments, spaces, and other records easier to identify, display, search, and sync safely.

**Data flow**: It receives one raw record and its stream description. Depending on the stream, it copies useful fields into clearer names such as `title`, `body`, `url`, `created_at`, `updated_at`, `author`, and `parent_external_id`. For most streams, it changes the primary ID into `cloud_id:id` so records from different Confluence sites do not collide. If the cursor field is nested, such as `version.createdAt`, it also copies that value to the flat cursor key.

**Call relations**: The connector framework uses this after records have been fetched by `paginate`. It calls `_body_text` for content-bearing records and uses `get_path` to read nested Confluence fields without assuming every field is present.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 234–250)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a stored Confluence record into a human-readable title and text body. It is especially important for pages, blog posts, comments, and spaces because their descriptions or bodies may contain Confluence HTML-like markup.

**Data flow**: It receives a flattened record and stream description. For pages, blog posts, and comments, it reads the title and extracts plain text from the body. For spaces, it uses the name or key as the title and extracts text from the description. If there is no title, it falls back to the first body line or to the base connector’s rendering. It returns a title plus a markdown-like text block headed with the Confluence stream name.

**Call relations**: The source framework calls this when it needs recallable text rather than raw JSON. It uses `_str` to safely treat only real strings as strings, `get_path` to read nested descriptions, and `_StorageTextExtractor.extract` to turn Confluence storage XHTML into plain prose. Streams not specially handled here are passed back to the parent connector’s default renderer.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 259–261)

```
def __init__(self) -> None
```

**Purpose**: This prepares a fresh text extractor for one piece of Confluence HTML-like content. It sets up the parser so character entities, such as `&amp;`, become normal readable characters.

**Data flow**: It receives no content directly. It initializes the parent HTML parser and creates an empty list where text fragments and line break markers will be collected as parsing happens. The result is a ready-to-use parser object.

**Call relations**: `_StorageTextExtractor.extract` creates an instance of this class whenever it needs to clean a raw Confluence body. After initialization, the HTML parser calls the handler methods as it reads through the markup.


##### `_StorageTextExtractor.extract`  (lines 264–269)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: This is the easy entry point for converting Confluence storage-format XHTML into plain text. Callers use it when they want readable content without tags, attributes, or macro markup.

**Data flow**: It receives any value. If the value is not a non-empty string, it returns an empty string. Otherwise, it creates a parser, feeds the raw markup into it, then asks the parser to assemble the collected pieces into cleaned text.

**Call relations**: `ConfluenceConnector.render` relies on this when rendering pages, blog posts, comments, and space descriptions. This method drives the parser, which in turn uses `handle_data`, `handle_starttag`, `handle_endtag`, and `_text` to produce the final text.


##### `_StorageTextExtractor.handle_data`  (lines 271–272)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records the actual readable characters found between HTML tags. It is the part that keeps the words people wrote on the Confluence page.

**Data flow**: It receives a text fragment from the parser. It appends that fragment to the extractor’s internal list. Nothing is returned; the extractor’s collected state is changed.

**Call relations**: The built-in HTML parser calls this automatically while `_StorageTextExtractor.extract` is feeding it markup. Later, `_text` combines these saved fragments into the final readable body.


##### `_StorageTextExtractor.handle_starttag`  (lines 274–276)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break marker when a block-like HTML tag begins. That keeps paragraphs, headings, table cells, and list items from being mashed together.

**Data flow**: It receives the tag name and its attributes. If the tag is one of the known block tags, it appends a newline marker to the internal parts list. It ignores attributes completely because they are not useful readable text here.

**Call relations**: The HTML parser calls this during extraction whenever it sees an opening tag. Its newline markers are later cleaned and normalized by `_text`, helping `render` produce readable prose instead of one long run-on line.


##### `_StorageTextExtractor.handle_endtag`  (lines 278–280)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break marker when a block-like HTML tag ends. It gives the extracted text natural separation around paragraphs, headings, list items, and table structure.

**Data flow**: It receives the closing tag name. If that tag is considered block-level, it appends a newline marker to the internal list. It returns nothing and only changes the extractor’s collected parts.

**Call relations**: The HTML parser calls this automatically as it reads closing tags. Together with `handle_starttag`, it gives `_text` enough separators to make the final output easy to read.


##### `_StorageTextExtractor._text`  (lines 282–285)

```
def _text(self) -> str
```

**Purpose**: This turns all collected text fragments and newline markers into the final clean plain-text string. It removes extra whitespace while preserving meaningful line breaks.

**Data flow**: It joins the stored fragments, splits them into lines at newline markers, collapses repeated spaces inside each line, removes blank lines, and trims the result. The output is a clean string ready for search or display.

**Call relations**: `_StorageTextExtractor.extract` calls this after the parser has finished reading the raw markup. It is the final polishing step before `ConfluenceConnector.render` receives readable Confluence content.


##### `_str`  (lines 288–289)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely returns a value only if it is already a string. It prevents titles from accidentally becoming things like numbers, dictionaries, or `None`.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. Otherwise, it returns an empty string.

**Call relations**: `ConfluenceConnector.render` calls this when choosing titles from record fields such as `title`, `name`, or `key`. That keeps the rendering logic simple and avoids treating non-text values as readable titles.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/github.py`

`io_transport` · `during GitHub source syncs, while discovering orgs/repos and fetching paginated API records`

GitHub data is spread across many API endpoints, and most useful records belong to a specific organization or repository. This file solves the problem of discovering those organizations and repositories automatically, then walking through GitHub’s paginated API safely without mixing records from different repos together.

The connector first defines the list of GitHub streams the system knows about, such as repositories, issues, commits, releases, teams, and users. Only streams with a GitHub API path are actually runnable. When a sync starts, the connector finds the organizations available to the credential, lists each organization’s non-archived, non-fork repositories, and then fans out repo-based streams across those repositories.

A key detail is “partition stamping.” Many GitHub IDs are only unique inside one repo, like a branch named `main` or a tag named `v1.0`. The connector adds the repo or organization name to each record before the system builds its page identity. Without that, two repos could overwrite each other’s records, like two houses on different streets both being filed only as “number 10.”

The file also deals with GitHub pagination, resume points, time-based backfills, skipped repos or orgs, and lightweight user enrichment. If GitHub refuses organization listing entirely, the stream is marked skipped rather than failed, because no useful scope can be discovered.

#### Function details

##### `_stream`  (lines 73–93)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', ordering: Ordering=Ordering.none, canonical:
```

**Purpose**: Builds a stream description for one kind of GitHub data, such as issues or commits. A stream description tells the sync system what the records are called, what field identifies them, and whether they can be resumed by a time cursor.

**Data flow**: It receives basic facts about a stream, including its name, primary key, cursor field, ordering style, and backfill settings. It fills in sensible defaults, creates a `StreamSpec` object, and returns that object for the connector’s catalog.

**Call relations**: This helper is used while the module is loaded to create `ALL_STREAMS`. It hands the finished stream definitions to the connector class, which later filters them in `GitHubConnector.streams` and uses them during pagination.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 197–200)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the GitHub streams that this connector can actually fetch today. Some streams are listed for catalog compatibility, but only streams with a wired GitHub API path are runnable.

**Data flow**: It reads the full stream list stored on the connector and checks each stream name against the path table. It returns a shorter list containing only streams that have an API route.

**Call relations**: The sync framework calls this when it wants to know what this connector supports. It acts as the gate between the broader catalog built with `_stream` and the real fetching work done by `GitHubConnector.paginate`.


##### `GitHubConnector._make_client`  (lines 202–206)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to GitHub and adds GitHub-specific headers. These headers tell GitHub which API format and API version the connector expects.

**Data flow**: It receives a base URL and a credential. It asks the base REST connector to create an authenticated `httpx.AsyncClient`, then adds the GitHub `Accept` and API-version headers, and returns the prepared client.

**Call relations**: The wider connector framework calls this during setup before fetching data. The returned client is then passed into pagination and helper methods such as `GitHubConnector.paginate`, which use it for all GitHub API calls.


##### `GitHubConnector.flatten`  (lines 208–248)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Prepares each GitHub record before the system turns it into a stored page. Its most important job is to make record IDs safe across repositories and organizations by prefixing them with the repo or org they came from.

**Data flow**: It receives one GitHub record and its stream description. For stargazers it merges the nested user into the main record, and for pull requests it removes bulky nested repo objects from `head` and `base`. Then it uses `_partition_field` to decide whether the record should carry a repo or org stamp, checks that stamp is present, and prefixes the record’s primary key with it when possible. It returns the shaped record, or raises an error if a partitioned record is missing its partition stamp.

**Call relations**: The sync adapter calls this after records are fetched and before it reads the primary key. It relies on `_partition_field` to understand the stream’s path shape, and its output determines the page reference and title used downstream.

*Call graph*: calls 1 internal fn (_partition_field).


##### `GitHubConnector.paginate_source`  (lines 250–261)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides the standard source pagination entry point for the sync framework. It passes through GitHub’s extra backfill setting so newest-first repository streams can resume safely.

**Data flow**: It receives the HTTP client, stream description, current cursor, current user ID, and optional backfill floor. It does not use the user ID here. It calls `GitHubConnector.paginate` with the cursor and backfill floor, then returns that asynchronous stream of pages.

**Call relations**: The base source runner calls this when it wants records for a stream. This function is a small bridge into `GitHubConnector.paginate`, where the actual GitHub routing and page walking happens.

*Call graph*: calls 1 internal fn (paginate).


##### `GitHubConnector.paginate`  (lines 263–334)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the right GitHub API walk for a stream and yields pages of records. It handles organization streams, repository streams, plain endpoints, and the special rules needed for resumable syncs.

**Data flow**: It receives a client, a stream, an optional cursor, and an optional backfill floor. It looks up the stream’s API path, builds common query parameters like page size, and then branches based on whether the path is org-scoped, repo-scoped, or neither. Repo-scoped streams are fed through `PartitionWalk`, which keeps track of each repository’s resume state. Org-scoped streams loop through granted organizations. The function yields lists of records or richer stream pages, with org or repo context attached where needed.

**Call relations**: It is called by `GitHubConnector.paginate_source`. It calls helper methods to list organizations, list repository pages, enrich users, fetch link-header pages, and create repo-page callbacks. It is the main traffic director for this file: it decides which lower-level helper should fetch each kind of GitHub data.

*Call graph*: calls 4 internal fn (_enrich_users, _iter_granted_org_repo_pages, _iter_user_orgs, _paginate_link_header); called by 1 (paginate_source); 4 external calls (__init__, Semaphore, astimezone, with_context).


##### `GitHubConnector.paginate.repos`  (lines 286–288)

```
async def repos() -> AsyncIterator[str]
```

**Purpose**: Supplies repository names to the partition walker for repo-based streams. It turns discovered repositories into `owner/repo` strings.

**Data flow**: It reads repository identities from `GitHubConnector._iter_user_repos`. For each `(owner, repo)` pair, it joins them into a single repo key string and yields that key.

**Call relations**: This local helper is created inside `GitHubConnector.paginate` when the stream path contains both owner and repo placeholders. `PartitionWalk` calls on it to know which repository partitions to walk.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector.paginate.repo_pages`  (lines 290–291)

```
def repo_pages(repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects the partition walker to the code that fetches one repository’s pages. It packages the stream, path, repo key, and resume bound into a call to `_repo_pages`.

**Data flow**: It receives a repository key and a `PartitionBound`, which describes the current resume window for that repo. It passes those, along with the client, stream, and API path, into `GitHubConnector._repo_pages` and returns the resulting asynchronous page iterator.

**Call relations**: This local helper is also created inside `GitHubConnector.paginate`. `PartitionWalk` uses it whenever it is ready to fetch a bounded slice of one repository.

*Call graph*: calls 1 internal fn (_repo_pages).


##### `GitHubConnector._repo_pages`  (lines 336–407)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Fetches one repository’s pages for a specific stream, using the right time filters and resume boundaries. It also marks each returned record with the repository it came from.

**Data flow**: It receives a client, stream, path template, repo key, and partition bound. It fills the owner and repo into the GitHub path, builds query parameters, and applies different resume rules for ascending streams, commits, and newest-first feeds. It fetches pages with `_paginate_link_header`, filters out pull requests from the issues stream, optionally filters newest-first records on the client side, computes high and low cursor values with `_cursor_bounds`, stamps records with `repo_full_name`, and yields `WalkPage` objects. If GitHub says a repo is unavailable or empty in expected ways, it raises `PartitionSkipped` so that repo can be skipped without failing the whole sync.

**Call relations**: It is called through the `repo_pages` helper created by `GitHubConnector.paginate`. It hands `WalkPage` objects back to `PartitionWalk`, which uses their cursor spans to update resume state for each repository.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); called by 1 (repo_pages); 3 external calls (__init__, __init__, with_context).


##### `GitHubConnector._iter_user_repos`  (lines 409–417)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: Lists repositories that belong to organizations available to the credential. It intentionally avoids the broader `/user/repos` endpoint so the sync follows the granted organization scope.

**Data flow**: It reads pages from `GitHubConnector._iter_granted_org_repo_pages`. For each repository record, it asks `_repo_identity` to find the owner and repo name. When an identity can be found, it yields the `(owner, repo)` pair.

**Call relations**: It is called by the local `repos` helper inside `GitHubConnector.paginate`. Its output becomes the set of repository partitions that `PartitionWalk` walks for repo-scoped streams.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (repos).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 419–438)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: Fetches repository pages for every organization the credential can see, while ignoring archived repositories and forks. This gives the connector the active org-owned repo catalog used for fan-out.

**Data flow**: It first gets organization logins from `GitHubConnector._iter_user_orgs`. For each org, it calls `_paginate_link_header` on `/orgs/{org}/repos` with repository-list parameters. It filters out records marked archived or forked, and yields only non-empty pages paired with their org login. If one organization refuses access or is gone, it skips that org and continues.

**Call relations**: It is used directly by `GitHubConnector.paginate` for the repositories stream, and by `GitHubConnector._iter_user_repos` when repo-scoped streams need a list of repos. It depends on `_iter_user_orgs` for the starting org list.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, paginate).


##### `GitHubConnector._iter_user_orgs`  (lines 440–461)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Lists the GitHub organizations exposed by the credential. This is the root discovery step for almost every runnable stream in this connector.

**Data flow**: It calls `_paginate_link_header` on `/user/orgs`, reads each returned record, and yields valid non-empty `login` strings. If GitHub returns a forbidden response at this root step, it raises `StreamSkipped` to say the whole stream cannot be read because organization scope is missing or blocked.

**Call relations**: It is called by `GitHubConnector.paginate` for org-scoped streams and by `GitHubConnector._iter_granted_org_repo_pages` for repo discovery. Because all organization and repository fan-out starts here, a failure here stops the stream cleanly rather than producing a partial or misleading sync.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, paginate).


##### `GitHubConnector._enrich_users`  (lines 463–483)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: Turns simple organization-member records into fuller public GitHub user records when possible. This can add public profile fields such as name or email.

**Data flow**: It receives a page of user-like records and a semaphore, which is a small lock-like counter that limits how many user lookups run at once. It starts one `one` task per member with `asyncio.gather`, waits for all of them, and returns the enriched list in page order.

**Call relations**: It is called by `GitHubConnector.paginate` only for the `users` stream. It delegates the per-user lookup to the nested `GitHubConnector._enrich_users.one` helper and gives the enriched page back to pagination before the org context is attached.

*Call graph*: called by 1 (paginate); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 469–481)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Fetches a fuller public profile for one GitHub member when the member has a usable login. If the user no longer exists or cannot be found, it keeps the original member record.

**Data flow**: It receives one member record from the surrounding `_enrich_users` call. It reads the `login`, waits for a semaphore slot, calls GitHub’s `/users/{login}` endpoint, and returns the JSON object if it is a dictionary. If the login is missing, the body is not a dictionary, or GitHub returns 404, it returns the original member record.

**Call relations**: This nested helper is launched many times by `GitHubConnector._enrich_users` through `asyncio.gather`. It performs the individual network lookups that make the users stream richer while respecting the concurrency limit.


##### `GitHubConnector._paginate_link_header`  (lines 485–493)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks GitHub endpoints that use the standard `Link` response header for pagination. It hides the repeated “follow the next page link” work behind a simple stream of record lists.

**Data flow**: It receives a client, an API path, and optional query parameters. It calls the base connector’s link-header page walker with the GitHub page size and `_parse_records` as the response parser, then yields each parsed page. Empty responses become empty pages rather than errors.

**Call relations**: Many helpers use this as their low-level page fetcher: organization listing, repository listing, repository stream pages, org-scoped stream pages, and plain endpoint pagination. It is the common doorway from this connector’s logic into the REST connector’s HTTP pagination machinery.

*Call graph*: called by 4 (_iter_granted_org_repo_pages, _iter_user_orgs, _repo_pages, paginate).


##### `_partition_field`  (lines 496–504)

```
def _partition_field(path: str) -> str | None
```

**Purpose**: Figures out which partition stamp a record should carry based on the API path. Repo-scoped paths need a repo stamp, org-scoped paths need an org stamp, and unscoped paths need none.

**Data flow**: It receives a path template string. If the path contains a repo placeholder, it returns `repo_full_name`; if it contains an org placeholder, it returns `org_login`; otherwise it returns `None`.

**Call relations**: It is called by `GitHubConnector.flatten` before primary keys are finalized. That lets `flatten` know whether it must prefix record IDs with a repository or organization.

*Call graph*: called by 1 (flatten).


##### `_parse_records`  (lines 507–511)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Converts a GitHub HTTP response into the list of records expected by the connector. GitHub list endpoints normally return a bare JSON array, and this helper accepts only that shape.

**Data flow**: It receives an HTTP response. If the response body is empty, it returns an empty list. Otherwise it parses the JSON body and returns it only if it is a list; non-list JSON becomes an empty list.

**Call relations**: It is passed into the base link-header pagination machinery by `GitHubConnector._paginate_link_header`. That shared paginator uses this function to turn each raw HTTP response into records.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 514–529)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: Extracts a repository’s owner and name from a GitHub repository record. It tries several common shapes because GitHub responses may include either `full_name`, nested owner information, or just a name.

**Data flow**: It receives a repository record and an optional fallback owner. It first tries to split `full_name` like `owner/repo`. If that is not available, it looks for `owner.login` plus `name`. If only `name` exists and a fallback owner was provided, it uses that. It returns `(owner, repo)` when successful, otherwise `None`.

**Call relations**: It is called by `GitHubConnector._iter_user_repos` while turning repository records into repository partitions. Its output feeds the repo keys used by `PartitionWalk`.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 532–542)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: Finds the newest and oldest cursor values present on one page of records. The sync walker uses these bounds to know how far it has moved through a time-ordered feed.

**Data flow**: It receives a page of records and the name of a cursor field, which may be a nested path such as `commit.committer.date`. If there is no cursor field, it returns `(None, None)`. Otherwise it reads the field from each record with `get_path`, keeps string values, and returns the maximum and minimum values found. If no valid values exist, it returns `(None, None)`.

**Call relations**: It is called by `GitHubConnector._repo_pages` after each GitHub page is fetched. `_repo_pages` puts these high and low values into `WalkPage` so `PartitionWalk` can update watermarks and resume windows correctly.

*Call graph*: called by 1 (_repo_pages); 1 external calls (get_path).


### Google Workspace sources
These connectors cover Google mail, calendar, documents, Drive files, meeting artifacts, and spreadsheet content.

### `extensions/sources/ufo_ext_sources/gmail.py`

`io_transport` · `source sync runs`

Gmail does not return an email as one simple text field. A message is a nested MIME tree, which means the readable text may be buried inside plain-text or HTML parts, and those parts are encoded. This file is the adapter that knows how to talk to Gmail’s API, decode that shape, and present each email as something a person would recognize: From, To, Cc, Subject, and body text.

The main class, GmailConnector, is a read-only source connector. On the first run, it backfills a fixed time window, normally recent mail, and records Gmail’s historyId as a cursor. Think of that cursor like a bookmark in Gmail’s change log. On later runs, it asks Gmail what was added or deleted since that bookmark. If Gmail says the bookmark is too old, the connector signals that the cursor expired so the wider sync system can start again safely.

The file also cleans up message bodies. It decodes Gmail’s URL-safe base64 body data, picks plain text when available, and strips tags from HTML when plain text is missing. It treats permission problems as a skipped stream rather than a crash, and it ignores messages that disappear between listing and fetching.

#### Function details

##### `GmailConnector.paginate_source`  (lines 92–102)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the connector-facing entry for reading Gmail pages. It accepts the sync system’s inputs, including the saved cursor and backfill cutoff, and passes them into the Gmail-specific pagination flow.

**Data flow**: It receives an HTTP client, a stream description, the last saved cursor if there is one, and an optional backfill start time. It does not transform the data itself; it forwards the useful pieces to paginate. The result is an asynchronous stream of pages of Gmail records and deletion markers.

**Call relations**: The wider source framework calls this method when it wants Gmail data. This method immediately hands the work to GmailConnector.paginate, which decides whether to do an initial backfill or an incremental history walk.

*Call graph*: calls 1 internal fn (paginate).


##### `GmailConnector.paginate`  (lines 104–141)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main read loop for Gmail messages. It decides whether this run should fetch an initial batch of messages or only the changes since the previous run, then yields results in pages the rest of the sync system can store.

**Data flow**: It receives the Gmail API client, the stream being synced, and possibly a cursor. With no cursor, it asks _backfill for message IDs and a new history bookmark. With a cursor, it asks _history for added and deleted IDs. It fetches full message bodies in chunks, wraps them into StreamPage objects, and attaches deletion IDs and the next cursor to the final page. If Gmail rejects the request because the account lacks permission, it raises StreamSkipped instead of treating it as a hard failure.

**Call relations**: GmailConnector.paginate_source delegates to this function. This function coordinates _backfill, _history, and _fetch_bodies, then hands structured StreamPage results back to the sync framework.

*Call graph*: calls 4 internal fn (__init__, _backfill, _fetch_bodies, _history); called by 1 (paginate_source); 1 external calls (__init__).


##### `GmailConnector._backfill`  (lines 143–168)

```
async def _backfill(self, client: httpx.AsyncClient, *, after: datetime | None) -> tuple[list[str], str | None]
```

**Purpose**: This performs the first-time scan of the mailbox, limited by the pinned backfill window when one is supplied. It gathers message IDs and chooses the first history cursor that later runs should continue from.

**Data flow**: It receives an HTTP client and an optional cutoff date. First it reads the mailbox profile history ID as a safe floor. Then it repeatedly calls Gmail’s messages list endpoint, optionally with an after:<timestamp> search query, collecting valid message IDs across pages. When listing is done, it asks _seed_history_id to choose the cursor to save, and returns the collected IDs plus that cursor.

**Call relations**: GmailConnector.paginate uses this when there is no saved cursor. It calls _profile_history_id before listing, then _seed_history_id afterward so the next run can switch from backfill mode to Gmail’s change-history mode.

*Call graph*: calls 2 internal fn (_profile_history_id, _seed_history_id); called by 1 (paginate); 1 external calls (timestamp).


##### `GmailConnector._profile_history_id`  (lines 170–173)

```
async def _profile_history_id(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This reads Gmail’s current mailbox history ID from the user profile. That value is used as a fallback cursor, especially when a backfill window contains no messages.

**Data flow**: It receives an HTTP client, calls Gmail’s profile endpoint, and looks for a historyId field in the response. If the field is a string, it returns it; otherwise it returns None.

**Call relations**: GmailConnector._backfill calls this before enumerating messages. Its result is passed along as the floor value used by _seed_history_id.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._seed_history_id`  (lines 175–199)

```
async def _seed_history_id(self, client: httpx.AsyncClient, added: list[str], *, floor: str | None) -> str | None
```

**Purpose**: This chooses the history cursor to save after an initial backfill. Its job is to avoid getting stuck in repeated backfills and to avoid skipping messages that arrive during an empty scan.

**Data flow**: It receives the HTTP client, the list of message IDs just found, and the earlier profile history ID. If there are message IDs, it fetches the newest listed message in minimal form and returns that message’s historyId when available. If that message vanished or no messages were listed, it returns the profile history ID floor instead.

**Call relations**: GmailConnector._backfill calls this after collecting IDs. The cursor it returns goes back through paginate in the final StreamPage so future runs can use _history instead of repeating the initial listing.

*Call graph*: called by 1 (_backfill).


##### `GmailConnector._history`  (lines 201–236)

```
async def _history(self, client: httpx.AsyncClient, history_id: str) -> tuple[list[str], list[str], str | None]
```

**Purpose**: This reads Gmail’s change log from a saved history ID. It finds which messages were added and which were deleted since the last sync.

**Data flow**: It receives an HTTP client and a saved history ID. It pages through Gmail’s history endpoint, collects message IDs from added and deleted records, keeps the latest history ID it sees, and returns three things: IDs that are newly present, IDs that were deleted, and the next cursor. If Gmail returns 404, it means the old cursor has aged out, so it raises CursorExpired.

**Call relations**: GmailConnector.paginate calls this whenever a cursor exists. This function uses _message_ids to pull IDs out of Gmail’s nested history records, then gives the net changes back to paginate.

*Call graph*: calls 1 internal fn (_message_ids); called by 1 (paginate); 1 external calls (__init__).


##### `GmailConnector._fetch_bodies`  (lines 238–252)

```
async def _fetch_bodies(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]
```

**Purpose**: This turns a list of Gmail message IDs into full, flattened message records. It fetches each message body and prepares it for storage and rendering.

**Data flow**: It receives an HTTP client and a list of message IDs. For each ID, it asks Gmail for the full message. If a message has disappeared and Gmail returns 404, it skips that message. Otherwise it flattens the raw Gmail response with _flatten_message and returns the list of flattened records.

**Call relations**: GmailConnector.paginate calls this after _backfill or _history has produced message IDs. It hands each raw Gmail message to _flatten_message so later rendering does not have to understand Gmail’s nested MIME format.

*Call graph*: calls 1 internal fn (_flatten_message); called by 1 (paginate).


##### `GmailConnector.render`  (lines 254–274)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This converts a stored Gmail message record into readable prose. It makes emails recallable as normal email text instead of as a hard-to-read JSON dump.

**Data flow**: It receives a flattened record and a stream description. For the messages stream, it chooses a title from the subject or the default renderer, formats sender and recipients, adds the subject when present, chooses the best body text, and returns both the title and a combined text document. For other streams, it falls back to the parent connector’s renderer.

**Call relations**: The sync or recall system uses this when it needs human-readable text for a Gmail record. It relies on _str, _format_contact, _format_recipients, and _message_body to build the final readable output.

*Call graph*: calls 4 internal fn (_format_contact, _format_recipients, _message_body, _str).


##### `_message_ids`  (lines 277–286)

```
def _message_ids(entries: Any) -> list[str]
```

**Purpose**: This extracts message IDs from Gmail history entries. Gmail nests the ID inside each entry, so this helper safely digs it out.

**Data flow**: It receives an unknown value that should be a list of Gmail history entries. It skips anything that is not shaped like the expected dictionary, reads entry.message.id when present, and returns a clean list of non-empty string IDs.

**Call relations**: GmailConnector._history calls this for both added-message and deleted-message sections. The returned IDs become the raw material for deciding what to fetch and what to mark as deleted.

*Call graph*: called by 1 (_history).


##### `_flatten_message`  (lines 289–316)

```
def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts Gmail’s raw message response into the simpler record shape used by the rest of the system. It pulls out useful headers, addresses, labels, and decoded bodies.

**Data flow**: It receives one raw Gmail message dictionary. It reads selected headers such as From, To, Cc, and Subject; extracts plain-text and HTML bodies; parses sender and recipient addresses; copies labels; and marks the direction as outbound if the SENT label is present. It returns one flat dictionary with stable, easy-to-use fields.

**Call relations**: GmailConnector._fetch_bodies calls this after downloading each full message. It delegates address parsing to _parse_first_address and _addresses, and body extraction to _extract_bodies.

*Call graph*: calls 3 internal fn (_addresses, _extract_bodies, _parse_first_address); called by 1 (_fetch_bodies).


##### `_extract_bodies`  (lines 319–333)

```
def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]
```

**Purpose**: This searches a Gmail MIME payload for the first plain-text body and the first HTML body. It hides the complexity of Gmail’s nested email structure.

**Data flow**: It receives the payload part of a Gmail message. It walks the payload tree, checking each part’s MIME type, which is the label that says what kind of content it is. When it finds text/plain or text/html data, it decodes the body and remembers the first one of each type. It returns a pair: plain text, HTML text, or None for either missing value.

**Call relations**: _flatten_message calls this while building a flat record. Its inner walk function does the recursive tree traversal and calls _b64url_decode when it finds an encoded body.

*Call graph*: called by 1 (_flatten_message).


##### `_extract_bodies.walk`  (lines 323–330)

```
def walk(part: dict[str, Any]) -> None
```

**Purpose**: This is the small recursive worker inside _extract_bodies. It visits one MIME part, records useful body text, and then visits any child parts.

**Data flow**: It receives one part of the Gmail payload tree. If the part has encoded data and is either plain text or HTML, and that type has not already been found, it decodes and stores it. Then it repeats the same process for each child part. It does not return a value; it changes the surrounding found dictionary.

**Call relations**: _extract_bodies starts this worker at the root payload. Whenever the worker finds encoded text, it hands the data to _b64url_decode before storing it.

*Call graph*: calls 1 internal fn (_b64url_decode).


##### `_b64url_decode`  (lines 336–342)

```
def _b64url_decode(data: str) -> str
```

**Purpose**: This decodes the way Gmail stores message body bytes. Gmail uses URL-safe base64, a text encoding that turns bytes into safe characters for web APIs, and may omit padding characters.

**Data flow**: It receives an encoded string. It adds any missing padding, decodes it with URL-safe base64, and converts the bytes into UTF-8 text while replacing invalid characters. If the input cannot be decoded, it returns an empty string.

**Call relations**: _extract_bodies.walk calls this after finding encoded plain-text or HTML body data. The decoded text then becomes part of the flattened message record.

*Call graph*: called by 1 (walk); 1 external calls (urlsafe_b64decode).


##### `_parse_first_address`  (lines 345–352)

```
def _parse_first_address(header: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This reads the first email address from a header such as From. It separates the actual email handle from the optional display name.

**Data flow**: It receives a header string or None. If there is no header or no parsed address, it returns two None values. Otherwise it uses the email parser, lowercases the address, keeps the display name when present, and returns them as a pair.

**Call relations**: _flatten_message calls this for the sender header. The returned handle and display name become the record’s from_handle and from_display_name fields.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_addresses`  (lines 355–362)

```
def _addresses(header: str | None) -> list[dict[str, str | None]]
```

**Purpose**: This reads all email addresses from a recipient header such as To or Cc. It turns a single header string into a list of simple contact dictionaries.

**Data flow**: It receives a header string or None. With no header, it returns an empty list. Otherwise it parses all addresses, skips entries without an address, lowercases each address, keeps any display name, and returns a list of objects with handle and display_name.

**Call relations**: _flatten_message calls this for To and Cc headers. Those parsed lists are later formatted by _format_recipients when the message is rendered as readable text.

*Call graph*: called by 1 (_flatten_message); 1 external calls (getaddresses).


##### `_format_contact`  (lines 365–370)

```
def _format_contact(handle: Any, display_name: Any) -> str
```

**Purpose**: This turns one contact into a human-readable email string. It uses the familiar form Display Name <email@example.com> when a name exists.

**Data flow**: It receives a possible email handle and possible display name. If the handle is not a non-empty string, it returns an empty string. If there is a display name, it combines name and handle; otherwise it returns just the handle.

**Call relations**: GmailConnector.render calls this for the sender. _format_recipients also calls it for each recipient before joining them into one line.

*Call graph*: called by 2 (render, _format_recipients).


##### `_format_recipients`  (lines 373–380)

```
def _format_recipients(items: Any) -> str
```

**Purpose**: This formats a list of recipient contacts into the text used on a To or Cc line. It turns structured contacts back into a comma-separated email header.

**Data flow**: It receives a value that should be a list of contact dictionaries. If it is not a list, it returns an empty string. For each dictionary item, it formats the contact and joins the results with commas.

**Call relations**: GmailConnector.render calls this when building the readable To and Cc lines. It relies on _format_contact for each individual recipient.

*Call graph*: calls 1 internal fn (_format_contact); called by 1 (render).


##### `_message_body`  (lines 383–391)

```
def _message_body(record: dict[str, Any]) -> str
```

**Purpose**: This chooses the best body text to show for an email. It prefers plain text, falls back to cleaned HTML, and finally falls back to Gmail’s short snippet.

**Data flow**: It receives a flattened message record. If body_text is a non-empty string, it returns that trimmed text. Otherwise, if body_html is available, it extracts readable text from the HTML. If neither body exists, it returns the trimmed snippet when present, or an empty string.

**Call relations**: GmailConnector.render calls this while building the final readable document. It supplies the body section that appears after the formatted email headers.

*Call graph*: called by 1 (render).


##### `_str`  (lines 394–395)

```
def _str(value: Any) -> str
```

**Purpose**: This safely treats only real strings as strings. It avoids accidentally using None, numbers, or other values where readable text is expected.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: GmailConnector.render calls this when reading the subject. That keeps title selection simple and prevents non-string data from appearing in the rendered email.

*Call graph*: called by 1 (render).


##### `_HtmlText.__init__`  (lines 402–404)

```
def __init__(self) -> None
```

**Purpose**: This prepares an HTML-to-text parser for one email body. It creates the empty list that will collect readable pieces of text as the parser scans HTML.

**Data flow**: It receives no content directly beyond the new parser instance. It initializes the parent HTML parser with automatic character-reference conversion, then creates an empty parts list. The result is a parser ready to receive HTML.

**Call relations**: This is the setup step for the _HtmlText helper class. The extract method creates an instance, and the parser’s callback methods fill the parts list as HTML is read.


##### `_HtmlText.extract`  (lines 407–412)

```
def extract(cls, raw: str) -> str
```

**Purpose**: This is the simple public doorway for turning HTML email into plain text. It removes tags, preserves readable words, and keeps sensible line breaks around block-like HTML elements.

**Data flow**: It receives a raw HTML string. It creates a parser, feeds the HTML into it, joins the collected text pieces, compresses extra whitespace on each line, removes empty lines, and returns clean plain text.

**Call relations**: This method coordinates the _HtmlText parser. As it feeds HTML, the parser calls handle_data, handle_starttag, and handle_endtag to collect text and insert line breaks.


##### `_HtmlText.handle_data`  (lines 414–415)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records the actual words found inside an HTML email. HTML tags are ignored, but the text between them is kept.

**Data flow**: It receives a chunk of text from the HTML parser. It appends that chunk to the parser’s parts list. It returns nothing, but it grows the text that extract will later clean and return.

**Call relations**: This callback is part of the _HtmlText parser flow started by extract. It supplies the readable content, while the tag callbacks add line breaks around larger HTML blocks.


##### `_HtmlText.handle_starttag`  (lines 417–419)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break when a block-like HTML tag begins. It helps the final text keep paragraph and table-like separation instead of becoming one long run-on line.

**Data flow**: It receives an HTML tag name and its attributes. If the tag is one of the known block tags, such as p, div, br, or table cell tags, it appends a newline to the parts list. Attributes are not kept.

**Call relations**: This callback participates in the parsing flow started by _HtmlText.extract. It works alongside handle_data and handle_endtag to make stripped HTML still readable.


##### `_HtmlText.handle_endtag`  (lines 421–423)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break when a block-like HTML tag ends. It gives the cleaned text natural stopping points after paragraphs, headings, list items, and similar elements.

**Data flow**: It receives an HTML tag name. If the tag is in the block-tag set, it appends a newline to the parser’s parts list. It returns nothing; the effect is visible later when extract joins and cleans the collected text.

**Call relations**: This callback is used during the _HtmlText.extract parsing flow. Together with handle_starttag and handle_data, it turns HTML structure into plain-text spacing.


### `extensions/sources/ufo_ext_sources/googlecalendar.py`

`io_transport` · `source sync run`

This connector is the bridge between Google Calendar and the rest of the system. Its job is read-only: it does not create or edit calendar events. It asks Google for events from the user's primary calendar, reshapes Google's event format into the simpler records this project stores, and reports deleted events so old records can be removed.

The important idea is incremental syncing. On the first run, there is no saved position, so the connector looks back 90 days and asks Google for events, including deleted ones. Google returns a special sync token, which works like a bookmark. On later runs, the connector sends that bookmark back to Google and receives only what changed since then. If Google says the bookmark is too old, the connector raises a cursor-expired signal so the wider system can start fresh. If the user's permission grant does not include Calendar access, it marks the stream as skipped rather than treating the whole sync as broken.

The file exposes two streams. `calendar_events` stores one row per event, with title, time, location, description, organizer, and attendees folded in. `event_attendees` turns each event into one row per invitee, like making a guest list from a meeting invite. The render method turns an event into readable text for search or recall.

#### Function details

##### `GoogleCalendarConnector.paginate`  (lines 50–108)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main reader for Google Calendar. It fetches pages of events from Google's API, turns them into the project's stream records, and remembers the next sync bookmark so future runs only fetch changes.

**Data flow**: It receives an HTTP client, a stream choice, and an optional saved cursor. If there is a cursor, it sends it to Google as a sync token; otherwise it asks for events from the last 90 days and includes deleted events. For each page Google returns, it separates normal records from deleted event IDs, converts events either into event records or attendee records, and yields a `StreamPage` containing those results. If Google reports an expired token, it raises `CursorExpired`; if access is refused because the calendar permission is missing, it raises `StreamSkipped`.

**Call relations**: The sync framework calls this when it wants data for either the event stream or the attendee stream. While reading Google results, it hands raw event objects to `_flatten_event` for normal event records or `_flatten_attendees` for per-attendee rows. It packages the converted data into `StreamPage` objects for the rest of the sync pipeline to store.

*Call graph*: calls 3 internal fn (__init__, _flatten_attendees, _flatten_event); 4 external calls (__init__, __init__, now, timedelta).


##### `GoogleCalendarConnector.render`  (lines 110–135)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a stored calendar event into readable text that a person could search or recall later. It gives calendar events a richer display than the generic connector rendering.

**Data flow**: It receives one stored record and the stream it came from. For calendar events, it safely reads the title, time range, location, attendee handles, and description, then builds a plain text body with those pieces. It returns a pair: the title and the rendered body. For non-event streams, it falls back to the parent connector's default rendering.

**Call relations**: The wider system calls this when it needs human-readable content from a synced record. Inside this file, it uses `_str` to safely turn a possible title value into a string before building the final text.

*Call graph*: calls 1 internal fn (_str).


##### `_flatten_event`  (lines 138–165)

```
def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This converts one raw Google Calendar event into the simpler event shape the system stores. It keeps the useful meeting facts and folds attendee summaries directly into the event record.

**Data flow**: It receives a dictionary in Google's event format. It reads fields such as ID, creation time, update time, summary, description, location, start and end time, organizer, recurrence information, and attendees. It normalizes email addresses to lowercase, converts Google start/end objects into timestamp strings, and returns one flat dictionary ready to be stored as a calendar event record.

**Call relations**: `GoogleCalendarConnector.paginate` calls this for each non-cancelled event in the `calendar_events` stream. `_flatten_event` delegates attendee cleanup to `_attendee` and date/time cleanup to `_parse_when`, then hands the finished event record back to pagination so it can be included in a `StreamPage`.

*Call graph*: calls 2 internal fn (_attendee, _parse_when); called by 1 (paginate).


##### `_attendee`  (lines 168–174)

```
def _attendee(attendee: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This extracts the small attendee summary that belongs inside an event record. It keeps the invitee's email handle, display name, and response in a consistent format.

**Data flow**: It receives one attendee object from Google. It lowercases the attendee email, copies the display name if present, translates Google's response wording into the project's preferred wording, and returns a compact attendee dictionary.

**Call relations**: `_flatten_event` calls this while building the attendee list embedded in a calendar event record. It does not call other project functions; it is a small cleanup step inside the larger event conversion.

*Call graph*: called by 1 (_flatten_event).


##### `_flatten_attendees`  (lines 177–203)

```
def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This turns one Google Calendar event into many attendee records, one per invitee. This lets the system store and query the event's guest list as its own stream instead of only as text inside the event.

**Data flow**: It receives a raw Google event. It reads the event ID, timestamps, organizer email, and attendee list. For each attendee with an email address, it creates a row with a unique ID made from the event ID and email, the attendee's role, response, display name, and whether the attendee is the current user. It returns a list of attendee rows.

**Call relations**: `GoogleCalendarConnector.paginate` calls this when syncing the `event_attendees` stream. For each attendee it asks `_attendee_role` to label the invitee as organizer, resource, optional, or required, then returns the rows for packaging into a `StreamPage`.

*Call graph*: calls 1 internal fn (_attendee_role); called by 1 (paginate).


##### `_attendee_role`  (lines 206–213)

```
def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str
```

**Purpose**: This decides what role an invitee has in a calendar event. It turns several Google flags into one simple label the rest of the system can understand.

**Data flow**: It receives one attendee object and a separate boolean saying whether that attendee matches the event organizer. It checks, in order, whether the attendee is the organizer, a resource such as a room, optional, or otherwise required. It returns one role string.

**Call relations**: `_flatten_attendees` calls this for every attendee row it creates. The role it returns becomes part of the per-attendee stream record that pagination later yields to the sync pipeline.

*Call graph*: called by 1 (_flatten_attendees).


##### `_parse_when`  (lines 216–225)

```
def _parse_when(when: Any) -> str | None
```

**Purpose**: This normalizes the two different ways Google represents event times. Timed events and all-day events come from Google in different shapes, and this function turns both into one timestamp-style string.

**Data flow**: It receives a possible Google time object. If it contains `dateTime`, it returns that value as a string. If it contains only `date`, meaning an all-day event, it turns the date into a midnight UTC timestamp. If the input is missing or not shaped like a time object, it returns `None`.

**Call relations**: `_flatten_event` calls this for the start and end fields of each event. Its output becomes the `starts_at` and `ends_at` values stored on the event record.

*Call graph*: called by 1 (_flatten_event).


##### `_str`  (lines 228–229)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper for text rendering. It prevents non-text values from accidentally becoming confusing titles.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged; otherwise it returns an empty string. Nothing else is changed.

**Call relations**: `GoogleCalendarConnector.render` calls this when reading an event title. That keeps rendering predictable even if a synced record has a missing or unexpected title value.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googledocs.py`

`io_transport` · `during source sync, while listing, fetching, and rendering Google Docs`

This connector is the bridge between the system and Google Docs. Its job is read-only: it does not create or edit documents. First, it asks Google Drive for a list of Google Docs files the account can access. Drive is used for the list because it knows file names, links, owners, and modification times. Then, for each listed file, it asks the Google Docs API for the full document contents.

The connector is careful about large and repeated syncs. It uses a saved time marker, called a cursor, so it only asks Drive for documents changed after the last successful run. Drive results are paged, meaning they arrive in batches rather than all at once, like turning pages in a catalog. The connector also groups fetched documents into batches before handing them back to the sync system.

A key safety behavior is that one bad document should not ruin the whole sync. If Drive says the whole list cannot be read because the account lacks permission, the stream is skipped with a clear reason. But if a single document is listed and then cannot be opened, the connector returns a small placeholder record instead.

Finally, Google Docs store text inside a nested document structure. This file includes code to walk through that structure and flatten paragraph text into readable prose.

#### Function details

##### `GoogleDocsConnector.paginate`  (lines 50–86)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for the Google Docs stream. It lists changed Google Docs, fetches each document, enriches it with Drive metadata such as title and modified time, and yields records in batches for the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing the last synced modification time. It asks _iter_doc_files for Drive file pages, then for each valid file id it asks _document for the full Google Doc. It combines the document content with file details like URL, creation time, update time, and MIME type, then outputs lists of document records. If Google refuses access to the overall Drive or Docs listing, it changes that failure into a StreamSkipped message so the run can report a clear skipped stream instead of crashing unexpectedly.

**Call relations**: During a sync, the broader source framework calls this method to get document records page by page. It relies on _iter_doc_files to find candidate Google Docs and on _document to fetch each one. If access to the whole stream is refused, it raises StreamSkipped so the sync layer can treat this connector stream as unavailable rather than partially broken.

*Call graph*: calls 3 internal fn (__init__, _document, _iter_doc_files).


##### `GoogleDocsConnector._document`  (lines 88–97)

```
async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]
```

**Purpose**: This fetches one Google Doc by its file id from the Google Docs API. It also protects the sync from failing when a single listed document cannot actually be opened.

**Data flow**: It receives an HTTP client and a Google file id. It requests the full document from the Docs API. If the request succeeds, the full document data comes out. If Google returns a permission or missing-file error for that one document, it returns a small fallback dictionary containing only the document id. Other errors are allowed to rise up because they may indicate a real service or connection problem.

**Call relations**: paginate calls this after Drive has listed a file. Its result is folded into the final synced record. This keeps the main sync moving even when one document was deleted, moved, or visible in Drive but not readable through the Docs API.

*Call graph*: called by 1 (paginate).


##### `GoogleDocsConnector._iter_doc_files`  (lines 99–126)

```
async def _iter_doc_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through the Google Drive file list and finds Google Docs files the account can access. It uses the cursor to ask only for documents modified after the last sync.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds a Drive search query for non-trashed Google Docs, adds a modified-time filter when there is a cursor, then repeatedly calls Drive with paging parameters. Each response is turned into a safe list of files, which it yields if not empty. It follows Drive's next-page token until there are no more pages.

**Call relations**: paginate calls this first, before any individual document is fetched. This function supplies the Drive metadata and file ids that paginate needs. It uses list_or_empty to avoid problems if Google returns a missing or unexpected files field.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDocsConnector.render`  (lines 128–133)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a synced Google Docs record into a simple text form with a title and body. That rendered text is what downstream parts of the system can index or show as readable prose.

**Data flow**: It receives a document record and the stream description. It reads the title if it is a string, asks _plain_text to extract the document body, and builds a markdown-like text block with a heading followed by the document text. It returns both the title and the rendered body string.

**Call relations**: The source framework calls this when it needs a human-readable version of a synced record. It delegates the difficult part, extracting text from Google Docs' nested body structure, to _plain_text.

*Call graph*: calls 1 internal fn (_plain_text).


##### `_plain_text`  (lines 136–152)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts readable paragraph text from the nested structure used by the Google Docs API. It is the part that turns Google's document-shaped data into ordinary text.

**Data flow**: It receives a document record. It looks for body.content, then walks through each paragraph and each text run inside that paragraph. Whenever it finds string content, it adds it to a list. At the end, it joins all text pieces in order and trims extra whitespace, returning one plain text string.

**Call relations**: render calls this when preparing a document for indexing or display. It does not fetch anything itself; it only interprets the document data that _document already retrieved.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googledrive.py`

`io_transport` · `source sync runs`

This connector is the bridge between the project and Google Drive. Its job is read-only: it never changes anything in Drive. On a first run, it asks Google Drive for all non-deleted files the user can access, including files in shared drives. After that, it uses Google Drive’s change feed, which is like a “what changed since last time?” notebook, so future syncs do not need to scan everything again.

The file also knows how to fetch related details for each file, such as permissions, comments, and revisions. For those child collections, it first walks through the files, then asks Drive for the matching sub-list for each file. If Google refuses access because the account does not have the right Drive permission scope, the connector records the stream as skipped instead of treating the whole sync as broken.

A key detail is the cursor. A cursor is a saved marker that says where the last sync stopped. For file changes, Google can expire this marker. When that happens, the connector raises a special “cursor expired” signal so the wider system can start fresh safely. The file also includes a small renderer that turns a Drive file record into readable text with the file name, type, owners, and link.

#### Function details

##### `GoogleDriveConnector.paginate`  (lines 86–118)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for Google Drive streams. Given a stream name, it chooses whether to fetch files, file changes, shared drives, or per-file child data such as comments and permissions.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks the stream name, calls the matching helper, and yields pages of records or special stream pages that can include a new cursor or delete markers. If Google answers with 401 or 403, meaning the grant is not allowed to read Drive, it turns that into a skipped stream instead of a failed run.

**Call relations**: The wider sync system calls this method when it wants records from one Google Drive stream. This method then hands the work to _paginate_files, _paginate_file_changes, _paginate_shared_drives, _paginate_file_children, or _start_page_token depending on what is being synced.

*Call graph*: calls 6 internal fn (__init__, _paginate_file_changes, _paginate_file_children, _paginate_files, _paginate_shared_drives, _start_page_token); 1 external calls (__init__).


##### `GoogleDriveConnector._paginate_files`  (lines 120–145)

```
async def _paginate_files(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches Google Drive file records in pages. It is used for the initial full file listing, and also as the starting point when fetching per-file child data.

**Data flow**: It receives an HTTP client and an optional time cursor. It builds a Google Drive files request for non-trashed files, optionally limiting results to files modified after the cursor, then follows Google’s next-page tokens until there are no more pages. It yields lists of file dictionaries, cleaned through list_or_empty so missing or malformed lists become safe empty lists.

**Call relations**: paginate calls this during the first file sync when there is no change-feed cursor yet. _paginate_file_children also calls it so it can discover every file before asking for that file’s permissions, comments, or revisions.

*Call graph*: called by 2 (_paginate_file_children, paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._start_page_token`  (lines 147–152)

```
async def _start_page_token(self, client: httpx.AsyncClient) -> str | None
```

**Purpose**: This asks Google Drive for the marker that future change-feed syncs should start from. It is how the connector switches from a first full scan to later incremental scans.

**Data flow**: It receives an HTTP client, calls Google’s startPageToken endpoint, reads the startPageToken value from the response, and returns it if it is a non-empty string. If the response does not contain a usable token, it returns None.

**Call relations**: paginate calls this after it has finished listing all files during an initial sync. The returned token is yielded inside a StreamPage so the core sync system can save it as the next cursor.

*Call graph*: called by 1 (paginate).


##### `GoogleDriveConnector._paginate_file_changes`  (lines 154–199)

```
async def _paginate_file_changes(self, client: httpx.AsyncClient, *, cursor: str) -> AsyncIterator[StreamPage]
```

**Purpose**: This reads Google Drive’s change feed to find files that were added, updated, removed, or trashed since the last sync. It is the efficient path used after the initial sync.

**Data flow**: It receives an HTTP client and a saved change-feed cursor. It repeatedly asks Google for changes from that token, separates live file records from deleted or trashed file IDs, and yields StreamPage objects containing records, delete markers, and the next cursor. If Google says the token is too old with status 410, it raises CursorExpired so the system knows it must refresh from scratch.

**Call relations**: paginate calls this when syncing the files stream and a cursor already exists. It hands StreamPage results back to paginate, which passes them onward to the sync core so records can be upserted and removed items can be tombstoned.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, __init__, list_or_empty).


##### `GoogleDriveConnector._paginate_shared_drives`  (lines 201–218)

```
async def _paginate_shared_drives(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches the list of shared drives visible to the grant. Unlike files, shared drives are read as a full list each time.

**Data flow**: It receives an HTTP client, requests shared drive pages from Google, extracts the drives list safely with list_or_empty, yields any records found, and follows next-page tokens until Google has no more pages.

**Call relations**: paginate calls this whenever the shared_drives stream is requested. It does not use the file change cursor, because this stream is refreshed by rereading the available shared drives.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector._paginate_file_children`  (lines 220–258)

```
async def _paginate_file_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches file-specific sub-data, such as permissions, comments, or revisions, for every visible file. It adds the parent file ID and name to each child record so the result can be traced back to its file.

**Data flow**: It receives an HTTP client, the child stream description, and an optional cursor. First it gets all files through _paginate_files. For each file with a valid ID, it calls the matching Google Drive child endpoint and follows child-page tokens. It filters child records by the cursor when the stream has a time field, adds file_id and file_name, and yields pages of enriched child records. If Google returns 403 or 404 for a particular child list, it skips that file’s child data and continues.

**Call relations**: paginate calls this for permissions, comments, and revisions. This function depends on _paginate_files to know which files to inspect, then performs the per-file fan-out work before returning child records to the main sync flow.

*Call graph*: calls 1 internal fn (_paginate_files); called by 1 (paginate); 1 external calls (list_or_empty).


##### `GoogleDriveConnector.render`  (lines 260–275)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a Google Drive file record into simple readable text. That text can be used later as recallable metadata, for example in search or display.

**Data flow**: It receives one record and its stream description. For non-file streams, it delegates to the base connector’s renderer. For file records, it reads the name, MIME type, owners, and web link, then returns a title and a short text body. It uses _str to avoid treating non-text names as valid titles.

**Call relations**: The connector framework calls this when it needs a human-readable version of a synced record. For file streams it formats the Drive-specific details itself; otherwise it hands off to the parent RestConnector behavior.

*Call graph*: calls 1 internal fn (_str).


##### `_str`  (lines 278–279)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely turns a value into a string only when it already is one. It prevents unexpected non-text values from becoming misleading titles.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged. Otherwise, it returns an empty string.

**Call relations**: GoogleDriveConnector.render calls this when reading a file name. It keeps the rendering step simple and safe by normalizing the title input.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/googlemeet.py`

`io_transport` · `source sync and page rendering`

This connector is a bridge between Google Meet’s web APIs and the project’s internal idea of a “page” of source content. It looks at Google Meet conference records, checks whether each meeting has generated transcripts or AI notes, fetches those extra pieces, and then formats everything as plain text that a person can search and read later.

The main flow starts with the list of conference records. For incremental syncing, it uses the last seen meeting start time, but looks back one extra day. That lookback matters because Google may create transcripts or notes after a meeting ends, like a package arriving after the event itself. The connector may re-check recent meetings so late artifacts are not missed.

For each conference, it gathers transcript sessions, transcript entries, and smart notes. Transcript entries are turned into speaker-by-speaker dialogue. Smart notes can point to a Google Docs file; when possible, the connector also reads that document’s plain text. If Google refuses access to Meet data with an authorization error, the stream is skipped rather than treated as a broken run. If a linked document is missing or forbidden, the connector keeps going and preserves the document link when it can. The result is one rendered page per meeting that has useful generated artifacts.

#### Function details

##### `GoogleMeetConnector.paginate`  (lines 54–88)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main fetch loop for the Google Meet stream. It asks Google Meet for conference records page by page, enriches each meeting with transcripts and notes, and yields batches of records for the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing the last start time already seen. It builds Google Meet API query parameters, optionally applies a one-day lookback filter, fetches conference pages, turns each conference into a richer record, keeps only meetings that actually have transcripts or smart notes, and outputs StreamPage objects with those records plus the next cursor. If Google replies with a permission refusal, it changes that failure into a skipped stream message.

**Call relations**: The sync driver calls this when it wants Google Meet content. During the loop it uses _lookback to widen the incremental search window, _max_start_time to advance the cursor, and _conference_record to fill in the details for each meeting. It hands completed batches back as StreamPage objects; if access is denied, it raises StreamSkipped so the wider run can continue.

*Call graph*: calls 4 internal fn (__init__, _conference_record, _lookback, _max_start_time); 2 external calls (__init__, list_or_empty).


##### `GoogleMeetConnector._conference_record`  (lines 90–113)

```
async def _conference_record(self, client: httpx.AsyncClient, conference: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This builds the full internal record for one Google Meet conference. It collects the meeting’s transcripts and smart notes, then wraps them with meeting-level details such as start time, end time, and Google’s conference name.

**Data flow**: It receives a raw conference dictionary from Google. It reads the conference resource name, fetches transcript artifacts and smart-note artifacts under that conference, converts each artifact into a cleaner internal shape, and returns one dictionary representing the whole meeting. The output includes an id, title, timing fields, transcript list, and smart-note list.

**Call relations**: paginate calls this for every conference it receives from Google. This function then fans out to _artifacts to list child items, _transcript to expand transcript data, and _smart_note to expand AI-note data. Its completed record is returned to paginate, which decides whether the meeting is worth emitting.

*Call graph*: calls 5 internal fn (_artifacts, _smart_note, _transcript, _resource_id, _str); called by 1 (paginate).


##### `GoogleMeetConnector._artifacts`  (lines 115–132)

```
async def _artifacts(self, client: httpx.AsyncClient, parent: str, collection: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches all child artifacts of a meeting from a chosen Google Meet collection, such as transcripts or smart notes. It hides the page-by-page API details so higher-level code can simply ask for the full list.

**Data flow**: It receives an HTTP client, a parent conference resource name, and the child collection name to fetch. If the parent name is empty, it returns an empty list. Otherwise it repeatedly calls the Google Meet API with page tokens, gathers artifact items from each response, and returns one combined list.

**Call relations**: _conference_record calls this twice for each meeting: once for transcripts and once for smart notes. This function does the low-level collection listing and hands the raw artifacts back to _conference_record, which sends them to _transcript or _smart_note for shaping.

*Call graph*: called by 1 (_conference_record); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._transcript`  (lines 134–146)

```
async def _transcript(self, client: httpx.AsyncClient, transcript: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one transcript session from Google Meet into a cleaner record the project can store and render. It includes transcript metadata, the linked Google Docs destination if present, and the actual spoken entries.

**Data flow**: It receives a raw transcript dictionary. It extracts the transcript name, id, state, start and end times, and any Google Docs destination information. Then it calls _transcript_entries to fetch the lines of speech belonging to this transcript. It returns a dictionary containing both the transcript summary and its entries.

**Call relations**: _conference_record calls this for every transcript artifact it finds. This function relies on _docs_destination to preserve document links and _transcript_entries to gather the transcript text. Its result is placed inside the meeting record returned to paginate.

*Call graph*: calls 4 internal fn (_transcript_entries, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._transcript_entries`  (lines 148–181)

```
async def _transcript_entries(self, client: httpx.AsyncClient, transcript_name: str) -> list[dict[str, Any]]
```

**Purpose**: This fetches the individual spoken lines inside a transcript. It turns Google’s transcript-entry records into a simple list with participant, text, language, and timing information.

**Data flow**: It receives an HTTP client and a transcript resource name. If the name is empty, it returns an empty list. Otherwise it asks Google Meet for transcript entries page by page, extracts each entry’s id, participant, text, language code, start time, and end time, and returns the accumulated list. If Google says the linked artifact is missing or forbidden, it returns whatever entries it already gathered instead of failing the whole sync.

**Call relations**: _transcript calls this when expanding a transcript artifact. Later, the rendering path uses these entries through _transcripts_section and _dialogue to display a readable conversation. This function is deliberately tolerant of missing document-style resources so one unavailable transcript does not stop the meeting from being recorded.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_transcript); 1 external calls (list_or_empty).


##### `GoogleMeetConnector._smart_note`  (lines 183–198)

```
async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns one Google Meet smart-note artifact into a clean record. If the note links to a Google Docs file and the connector can read it, it also includes the note’s plain text body.

**Data flow**: It receives a raw smart-note dictionary. It extracts the note id, name, state, timing fields, and document destination. If a document id is present, it calls _document_text to try to fetch and flatten the Google Doc. It returns a dictionary for the note, optionally with a body field containing readable text.

**Call relations**: _conference_record calls this for each smart-note artifact in a meeting. This function uses _docs_destination to preserve the Google Docs link and _document_text to inline the note content when possible. Its result becomes part of the meeting record that paginate emits.

*Call graph*: calls 4 internal fn (_document_text, _docs_destination, _resource_id, _str); called by 1 (_conference_record).


##### `GoogleMeetConnector._document_text`  (lines 200–209)

```
async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str
```

**Purpose**: This reads a Google Docs document and extracts its plain text. It is used so AI meeting summaries can be included directly instead of only linked.

**Data flow**: It receives an HTTP client and a Google Docs document id. It safely quotes the id for use in a URL, requests the document from the Google Docs API, and passes the returned document structure to _plain_text. If the document is missing or access is forbidden, it returns an empty string; otherwise it returns the extracted text.

**Call relations**: _smart_note calls this when a smart note points to a Docs document. This function performs the Docs API fetch and then hands the nested document data to _plain_text, which does the actual text extraction. The resulting string is added back to the smart-note record if it is not empty.

*Call graph*: calls 1 internal fn (_plain_text); called by 1 (_smart_note); 1 external calls (quote).


##### `GoogleMeetConnector.render`  (lines 211–228)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a stored Google Meet meeting record into a readable text page. The page includes meeting metadata, transcript text, and AI summaries in a simple document-like layout.

**Data flow**: It receives a record and a stream description. If the stream is not the Google Meet meeting-artifacts stream, it lets the parent connector render it. For meeting artifacts, it reads the title and key fields, formats labeled metadata, builds transcript and smart-note sections, and returns a title plus the finished text body.

**Call relations**: After paginate has produced records, the source framework calls render when it needs the human-readable page content. This function delegates the transcript block to _transcripts_section, the AI-summary block to _smart_notes_section, and repeated label formatting to _labeled. The output is what downstream search or recall features will see.

*Call graph*: calls 4 internal fn (_labeled, _smart_notes_section, _str, _transcripts_section).


##### `_transcripts_section`  (lines 231–247)

```
def _transcripts_section(value: Any) -> str
```

**Purpose**: This formats all transcript sessions for a meeting into a readable “Transcripts” section. It includes transcript metadata and, when present, the dialogue itself.

**Data flow**: It receives an unknown value that should contain a list of transcripts. It normalizes that value into a list, skips the section if there are no transcripts, and for each transcript formats state, timing, document link, and spoken dialogue. It returns one text block, or an empty string when there is nothing to show.

**Call relations**: GoogleMeetConnector.render calls this while building the meeting page. This function uses _dialogue to turn transcript entries into speaker lines and _labeled to present metadata neatly. Its returned section is joined with the rest of the rendered page.

*Call graph*: calls 3 internal fn (_dialogue, _labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_smart_notes_section`  (lines 250–266)

```
def _smart_notes_section(value: Any) -> str
```

**Purpose**: This formats Google Meet smart notes into a readable “AI summaries” section. It shows note metadata, the source document link, and any summary body that could be read from Google Docs.

**Data flow**: It receives an unknown value that should contain a list of smart-note records. It normalizes that value into a list, returns an empty string if there are no notes, and otherwise creates one section with state, timing, document link, and body text for each note. The result is a single text block ready to place in the rendered page.

**Call relations**: GoogleMeetConnector.render calls this after the transcript section. It uses _labeled for the small metadata block and _str to safely read optional text. Its output becomes the AI-summary part of the final meeting page.

*Call graph*: calls 2 internal fn (_labeled, _str); called by 1 (render); 1 external calls (list_or_empty).


##### `_dialogue`  (lines 269–282)

```
def _dialogue(value: Any) -> str
```

**Purpose**: This turns raw transcript entries into a clean conversation transcript. It groups back-to-back entries from the same speaker onto the same line, making the output easier to read.

**Data flow**: It receives an unknown value that should contain transcript entries. It normalizes that value into a list, ignores entries with no text, finds a speaker name for each entry, and builds lines like “Speaker: words.” If the same speaker continues speaking, it appends the new text to the previous line. It returns the finished dialogue as newline-separated text.

**Call relations**: _transcripts_section calls this when it needs the spoken part of a transcript. This function uses _speaker to get a readable participant label and _str to avoid treating non-text values as text. The resulting dialogue is inserted into the transcript section.

*Call graph*: calls 2 internal fn (_speaker, _str); called by 1 (_transcripts_section); 1 external calls (list_or_empty).


##### `_plain_text`  (lines 285–298)

```
def _plain_text(record: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from the nested structure returned by the Google Docs API. It strips away the document model and keeps only the text runs that a person would read.

**Data flow**: It receives a Google Docs document dictionary. It looks inside the document body, walks through content items, paragraphs, paragraph elements, and text runs, collects any string content it finds, joins those pieces together, trims the edges, and returns the plain text.

**Call relations**: _document_text calls this after fetching a document from Google Docs. _document_text handles the network request and error behavior, while _plain_text focuses only on converting Google’s nested document shape into a simple string for smart notes.

*Call graph*: called by 1 (_document_text).


##### `_docs_destination`  (lines 301–308)

```
def _docs_destination(record: dict[str, Any]) -> dict[str, str]
```

**Purpose**: This pulls Google Docs destination information out of a transcript or smart-note record. It preserves both the document id and the export URL when Google provides them.

**Data flow**: It receives a raw artifact dictionary. It checks whether the docsDestination field is a dictionary; if not, it returns an empty dictionary. If it is present, it reads the document id and export URL as safe strings and returns them under the keys docs_document and docs_url.

**Call relations**: _transcript and _smart_note both call this while shaping Google artifacts into internal records. The document id can later be used by _smart_note to fetch text through _document_text, and the URL can be shown during rendering as a durable link.

*Call graph*: calls 1 internal fn (_str); called by 2 (_smart_note, _transcript).


##### `_max_start_time`  (lines 311–317)

```
def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This chooses the newest meeting start time seen so far. It is used to advance the sync cursor, which is the bookmark that tells the next run where to resume.

**Data flow**: It receives a list of conference dictionaries and the current cursor value. It scans each conference’s startTime field, compares string timestamps, and keeps the greatest one. It returns the updated cursor, or the original cursor if no newer start time is found.

**Call relations**: GoogleMeetConnector.paginate calls this after each page of conference records. Even meetings without transcripts or notes still influence the cursor, which prevents the connector from repeatedly scanning the same old window forever.

*Call graph*: called by 1 (paginate).


##### `_lookback`  (lines 320–322)

```
def _lookback(cursor: str) -> str
```

**Purpose**: This moves a cursor timestamp back by one day. It helps the connector re-check recent meetings because transcripts and AI notes can appear after the meeting itself has ended.

**Data flow**: It receives a timestamp string, parses it as a date and time, subtracts the configured one-day lookback, and returns a new timestamp string formatted for Google’s API. The returned time is used as the lower bound for the next incremental fetch.

**Call relations**: GoogleMeetConnector.paginate calls this when it has a saved cursor. The widened time window gives _conference_record another chance to find late-arriving artifacts for recent conferences while still avoiding a full historical rescan.

*Call graph*: called by 1 (paginate); 1 external calls (fromisoformat).


##### `_resource_id`  (lines 325–326)

```
def _resource_id(name: str) -> str
```

**Purpose**: This extracts the short id from a Google resource name. Google resource names are often path-like strings, and this helper keeps only the last piece.

**Data flow**: It receives a resource name string. If the string is not empty, it splits on the final slash and returns the last segment; if it is empty, it returns an empty string.

**Call relations**: Several shaping helpers call this when building ids for conferences, transcripts, smart notes, and transcript entries. _speaker also uses it to turn a participant resource name into a readable fallback label. It keeps ids consistent across the connector.

*Call graph*: called by 5 (_conference_record, _smart_note, _transcript, _transcript_entries, _speaker).


##### `_speaker`  (lines 329–331)

```
def _speaker(value: Any) -> str
```

**Purpose**: This turns a transcript participant value into a display name for dialogue output. If no usable participant id is available, it uses the friendly fallback “Participant.”

**Data flow**: It receives an unknown participant value. It first keeps the value only if it is a string, then extracts the final resource id from that string. It returns that id if present, otherwise it returns “Participant.”

**Call relations**: _dialogue calls this for each transcript entry while building speaker lines. _speaker relies on _str for safe string handling and _resource_id for shortening Google’s resource-style participant names.

*Call graph*: calls 2 internal fn (_resource_id, _str); called by 1 (_dialogue).


##### `_str`  (lines 334–335)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that returns a value only when it is actually text. It prevents accidental display of non-text objects where the rendered page expects a string.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. It does not change any outside state.

**Call relations**: Many functions use this when reading optional fields from Google responses, including conference shaping, transcript shaping, smart-note shaping, rendering, dialogue creation, and document-link extraction. It acts like a simple guardrail around unpredictable API data.

*Call graph*: called by 10 (_conference_record, _smart_note, _transcript, _transcript_entries, render, _dialogue, _docs_destination, _smart_notes_section, _speaker, _transcripts_section).


##### `_labeled`  (lines 338–339)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This formats a small set of labels and values into readable lines. It omits labels whose values are empty so the final page does not contain blank or noisy fields.

**Data flow**: It receives a list of label-and-value pairs. It keeps only pairs with a non-empty value, formats each as “label: value,” joins them with newlines, and returns the resulting text block.

**Call relations**: GoogleMeetConnector.render uses this for meeting-level metadata, while _transcripts_section and _smart_notes_section use it for artifact metadata. It gives all rendered sections a consistent, simple look.

*Call graph*: called by 3 (render, _smart_notes_section, _transcripts_section).


### `extensions/sources/ufo_ext_sources/googlesheets.py`

`io_transport` · `source sync pagination and rendering`

This connector is the bridge between UFO and Google Sheets. Without it, the system could not discover a user's spreadsheets, notice which ones changed, or pull sheet rows into the source index. It first asks Google Drive for spreadsheet files, because Drive knows which files exist and when they were last modified. Then, for each spreadsheet, it asks the Google Sheets API for the spreadsheet details and its tab list. For the row data stream, it asks for tab values in batches so it does not make one web request for every tab unless it has to.

A key idea in this file is the cursor, also called a watermark: a saved “last seen modified time” that lets later runs continue from where the previous sync stopped. The file is careful with edge cases. If Google refuses access to one spreadsheet, that file is remembered and retried later, instead of blocking all newer files. If Google refuses the whole grant, such as missing permissions or a disabled API, the stream is skipped rather than pretending the data is empty. Quota errors are treated as real failures.

The connector exposes three streams: spreadsheet metadata, sheet tab metadata, and sheet cell values. It also renders each record into readable text, so a spreadsheet or tab becomes useful content rather than raw API data.

#### Function details

##### `GoogleSheetsConnector.paginate`  (lines 162–212)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main sync loop for a Google Sheets stream. It walks through changed spreadsheets, turns each one into the requested kind of records, and yields pages of records with a cursor so future runs can resume safely.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It decodes the cursor into a last-seen modified time and any previously refused file IDs, lists spreadsheets from Drive, fetches or derives records for the chosen stream, groups them into pages, and outputs StreamPage objects. Along the way it updates the cursor and remembers files that were refused so they can be retried later.

**Call relations**: The sync framework calls this when it needs records from the connector. It asks _spreadsheet_visits for normal Drive-listed files, uses _visit_records to turn each visit into records, and then calls _carried_visit for refused files carried over from earlier runs. It uses _decode_cursor, _encode_cursor, _settled, _error_detail, and _is_quota_refusal to keep progress and error handling consistent.

*Call graph*: calls 9 internal fn (__init__, _carried_visit, _spreadsheet_visits, _visit_records, _decode_cursor, _encode_cursor, _error_detail, _is_quota_refusal, _settled); 1 external calls (__init__).


##### `GoogleSheetsConnector._iter_spreadsheet_files`  (lines 214–239)

```
async def _iter_spreadsheet_files(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function lists spreadsheet files from Google Drive in pages. It limits the results to untrashed Google Sheets and, when possible, only asks for files modified at or after the saved watermark.

**Data flow**: It receives an HTTP client and an optional watermark. It builds a Drive search query, repeatedly calls the Drive files endpoint with a page token, converts the returned files field into a safe list, and yields each non-empty page of file metadata. It stops when Drive no longer returns a next page token.

**Call relations**: _spreadsheet_visits uses this as its source of Drive file batches. It is the first stage of the normal listing path that paginate drives.

*Call graph*: called by 1 (_spreadsheet_visits); 1 external calls (list_or_empty).


##### `GoogleSheetsConnector._spreadsheet_visits`  (lines 241–249)

```
async def _spreadsheet_visits(self, client: httpx.AsyncClient, *, watermark: str | None) -> AsyncIterator[_FileVisit]
```

**Purpose**: This turns raw Drive file entries into richer spreadsheet visits. A visit is the connector's internal package containing the file ID, the spreadsheet record if readable, and whether access was refused.

**Data flow**: It receives an HTTP client and watermark, reads Drive file pages from _iter_spreadsheet_files, skips malformed entries without a usable ID, and calls _file_visit for each valid spreadsheet. It yields one _FileVisit at a time.

**Call relations**: paginate calls this during the normal Drive listing phase. It hands each Drive file onward to _file_visit so the connector can add Sheets API metadata before records are produced.

*Call graph*: calls 2 internal fn (_file_visit, _iter_spreadsheet_files); called by 1 (paginate).


##### `GoogleSheetsConnector._carried_visit`  (lines 251–265)

```
async def _carried_visit(self, client: httpx.AsyncClient, file_id: str) -> _FileVisit
```

**Purpose**: This retries a spreadsheet that was refused in an earlier run. It checks whether the file still exists, whether it was trashed, and whether the current grant can now read it.

**Data flow**: It receives an HTTP client and a file ID saved in the cursor. It asks Drive for that one file's metadata, including whether it is trashed. If the file is gone or still refused in a file-specific way, it returns a visit with no record and an appropriate refused flag. If the file is present and readable, it passes the file metadata to _file_visit and returns the resulting visit.

**Call relations**: paginate calls this after finishing the normal Drive listing, but only for refused IDs that were not already listed again. It relies on _error_detail and _is_per_file_refusal to decide whether an error is about this one file or should stop the stream.

*Call graph*: calls 3 internal fn (_file_visit, _error_detail, _is_per_file_refusal); called by 1 (paginate); 1 external calls (__init__).


##### `GoogleSheetsConnector._file_visit`  (lines 267–294)

```
async def _file_visit(self, client: httpx.AsyncClient, file_id: str, file: dict[str, Any]) -> _FileVisit
```

**Purpose**: This builds the main spreadsheet record for one Drive file. It combines Drive metadata, such as timestamps and web link, with Sheets metadata, such as tab information and spreadsheet title.

**Data flow**: It receives an HTTP client, a file ID, and Drive file metadata. It asks the Sheets API for the spreadsheet structure. If that request is refused only for this file, it falls back to a minimal record using Drive's file name and marks the visit as refused. It outputs an _FileVisit containing the assembled spreadsheet record and the refusal status.

**Call relations**: _spreadsheet_visits calls this for files found in Drive listings, and _carried_visit calls it for previously refused files being retried. It uses _error_detail and _is_per_file_refusal to decide whether to fall back or raise the error.

*Call graph*: calls 2 internal fn (_error_detail, _is_per_file_refusal); called by 2 (_carried_visit, _spreadsheet_visits); 1 external calls (__init__).


##### `GoogleSheetsConnector._visit_records`  (lines 296–312)

```
async def _visit_records(self, client: httpx.AsyncClient, stream: StreamSpec, visit: _FileVisit) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This chooses how to turn one spreadsheet visit into records for the specific stream being synced. It is the small dispatcher that separates spreadsheet records, tab records, and tab-value records.

**Data flow**: It receives an HTTP client, a stream description, and a _FileVisit. If the visit has no readable record, it returns no records plus the refusal flag. For the spreadsheets stream it returns the spreadsheet record itself; for sheets it derives one record per tab; for sheet_values it fetches tab row data. It returns both the records and whether anything remained refused.

**Call relations**: paginate calls this for every normal or carried visit. It hands off to _sheet_records for tab metadata and to _sheet_value_records when actual grid values must be read from Google.

*Call graph*: calls 2 internal fn (_sheet_value_records, _sheet_records); called by 1 (paginate).


##### `GoogleSheetsConnector._sheet_value_records`  (lines 314–370)

```
async def _sheet_value_records(self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]
```

**Purpose**: This reads the cell rows from each tab in a spreadsheet. It uses batch requests for speed, but can fall back to one-tab-at-a-time reads if Google refuses a batch.

**Data flow**: It receives an HTTP client and a spreadsheet record. It extracts valid tab titles and IDs, groups them into bounded chunks, asks the Sheets values API for those tab ranges, checks that Google returned the same number of ranges requested, and creates one value record per tab. If a batch is refused for a file-specific reason, it retries each tab separately and drops only the tabs that are still refused. It outputs the value records and a flag saying whether any tab was refused.

**Call relations**: _visit_records calls this for the sheet_values stream. It uses _quoted_sheet_range to name tabs safely, _sheet_value_record to shape each output record, _error_detail and _is_per_file_refusal to classify refusals, and raises StreamFault if Google's batch response does not line up with the request.

*Call graph*: calls 5 internal fn (__init__, _error_detail, _is_per_file_refusal, _quoted_sheet_range, _sheet_value_record); called by 1 (_visit_records); 2 external calls (list_or_empty, quote).


##### `GoogleSheetsConnector.render`  (lines 372–391)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns synced Google Sheets records into readable text. The result is what a person or search system can use instead of raw JSON from Google.

**Data flow**: It receives a record and its stream description. For spreadsheet records it builds a heading and lists tab names; for tab records it names the parent spreadsheet; for value records it turns rows into lines of pipe-separated cell text. It outputs a short title and a formatted body string.

**Call relations**: The wider source system calls this after records are fetched, when it needs human-readable content. It uses _str to safely read text fields and _grid_text to format sheet rows.

*Call graph*: calls 2 internal fn (_grid_text, _str).


##### `_decode_cursor`  (lines 394–407)

```
def _decode_cursor(cursor: str | None) -> tuple[str | None, tuple[str, ...], str | None]
```

**Purpose**: This reads the saved sync cursor back into useful parts. It supports both old simple cursors that are just a timestamp and newer JSON cursors that also remember refused file IDs.

**Data flow**: It receives a cursor string or nothing. If there is no cursor, it returns empty progress. If the string is not JSON or is not a JSON object, it treats it as the old watermark form. If it is a valid checkpoint object, it returns the watermark, refused IDs, and the last retried ID. If the JSON shape is wrong, it raises an error because the saved progress is malformed.

**Call relations**: paginate calls this at the start of a run. Its output controls which Drive files are listed and which refused files are retried later in the same pagination flow.

*Call graph*: called by 1 (paginate); 1 external calls (loads).


##### `_encode_cursor`  (lines 410–415)

```
def _encode_cursor(watermark: str | None, refused: set[str], retried: str | None) -> str | None
```

**Purpose**: This writes the connector's current progress into a cursor string. It keeps the cursor simple when there are no refused files, and uses a JSON checkpoint when retry information must be saved.

**Data flow**: It receives the current watermark, a set of refused file IDs, and an optional retried marker. If there is no watermark or no refused IDs, it returns just the watermark. Otherwise it creates a checkpoint with the watermark, a sorted limited list of refused IDs, and the retried marker when present, then outputs it as JSON.

**Call relations**: paginate calls this whenever it yields a page or final checkpoint. The encoded value is handed back to the sync framework so the next run knows where to continue.

*Call graph*: called by 1 (paginate); 1 external calls (__init__).


##### `_settled`  (lines 418–419)

```
def _settled(refused: set[str], file_id: str, still_refused: bool) -> set[str]
```

**Purpose**: This updates the remembered set of refused files after trying one file. It either keeps/adds the file as still refused or removes it because it is now settled.

**Data flow**: It receives the current refused ID set, one file ID, and a boolean saying whether that file is still refused. If still refused, it returns a set including the file ID. Otherwise it returns a set with that file ID removed.

**Call relations**: paginate calls this after each listed or carried file is processed. Its result feeds into _encode_cursor so the next cursor carries only files that still need attention.

*Call graph*: called by 1 (paginate).


##### `_error_detail`  (lines 422–427)

```
def _error_detail(error: httpx.HTTPStatusError) -> dict[str, Any]
```

**Purpose**: This extracts the useful Google error object from an HTTP failure. It gives the rest of the file a consistent dictionary to inspect, even when the response body is missing or not JSON.

**Data flow**: It receives an HTTPStatusError. It tries to parse the response body as JSON, then safely pulls out the nested error field. If parsing fails or the shape is not a dictionary, it returns an empty dictionary.

**Call relations**: paginate, _carried_visit, _file_visit, and _sheet_value_records call this before classifying an HTTP error. The returned detail is then passed to helpers such as _is_per_file_refusal and _is_quota_refusal.

*Call graph*: called by 4 (_carried_visit, _file_visit, _sheet_value_records, paginate); 1 external calls (dict_or_empty).


##### `_is_per_file_refusal`  (lines 430–436)

```
def _is_per_file_refusal(status: int, detail: dict[str, Any]) -> bool
```

**Purpose**: This decides whether a Google error means “this one file cannot be read” rather than “the whole connection is broken.” That difference matters because one-file refusals can be skipped and retried without stopping the whole sync.

**Data flow**: It receives an HTTP status code and parsed Google error detail. It checks that the status is one used for file metadata fallback, that the error body exists, and that the error is not a quota refusal and not a grant-wide refusal. It returns true only for errors that can safely be treated as about the single requested file.

**Call relations**: _carried_visit, _file_visit, and _sheet_value_records call this while handling Google failures. It calls _is_quota_refusal and _is_grant_refusal to rule out broader problems.

*Call graph*: calls 2 internal fn (_is_grant_refusal, _is_quota_refusal); called by 3 (_carried_visit, _file_visit, _sheet_value_records).


##### `_is_quota_refusal`  (lines 439–442)

```
def _is_quota_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This detects errors caused by Google usage limits, such as rate limits or daily quota exhaustion. These should fail the run rather than be mistaken for missing file permission.

**Data flow**: It receives parsed Google error detail. It checks the top-level status and the reasons listed in Google's error entries. It returns true when the error names a known quota or usage-limit condition.

**Call relations**: paginate uses this when deciding whether a 401 or 403 should skip the stream or raise. _is_per_file_refusal also uses it to make sure quota problems are not treated as ordinary per-file refusals.

*Call graph*: called by 2 (paginate, _is_per_file_refusal); 1 external calls (list_or_empty).


##### `_is_grant_refusal`  (lines 445–450)

```
def _is_grant_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This detects errors that mean the user's authorization grant or Google project setup cannot read Drive or Sheets at all. Examples include missing scopes or a disabled API.

**Data flow**: It receives parsed Google error detail. It looks for known permission/setup reasons in Google's error list, and for Google service-domain details that indicate a credential, project, or service problem. It returns true when the refusal is grant-wide rather than file-specific.

**Call relations**: _is_per_file_refusal calls this to avoid falling back on one file when the real problem is the whole connector's access. That helps paginate and the file-reading paths make the correct skip-or-fail decision.

*Call graph*: called by 1 (_is_per_file_refusal); 1 external calls (list_or_empty).


##### `_sheet_records`  (lines 453–474)

```
def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This converts a spreadsheet record into one record per tab. It lets the system sync tab-level information separately from the whole spreadsheet.

**Data flow**: It receives a spreadsheet dictionary with a spreadsheet ID, tab list, title, and timestamps. It walks through the tab entries, ignores malformed ones without a sheet ID, and creates records that include the tab data plus parent spreadsheet information and inherited created/updated times. It returns the list of tab records.

**Call relations**: _visit_records calls this when the requested stream is sheets. The records it returns are later paged by paginate and rendered by GoogleSheetsConnector.render.

*Call graph*: called by 1 (_visit_records).


##### `_sheet_value_record`  (lines 477–490)

```
def _sheet_value_record(spreadsheet: dict[str, Any], title: str, sheet_id: Any, value_range: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This wraps one tab's returned cell values with the identifying information the sync system needs. It gives the raw value range a stable ID and links it back to its spreadsheet and tab.

**Data flow**: It receives the parent spreadsheet record, the tab title, the tab ID, and one valueRange response from Google. It copies the valueRange fields and adds an ID, spreadsheet ID and title, sheet ID and title, and inherited timestamps. It returns one complete sheet-value record.

**Call relations**: _sheet_value_records calls this after each successful batch or single-tab values request. The resulting records are handed back through _visit_records to paginate.

*Call graph*: called by 1 (_sheet_value_records).


##### `_quoted_sheet_range`  (lines 493–495)

```
def _quoted_sheet_range(title: str) -> str
```

**Purpose**: This formats a sheet tab title so Google reads it as a tab name in A1 notation. Quoting is important because unquoted names can be misunderstood as cell references or named ranges.

**Data flow**: It receives a tab title string. It doubles any apostrophes inside the title, then wraps the whole title in apostrophes. It returns the safely quoted range name.

**Call relations**: _sheet_value_records calls this before asking Google for tab values, both for batch requests and for fallback single-tab requests.

*Call graph*: called by 1 (_sheet_value_records).


##### `_grid_text`  (lines 498–503)

```
def _grid_text(values: Any) -> str
```

**Purpose**: This turns a grid of sheet values into simple readable text. Each row becomes one line, and cells in a row are separated with vertical bars.

**Data flow**: It receives a value that may or may not be a list of rows. If it is not a list, it returns an empty string. For each row that is itself a list, it converts cells to strings, joins cells with ' | ', joins rows with newlines, and returns the result.

**Call relations**: GoogleSheetsConnector.render calls this when rendering the sheet_values stream. It is the final step that makes raw cell arrays readable.

*Call graph*: called by 1 (render).


##### `_str`  (lines 506–507)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns optional values into display strings. It avoids showing Python placeholders or non-text data where a title is expected.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: GoogleSheetsConnector.render calls this while building headings and short bodies for spreadsheet and sheet records.

*Call graph*: called by 1 (render).


### Issue tracking and team collaboration
These connectors finish the stage with issue trackers, work-management systems, messaging platforms, workspace knowledge, and Microsoft collaboration data.

### `extensions/sources/ufo_ext_sources/jira.py`

`io_transport` · `source sync`

This connector is the bridge between a user's Jira account and the rest of the UFO sync system. Jira data lives behind Atlassian's web API, and a single login grant can cover several Jira sites. This file first asks Atlassian which sites the grant can reach, then reads each chosen kind of Jira data from each site.

The file defines the available Jira streams: projects, issues, issue comments, users, boards, and sprints. A stream is one category of records to sync. Some streams can be synced incrementally, meaning the connector remembers the last update time and asks only for newer records next time. Issues, comments, and sprints use this pattern.

Jira returns many lists in pages, like a long book split into numbered chunks. The helper for offset-based paging keeps asking for the next chunk until Jira says there are no more. For nested data, the connector fans out: comments are fetched by first reading issues, and sprints are fetched by first reading boards.

The connector also improves raw Jira data before it becomes recallable text. Jira issue descriptions and comments use Atlassian Document Format, a tree-shaped document format. The file walks that tree and extracts ordinary text. If Jira refuses access with a 401 or 403 response, the connector marks that stream as skipped rather than treating the whole sync as broken.

#### Function details

##### `JiraConnector.paginate`  (lines 73–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading one Jira stream. Given a stream name, it chooses the right Jira-reading routine and yields pages of records back to the sync runner.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor such as the last update time. It routes the request to the matching helper for projects, issues, comments, users, boards, or sprints. It outputs batches of Jira records as they arrive. If Jira says the user is not allowed to read that resource, it changes that hard web error into a clean "stream skipped" signal.

**Call relations**: The broader sync machinery calls this method when it wants records for a Jira stream. This method then calls the stream-specific readers, such as _issues or _boards. If the stream is unknown, or if Jira refuses access, it raises StreamSkipped so the caller can record a skip instead of failing the full run.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 106–110)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Jira sites the current authorization grant can access. It matters because Atlassian Cloud APIs require every later Jira request to include the site's cloud ID.

**Data flow**: It receives an authenticated HTTP client. It calls Atlassian's accessible-resources endpoint, reads the JSON response, and turns it into a list safely even if the response is missing or shaped unexpectedly. It returns site records, usually including an ID and URL.

**Call relations**: The project, issue, user, and board readers call this before making site-specific API calls. Those readers use each returned site's ID to build the correct Jira API path.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 112–132)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Jira list endpoint that is split into numbered pages. It is the shared paging loop used by most of the stream readers.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, and the name of the field where records live in Jira's response. It starts at offset zero, asks for up to 100 records, extracts the record list, yields it if present, then moves the offset forward. It stops when Jira says it is on the last page, when no records come back, or when the reported total has been reached.

**Call relations**: Stream readers such as _projects, _issues, _comments, _boards, and _sprints rely on this helper instead of each writing their own paging loop. It uses records_at to pull the actual list out of Jira's response envelope.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 134–141)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira projects from every Jira site the grant can reach. Projects are the top-level containers where Jira issues live.

**Data flow**: It gets the accessible sites, skips any site without a usable cloud ID, and calls Jira's project search endpoint for each valid site. For every page of projects, it adds context such as the cloud ID and site URL, then yields that page.

**Call relations**: JiraConnector.paginate calls this when the sync asks for the projects stream. It depends on _sites to find each Jira site and _offset_values to walk through all project pages.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 143–155)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira issues, optionally only those updated after a saved cursor. Issues are the main work items in Jira, so this is one of the connector's central streams.

**Data flow**: It receives an HTTP client and an optional cursor. If a cursor is present, it builds a Jira Query Language filter asking for issues updated after that time; otherwise it asks for all issues ordered by update time. It then reads issues from every accessible site, requesting only the fields this connector needs, adds site context, and yields pages of issues.

**Call relations**: JiraConnector.paginate calls this for the issues stream. _comments also calls it, because comments must be fetched issue by issue. It uses _sites to find Jira sites, _offset_values to page through search results, and with_context to attach site information to each issue.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 157–177)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads comments attached to Jira issues. Because Jira comments are found under each issue, it first walks through issues and then asks for comments on each one.

**Data flow**: It receives an HTTP client and an optional comment cursor. It reads all issues, takes each issue's ID and cloud ID, then calls that issue's comment endpoint. If a cursor was supplied, it keeps only comments whose updated time is newer. It yields non-empty comment pages enriched with the site, issue ID, and issue key.

**Call relations**: JiraConnector.paginate calls this for the issue_comments stream. This function calls _issues to discover the issues to inspect and _offset_values to page through each issue's comments. It then passes enriched comment records back to paginate.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 179–190)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira users from every accessible site. Unlike many Jira endpoints, this user search response is treated as one simple list rather than a paged envelope.

**Data flow**: It gets accessible sites, skips invalid cloud IDs, and calls Jira's user search endpoint for each valid site with a fixed page size. It converts the JSON response into a safe list, adds cloud ID and site URL to each user, and yields the list if it is not empty.

**Call relations**: JiraConnector.paginate calls this when the users stream is requested. It relies on _sites to know which Jira sites to query, list_or_empty to safely interpret the response, and with_context to preserve where each user came from.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 192–199)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira Agile boards from every accessible site. Boards are needed both as their own records and as the starting point for finding sprints.

**Data flow**: It asks for accessible sites, skips any site without a valid cloud ID, and calls Jira's Agile board endpoint for each one. It pages through board results, adds site context, and yields each page.

**Call relations**: JiraConnector.paginate calls this for the boards stream. _sprints also calls it first, because Jira sprints are reached through boards. It uses _sites for site discovery and _offset_values for paging.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 201–215)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads sprints from Jira Agile boards, optionally keeping only sprints updated after a saved cursor. A sprint is a time-boxed work period used by many Jira teams.

**Data flow**: It first reads boards. For each board with a usable board ID and cloud ID, it calls that board's sprint endpoint and pages through the sprint records. If a cursor is present, it filters out older sprints by updatedDate. It yields non-empty sprint pages with added cloud ID and board ID context.

**Call relations**: JiraConnector.paginate calls this for the sprints stream. This function calls _boards to discover the boards to inspect and _offset_values to read each board's sprint pages.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 217–224)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This normalizes records before cursor tracking. In particular, Jira issues store their update time inside a nested fields object, while the sync system expects the cursor field at the top level.

**Data flow**: It receives one record and its stream description. If the record is an issue, it safely reads record.fields.updated and returns a copy of the record with updated placed at the top level. For every other stream, it returns the record unchanged.

**Call relations**: The connector framework uses this as part of preparing records for storage and cursor advancement. It calls _dict_or_empty so malformed or missing fields do not crash the sync.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 226–252)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Jira records into readable page text. It makes issues and comments useful to humans by extracting titles, status details, people names, and plain description text instead of storing only raw JSON.

**Data flow**: It receives a Jira record and its stream description. For issues, it pulls summary, status, priority, assignee, reporter, and description text into a simple Markdown-like page. For comments, it uses the author as the title and extracts the comment body text. Other streams are handed back to the base connector's normal rendering behavior. It returns a title and a rendered body string.

**Call relations**: The sync framework calls this when it needs searchable text for a record. This method uses small helpers to safely read strings and dictionaries, format metadata lines, turn people objects into names, and extract text from Atlassian's document tree.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 255–256)

```
def _str(value: Any) -> str
```

**Purpose**: This small safety helper returns a value only if it is actually a string. It prevents unexpected Jira data shapes from leaking into rendered text.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string.

**Call relations**: JiraConnector.render uses this while building issue text, and _person uses it while choosing a display name or email address. It keeps those callers simple and defensive.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 259–260)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This small safety helper returns a dictionary only when the input really is a dictionary. It lets the connector read nested Jira objects without crashing when data is missing or shaped differently than expected.

**Data flow**: It receives any value. If the value is a dictionary, it returns it; otherwise it returns an empty dictionary.

**Call relations**: JiraConnector.flatten uses it to read issue fields safely. JiraConnector.render and _person use it to safely inspect nested Jira objects such as status, priority, assignee, and reporter.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 263–265)

```
def _person(value: Any) -> str
```

**Purpose**: This extracts a human-readable name from a Jira user-like object. It prefers the person's display name and falls back to their email address.

**Data flow**: It receives any value that might be a Jira person object. It safely treats it as a dictionary, reads displayName and emailAddress as strings, and returns the first useful value. If neither is present, it returns an empty string.

**Call relations**: JiraConnector.render calls this when writing issue assignee, issue reporter, and comment author text. It relies on _dict_or_empty and _str to stay safe around missing or unusual data.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 268–269)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: This formats one metadata line, such as "Status: Done", but only when there is a value to show. It keeps rendered issue pages from filling up with empty labels.

**Data flow**: It receives a label and a value string. If the value is non-empty, it returns the label and value joined as a readable line. If the value is empty, it returns an empty string.

**Call relations**: JiraConnector.render uses this while building the issue metadata block. Render then discards empty lines before joining the useful ones together.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 272–289)

```
def _doc_text(value: Any) -> str
```

**Purpose**: This turns Atlassian Document Format into plain text. Atlassian Document Format is a tree-shaped structure used for Jira descriptions and comment bodies, and this helper pulls out the readable text leaves.

**Data flow**: It receives any value, usually a nested document object from Jira. It walks through dictionaries and lists, collects every string found under a text field, joins those pieces with newlines, trims the result, and returns the plain text. If the input is missing or not a document-like shape, it returns an empty string.

**Call relations**: JiraConnector.render calls this when rendering issue descriptions and comment bodies. The helper uses its nested walk function to do the actual tree traversal.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 277–286)

```
def walk(node: Any) -> None
```

**Purpose**: This is the recursive worker inside _doc_text. It explores one node of the Atlassian document tree and collects any text it finds.

**Data flow**: It receives one node, which may be a dictionary, a list, or something else. For dictionaries, it saves the node's text value if it is a string, then visits each child under content. For lists, it visits each item. It does not return a separate value; instead, it adds found text into the surrounding chunks list.

**Call relations**: _doc_text starts this walker on the whole document value. Each call may call itself again for child nodes, like following branches of a tree until all readable leaves have been collected.


### `extensions/sources/ufo_ext_sources/linear.py`

`io_transport` · `sync run, while reading Linear streams`

Linear is a project and issue tracker, and its API speaks GraphQL, which means the client sends one structured question and gets back only the fields it asked for. This file defines those questions for every Linear collection the system knows how to import. Think of it like a set of order forms: one form asks for issues, another for projects, another for users, and so on.

The connector lists 16 Linear streams. Some streams can be synced incrementally, meaning the connector asks Linear only for records changed since the last successful run by filtering on `updatedAt`. Other streams do not support that filter, so they are fully reread each time.

The main class, `LinearConnector`, plugs into the shared REST connector framework even though Linear uses GraphQL over HTTP. Its `paginate` method sends repeated `POST /graphql` requests, follows Linear’s `pageInfo.endCursor` value to get the next page, and stops when Linear says there are no more pages. If Linear refuses access with HTTP 401 or 403, the stream is marked as skipped rather than silently failing the whole idea of the sync. But if GraphQL itself reports errors, the connector fails loudly so it does not save partial or misleading data.

The file also improves how important records are shown. Issues, projects, comments, and users are rendered as readable text with useful labels instead of raw nested API data.

#### Function details

##### `_stream`  (lines 32–42)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a stream description for one Linear collection, such as `issues` or `projects`. The rest of the connector uses this description to know what the stream is called, whether it has a change-tracking cursor, and whether it is a main searchable content stream.

**Data flow**: It receives a stream name plus optional settings, such as which field tracks updates and whether the stream is canonical. It packages those choices into a `StreamSpec` object. The result is stored in the file’s stream list and later used during syncing.

**Call relations**: This helper is used while the file is loaded to build the connector’s catalog of Linear streams. It hands the finished stream definition to the shared source framework through `StreamSpec`, so later methods like pagination and rendering can make decisions based on the stream’s name and cursor field.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 269–312)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Linear stream page by page from the GraphQL API. It is the part that actually asks Linear for data, follows Linear’s next-page pointer, and yields batches of records to the sync system.

**Data flow**: It starts with an HTTP client, a stream description, and an optional saved cursor from a previous run. It chooses the matching GraphQL query, adds an `updatedAt` filter when the stream supports incremental syncing, sends the request, checks for access refusal or GraphQL errors, extracts the returned `nodes`, and yields them as record batches. After each page, it reads Linear’s `pageInfo` to decide whether to request another page; when there is no valid next page, it stops.

**Call relations**: The shared connector framework calls this method when it wants records from a Linear stream. Inside the loop, it uses the base connector’s posting behavior to talk to Linear, uses `list_or_empty` to safely normalize the returned records, and raises `StreamSkipped` when Linear says the token or permissions are not good enough for that stream.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 314–353)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns selected Linear records into readable text for storage or recall. Instead of saving only raw API-shaped data, it creates a human-friendly title and body for issues, projects, comments, and users.

**Data flow**: It receives one Linear record and the stream it came from. For issues and projects, it pulls out fields like title, state, priority, assignee, lead, target date, and description. For comments, it uses the comment body. For users, it builds a small labeled summary with name and email. If the stream is not specially supported, it falls back to the parent connector’s default rendering. It returns a title and a Markdown-like text body.

**Call relations**: This method is called after records have been fetched and the system needs text suitable for indexing or display. It relies on `_str`, `_ref_id`, and `_labeled` to safely clean values and format labels, then hands the final title and body back to the source framework.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 356–357)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns a value into text only when it is already a string. This prevents accidental display of non-text values like dictionaries, numbers, or missing data in places meant for readable prose.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise, it returns an empty string, so later formatting code can simply skip or leave blank unsafe values.

**Call relations**: The rendering code calls this helper whenever it pulls text from a Linear record. `_ref_id` also uses it after finding an `id`, so both direct fields and nested reference IDs are cleaned the same way.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 360–361)

```
def _ref_id(value: Any) -> str
```

**Purpose**: Extracts an `id` from a nested Linear reference, such as an assignee or project lead. Linear often represents related objects as small dictionaries, and this helper turns those into a simple readable ID string.

**Data flow**: It receives any value. If the value is a dictionary, it looks for its `id` field and passes that through `_str`; if not, it returns an empty string. The output is either a safe ID string or nothing.

**Call relations**: The render method uses this when building issue and project summaries, for example to show an assignee or lead. It delegates final text safety to `_str`, so malformed or missing references do not break rendering.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 364–365)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: Formats a short list of label-and-value pairs into readable lines, skipping empty values. It is used to make record summaries look like simple notes, such as `state: open` or `email: person@example.com`.

**Data flow**: It receives a list of pairs, where each pair has a label and a text value. It keeps only pairs with a non-empty value, formats each as `label: value`, and joins them with line breaks. The result is a compact block of human-readable metadata.

**Call relations**: The render method calls this helper when it needs to build the metadata section for issues, projects, and users. It keeps that rendering code simple by centralizing the repeated label formatting.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/microsoft_teams.py`

`io_transport` · `source sync run`

Microsoft Teams stores conversations in several layers: a user belongs to teams, teams contain channels, channels contain messages, and the user may also have separate chats with their own messages. This connector walks that structure like opening folders inside folders. It first asks Microsoft Graph for the signed-in user’s joined teams and chats. Then it asks for each team’s channels and each channel’s messages, and for each chat’s messages.

Microsoft Graph returns long lists in pages, so this file relies on shared OData paging support. OData is a common Microsoft-style web API format where results are under a `value` field and a `nextLink` points to the next page. Message streams are incremental: if the system already synced up to a saved timestamp, this connector only keeps messages whose `lastModifiedDateTime` is newer.

The file is deliberately read-only. It never sends or edits Teams messages. It also tries to be forgiving. If one team, channel, or chat cannot be read because access was removed or the item disappeared, it skips that parent and keeps syncing the rest. But if the whole grant cannot list teams or chats, it reports the stream as skipped rather than treating the run as a crash. For message display, it converts Microsoft’s HTML message body into simpler plain text.

#### Function details

##### `MicrosoftTeamsConnector._teams`  (lines 58–62)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the full list of Microsoft Teams that the signed-in user has joined. Other parts of the connector use this as the starting point for finding channels and channel messages.

**Data flow**: It receives an authenticated HTTP client. It asks Microsoft Graph for `/me/joinedTeams`, follows all returned pages, collects every team record into one list, and returns that list.

**Call relations**: When the connector needs the teams stream directly, `paginate` calls this function. When it needs channels, `_channels` calls this first so it knows which teams to look inside.

*Call graph*: called by 2 (_channels, paginate).


##### `MicrosoftTeamsConnector._channels`  (lines 64–77)

```
async def _channels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds the channels inside each joined team. It attaches the parent team’s ID and name to each channel record, so later records still know where they came from.

**Data flow**: It starts with an HTTP client, calls `_teams` to get the user’s teams, and skips any team without a usable ID. For each valid team, it asks Microsoft Graph for that team’s channels, adds context such as `team_id` and `team_name`, and yields pages of channel records. If a single team cannot be read because access is forbidden or the team is missing, it skips that team and continues.

**Call relations**: This function sits between team discovery and message discovery. `paginate` uses it to produce the channels stream, and `_channel_messages` uses it to know which channels should be searched for messages.

*Call graph*: calls 1 internal fn (_teams); called by 2 (_channel_messages, paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._channel_messages`  (lines 79–110)

```
async def _channel_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages from every accessible channel in every accessible joined team. It supports incremental syncing by ignoring messages that are not newer than the saved cursor timestamp.

**Data flow**: It receives an HTTP client and an optional cursor, which is the last synced modification time. It gets channel records from `_channels`, uses their team and channel IDs to request channel messages, filters out older messages when a cursor is present, adds context such as `team_id`, `channel_id`, and `thread_id`, and yields only non-empty pages. If one channel cannot be read because it is forbidden or missing, it skips that channel and continues.

**Call relations**: `paginate` calls this when the requested stream is `channel_messages`. This function depends on `_channels` for the list of places to search and uses `with_context` so downstream sync code can preserve where each message belongs.

*Call graph*: calls 1 internal fn (_channels); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector._chats`  (lines 112–116)

```
async def _chats(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the full list of chats visible to the signed-in user. This is the starting point for syncing one-to-one or group chat messages outside team channels.

**Data flow**: It receives an authenticated HTTP client. It asks Microsoft Graph for `/me/chats`, follows all returned pages, gathers every chat record into a list, and returns that list.

**Call relations**: `paginate` calls this when syncing the chats stream itself. `_chat_messages` calls it first so it can visit each chat and fetch its messages.

*Call graph*: called by 2 (_chat_messages, paginate).


##### `MicrosoftTeamsConnector._chat_messages`  (lines 118–138)

```
async def _chat_messages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads messages from each accessible Microsoft Teams chat. Like channel messages, it can sync only messages changed after the saved cursor timestamp.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It calls `_chats`, skips chats without a usable ID, requests each chat’s messages from Microsoft Graph, filters out older messages if a cursor exists, adds context such as `chat_id` and `thread_id`, and yields pages that still contain messages. If one chat is forbidden or missing, it skips that chat and keeps going.

**Call relations**: `paginate` calls this for the `chat_messages` stream. It builds on `_chats` to know which chats exist and uses `with_context` so later sync and rendering steps can tell which chat a message came from.

*Call graph*: calls 1 internal fn (_chats); called by 1 (paginate); 1 external calls (with_context).


##### `MicrosoftTeamsConnector.paginate`  (lines 140–173)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the connector’s traffic director. Given a requested stream, it chooses the right helper to fetch teams, channels, chats, channel messages, or chat messages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching helper, and yields pages of records to the sync system. If Microsoft Graph refuses access to a top-level stream because the credential lacks permission, it raises `StreamSkipped`, which tells the run to record a skipped stream instead of a broken sync. If the stream name is unknown, it also reports it as skipped.

**Call relations**: The larger source-sync framework calls `paginate` whenever it wants records for one Teams stream. `paginate` then hands the work to `_teams`, `_channels`, `_channel_messages`, `_chats`, or `_chat_messages`, and translates broad authorization failures into a clear skip signal.

*Call graph*: calls 6 internal fn (__init__, _channel_messages, _channels, _chat_messages, _chats, _teams).


##### `MicrosoftTeamsConnector.render`  (lines 175–181)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw Teams record into a title and readable page text for recall or search. For ordinary structural records like teams and channels it uses the default rendering, but for messages it makes the HTML body easier to read.

**Data flow**: It receives one record and the stream it belongs to. If the record is not a channel or chat message, it delegates to the parent connector’s default rendering. For message records, it pulls out the subject, reads `body.content`, strips HTML tags, builds a Markdown-style heading, and returns the title plus the cleaned body text.

**Call relations**: The sync framework calls this after records have been fetched and need to become human-readable content. It uses `_str` to safely read the subject, `get_path` to reach the nested message body, and `_strip_html` to simplify Microsoft Graph’s HTML.

*Call graph*: calls 2 internal fn (_str, _strip_html); 1 external calls (get_path).


##### `_strip_html`  (lines 184–187)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: Removes simple HTML tags from a Teams message body so the stored text is easier for people to read. It is a small cleanup helper used only when rendering messages.

**Data flow**: It receives any value. If the value is not a string, it returns `None`. If it is a string, it replaces anything that looks like an HTML tag with a space, trims extra space at the ends, and returns the cleaned text.

**Call relations**: `MicrosoftTeamsConnector.render` calls this after reading `body.content` from a message record. Its cleaned output becomes the body of the page that the rest of the system stores or displays.

*Call graph*: called by 1 (render).


##### `_str`  (lines 190–191)

```
def _str(value: Any) -> str
```

**Purpose**: Safely converts an optional record field into a string title. It prevents non-text values from leaking into the rendered message title.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: `MicrosoftTeamsConnector.render` calls this when reading a message subject. The result is used as the page title and in the rendered heading.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/monday.py`

`io_transport` · `during source sync runs`

monday.com exposes its data through GraphQL, which is a query language where the caller asks for exactly the fields it wants. This file is the read-only connector for that API. Without it, the system would not know how to fetch monday.com content, page through long result lists, skip data it is not allowed to read, or shape monday records into the common format used elsewhere.

The file defines several stream descriptions, one for each kind of monday.com object the system can sync. A stream is like a lane on a conveyor belt: one lane for users, one for boards, one for items, and so on. The main `MondayConnector` class then decides how each lane is fetched.

Most top-level monday.com lists use simple page numbers, so `_paged_root` repeatedly asks for page 1, page 2, and so forth until monday.com stops returning records. Board items are different: monday.com returns a special cursor, like a bookmark, that must be sent back to get the next item page. Activity logs are also special because they must be requested board by board.

The connector is careful about partial or forbidden data. If monday.com returns GraphQL errors, or if the API refuses access with an authentication or permission error, the stream is marked as skipped instead of pretending the sync was complete. Finally, `flatten` turns monday-specific shapes into common fields such as name, body, author, created time, and parent record.

#### Function details

##### `_extract_person_ids`  (lines 70–98)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: This helper pulls assigned person IDs out of monday.com item column data. monday.com stores assignees inside board-specific “people” columns, so this function looks for the column type rather than relying on a fixed column name.

**Data flow**: It receives the raw `column_values` field from a monday.com item. It ignores anything that is not a list, then scans each column for one marked as a people column. For each usable people column, it reads the JSON value, finds entries that are people rather than teams, and returns their IDs as strings. Bad, empty, or unexpected values are quietly skipped.

**Call relations**: When `MondayConnector._items` fetches item records, it calls this helper for each item. The returned IDs are added to the item as `assignee_ids`, so later parts of the sync can see who is assigned without understanding monday.com’s nested column format.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 106–118)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This is the shared way the connector sends a GraphQL request to monday.com and unwraps the response. It also turns monday.com GraphQL errors into a controlled stream skip, so the sync does not save a misleading partial result.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional variables. It posts them to monday.com’s API root, reads the returned body, checks for an `errors` section, and raises `StreamSkipped` if monday.com refused or could not satisfy the query. If the response contains a normal `data` object, it returns that object; otherwise it returns an empty dictionary.

**Call relations**: This is the central doorway to monday.com for the rest of the file. `_paged_root`, `_items`, `_activity_logs`, and `paginate` all call it whenever they need data from the API. By putting the error check here, all those callers get the same safe behavior.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, paginate).


##### `MondayConnector._paged_root`  (lines 120–143)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches monday.com collections that use simple numbered pages, such as users, workspaces, boards, and updates. It keeps asking for the next page until no more records are returned.

**Data flow**: It receives the API client, the monday.com field to query, the fields to request for each record, and optionally a saved cursor value. It starts at page 1, asks monday.com for up to 100 records, converts the answer into a list, and optionally filters out records older than or equal to the saved cursor. Each non-empty page is yielded to the caller. When a page has no remaining records, it stops.

**Call relations**: `paginate` uses this helper for several streams that follow monday.com’s normal page-number pattern. `_boards` also uses it to collect board records before item and activity-log fetches fan out by board. `_paged_root` relies on `_graphql` for the actual API call and on list-cleaning helpers to tolerate missing or oddly shaped response data.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, paginate); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 145–156)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper collects all monday.com boards and their basic workspace information. Other parts of the connector need the board list before they can ask for board-specific data like items and activity logs.

**Data flow**: It receives the API client. It calls `_paged_root` for the `boards` collection, requesting board identity, description, state, dates, URL, and workspace details. It gathers all yielded pages into one list and returns that complete board list.

**Call relations**: `MondayConnector._items` and `MondayConnector._activity_logs` call this first because monday.com requires board IDs for those follow-up queries. In that flow, `_boards` acts like a directory: it finds every board so the connector can then visit each board individually.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 158–217)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches item records from every monday.com board. Items need special treatment because monday.com pages them with an opaque cursor, which is a server-provided bookmark for the next page.

**Data flow**: It receives the API client and an optional saved cursor for incremental sync. It first gets all boards, then loops through each board. For the first item page on a board, it asks through the board’s `items_page`; for later pages, it sends monday.com the cursor returned by the previous page. For every item, it extracts assignee IDs from column values. If a saved cursor is present, it keeps only items whose `updated_at` value is newer. It yields each non-empty page of item records.

**Call relations**: `paginate` calls this when the active stream is `items`. Inside, it depends on `_boards` to know which boards to visit, `_graphql` to fetch each item page, `_extract_person_ids` to make assignees easy to use, and safe dictionary/list helpers to handle missing pieces in monday.com’s response.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (paginate); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 219–247)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches recent activity log entries from every monday.com board. Activity logs are board-scoped, so the connector must ask for them one board at a time.

**Data flow**: It receives the API client and an optional saved cursor. It gets all boards, then for each board asks monday.com for up to 100 activity log entries. It adds the board ID onto each log record so the log can later be connected back to its board. If a saved cursor is present, it keeps only logs with a newer `created_at` value. Each non-empty group of logs is yielded.

**Call relations**: `paginate` calls this when syncing the `activity_logs` stream. The function first uses `_boards` as its list of places to visit, then uses `_graphql` for each board request. The yielded records flow back to `paginate`, which passes them onward to the broader sync machinery.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (paginate); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 249–323)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher that decides how to fetch each monday.com stream. Given a stream such as users, boards, or items, it chooses the right query pattern and yields pages of records.

**Data flow**: It receives the API client, a stream description, and an optional saved cursor. It checks the stream name and runs the matching fetch logic: simple paged queries for users, workspaces, boards, and updates; direct single queries for teams and tags; cursor-based item paging for items; and board-by-board fetching for activity logs. It yields pages of records as they are found. If a stream is unknown, or if monday.com refuses access with an authentication or permission status, it raises `StreamSkipped` so the run records that this stream could not be safely synced.

**Call relations**: The connector framework calls `paginate` when it wants records for one stream. `paginate` then hands off to `_paged_root`, `_items`, `_activity_logs`, or `_graphql` depending on the stream. It is the traffic controller for the file: it does not store the data itself, but it chooses the route each kind of monday.com data must take.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _graphql, _items, _paged_root).


##### `MondayConnector.flatten`  (lines 325–366)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This turns raw monday.com records into a more common shape used by the rest of the system. It keeps the original fields but adds or normalizes familiar fields such as name, body, author, status, created time, and parent record.

**Data flow**: It receives one raw record and the stream it came from. Depending on the stream name, it copies the record and fills in standardized fields. For updates, it chooses a readable body and derives an author from the creator object. For activity logs, it maps the event and data into subject and body fields and connects the log to its board. For streams that need no special treatment, it returns the record unchanged.

**Call relations**: After `paginate` has produced raw pages, the wider connector framework can call `flatten` on each record before storing or indexing it. `flatten` uses a safe dictionary helper for nested creator data so missing creator details do not break the sync.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/notion.py`

`io_transport` · `during source sync when reading Notion records and rendering them for recall`

Notion stores information in a nested way: a page has properties, its body is made of blocks, blocks can have child blocks, comments are fetched separately, and text is often stored as small “rich text” pieces rather than one simple string. Without this connector, the system could still receive raw Notion JSON, but that would be hard for people or search tools to recall and understand.

The file defines a NotionConnector, which is a read-only bridge to the Notion API. It knows which Notion streams exist, how to ask Notion for each one, and how to follow Notion’s pagination cursors so large workspaces can be read in chunks. For pages and data sources, it searches Notion in last-edited order so later syncs can skip records already seen. For page bodies, it walks the block tree recursively, like opening folders inside folders, but stops at a safe depth and does not descend into child pages or databases because those are treated as their own records. For comments, it first finds pages, then asks for comments on each page.

A key job here is rendering. Notion’s API returns structured data, but this connector extracts the words a human would actually read: page titles, property values, paragraph text, to-do checkboxes, comment bodies, and user names or emails. If Notion refuses access because the integration lacks permission, the stream is marked as skipped rather than making the whole sync fail.

#### Function details

##### `NotionConnector._make_client`  (lines 80–83)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Notion. It adds the required Notion-Version header, which tells Notion which version of its API rules this connector expects.

**Data flow**: It receives a base API address and a credential object supplied by the wider system. It first lets the parent REST connector create the basic client, then adds the Notion version header. It returns a ready-to-use async HTTP client for Notion requests.

**Call relations**: This is part of the connector setup before any Notion stream is read. The parent connector supplies the common client behavior, and this method adds the Notion-specific requirement that every request identify the API version.


##### `NotionConnector.paginate`  (lines 85–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading Notion streams. Given a requested stream such as pages, blocks, comments, or users, it chooses the right fetching method and yields records in pages of results.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. It checks the stream name, calls the matching helper, and passes each batch of Notion records onward. If Notion responds with a permission error, it turns that into a skipped stream instead of a hard failure.

**Call relations**: The sync system calls this when it wants records from one Notion stream. It hands off to _collection for users, _search for pages and data sources, _comments for comments, and _blocks for page body blocks. If a stream is unknown or Notion refuses access, it raises StreamSkipped so the larger run can record that outcome cleanly.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 117–140)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads pages or data sources through Notion’s search endpoint. It sorts results from oldest edited to newest edited so incremental syncs can compare them against the last saved edit time.

**Data flow**: It receives the Notion client, the kind of object to search for, and an optional cursor timestamp. It repeatedly sends POST requests to /search, extracts the results list safely, filters out records whose last_edited_time is not newer than the cursor, and yields non-empty batches. It follows Notion’s next_cursor until there are no more pages.

**Call relations**: paginate uses this directly for pages and data_sources. _blocks and _comments also use it first to find pages, because blocks and comments are discovered by starting from each page.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 142–152)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This finds the body blocks that make up Notion pages. It starts from every page and then asks for that page’s child blocks so the page body can be captured as readable text.

**Data flow**: It receives the Notion client and an optional cursor timestamp for changed blocks. It searches all pages without filtering pages by that cursor, takes each page id, and asks _block_children to walk the block tree under that page. It yields each batch of block records produced by that recursive walk.

**Call relations**: paginate calls this when the blocks stream is being synced. This function relies on _search to discover page ids and delegates the actual recursive traversal to _block_children.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 154–173)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through the nested block tree under a page or block. It captures child blocks while avoiding infinite or overly deep walks.

**Data flow**: It receives the Notion client, a block id to inspect, the current depth, and an optional cursor timestamp. If the depth is beyond the configured limit, it stops. Otherwise it fetches that block’s children, yields only the children edited after the cursor when a cursor is present, and then descends into children that themselves have children, except for block types that should be treated as separate records or not expanded.

**Call relations**: _blocks starts this process for each page. The function calls _collection to fetch each level of children, then calls itself again for deeper levels, like walking a tree branch by branch.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 175–191)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This collects comments attached to Notion pages. Because comments are fetched per page, it first finds pages and then asks Notion for comments on each one.

**Data flow**: It receives the Notion client and an optional cursor timestamp. It searches all pages, takes each valid page id, calls the comments endpoint with that page id, filters comments to only those created after the cursor when needed, and yields non-empty comment batches.

**Call relations**: paginate calls this for the comments stream. It uses _search to find pages and _collection to handle the paginated Notion comments endpoint for each page.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 193–207)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared helper for Notion endpoints that return a paginated list of results. It hides the repeated work of following Notion’s start_cursor and next_cursor fields.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the parent REST machinery to fetch cursor-based pages, using Notion’s field names for records, cursors, and page size. It yields each list of result records as it arrives.

**Call relations**: paginate uses this for users, _block_children uses it for block children, and _comments uses it for page comments. It acts as the common conveyor belt for simple GET-based Notion collections.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 209–231)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns raw Notion API records into readable text with a title and body. It is what makes synced Notion content useful for recall instead of leaving it as hard-to-read structured JSON.

**Data flow**: It receives one Notion record and the stream it came from. Depending on the stream, it extracts the meaningful human text: page title and properties, data source title and description, block text, comment text, or user name and email. It chooses a fallback title when needed, adds a simple heading, and returns the title plus the final rendered text.

**Call relations**: The wider source system calls this after records are fetched. It delegates stream-specific text extraction to helper functions such as _page_title, _properties_text, _block_text, _rich_text_text, _str, and _user_text, then hands back prose suitable for indexing or display.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 234–235)

```
def _str(value: Any) -> str
```

**Purpose**: This small helper safely returns a value only if it is already a string. It prevents non-text values from accidentally being treated as readable text.

**Data flow**: It receives any value. If the value is a string, it returns that string. Otherwise it returns an empty string.

**Call relations**: render and several text helpers call this whenever Notion may or may not have provided a string field. It is a small safety check used while building readable output.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 238–246)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: This extracts plain words from Notion’s rich text format. Notion stores formatted text as a list of pieces, and this helper joins the plain_text from those pieces into one readable string.

**Data flow**: It receives a value that should be a list of rich text parts. If it is not a list, it returns an empty string. Otherwise it keeps the plain_text from each valid part, joins them together, trims extra space, and returns the result.

**Call relations**: render and helper functions use this wherever Notion text may be stored as rich text, such as page titles, page properties, blocks, comments, and data source descriptions.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 249–258)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: This finds the visible title of a Notion page. Page titles are stored inside the page’s properties, so this helper searches those properties for the one marked as the title.

**Data flow**: It receives a page record. It looks at the properties dictionary, finds a property whose type is title, extracts its rich text with _rich_text_text, and returns the first non-empty title it finds. If no title is available, it returns an empty string.

**Call relations**: render calls this when rendering page records. It relies on _rich_text_text because Notion page titles use the same rich text structure as other formatted text.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 261–270)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion page’s properties into simple lines of text. It makes database-style fields like status, people, dates, and checkboxes readable.

**Data flow**: It receives a page record. It reads the properties dictionary, asks _property_text to turn each supported property into text, and builds lines in the form “property name: value” for non-empty values. It returns all lines joined with newlines.

**Call relations**: render calls this for page records after finding the title. It delegates the details of each property type to _property_text.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 273–292)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: This converts one Notion property value into readable text. It knows the common Notion property shapes, such as title, rich text, select, people, date, number, URL, email, phone number, and checkbox.

**Data flow**: It receives one property dictionary. It checks the property’s type, pulls the matching value field, and converts supported types into a string: rich text is joined, select-like fields use their names, lists are joined with commas, dates use their start date, and simple values are stringified. Unsupported or missing values become an empty string.

**Call relations**: _properties_text calls this for every property on a page. It uses _rich_text_text for formatted text fields and _str for fields that may or may not contain normal strings.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 295–305)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: This extracts the readable text from a Notion block, such as a paragraph, heading, list item, code block, to-do item, child page, or child database reference.

**Data flow**: It receives a block record. It looks up the block’s type-specific content. For child pages and child databases, it returns their title. For normal text blocks, it joins their rich text. For to-do blocks, it adds [x] or [ ] to show whether the task is checked. If the block has no readable content, it returns an empty string.

**Call relations**: render calls this for records from the blocks stream. It uses _rich_text_text for normal block text and _str for child page or child database titles.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 308–311)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion user record into a short readable description. It includes the user’s name and, when available, their email address.

**Data flow**: It receives a user record. It looks for the top-level name and the nested person.email field, keeps only real strings, and joins the available parts with a newline. The result is a compact text body for that user.

**Call relations**: render calls this when rendering the users stream. It uses _str to safely ignore missing or non-string name and email fields.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


### `extensions/sources/ufo_ext_sources/outlook.py`

`io_transport` · `source sync and incremental polling`

This connector is the project’s bridge to Outlook. Microsoft Graph is the web API used by Microsoft 365; it returns Outlook data in pages and, for many resources, gives a special “delta link” that works like a bookmark. The first sync walks through all available pages, then saves that bookmark. A later sync starts from the bookmark and receives only new, changed, or deleted items.

The file defines five streams: contacts, messages, conversations, events, and mail folders. Contacts, messages, events, and folders come mostly from Microsoft Graph’s delta endpoints. Messages and contacts are a little more complicated because they can live in multiple folders, so the connector stores a small folder-to-bookmark map as JSON. Conversations are not a separate Outlook object here. They are built by reading messages and grouping them by conversation ID, like sorting letters into thread piles.

The connector also protects the wider sync run from permission problems. If Microsoft Graph refuses access with 401 or 403, it raises a “stream skipped” signal instead of crashing the whole job. Finally, the flattening step reshapes Microsoft’s raw fields into friendlier fields such as email, phone, snippet, start time, and location.

#### Function details

##### `_graph_instant`  (lines 44–45)

```
def _graph_instant(value: datetime) -> str
```

**Purpose**: This helper turns a Python date and time into the exact UTC timestamp format Microsoft Graph expects in filters. It is used when the connector needs to ask Graph for items after a certain point in time.

**Data flow**: It takes a datetime value, converts it to UTC, formats it as a string like 2024-01-01T12:00:00Z, and returns that string. It does not change anything outside itself.

**Call relations**: When conversation or message syncs need a starting time, they call this helper before building the Microsoft Graph filter. It keeps timestamp formatting consistent across those flows.

*Call graph*: called by 2 (_conversation_pages, _message_delta_pages); 1 external calls (astimezone).


##### `_strip_html`  (lines 48–51)

```
def _strip_html(value: Any) -> str | None
```

**Purpose**: This helper removes simple HTML tags from text, mainly so calendar event descriptions are readable as plain text. If the value is not text, it safely returns nothing.

**Data flow**: It receives any value. If the value is a string, it replaces HTML tags with spaces, trims the result, and returns the cleaned text. If the value is not a string, it returns null.

**Call relations**: The flattening step calls this when preparing event records. It turns Microsoft’s HTML event body into a simpler description field for the rest of the system.

*Call graph*: called by 1 (flatten).


##### `_first_email`  (lines 54–62)

```
def _first_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper picks the first usable email address from an Outlook contact. Contacts can contain several email slots, so this gives the system one main email value to index or display.

**Data flow**: It reads the contact record’s emailAddresses list. It walks through that list, looks inside each address object for emailAddress.address, and returns the first non-empty string it finds. If none are usable, it returns null.

**Call relations**: The flattening step calls this while reshaping contact records. It uses the shared get_path helper to safely read nested data without failing if Microsoft leaves part of the structure out.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `_phone`  (lines 65–75)

```
def _phone(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper chooses a useful phone number from an Outlook contact. It prefers the mobile phone, then falls back to the first business phone.

**Data flow**: It receives a contact record. It first checks mobilePhone and returns it if present. If not, it checks businessPhones, scans the list for the first non-empty string, and returns that. If there is no phone number, it returns null.

**Call relations**: The flattening step calls this for contacts. It gives downstream code a single phone field instead of forcing every caller to understand Outlook’s several phone fields.

*Call graph*: called by 1 (flatten).


##### `OutlookConnector.paginate_source`  (lines 127–138)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the connector’s public paging entry for the sync framework. It accepts the normal stream request plus an optional backfill floor, then forwards the work to the connector’s main paginate method.

**Data flow**: It receives an HTTP client, a stream description, an optional saved cursor, an optional user ID, and an optional backfill date. It passes the stream, cursor, and backfill date into paginate and returns the pages that paginate yields.

**Call relations**: The broader source runner calls this when it wants Outlook records. This method is a small adapter: it widens the standard connector interface so mail streams can honor their pinned backfill window, then hands off to OutlookConnector.paginate.

*Call graph*: calls 1 internal fn (paginate).


##### `OutlookConnector.paginate`  (lines 140–182)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for Outlook syncing. It looks at which stream is being requested and sends the request to the right specialized reader.

**Data flow**: It receives an HTTP client, a stream spec, a saved cursor, and sometimes a backfill date. Based on the stream name, it yields pages from the matching method for conversations, messages, contacts, events, or mail folders. If Microsoft refuses access with 401 or 403, it turns that into a skipped stream; if the stream name is unknown, it also reports it as skipped.

**Call relations**: paginate_source calls this during a sync. From here, the flow branches into the stream-specific page readers, and those readers return records, deletion markers, and next cursors back through this method to the runner.

*Call graph*: calls 6 internal fn (__init__, _contact_delta_pages, _conversation_pages, _event_delta_pages, _graph_delta_pages, _message_delta_pages); called by 1 (paginate_source).


##### `OutlookConnector._conversation_pages`  (lines 184–224)

```
async def _conversation_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This builds conversation records from messages because this connector treats an email thread as a derived object. It reads messages in time order and collapses all messages with the same conversation ID into one conversation summary.

**Data flow**: It receives an HTTP client, an optional cursor, and an optional start date. It builds Microsoft Graph query parameters, reads message pages, groups messages by conversationId, keeps the earliest created time and latest updated message per thread, and finally yields a list of conversation records. It does not emit deletion records because conversations are derived from messages.

**Call relations**: OutlookConnector.paginate calls this when the conversations stream is requested. It uses _graph_instant when it needs to format the first backfill date for Microsoft Graph.

*Call graph*: calls 1 internal fn (_graph_instant); called by 1 (paginate).


##### `OutlookConnector._graph_delta_pages`  (lines 226–263)

```
async def _graph_delta_pages(self, client: httpx.AsyncClient, *, initial_path: str, cursor: str | None, params: dict[str, Any] | None=None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the reusable reader for Microsoft Graph delta endpoints. A delta endpoint returns changed records, deletion notices, and a bookmark for the next sync.

**Data flow**: It receives an HTTP client, an initial Graph path, an optional saved cursor, and optional query parameters. It requests pages from Graph, separates normal records from items marked as removed, captures the next link or delta link, and yields a StreamPage containing records, deleted IDs, and the next cursor. It keeps following nextLink pages until Graph stops giving more pages.

**Call relations**: The message, contact, event, and mail-folder flows all rely on this method for delta sync behavior. paginate also calls it directly for mail folders. It wraps each Graph response into the standard StreamPage shape so the rest of the system does not need to know Microsoft’s response format.

*Call graph*: called by 4 (_contact_delta_pages, _event_delta_pages, _message_delta_pages, paginate); 1 external calls (__init__).


##### `OutlookConnector._message_delta_pages`  (lines 265–288)

```
async def _message_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This syncs Outlook messages across all mail folders. Because each folder has its own Microsoft Graph delta bookmark, it stores and updates a folder-to-cursor map.

**Data flow**: It receives an HTTP client, an optional encoded cursor map, and an optional backfill date. It decodes the cursor map, lists mail folders, builds an initial delta path for each folder that needs one, and reads each folder through _graph_delta_pages. For every message record, it adds the folder ID, updates that folder’s saved cursor, re-encodes the cursor map, and yields a StreamPage.

**Call relations**: OutlookConnector.paginate calls this for the messages stream. This method calls _list_mail_folders to discover folders, _decode_cursor_map and _encode_cursor_map to maintain per-folder bookmarks, _graph_instant to build an initial time filter, and _graph_delta_pages to do the actual Graph paging.

*Call graph*: calls 5 internal fn (_graph_delta_pages, _list_mail_folders, _decode_cursor_map, _encode_cursor_map, _graph_instant); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._contact_delta_pages`  (lines 290–317)

```
async def _contact_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This syncs Outlook contacts from the default contact area and from any contact folders. Like messages, it keeps a separate delta bookmark for each place contacts can live.

**Data flow**: It receives an HTTP client and an optional encoded cursor map. It decodes the map, builds a folder list containing the default contacts plus named contact folders, and reads each one through _graph_delta_pages. After each page, it updates the matching folder cursor, encodes the whole map, and yields records and deletes in a StreamPage. If the default contacts delta endpoint is missing or invalid, it skips that default area and continues.

**Call relations**: OutlookConnector.paginate calls this for the contacts stream. It calls _list_contact_folders to find extra folders, uses the cursor encode/decode helpers to remember each folder’s place, and delegates the Microsoft delta paging to _graph_delta_pages.

*Call graph*: calls 4 internal fn (_graph_delta_pages, _list_contact_folders, _decode_cursor_map, _encode_cursor_map); called by 1 (paginate); 2 external calls (__init__, quote).


##### `OutlookConnector._event_delta_pages`  (lines 319–330)

```
async def _event_delta_pages(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This syncs calendar events through Microsoft Graph’s calendar view delta endpoint. It limits the calendar window to a practical range around the current date rather than asking for all time.

**Data flow**: It receives an HTTP client and an optional cursor. It computes a window from one year in the past to two years in the future, passes that window to _graph_delta_pages on the first request, and yields whatever pages that method returns. If a cursor already exists, the delta link carries the continuation state.

**Call relations**: OutlookConnector.paginate calls this for the events stream. This method is a thin stream-specific wrapper around _graph_delta_pages, adding only the calendar date window that Microsoft Graph requires.

*Call graph*: calls 1 internal fn (_graph_delta_pages); called by 1 (paginate); 1 external calls (now).


##### `OutlookConnector._list_mail_folders`  (lines 332–339)

```
async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This asks Outlook for the user’s mail folders and returns their IDs. Message syncing needs these IDs because each folder is synced separately.

**Data flow**: It receives an HTTP client, reads paged results from /me/mailFolders, collects every non-empty folder id string, and returns the list of IDs. It ignores malformed folder entries.

**Call relations**: The message delta flow calls this before syncing messages. The resulting folder IDs decide which per-folder delta feeds _message_delta_pages will walk.

*Call graph*: called by 1 (_message_delta_pages).


##### `OutlookConnector._list_contact_folders`  (lines 341–348)

```
async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This asks Outlook for the user’s contact folders and returns their IDs. Contact syncing uses the result to include contacts stored outside the default contact area.

**Data flow**: It receives an HTTP client, reads paged results from /me/contactFolders, collects valid folder id strings, and returns them as a list. Bad or missing IDs are skipped.

**Call relations**: The contact delta flow calls this after adding the built-in default contact area. The IDs it returns become the folder-specific contact delta feeds read by _contact_delta_pages.

*Call graph*: called by 1 (_contact_delta_pages).


##### `OutlookConnector.flatten`  (lines 350–380)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes raw Microsoft Graph records into friendlier fields used by the rest of the system. It keeps the original record but adds common names like email, phone, snippet, start_at, and location where appropriate.

**Data flow**: It receives one raw record and its stream description. For contacts, it adds name, first and last name, first email, phone, and created_at. For messages, it adds subject, snippet, sender address, sent time, conversation ID, and thread ID. For events, it adds title, plain-text description, start and end times, and location. For other streams, it returns the record unchanged.

**Call relations**: The sync framework calls this after records are fetched and before they are stored or indexed. It relies on _first_email, _phone, _strip_html, and get_path to safely extract useful values from Microsoft’s nested response objects.

*Call graph*: calls 3 internal fn (_first_email, _phone, _strip_html); 1 external calls (get_path).


##### `_decode_cursor_map`  (lines 383–392)

```
def _decode_cursor_map(raw: str | None) -> dict[str, str]
```

**Purpose**: This turns the saved JSON cursor for folder-based streams back into a normal dictionary. It is deliberately forgiving so a missing or bad cursor does not break the sync.

**Data flow**: It receives a raw cursor string or null. If there is no string, invalid JSON, or a JSON value that is not a dictionary, it returns an empty dictionary. Otherwise it returns a dictionary of folder IDs to non-empty cursor strings.

**Call relations**: Message and contact delta flows call this at the start of their work. It gives them the per-folder bookmarks they need before they ask Microsoft Graph what changed.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (loads).


##### `_encode_cursor_map`  (lines 395–396)

```
def _encode_cursor_map(value: dict[str, str]) -> str | None
```

**Purpose**: This turns a folder-to-cursor dictionary into the JSON string that can be saved as the stream cursor. It returns nothing if there are no cursors to save.

**Data flow**: It receives a dictionary whose keys are folder IDs and whose values are delta cursor links. If the dictionary has entries, it converts it to sorted JSON text and returns that. If it is empty, it returns null.

**Call relations**: Message and contact delta flows call this after receiving new folder bookmarks from Microsoft Graph. The encoded result becomes the next cursor handed back in each StreamPage.

*Call graph*: called by 2 (_contact_delta_pages, _message_delta_pages); 1 external calls (dumps).


### `extensions/sources/ufo_ext_sources/slack.py`

`io_transport` · `during Slack sync runs`

Slack does not hand over a workspace in one simple download. It returns results in pages, uses special cursor tokens to ask for the next page, and sometimes reports errors inside a normal-looking successful HTTP response. This file hides those Slack-specific details behind a connector.

The connector first knows which Slack streams exist: users, conversations, threads, messages, and message participants. For users and conversations, it reads the full current list each run. That matters because Slack does not always send a clear “this was deleted” signal, so the system treats missing users or channels as removed from view.

Messages are more delicate. Slack history is read one channel at a time, newest first. The file uses a partition walk, which is like keeping a separate bookmark for every channel, so a busy channel cannot cause a quiet one to be skipped. From each raw Slack message page it creates the records needed by different streams: message rows, thread summaries, and participant rows.

The file also decides when an error should stop the sync and when it should merely skip something. If the whole workspace cannot be listed because a permission is missing, the stream is skipped. If just one channel refuses history access, that channel is skipped while the others continue.

#### Function details

##### `SlackApiError.__init__`  (lines 93–97)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: Creates a Slack-specific error object when Slack says a request failed even though the HTTP request itself may have looked successful. It keeps the Slack error code, and sometimes the missing permission scope, so later code can decide whether to skip or fail.

**Data flow**: It receives Slack’s error name and an optional needed permission → builds a readable error message and stores both pieces of information on the exception → the exception can be caught later with the original Slack reason still attached.

**Call relations**: It is created by _ok_or_raise when a Slack response contains ok=false. The connector then uses this stored error code to choose between skipping a stream, skipping one channel, or raising a real failure.

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate_source`  (lines 105–120)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Acts as the standard entry point the wider source-sync system uses to ask this connector for pages of Slack records. It simply passes the request into the connector’s main pagination logic.

**Data flow**: It receives an HTTP client, a stream description, cursor information, the connector’s own Slack user id, and an optional backfill cutoff → forwards those values unchanged → returns the pages produced by paginate.

**Call relations**: The source framework calls this method when it wants Slack data. This method hands the work to SlackConnector.paginate so the rest of the file can choose the right Slack API path.

*Call graph*: calls 1 internal fn (paginate).


##### `SlackConnector.paginate`  (lines 122–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None=None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]
```

**Purpose**: Chooses how to read each Slack stream. It knows that users and conversations are simple lists, while message-related streams require reading channel history one channel at a time.

**Data flow**: It receives the requested stream and sync state → for users or conversations, yields pages from the matching list reader; for message streams, builds a user lookup, collects readable non-archived channels, and starts a per-channel walk → yields record pages or raises a skip for unsupported streams.

**Call relations**: It is called by paginate_source. It calls iter_users, iter_conversations, user_index, _slack_ts, and sets up PartitionWalk so channel history can be read safely with separate bookmarks per channel.

*Call graph*: calls 5 internal fn (__init__, iter_conversations, iter_users, user_index, _slack_ts); called by 1 (paginate_source); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 148–150)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: Provides the list of channel ids that should be walked for message history. It is a small helper used by the per-channel walking machinery.

**Data flow**: It reads the already-collected channel dictionary → yields each channel id one by one → gives PartitionWalk the set of channel partitions to visit.

**Call relations**: It is created inside SlackConnector.paginate when reading message-related streams. PartitionWalk uses it to know which Slack channels need history pages.


##### `SlackConnector.paginate.channel_pages`  (lines 152–160)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects the general partition walker to Slack’s channel-history reader. For one channel and one time bound, it asks _channel_pages to fetch the actual pages.

**Data flow**: It receives a channel id and a time/window bound → looks up the saved channel details and passes them, along with users and stream settings, to _channel_pages → returns the async page iterator for that channel.

**Call relations**: It is defined inside SlackConnector.paginate and handed to PartitionWalk. PartitionWalk calls it whenever it needs the next slice of history for a specific channel.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 177–193)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack users in pages and reshapes them into the project’s user record format. It follows Slack’s cursor until there are no more users.

**Data flow**: It starts with no cursor → asks Slack users.list for a page, filters out malformed entries, flattens each valid user, yields a page if any users remain, then reads the next cursor → finishes when Slack has no next cursor.

**Call relations**: SlackConnector.paginate uses it for the users stream, and user_index uses it to build a lookup table for message authors. It relies on _enumerate for safe Slack requests, _flatten_user for shaping records, and _next_cursor for page movement.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 195–235)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all visible Slack conversations, including public channels, private channels, group messages, and direct messages. It turns Slack’s channel objects into simpler conversation records.

**Data flow**: It starts without a cursor → asks Slack conversations.list for a page, extracts key fields like id, name, type, privacy, archive status, topic, and purpose, then yields the cleaned page → repeats with Slack’s next cursor until done.

**Call relations**: SlackConnector.paginate uses it both for the conversations stream and to discover which channels should be walked for messages. It calls _enumerate for the API request and helper functions to classify channels, read nested fields, convert dates, and find the next page.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 237–244)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a quick lookup table from Slack user id to user record. Message processing uses this to turn bare user ids into useful author details such as email or display name.

**Data flow**: It reads every page from iter_users → stores each valid user under its id → returns a dictionary of users keyed by Slack user id.

**Call relations**: SlackConnector.paginate calls this before reading message streams. The resulting lookup is passed down into channel and message processing so message records and participant records can include human-friendly user information.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 246–298)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]], self_user_id: str | None) -> AsyncI
```

**Purpose**: Reads Slack message history for one channel within the time window requested by the partition walker. If that one channel cannot be read, it skips only that channel instead of stopping the whole sync.

**Data flow**: It receives a channel, stream, time bound, user lookup, and self-user id → builds Slack conversations.history requests with oldest/latest limits and cursor paging → turns each raw history page into a WalkPage through _message_page → yields pages until Slack has no next cursor.

**Call relations**: The channel_pages helper inside paginate calls this for each channel chosen by PartitionWalk. It calls _slack_post to talk to Slack, _message_page to shape records, _next_cursor to continue paging, and raises PartitionSkipped when a channel-level permission or availability problem is safe to ignore.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 300–348)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]], self_user_id: str | None) -> WalkPage
```

**Purpose**: Turns one raw Slack history page into the specific stream records being requested: messages, thread summaries, or message participants. It also notes deleted message ids when Slack reports a deletion event.

**Data flow**: It receives raw Slack messages plus channel and user context → ignores deletion marker messages except for recording their deleted ids, drops messages from the connector’s own live bot user, creates message rows, possible thread rows, and participant rows → returns a WalkPage containing the records for the requested stream plus the high and low Slack timestamps on that page.

**Call relations**: _channel_pages calls this after each conversations.history response. It delegates record shaping to _flatten_message, _conversation_thread_from_message, and _participant_for_message, then wraps the result for PartitionWalk using WalkPage.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 350–370)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Performs top-level Slack list requests, such as users or conversations, with special handling for missing permissions. If Slack refuses an entire list because the token lacks access, the stream is marked skipped rather than failed.

**Data flow**: It receives an HTTP client, API path, and query parameters → calls _slack_get → if Slack or HTTP status shows a permission refusal, raises StreamSkipped; otherwise returns the decoded Slack data.

**Call relations**: iter_users and iter_conversations call this for their list APIs. It sits between those readers and _slack_get so Slack permission problems are translated into the sync system’s skip behavior.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 372–375)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Runs a Slack GET request and checks Slack’s own ok flag. This protects callers from treating ok=false responses as valid data.

**Data flow**: It receives an HTTP client, API path, and optional query parameters → uses the base REST connector’s GET method to fetch data → passes the decoded response through _ok_or_raise → returns valid Slack data or raises a SlackApiError.

**Call relations**: _enumerate calls this for Slack list endpoints. It delegates the Slack-specific success check to _ok_or_raise.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 377–380)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Runs a Slack POST request and checks Slack’s own ok flag. It is used for Slack APIs, like conversation history, that this connector reads with POST.

**Data flow**: It receives an HTTP client, API path, and optional JSON body → uses the base REST connector’s POST method → validates Slack’s response with _ok_or_raise → returns valid Slack data or raises a SlackApiError.

**Call relations**: _channel_pages calls this while walking channel history. It hands Slack’s response to _ok_or_raise so channel refusals can later be recognized and skipped when appropriate.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 383–388)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Checks whether Slack’s response really succeeded. Slack often puts failures in the response body using ok=false, so this function turns those hidden failures into normal Python errors.

**Data flow**: It receives decoded Slack response data → if ok is false, extracts the error code and optional needed scope and raises SlackApiError; otherwise returns the original data unchanged.

**Call relations**: _slack_get and _slack_post call this after every Slack request. When it raises, SlackApiError.__init__ packages the error code for higher-level skip-or-fail decisions.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 391–396)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: Finds Slack’s next-page token in a response. This lets the connector keep asking for more results until Slack says the list is finished.

**Data flow**: It receives a Slack response dictionary → looks inside response_metadata.next_cursor → returns the non-empty cursor string, or None if there is no next page.

**Call relations**: iter_users, iter_conversations, and _channel_pages call this at the end of each page. Its return value decides whether those loops continue or stop.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 399–406)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: Converts Slack’s whole-second Unix timestamps into ISO date strings, which are easier for the rest of the system to store and compare. It rejects booleans and invalid values instead of guessing.

**Data flow**: It receives any value → tries to interpret it as seconds since 1970 in UTC → returns an ISO-formatted timestamp string, or None if the input is not a usable time.

**Call relations**: iter_conversations uses it for channel creation times, and _flatten_user uses it for user update times. It relies on datetime.fromtimestamp to do the actual conversion.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts`  (lines 409–421)

```
def _slack_ts(value: datetime | None) -> str | None
```

**Purpose**: Converts a normal datetime into Slack’s message timestamp string format for history walking. The padding is important because the walker compares Slack timestamps as text, so uneven widths could make old dates sort incorrectly.

**Data flow**: It receives a datetime or None → if there is no date or the date is before the Unix epoch, returns None; otherwise formats the timestamp with fixed width and six decimal places → returns a Slack-style timestamp string.

**Call relations**: SlackConnector.paginate calls this when setting the backfill floor for message streams. That floor is then passed into PartitionWalk so history reads stop at the intended old boundary.

*Call graph*: called by 1 (paginate); 1 external calls (timestamp).


##### `_slack_ts_to_iso`  (lines 424–430)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: Converts Slack’s message timestamp strings into ISO date strings. This makes message and thread times usable outside Slack’s own format.

**Data flow**: It receives a Slack timestamp string or None → returns None for blank or invalid values; otherwise parses the number as seconds since 1970 UTC → returns an ISO timestamp.

**Call relations**: _flatten_message uses it for message sent times, and _conversation_thread_from_message uses it for thread last-message and update times.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 433–460)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns Slack’s nested user object into a simpler user record for storage. It pulls useful profile details such as display name, real name, email, phone, title, bot status, deletion status, and update time.

**Data flow**: It receives one raw Slack user dictionary → safely reads the nested profile, normalizes email casing, chooses the best available display and real names, converts update time → returns a flat dictionary keyed by the project’s expected field names.

**Call relations**: iter_users calls this for every valid Slack member. It uses _first_text to choose the first meaningful name and _unix_to_iso to convert Slack’s update timestamp.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 463–504)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]], self_user_id: str | None) -> dict[str, Any] | None
```

**Purpose**: Turns one raw Slack message into a normal message record. It also filters out messages sent by this connector’s own Slack bot user so the system does not index its own live surface messages.

**Data flow**: It receives a raw message, channel information, user lookup, and optional self-user id → validates the message timestamp and channel id, skips the self user, finds author details, chooses thread id, builds a snippet and timestamps → returns a flat message dictionary or None if the message should not be used.

**Call relations**: _message_page calls this for every raw history item that is not only a deletion marker. It uses _slack_ts_to_iso for sent_at, _snippet for a short preview, and _first_text to choose the best author handle.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 507–537)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Creates a thread summary record when a message is either the root of a Slack thread or a reply inside one. Plain standalone messages do not become thread records.

**Data flow**: It receives a flattened message plus the original raw Slack message and channel details → checks whether the message belongs to a real thread → derives title, counts, privacy/archive flags, parent channel, and last-message times → returns a thread dictionary or None.

**Call relations**: _message_page calls this after flattening each message. It uses _slack_ts_to_iso to convert Slack thread timestamps into normal date strings.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 540–560)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: Creates a participant record for the sender of a message. This gives the system a separate searchable link between a message and the person or handle who sent it.

**Data flow**: It receives a message record and the user lookup → finds the sender’s email or user id as a handle → if no handle exists, returns None; otherwise returns a participant dictionary with role from, message id, channel id, thread id, email, Slack user id, display name, and created time.

**Call relations**: _message_page calls this for each flattened message when building the message_participants stream. It uses _first_text to choose the best available sender handle.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 563–570)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: Classifies a Slack conversation into a simple type name such as direct message, multi-person direct message, private channel, or public channel. This gives downstream records a clear category.

**Data flow**: It receives a raw Slack conversation dictionary → checks Slack’s boolean flags in priority order → returns one type string.

**Call relations**: iter_conversations calls this while shaping channel records. The returned type is stored on conversation records and later copied into message records.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 573–579)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: Safely reads a value from inside nested dictionaries, such as a channel topic’s value. It avoids crashes when Slack omits part of the structure.

**Data flow**: It receives a starting dictionary and a path of keys → walks through each key only while the current value is still a dictionary → returns the final value, or None if the path cannot be followed.

**Call relations**: iter_conversations calls this to read channel topic and purpose text from Slack’s nested fields.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 582–586)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: Chooses the first usable non-empty string from several possible values. It is used when Slack may provide the same idea, like a name or handle, in several fields.

**Data flow**: It receives any number of candidate values → skips non-strings and blank strings → returns the first trimmed string that has content, or None if none do.

**Call relations**: _flatten_user uses it for names, _flatten_message uses it for sender handles, and _participant_for_message uses it for participant handles.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 589–593)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: Creates a short, clean preview of message text. It collapses messy whitespace and limits the preview length so records stay compact.

**Data flow**: It receives message text or None → returns None for empty input; otherwise collapses all whitespace to single spaces and cuts the result to the configured snippet length → returns the preview string.

**Call relations**: _flatten_message calls this when building each message record, so messages and derived thread titles can have readable short text.

*Call graph*: called by 1 (_flatten_message).


### `extensions/sources/ufo_ext_sources/wrike.py`

`io_transport` · `source sync`

Wrike is a project-management service, and this file is the read-only bridge from Wrike into UFO. Its job is to know which Wrike collections can be synced, how to page through Wrike’s API results, and how to reshape some Wrike records into friendlier fields like name, email, due date, and author.

Wrike sends list responses inside a wrapper that contains a data list and, sometimes, a next page token. The connector follows those tokens until there are no more pages, like turning pages in a book until the bookmark disappears. Wrike does not provide a dependable server-side “only give me changes after this time” filter, so this connector fetches pages and then locally drops records whose updatedDate is not newer than the saved cursor.

The file also defines which streams are available and which ones are actually runnable. If Wrike refuses access with an authorization error, the connector does not crash the whole sync; it raises StreamSkipped so that this stream can be skipped with a clear explanation. The connector never stores or writes credentials itself. It relies on the surrounding runner and authentication proxy to provide an authorized HTTP client.

#### Function details

##### `_profile_email`  (lines 54–64)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper looks inside a Wrike contact record and finds the first usable email address from its profiles list. It exists because Wrike stores contact email addresses nested inside profile objects rather than as one simple top-level field.

**Data flow**: It receives one Wrike record as a dictionary. It checks whether the record has a profiles value that is actually a list, then walks through each profile that is a dictionary and looks for a non-empty string email. It returns that email if it finds one, or returns nothing if the structure is missing, malformed, or has no email.

**Call relations**: When WrikeConnector.flatten is preparing a contact for the rest of the system, it calls this helper to pull out the contact’s email. The helper does only this small extraction job and hands the result back to flatten, which adds it to the normalized contact record.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 72–96)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Wrike stream page by page and yields batches of records to the sync engine. It also protects the wider sync from unsupported streams or missing permissions by turning those cases into a clear skip signal.

**Data flow**: It receives an authorized HTTP client, a stream description, and an optional cursor value that marks the last synced update time. It first checks that the requested stream is one this connector knows how to run. Then it asks the shared REST paging helper to fetch Wrike pages from the right API endpoint, using Wrike’s nextPageToken to move forward. If a cursor is present and the stream has an updatedDate-style cursor field, it filters out records that are not newer than that cursor. It yields only non-empty record batches. If Wrike returns a 401 or 403 authorization refusal, it raises StreamSkipped with an explanation; other HTTP errors are allowed to continue upward as real failures.

**Call relations**: The sync runner calls this method when it wants records for a Wrike stream. paginate delegates the low-level page fetching to the base REST connector’s cursor-page helper, then gives cleaned batches back to the runner. If the stream is unsupported or Wrike refuses access, it creates a StreamSkipped exception so the caller can move on without treating that stream as successfully synced.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 98–136)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method reshapes raw Wrike records into a more consistent form for indexing and recall. It adds common, easy-to-use fields such as name, email, status, due_date, created_at, body, author, and parent_external_id depending on the stream.

**Data flow**: It receives a raw Wrike record and the stream description that says what kind of object it is. For contacts, it builds a display name from first and last name when possible and extracts an email through _profile_email. For folders, it maps title to name and builds a Wrike API URL. For tasks, it reads nested date information safely through dict_or_empty and chooses a status and due date. For comments, it maps Wrike’s text and author/task identifiers into fields the rest of the system expects. For streams without special treatment, it returns the record unchanged.

**Call relations**: After records have been fetched by the connector, the broader source framework calls flatten to prepare each item for storage or indexing. flatten uses _profile_email for contact email extraction and dict_or_empty to safely read task date data even when Wrike omits or changes that nested field.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).
