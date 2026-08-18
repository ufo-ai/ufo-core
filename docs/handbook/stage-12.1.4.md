# Engineering, issue tracking, and incident source connectors  `stage-12.1.4`

This stage is the system’s set of “source connectors” for engineering work. A connector is a small translator that logs in to an outside service, reads its data through that service’s API, and turns it into the common record format the rest of the system can store, sync, and search. It is mostly behind-the-scenes support for the main syncing work.

The GitHub connector discovers accessible organizations and repositories, then reads code-related activity such as issues, commits, comments, users, teams, and releases. The Jira connector reads Jira Cloud sites, including projects, issues, comments, users, boards, and sprints. The Linear connector reads similar planning and tracking data from Linear, using its GraphQL API, which is a structured way to ask for exactly the needed fields. The PagerDuty connector brings in incident-response data such as services, incidents, notes, schedules, and on-call records. The Sentry connector reads error-monitoring information such as projects, issues, events, members, and releases. Together, these connectors let the system build one searchable memory of engineering work across many tools.

## Files in this stage

### Engineering work tracking
Connectors for developer and project-tracking platforms that sync repositories, issues, projects, comments, users, teams, and related engineering records.

### `extensions/sources/ufo_ext_sources/github.py`

`io_transport` · `source sync`

GitHub exposes data through many web API endpoints, and most useful data is split by organization or repository. This file is the map and walking logic for that world. It defines the list of GitHub streams the system knows about, such as issues, commits, pull requests, stargazers, and workflows, then connects the runnable ones to real GitHub API paths.

The connector first discovers the organizations available to the granted credential. From those organizations it finds repositories, skipping archived repositories and forks. It then “fans out” repository-level streams, meaning it reads the same kind of data once per repository, like checking every aisle in a warehouse instead of only the front desk.

A key detail is partition stamping. Many GitHub values are only unique inside one repository, such as a branch named `main` or a tag name. The connector adds the repository or organization name to records before they are stored, so two repositories do not accidentally overwrite each other’s pages.

It also knows how to resume long syncs. Some streams move oldest-to-newest using GitHub’s `since` filter. Others, like commits and events, are newest-first feeds, so the connector tracks time windows carefully to avoid missing new records that appear while a sync is running. GitHub does not provide delete notices here, so this connector is read-only and focuses on fetching, shaping, and safely paging through records.

#### Function details

##### `_stream`  (lines 73–93)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', ordering: Ordering=Ordering.none, canonical:
```

**Purpose**: Creates a small description of one GitHub data stream, such as which field identifies each record and which timestamp can be used for resuming syncs. It keeps the long stream catalog readable by avoiding repeated setup code.

**Data flow**: It receives stream settings such as a name, primary key, cursor field, ordering style, and backfill window. It fills in defaults where needed, then returns a `StreamSpec`, which is the project’s standard description of a readable source stream.

**Call relations**: This helper is used while building the file-level stream list. It hands its settings to `StreamSpec`, so later connector methods can decide which API path to call and how to resume reading each stream.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 197–200)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns only the GitHub streams that this connector can actually fetch today. Some streams are listed for catalog compatibility but do not yet have the extra parent-by-parent walking code they would need.

**Data flow**: It reads the connector’s full stream list and the path table in this file. It filters out any stream whose name has no configured GitHub API path, then returns the runnable subset.

**Call relations**: The wider sync system asks this method what streams are available. This method does not fetch data itself; it decides which stream definitions may be passed into the paging flow.


##### `GitHubConnector._make_client`  (lines 202–206)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to GitHub and adds the GitHub-specific headers required for the API version and response format. These headers tell GitHub which API contract and media types the connector expects.

**Data flow**: It receives a base URL and credential, asks the parent connector to create the authenticated HTTP client, then adds GitHub `Accept` and API version headers. It returns the ready-to-use client.

**Call relations**: This is part of the setup path before any GitHub request is made. The inherited connector supplies the base authenticated client, and this method specializes it for GitHub.


##### `GitHubConnector.flatten`  (lines 208–248)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Prepares a GitHub record for storage and prevents records from different repositories or organizations from colliding. For example, it turns a branch key like `main` into something scoped by its repository.

**Data flow**: It receives one raw GitHub record and the stream it belongs to. It may reshape special cases: stargazers get user fields lifted up, and pull requests have bulky nested repository objects removed from `head` and `base`. Then it checks whether the stream was read per repository or per organization, reads the stamped partition field, and prefixes the primary key with that partition. It returns the shaped record, or raises an error if a partition-scoped record is missing its partition stamp.

**Call relations**: The storage adapter calls this before it reads the record’s primary key. It relies on `_partition_field` to know whether the stream should be scoped by repository, organization, or neither.

*Call graph*: calls 1 internal fn (_partition_field).


##### `GitHubConnector.paginate_source`  (lines 250–261)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Adapts the project’s generic source-reading interface to GitHub’s paging method. It also passes through a backfill floor, which is the oldest time a newest-first sync should fetch.

**Data flow**: It receives the HTTP client, stream description, saved cursor, current user information, and optional backfill cutoff time. It forwards the relevant pieces into `paginate` and returns the resulting asynchronous stream of pages.

**Call relations**: The sync runner calls this as the public paging entry for the connector. It immediately hands the real work to `GitHubConnector.paginate`.

*Call graph*: calls 1 internal fn (paginate).


##### `GitHubConnector.paginate`  (lines 263–334)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the correct GitHub walking strategy for a stream. It knows whether to read one global endpoint, one endpoint per organization, or one endpoint per repository.

**Data flow**: It receives a client, stream, saved cursor, and optional backfill cutoff. It looks up the stream’s API path, prepares common query parameters such as page size, then branches by path shape. Repository streams are sent through a partition walker that can resume per repository. Organization streams loop through granted organizations and stamp each page with the organization. Repository catalog pages are discovered from granted organizations. Plain paths are paged directly. It yields lists of records or richer page objects as they arrive.

**Call relations**: This is the central dispatcher called by `paginate_source`. It calls `_iter_granted_org_repo_pages`, `_iter_user_orgs`, `_paginate_link_header`, and `_enrich_users` as needed. For repository-level streams it creates a `PartitionWalk`, using the nested `repos` and `repo_pages` helpers to supply repository names and page readers.

*Call graph*: calls 4 internal fn (_enrich_users, _iter_granted_org_repo_pages, _iter_user_orgs, _paginate_link_header); called by 1 (paginate_source); 4 external calls (__init__, Semaphore, astimezone, with_context).


##### `GitHubConnector.paginate.repos`  (lines 286–288)

```
async def repos() -> AsyncIterator[str]
```

**Purpose**: Provides repository names to the per-repository walking machinery. It turns discovered owner and repository pairs into strings like `owner/repo`.

**Data flow**: It reads repository pairs from `_iter_user_repos`. For each pair, it combines the owner and repository name into one repository key and yields it.

**Call relations**: This nested helper is used only inside `GitHubConnector.paginate` when a stream path contains both `{owner}` and `{repo}`. The `PartitionWalk` calls on it to know which repositories need to be walked.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector.paginate.repo_pages`  (lines 290–291)

