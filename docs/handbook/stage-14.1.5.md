# Work management, engineering, and operational connectors  `stage-14.1.5`

This stage is part of the system’s source-sync work loop: it reaches out to outside tools, reads what the current credential is allowed to see, and reshapes that data into standard “streams” of records the rest of the system can store, search, and recall. A stream is just a steady list of items, like pages moving along a conveyor belt.

Each connector knows the map and rules of one service. Airtable walks through bases, tables, and records. Asana reads workspaces, teams, projects, tasks, stories, and users. ClickUp follows its nested teams, spaces, folders, lists, tasks, comments, goals, and fields. GitHub discovers organizations and repositories, then fetches issues, comments, users, and related objects. Jira reads projects, issues, boards, sprints, comments, and users. Linear uses its GraphQL interface, a query-based API, to gather issues, projects, teams, comments, and workflow states. monday.com, PagerDuty, Sentry, and Wrike do the same for boards and items, incidents and on-call records, error reports and releases, or folders and tasks. Together, they turn many different tools into one common memory format.

## Files in this stage

### Structured workspace records
Connector for discovering Airtable bases, tables, and records as syncable structured pages.

### `extensions/sources/ufo_ext_sources/providers/airtable.py`

`io_transport` · `source sync`

Airtable is organized like a set of workspaces: a base contains tables, and each table contains records. This connector exists because there is no single Airtable endpoint that says “give me everything.” Instead, it must first ask Airtable for the list of bases, then ask each base for its tables, then read records from each table.

The file defines three streams of data: bases, tables, and records. A stream is a named kind of data the sync system can request. The connector uses Airtable’s web API with an OAuth bearer token supplied elsewhere by the credential system. When Airtable says access is refused with a 401 or 403 status, the connector skips that stream with a clear message instead of crashing the whole sync.

For records, Airtable returns data in pages. Think of this like reading a long document one screen at a time: each response may include an offset token, which is used to request the next screen. The connector also adds context such as base ID and table ID to records and tables, so downstream code can tell where each item came from. This connector only reads from Airtable; it does not create, edit, or delete anything.

#### Function details

##### `AirtableConnector._bases`  (lines 40–42)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the list of Airtable bases available to the authenticated user. A base is Airtable’s top-level container, similar to a workbook that contains multiple sheets.

**Data flow**: It receives an HTTP client that is already ready to talk to Airtable. It asks Airtable’s metadata endpoint for bases, pulls the list found under the "bases" field, and returns that list as plain dictionaries.

**Call relations**: This is the first discovery step used by AirtableConnector.paginate. The broader sync asks for bases, tables, or records; paginate calls this helper whenever it needs to know which bases exist before moving deeper into tables or records.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 44–51)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: Fetches the tables inside one Airtable base. It also tags each table with the base it belongs to, so later steps do not lose that parent-child relationship.

**Data flow**: It receives an HTTP client and one base record. It reads the base ID; if the ID is missing or not usable, it returns an empty list. Otherwise it asks Airtable for that base’s tables, extracts the table list, adds base ID and base name to each table, and returns the enriched list.

**Call relations**: AirtableConnector.paginate calls this after it has fetched bases. When the requested stream is tables, these results are yielded directly. When the requested stream is records, these table results become the map that tells the connector where to fetch records next.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 53–71)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads records from one Airtable table, one page at a time. It adds base and table context to every record page so each record can later be traced back to its Airtable location.

**Data flow**: It receives an HTTP client, a base ID, and a table record. It checks for a valid table ID; if there is none, it produces nothing. If the table is valid, it repeatedly requests pages of records from Airtable, using Airtable’s offset token to move to the next page, and yields each non-empty page with base ID, table ID, and table name attached.

**Call relations**: AirtableConnector.paginate calls this only after it has discovered bases and tables. This helper is the final step in the discovery chain: bases lead to tables, and tables lead to record pages that are handed back to the sync system.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 73–110)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the main reading path for each Airtable stream. Depending on whether the sync system asks for bases, tables, or records, it gathers the right data and yields it in pages.

**Data flow**: It receives an HTTP client, a stream description, and a cursor value. It checks the stream name, then follows the needed path: bases are fetched directly; tables are gathered by walking all bases; records are gathered by walking all bases, then all tables, then each table’s record pages. It yields lists of dictionaries as pages. If Airtable refuses access with a 401 or 403 response, it turns that into a skipped stream message; other HTTP errors are left to bubble up.

**Call relations**: This is the central method the source-sync framework calls when it wants Airtable data. It coordinates the helper methods _bases, _tables_for_base, and _records_for_table, and uses StreamSkipped when a stream is unsupported or the user’s Airtable permission grant does not allow access.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 112–134)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes raw Airtable records into a cleaner shape for downstream use. It makes important fields easier to find and adds helpful API links for bases and tables.

**Data flow**: It receives one raw record and the stream it came from. For bases, it keeps the original data and adds a stable name field and metadata API URL. For tables, it adds the table API URL using the stored base ID. For records, it pulls Airtable’s created time into a clearer created_at field and ensures fields is always a dictionary. It returns the adjusted record without modifying the original stream flow.

**Call relations**: After paginate has supplied raw pages, the source framework can call this method to prepare individual items for indexing or storage. It does not fetch more data; it shapes what has already been read so other parts of the system see consistent, useful records.


### Collaborative task suites
Connectors for broad project-management platforms that expose nested teams, projects, tasks, comments, users, and workspace records.

### `extensions/sources/ufo_ext_sources/providers/asana.py`

`io_transport` · `source sync runs`

Asana is a work-tracking service, and its API does not simply return “all tasks” or “all projects” in one answer. It sends records in pages, with a token that says where to continue next. This file defines an Asana connector that knows which Asana objects can be read and how to walk through those pages safely.

The file first defines the list of Asana streams the system can sync. A stream is one kind of Asana object, like tasks, projects, stories, users, teams, or tags. Some streams are marked as canonical, meaning they are especially central to the product’s memory of Asana work. Projects and tasks can be synced incrementally using Asana’s `modified_since` filter, so the connector can ask only for records changed after a saved cursor. Other streams are read as full refreshes each time.

The main class, `AsanaConnector`, supplies Asana’s base API address and a `paginate` method. That method asks Asana for one page at a time, yields any records it receives, follows the `next_page.offset` token, and stops when there is no next token. If Asana refuses access with a 401 or 403 status, the connector treats that stream as skipped rather than crashing the whole sync. This matters because a user’s Asana grant may allow some data but not every stream.

#### Function details

##### `_stream`  (lines 25–39)

```
def _stream(name: str, *, cursor_field: str | None=None, updated_at_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Asana data stream, such as `tasks` or `projects`. It keeps the stream list concise and consistent, so every stream uses Asana’s `gid` field as its unique ID and can optionally declare cursor fields for incremental syncing.

**Data flow**: It receives a stream name and optional settings such as which timestamp field should be used as a cursor and whether the stream is canonical. It packages those details into a `StreamSpec`, which is the shared description object the rest of the source-sync system understands. The result is a ready-to-use stream definition added to the Asana stream catalog.

**Call relations**: This helper is used while building the module-level `ASANA_STREAMS` list. It hands each stream’s plain settings to `StreamSpec`, so the broader connector framework can later know what Asana endpoint to read and how to identify or watermark records.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 75–99)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Asana stream page by page and yields batches of records to the sync system. It hides Asana’s pagination details, so the rest of the system can think in terms of records instead of API continuation tokens.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. It builds a request for the stream’s Asana endpoint, adds a page limit, and for tasks or projects adds `modified_since` when a cursor is available. For each API response, it takes the `data` list, safely normalizes it with `list_or_empty`, yields records if any exist, then reads `next_page.offset` to decide whether to request another page. If Asana returns 401 or 403, it turns that refusal into a `StreamSkipped` signal; other HTTP errors are passed upward unchanged.

**Call relations**: The source framework calls this method when it is time to sync a particular Asana stream. During the loop it relies on the base REST connector’s request helper to fetch JSON from Asana, uses `list_or_empty` to avoid breaking on missing or non-list `data`, and raises `StreamSkipped` when the user’s Asana permission does not cover that stream.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/providers/clickup.py`

`io_transport` · `source sync`

ClickUp stores work like a set of nested boxes: a team contains spaces, spaces contain folders, folders contain lists, and lists contain tasks and comments. This connector walks that structure from the top down so it can find the records that live at the lower levels. Without this file, the system would not know how to discover ClickUp lists before asking for their tasks, or how to attach useful parent information like the list or team each record came from.

The file defines the ClickUp streams the system can read, including teams, users, spaces, folders, lists, tasks, list comments, custom fields, and goals. The `ClickUpConnector` uses ClickUp’s HTTP API with an OAuth bearer token supplied by the broader source framework. It is read-only: there is no code here to create or update ClickUp data.

