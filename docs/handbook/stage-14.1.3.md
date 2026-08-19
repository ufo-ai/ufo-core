# Work management and productivity connectors  `stage-14.1.3`

This stage is shared behind-the-scenes support for bringing work-planning information into the system. It is like a set of adapters for different office tools: each adapter knows how that tool organizes its data, asks for it through the tool’s web API, and reshapes it into standard records the rest of the system can store, search, and recall.

The Airtable connector finds bases, tables, and records. The Asana connector reads workspaces, projects, tasks, stories, and users, including results that arrive in pages. Calendly brings in scheduling data such as event types, groups, scheduled events, and invitees. ClickUp walks through its layered structure, from teams down to lists and tasks. The monday.com connector collects users, teams, workspaces, boards, items, updates, logs, and tags. Notion reads pages, blocks, databases, comments, and users, turning workspace content into searchable text without writing anything back. Wrike imports contacts, folders, tasks, comments, workflows, and custom fields. Together, these connectors turn scattered work tools into a common memory source.

## Files in this stage

### Airtable table workspaces
Starts with Airtable's base, table, and record discovery for no-code tabular workspace data.

### `extensions/sources/ufo_ext_sources/airtable.py`

`io_transport` · `sync run`

Airtable data is not exposed as one simple list. It is more like a building directory: first you find the buildings, then the rooms inside each building, then the items in each room. This connector follows that shape. It first asks Airtable for all bases, then asks for the tables in each base, then reads the records in each table.

The file defines three streams: bases, tables, and records. A stream is a named kind of data the sync system can request. Bases are treated as the main, canonical stream, while tables and records are related details.

The connector uses Airtable's web API through an asynchronous HTTP client, meaning it can wait for network replies without blocking other work. For records, Airtable returns data in pages of up to 100 items and supplies an offset token when more pages exist. The connector follows those tokens until a table is fully read.

A key detail is context. Tables are tagged with the base they came from, and records are tagged with their base and table. Without this, a record's origin could be ambiguous later. The file only reads from Airtable; it does not create or update anything.

#### Function details

##### `AirtableConnector._bases`  (lines 39–41)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function asks Airtable for the list of bases the connected account can access. A base is Airtable's top-level container, similar to a workbook or database.

**Data flow**: It receives an HTTP client that already knows how to make web requests. It sends a request to Airtable's metadata endpoint for bases, pulls the list stored under the "bases" field from the response, and returns that list as plain dictionaries.

**Call relations**: The main pagination flow calls this whenever it needs to start from the top of Airtable's structure. It relies on the shared source helper records_at to extract the useful list from Airtable's response before handing the bases back to paginate.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 43–50)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This function gets all tables inside one Airtable base. It also labels each table with the base it came from, so later code does not lose that relationship.

**Data flow**: It receives an HTTP client and one base record. It reads the base's id; if the id is missing or invalid, it returns an empty list. Otherwise, it asks Airtable for that base's tables, extracts the table list, adds base_id and base_name to each table, and returns the enriched tables.

**Call relations**: The pagination flow calls this after it has discovered bases. It uses records_at to pull tables out of the API response and with_context to attach base information before tables are passed on to table syncing or record discovery.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 52–70)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads the records from a specific table in a specific base. It follows Airtable's page-by-page record API until all available records for that table have been yielded.

**Data flow**: It receives an HTTP client, a base id, and a table record. It checks that the table has a usable id. If not, it produces nothing. If the id is valid, it requests record pages from Airtable using a page size of 100 and Airtable's offset token for continuation. For each non-empty page, it adds base_id, table_id, and table_name to every record, then yields that page.

**Call relations**: The main pagination flow calls this while walking through every discovered base and table. The actual page fetching is delegated to the inherited cursor-page helper, while this function adds the Airtable-specific path and context needed for downstream readers.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 72–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector's main dispatcher for reading Airtable streams. Given a requested stream, it decides whether to return bases, tables, or records, and yields the data in batches.

**Data flow**: It receives an HTTP client, a stream description, and a cursor value. For the bases stream, it fetches all bases and yields them once. For the tables stream, it fetches bases, then tables for each base, collecting them into pages of about 100. For the records stream, it walks bases, then tables, then record pages. If the stream name is unknown, it raises a StreamSkipped signal to tell the sync system this stream is not available here.

**Call relations**: The broader sync engine calls paginate when it wants data for one Airtable stream. This function orchestrates the smaller helpers: _bases starts discovery, _tables_for_base expands each base into tables, and _records_for_table reads the table contents. It hands yielded pages back to the sync engine.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 103–125)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes Airtable API records into a cleaner form for the rest of the system. It keeps the original information but adds or normalizes a few fields that make records easier to identify and link back to Airtable.

