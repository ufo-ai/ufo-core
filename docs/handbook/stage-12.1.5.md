# Work management, scheduling, and forms source connectors  `stage-12.1.5`

This stage is behind-the-scenes support for syncing work information from outside tools into the system. Each connector acts like an adapter plug: it knows how one service organizes its data, asks that service for the data through its API, and reshapes the results into records the rest of the system can store, search, and display as readable pages.

Airtable reads bases, tables, and table records from structured workspaces. Asana reads workspaces, projects, tasks, stories, users, and related metadata, including results that arrive in pages. ClickUp walks through its nested structure of teams, spaces, folders, lists, tasks, comments, fields, and goals. monday.com imports users, teams, workspaces, boards, items, updates, logs, and tags. Wrike brings in contacts, folders, tasks, comments, workflows, and custom fields. Calendly focuses on scheduling data such as users, event types, groups, scheduled events, and invitees. Typeform reads forms, responses, workspaces, themes, images, and webhook settings. Together, these files turn many different work tools into one steady stream of searchable system records.

## Files in this stage

### Structured workspace tables
Connects tabular operational workspaces and exposes their bases, tables, and records as syncable pages.

### `extensions/sources/ufo_ext_sources/airtable.py`

`io_transport` · `during Airtable source sync`

Airtable is organized like a set of workspaces. A “base” contains “tables,” and each table contains “records.” This connector walks that structure from the top down instead of expecting someone to list every table by hand. That matters because if a user adds a new Airtable table, the next sync can find it automatically.

The file defines three streams of data: bases, tables, and records. A stream is a named kind of thing the sync system can ask for. The connector first calls Airtable’s metadata API to list bases. For each base, it asks for that base’s tables. For each table, it reads records in pages of up to 100 items, using Airtable’s offset token to move from one page to the next. That offset is like a bookmark Airtable gives back so the connector knows where to continue.

The connector also adds helpful origin labels, such as the base ID and table ID, onto tables and records. Without those labels, a record could be hard to trace back to the Airtable table it came from. The file only reads data; it does not create, update, or delete anything in Airtable.

#### Function details

##### `AirtableConnector._bases`  (lines 39–41)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Airtable bases available to the authenticated user. A base is Airtable’s top-level container, similar to a small database or workspace.

**Data flow**: It receives an HTTP client that is already ready to talk to Airtable. It asks Airtable for the `/meta/bases` endpoint, then pulls the `bases` list out of the response. It returns that list as plain record dictionaries.

**Call relations**: The main `AirtableConnector.paginate` flow calls this whenever it needs to start from the top of the Airtable hierarchy. After `_bases` returns the available bases, `paginate` either yields them directly or uses them to find tables and then records.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 43–50)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Fetches the tables that belong to one Airtable base. It also tags each table with the base it came from, so later steps do not lose that context.

**Data flow**: It receives an HTTP client and one base record. It reads the base’s `id`; if the ID is missing or invalid, it returns an empty list. Otherwise it asks Airtable for that base’s tables, extracts the `tables` list, adds the base ID and base name to each table, and returns the enriched table list.

**Call relations**: `AirtableConnector.paginate` calls this after `_bases` when it is syncing either the `tables` stream or the `records` stream. It uses `records_at` to pick the table list out of Airtable’s response and `with_context` to attach the base information before handing the tables back.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 52–70)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the records from one specific Airtable table, one page at a time. It adds table and base information to every record page so each record can be traced back to its source.

**Data flow**: It receives an HTTP client, a base ID, and a table record. It checks that the table has a usable ID. If so, it repeatedly asks Airtable for pages of records from that base and table, using Airtable’s offset value as the “next page” marker. For each non-empty page, it adds the base ID, table ID, and table name, then yields that page to the caller.

**Call relations**: `AirtableConnector.paginate` calls this while syncing the `records` stream, after it has already found bases and tables. This function is the final step in the discovery chain: bases lead to tables, and tables lead to record pages.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 72–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main reader. Given a requested stream, it decides whether to return Airtable bases, tables, or records, and yields the data in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. For the `bases` stream, it fetches bases and yields them. For the `tables` stream, it fetches bases, then gathers tables for each base and yields them in batches of about 100. For the `records` stream, it fetches bases, then tables, then yields record pages from each table. If the stream name is not one this connector knows, it raises `StreamSkipped`, which tells the sync system that this stream is not implemented here.

