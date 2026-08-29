# Work, engineering, and collaboration connectors  `stage-14.5`

This stage is shared behind-the-scenes support for syncing outside work tools into the system. Each connector is like an adapter plug: it talks to one service’s API, meaning its web doorway for data, then reshapes the answer into records the rest of the system can store, search, and recall.

Airtable reads bases, tables, and records. Calendly reads users, event types, scheduled events, and invitees. Asana, ClickUp, Jira, Linear, monday.com, and Wrike read project-management data such as tasks, issues, projects, comments, teams, boards, folders, and custom fields. Confluence and Notion bring in team knowledge, turning pages, databases, comments, and rich content blocks into readable text instead of raw technical data. Slack reads conversations, messages, threads, users, and participants. GitHub reads engineering work such as organizations, repositories, issues, commits, releases, and users. PagerDuty reads incident-response information, including services, schedules, incidents, and on-call records. Sentry reads error-tracking data such as projects, issues, events, members, and releases. Together, these files feed many workplace sources into one common search-and-sync pipeline.

## Files in this stage

### Structured records and scheduling
Connectors that ingest flexible business databases and calendar scheduling data into searchable records.

### `extensions/sources/ufo_ext_sources/providers/airtable.py`

`io_transport` · `during Airtable source sync`

Airtable stores information in a nested shape: an account has bases, each base has tables, and each table has records. This connector exists because the rest of the system wants a steady stream of items, but Airtable does not provide one simple “give me everything” endpoint. Without this file, Airtable content would not be discoverable or syncable.

The connector starts at Airtable’s metadata API to find all bases. For each base, it asks Airtable for the tables inside it. For each table, it reads records in pages of up to 100, following Airtable’s offset token when there is more data. This is like checking a building directory first, then visiting every floor and room, instead of assuming you already know the layout.

As it reads, it adds useful context to each item. Tables are marked with the base they came from, and records are marked with both their base and table. That matters because a record ID alone is not enough for a person or later process to understand where it lives. The connector is read-only: it can pull Airtable data into the system, but it does not create or edit anything in Airtable.

#### Function details

##### `AirtableConnector._bases`  (lines 39–41)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper asks Airtable for the list of bases the authenticated user can access. A base is Airtable’s top-level container, similar to a workbook or project space.

**Data flow**: It receives an HTTP client that is already set up for making web requests. It requests Airtable’s `/meta/bases` endpoint, extracts the list stored under `bases`, and returns that list as plain dictionaries.

**Call relations**: The main `AirtableConnector.paginate` flow calls this whenever it needs to start from the top of the Airtable hierarchy. It relies on `records_at` to pull the useful list out of Airtable’s response body.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 43–50)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This helper finds the tables inside one Airtable base. It also labels each table with the base it came from, so later steps do not lose that context.

**Data flow**: It receives an HTTP client and one base record. It reads the base ID, skips the request if the ID is missing or invalid, asks Airtable for that base’s tables, extracts the table list, adds the base ID and base name to each table, and returns the enriched list.

**Call relations**: The main `AirtableConnector.paginate` flow calls this after it has found bases. It uses `records_at` to extract table records and `with_context` to attach base information before those tables are passed onward or used to find records.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 52–70)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads the actual rows, called records, from one Airtable table. It returns them in pages so large tables can be synced without loading everything at once.

**Data flow**: It receives an HTTP client, a base ID, and a table record. It checks that the table has a valid ID, then requests records from Airtable using cursor-style paging, where Airtable gives back an `offset` token for the next page. Each non-empty page is labeled with the base ID, table ID, and table name, then yielded to the caller.

**Call relations**: `AirtableConnector.paginate` calls this while walking through every discovered base and table. This function hands each record page back upward, after adding table and base context with `with_context`.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 72–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading routine for the connector. Given a requested stream, it decides whether to produce bases, tables, or records, and then walks Airtable’s hierarchy in the right order.

**Data flow**: It receives an HTTP client, a stream description, and a cursor value. For the `bases` stream, it fetches bases and yields them once. For the `tables` stream, it fetches bases, then their tables, batching tables into pages of about 100. For the `records` stream, it fetches bases, then tables, then record pages for each table. If the stream name is unknown, it raises a skip signal instead of pretending it can sync it.

**Call relations**: The source-sync framework calls this when it needs Airtable data. This function orchestrates the helper methods `_bases`, `_tables_for_base`, and `_records_for_table`, and raises `StreamSkipped` when the framework asks for a stream this connector does not implement.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 103–125)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes Airtable items into a more consistent form for the rest of the system. It adds convenient fields such as API URLs and normalizes record fields.

**Data flow**: It receives one Airtable record and the stream it belongs to. For bases, it keeps the original data and adds a direct metadata API URL. For tables, it adds an API URL built from the base and table IDs. For records, it exposes `createdTime` as `created_at` and ensures `fields` is always a dictionary. It returns the cleaned-up record without changing the original stream flow.

**Call relations**: After `paginate` has supplied raw items, the broader connector framework can call `flatten` to make each item easier to store, display, or search. It does not call other project functions itself; it is the final shaping step for each Airtable item.


### `extensions/sources/ufo_ext_sources/providers/calendly.py`

`io_transport` · `source sync`

This connector is a read-only bridge between Calendly and the project’s source-sync system. Calendly stores scheduling data behind a web API, so this file knows which API endpoints to call, how to page through long lists, and how to keep later syncs from rereading everything from scratch.

The connector starts by asking Calendly who the current API user is. From that answer it finds the current organization, because most Calendly data is scoped to an organization. If Calendly does not provide an organization, the connector skips those organization-based streams rather than guessing.

For ordinary lists, such as event types, groups, memberships, and scheduled events, it requests pages of up to 100 records and follows Calendly’s “next page token,” which is like a bookmark for the next batch. Some streams use a saved cursor, meaning a remembered timestamp, so future runs can ask only for newer or relevant records.

Invitees are a little different. Calendly exposes invitees under each scheduled event, so the connector first lists scheduled events, extracts each event’s ID from its URI, then asks for that event’s invitees. It adds context, such as the parent scheduled event, so those invitee records still make sense on their own.

Finally, the file reshapes selected records in `flatten`, adding common fields like name, email, title, start time, and location where useful.

#### Function details

##### `_uuid_from_uri`  (lines 61–64)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: This helper pulls the final ID-like part out of a Calendly URI. It is used when the connector needs the scheduled event identifier that Calendly expects in an invitees API path.

**Data flow**: It receives any value that might be a URI. If the value is a non-empty string, it removes any trailing slash and returns the text after the last slash. If the input is missing or not a string, it returns nothing.

**Call relations**: When `CalendlyConnector._invitees` is walking through scheduled events, it calls this helper to turn each event’s full URI into the shorter event ID needed to request that event’s invitees.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 72–75)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: This asks Calendly for the account connected to the current credentials. The result is important because it contains the current organization, which is needed before most other Calendly data can be read.

**Data flow**: It takes an HTTP client, calls Calendly’s `/users/me` endpoint, and looks for a `resource` object in the response. If that object is a dictionary, it returns it as the current user; otherwise it returns an empty dictionary.

**Call relations**: `CalendlyConnector.paginate` uses this directly for the `api_user` stream. `CalendlyConnector._org_stream` also uses it before reading organization-scoped streams, because those streams need the organization URI from the current user.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 77–90)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Calendly list endpoint page by page. It hides the repetitive work of following Calendly’s pagination token so callers can simply receive batches of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST helper to read records from the response’s `collection` field, follow `pagination.next_page_token`, and request up to 100 records per page. It yields each page as a list of record dictionaries.

**Call relations**: `CalendlyConnector._org_stream` uses this for organization-level lists. `CalendlyConnector._invitees` uses it for the invitees under each scheduled event. In both cases, this function is the common paging machinery.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 92–108)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a Calendly stream that belongs to the current organization. It makes sure every request includes the organization and optionally adds a cursor so the sync can focus on newer data.

**Data flow**: It receives an HTTP client, an API path, and optional cursor information. First it fetches the current user and reads `current_organization`. If no valid organization is present, it raises `StreamSkipped`, meaning this stream cannot safely run. Otherwise it builds request parameters, adds the cursor if provided, reads pages through `_paginate_collection`, and yields those records with the organization added as extra context.

**Call relations**: `CalendlyConnector.paginate` calls this for event types, groups, organization memberships, and scheduled events. `CalendlyConnector._invitees` also calls it to get the scheduled events it must inspect before fetching invitees. It relies on `_current_user` for the organization, `_paginate_collection` for page-by-page reading, and `with_context` to attach the organization to each record.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 110–128)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads invitees for scheduled events. Calendly does not provide these as one simple organization-wide list, so this function visits each scheduled event and then asks for the invitees attached to it.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It first reads scheduled events through `_org_stream`. For each event, it extracts the event ID from the event URI. If the ID is usable, it reads that event’s invitees through `_paginate_collection`. When a cursor is present, it keeps only invitees whose `created_at` value is later than the cursor. It yields non-empty batches and adds the scheduled event URI and event ID as context.

**Call relations**: `CalendlyConnector.paginate` calls this when syncing the `event_invitees` stream. This function depends on `_org_stream` to find events, `_uuid_from_uri` to build the invitee endpoint, `_paginate_collection` to read invitee pages, and `with_context` so invitees remain connected to their scheduled event.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 130–162)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading Calendly streams. Given a stream name, it chooses the correct Calendly endpoint and yields pages of records for that stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. For `api_user`, it fetches the current user and yields it as a one-record page. For organization-based streams, it calls `_org_stream`, sometimes passing the cursor as Calendly’s expected filter parameter. For invitees, it calls `_invitees`. If the stream name is unknown, it raises `StreamSkipped` so the sync system knows this connector does not implement it.

**Call relations**: The broader sync framework calls this when it wants records for one of the connector’s declared streams. This function then hands the work to `_current_user`, `_org_stream`, or `_invitees` depending on the stream, acting like a traffic director for all Calendly reads.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 164–204)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes raw Calendly records into friendlier records for the rest of the system. It keeps the original data but adds or lifts useful fields like name, email, title, start time, and location.

**Data flow**: It receives one raw record and the stream it came from. For user and invitee records, it makes key identity fields explicit. For organization memberships, it copies the member’s name and email out of the nested `user` object and removes that nested user object. For scheduled events, it adds fields such as title, description, start and end times, and a simplified location. For event types, it adds fields such as API URL and created time. Streams without special rules are returned unchanged.

**Call relations**: After `paginate` has supplied raw Calendly records, the sync system can call this function to prepare each record for storage or indexing. It uses `dict_or_empty` when reading nested membership user data, so missing or malformed user details do not break the transformation.

*Call graph*: 1 external calls (dict_or_empty).


### Project management platforms
Connectors that read tasks, projects, boards, teams, comments, and work metadata from major work-tracking systems.

### `extensions/sources/ufo_ext_sources/providers/asana.py`

`io_transport` · `source sync`

Asana is a work-tracking service, and its API returns information in small pages rather than all at once. This file is the read-only connector for that API. Without it, the system would not know which Asana objects exist, which ones are most important, or how to keep asking Asana for the next page of results.

The file first defines the Asana streams the system can read. A stream is a named kind of Asana object, like tasks, projects, or users. Some streams are marked as canonical, meaning they are core objects worth treating as primary recallable content. Tasks and projects also support incremental syncing: the connector can ask Asana for only records changed since a saved time, instead of rereading everything.

The `AsanaConnector` class then supplies the Asana base web address and the paging behavior. Asana wraps each response in a `data` list and may include a `next_page` object with an `offset`, which is like a claim ticket for the next batch. The connector repeatedly fetches pages, yields any records it finds, and stops when Asana no longer provides a next-page offset. It does not store or write Asana data itself, and it does not hold the user’s token directly; authentication is supplied by the surrounding source-running system.

#### Function details

##### `_stream`  (lines 24–38)

```
def _stream(name: str, *, cursor_field: str | None=None, updated_at_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Asana data stream, such as tasks or projects. It keeps the stream list short and consistent by filling in shared Asana details like the primary key field.

**Data flow**: It receives a stream name and optional timing fields that say how changes are tracked. It builds a `StreamSpec`, which is the system’s small instruction card for how to identify and sync that kind of record. The result is returned and later placed into the connector’s stream catalog.

**Call relations**: This function is used while the file is being loaded to build the Asana stream list. It hands each finished stream description to `StreamSpec`, so the connector can later expose a complete catalog of Asana objects to the broader sync system.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 74–90)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one Asana stream page by page and produces batches of records for the sync system. It hides Asana’s pagination details so the rest of the system can simply consume lists of records.

**Data flow**: It starts with an HTTP client, a stream description, and an optional saved cursor time. It builds request parameters, adding `modified_since` only for streams where Asana supports incremental updates. For each response, it takes the `data` value, safely treats missing or non-list data as empty through `list_or_empty`, yields real records, then follows Asana’s next-page offset until there is no valid offset left.

**Call relations**: The broader REST connector machinery calls this when it needs records from an Asana stream. During each loop it relies on the inherited `_get` request helper to fetch from Asana, uses `list_or_empty` to normalize the returned record list, and then hands record batches back to the caller until the stream is exhausted.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/providers/clickup.py`

