# Work management, development, and operations source connectors  `stage-12.3`

This stage is shared behind-the-scenes support for bringing outside work systems into the project’s memory. Each connector knows how to talk to one service’s web API, meaning the service’s online doorway for requesting data. Because these services return results in pages, like search results spread over many screens, the connectors keep asking for the next page and turn everything into a steady stream of standard records.

The Asana, ClickUp, Jira, Linear, monday.com, and Wrike connectors gather project-management data such as tasks, issues, projects, comments, users, boards, folders, and custom fields. ClickUp also has to walk a nested structure of teams, spaces, folders, lists, and tasks. The GitHub connector brings in engineering work: organizations, repositories, issues, commits, events, comments, and users. PagerDuty adds operations data such as services, incidents, schedules, notes, and on-call records. Sentry adds error-tracking data such as projects, issues, events, members, and releases. Together, these files act like adapters for different plug shapes, making many tools feed the same sync and search system.

## Files in this stage

### Task and workspace management
Connectors for general-purpose work-management platforms that expose projects, tasks, comments, users, and workspace structure.

### `extensions/sources/ufo_ext_sources/asana.py`

`io_transport` · `source sync / request handling`

Asana stores work-tracking information behind a REST API, which is a web interface where the system asks for data using URLs. This file defines the Asana source connector: the read-only bridge between Asana and the larger sync system.

The file first describes the kinds of Asana objects the system knows how to fetch. These are called streams: projects, tasks, stories, users, attachments, teams, workspaces, and other supporting collections. A stream is like a named checkout lane for one kind of object. Some important streams are marked as canonical, meaning they are the main records people are most likely to search and recall later.

Asana does not send every record at once. It sends pages of up to 100 items, plus a token that says where to continue. The connector follows those tokens until Asana says there are no more pages. For projects and tasks, it can also ask Asana for only records changed since a saved time, which makes later syncs faster. Other streams are fetched fresh each run.

The connector does not store or create Asana credentials itself, and it does not write back to Asana. Its job is only to read data safely through the shared REST connector machinery.

#### Function details

##### `_stream`  (lines 24–31)

```
def _stream(name: str, *, cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one type of Asana object. It keeps the repeated setup for streams short and consistent, so every stream has the same basic shape: a name, an Asana object name, a primary key, and optional cursor information.

**Data flow**: It receives a stream name, plus optional information about which time field can be used as a cursor and whether the stream is a main searchable stream. It packages those details into a StreamSpec object, using Asana’s `gid` field as the unique record ID. The result is a stream definition that the connector can later use when fetching data.

**Call relations**: This helper is used while building the file’s list of Asana streams. It hands the finished stream descriptions to StreamSpec.__init__, which creates the objects later read by AsanaConnector when deciding which API path to call and whether a cursor can be used.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 62–78)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one Asana stream page by page. It knows Asana’s pagination style: ask for a page, yield the records, then follow Asana’s next-page offset until there is nothing left.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It builds a request path from the stream name and starts with a page size of 100. If the stream is projects or tasks and a cursor is available, it adds `modified_since` so Asana only returns recently changed records. For each response, it pulls the `data` list out safely, yields that list if it has records, reads the next-page offset, and repeats. When there is no valid offset, it stops.

**Call relations**: The wider REST source runner calls this method when it needs records for a particular Asana stream. Inside the loop, it relies on the inherited `_get` method to make the actual web request, then uses `ufo.sdk.sources.list_or_empty` to turn Asana’s `data` field into a safe list before handing records back to the sync pipeline.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/clickup.py`

`io_transport` · `source sync`

ClickUp stores work like a set of nested boxes: a team contains spaces, a space can contain folders, folders contain lists, and lists contain tasks and comments. This connector is the map that tells the sync system how to open those boxes in the right order. Without it, the project could not import ClickUp work items or remember where each item came from.

The file defines the ClickUp streams the system can read, such as teams, users, spaces, tasks, list comments, custom fields, and goals. A stream is one kind of data the sync system treats as a separate feed. The connector uses ClickUp’s web API with an OAuth bearer token supplied elsewhere by the credential system.

Most methods follow the same pattern: ask ClickUp for a parent object, then use its ID to fetch its children. When records are found, the connector adds context like `team_id`, `space_id`, `folder_id`, or `list_id`, so a task or comment is not separated from its place in the ClickUp hierarchy. Tasks are read page by page, and both tasks and some child streams can use a cursor, which is a saved “last seen” value used to avoid re-reading older records.

The connector is read-only. It imports ClickUp data into the system; it does not create or update anything in ClickUp.

#### Function details

##### `ClickUpConnector._teams`  (lines 50–52)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the top-level ClickUp teams available to the authenticated account. Teams are the starting point for almost every other ClickUp lookup, so this is the connector’s first step into the hierarchy.

**Data flow**: It takes an HTTP client that can make authorized requests. It calls ClickUp’s `/team` endpoint, pulls the `teams` list out of the response, and returns that list as plain records for the rest of the connector to use.

**Call relations**: Other methods call this when they need a starting set of teams. `_spaces` uses it to find which teams to search for spaces, and `paginate` uses it directly when the requested stream is teams or when it needs to build users or goals from team data.

*Call graph*: called by 2 (_spaces, paginate); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 54–62)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This finds the non-archived spaces inside every ClickUp team. A space is the next level down from a team, so finding spaces is needed before folders and many lists can be discovered.

**Data flow**: It starts with an HTTP client, asks `_teams` for all teams, and skips any team without a usable ID. For each valid team, it requests that team’s spaces, extracts the returned space records, adds the parent `team_id` to each one, and returns one combined list.

**Call relations**: This sits one step below `_teams` in the hierarchy walk. `_folders`, `_lists`, and `paginate` call it when they need space records, and it relies on `records_at` to pull records from ClickUp responses and `with_context` to attach the team ID.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 64–74)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This finds the non-archived folders inside every ClickUp space. Folders are one possible container for lists, so they must be discovered before folder-based lists can be read.

**Data flow**: It receives an HTTP client, asks `_spaces` for all spaces, and ignores spaces without a valid ID. For each valid space, it calls ClickUp’s folder endpoint, extracts the folder records, adds the parent `space_id`, and returns all folders together.

**Call relations**: This is the next link in the parent-to-child chain. `_lists` calls it to discover lists that live inside folders, while `paginate` calls it when the sync system asks specifically for the folders stream.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 76–92)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This gathers all non-archived lists, whether they live inside folders or directly inside spaces. Lists matter because tasks, comments, and custom fields are read from individual lists.

**Data flow**: It first asks `_folders` for folders and fetches lists under each valid folder, adding `folder_id` to those list records. Then it asks `_spaces` for spaces and fetches lists that are directly under each valid space, adding `space_id`. The output is one combined list of ClickUp list records with their parent context attached.

**Call relations**: This method is the gateway to ClickUp’s leaf data. `_tasks` and `_list_child_stream` call it before reading tasks, comments, or custom fields, and `paginate` calls it when the requested stream is lists.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _tasks, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 94–119)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads tasks from every discovered ClickUp list. It supports incremental syncing by using a cursor, meaning it can skip tasks whose `date_updated` value is not newer than the saved checkpoint.

