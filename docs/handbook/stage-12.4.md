# Work, engineering, and incident source connectors  `stage-12.4`

This stage is a set of read-only “source connectors”: small adapters that know how to visit outside work tools, ask for data, and translate the answers into the project’s common record format. It is behind-the-scenes support for syncing and search. Nothing here changes data in those outside systems.

Each file speaks to one service. The Asana connector reads workspaces, projects, tasks, stories, and users, stepping through API pages like turning pages in a catalog. ClickUp walks its hierarchy from teams down to spaces, folders, lists, tasks, comments, fields, and goals. GitHub brings in organizations, repositories, issues, commits, pull requests, users, and activity. Jira reads projects, issues, comments, users, boards, and sprints. Linear uses GraphQL, a query language for APIs, to stream issues, projects, teams, labels, and comments. monday.com covers users, teams, workspaces, boards, items, updates, logs, and tags. PagerDuty reads incident-response data such as incidents, schedules, services, and on-call records. Sentry reads error-tracking projects, issues, events, members, and releases. Wrike reads contacts, folders, tasks, comments, workflows, and custom fields.

## Files in this stage

### Project-management task hierarchies
Connectors for task- and project-centric systems that expose work through nested projects, teams, lists, tasks, comments, and metadata.

### `extensions/sources/ufo_ext_sources/asana.py`

`io_transport` · `source sync`

This connector is the system’s read-only doorway into Asana. Asana is a work-tracking tool, and its API returns records in pages rather than all at once, like a long book split into chapters. This file lists the Asana “streams” the system can remember: core work items such as projects, tasks, stories, and users, plus supporting collections like teams, tags, sections, and workspaces.

Each stream is described with a small recipe: its name, the Asana object it comes from, the field that uniquely identifies each record, and sometimes the date field used to tell what changed since the last sync. Projects and tasks can be fetched incrementally using Asana’s `modified_since` option, meaning the connector can ask only for records changed after a saved time. Stories track their creation time, but the API path here does not use that for filtered fetching. Other streams are read fresh each run.

The main class, `AsanaConnector`, sets the Asana API base address and provides pagination. It asks Asana for up to 100 records, yields them to the rest of the system, then follows Asana’s `next_page.offset` token until there are no more pages. The connector does not store or write credentials itself, and it does not change anything in Asana.

#### Function details

##### `_stream`  (lines 24–38)

```
def _stream(name: str, *, cursor_field: str | None=None, updated_at_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a standard stream description for one Asana collection. It keeps the stream list compact and consistent, so every Asana object is described the same way.

**Data flow**: It takes a stream name and optional details such as the cursor field, updated-time field, and whether the stream is a main, canonical collection. It uses those values to create a `StreamSpec`, which is a small description object telling the rest of the sync system how to identify and track records from that collection.

**Call relations**: This function is used while the file is loaded to build `ASANA_STREAMS`. It hands each completed stream description to the connector class through `streams_list`, so the broader source framework knows which Asana collections can be read.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 74–90)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one Asana stream page by page. It hides Asana’s pagination details so the rest of the system can simply receive batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor value from a previous sync. It builds request parameters with a page size of 100, adds `modified_since` when the stream supports incremental fetching, calls Asana, extracts the `data` list safely, and yields any records found. If Asana returns a next-page offset, it repeats the request with that offset; when no valid offset remains, it stops.

**Call relations**: The source framework calls this method when it wants records for a particular Asana stream. Inside the loop, it relies on the connector’s HTTP get helper to fetch from Asana and uses `ufo.sdk.sources.list_or_empty` to turn the response’s `data` field into a safe list before passing batches onward to the sync pipeline.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/clickup.py`

`io_transport` · `during source sync`

ClickUp does not present everything as one simple list. Its data is more like a set of nested boxes: teams contain spaces, spaces contain folders and lists, and lists contain tasks, comments, and custom fields. This connector opens those boxes in the right order so the rest of the system can remember and search the contents later.

The file defines the ClickUp streams the system can read, such as teams, users, tasks, list comments, and goals. The ClickUpConnector class then provides the actual reading steps. It starts at teams, uses team IDs to find spaces, uses space IDs to find folders, and uses folder or space IDs to find lists. Once it reaches lists, it can fetch leaf data such as tasks, comments, and custom fields.

The connector also adds parent information to child records. For example, a task is stamped with the list it came from. This matters because otherwise a task would arrive without enough context to understand where it belongs. For incremental syncing, tasks and comments can be filtered by a saved cursor, meaning the connector can skip older records it has already seen. The file is read-only: it fetches ClickUp data but never creates or changes anything in ClickUp.

#### Function details

##### `ClickUpConnector._teams`  (lines 58–60)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the top-level ClickUp teams available to the authenticated account. This is the starting point for almost every other ClickUp read, because the rest of the hierarchy hangs under teams.

**Data flow**: It receives an HTTP client that can talk to ClickUp. It asks the ClickUp team endpoint for data, pulls out the list stored under the teams field, and returns that list as plain records.

**Call relations**: This is the first step in the hierarchy. _spaces calls it when it needs team IDs to find spaces, and paginate calls it directly when the requested stream is teams or when it needs teams to build users or goals.

*Call graph*: called by 2 (_spaces, paginate); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 62–70)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived spaces under every ClickUp team. A space is a major area inside a team, so this function moves the sync one level deeper.

**Data flow**: It starts by getting teams from _teams. For each team with a usable ID, it asks ClickUp for that team’s spaces, extracts the spaces list, adds the parent team_id to each space record, and returns one combined list.

**Call relations**: _folders calls this to discover where folders live, _lists calls it to find folderless lists, and paginate calls it when the spaces stream is requested. It depends on _teams because ClickUp requires a team ID before spaces can be requested.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 72–82)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived folders inside ClickUp spaces. Folders are another layer of organization before lists and tasks.

**Data flow**: It receives an HTTP client, gets spaces from _spaces, and for each valid space ID asks ClickUp for that space’s folders. It extracts the folder records, adds the parent space_id to each one, and returns them together.

**Call relations**: _lists calls this when it needs to find lists that live inside folders, and paginate calls it when the folders stream is requested. It builds on _spaces because folders cannot be found without first knowing the spaces.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 84–100)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Collects ClickUp lists, which are the containers that hold tasks, comments, and custom fields. It covers both lists inside folders and lists that sit directly inside spaces.

**Data flow**: It first gets folders from _folders and asks ClickUp for each folder’s lists, adding folder_id to those records. Then it gets spaces from _spaces and asks for lists directly under each space, adding space_id to those records. The result is one combined list of list records.

**Call relations**: _tasks and _list_child_stream call this because they need list IDs before they can fetch tasks, comments, or fields. paginate calls it directly for the lists stream. This function joins two branches of ClickUp’s layout: folder-based lists and folderless space lists.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _tasks, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 102–127)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads tasks from every discovered ClickUp list, page by page. It can also skip tasks that are not newer than a saved update marker, so repeated syncs do less work.

**Data flow**: It receives an HTTP client and an optional cursor, which is the last saved date_updated value. It gets all lists from _lists, then for each valid list ID requests task pages from ClickUp. Each task is tagged with its list_id and list_name. If a cursor is present, older or equal tasks are filtered out. It yields batches of task records as they are found.