**Data flow**: It receives one record and the stream it belongs to. For bases, it keeps the record and adds a metadata API URL. For tables, it adds the table API URL using the stored base id and table id. For records, it normalizes createdTime into created_at and ensures fields is a dictionary. It returns the adjusted record without making network calls.

**Call relations**: After paginate yields raw pages, the source framework can call flatten on each item before storing or indexing it. This function does not call other local helpers; it is the final cleanup step that makes Airtable data more consistent for downstream use.


### Asana work data
Adds Asana project, task, story, user, and workspace streams from paged work-management APIs.

### `extensions/sources/ufo_ext_sources/asana.py`

`io_transport` · `during source sync`

Asana is a work-tracking tool, and its API returns information in small pages rather than all at once. This file defines an Asana connector: a read-only bridge between Asana and the larger UFO source-sync system. Without it, the system would not know which Asana objects exist, which ones are important, or how to keep asking Asana for the next batch of results.

The file first describes each Asana “stream,” meaning a category of records to fetch, such as tasks, projects, stories, users, tags, teams, and workspaces. Each stream says what field uniquely identifies a record, and whether it can be synced incrementally. Incremental sync means “only ask for things changed since last time,” like checking only today’s new mail instead of rereading the whole mailbox. Asana supports that for tasks and projects through a modified-since filter; most other streams are fetched fresh each run.

The AsanaConnector class supplies the API base address and the list of streams. Its main behavior is pagination: it calls Asana with a page size, yields any records it receives, then follows Asana’s next-page offset until there are no more pages. The connector does not store or create Asana data, and it does not hold credentials itself; authentication is handled outside this file.

#### Function details

##### `_stream`  (lines 24–38)

