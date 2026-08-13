# Work Management and Engineering Operations Source Connectors  `stage-14.1.3`

This stage is shared behind-the-scenes support for the system’s syncing work. Its job is to connect to common tools where teams plan work, write code, respond to incidents, and track bugs, then turn those outside records into standard pages the rest of the system can store and search. Think of each file as an adapter plug for a different service.

The Asana, ClickUp, Jira, Linear, monday.com, and Wrike connectors read project-management data such as tasks, issues, comments, teams, boards, folders, and workspace details. They also deal with each service’s shape: ClickUp has nested workspaces, Linear uses GraphQL, and others return paged web responses. The GitHub connector brings in code-collaboration records like organizations, repositories, issues, pull requests, comments, and users. PagerDuty adds incident-response information, including incidents, services, schedules, notes, and on-call records. Sentry adds error-tracking data, such as projects, issues, events, members, and releases.

Together, these connectors are read-only translators. They fetch data from outside systems, reshape it into common records, and leave storage and search to the rest of the platform.

## Files in this stage

### Workspace Task Platforms
Connectors for broad work-tracking systems that expose projects, tasks, workspace structure, users, and related collaboration records.

### `extensions/sources/ufo_ext_sources/asana.py`

`io_transport` · `source sync`

Asana is a project and task tracking service. This connector is the read-only bridge between Asana and the UFO source-sync system. Without it, the system would not know which Asana objects exist, how to ask for them, or how to walk through Asana’s paginated API responses.

The file first defines the set of Asana “streams” the system can sync. A stream is one kind of object, like tasks or projects. Each stream says what Asana object to request, which field uniquely identifies records, and whether the stream can be synced incrementally. Incremental sync means “only ask for things changed since last time,” like checking only the new mail since yesterday instead of rereading the whole mailbox.

The main class, `AsanaConnector`, supplies Asana’s base API address and a `paginate` method. Asana returns lists in pages: each response contains a `data` list and sometimes a `next_page.offset` token. The connector repeatedly asks for the next page until Asana says there are no more. For tasks and projects, it also sends `modified_since` when a previous cursor is available, because Asana supports that shortcut only for those streams. This connector does not write anything back to Asana and does not store credentials itself; authentication is provided by the surrounding runner.

#### Function details

##### `_stream`  (lines 24–38)

```
def _stream(name: str, *, cursor_field: str | None=None, updated_at_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a `StreamSpec`, which is the small description the sync system uses to know how to read one kind of Asana object. It keeps the stream list short and consistent instead of repeating the same setup for every Asana collection.

**Data flow**: It receives a stream name plus optional information about cursor fields, update-time fields, and whether the stream is considered canonical. It fills in shared defaults, such as using Asana’s `gid` field as the primary key, and returns a ready-to-use `StreamSpec` object.

**Call relations**: This helper is used while the file is loaded to build `ASANA_STREAMS`. It hands each completed stream description to the connector class through `streams_list`, so later sync code knows what Asana endpoints are available.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 74–90)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Asana stream page by page. It hides Asana’s pagination details so the rest of the sync system can simply receive batches of records.

**Data flow**: It starts with an HTTP client, a stream description, and an optional saved cursor from a previous sync. It builds request parameters, including a page limit and sometimes `modified_since` for tasks or projects. It repeatedly calls Asana, pulls the list of records out of the response, yields non-empty batches, then follows Asana’s `next_page.offset` token until there is no valid next page.

**Call relations**: The broader REST connector framework calls this when it needs records for a particular Asana stream. Inside the loop it uses the inherited `_get` request method to fetch data and `ufo.sdk.sources.list_or_empty` to safely turn the response’s `data` field into a list. Each yielded batch is passed back to the sync pipeline for storage and cursor tracking.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/clickup.py`

`io_transport` · `source sync`

ClickUp organizes work like a set of nested boxes: teams contain spaces, spaces contain folders and lists, and lists contain tasks, comments, and custom fields. This connector walks through those boxes in order so it can find the useful records at the bottom. Without this file, UFO would not know where to ask ClickUp for data, how to follow the hierarchy, or how to label each task or comment with the list, folder, space, or team it came from.

The file defines the available ClickUp streams, such as "tasks" or "list_comments", and then implements a ClickUpConnector that reads them from the ClickUp REST API. A REST API is a web interface where the connector asks for data using URLs. The connector starts with teams, then uses those team IDs to find spaces, then folders, then lists. Once it has lists, it can fetch tasks, list comments, and custom fields.

Some streams support a cursor, which is a saved marker saying “only fetch records newer than this.” Tasks use ClickUp’s page numbers and are filtered by date_updated. Comments can also be filtered by their date. The connector does not write anything back to ClickUp; it is read-only. It also reshapes a few records in flatten so common fields like name, email, status, and created_at are easier for the rest of UFO to use.

#### Function details

##### `ClickUpConnector._teams`  (lines 58–60)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the top-level ClickUp teams available to the authenticated account. Teams are the starting point for almost every other ClickUp lookup, so this is the first step in walking the workspace tree.

**Data flow**: It receives an HTTP client that can talk to ClickUp. It asks ClickUp for /team, pulls the list stored under the "teams" key from the response, and returns that list of team records.

**Call relations**: This is the root lookup used by _spaces to continue down into spaces. paginate also calls it directly when the requested stream is teams, users, or goals.

*Call graph*: called by 2 (_spaces, paginate); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 62–70)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds the active spaces inside every ClickUp team. A space is the next container below a team, so this function expands the sync from the team level to the workspace area level.

**Data flow**: It starts by calling _teams. For each valid team ID, it asks ClickUp for that team’s non-archived spaces, extracts the records under "spaces", adds the parent team_id to each space, and returns one combined list.

**Call relations**: _folders and _lists call this when they need to move farther down the hierarchy. paginate calls it directly when the spaces stream is requested.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 72–82)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds the active folders inside every ClickUp space. Folders are one possible container for lists, so they must be discovered before folder-based lists can be read.

**Data flow**: It calls _spaces to get all spaces. For each valid space ID, it requests that space’s non-archived folders, extracts the "folders" records, adds the parent space_id to each one, and returns the collected folders.

**Call relations**: _lists calls this to find lists that live inside folders. paginate calls it directly when the folders stream is requested.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 84–100)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all active ClickUp lists, both lists inside folders and lists placed directly under spaces. Lists matter because tasks, comments, and custom fields are read from them.

**Data flow**: It first calls _folders and fetches each folder’s non-archived lists, adding folder_id to those records. Then it calls _spaces and fetches lists that are directly under each space, adding space_id to those records. It returns one combined list of list records.

**Call relations**: _tasks uses this to know which lists to fetch tasks from. _list_child_stream uses it to fetch comments and custom fields for each list. paginate calls it directly when the lists stream is requested.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _tasks, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 102–127)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads tasks from every discovered ClickUp list, page by page. It can skip older tasks when given a saved cursor, which helps incremental syncs avoid rereading everything.

**Data flow**: It calls _lists to get all lists. For each valid list ID, it requests tasks with archived tasks excluded, closed tasks included, subtasks included, and a page number. It adds list_id and list_name to each task, filters out tasks whose date_updated is not newer than the cursor when a cursor exists, yields any remaining tasks, and keeps requesting pages until ClickUp returns no tasks.