**Call relations**: paginate calls this when the tasks stream is requested. This function depends on _lists because ClickUp tasks are fetched from a specific list, not from one global tasks endpoint. It hands batches back to paginate as soon as there are tasks to sync.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 129–148)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads smaller per-list data streams: list comments and list custom fields. These records belong to a specific list, so the function first discovers lists and then asks for each list’s child records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It gets all lists from _lists, chooses the correct ClickUp endpoint based on whether it is reading comments or fields, extracts the matching records, and adds list_id and list_name. For cursor-based streams such as comments, it filters out old records. It yields each non-empty batch.

**Call relations**: paginate calls this for the list_comments and list_custom_fields streams. It sits after _lists in the flow, because comments and fields can only be read once the connector knows which list they belong to.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 150–204)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the dispatcher for reading a requested ClickUp stream. Given a stream name, it chooses the right helper function, fetches the data, and yields records in batches for the sync framework.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name and follows the matching path: teams, users, spaces, folders, lists, tasks, list child streams, or goals. For users, it pulls members out of team records and deduplicates them by user ID. For unknown streams, it raises a StreamSkipped signal so the framework knows this stream is not implemented.

**Call relations**: This is the main read entry inside the connector. It calls _teams, _spaces, _folders, _lists, _tasks, and _list_child_stream as needed, and also performs the special team-based reads for users and goals. Other code in the source framework calls paginate when it is time to sync one ClickUp stream.

*Call graph*: calls 7 internal fn (__init__, _folders, _list_child_stream, _lists, _spaces, _tasks, _teams); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 206–240)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes ClickUp records into a more consistent shape for the rest of the system. It adds common fields like name, created_at, body, author, and parent_external_id where ClickUp’s raw response uses different names or nested structures.

**Data flow**: It receives one raw record and the stream it belongs to. Depending on the stream, it copies the record and adds or rewrites easy-to-use fields: users get name and created_at, spaces/folders/lists get api_url, tasks get status and created_at, and comments get body, author, created_at, and parent_external_id. Streams without special treatment are returned unchanged.

**Call relations**: This is not part of the hierarchy-walking helpers; it is the cleanup step used after records have been fetched. It uses dict_or_empty when reading a comment’s user field so missing or malformed user data does not break the normalization.

*Call graph*: 1 external calls (dict_or_empty).


### Engineering development trackers
Connectors for source-code, issue-tracking, and engineering planning systems that sync repositories, issues, pull requests, sprints, teams, and related activity.

### `extensions/sources/ufo_ext_sources/github.py`

`io_transport` · `source sync`

GitHub exposes project data through many web API endpoints, and most of those endpoints return results in pages. This file is the GitHub reader for the project. Its job is to discover which organizations and repositories the connected account can see, then walk through the relevant GitHub endpoints and yield clean batches of records.

A key problem it solves is identity. Many GitHub things are only unique inside one repository: for example, a branch called `main` or a tag called `v1.0`. If the system stored every branch only by its name, branches from different repositories would overwrite each other. This connector stamps each record with the organization or repository it came from, then scopes the record’s key with that stamp. In everyday terms, it writes the street address on the package, not just the apartment number.

The connector also knows how different GitHub streams should be resumed. Some streams can safely continue from an `updated_at` time, while others are newest-first feeds where new records may appear at the front. It delegates that careful partition-by-repository walking to the SDK’s `PartitionWalk`. The file only reads from GitHub; it has no write path. If GitHub refuses access to an organization or repository, the connector skips what it cannot read instead of failing the whole sync when that is safe.

#### Function details

##### `_stream`  (lines 71–89)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', ordering: Ordering=Ordering.none, canonical:
```

**Purpose**: Creates a small description of one GitHub data stream, such as issues or commits. The rest of the connector uses this description to know the stream’s name, main identifier, time field, and sync ordering.

**Data flow**: It receives stream settings like the stream name, primary key, cursor field, and ordering choice. It fills in defaults where the caller did not provide details, then returns a `StreamSpec`, which is the SDK’s standard stream description object.

**Call relations**: This helper is used while building the file’s stream catalog. It hands each stream definition to `StreamSpec`, so later methods such as `streams`, `paginate`, and `flatten` can treat all streams in a uniform way.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 182–185)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the GitHub streams this connector can actually run today. Some streams are listed for catalog completeness, but only streams with a known API path are included here.

**Data flow**: It reads the connector’s full stream list and the internal path table. It filters out streams that do not have an API path, then returns the runnable stream descriptions.

**Call relations**: The sync framework asks this method what GitHub data can be read. Its answer controls which streams later reach `paginate`.


##### `GitHubConnector._make_client`  (lines 187–191)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to GitHub, then adds the GitHub-specific headers required for the API version and response format. An HTTP client is the object that sends web requests and receives web responses.

**Data flow**: It receives the base API URL and the resolved credential. It asks the parent connector to build the authenticated client, adds GitHub’s `Accept` and API version headers, and returns the ready-to-use client.

**Call relations**: The base source machinery calls this when preparing to sync. The client it returns is later passed through `paginate` and all helper methods that make GitHub requests.


##### `GitHubConnector.flatten`  (lines 193–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Shapes a GitHub record into the form the system should store, and prevents records from different repositories or organizations from colliding. It is especially important for records whose GitHub ID is only unique inside one repository.

**Data flow**: It receives one raw GitHub record and the stream it belongs to. For stargazers it brings user details up to the top level; for pull requests it removes bulky nested repository objects from `head` and `base`; for other streams it keeps the record as is. Then it checks whether the stream was read per repository or per organization, reads the stamped partition value, and prefixes the primary key with that partition when a key exists. It returns the shaped record, or raises an error if a partitioned stream failed to stamp its records.

**Call relations**: The storage adapter calls this before it reads the stream’s primary key. It uses `_partition_field` to decide whether the record needs repository or organization scoping.

*Call graph*: calls 1 internal fn (_partition_field).


##### `GitHubConnector.paginate`  (lines 235–291)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main reader for a GitHub stream. It chooses the right GitHub endpoint, fans the work out across organizations or repositories when needed, and yields pages of records to the sync runner.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional saved cursor that says where the last sync left off. It builds request parameters, chooses a path, discovers organizations or repositories when necessary, and repeatedly yields lists of records or partition-walk pages. For users, it can also enrich simple member records with fuller public user profiles.

**Call relations**: The sync framework calls this for each stream. It hands organization discovery to `_iter_user_orgs`, repository discovery to `_iter_user_repos` through the nested `repos` helper, repository page reading to `_repo_pages` through the nested `repo_pages` helper, raw GitHub paging to `_paginate_link_header`, and user profile expansion to `_enrich_users`. For repository streams, it wraps those pieces in `PartitionWalk`, which keeps per-repository resume state.

*Call graph*: calls 4 internal fn (_enrich_users, _iter_granted_org_repo_pages, _iter_user_orgs, _paginate_link_header); 3 external calls (__init__, Semaphore, with_context).


##### `GitHubConnector.paginate.repos`  (lines 253–255)

```
async def repos() -> AsyncIterator[str]
```

**Purpose**: Provides repository names to the partition walker, one at a time. Each repository becomes its own sync partition, meaning it can be resumed independently.

**Data flow**: It reads repositories from `_iter_user_repos`. For each `(owner, repo)` pair it turns the pair into a single `owner/repo` string and yields that string.

**Call relations**: This small inner helper exists for `paginate`. `paginate` gives it to `PartitionWalk`, and `PartitionWalk` calls on it when it needs to know which repositories to walk.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector.paginate.repo_pages`  (lines 257–258)