```
def _stream(name: str, *, cursor_field: str | None=None, updated_at_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Asana record type, such as tasks or users. It keeps the stream list short and consistent so each stream is described in the same shape.

**Data flow**: It receives a stream name and optional details such as which field acts as the sync cursor, which field shows the last update time, and whether the stream is a main record type. It packages those choices into a StreamSpec object, always using Asana’s gid field as the unique record key, and returns that stream description for later use.

**Call relations**: This function is used while the file is being loaded to build the Asana stream catalog. It hands each completed stream description to the connector’s stream list, and it relies on StreamSpec to create the shared format expected by the broader source-sync system.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 74–90)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one Asana stream page by page. Someone would use it when they need all records for a stream without caring about Asana’s page tokens and response wrapping.

**Data flow**: It starts with an HTTP client, a stream description, and an optional saved cursor from a previous sync. It builds the Asana API path and request parameters, including a page size and, for tasks or projects, a modified-since value when a cursor is available. For each response, it pulls the record list out of the data field, safely treats missing or invalid lists as empty, yields non-empty batches, then reads Asana’s next-page offset. If there is another offset, it requests the next page; if not, it stops.

**Call relations**: During a sync, the broader RestConnector machinery calls this method for a particular Asana stream. This method does the Asana-specific page-following work, uses list_or_empty to normalize the returned record list, and yields batches back to the rest of the ingestion pipeline so they can be stored or processed.

*Call graph*: 1 external calls (list_or_empty).


### Calendly scheduling data
Covers Calendly scheduling entities, from users and event types through scheduled events and invitees.

### `extensions/sources/ufo_ext_sources/calendly.py`

`io_transport` · `source sync`

Calendly is organized around an account’s current organization, so this connector first asks Calendly who the logged-in API user is and which organization they belong to. It then reads each supported collection from Calendly using that organization as a filter. This matters because without the organization value, most of the useful Calendly lists cannot be fetched reliably.

The file defines the available streams, which are the separate kinds of Calendly data the system can sync. Some streams are simple, like the API user. Others are paged lists, meaning Calendly sends results in batches instead of all at once. The connector follows Calendly’s “next page token” like turning pages in a book until there are no more pages.

For repeated syncs, it uses cursors, which are saved positions such as “last updated time” or “last created time.” That lets the system avoid rereading everything every time. Invitees are a special case: Calendly exposes them underneath each scheduled event, so the connector first reads events, then asks for invitees for each event.

At the end, the connector flattens records into friendlier shapes. For example, it pulls a membership user’s name and email up to the top level, and gives scheduled events clearer fields like title, start time, end time, and location.

#### Function details

##### `_uuid_from_uri`  (lines 61–64)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: This helper pulls the final ID-like part out of a Calendly URI. It is used when the connector needs the event’s UUID to build another Calendly API URL.

**Data flow**: It receives a value that might be a URI. If the value is not a non-empty string, it returns nothing. If it is a string, it trims any trailing slash, takes the text after the last slash, and returns that as the UUID.

**Call relations**: When invitees are being synced, the connector reads scheduled events first. For each event, Calendly gives a full URI, and this helper extracts the part needed so the invitee endpoint can be called for that event.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 72–75)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: This asks Calendly for the authenticated API user. The connector needs this because the user record contains the current organization, which is required for most other Calendly reads.

**Data flow**: It receives an HTTP client that can make authorized requests. It calls Calendly’s `/users/me` endpoint, looks for the `resource` object in the response, and returns that object if it is a dictionary. If the response is not shaped as expected, it returns an empty dictionary.

**Call relations**: The organization-based stream reader calls this before reading organization data. The main pagination method also calls it directly when syncing the `api_user` stream.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 77–90)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Calendly list endpoint page by page. It hides the repeated work of asking for the next batch of records until Calendly says there are no more.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST pagination helper to read records from the response’s `collection` field, follow `pagination.next_page_token`, request up to 100 records at a time, and yield each page as a list of records.

**Call relations**: Organization streams use this after they have added the organization filter. Invitee syncing also uses it to read invitee lists under each scheduled event.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 92–108)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the common path for Calendly streams that must be limited to the account’s current organization. It also applies an optional cursor so incremental syncs can start from a saved point.

**Data flow**: It receives an HTTP client, a Calendly path, and optional cursor information. It first reads the current user, takes the `current_organization` value, and stops the stream if that value is missing. It then builds request parameters with the organization and optional cursor, reads pages from Calendly, adds the organization as context to each record, and yields the enriched pages.

**Call relations**: The main `paginate` method uses this for event types, groups, memberships, and scheduled events. Invitee syncing also uses it to discover scheduled events before fetching their invitees.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 110–128)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads invitees for scheduled Calendly events. Calendly does not expose invitees as one flat organization-wide list here, so the connector must visit each event and then fetch that event’s invitees.

**Data flow**: It receives an HTTP client and an optional saved cursor. It reads scheduled events through the organization stream, extracts each event UUID from its URI, calls the invitees endpoint for that event, and optionally filters out invitees whose `created_at` value is not newer than the cursor. For records that remain, it adds context showing which scheduled event they came from and yields them.

**Call relations**: The main `paginate` method calls this when the requested stream is `event_invitees`. Inside, it relies on the organization stream for events, the URI helper for event IDs, the collection paginator for invitee pages, and the context helper to preserve the parent event information.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 130–162)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main routing point for reading Calendly streams. Given a stream name, it chooses the correct Calendly API path and sync strategy.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. For the user stream, it returns the current user as a single-record page. For organization-based streams, it delegates to the organization stream reader with the right endpoint and cursor parameter. For invitees, it delegates to the special invitee reader. If the stream name is unknown, it marks the stream as skipped.

**Call relations**: The broader source-sync framework calls this to obtain pages of records for each configured Calendly stream. This method then hands work to `_current_user`, `_org_stream`, or `_invitees` depending on what kind of Calendly data is being requested.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 164–204)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes raw Calendly records into records that are easier for the rest of the system to store, display, and search. It keeps the original data but adds or lifts important human-friendly fields.

**Data flow**: It receives one Calendly record and the stream it belongs to. Depending on the stream, it copies the record and adds fields like name, email, title, description, start and end times, API URL, or a simplified location. For organization memberships, it safely reads the nested `user` object, lifts out the member’s name and email, and removes the nested user object so later user profile changes do not make the membership look changed.

**Call relations**: After `paginate` has produced raw Calendly records, the source framework can call this to normalize each record. It uses a small helper to safely treat a missing or invalid nested user value as an empty dictionary.

*Call graph*: 1 external calls (dict_or_empty).


### Hierarchical project suites
Walks nested project-management platforms that expose teams, boards, lists, tasks, updates, and related metadata.

### `extensions/sources/ufo_ext_sources/clickup.py`

`io_transport` · `during ClickUp source sync`

ClickUp organizes work like a set of nested boxes: teams contain spaces, spaces contain folders, folders contain lists, and lists contain tasks, comments, and custom fields. This connector is the map the sync system uses to open those boxes in the right order. Without it, the system would not know which ClickUp API addresses to call, how to find child objects, or how to attach useful parent information like “this task came from this list.”

The file defines the ClickUp streams the system can read, such as teams, users, spaces, folders, lists, tasks, comments, custom fields, and goals. A stream is one kind of data the sync can fetch. The connector then provides helper methods that climb down the ClickUp hierarchy. For example, it first gets teams, then uses each team id to get spaces, then uses each space id to get folders and lists.

For leaf data like tasks and list comments, it also supports cursor filtering. A cursor is a saved “last seen” value, like a bookmark, so the next sync can skip older records. The connector only reads from ClickUp; it does not create or update anything there. Finally, `flatten` reshapes some ClickUp records into more consistent fields, such as turning a task status object into a simple status value.

#### Function details

##### `ClickUpConnector._teams`  (lines 58–60)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the ClickUp teams visible to the authenticated account. This is the starting point for almost every other ClickUp read, because spaces, goals, and users are found through teams.

**Data flow**: It receives an HTTP client that can make API calls. It asks ClickUp for `/team`, pulls the `teams` list out of the response, and returns that list of team records.

**Call relations**: This is the top of the hierarchy. `_spaces` calls it before looking for spaces inside each team, and `paginate` calls it directly when the requested stream is teams, users, or goals.

*Call graph*: called by 2 (_spaces, paginate); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 62–70)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived spaces under all ClickUp teams. A space is a major work area inside a team, so later steps need these ids to keep walking downward.

**Data flow**: It starts with the team records from `_teams`. For each team with a usable id, it calls ClickUp for that team’s spaces, extracts the `spaces` list, adds the parent `team_id` to each space record, and returns one combined list.

**Call relations**: This builds on `_teams` and becomes the next stepping stone. `_folders`, `_lists`, and `paginate` call it when they need space records or need to continue deeper into the ClickUp structure.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 72–82)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived folders inside all spaces. Folders are one of the places where ClickUp lists can live, so they must be discovered before folder-based lists can be read.

**Data flow**: It gets spaces from `_spaces`. For each space with a valid id, it calls ClickUp for that space’s folders, extracts the `folders` list, adds the parent `space_id` to each folder, and returns the combined result.

**Call relations**: This method sits between spaces and lists. `_lists` calls it to find lists inside folders, and `paginate` calls it when the sync is specifically reading the folders stream.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 84–100)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Collects all non-archived ClickUp lists, whether they are inside folders or directly inside spaces. Lists matter because tasks, comments, and custom fields are read from each list.

**Data flow**: It first gets folders from `_folders`, then calls ClickUp for the lists in each folder and tags each list with its `folder_id`. It also gets spaces from `_spaces`, calls ClickUp for lists that live directly under each space, tags those with `space_id`, and returns all discovered lists together.

**Call relations**: This is the launch point for the most detailed reads. `_tasks` and `_list_child_stream` call it so they can visit every list, while `paginate` calls it directly when syncing the lists stream.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _tasks, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 102–127)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads tasks from every ClickUp list, including closed tasks and subtasks. It yields tasks in batches so the sync process can process data as it arrives instead of waiting for everything at once.

**Data flow**: It receives an HTTP client and an optional cursor, which is the last saved `date_updated` value from a previous sync. It gets all lists from `_lists`, calls ClickUp’s task endpoint page by page for each list, adds `list_id` and `list_name` to each task, filters out tasks that are not newer than the cursor, and yields each non-empty batch.

**Call relations**: This is used by `paginate` when the requested stream is tasks. It depends on `_lists` to know where to look, and it uses the shared record-extraction and context helpers to shape each API response into useful sync records.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 129–148)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads list-level child data, currently comments and custom fields, from every ClickUp list. It exists because these records are not fetched from one global ClickUp endpoint; they must be requested list by list.

**Data flow**: It receives an HTTP client, the stream definition, and an optional cursor. It gets all lists from `_lists`, chooses the right ClickUp endpoint for comments or custom fields, extracts the correct record list from the response, adds `list_id` and `list_name`, filters by the stream’s cursor field when possible, and yields each non-empty group.

**Call relations**: This is called by `paginate` for the `list_comments` and `list_custom_fields` streams. Like `_tasks`, it uses `_lists` as its directory of places to visit, then hands back batches to the main sync flow.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 150–204)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main dispatcher for reading any ClickUp stream. Given a stream name, it chooses the correct helper, fetches the data, and yields it in batches for the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. It checks the stream name, calls the matching helper, sometimes reshapes or combines records such as users from team members, and yields batches only when there is data. If the stream name is not supported, it raises `StreamSkipped` to tell the sync system this stream cannot be read here.

**Call relations**: The broader source framework calls this method when it wants ClickUp records. `paginate` then delegates to `_teams`, `_spaces`, `_folders`, `_lists`, `_tasks`, or `_list_child_stream` depending on the stream, and it also performs the special goal and user reads itself.

*Call graph*: calls 7 internal fn (__init__, _folders, _list_child_stream, _lists, _spaces, _tasks, _teams); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 206–240)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes ClickUp records into fields that are easier for the rest of the system to search, display, or compare. It keeps the original record data but adds or simplifies common fields like name, email, status, body, and created time.

**Data flow**: It receives one raw record and the stream it belongs to. Depending on the stream, it copies the record and adds cleaned-up fields: users get a consistent name and created date, spaces/folders/lists get an API URL, tasks get a simple status and created date, and comments get body, author, creation time, and parent list id. For streams without special rules, it returns the record unchanged.

**Call relations**: This is used after records have been fetched by the pagination flow, as the connector’s final cleanup step. It calls `dict_or_empty` for comment users so missing or malformed user data does not break the normalization.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/monday.py`