**Call relations**: paginate hands work to this function when the requested stream is tasks. This function depends on _lists because ClickUp tasks are fetched from individual lists, not from one flat account-wide endpoint.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 129–148)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads per-list child data, currently list comments and list custom fields. These records live under individual lists, so this function repeats the same fetch for every list it can find.

**Data flow**: It calls _lists, then for each valid list ID chooses the correct ClickUp path for either comments or fields. It extracts the returned records, adds list_id and list_name, optionally filters by the stream’s cursor field, and yields records when there are any.

**Call relations**: paginate calls this for the list_comments and list_custom_fields streams. It hands off hierarchy discovery to _lists, then focuses only on the child endpoint needed for the selected stream.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 150–204)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses how to fetch each ClickUp stream and yields records in batches. This is the main routing point that connects a stream name like "tasks" or "spaces" to the helper that knows how to read it.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. Based on the stream name, it calls the matching helper, builds special streams like users and goals when needed, yields non-empty batches of records, and raises StreamSkipped if the stream is not implemented.

**Call relations**: The source sync machinery calls this to get pages of ClickUp data. It delegates to _teams, _spaces, _folders, _lists, _tasks, and _list_child_stream depending on the stream; for users it extracts embedded members from teams, and for goals it fetches goals under each team.

*Call graph*: calls 7 internal fn (__init__, _folders, _list_child_stream, _lists, _spaces, _tasks, _teams); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 206–240)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Reshapes certain ClickUp records into a more consistent form for the rest of UFO. It keeps the original data but adds or normalizes common fields such as name, email, status, created_at, body, author, and parent_external_id.

**Data flow**: It receives one record and the stream it came from. For users, spaces, folders, lists, tasks, and list comments, it copies the record and adds easier-to-use fields. For comments, it safely reads the nested user object using dict_or_empty. For streams that need no special reshaping, it returns the record unchanged.

**Call relations**: This works after records have been fetched by paginate and its helper functions. It does not fetch from ClickUp itself; instead, it prepares the fetched records so later indexing or recall code can treat common fields in a predictable way.

*Call graph*: 1 external calls (dict_or_empty).


### Engineering Issue Collaboration
Connectors for code and issue-management systems used by engineering teams to track repositories, projects, issues, pull requests, comments, and team metadata.

### `extensions/sources/ufo_ext_sources/github.py`

`io_transport` · `during GitHub source sync`

GitHub has many separate web API endpoints, and most of them are split into pages of results. This file is the connector that knows which GitHub endpoints to call, how to follow GitHub's “next page” links, and how to keep records from different repositories from overwriting each other. Without it, the system would not know how to discover a user's GitHub organizations and repositories, or how to safely sync repo-level data like issues, branches, commits, tags, and stargazers.

The connector starts by declaring a catalog of possible streams, where a stream means “one kind of thing to sync,” such as repositories or issues. Only streams with a known GitHub API path are runnable. At sync time, it first discovers the organizations visible to the credential, then lists non-archived, non-fork repositories in those organizations. Repository-specific streams are then run once per repository, like sending a delivery driver down one street at a time instead of mixing all addresses together.

A key detail is partition stamping. Many GitHub IDs are only unique inside one repository, such as a branch named “main.” The connector adds the repository or organization name to each record and folds that into the record key, so two repositories do not fight over the same page. It also supports careful resume behavior for ordered streams, so interrupted syncs can continue without missing newer records.

#### Function details

##### `_stream`  (lines 71–89)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', ordering: Ordering=Ordering.none, canonical:
```

**Purpose**: Builds a stream definition, which is the connector's small recipe for one kind of GitHub data. It records things like the stream name, its unique key field, and which timestamp can be used to resume syncing.

**Data flow**: It takes simple settings such as a stream name, primary key, cursor field, and ordering style. It fills in sensible defaults, then returns a StreamSpec object that the rest of the connector can use as a standard description of that GitHub stream.

**Call relations**: This helper is used while the file defines the GitHub stream catalog. It hands its settings to StreamSpec so the connector and the wider source framework can treat every stream in a consistent way.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 182–185)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the GitHub streams that are actually ready to run. Some streams are listed for future compatibility, but this method filters out any stream that does not yet have a GitHub API path wired in.

**Data flow**: It reads the connector's full stream list and the path table in this file. It returns only the stream definitions whose names appear in the path table.

**Call relations**: The source framework calls this when it asks the connector what it can sync. This keeps unfinished or parent-dependent streams from being attempted.


##### `GitHubConnector._make_client`  (lines 187–191)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to GitHub and adds the GitHub-specific request headers. These headers tell GitHub which API format and version the connector expects.

**Data flow**: It receives a base URL and a credential. It asks the parent REST connector to build the authenticated client, then adds GitHub's Accept and API-version headers before returning the client.

**Call relations**: This is part of connector setup. It builds on the base RestConnector client and prepares it so later pagination and fetch methods can call GitHub correctly.


##### `GitHubConnector.flatten`  (lines 193–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Shapes a raw GitHub record into the form the system should store, and prevents records from different repositories or organizations from colliding. For example, it turns a branch key like “main” into a key scoped by its repository.

**Data flow**: It receives one GitHub record and the stream it belongs to. It may adjust special records, such as merging stargazer user details or removing large nested repository objects from pull requests. Then it checks whether the stream is repository-scoped or organization-scoped, reads the stamped partition field, and prefixes the record's primary key with that partition. It returns the shaped record, or raises an error if a partitioned record is missing its partition stamp.

**Call relations**: The adapter calls this before it reads the stream's primary key for page naming. It uses _partition_field to learn which partition stamp should be present for the stream's API path.

*Call graph*: calls 1 internal fn (_partition_field).


##### `GitHubConnector.paginate`  (lines 235–291)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for reading a GitHub stream. It decides whether a stream should be fetched once, once per organization, or once per repository, and then yields pages of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor for resuming. It looks up the GitHub API path, prepares common query parameters, discovers organizations or repositories when needed, fetches paged results, stamps each page with its organization or repository, and yields those pages to the sync runner.

**Call relations**: The sync framework calls this when it wants records for a stream. It hands organization discovery to _iter_user_orgs, repository discovery to _iter_granted_org_repo_pages and _iter_user_repos through its nested repos helper, repository page fetching to _repo_pages through its nested repo_pages helper, user detail expansion to _enrich_users, and plain GitHub page walking to _paginate_link_header. For repository streams it uses PartitionWalk so each repository can keep its own resume state.

*Call graph*: calls 4 internal fn (_enrich_users, _iter_granted_org_repo_pages, _iter_user_orgs, _paginate_link_header); 3 external calls (__init__, Semaphore, with_context).


##### `GitHubConnector.paginate.repos`  (lines 253–255)

```
async def repos() -> AsyncIterator[str]
```

**Purpose**: Provides PartitionWalk with the list of repositories to walk. It turns discovered repository owner/name pairs into the single text key format used by the partition walker.

**Data flow**: It reads repository pairs from _iter_user_repos. For each pair, it combines owner and repository name into a string like “owner/repo” and yields that as the partition name.

**Call relations**: This nested helper exists inside paginate for repository-scoped streams. PartitionWalk calls on it to know which repositories should be synced, and it relies on _iter_user_repos for the actual discovery work.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector.paginate.repo_pages`  (lines 257–258)