```
def repo_pages(repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects the partition walker to the code that reads one repository’s pages. It is a small adapter that supplies the current stream, path, repository key, and resume boundary.

**Data flow**: It receives one repository key and a `PartitionBound`, which describes the current resume window for that repository. It returns the asynchronous page iterator produced by `_repo_pages`.

**Call relations**: This nested helper is used by `GitHubConnector.paginate` when setting up `PartitionWalk`. The walker calls it whenever it is time to fetch pages for a particular repository.

*Call graph*: calls 1 internal fn (_repo_pages).


##### `GitHubConnector._repo_pages`  (lines 336–407)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Reads one repository’s slice of one stream, applying the right resume rules for that stream. It is where repository-scoped GitHub API calls are turned into bounded pages for the partition walker.

**Data flow**: It receives a client, stream, path template, repository key, and resume boundary. It fills in the owner and repository in the path, builds query parameters, and applies stream-specific rules such as `since` for ascending issue/comment walks or `until` for commit backfills. It pages through GitHub, removes pull requests from the issues stream, optionally filters newest-first event pages by time on the client side, stamps records with `repo_full_name`, computes the page’s high and low cursor values, and yields `WalkPage` objects. If GitHub says a repository is unavailable with expected skip statuses, it raises `PartitionSkipped` instead of failing the whole run.

**Call relations**: This is called through the nested `repo_pages` helper inside `GitHubConnector.paginate`. It calls `_paginate_link_header` to fetch pages, `_cursor_bounds` to report time spans to the walker, and `with_context` to add the repository stamp that `flatten` later depends on.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); called by 1 (repo_pages); 3 external calls (__init__, __init__, with_context).


##### `GitHubConnector._iter_user_repos`  (lines 409–417)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: Lists repositories that belong to organizations the credential can access. It deliberately avoids `/user/repos` because that endpoint can include personal, collaborator, archived, or forked repositories outside the intended organization scope.

**Data flow**: It receives the HTTP client, asks `_iter_granted_org_repo_pages` for repository pages grouped by organization, and examines each repository record. `_repo_identity` extracts a usable owner and repository name. Valid pairs are yielded one at a time.

**Call relations**: The nested `paginate.repos` helper calls this when repository-scoped streams need their partition list. It depends on `_iter_granted_org_repo_pages` for discovery and `_repo_identity` for turning varied GitHub repository shapes into a consistent pair.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (repos).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 419–438)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: Finds repository pages for each organization the credential can see, while skipping archived repositories and forks. This gives the connector the active organization-owned repositories it should sync.

**Data flow**: It receives the HTTP client, loops through organization logins from `_iter_user_orgs`, and calls GitHub’s organization repository endpoint for each one. It filters each returned page to remove archived repositories and forks. Non-empty filtered pages are yielded with their organization login. If an individual organization refuses access or is gone, it skips that organization and continues.

**Call relations**: This is used both by `GitHubConnector.paginate` for the repositories stream and by `_iter_user_repos` for all repository-scoped streams. It calls `_paginate_link_header` for GitHub paging and `_iter_user_orgs` for the organization list.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, paginate).


##### `GitHubConnector._iter_user_orgs`  (lines 440–461)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Lists the GitHub organizations exposed by the credential. This is the root discovery step for nearly every runnable stream in this connector.

**Data flow**: It receives the HTTP client and pages through `/user/orgs`. For each organization record, it reads the `login` value and yields it if it is a non-empty string. If GitHub returns a permission refusal at this root step, it raises `StreamSkipped`, meaning the sync records a skipped stream rather than a broken run.

**Call relations**: Organization-scoped paging in `GitHubConnector.paginate` calls this directly, and repository discovery calls it through `_iter_granted_org_repo_pages`. It calls `_paginate_link_header` to perform the actual API paging.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, paginate).


##### `GitHubConnector._enrich_users`  (lines 463–483)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: Replaces basic organization member records with fuller public GitHub user records when possible. This lets synced user pages include public fields such as name or email, if GitHub exposes them.

**Data flow**: It receives a page of member records and a semaphore, which is a small traffic light that limits how many user lookups run at once. It starts one lookup task per member, waits for all of them with `asyncio.gather`, and returns a new list containing enriched user records where available and original member records where not.

**Call relations**: Organization user paging in `GitHubConnector.paginate` calls this only for the `users` stream. It delegates each member lookup to the nested `one` helper so the page can be enriched concurrently without overwhelming GitHub.

*Call graph*: called by 1 (paginate); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 469–481)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Fetches the fuller public profile for one GitHub member, if the member has a usable login. If the profile is missing, it safely keeps the original member record.

**Data flow**: It receives one member record from the surrounding `_enrich_users` function. It reads the `login`; if there is none, it returns the original record. Otherwise it waits for permission from the semaphore, calls `/users/{login}`, parses the JSON response, and returns that full user dictionary if it is valid. A 404 response is treated as “keep the original member.”

**Call relations**: This helper is created and used inside `_enrich_users`. The outer function launches it for every member on a page and gathers the results into the enriched page returned to `GitHubConnector.paginate`.


##### `GitHubConnector._paginate_link_header`  (lines 485–493)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Pages through GitHub list endpoints that use HTTP `Link` headers to point to the next page. In plain terms, it keeps following GitHub’s “next page” sign until there are no more records.

**Data flow**: It receives a client, API path, and optional query parameters. It calls the inherited link-header page reader with this file’s page size and record parser, then yields each parsed page as a list of dictionaries. Empty responses, including GitHub’s temporary empty stats responses, produce no records.

**Call relations**: Most fetching helpers call this: organization discovery, repository discovery, repository page reads, and direct organization/global stream paging. It sits between GitHub-specific methods and the lower-level REST connector machinery.

*Call graph*: called by 4 (_iter_granted_org_repo_pages, _iter_user_orgs, _repo_pages, paginate).


##### `_partition_field`  (lines 496–504)

```
def _partition_field(path: str) -> str | None
```

**Purpose**: Decides which context field should be used to scope records from a given API path. Repository paths need `repo_full_name`, organization paths need `org_login`, and unscoped paths need neither.

**Data flow**: It receives an API path template as text. If the path contains a repository placeholder, it returns the repository partition field name. If it contains an organization placeholder, it returns the organization partition field name. Otherwise it returns `None`.

**Call relations**: `GitHubConnector.flatten` calls this before changing a record’s primary key. The answer tells `flatten` whether the record must already carry a repository or organization stamp.

*Call graph*: called by 1 (flatten).


##### `_parse_records`  (lines 507–511)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Turns a GitHub HTTP response into the list of records expected by the connector. GitHub list endpoints normally return a bare JSON array, and this function accepts only that shape.

**Data flow**: It receives an HTTP response. If the response body is empty, it returns an empty list. Otherwise it parses the JSON body and returns it only if it is a list; non-list bodies are treated as no records.

**Call relations**: This parser is passed into the inherited link-header paging helper by `_paginate_link_header`. It keeps the rest of the connector working with simple lists of record dictionaries.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 514–529)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: Extracts a repository’s owner and name from a GitHub repository record. It copes with several shapes GitHub may return, using a fallback organization when necessary.

**Data flow**: It receives one repository record and an optional fallback owner. It first tries `full_name`, such as `owner/repo`. If that is not usable, it tries the nested owner login plus repository name. If that also fails but a fallback owner exists, it uses the fallback owner with the repository name. It returns an `(owner, repo)` pair or `None` if it cannot find enough information.

**Call relations**: `GitHubConnector._iter_user_repos` calls this for each repository record discovered from organization repository pages. Its output becomes the repository keys used by repository-scoped stream paging.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 532–542)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: Finds the newest and oldest cursor values on a page, so the sync can remember how far it has walked. A cursor is a field, usually a timestamp, used as a bookmark for resuming later.

**Data flow**: It receives a page of records and the cursor field path. If there is no cursor field, it returns two `None` values. Otherwise it reads that field from each record, including nested paths such as `commit.committer.date`, keeps string values, and returns the maximum and minimum values found. If none are found, it returns two `None` values.

**Call relations**: `GitHubConnector._repo_pages` calls this for every repository page. The resulting high and low values are put into `WalkPage` so `PartitionWalk` can maintain watermarks and backfill windows.

*Call graph*: called by 1 (_repo_pages); 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/jira.py`