**Data flow**: It takes an HTTP client and an optional cursor. It asks `_lists` for all lists, then for each valid list ID it requests tasks page by page. Each task is tagged with its `list_id` and `list_name`; if a cursor is present, older or already-seen tasks are filtered out. It yields batches of task records as they are found, and stops paging a list when ClickUp returns no tasks.

**Call relations**: The main `paginate` method delegates to this when the sync system asks for the tasks stream. `_tasks` depends on `_lists` to know where to look, then hands batches back upward so the broader sync machinery can store or process them.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 121–140)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads data that belongs to a list but is not a task: list comments and list custom fields. It uses the same list-by-list walk so each child record stays tied to the list it came from.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It asks `_lists` for all lists, chooses the correct ClickUp endpoint based on whether the stream is comments or custom fields, extracts the records, and adds `list_id` and `list_name`. If the stream has a cursor field and a cursor was supplied, it keeps only newer records. It yields each non-empty batch.

**Call relations**: `paginate` calls this for the `list_comments` and `list_custom_fields` streams. It is a shared helper so the two per-list child feeds can reuse the same discovery and filtering pattern instead of duplicating it.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 142–196)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main dispatcher for reading a requested ClickUp stream. The sync system asks for one stream, and this method chooses the right helper, gathers records, and yields them in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. Based on the stream name, it fetches teams, users, spaces, folders, lists, tasks, list comments, custom fields, or goals. For users, it builds a deduplicated user list from team members embedded in team responses. For goals, it fetches goals under each team. It yields records only when there is something to return; if the stream is unknown, it raises `StreamSkipped` to say this connector does not implement it.

**Call relations**: This is the method the general REST source framework calls during a ClickUp sync. It coordinates the lower-level hierarchy methods such as `_teams`, `_spaces`, `_folders`, `_lists`, `_tasks`, and `_list_child_stream`, then hands the resulting batches back to the framework.

*Call graph*: calls 7 internal fn (__init__, _folders, _list_child_stream, _lists, _spaces, _tasks, _teams); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 198–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes selected ClickUp records into a more consistent form for the rest of the system. It keeps the original data but adds or normalizes common fields like name, email, status, creation time, body text, and parent IDs.

**Data flow**: It receives one raw record and the stream it belongs to. For users, it chooses a display name and creation date from ClickUp’s fields. For spaces, folders, and lists, it ensures there is a name and API URL. For tasks, it simplifies status and creation fields. For list comments, it extracts the comment body, author, creation time, and parent list ID. If no special shaping is needed, it returns the record unchanged.

**Call relations**: After `paginate` has produced raw records, the source framework can call this to make records easier to search, display, or store consistently. It uses `dict_or_empty` when reading a comment’s user object so missing or malformed user data does not break the flattening step.

*Call graph*: 1 external calls (dict_or_empty).


### Engineering work tracking
Connectors for developer-centric systems that synchronize repositories, issues, projects, comments, teams, and sprint or issue metadata.

### `extensions/sources/ufo_ext_sources/github.py`

`io_transport` · `during source sync`

GitHub does not hand over all account data in one neat bundle. It exposes many web API endpoints, each returning one page at a time, and many useful records live under a specific organization or repository. This file is the adapter that knows how to walk that maze safely.

The connector starts from the organizations the authenticated user can see. From each organization, it finds repositories, skipping archived repositories and forks. Then, for repository-based streams such as issues, commits, releases, and branches, it visits each repository in turn. This is like first making a map of all library branches, then checking each branch shelf by shelf.

The file also knows how to resume work. Some GitHub endpoints can be asked for records updated after a time. Others only show newest records first, so the connector uses a partition walk: each repository is treated as its own section with its own progress marker. That prevents new GitHub activity from shifting pages and causing missed records.

It also handles GitHub-specific behavior. It follows GitHub’s Link header for pagination, adds required API headers, enriches organization members with fuller user profiles when possible, and treats some permission or missing-resource errors as skips instead of run-ending failures. There is no write path here; this connector only reads from GitHub.

#### Function details

##### `_stream`  (lines 58–74)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, ordering: Ordering=Ordering.none, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a stream description for one kind of GitHub data, such as issues or repositories. A stream description tells the sync system the stream name, how to identify records, and whether records can be read in a useful time order.

**Data flow**: It receives a stream name plus optional details like the GitHub object name, primary key, cursor field, ordering, and whether it is a main supported stream. It fills in sensible defaults, then returns a StreamSpec object that the connector uses later when deciding what to fetch.

**Call relations**: This helper is used while building the file’s stream catalog. It hands the finished settings to StreamSpec.__init__, so the rest of the connector can treat each GitHub data type in a uniform way.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 163–166)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the GitHub streams that this connector can actually fetch today. Some streams are listed for catalog compatibility, but only streams with a matching API path are runnable.

**Data flow**: It reads the connector’s full stream list and the internal path table. It filters out any stream that has no configured GitHub API path, then returns the remaining stream descriptions.

**Call relations**: The sync framework calls this when it needs to know what this connector can offer. It keeps the advertised runnable set tied to the path table, so adding a path automatically makes a catalogued stream available.


##### `GitHubConnector._make_client`  (lines 168–172)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to GitHub and adds GitHub’s required API headers. These headers tell GitHub which response format and API version the connector expects.

**Data flow**: It receives a base URL and a credential. It asks the parent RestConnector to create the authenticated client, then adds the GitHub Accept header and API version header. It returns the ready-to-use client.

**Call relations**: The wider connector framework calls this during setup before requests are made. It builds on the base connector’s client creation and adapts it for GitHub specifically.


##### `GitHubConnector.paginate`  (lines 174–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main dispatcher for reading a GitHub stream page by page. It chooses the right path through GitHub’s API depending on whether the stream is organization-based, repository-based, or a special repository catalog.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor showing where a previous sync left off. It looks up the stream’s API path, prepares common query parameters, then yields pages of records or partition-aware pages back to the sync runner. Along the way it may enumerate organizations, enumerate repositories, enrich users, or create a PartitionWalk to resume repository streams safely.

**Call relations**: The sync runner calls this when it wants records for a stream. It calls _iter_granted_org_repo_pages for repository discovery, _iter_user_orgs for organization streams, _paginate_link_header for ordinary GitHub pagination, and _enrich_users for fuller user records. For repository streams, it creates a PartitionWalk and supplies the nested repos and repo_pages helpers so each repository can be synced with its own progress.

*Call graph*: calls 4 internal fn (_enrich_users, _iter_granted_org_repo_pages, _iter_user_orgs, _paginate_link_header); 2 external calls (__init__, Semaphore).


##### `GitHubConnector.paginate.repos`  (lines 192–194)

```
async def repos() -> AsyncIterator[str]
```

**Purpose**: Provides repository names to the partition walker. It turns discovered repositories into simple owner/repo strings.

**Data flow**: It reads repositories from _iter_user_repos. For each owner and repository name, it joins them into a single text key like "owner/repo" and yields that key to the partition walk.