```
def repo_pages(repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects the partition walker to the code that reads one repository’s pages. It adapts the generic partition-walk interface to this connector’s `_repo_pages` method.

**Data flow**: It receives a repository key such as `owner/repo` and a time/window bound from the partition walker. It passes those, along with the current stream, path, and client, into `_repo_pages`, and returns the resulting page iterator.

**Call relations**: This inner helper is created inside `paginate` and handed to `PartitionWalk`. When `PartitionWalk` is ready to fetch a repository slice, it calls this helper, which hands the real work to `_repo_pages`.

*Call graph*: calls 1 internal fn (_repo_pages).


##### `GitHubConnector._repo_pages`  (lines 293–346)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Reads one repository’s records for one stream, while respecting the resume bounds chosen by the partition walker. It also stamps every returned record with the repository it came from.

**Data flow**: It receives the HTTP client, stream, API path, repository key, and a bound that may say read after or before a certain time. It formats the path with the repository owner and name, adds GitHub query parameters such as `since` or `until` when useful, reads each GitHub page, filters out pull requests from the issues stream, optionally trims newest-first pages client-side, computes the page’s highest and lowest cursor values, stamps records with `repo_full_name`, and yields a `WalkPage`. If GitHub says the repository is unavailable in expected ways, it raises a partition skip instead of a general failure.

**Call relations**: It is called through `GitHubConnector.paginate.repo_pages` during repository-scoped syncs. It relies on `_paginate_link_header` for web paging, `_cursor_bounds` for resume bookkeeping, and `with_context` plus `WalkPage` to package records for `PartitionWalk`.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); called by 1 (repo_pages); 3 external calls (__init__, __init__, with_context).


##### `GitHubConnector._iter_user_repos`  (lines 348–356)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: Finds the repositories that should be synced by looking through the organizations the account can access. It avoids using the broader personal repository endpoint because this connector treats organization access as the sync scope.

**Data flow**: It reads organization repository pages from `_iter_granted_org_repo_pages`. For each repository record, it extracts a reliable `(owner, repo)` pair using `_repo_identity`, then yields that pair when it can be found.

**Call relations**: It is called by the inner `GitHubConnector.paginate.repos` helper. That means repository-scoped streams all depend on this method to decide which repositories become sync partitions.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (repos).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 358–377)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: Reads repository pages for every organization the connected account can see, keeping only repositories that are active originals rather than archived repositories or forks.

**Data flow**: It first gets organization logins from `_iter_user_orgs`. For each organization, it requests `/orgs/{org}/repos`, filters out records marked archived or forked, and yields the organization name together with each non-empty page of repositories. If one organization is forbidden or gone, it skips that organization and continues with the rest.

**Call relations**: It is used directly by `paginate` for the repositories stream, and by `_iter_user_repos` for all repository-scoped streams. It uses `_paginate_link_header` to walk GitHub’s paged repository endpoint.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, paginate).


##### `GitHubConnector._iter_user_orgs`  (lines 379–400)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Lists the organizations exposed by the GitHub credential. This is the root discovery step for nearly every stream in this connector.

**Data flow**: It asks GitHub for `/user/orgs`, reads each returned page, pulls out each organization `login`, and yields valid login strings. If GitHub returns a permission error at this root step, it raises `StreamSkipped`, meaning the sync should record that the stream could not be read because the grant lacks organization access.

**Call relations**: Organization-scoped work in `paginate` calls this directly. Repository discovery in `_iter_granted_org_repo_pages` also starts here. It uses `_paginate_link_header` for the actual paged API walk.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, paginate).


##### `GitHubConnector._enrich_users`  (lines 402–422)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: Turns lightweight organization member records into fuller public GitHub user records when possible. This lets stored user pages include fields such as public name or email when GitHub makes them available.

**Data flow**: It receives one page of member records and a semaphore, which is a limit that stops too many profile requests from running at the same time. It starts one enrichment task per member, waits for all of them with `asyncio.gather`, and returns a new list containing the fuller user records where available and the original member records otherwise.

**Call relations**: It is called from `paginate` only for the `users` stream. It coordinates many calls to its inner `one` helper, which performs the per-user lookup.

*Call graph*: called by 1 (paginate); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 408–420)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Fetches the fuller public profile for a single GitHub user, while safely falling back to the original member record. It is the per-person worker used by `_enrich_users`.

**Data flow**: It receives one member record, reads its `login`, and if the login is missing it returns the original record. Otherwise it waits for permission from the semaphore, requests `/users/{login}`, and returns the JSON body if it is a dictionary. If GitHub says the user was not found, it returns the original member record.

**Call relations**: This helper runs inside `_enrich_users`, often many times at once. `_enrich_users` gathers all of these per-user results and hands the enriched page back to `paginate`.


##### `GitHubConnector._paginate_link_header`  (lines 424–432)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks through GitHub endpoints that use the standard `Link` response header to point to the next page. It hides the repeated request-and-next-page loop from the rest of the connector.

**Data flow**: It receives the HTTP client, an API path, and optional query parameters. It delegates to the base connector’s link-header page reader with the GitHub page size and `_parse_records`, then yields each parsed list of records. Empty responses produce no records.

**Call relations**: This is the common paging tool used by `paginate`, `_repo_pages`, `_iter_user_orgs`, and `_iter_granted_org_repo_pages`. Those higher-level methods decide what to read; this method performs the repeated page walk.

*Call graph*: called by 4 (_iter_granted_org_repo_pages, _iter_user_orgs, _repo_pages, paginate).


##### `_partition_field`  (lines 435–443)

```
def _partition_field(path: str) -> str | None
```

**Purpose**: Decides whether a stream path is repository-scoped, organization-scoped, or not partitioned. This tells the connector which stamp should be present on records before their keys are scoped.

**Data flow**: It receives an API path template. If the path contains a repository placeholder, it returns `repo_full_name`; if it contains an organization placeholder, it returns `org_login`; otherwise it returns `None`.

**Call relations**: `GitHubConnector.flatten` calls this before changing a record’s primary key. The result tells `flatten` where to find the partition value that prevents cross-repository or cross-organization collisions.

*Call graph*: called by 1 (flatten).


##### `_parse_records`  (lines 446–450)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Converts a GitHub HTTP response into the record list expected by the paging code. It accepts only JSON arrays, because GitHub list endpoints return arrays.

**Data flow**: It receives an HTTP response. If the response body is empty, it returns an empty list. Otherwise it parses the body as JSON and returns it only if it is a list; non-list JSON becomes an empty list.

**Call relations**: _paginate_link_header passes this function into the base link-header pager. The pager uses it to turn each raw GitHub response into records before yielding the page.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 453–468)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: Extracts a repository’s owner and name from a GitHub repository record. It is careful because GitHub records may provide this identity in more than one shape.

**Data flow**: It receives a repository record and, optionally, a fallback owner name. It first tries `full_name` such as `owner/repo`; if that is not usable, it tries the nested owner login plus the repository `name`; if that is also missing but a fallback owner exists, it uses the fallback owner with the repository name. It returns an `(owner, repo)` pair or `None` if it cannot form one.

**Call relations**: _iter_user_repos calls this for every repository record it gets from organization repository pages. Only records with a usable identity are yielded onward as repository partitions.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 471–481)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: Finds the newest and oldest cursor values on a page of records. A cursor is a field, usually a timestamp, that lets a later sync resume from the right place.

**Data flow**: It receives a page of records and the cursor field name. If there is no cursor field, it returns `(None, None)`. Otherwise it reads that field from each record, including nested fields such as `commit.committer.date`, keeps string values, and returns the maximum and minimum values found. If no usable values exist, it returns `(None, None)`.

**Call relations**: _repo_pages calls this after reading and filtering a repository page. The returned high and low values are placed into `WalkPage` so `PartitionWalk` can track watermarks and resume windows.

*Call graph*: called by 1 (_repo_pages); 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/jira.py`