A central `paginate` method acts like a dispatcher. When the sync engine asks for a stream, it chooses the right helper method. Some streams are simple, such as teams. Others require discovery first, such as tasks, where the connector finds every list and then pages through that list’s tasks. For incremental syncing, it can skip older tasks or comments using a stored cursor value, such as ClickUp’s `date_updated`. If ClickUp refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `ClickUpConnector._teams`  (lines 61–63)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the ClickUp teams available to the connected account. Teams are the top-level starting point for most other ClickUp reads, so many later lookups depend on this first step.

**Data flow**: It receives an HTTP client that is already ready to talk to ClickUp. It asks the `/team` endpoint for data, pulls the `teams` list out of the response, and returns that list of team records.

**Call relations**: This is the first rung of the ClickUp hierarchy. Other helper methods call it when they need team IDs before finding spaces, users, or goals, and the root-stream path uses it when the sync asks directly for teams.

*Call graph*: called by 4 (_goals, _root_records, _spaces, _users); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 65–73)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all active spaces inside every ClickUp team. A space is the next container below a team, so this builds the bridge from teams to the rest of the workspace.

**Data flow**: It starts with the teams returned by `_teams`. For each team with a usable ID, it asks ClickUp for non-archived spaces, extracts the `spaces` records, adds the parent `team_id` to each record, and returns one combined list.

**Call relations**: This method continues the top-down walk that began with `_teams`. Folder and list discovery rely on it, and `paginate` can also reach it through `_root_records` when the sync asks for the spaces stream.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, _root_records); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 75–85)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all active folders inside every ClickUp space. Folders are one of the paths that lead to lists, so this is needed before many task lists can be discovered.

**Data flow**: It reads spaces from `_spaces`. For each valid space ID, it asks ClickUp for non-archived folders in that space, extracts the folder records, attaches the parent `space_id`, and returns all folders together.

**Call relations**: This is the next step in the hierarchy after spaces. `_lists` uses it to find lists that live inside folders, and `_root_records` uses it when the sync asks directly for folders.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, _root_records); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 87–103)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all active ClickUp lists, both lists inside folders and lists directly under spaces. Lists are important because tasks, comments, and custom fields are read from each list.

**Data flow**: It first gets folders from `_folders`, then asks ClickUp for each folder’s non-archived lists and labels those records with `folder_id`. It also gets spaces from `_spaces`, asks for folderless lists directly under each space, labels those with `space_id`, and returns the combined result.

**Call relations**: This is the key discovery step before reading list-level data. `_tasks` and `_list_child_stream` call it so they know which list IDs to query, and `_root_records` calls it when the sync asks for the lists stream itself.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _root_records, _tasks); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 105–130)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads tasks from every discovered ClickUp list. It supports incremental sync by keeping only tasks newer than the stored update marker when one is provided.

**Data flow**: It receives an HTTP client and an optional cursor, which is a saved “last seen” value. It gets all lists from `_lists`, then for each list requests task pages one by one, including closed tasks and subtasks. Each task is stamped with its list ID and list name. If a cursor exists, older tasks are filtered out. Non-empty batches are yielded back to the caller as they are found.

**Call relations**: `paginate` uses this when the sync engine asks for the tasks stream. `_tasks` depends on `_lists` because ClickUp tasks are not fetched from one flat endpoint; each list must be visited separately.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 132–151)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads data that hangs directly off each list, currently list comments and list custom fields. It avoids duplicating the same list-walking pattern in two separate places.

**Data flow**: It receives an HTTP client, the stream description, and an optional cursor. It gets all lists from `_lists`, chooses the correct ClickUp endpoint for comments or fields, extracts the right records from the response, attaches the list ID and list name, optionally filters by the stream’s cursor field, and yields each non-empty batch.

**Call relations**: `paginate` calls this for the list comments and list custom fields streams. It sits just below the main dispatcher and just above `_lists`, using list discovery to fan out many small child reads.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 153–187)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main routing point for reading any ClickUp stream. The sync engine asks for a stream here, and this method chooses the correct helper to produce records in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. Based on the stream name, it calls the matching method for root records, users, tasks, list child records, or goals. It yields any records those helpers produce. If a stream is unknown, or if ClickUp refuses access with an authorization error, it raises `StreamSkipped`, meaning this stream should be skipped rather than treated as a successful read.

**Call relations**: This is the connector’s public reading path for the rest of the source framework. It hands simple streams to `_root_records`, derived users to `_users`, task pages to `_tasks`, list comments and fields to `_list_child_stream`, and goals to `_goals`.

*Call graph*: calls 6 internal fn (__init__, _goals, _list_child_stream, _root_records, _tasks, _users).


##### `ClickUpConnector._root_records`  (lines 189–200)

```
async def _root_records(self, client: httpx.AsyncClient, name: str) -> list[dict[str, Any]]
```

**Purpose**: Provides a small switchboard for the simple hierarchy streams: teams, spaces, folders, and lists. It lets the main paginator ask for one of these by name without repeating the same branching logic there.

**Data flow**: It receives a stream name and an HTTP client. If the name is one of the supported root-style collections, it calls the matching helper and returns those records. If the name is not supported, it raises an error because there is no matching ClickUp root collection in this connector.

**Call relations**: `paginate` uses this for the streams that can be returned as one collected list. It delegates to `_teams`, `_spaces`, `_folders`, or `_lists`, depending on which part of the ClickUp hierarchy the sync requested.

*Call graph*: calls 4 internal fn (_folders, _lists, _spaces, _teams); called by 1 (paginate).


##### `ClickUpConnector._users`  (lines 202–211)

```
async def _users(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a user list from the member information embedded in ClickUp teams. ClickUp does not need a separate flat user walk here, so this method collects users from team membership data.

**Data flow**: It starts by reading teams through `_teams`. For each team, it looks through the `members` field, finds nested `user` objects, and stores them by user ID so duplicates collapse into one entry. Each saved user is also tagged with the team ID it came from. The result is a dictionary keyed by user ID.

**Call relations**: `paginate` calls this when the sync asks for the users stream. It depends on `_teams` because team records already contain the member data needed to assemble users.

*Call graph*: calls 1 internal fn (_teams); called by 1 (paginate).


##### `ClickUpConnector._goals`  (lines 213–221)

```
async def _goals(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Reads ClickUp goals for every available team. Goals are team-level records, so the connector must discover teams before asking for them.

**Data flow**: It receives an HTTP client, gets teams from `_teams`, and for each valid team ID asks ClickUp for that team’s goals. It extracts the `goals` list from each response, adds the parent `team_id`, and returns one combined list.

**Call relations**: `paginate` uses this when the sync asks for the goals stream. Like spaces and users, it starts from `_teams` because ClickUp’s goal endpoint is organized under a team.

*Call graph*: calls 1 internal fn (_teams); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 223–257)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes selected ClickUp records into fields the rest of the system expects, such as `name`, `created_at`, `status`, `body`, and parent IDs. This makes records easier to display, search, and compare across different source systems.

**Data flow**: It receives one raw ClickUp record and the stream description. For users, it chooses a display name, email, and join date. For spaces, folders, and lists, it fills in a name and API URL. For tasks, it simplifies status and creation fields. For list comments, it extracts the comment body, author, creation date, and parent list ID. For other streams, it returns the record unchanged.

**Call relations**: This method is part of the broader source framework’s cleanup step after records are read. It does not fetch more data; instead, it reshapes records produced by the pagination helpers into a more consistent form.

*Call graph*: 1 external calls (dict_or_empty).


### Engineering issue trackers
Connectors for developer and engineering planning systems that turn repositories, projects, issues, comments, users, teams, and workflow metadata into searchable sync streams.

### `extensions/sources/ufo_ext_sources/providers/github.py`

`io_transport` · `source sync`

This connector is like a careful librarian for GitHub. It first finds the organizations the authenticated GitHub account can access, then finds the non-archived, non-fork repositories inside those organizations, and then reads many GitHub lists from each repository or organization. GitHub returns long lists in pages, so this file follows GitHub’s “next page” links until a stream is complete or until the sync walk knows it can stop.

A major job here is avoiding mix-ups between repositories. Many GitHub values are only unique inside one repository, such as a branch named “main” or a tag named “v1.0”. Before records are handed to the rest of the system, the connector stamps them with the repository or organization they came from and scopes their identity with that stamp. That prevents two repositories’ “main” branches from overwriting the same page.

The file also handles different styles of syncing. Some streams can resume from an “updated since this time” cursor. Some newest-first feeds, such as commits and events, need windowed walking so new records inserted at the front are not missed. If GitHub refuses access to one repository or organization, the connector often skips just that part. If GitHub refuses the root organization list, it marks the whole stream as skipped rather than treating it as a broken run.

#### Function details

