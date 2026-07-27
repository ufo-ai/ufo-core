# Developer operations and incident connectors  `stage-13.1.4`

This stage is shared behind-the-scenes support for bringing developer and operations data into the system. It is like a set of adapters for different tools that engineering teams already use. Each adapter talks to an outside web service through its API, meaning a structured way for software to request data, then reshapes the answers into records the rest of the system can sync, store, search, and recall.

The GitHub connector reads software-development activity: organizations, repositories, issues, comments, users, and related objects. It helps the system discover what code projects a user has access to and keep their GitHub activity up to date.

The PagerDuty connector reads operations data: services, incidents, notes, schedules, users, and on-call entries. This lets the system understand outages, responsibilities, and response history.

The Sentry connector reads application-monitoring data: organizations, projects, issues, events, members, and releases. Together, these connectors give the system a fuller picture of code, incidents, and production errors.

## Files in this stage

### Engineering and incident source connectors
Source connectors that ingest repository, issue, incident, service, project, event, schedule, and release data from developer operations platforms.

### `extensions/sources/ufo_ext_sources/github.py`

`io_transport` · `during GitHub source sync`

This file is the GitHub “source connector”: a read-only bridge between GitHub’s web API and the project’s general sync system. Its job is to know which GitHub lists exist, where to fetch them, how to page through long results, and how to resume later without rereading everything unnecessarily.

The connector starts from the organizations the GitHub credential can see. It asks GitHub for `/user/orgs`, then for each organization’s repositories, skipping forks and archived repositories. From there, it fans out into repository-based streams such as issues, commits, releases, branches, and workflow runs. This is like first getting a building directory, then visiting each room listed there instead of asking the user to name every room by hand.

GitHub returns most list results in pages, so this file follows GitHub’s `Link` header, which points to the next page. Some streams can be resumed by time, such as issues updated after a saved point. Others, like newest-first event feeds, need careful “watermark” tracking so that new records appearing at the top do not cause older records to be missed.

The file also smooths over GitHub quirks. Pull requests are flattened so large nested repository objects are removed. Stargazer records expose their user fields more directly. If an organization or repository cannot be read, the connector skips just that part when safe; if the whole organization list is blocked, it marks the stream as skipped instead of crashing the run.

#### Function details