`io_transport` · `during monday.com source sync runs`

monday.com exposes its data through GraphQL, which is a query language where the client asks for exactly the fields it wants. This file is the monday.com “source connector”: it does not write anything back to monday, it only reads data so the rest of the system can store it as recallable pages.

The connector defines the streams it can sync, such as users, boards, and items. A stream is one kind of record the system can pull in batches. The main job of the file is to ask monday for those records, page through large result sets, skip streams cleanly when monday refuses a query, and shape raw monday records into a more consistent form.

There are two paging styles here. Some top-level monday collections use simple page numbers, like turning pages in a book. Board items use monday’s cursor system, where monday gives back a token saying “start the next request from here.” For records that can be synced incrementally, monday does not provide a server-side “only changed since this time” filter, so the connector fetches pages and filters out older records locally using stored timestamp cursors.

One important detail is permissions failure. If monday returns GraphQL errors, or the HTTP request is refused with 401 or 403, the connector raises `StreamSkipped`, meaning “record this stream as skipped” rather than saving half-good data.

#### Function details

##### `_extract_person_ids`  (lines 70–98)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: Pulls assigned person IDs out of monday item column data. monday stores people assignments inside board-specific columns, so this helper looks for columns whose type is `people` instead of relying on a fixed column name.

**Data flow**: It receives the raw `column_values` field from a monday item. It checks each column, reads the JSON-shaped assignment value when present, keeps only entries marked as people rather than teams, and returns a plain list of person ID strings. If the data is missing, malformed, or not a people column, it simply ignores it.