##### `_stream`  (lines 74–94)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', ordering: Ordering=Ordering.none, canonical:
```

**Purpose**: Builds a description of one GitHub stream, such as issues, commits, or repositories. This keeps the long stream catalog compact and consistent.

**Data flow**: It receives the stream name plus details like the primary key, cursor field, and ordering style. It fills in defaults where the caller did not provide them, then returns a StreamSpec object that the connector uses later to know how that stream should be read.

**Call relations**: The file uses this helper while defining the global GitHub stream catalog. It hands the finished stream descriptions to the connector class, which later filters them and uses their settings during pagination, identity building, and rendering.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 198–201)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns only the GitHub streams that this connector can actually fetch today. Some streams are listed for catalog compatibility but do not yet have a wired GitHub API path.

**Data flow**: It reads the connector’s full stream list and the internal path table. It keeps only streams whose names appear in that path table, then returns that runnable list.

**Call relations**: The sync framework asks the connector what streams are available. This method acts as the gatekeeper, so adding a path for a catalogued stream automatically makes it runnable.


##### `GitHubConnector._make_client`  (lines 203–207)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to GitHub, with GitHub-specific headers added. These headers tell GitHub which API format and version the connector expects.

**Data flow**: It receives a base URL and credential, asks the parent connector to build the authenticated client, then adds the GitHub Accept header and API version header. It returns the ready-to-use async HTTP client.

**Call relations**: The base source machinery calls this when setting up communication. After this, all API calls made by the connector go through a client that GitHub will understand.


##### `GitHubConnector.flatten`  (lines 209–258)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Prepares each GitHub record before it becomes a page, especially by making its identity unique to the repository or organization it came from. This prevents records with local-only keys from colliding across repositories.

**Data flow**: It receives a raw GitHub record and its stream description. It may reshape special cases, such as merging stargazer user data or removing bulky nested repository objects from pull requests. Then it checks whether the stream is repository-scoped or organization-scoped, reads the partition stamp, and prefixes the record’s primary key with that partition when possible. It returns the shaped record, or raises an error if a partitioned record was not stamped correctly.

**Call relations**: The adapter uses this before reading the record’s primary key. It relies on _partition_field to know which stamp should exist, and on get_path when a key is nested inside the record.

*Call graph*: calls 1 internal fn (_partition_field); 1 external calls (get_path).


##### `GitHubConnector.record_identity`  (lines 260–267)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Finds the stable identity for a record, with special treatment for contributor activity records. Contributor activity can store its author id inside a nested author object, so it needs a custom identity rule.

**Data flow**: It receives a record and stream description. For most streams it delegates to the parent connector. For contributor activity, it reads the repository stamp and nested author id, combines them into one identity string, and returns that string. If either part is missing, it returns nothing.

**Call relations**: The wider sync system uses record identity to deduplicate and address records. This override keeps anonymous contributor rows from pretending to have a reliable identity while still allowing normal contributor rows to be keyed by repository plus author.

*Call graph*: 1 external calls (get_path).


##### `GitHubConnector.render`  (lines 269–274)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a GitHub record into page content, with a small cleanup for pull requests. Pull request update timestamps are kept as metadata rather than repeated in the page body.

**Data flow**: It receives a record and stream description. For non-pull-request streams, it passes the record straight to the parent renderer. For pull requests, it removes the field used as the update cursor from the visible content, then asks the parent renderer to produce the final title and body.

**Call relations**: The sync adapter calls rendering after records have been fetched and shaped. This method keeps pull request pages cleaner while still letting the framework track their update time.


##### `GitHubConnector.paginate_source`  (lines 276–287)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides the sync framework with pages of GitHub records, while accepting an extra backfill floor used by newest-first repository streams. It is the public pagination seam for this connector.

**Data flow**: It receives the HTTP client, stream, current cursor, optional user id, and optional backfill cutoff time. It passes the relevant pieces to paginate and returns the async sequence of pages produced there.

**Call relations**: The source runner calls this when it wants records for a stream. This method immediately hands off to GitHubConnector.paginate, which chooses the correct fetching strategy.

*Call graph*: calls 1 internal fn (paginate).


##### `GitHubConnector.paginate`  (lines 289–323)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses how to fetch pages for a particular GitHub stream. Different streams are fetched directly, per organization, per repository, or through the repository catalog.

**Data flow**: It receives a client, stream, cursor, and optional backfill cutoff. It looks up the stream’s API path, builds common request parameters, then routes the work: repositories go through repository discovery, repository paths fan out over repositories, organization paths fan out over organizations, and simple paths use basic link-header pagination. It yields each page as it arrives.

**Call relations**: paginate_source calls this as the central dispatcher. It then delegates to _repository_pages, _repo_stream_pages, _org_stream_pages, or _paginate_link_header depending on what kind of GitHub endpoint the stream uses.

*Call graph*: calls 4 internal fn (_org_stream_pages, _paginate_link_header, _repo_stream_pages, _repository_pages); called by 1 (paginate_source).


##### `GitHubConnector._repository_pages`  (lines 325–329)

```
async def _repository_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Produces pages for the repositories stream. It reads repositories from each granted organization and stamps each record with the organization it came from.

**Data flow**: It receives the HTTP client. It walks organization repository pages, adds org_login context to every repository record in each page, and yields those stamped pages.

**Call relations**: GitHubConnector.paginate uses this for the repositories stream. It relies on _iter_granted_org_repo_pages for discovery and with_context to attach the organization stamp.

*Call graph*: calls 1 internal fn (_iter_granted_org_repo_pages); called by 1 (paginate); 1 external calls (with_context).


##### `GitHubConnector._repo_partitions`  (lines 331–333)

```
async def _repo_partitions(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Lists the repository partitions that repository-scoped streams should walk. A partition here is one repository, named as owner/repo.

**Data flow**: It receives the HTTP client. It reads accessible repositories and converts each owner and repository name into a single owner/repo string, yielding one string at a time.

**Call relations**: PartitionWalk uses this through _repo_stream_pages to know which repositories exist. It gets its repository list from _iter_user_repos.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector._repo_stream_pages`  (lines 335–359)

```
async def _repo_stream_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, cursor: str | None, backfill_after: datetime | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Sets up a partitioned walk for streams that must be fetched once per repository, such as issues, commits, branches, or releases. It lets shared walking logic track cursors and resume state across many repositories.

**Data flow**: It receives the client, stream, API path, current cursor, and optional backfill cutoff. It converts the cutoff time into GitHub’s timestamp format, creates a PartitionWalk with repository partitions and per-repository page fetching, then yields the pages that walk produces. When finished, it closes the async generator if needed.

**Call relations**: GitHubConnector.paginate calls this for paths containing owner and repo placeholders. It supplies _repo_partitions as the list of repositories and _repo_pages as the worker for one repository at a time.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, astimezone, partial).


##### `GitHubConnector._org_stream_pages`  (lines 361–379)

```
async def _org_stream_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, params: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches streams that are read once per organization, such as teams or organization members. For users, it can also fetch richer public profile data for each member.

**Data flow**: It receives the client, stream, API path, and request parameters. It iterates through visible organizations, formats the organization path, fetches all pages for that organization, optionally enriches user records, stamps each page with org_login, and yields it. If one organization has disappeared or is inaccessible, it skips that organization and continues.

**Call relations**: GitHubConnector.paginate calls this for organization-scoped paths. It depends on _iter_user_orgs for the organization list, _paginate_link_header for GitHub paging, _enrich_users for member profile expansion, and with_context for stamping records.

*Call graph*: calls 3 internal fn (_enrich_users, _iter_user_orgs, _paginate_link_header); called by 1 (paginate); 2 external calls (Semaphore, with_context).


##### `GitHubConnector._repo_pages`  (lines 381–452)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Fetches one bounded slice of one repository-scoped stream. It translates the sync walker’s resume instructions into GitHub query parameters or local filtering, then reports what time range each page covered.

**Data flow**: It receives the client, stream, API path, one repository key, and a PartitionBound describing the current window. It builds the repository URL and parameters, including state=all for issues and pull requests, since filters for ascending streams, and until/since filters for commit backfills. It fetches pages, removes pull requests from the issues stream, filters newest-first event streams when GitHub cannot do it server-side, stamps records with repo_full_name, calculates high and low cursor values, and yields WalkPage objects. If GitHub says this repository cannot be read, it raises a partition skip instead of failing the whole stream.

**Call relations**: _repo_stream_pages gives this function to PartitionWalk as the per-repository page reader. It uses _paginate_link_header for raw API paging, _cursor_bounds to describe page ranges, with_context to stamp records, and PartitionSkipped to tell the walker a single repository should be skipped.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); 3 external calls (__init__, __init__, with_context).


##### `GitHubConnector._iter_user_repos`  (lines 454–462)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: Discovers the repositories that repository-scoped streams should read. It deliberately uses organization grants rather than the broader /user/repos endpoint, so it stays inside the organization access model.

**Data flow**: It receives the HTTP client. It walks repository pages for each granted organization, extracts a reliable owner and repository name from each repository record, and yields owner/repo pairs when it can identify them.

**Call relations**: _repo_partitions calls this to feed PartitionWalk. It relies on _iter_granted_org_repo_pages to get repository records and _repo_identity to normalize each repository’s name.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (_repo_partitions).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 464–483)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: Reads repository pages for every organization the credential can see, while ignoring archived repositories and forks. This keeps later per-repository syncing focused on active organization-owned repositories.