`io_transport` · `during Jira source sync`

This file is the bridge between the system and Atlassian Jira Cloud. Jira data is spread across one or more Atlassian sites, and the access token decides which sites are visible. The connector first asks Atlassian which sites the token can reach, then visits each site and reads the chosen streams of data. It knows Jira’s paging style, where large lists are returned in chunks using a starting position and a maximum page size. It also knows which streams can be synced incrementally, meaning it can ask only for records updated after the last saved timestamp instead of re-reading everything every time. Issues are treated specially because Jira stores their useful fields inside a nested "fields" object, and descriptions and comments use Atlassian Document Format, a tree-shaped rich text format. This connector walks that tree and extracts plain text, so the final page is readable by a person rather than a dump of raw JSON. If Jira refuses access to a site or resource, the connector marks that stream as skipped instead of crashing the whole sync. Without this file, the system would not know how to discover Jira sites, page through Jira APIs, attach site context to records, or render Jira issues and comments into useful text.

#### Function details

##### `JiraConnector.paginate`  (lines 73–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading one Jira stream. Given a stream name, it chooses the right reader for projects, issues, comments, users, boards, or sprints, and yields pages of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor timestamp. It sends the work to the matching private reader, yields each page that reader produces, and turns Jira access-denied errors into a clean "stream skipped" result instead of a full failure.

**Call relations**: The sync framework calls this when it wants records for a Jira stream. This function then calls the stream-specific methods such as _issues or _projects. If the stream is unknown, or Jira refuses access with an authentication or permission error, it hands back a StreamSkipped signal so the larger sync can continue safely.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 106–110)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This finds the Atlassian Cloud sites that the current credential can access. Jira API calls need a site identifier, so this is the first step before reading most Jira data.

**Data flow**: It uses the HTTP client to call Atlassian’s accessible-resources endpoint. It reads the JSON response, makes sure the result is a list, and returns that list of site records, each usually containing an id and URL.

**Call relations**: The project, issue, user, and board readers call this before building site-specific Jira API paths. Those downstream readers use each site’s id as the cloud id that scopes their requests.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 112–132)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira endpoints that return large lists in numbered pages. It keeps asking for the next page until Jira says there is no more data.

**Data flow**: It receives an API path, optional query parameters, and the JSON field where records live. It repeatedly adds startAt and maxResults values, fetches a page, extracts the record list, yields non-empty pages, and stops when the response says it is the last page or when no more records are found.

**Call relations**: Most stream-specific readers use this as their common paging engine. Projects, issues, comments, boards, and sprints all hand it a Jira path and then receive clean pages of records back.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 134–141)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira projects from every accessible Jira site. Projects are the containers that issues belong to.

**Data flow**: It first asks _sites for reachable sites. For each valid site id, it builds the project-search API path, pages through the results with _offset_values, and adds context such as the cloud id and site URL to each record page before yielding it.

**Call relations**: paginate calls this when the requested stream is projects. It relies on _sites to know which Jira sites to visit and on _offset_values to move through Jira’s paged project results.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 143–155)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira issues, optionally only those updated after a saved cursor. Issues are the main work items in Jira, such as tasks, bugs, or stories.

**Data flow**: It builds a Jira Query Language filter, which is Jira’s search syntax, ordering issues by update time and adding an updated-after condition when a cursor is present. For each accessible site, it fetches issue pages with selected useful fields, adds site context, and yields those pages.

**Call relations**: paginate calls this for the issues stream. _comments also calls it without a cursor so it can discover issues whose comments should be checked. The method uses _sites for site discovery, _offset_values for pagination, and with_context to preserve where each issue came from.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 157–177)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads comments attached to Jira issues. Since Jira comments are reached through their parent issue, it first finds issues and then reads comments issue by issue.

**Data flow**: It reads all issues, takes each issue’s id and cloud id, then calls the issue-comments API for that issue. If a cursor is provided, it filters out comments whose updated time is not newer than the cursor. It adds context such as cloud id, site URL, issue id, and issue key before yielding comment pages.

**Call relations**: paginate calls this for the issue_comments stream. This method depends on _issues to find the parent issues and _offset_values to page through each issue’s comments. It enriches the comments so later stages know which issue they belonged to.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 179–190)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira users from each accessible site. Users are fetched differently from most Jira lists because this endpoint returns a bare JSON array rather than the usual paged envelope.

**Data flow**: It asks _sites for accessible sites, builds the users/search path for each site, and fetches one page of users. It turns the JSON response into a list if possible, adds the cloud id and site URL, and yields the users when any are present.

**Call relations**: paginate calls this for the users stream. It shares site discovery with other stream readers but does not use _offset_values because Jira’s user search response shape is different.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 192–199)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira Agile boards from every accessible site. Boards are used later to find sprints.

**Data flow**: It gets accessible sites, builds the Agile board API path for each valid cloud id, pages through boards with _offset_values, adds cloud id and site URL context, and yields each page.

**Call relations**: paginate calls this for the boards stream. _sprints also calls it first because Jira’s sprint endpoint is reached through a board. Like projects and issues, it depends on _sites and _offset_values.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 201–215)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads sprints from Jira Agile boards, optionally keeping only sprints updated after a saved cursor. A sprint is a time-boxed work period used by many Jira teams.

**Data flow**: It first reads boards, then for each board with a valid id and cloud id, it calls the board’s sprint endpoint. It pages through sprint records, filters by updatedDate if a cursor is present, adds board and cloud context, and yields non-empty pages.

**Call relations**: paginate calls this for the sprints stream. It relies on _boards to discover where sprints live and on _offset_values to read each board’s sprint list.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 217–224)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This prepares records for the sync engine by putting the issue update timestamp where the engine expects it. For most streams, no change is needed.

**Data flow**: It receives one record and its stream description. If the record is an issue, it safely reads the nested fields object and copies fields.updated to a top-level updated field; otherwise it returns the record unchanged.

**Call relations**: The broader connector framework uses this after records are fetched and before cursor tracking. It calls _dict_or_empty so a malformed or missing fields value does not cause an error.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 226–252)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns raw Jira records into readable page text. It gives issues and comments human-friendly titles and bodies instead of leaving them as nested JSON.

**Data flow**: It receives a record and stream description. For issues, it extracts the summary, status, priority, assignee, reporter, and description text; for comments, it extracts the author and comment body text. It returns a title and a markdown-like body with a heading.

**Call relations**: The connector framework calls this when it needs text to store or index. This method uses small helper functions to safely read strings, people, field lines, and Atlassian Document Format text. For streams other than issues and comments, it delegates to the base connector’s render behavior.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 255–256)