`io_transport` · `source sync`

ClickUp does not present its data as one simple table. It is more like a set of filing cabinets: teams contain spaces, spaces contain folders and lists, and lists contain tasks, comments, and custom fields. This connector walks that structure from the top down so it can find the useful items at the bottom without losing where they came from.

The file defines the ClickUp streams the system knows about, such as teams, users, tasks, and list comments. The ClickUpConnector then uses HTTP requests to ClickUp’s API to fetch each stream. As it moves through the hierarchy, it adds context like team_id, space_id, folder_id, or list_id to records. That context matters because a task or comment is much more useful when the system remembers which list or team it belonged to.

For tasks and comments, the connector can use a cursor, which is a saved “last seen” value, to skip older records on later syncs. This keeps repeat syncs from rereading everything. The connector only reads from ClickUp; it does not create or update anything there. Without this file, the system would not know how to discover ClickUp’s hierarchy or convert ClickUp API responses into clean, recallable records.

#### Function details

##### `ClickUpConnector._teams`  (lines 58–60)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the ClickUp teams available to the connected account. Teams are the top level of ClickUp’s structure, so almost every deeper read starts here.

**Data flow**: It receives an HTTP client that can talk to ClickUp. It asks the /team endpoint for data, pulls the list stored under the teams key, and returns that list of team records.

**Call relations**: This is the first step in the hierarchy. _spaces calls it so it can find spaces inside each team, and paginate calls it directly when the requested stream is teams or when it needs team information for users or goals.

*Call graph*: called by 2 (_spaces, paginate); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 62–70)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived spaces across all ClickUp teams. A space is the next container below a team.

**Data flow**: It starts with the teams from _teams. For each team with a valid id, it requests that team’s spaces from ClickUp, extracts the spaces list, adds the team_id to each space, and returns one combined list.

**Call relations**: This function builds on _teams and becomes the doorway to lower levels. _folders uses it to find folders inside spaces, _lists uses it to find folderless lists inside spaces, and paginate uses it when syncing the spaces stream.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 72–82)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived folders inside ClickUp spaces. Folders are optional containers that can hold lists.

**Data flow**: It receives an HTTP client, asks _spaces for all spaces, and then requests folders for each valid space id. It extracts folder records, adds the space_id they came from, and returns all folders together.

**Call relations**: This function sits between spaces and lists in the ClickUp hierarchy. _lists calls it to discover lists stored inside folders, and paginate calls it directly when the folders stream is being synced.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 84–100)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Collects all non-archived lists, whether they are inside folders or directly inside spaces. Lists are important because tasks, comments, and custom fields are read from them.

**Data flow**: It first asks _folders for folder-based locations, then requests lists for each valid folder id and adds folder_id to those records. It then asks _spaces for spaces again, requests lists that live directly under each space, adds space_id to those records, and returns the combined list.

**Call relations**: This is the main bridge to the leaf data. _tasks uses it before reading tasks, _list_child_stream uses it before reading comments or custom fields, and paginate uses it when syncing the lists stream itself.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _tasks, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 102–127)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads tasks from every discovered ClickUp list, one API page at a time. It can skip tasks that are not newer than the saved cursor, which makes repeated syncs faster.

**Data flow**: It receives an HTTP client and an optional cursor value. It gets all lists from _lists, then for each valid list id it requests task pages from ClickUp. Each task is stamped with its list_id and list_name. If a cursor is present, tasks whose date_updated is not newer are removed. Non-empty batches are yielded outward, and paging stops when ClickUp returns no more tasks.

**Call relations**: paginate calls this when the requested stream is tasks. This function depends on _lists to know where tasks live, then hands batches of task records back to paginate so the larger sync process can consume them.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 129–148)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads list-level child records, currently comments and custom fields, from every ClickUp list. It keeps those records tied to the list they came from.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It gets all lists from _lists, chooses the correct ClickUp endpoint for comments or custom fields, extracts the right records from the response, adds list_id and list_name, filters by the stream’s cursor field when possible, and yields non-empty batches.

**Call relations**: paginate calls this for the list_comments and list_custom_fields streams. It relies on _lists to find every list first, then returns the child records in batches for the sync process.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 150–204)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the traffic director for ClickUp syncing. Given a stream name, it chooses the correct reading method and yields batches of records for that stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching helper, gathers or yields the resulting records, and stops once that stream is complete. For users, it builds a unique user list from team members embedded inside team records. For goals, it reads goals under each team. If the stream is unknown, it raises a StreamSkipped signal so the system knows this stream is not supported here.

**Call relations**: The broader source framework calls paginate when it wants records from a ClickUp stream. paginate then delegates to _teams, _spaces, _folders, _lists, _tasks, or _list_child_stream as needed, or performs small stream-specific work itself for users and goals.

*Call graph*: calls 7 internal fn (__init__, _folders, _list_child_stream, _lists, _spaces, _tasks, _teams); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 206–240)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes ClickUp records into a shape that is easier for the rest of the system to understand. It adds common fields like name, created_at, status, body, author, and api_url where ClickUp’s raw response uses different names or nested data.

**Data flow**: It receives one raw record and the stream description for that record. Based on the stream name, it copies the original record and adds or rewrites a few friendly fields. For comments, it safely reads the nested user object before choosing an author name. It returns the cleaned-up record and does not change ClickUp or fetch new data.