**Call relations**: The wider source-sync system calls `paginate` when it wants Airtable data. `paginate` then coordinates the helper methods: `_bases` starts discovery, `_tables_for_base` expands each base into tables, and `_records_for_table` reads the actual row-like records. It is the traffic director for the connector.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 103–125)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Shapes Airtable records into a more consistent form before the rest of the system stores or indexes them. It adds useful URLs and normalizes a few important fields.

**Data flow**: It receives one record and the stream it belongs to. For a base, it keeps the original data and adds an Airtable metadata API URL. For a table, it adds an API URL built from the base and table IDs. For a record, it copies the Airtable creation time into `created_at` and makes sure `fields` is a dictionary. It returns the adjusted record without changing Airtable itself.

**Call relations**: After `paginate` has yielded raw data pages, the surrounding sync machinery can call `flatten` on each item to make it easier to work with. Unlike the pagination helpers, this function does not call Airtable; it prepares already-fetched data for downstream readers.


### Project and task systems
Reads project-management platforms and normalizes their workspaces, users, tasks, comments, boards, workflows, and related metadata.

### `extensions/sources/ufo_ext_sources/asana.py`

`io_transport` · `source sync`

Asana stores work-tracking information behind a web API. This file is the read-only connector for that API. Its job is to say which Asana collections should be synced, what field uniquely identifies each record, and how to keep asking Asana for the next page until there is nothing left.

The file defines a catalog of Asana “streams,” where a stream means one kind of thing to fetch, such as tasks, projects, tags, teams, or workspaces. The most important work items are marked as canonical, meaning they are treated as core recallable objects by the wider system. Most streams are refreshed completely each time. Tasks and projects are special: Asana supports asking only for items changed since a previous timestamp, so the connector can do a smaller incremental sync for them.

The `AsanaConnector` class supplies the Asana base URL and the stream list, then implements pagination. Asana returns lists inside a `data` field and may include a `next_page.offset` token. The connector is like someone reading a long binder one tab at a time: fetch a page, hand over the records, check whether there is a next-page marker, then repeat. It does not store or write Asana data, and it does not hold the access token itself; authentication is provided by the surrounding runner.

#### Function details

##### `_stream`  (lines 24–38)

```
def _stream(name: str, *, cursor_field: str | None=None, updated_at_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a description of one Asana collection that can be synced. It keeps the stream catalog compact and consistent, so every stream is declared with the same basic shape.

**Data flow**: It receives a stream name plus optional details such as which timestamp field can act as a cursor and whether the stream is a core, canonical one. It fills in the common Asana choices, especially that records are keyed by Asana’s `gid`, and returns a `StreamSpec` object that the connector framework can understand.

**Call relations**: This helper is used while building the file’s Asana stream list. It hands the finished stream descriptions to the connector class through `ASANA_STREAMS`, and it relies on `StreamSpec` to package those details in the standard form expected by the source framework.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 74–90)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches all pages for one Asana stream and yields records in batches. It also applies Asana’s incremental-sync option for tasks and projects when a previous cursor timestamp is available.

**Data flow**: It starts with an HTTP client, a stream description, and an optional cursor. It builds request parameters with a page size, adds `modified_since` when Asana supports it, then repeatedly asks Asana for data. From each response it safely extracts the list under `data`, yields that list if it contains records, reads the next-page offset, and either continues with that offset or stops when there is no valid next page.

**Call relations**: The wider REST connector framework calls this method when it needs records for an Asana stream. Inside the loop, it uses the connector’s HTTP `get` helper to make the request, then uses `list_or_empty` to turn Asana’s response field into a safe list before handing each batch back to the sync process.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/clickup.py`

`io_transport` · `source sync`

ClickUp does not keep everything in one simple list. Its data is arranged like a building: teams contain spaces, spaces contain folders, folders contain lists, and lists contain tasks and comments. This connector walks through that building from the top down so it can find the useful records at the bottom without losing where each one came from.