```
def repo_pages(repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Provides PartitionWalk with the page reader for one repository. It connects a repository key and a resume bound to the GitHub-specific repository fetcher.

**Data flow**: It receives a repository key such as “owner/repo” and a PartitionBound, which is the saved time window or resume boundary for that repository. It returns the async page iterator produced by _repo_pages.

**Call relations**: This nested helper is passed into PartitionWalk by paginate. When PartitionWalk decides to fetch a particular repository, this helper hands the work to _repo_pages.

*Call graph*: calls 1 internal fn (_repo_pages).


##### `GitHubConnector._repo_pages`  (lines 293–346)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Fetches one repository's pages for a repository-scoped stream, while applying the right resume rules. It also skips repositories that GitHub says are gone, empty, or inaccessible, without failing the whole sync.

**Data flow**: It receives the HTTP client, stream, API path, repository key, and resume bound. It fills the path with the repository owner and name, builds GitHub query parameters, adds filters such as “since” or “until” where possible, fetches pages, removes pull requests from the issues stream, filters newest-first pages when GitHub cannot do it server-side, calculates the page's high and low cursor values, stamps records with repo_full_name, and yields WalkPage objects. If GitHub returns certain repository-level errors, it raises PartitionSkipped so only that repository is skipped.

**Call relations**: It is called through paginate.repo_pages as part of PartitionWalk. It relies on _paginate_link_header for GitHub pagination, _cursor_bounds to summarize timestamps for resume tracking, and with_context to attach the repository stamp to every record.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); called by 1 (repo_pages); 3 external calls (__init__, __init__, with_context).


##### `GitHubConnector._iter_user_repos`  (lines 348–356)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: Discovers the repositories that should be synced by looking inside the organizations visible to the credential. It deliberately uses organization repositories rather than all user repositories, so the sync follows the granted organization scope.

**Data flow**: It receives an HTTP client. It asks _iter_granted_org_repo_pages for repository pages, extracts an owner and repository name from each repository record with _repo_identity, and yields valid owner/name pairs.

**Call relations**: The nested repos helper inside paginate calls this when PartitionWalk needs repository partitions. This function depends on _iter_granted_org_repo_pages for the raw repository pages and _repo_identity for safe parsing of each repository record.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (repos).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 358–377)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: Lists usable repositories for every organization the credential can see. It filters out archived repositories and forks, because those are not part of the active organization-owned repository set this connector syncs.

**Data flow**: It receives an HTTP client. It gets organization logins from _iter_user_orgs, calls each organization's repository endpoint, filters each page to keep only non-archived, non-fork repositories, and yields the organization name together with each non-empty repository page. If one organization refuses access with expected skip statuses, it moves on to the next one.

**Call relations**: paginate calls this directly for the repositories stream, and _iter_user_repos calls it when building repository partitions for repo-scoped streams. It uses _paginate_link_header to follow GitHub's paged repository results.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, paginate).


##### `GitHubConnector._iter_user_orgs`  (lines 379–400)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Finds the organization logins visible to the current GitHub credential. This is the root discovery step for nearly every runnable stream in this connector.

**Data flow**: It receives an HTTP client. It calls GitHub's /user/orgs endpoint, reads each page, extracts valid login strings, and yields them. If GitHub rejects the whole organization listing with a 403 status, it raises StreamSkipped to mark the stream as skipped rather than broken.

**Call relations**: paginate uses this for organization-scoped streams, and _iter_granted_org_repo_pages uses it before listing repositories. It relies on _paginate_link_header for the page-by-page API walk and raises StreamSkipped when the credential lacks the organization access needed for the connector's fan-out model.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, paginate).


##### `GitHubConnector._enrich_users`  (lines 402–422)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: Expands simple organization member records into fuller public GitHub user records when possible. This can add public profile details such as name or email.

**Data flow**: It receives an HTTP client, a page of member records, and a semaphore, which is a small traffic light that limits how many user lookups happen at once. It launches one lookup task per member, waits for all of them, and returns a new page where each member is replaced by the fuller user record if GitHub returned one.

**Call relations**: paginate calls this only for the users stream after fetching an organization members page. It uses asyncio.gather to run the per-user one helper concurrently while the semaphore keeps the number of simultaneous GitHub calls under control.

*Call graph*: called by 1 (paginate); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 408–420)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Looks up one organization member's public GitHub user profile. If the user cannot be found, it safely keeps the original member record.

**Data flow**: It receives one member record from the surrounding _enrich_users function and reads its login. If there is no valid login, it returns the member unchanged. Otherwise it waits for a semaphore slot, fetches /users/{login}, parses the JSON body, and returns that body if it is a dictionary; on a 404 it returns the original member.

**Call relations**: This is the per-record worker used by _enrich_users. Many copies of it run together through asyncio.gather, but the semaphore from paginate limits the load placed on GitHub.


##### `GitHubConnector._paginate_link_header`  (lines 424–432)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks through GitHub's standard paginated responses. GitHub points to the next page using an HTTP Link header, and this helper follows those links until there are no more pages.

**Data flow**: It receives an HTTP client, a path, and optional query parameters. It asks the base REST connector to fetch link-header pages with the configured page size and parse each response into a list of records. It yields each parsed page and treats empty bodies as no records.

**Call relations**: This is the shared low-level page reader used by paginate, _repo_pages, _iter_granted_org_repo_pages, and _iter_user_orgs. It centralizes GitHub's pagination style so the higher-level methods can focus on which endpoint to call.

*Call graph*: called by 4 (_iter_granted_org_repo_pages, _iter_user_orgs, _repo_pages, paginate).


##### `_partition_field`  (lines 435–443)

```
def _partition_field(path: str) -> str | None
```

**Purpose**: Figures out which partition stamp a stream should have based on its API path. Repository paths need repo_full_name, organization paths need org_login, and unpartitioned paths need no stamp.

**Data flow**: It receives an API path string. It checks whether the path contains repository or organization placeholders and returns the matching field name, or None when the path is not fanned out over a partition.

**Call relations**: flatten calls this before scoping a record's primary key. It is the small rule that connects the endpoint shape to the record field used to keep records from different repos or organizations separate.

*Call graph*: called by 1 (flatten).


##### `_parse_records`  (lines 446–450)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Turns a GitHub HTTP response into the list of records expected by the connector. It accepts only JSON arrays, because GitHub list endpoints return arrays for the streams this helper reads.

**Data flow**: It receives an HTTP response. If the response body is empty, it returns an empty list. Otherwise it parses JSON and returns it only if it is a list; any other JSON shape becomes an empty list.

**Call relations**: _paginate_link_header passes this parser into the base link-header page reader. That keeps response parsing consistent for every endpoint using GitHub's normal list pagination.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 453–468)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: Extracts a safe owner and repository name from a GitHub repository record. It supports several shapes GitHub may return, including full_name, owner.login plus name, or a fallback organization owner.

**Data flow**: It receives one repository record and an optional fallback owner. It first tries to split full_name, then tries owner.login with name, then tries name with the fallback owner. It returns an owner/name pair when it can prove both parts are present, otherwise None.

**Call relations**: _iter_user_repos calls this for each repository record found through organization repository listing. It prevents malformed or incomplete repository records from becoming bad repository partitions.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 471–481)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: Finds the newest and oldest cursor values on a page. A cursor is a timestamp-like field used to remember how far a sync has progressed.

**Data flow**: It receives a page of records and the cursor field path. If there is no cursor field, it returns two None values. Otherwise it reads that field from each record, keeps string values, and returns the maximum and minimum values found, or None values if none are present.

**Call relations**: _repo_pages calls this after fetching and filtering a repository page. The resulting high and low values are placed into WalkPage so PartitionWalk can track watermarks and resume windows for each repository.

*Call graph*: called by 1 (_repo_pages); 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/jira.py`