**Call relations**: After paginate has produced raw records, the source framework can call flatten to prepare each one for storage or indexing. It does not call the hierarchy-reading helpers; its job is the final cleanup step before the record leaves this connector.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/jira.py`

`io_transport` · `during Jira source sync`

This file is the Jira “source connector.” Its job is to visit every Jira site that an OAuth grant can reach, ask Jira for useful objects, and feed those objects into the wider sync system. Without it, the product would not know how to discover Jira sites, page through Jira’s REST API, continue from the last successful sync, or turn Jira’s nested issue data into human-readable text.

The connector starts by asking Atlassian which cloud sites the current credential can access. Each site has a cloud ID, and almost every Jira API path needs that ID, like a street address before a house number. For each supported stream, the connector chooses the right API route and reads results in pages. Most Jira lists use `startAt` and `maxResults`, meaning “start here and give me this many.” Issues, comments, and sprints can use a cursor, which is a saved timestamp that lets later syncs fetch only newer updates.

Some streams depend on others. Comments are fetched by first finding issues, then asking for each issue’s comments. Sprints are fetched by first finding boards, then asking for each board’s sprints. If Jira says the token is not allowed to read something, the connector marks that stream as skipped rather than treating the whole sync as broken.

Finally, it cleans and renders records. Jira issue descriptions and comments use Atlassian Document Format, a tree-shaped rich text format. This file walks that tree and pulls out plain text so the stored page is readable.

#### Function details

##### `JiraConnector.paginate`  (lines 73–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway the sync engine uses to ask for one Jira stream of data. It looks at the stream name, calls the matching reader, and turns permission-denied responses into a clean “skipped” result instead of a hard failure.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor timestamp. It chooses the correct Jira-reading helper, yields pages of records from that helper, and if Jira returns 401 or 403, it changes that error into a stream skip message. Unknown stream names also become skips.

**Call relations**: The wider source sync calls this when it wants pages for a Jira stream. This function then delegates to the specific reader for projects, issues, comments, users, boards, or sprints. Those helpers do the actual API walking and hand pages back up through this function.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 106–110)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Jira cloud sites the current OAuth grant can access. The rest of the connector needs these site IDs before it can request projects, issues, users, or boards.

**Data flow**: It uses the HTTP client to call Atlassian’s accessible-resources endpoint. It reads the JSON response, safely treats missing or non-list data as an empty list, and returns a list of site records. Each useful site record includes an ID used in later Jira API paths.

**Call relations**: Project, issue, user, and board readers call this first so they know which Jira sites to visit. If no sites come back, those readers simply have nowhere to fetch from.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 112–132)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared paging helper for Jira endpoints that return a list inside a response envelope. It keeps asking for the next page until Jira says there is no more data.

**Data flow**: It receives an API path, optional query parameters, and the name of the list field to read from the response. It repeatedly sends requests with `startAt` and `maxResults`, extracts the record list, yields non-empty pages, and stops when Jira reports the last page, the total has been reached, or no records arrive.

**Call relations**: Most stream-specific readers use this instead of rewriting Jira paging rules themselves. Projects, issues, comments, boards, and sprints pass their endpoint paths into it and receive ready-to-yield pages back.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 134–141)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira projects from every accessible Jira site. It adds site information to each project so later code knows where the project came from.

**Data flow**: It first asks for accessible sites. For each site with a valid cloud ID, it builds the project-search API path, reads paged project results, attaches the cloud ID and site URL to each record, and yields those project pages.

**Call relations**: The main paginator calls this when the requested stream is projects. It relies on `_sites` for site discovery and `_offset_values` for Jira-style paging, then returns enriched records upward to the sync engine.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 143–155)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira issues, optionally only those updated after the last saved cursor. It is the core issue reader and also supplies issues for the comment reader.

**Data flow**: It receives an optional cursor timestamp. It builds a JQL query, which is Jira’s issue search language, ordering issues by update time and filtering to newer issues when a cursor is present. For each accessible site, it calls Jira search with a selected set of issue fields, attaches site context to each page, and yields the pages.

**Call relations**: The main paginator calls this for the issues stream. The comments reader also calls it with no cursor so it can find all issues whose comments may need checking. It uses `_sites` for site discovery and `_offset_values` for paged search results.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 157–177)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads comments for Jira issues. Because Jira comments live under individual issues, it first finds issues and then asks for comments on each one.

**Data flow**: It receives an optional cursor timestamp. It fetches issues, takes each issue’s ID and cloud ID, reads that issue’s comment pages, filters comments to those updated after the cursor when one is provided, adds site and issue context, and yields only non-empty comment pages.

**Call relations**: The main paginator calls this for the issue_comments stream. This function depends on `_issues` to know which issues to inspect and `_offset_values` to page through each issue’s comments. It hands context-rich comment records back to the sync flow.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 179–190)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira users from each accessible site. Unlike most Jira endpoints in this file, the users endpoint returns a plain JSON array for the requested page.

**Data flow**: It asks for accessible sites, builds a users/search path for each valid cloud ID, requests the first page of users, safely turns the response into a list, adds the cloud ID and site URL to each user, and yields the page if it contains users.

**Call relations**: The main paginator calls this when syncing users. It uses `_sites` for site discovery and the shared list-cleaning helper for safe response handling, but it does not use the common offset pager because this Jira endpoint’s response shape is different.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 192–199)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira Agile boards from every accessible site. Boards matter because sprints are found underneath boards.

**Data flow**: It gets accessible sites, checks each one for a valid cloud ID, builds the Agile board API path, reads board pages through the common paging helper, attaches site context, and yields those board pages.

**Call relations**: The main paginator calls this for the boards stream. The sprints reader also calls it first, because it needs board IDs before it can ask Jira for sprints. It uses `_sites` and `_offset_values` as its building blocks.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 201–215)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads sprints by walking through Jira boards first. It can filter to sprints updated after a saved cursor, so repeated syncs do not need to keep all old sprint updates.

**Data flow**: It receives an optional cursor timestamp. It reads board pages, takes each board’s ID and cloud ID, requests the sprint list for that board, filters by `updatedDate` when a cursor exists, adds the cloud ID and board ID to each sprint, and yields non-empty sprint pages.

**Call relations**: The main paginator calls this for the sprints stream. This function depends on `_boards` to discover where sprints live and `_offset_values` to page through each board’s sprint list.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 217–224)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This prepares records for the sync engine’s cursor tracking. For issues, it copies Jira’s nested update timestamp into a top-level `updated` field where the rest of the system expects to find it.

**Data flow**: It receives one record and its stream description. If the stream is issues, it safely reads the nested `fields` object, takes `fields.updated`, and returns a copy of the record with a top-level `updated` value. For every other stream, it returns the record unchanged.

**Call relations**: The connector framework calls this after records are fetched and before cursor bookkeeping. It uses `_dict_or_empty` to avoid crashing if Jira sends an unexpected shape.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 226–252)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Jira records into readable page text. For issues and comments, it avoids storing raw nested JSON as the main display and instead produces a title and a simple text body.

**Data flow**: It receives a record and stream description. For issues, it extracts the summary, status, priority, assignee, reporter, and description text, then builds a Markdown-like page. For comments, it uses the author as the title and pulls readable text from the comment body. Other streams fall back to the parent connector’s default rendering.

**Call relations**: The sync/rendering layer calls this when it needs a human-readable version of a Jira record. It calls small helpers to safely read strings and dictionaries, format people and field lines, and turn Atlassian Document Format into plain text.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 255–256)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns a value into a string only if it already is one. It prevents accidental display of non-string data where the connector expects text.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string. It does not change anything outside itself.

**Call relations**: Rendering code calls this when extracting text fields from Jira records. `_person` also uses it while choosing a person’s display name or email address.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 259–260)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This safely treats a value as a dictionary only when it really is one. It keeps malformed or missing Jira fields from causing crashes.

**Data flow**: It receives any value. If the value is a dictionary, it returns it; otherwise it returns an empty dictionary. The caller can then look up keys without first checking the type.

**Call relations**: The flattening and rendering paths use this whenever they step into nested Jira objects. `_person` also uses it to safely inspect author, assignee, or reporter objects.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 263–265)

```
def _person(value: Any) -> str
```

**Purpose**: This picks a readable name for a Jira person object. It prefers the display name and falls back to the email address.

**Data flow**: It receives a value that may or may not be a Jira person dictionary. It safely reads it as a dictionary, looks for `displayName`, then `emailAddress`, and returns the first usable string or an empty string.

**Call relations**: Issue and comment rendering call this when they need to show who the assignee, reporter, or comment author is. It relies on `_dict_or_empty` and `_str` for safe extraction.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 268–269)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: This formats one label-and-value pair for the rendered issue text. It omits empty values so the final page does not contain blank or misleading metadata lines.

**Data flow**: It receives a label such as `Status` and a text value. If the value is present, it returns `Label: value`; if not, it returns an empty string. It has no side effects.

**Call relations**: Issue rendering calls this while building the issue metadata block. The rendered output then keeps only the non-empty lines.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 272–289)

```
def _doc_text(value: Any) -> str
```

**Purpose**: This converts Jira’s rich text document format into plain readable text. Jira descriptions and comments are stored as nested trees, so this function pulls out the actual text leaves.

**Data flow**: It receives any value, usually an Atlassian Document Format object. It walks through dictionaries and lists, collects every string found under a `text` key, joins those pieces with newlines, trims the result, and returns the final plain text.

**Call relations**: The rendering function calls this for issue descriptions and comment bodies. Its inner `walk` helper does the recursive tree-walking work, like opening folders inside folders until it finds the papers with words on them.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 277–286)

```
def walk(node: Any) -> None
```

**Purpose**: This is the small recursive helper inside `_doc_text` that explores the rich text tree. It visits each node and collects text from the leaves.

**Data flow**: It receives one node from the document tree. If the node is a dictionary, it records the node’s `text` value when it is a string, then visits each child in `content`. If the node is a list, it visits each item. It adds found text into the surrounding `_doc_text` collection and returns nothing.

**Call relations**: Only `_doc_text` uses this helper, and it exists inside that function because it is not useful anywhere else. `_doc_text` starts the walk with the original value and later joins the collected text into the final rendered body.


### `extensions/sources/ufo_ext_sources/providers/linear.py`

`io_transport` · `source sync runs`

Linear is a project and issue tracker, and its API only speaks GraphQL, a query language where the caller asks for exactly which fields it wants. This file is the bridge between Linear and the project’s source-sync framework. Without it, the system would not know what Linear data exists, how to ask Linear for it, how to page through large result sets, or how to turn important records into readable text.

The file first defines the list of Linear streams, meaning the different collections it can read, such as issues, projects, comments, users, labels, cycles, and customer tiers. For each stream it stores a GraphQL query. Most streams can be synced incrementally by asking Linear for records updated since the last saved cursor, using the `updatedAt` timestamp like a bookmark in a book. A few Linear collections do not support that filter, so they are read fully each time.

`LinearConnector.paginate` does the actual reading. It sends one GraphQL request at a time, yields the records from that page, then follows Linear’s `endCursor` to fetch the next page until there are no more. It treats permission failures as a skipped stream, but treats GraphQL errors as real failures so the system does not silently save partial or unreliable data.

`LinearConnector.render` makes selected records more human-friendly. Instead of storing only raw API-shaped data, it writes issues, projects, comments, and users as readable notes with titles and useful details.

#### Function details

##### `_stream`  (lines 35–45)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a stream description for one Linear collection. A stream description tells the sync framework what the collection is called, which timestamp can be used as its progress marker, and whether it is one of the main content streams.

**Data flow**: It receives a stream name, an optional cursor field, and a flag saying whether the stream is canonical. It fills in the standard Linear timestamp fields and builds a `StreamSpec`, which is the small object the rest of the source framework uses to know how to sync that collection.

**Call relations**: This helper is used while the file defines `LINEAR_STREAMS`, the catalog of Linear collections. It hands its information into `StreamSpec.__init__`, so the broader connector framework receives consistent stream metadata instead of many hand-written stream objects.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 265–308)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads records from one Linear stream, one page at a time. It is the main network-reading loop for Linear data, including incremental syncs that only ask for records changed since the last saved cursor.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It looks up the GraphQL query for that stream, adds sorting and an `updatedAt` filter when Linear supports it, sends the request to `/graphql`, checks for permission or GraphQL errors, extracts the returned `nodes`, and yields each non-empty page of records. It then reads Linear’s paging information and repeats until Linear says there is no next page or gives no usable cursor.

**Call relations**: The source framework calls this method when it needs records for a Linear stream. Inside the loop, it uses `list_or_empty` to safely turn Linear’s `nodes` value into a list, even if the API response is missing or empty. If Linear refuses access with HTTP 401 or 403, it creates a `StreamSkipped` error so the larger run can record that this stream was skipped rather than treating the whole connector as successfully complete.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 310–352)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns selected Linear records into readable page text. This matters because a raw API record is shaped for machines, while the recall system needs useful titles and prose that people can search and understand.

**Data flow**: It receives one Linear record and the stream it came from. For issues, projects, comments, and users, it pulls out important fields such as title, state, assignee, description, body, name, and email, then formats them into a title and a Markdown-like body. If a comment or other record has no clear title, it uses the first non-empty line of the body, and if that still fails it falls back to the parent connector’s default rendering.

**Call relations**: This method is used after records have been fetched, when the connector framework needs to turn them into stored pages. It calls `_str` to safely accept only real strings, `_ref_id` to extract IDs from small nested reference objects, and `_labeled` to format useful metadata lines. For streams it does not customize, it hands the work back to the base connector’s render behavior.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 355–356)

```
def _str(value: Any) -> str
```

**Purpose**: Safely converts a value into text only when it is already a string. It avoids accidentally turning missing values, numbers, or nested objects into misleading page text.

**Data flow**: It receives any value. If that value is a string, it returns it unchanged; otherwise it returns an empty string. The result is predictable text that formatting code can safely combine.

**Call relations**: `LinearConnector.render` uses this whenever it reads optional text fields from Linear records. `_ref_id` also uses it after pulling an `id` value out of a nested object, so both direct text fields and referenced IDs follow the same safety rule.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 359–360)

```
def _ref_id(value: Any) -> str
```

**Purpose**: Extracts the `id` from a small nested reference object, such as an assignee or project lead. It gives the rendered page a useful link-like identifier without trying to expand the whole related object.

**Data flow**: It receives any value. If the value is a dictionary-like object, it reads its `id` field and passes that through `_str`; otherwise it returns an empty string. The output is either a clean ID string or nothing.

**Call relations**: `LinearConnector.render` calls this when building readable metadata for issues and projects. `_ref_id` delegates the final text check to `_str`, so malformed or missing IDs do not leak into the rendered page.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 363–364)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: Formats a set of label-and-value pairs into simple human-readable lines, such as `state: started` or `email: person@example.com`. It leaves out blank values so pages do not fill up with empty headings.

**Data flow**: It receives a list of pairs, where each pair has a label and a string value. It keeps only pairs with a non-empty value, joins each as `label: value`, and returns the lines joined with newline characters.

**Call relations**: `LinearConnector.render` uses this to build the metadata block for issues, projects, and users. It sits at the final formatting step: after values are cleaned by `_str` or `_ref_id`, `_labeled` turns them into readable lines for the page body.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/monday.py`

`io_transport` · `during source sync, when monday.com streams are fetched and normalized`

monday.com exposes its data through GraphQL, which is a query language where the client asks for exactly the fields it wants. This file wraps those GraphQL calls in a connector named MondayConnector. Without it, the system would not know how to ask monday.com for pages of data, how to continue through long result lists, or how to shape monday records into the common format used by the rest of the source framework.

The file first defines the streams it can read, such as boards, items, and updates. Each stream says what kind of object it reads, what field uniquely identifies a record, and, when possible, what timestamp can be used as a watermark for incremental syncs. monday does not offer a true “give me only changes since this time” option for these queries, so the connector fetches pages and filters out older records itself.

The connector has a small GraphQL helper that posts queries to monday and treats GraphQL errors as a skipped stream rather than a half-successful sync. It has shared paging logic for top-level lists, special item paging because monday uses a different cursor system for board items, and special activity-log logic because logs must be fetched per board. Finally, flatten turns monday’s nested records into simpler fields like body, author, parent_external_id, and api_url so downstream code can treat different sources more consistently.

#### Function details

##### `_extract_person_ids`  (lines 70–98)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: This helper finds the monday user IDs assigned to an item through monday’s “people” columns. It exists because monday stores assignees inside board-specific column data, not in one stable top-level field.

**Data flow**: It receives the raw column_values value from a monday item. It scans only columns marked as people columns, reads their stored JSON data, keeps entries whose kind is person, and converts their IDs to strings. It returns a flat list of person IDs; if the input is missing, malformed, or about teams instead of people, it quietly returns an empty list or skips bad parts.

**Call relations**: When MondayConnector._items reads item records, it calls this helper for each item so the item gets a simple assignee_ids field. Internally this helper uses JSON parsing because monday often stores the people-column value as a JSON string.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 106–118)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This is the connector’s common doorway to monday’s GraphQL API. It sends one query, checks whether monday reported a GraphQL-level error, and returns the useful data section.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional variables. It posts that request to monday’s API endpoint, looks for an errors field in the response, and raises StreamSkipped if monday refused or could not answer the query. If the response has a data object, it returns that object; otherwise it returns an empty dictionary.

**Call relations**: All higher-level readers depend on this function instead of posting directly to monday. MondayConnector._paged_root, MondayConnector._items, MondayConnector._activity_logs, and MondayConnector.paginate call it whenever they need data, so error handling stays consistent across every stream.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, paginate).


##### `MondayConnector._paged_root`  (lines 120–143)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads ordinary monday collections that use page numbers, such as users, workspaces, boards, or updates. It is like turning the pages of a book until there are no more pages with records.

**Data flow**: It receives a field name to query, the GraphQL field selection to request, and optionally a saved cursor timestamp plus the record field to compare against it. It asks monday for 100 records at a time, turns missing or invalid lists into an empty list, filters out old records when a cursor is supplied, and yields each non-empty page. When a page has no records after filtering, it stops.

**Call relations**: MondayConnector.paginate uses this for several stream types that follow monday’s simple page-number pattern. MondayConnector._boards also uses it to get the full board list, which is then needed before fetching per-board items and activity logs.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, paginate); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 145–156)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper collects all boards from monday so other parts of the connector can work board by board. Items and activity logs cannot be read globally in the same simple way, so the connector first needs the board list.

**Data flow**: It receives an HTTP client. It asks _paged_root for every page of boards with key board details and workspace information, appends each page into one list, and returns the full list of board records.

**Call relations**: MondayConnector._items and MondayConnector._activity_logs call this before they fan out across boards. It delegates the actual page-by-page GraphQL work to MondayConnector._paged_root.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 158–217)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads item records from every monday board, including basic item details, board and group context, and assignee IDs. It is separate from simple paging because monday uses a special cursor system for item pages inside boards.

**Data flow**: It receives an HTTP client and an optional cursor timestamp from a previous sync. It first gets all boards, then for each board asks monday for the first page of items. If monday returns a next-page cursor, it keeps asking for following pages. For every item, it extracts assignee IDs from column values, filters out records whose updated_at value is not newer than the saved cursor, and yields each non-empty batch of items.