The file defines the ClickUp streams the system can read, such as teams, users, tasks, and list comments. Each stream says what kind of ClickUp object it represents and which field identifies each record. Some streams also name a time field, so later syncs can skip records that have not changed since the last run.

The `ClickUpConnector` talks to ClickUp’s REST API, meaning it makes web requests to ClickUp’s HTTP endpoints and receives JSON data back. It never writes to ClickUp; it is read-only. Helper methods fetch each level of the hierarchy and attach parent information, such as a `team_id` or `list_id`, so records remain understandable after they are pulled out of ClickUp. The main `paginate` method chooses the right fetching path for each stream and yields batches of records. Finally, `flatten` reshapes some records into more consistent fields, such as a task’s status or a comment’s author.

#### Function details

##### `ClickUpConnector._teams`  (lines 58–60)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the ClickUp teams available to the authenticated account. This is the starting point for most other reads, because ClickUp organizes spaces, goals, and users under teams.

**Data flow**: It receives an HTTP client that can make authenticated requests. It asks ClickUp for `/team`, pulls the `teams` list out of the response, and returns that list as plain record dictionaries.

**Call relations**: This is the first step in the hierarchy. `_spaces` calls it before looking for spaces under each team, and `paginate` calls it directly when syncing the teams stream or when building team-based streams such as users and goals.

*Call graph*: called by 2 (_spaces, paginate); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 62–70)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived spaces inside every ClickUp team. A space is a major area of work inside a team, so later steps need these IDs to go deeper.

**Data flow**: It starts with the list of teams from `_teams`. For each team with a valid ID, it asks ClickUp for that team’s spaces, extracts the `spaces` records, adds the parent `team_id` to each one, and returns one combined list.

**Call relations**: This continues the top-down walk begun by `_teams`. `_folders`, `_lists`, and `paginate` call it when they need to sync spaces themselves or discover the lower parts of the ClickUp structure.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 72–82)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived folders inside the discovered spaces. Folders are one of the places where ClickUp lists can live.

**Data flow**: It receives an HTTP client, gets spaces from `_spaces`, and then asks ClickUp for folders in each valid space. It extracts the folder records, stamps each with the parent `space_id`, and returns them together.

**Call relations**: This method sits between spaces and lists in the hierarchy. `_lists` calls it to find lists that live inside folders, and `paginate` calls it when the folders stream is being synced.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 84–100)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived ClickUp lists, both lists inside folders and lists directly inside spaces. Lists matter because tasks, comments, and custom fields are read from each list.

**Data flow**: It first gathers folders and asks ClickUp for each folder’s lists, adding the `folder_id` to those records. Then it gathers spaces and asks for folderless lists directly under each space, adding the `space_id`. It returns all discovered list records as one collection.

**Call relations**: This is the key bridge to the leaf data. `_tasks` and `_list_child_stream` call it before reading per-list records, and `paginate` calls it directly when syncing the lists stream.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _tasks, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 102–127)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads tasks from every discovered ClickUp list. It supports incremental syncing by keeping only tasks whose `date_updated` value is newer than the saved cursor.

**Data flow**: It receives an HTTP client and an optional cursor, which is the last-seen update marker. For each valid list from `_lists`, it requests task pages one page number at a time, includes closed tasks and subtasks, adds the list ID and name to every task, filters out older tasks when a cursor is present, and yields each non-empty batch.

**Call relations**: `paginate` calls this when the tasks stream is requested. This method depends on `_lists` to know where tasks live, then uses the shared record-extraction and context helpers before handing task batches back to the sync loop.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 129–148)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads list-level child records, currently comments and custom fields, from every discovered list. It gives these child records their list context so they are not detached from the list they belong to.

**Data flow**: It receives an HTTP client, the stream definition, and an optional cursor. It gets all lists from `_lists`, chooses the correct ClickUp endpoint for comments or fields, extracts the matching records, adds `list_id` and `list_name`, optionally filters by the stream’s cursor field, and yields any non-empty batches.

**Call relations**: `paginate` uses this method for the `list_comments` and `list_custom_fields` streams. It reuses the list discovery work from `_lists` and then feeds normalized batches back to the caller.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 150–204)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses how to fetch each ClickUp stream and yields records in batches. This is the main read dispatcher that the broader connector framework calls during a sync.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. Depending on the stream name, it calls the appropriate helper, builds special streams such as users from team members, reads goals per team, yields batches when data exists, or raises a skip signal if the stream is not implemented.