**Call relations**: This helper lives inside paginate because it is only needed when paginating repository-scoped streams. PartitionWalk calls on it to learn which repositories are the separate partitions to walk.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector.paginate.repo_pages`  (lines 196–197)

```
def repo_pages(repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Provides the pages for one repository partition. It connects the generic partition walker to the GitHub-specific repository page reader.

**Data flow**: It receives a repository key and a partition bound, which is the saved time window or resume marker for that repository. It passes the client, stream, API path, repository key, and bound into _repo_pages, then returns that asynchronous page stream.

**Call relations**: This helper is supplied to PartitionWalk from paginate. When the walker decides it is time to read a particular repository, this function hands off the real API work to _repo_pages.

*Call graph*: calls 1 internal fn (_repo_pages).


##### `GitHubConnector._repo_pages`  (lines 232–280)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Reads one repository’s pages for one stream, while respecting the saved resume point. It is where repository-specific rules such as issue filtering, time bounds, and skipped repositories are applied.

**Data flow**: It receives a client, stream description, API path, repository key, and partition bound. It formats the API path with the repository owner and name, builds query parameters, applies time filters when GitHub supports them, and reads pages through _paginate_link_header. It may remove pull requests from the issues stream, trim newest-first pages on the client side, compute each page’s cursor range, and yield WalkPage objects. If GitHub says the repository is missing or unavailable, it raises PartitionSkipped instead of failing the whole sync.

**Call relations**: It is called by the nested paginate.repo_pages helper during a PartitionWalk. It relies on _paginate_link_header to fetch GitHub pages and _cursor_bounds to report the high and low cursor values that the walker needs for checkpointing.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); called by 1 (repo_pages); 2 external calls (__init__, __init__).


##### `GitHubConnector._iter_user_repos`  (lines 282–290)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: Finds repositories that belong to organizations the current GitHub grant can see. It deliberately avoids the broader /user/repos endpoint so the sync stays scoped to organization access.

**Data flow**: It receives an HTTP client. It asks _iter_granted_org_repo_pages for pages of repository records, extracts each repository’s owner and name with _repo_identity, and yields valid owner/repo pairs.

**Call relations**: The nested paginate.repos helper calls this when repository-scoped streams need their partition list. It sits between organization repository-page discovery and the partition walker’s simple list of repository keys.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (repos).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 292–311)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: Walks through all visible organizations and yields pages of usable repositories from each one. It filters out archived repositories and forks so the sync focuses on active organization-owned repositories.

**Data flow**: It receives an HTTP client. It gets organization logins from _iter_user_orgs, requests each organization’s repositories through _paginate_link_header, removes archived and forked repositories, and yields only non-empty pages together with the organization name. If one organization refuses access or is gone, it skips that organization and continues.

**Call relations**: It is called directly by paginate for the repositories stream and by _iter_user_repos for repository-scoped streams. It depends on _iter_user_orgs for the organization list and _paginate_link_header for GitHub’s paged API responses.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, paginate).


##### `GitHubConnector._iter_user_orgs`  (lines 313–334)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Lists the GitHub organizations visible to the current credential. This is the root discovery step for almost every runnable stream in this connector.

**Data flow**: It receives an HTTP client, requests /user/orgs through _paginate_link_header, and yields each valid organization login it finds. If GitHub returns a permission error for this root organization listing, it raises StreamSkipped so the run records a clean skip rather than a broken failure.

**Call relations**: It is called by _iter_granted_org_repo_pages and by paginate for organization-scoped streams. Because all repository discovery starts here, a refusal at this step means the connector has no organization scope to work with.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, paginate).


##### `GitHubConnector._enrich_users`  (lines 336–356)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: Turns basic organization member records into fuller public user records when GitHub allows it. This can add details such as public name or email.

**Data flow**: It receives an HTTP client, one page of user member records, and a semaphore, which is a small gate that limits how many user lookups run at the same time. It starts one lookup task per member, waits for them all with asyncio.gather, and returns a new list where each member is replaced by the fuller user record when available.

**Call relations**: paginate calls this only for the users stream after fetching organization members. It uses the nested one helper for each individual member and the semaphore created in paginate to avoid sending too many GitHub requests at once.

*Call graph*: called by 1 (paginate); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 342–354)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Looks up one GitHub user by login and returns the fuller public profile if possible. If the member has no usable login or GitHub cannot find the user, it leaves the original record unchanged.

**Data flow**: It receives one member record from the surrounding _enrich_users function. It reads the login field, waits for permission from the semaphore, requests /users/{login}, and parses the response. It returns the full user dictionary when the response is usable, otherwise the original member dictionary.

**Call relations**: This nested helper is launched once for each user in a page by _enrich_users. Its work is coordinated with asyncio.gather so the page can be enriched concurrently but still returned as one list.


##### `GitHubConnector._paginate_link_header`  (lines 358–366)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the common GitHub pagination loop. GitHub uses a Link header to point to the next page, and this function wraps that pattern for all streams in the file.

**Data flow**: It receives a client, an API path, and optional query parameters. It calls the base connector’s link-header page reader with the GitHub page size and _parse_records as the response parser, then yields each parsed list of records. Empty responses or temporary empty stats responses produce no records.

**Call relations**: This is the shared page-reading tool used by paginate, _repo_pages, _iter_user_orgs, and _iter_granted_org_repo_pages. It hides the repeated mechanics of following GitHub’s next-page links.

*Call graph*: called by 4 (_iter_granted_org_repo_pages, _iter_user_orgs, _repo_pages, paginate).


##### `_parse_records`  (lines 369–373)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Extracts a list of records from a GitHub HTTP response. It keeps the connector focused on endpoints that return bare JSON arrays.

**Data flow**: It receives an HTTP response. If the response body is empty, it returns an empty list. Otherwise it parses the JSON body and returns it only if it is a list; non-list bodies are treated as no records.

**Call relations**: _paginate_link_header passes this parser into the lower-level page reader. That lets the shared pagination code know how to turn each GitHub response into records for the connector.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 376–391)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: Finds the owner and repository name inside a GitHub repository record. It accepts several shapes of GitHub data so repository discovery is more tolerant.

**Data flow**: It receives a repository record and, optionally, a fallback owner name. It first tries the full_name field, then the nested owner.login plus name fields, then the fallback owner plus name. It returns an owner/repo pair when it can build one, or None when the record does not contain enough information.

**Call relations**: _iter_user_repos calls this while turning repository records into partition keys. It protects the repository walk from malformed or unexpectedly shaped records by simply ignoring records that cannot identify a repository.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 394–404)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: Finds the newest and oldest cursor values on a page. The partition walker uses these values to know what progress has been made and where to resume later.

**Data flow**: It receives a page of records and the name of the cursor field, such as updated_at or created_at. If there is no cursor field or no usable cursor values, it returns two None values. Otherwise it collects string cursor values and returns the maximum and minimum values from the page.

**Call relations**: _repo_pages calls this after it has prepared a non-empty page. The resulting high and low values are stored in the WalkPage given back to PartitionWalk, which uses them for watermark and window tracking.

*Call graph*: called by 1 (_repo_pages).


### `extensions/sources/ufo_ext_sources/jira.py`

`io_transport` · `during source sync when Jira streams are fetched and rendered`

Jira data lives behind Atlassian’s web API, and one user authorization may cover several separate Jira sites. This file is the bridge between that outside service and the project’s source-sync system. First it asks Atlassian which Jira sites are available. Then, for each requested stream, it visits the right Jira API paths, follows Jira’s page-by-page results, and adds helpful context such as the site ID and site URL to each record.