```
def _str(value: Any) -> str
```

**Purpose**: This is a small safety helper that returns a value only if it is actually a string. It prevents accidental display of non-text values.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string.

**Call relations**: render uses this while building issue text, and _person uses it while choosing a display name or email address. It keeps the rendering code simple and safe.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 259–260)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This is a small safety helper for reading nested JSON objects. It avoids errors when Jira returns missing, null, or unexpected values.

**Data flow**: It receives any value. If the value is a dictionary-like object, it returns it; otherwise it returns an empty dictionary.

**Call relations**: flatten, render, and _person call this before looking inside nested Jira data. It acts like checking that a box is really a box before reaching into it.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 263–265)

```
def _person(value: Any) -> str
```

**Purpose**: This extracts a readable person name from a Jira user object. It prefers the display name and falls back to the email address.

**Data flow**: It receives a value that may or may not be a Jira person object. It safely treats it as a dictionary, reads displayName first, then emailAddress, and returns the first usable string or an empty string.

**Call relations**: render calls this when it writes issue assignees, reporters, and comment authors. It uses _dict_or_empty and _str so missing user data does not break rendering.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 268–269)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: This formats one labeled metadata line, such as "Status: Done", but only when there is a real value to show.

**Data flow**: It receives a label and a value string. If the value is non-empty, it returns a formatted line; if the value is empty, it returns an empty string.

**Call relations**: render uses this while building the issue metadata block. Empty results are filtered out, so the final page does not contain blank labels.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 272–289)

```
def _doc_text(value: Any) -> str
```

**Purpose**: This extracts plain readable text from Atlassian Document Format, the tree-shaped rich text format Jira uses for descriptions and comments.

**Data flow**: It receives any value, usually a nested dictionary or list from Jira. It walks through the structure, collects every text leaf it finds, joins those pieces with newlines, trims extra space, and returns the resulting plain text.

**Call relations**: render calls this for issue descriptions and comment bodies. Inside it, the nested walk function does the recursive tree traversal, meaning it can find text even when it is buried several levels deep.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 277–286)

```
def walk(node: Any) -> None
```

**Purpose**: This is the recursive worker inside _doc_text. It visits each part of the Atlassian Document Format tree and collects text nodes.

**Data flow**: It receives one node from the document tree. If the node is a dictionary, it records its text field when present and then visits its content children; if the node is a list, it visits each item in the list. It changes the surrounding chunks list by adding found text.

**Call relations**: _doc_text starts this walk with the original document value. The helper calls itself for child nodes until the whole tree has been checked, then _doc_text turns the collected chunks into the final plain-text body.


### `extensions/sources/ufo_ext_sources/linear.py`

`io_transport` · `source sync`

Linear is a project and issue tracking tool. This connector is the bridge between Linear and the rest of the system’s source-sync framework. Without it, the system would not know which Linear objects to ask for, how to page through Linear’s GraphQL API, or how to turn key items like issues and projects into text that people can search or recall later.

The file starts by defining the Linear streams: the named collections the system can sync, such as issues, projects, users, comments, cycles, and workflow states. Most streams use `updatedAt` as a cursor, meaning the connector can ask Linear for only records changed since the last sync. A few Linear collections do not support that kind of filter, so they are fetched fully each time.

The large query strings are the exact GraphQL questions sent to Linear. GraphQL is an API style where the client asks for specific fields. The connector maps each stream name to its query and to the field in Linear’s response where records appear.

`LinearConnector.paginate` does the actual reading. It sends one GraphQL request at a time, yields records page by page, follows Linear’s `endCursor` to get the next page, and stops when Linear says there are no more pages. If Linear refuses access, it records the stream as skipped instead of pretending the sync succeeded. If Linear returns GraphQL errors, it fails loudly so a partial page is not silently accepted.

`LinearConnector.render` makes important records more human-friendly. Instead of saving only raw API-shaped data, it builds short readable pages with titles, states, descriptions, assignees, and similar context.

#### Function details

##### `_stream`  (lines 32–42)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard stream description for one Linear collection. This keeps the stream list short and consistent, so every collection has the same basic metadata unless it needs a special setting.

**Data flow**: It takes a stream name plus optional choices about the cursor field and whether the stream is canonical. It fills in the common Linear timestamp fields, builds a `StreamSpec`, and returns that stream description for the connector to use later.

**Call relations**: This helper is used while the file defines the connector’s stream catalog. It hands the source framework a clear description of each Linear collection, so later the connector can decide what to sync and how to track progress.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 269–312)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads records from Linear one page at a time. It is the main fetch loop for every Linear stream, including incremental syncs that only ask for recently updated records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from the previous sync. It looks up the matching GraphQL query, adds sorting and an `updatedAt` filter when the stream supports it, posts the query to Linear, checks for access refusals or GraphQL errors, extracts the returned `nodes`, and yields them as lists of records. It then reads `pageInfo.endCursor` and repeats until Linear says there is no next page or the response is not shaped as expected.

**Call relations**: The source-sync framework calls this when it wants data for a Linear stream. Inside the loop, it relies on the shared connector POST behavior from its parent class, uses `list_or_empty` to safely turn response nodes into a list, and raises `StreamSkipped` when Linear rejects a stream because the token lacks access. It hands each page of records back to the sync engine, which can then store them and update its cursor.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 314–353)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns selected Linear records into readable text instead of leaving them as raw API data. This matters for recall and search, because a person can understand a page titled with an issue or project name much more easily than a nested JSON object.

**Data flow**: It receives one Linear record and the stream it came from. For issues, projects, comments, and users, it pulls out useful fields like title, name, state, assignee, email, target date, body, or description, cleans them into strings, and formats them into a title and markdown-like page text. For other streams, it falls back to the parent connector’s default rendering.

**Call relations**: After records have been fetched, the broader source framework can call this to make stored pages. This method uses `_str` to safely read text fields, `_ref_id` to pull IDs from nested reference objects, and `_labeled` to build small labeled summaries before returning the finished title and body.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 356–357)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns a value into text only when it is already a string. It prevents `None`, numbers, or nested objects from accidentally appearing in rendered pages as confusing Python-style text.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged; otherwise it returns an empty string.

**Call relations**: The render path calls this whenever it wants a clean piece of text from a Linear record. `_ref_id` also uses it after pulling an `id` field out of a nested object, so ID extraction follows the same safety rule.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 360–361)

```
def _ref_id(value: Any) -> str
```

**Purpose**: Extracts the `id` from a nested Linear reference, such as an assignee or project lead. Linear often represents related objects as small dictionaries containing an ID, and this helper turns that pattern into simple text.

**Data flow**: It receives any value. If the value is a dictionary, it reads its `id` field and passes that through `_str`; if the value is not a dictionary, it returns an empty string.

**Call relations**: The custom renderer calls this when building human-readable metadata for issues and projects. It delegates the final string check to `_str`, then gives the cleaned ID back to `LinearConnector.render` for inclusion in labeled metadata.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 364–365)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: Builds a compact block of label-and-value lines, such as `state: started` or `email: person@example.com`. It keeps rendered pages tidy and skips empty values so the output does not contain blank or meaningless fields.

**Data flow**: It receives a list of label/value pairs. It keeps only pairs where the value is not empty, formats each as `label: value`, joins the lines with newlines, and returns the finished text block.