##### `_stream`  (lines 60–78)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', ordering: Ordering=Ordering.none, canonical:
```

**Purpose**: Builds a description of one GitHub stream, such as issues or repositories. The rest of the connector uses this description to know the stream’s name, unique key, date fields, and sync ordering.

**Data flow**: It receives stream settings like the stream name, primary key, cursor field, and ordering choice. It fills in sensible defaults, then creates and returns a `StreamSpec`, which is the sync system’s standard stream description object.

**Call relations**: This helper is used while defining the file’s catalog of GitHub streams. It hands those stream descriptions to the connector class, which later filters them in `GitHubConnector.streams` and uses them in `GitHubConnector.paginate`.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 171–174)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the GitHub streams that this connector can actually fetch today. Some streams are listed for catalog completeness, but only streams with an API path wired in this file are runnable.

**Data flow**: It reads the full stream list stored on the connector. It keeps only the streams whose names appear in the path table, then returns that smaller list.

**Call relations**: The sync framework asks this method what can be read. Its answer controls which streams later reach `GitHubConnector.paginate` for actual fetching.


##### `GitHubConnector._make_client`  (lines 176–180)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to GitHub, then adds the GitHub-specific headers required for the API version and response format. These headers tell GitHub which API contract the connector expects.

**Data flow**: It receives a base URL and a credential. It asks the parent REST connector to build the authenticated client, adds GitHub `Accept` and API-version headers, and returns the prepared client.

**Call relations**: The base connector setup calls this when preparing network access. The returned client is then used by pagination and helper methods throughout this file.


##### `GitHubConnector.flatten`  (lines 182–198)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Cleans up a few GitHub records before they are stored or rendered. It keeps most records unchanged, but makes pull request and stargazer records easier and safer to use.

**Data flow**: It receives one GitHub record and the stream it came from. For stargazers, it merges the nested user object into the top-level record when possible. For pull requests, it removes the large nested `repo` objects inside `head` and `base`. It returns the adjusted record.

**Call relations**: The broader sync system calls this after records are fetched. It does not fetch more data itself; it prepares each record produced by `GitHubConnector.paginate` for downstream storage or display.


##### `GitHubConnector.paginate`  (lines 200–256)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main reading route for a stream. Given a stream and an optional saved cursor, it decides which GitHub endpoint to call, how to fan out across organizations or repositories, and how to yield pages of records back to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and a cursor from a previous sync if one exists. It looks up the stream’s GitHub API path, builds common request parameters such as page size, and then chooses a path: repository catalog, repository-scoped stream, organization-scoped stream, or simple endpoint. It yields pages of records, sometimes wrapped with partition progress information.

**Call relations**: The sync runner calls this when it wants records for a stream. For repository streams it creates a `PartitionWalk`, using the nested `repos` function to discover repositories and the nested `repo_pages` function to fetch each repository. For organization streams it calls `_iter_user_orgs`, and for users it also calls `_enrich_users`. All actual page walking goes through `_paginate_link_header` or helpers built on it.

*Call graph*: calls 4 internal fn (_enrich_users, _iter_granted_org_repo_pages, _iter_user_orgs, _paginate_link_header); 2 external calls (__init__, Semaphore).


##### `GitHubConnector.paginate.repos`  (lines 218–220)

```
async def repos() -> AsyncIterator[str]
```

**Purpose**: Provides the repository list for the partitioned repository walk. Each repository becomes one partition, meaning one separately tracked slice of work.

**Data flow**: It takes no direct user input beyond the surrounding HTTP client. It asks `_iter_user_repos` for each visible organization-owned repository, formats each one as `owner/repo`, and yields those strings.

**Call relations**: This nested helper exists only inside `GitHubConnector.paginate`. `PartitionWalk` calls on it when it needs to know which repositories should be visited before `repo_pages` fetches records for each one.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector.paginate.repo_pages`  (lines 222–223)