**Call relations**: This is the central traffic director for the file. It calls `_teams`, `_spaces`, `_folders`, `_lists`, `_tasks`, and `_list_child_stream` at the right moment, then passes their batches outward to the sync system. If no path exists for a requested stream, it raises `StreamSkipped` so the caller can move on safely.

*Call graph*: calls 7 internal fn (__init__, _folders, _list_child_stream, _lists, _spaces, _tasks, _teams); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 206–240)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Cleans up selected ClickUp records into fields that are easier for the rest of the system to use. For example, it turns nested task status data into a simple `status` value and gives comments a clear body and author.

**Data flow**: It receives one raw record and the stream it came from. For users, spaces, folders, lists, tasks, and list comments, it copies the original data and adds or standardizes common fields such as `name`, `email`, `created_at`, `api_url`, `status`, `body`, and `parent_external_id`. For other streams, it returns the record unchanged.

**Call relations**: This method is the cleanup step after records have been fetched. Within this file it only calls `dict_or_empty` to safely read a comment’s user object, and it serves as the connector’s formatting hook for the broader REST source framework.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/monday.py`

`io_transport` · `source sync`

monday.com exposes its data through GraphQL, which is a query language where the client asks for exactly the fields it wants. This file is the read-only monday.com connector: it builds those GraphQL queries, sends them to monday, pages through large result sets, and shapes the returned data into records the wider system can store and search.

The connector is careful about two monday-specific wrinkles. First, different monday endpoints page data in different ways. Top-level things like users and boards use numbered pages, while board items use a cursor, which is like a “next page ticket” returned by monday. Second, monday does not provide a server-side “only give me things changed since this date” option for these streams, so the connector fetches pages and filters records locally using stored timestamps.

If monday refuses a query, either through a GraphQL error or an HTTP 401/403 permission problem, the file raises `StreamSkipped`. That means the sync records the stream as skipped instead of silently saving incomplete data. The file also extracts useful hidden details, such as assigned person IDs from monday item column values, and normalizes records in `flatten` so the rest of the system sees familiar fields like name, body, author, and parent ID.

#### Function details

##### `_extract_person_ids`  (lines 70–98)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: This helper pulls assigned person IDs out of monday item columns. monday stores assignments inside board-specific “people” columns, so this function finds those columns by type and extracts the stable person IDs hidden inside them.

**Data flow**: It receives the raw `column_values` from a monday item. It ignores anything that is not a list, skips columns that are not people columns, parses the column’s JSON value when needed, and keeps only entries marked as people rather than teams. It returns a simple list of person ID strings and does not change the original item by itself.

**Call relations**: During item syncing, `MondayConnector._items` calls this helper for each item it receives from monday. `_items` then adds the returned list to the item as `assignee_ids`, so later parts of the system can understand who is assigned without needing to know monday’s column format.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 106–118)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This is the connector’s basic GraphQL request helper. It sends one query to monday.com, unwraps the useful `data` section, and turns monday GraphQL errors into a clean stream skip.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional variables. It posts them to monday’s GraphQL endpoint, checks whether monday returned an `errors` section, and if so raises `StreamSkipped` so the sync does not commit partial or refused data. If the response contains a dictionary under `data`, it returns that dictionary; otherwise it returns an empty dictionary.

**Call relations**: Most of the connector’s data-fetching paths rely on this helper instead of talking to monday directly. `_paged_root`, `_items`, `_activity_logs`, and `paginate` call it whenever they need a monday query answered, which keeps error handling consistent across all streams.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, paginate).


##### `MondayConnector._paged_root`  (lines 120–143)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads monday collections that use simple numbered pages, such as users, workspaces, boards, and updates. It keeps asking for page 1, page 2, and so on until monday returns no records.

**Data flow**: It receives the monday field to query, the field selection to request, and optionally a saved timestamp cursor. For each page, it asks `_graphql` for up to 100 records, converts missing or malformed results into an empty list, and optionally removes records whose cursor field is not newer than the saved cursor. It yields each non-empty page of records to its caller and stops when there are no records left after filtering.

**Call relations**: `MondayConnector._boards` uses this to collect all boards before fetching board-specific data. `MondayConnector.paginate` also uses it directly for streams that can be fetched from a top-level monday collection, making it the shared “numbered-page reader” for the connector.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, paginate); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 145–156)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper fetches all monday boards with the board details needed by other streams. It exists because items and activity logs are attached to boards, so the connector must know which boards to visit first.

**Data flow**: It starts with an empty list, asks `_paged_root` for every page of boards, and adds each returned page to the list. The result is one combined list of board records, including fields such as ID, name, description, state, URL, timestamps, and workspace information.

**Call relations**: `MondayConnector._items` and `MondayConnector._activity_logs` call this before they fan out into board-specific queries. In that bigger flow, `_boards` acts like the itinerary: it tells the connector which monday boards it needs to inspect next.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 158–217)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads items from every monday board. Items use monday’s cursor-based paging, so this function follows each board’s “next page” cursor until all item pages for that board have been read.

**Data flow**: It receives an HTTP client and an optional saved `updated_at` cursor. First it gets all boards from `_boards`. For each board, it asks monday for the first item page, then uses monday’s returned cursor to ask for later pages. For every item it receives, it adds `assignee_ids` by reading people columns through `_extract_person_ids`; if a saved cursor exists, it keeps only items updated after that cursor. It yields each non-empty batch of items and moves on when a board has no next item cursor.

**Call relations**: `MondayConnector.paginate` calls this when the active stream is `items`. Inside, `_items` depends on `_boards` to know where to look, `_graphql` to fetch each page, and the small safe-conversion helpers to avoid crashing on missing or oddly shaped monday responses.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (paginate); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 219–247)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads recent activity log entries from each monday board. Activity logs are board-specific, so it walks through the board list and asks monday for logs board by board.

**Data flow**: It receives an HTTP client and an optional saved `created_at` cursor. It fetches all boards, queries monday for up to 100 activity logs for each board, and adds the board ID onto each log so the system can tell where the log came from. If a cursor is present, it keeps only logs created after that saved value. It yields a batch of logs for each board that has matching records.

**Call relations**: `MondayConnector.paginate` calls this for the `activity_logs` stream. The function uses `_boards` as its list of places to visit and `_graphql` for each board query, then hands cleaned batches back to the pagination flow.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (paginate); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 249–323)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading monday streams. Given a stream name, it chooses the right query strategy and yields pages of records for that stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks the stream name and either uses `_paged_root`, calls a specialized helper like `_items` or `_activity_logs`, or sends a one-off GraphQL query for simpler streams like teams and tags. It yields pages of records as they are found. If the stream is unknown, or monday refuses access with a 401 or 403 status, it raises `StreamSkipped` so the sync records a controlled skip rather than treating the run as successful.

**Call relations**: The connector framework calls `paginate` when it wants records for a monday stream. `paginate` then routes the work to `_paged_root`, `_items`, `_activity_logs`, or `_graphql` depending on the stream, making it the central traffic director for all monday reads.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _graphql, _items, _paged_root).


##### `MondayConnector.flatten`  (lines 325–366)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes raw monday records into a more consistent form for the rest of the system. It keeps the original fields but adds or standardizes common fields like `name`, `body`, `author`, `created_at`, and parent links.

**Data flow**: It receives one record and the stream it came from. Based on the stream name, it copies the record and fills in normalized fields: users keep name and email, boards and workspaces get a usable API URL, items get status, updates get body and author details, and activity logs get subject, body, author, and parent board ID. If the stream has no special rules, it returns the record unchanged.

**Call relations**: After `paginate` has produced raw pages from monday, the connector framework can call `flatten` on individual records before storage or indexing. `flatten` does not fetch more data; it is the final cleanup step that makes monday-specific shapes easier for the rest of the system to use.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/wrike.py`