The connector is careful about incremental syncing. For issues, comments, and sprints, it can use a saved timestamp cursor so later runs only bring back records updated after the last sync. That is like checking a mailbox only for letters newer than the last one you opened. If Jira says the authorization cannot access a site or resource, the connector marks that stream as skipped instead of crashing the whole run.

The file also improves how Jira records become readable text. Jira issue descriptions and comments may be stored as Atlassian Document Format, a nested document tree rather than plain text. The helper `_doc_text` walks that tree and extracts the human words. For issues, `render` builds a page with the summary, status, priority, assignee, reporter, and description, instead of exposing raw JSON.

#### Function details

##### `JiraConnector.paginate`  (lines 63–94)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct Jira-reading routine for the stream being synced. It is the main dispatcher that turns a stream name like `issues` or `users` into pages of records from Jira.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks the stream name, calls the matching private reader, and yields each page of records it gets back. If Jira refuses access with a 401 or 403 status, it turns that into a skipped stream; other errors are allowed to continue upward as real failures.

**Call relations**: The sync framework calls this when it wants records for a Jira stream. `paginate` then hands the work to `_projects`, `_issues`, `_comments`, `_users`, `_boards`, or `_sprints`. If the stream is unknown or access is refused, it raises `StreamSkipped` so the larger sync can record a skip rather than treating it as a broken connector.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 96–100)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds every Atlassian Cloud site the current authorization can access. Jira API calls need a site-specific `cloud_id`, so this is the starting point for most streams.

**Data flow**: It receives the HTTP client and asks Atlassian for accessible resources. It reads the JSON response, safely turns it into a list, and returns that list of site records. Empty or missing response content becomes an empty list.

**Call relations**: `_projects`, `_issues`, `_users`, and `_boards` call this before making site-scoped Jira requests. Those streams then loop over the returned sites and use each site’s ID to build the proper Jira API path.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 102–122)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a Jira list endpoint that returns results in pages. It hides the repeated work of asking for page 1, page 2, and so on until Jira says there is no more data.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, and the JSON key where records are stored. It repeatedly sends requests with `startAt` and `maxResults`, extracts the records from the response, yields non-empty batches, and stops when Jira reports the last page, when no records arrive, or when the total count has been reached.

**Call relations**: Many stream readers use this shared paging helper: `_projects`, `_issues`, `_comments`, `_boards`, and `_sprints`. It calls `records_at` to pull the list of records out of Jira’s response envelope, then gives each page back to the stream-specific reader.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 124–131)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Jira projects from every accessible site. Projects are the high-level work containers in Jira, so they are one of the core things the sync can remember.

**Data flow**: It asks `_sites` for reachable Jira sites. For each site with a valid ID, it builds the project search API path, reads all pages through `_offset_values`, adds site context to each project record, and yields those enriched pages.

**Call relations**: `paginate` calls this when the `projects` stream is requested. `_projects` depends on `_sites` to discover where to look, `_offset_values` to page through Jira’s project results, and `with_context` to attach the cloud ID and site URL before handing records back.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 133–145)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Jira issues, optionally only those updated after a saved cursor. This is the main path for bringing task and ticket content into the system.

**Data flow**: It receives the HTTP client and an optional cursor timestamp. It builds a Jira Query Language filter, called JQL, that orders issues by update time and, when a cursor exists, asks only for newer issues. For each accessible site, it reads issue pages, requests a focused set of fields, adds site context, and yields the results.

**Call relations**: `paginate` calls this for the `issues` stream, and `_comments` also calls it to find the issues whose comments should be checked. It uses `_sites` for site discovery, `_offset_values` for Jira pagination, and `with_context` so later steps know which site each issue came from.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 147–167)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches comments attached to Jira issues. Comments are not fetched alone; the connector first finds issues, then asks Jira for the comments on each issue.

**Data flow**: It receives the HTTP client and an optional cursor timestamp. It loops through all issues from `_issues` without limiting by issue cursor, uses each issue’s ID and cloud ID to request its comments, filters comments by the comment update cursor when present, adds issue and site context, and yields comment batches that still have records after filtering.

**Call relations**: `paginate` calls this for the `issue_comments` stream. `_comments` calls `_issues` to know which issue pages to inspect, then calls `_offset_values` to read each issue’s comment pages. It uses `with_context` to attach cloud ID, site URL, issue ID, and issue key before returning comments to the sync.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 169–180)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Jira users from every accessible site. Unlike most Jira lists in this file, this endpoint returns a plain array rather than a wrapped page object.

**Data flow**: It asks `_sites` for accessible sites. For each site with a valid ID, it calls the users search endpoint once with a start and maximum size, reads the JSON array safely, adds site context if users were returned, and yields that batch.

**Call relations**: `paginate` calls this for the `users` stream. It uses `_sites` to find each Jira site, `list_or_empty` to safely treat the response as a list, and `with_context` to mark which site the users came from.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 182–189)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Jira Agile boards from every accessible site. Boards are used by Jira’s agile features and are also needed before the connector can fetch sprints.

**Data flow**: It asks `_sites` for reachable sites. For each valid cloud ID, it builds the Agile board API path, reads all board pages through `_offset_values`, adds cloud ID and site URL context, and yields each page.

**Call relations**: `paginate` calls this for the `boards` stream, and `_sprints` calls it as the first step in finding sprints. It relies on `_sites` for site discovery, `_offset_values` for page-by-page API reading, and `with_context` to carry site information forward.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 191–205)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches sprints from Jira Agile boards, optionally only those updated after a saved cursor. A sprint belongs to a board, so the connector must discover boards first.

**Data flow**: It receives the HTTP client and optional cursor timestamp. It loops through boards from `_boards`, builds the sprint API path for each board, reads sprint pages, filters by `updatedDate` when a cursor is present, adds cloud and board context, and yields non-empty sprint batches.

**Call relations**: `paginate` calls this for the `sprints` stream. `_sprints` calls `_boards` to know which boards to inspect and `_offset_values` to page through each board’s sprints, then returns the enriched sprint records to the sync process.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 207–214)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Makes Jira issue records easier for the sync engine to track by copying the issue update time to the top level of the record. Other stream records already have their key fields in the expected place.

**Data flow**: It receives one record and the stream description. If the stream is `issues`, it safely reads the nested `fields` object and returns a copy of the record with a top-level `updated` value. For any other stream, it returns the record unchanged.

**Call relations**: The source framework uses this before storing or advancing cursors. `flatten` calls `_dict_or_empty` so malformed or missing `fields` data does not cause an error while preparing issue records.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 216–242)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns selected Jira records into readable page text. It gives issues and comments a human-friendly title and body instead of leaving them as raw API JSON.

**Data flow**: It receives a record and its stream description. For issues, it pulls the summary, status, priority, assignee, reporter, and description text, then builds a Markdown-like page. For issue comments, it uses the author as the title and extracts the comment body text. For other streams, it delegates to the base connector’s default rendering.

**Call relations**: The sync system calls this when it needs searchable text for a record. `render` uses `_dict_or_empty`, `_str`, `_person`, `_field_line`, and `_doc_text` to safely extract readable parts from Jira’s nested data before handing back a title and body.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 245–246)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is actually a string. This keeps rendering code from accidentally showing objects, numbers, or missing values as text.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string.