```
def repo_pages(repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects the partition walker to the code that fetches one repository’s pages. It adapts the general partition-walk interface to this connector’s `_repo_pages` method.

**Data flow**: It receives a repository key such as `owner/repo` and a partition bound, which is the saved time window or resume limit for that repository. It passes the client, stream, path, repository key, and bound into `_repo_pages`, and returns that async page stream.

**Call relations**: This nested helper is handed to `PartitionWalk` by `GitHubConnector.paginate`. Whenever the walk chooses a repository to read, this helper delegates the real work to `GitHubConnector._repo_pages`.

*Call graph*: calls 1 internal fn (_repo_pages).


##### `GitHubConnector._repo_pages`  (lines 258–306)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Fetches pages for one repository and one stream, while applying the correct resume rules. This is what prevents repeated syncs from losing records or rereading too much data.

**Data flow**: It receives a client, stream, GitHub path template, repository key, and a bound describing where to resume. It fills in the owner and repository name, builds query parameters, applies stream-specific filters such as `since` or `until`, walks GitHub pages, removes pull requests from the issues stream, and computes each page’s highest and lowest cursor values. It yields `WalkPage` objects containing records plus their cursor span. If GitHub says the repository is gone or unreadable in an expected way, it raises `PartitionSkipped` so only that repository is skipped.

**Call relations**: It is called through the nested `GitHubConnector.paginate.repo_pages` helper during a `PartitionWalk`. It relies on `_paginate_link_header` for network paging and `_cursor_bounds` to report progress back to the partition walker.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); called by 1 (repo_pages); 2 external calls (__init__, __init__).


##### `GitHubConnector._iter_user_repos`  (lines 308–316)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: Finds the organization-owned repositories the credential can read. It deliberately avoids GitHub’s broader `/user/repos` list so the sync stays limited to repositories under granted organizations.

**Data flow**: It receives the HTTP client. It asks `_iter_granted_org_repo_pages` for pages of repositories, extracts a usable `(owner, repo)` pair from each repository record with `_repo_identity`, and yields those pairs.

**Call relations**: It is called by the nested `GitHubConnector.paginate.repos` helper when repository-scoped streams need their partitions. It builds on organization and repository discovery rather than making independent API decisions.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (repos).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 318–337)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: Walks through each visible organization and yields pages of usable repositories from that organization. It filters out archived repositories and forks because those are not part of the intended sync set.

**Data flow**: It receives the HTTP client. It gets organization names from `_iter_user_orgs`, calls each organization’s repository endpoint, removes records marked as archived or forked, and yields the organization name together with each non-empty repository page. If one organization is forbidden or gone, it skips that organization and keeps going.

**Call relations**: It is used in two places: `GitHubConnector.paginate` uses it directly for the repositories stream, and `_iter_user_repos` uses it to feed repository-scoped streams. It depends on `_paginate_link_header` for following GitHub pages.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, paginate).


##### `GitHubConnector._iter_user_orgs`  (lines 339–360)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Lists the GitHub organizations exposed by the credential. This is the root discovery step for nearly every runnable stream in this connector.

**Data flow**: It receives the HTTP client. It pages through `/user/orgs`, reads each organization’s `login`, and yields valid organization names. If GitHub refuses this root organization list with a permission error, it raises `StreamSkipped` so the sync records a skipped stream instead of treating it as a broken run.

**Call relations**: Organization-scoped reads in `GitHubConnector.paginate` call this directly. Repository discovery calls it through `_iter_granted_org_repo_pages`. Because so much fan-out starts here, this method decides whether the connector can read anything for the grant at all.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, paginate).


##### `GitHubConnector._enrich_users`  (lines 362–382)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: Expands simple organization-member records into fuller public GitHub user records when possible. This can add fields such as public name or email that are not present in the basic member list.

**Data flow**: It receives an HTTP client, a page of member records, and a semaphore, which is a small gate that limits how many user lookups run at the same time. It launches one lookup task per member, waits for all of them with `asyncio.gather`, and returns a list where each member is replaced by the fuller user record if GitHub returned one.

**Call relations**: It is called by `GitHubConnector.paginate` only for the `users` stream. The nested `one` function performs each individual user lookup, while the semaphore keeps the connector from flooding GitHub with too many simultaneous requests.

*Call graph*: called by 1 (paginate); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 368–380)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Looks up one GitHub user by login and returns the fuller public user record if it can. If the login is missing or GitHub says the user no longer exists, it keeps the original member record.

**Data flow**: It receives one member dictionary from the surrounding `_enrich_users` page. It checks for a valid `login`, waits for a semaphore slot, calls `/users/{login}`, parses the response body if present, and returns either the detailed user dictionary or the original member dictionary.

**Call relations**: This helper is created inside `_enrich_users` and is run concurrently for each member on a page. Its results are gathered back into one enriched page, which `GitHubConnector.paginate` then yields for the `users` stream.


##### `GitHubConnector._paginate_link_header`  (lines 384–392)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Follows GitHub’s standard page-to-page navigation for list endpoints. GitHub puts the next-page URL in a `Link` header, and this helper turns that into a simple stream of record lists.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It delegates to the base REST paging helper with the GitHub page size and `_parse_records` parser, then yields each parsed list of records. Empty responses, including GitHub’s temporary empty stats responses, become empty pages rather than errors.

**Call relations**: Most fetching methods in this file use this helper: `GitHubConnector.paginate`, `_repo_pages`, `_iter_user_orgs`, and `_iter_granted_org_repo_pages`. It centralizes GitHub pagination so those callers can focus on stream-specific choices.

*Call graph*: called by 4 (_iter_granted_org_repo_pages, _iter_user_orgs, _repo_pages, paginate).


##### `_parse_records`  (lines 395–399)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Turns a GitHub HTTP response into a list of records. It accepts only JSON arrays because GitHub list endpoints return arrays for the streams this connector reads.

**Data flow**: It receives an HTTP response. If there is no body, it returns an empty list. Otherwise it parses the JSON body and returns it only if it is a list; non-list bodies also become an empty list.

**Call relations**: It is passed into `_paginate_link_header`, which uses it for every page fetched through the shared GitHub pagination path.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 402–417)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: Extracts the owner and repository name from a GitHub repository record. This gives the connector the `owner/repo` information needed to call repository-specific endpoints.

**Data flow**: It receives one repository record and, optionally, a fallback owner name from the organization being scanned. It first tries `full_name`, then the nested owner login plus repository name, then the fallback owner plus repository name. It returns an `(owner, repo)` pair when it can, otherwise `None`.

**Call relations**: It is called by `_iter_user_repos` while turning repository pages into repository partitions. If it cannot identify a repository, that record is simply not yielded for repository-scoped syncing.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 420–430)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: Finds the newest and oldest cursor values on a page of records. The partition walker uses these values as progress markers so future syncs know where to resume.

**Data flow**: It receives a page of records and the name of the cursor field, which may be a nested path such as `commit.committer.date`. If there is no cursor field, it returns two `None` values. Otherwise it reads that field from each record, keeps string values, and returns the maximum and minimum values found.

**Call relations**: It is called by `_repo_pages` after a page has been fetched and filtered. The returned high and low values are placed into a `WalkPage`, which lets `PartitionWalk` update watermarks and resume windows.

*Call graph*: called by 1 (_repo_pages); 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/pagerduty.py`