`io_transport` · `source sync`

This connector is the bridge between the system and Jira. Its job is to ask Atlassian which Jira sites the user’s permission grant can reach, then read useful records from each site. Without it, Jira work items and conversations would not become recallable pages in the system.

The flow is like visiting every office a badge can open. First, the connector asks Atlassian for the list of accessible sites. For each site, it builds Jira API paths using that site’s cloud ID. Then it fetches each kind of data stream: projects, issues, comments, users, boards, and sprints. Most Jira lists arrive in pages, so the connector keeps asking for the next page until Jira says there is no more.

Issues, comments, and sprints can be synced incrementally. That means the connector uses a saved “cursor” value, usually a last-updated timestamp, so later runs only fetch records newer than the previous run. If Jira refuses access with a permission-style error, the connector marks that stream as skipped instead of treating the whole sync as broken.

The file also turns raw Jira issue JSON into readable text. For example, it extracts an issue’s summary, status, priority, people, and description. Jira descriptions and comments use Atlassian Document Format, a nested document tree, so this file walks that tree and collects the visible text.

#### Function details

##### `JiraConnector.paginate`  (lines 73–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right Jira-reading routine for the requested stream and yields batches of records. It is the main doorway the sync runner uses when it wants Jira data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching helper for projects, issues, comments, users, boards, or sprints, and passes each returned page onward. If Jira says access is forbidden or unauthorized, it turns that into a clean “stream skipped” result instead of a hard failure.

**Call relations**: The broader sync process calls this method for each Jira stream. This method then delegates to _projects, _issues, _comments, _users, _boards, or _sprints. If the stream name is unknown, or Jira refuses access, it raises StreamSkipped so the runner can record a skip.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 106–110)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds the Atlassian Cloud Jira sites that the current permission grant can access. This matters because Jira API calls must be scoped to a specific site cloud ID.

**Data flow**: It sends a request to Atlassian’s accessible-resources endpoint. It reads the JSON response, makes sure it is treated as a list, and returns the site records. Each site record can include an ID used in later API paths and a URL used as context.

**Call relations**: _projects, _issues, _users, and _boards call this before reading site-specific data. It uses list_or_empty to avoid crashing if Atlassian returns an empty or unexpected response shape.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 112–132)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira list endpoints that use startAt and maxResults paging. In plain terms, it keeps asking for the next page until the list is finished.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, and the name of the response field that contains the records. It starts at offset zero, requests up to PAGE_SIZE records, pulls the records out of the response, yields them, then advances the offset. It stops when Jira says it is the last page, when no records arrive, or when the total count has been reached.

**Call relations**: This is the shared paging tool used by _projects, _issues, _comments, _boards, and _sprints. It calls records_at to safely extract the list of records from Jira’s response envelope.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 134–141)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira projects from every accessible Jira site. Projects are the containers that organize Jira issues.

**Data flow**: It first gets the accessible sites. For each site with a usable cloud ID, it builds the project search API path, reads all paged results, and adds context such as cloud_id and site_url to every record before yielding it.

**Call relations**: paginate calls this when the requested stream is projects. This function relies on _sites to discover where to look, _offset_values to walk through paged Jira results, and with_context to attach site information to the returned project records.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 143–155)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira issues, optionally only those updated after the saved cursor. This is the main path for bringing work items into the system.

**Data flow**: It builds a Jira Query Language query, which is Jira’s search syntax. If a cursor exists, the query asks only for issues updated after that timestamp; otherwise it asks for all issues ordered by update time. It then visits each accessible site, fetches issue pages with selected fields, adds site context, and yields the issue records.

**Call relations**: paginate calls this for the issues stream, and _comments also calls it so it can discover which issues to inspect for comments. It uses _sites for site discovery, _offset_values for paging, and with_context to preserve which site each issue came from.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 157–177)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads comments for Jira issues. Comments are fetched by first finding issues, then asking Jira for the comments attached to each issue.

**Data flow**: It reads issues without applying the comments cursor to the issue search itself. For each issue with a valid issue ID and cloud ID, it requests that issue’s comment pages. If a cursor was provided, it filters out comments whose updated time is not newer than the cursor. It yields comment batches with added context such as cloud_id, site_url, issue_id, and issue_key.

**Call relations**: paginate calls this for the issue_comments stream. This function depends on _issues to find the issues to inspect, _offset_values to page through each issue’s comments, and with_context to keep each comment tied back to its Jira site and issue.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 179–190)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira users from every accessible site. This lets the system know about people referenced by Jira work.

**Data flow**: It gets the accessible sites, then for each site with a valid cloud ID it calls Jira’s users search endpoint. That endpoint returns a plain JSON array rather than the usual paged envelope, so the function reads the array directly, normalizes it to a list, adds site context, and yields it if there are users.

**Call relations**: paginate calls this for the users stream. It uses _sites for site discovery, list_or_empty to safely treat the response as a list, and with_context to mark which site the users came from.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 192–199)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira Agile boards from every accessible site. Boards are used later to find sprints.

**Data flow**: It asks for accessible sites, skips any site without a valid cloud ID, and calls the Agile board endpoint for each site. It pages through the board results and attaches cloud_id and site_url to each returned board record.

**Call relations**: paginate calls this for the boards stream, and _sprints calls it as the first step in finding sprint data. It uses _sites to discover sites, _offset_values to read paged board lists, and with_context to add site information.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 201–215)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sprints from Jira boards, optionally only those updated after a saved cursor. Sprints are fetched through boards because Jira organizes them that way in the Agile API.

**Data flow**: It first reads boards. For each board with a board ID and cloud ID, it requests that board’s sprint pages. If a cursor exists, it keeps only sprints whose updatedDate is newer than the cursor. It yields the remaining sprint records with cloud_id and board_id attached.

**Call relations**: paginate calls this for the sprints stream. It relies on _boards to find the boards first, then uses _offset_values to page through each board’s sprints and with_context to remember which board and site each sprint belongs to.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 217–224)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Adjusts Jira issue records so the sync system can find the issue update timestamp in a simple, top-level place. Other streams already have their important fields at the top level, so they are left unchanged.

**Data flow**: It receives one record and the stream description. If the record is an issue, it reads the nested fields object and copies fields.updated into a top-level updated value. It returns the adjusted record. For non-issue streams, it returns the original record.