**Call relations**: The custom renderer uses this when making summaries for issues, projects, and users. It receives already-cleaned values from `LinearConnector.render` and returns the metadata block that becomes part of the final readable page.

*Call graph*: called by 1 (render).


### Operational work and incidents
Connectors for broader work-management and operational response platforms, covering boards, incidents, error events, releases, schedules, and enterprise task structures.

### `extensions/sources/ufo_ext_sources/monday.py`

`io_transport` · `during monday.com source sync`

monday.com exposes its data through GraphQL, which is a query language where the caller asks for exactly the fields it wants. This connector is the bridge between that GraphQL API and UFO’s source-sync framework. Without it, UFO would not know how to ask monday for pages of data, how to keep reading through multi-page results, or how to shape monday records into the common fields used by the rest of the system.

The file first defines the monday streams: users, boards, items, updates, and so on. Each stream says what kind of object it reads, which field uniquely identifies a record, and, where possible, which timestamp can be used as a progress marker for incremental syncs. monday does not support a true “give me only changes since this time” query here, so the connector fetches pages and filters out older records itself.

The main class, `MondayConnector`, sends GraphQL requests, unwraps the response, and turns certain failures into `StreamSkipped`, meaning “record this stream as skipped instead of saving a partial or misleading result.” It has special paths for board items and activity logs because those must be fetched board by board. It also adds helpful normalized fields, such as an update’s author or an item’s assignee IDs, so later parts of the system can work with monday data without knowing monday’s internal shapes.

#### Function details

##### `_extract_person_ids`  (lines 70–98)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: This helper pulls assigned person IDs out of monday item column data. monday stores assignments inside board-specific “people” columns, so this function finds those columns by type and extracts only real people, not teams.

**Data flow**: It receives a value that should be a list of monday column objects. It scans each column, keeps only columns marked as people columns, reads their JSON assignment data, and collects each entry whose kind is `person`. It returns a simple list of person ID strings; if the input is missing, malformed, or unreadable, it quietly returns an empty list.

**Call relations**: When `MondayConnector._items` fetches board items, it calls this helper for each item’s `column_values`. The item record is then enriched with `assignee_ids`, giving the rest of the sync a stable, easy-to-use list instead of monday’s nested column format.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 106–118)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This is the connector’s single doorway for talking to monday’s GraphQL API. It posts a query, checks whether monday reported GraphQL-level errors, and returns the useful `data` part of the response.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional variables for that query. It sends them to monday’s API root path, reads the response body, and looks for an `errors` field. If monday refused or could not answer the query, it raises `StreamSkipped`; otherwise it returns the response’s `data` object, or an empty dictionary if the response shape is not as expected.

**Call relations**: Most of the connector’s fetch paths rely on this method: root pagination, item paging, activity logs, and several direct stream queries in `paginate`. By centralizing GraphQL error handling here, the rest of the file can treat successful responses consistently and skipped streams safely.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, paginate).


##### `MondayConnector._paged_root`  (lines 120–143)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads monday collections that use ordinary numbered pages, such as users, workspaces, boards, and updates. It keeps asking for page 1, page 2, and so on until monday returns no records.

**Data flow**: It receives the API client, the name of the GraphQL field to read, the fields to request for each record, and optionally a saved cursor timestamp. For each page, it asks monday for up to 100 records, turns the result into a list, filters out records older than the cursor when requested, and yields each non-empty page. Once a page has no remaining records, it stops.

**Call relations**: `MondayConnector._boards` uses this as its board reader, and `MondayConnector.paginate` uses it for several top-level streams. It depends on `_graphql` for the actual API call and on `list_or_empty` to safely treat missing or odd response values as an empty list.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, paginate); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 145–156)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper fetches all boards with the fields needed by item and activity-log syncs. It exists because several monday streams must first know which boards exist before they can fetch board-specific data.

**Data flow**: It receives the API client. It uses `_paged_root` to read every page of boards, asks for board details such as name, description, state, timestamps, URL, and workspace information, and gathers all pages into one list. It returns that full list of board records.

**Call relations**: `MondayConnector._items` and `MondayConnector._activity_logs` call this before fetching their own records, because monday’s item pages and activity logs are reached through individual boards. `_boards` itself delegates the repeated page-by-page work to `_paged_root`.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 158–217)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads board items from monday, board by board, including their group, board, timestamps, URL, and column values. It also adds a plain `assignee_ids` list by decoding monday’s people columns.

**Data flow**: It receives the API client and an optional cursor timestamp. First it fetches all boards. For each board, it asks monday for the first item page, then follows monday’s opaque item-page cursor to request later pages. For every item, it extracts assignee person IDs from `column_values`; if a cursor was provided, it keeps only items whose `updated_at` value is newer. It yields each non-empty page of item records.

**Call relations**: `MondayConnector.paginate` calls this when the requested stream is `items`. This method calls `_boards` to discover where to look, `_graphql` to fetch first and later item pages, `_extract_person_ids` to simplify assignments, and safe conversion helpers so missing response pieces do not crash the sync.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (paginate); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 219–247)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads recent activity log entries for each monday board. Activity logs are not fetched as one global list here, so the connector fans out across boards and then labels each log with its board ID.

**Data flow**: It receives the API client and an optional cursor timestamp. It fetches all boards, then for each board asks monday for up to 100 activity log records. It copies each log into a shared list while adding `board_id`, filters out older records when a cursor is present, and yields the page if anything remains.

**Call relations**: `MondayConnector.paginate` calls this for the `activity_logs` stream. The method uses `_boards` to know which boards to inspect, `_graphql` to request each board’s logs, and `list_or_empty` to safely process monday’s response.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (paginate); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 249–323)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher that the sync framework calls to get records for a particular monday stream. It chooses the right query pattern for users, teams, boards, items, updates, activity logs, tags, and other supported streams.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. Based on the stream name, it either calls a shared helper like `_paged_root`, `_items`, or `_activity_logs`, or sends a one-off GraphQL query for streams such as teams and tags. It yields pages of records to the caller. If the stream is unknown, or if monday refuses access with a 401 or 403 HTTP status, it raises `StreamSkipped` so the run records a clean skip instead of pretending the stream synced successfully.

**Call relations**: The broader source-sync framework calls this method whenever it needs monday records. `paginate` is the traffic controller: it routes each stream to the correct lower-level reader and passes record pages back upstream for storage or indexing.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _graphql, _items, _paged_root).


##### `MondayConnector.flatten`  (lines 325–366)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method reshapes raw monday records into a more common format used by the rest of the system. It keeps the original fields but adds or normalizes helpful fields like `name`, `body`, `author`, `created_at`, and parent links.

**Data flow**: It receives one raw record and the stream it came from. For known stream types, it copies the record and adds standardized fields: users expose name and email, boards and workspaces get an API URL, items get a status, updates get a readable body and author, and activity logs get subject, body, author, and parent board ID. If the stream has no special rules, it returns the record unchanged.