`io_transport` · `source sync`

PagerDuty is an external service, so the system needs a careful translator between PagerDuty's API and UFO's source-sync format. This file is that translator. It defines which PagerDuty collections can be read, what field uniquely identifies each record, and which timestamp can be used to continue a later sync from where the last one stopped.

The main class, PagerDutyConnector, is a read-only connector. It builds an HTTP client with PagerDuty's required Accept header, then asks PagerDuty for records in pages. Pagination is like reading a long book one chapter at a time: PagerDuty returns a batch of records and says whether there is more to fetch. For incidents, the connector can use a cursor, meaning a saved timestamp, so it only asks for incidents updated after the last sync. Incident notes are different: PagerDuty exposes notes under each incident, so the connector first reads incidents, then visits each incident's notes endpoint and attaches the incident id as context.

A useful safety detail is how permission problems are treated. If PagerDuty replies with 401 or 403, meaning the token is invalid or lacks permission, the connector marks that stream as skipped instead of crashing the whole run. The file does not write anything back to PagerDuty; it only reads.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 74–77)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the web client used to talk to PagerDuty. It adds PagerDuty's required API version header so PagerDuty knows which response format the connector expects.

**Data flow**: It receives a base API address and a credential supplied by the system's authentication layer. It first lets the shared REST connector create the normal HTTP client, then adds the PagerDuty-specific Accept header. The result is a ready-to-use asynchronous HTTP client for PagerDuty requests.

**Call relations**: This is the setup step for later API calls. Once the client is created, the paging functions use it to fetch users, incidents, notes, and the other PagerDuty streams.


##### `PagerDutyConnector._offset_pages`  (lines 79–99)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one PagerDuty collection that uses offset-based pagination, meaning the API returns records in numbered batches. It can also filter out records that are not newer than a saved cursor timestamp.

**Data flow**: It takes an HTTP client, a stream description, optional request parameters, and an optional cursor. It asks the shared REST helper for pages from the stream's PagerDuty endpoint, using PagerDuty's 'more' and 'limit' response fields to know how to keep going. For each batch, it removes old records when a cursor field is available, then yields only non-empty batches.