**Call relations**: MondayConnector.paginate calls this when the requested stream is items. This function relies on MondayConnector._boards to know which boards to inspect, MondayConnector._graphql to talk to monday, _extract_person_ids to simplify assignee data, and small safety helpers to treat missing dictionaries or lists as empty values.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (paginate); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 219–247)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads recent activity-log entries from each monday board. Activity logs describe events that happened on boards, so each log is also tagged with the board it came from.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It gets the full board list, queries each board for up to 100 activity logs, copies each log while adding board_id, filters out logs whose created_at value is not newer than the cursor, and yields the remaining records when there are any.

**Call relations**: MondayConnector.paginate calls this for the activity_logs stream. It uses MondayConnector._boards to decide which boards to query and MondayConnector._graphql for each board-specific GraphQL request.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (paginate); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 249–323)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main read dispatcher for monday streams. Given a stream name, it chooses the right GraphQL query or helper and yields records in pages to the source framework.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks the stream name, runs the matching query path, and yields lists of records as they arrive. Some streams use the shared page-number helper, some use one-off GraphQL queries, and items or activity logs use their special helper methods. If the stream is unknown, or if monday returns an authorization-style HTTP refusal, it raises StreamSkipped so the wider sync can record that this stream could not be read instead of treating it as successful.

**Call relations**: The source framework calls this when it wants data for one monday stream. This function then hands work to MondayConnector._paged_root, MondayConnector._items, MondayConnector._activity_logs, or MondayConnector._graphql depending on the stream. It is the central traffic director for reading monday data.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _graphql, _items, _paged_root).


##### `MondayConnector.flatten`  (lines 325–366)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This converts raw monday records into a more consistent shape for downstream storage and search. It keeps the original fields but adds or standardizes common fields such as name, body, author, created_at, status, and parent_external_id.

**Data flow**: It receives one raw record and the stream it belongs to. Based on the stream name, it copies the record and fills in normalized fields from monday-specific places, such as using text_body as an update body or user_id as an activity-log author. It returns the normalized dictionary and does not make network calls or change external state.

**Call relations**: After paginate has yielded raw records, the connector framework can call flatten before saving or indexing them. For update records, it uses a safe dictionary helper to read creator information even when monday leaves that nested object missing or malformed.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/wrike.py`

`io_transport` · `source sync`

Wrike is an external work-management service, so this file acts like an adapter between Wrike’s API and this project’s source-sync framework. Without it, the system would not know which Wrike objects can be read, where to fetch them, how to move through multiple pages of API results, or how to turn Wrike’s field names into the more common fields used elsewhere.

The file first declares the Wrike streams the connector supports. A stream is one kind of thing to sync, such as tasks or folders. Some streams also have an update timestamp, which lets the connector skip older records during incremental syncs. Wrike does not provide a dependable “only give me things changed since this time” filter, so the connector fetches pages and then locally drops records whose `updatedDate` is not newer than the saved cursor.

`WrikeConnector` supplies the API base address, asks the shared REST connector to follow Wrike’s `nextPageToken` pagination, and treats permission failures as a skipped stream rather than crashing the whole sync. It also reshapes some records: for example, contacts get a readable full name and email, tasks get a status and due date, and comments get a body and parent task ID. This makes Wrike data easier for downstream code to understand.

#### Function details

##### `_profile_email`  (lines 54–64)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper looks inside a Wrike contact record and finds the first usable email address from its profiles. It exists because Wrike stores email addresses inside a nested list rather than as one simple top-level field.

**Data flow**: It receives one contact-like dictionary. It checks whether the `profiles` field is a list, then walks through each profile that is itself a dictionary. If it finds a non-empty string in the `email` field, it returns that email; if not, it returns `None`.

**Call relations**: This is used by `WrikeConnector.flatten` when it is preparing contact records. The larger connector does not make every caller understand Wrike’s nested profile shape; it asks this small helper to pull out the email in one place.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 72–96)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous function fetches records from Wrike one page at a time for a requested stream. It also skips unsupported streams and turns Wrike permission errors into a clear “this stream was skipped” signal.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor value. First it checks whether the stream is one this connector can actually run. Then it asks the shared REST paging helper to call the matching Wrike endpoint and follow `nextPageToken` values until there are no more pages. If a cursor is present and the stream has an update field, it keeps only records newer than that cursor. It yields each non-empty batch of records. If Wrike replies with 401 or 403, meaning unauthorized or forbidden, it raises `StreamSkipped` with an explanation instead of returning records.

**Call relations**: During a sync, the source framework calls this method to obtain raw Wrike records for a stream. The method delegates the low-level page-following work to the inherited cursor-page helper, and it uses `StreamSkipped` when a stream is not implemented or when Wrike refuses access because the credential lacks permission.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 98–136)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function converts Wrike’s raw records into a friendlier shape for the rest of the system. It keeps the original data but adds common fields like `name`, `created_at`, `body`, `author`, or `due_date` where they make sense.

**Data flow**: It receives one Wrike record and the stream it came from. For contacts, it builds a display name from first and last name, finds an email through `_profile_email`, and copies the creation date. For folders, it uses the title as the name and adds a direct API URL. For tasks, it reads nested date information with `dict_or_empty`, chooses a status, and adds a due date. For comments, it maps Wrike’s text and author fields into simpler names and links the comment to its task. Streams without special rules are returned unchanged.

**Call relations**: After records have been fetched by the connector, the source framework can call this method to normalize each one before downstream storage or indexing. Inside this cleanup step, it hands contact email extraction to `_profile_email` and uses `dict_or_empty` so task date handling is safe even when Wrike omits or reshapes the `dates` field.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).


### Knowledge and collaboration spaces
Connectors that transform team documentation, workspace pages, and conversations into readable synced content.

### `extensions/sources/ufo_ext_sources/providers/confluence.py`

`io_transport` · `source sync`

Confluence does not store page bodies as simple text. It stores them as storage-format XHTML, which is a structured HTML-like format full of tags, attributes, and Confluence-specific macro elements. If the system saved that raw format, a human searching later would see noisy markup instead of the page as readers understand it. This file solves that by fetching Confluence records, shaping them into consistent fields, and rendering their bodies as clean prose.

The connector first asks Atlassian which Confluence sites the current OAuth grant can access. An OAuth grant is a permission token, but this connector does not hold the token itself; the wider runner supplies it through the auth layer. For each reachable site, the connector reads the requested stream, such as pages or comments, through Atlassian’s API. It pages through results in small batches and, for streams that support incremental syncing, filters out records older than the saved cursor value.

Each record is then flattened into fields the rest of the system expects, such as title, body, URL, creation time, and parent page. Record IDs are prefixed with the Confluence site ID so two different sites cannot accidentally produce the same reference. Finally, page-like content is rendered as a readable heading plus plain text. If Confluence refuses access with a permission error, the stream is marked as skipped instead of failing the whole run.

#### Function details

##### `_body_text`  (lines 114–116)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: This helper finds the readable body field inside a Confluence record. It checks Confluence’s stored body first, then falls back to the view body, and only returns it if it is real non-empty text.

**Data flow**: It receives one record as a dictionary-like object. It looks inside nested fields such as body.storage.value and body.view.value. If it finds a non-empty string, it returns that string; otherwise it returns nothing.

**Call relations**: ConfluenceConnector.flatten uses this when it prepares pages, blog posts, and comments for the rest of the system. It relies on the shared get_path helper so it can safely read deeply nested fields without crashing when a part is missing.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 124–150)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Confluence streams. It finds every Confluence site the grant can reach, asks each site for the requested kind of data, and yields batches of records for syncing.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous run. It chooses the right Confluence API path, asks for accessible sites, loops through each site, fetches batches of records, attaches site information to those records, and yields them onward. If Confluence says access is forbidden or unauthorized, it turns that into a skip notice rather than a hard failure.

**Call relations**: The sync framework calls this when it needs records for a Confluence stream. This method calls _sites to learn which sites are available, then calls _offset_results to walk through each site’s API results. Before yielding a batch, it uses with_context to add the site ID and site URL so later steps can build safe IDs and links.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 152–156)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Confluence sites the current permission grant can access. It matters because one user grant may cover more than one Confluence site.

**Data flow**: It receives an HTTP client. It sends a request to Atlassian’s accessible-resources endpoint, reads the JSON response, and returns it as a list of site records. If the response is empty or not a list-like value, it returns an empty list.

**Call relations**: ConfluenceConnector.paginate calls this at the start of reading a stream. The returned site records supply the cloud_id used to build the site-specific Confluence API paths.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 158–182)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: This walks through one Confluence collection page by page. It also performs client-side incremental filtering, because Confluence does not provide a simple server-side 'only records since this time' option here.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, and optional cursor information. It requests results with a start position and fixed page size, pulls the records out of the response, removes records that are not newer than the cursor when needed, and yields any remaining records. It continues while Confluence says there is a next page.

**Call relations**: ConfluenceConnector.paginate calls this once for each accessible site and stream. Inside, it uses records_at to pull the results list from the API response and get_path to read nested cursor and pagination fields safely.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 184–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes raw Confluence API records into the common fields the sync system expects. It gives pages, blog posts, comments, and spaces clearer names, body text, URLs, timestamps, and stable IDs.

**Data flow**: It receives one raw record and the stream it came from. Depending on the stream type, it copies the original data and adds or normalizes fields such as title, kind, url, body, author, created_at, updated_at, and parent_external_id. It prefixes most primary keys with the Confluence site ID, and it lifts nested cursor values to the flat key used by the sync watermark.

**Call relations**: This is used after records have been fetched by pagination and before they are stored or rendered. It calls _body_text for page-like bodies and get_path for nested fields such as web links, version timestamps, and authors.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 234–250)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a flattened Confluence record into a human-readable title and text body. It replaces raw Confluence storage HTML with plain prose that is useful for search and recall.

**Data flow**: It receives a record and its stream description. For pages, blog posts, comments, and spaces, it chooses a title, extracts readable text from the body or description, builds a heading, and returns both the title and final text. For other streams, it lets the base connector render them in the usual way.

**Call relations**: The wider source system calls this when it needs the text representation of a synced record. It uses _StorageTextExtractor.extract to clean Confluence HTML-like content and _str to safely treat only real strings as titles.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 259–261)

```
def __init__(self) -> None
```

**Purpose**: This prepares a small HTML text reader used to turn Confluence’s storage-format XHTML into plain text. It starts with an empty list of text pieces.

**Data flow**: It receives no outside data beyond the new object being created. It initializes the underlying HTML parser with automatic character unescaping and creates an empty internal parts list. The result is a parser ready to receive raw Confluence body markup.

**Call relations**: _StorageTextExtractor.extract creates an instance of this class whenever it needs to clean a raw body string. The parser’s later callback methods fill the parts list as the markup is read.


##### `_StorageTextExtractor.extract`  (lines 264–269)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: This is the easy entry point for converting Confluence storage HTML into readable plain text. Callers use it when they have a raw body or description and want the words without the markup.

**Data flow**: It receives any value. If the value is not a non-empty string, it returns an empty string. If it is text, it feeds that text into a new parser and returns the parser’s cleaned final text.

**Call relations**: ConfluenceConnector.render calls this for page, blog post, comment, and space content. It creates the parser, which then uses handle_data, handle_starttag, handle_endtag, and _text as the HTML is processed.


##### `_StorageTextExtractor.handle_data`  (lines 271–272)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This records actual human-readable characters found inside the Confluence markup. It keeps the words and ignores the surrounding tags.

**Data flow**: It receives a chunk of text from the HTML parser. It appends that chunk to the parser’s internal list. Nothing is returned, but the parser’s collected text grows.

**Call relations**: The Python HTML parser calls this automatically while _StorageTextExtractor.extract feeds it raw markup. The collected pieces are later joined and cleaned by _StorageTextExtractor._text.


##### `_StorageTextExtractor.handle_starttag`  (lines 274–276)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This notices the start of block-like HTML tags and inserts a line break marker. That helps paragraphs, headings, table cells, and list items stay readable instead of running together.

**Data flow**: It receives the tag name and its attributes. If the tag is one of the known block tags, it appends a newline marker to the internal text parts. It ignores attributes and returns nothing.

**Call relations**: The HTML parser calls this during _StorageTextExtractor.extract. Its newline markers are later cleaned and compacted by _StorageTextExtractor._text, giving the final plain text sensible line breaks.


##### `_StorageTextExtractor.handle_endtag`  (lines 278–280)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This notices the end of block-like HTML tags and inserts a line break marker. It helps separate sections of text in the final output.

**Data flow**: It receives the closing tag name. If the tag is one of the known block tags, it appends a newline marker to the internal text parts. It returns nothing, but it changes the parser’s accumulated parts.

**Call relations**: The HTML parser calls this while processing markup in _StorageTextExtractor.extract. Together with handle_starttag, it gives _StorageTextExtractor._text enough boundaries to format the final plain text cleanly.


##### `_StorageTextExtractor._text`  (lines 282–285)

```
def _text(self) -> str
```

**Purpose**: This turns the parser’s collected pieces into the final clean text. It removes extra spacing and blank lines while keeping useful line breaks.

**Data flow**: It reads the parser’s internal list of text and newline markers. It joins them, splits them into lines, collapses repeated spaces inside each line, drops empty lines, and returns a trimmed plain-text string.

**Call relations**: _StorageTextExtractor.extract calls this after feeding the raw Confluence markup into the parser. It is the final cleanup step after handle_data, handle_starttag, and handle_endtag have collected the pieces.


##### `_str`  (lines 288–289)

```
def _str(value: Any) -> str
```

**Purpose**: This small safety helper returns a value only if it is actually a string. It prevents non-text values from accidentally becoming titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: ConfluenceConnector.render uses this when choosing titles from records. That keeps rendering simple and avoids treating missing or unexpected values as readable text.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/notion.py`