**Call relations**: `JiraConnector.render` uses this while building issue text, and `_person` uses it when choosing a display name or email address. It is a small guardrail around Jira data that may not always have the expected shape.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 249–250)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. This lets the connector read nested Jira objects without crashing when a field is missing or has an unexpected type.

**Data flow**: It receives any value. If the value is a dictionary, it returns it; otherwise it returns an empty dictionary.

**Call relations**: `flatten`, `render`, and `_person` use this before reading nested fields. It acts like checking that a box really contains folders before trying to open one.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 253–255)

```
def _person(value: Any) -> str
```

**Purpose**: Extracts a readable person name from a Jira user object. It prefers the display name and falls back to the email address.

**Data flow**: It receives a possible Jira person record. It safely treats it as a dictionary, reads `displayName` and `emailAddress`, keeps only real strings, and returns the first useful value or an empty string.

**Call relations**: `JiraConnector.render` calls this when showing an issue assignee, issue reporter, or comment author. `_person` relies on `_dict_or_empty` and `_str` to avoid errors from incomplete Jira user data.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 258–259)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: Formats one labeled line of issue metadata, but only when there is a real value to show. This avoids blank lines like `Priority:` with nothing after them.

**Data flow**: It receives a label such as `Status` and a text value. If the value is non-empty, it returns `Label: value`; otherwise it returns an empty string.

**Call relations**: `JiraConnector.render` calls this while building the metadata block for an issue. The rendered issue page then includes only the fields that Jira actually supplied.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 262–279)

```
def _doc_text(value: Any) -> str
```

**Purpose**: Extracts plain readable text from Atlassian Document Format, Jira’s nested structure for rich text descriptions and comments. It ignores formatting and keeps the words.

**Data flow**: It receives any value that may be a document tree. It creates an empty list of text chunks, walks through dictionaries and lists looking for string `text` leaves, joins the collected pieces with newlines, trims extra space, and returns the result.

**Call relations**: `JiraConnector.render` calls this for issue descriptions and comment bodies. Inside `_doc_text`, the nested `walk` helper does the actual tree traversal, so callers do not need to know the shape of Atlassian’s rich text format.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 267–276)

```
def walk(node: Any) -> None
```

**Purpose**: Recursively visits each part of an Atlassian Document Format tree and collects text leaves. It is the worker inside `_doc_text` that turns nested rich text into simple lines.

**Data flow**: It receives one node from the document tree. If the node is a dictionary, it saves its string `text` value if present and then visits each child in `content`; if the node is a list, it visits each item. It does not return a value directly; instead, it adds found text to the surrounding `chunks` list.

**Call relations**: `_doc_text` defines and uses this helper during rendering. It stays private inside `_doc_text` because its only job is to support that one text-extraction pass.


### `extensions/sources/ufo_ext_sources/linear.py`

`io_transport` · `during source sync, while fetching and rendering Linear records`

Linear is a project and issue tracking tool, and this connector is the read-only bridge from Linear into the larger system. Without it, the system would not know which Linear objects exist, how to ask Linear for them, how to continue through multiple pages of results, or how to turn key records into readable text.

The file first defines the list of Linear streams, which are categories of data such as issues, projects, comments, users, cycles, labels, and statuses. Each stream says whether it can use an update cursor. A cursor is like a bookmark: after one sync, the next sync can ask Linear only for records updated since that saved point. Some Linear collections do not support that kind of filter, so those are read fully each time.

Most of the file is a catalog of GraphQL queries. GraphQL is an API style where the client sends one structured query describing exactly which fields it wants back. The `LinearConnector` sends these queries to Linear’s `/graphql` endpoint, follows Linear’s page-by-page response markers, and yields batches of records. If Linear refuses access because the token is invalid or lacks permission, the stream is marked as skipped instead of pretending it succeeded. If Linear reports GraphQL errors, the connector stops loudly so it does not save partial or misleading data.

The file also customizes how important records are displayed. For issues, projects, comments, and users, it creates human-readable text instead of leaving users with raw nested API data.

#### Function details

##### `_stream`  (lines 32–35)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a small description of one Linear data stream, such as `issues` or `projects`. This keeps the stream list compact and consistent, including whether a stream has an update-time bookmark and whether it is considered core content.

**Data flow**: It receives a stream name, an optional cursor field, and a flag saying whether the stream is canonical. It packages those values into a `StreamSpec`, which is the standard object the sync framework uses to know what to read.

**Call relations**: This helper is used while building the file’s Linear stream catalog. It hands each finished stream description to the connector framework, which later passes those descriptions into `LinearConnector.paginate` and `LinearConnector.render` during a sync.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 262–305)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Linear stream from the GraphQL API, one page at a time. It is the main fetch loop that turns Linear’s paginated API responses into batches of records for the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous run. It chooses the right GraphQL query, adds an `updatedAt` filter when Linear supports incremental reads, sends requests to `/graphql`, checks for permission failures or GraphQL errors, extracts the `nodes` records, and yields each non-empty page. It keeps following Linear’s `endCursor` until Linear says there are no more pages.

**Call relations**: The sync framework calls this when it needs records for a Linear stream. Inside the loop, it relies on the connector base class’s POST behavior for the actual network request, uses `list_or_empty` to safely normalize the returned record list, and raises `StreamSkipped` when Linear refuses access so the broader run can record that stream as skipped rather than crashed.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 307–346)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns selected Linear records into readable text that looks more like a useful note than a raw API dump. This matters for recall and search, because an issue title, state, priority, assignee, or project description is easier to understand in prose.

**Data flow**: It receives one Linear record and the stream it came from. For issues, projects, comments, and users, it pulls out the most useful fields, cleans them into strings, formats small labeled sections, and returns both a title and a full text body. For other stream types, it lets the parent connector use the default rendering.

**Call relations**: After records have been fetched by `LinearConnector.paginate`, the sync framework can call this to prepare content for storage or display. It uses `_str`, `_ref_id`, and `_labeled` as small formatting helpers so missing or nested fields do not produce messy output.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 349–350)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns a value into a string only when it is already a string. It prevents missing fields, numbers, dictionaries, or other unexpected values from leaking into rendered text.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string. It does not change anything outside itself.

**Call relations**: `LinearConnector.render` uses this whenever it pulls plain text fields from a Linear record. `_ref_id` also uses it after extracting an `id` from a nested reference object.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 353–354)

```
def _ref_id(value: Any) -> str
```

**Purpose**: Extracts the `id` from a small nested reference object, such as an assignee or lead. Linear often represents linked objects as dictionaries containing only an `id`, and this helper makes that safe to read.

**Data flow**: It receives any value. If the value is a dictionary, it looks for its `id` field and passes that through `_str`; if not, it returns an empty string. The result is a clean ID string or nothing.

**Call relations**: `LinearConnector.render` calls this when it wants to show linked people or objects without dumping the whole nested structure. It delegates the final string check to `_str`.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 357–358)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: Formats a short list of label-and-value pairs into readable lines, skipping empty values. It is used to create compact metadata blocks such as `state: open` or `email: person@example.com`.

**Data flow**: It receives a list of pairs, where each pair has a label and a value. It keeps only pairs with a non-empty value, turns each into `label: value`, joins them with line breaks, and returns the resulting text.