`io_transport` · `sync run`

This connector is the bridge between Jira Cloud and the rest of the sync system. Jira data is spread across Atlassian “sites,” and the access token can cover more than one site. So the connector first asks Atlassian which sites are available, then reads each kind of Jira data under that site’s cloud ID. Think of it like asking a building directory which floors your badge opens, then visiting the rooms on each floor.

The file defines the Jira streams the system can sync: projects, issues, issue comments, users, boards, and sprints. For most streams, Jira returns data in pages, so the connector repeatedly asks for the next page until Jira says there is no more. Issues, comments, and sprints can also be read incrementally using a cursor, which is a saved “last seen update time” that prevents rereading everything on every sync.

If Jira says the grant is not allowed to reach a site or resource, the connector marks that stream as skipped instead of crashing the whole run. Finally, it turns raw Jira issue and comment JSON into useful text. Jira descriptions and comments use Atlassian Document Format, a nested tree format, so this file walks that tree and extracts readable text.

#### Function details

##### `JiraConnector.paginate`  (lines 73–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right Jira-reading routine for the requested stream and yields pages of records. It is the main doorway the sync system uses when it wants Jira data.

**Data flow**: It receives an HTTP client, a stream description such as “issues” or “users,” and an optional cursor. It routes the request to the matching helper, passes along the cursor when needed, and yields each page of records. If Jira refuses access with a 401 or 403 response, it turns that into a skipped stream rather than a failed sync.