**Call relations**: This is the common paging workhorse. PagerDutyConnector._incidents uses it after adding incident-specific sorting and since parameters, and PagerDutyConnector.paginate uses it directly for the simpler streams such as users, teams, services, schedules, and on-calls.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 101–113)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads PagerDuty incidents in update-time order so incremental syncs can resume cleanly. It is used when the system needs the incident stream itself or when it needs incidents as a starting point for fetching incident notes.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds request parameters that sort incidents by updated_at from oldest to newest, and, when a cursor exists, asks PagerDuty for incidents since that time. It then delegates the actual page-by-page fetching to PagerDutyConnector._offset_pages and yields each page of incidents.

**Call relations**: PagerDutyConnector.paginate calls this when the requested stream is incidents. PagerDutyConnector._incident_notes also calls it, but without a cursor, because it needs to walk through incidents to discover where notes live.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 115–128)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads notes attached to PagerDuty incidents. Because notes are not fetched as one global list, it first finds incidents and then asks PagerDuty for the notes under each incident.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It reads incidents, takes each valid incident id, calls the incident's notes endpoint, extracts the notes list from the response, and filters out old notes if a cursor was provided. Before yielding notes, it adds the incident id as extra context so each note can be traced back to its incident.

**Call relations**: PagerDutyConnector.paginate calls this for the incident_notes stream. Inside, it relies on PagerDutyConnector._incidents to discover incidents, records_at to pull the notes array out of PagerDuty's response, and with_context to attach the parent incident id before handing records back to the sync flow.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 130–164)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher that decides how to read each PagerDuty stream. It chooses the right paging method for incidents, incident notes, or the simpler PagerDuty collections.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, then yields pages from the matching helper: incidents use the incident reader, incident notes use the notes reader, and standard collections use the offset page reader. If a stream is unknown, it marks it as skipped. If PagerDuty refuses access with 401 or 403, it also turns that into a skipped stream with a clear explanation; other HTTP errors are allowed to fail normally.

**Call relations**: This is the function the broader source-sync runner calls when it wants records for one stream. It hands the work to PagerDutyConnector._incidents, PagerDutyConnector._incident_notes, or PagerDutyConnector._offset_pages, and uses StreamSkipped to report unsupported streams or permission-related refusals without treating them as ordinary data pages.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/sentry.py`

`io_transport` · `during a Sentry source sync`

Sentry is an error-tracking service, and its data is arranged like a tree: organizations contain projects, and projects contain issues and events. This file is the connector that walks that tree and asks Sentry for each useful kind of record. Without it, the system would not know which Sentry API addresses to call, how to follow Sentry’s pagination, or how to label records with the organization or project they came from.

The file defines the available Sentry streams first. A stream is a named type of data, like “issues” or “releases,” with information about its main ID field and date fields. The main class, SentryConnector, is read-only. It does not store or change anything in Sentry.

The connector’s central job is pagination: Sentry returns results in pages, like a long book split into chapters. The next page is announced in an HTTP Link header, so this file reads that header and keeps requesting pages until Sentry says there are no more. For deeper streams, it first collects organizations or projects, then asks for members, releases, issues, or events under each one. When possible, it uses a cursor, which is a “last seen” marker, to fetch only newer records. If Sentry refuses access because the token is missing permissions or invalid, the connector skips that stream cleanly instead of crashing the whole sync.

#### Function details

##### `_sentry_next_cursor`  (lines 76–81)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper looks at Sentry’s response headers and finds the cursor for the next page of results, if there is one. It exists because Sentry hides the “next page” marker inside an HTTP Link header rather than in the JSON body.

**Data flow**: It receives response headers from Sentry. It looks for a Link header, searches it for a next-page cursor marked as having more results, and returns that cursor text. If there is no Link header or no usable next cursor, it returns nothing.

**Call relations**: The page-fetching loop in SentryConnector._paged_list calls this after each Sentry request. Its answer decides whether _paged_list should ask Sentry for another page or stop.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.paginate`  (lines 89–128)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading a requested Sentry stream. Given a stream name, it chooses the right helper to fetch organizations, projects, members, issues, events, or releases.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing where the last sync stopped. It routes the request to the matching helper, yields pages of records back to the sync system, and applies simple cursor filtering for project records when needed. If the stream is unknown, or Sentry refuses access with an authorization error, it raises StreamSkipped so the sync can continue with other work.