**Call relations**: When `MondayConnector._items` fetches item records, it calls this helper for each item so the item gains an easy-to-use `assignee_ids` field. The helper uses JSON parsing because monday stores the assignment details as a JSON blob inside the column value.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 106–118)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Sends one GraphQL request to monday and returns the useful `data` part of the response. It also turns monday GraphQL errors into a clean stream skip so the sync does not treat an unavailable query as valid data.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional variables. It posts them to monday’s API endpoint, looks for an `errors` section, and raises `StreamSkipped` if monday says the query failed. Otherwise it returns the response’s `data` object, or an empty dictionary if the response is not shaped as expected.

**Call relations**: This is the central doorway to monday’s API. The paging helpers and the main `paginate` method call it whenever they need records. By centralizing error handling here, callers do not each have to understand monday’s GraphQL error format.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, paginate).


##### `MondayConnector._paged_root`  (lines 120–143)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a top-level monday collection that uses page numbers, such as users, workspaces, boards, or updates. It yields records in batches so the sync can process large collections without needing everything at once.

**Data flow**: It receives the monday field to query, the fields to request for each record, and optional cursor information for incremental sync. It requests page 1, then page 2, and so on, converting missing or non-list responses into empty lists. If a cursor is provided, it keeps only records newer than that cursor, then yields each non-empty batch until no records remain.

**Call relations**: This helper is used by `MondayConnector._boards` and by `MondayConnector.paginate` for streams with simple page-number pagination. It calls `MondayConnector._graphql` for each page and uses a safe list conversion helper so odd response shapes do not crash the whole sync.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, paginate); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 145–156)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches all monday boards with the board details needed by other streams. Boards are important because some monday data, like items and activity logs, must be requested board by board.

**Data flow**: It starts with an empty list, asks `_paged_root` for all pages of boards, and appends each page’s records into one complete list. The result is a list of board dictionaries, including details such as name, state, timestamps, URL, and workspace.

**Call relations**: `MondayConnector._items` and `MondayConnector._activity_logs` call this first because they need board IDs before they can ask monday for per-board data. It relies on `_paged_root` to do the actual page-by-page API requests.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 158–217)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches item records from every board. Items require special paging in monday, so this function walks each board and follows monday’s item-page cursor until all items for that board have been read.

**Data flow**: It receives an HTTP client and an optional timestamp cursor. It first gets all boards, then for each board asks for the first page of items. If monday returns a next-page cursor, it keeps requesting more item pages using that cursor. For each item, it adds `assignee_ids` by reading people columns, filters out items older than the saved cursor when needed, and yields each non-empty batch.