**Call relations**: When the sync runner asks for a Jira stream, this method dispatches to _projects, _issues, _comments, _users, _boards, or _sprints. If the stream name is unknown, or Jira refuses access, it raises StreamSkipped so the larger run can record a skip cleanly.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 106–110)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all Atlassian Cloud sites that the current authorization can access. Every later Jira request needs one of these site IDs in its path.

**Data flow**: It sends a request to Atlassian’s accessible-resources endpoint. It reads the JSON response, makes sure the result is treated as a list, and returns that list of site records. If the response is empty, it returns an empty list.

**Call relations**: _projects, _issues, _users, and _boards call this before reading site-specific data. Those methods then use each site’s cloud ID to build the correct Jira API path.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 112–132)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a Jira collection that is split into numbered pages. This prevents the connector from trying to fetch a large list all at once.

**Data flow**: It receives an API path, optional query parameters, and the JSON key where records live. It repeatedly requests pages using Jira’s startAt and maxResults parameters, extracts the records, yields non-empty pages, and stops when Jira says it is on the last page or there are no more records.

**Call relations**: _projects, _issues, _comments, _boards, and _sprints use this shared paging helper. It relies on records_at to pull the actual list out of Jira’s response envelope.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 134–141)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira projects from every accessible site. Projects are the containers Jira uses to group issues.

**Data flow**: It asks _sites for reachable Jira sites. For each valid cloud ID, it calls the project search endpoint through _offset_values, then adds context such as cloud_id and site_url to each returned page before yielding it.

**Call relations**: paginate calls this when the requested stream is projects. This method depends on _sites to know where to look and _offset_values to walk through Jira’s paged project list.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 143–155)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira issues, optionally only those updated after the saved cursor. This is the main stream for tickets, bugs, tasks, and similar Jira work items.

**Data flow**: It builds a Jira Query Language filter, called JQL, ordering issues by update time and adding an “updated after cursor” condition when a cursor exists. It then visits each accessible site, requests issue search pages with the chosen fields, adds site context, and yields those pages.

**Call relations**: paginate calls this for the issues stream. _comments also calls it with no cursor so it can discover which issues exist before asking Jira for each issue’s comments.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 157–177)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads comments attached to Jira issues. Comments are fetched by first finding issues, then asking Jira for the comments on each issue.

**Data flow**: It gets issue pages from _issues, looks at each issue’s ID and cloud ID, and calls the issue comment endpoint for that issue. If a cursor is provided, it filters out comments whose updated time is not newer than the cursor. It adds context such as the issue ID and issue key before yielding comment pages.

**Call relations**: paginate calls this for the issue_comments stream. This method uses _issues to find the parent issues and _offset_values to page through comments for each issue.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 179–190)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira users from every accessible site. This gives the sync system people records such as account IDs and display names.

**Data flow**: It asks _sites for reachable sites, then calls Jira’s users/search endpoint once per site with a page-size limit. It turns the JSON array into a safe list, adds cloud_id and site_url context, and yields the users if any are returned.

**Call relations**: paginate calls this when the requested stream is users. Unlike the other list readers, it does not use _offset_values because this Jira endpoint returns a bare JSON array rather than the usual paged envelope.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 192–199)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira Agile boards from every accessible site. Boards are views Jira uses for agile work, and they are needed to find sprints.