`io_transport` · `during a Wrike source sync`

Wrike exposes its data through a web API, but the raw responses are shaped for Wrike, not for this project. This file acts like a translator at the edge of the system. It knows which Wrike collections are available, how to ask Wrike for each page of results, and how to reshape important fields into common names like “name,” “created_at,” “body,” and “parent_external_id.”

Wrike sends lists of records inside a wrapper that includes both the actual data and a token for the next page. The connector follows those page tokens until there is nothing left to read. Wrike does not provide a dependable way to ask “only give me things changed since last time,” so the connector fetches pages and then filters out records whose update date is not newer than the saved cursor. This is less elegant than true server-side filtering, but it keeps repeated syncs from reprocessing old items when possible.

The connector is read-only. It does not create or update Wrike data. It also does not store an access token itself; authentication is supplied by the surrounding runner. If Wrike refuses access, such as because the user lacks permission or the credential is invalid, the connector skips that stream cleanly instead of crashing the whole sync.

#### Function details

##### `_profile_email`  (lines 54–64)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper looks inside a Wrike contact record and tries to find the first usable email address. Wrike stores emails inside a list of profile objects, so this function hides that nested shape from the rest of the connector.

**Data flow**: It receives one Wrike record as a dictionary. It reads the record’s “profiles” field, checks that it is a list, then walks through each profile looking for a non-empty string under “email.” It returns that email if it finds one; otherwise it returns nothing.