**Call relations**: The sync framework uses this after records are fetched and before cursor tracking. It calls _dict_or_empty so a missing or malformed fields value does not cause an error.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 226–252)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns selected Jira records into human-readable page text. This is what makes an issue or comment useful to read and search, instead of leaving it as raw JSON.

**Data flow**: It receives a record and its stream. For issues, it pulls out the summary, status, priority, assignee, reporter, and description text. For comments, it uses the author as the title and extracts readable text from the comment body. For other streams, it falls back to the parent connector’s default rendering. It returns a title and a formatted text body.

**Call relations**: The sync system calls this when it needs displayable content for a record. It uses _dict_or_empty and _str for safe value reading, _person for names, _field_line for optional metadata lines, and _doc_text to turn Atlassian’s nested document format into plain text.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 255–256)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns a value into a string only if it already is one. It avoids accidentally showing values like dictionaries or nulls as confusing text.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged. Otherwise it returns an empty string.

**Call relations**: render calls this while building issue text, and _person calls it while choosing a display name or email address. It is a small safety helper used to keep rendering predictable.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 259–260)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. This prevents crashes when Jira data is missing or shaped differently than expected.

**Data flow**: It receives any value. If the value is a dictionary, it returns it. Otherwise it returns an empty dictionary, letting callers ask for keys without failing.

**Call relations**: flatten uses this to inspect issue fields, render uses it for nested Jira fields like status and priority, and _person uses it for user objects.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 263–265)

```
def _person(value: Any) -> str
```

**Purpose**: Extracts a readable person name from a Jira user object. It prefers the display name and falls back to the email address.

**Data flow**: It receives a value that may be a Jira person dictionary. It safely treats it as a dictionary, reads displayName and emailAddress, keeps only real strings, and returns the best available label or an empty string.

**Call relations**: render calls this when writing issue assignee, issue reporter, and comment author text. It uses _dict_or_empty and _str so missing or unusual person data does not break rendering.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 268–269)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: Formats one optional metadata line, such as “Status: Done”. It leaves the line out entirely when there is no value.

**Data flow**: It receives a label and a value string. If the value is present, it returns the label and value joined with a colon. If the value is empty, it returns an empty string.

**Call relations**: render uses this while building the readable issue metadata block. This lets render include only meaningful fields and avoid blank lines like “Priority:” with nothing after them.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 272–289)

```
def _doc_text(value: Any) -> str
```

**Purpose**: Extracts readable text from Atlassian Document Format, which is Jira’s nested structure for rich text descriptions and comments. It turns that tree into plain lines of text.

**Data flow**: It receives any value, usually a nested dictionary or list from Jira. It walks through dictionaries and lists, collects every string found under a text key, filters out empty parts, joins the pieces with newlines, and returns the final plain text.

**Call relations**: render calls this for issue descriptions and comment bodies. Inside this function, the nested walk helper does the actual tree traversal.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 277–286)

```
def walk(node: Any) -> None
```

**Purpose**: Walks through one part of an Atlassian document tree and collects visible text. It is the recursive worker inside _doc_text.

**Data flow**: It receives a node from the document tree. If the node is a dictionary, it saves the node’s text value when it is a string, then visits each child in its content list. If the node is a list, it visits each item. It changes the surrounding chunks list by adding found text; it does not return a separate value.

**Call relations**: _doc_text starts this helper at the root document value. The helper then calls itself for child nodes until every nested piece has been inspected.


### `extensions/sources/ufo_ext_sources/linear.py`

`io_transport` · `source sync runs`

Linear is a project and issue tracker, and its API is GraphQL, which means the caller sends a structured query asking for exactly the fields it wants. This file defines the Linear source connector: the part of the system that knows which Linear collections exist, what to ask Linear for, how to page through results, and how to turn important records into readable text.

The file first lists the streams the connector can sync. A stream is one kind of object, like issues, projects, comments, or users. Most streams can be synced incrementally by asking Linear only for records whose `updatedAt` time is newer than the last saved cursor. A few Linear collections do not support that filter, so they are fetched fully each run.

The large query strings are the exact GraphQL questions sent to Linear. The `_STREAM_QUERIES` map connects each stream name to its query, the response field to read, and whether it supports the update-time filter.

`LinearConnector.paginate` is the main reading loop. It sends one request, yields the returned records, follows Linear’s `endCursor` to the next page, and stops when there are no more pages. If Linear refuses access, the stream is skipped cleanly. If Linear reports a GraphQL error, the sync fails rather than silently saving incomplete data.

`render` gives issues, projects, comments, and users a human-friendly text form instead of leaving them as raw API data.

#### Function details

##### `_stream`  (lines 32–42)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard stream description for one Linear collection. This avoids repeating the same setup fields, such as which timestamp is used for incremental syncing.

**Data flow**: It receives a stream name, an optional cursor field, and whether the stream is canonical content. It fills in the common Linear fields, including `createdAt` and `updatedAt`, and returns a `StreamSpec` object that the sync framework can use.

**Call relations**: This helper is used while the file defines `LINEAR_STREAMS`, the catalog of Linear collections available to sync. It hands the finished stream descriptions to the connector class through `streams_list`.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 269–312)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches records from Linear one page at a time for a chosen stream. It is the connector’s main read path for GraphQL data.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous run. It looks up the GraphQL query for that stream, adds an `updatedAt` filter when Linear supports it, posts the request to `/graphql`, yields any records found, then follows Linear’s next-page cursor until there are no more pages. If Linear returns a permission refusal, it raises a skip signal; if Linear returns GraphQL errors, it raises an error so partial data is not accepted.

**Call relations**: The broader sync framework calls this when it wants data for one Linear stream. Inside the loop, it relies on the inherited `_post` request helper from the REST connector base class, uses `list_or_empty` to safely turn the returned `nodes` into a list, and raises `StreamSkipped` when Linear says the token or grant is not allowed to read that stream.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 314–353)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns selected Linear records into readable page text. This matters because a human searching or recalling an issue benefits from seeing a title, state, priority, assignee, or description instead of raw nested API data.

**Data flow**: It receives one Linear record and the stream it came from. For issues, projects, comments, and users, it picks useful fields, cleans them into strings, builds a heading and body text, and returns both a title and rendered content. For other streams, it falls back to the base connector’s default rendering.

**Call relations**: The sync system calls this after records have been fetched and are ready to become recallable pages. It uses `_str` to safely read text, `_ref_id` to pull IDs from nested reference objects, and `_labeled` to build compact metadata blocks.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 356–357)

```
def _str(value: Any) -> str
```

**Purpose**: Safely converts a value into text only when it is already a string. It prevents accidental display of `None`, numbers, dictionaries, or other raw values where clean text is expected.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string.

**Call relations**: This helper is used by `LinearConnector.render` when building readable pages, and by `_ref_id` when extracting an ID from a nested object. It acts like a small safety filter before text is shown to users.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 360–361)

```
def _ref_id(value: Any) -> str
```

**Purpose**: Extracts the `id` from a small nested Linear reference object, such as an assignee or lead. It gives the renderer a simple way to show linked-object IDs without exposing the whole nested object.

**Data flow**: It receives any value. If the value is a dictionary, it reads its `id` field and passes that through `_str`; if not, it returns an empty string.