**Data flow**: It asks _sites for reachable sites. For each valid cloud ID, it reads the agile board endpoint through _offset_values, attaches site context to the returned records, and yields each page.

**Call relations**: paginate calls this for the boards stream. _sprints also calls it first, because Jira sprints are listed under individual boards.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 201–215)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sprints from Jira Agile boards, optionally only those updated after the saved cursor. Sprints are time-boxed work periods used by agile teams.

**Data flow**: It gets board pages from _boards, then for each board with a usable ID and cloud ID it requests that board’s sprint list. If a cursor is present, it keeps only sprints whose updatedDate is newer. It adds cloud_id and board_id context before yielding the sprint records.

**Call relations**: paginate calls this for the sprints stream. This method depends on _boards to discover where sprints live and _offset_values to page through each board’s sprint endpoint.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 217–224)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Moves the issue update time into a simple top-level field so the sync system can track progress consistently. Other streams already have their important fields in the expected place.

**Data flow**: It receives one record and its stream description. For issue records, it safely reads the nested fields object and copies fields.updated to a top-level updated value, returning the adjusted record. For all other streams, it returns the record unchanged.

**Call relations**: The connector framework uses this kind of method after records are fetched and before cursor tracking. It calls _dict_or_empty so malformed or missing issue fields do not cause an error.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 226–252)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns raw Jira issue and comment records into readable text that people can search and recall. It avoids showing users a wall of raw JSON.

**Data flow**: It receives a record and stream description. For issues, it extracts the summary, status, priority, assignee, reporter, and description text; for comments, it extracts the author and comment body. It returns a short title and a formatted text body with a heading.

**Call relations**: The connector framework calls this when it needs a human-readable page for a record. It uses _dict_or_empty, _str, _person, _field_line, and _doc_text to safely pull useful text out of Jira’s nested data.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 255–256)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns a value into a string only when it already is one. This avoids accidentally displaying non-text values as confusing text.

**Data flow**: It receives any value. If the value is a string, it returns it; otherwise it returns an empty string.

**Call relations**: render and _person use this helper while building readable Jira text. It keeps missing or oddly shaped Jira fields from breaking formatting.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 259–260)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. This protects the connector from missing or unexpected nested Jira data.

**Data flow**: It receives any value. If the value is a dictionary, it returns that dictionary; otherwise it returns an empty dictionary.

**Call relations**: flatten, render, and _person call this before reading nested fields. It is a small guardrail used wherever Jira may return absent or differently shaped data.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 263–265)

```
def _person(value: Any) -> str
```

**Purpose**: Extracts a readable name for a Jira person, such as an assignee, reporter, or comment author. It prefers the display name and falls back to the email address.

**Data flow**: It receives a possible person object. It safely treats it as a dictionary, reads displayName and emailAddress as strings, and returns the first non-empty value.

**Call relations**: render calls this when building issue metadata and comment titles. It uses _dict_or_empty and _str so incomplete person data does not cause errors.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 268–269)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: Formats one metadata line, such as “Status: Done,” but only when there is a value to show. This keeps rendered pages from filling with empty labels.

**Data flow**: It receives a label and a value. If the value is present, it returns a formatted “Label: value” line; if not, it returns an empty string.

**Call relations**: render uses this helper while building the issue metadata block. Empty results are filtered out before the final body is assembled.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 272–289)

```
def _doc_text(value: Any) -> str
```

**Purpose**: Extracts plain readable text from Atlassian Document Format, the nested structure Jira uses for descriptions and comments. Without this, descriptions and comments would stay trapped inside raw tree-shaped JSON.

**Data flow**: It receives any value that may be an Atlassian document tree. It walks through dictionaries and lists, collects every string found in a text field, joins those pieces with newlines, trims the result, and returns the final plain text.

**Call relations**: render calls this for issue descriptions and comment bodies. Inside itself, it uses the nested walk function to travel through the document tree.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 277–286)

```
def walk(node: Any) -> None
```

**Purpose**: Recursively visits each part of an Atlassian Document Format tree and collects text leaves. “Recursively” means it can enter a child, then that child’s child, as deep as the document goes.

**Data flow**: It receives one node from the document tree. If the node is a dictionary, it saves its text field when present and visits its content children; if the node is a list, it visits each item. It changes the surrounding chunks list by appending found text and returns nothing directly.

**Call relations**: _doc_text starts this walker on the original document value. The collected chunks are later joined by _doc_text into the plain text returned to render.


### `extensions/sources/ufo_ext_sources/linear.py`

`io_transport` · `during source sync, while reading Linear API pages and rendering synced records`

Linear is a project and issue tracking tool. This file is the connector that lets the system copy data out of Linear without knowing Linear's API details elsewhere in the codebase. Without it, the system would not know which Linear objects to ask for, how to page through large result sets, or how to turn raw Linear records into useful text.

The file first defines the list of Linear streams, meaning the kinds of things that can be synced: issues, projects, comments, users, teams, workflow states, labels, customer data, and more. For each stream, it stores a GraphQL query. GraphQL is an API style where the request names exactly which fields it wants back. Think of each query like a shopping list for one kind of Linear object.