**Call relations**: This helper is used when WrikeConnector.flatten prepares contact records. The flattening step asks it for a simple top-level email value, so downstream code does not need to understand Wrike’s nested profile format.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 72–96)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Wrike stream page by page. It is the part that actually asks Wrike for data, follows Wrike’s next-page token, skips streams that are not supported, and avoids yielding records that are older than the saved cursor when possible.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. First it checks whether this stream is one the connector knows how to run. Then it asks the shared REST paging helper for batches of records from the matching Wrike API path. If a cursor and cursor field exist, it keeps only records whose cursor value is newer. It yields each non-empty batch to the sync runner. If Wrike responds with an access refusal, it turns that into a stream skip instead of returning records.

**Call relations**: The wider source sync calls this method when it needs records for a specific Wrike stream. Inside the method, unsupported streams or refused API access are reported through StreamSkipped, which tells the runner that this stream cannot be read right now without treating it like an unexpected failure.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 98–136)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method reshapes raw Wrike records into friendlier, more consistent records for the rest of the system. It keeps the original fields but adds common fields that make contacts, folders, tasks, and comments easier to search and display.

**Data flow**: It receives one raw Wrike record and the stream it came from. For contacts, it builds a display name from first and last name and adds an email found by _profile_email. For folders and tasks, it maps Wrike titles to “name” and adds useful dates or status values. For comments, it maps text to “body,” authorId to “author,” and taskId to a parent link. For task date details, it uses dict_or_empty so missing or malformed date data becomes a safe empty dictionary. It returns the enriched record.

**Call relations**: After WrikeConnector.paginate yields raw batches, the sync framework can call this method on each record before storing or indexing it. It hands off contact email extraction to _profile_email and uses the shared dict_or_empty helper to safely read nested task dates.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).


### Scheduling metadata
Imports scheduling-platform entities such as users, event types, groups, memberships, scheduled events, and invitees.

### `extensions/sources/ufo_ext_sources/calendly.py`

`io_transport` · `source sync`

Calendly is organized around an account's current organization, so this connector first asks Calendly, “Who is the current user?” and then uses that user's organization to fetch most other data. Without this step, the connector would not know which organization’s events, groups, or memberships to read.

The file defines the Calendly streams the system can sync. A stream is a named kind of data, like scheduled events or invitees. For each stream, the connector knows which Calendly API endpoint to call and which field uniquely identifies each record.

Calendly sends large collections in pages, like a book split into chapters. This connector follows Calendly’s “next page token” until all pages have been read. For streams that can be updated over time, it also uses a saved cursor, which is like a bookmark saying “only give me records newer than this.” Event types use an updated-time bookmark, scheduled events use a start-time bookmark, and invitees are filtered by creation time after they are fetched.

The connector also reshapes some records before storage. For example, membership records copy out the member’s name and email, then remove the nested user object so later profile changes do not make the membership look like a different record.

#### Function details

##### `_uuid_from_uri`  (lines 61–64)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: This helper pulls the final ID-like part out of a Calendly URI. It is used when the connector needs the event’s UUID to call an invitee endpoint.