**Data flow**: It receives the HTTP client. It gets organization logins, requests each organization’s repositories with the configured page size and sort order, filters out archived and forked repositories, and yields only non-empty repository pages paired with the organization login. If one organization refuses access or is gone, it skips that organization.

**Call relations**: Both _repository_pages and _iter_user_repos call this. It uses _iter_user_orgs as the root organization list and _paginate_link_header to walk each organization’s repository pages.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, _repository_pages).


##### `GitHubConnector._iter_user_orgs`  (lines 485–506)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Finds the GitHub organizations visible to the credential. This is the root of almost every fan-out in the connector.

**Data flow**: It receives the HTTP client. It requests /user/orgs, follows pages, reads each organization login, and yields valid non-empty logins. If GitHub returns the access-denied status used when organization enumeration is not allowed, it raises StreamSkipped so the run records a skip instead of a failure.

**Call relations**: _iter_granted_org_repo_pages and _org_stream_pages both depend on this organization list. It uses _paginate_link_header for paging and StreamSkipped to signal that the credential cannot read any organization-based stream.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, _org_stream_pages).


##### `GitHubConnector._enrich_users`  (lines 508–528)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: Replaces simple organization member records with fuller public GitHub user records when possible. This can add public profile fields such as name or email.

**Data flow**: It receives the client, a page of member records, and a semaphore, which is a small traffic light that limits how many profile requests run at once. It starts one enrichment task per member, waits for them all, and returns a new list containing enriched records where available and original records where not.

**Call relations**: _org_stream_pages calls this only for the users stream. It coordinates many calls to the nested one function using asyncio.gather so enrichment is concurrent but bounded.

*Call graph*: called by 1 (_org_stream_pages); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 514–526)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Fetches the fuller public profile for one GitHub member record. If the profile cannot be found, it keeps the original member record.

**Data flow**: It receives one member record from the surrounding enrichment function. It reads the login, waits for permission from the semaphore, requests /users/{login}, and returns the JSON profile if GitHub returns an object. If there is no usable login, no body, a non-object body, or a 404 response, it returns the original member record.

**Call relations**: This helper is created and used inside _enrich_users. _enrich_users launches it for every member in a page, then gathers the results into the enriched users page.


##### `GitHubConnector._paginate_link_header`  (lines 530–538)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks GitHub list endpoints using GitHub’s standard next-page link headers. It hides the repeated work of following pagination links.

**Data flow**: It receives the client, path, and optional query parameters. It asks the parent REST connector to fetch pages using the fixed GitHub page size and the local record parser, then yields each list of records. Empty responses become empty pages rather than errors.

**Call relations**: This is the common low-level paging helper used by direct streams, organization discovery, repository discovery, organization-scoped streams, and repository-scoped streams. It delegates response parsing to _parse_records through the parent paging machinery.

*Call graph*: called by 5 (_iter_granted_org_repo_pages, _iter_user_orgs, _org_stream_pages, _repo_pages, paginate).


##### `_partition_field`  (lines 541–549)

```
def _partition_field(path: str) -> str | None
```

**Purpose**: Decides which context field should be present on records from a given API path. Repository paths need repo_full_name, organization paths need org_login, and unpartitioned paths need no stamp.

**Data flow**: It receives a path template. It checks whether the template contains repository or organization placeholders, then returns the matching context field name or nothing.

**Call relations**: GitHubConnector.flatten calls this before scoping a primary key. Its answer tells flatten which partition stamp must exist on the record.

*Call graph*: called by 1 (flatten).


##### `_parse_records`  (lines 552–556)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Turns an HTTP response from GitHub into the list of records expected by the paging code. GitHub list endpoints usually return a bare JSON array.

**Data flow**: It receives an HTTP response. If the response body is empty, it returns an empty list. Otherwise it parses the JSON and returns it only if it is a list; any other JSON shape becomes an empty list.

**Call relations**: _paginate_link_header supplies this parser to the parent link-header paging helper. That keeps page fetching generic while this function applies GitHub’s expected response shape.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 559–574)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: Extracts a dependable owner and repository name from a GitHub repository record. It handles the different shapes GitHub may provide.

**Data flow**: It receives a repository record and an optional fallback owner. It first tries full_name, then owner.login plus name, then fallback owner plus name. If none of those form a valid pair, it returns nothing.

**Call relations**: _iter_user_repos calls this while converting repository records into repository partitions. The returned owner/repo pair becomes part of the partition list used by repository-scoped syncs.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 577–587)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: Finds the highest and lowest cursor values in a page of records. The sync walker uses these bounds to know what time range a page covered.

**Data flow**: It receives a page and the name of the cursor field, which may be a nested path such as commit.committer.date. If there is no cursor field or no string cursor values, it returns two empty values. Otherwise it returns the maximum and minimum cursor strings found on the page.

**Call relations**: _repo_pages calls this for repository-scoped streams that need resume tracking. It uses get_path so nested cursor fields are read the same way as flat fields.