**Call relations**: `LinearConnector.render` uses this to build the metadata sections for issues, projects, and users. It keeps the rendered pages clean by leaving out blank fields instead of showing empty labels.

*Call graph*: called by 1 (render).


### Boards and operational work
Connectors for board-based work management, incident response, error tracking, and enterprise task records.

### `extensions/sources/ufo_ext_sources/monday.py`

`io_transport` · `data sync`

monday.com exposes its data through GraphQL, which is a query language where the client asks for exactly the fields it wants. This connector is the project’s read-only bridge to that API. Without it, the system would not know how to fetch monday content, page through large result sets, or shape monday’s nested data into the simpler form used elsewhere.

The file defines a set of streams, one for each kind of monday data the system can sync. A stream is like a labeled conveyor belt: “boards” brings board records, “items” brings item records, and so on. The connector sends GraphQL queries to monday, checks for monday’s error format, and treats refused or unavailable queries as skipped streams rather than pretending a partial sync succeeded.

Some monday data needs special care. Top-level lists use numbered pages. Board items use a separate cursor, which is an opaque bookmark returned by monday for the next page. Activity logs must be fetched board by board. monday also does not offer a true “only changed since this time” query here, so the connector fetches pages and filters them locally using stored timestamp cursors. Finally, `flatten` reshapes records into common fields such as name, body, author, and parent link so later parts of the system can work with monday data without knowing monday’s exact API shape.

#### Function details

##### `_extract_person_ids`  (lines 66–94)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: This helper pulls assigned person IDs out of monday item column data. monday stores people assignments inside board-specific columns as JSON, so this function looks for columns marked as people columns and extracts only actual people, not teams.

**Data flow**: It receives the raw `column_values` field from a monday item. It checks that the value is a list, looks through each people-type column, parses the column’s stored JSON when needed, and collects every entry whose kind is `person`. It returns a simple list of person ID strings; bad shapes, missing values, or invalid JSON are quietly ignored.

**Call relations**: When `MondayConnector._items` fetches item records, it calls this helper for each item’s column values. The extracted IDs are added back onto the item as `assignee_ids`, giving later parts of the sync a stable way to see who is assigned without understanding monday’s board-specific column layout.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 102–114)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This is the connector’s common doorway to monday’s GraphQL API. It sends one query, unwraps the useful `data` part of the response, and converts monday-reported GraphQL errors into a skipped stream signal.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional variables. It posts those to monday’s API endpoint, reads the returned body, and checks whether monday included an `errors` list. If there are errors, it raises `StreamSkipped`; otherwise it returns the response’s `data` object, or an empty dictionary if the response is not shaped as expected.

**Call relations**: All monday fetch paths rely on this function rather than posting directly. Paging helpers, item loading, activity log loading, and the main `paginate` dispatcher call it whenever they need data from monday. By centralizing error handling here, the rest of the file can focus on what records to ask for.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, paginate).


##### `MondayConnector._paged_root`  (lines 116–139)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads monday collections that use simple numbered pages, such as users, workspaces, boards, and updates. It keeps requesting the next page until monday returns no more records.

**Data flow**: It receives the API client, the GraphQL field to query, the fields to select, and optionally a saved cursor timestamp. It starts at page 1, asks monday for up to 100 records, normalizes the answer into a list, and filters out records that are not newer than the cursor when a cursor field is supplied. It yields each non-empty page of records and stops when a page produces no records after filtering.

**Call relations**: `MondayConnector._boards` uses this to gather boards, and `MondayConnector.paginate` uses it directly for streams with straightforward top-level pagination. It depends on `_graphql` for the actual API call and on `list_or_empty` to safely treat missing or malformed API fields as an empty list.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, paginate); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 141–152)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function fetches all monday boards with the board details needed by other streams. It exists because items and activity logs are not fetched globally; they must be found by first knowing which boards exist.

**Data flow**: It receives the HTTP client, asks `_paged_root` for every page of boards, and appends all returned board records into one list. It returns that complete list of boards to the caller.

**Call relations**: This is a support step for board-based streams. `MondayConnector._items` calls it before walking through each board’s items, and `MondayConnector._activity_logs` calls it before asking each board for its activity logs. It hands off the actual page-by-page GraphQL work to `_paged_root`.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 154–213)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches monday items from every board. It also adds a plain `assignee_ids` list to each item so assignments are easier for the rest of the system to use.

**Data flow**: It receives the HTTP client and an optional saved update-time cursor. First it gets all boards. For each board, it requests the first page of items, then follows monday’s item-page cursor to request later pages. Each page is cleaned into a list, each item has person assignment IDs extracted from its column values, and older items are filtered out if a cursor was supplied. It yields each non-empty page of item records.

**Call relations**: `MondayConnector.paginate` calls this when the active stream is `items`. Internally it calls `_boards` to know where to look, `_graphql` to ask monday for item pages, `_extract_person_ids` to simplify assignee data, and safe dictionary/list helpers to protect against missing API fields.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (paginate); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 215–243)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches activity log entries for every monday board. It attaches the board ID to each log entry so the log can later be linked back to the board it came from.

**Data flow**: It receives the HTTP client and an optional saved creation-time cursor. It first gets the list of boards, then asks monday for up to 100 activity log records for each board. It gathers those logs, adds `board_id` to each one, filters out records that are not newer than the cursor when needed, and yields each non-empty batch.

**Call relations**: `MondayConnector.paginate` calls this for the `activity_logs` stream. The function uses `_boards` to decide which boards to query, `_graphql` to fetch the logs from monday, and list normalization to avoid crashing when monday omits or reshapes fields.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (paginate); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 245–319)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading monday streams. Given a stream name, it chooses the right GraphQL query or helper and yields records in pages for the sync runner.

**Data flow**: It receives the HTTP client, a stream description, and an optional saved cursor. It compares the stream name against the supported monday streams, runs the matching fetch path, and yields pages of records. For some streams it uses the common numbered-page helper; for items and activity logs it delegates to their board-aware helpers; for teams and tags it makes one direct GraphQL query. If a stream is unknown, or if monday refuses access with an authorization-related HTTP status, it raises `StreamSkipped` so the run records that the stream could not be read.

**Call relations**: The broader connector framework calls this function when it is time to sync a particular monday stream. `paginate` then coordinates the file’s lower-level pieces: `_paged_root` for simple paged lists, `_items` for board item pages, `_activity_logs` for per-board logs, and `_graphql` for direct one-shot queries.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _graphql, _items, _paged_root).


##### `MondayConnector.flatten`  (lines 321–362)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes raw monday records into a more standard form used by the rest of the system. It keeps the original fields but adds or normalizes common fields such as title-like names, body text, author, timestamps, API URL, and parent record IDs.

**Data flow**: It receives one record and the stream it came from. Depending on the stream name, it copies the record and fills in common fields from monday-specific fields: users keep name and email, boards and workspaces get an API URL, items get status, updates get body and author, and activity logs get subject, body, author, and parent board ID. If the stream has no special rule, the original record is returned unchanged.