**Call relations**: This is called by `LinearConnector.render` when rendering fields like an issue assignee or project lead. It delegates final text cleanup to `_str` so missing or non-string IDs do not leak into the rendered page.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 364–365)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: Builds a small block of readable metadata lines, such as `state: open` or `email: person@example.com`. It leaves out empty values so rendered pages stay tidy.

**Data flow**: It receives a list of label-and-value pairs. It keeps only pairs with a non-empty value, formats each as `label: value`, joins them with line breaks, and returns the finished text block.

**Call relations**: This helper is called by `LinearConnector.render` while creating human-friendly pages for issues, projects, and users. It is the final formatting step for the metadata gathered by `_str` and `_ref_id`.

*Call graph*: called by 1 (render).


### Work Operating Systems
Connectors for structured work-management platforms that organize boards, items, activity, tasks, folders, contacts, and comments into searchable records.

### `extensions/sources/ufo_ext_sources/monday.py`

`io_transport` · `during source sync, while fetching and normalizing monday.com streams`

monday.com exposes its data through GraphQL, which is a way to ask an API for exactly the fields you want. This connector is the translator between monday.com’s GraphQL world and this project’s stream-based sync system. Without it, the system would not know how to fetch monday.com pages, follow monday.com’s different paging styles, or shape monday records into useful searchable pages.

The file first defines the streams that can be synced, such as boards, items, and activity logs. Each stream says what kind of object it reads, which field uniquely identifies a record, and, when possible, which timestamp is used for incremental sync. Incremental sync means “only bring back records newer than the last saved point.” monday.com does not provide server-side filtering for this, so the connector fetches pages and filters them locally.

Most reads go through a helper that sends a GraphQL request and unwraps the returned data. If monday.com refuses a query or reports GraphQL errors, the connector marks that stream as skipped instead of saving half-good data. Some monday.com data needs special treatment: board items use a cursor-based page token, activity logs are fetched board by board, and item assignees are hidden inside JSON stored in “people” columns. The final step, `flatten`, adds common fields like title, body, author, and parent links so downstream code can treat different monday.com objects in a more consistent way.

#### Function details

##### `_extract_person_ids`  (lines 70–98)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: This helper pulls assigned person IDs out of monday.com item column data. monday.com stores assignees inside board-specific “people” columns, so this function looks for those columns by type rather than by a fixed column name.

**Data flow**: It receives the raw `column_values` field from a monday.com item. It checks that the value is a list, looks through each column for columns marked as `people`, parses the column’s JSON value when needed, and collects entries whose kind is `person`. It returns a simple list of person IDs as strings, and ignores malformed or unrelated data instead of failing the sync.

**Call relations**: When item records are being fetched, `MondayConnector._items` calls this helper for each item. The extracted IDs are added back onto the item record as `assignee_ids`, making assignees easier for later parts of the system to use.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 106–118)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This is the connector’s common doorway to monday.com’s GraphQL API. It sends a query, checks whether monday.com reported an error, and returns the useful `data` part of the response.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional variables for that query. It posts them to monday.com’s API endpoint, looks for a GraphQL `errors` section, and turns those errors into a skipped stream so the run does not save partial or unreliable data. If the response has a dictionary-shaped `data` section, it returns that; otherwise it returns an empty dictionary.

**Call relations**: This helper is used by the pagination and per-stream fetch functions whenever they need data from monday.com. `MondayConnector._paged_root`, `MondayConnector._items`, `MondayConnector._activity_logs`, and `MondayConnector.paginate` all rely on it so they do not each have to repeat the same request and error-checking steps.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, paginate).


##### `MondayConnector._paged_root`  (lines 120–143)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads monday.com top-level collections that use simple page numbers, such as page 1, page 2, and so on. It also applies local timestamp filtering when an incremental cursor is provided.

**Data flow**: It receives the API client, the monday.com field to query, the fields to select, and optionally a saved cursor and cursor field. It repeatedly asks monday.com for up to 100 records from the next page, converts missing or invalid list data into an empty list, filters out records older than or equal to the saved cursor when requested, and yields each non-empty page. When a page produces no records, it stops.

**Call relations**: This is the shared paging engine for simpler monday.com streams. `MondayConnector._boards` uses it to gather all boards, and `MondayConnector.paginate` uses it directly for streams such as users, workspaces, boards, and updates.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, paginate); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 145–156)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper fetches all monday.com boards, including basic board details and workspace information. Other streams need the board list before they can fetch board-specific data.

**Data flow**: It starts with an empty list, asks `_paged_root` for every page of boards, and adds each page’s records to that list. It returns one combined list of board dictionaries.

**Call relations**: Board items and activity logs are not fetched as one global collection; they are fetched board by board. `MondayConnector._items` and `MondayConnector._activity_logs` call this function first so they know which board IDs to query next.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 158–217)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches item records from every monday.com board. It understands monday.com’s special item paging system, where the first request starts from a board and later requests continue using an opaque cursor token.

**Data flow**: It receives the API client and an optional saved timestamp cursor. It first gets all boards, then for each board asks for the first page of items. If monday.com returns a next-page cursor, it keeps asking for following item pages until no cursor remains. For each item, it adds `assignee_ids` by reading the item’s people columns, filters out old items when a cursor is present, and yields each non-empty batch of item records.

**Call relations**: `MondayConnector.paginate` calls this when the active stream is `items`. Inside its work, it depends on `_boards` to discover board IDs, `_graphql` to make the GraphQL requests, `_extract_person_ids` to simplify assignee data, and small safety helpers to treat missing dictionaries or lists as empty values.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (paginate); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 219–247)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches recent activity log entries from each monday.com board. Activity logs are tied to boards, so the connector has to fan out across boards rather than ask for one global list.

**Data flow**: It receives the API client and an optional saved timestamp cursor. It gets all boards, queries activity logs for each board, attaches the board ID to every log entry, filters out entries that are not newer than the cursor when one is provided, and yields non-empty batches.

**Call relations**: `MondayConnector.paginate` calls this when syncing the `activity_logs` stream. It uses `_boards` to decide which boards to inspect and `_graphql` to fetch the log data from monday.com.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (paginate); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 249–323)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher that knows how to fetch each monday.com stream. Given a stream name, it chooses the right GraphQL query or helper and yields records page by page.

**Data flow**: It receives the HTTP client, a stream description, and an optional saved cursor. It checks the stream name, runs the matching query path, and yields pages of records for that stream. For streams with timestamps, it passes the cursor along so older records can be filtered out. If the stream is unknown, or monday.com refuses access with an authorization-style response, it raises a skipped-stream signal instead of pretending the sync succeeded.

**Call relations**: The connector framework calls this method when it wants records for a particular monday.com stream. This method then hands off to `_paged_root` for simple paged collections, `_items` for board items, `_activity_logs` for board logs, or `_graphql` directly for smaller one-shot streams like teams and tags.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _graphql, _items, _paged_root).


##### `MondayConnector.flatten`  (lines 325–366)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes raw monday.com records into a more consistent form for the rest of the system. It keeps the original fields but adds or standardizes important fields such as name, body, author, status, timestamps, API URL, and parent record links.

**Data flow**: It receives one raw record and the stream it came from. Depending on the stream, it copies the record and fills in normalized fields from monday.com-specific fields: for example, updates get a readable body and author, activity logs get a subject and parent board ID, and boards get an API URL fallback. It returns the adjusted record without contacting monday.com.