`io_transport` · `source sync`

Notion stores information in nested shapes: pages have properties, page bodies are made of blocks, blocks can contain more blocks, and text is often split into small “rich text” pieces. This connector is the translator between that world and the rest of the system. It knows which Notion streams exist, how to ask Notion for each one, how to follow Notion’s pagination cursors, and how to turn the results into prose a person might actually recognize.

The file reads data only. It creates an HTTP client with the required Notion API version header, then routes each requested stream to the right reader. Users come from a simple collection endpoint. Pages and data sources come from Notion search, sorted by edit time so later syncs can skip older records. Blocks are gathered by first finding pages, then walking each page’s block tree, like opening folders inside folders, while stopping at a safe depth and not descending into child pages or databases that are separate records. Comments are fetched page by page.

A key detail is graceful skipping. If Notion says the integration is not allowed to read something, the connector reports that stream as skipped rather than crashing the whole sync. The render helpers then extract titles, property values, block text, to-do checkboxes, comment bodies, and user emails into clean readable text.

#### Function details

##### `NotionConnector._make_client`  (lines 80–83)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Notion. It adds the required Notion API version header so Notion knows which version of its rules the connector expects.

**Data flow**: It receives a base URL and a credential supplied by the wider source system. It asks the parent REST connector to build the normal client, adds the Notion-Version header, and returns that ready-to-use client.

**Call relations**: This is part of the connector setup before any Notion request is made. Later reader functions use the client it prepares, so every call to Notion carries the expected version information.


##### `NotionConnector.paginate`  (lines 85–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading Notion streams. Given a stream name such as pages, users, blocks, or comments, it chooses the right fetching method and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It dispatches to the matching reader, yields each page of records it gets back, and turns Notion permission errors into a clean stream skip instead of a hard failure.

**Call relations**: The sync engine calls this when it wants records for one Notion stream. It hands off to _collection for users, _search for pages and data sources, _comments for comments, and _blocks for page body blocks; if a stream is unknown or forbidden, it raises StreamSkipped so the larger run can continue.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 117–140)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Notion for either pages or data sources and returns them in edit-time order. It also applies the saved sync cursor itself because Notion search does not provide a true “only since this time” option.

**Data flow**: It receives the client, the object type to search for, and an optional last-seen edit time. It repeatedly posts to Notion’s search endpoint, pulls the results list safely, filters out records at or before the cursor, yields non-empty batches, and follows Notion’s next cursor until there are no more pages.

**Call relations**: paginate uses this directly for pages and data sources. _blocks and _comments also use it first to find the pages whose block trees or comments should be visited.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 142–152)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers the body blocks for all Notion pages. It exists because a page’s readable content is not stored on the page record itself; it lives in a separate tree of blocks.

**Data flow**: It receives the client and an optional block edit-time cursor. It first searches all pages, takes each page id, then asks _block_children to walk that page’s block tree and yield batches of blocks that are new enough.

**Call relations**: paginate calls this for the blocks stream. It uses _search to find page roots, then delegates the recursive block walking to _block_children.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 154–173)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through the children of one Notion block or page and returns readable block records. It carefully avoids endless or overly deep walks by stopping at a maximum depth and by not descending into special block types that are treated as their own records.

**Data flow**: It receives a block id, the current depth, and an optional last-seen edit time. It fetches one page of child blocks at a time, yields only those newer than the cursor, then looks for child blocks that themselves have children and recursively visits them when safe.

**Call relations**: _blocks starts this process for each page id. During the walk, this function calls _collection to fetch each block’s children and calls itself again for nested children.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 175–191)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches comments attached to Notion pages. It works page by page because Notion comments are requested for a specific block or page id.

**Data flow**: It receives the client and an optional comment creation-time cursor. It searches all pages, skips any page without a usable id, fetches comments for each page, filters out comments at or before the cursor, and yields non-empty batches.

**Call relations**: paginate calls this for the comments stream. It uses _search to discover pages, then uses _collection to page through the comments endpoint for each page.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 193–207)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared helper for Notion endpoints that return a list of results with a next cursor. It prevents every reader from having to repeat the same pagination pattern.

**Data flow**: It receives the client, an API path, and optional query parameters. It asks the parent REST machinery to fetch pages using Notion’s result and cursor field names, then yields each batch of records it receives.

**Call relations**: paginate uses it for users. _block_children uses it for block children, and _comments uses it for page comments, so those functions can focus on their own filtering and traversal rules.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 209–231)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns raw Notion API records into a title and readable text. That matters because Notion’s raw data is structured for machines, while recall and search need text closer to what a human sees in Notion.

**Data flow**: It receives one record and the stream it came from. Depending on the stream, it extracts a page title and properties, a data source title and description, a block’s text, a comment body, or a user name and email; then it builds a heading and body string and returns both the short title and full rendered text.

**Call relations**: The wider source system calls render after records are fetched. This function relies on small text-extraction helpers such as _page_title, _properties_text, _block_text, _rich_text_text, _str, and _user_text; unknown streams fall back to the parent connector’s rendering.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 234–235)

```
def _str(value: Any) -> str
```

**Purpose**: This safely returns a value only when it is already text. It avoids accidental display of non-text data where a clean string is expected.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: Several render helpers use this as a small safety gate when reading optional Notion fields such as names, emails, URLs, and titles.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 238–246)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: This combines Notion rich-text pieces into normal readable text. Notion often stores one visible sentence as a list of small runs, and this helper stitches their plain_text fields together.

**Data flow**: It receives a value that should be a list of rich-text parts. It ignores anything that is not a proper text part, joins the plain text pieces, trims surrounding whitespace, and returns the final string.

**Call relations**: render and the helpers for pages, properties, and blocks call this whenever they need to turn Notion rich text into ordinary prose.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 249–258)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: This finds the human title of a Notion page. Page titles are stored inside the page’s properties rather than in a single simple title field.

**Data flow**: It receives a page record, looks inside its properties, finds the property whose type is title, extracts its rich text, and returns the first non-empty title it finds. If the page shape is unexpected or no title is present, it returns an empty string.

**Call relations**: render calls this when rendering page records. It uses _rich_text_text to convert Notion’s title pieces into plain text.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 261–270)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion page’s visible properties into simple lines of text. It makes database-like fields such as status, dates, people, and emails searchable and readable.

**Data flow**: It receives a page record, reads its properties dictionary, converts each supported property through _property_text, and joins the non-empty results as lines like “Name: value”. If there are no usable properties, it returns an empty string.

**Call relations**: render calls this for page records after finding the title. It delegates the details of each property type to _property_text.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 273–292)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from one Notion page property. It understands the common property types that people see in Notion, such as title, select, status, people, date, number, URL, email, phone number, and checkbox.

**Data flow**: It receives one property dictionary, checks its type, then pulls the value from the matching field. It converts rich text through _rich_text_text, names through _str, lists into comma-separated names, and simple values into strings; unsupported or malformed properties become empty text.

**Call relations**: _properties_text calls this for each property on a page. It uses _rich_text_text and _str so the higher-level page rendering stays clean and consistent.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 295–305)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: This extracts the readable content from a Notion block. Blocks are the building blocks of page bodies, such as paragraphs, headings, list items, code, child page links, or to-do items.

**Data flow**: It receives a block record, finds the content section named by the block’s type, and returns the visible text. For child page or child database blocks it returns the title; for to-do blocks it prefixes the text with [x] or [ ] to show whether the task is checked.

**Call relations**: render calls this for block records. It uses _rich_text_text for ordinary block text and _str for title fields on child page or database blocks.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 308–311)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: This renders a Notion user as simple contact text. It includes the person’s name and, when available, their email address.

**Data flow**: It receives a user record, reads the top-level name and the nested person email field, keeps only real strings, joins the available pieces with a line break, and returns the result.

**Call relations**: render calls this for user records. It uses _str to avoid treating missing or non-text fields as displayable text.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/slack.py`

`io_transport` · `source sync and pagination`

Slack does not hand over a whole workspace in one neat bundle. It returns small pages of users, channels, and messages, often with a cursor that means “ask again from here.” This file is the Slack-specific adapter that knows how to keep asking, how to understand Slack timestamps, and how to turn Slack’s nested response objects into simpler rows.

The main flow starts with a requested stream, such as users, conversations, messages, conversation threads, or participants. For users and conversations, the connector repeatedly calls Slack list endpoints and yields cleaned pages. For message-like streams, it first builds a user lookup table and a list of readable, non-archived conversations. Then it walks each channel separately, using per-channel time bounds so a sync can resume without rereading everything forever.

A key idea here is “partition walking”: each Slack channel is treated like its own shelf of messages. The sync walks one shelf at a time, keeping track of the newest and oldest Slack message timestamps it saw. If Slack refuses access to a whole stream or one channel, the code turns that into a skip rather than crashing the entire run when that is safe.

The file also contains small translators: Slack timestamps become ISO date strings, user profiles become flat user rows, messages become searchable message rows, and thread or participant records are derived from the same raw message page.

#### Function details

##### `SlackApiError.__init__`  (lines 93–97)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: Creates a Slack-specific error with a readable message. It keeps both Slack’s error code and any missing permission information so higher-level code can decide whether to skip or fail.

**Data flow**: It receives Slack’s error text and, optionally, the permission Slack says was needed. It builds an exception message like “slack: missing_scope (needed: channels:read)” and stores the raw details on the error object. The result is an exception ready to be raised and inspected later.

**Call relations**: The Slack response checker uses this when Slack replies with ok=false. Later, functions such as channel history reading and top-level enumeration catch this error and decide whether the problem means “skip this stream or channel” or “stop because something unexpected happened.”

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate_source`  (lines 105–120)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides the standard source-pagination entry used by the wider sync framework. It simply forwards the request to the Slack connector’s real pagination logic.

**Data flow**: It receives an HTTP client, the stream to read, a saved cursor, the current integration’s own Slack user ID, and an optional backfill floor date. It passes those values unchanged into paginate. What comes out is the same async stream of record pages or stream pages produced by paginate.

**Call relations**: The broader source system calls this method when it wants Slack data. This method hands control to SlackConnector.paginate so the rest of the file can choose the correct Slack API path for the requested stream.

*Call graph*: calls 1 internal fn (paginate).