*Call graph*: called by 1 (_repo_pages); 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/providers/jira.py`

`io_transport` · `source sync`

This connector is the bridge between an Atlassian Jira Cloud account and the project’s source-sync system. Jira data lives behind Atlassian’s web API, and the API is split by “site,” meaning one login may have access to several Jira workspaces. This file first asks Atlassian which sites the current OAuth grant can reach, then reads each supported kind of Jira data from the right site-specific path.

Most Jira lists are paged, like a long book read one chapter at a time. The connector repeatedly asks for the next page using Jira’s `startAt` and `maxResults` fields until Jira says there is nothing left. Issues, comments, and sprints can also be read incrementally using a cursor, which is a stored timestamp that says “only fetch things updated after this point.” That keeps later syncs from rereading everything.

The file also protects the sync from normal permission problems. If Jira says the grant is not allowed to read a site or resource, the stream is skipped instead of failing the whole run. Finally, it converts selected records into readable text. For issues and comments, Jira often stores rich text as Atlassian Document Format, a nested tree; this file walks that tree and extracts the human words.

#### Function details

##### `JiraConnector.paginate`  (lines 73–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway for reading a Jira stream. Given a stream name, it chooses the right Jira-reading method and yields pages of records for the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor timestamp. It routes to the matching reader for projects, issues, comments, users, boards, or sprints, then passes each page of records outward. If Jira rejects access with a 401 or 403 response, it turns that into a skip notice rather than a hard failure.

**Call relations**: The source-sync runner calls this when it wants records from a Jira stream. This function then delegates to _projects, _issues, _comments, _users, _boards, or _sprints. If the stream is unknown or access is refused, it raises StreamSkipped so the wider run can continue safely.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 106–110)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Jira Cloud sites the current authorization can access. Each site has a cloud ID, and that ID is needed to build all later Jira API paths.

**Data flow**: It receives the HTTP client, sends a request to Atlassian’s accessible-resources endpoint, and reads the JSON response. It returns a list of site dictionaries, or an empty list if the response has no usable list.

**Call relations**: _projects, _issues, _users, and _boards call this before reading site-specific data. It is the connector’s map of which Jira workspaces exist for this login.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 112–132)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a paginated Jira list, one page at a time. It is the shared helper for Jira endpoints that use `startAt` and `maxResults` to move through long result sets.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, and the JSON key where records live. It repeatedly requests pages, extracts the records under that key, yields non-empty pages, and stops when Jira says it is on the last page or no more records remain.

**Call relations**: _projects, _issues, _comments, _boards, and _sprints use this instead of each reimplementing paging. It calls records_at to safely pull the list of records out of Jira’s response envelope.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 134–141)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira projects from every accessible Jira site. Projects are the top-level containers where Jira issues live.

**Data flow**: It first gets the accessible sites. For each site with a valid cloud ID, it requests the project search endpoint page by page, then adds context such as the cloud ID and site URL to each project record before yielding it.

**Call relations**: paginate calls this when the requested stream is projects. It relies on _sites to know where to look, _offset_values to read paged API results, and with_context to attach site information that later steps need.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 143–155)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira issues, optionally only those updated after a saved cursor. Issues are the main work items in Jira, such as bugs, tasks, and stories.

**Data flow**: It builds a Jira Query Language request, which is Jira’s search syntax, ordering issues by update time and filtering after the cursor if one is present. For each accessible site, it fetches pages of matching issues with a selected set of useful fields, adds site context, and yields the pages.

**Call relations**: paginate calls this for the issues stream, and _comments also calls it so it can discover which issues may have comments. It uses _sites for accessible workspaces, _offset_values for Jira paging, and with_context to keep each issue tied to its site.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 157–177)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads comments attached to Jira issues. Because Jira comments are reached through an issue, it first finds issues and then visits each issue’s comment endpoint.

**Data flow**: It reads all issues without applying an issue cursor, then inspects each issue for its ID and cloud ID. For each valid issue, it fetches comment pages, filters comments by the comment cursor if supplied, adds issue and site context, and yields only pages that still contain comments.

**Call relations**: paginate calls this for the issue_comments stream. It depends on _issues to find the parent issues, _offset_values to page through each issue’s comments, and with_context to record which issue and site each comment came from.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 179–190)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira users from each accessible site. Users are fetched differently from many Jira collections because the endpoint returns a plain JSON list instead of the usual envelope with a `values` field.

**Data flow**: It gets the accessible sites, then for each site calls the users search endpoint with a starting offset and page size. It converts the JSON response into a list, and if users are present, adds the cloud ID and site URL before yielding them.

**Call relations**: paginate calls this when syncing users. It uses _sites to discover Jira workspaces, list_or_empty to make the raw JSON safe to treat as a list, and with_context to attach where each user came from.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 192–199)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira Agile boards from every accessible site. Boards are views used by Jira teams to organize work, and they are also needed to find sprints.

**Data flow**: It asks for accessible sites, skips any site without a valid cloud ID, and then reads the Agile board endpoint page by page. Each board page is enriched with the cloud ID and site URL before being yielded.

**Call relations**: paginate calls this for the boards stream, and _sprints calls it because sprints are listed under a board. It uses _sites for site discovery, _offset_values for paging, and with_context for site metadata.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 201–215)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads sprints from Jira Agile boards, optionally only those updated after a saved cursor. A sprint is a time-boxed work period used by many Jira teams.

**Data flow**: It first reads boards, then for each board with a valid board ID and cloud ID, it requests that board’s sprint endpoint page by page. If a cursor is present, it keeps only sprints whose updatedDate is newer, adds board and cloud context, and yields the remaining sprint pages.

**Call relations**: paginate calls this for the sprints stream. It depends on _boards to discover where sprints can be found, _offset_values to read sprint pages, and with_context to keep each sprint tied to its board.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 217–224)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This normalizes Jira issue records so the sync system can see the issue update timestamp in the expected place. Without this, issue cursors would miss the `updated` value because Jira nests it inside `fields`.

**Data flow**: It receives one record and its stream description. For issues, it safely reads the nested `fields` dictionary and copies `fields.updated` to a top-level `updated` field; for all other streams, it returns the record unchanged.

**Call relations**: This fits into the record-processing step after records are fetched. It calls _dict_or_empty to avoid errors if Jira sends an unexpected shape, then hands back a record whose cursor field is easier for the sync system to read.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 226–252)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Jira records into readable text for storage and later recall. It gives issues and comments human-friendly titles and bodies instead of leaving them as raw JSON.

**Data flow**: It receives a Jira record and stream description. For issues, it extracts summary, status, priority, assignee, reporter, and description text; for comments, it extracts the author and comment body. Other stream types are passed to the parent renderer. It returns a title and a formatted text page.

**Call relations**: This is used when fetched records are being converted into searchable pages. It calls small helpers to safely read strings and dictionaries, format people and field lines, and turn Atlassian Document Format trees into plain text.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 255–256)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns a value into a string only if it already is one. It prevents unexpected Jira data shapes from leaking into rendered text.

**Data flow**: It receives any value. If the value is a string, it returns it; otherwise it returns an empty string.

**Call relations**: render uses this when reading issue fields, and _person uses it when choosing a display name or email address. It is a small guardrail around data from an external API.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 259–260)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This safely treats a value as a dictionary only when it really is one. It avoids crashes when Jira returns missing or differently shaped nested data.

**Data flow**: It receives any value. If the value is a dictionary, it returns that dictionary; otherwise it returns an empty dictionary.

**Call relations**: flatten uses this to read issue fields, render uses it for nested issue data, and _person uses it for user-like records. It keeps the rest of the code simple and defensive.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 263–265)

```
def _person(value: Any) -> str
```

**Purpose**: This extracts a readable name for a Jira person. It prefers the person’s display name, and falls back to their email address.

**Data flow**: It receives a value that may represent a Jira user. It safely treats it as a dictionary, reads `displayName` and `emailAddress` as strings, and returns the first non-empty one.

**Call relations**: render calls this when displaying assignees, reporters, and comment authors. It relies on _dict_or_empty and _str so missing user details simply become blank text.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 268–269)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: This formats one labeled line of issue metadata, such as `Status: In Progress`. It leaves the line out when there is no value to show.

**Data flow**: It receives a label and a value string. If the value is non-empty, it returns `label: value`; if the value is empty, it returns an empty string.

**Call relations**: render uses this while building the readable issue metadata block. This keeps blank fields like missing priority or assignee from creating noisy empty lines.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 272–289)

```
def _doc_text(value: Any) -> str
```

**Purpose**: This extracts plain words from Atlassian Document Format, the nested rich-text structure Jira uses for descriptions and comments. It turns a tree of content into readable text lines.

**Data flow**: It receives any value, usually a nested dictionary or list from Jira. It walks through the structure, collects every string stored under a `text` key, joins the collected pieces with newlines, trims extra whitespace, and returns the final plain text.

**Call relations**: render calls this when it needs issue descriptions or comment bodies. Inside, it uses the nested walk helper to visit every part of the document tree.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 277–286)

```
def walk(node: Any) -> None
```

**Purpose**: This is the recursive helper that does the actual tree-walking for _doc_text. Recursive means it can call the same logic on each child inside a nested structure.

**Data flow**: It receives one node from the document tree. If the node is a dictionary, it collects its `text` value when present and then walks its children under `content`; if the node is a list, it walks each item in the list. It does not return a value, but it adds found text to the surrounding `chunks` list.

**Call relations**: _doc_text starts this helper on the original Jira document value. The helper repeatedly descends into child nodes until all reachable text leaves have been collected.


### `extensions/sources/ufo_ext_sources/providers/linear.py`

`io_transport` · `source sync`

Linear is a project and issue tracker, and it exposes its data through GraphQL, a web API style where the caller sends a query describing exactly which fields it wants. This file is the Linear source connector: it defines which Linear collections can be synced, what GraphQL query to send for each one, how to page through large result sets, and how to make selected records readable after they are fetched.

The main idea is like reading a long book one page at a time. Linear returns a page of records plus a marker for the next page. `LinearConnector.paginate` sends a request, yields the records it got, then uses Linear’s next-page marker until there is nothing left. For most streams it also asks Linear for only records updated since the last saved cursor, using the `updatedAt` timestamp. A few Linear collections do not support that filter, so they are reread in full each time.

The file is careful about failures. If Linear says the token is invalid or lacks permission, the stream is marked as skipped rather than pretending it succeeded. If GraphQL returns errors, the sync fails loudly so the system does not save a partial or misleading result.

For issues, projects, comments, and users, the connector also builds friendly page text instead of storing only raw API-shaped data. This matters because the synced output is meant to be recalled and read by people, not just stored as machine data.

#### Function details

##### `_stream`  (lines 35–45)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates the small stream description object used by the connector to say, “Linear has a collection with this name, these timestamp fields, and maybe this is a main content stream.” It keeps the stream list short and consistent instead of repeating the same setup for every Linear collection.

**Data flow**: It takes a stream name, an optional cursor field name, and a flag saying whether the stream is canonical content. It fills in the standard Linear timestamp fields, creates a `StreamSpec` object, and returns that object for use in the connector’s stream catalog.

**Call relations**: This helper is used while the file is loaded to build `LINEAR_STREAMS`, the list of Linear collections the connector offers. Its only handoff is to `StreamSpec`, the shared source framework type that records how a stream should be synced.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 265–308)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the core reader for Linear. It sends the right GraphQL query for a stream, follows Linear’s page-by-page navigation, and yields batches of records for the rest of the sync system to process.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous run. It looks up the stream’s GraphQL query, adds an `updatedAt` filter when the stream supports incremental syncing, sends a POST request to Linear, checks for permission and GraphQL errors, extracts the `nodes` list from the response, and yields that list. It then reads Linear’s `pageInfo` to decide whether to request the next page, stopping when there is no next page or the response is not in the expected shape.

**Call relations**: The source framework calls this method when it wants records for one Linear stream. Inside the loop, it uses the connector’s shared POST request behavior and uses `list_or_empty` to safely turn the API’s `nodes` value into a list. If Linear refuses access with an authorization status, it raises `StreamSkipped`, which tells the wider sync run to record that this stream could not be read because of permissions rather than treating it as an empty success.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 310–352)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Linear records into readable page titles and page bodies. It makes important objects like issues, projects, comments, and users useful to a person reading or searching synced content, instead of leaving them as raw API data.

**Data flow**: It receives one Linear record and the stream it came from. For issues, projects, comments, and users, it pulls out human-friendly fields such as title, description, state, assignee, target date, name, and email. It builds a title, creates labeled summary lines when useful, and returns both the title and a Markdown-like text body headed with the Linear stream name. If a comment has no title, it uses the first non-empty line of the body, and if that still fails it falls back to the base connector’s default title behavior. For streams it does not customize, it delegates to the parent connector’s rendering.

**Call relations**: The sync framework calls this after records have been fetched and need to become recallable pages. This method relies on `_str` to safely read string fields, `_ref_id` to pull IDs out of nested reference objects, and `_labeled` to format small pieces of metadata. When it cannot or should not custom-render a stream, it hands the record back to the shared `RestConnector` rendering behavior.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 355–356)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny safety helper returns a value only if it is actually a string. It prevents accidental `None`, numbers, or nested objects from being inserted into readable page text.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged; otherwise it returns an empty string. Nothing else is changed.

**Call relations**: It is used by `LinearConnector.render` whenever the connector pulls text fields out of a Linear record. `_ref_id` also uses it after finding an `id` field, so nested references get the same safe string treatment.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 359–360)

```
def _ref_id(value: Any) -> str
```

**Purpose**: This helper extracts the `id` from a nested Linear reference, such as an assignee or project lead. It is used when the page body should mention the linked object without expanding the whole nested object.

**Data flow**: It receives any value. If the value is a dictionary-like record, it reads its `id` field and passes that through `_str`; if the value is not a dictionary, it returns an empty string. The output is always a plain string, possibly empty.

**Call relations**: It is called by `LinearConnector.render` while building readable metadata for issues and projects. It delegates the final type check to `_str`, keeping reference formatting consistent with ordinary string fields.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 363–364)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This helper formats small metadata facts as readable lines like `state: started` or `email: person@example.com`. It skips missing values so the page body does not fill up with empty labels.

**Data flow**: It receives a list of label-and-value pairs. It keeps only the pairs whose value is not empty, formats each as `label: value`, joins them with line breaks, and returns the resulting text block.

**Call relations**: It is called by `LinearConnector.render` when building the summary sections for issues, projects, and users. It does not call into the rest of the connector; it simply gives the render method a clean way to turn selected fields into readable prose.

*Call graph*: called by 1 (render).


### Board-based work management
Connector for monday.com boards, items, updates, activity logs, teams, workspaces, tags, and related collaboration records.

### `extensions/sources/ufo_ext_sources/providers/monday.py`

`io_transport` · `source sync`

monday.com exposes its data through GraphQL, which is an API style where the caller sends a query describing exactly what fields it wants. This file is the monday.com connector: it knows which queries to send, how to page through large result sets, and how to shape the returned records into a common form.

The file defines several stream descriptions, such as users, boards, items, and activity logs. A stream is one kind of object the sync system can pull. The connector reads those streams without ever writing back to monday.com.

Most top-level monday.com lists use page numbers, so `_paged_root` repeatedly asks for page 1, page 2, and so on until there is nothing left. Items are different: monday.com gives an opaque cursor, like a “next page ticket,” so `_items` follows that ticket until it runs out. Some data, such as activity logs, must be fetched board by board.

The connector also supports incremental syncing. monday.com does not let these queries ask “only records since this time,” so the connector filters each downloaded page itself using stored timestamp cursors like `updated_at` or `created_at`.

If monday.com refuses a query or reports a GraphQL error, the connector raises `StreamSkipped`. That tells the larger sync run to record a clean skip instead of saving half-trusted data.

#### Function details

##### `_extract_person_ids`  (lines 70–98)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: This helper pulls assigned-person IDs out of monday.com item column data. monday.com stores assignees inside flexible board-specific columns, so this function looks for columns marked as people columns instead of relying on a fixed column name.

**Data flow**: It receives a value that should be a list of column records. It ignores anything that is not a list, skips non-people columns, parses the people column’s JSON value when needed, keeps only entries marked as real people, and returns their IDs as strings. It does not change the original input.

**Call relations**: During item syncing, `MondayConnector._items` calls this after receiving item records from monday.com. The extracted IDs are added to each item as `assignee_ids`, making assignments easier for later parts of the system to use.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 106–118)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This is the connector’s central way to talk to monday.com’s GraphQL API. It sends one query, checks whether monday.com reported an error, and returns the useful `data` section.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional variables. It posts them to monday.com, inspects the response, raises `StreamSkipped` if monday.com returned GraphQL errors, and otherwise returns the response’s `data` object or an empty dictionary if the shape is unexpected.

**Call relations**: `_paged_root`, `_items`, `_activity_logs`, and `_single_root` all use this function whenever they need data from monday.com. It is the shared gatekeeper that keeps API error handling consistent across all streams.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, _single_root).


##### `MondayConnector._paged_root`  (lines 120–143)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads monday.com collections that use ordinary page numbers, such as users, workspaces, boards, or updates. It keeps asking for the next page until monday.com returns no records.

**Data flow**: It receives the API client, the GraphQL field to query, the field selection to request, and optionally a timestamp cursor. It fetches one page at a time, turns missing or malformed lists into an empty list, filters out old records when a cursor is supplied, yields each non-empty page, and stops when there are no records left after filtering.

**Call relations**: `MondayConnector._boards` uses this to collect all boards. `MondayConnector._stream_pages` uses it directly for several streams. Each page request goes through `_graphql`, so API errors are handled in the common way.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, _stream_pages); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 145–156)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function fetches the full list of monday.com boards with the board details needed by other streams. Boards are important because items and activity logs are fetched board by board.

**Data flow**: It receives the API client, asks `_paged_root` for all board pages, extends one output list with every board it sees, and returns that complete list. It includes board information such as name, description, state, timestamps, URL, and workspace.

**Call relations**: `MondayConnector._items` and `MondayConnector._activity_logs` call this first so they know which boards to visit. It delegates the actual page-by-page API work to `_paged_root`.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 158–217)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads items from every monday.com board. It also enriches each item with a simple list of assigned-person IDs pulled from monday.com’s flexible column format.

**Data flow**: It receives the API client and an optional timestamp cursor. It first gets all boards, then for each board asks for the first item page and follows monday.com’s item-page cursor for later pages. For every item, it extracts assignee IDs from `column_values`, filters out items older than the cursor when one is provided, yields non-empty batches, and moves on when there is no next-page cursor.

**Call relations**: `MondayConnector._stream_pages` calls this when the requested stream is `items`. This function calls `_boards` to find boards, `_graphql` to fetch item pages, `_extract_person_ids` to simplify assignee data, and small safe-conversion helpers to avoid crashing on missing response fields.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (_stream_pages); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 219–247)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads activity log entries from each monday.com board. Activity logs show what happened on a board, such as changes or events, and are linked back to their board.

**Data flow**: It receives the API client and an optional timestamp cursor. It gets all boards, asks monday.com for recent activity logs for each board, adds the board ID to every log entry, filters out entries older than the cursor when needed, and yields batches that contain at least one log.

**Call relations**: `MondayConnector._stream_pages` calls this for the `activity_logs` stream. It relies on `_boards` to know which boards exist and `_graphql` to fetch each board’s logs.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (_stream_pages); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 249–265)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the public paging entry used by the source-sync framework to read one monday.com stream. It also turns authentication or permission refusals into a clean stream skip.

**Data flow**: It receives an HTTP client, a stream description, and the saved cursor for incremental syncing. It asks `_stream_pages` for pages and yields them onward. If monday.com responds with HTTP 401 or 403, meaning unauthorized or forbidden, it raises `StreamSkipped`; other HTTP errors are allowed to continue upward as real failures.

**Call relations**: The wider connector framework calls this when it wants records for a stream. `paginate` then hands the stream name to `_stream_pages`, and it wraps that lower-level work with monday-specific refusal handling.

*Call graph*: calls 2 internal fn (__init__, _stream_pages).


##### `MondayConnector._stream_pages`  (lines 267–305)

```
def _stream_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function chooses the right fetching strategy for a named monday.com stream. It is the connector’s dispatcher: users are fetched one way, items another, activity logs another.