**Call relations**: After `paginate` has produced raw records, the connector framework can call `flatten` to make those records easier to index or display. For update author data, it uses `dict_or_empty` so a missing creator object does not break the transformation.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/pagerduty.py`

`io_transport` · `during source sync`

PagerDuty is an external service, so the system cannot read all its data from a local database. This connector is the bridge: it knows which PagerDuty endpoints exist, how to ask for each page of results, and how to continue from a previous sync without rereading everything.

The file defines several streams, such as users, incidents, and on-calls. A stream is a named kind of data with a main ID field, and sometimes a cursor field, which is a timestamp used like a bookmark. Incidents use `updated_at` as that bookmark, while incident notes use `created_at`.

Most PagerDuty endpoints use offset-based pagination, meaning the connector asks for records in chunks and moves forward by an offset until PagerDuty says there are no more pages. Incidents get special treatment: they are requested in oldest-updated-first order and can include a `since` value so the sync starts near the last saved cursor. Incident notes are more nested: the connector first reads incidents, then asks PagerDuty for notes for each incident.

If PagerDuty refuses access with a 401 or 403 status, the connector marks that stream as skipped instead of treating the whole run as broken. This matters because one token may have access to some PagerDuty data but not all of it.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 72–75)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to PagerDuty and adds PagerDuty’s required `Accept` header. That header tells PagerDuty which version of its API format the connector expects.

**Data flow**: It receives a base URL and a credential object. It asks the parent REST connector to build the basic HTTP client, then adds the PagerDuty-specific media type header. It returns the prepared client, ready to make API requests.

**Call relations**: This is part of the connector setup path inherited from the REST connector. Before any stream can be paged through, the system needs this client so later methods can make correctly formatted PagerDuty requests.


##### `PagerDutyConnector._offset_pages`  (lines 77–97)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one PagerDuty stream that uses ordinary offset-and-limit pagination. It also optionally filters out records that are not newer than the saved cursor, so repeat syncs avoid old data.

**Data flow**: It receives an HTTP client, a stream description, optional request parameters, and an optional cursor timestamp. It asks the shared REST pagination helper for pages from the stream’s endpoint, using PagerDuty’s `more` and `limit` fields to know how to continue. If a cursor is present and the stream has a cursor field, it keeps only records newer than that cursor, then yields non-empty pages.

**Call relations**: This is the common paging worker for most PagerDuty streams. `PagerDutyConnector._incidents` calls it after adding incident-specific sorting and cursor parameters, and `PagerDutyConnector.paginate` calls it directly for streams like users, teams, services, escalation policies, schedules, and on-calls.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 99–111)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads PagerDuty incidents in a safe order for incremental syncing. It asks PagerDuty to sort incidents by `updated_at` from oldest to newest, so the saved bookmark can move forward cleanly.

**Data flow**: It receives an HTTP client and an optional cursor. It builds request parameters with `sort_by` set to `updated_at:asc`; if a cursor exists, it also sends it as PagerDuty’s `since` parameter. It then delegates the actual page-by-page fetching to `_offset_pages` and yields each page of incidents.

**Call relations**: This is the incident-specific layer above the generic offset pager. `PagerDutyConnector.paginate` uses it when the requested stream is incidents, and `PagerDutyConnector._incident_notes` uses it to discover which incidents need note lookups.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 113–126)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads notes attached to incidents, which cannot be fetched as one simple top-level stream. It first finds incidents, then asks PagerDuty for the notes belonging to each incident.

**Data flow**: It receives an HTTP client and an optional cursor for note creation time. It reads all incidents through `_incidents` without an incident cursor, takes each valid incident ID, and requests `/incidents/{id}/notes`. It extracts the `notes` list from the response, filters notes newer than the cursor if needed, adds the incident ID as context to each note, and yields note pages.

**Call relations**: This function sits between incident discovery and note syncing. `PagerDutyConnector.paginate` calls it for the `incident_notes` stream; inside, it calls `_incidents` to get incident IDs, then uses `records_at` to pull notes from the response and `with_context` to attach the parent incident ID.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 128–162)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading any PagerDuty stream. Given a stream name, it chooses the right paging method and yields records page by page.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If the stream is incidents, it sends the work to `_incidents`; if it is incident notes, it sends the work to `_incident_notes`; for the other supported streams, it uses `_offset_pages`. If the stream is unknown, it reports it as skipped. If PagerDuty returns 401 or 403, it converts that refusal into a skipped stream with a clear message; other HTTP errors are allowed to fail normally.

**Call relations**: The sync runner calls this when it wants records for a specific PagerDuty stream. This function then hands off to `_incidents`, `_incident_notes`, or `_offset_pages` depending on the stream, and uses `StreamSkipped` when the connector should record a graceful skip instead of a hard failure.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/sentry.py`

`io_transport` · `source sync runs`

Sentry stores useful project history behind a web API, and it sends large results back in pages instead of all at once. This file is the Sentry “source connector”: it knows which Sentry objects can be read, which API addresses to call, how to follow Sentry’s next-page marker, and how to add missing context such as the organization or project a record came from.

The connector is read-only. It does not create or update anything in Sentry. When the sync runner asks for a stream, `paginate` acts like a dispatcher: organizations, projects, members, issues, events, and releases each take a slightly different route. Some data is global to the account, such as organizations and projects. Other data must be fetched by first walking that tree: members and releases are fetched per organization, while issues and events are fetched per project.

Sentry uses a `Link` response header to say whether there is another page. `_sentry_next_cursor` extracts that cursor, and `_paged_list` keeps requesting pages until no cursor remains. For incremental syncs, cursor values are used to ask Sentry for only newer issues or events where possible, or to filter results after fetching for projects and releases. If Sentry refuses access with a 401 or 403 response, the connector skips that stream instead of crashing the whole sync, because the user’s token may simply lack permission.

#### Function details

##### `_sentry_next_cursor`  (lines 48–53)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper reads Sentry’s pagination header and finds the cursor for the next page, if there is one. A cursor is like a bookmark that tells the API where to continue reading.

**Data flow**: It receives HTTP response headers → looks for a `link` or `Link` header → searches that text for Sentry’s “next page with results” cursor → returns the cursor string, or `None` if there is no next page.

**Call relations**: `SentryConnector._paged_list` calls this after every API response. If it returns a cursor, `_paged_list` keeps going; if it returns nothing, the page-reading loop stops.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.paginate`  (lines 61–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry point for reading one Sentry stream. Given a stream name, it chooses the right Sentry-reading method and yields records in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync → checks which Sentry stream was requested → calls the matching helper, sometimes applying cursor-based filtering → yields lists of records. If Sentry denies access with a 401 or 403 response, it turns that into a skipped stream message rather than letting the whole sync fail.

**Call relations**: The wider source-sync framework calls `paginate` when it wants records from Sentry. `paginate` then hands work to `_organizations`, `_projects`, `_members`, `_issues`, `_events`, or `_releases`. If the requested stream is not implemented, it raises `StreamSkipped` to tell the framework this stream should not be read.

*Call graph*: calls 7 internal fn (__init__, _events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._paged_list`  (lines 102–119)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads a Sentry API endpoint page by page. It hides the repeated work of sending requests, decoding JSON, and following Sentry’s next-page cursor.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters → requests the path, adding a cursor parameter when continuing from a previous page → converts a JSON list response into records and yields them → reads the next-page cursor from the response headers → repeats until there is no cursor left.