##### `SlackConnector.paginate`  (lines 122–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None=None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]
```

**Purpose**: Chooses how to read each Slack stream. It knows that users and conversations are simple lists, while messages, threads, and participants must be read channel by channel.

**Data flow**: It receives the stream name, sync cursor, optional self-user ID, and optional backfill date. For users or conversations, it yields pages directly from the matching iterator. For message-related streams, it first reads all users, then all conversations, filters out archived channels, and creates a per-channel walk that yields pages with progress markers. If the stream is unknown, it reports that the stream is skipped.

**Call relations**: This is the central dispatcher behind paginate_source. It calls iter_users, iter_conversations, user_index, _slack_ts, and creates a PartitionWalk so channel history can be resumed safely. Its small nested helpers provide the channel list and per-channel page reader to that walk.

*Call graph*: calls 5 internal fn (__init__, iter_conversations, iter_users, user_index, _slack_ts); called by 1 (paginate_source); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 148–150)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: Supplies the list of channel IDs that should be walked for message-related streams. Each channel is treated as a separate partition, like a separate folder to scan.

**Data flow**: It reads the already-built dictionary of active Slack conversations. It yields one channel ID at a time. It does not return a final collection; it streams the IDs as the partition walker asks for them.

**Call relations**: This helper is created inside paginate after conversations have been collected. PartitionWalk calls it to know which channels need message history pages.


##### `SlackConnector.paginate.channel_pages`  (lines 152–160)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Creates the page reader for one Slack channel. It connects the generic partition-walking machinery to Slack’s conversations.history endpoint.

**Data flow**: It receives a channel ID and a time bound from the partition walker. It looks up the full conversation details and passes the client, stream, conversation, bound, user lookup, and self-user ID into _channel_pages. The output is an async iterator of walk pages for that one channel.

**Call relations**: This helper is passed into PartitionWalk by paginate. Whenever the walk wants records from a particular channel, it calls this helper, which delegates the Slack-specific work to _channel_pages.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 177–193)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Slack workspace users page by page and turns each user into a simpler record. This is used both when syncing the users stream and when messages need user details for names and emails.

**Data flow**: It starts with no Slack cursor and asks /api/users.list for a page. It flattens valid member objects into user rows, yields a page if there are users, then reads Slack’s next cursor and repeats until there is no cursor. The output is a sequence of user-record lists.

**Call relations**: paginate calls this directly for the users stream. user_index also calls it to build a lookup table before message pages are flattened. It relies on _enumerate for safe Slack API listing, _flatten_user for record shaping, and _next_cursor to continue through Slack pages.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 195–235)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Slack conversations, including channels and direct-message-like spaces, and converts them into normalized conversation records. It keeps enough metadata to later decide which conversations can be scanned for messages.

**Data flow**: It repeatedly calls /api/conversations.list with Slack’s paging cursor. For each valid channel object, it builds a flat record with identifiers, name, type, privacy flags, archive status, creation time, topic, purpose, and member count. It yields non-empty pages until Slack stops providing a next cursor.

**Call relations**: paginate uses this for the conversations stream and also before reading message-related streams. It calls _enumerate to contact Slack, _conversation_type to label the kind of conversation, _nested_value to safely read nested topic and purpose fields, _unix_to_iso for creation dates, and _next_cursor for pagination.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 237–244)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a dictionary of users keyed by Slack user ID. Message conversion uses this to attach names and emails to message senders.

**Data flow**: It reads all user pages from iter_users. For each user with a string ID, it stores that user under the ID in a dictionary. It returns the completed lookup table.

**Call relations**: paginate calls this before scanning channel messages. The resulting lookup is passed down into _channel_pages and then _message_page so message and participant records can include human-friendly sender details.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 246–298)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]], self_user_id: str | None) -> AsyncI
```

**Purpose**: Reads one Slack conversation’s message history within the time window requested by the partition walker. It also turns permission problems on a single channel into a skipped partition when appropriate.

**Data flow**: It receives one conversation, a stream type, a time bound, a user lookup, and the integration’s own user ID. It builds Slack conversations.history parameters, including cursor and timestamp limits. It posts to Slack, filters raw messages that have timestamps, turns each page into a WalkPage through _message_page, and repeats until Slack has no next cursor. If Slack refuses that channel for a known reason, it raises a partition skip instead of failing the whole sync.

**Call relations**: The channel_pages helper inside paginate calls this for each channel chosen by PartitionWalk. It calls _slack_post to talk to Slack, _next_cursor to continue paging, and _message_page to derive records for messages, threads, or participants.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 300–348)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]], self_user_id: str | None) -> WalkPage
```

**Purpose**: Turns one raw Slack history page into the exact record type the requested stream needs. The same Slack message page can produce message rows, thread rows, or participant rows.

**Data flow**: It receives the stream, conversation details, raw Slack messages, users, and self-user ID. It skips deleted-message events except for collecting delete IDs, flattens normal messages, derives thread records when a message belongs to a thread, and derives sender participant records. It also calculates the newest and oldest Slack timestamps in the raw page. It returns a WalkPage containing the selected records and the timestamp span; for the messages stream, it also includes message IDs that should be deleted.

**Call relations**: _channel_pages calls this after receiving a Slack history page. It delegates record shaping to _flatten_message, _conversation_thread_from_message, and _participant_for_message, then packages the result in a WalkPage for PartitionWalk to track progress.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 350–370)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Performs a top-level Slack list request and translates missing-permission refusals into a clean stream skip. This lets the sync record “we cannot read this stream” instead of treating a permissions gap as a system failure.

**Data flow**: It receives an HTTP client, Slack API path, and query parameters. It calls _slack_get. If Slack reports a known permission refusal, or the HTTP response is a known refusal status, it raises StreamSkipped with a clear explanation. Otherwise, it returns Slack’s response data or lets unexpected errors continue upward.

**Call relations**: iter_users and iter_conversations call this for their listing endpoints. It sits between those higher-level iterators and _slack_get, adding Slack-specific permission interpretation.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 372–375)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Runs a Slack GET request and checks Slack’s own success flag. Slack can return an HTTP success while still saying the API call failed, so this extra check is needed.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It calls the connector’s lower-level _get method, then passes the returned JSON-like data through _ok_or_raise. It returns the data only if Slack marked it successful.

**Call relations**: _enumerate calls this for Slack list endpoints. It hands response validation to _ok_or_raise, which may raise SlackApiError for callers to interpret.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 377–380)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Runs a Slack POST request and checks Slack’s own success flag. It is used for Slack calls where parameters are sent in the request body.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It calls the connector’s lower-level _post method, then validates the returned data with _ok_or_raise. It returns the successful Slack response or raises a SlackApiError.

**Call relations**: _channel_pages calls this when reading conversations.history. If Slack says the channel cannot be read, the SlackApiError raised here is caught there and may become a skipped channel.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 383–388)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Checks Slack’s ok field and turns Slack API failures into a typed exception. This gives the rest of the file one consistent way to notice Slack-level errors.

**Data flow**: It receives a dictionary returned by Slack. If ok is false, it reads the error code and optional needed permission, then raises SlackApiError. If Slack did not report failure, it returns the original data unchanged.

**Call relations**: _slack_get and _slack_post both use this immediately after lower-level HTTP calls. The errors it raises are later interpreted by _enumerate and _channel_pages.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 391–396)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: Extracts Slack’s “next page” marker from a response. Without this, the connector would only read the first page of large workspaces.

**Data flow**: It receives Slack response data. It looks inside response_metadata for next_cursor and returns it only if it is a non-empty string. If there is no usable cursor, it returns None, meaning pagination is finished.

**Call relations**: iter_users, iter_conversations, and _channel_pages call this after each Slack page. Its result decides whether those loops ask Slack for another page or stop.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 399–406)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: Converts Slack-style Unix timestamps into ISO date strings. ISO strings are easier for the rest of the system to store, compare, and display.

**Data flow**: It receives any value that might represent seconds since 1970. It rejects booleans and values that cannot be converted to a number. If conversion works, it returns a UTC ISO-formatted timestamp string; otherwise it returns None.

**Call relations**: iter_conversations uses this for conversation creation times, and _flatten_user uses it for user update times.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts`  (lines 409–421)

```
def _slack_ts(value: datetime | None) -> str | None
```

**Purpose**: Converts a Python datetime into Slack’s timestamp string format for history bounds. It pads the value so string comparisons behave correctly for old dates.

**Data flow**: It receives an optional datetime. If there is no date, it returns None. If the date is before the Unix epoch, it also returns None. Otherwise, it returns a fixed-width timestamp string with six decimal places, matching Slack’s ordering style.

**Call relations**: paginate uses this when creating the floor for PartitionWalk during backfill. The resulting string tells the per-channel walk how far back it is allowed to descend.

*Call graph*: called by 1 (paginate); 1 external calls (timestamp).


##### `_slack_ts_to_iso`  (lines 424–430)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: Converts Slack message timestamps, such as 1712345678.123456, into UTC ISO date strings. This turns Slack’s ordering value into a normal date for records.

**Data flow**: It receives a Slack timestamp string or None. If the value is empty or cannot be parsed as a number, it returns None. Otherwise, it converts the timestamp to a UTC ISO string.

**Call relations**: _flatten_message uses this for message sent times. _conversation_thread_from_message uses it for thread creation, update, and last-message times.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 433–460)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns Slack’s nested user object into a simpler user record. It picks useful identity fields such as display name, email, phone, title, timezone, and deletion status.

**Data flow**: It receives one raw Slack user dictionary. It safely reads the profile object, normalizes the email to lowercase when present, chooses the best available display and real names, converts the update timestamp, and returns a flat dictionary keyed by predictable field names.

**Call relations**: iter_users calls this for each valid Slack member. It uses _first_text to choose the first useful name and _unix_to_iso to format the updated time.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 463–504)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]], self_user_id: str | None) -> dict[str, Any] | None
```

**Purpose**: Turns one raw Slack message into a normalized message row. It also filters out messages sent by the integration’s own Slack user so the sync does not ingest its own activity.

**Data flow**: It receives a raw message, conversation details, user lookup, and optional self-user ID. It checks that the message and channel have valid IDs, skips self-authored messages, looks up the sender, chooses the thread timestamp, builds stable IDs, formats the sent time, creates a short snippet, and returns a message dictionary. If required IDs are missing or the message should be ignored, it returns None.

**Call relations**: _message_page calls this for each non-deleted raw Slack message. It uses _slack_ts_to_iso for dates, _snippet for preview text, and _first_text to choose the best sender handle.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 507–537)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Derives a thread record from a Slack message when that message is a thread root or a reply. This lets the system sync thread-level conversations separately from individual messages.

**Data flow**: It receives a flattened message plus the original raw message and conversation. It checks whether the message starts a thread with replies or belongs to an existing thread. If not, it returns None. If it is part of a thread, it builds a thread record with title, snippet, privacy/archive flags, message count, participant count, and created/updated times.

**Call relations**: _message_page calls this after flattening each message. It uses _slack_ts_to_iso to format Slack timestamps for thread activity dates.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 540–560)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: Creates a participant record for the sender of a message. This links a person or bot-like sender to the message and thread they contributed to.

**Data flow**: It receives a flattened message and the user lookup. It finds the sender user when possible, chooses an email or Slack user ID as the handle, and returns a participant dictionary with role “from,” IDs, display name, and creation time. If no usable handle exists, it returns None.

**Call relations**: _message_page calls this for each flattened message when producing participant records. It uses _first_text to choose the best sender handle.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 563–570)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: Labels a Slack conversation as a direct message, multi-person direct message, private channel, or public channel. This hides Slack’s multiple boolean flags behind one readable category.

**Data flow**: It receives a raw Slack conversation dictionary. It checks Slack’s type flags in priority order and returns a string category. The output is one normalized conversation type.

**Call relations**: iter_conversations calls this while building conversation records so downstream code does not need to understand Slack’s separate is_im, is_mpim, is_group, and is_private flags.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 573–579)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: Safely reads a value from inside nested dictionaries. It avoids crashes when Slack leaves out an expected object such as topic or purpose.

**Data flow**: It receives a dictionary and a path of keys. It walks through the keys one by one. If the current value is not a dictionary before the path ends, it returns None; otherwise it returns the final nested value.

**Call relations**: iter_conversations uses this to read fields like topic.value and purpose.value from Slack conversation objects.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 582–586)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: Chooses the first non-empty text value from several options. It is a small fallback helper for names and handles.

**Data flow**: It receives any number of candidate values. It returns the first value that is a string and still has text after trimming spaces. If none qualify, it returns None.

**Call relations**: _flatten_user uses this to choose display and real names. _flatten_message and _participant_for_message use it to choose the best sender handle.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 589–593)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: Creates a short, tidy preview of a message’s text. This is useful for search results or thread titles where the full message may be too long.

**Data flow**: It receives optional text. If the text is missing, it returns None. Otherwise, it collapses repeated whitespace into single spaces and cuts the result to the configured snippet length. Empty text after cleanup becomes None.

**Call relations**: _flatten_message calls this when building a message row. Thread records can later use that message snippet as a title or preview.

*Call graph*: called by 1 (_flatten_message).


### Engineering and operations systems
Connectors that ingest code, release, incident, on-call, and error-tracking data for engineering and reliability workflows.

### `extensions/sources/ufo_ext_sources/providers/github.py`

`io_transport` · `during source sync, while fetching GitHub data`

This connector is the bridge between GitHub and the rest of the source-sync system. Its main job is to discover which GitHub organizations the current credential can see, find the usable repositories in those organizations, then read many GitHub streams from each repository or organization. Without this file, the system would not know where to ask GitHub for data, how to follow GitHub’s paginated responses, or how to avoid mixing up records from different repositories.

A key idea here is “partitioning”: the connector treats each repository or organization like its own labeled folder. That matters because GitHub has many identifiers that are only unique inside one repository, such as a branch named `main` or a tag named `v1.0`. The connector stamps each record with its repository or organization and folds that label into the record’s identity, so two repositories do not overwrite each other’s pages.

The file also handles different sync styles. Some streams, like issues and comments, can be read from oldest updated item to newest using GitHub’s `since` filter. Others, like commits and events, are read newest-first and need careful stopping rules so new records arriving during a sync are not missed. GitHub does not provide delete notifications here, so the wider sync runner deduplicates already-seen rows. The connector only reads data; it does not write anything back to GitHub.

#### Function details

##### `_stream`  (lines 73–93)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', ordering: Ordering=Ordering.none, canonical:
```