**Data flow**: It receives the API client, a stream name, and an optional cursor. It compares the name to the supported stream types and returns the appropriate asynchronous page producer. If the name is not implemented, it raises `StreamSkipped` so the sync run records that the stream could not be read.

**Call relations**: `MondayConnector.paginate` calls this after the framework requests a stream. It routes work to `_paged_root`, `_single_root`, `_items`, or `_activity_logs` depending on the stream, so the rest of the connector does not need one giant fetching routine.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _items, _paged_root, _single_root); called by 1 (paginate).


##### `MondayConnector._single_root`  (lines 307–318)

```
async def _single_root(self, client: httpx.AsyncClient, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads small monday.com collections that can be fetched in one request: teams and tags. These do not need the page-by-page loop used by larger collections.

**Data flow**: It receives the API client and the collection name. It builds the correct GraphQL query for teams or tags, sends it through `_graphql`, checks that the returned value is a list, and yields that list if it contains records.

**Call relations**: `MondayConnector._stream_pages` calls this for the `teams` and `tags` streams. It still uses `_graphql`, so GraphQL errors are treated the same way as in the paged streams.

*Call graph*: calls 1 internal fn (_graphql); called by 1 (_stream_pages).


##### `MondayConnector.flatten`  (lines 320–361)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function normalizes monday.com records into fields the rest of the system can understand consistently. For example, it turns an update’s creator into an `author` field and links updates or activity logs back to their parent item or board.

**Data flow**: It receives one raw monday.com record and the stream description it belongs to. Depending on the stream, it copies the original fields and adds or standardizes fields such as `name`, `body`, `author`, `created_at`, `status`, `api_url`, and `parent_external_id`. It returns the reshaped record and does not send any network requests.

**Call relations**: No in-file caller is shown, because this is part of the connector interface used by the source framework after records are fetched. Inside the function, update records use `dict_or_empty` to safely read the nested creator object before choosing an author value.

*Call graph*: 1 external calls (dict_or_empty).


### Operational response systems
Connectors for incident response and observability platforms that sync incidents, services, schedules, on-call records, projects, issues, events, members, and releases.

### `extensions/sources/ufo_ext_sources/providers/pagerduty.py`

`io_transport` · `source sync run`

PagerDuty is an incident management service, and its API returns information in separate collections: users, incidents, teams, schedules, and so on. This file is the read-only bridge between PagerDuty and the project’s source-sync system. Without it, the system would not know which PagerDuty endpoints to call, how to move through pages of results, or how to resume from the last item it already saw.

The file first defines the available streams. A stream is one kind of data to sync, such as "incidents" or "users". Each stream says where the data appears in PagerDuty’s response, which field uniquely identifies a record, and, where possible, which timestamp can be used as a bookmark for incremental syncing.

The main class, `PagerDutyConnector`, uses the shared REST connector machinery but adds PagerDuty-specific rules. It sets the correct API base address, adds PagerDuty’s required versioned `Accept` header, and understands PagerDuty’s page-by-page response style. Think of pagination like reading a long report one folder at a time: PagerDuty says whether there is another folder and how big the current one was.

Incidents get special treatment because they can be requested in update-time order and resumed from a cursor. Incident notes are different again: they are fetched by first reading incidents, then asking PagerDuty for the notes under each incident. If PagerDuty refuses access with a 401 or 403 status, the connector marks that stream as skipped rather than crashing the whole sync.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 74–77)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to PagerDuty and adds the special header PagerDuty requires for version 2 of its API. A header is extra information sent with every web request, like putting a required label on every envelope.

**Data flow**: It receives a base URL and a credential object. It asks the parent REST connector to build the normal authenticated client, then adds PagerDuty’s required `Accept` header. It returns that ready-to-use client, with authentication and PagerDuty API versioning prepared.

**Call relations**: This is part of the shared connector setup flow inherited from the base REST connector. Once the client is created, the other methods in this file use it to fetch PagerDuty pages and records.


##### `PagerDutyConnector._offset_pages`  (lines 79–99)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one PagerDuty stream page by page using PagerDuty’s offset-and-limit pagination style. It also filters out older records when a stream has a cursor, so a resumed sync does not repeat data it already processed.

**Data flow**: It receives an HTTP client, a stream description, optional query parameters, and an optional cursor value. It asks the shared REST helper for pages from the endpoint named by the stream, extracts the records, and, when possible, keeps only records newer than the cursor. It yields non-empty batches of records back to the caller.

**Call relations**: This is the common paging helper for most PagerDuty streams. `PagerDutyConnector._incidents` uses it with incident-specific sorting and cursor parameters, while `PagerDutyConnector.paginate` uses it directly for simpler streams such as users, teams, services, schedules, and on-calls.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 101–113)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads PagerDuty incidents in ascending update-time order, which makes incremental syncing safer and easier. If a previous sync left a cursor, it asks PagerDuty for incidents changed since that point.

**Data flow**: It receives an HTTP client and an optional cursor. It builds PagerDuty query parameters that sort incidents by `updated_at` from oldest to newest, and adds a `since` parameter when a cursor exists. It then delegates the actual page fetching to `_offset_pages` and yields each page of incident records.

**Call relations**: This is the incident-specific path used by `PagerDutyConnector.paginate` when the requested stream is incidents. `PagerDutyConnector._incident_notes` also calls it, because notes can only be found by first discovering the incidents they belong to.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 115–128)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads notes attached to PagerDuty incidents. PagerDuty does not expose these as one simple global list here, so the connector first walks through incidents and then fetches notes for each incident one by one.

**Data flow**: It receives an HTTP client and an optional cursor. It fetches incidents, takes each valid incident ID, calls the incident-notes endpoint for that ID, extracts the `notes` list from the response, and filters by `created_at` when a cursor is present. Before yielding notes, it adds the incident ID as context so each note stays connected to the incident it came from.

**Call relations**: This method is called by `PagerDutyConnector.paginate` for the `incident_notes` stream. It relies on `PagerDutyConnector._incidents` to find incidents, uses `records_at` to pull the notes list out of PagerDuty’s response, and uses `with_context` to attach the parent incident ID to each note.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 130–164)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading any PagerDuty stream. Given a stream request, it chooses the correct fetching strategy and yields batches of records to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. For incidents, it sends the work to `_incidents`; for incident notes, it sends the work to `_incident_notes`; for the simpler top-level streams, it uses `_offset_pages`. If the stream is unknown, it raises a skip signal, and if PagerDuty refuses access with 401 or 403, it turns that refusal into a skipped stream instead of a hard failure.

**Call relations**: The wider source-sync framework calls this method when it wants records for a PagerDuty stream. This method then hands off to the specialized helpers in this file. It also uses `StreamSkipped` to report unsupported or unauthorized streams in a controlled way, so the overall run can continue where appropriate.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/providers/sentry.py`