**Call relations**: All stream-specific helpers use `_paged_list` as their common page reader. It calls `_sentry_next_cursor` to decide whether another request is needed, then returns batches upward to organization, project, member, issue, event, or release readers.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 121–125)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This reads all Sentry organizations visible to the credential. Organizations are the top-level containers that many other Sentry objects belong to.

**Data flow**: It receives an HTTP client → asks `_paged_list` to read `/organizations/` across all pages → gathers those page batches into one list → returns the full list of organization records.

**Call relations**: `paginate` calls this when the organizations stream is requested. `_members` and `_releases` also call it first, because they need each organization slug before they can ask Sentry for members or releases inside that organization.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, paginate).


##### `SentryConnector._projects`  (lines 127–131)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This reads all Sentry projects visible to the credential. Projects are needed both as their own stream and as the starting point for project-specific issues and events.

**Data flow**: It receives an HTTP client → asks `_paged_list` to read `/projects/` across all pages → combines all returned pages into one list → returns the full list of project records.

**Call relations**: `paginate` calls this for the projects stream. `_issues` and `_events` call it first so they can visit each project and then fetch that project’s issue or event data.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, paginate).


##### `SentryConnector._members`  (lines 133–139)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads organization members from Sentry. Because Sentry exposes members under each organization, it first finds the organizations and then reads members for each one.

**Data flow**: It receives an HTTP client → gets all organizations from `_organizations` → for each organization with a valid slug, reads `/organizations/{slug}/members/` through `_paged_list` → adds the organization slug to each member record → yields member pages with that context attached.

**Call relations**: `paginate` calls `_members` when the members stream is requested. `_members` depends on `_organizations` to know where to look, uses `_paged_list` for the actual API paging, and uses `with_context` so downstream code can tell which organization each member came from.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 141–155)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads issues for every accessible Sentry project. Issues are fetched project by project, and a cursor can limit the request to issues seen after the last sync point.

**Data flow**: It receives an HTTP client and an optional cursor → gets all projects from `_projects` → extracts each project’s organization slug and project slug → skips projects missing that identifying information → builds a Sentry query like `lastSeen:>{cursor}` when a cursor is present → reads the project’s issues through `_paged_list` → adds organization and project slugs to each issue record → yields the enriched issue pages.

**Call relations**: `paginate` calls `_issues` for the issues stream. `_issues` first asks `_projects` for the project list, then uses `_paged_list` to read each project’s issue endpoint, and finally uses `with_context` so the synced records keep their place in the Sentry organization/project tree.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 157–171)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads event records for every accessible Sentry project. Events are fetched per project, with an optional cursor query to request only events newer than the previous sync point.

**Data flow**: It receives an HTTP client and an optional cursor → gets all projects from `_projects` → finds each project’s organization slug and project slug → skips projects that do not provide those values as strings → builds a Sentry query like `event.timestamp:>{cursor}` when possible → reads the project’s events through `_paged_list` → adds organization and project slugs to the returned records → yields those event pages.

**Call relations**: `paginate` calls `_events` for the events stream. Like `_issues`, it uses `_projects` to discover where to fetch from, `_paged_list` to follow Sentry’s paginated API, and `with_context` to preserve which project produced each event.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 173–184)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads release records for every accessible Sentry organization. Releases are attached to organizations, so the connector visits each organization and gathers its releases.

**Data flow**: It receives an HTTP client and an optional cursor → gets all organizations from `_organizations` → for each organization with a valid slug, reads `/organizations/{slug}/releases/` through `_paged_list` → if a cursor is present, keeps only releases whose `dateCreated` value is later than the cursor → adds the organization slug to the remaining records → yields non-empty release pages.

**Call relations**: `paginate` calls `_releases` for the releases stream. `_releases` relies on `_organizations` for the list of organization slugs, uses `_paged_list` to read Sentry’s pages, and uses `with_context` so later stages know which organization each release belongs to.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).


### `extensions/sources/ufo_ext_sources/wrike.py`

`io_transport` · `during Wrike source sync`

Wrike’s API returns lists of things, such as tasks or folders, in pages. This connector is the part of the system that knows which Wrike lists are available, how to ask Wrike for the next page, and how to shape the raw Wrike answers into friendlier records. Without it, the wider sync system would not know where Wrike data lives, how to keep paging through it, or how to extract useful fields like a task name or a contact email.

The file first defines the Wrike streams: the named collections that can be synced. Some have an “updatedDate” cursor, meaning the sync can remember the newest record it saw and skip older records next time. Wrike does not provide a dependable server-side “only send changes since this time” filter, so this connector fetches pages and then filters old records locally. Think of it like receiving a stack of mail and throwing away envelopes you already read.

The main class, WrikeConnector, inherits shared REST API behavior from the source SDK. Its paginate method walks through Wrike’s paged responses using Wrike’s nextPageToken. If Wrike refuses access with a permission or authentication error, it marks that stream as skipped instead of crashing the whole idea of syncing other streams. Its flatten method then normalizes selected records, adding common fields such as name, email, body, due date, and created_at.

#### Function details

##### `_profile_email`  (lines 43–53)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper looks inside a Wrike contact record and finds the first usable email address in its profiles list. It exists because Wrike nests email information instead of putting it directly at the top level of the contact.

**Data flow**: It receives one Wrike record as a dictionary. It checks whether the record has a profiles value that is actually a list, then scans each profile that is a dictionary and returns the first non-empty string stored under email. If the structure is missing, malformed, or has no email, it returns None.

**Call relations**: WrikeConnector.flatten calls this when it is preparing contact records. The helper gives flatten a clean email value so the final contact page can expose email in a simple top-level field.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 61–85)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This async method fetches Wrike records page by page for one stream, such as tasks or folders. It also skips streams that are not implemented and turns permission failures into a clear “stream skipped” result.

**Data flow**: It receives an HTTP client, a stream description, and an optional stored cursor from a previous sync. It asks Wrike for pages under the stream’s API path, follows Wrike’s nextPageToken to continue, and reads records from the data field. If a cursor is present and the stream has a cursor field, it removes records whose updatedDate is not newer than that cursor. It yields each non-empty batch of remaining records. If Wrike returns 401 or 403, meaning unauthorized or forbidden, it raises StreamSkipped with an explanation; other HTTP errors are passed upward unchanged.

**Call relations**: The sync framework calls this method when it needs records for a Wrike stream. Inside, it relies on the inherited cursor-page fetching behavior from the REST connector. When a stream cannot or should not be read, it creates a StreamSkipped signal so the larger sync can treat that stream as unavailable rather than as successfully read.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 87–125)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method turns raw Wrike records into records with common, easy-to-use fields. It keeps the original data but adds clearer fields such as name, email, body, status, due_date, created_at, and parent_external_id where appropriate.

**Data flow**: It receives a raw Wrike record and the stream it came from. For contacts, it builds a display name from first and last name, falls back to other identifiers if needed, pulls out an email with _profile_email, and copies the created date. For folders, it maps title to name and builds an API URL. For tasks, it reads nested dates safely with dict_or_empty, maps title to name, chooses a status, and finds a due date. For comments, it maps text to body, authorId to author, and taskId to the parent item. For streams without special shaping, it returns the record unchanged.

**Call relations**: The broader connector flow uses this after records have been fetched by pagination. It calls _profile_email for contacts and dict_or_empty when reading task date details, then hands back a normalized record that later parts of the source system can index or display more consistently.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).