The main class, LinearConnector, sends those queries to Linear's `/graphql` endpoint. It asks for one page of results, yields the records, then follows Linear's `endCursor` marker to ask for the next page. For most streams, it can also request only records updated since the last sync, using `updatedAt` as a bookmark. A few Linear collections do not support that filter, so they are fully reread each time.

The connector also turns important records into readable prose. For example, an issue becomes a short page with title, state, priority, assignee, and description, instead of a raw nested data dump.

#### Function details

##### `_stream`  (lines 32–42)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates the small description object the sync system uses to know that a Linear collection exists. It records the stream name, whether it has a cursor for incremental syncing, and whether it is one of the main content streams.

**Data flow**: It takes a Linear stream name plus optional settings for the cursor field and canonical status. It fills in common Linear defaults, such as `createdAt` and `updatedAt`, and returns a StreamSpec object that the rest of the connector framework can use.

**Call relations**: This helper is used while building the file's Linear stream list. It hands each prepared stream definition to the connector class through `streams_list`, so later sync code can ask LinearConnector which Linear collections are available.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 269–312)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Linear stream from the GraphQL API, one page at a time. It is the core network-reading loop that makes large Linear collections safe to sync without loading everything at once.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It looks up the right GraphQL query, adds an `updatedAt` filter when Linear supports it, posts the request, checks for permission or GraphQL errors, extracts the returned `nodes`, and yields each non-empty page as a list of records. It keeps following Linear's next-page cursor until Linear says there are no more pages, or until the response is missing a usable next cursor.

**Call relations**: The connector framework calls this when it wants records for a Linear stream. Inside the loop, it uses the base connector's POST helper to talk to Linear, uses `list_or_empty` to safely normalize the returned node list, and raises `StreamSkipped` when Linear refuses access with HTTP 401 or 403 so the wider sync can record a skipped stream instead of silently failing.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 314–353)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns selected Linear records into human-readable text pages. This matters because people searching or reading synced data need useful summaries, not raw API-shaped JSON.

**Data flow**: It receives one Linear record and its stream description. For issues, projects, comments, and users, it picks important fields, cleans them into strings, formats labels such as state or email, and returns a title plus a Markdown-like body. For other streams, it lets the base connector use its default rendering.

**Call relations**: The sync framework calls this after records have been fetched and need to become recallable pages. It relies on `_str` to avoid non-string surprises, `_ref_id` to pull IDs from nested Linear reference objects, and `_labeled` to make compact readable metadata blocks.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 356–357)

```
def _str(value: Any) -> str
```

**Purpose**: Safely converts a value into text only when it is already a string. It avoids accidentally rendering Python objects, numbers, or missing values as confusing text.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged; otherwise it returns an empty string.

**Call relations**: The render path uses this whenever it reads optional Linear fields such as titles, names, descriptions, and emails. `_ref_id` also uses it after pulling an `id` field from a nested object.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 360–361)

```
def _ref_id(value: Any) -> str
```

**Purpose**: Extracts the `id` from a nested Linear reference object, such as an assignee or project lead. This lets rendered text show a compact reference without dumping the whole nested object.

**Data flow**: It receives any value. If the value is a dictionary-like Linear reference, it reads its `id` field and passes that through `_str`; otherwise it returns an empty string.

**Call relations**: LinearConnector.render calls this when building readable metadata for records that point to other Linear objects. It delegates the final string safety check to `_str`.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 364–365)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: Formats a short block of labeled metadata, such as `state: started` or `email: person@example.com`. It keeps rendered pages tidy by omitting empty fields.

**Data flow**: It receives a list of label-and-value pairs. It drops pairs whose value is empty, turns the rest into `label: value` lines, and joins those lines with newlines.

**Call relations**: LinearConnector.render uses this to build the metadata sections for issues, projects, and users. It is the last formatting step before those sections are combined with descriptions or comment bodies.

*Call graph*: called by 1 (render).


### Incident and error monitoring
Connectors for operational response and error-monitoring systems that sync incidents, services, schedules, projects, issues, events, members, and releases.

### `extensions/sources/ufo_ext_sources/pagerduty.py`

`io_transport` · `sync request handling`

PagerDuty is an incident-response service, and its data is useful only if the system can fetch it reliably and in small repeatable batches. This file defines a read-only connector for that job. Think of it like a librarian who knows which PagerDuty shelves exist, how to ask for the next stack of books, and how to skip shelves the current library card is not allowed to open.

At the top, the file describes each stream of data the connector can read. A stream is one kind of PagerDuty object, such as users, incidents, or schedules. Each stream says where records live in the PagerDuty response and which field uniquely identifies each record. Some streams also name a time field used as a cursor, which is a saved checkpoint so the next sync can ask only for newer changes.

The connector uses PagerDuty's offset-and-limit pagination, meaning it asks for records in numbered chunks. For incidents, it requests them sorted by update time and can start from a saved cursor. Incident notes are special: PagerDuty exposes them under each incident, so the connector first reads incidents, then asks for notes for each one.