**Call relations**: After `paginate` has produced records, the broader connector framework can call this method to prepare each record for storage or indexing. It does not fetch more data; it is the final cleanup step that makes different monday.com streams easier to treat uniformly.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/wrike.py`

`io_transport` · `source sync run`

Wrike’s API sends data in pages, like a book where each page tells you how to find the next one. This connector knows how to follow those pages for several Wrike object types: contacts, folders, tasks, comments, workflows, and custom fields. It defines which streams exist, what field uniquely identifies each item, and which streams can be checked incrementally using Wrike’s updatedDate field.

The main class, WrikeConnector, plugs into the project’s shared REST connector framework. During a sync, it asks Wrike for each stream’s endpoint, follows Wrike’s nextPageToken until there are no more pages, and yields batches of records. Wrike does not provide a dependable “give me only items changed since this time” option, so the connector fetches pages and then locally drops records whose updatedDate is not newer than the stored cursor. If Wrike refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of crashing the whole source run.

After records arrive, flatten reshapes some Wrike-specific fields into common names such as name, email, created_at, due_date, body, and author. This makes Wrike data easier for the rest of the system to search and display without needing to understand Wrike’s raw response format.

#### Function details

##### `_profile_email`  (lines 54–64)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper looks inside a Wrike contact record and finds the first usable email address. Wrike stores email addresses inside a profiles list, so this function hides that nesting from the rest of the connector.

**Data flow**: It receives one Wrike record as a dictionary. It checks whether the record has a profiles value that is really a list, then walks through that list looking for a dictionary with a non-empty string email. It returns that email if it finds one; otherwise it returns nothing.

**Call relations**: WrikeConnector.flatten calls this when it is reshaping a contact record. The helper gives flatten a clean email value so the final contact record can expose email directly instead of forcing later code to inspect Wrike’s profiles structure.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 72–96)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches records from Wrike a page at a time for one stream, such as tasks or folders. It also enforces which Wrike streams are actually supported and skips streams when Wrike refuses access.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor value. It checks that the stream is one this connector can run, asks the shared REST paging helper to request Wrike pages, and reads records from each page’s data field while following nextPageToken for the next page. If a cursor and cursor field are available, it keeps only records newer than that cursor. It yields non-empty batches of records. If Wrike returns 401 or 403, it turns that refusal into a StreamSkipped result; other HTTP errors are allowed to continue upward.

**Call relations**: The sync framework calls this when it needs raw records for a Wrike stream. Inside the flow, this function relies on the base REST connector’s cursor-page fetching helper to do the repeated HTTP requests. When a stream is unsupported or Wrike denies access, it raises StreamSkipped so the wider sync can move past that stream instead of treating it as a full connector failure.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 98–136)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function turns Wrike’s raw records into records with common, easier-to-use fields. It is where stream-specific cleanup happens, such as making a contact name from first and last name or copying a task due date into a standard due_date field.

**Data flow**: It receives one raw Wrike record and the stream description that says what kind of object it is. For contacts, it builds a display name, extracts an email with _profile_email, and copies the creation date. For folders, it uses the title as the name and builds an API URL. For tasks, it reads nested date information safely with dict_or_empty and sets name, status, due_date, and created_at. For comments, it maps Wrike’s text and author fields into body and author and links the comment back to its task. For streams without special rules, it returns the record unchanged.

**Call relations**: After paginate has supplied raw Wrike records, the connector framework can call flatten to normalize each record before downstream storage or indexing. It calls _profile_email for contact email extraction and dict_or_empty when reading task date details, so malformed or missing nested date data does not break the flattening step.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).


### Reliability Operations
Connectors for incident response and error-tracking systems that sync operational records such as incidents, services, schedules, projects, issues, events, releases, and members.

### `extensions/sources/ufo_ext_sources/pagerduty.py`

`io_transport` · `source sync run`

PagerDuty is an incident-response service, and its API returns information in small pages rather than all at once. This file is the read-only connector for that API. Without it, the system would not know which PagerDuty endpoints to call, how to move through PagerDuty’s pagination, or how to treat permission problems without failing the whole run.

At the top, the file defines the streams it can read. A stream is one kind of thing to sync, such as “incidents” or “users.” Each stream says where the records live in PagerDuty’s response and which field uniquely identifies each record. Some streams also say which timestamp should be used as a cursor, meaning a saved “last seen” point so the next run can fetch only newer changes.

The `PagerDutyConnector` builds on a general REST connector. It adds PagerDuty-specific details: the required API media type header, offset-and-limit pagination, incremental incident fetching sorted by update time, and a special path for incident notes, which must be fetched separately for each incident. If PagerDuty replies with “unauthorized” or “forbidden,” the connector marks that stream as skipped instead of treating the entire sync as broken. Like a polite librarian, it notes that a shelf was locked and keeps working where it can.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 74–77)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to PagerDuty. It adds PagerDuty’s required `Accept` header, which tells the API which version and response format the connector expects.

**Data flow**: It receives a base URL and a credential object. It asks the parent REST connector to create the normal authenticated HTTP client, then adds the PagerDuty-specific header to that client. The result is a ready-to-use client for PagerDuty API requests.

**Call relations**: This is a setup hook for the connector’s network access. Before any stream can be paged through, the connector needs this client so later methods can make correctly formatted PagerDuty requests.


##### `PagerDutyConnector._offset_pages`  (lines 79–99)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads ordinary PagerDuty list endpoints one page at a time. It also applies a saved cursor when needed, so records older than the last sync point are not sent onward again.

**Data flow**: It receives an HTTP client, a stream description, optional request parameters, and an optional cursor value. It calls the shared offset-pagination helper for the stream’s endpoint, using PagerDuty’s `more` flag and returned `limit` value to know when and how to ask for the next page. If a cursor field is defined, it filters out records whose timestamp is not newer than the cursor, then yields each non-empty batch of records.

**Call relations**: This is the common paging worker. `PagerDutyConnector._incidents` uses it for incident pages with incident-specific sorting and filtering, and `PagerDutyConnector.paginate` uses it directly for standard streams such as users, teams, services, schedules, escalation policies, and on-calls.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 101–113)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads PagerDuty incidents in update-time order, which makes incremental syncing reliable. If a cursor is present, it asks PagerDuty for incidents changed since that point.

**Data flow**: It receives an HTTP client and an optional cursor. It builds request parameters that sort incidents by `updated_at` from oldest to newest, adds `since` when a cursor is available, and then delegates the actual page-by-page reading to `PagerDutyConnector._offset_pages`. It yields each incident page it receives.

**Call relations**: This is the incident-specific layer above the generic offset pager. `PagerDutyConnector.paginate` calls it when the requested stream is incidents, and `PagerDutyConnector._incident_notes` calls it to discover which incidents exist before fetching notes for each one.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 115–128)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads notes attached to incidents. PagerDuty does not expose these as one simple global list, so the connector first walks through incidents and then asks for the notes on each incident.

**Data flow**: It receives an HTTP client and an optional note cursor. It reads incidents, takes each valid incident ID, calls PagerDuty’s incident-notes endpoint for that ID, and extracts the `notes` list from the response. If a cursor is present, it keeps only notes created after that point. Before yielding notes, it adds the incident ID as extra context so each note can be tied back to its incident.

**Call relations**: This function is used by `PagerDutyConnector.paginate` when the stream is `incident_notes`. Inside, it relies on `PagerDutyConnector._incidents` to find incidents, `records_at` to pull the notes list out of PagerDuty’s response, and `with_context` to attach the parent incident ID to each note batch.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 130–164)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading any PagerDuty stream. Given a stream name, it chooses the right fetching strategy and yields batches of records to the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. For incidents, it sends the work to `PagerDutyConnector._incidents`; for incident notes, it sends the work to `PagerDutyConnector._incident_notes`; for the standard list-style streams, it uses `PagerDutyConnector._offset_pages`. If the stream is unknown, it raises `StreamSkipped`. If PagerDuty refuses access with HTTP 401 or 403, it turns that into a skipped stream message instead of a hard failure; other HTTP errors still bubble up.

**Call relations**: The sync runner calls this when it wants records for a particular PagerDuty stream. This method acts like a traffic director: it hands each stream to the helper that understands that endpoint, and it converts known permission failures into `StreamSkipped` so the larger run can record the skip and continue appropriately.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/sentry.py`