`io_transport` · `source sync`

Sentry is a service teams use to track software errors and releases. This connector is the bridge between Sentry’s web API and UFO’s source-sync system. Without it, UFO would not know which Sentry endpoints to call, how to move through Sentry’s pages of results, or how to label records with the organization and project they came from.

The file first describes the available Sentry streams, such as organizations, projects, issues, events, members, and releases. A stream is a category of records that can be synced. Each stream says which field identifies a record and which date field can be used to resume from a previous sync.

The main class, SentryConnector, is read-only. It does not create or change anything in Sentry. It asks Sentry for lists of records, follows Sentry’s pagination cursor from the HTTP Link header, and yields batches of plain dictionaries. For nested data, it works like walking a tree: first find organizations or projects, then use those names to fetch members, issues, events, or releases below them. When it returns nested records, it stamps them with helpful context like organization_slug and project_slug, so a later reader knows where each item came from.

If Sentry refuses a request because the key is invalid or lacks permission, the connector skips that stream with a clear explanation instead of crashing the whole sync.

#### Function details

##### `_sentry_next_cursor`  (lines 76–81)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper reads Sentry’s pagination instruction from an HTTP response header. Sentry tells clients where the next page is by putting a cursor token in the Link header; this function extracts that token when another page is available.

**Data flow**: It receives HTTP headers from a Sentry response. It looks for a Link header, searches it for the special next-page cursor where Sentry also says results are still available, and returns that cursor text. If the header is missing or does not contain a usable next cursor, it returns nothing.

**Call relations**: SentryConnector._paged_list calls this after every API request. The cursor it returns becomes the input for the next request, like using a bookmark to continue reading from the next page.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.record_ref`  (lines 89–93)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This chooses a stable human-friendly reference for a Sentry record. For organizations, it uses the organization slug instead of the default identifier, because the slug is the name people usually recognize in URLs and Sentry screens.

**Data flow**: It receives one Sentry record and the stream description it belongs to. If the record is from the organizations stream, it reads the slug field and returns it as text when possible. For every other stream, it lets the base connector choose the normal reference.

**Call relations**: The broader source framework uses this when it needs a short reference for a synced record. This method customizes that behavior only for organizations and otherwise hands the decision back to the shared RestConnector behavior.


##### `SentryConnector.paginate`  (lines 95–107)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the public paging entry for the connector. It streams batches of Sentry records for one requested stream and turns permission failures into a clean “skip this stream” signal.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It asks SentryConnector._stream_pages to produce pages of records and yields each page onward. If Sentry replies with 401 or 403, meaning unauthorized or forbidden, it raises StreamSkipped with a helpful reason; other HTTP errors are left unchanged.

**Call relations**: The sync runner calls this when it wants records from a Sentry stream. paginate delegates the actual choice of endpoint to SentryConnector._stream_pages, then protects the larger sync from expected permission problems by converting them into StreamSkipped.

*Call graph*: calls 2 internal fn (__init__, _stream_pages).


##### `SentryConnector._stream_pages`  (lines 109–122)

```
def _stream_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function routes each stream name to the right Sentry-reading method. It is the connector’s switchboard: organizations and projects go one way, issues and events another, and unsupported names are rejected clearly.

**Data flow**: It receives an HTTP client, a stream name, and an optional cursor. It checks the stream name and returns the matching async page iterator: root pages, members, issues, events, or releases. If the name is not implemented, it raises StreamSkipped instead of pretending it can sync it.

**Call relations**: SentryConnector.paginate calls this after the sync runner asks for a stream. _stream_pages then hands control to SentryConnector._root_pages, SentryConnector._members, SentryConnector._issues, SentryConnector._events, or SentryConnector._releases depending on what is being synced.