If PagerDuty says the token is missing permission or invalid, the connector reports that stream as skipped instead of crashing the whole run. It also sets PagerDuty's required API version header, but it does not store credentials itself; credentials come from the surrounding authentication system.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 74–77)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to PagerDuty and adds the special Accept header PagerDuty requires for its version 2 API. Someone would use this when starting a PagerDuty sync so every request speaks the API version PagerDuty expects.

**Data flow**: It receives the PagerDuty base URL and a credential supplied by the wider authentication system. It asks the parent REST connector to build a normal async HTTP client, then adds PagerDuty's versioned media type to the client's request headers. It returns that prepared client, ready to make authenticated PagerDuty API calls.

**Call relations**: This is part of the connector setup inherited from the general REST connector flow. Before any stream is paged through, the system needs a client; this method customizes that client just enough for PagerDuty.


##### `PagerDutyConnector._offset_pages`  (lines 79–99)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads ordinary PagerDuty streams that come back in offset-based pages, such as users or services. It also applies an optional cursor filter so old records can be ignored after a previous sync.

**Data flow**: It receives an HTTP client, a stream description, optional query parameters, and an optional saved cursor. It asks the shared REST pagination helper for pages from the stream's endpoint, using PagerDuty's `more` flag and returned `limit` value to know when and how to continue. If a cursor and cursor field are present, it removes records whose cursor value is not newer than the saved cursor. It yields only non-empty lists of records.

**Call relations**: This is the common paging worker for most PagerDuty streams. `PagerDutyConnector._incidents` calls it with incident-specific sorting and cursor parameters, while `PagerDutyConnector.paginate` calls it directly for straightforward streams like users, teams, services, schedules, escalation policies, and on-calls.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 101–113)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads PagerDuty incidents in update-time order, which makes incremental syncing possible. It is used when the system wants incident records and when it needs incidents as a starting point for finding incident notes.

**Data flow**: It receives an HTTP client and an optional cursor. It builds query parameters that ask PagerDuty to sort incidents by `updated_at` from oldest to newest. If a cursor exists, it adds it as PagerDuty's `since` parameter, then passes the request to `_offset_pages`. It yields each page of incident records that comes back.

**Call relations**: This function is a focused wrapper around `_offset_pages` for the incident stream. `PagerDutyConnector.paginate` uses it when the requested stream is incidents, and `PagerDutyConnector._incident_notes` uses it to discover which incident IDs should be checked for notes.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 115–128)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads notes attached to PagerDuty incidents. Notes are not fetched as one simple global list, so this function first finds incidents and then asks PagerDuty for the notes under each incident.

**Data flow**: It receives an HTTP client and an optional cursor for note creation time. It reads incidents, looks at each incident's ID, and skips any incident without a usable string ID. For each valid incident, it requests `/incidents/{incident_id}/notes`, extracts the `notes` list from the response, filters out notes that are not newer than the cursor when a cursor is present, and then adds the incident ID as extra context to each note before yielding the notes.

**Call relations**: This function is called by `PagerDutyConnector.paginate` when the requested stream is `incident_notes`. It relies on `PagerDutyConnector._incidents` to find incidents to inspect, `records_at` to pull the notes list out of PagerDuty's response shape, and `with_context` to attach the parent incident ID so each note keeps its link back to the incident it came from.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 130–164)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main stream router for the PagerDuty connector. Given a requested stream, it chooses the right reading method and yields pages of records for the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name: incidents go through the incident-specific reader, incident notes go through the note fan-out reader, and the simpler PagerDuty streams go through the shared offset-page reader. If the stream is unknown, it marks it as skipped. If PagerDuty returns HTTP 401 or 403, meaning unauthorized or forbidden, it converts that failure into a skipped stream message; other HTTP errors are allowed to rise as real failures.

**Call relations**: The wider source-sync runner calls this method when it is time to pull records for one PagerDuty stream. This method then delegates to `_incidents`, `_incident_notes`, or `_offset_pages` depending on the stream, and uses `StreamSkipped` to tell the runner that a stream could not be read because it is unsupported or the current credential lacks permission.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/sentry.py`

`io_transport` · `source sync`

Sentry is a service teams use to track software errors and releases. This file is the read-only bridge between Sentry and this project. Without it, the system would not know how to ask Sentry for data, follow Sentry’s page-by-page API results, or label records with the organization and project they came from.

The file first describes the Sentry “streams,” meaning the kinds of records that can be synced: organizations, members, projects, issues, events, and releases. Each stream says which field uniquely identifies a record and which date field can be used to continue from the last sync.

The main class, SentryConnector, is built on a shared REST connector, which is a helper for talking to web APIs. Its paginate method is the front door: given a requested stream, it chooses the right helper method. Some Sentry data is nested. For example, issues and events live under projects, and members and releases live under organizations. So the connector first asks Sentry for organizations or projects, then uses those results to ask for the child records.

Sentry returns large lists in pages. This connector follows the “next page” cursor from Sentry’s Link header, like following a “next” sign in a library catalog. If Sentry refuses access because the token is invalid or lacks permission, the stream is skipped instead of crashing the whole sync.

#### Function details

##### `_sentry_next_cursor`  (lines 76–81)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This small helper looks at Sentry’s response headers and finds the cursor for the next page of results, if Sentry says there is one. A cursor is a bookmark that tells the API where to continue reading.

**Data flow**: It receives HTTP headers from a Sentry response. It checks the Link header, searches for the part that means “there is a next page with results,” and returns the cursor text. If there is no such header or no usable cursor, it returns nothing.

**Call relations**: SentryConnector._paged_list calls this after each API response. If this helper finds a cursor, _paged_list uses it to request the next page; if not, paging stops.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.paginate`  (lines 89–128)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a Sentry stream. It decides which Sentry endpoint path and helper method should be used for the requested kind of data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching helper, and yields batches of records. For some streams it filters records newer than the cursor. If the stream is unknown, or Sentry refuses access with a permission or authentication error, it raises StreamSkipped so the sync can move on safely.