`io_transport` · `source sync`

This connector is the bridge between UFO and Sentry’s web API. Without it, UFO would not know which Sentry URLs to call, how to follow Sentry’s pagination, or how to keep records tied to the organization and project they came from.

The file starts by defining the Sentry streams UFO can sync. A stream is a category of records, such as projects or issues, with details like its unique key and timestamp fields. The main class, SentryConnector, then chooses the right fetching path for each stream.

Sentry returns long lists in pages. Instead of giving all data at once, it puts a “next page” cursor in a Link header. Think of this like a bookmark tucked into a stack of papers: after reading one stack, the bookmark tells you where the next stack begins. The helper _sentry_next_cursor reads that bookmark, and _paged_list keeps requesting pages until there is no next cursor.

Some streams depend on others. For example, members are fetched organization by organization, while issues and events are fetched project by project. The connector adds organization_slug and project_slug to child records so later code can tell where each item belongs. If Sentry refuses access with a 401 or 403 response, the connector skips that stream with a clear message instead of crashing the whole sync.

#### Function details

##### `_sentry_next_cursor`  (lines 76–81)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper looks at Sentry’s response headers and finds the cursor for the next page, if there is one. It exists because Sentry hides pagination state in a Link header rather than in the JSON body.

**Data flow**: It receives HTTP headers from a Sentry response. It checks for a Link header, searches it for the specific pattern that means “there is another page with results,” and returns the cursor text. If there is no usable next-page marker, it returns nothing.

**Call relations**: SentryConnector._paged_list calls this after each API response. The returned cursor decides whether _paged_list asks Sentry for another page or stops reading that list.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.paginate`  (lines 89–128)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry point the sync system uses to ask for one Sentry stream. It decides which lower-level reader to use for organizations, projects, members, issues, events, or releases.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It routes the request to the matching helper, yields pages of records, and applies simple cursor filtering for projects when needed. If the requested stream is unknown, or Sentry refuses access with 401 or 403, it turns that situation into a StreamSkipped signal.

**Call relations**: The broader UFO source runner calls paginate when it wants records for a stream. paginate then hands off to _organizations, _projects, _members, _issues, _events, or _releases. It is the dispatcher that keeps the rest of the runner from needing to know Sentry’s URL layout.

*Call graph*: calls 7 internal fn (__init__, _events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._paged_list`  (lines 130–147)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads one Sentry list endpoint from start to finish, page by page. It is the shared paging engine used by all the stream-specific readers.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It repeatedly calls the Sentry endpoint, turns the JSON response into a list of dictionary-like records, yields non-empty pages, and then uses the response headers to find the next cursor. When there is no next cursor, it stops.

**Call relations**: _organizations, _projects, _members, _issues, _events, and _releases all rely on this function so they do not each have to reimplement Sentry pagination. It calls _sentry_next_cursor after every response to know whether to continue.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 149–153)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function fetches all Sentry organizations visible to the current credential. Organizations are the top-level containers that many other Sentry records belong to.

**Data flow**: It starts with an empty list, asks _paged_list for pages from the organizations endpoint, and appends every page into one combined list. It returns that full list of organization records.

**Call relations**: paginate calls this directly for the organizations stream. _members and _releases also call it first, because they need to know which organizations to visit before fetching members or releases.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, paginate).


##### `SentryConnector._projects`  (lines 155–159)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function fetches all Sentry projects visible to the current credential. Projects are needed before the connector can fetch project-specific data such as issues and events.

**Data flow**: It starts with an empty list, reads every page from the projects endpoint through _paged_list, adds those records together, and returns the combined project list.

**Call relations**: paginate calls this directly for the projects stream. _issues and _events call it first so they can loop through every project and then request that project’s issues or events.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, paginate).


##### `SentryConnector._members`  (lines 161–167)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches members for each Sentry organization. It also labels each member record with the organization slug so the member is not separated from its context later.

**Data flow**: It first reads all organizations. For each organization with a valid slug, it requests that organization’s members through _paged_list. Before yielding each page, it adds organization_slug to every record using with_context.

**Call relations**: paginate calls _members when the sync runner asks for the members stream. _members depends on _organizations to discover where to look, and on _paged_list to do the actual page-by-page API reading.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 169–183)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches issues for every accessible Sentry project. If a cursor is supplied, it asks Sentry only for issues seen after that point, which helps incremental syncs avoid rereading old data.

**Data flow**: It reads the project list, extracts each project’s organization slug and project slug, and skips projects missing those values. It builds an issues API path for each valid project, optionally adds a lastSeen query filter, reads pages through _paged_list, and adds organization_slug and project_slug to each yielded record.

**Call relations**: paginate calls _issues for the issues stream. _issues uses _projects as its map of where to search, _paged_list to fetch each project’s issues, and with_context to preserve where each issue came from.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 185–199)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches event records for every accessible Sentry project. Events are lower-level occurrences, and this stream is marked as non-canonical because issues are usually the main user-facing error records.

**Data flow**: It reads all projects, pulls out each project’s organization slug and project slug, and skips incomplete entries. It optionally builds a query that asks for events newer than the cursor, reads event pages for each project, and adds organization_slug and project_slug to the records before yielding them.

**Call relations**: paginate calls _events when the events stream is requested. Like _issues, it first depends on _projects to know which project endpoints exist, then uses _paged_list for Sentry pagination and with_context for useful record labeling.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 201–212)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches releases for each Sentry organization. Releases are version markers, and the function can filter them by creation date when continuing from a previous sync cursor.

**Data flow**: It reads all organizations, keeps only those with a valid slug, and fetches releases for each one through _paged_list. If a cursor is provided, it removes releases whose dateCreated value is not newer than that cursor. It yields only non-empty pages, with organization_slug added to every record.

**Call relations**: paginate calls _releases for the releases stream. _releases uses _organizations to find the organizations to visit, _paged_list to read each organization’s release pages, and with_context to attach the organization identity to the returned records.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).