**Purpose**: Creates one stream description for a GitHub data type, such as issues, commits, or users. A stream description tells the sync system what the stream is called, what field identifies a record, and how progress through the stream should be tracked.

**Data flow**: It receives settings like a stream name, primary key field, cursor field, and ordering style. It packages those settings into a `StreamSpec`, which is the shared description object the rest of the connector uses later.

**Call relations**: This helper is used when the file builds the full GitHub stream catalog. The created stream descriptions are later filtered by `GitHubConnector.streams` and interpreted by pagination, flattening, rendering, and identity code.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 197–200)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns only the GitHub streams that this connector currently knows how to fetch. Some streams are listed for catalog compatibility, but they are not runnable until an API path has been wired for them.

**Data flow**: It reads the connector’s full stream list and the internal path map. It keeps streams whose names have an API path and returns that smaller list to the sync system.

**Call relations**: The sync runner asks this method what can be synced. Later, `GitHubConnector.paginate` relies on the same path map to decide how to fetch each selected stream.


##### `GitHubConnector._make_client`  (lines 202–206)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to GitHub and adds the GitHub-specific headers that say which API format and version the connector expects.

**Data flow**: It receives a base URL and a credential, asks the parent REST connector to create an authenticated client, then adds GitHub `Accept` and API-version headers. It returns the ready-to-use client.

**Call relations**: This fits into connector setup before requests are made. The parent class supplies the common authenticated client, and this method customizes it so later calls to GitHub endpoints receive the right response shape.


##### `GitHubConnector.flatten`  (lines 208–257)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Shapes each GitHub record before it becomes a page, and makes its identity safe across repositories or organizations. This prevents records like `main` branches from different repositories from colliding.

**Data flow**: It receives one raw record and its stream description. It may reshape special cases, such as flattening stargazer user details or removing bulky nested repository objects from pull requests. Then it checks whether the stream came from a repository or organization partition, reads the record’s key, and prefixes that key with the partition label. It returns the shaped record, or raises an error if a partitioned record is missing its required partition label.

**Call relations**: The page-building adapter calls this before it reads a record’s primary key. It uses `_partition_field` to know which partition label should exist and `get_path` when the key lives inside nested data.

*Call graph*: calls 1 internal fn (_partition_field); 1 external calls (get_path).


##### `GitHubConnector.record_identity`  (lines 259–266)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Calculates the stable identity for a record when the normal primary-key logic is not enough. Its special job is to identify contributor activity records by repository plus author id.

**Data flow**: For most streams, it delegates to the parent connector’s normal identity logic. For contributor activity, it reads the repository label and the nested `author.id`; if both exist, it joins them into one identity string. If either piece is missing, it returns no identity.

**Call relations**: The sync system uses record identities to decide which page a record belongs to. This method mirrors the safety provided by `flatten`, but handles contributor activity separately because its declared key is nested and anonymous contributor rows may not have an author id.

*Call graph*: 1 external calls (get_path).


##### `GitHubConnector.render`  (lines 268–273)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a record into page content and title, with one special rule for pull requests. For pull requests, it keeps the update timestamp available as metadata but leaves it out of the visible page body.

**Data flow**: It receives a record and stream description. For most streams, it passes them to the parent renderer unchanged. For pull requests, it copies the record without the update cursor field, then asks the parent renderer to render that cleaned content.

**Call relations**: This is called after records have been fetched and shaped. It relies on the parent connector for the actual rendering format, changing only what content is handed over for pull request pages.


##### `GitHubConnector.paginate_source`  (lines 275–286)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides the standard source-sync entry point for reading pages from GitHub, while allowing newest-first backfills to receive a pinned lower time limit. A backfill is the first catch-up pass through older history.

**Data flow**: It receives the HTTP client, stream, saved cursor, current user id, and optional backfill floor. It passes the useful pieces to `GitHubConnector.paginate` and returns that asynchronous stream of result pages.

**Call relations**: The wider source framework calls this method when it wants records for a stream. This method is intentionally thin: it adapts the framework’s call shape to the GitHub-specific `paginate` method.

*Call graph*: calls 1 internal fn (paginate).


##### `GitHubConnector.paginate`  (lines 288–359)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Decides how to fetch one GitHub stream and yields pages of records. It handles top-level organization reads, organization-scoped reads, repository-scoped fan-out, and plain one-path reads.

**Data flow**: It receives a client, stream, saved cursor, and optional backfill floor. It finds the API path for the stream, prepares request parameters, then chooses a route: repositories are discovered through organizations; repo-scoped streams are walked once per repository using `PartitionWalk`; org-scoped streams are fetched once per organization; and simple paths are paged directly. It yields lists of records or richer stream pages with context attached.

**Call relations**: `GitHubConnector.paginate_source` hands work to this method. It calls helpers that discover organizations and repositories, fetch link-header pages, enrich users, and run per-repository page walks. It is the main traffic director for GitHub reads.

*Call graph*: calls 4 internal fn (_enrich_users, _iter_granted_org_repo_pages, _iter_user_orgs, _paginate_link_header); called by 1 (paginate_source); 4 external calls (__init__, Semaphore, astimezone, with_context).


##### `GitHubConnector.paginate.repos`  (lines 311–313)

```
async def repos() -> AsyncIterator[str]
```

**Purpose**: Supplies repository names to the partition walker. It turns discovered `(owner, repo)` pairs into the `owner/repo` text labels used as repository partitions.

**Data flow**: It reads repositories from `_iter_user_repos`, combines owner and repository name into one string, and yields those strings one at a time.

**Call relations**: This small nested function exists inside `GitHubConnector.paginate` for repository-scoped streams. `PartitionWalk` calls on it when it needs to know which repositories to walk.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector.paginate.repo_pages`  (lines 315–316)

```
def repo_pages(repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Provides `PartitionWalk` with the pages for one specific repository. It connects the generic partition-walking machinery to this connector’s GitHub-specific repository fetcher.

**Data flow**: It receives a repository key and a bound that describes where to start or stop in time. It calls `_repo_pages` with the current client, stream, API path, repository key, and bound, then returns that asynchronous page iterator.

**Call relations**: This nested function is created by `GitHubConnector.paginate` and handed to `PartitionWalk`. Whenever the walker chooses a repository and time window, this function delegates the actual GitHub fetching to `_repo_pages`.

*Call graph*: calls 1 internal fn (_repo_pages).


##### `GitHubConnector._repo_pages`  (lines 361–432)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Fetches a bounded slice of pages for one repository. It applies the right GitHub filters for the stream and reports each page’s time range so the partition walker can resume safely later.

**Data flow**: It receives a client, stream, repository API path, repository label, and time bound. It builds the repository-specific URL and request parameters, fetches pages, removes pull requests from the issues stream, optionally filters newest-first records by time on the client side, stamps records with their repository label, and yields `WalkPage` objects that include records plus high and low cursor values. If a repository is unavailable or unreadable in expected ways, it raises a partition-skip signal instead of failing the whole stream.

**Call relations**: `GitHubConnector.paginate.repo_pages` calls this for each repository selected by `PartitionWalk`. It uses `_paginate_link_header` for network paging, `_cursor_bounds` to summarize cursor ranges, and `with_context` to attach the repository label needed later by `flatten`.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); called by 1 (repo_pages); 3 external calls (__init__, __init__, with_context).


##### `GitHubConnector._iter_user_repos`  (lines 434–442)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: Discovers the repositories that should be synced by looking through the organizations the credential can access. It intentionally avoids the broader personal repository endpoint so the sync stays within the organization grant.

**Data flow**: It receives the HTTP client, asks `_iter_granted_org_repo_pages` for repository pages, extracts each repository’s owner and name using `_repo_identity`, and yields usable `(owner, repo)` pairs.

**Call relations**: The nested `GitHubConnector.paginate.repos` function calls this when repository-scoped streams need their partition list. It depends on organization-based repository discovery rather than manual repository configuration.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (repos).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 444–463)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: Lists repository pages for every organization the credential can see, while skipping archived repositories and forks. This gives the connector its clean set of organization-owned repositories to sync.

**Data flow**: It receives the HTTP client, gets organization logins from `_iter_user_orgs`, then fetches `/orgs/{org}/repos` pages for each org. It filters out records marked archived or forked, yields non-empty pages with the organization login, and silently skips organizations that GitHub says are forbidden, missing, or gone.

**Call relations**: Both `GitHubConnector.paginate` and `_iter_user_repos` use this helper. It sits between root organization discovery and all repository fan-out work.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, paginate).


##### `GitHubConnector._iter_user_orgs`  (lines 465–486)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Finds the GitHub organizations exposed by the current credential. Because every runnable stream starts from organizations, failure here means the whole stream should be skipped rather than treated as a broken sync.

**Data flow**: It receives the HTTP client, pages through `/user/orgs`, reads each organization `login`, and yields valid login strings. If GitHub refuses this root organization listing with the expected permission error, it raises `StreamSkipped` so the run records a skip. Other HTTP errors are allowed to bubble up.

**Call relations**: Organization-scoped reads in `GitHubConnector.paginate` call this directly, and repository discovery calls it through `_iter_granted_org_repo_pages`. It is the gatekeeper for the connector’s organization-based access model.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, paginate).


##### `GitHubConnector._enrich_users`  (lines 488–508)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: Replaces simple organization member records with fuller public user records when GitHub allows it. This can add useful public details such as name or email.

**Data flow**: It receives a page of user-like records and a semaphore, which is a small traffic light that limits how many user detail requests run at once. It starts one enrichment task per member, waits for them all, and returns a page where each member is either replaced by the fuller `/users/{login}` response or left unchanged.

**Call relations**: `GitHubConnector.paginate` calls this only for the `users` stream while reading organization members. The nested `one` function does the work for an individual member, and `asyncio.gather` lets several lookups happen concurrently without exceeding the chosen limit.

*Call graph*: called by 1 (paginate); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 494–506)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Fetches the fuller public GitHub profile for one organization member. If the profile cannot be found, it keeps the original member record.

**Data flow**: It receives one member record from the surrounding enrichment function. It reads the member’s `login`; if there is no usable login, it returns the original record. Otherwise it waits for permission from the semaphore, calls `/users/{login}`, parses the response, and returns the fuller profile when it is a dictionary. A 404 response also returns the original member.

**Call relations**: This helper lives inside `_enrich_users` because it only makes sense as part of page enrichment. `_enrich_users` launches it for every member in the page and collects the results.


##### `GitHubConnector._paginate_link_header`  (lines 510–518)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Follows GitHub’s standard pagination style, where the next page is advertised in an HTTP `Link` header. It also treats empty responses as empty pages rather than as failures.

**Data flow**: It receives a client, path, and optional query parameters. It delegates to the parent link-header paging helper with this connector’s page size and record parser, then yields each parsed page of records.

**Call relations**: Most fetching helpers call this method instead of making raw paginated requests themselves. It is used by organization discovery, repository discovery, repository stream reads, and direct stream reads in `GitHubConnector.paginate`.

*Call graph*: called by 4 (_iter_granted_org_repo_pages, _iter_user_orgs, _repo_pages, paginate).


##### `_partition_field`  (lines 521–529)

```
def _partition_field(path: str) -> str | None
```

**Purpose**: Determines which partition label a record should carry based on the API path being used. In plain terms, it asks whether this path is run once per repository, once per organization, or not partitioned at all.

**Data flow**: It receives an API path string. If the path contains a repository placeholder, it returns the repository partition field name. If it contains an organization placeholder, it returns the organization partition field name. Otherwise it returns nothing.

**Call relations**: `GitHubConnector.flatten` calls this to know which context field must be present before it scopes a record’s key. That keeps record identities tied to the repository or organization they came from.

*Call graph*: called by 1 (flatten).


##### `_parse_records`  (lines 532–536)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Turns a GitHub HTTP response into a list of record dictionaries. It accepts only the common GitHub list response shape and ignores anything else.

**Data flow**: It receives an HTTP response. If the response body is empty, it returns an empty list. Otherwise it parses the JSON body and returns it only if it is a list; non-list bodies become an empty list.