**Call relations**: `MondayConnector.paginate` calls this when the active stream is `items`. This function depends on `_boards` to discover where to look, `_graphql` to talk to monday, `_extract_person_ids` to simplify assignee data, and safe dictionary/list helpers to tolerate missing response pieces.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (paginate); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 219–247)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches activity log entries for every board. Activity logs are board-specific, so the connector must fan out across boards and attach the board ID to each log entry.

**Data flow**: It receives an HTTP client and an optional timestamp cursor. It gets all boards, queries monday for each board’s recent activity logs, adds the related `board_id` to every log entry, filters out entries older than the cursor when present, and yields the remaining records in batches.

**Call relations**: `MondayConnector.paginate` calls this for the `activity_logs` stream. Like item syncing, it first uses `_boards` to find board IDs, then calls `_graphql` for each board’s logs and uses safe list conversion to avoid failing on missing or oddly shaped responses.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (paginate); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 249–323)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right monday query strategy for each stream and yields pages of records to the sync engine. This is the connector’s main read dispatcher: the rest of the system asks for a stream, and this method knows how to fetch it.

**Data flow**: It receives an HTTP client, a stream description, and an optional stored cursor. It checks the stream name and either calls a helper like `_paged_root`, `_items`, or `_activity_logs`, or runs a one-off GraphQL query for simpler streams such as teams and tags. It yields batches of dictionaries. If monday refuses access with HTTP 401 or 403, it turns that into `StreamSkipped` with a helpful explanation; unknown stream names are skipped too.

**Call relations**: The connector framework calls this during a sync when it needs records for a particular monday stream. Inside, it delegates to the lower-level helpers that know each monday paging style. It is also where transport permission failures are translated into the sync system’s “skip this stream” signal.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _graphql, _items, _paged_root).


##### `MondayConnector.flatten`  (lines 325–366)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes raw monday records into fields the rest of the system can understand consistently. It keeps the original data but adds or renames common fields such as name, body, author, status, created time, and parent record ID.

**Data flow**: It receives one raw record and the stream it came from. Based on the stream name, it copies the record and fills in standard fields: for example, updates get a readable body and author, activity logs get a subject and parent board ID, and boards get an API URL. If the stream has no special rules, it returns the record unchanged.

**Call relations**: After `paginate` has fetched raw monday records, the connector framework can call `flatten` before storing or indexing them. It uses a safe dictionary helper when reading nested creator information so missing creator data does not break update records.

*Call graph*: 1 external calls (dict_or_empty).


### Notion workspace knowledge
Transforms Notion pages, blocks, databases, comments, and users into searchable read-only workspace memory.

### `extensions/sources/ufo_ext_sources/notion.py`

`io_transport` · `sync run and record rendering`

Notion stores information in a nested, flexible way: a page has properties, a body is made of blocks, blocks can contain other blocks, and visible words are often hidden inside “rich text” arrays. A plain JSON copy would be hard for a person, or a recall system, to understand. This connector solves that by fetching Notion records and rendering the parts a human would actually read.

The file defines the Notion streams the system can sync: users, pages, data sources, comments, and blocks. It connects to Notion’s REST API, which means it talks to Notion through ordinary web requests. It adds the Notion API version header every request needs, follows Notion’s page-by-page result format, and uses saved time markers so repeat syncs can skip older records.

Pages and data sources come from Notion search. Blocks are found by walking each page’s block tree, like opening folders inside folders, with a depth limit to avoid runaway nesting. Comments are fetched per page, and users come from the users endpoint. If Notion refuses access because the integration lacks permission, the stream is marked as skipped instead of crashing the whole run.

#### Function details

##### `NotionConnector._make_client`  (lines 80–83)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the web client used to talk to Notion. It makes sure every request includes the Notion API version, which Notion requires to interpret requests correctly.

**Data flow**: It receives a base URL and a credential object, asks the parent connector to build the basic HTTP client, then adds the Notion-Version header. The result is a ready-to-use client whose future requests carry the required version information.

**Call relations**: This is part of the connector setup inherited from the shared REST connector. Other fetching methods later use the client it prepares, so they do not each need to remember the Notion version header.


##### `NotionConnector.paginate`  (lines 85–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading each Notion stream. Given a stream name, it chooses the right Notion-reading method and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor value such as a timestamp. It routes users, pages, data sources, comments, or blocks to the matching helper, passes along the cursor where useful, and yields lists of Notion records. If Notion returns an access-denied response, it converts that into a skipped stream rather than a failed run.

**Call relations**: The wider sync system calls this when it wants records for a stream. It hands work to _collection, _search, _comments, or _blocks depending on the stream, and uses StreamSkipped to tell the runner that missing Notion permissions should be recorded as a skip.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 117–140)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads pages or data sources through Notion’s search endpoint. It asks Notion for results sorted by edit time so incremental syncs can keep only newer records.