**Data flow**: It receives any value that might be a URI. If the value is not a non-empty string, it returns nothing. If it is a string, it trims any trailing slash, takes the text after the final slash, and returns that as the UUID.

**Call relations**: When reading invitees, CalendlyConnector._invitees gets each scheduled event’s URI and asks this helper to extract the event UUID. That UUID is then used to build the API path for the event’s invitees.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 72–75)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: This asks Calendly for the account connected to the current credential. The most important piece it retrieves is the account’s current organization, which other streams need before they can be read.

**Data flow**: It receives an HTTP client that can make authenticated requests. It requests /users/me, looks inside the response for the resource object, and returns that object if it is a dictionary. If Calendly does not return a usable user object, it returns an empty dictionary.

**Call relations**: CalendlyConnector.paginate uses this directly for the api_user stream. CalendlyConnector._org_stream also calls it before reading organization-based streams, because those streams need the current organization URI.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 77–90)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Calendly collection endpoint page by page. It hides the repeated work of following Calendly’s next-page token until there are no more records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It calls the shared REST pagination helper with Calendly’s page format: records live under collection, the next page token lives under pagination.next_page_token, and each page asks for up to 100 records. It yields each page as a list of record dictionaries.

**Call relations**: CalendlyConnector._org_stream uses this for organization-wide collections such as event types and scheduled events. CalendlyConnector._invitees uses it again for the invitee list under each scheduled event.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 92–108)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Calendly stream that belongs to the current organization. It makes sure every request includes the organization, and it can add a cursor filter when the stream supports incremental syncing.

**Data flow**: It receives an HTTP client, an API path, and optionally a saved cursor plus the Calendly parameter name that should receive that cursor. It first fetches the current user, reads current_organization, and stops the stream with StreamSkipped if no organization is available. Then it builds request parameters, paginates the collection, attaches the organization as context to each record, and yields the pages.

**Call relations**: CalendlyConnector.paginate calls this for event types, groups, organization memberships, and scheduled events. CalendlyConnector._invitees also calls it to get the scheduled events whose invitees must be fetched. It relies on CalendlyConnector._current_user for organization discovery, CalendlyConnector._paginate_collection for page-by-page reading, and with_context to preserve which organization the records came from.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 110–128)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads invitees for scheduled events. Calendly does not expose invitees as one simple organization-wide list, so the connector first reads events and then asks for invitees event by event.

**Data flow**: It receives an HTTP client and an optional cursor. It reads scheduled events through the organization stream, extracts each event UUID from its URI, and calls the event-specific invitee endpoint. If a cursor is present, it keeps only invitees whose created_at value is newer than the cursor. It yields non-empty invitee pages with extra context showing which scheduled event they belong to.

**Call relations**: CalendlyConnector.paginate calls this when the requested stream is event_invitees. This function depends on CalendlyConnector._org_stream to find events, _uuid_from_uri to turn event URIs into endpoint IDs, CalendlyConnector._paginate_collection to read invitee pages, and with_context to attach the parent event information.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 130–162)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading Calendly streams. Given a stream name, it chooses the correct Calendly API path and yields records in pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. For api_user, it fetches the current user and yields that single record. For organization-based streams, it calls the organization stream helper with the right path and, where needed, the right cursor parameter. For event_invitees, it delegates to the invitee-specific flow. If the stream name is unknown, it marks the stream as skipped.

**Call relations**: The wider sync system calls this when it wants records for a Calendly stream. This function then hands the work to CalendlyConnector._current_user, CalendlyConnector._org_stream, or CalendlyConnector._invitees depending on the stream, and uses StreamSkipped when there is no implemented path.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 164–204)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes Calendly records into easier-to-use records for storage and search. It adds common fields like name, email, title, dates, and location in predictable places.

**Data flow**: It receives one raw Calendly record and the stream it came from. Depending on the stream, it copies useful fields to standard names, extracts nested values such as a scheduled event’s location, or removes the nested user object from organization memberships after copying out the user’s name and email. It returns the cleaned-up record without changing the original stream selection.

**Call relations**: After records are fetched through pagination, the connector framework can call this to prepare each record for downstream use. It uses dict_or_empty when reading a nested membership user so missing or malformed user data does not break the flattening step.