**Call relations**: `GitHubConnector._paginate_link_header` passes this parser into the shared pagination helper. That helper handles page traversal, while this function decides what counts as records on each page.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 539–554)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: Extracts a repository’s owner and name from a GitHub repository record. It is tolerant of slightly different GitHub response shapes.

**Data flow**: It receives one repository record and an optional fallback owner. It first tries `full_name` like `owner/repo`; if that is not usable, it tries the nested owner login plus repository name; if that is missing but a fallback organization is available, it uses that. It returns an `(owner, repo)` pair or nothing if it cannot identify the repository.

**Call relations**: `GitHubConnector._iter_user_repos` calls this while turning repository pages into repository partitions. Its output becomes the owner/repo labels used by repository-scoped pagination.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 557–567)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: Finds the newest and oldest cursor values on a page. A cursor is the timestamp-like field the sync uses to know how far it has read.

**Data flow**: It receives a page of records and the cursor field name. If there is no cursor field, or no records have a string value at that field, it returns two empty values. Otherwise it reads the cursor value from each record, including nested fields, and returns the maximum and minimum values.

**Call relations**: `GitHubConnector._repo_pages` calls this for each fetched page. The resulting high and low values are handed to `WalkPage`, which lets `PartitionWalk` track watermarks and resume repository walks safely.

*Call graph*: called by 1 (_repo_pages); 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/providers/pagerduty.py`

`io_transport` · `source sync`

PagerDuty is an incident-response service, and its API returns data in small pages rather than all at once. This file is the adapter that knows PagerDuty’s rules: which endpoints exist, how pages continue, how to ask only for newer incidents, and how to treat missing permissions. Without it, the system would not know how to fetch PagerDuty records safely or consistently.

At the top, the file defines the available streams, such as users, teams, services, incidents, incident notes, escalation policies, schedules, and on-calls. A stream is a named kind of data with a stable ID field, and sometimes a time field used as a bookmark so later syncs can resume from where the last one stopped.

The `PagerDutyConnector` then supplies the actual reading behavior. It creates an HTTP client with PagerDuty’s required `Accept` header, walks through PagerDuty’s offset-based pages, and yields batches of records. Incidents get special treatment because they can be fetched in update-time order using a cursor. Incident notes are more indirect: the connector first lists incidents, then asks PagerDuty for the notes attached to each incident, like checking each folder for its loose papers.

If PagerDuty replies with 401 or 403, meaning “not allowed” or “bad credential,” this connector marks that stream as skipped instead of crashing the whole run. This is important because one missing permission should not necessarily stop every other source from syncing.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 74–77)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to PagerDuty. It adds PagerDuty’s required versioned media type header so the API knows which response format the connector expects.

**Data flow**: It receives a base URL and a credential reference. It asks the parent REST connector to build the basic client, then adds an `Accept` header for PagerDuty API version 2. It returns the ready-to-use asynchronous HTTP client.

**Call relations**: This is the setup step before any PagerDuty requests are made. The broader REST connector machinery calls it when creating the client, and the client it returns is later passed into the pagination functions that fetch actual records.


##### `PagerDutyConnector._offset_pages`  (lines 79–99)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one ordinary PagerDuty stream page by page. It hides PagerDuty’s paging details so the rest of the connector can simply receive batches of records.

**Data flow**: It receives an HTTP client, a stream description, optional query parameters, and an optional cursor bookmark. It asks the shared REST helper for offset-based pages, using PagerDuty’s `more` flag and returned `limit` value to know when and how to continue. If a cursor is present and the stream has a cursor field, it filters out records that are not newer than that bookmark. It yields only non-empty batches of records.

**Call relations**: This is the common reader used by `PagerDutyConnector._incidents` and by `PagerDutyConnector.paginate` for the simpler streams. It delegates the low-level page fetching to the base connector’s offset-page helper, then hands cleaned batches back to its caller.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 101–113)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads PagerDuty incidents in a way that supports incremental syncing. It asks PagerDuty for incidents sorted from oldest update to newest, so a saved bookmark can be used reliably.

**Data flow**: It receives an HTTP client and an optional cursor. It builds query parameters that sort incidents by `updated_at` in ascending order. If a cursor exists, it also sends it as PagerDuty’s `since` parameter. It then uses `_offset_pages` to fetch matching incident pages and yields each page onward.

**Call relations**: When `PagerDutyConnector.paginate` is asked for the incidents stream, it calls this function. `PagerDutyConnector._incident_notes` also calls it when it needs a list of incidents to inspect for notes. This function relies on `_offset_pages` for the actual page-by-page API walking.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 115–128)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads notes attached to incidents. PagerDuty does not expose these as one simple top-level list here, so the connector first finds incidents and then fetches notes for each one.

**Data flow**: It receives an HTTP client and an optional cursor. It first reads incidents using `_incidents`, without limiting that incident scan by the notes cursor. For each incident with a valid ID, it requests `/incidents/{id}/notes`, extracts the `notes` list from the response, filters out notes older than the cursor when needed, adds the parent incident ID as context, and yields non-empty note batches.

**Call relations**: This function is called by `PagerDutyConnector.paginate` when the requested stream is `incident_notes`. It calls `_incidents` to discover which incidents to inspect, uses `records_at` to pull the notes list out of PagerDuty’s response, and uses `with_context` to attach the incident ID so downstream code knows which incident each note came from.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 130–164)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a PagerDuty stream. Given a stream name, it chooses the right fetching method and yields batches of records to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. For incidents, it forwards the work to `_incidents`; for incident notes, it forwards to `_incident_notes`; for the other supported PagerDuty streams, it uses `_offset_pages`. If the stream is unknown, it raises `StreamSkipped`. If PagerDuty rejects the request with HTTP 401 or 403, it turns that refusal into a skipped stream with a clear message. Other HTTP errors are allowed to continue as real failures.

**Call relations**: The source-sync framework calls this when it is time to read records for a particular PagerDuty stream. This function decides which specialist helper should do the work, then yields that helper’s pages back to the framework. It also acts as the safety gate that converts permission refusals into `StreamSkipped` instead of letting one denied stream stop the whole sync.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/providers/sentry.py`

`io_transport` · `source sync / request handling`

This connector is the bridge between UFO and Sentry’s web API. Sentry stores useful project history, but its API returns that history in small pages and uses organization and project names in many URLs. This file knows that shape, walks through it, and produces clean batches of records for the shared sync engine.

At the top, the file declares the Sentry streams it supports: organizations, members, projects, issues, events, and releases. A stream is one kind of thing to sync. Each stream says which field identifies a record and which date field can be used as a cursor, meaning a bookmark for “only fetch records newer than this.”

The main class, SentryConnector, reads only; it has no write path. Its paginate method is the dispatcher. Given a stream name, it calls the right helper. Some helpers first fetch all organizations or projects, then use those to ask Sentry for nested data such as members inside an organization or issues inside a project. When records come from inside a project or organization, the connector adds that context, like writing the folder name on every paper taken out of a filing cabinet.

Sentry pagination is handled through the HTTP Link header. If Sentry says there is another page, the connector follows the cursor token. If Sentry rejects the request with 401 or 403, the stream is skipped with a clear message instead of crashing the whole sync.

#### Function details

##### `_sentry_next_cursor`  (lines 76–81)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper looks at Sentry’s HTTP response headers and finds the token for the next page of results, if one exists. It is needed because Sentry does not put the next-page marker in the response body; it hides it in the Link header.

**Data flow**: It receives HTTP headers from a Sentry response. It checks the lowercase or uppercase Link header, searches for the part that says there is a next page with real results, and extracts the cursor text. It returns that cursor string, or returns nothing if there is no next page.

**Call relations**: The shared page reader, SentryConnector._paged_list, calls this after each request. If this function finds a cursor, _paged_list makes another request; if not, the paging loop stops.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.record_ref`  (lines 89–93)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This chooses the human-friendly reference used for a synced record. For organizations, it uses the organization slug instead of the default identifier, because the slug is what Sentry URLs and nested API calls use.

**Data flow**: It receives one record and the stream it came from. If the stream is organizations, it reads the record’s slug and returns it as text when possible. For every other stream, it lets the parent RestConnector choose the normal reference.

**Call relations**: This is used by the broader source framework when it needs a stable, readable reference for a record. It customizes only the organization case and leaves all other streams to the base connector behavior.


##### `SentryConnector.paginate`  (lines 95–134)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway for reading any Sentry stream. It decides which Sentry helper to call, applies incremental filtering where needed, and yields pages of records back to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. It checks the stream name, calls the matching helper, and yields each page of records that helper produces. If Sentry refuses access with 401 or 403, it turns that into a StreamSkipped message so the sync can continue safely; unsupported streams are skipped too.

**Call relations**: The sync framework calls this when it wants records for one stream. From there, paginate hands work to _organizations, _projects, _members, _issues, _events, or _releases. Those helpers do the actual Sentry API walking, while paginate keeps the top-level routing and error policy in one place.

*Call graph*: calls 7 internal fn (__init__, _events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._paged_list`  (lines 136–153)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the common page-turning routine for Sentry list endpoints. It repeatedly asks one API path for data until Sentry stops offering a next-page cursor.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It sends a request, reads the JSON body, keeps only dictionary-like records, yields them as a page, then asks _sentry_next_cursor whether another page exists. It repeats with the new cursor until no cursor is found.

**Call relations**: All the higher-level helpers use this instead of each reimplementing Sentry pagination. It calls _sentry_next_cursor after every HTTP response, then hands pages back to _organizations, _projects, _members, _issues, _events, and _releases.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 155–159)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This collects every organization visible to the Sentry credential. Other streams depend on organizations because Sentry’s member and release URLs are organized under an organization slug.

**Data flow**: It receives an HTTP client. It asks _paged_list for all pages from the organizations endpoint, appends every page into one list, and returns that full list of organization records.

**Call relations**: paginate calls this directly for the organizations stream. _members and _releases also call it first so they know which organization-specific URLs to visit next.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, paginate).


##### `SentryConnector._projects`  (lines 161–165)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This collects every project visible to the Sentry credential. It provides the project and organization slugs needed to fetch project-level data such as issues and events.

**Data flow**: It receives an HTTP client. It asks _paged_list for all pages from the projects endpoint, gathers those pages into one list, and returns the full project list.

**Call relations**: paginate calls this directly for the projects stream. _issues and _events call it first, then use each project’s organization and project slug to build the more specific Sentry URLs.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, paginate).


##### `SentryConnector._members`  (lines 167–173)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads organization members from Sentry, one organization at a time. It adds the organization slug to each member record so the rest of the system knows where that member came from.

**Data flow**: It receives an HTTP client. It first gets all organizations, skips any organization without a usable slug, then reads member pages from that organization’s members endpoint. Before yielding each page, it adds organization_slug to every record in the page.

**Call relations**: paginate calls this for the members stream. This function depends on _organizations to know where to look, uses _paged_list to read each organization’s members, and uses with_context to attach the missing organization information.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 175–189)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads issues for every visible Sentry project. If a cursor is supplied, it asks Sentry to return only issues whose last-seen time is newer than that bookmark.

**Data flow**: It receives an HTTP client and an optional cursor. It fetches all projects, extracts each project’s organization slug and project slug, skips projects missing those values, builds the project issues URL, and optionally adds a lastSeen filter. It yields pages of issue records with both organization_slug and project_slug added.

**Call relations**: paginate calls this for the issues stream. This function uses _projects to discover the project tree, _paged_list to read each project’s issue pages, and with_context to make every issue record self-explanatory outside Sentry’s nested URL structure.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 191–205)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads event records for every visible Sentry project. Events can be numerous, so when a cursor is present it asks Sentry for only events newer than that timestamp.

**Data flow**: It receives an HTTP client and an optional cursor. It fetches all projects, extracts the organization and project slugs, skips incomplete project records, builds the events endpoint for each project, and optionally adds an event.timestamp filter. It yields pages of event records with organization_slug and project_slug attached.

**Call relations**: paginate calls this for the events stream. Like _issues, it relies on _projects for the list of project locations, _paged_list for Sentry pagination, and with_context to label each event with its project and organization.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 207–218)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads release records for each visible Sentry organization. If a cursor is supplied, it keeps only releases whose creation date is newer than that bookmark.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all organizations, skips any without a usable slug, reads release pages from each organization’s releases endpoint, filters by dateCreated when a cursor exists, and yields non-empty pages with organization_slug added.

**Call relations**: paginate calls this for the releases stream. This function uses _organizations to find organization slugs, _paged_list to walk through release pages, and with_context to preserve which organization each release belongs to.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).