**Data flow**: It receives the client, an object type such as page or data_source, and an optional cursor timestamp. It repeatedly posts search requests, turns the results field into a list, filters out records whose last edited time is not newer than the cursor, yields non-empty batches, and follows Notion’s next cursor until there are no more pages.

**Call relations**: paginate uses this directly for pages and data sources. _blocks and _comments also use it first to find all pages, because blocks and comments are discovered by starting from pages.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 142–152)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers the block bodies that make up Notion pages. It starts with pages, then walks into each page’s block tree so the page’s visible content can be synced.

**Data flow**: It receives the client and an optional cursor timestamp. It searches for all pages without filtering by the page cursor, reads each page id, and for each valid id asks _block_children to fetch that page’s blocks and nested blocks. It yields block batches as they are found.

**Call relations**: paginate calls this when the sync asks for the blocks stream. It depends on _search to find pages first, then delegates the recursive walking to _block_children.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 154–173)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through the children of one Notion block or page. It is what lets the connector capture nested content such as lists, toggles, and sub-blocks.

**Data flow**: It receives a block id, the current nesting depth, and an optional cursor timestamp. If the nesting is too deep, it stops. Otherwise it fetches the block’s children, yields only children edited after the cursor when a cursor exists, then repeats the process for child blocks that have children and are safe to descend into.

**Call relations**: _blocks calls this for each page id. It uses _collection to fetch each page of child blocks, and it calls itself recursively to move down the block tree until there are no more allowed children or the depth limit is reached.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 175–191)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches comments attached to Notion pages. It makes comments searchable alongside the documents they discuss.

**Data flow**: It receives the client and an optional cursor timestamp. It searches for pages, takes each valid page id, requests comments for that page, optionally filters out comments not newer than the cursor, and yields any remaining comment batches.

**Call relations**: paginate calls this for the comments stream. It uses _search to find pages and _collection to handle Notion’s paged comments responses.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 193–207)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared helper for Notion endpoints that return a paged list of results. It hides the repeated work of following Notion’s next_cursor field.

**Data flow**: It receives a client, an API path, and optional query parameters. It asks the shared REST paging helper to repeatedly GET that path, read records from the results field, send start_cursor for the next page, and request up to the configured page size. It yields each list of records as it arrives.

**Call relations**: paginate uses this for users, while _block_children uses it for block children and _comments uses it for comments. It is the common paging conveyor belt for Notion GET collection endpoints.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 209–231)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns raw Notion records into readable text. Without it, synced Notion data would mostly look like nested API objects instead of page titles, block text, comments, names, and emails.

**Data flow**: It receives one record and the stream it came from. Based on the stream, it extracts a title and body using small text helpers, falls back to a sensible title when needed, then returns both the title and a Markdown-like text block headed with the Notion stream name.

**Call relations**: The sync or indexing layer calls this after records are fetched. It relies on _page_title, _properties_text, _block_text, _rich_text_text, _str, and _user_text to translate Notion’s different record shapes into readable prose.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 234–235)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns a value into text only when it is already a string. It prevents accidental output like Python object descriptions or None where user-facing text is expected.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: render and several text-extraction helpers call this whenever a Notion field might or might not contain a real string. It acts like a small safety filter before text is included in rendered output.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 238–246)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: This extracts the visible words from Notion rich text. Notion stores formatted text as a list of pieces, and this joins their plain_text values into one readable string.

**Data flow**: It receives any value, expects a list of rich-text parts, ignores anything that is not a proper text part, joins the plain_text strings, trims surrounding whitespace, and returns the result. If the input is not a list, it returns an empty string.

**Call relations**: render uses this directly for comments and data sources. _page_title, _property_text, and _block_text also call it whenever they need to turn Notion’s rich-text structure into normal text.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 249–258)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: This finds the human title of a Notion page. Page titles are stored as a special property, not as a simple top-level field.

**Data flow**: It receives a page record, looks inside its properties dictionary, searches for the property whose type is title, extracts its rich text, and returns the first non-empty title it finds. If the record does not have a usable title property, it returns an empty string.

**Call relations**: render calls this when preparing a page record. It delegates the actual rich-text joining to _rich_text_text.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 261–270)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion page’s properties into readable lines like “Status: In progress”. It captures useful page metadata beyond the title.

**Data flow**: It receives a page record, reads its properties dictionary, converts each supported property to text with _property_text, and joins non-empty results as one line per property. If there are no usable properties, it returns an empty string.

**Call relations**: render calls this for page bodies. It relies on _property_text to understand the different property types Notion can store.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 273–292)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: This converts one Notion property value into plain text. It knows the common property shapes, such as titles, selections, people, dates, numbers, links, emails, phone numbers, and checkboxes.