**Call relations**: After `paginate` has yielded records, the connector framework can call `flatten` to prepare each one for common downstream use. For updates, it uses `dict_or_empty` so a missing or malformed creator object does not break the conversion.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/pagerduty.py`

`io_transport` · `sync run / source data fetching`

PagerDuty is an external service, so the system cannot read its data directly like a local file. This connector is the bridge. It knows which PagerDuty objects are available, where to ask for them in the PagerDuty web API, and how to walk through long result lists page by page.

The file defines several stream descriptions, one for each kind of PagerDuty data. A stream is like a named lane of data, with a known record key and, for some streams, a timestamp used to continue from the last successful sync. Incidents are synced incrementally using their `updated_at` time, so a later run can ask mostly for changes instead of rereading everything. Incident notes are special: PagerDuty exposes them under each incident, so the connector first reads incidents, then asks for notes for each one and attaches the incident id as context.

The `PagerDutyConnector` builds an HTTP client, adds PagerDuty’s required API version header, and provides a `paginate` method that chooses the right reading strategy for each stream. If PagerDuty says the token is invalid or lacks permission, the connector marks that stream as skipped instead of crashing the whole run. Without this file, the system would not know how to safely and consistently import PagerDuty data.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 74–77)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the web client used to talk to PagerDuty and adds the special `Accept` header PagerDuty expects for its version 2 API. In plain terms, it makes sure every request says, “please answer in the PagerDuty API format this connector understands.”

**Data flow**: It receives a base web address and a credential object supplied by the wider authentication system. It asks the parent REST connector to build the basic HTTP client, then adds PagerDuty’s required media-type header to that client. It returns the prepared client; it does not store the access token itself.

**Call relations**: This function is part of the connector setup inherited from the general REST source framework. The rest of the PagerDuty reading functions use the client it prepares, so their API calls all carry the right PagerDuty version header.


##### `PagerDutyConnector._offset_pages`  (lines 79–99)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one PagerDuty list-style endpoint page by page. It is the shared helper for streams where PagerDuty uses an offset and limit, which means “start here and give me this many records.”

**Data flow**: It receives an HTTP client, a stream description, optional request parameters, and an optional cursor timestamp. It asks the shared REST connector to fetch pages from the matching PagerDuty endpoint. For each page, it optionally removes records whose cursor field is not newer than the saved cursor, then yields only non-empty pages of records.

**Call relations**: This is the common page-walking helper. `PagerDutyConnector._incidents` calls it after adding incident-specific sorting and date parameters. `PagerDutyConnector.paginate` also calls it directly for simpler streams such as users, teams, services, schedules, and on-calls.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 101–113)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads PagerDuty incidents in oldest-to-newest update order, optionally starting from a saved cursor. It exists because incidents support a useful incremental sync path based on their `updated_at` timestamp.

**Data flow**: It receives an HTTP client and an optional cursor. It builds request parameters that tell PagerDuty to sort incidents by update time ascending, and if a cursor is present, to ask PagerDuty for incidents since that time. It then uses `_offset_pages` to fetch and yield batches of incident records.

**Call relations**: This function is the incident-specific layer on top of the shared paging helper. `PagerDutyConnector.paginate` calls it when the requested stream is incidents. `PagerDutyConnector._incident_notes` also calls it to discover which incident ids it should use when fetching notes.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 115–128)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads notes attached to incidents. PagerDuty does not expose these as one simple global list, so the connector must first find incidents, then fetch notes for each incident one at a time.

**Data flow**: It receives an HTTP client and an optional cursor. It reads incidents with no incident cursor restriction, takes each valid incident id, requests `/incidents/{id}/notes`, extracts the `notes` list from the response, and optionally keeps only notes created after the cursor. Before yielding notes, it adds the parent incident id as extra context so downstream code knows which incident each note came from.

**Call relations**: This function is called by `PagerDutyConnector.paginate` when syncing the `incident_notes` stream. It relies on `PagerDutyConnector._incidents` to supply incident ids, uses `records_at` to pull the notes list out of PagerDuty’s response, and uses `with_context` to attach the incident id before handing the notes back to the sync flow.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 130–164)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a PagerDuty stream. Given a stream name, it chooses the correct way to fetch that stream and yields batches of records to the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name: incidents go through the incident reader, incident notes go through the note reader, and the simpler list streams go through the shared offset-page reader. If the stream is unknown, it raises a controlled skip. If PagerDuty refuses access with HTTP 401 or 403, it also raises a controlled skip explaining that the token or permissions are not sufficient; other HTTP errors are allowed to continue as real failures.

**Call relations**: The source framework calls this method when it needs records for a particular PagerDuty stream. `paginate` then delegates to `_incidents`, `_incident_notes`, or `_offset_pages` depending on the stream. When access is refused or a stream is not implemented, it creates a `StreamSkipped` result so the overall run can record the problem without treating it like a broken connector.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/sentry.py`

`io_transport` · `during source sync`

Sentry stores useful information in several nested places: organizations contain projects, projects contain issues and events, and organizations also have members and releases. This file is the connector that knows how to walk that tree safely and consistently. Without it, the larger sync system would not know which Sentry web addresses to call, how to follow Sentry’s page-by-page results, or how to label each record with the organization or project it came from.

At the top, the file defines the Sentry streams the rest of the system can ask for. A stream is one kind of data, such as “issues” or “projects,” with notes about its unique ID and time fields. The `SentryConnector` then acts like a small travel guide for the Sentry API. Its main `paginate` method receives a stream request and sends it to the right helper.

Sentry returns long lists in pages, with a special `Link` header that says whether another page exists. `_paged_list` repeatedly fetches those pages until the header says to stop. For nested data, helpers first fetch organizations or projects, then fetch the related child records. Each child record is stamped with context, such as `organization_slug` or `project_slug`, so the record still makes sense after it is stored elsewhere. If Sentry refuses access with a permission or authentication error, the connector skips that stream instead of crashing the whole sync.

#### Function details

##### `_sentry_next_cursor`  (lines 76–81)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper looks at Sentry’s response headers and finds the cursor for the next page, if Sentry says there is one. A cursor is like a bookmark that tells the API where to continue reading a long list.

**Data flow**: It receives HTTP headers from a Sentry response. It looks for a `Link` header, searches it for Sentry’s “next page with results” pattern, and returns the cursor text if found. If the header is missing or does not contain a usable next-page cursor, it returns nothing.

**Call relations**: After `_paged_list` fetches a page from Sentry, it calls `_sentry_next_cursor` to decide whether to keep going. This helper only reads the headers; `_paged_list` uses the answer to either request another page or stop.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.paginate`  (lines 89–128)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway used by the sync system to read one Sentry stream. It chooses the correct Sentry-reading helper for the requested stream and yields pages of records back to the caller.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. Based on the stream name, it fetches organizations, projects, members, issues, events, or releases. It yields lists of records as they are found, filters some streams by the cursor, and turns permission failures into a clear “skip this stream” signal.

**Call relations**: The larger source-sync machinery calls `paginate` when it wants records for a stream. `paginate` then delegates to `_organizations`, `_projects`, `_members`, `_issues`, `_events`, or `_releases`. If the stream is unknown, or if Sentry rejects access with an authentication or permission status, it raises `StreamSkipped` so the rest of the sync can continue.

*Call graph*: calls 7 internal fn (__init__, _events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._paged_list`  (lines 130–147)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches every page for one Sentry list endpoint. It hides Sentry’s pagination details so the rest of the connector can simply ask for “all records from this path.”

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It repeatedly requests the path, adding the current cursor when there is one, turns a JSON list response into a list of dictionary-like records, yields non-empty pages, then asks `_sentry_next_cursor` whether another page exists. When there is no next cursor, it stops.