**Call relations**: The wider source-sync framework calls this when it wants records for one Sentry stream. This method then hands off to _organizations, _projects, _members, _issues, _events, or _releases, depending on the stream.

*Call graph*: calls 7 internal fn (__init__, _events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._paged_list`  (lines 130–147)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches one Sentry API list endpoint page by page. It is the shared machinery that prevents every stream from having to reimplement pagination.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It sends a request, reads the JSON response, keeps only dictionary-shaped records, yields non-empty pages, then checks the response headers for a next cursor. If a next cursor exists, it repeats with that cursor added to the query; otherwise it stops.

**Call relations**: All the stream-specific helpers call this whenever they need to read a Sentry list endpoint. It calls _sentry_next_cursor after each response so it knows whether another request is needed.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 149–153)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches all organizations visible to the Sentry credential. Organizations are the top-level containers needed before fetching organization-specific data like members and releases.

**Data flow**: It receives an HTTP client. It asks _paged_list for every page from Sentry’s organizations endpoint, gathers those pages into one list, and returns that list of organization records.

**Call relations**: SentryConnector.paginate calls this directly for the organizations stream. _members and _releases also call it first so they know which organizations to visit.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, paginate).


##### `SentryConnector._projects`  (lines 155–159)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches all projects visible to the Sentry credential. Projects are needed before the connector can fetch project-specific data like issues and events.

**Data flow**: It receives an HTTP client. It asks _paged_list for every page from Sentry’s projects endpoint, combines the pages into one list, and returns that list of project records.

**Call relations**: SentryConnector.paginate calls this directly for the projects stream. _issues and _events call it first so they can visit each project in turn.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, paginate).


##### `SentryConnector._members`  (lines 161–167)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches organization members from each accessible Sentry organization. It also adds the organization slug to each member record so the record still says where it came from after it leaves Sentry.

**Data flow**: It receives an HTTP client. It first gets the organizations, skips any organization without a usable slug, then requests the members endpoint for each valid organization. Before yielding each page, it stamps the records with organization_slug.

**Call relations**: SentryConnector.paginate calls this for the members stream. This function depends on _organizations to find the organizations and _paged_list to walk through each organization’s member pages; it uses with_context to attach the organization label.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 169–183)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches issues for each accessible Sentry project. If a cursor is available, it asks Sentry only for issues whose last-seen time is newer than that cursor.

**Data flow**: It receives an HTTP client and an optional cursor. It first gets all projects, extracts each project’s organization slug and project slug, skips incomplete project records, then requests that project’s issues endpoint. Each yielded page is labeled with both organization_slug and project_slug.

**Call relations**: SentryConnector.paginate calls this for the issues stream. This function uses _projects to find where to look, _paged_list to fetch each project’s issues, and with_context to preserve the project and organization identity on the returned records.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 185–199)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches events for each accessible Sentry project. Events are filtered by timestamp when a cursor is provided, so repeat syncs can focus on newer events.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all projects, finds the organization and project slugs for each one, skips records missing those names, then requests that project’s events endpoint. When a cursor exists, it sends a Sentry query asking for events after that timestamp, and it labels returned records with organization_slug and project_slug.

**Call relations**: SentryConnector.paginate calls this for the events stream. Like _issues, it relies on _projects for the project list, _paged_list for Sentry pagination, and with_context to attach the source project details.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 201–212)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches releases for each accessible Sentry organization. It can filter releases by creation date when a cursor is available.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all organizations, skips any without a valid slug, then requests the releases endpoint for each organization. If a cursor is present, it keeps only releases with a dateCreated value newer than the cursor, labels the remaining records with organization_slug, and yields non-empty pages.

**Call relations**: SentryConnector.paginate calls this for the releases stream. This function uses _organizations to find organizations, _paged_list to read release pages, and with_context to keep the organization identity attached to each release.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).