**Data flow**: It receives one property dictionary, reads its declared type, then looks up the matching value field. Depending on the type, it extracts rich text, a name, a comma-separated list of names, a date start, or a simple string version of the value. Unsupported or malformed properties become an empty string.

**Call relations**: _properties_text calls this for each page property. It uses _rich_text_text for rich-text-like properties and _str for fields that should only be included when they are real strings.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 295–305)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: This extracts the readable text from a Notion block. Blocks are the building pieces of a page, such as paragraphs, headings, child pages, databases, and to-do items.

**Data flow**: It receives a block record, finds the block’s type-specific content, and returns the visible text. For child pages or child databases it returns the title. For ordinary rich-text blocks it joins the rich text. For to-do blocks it prefixes the text with a checked or unchecked box marker.

**Call relations**: render calls this for block records. It uses _rich_text_text for block body text and _str for child page or child database titles.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 308–311)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion user record into readable identity text. It includes the user’s name and email when those fields are available.

**Data flow**: It receives a user record, reads the top-level name and the email nested under the person field, keeps only real strings, and joins the available parts with a newline. The result is a small text summary of the user.

**Call relations**: render calls this for the users stream. It uses _str to avoid including missing or non-string values in the final rendered text.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


### Wrike work management
Finishes with Wrike contacts, folders, tasks, comments, workflows, and custom fields as standard recallable records.

### `extensions/sources/ufo_ext_sources/wrike.py`

`io_transport` · `source sync`

Wrike’s API returns information in pages, like a long report split across many screens. This connector knows which Wrike objects are worth reading, how to ask for each page, and how to follow Wrike’s “next page” marker until there is no more data. Because Wrike does not provide a dependable “only give me things changed since last time” option, the connector does that filtering itself: after each page comes back, it drops records whose update date is not newer than the saved cursor, which is the last-seen timestamp used for incremental syncing.

The file also protects the wider sync run from common permission problems. If Wrike says the credential is invalid or lacks access, the connector raises `StreamSkipped`, meaning “do not crash the whole run; skip this stream with an explanation.” The connector does not store or create credentials itself; it relies on the surrounding runner and authentication proxy to provide an authorized HTTP client.

Finally, the `flatten` method reshapes Wrike-specific records into friendlier fields. For example, it turns contact first and last names into one `name`, extracts an email from nested profiles, maps task dates into `due_date`, and links comments back to their parent task. This makes Wrike data look more consistent to the rest of the system.

#### Function details

##### `_profile_email`  (lines 54–64)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper looks inside a Wrike contact record and finds the first usable email address. Wrike stores emails inside a nested `profiles` list, so this function gives the rest of the connector a simple single value to use.

**Data flow**: It receives one contact-like dictionary. It reads the `profiles` field, checks that it is a list, then scans each profile that is shaped like a dictionary. If it finds a non-empty string under `email`, it returns that email; if not, it returns `None`.

**Call relations**: This helper is used by `WrikeConnector.flatten` when contacts are being converted into the system’s standard shape. It keeps the contact-flattening code from having to repeat the nested email-search logic inline.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 72–96)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Wrike stream from the API, page by page. It also applies the saved update cursor when possible, so an incremental sync only passes along records that look newer than the last successful run.

**Data flow**: It receives an authorized HTTP client, a stream description, and an optional cursor value. It checks whether the requested stream is supported, asks Wrike for pages under that stream’s API path, follows Wrike’s `nextPageToken` to keep going, filters out old records when the stream has an update-date field, and yields non-empty batches of records. If Wrike replies with an authorization or permission error, it turns that into a skipped stream instead of returning data.

**Call relations**: During a sync, the source framework calls this method to obtain raw Wrike records. It delegates the low-level page fetching to the shared REST connector paging helper, and it uses `StreamSkipped` when a stream cannot safely run, such as when Wrike refuses access.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 98–136)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method turns raw Wrike API records into records with clearer, more consistent fields for the rest of the system. It preserves the original data but adds common names such as `name`, `created_at`, `due_date`, `body`, and parent links where they make sense.

**Data flow**: It receives one Wrike record and the stream description telling it what kind of object the record is. For contacts, it builds a display name and extracts an email. For folders, it copies the title into `name` and builds an API URL. For tasks, it reads nested date information and chooses a status. For comments, it maps text, author, creation time, and parent task information into simpler fields. For streams without special rules, it returns the record unchanged.

**Call relations**: After `WrikeConnector.paginate` has supplied raw records, the connector framework can call this method before storing or indexing them. It calls `_profile_email` for contact email extraction and `dict_or_empty` when reading task date details, so missing or oddly shaped nested data does not break the conversion.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).