**Call relations**: All the stream-specific helpers call `_paged_list` when they need records from Sentry. `_paged_list` calls `_sentry_next_cursor` after each response, so the stream helpers do not need to know how Sentry encodes next-page information.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 149–153)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This reads all organizations visible to the current Sentry credential. Organizations are the top-level containers needed before members and releases can be fetched.

**Data flow**: It receives an HTTP client. It asks `_paged_list` for `/organizations/`, gathers every returned page into one list, and returns that list of organization records. It does not change stored state.

**Call relations**: `paginate` calls `_organizations` when the requested stream is organizations. `_members` and `_releases` also call it first because they need each organization’s slug before they can ask Sentry for organization-specific records.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, paginate).


##### `SentryConnector._projects`  (lines 155–159)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This reads all projects visible to the current Sentry credential. Projects are needed both as their own stream and as starting points for project-specific issues and events.

**Data flow**: It receives an HTTP client. It asks `_paged_list` for `/projects/`, gathers all pages into one list, and returns that list of project records. Any later cursor filtering is done by the caller, not here.

**Call relations**: `paginate` calls `_projects` when syncing projects. `_issues` and `_events` call it first because Sentry’s issue and event endpoints require both the organization slug and the project slug.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, paginate).


##### `SentryConnector._members`  (lines 161–167)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads organization members from Sentry. It does this one organization at a time because Sentry’s member endpoint is scoped to a specific organization.

**Data flow**: It receives an HTTP client. It first gets all organizations, takes each valid organization slug, fetches that organization’s members through `_paged_list`, and adds `organization_slug` to each returned member record. It yields member pages as they are found.

**Call relations**: `paginate` calls `_members` for the members stream. `_members` depends on `_organizations` to discover where to look, uses `_paged_list` to fetch each organization’s member pages, and uses `with_context` to attach the organization label before handing records back.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 169–183)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads issues for every accessible Sentry project. If a cursor is supplied, it asks Sentry only for issues whose `lastSeen` time is newer than that cursor.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all projects, extracts each project’s organization slug and project slug, builds a Sentry issues path for that project, and optionally adds a query filter based on `lastSeen`. For each page returned, it adds both `organization_slug` and `project_slug` so the issue’s origin is preserved, then yields the page.

**Call relations**: `paginate` calls `_issues` for the issues stream. `_issues` first calls `_projects` to find the project locations, then `_paged_list` to fetch issue pages, and finally `with_context` to label the records before returning them to the main sync flow.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 185–199)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads events for every accessible Sentry project. Events are individual occurrences, and if a cursor is supplied, the helper asks for only events newer than that timestamp.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all projects, finds each project’s organization and project slugs, builds the events endpoint path, and optionally adds a query filter based on `event.timestamp`. It fetches event pages and adds organization and project context to each record before yielding them.

**Call relations**: `paginate` calls `_events` for the events stream. Like `_issues`, it relies on `_projects` to know which project endpoints exist, `_paged_list` to walk Sentry’s pages, and `with_context` to preserve where each event came from.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 201–212)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads releases for each accessible Sentry organization. Releases are fetched per organization and can be filtered locally to only return releases created after a cursor.

**Data flow**: It receives an HTTP client and an optional cursor. It first reads all organizations, then for each valid organization slug it fetches release pages. If a cursor is present, it keeps only releases whose `dateCreated` value is newer than the cursor. It adds `organization_slug` to the remaining records and yields non-empty pages.

**Call relations**: `paginate` calls `_releases` for the releases stream. `_releases` uses `_organizations` to find organization slugs, `_paged_list` to read each organization’s releases, and `with_context` to label each release with its organization before returning it to the sync.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).


### `extensions/sources/ufo_ext_sources/wrike.py`

`io_transport` · `source sync`

Wrike has its own web API, its own field names, and its own way of splitting long results into pages. This file is the adapter that hides those Wrike-specific details from the rest of the system. Without it, the project would not know which Wrike objects can be synced, how to ask Wrike for the next page of results, or how to rename important fields into the common shape used elsewhere.

The file first declares the Wrike streams: contacts, folders, tasks, comments, workflows, and custom fields. A stream is one kind of thing the connector can read. Some streams have an update time, called a cursor, so later syncs can skip older records that were already seen.

The `WrikeConnector` then provides two main jobs. Its `paginate` method talks to Wrike’s API and follows Wrike’s `nextPageToken`, which is like a “continue from here” ticket for the next batch of results. If Wrike rejects the request because the credential is missing permission or invalid, the stream is skipped instead of crashing the whole sync.

Its `flatten` method reshapes Wrike records into friendlier records. For example, it builds a contact name from first and last name, pulls an email out of profile data, turns folder and task titles into a shared `name` field, and links comments back to their parent task.

#### Function details

##### `_profile_email`  (lines 54–64)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper looks inside a Wrike contact record and finds the first usable email address stored in its profile list. It exists because Wrike does not put the email directly at the top level of the contact record.

**Data flow**: It receives one Wrike record as a dictionary. It checks whether the record has a `profiles` value that is actually a list, then walks through that list looking for a profile dictionary with a non-empty string in its `email` field. It returns that email address if it finds one, or `None` if the data is missing or not in the expected shape.

**Call relations**: When `WrikeConnector.flatten` is preparing a contact for the rest of the system, it calls this helper to extract the contact email. The helper does only this small lookup and hands the result back so the flattened contact can include a simple top-level `email` field.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 72–96)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Wrike stream from the Wrike API, page by page. It also skips streams that are not implemented or cannot be accessed with the current permission, so one refused Wrike area does not necessarily stop the whole connector.

**Data flow**: It receives an HTTP client, a stream description, and an optional stored cursor value from a previous sync. It asks Wrike for records at the stream’s API path, follows Wrike’s `nextPageToken` to keep fetching more pages, and looks for records under Wrike’s `data` field. If a cursor is available for that stream, it drops records whose update time is not newer than the cursor. It yields each non-empty batch of newer records. If Wrike returns a 401 or 403 refusal, it turns that into a `StreamSkipped` signal; other HTTP errors are passed upward.

**Call relations**: The broader sync runner calls this method when it wants records for a Wrike stream. Inside the method, the connector relies on the shared REST paging helper to do the repeated web requests. If the stream name is not one this connector can run, or Wrike refuses access, it raises `StreamSkipped` to tell the runner to move on cleanly.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 98–136)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method converts raw Wrike records into a more consistent shape used by the rest of the system. It keeps the original data but adds or renames useful fields such as `name`, `created_at`, `body`, `author`, and parent task links.

**Data flow**: It receives one raw record and the stream description that says what kind of Wrike object it is. For contacts, it builds a display name, finds the email with `_profile_email`, and copies the creation date into `created_at`. For folders, it uses the title as the name and builds an API URL. For tasks, it reads nested date information safely with `dict_or_empty`, chooses a status, and exposes the due date and creation date. For comments, it maps Wrike’s text, author, creation date, and task ID into common fields. For other streams, it returns the record unchanged.

**Call relations**: After `WrikeConnector.paginate` has supplied raw records from Wrike, the sync process can call this method to make each record easier for the rest of the system to understand. It delegates contact email extraction to `_profile_email` and uses the shared `dict_or_empty` helper so task date parsing does not break when Wrike sends missing or unexpected date data.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).