**Call relations**: The broader sync system calls this when it wants records from Sentry. paginate then hands work to _organizations, _projects, _members, _issues, _events, or _releases depending on the stream. It is the coordinator that keeps the rest of the system from needing to know Sentry’s URL layout.

*Call graph*: calls 7 internal fn (__init__, _events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._paged_list`  (lines 130–147)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads one Sentry list endpoint from start to finish, following Sentry’s page cursors until there are no more results. It keeps the repeated paging logic in one place.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It sends a request, turns the JSON response into a list of dictionary-like records, yields any records it found, then looks for a next-page cursor in the response headers. If there is a cursor, it repeats with that cursor added to the query; otherwise it stops.

**Call relations**: All the specific readers use this helper when they need a paged Sentry endpoint. It calls _sentry_next_cursor to understand Sentry’s Link header, then supplies clean pages of records back to organization, project, member, issue, event, and release readers.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 149–153)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This reads all organizations visible to the current Sentry grant. Organizations are the top-level containers needed before reading organization-specific data like members and releases.

**Data flow**: It receives an HTTP client. It asks _paged_list for every page from Sentry’s organizations endpoint, gathers all pages into one list, and returns that list of organization records.

**Call relations**: paginate calls this directly for the organizations stream. _members and _releases also call it first, because they need each organization slug before they can ask Sentry for members or releases inside that organization.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, paginate).


##### `SentryConnector._projects`  (lines 155–159)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This reads all projects visible to the current Sentry grant. Projects are needed both as their own stream and as the starting points for issue and event reads.

**Data flow**: It receives an HTTP client. It asks _paged_list for every page from Sentry’s projects endpoint, combines those pages into one list, and returns the project records.

**Call relations**: paginate calls this directly for the projects stream. _issues and _events call it first so they can walk through each project and request that project’s issue or event data.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, paginate).


##### `SentryConnector._members`  (lines 161–167)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads organization members from Sentry. Because members belong to an organization, it first finds the organizations and then reads members inside each one.

**Data flow**: It receives an HTTP client. It loads organizations, takes each valid organization slug, asks Sentry for that organization’s members page by page, and adds the organization_slug to each returned member record. It yields member batches as they are found.

**Call relations**: paginate calls this when the members stream is requested. _members relies on _organizations to find where to look, uses _paged_list to read each members endpoint, and uses with_context to stamp records with the organization they came from.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 169–183)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads issues from each Sentry project. It can also ask Sentry for only issues whose last-seen time is newer than the previous sync cursor.

**Data flow**: It receives an HTTP client and an optional cursor. It loads projects, extracts each project’s organization slug and project slug, builds a project-specific issues API path, and optionally adds a query such as “lastSeen is after this cursor.” For every page of issues, it adds organization_slug and project_slug to the records before yielding them.

**Call relations**: paginate calls this when the issues stream is requested. _issues uses _projects to discover the project tree, _paged_list to read each project’s issues, and with_context to preserve where each issue came from.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 185–199)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads events from each Sentry project. Events are individual occurrences, so this method walks through projects and can limit results to events newer than the last sync cursor.

**Data flow**: It receives an HTTP client and an optional cursor. It loads projects, finds each project’s organization and project slug, builds that project’s events endpoint, and optionally asks Sentry for events with timestamps after the cursor. It yields pages of event records after adding organization_slug and project_slug.

**Call relations**: paginate calls this for the events stream. Like _issues, it depends on _projects to know which project endpoints exist, _paged_list to follow Sentry pagination, and with_context to attach the project and organization labels.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 201–212)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads releases for each Sentry organization. A release usually represents a deployed version of software, and this method gathers those versions organization by organization.

**Data flow**: It receives an HTTP client and an optional cursor. It loads organizations, takes each valid organization slug, reads that organization’s releases through _paged_list, and if a cursor is present keeps only releases created after that cursor. It then adds organization_slug and yields non-empty release batches.

**Call relations**: paginate calls this when the releases stream is requested. _releases starts with _organizations because releases are organized under organizations, uses _paged_list for API paging, and uses with_context so downstream storage knows which organization each release belongs to.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).