*Call graph*: 1 external calls (dict_or_empty).


### Form response platforms
Reads form-building and response data, including forms, submissions, workspaces, themes, media, and webhooks.

### `extensions/sources/ufo_ext_sources/typeform.py`

`io_transport` · `source sync`

Typeform stores useful information behind a web API, but that API is split into several kinds of data and uses different paging styles. This file is the adapter that knows those Typeform-specific rules. Without it, the wider system could not reliably pull Typeform forms, form answers, or webhook settings.

The main class, `TypeformConnector`, is a read-only connector. It does not keep an access token itself; the surrounding runner supplies an authenticated HTTP client. Think of it like a tour guide: the system says which room it wants to visit, and this file knows which Typeform doorway to use and how to keep walking until all pages have been collected.

Simple collections, such as workspaces, images, and themes, are fetched through normal numbered pages. Forms are fetched the same way, with an optional cursor, meaning “only records newer than this point.” Responses are more involved: the connector first lists all forms, then asks Typeform for the responses for each form, adding the form’s id and title to every response so the answer still has its context. Webhooks work similarly, one form at a time.

If Typeform refuses a request with an authorization error, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `TypeformConnector.paginate`  (lines 52–79)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway for reading one Typeform stream. Given a stream name, it chooses the right fetching method and yields batches of records for the rest of the sync system.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional cursor saying where a previous sync left off. It checks the stream name, delegates to the matching helper, and passes each batch of records back to its caller. If the stream is not supported, or Typeform refuses access with a 401 or 403 status, it turns that into a clear “stream skipped” result rather than treating it as normal data.

**Call relations**: The broader source runner calls this method when it wants records from a particular Typeform stream. `paginate` then calls `_forms`, `_responses`, `_paged_items`, or `_webhooks` depending on the stream, so the rest of the system does not need to know Typeform’s different API shapes.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 81–99)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Typeform endpoints that return ordinary numbered pages of `items`. It keeps asking for the next page until Typeform says there are no more pages.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. For each page, it adds the page number and a default page size, requests the data, extracts the list under `items`, and yields that list if it is not empty. It stops when Typeform’s `page_count` says the last page has been reached, or when a short page suggests there is nothing more to fetch.

**Call relations**: `paginate` uses this directly for streams like workspaces, images, and themes. `_forms` also relies on it as the basic way to list forms before adding form-specific filtering.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 101–108)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches Typeform forms and, when asked, filters them to only forms updated after a saved cursor. It is the shared starting point for several other Typeform reads.

**Data flow**: It receives an HTTP client and an optional cursor. It pulls form pages through `_paged_items`, then, if a cursor is present, keeps only forms whose `last_updated_at` value is later than that cursor. Non-empty batches are yielded onward.

**Call relations**: `paginate` calls this when the requested stream is forms. `_responses` and `_webhooks` also call it first because both need to know which forms exist before they can fetch form-specific responses or webhook settings.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 110–132)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches submitted answers for every Typeform form. It also adds the form id and title to each response so that an answer can be traced back to the form it came from.

**Data flow**: It receives an HTTP client and an optional cursor. First it fetches all forms without filtering them by form update time. For each valid form id, it requests that form’s responses; if a cursor was supplied, it sends it as a `since` parameter so Typeform can return newer responses. Each batch of responses is enriched with `form_id` and `form_title`, then yielded.

**Call relations**: `paginate` calls this when the requested stream is responses. Inside, it calls `_forms` to discover the forms to visit, then uses the shared context helper so downstream code receives responses with their parent form information attached.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 134–143)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches webhook settings for every Typeform form. A webhook is a saved instruction telling Typeform to notify another system when something happens.

**Data flow**: It receives an HTTP client. It first lists all forms, skips any form without a usable id, then requests the webhook list for each form. It extracts the `items` list from Typeform’s response, adds the form id and title to each webhook record, and yields the enriched records.

**Call relations**: `paginate` calls this when the requested stream is webhooks. Like `_responses`, it depends on `_forms` to find each form before making form-specific requests, and it uses the shared record-extraction and context helpers to shape the data for the rest of the sync.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).