*Call graph*: calls 6 internal fn (__init__, _events, _issues, _members, _releases, _root_pages); called by 1 (paginate).


##### `SentryConnector._root_pages`  (lines 124–137)

```
async def _root_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This returns the top-level Sentry lists: organizations or projects. These are called “root” pages because other streams, such as members or issues, are found by first knowing the organization or project.

**Data flow**: It receives an HTTP client, either the organizations or projects name, and an optional cursor. It fetches all organizations or all projects. For projects, if a cursor is provided, it keeps only projects whose dateCreated value is newer than that cursor. If any records remain, it yields them as one page.

**Call relations**: SentryConnector._stream_pages uses this for the organizations and projects streams. It relies on SentryConnector._organizations and SentryConnector._projects to do the API fetching, then supplies the resulting page back up to paginate.

*Call graph*: calls 2 internal fn (_organizations, _projects); called by 1 (_stream_pages).


##### `SentryConnector._paged_list`  (lines 139–156)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the common loop for reading any Sentry endpoint that returns a list. It keeps requesting pages until Sentry stops giving a next-page cursor.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It sends a request, reads the JSON response, keeps only list items that are dictionaries, yields those records if there are any, then reads the next cursor from the response headers. If there is a next cursor, it repeats with that cursor added to the query; if not, it stops.

**Call relations**: Most specific fetchers use this because Sentry paginates many endpoints the same way. SentryConnector._organizations, SentryConnector._projects, SentryConnector._members, SentryConnector._issues, SentryConnector._events, and SentryConnector._releases all call it, and it calls _sentry_next_cursor to know when to continue.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 158–162)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches all organizations visible to the Sentry credential. Organizations are the top-level containers that members and releases are attached to.

**Data flow**: It receives an HTTP client. It asks SentryConnector._paged_list for every page from the /organizations/ endpoint, appends all returned records into one list, and returns that list.

**Call relations**: SentryConnector._root_pages calls this when syncing organizations. SentryConnector._members and SentryConnector._releases also call it first because they need each organization slug before they can ask Sentry for members or releases under that organization.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, _root_pages).


##### `SentryConnector._projects`  (lines 164–168)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches all projects visible to the Sentry credential. Projects are needed before the connector can fetch project-specific issues and events.

**Data flow**: It receives an HTTP client. It asks SentryConnector._paged_list for every page from the /projects/ endpoint, gathers all page records into one list, and returns that list.

**Call relations**: SentryConnector._root_pages calls this when syncing projects. SentryConnector._issues and SentryConnector._events also call it because they must know each project’s organization slug and project slug before building the project-specific API path.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, _root_pages).


##### `SentryConnector._members`  (lines 170–176)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches organization members from Sentry. It first discovers organizations, then asks for the member list inside each one.

**Data flow**: It receives an HTTP client. It loads organizations, reads each organization slug, skips organizations without a usable slug, then fetches pages from that organization’s members endpoint. Before yielding each page, it adds organization_slug to every record so the member can be traced back to its organization.

**Call relations**: SentryConnector._stream_pages calls this for the members stream. _members depends on SentryConnector._organizations to find where to look, uses SentryConnector._paged_list to read Sentry’s pages, and uses with_context to attach the organization label before returning data upward.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (_stream_pages); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 178–192)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches Sentry issues for every visible project. If the sync has a cursor, it asks Sentry only for issues seen after that cursor, which avoids rereading older issue activity.

**Data flow**: It receives an HTTP client and an optional cursor. It loads projects, extracts each project’s organization slug and project slug, skips projects missing either value, and builds an issues API path for that project. If there is a cursor, it sends a query like lastSeen greater than the cursor. Each returned page is stamped with organization_slug and project_slug before being yielded.

**Call relations**: SentryConnector._stream_pages calls this for the issues stream. _issues calls SentryConnector._projects to discover project locations, SentryConnector._paged_list to read each project’s issue pages, and with_context to preserve where each issue came from.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (_stream_pages); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 194–208)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches Sentry events for every visible project. Events are individual occurrences, so this stream is filtered by event timestamp when a cursor is available.

**Data flow**: It receives an HTTP client and an optional cursor. It loads all projects, finds each project’s organization slug and project slug, skips incomplete project records, and requests the project’s events endpoint. If a cursor exists, it adds a query asking for events whose timestamp is newer than that cursor. It adds organization_slug and project_slug to each page before yielding it.

**Call relations**: SentryConnector._stream_pages calls this for the events stream. _events follows the same project-first pattern as issues: it gets projects from SentryConnector._projects, reads pages through SentryConnector._paged_list, and uses with_context to label the returned records.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (_stream_pages); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 210–221)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches releases for each visible Sentry organization. Releases are organization-level records, so the connector walks through organizations rather than projects.

**Data flow**: It receives an HTTP client and an optional cursor. It loads organizations, reads each organization slug, skips organizations without a usable slug, then requests that organization’s releases endpoint. If a cursor exists, it keeps only releases whose dateCreated value is newer than the cursor. Non-empty pages are labeled with organization_slug and yielded.

**Call relations**: SentryConnector._stream_pages calls this for the releases stream. _releases uses SentryConnector._organizations to find the organizations, SentryConnector._paged_list to fetch release pages, and with_context to attach the organization slug before the data goes back to the paging flow.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (_stream_pages); 1 external calls (with_context).


### Enterprise work management
Connector for Wrike contacts, folders, tasks, comments, workflows, and custom fields as read-only syncable work records.

### `extensions/sources/ufo_ext_sources/providers/wrike.py`

`io_transport` · `source sync / request handling`

Wrike’s API returns lists of things, such as tasks or folders, in pages. This file provides a Wrike connector that knows which Wrike collections are available, how to ask Wrike for each page, and how to shape Wrike’s raw answers into friendlier records. Without this file, the system would not know how to sync Wrike content into its source pipeline.

The central piece is `WrikeConnector`, which inherits from a shared `RestConnector`. Think of the shared connector as a delivery truck, and this file as the Wrike-specific route map: which roads to take, which packages to pick up, and how to label them when they arrive.

Wrike uses a `nextPageToken` to say “there is another page after this one.” The connector follows those tokens until all pages are read. Wrike does not provide a dependable “only show me changes since this time” filter, so incremental syncing is done after records arrive: if a stored cursor exists, the connector drops records whose `updatedDate` is not newer.

If Wrike refuses access with an authorization error, the connector skips that stream with a clear explanation instead of crashing the whole sync. The file also normalizes common fields, such as making task titles into `name`, comment text into `body`, and contact profile emails into a top-level `email`.

#### Function details

##### `_profile_email`  (lines 54–64)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper looks inside a Wrike contact record and finds the first usable email address in its profile list. It exists because Wrike stores email addresses inside nested profile objects rather than as a simple top-level field.

**Data flow**: It receives one Wrike contact record as a dictionary. It reads the record’s `profiles` value, checks that it is a list, then scans each profile for a non-empty string called `email`. It returns that email address when it finds one, or returns `None` if the record has no usable email.

**Call relations**: This function is used by `WrikeConnector.flatten` when contact records are being cleaned up. The flattening step asks `_profile_email` to pull the email out of Wrike’s nested shape so the final contact record has an easy-to-read top-level `email` field.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 72–96)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads Wrike records page by page for one stream, such as tasks or folders. It also applies the connector’s incremental-sync rule by keeping only records newer than the saved cursor when the stream has an update date.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. It first checks whether this stream is one this connector can actually run. Then it asks Wrike for pages under the stream’s API path, following Wrike’s `nextPageToken` until there are no more pages. For streams with an update cursor, it filters out records whose cursor field is not newer than the stored cursor. It yields each non-empty batch of records. If Wrike replies with a 401 or 403, meaning the credential is invalid or lacks permission, it turns that into a skipped stream message.

**Call relations**: The source runner calls this method when it wants records for a Wrike stream. The method relies on the shared REST connector’s cursor-page reader to do the repeated HTTP fetching. When a stream is unavailable or access is refused, it raises `StreamSkipped` so the larger sync can continue with a clear reason instead of failing unexpectedly.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 98–136)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method turns Wrike’s raw records into a more standard shape used by the rest of the system. It gives important fields common names, such as `name`, `created_at`, `body`, and `parent_external_id`, so downstream code does not need to know every Wrike-specific field name.

**Data flow**: It receives one raw Wrike record and the stream description that says what kind of record it is. For contacts, it builds a display name from first and last name, extracts an email through `_profile_email`, and maps the creation date. For folders, it uses the folder title as the name and adds an API URL. For tasks, it reads task dates safely, sets the name, status, due date, and creation time. For comments, it maps text to `body`, author ID to `author`, and task ID to the parent record link. For streams without special rules, it returns the record unchanged.

**Call relations**: After `WrikeConnector.paginate` has supplied raw records, the broader source pipeline calls `flatten` to prepare each record for indexing or storage. Inside that cleanup, it calls `_profile_email` for contact email extraction and `dict_or_empty` when reading task date details, so missing or malformed nested date data does not break the record conversion.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).
