# Engineering collaboration and service operations connectors  `stage-14.1.2`

This stage is the set of “connectors” that lets the system bring in day-to-day engineering knowledge from outside tools. It is shared behind-the-scenes support for the sync process: each connector talks to a service through its API, meaning the service’s structured doorway for reading data, then reshapes the results into records the rest of the system can store, search, and recall.

The Confluence connector reads spaces, pages, blog posts, comments, groups, and audit logs, and converts rich page layouts into plain text. GitHub gathers repositories, issues, comments, users, commits, and releases, while carefully handling many repositories and paged results. Jira and Linear pull work-tracking data such as projects, issues, teams, comments, boards, and sprints. PagerDuty brings in operational data like incidents, services, schedules, notes, and on-call entries. Sentry adds error-monitoring records, including projects, issues, events, releases, and members. Slack reads workspace conversations, messages, threads, users, and participants. Together, these parts act like translators, turning many workplace tools into one searchable memory.

## Files in this stage

### Knowledge content
Connectors that ingest internal documentation and collaboration knowledge for later search and recall.

### `extensions/sources/ufo_ext_sources/confluence.py`

`io_transport` · `sync run`

Confluence does not store page bodies as simple text. It stores them as XHTML, which is like HTML with extra Confluence-specific tags for macros and page structure. If the system saved that raw markup, a person searching later would see noisy tags instead of the words on the page. This connector solves that by reading Confluence records through Atlassian’s API and then shaping them into cleaner records with titles, URLs, dates, authors, parent links, and plain text bodies.

The connector first asks Atlassian which Confluence sites the current authorization grant can reach. A single grant may cover more than one site, so every stream is read once per site. For each site, it calls the right Confluence API path and walks through results in pages, like reading a long list 50 items at a time. Some streams are incremental, meaning the connector compares each record’s timestamp with the last saved cursor and only yields newer records, even though Confluence itself does not offer a true “since this time” filter for these endpoints.

It also protects against cross-site confusion by prefixing most record IDs with the site’s cloud ID. Finally, for pages, posts, comments, and space descriptions, it renders Confluence storage HTML into readable prose.

#### Function details

##### `_body_text`  (lines 114–116)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: Finds the useful body text field inside a Confluence record. It prefers Confluence’s storage-format body, and falls back to the view-format body if needed.

**Data flow**: It receives one record as a dictionary-like object. It looks inside nested fields for `body.storage.value` first, then `body.view.value`; if it finds a non-empty string, it returns that string, otherwise it returns nothing.

**Call relations**: This is a small helper used when `ConfluenceConnector.flatten` is turning raw page, blog post, or comment records into the simpler shape the rest of the system expects.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 124–150)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Confluence stream from every accessible Confluence site, yielding batches of records. It is the main doorway from the sync engine into Confluence’s API for this connector.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It finds the API path for that stream, asks which Confluence sites are available, reads records from each site, adds site context such as the cloud ID and site URL, and yields lists of records. If Confluence refuses access with a permission-related error, it turns that into a skipped stream rather than a failed sync.

**Call relations**: During a sync, the base connector machinery calls this method to fetch records. It calls `_sites` to discover reachable sites, then calls `_offset_results` for each site to page through that stream. Before yielding each batch, it uses `with_context` so later steps know which site each record came from.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 152–156)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Asks Atlassian which Confluence sites the current authorization grant can access. This matters because one user connection can cover multiple Confluence sites.

**Data flow**: It uses the provided HTTP client to call Atlassian’s accessible-resources endpoint. It reads the JSON response and normalizes it into a list, returning an empty list if the response is missing or not shaped as expected.

**Call relations**: `paginate` calls this before reading any stream. The returned site list tells `paginate` which cloud IDs to plug into the site-specific Confluence API URLs.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 158–182)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Walks through a Confluence collection page by page. It also filters incremental streams so only records newer than the saved cursor are passed onward.

**Data flow**: It receives an API path, optional query parameters, and optional cursor information. It repeatedly requests results with a `start` position and a fixed page size, pulls the records out of the response, removes records at or before the stored cursor when needed, yields non-empty batches, and stops when there are no more records or no next-page link.

**Call relations**: `paginate` calls this for each stream on each accessible site. It relies on helper functions to pull nested values from Confluence responses and to extract the list of records from the response body.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 184–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Confluence API records into a cleaner, more consistent record shape for syncing and recall. It adds common fields such as title, body, URL, timestamps, author, and parent IDs where they make sense.

**Data flow**: It receives one raw record and the stream it belongs to. Depending on the stream, it copies the raw data and adds or lifts key fields into predictable places; for most streams it also prefixes the record ID with the Confluence site cloud ID so records from different sites cannot collide. If the cursor field is nested, such as `version.createdAt`, it copies that nested value to the flat cursor key.

**Call relations**: This runs after records have been fetched by `paginate`. It calls `_body_text` to pull page-like body content out of Confluence’s nested body fields and uses nested-path lookups to read fields such as web links, versions, and authors.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 234–250)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Builds the human-readable text that will be stored for recall. For page-like records, it removes Confluence’s HTML-like markup and keeps the words a person would actually read.

**Data flow**: It receives a flattened record and its stream description. For pages, blog posts, comments, and spaces, it chooses a title, extracts readable body text from the stored markup or description, creates a heading, and returns both the title and the final text block. For other streams, it leaves rendering to the parent connector behavior.

**Call relations**: This is used after records are flattened, when the system needs the searchable prose version of a record. It uses `_str` to safely treat non-string titles as empty and reads nested description fields for spaces.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 259–261)

```
def __init__(self) -> None
```

**Purpose**: Prepares a fresh HTML text extractor with an empty place to collect text pieces. It also configures the parser so character references like `&amp;` become normal characters.

**Data flow**: It receives no outside data beyond the new object being created. It initializes the underlying HTML parser and creates an empty list that will collect text and line-break markers as the parser reads the input.

**Call relations**: `_StorageTextExtractor.extract` creates an instance of this class when it needs to turn Confluence storage XHTML into plain text. The rest of the parser methods then fill the collected parts.


##### `_StorageTextExtractor.extract`  (lines 264–269)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: Converts Confluence storage-format XHTML into plain readable text. It is the simple public entry point for the extractor class.

**Data flow**: It receives any value. If the value is not a non-empty string, it returns an empty string. If it is text, it feeds that text into a parser, lets the parser collect words and line breaks, then returns the cleaned result.

**Call relations**: Rendering code uses this when it needs the body of a page, blog post, comment, or space description to be readable. It creates the parser object, which then calls the parser callback methods as it reads tags and text.


##### `_StorageTextExtractor.handle_data`  (lines 271–272)

```
def handle_data(self, data: str) -> None
```

**Purpose**: Collects the actual words found between HTML tags. This is where readable page content is kept.

**Data flow**: The HTML parser gives it a piece of text from the input. It appends that text to the extractor’s internal list so it can be cleaned and joined later.

**Call relations**: This is called by Python’s HTML parser while `_StorageTextExtractor.extract` is feeding it Confluence XHTML. Later, `_text` combines the collected pieces into the final plain text.


##### `_StorageTextExtractor.handle_starttag`  (lines 274–276)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: Adds a line break when a block-like HTML tag begins. This keeps paragraphs, headings, table cells, and list items from being squashed together.

**Data flow**: The HTML parser gives it a tag name and any tag attributes. If the tag is one of the known block-level tags, it appends a newline marker to the collected parts; otherwise it ignores the tag.

**Call relations**: This is called automatically by the HTML parser during extraction. Its newline markers are later cleaned up by `_text`, making the final prose easier to read.


##### `_StorageTextExtractor.handle_endtag`  (lines 278–280)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: Adds a line break when a block-like HTML tag ends. This gives readable separation after paragraphs, headings, rows, and similar page structures.

**Data flow**: The HTML parser gives it a closing tag name. If that tag is one of the known block-level tags, it appends a newline marker; otherwise it does nothing.

**Call relations**: This works alongside `handle_starttag` while `_StorageTextExtractor.extract` parses the markup. The line breaks it adds are later normalized by `_text`.


##### `_StorageTextExtractor._text`  (lines 282–285)

```
def _text(self) -> str
```

**Purpose**: Cleans up the collected text pieces into the final plain-text body. It removes extra spaces and blank lines while preserving useful line breaks.

**Data flow**: It joins all collected parts into one string, splits that string on newline markers, compresses repeated whitespace inside each line, drops empty lines, and returns the cleaned text with leading and trailing whitespace removed.

**Call relations**: `_StorageTextExtractor.extract` calls this after the parser has finished reading the raw Confluence markup. It is the final polishing step before rendered text is returned to the connector.


##### `_str`  (lines 288–289)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns a value into a string only if it already is one. It avoids accidentally displaying Python-style representations of non-string values as titles.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: `ConfluenceConnector.render` uses this when choosing titles from records, so missing or wrongly shaped title fields do not leak into the readable output.

*Call graph*: called by 1 (render).


### Development work tracking
Connectors that read software repositories, issues, projects, comments, and planning metadata from engineering workflow systems.

### `extensions/sources/ufo_ext_sources/github.py`

`io_transport` · `source sync / API pagination`

This connector is the bridge between GitHub and the rest of the UFO source-sync system. Its job is read-only: it fetches GitHub data and presents it as streams of records that the common sync runner can store as recallable pages.

GitHub data is naturally split by organization and repository. This file first discovers the organizations available to the credential, then lists each organization’s repositories, skipping archived repositories and forks. It then fans many streams out across those repositories, like visiting every shelf in a library aisle by aisle instead of expecting someone to list every book manually.

A key detail is partition stamping. Many GitHub objects have identifiers that are only unique inside one repository, such as a branch named `main` or a tag name. The connector adds the repository name or organization name to each record before the rest of the system chooses a page key. Without that, different repositories could fight over the same stored page.

The file also handles GitHub pagination through `Link` headers, adds required GitHub API headers, enriches organization members with public user details when possible, and treats some permission problems as skipped streams rather than hard failures. It supports different sync styles: some streams move forward by update time, while append-only streams such as commits and events are walked newest-first with careful resume bounds.

#### Function details

##### `_stream`  (lines 73–93)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', ordering: Ordering=Ordering.none, canonical:
```

**Purpose**: Builds a stream description for one kind of GitHub data, such as issues or commits. A stream description tells the sync system what the records are called, which field identifies each record, and how the stream can be resumed.

**Data flow**: It receives a stream name and optional settings such as primary key, cursor field, creation time field, ordering style, and backfill window. It packages those choices into a StreamSpec object, which is later used by the connector and sync runner.

**Call relations**: This helper is used while defining the module-level catalog of GitHub streams. It hands its settings to StreamSpec so the rest of the file can treat every stream through the same shared shape.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 197–200)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns only the GitHub streams that this connector can actually fetch today. Some streams are listed for catalog compatibility, but only streams with an API path are runnable.

**Data flow**: It reads the connector’s full stream list and the path table in this file. It filters out any stream whose name has no configured GitHub API path, then returns the runnable list.

**Call relations**: The sync system asks the connector for available streams before running. This method acts as the gatekeeper that keeps unfinished or unwired stream types out of the active sync.


##### `GitHubConnector._make_client`  (lines 202–206)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to GitHub and adds the GitHub-specific headers required for the API version and response format. An HTTP client is the object that sends web requests and receives web responses.

**Data flow**: It receives the base URL and resolved credential, asks the parent RestConnector to create the authenticated client, then adds GitHub `Accept` and API version headers. It returns the prepared client.

**Call relations**: The base connector provides the general authenticated client. This method customizes it for GitHub so every later request made by pagination helpers uses the right GitHub protocol settings.


##### `GitHubConnector.flatten`  (lines 208–248)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Prepares one GitHub record before it becomes a stored page. Most importantly, it adds the repository or organization to the record’s key so records from different places do not collide.

**Data flow**: It receives one raw record and its stream description. It lightly reshapes special cases: stargazers expose the user fields more directly, and pull requests drop large nested repository objects from `head` and `base`. Then it checks whether this stream is repository-scoped or organization-scoped, reads the stamped partition field, and prefixes the primary key with that partition when a key exists. It returns the shaped record, or raises an error if a partitioned record is missing its partition stamp.

**Call relations**: The adapter calls this before reading the stream’s primary key. It relies on `_partition_field` to know whether the stream is scoped by repository or organization, and its output protects downstream page storage from accidental overwrites.

*Call graph*: calls 1 internal fn (_partition_field).


##### `GitHubConnector.paginate_source`  (lines 250–261)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Adapts the connector’s pagination entry point to accept a pinned backfill floor. That floor is a minimum time limit used by newest-first repository walks so old history can be bounded.

**Data flow**: It receives the HTTP client, stream, current cursor, self-user id, and optional backfill-after time. It ignores the self-user id here and passes the stream, cursor, and backfill floor into `paginate`. It returns the same async stream of pages that `paginate` produces.

**Call relations**: The wider source framework calls this method when it wants records. This method immediately hands the real work to `GitHubConnector.paginate`, adding only the GitHub-specific support for backfill bounds.

*Call graph*: calls 1 internal fn (paginate).


##### `GitHubConnector.paginate`  (lines 263–334)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the right GitHub API walk for a stream and yields pages of records. It is the main traffic director for fetching organizations, repositories, repository-scoped data, and organization-scoped data.

**Data flow**: It receives a client, stream, cursor, and optional backfill floor. It finds the stream’s API path, prepares common query parameters, and then follows one of several routes: repository catalog pages, per-repository partition walks, per-organization walks, or a direct path. It stamps records with organization or repository context where needed, enriches user records when possible, and yields lists of records or structured stream pages.

**Call relations**: This method is called by `paginate_source`. It calls `_iter_granted_org_repo_pages` for repositories, builds a PartitionWalk for repository-scoped streams, calls `_iter_user_orgs` for organization-scoped streams, uses `_paginate_link_header` for GitHub page following, and calls `_enrich_users` for the users stream.

*Call graph*: calls 4 internal fn (_enrich_users, _iter_granted_org_repo_pages, _iter_user_orgs, _paginate_link_header); called by 1 (paginate_source); 4 external calls (__init__, Semaphore, astimezone, with_context).


##### `GitHubConnector.paginate.repos`  (lines 286–288)

```
async def repos() -> AsyncIterator[str]
```

**Purpose**: Provides the repository names that a repository-scoped sync should visit. It turns discovered owner/repository pairs into `owner/repo` strings.

**Data flow**: It reads repository identities from `_iter_user_repos`. For each owner and repository name, it combines them into a single repository key string and yields that key to the partition walker.

**Call relations**: This small inner helper is created inside `paginate` when a stream needs to be fetched once per repository. PartitionWalk calls on it to know which repository partitions exist.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector.paginate.repo_pages`  (lines 290–291)

```
def repo_pages(repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Creates the page iterator for one repository during a partitioned walk. It binds together the current stream, path, repository key, and resume boundary.

**Data flow**: It receives a repository key and a PartitionBound, which describes where this repository’s sync should resume or stop. It passes those values into `_repo_pages`, which performs the actual GitHub requests and page shaping.

**Call relations**: This inner helper is handed to PartitionWalk from `paginate`. PartitionWalk calls it for each repository partition whenever it needs that repository’s bounded slice of records.

*Call graph*: calls 1 internal fn (_repo_pages).


##### `GitHubConnector._repo_pages`  (lines 336–407)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Fetches pages for one repository and applies the correct resume rules for that stream. It knows how to ask GitHub for updated records, newest commits, events, and other repository-scoped data without losing records between runs.

**Data flow**: It receives the client, stream, path template, repository key, and resume bound. It fills in the owner and repository in the path, builds query parameters such as `since` or `until`, fetches GitHub pages, filters issues so pull requests are not mixed into the issues stream, optionally filters newest-first event pages client-side, stamps landed records with `repo_full_name`, computes page cursor bounds, and yields WalkPage objects. If a repository is unavailable with known skip statuses, it raises PartitionSkipped instead of failing the whole sync.

**Call relations**: PartitionWalk reaches this through the `repo_pages` inner helper in `paginate`. It uses `_paginate_link_header` to fetch API pages, `_cursor_bounds` to report high and low cursor values, and `with_context` to add repository context before handing pages back to the partition walk.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); called by 1 (repo_pages); 3 external calls (__init__, __init__, with_context).


##### `GitHubConnector._iter_user_repos`  (lines 409–417)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: Discovers repositories that belong to the organizations available to the credential. It deliberately avoids GitHub’s broader user repository list so personal, collaborator, archived, and forked repositories do not sneak in.

**Data flow**: It reads pages from `_iter_granted_org_repo_pages`. For each repository record, it extracts an owner and repository name using `_repo_identity`; valid identities are yielded as `(owner, repo)` pairs.

**Call relations**: The `repos` helper inside `paginate` calls this when a repository-scoped stream needs partitions. This function depends on organization repository pages and hands clean repository identities to the partition walk.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (repos).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 419–438)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: Lists repository pages for every organization the credential can see, keeping only repositories the connector wants to sync. It skips archived repositories and forks.

**Data flow**: It first gets organization logins from `_iter_user_orgs`. For each organization, it requests `/orgs/{org}/repos` through `_paginate_link_header`, filters out records marked archived or fork, and yields the organization login with each non-empty filtered page. If one organization refuses access with expected statuses, it skips that organization and continues.

**Call relations**: This function supports both the repositories stream in `paginate` and repository discovery in `_iter_user_repos`. It sits between organization enumeration and per-repository syncing.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, paginate).


##### `GitHubConnector._iter_user_orgs`  (lines 440–461)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Finds the organizations visible to the GitHub credential. This is the root discovery step because most runnable streams are reached through organizations.

**Data flow**: It requests `/user/orgs` through `_paginate_link_header`, reads each record’s `login`, and yields valid organization names. If GitHub returns a root 403 permission error, it raises StreamSkipped so the run records a skipped stream rather than a broken sync.

**Call relations**: Organization-scoped work in `paginate` and repository discovery in `_iter_granted_org_repo_pages` both call this. Because it is the starting point for fan-out, a refusal here means the connector cannot safely read any organization-based stream.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, paginate).


##### `GitHubConnector._enrich_users`  (lines 463–483)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: Turns simple organization member records into fuller public GitHub user records when GitHub allows it. This can add useful public fields such as name or email.

**Data flow**: It receives one page of member records and a semaphore, which is a limit that stops too many user-detail requests from running at once. It launches one enrichment task per member, waits for all of them with `asyncio.gather`, and returns a new list where each member is replaced by fuller user data when available.

**Call relations**: The main `paginate` method calls this only for the `users` stream. It uses the inner `one` helper for each member and hands the enriched page back to the normal page-yielding flow.

*Call graph*: called by 1 (paginate); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 469–481)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Fetches the detailed public profile for one GitHub member, if the member has a usable login. If the profile cannot be found, it keeps the original member record.

**Data flow**: It receives a single member record from the surrounding `_enrich_users` function. It reads the `login`; if missing, it returns the original record. Otherwise it waits for permission from the semaphore, requests `/users/{login}`, parses the JSON body, and returns that body if it is a dictionary. A 404 response falls back to the original member.

**Call relations**: This inner helper is run many times in parallel by `_enrich_users`. It performs the per-user API request that lets the users stream contain richer records.


##### `GitHubConnector._paginate_link_header`  (lines 485–493)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Follows GitHub’s standard pagination style, where the next page is advertised in an HTTP `Link` header. It returns each page as a list of record dictionaries.

**Data flow**: It receives a client, path, and optional query parameters. It delegates to the base link-header pagination helper with GitHub’s page size and `_parse_records` as the response parser, then yields each parsed page. Empty responses and GitHub’s temporary empty stats responses simply produce no records.

**Call relations**: Most fetching helpers use this as their low-level page reader: organization listing, organization repositories, repository pages, and direct stream pagination. It keeps GitHub pagination rules in one place.

*Call graph*: called by 4 (_iter_granted_org_repo_pages, _iter_user_orgs, _repo_pages, paginate).


##### `_partition_field`  (lines 496–504)

```
def _partition_field(path: str) -> str | None
```

**Purpose**: Decides which context field should be present on records fetched from a given API path. Repository paths use `repo_full_name`, organization paths use `org_login`, and unscoped paths use no partition field.

**Data flow**: It receives an API path template. If the template contains a repository placeholder, it returns the repository partition field name; if it contains an organization placeholder, it returns the organization partition field name; otherwise it returns nothing.

**Call relations**: `GitHubConnector.flatten` calls this before scoping a record’s primary key. This helper is what connects the path fan-out shape to the key-safety behavior.

*Call graph*: called by 1 (flatten).


##### `_parse_records`  (lines 507–511)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Converts a GitHub HTTP response into the list of records expected by the pagination code. GitHub list endpoints usually return a JSON array.

**Data flow**: It receives an HTTP response. If the body is empty, it returns an empty list. Otherwise it parses JSON and returns it only if it is a list; non-list JSON is treated as no records.

**Call relations**: This parser is supplied to the base link-header pagination helper by `_paginate_link_header`. It keeps non-list or empty GitHub responses from confusing the rest of the connector.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 514–529)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: Extracts a reliable `(owner, repository)` identity from a GitHub repository record. It supports several shapes GitHub records may use.

**Data flow**: It first tries the `full_name` field, which usually looks like `owner/repo`. If that is not usable, it tries the nested owner login plus the repository name. If the owner is missing but a fallback organization was provided, it uses that fallback. If none of these produce both parts, it returns nothing.

**Call relations**: `_iter_user_repos` calls this while turning repository records into repository partitions. Its output becomes the repository key used by per-repository streams.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 532–542)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: Finds the newest and oldest cursor values on a page. Cursor values are timestamps or similar fields used to resume a sync without starting from the beginning.

**Data flow**: It receives a page of records and the cursor field path. If there is no cursor field, it returns two empty values. Otherwise it reads that field from each record, keeps string values, and returns the maximum and minimum values found.

**Call relations**: `_repo_pages` calls this to tell PartitionWalk what time range a GitHub page covered. That information lets the partition walk update watermarks and decide when a newest-first backfill has gone far enough.

*Call graph*: called by 1 (_repo_pages); 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/jira.py`

`io_transport` · `sync run`

This file is a read-only bridge between Jira Cloud and the rest of the system. Jira data lives behind Atlassian’s web API, and one account may have access to several Jira sites. The connector first asks Atlassian which sites the account can reach, then visits each site and downloads the requested kind of data.

The main class, `JiraConnector`, follows the shared `RestConnector` pattern used by source connectors. Its `paginate` method acts like a switchboard: when the sync runner asks for “issues” or “projects,” it sends the request to the right helper. Most Jira lists come back in pages, like a long notebook split into batches of 100 entries. `_offset_values` keeps asking for the next page until Jira says there is no more.

Issues, comments, and sprints support incremental syncing. That means the connector can ask only for items updated after the last saved time, instead of rereading everything every run. If Jira says the account is not allowed to read something, the connector marks that stream as skipped rather than treating the whole run as broken.

The file also makes Jira records easier for humans to read. Raw issue descriptions and comments can be stored as Atlassian Document Format trees, which are nested structures. `_doc_text` walks those trees and extracts plain text, so the final pages contain useful summaries instead of raw JSON.

#### Function details

##### `JiraConnector.paginate`  (lines 73–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading a Jira stream. Given a stream name, it chooses the correct helper to fetch pages of records from Jira.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor value. It checks the stream name, calls the matching reader such as `_issues` or `_projects`, and yields each page it gets back. If Jira returns a permission-related error, it turns that into a `StreamSkipped` result so the sync can continue cleanly.

**Call relations**: The sync framework calls this when it wants records for one Jira stream. `paginate` then hands off to `_projects`, `_issues`, `_comments`, `_users`, `_boards`, or `_sprints`. If the stream is unknown, or Jira refuses access with a 401 or 403 status, it raises `StreamSkipped` to tell the wider run this stream should be recorded as skipped rather than failed.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 106–110)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Jira sites the connected account can access. Jira Cloud uses a separate site identifier, called a cloud ID, and later requests need that ID in the URL.

**Data flow**: It sends a request to Atlassian’s accessible-resources endpoint. It reads the JSON response, makes sure it is treated as a list even if the response is empty or oddly shaped, and returns a list of site records. Each usable record may contain an `id` for the cloud ID and a site URL.

**Call relations**: `_projects`, `_issues`, `_users`, and `_boards` call this before fetching their own data. Those stream readers use the returned site list as their map of places to visit.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 112–132)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira API lists that are split into numbered pages. It hides the repeated “ask for the next 100 records” loop from the stream-specific methods.

**Data flow**: It receives an HTTP client, a Jira API path, optional query parameters, and the JSON key where records are stored. It repeatedly sends requests with `startAt` and `maxResults`, extracts the record list from the response, yields each non-empty page, and stops when Jira says it is the last page or no more records remain.

**Call relations**: The stream readers for projects, issues, comments, boards, and sprints rely on this helper whenever Jira uses its usual paged response format. It calls `records_at` to pull the actual list out of Jira’s response envelope, then gives complete pages back to the caller.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 134–141)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This downloads Jira projects from every accessible Jira site. A project is the container that groups related issues in Jira.

**Data flow**: It first gets the accessible sites from `_sites`. For each site with a valid cloud ID, it builds the project-search API path, reads all pages through `_offset_values`, and adds context such as the cloud ID and site URL to every project record before yielding it.

**Call relations**: `paginate` calls this when the requested stream is `projects`. This function depends on `_sites` to know which Jira sites to visit and `_offset_values` to walk through Jira’s paged project list. It uses `with_context` so later steps know which site each project came from.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 143–155)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This downloads Jira issues, optionally only those updated after a saved point in time. Issues are the main work items in Jira, such as bugs, tasks, or stories.

**Data flow**: It receives an optional cursor, which is the last known update time. It builds a Jira Query Language filter, or JQL, which is Jira’s search syntax, ordering results by update time. It visits each accessible site, requests issue pages with selected fields, adds site context to each issue, and yields the pages.

**Call relations**: `paginate` calls this for the `issues` stream, and `_comments` also calls it when it needs to find issues before reading their comments. This function calls `_sites` to find each Jira site and `_offset_values` to page through search results, then uses `with_context` to carry cloud and site information forward.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 157–177)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This downloads comments for Jira issues. Because Jira comments belong to issues, it first has to find the issues and then ask for comments issue by issue.

**Data flow**: It reads all issues by calling `_issues` without an issue cursor, then looks at each issue’s ID and cloud ID. For each valid issue, it requests the issue’s comment pages. If a comment cursor was supplied, it keeps only comments updated after that cursor, adds context such as issue ID and issue key, and yields the remaining comments.

**Call relations**: `paginate` calls this for the `issue_comments` stream. `_comments` depends on `_issues` to provide the list of issues to inspect, and on `_offset_values` to read each issue’s paged comments. It uses `with_context` so each comment remains tied to the Jira site and issue it came from.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 179–190)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This downloads Jira users from each accessible site. Jira’s user search endpoint returns a plain list, so this method is simpler than the paged readers.

**Data flow**: It asks `_sites` for the accessible Jira sites. For each valid cloud ID, it calls Jira’s user search endpoint once with a maximum page size, reads the JSON array, normalizes it into a list, adds site context, and yields the users if any are present.

**Call relations**: `paginate` calls this when the stream is `users`. It uses `_sites` to know which Jira sites to query, `list_or_empty` to safely interpret the response as a list, and `with_context` to preserve the site each user record came from.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 192–199)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This downloads Jira Agile boards from every accessible site. Boards are the Jira views teams use to organize work, often for Scrum or Kanban.

**Data flow**: It gets the accessible sites from `_sites`. For each site with a valid cloud ID, it builds the Agile board API path, reads all board pages through `_offset_values`, attaches the cloud ID and site URL, and yields those board records.

**Call relations**: `paginate` calls this for the `boards` stream, and `_sprints` calls it because sprints belong to boards. It uses `_sites` to find sites, `_offset_values` to read paged board results, and `with_context` to keep site information attached.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 201–215)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This downloads sprints from Jira boards, optionally only those updated after a saved time. A sprint is a time-boxed work period used by many agile teams.

**Data flow**: It first reads boards by calling `_boards`. For each board with a valid ID and cloud ID, it asks Jira for that board’s sprint pages. If a cursor exists, it filters out sprints whose `updatedDate` is not newer, then adds context such as the cloud ID and board ID before yielding the sprint records.

**Call relations**: `paginate` calls this for the `sprints` stream. `_sprints` relies on `_boards` to discover where sprints can be found, and on `_offset_values` to page through each board’s sprint list. It uses `with_context` so each sprint stays linked to its board.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 217–224)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes records just enough for the sync system to track updates consistently. In particular, Jira issue update times live inside a nested `fields` object, but the stream expects `updated` at the top level.

**Data flow**: It receives a record and its stream description. If the stream is `issues`, it safely reads the nested `fields` dictionary and returns a copy of the record with a top-level `updated` value. For all other streams, it returns the record unchanged.

**Call relations**: This is a connector hook used after records are fetched and before the sync system stores or advances cursors. It calls `_dict_or_empty` so a missing or malformed `fields` value does not crash the transformation.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 226–252)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Jira records into readable text pages. Instead of exposing raw Jira JSON, it creates a title and body that a person can search and understand.

**Data flow**: It receives a record and stream description. For issues, it extracts the summary, status, priority, assignee, reporter, and description text. For issue comments, it extracts the author and comment body. It returns a pair: a short title and a markdown-like text body with a heading.

**Call relations**: This is the connector’s presentation step for records that need special human-friendly text. It calls `_dict_or_empty`, `_str`, `_person`, `_field_line`, and `_doc_text` to safely pull useful values from Jira’s nested data. For streams other than issues and comments, it hands rendering back to the parent `RestConnector` behavior.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 255–256)

```
def _str(value: Any) -> str
```

**Purpose**: This small helper safely turns a value into a string only when it already is one. It prevents accidental display of non-text values as confusing text.

**Data flow**: It receives any value. If the value is a string, it returns that string. Otherwise it returns an empty string.

**Call relations**: `JiraConnector.render` uses this while building issue text, and `_person` uses it when reading names or email addresses. It is a guardrail around Jira fields that may be missing or shaped differently than expected.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 259–260)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This helper safely treats a value as a dictionary only when it really is one. It avoids errors when Jira returns missing fields or unexpected shapes.

**Data flow**: It receives any value. If the value is a dictionary, it returns it. Otherwise it returns an empty dictionary, giving callers a safe object to read from.

**Call relations**: `flatten`, `render`, and `_person` call this before reading nested Jira data. It lets those functions ask for fields without first repeating the same type checks.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 263–265)

```
def _person(value: Any) -> str
```

**Purpose**: This extracts a readable person label from a Jira user object. It prefers the display name and falls back to the email address.

**Data flow**: It receives a value that might be a Jira person record. It safely treats it as a dictionary, reads `displayName` and `emailAddress` as strings, and returns the first non-empty one. If neither is available, it returns an empty string.

**Call relations**: `JiraConnector.render` calls this when showing an issue assignee, issue reporter, or comment author. `_person` relies on `_dict_or_empty` and `_str` so incomplete Jira user objects do not break rendering.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 268–269)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: This formats one labeled line of issue metadata, such as `Status: Done`. It leaves the line out entirely when there is no value to show.

**Data flow**: It receives a label and a text value. If the value is non-empty, it returns `label: value`; if the value is empty, it returns an empty string.

**Call relations**: `JiraConnector.render` uses this while building the compact metadata block for issues. The render method then joins only the non-empty lines, so missing Jira fields do not create blank or misleading entries.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 272–289)

```
def _doc_text(value: Any) -> str
```

**Purpose**: This extracts plain readable text from Atlassian Document Format, the nested structure Jira uses for rich descriptions and comment bodies. It is like walking through a folder tree and collecting every note that contains actual words.

**Data flow**: It receives any value, usually a nested dictionary or list from Jira. It creates an empty list of text chunks, walks through the structure, collects every string stored under a `text` key, joins the collected pieces with newlines, trims extra space, and returns the final plain text.

**Call relations**: `JiraConnector.render` calls this for issue descriptions and comment bodies. Inside `_doc_text`, the nested `walk` helper performs the recursive search through dictionaries and lists.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 277–286)

```
def walk(node: Any) -> None
```

**Purpose**: This inner helper does the actual tree-walking for `_doc_text`. It searches through nested Jira document nodes and collects text leaves.

**Data flow**: It receives one node from the document structure. If the node is a dictionary, it saves its `text` value when that value is a string, then visits each child in `content`. If the node is a list, it visits each item. It does not return a value; it changes the surrounding `chunks` list by adding found text.

**Call relations**: `_doc_text` calls `walk` to process the original value and any nested children. The collected text is then joined by `_doc_text` and returned to `JiraConnector.render` as readable issue or comment content.


### `extensions/sources/ufo_ext_sources/linear.py`

`io_transport` · `source sync`

Linear is a project and issue tracker, and it exposes its data through GraphQL, a web API style where the caller asks for exactly shaped data. This file is the read-only bridge between Linear and UFO’s source-sync framework. Without it, the system would not know which Linear objects exist, how to ask Linear for them, how to page through long result lists, or how to turn important records into readable text.

The file first defines the list of Linear “streams,” meaning named collections like issues, projects, comments, users, teams, and labels. Some streams can be synced incrementally: after the first run, the connector asks Linear only for records updated since the last saved timestamp. A few Linear collections do not support that kind of date filter, so they are fully read again each run.

Most of the file is GraphQL query text. Each query says which fields to fetch for one stream and includes paging information, like a bookmark in a long book. `LinearConnector.paginate` sends one request, yields the records it got, then follows Linear’s `endCursor` bookmark until there are no more pages. If Linear refuses access with HTTP 401 or 403, the stream is marked skipped instead of pretending it succeeded. If GraphQL reports errors, the sync stops loudly to avoid saving partial or misleading data.

Finally, `render` makes key records human-readable. For example, an issue becomes a title plus state, priority, assignee, and description rather than a raw data dump.

#### Function details

##### `_stream`  (lines 32–42)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard description of one Linear collection that the sync framework can read. It records the stream name, which timestamp field should be used for incremental syncing, and whether the stream is one of the main searchable content streams.

**Data flow**: It receives a Linear stream name plus optional choices about its cursor field and canonical status. It fills in the common Linear timestamp fields, builds a `StreamSpec` object, and returns that object for the connector’s stream list.

**Call relations**: This helper is used while the module is being loaded to build `LINEAR_STREAMS`. It hands the completed stream descriptions to the connector class, which later uses them when deciding what to sync and how to advance each stream’s saved cursor.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 269–312)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Linear stream from the GraphQL API, one page at a time. It is the main download loop for Linear data, including incremental syncing when Linear supports filtering by `updatedAt`.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor timestamp from a previous run. It looks up the GraphQL query for that stream, adds an `updatedAt` filter when allowed, sends requests to Linear, checks for access refusal or GraphQL errors, extracts the `nodes` list from each response, and yields each non-empty page of records. It also follows Linear’s `pageInfo.endCursor` until Linear says there are no more pages.

**Call relations**: The source-sync framework calls this when it wants records for a Linear stream. Inside the loop it uses the shared REST connector’s POST behavior to contact Linear, uses `list_or_empty` to safely normalize the returned node list, and raises `StreamSkipped` when Linear says the token is invalid or lacks permission, so the larger run can record a skip instead of crashing the whole connector unnecessarily.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 314–353)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns important Linear records into readable text for storage or search. Instead of keeping only raw GraphQL-shaped data, it creates page-like prose for issues, projects, comments, and users.

**Data flow**: It receives one record dictionary and its stream description. Depending on the stream name, it pulls out friendly fields such as issue title, project name, state, priority, assignee, target date, comment body, user name, and email. It builds a title and a Markdown-like body string, then returns both. For streams it does not specially understand, it falls back to the base connector’s rendering behavior.

**Call relations**: This is called after records have been fetched and need to become recallable content. It relies on `_str` to safely read text fields, `_ref_id` to pull IDs out of nested reference objects, and `_labeled` to format small blocks of metadata before handing the finished title and body back to the broader source framework.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 356–357)

```
def _str(value: Any) -> str
```

**Purpose**: Safely converts a value into text only when it is already a string. It prevents `None`, numbers, or nested objects from leaking into rendered prose where a plain text field is expected.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged; otherwise it returns an empty string.

**Call relations**: This small helper is used by `LinearConnector.render` whenever it reads optional text from a Linear record. `_ref_id` also uses it after pulling an `id` field from a nested object, so ID extraction follows the same safe text rule.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 360–361)

```
def _ref_id(value: Any) -> str
```

**Purpose**: Extracts the `id` from a nested Linear reference, such as an assignee or project lead. It gives rendering code a simple way to show linked object IDs without assuming the nested value is always present.

**Data flow**: It receives any value. If the value is a dictionary, it reads its `id` field and passes that through `_str`; if not, it returns an empty string. The result is always safe plain text.

**Call relations**: This helper is called by `LinearConnector.render` when building metadata for issues and projects. It delegates the final string check to `_str`, keeping the rendering path tolerant of missing or oddly shaped API data.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 364–365)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: Formats a short list of metadata fields into readable `label: value` lines. Empty values are skipped, so rendered pages do not fill up with blank labels.

**Data flow**: It receives a list of label-and-value pairs. It keeps only the pairs whose value is not empty, turns each into a line like `state: completed`, joins the lines with newlines, and returns the resulting text block.

**Call relations**: This helper is used by `LinearConnector.render` to build the small metadata sections for issues, projects, and users. It turns the individual safe strings prepared by `_str` and `_ref_id` into a compact human-readable block.

*Call graph*: called by 1 (render).


### Operations monitoring and incidents
Connectors that ingest incident-management, on-call, service, error-monitoring, and release data for operational awareness.

### `extensions/sources/ufo_ext_sources/pagerduty.py`

`io_transport` · `source sync`

PagerDuty is an external service, so the system needs a careful reader that knows PagerDuty’s API rules. This file is that reader. It defines the PagerDuty streams the project can import, including users, teams, services, incidents, incident notes, escalation policies, schedules, and on-calls. Each stream says where the records live in PagerDuty’s response and which field uniquely identifies a record.

The connector mostly reads lists from PagerDuty’s REST API, which is a web API accessed over HTTP. PagerDuty sends large lists in pages, like a book split into chapters. The connector asks for one page at a time, follows PagerDuty’s “more pages exist” signal, and yields batches of records as they arrive.

Incidents get special treatment because they can be synced incrementally. A cursor, meaning “the last timestamp we already saw,” lets the connector ask only for newer incident updates. Incident notes are also special: PagerDuty exposes notes under each incident, so the connector first reads incidents, then asks for notes for each incident and adds the incident ID as context.

If PagerDuty refuses access with a 401 or 403 status, the connector marks that stream as skipped instead of crashing the whole run. This matters because a token may not have permission for every PagerDuty area. The connector only reads; it does not write back to PagerDuty.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 74–77)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to PagerDuty and adds the PagerDuty-specific Accept header. That header tells PagerDuty which version of its API response format the connector expects.

**Data flow**: It receives a base URL and a credential object. It first lets the shared REST connector build the normal client, then adds PagerDuty’s required media-type header to the client’s outgoing requests. It returns the prepared asynchronous HTTP client, ready for later API calls.

**Call relations**: This is part of the connector setup before any stream is read. The common REST connector does the generic client creation, and this function adds the PagerDuty-specific finishing touch so later pagination calls speak the right API dialect.


##### `PagerDutyConnector._offset_pages`  (lines 79–99)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one PagerDuty list endpoint page by page. It is the common path for streams that use PagerDuty’s offset-and-limit pagination, where each request asks for the next slice of a larger list.

**Data flow**: It receives an HTTP client, a stream description, optional query parameters, and an optional cursor timestamp. It asks PagerDuty for pages under the stream’s API object name, using a limit of 100 records at a time. If a cursor and cursor field are present, it keeps only records newer than that cursor. It yields non-empty batches of records to the caller.

**Call relations**: This is the shared page-reading helper. The incident reader uses it after adding incident-specific sorting and cursor parameters, and the main paginate function uses it directly for simpler streams such as users, teams, services, schedules, and on-calls.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 101–113)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads PagerDuty incidents in update-time order so the sync can resume from where it left off. It exists because incidents support incremental syncing through their updated_at timestamp.

**Data flow**: It receives an HTTP client and an optional cursor. It builds request parameters that sort incidents by updated_at from oldest to newest, and if a cursor is present it sends that as PagerDuty’s since value. It then delegates the actual page fetching to _offset_pages and yields each resulting batch of incident records.

**Call relations**: The main paginate function calls this when the selected stream is incidents. The incident-notes reader also calls it, because notes are discovered by first walking through incidents and then asking PagerDuty for each incident’s notes.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 115–128)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads notes attached to PagerDuty incidents. PagerDuty does not expose these as one simple global list here, so the connector must visit incidents first and then fetch notes for each one.

**Data flow**: It receives an HTTP client and an optional note cursor. It reads incidents, extracts each valid incident ID, requests that incident’s notes endpoint, and pulls the notes list out of the response. If a cursor is present, it keeps only notes whose created_at timestamp is newer. Before yielding notes, it adds the incident ID as extra context so downstream code knows which incident each note belongs to.

**Call relations**: The main paginate function calls this for the incident_notes stream. Inside, it relies on _incidents to find incidents, records_at to extract the notes array from PagerDuty’s response, and with_context to attach the parent incident ID to each note batch.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 130–164)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function is the traffic director for reading any PagerDuty stream. Given a stream name, it chooses the correct reading strategy and turns PagerDuty API responses into batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. For incidents, it sends the work to _incidents. For incident notes, it sends the work to _incident_notes. For the standard list streams, it sends the work to _offset_pages. It yields batches from whichever helper is appropriate. If the stream is unknown, or if PagerDuty refuses access with a 401 or 403 response, it raises StreamSkipped so the larger sync can record a skip instead of treating the whole run as broken.

**Call relations**: This is the connector method the broader source-sync framework calls when it wants records for a PagerDuty stream. It hands off to the specialized helper for the chosen stream, and it wraps PagerDuty permission failures in a clear skip signal for the rest of the system.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/sentry.py`

`io_transport` · `source sync`

This connector is the bridge between this project and Sentry’s web API. Without it, the system would not know how to ask Sentry for useful records, how to follow Sentry’s page-by-page results, or how to remember which organization and project each record came from.

Sentry returns large lists in pages, like a long report split across many sheets. The next sheet is not found by guessing a page number; it is described in a special HTTP header called Link, which can contain a cursor. A cursor is a marker that says “continue from here.” This file reads that marker and keeps requesting pages until Sentry says there are no more.

The main class, SentryConnector, exposes several named streams. Some are simple, such as organizations and projects. Others need a first step: to fetch members or releases, it first finds organizations; to fetch issues or events, it first finds projects. When records are returned, the connector adds context such as organization_slug and project_slug, so later readers can tell where each item belongs.

The connector only reads from Sentry. It does not create or update Sentry data. If Sentry refuses access because the token is missing permissions or invalid, the connector skips that stream cleanly instead of crashing the whole sync.

#### Function details

##### `_sentry_next_cursor`  (lines 76–81)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper looks at Sentry’s response headers and extracts the cursor for the next page of results, if one exists. It is what lets the connector continue through a long Sentry list without missing later pages.

**Data flow**: It receives HTTP headers from a Sentry response. It looks for a Link header, searches that text for Sentry’s “next page with results” marker, and returns the cursor string if it finds one. If there is no Link header or no usable next cursor, it returns nothing.

**Call relations**: The page-reading loop in SentryConnector._paged_list calls this after every Sentry response. _paged_list then uses the returned cursor to decide whether to request another page or stop.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.paginate`  (lines 89–128)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading one Sentry stream. Given a stream name such as organizations, issues, or releases, it chooses the right helper and yields batches of records to the wider sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks which Sentry stream was requested, calls the matching helper, applies cursor filtering where needed, and yields lists of records. If the stream is unknown or Sentry refuses access with a permission-related error, it turns that into a clean “stream skipped” result.

**Call relations**: The broader source framework calls this when it wants records from Sentry. paginate then hands the work to _organizations, _projects, _members, _issues, _events, or _releases depending on the stream. It is also the place where permission failures are translated into StreamSkipped so the rest of the run can continue.

*Call graph*: calls 7 internal fn (__init__, _events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._paged_list`  (lines 130–147)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads a list endpoint from Sentry until all pages have been fetched. It is the shared page-turning machinery used by every stream in this connector.

**Data flow**: It starts with an API path and optional query parameters. It requests that path, turns the JSON response into a list of dictionary-like records, yields any records it found, then checks the response headers for the next cursor. If there is a next cursor, it repeats the request with that cursor; otherwise it stops.

**Call relations**: All the stream-specific helpers call _paged_list so they do not each need to know Sentry’s pagination rules. After every request, _paged_list calls _sentry_next_cursor to learn whether there is another page.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 149–153)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This reads all organizations visible to the Sentry credential. Organizations are the top-level containers needed before the connector can fetch organization-based data such as members and releases.

**Data flow**: It receives the HTTP client, asks Sentry’s organizations endpoint for every page, and collects all organization records into one list. The result is a single list of organization dictionaries.

**Call relations**: paginate calls this directly for the organizations stream. _members and _releases also call it first because they need each organization’s slug before they can ask Sentry for members or releases inside that organization.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, paginate).


##### `SentryConnector._projects`  (lines 155–159)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This reads all projects visible to the Sentry credential. Projects are needed as starting points for project-specific data such as issues and events.

**Data flow**: It receives the HTTP client, asks Sentry’s projects endpoint page by page, and collects all project records into one list. The result is a list of project dictionaries, each expected to include enough organization and project identity to build later API paths.

**Call relations**: paginate calls this directly for the projects stream. _issues and _events also call it first because Sentry’s issue and event endpoints are addressed through a specific organization and project.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, paginate).


##### `SentryConnector._members`  (lines 161–167)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads organization member records from Sentry. It first finds the organizations the credential can see, then fetches members for each one.

**Data flow**: It receives the HTTP client, gets the organization list, and for each organization with a usable slug, requests that organization’s members page by page. Before yielding each page, it adds the organization_slug to every member record so the record keeps its source context.

**Call relations**: paginate calls _members when the members stream is requested. _members depends on _organizations to know which organizations to visit, uses _paged_list to read each members endpoint, and uses with_context to attach the organization slug before handing records back.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 169–183)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads issue records for every visible Sentry project. Issues are Sentry’s grouped error or problem reports, and the optional cursor lets the connector ask mostly for recently seen issues during later syncs.

**Data flow**: It receives the HTTP client and an optional cursor. It gets all projects, pulls out each project’s organization slug and project slug, and skips projects that do not have both. For each valid project, it requests the project’s issues, adding a lastSeen filter when a cursor is available. Each returned issue is stamped with its organization_slug and project_slug before being yielded.

**Call relations**: paginate calls _issues for the issues stream. _issues calls _projects to discover where to look, _paged_list to read each project’s issues endpoint, and with_context to preserve the project and organization identity on the outgoing records.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 185–199)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads event records for every visible Sentry project. Events are individual occurrences, and the cursor is used to ask Sentry for events newer than the last synced timestamp.

**Data flow**: It receives the HTTP client and an optional cursor. It gets the project list, extracts the organization and project slugs, and skips incomplete project records. For each valid project, it requests events from Sentry, adding an event timestamp filter when a cursor is present. It then adds organization_slug and project_slug to each event page before yielding it.

**Call relations**: paginate calls _events for the events stream. _events uses _projects to find project locations, _paged_list to walk through Sentry’s event pages, and with_context so downstream storage can tell which project each event came from.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 201–212)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads release records for every visible Sentry organization. Releases describe shipped versions of software, and the cursor lets the connector keep only releases created after a previous sync point.

**Data flow**: It receives the HTTP client and an optional cursor. It gets all organizations, skips any without a valid slug, and reads that organization’s releases page by page. If a cursor is present, it filters out releases whose dateCreated is not newer than the cursor. It then adds organization_slug and yields only non-empty pages.

**Call relations**: paginate calls _releases for the releases stream. _releases calls _organizations to know which organizations to scan, _paged_list to read each releases endpoint, and with_context to attach the organization slug before returning records.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).


### Team chat
Connectors that read workspace conversations, users, messages, threads, and participants from chat systems.

### `extensions/sources/ufo_ext_sources/slack.py`

`io_transport` · `source sync runs`

Slack does not hand over a whole workspace in one simple download. It gives data in pages, uses cursors to say “there is another page,” and sometimes reports errors inside a normal-looking HTTP response. This file is the Slack-specific adapter that knows those rules.

The connector offers five streams: users, conversations, conversation threads, messages, and message participants. Users and conversations are treated like snapshots: each sync lists what Slack currently exposes, so anything that disappears from that list can be marked as gone. Messages are trickier. Slack history is read one channel at a time, newest first, so the file uses a partitioned walk: imagine checking many mailboxes separately, remembering how far you got in each one so a busy mailbox does not make you skip a quiet one.

The file also reshapes Slack’s raw data into simpler records. User profiles are flattened, channel types are named consistently, message timestamps become readable dates, long text is shortened into snippets, and thread and participant records are derived from messages. It deliberately does not write anything back to Slack. Its job is only to read safely, skip streams or channels when permissions are missing, and preserve enough position information to continue later without losing messages.

#### Function details

##### `SlackApiError.__init__`  (lines 93–97)

```
def __init__(self, error: str, *, needed: str | None=None) -> None
```

**Purpose**: This creates a Slack-specific error object when Slack says a request failed even though the HTTP request itself may have looked successful. It keeps the short Slack error code, and optionally the missing permission scope, so later code can decide whether to skip or fail.

**Data flow**: It receives a Slack error name and an optional needed permission. It builds a readable error message, stores the error code and needed scope on the object, and produces an exception that can be raised and caught.

**Call relations**: When Slack responses are checked by _ok_or_raise, this constructor is used to turn Slack’s `ok=false` reply into a normal Python exception. Later connector code reads the stored error code to decide whether a missing permission should skip a stream or channel.

*Call graph*: called by 1 (_ok_or_raise).


##### `SlackConnector.paginate_source`  (lines 105–120)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the connector’s public paging entry point used by the wider source-sync framework. It passes Slack paging work to the connector’s main pagination method.

**Data flow**: It receives an HTTP client, a stream description, cursor information, the connector’s own Slack user id, and an optional backfill boundary. It forwards those inputs unchanged and yields whatever pages the main pagination method produces.

**Call relations**: The framework calls this when it wants records from a Slack stream. This method immediately hands the work to SlackConnector.paginate, keeping the interface expected by the shared connector base class.

*Call graph*: calls 1 internal fn (paginate).


##### `SlackConnector.paginate`  (lines 122–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None=None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]
```

**Purpose**: This decides how to read each Slack stream. It uses simple page-by-page listing for users and conversations, and a per-channel history walk for message-derived streams.

**Data flow**: It receives a stream request plus sync state such as a cursor and optional backfill date. For users or conversations, it yields listed pages directly. For messages, threads, and participants, it first builds a user lookup and a channel list, then uses a partitioned walk to read each channel’s history and yield stream pages. If a stream name is unknown, it reports that the stream is skipped.

**Call relations**: paginate_source calls this as the main dispatcher. It calls iter_users, iter_conversations, user_index, _slack_ts, and PartitionWalk to gather the right raw Slack pages and turn them into resumable stream pages.

*Call graph*: calls 5 internal fn (__init__, iter_conversations, iter_users, user_index, _slack_ts); called by 1 (paginate_source); 1 external calls (__init__).


##### `SlackConnector.paginate.partitions`  (lines 148–150)

```
async def partitions() -> AsyncIterator[str]
```

**Purpose**: This small inner generator supplies the list of Slack channel ids that message history should be read from. It lets the partition walker treat each channel as a separate work lane.

**Data flow**: It reads the already-built channel dictionary from the surrounding paginate call. It yields one channel id at a time and does not return a collected list.

**Call relations**: It is created inside SlackConnector.paginate when reading message-related streams. PartitionWalk uses it to know which channel partitions exist before asking for pages from each one.


##### `SlackConnector.paginate.channel_pages`  (lines 152–160)

```
def channel_pages(channel_id: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: This small inner function connects the generic partition walker to Slack’s channel-history reader. Given one channel and a time boundary, it asks for the right pages from that channel.

**Data flow**: It receives a channel id and a PartitionBound, which describes the time window to read. It looks up the channel details and passes them, along with the HTTP client, stream, user lookup, and self user id, into _channel_pages. The result is an async stream of walk pages for that channel.

**Call relations**: PartitionWalk calls this whenever it needs more data for a specific channel. It delegates the Slack-specific API request details to SlackConnector._channel_pages.

*Call graph*: calls 1 internal fn (_channel_pages).


##### `SlackConnector.iter_users`  (lines 177–193)

```
async def iter_users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Slack users in pages and normalizes each user into a simpler shape. It is used both to sync the users stream and to build a lookup table for message authors.

**Data flow**: It starts with no Slack cursor, asks `/api/users.list` for up to 200 users at a time, flattens valid member objects, yields each non-empty page, then follows Slack’s next cursor until there is no more data.

**Call relations**: SlackConnector.paginate calls it for the users stream. SlackConnector.user_index also calls it to build a user-id-to-user-record map used while processing messages.

*Call graph*: calls 3 internal fn (_enumerate, _flatten_user, _next_cursor); called by 2 (paginate, user_index).


##### `SlackConnector.iter_conversations`  (lines 195–235)

```
async def iter_conversations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Slack conversations, including channels, private channels, group messages, and direct messages, and converts them into consistent conversation records. It includes archived conversations so the system can notice when a conversation is archived rather than silently disappearing.

**Data flow**: It asks `/api/conversations.list` with paging options and conversation types. For each valid Slack channel object, it extracts identifiers, names, type flags, creation time, topic, purpose, and membership counts, then yields pages until Slack provides no next cursor.

**Call relations**: SlackConnector.paginate calls this for the conversations stream and again before reading message streams so it knows which non-archived channels can be walked for history.

*Call graph*: calls 5 internal fn (_enumerate, _conversation_type, _nested_value, _next_cursor, _unix_to_iso); called by 1 (paginate).


##### `SlackConnector.user_index`  (lines 237–244)

```
async def user_index(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: This builds a quick lookup table of Slack users by user id. Message processing uses it to attach names and emails to message authors when possible.

**Data flow**: It reads all user pages from iter_users. For every user with a string id, it stores that user record in a dictionary keyed by id, then returns the dictionary.

**Call relations**: SlackConnector.paginate calls this before walking message-related streams. The resulting lookup is passed into _channel_pages and then _message_page so message and participant records can include human-friendly user details.

*Call graph*: calls 1 internal fn (iter_users); called by 1 (paginate).


##### `SlackConnector._channel_pages`  (lines 246–298)

```
async def _channel_pages(self, client: httpx.AsyncClient, stream: StreamSpec, conversation: dict[str, Any], bound: PartitionBound, users: dict[str, dict[str, Any]], self_user_id: str | None) -> AsyncI
```

**Purpose**: This reads one Slack channel’s message history within the time window chosen by the partition walker. It is careful about Slack’s newest-first ordering so capped or resumed syncs do not drop messages.

**Data flow**: It receives one conversation, a stream type, a partition boundary, the user lookup, and the connector’s own Slack user id. It builds Slack history request parameters such as channel id, page size, cursor, oldest time, or latest time. It posts to `/api/conversations.history`, skips only that channel if Slack refuses it for known channel-level reasons, filters valid raw messages, converts each page with _message_page, and follows the next cursor until done.

**Call relations**: The inner channel_pages function in SlackConnector.paginate calls this for PartitionWalk. It calls _slack_post for the API request, _message_page to turn raw Slack messages into records, and _next_cursor to continue through Slack pages.

*Call graph*: calls 3 internal fn (_message_page, _slack_post, _next_cursor); called by 1 (channel_pages); 1 external calls (__init__).


##### `SlackConnector._message_page`  (lines 300–348)

```
def _message_page(self, stream: StreamSpec, conversation: dict[str, Any], raw_messages: list[dict[str, Any]], users: dict[str, dict[str, Any]], self_user_id: str | None) -> WalkPage
```

**Purpose**: This turns one raw Slack history page into the requested record type: messages, conversation threads, or message participants. It also reports the newest and oldest Slack timestamps on that page so the partition walker can remember progress.

**Data flow**: It receives raw Slack messages plus channel information, user details, and the stream being produced. It skips Slack deletion marker rows except to record deleted message ids for the messages stream, filters out the connector’s own live bot user, flattens valid messages, derives thread records and participant records, then returns a WalkPage with the appropriate records and timestamp span.

**Call relations**: SlackConnector._channel_pages calls this after each Slack history response. It uses _flatten_message, _conversation_thread_from_message, and _participant_for_message, then packages the result in a WalkPage for PartitionWalk to consume.

*Call graph*: calls 3 internal fn (_conversation_thread_from_message, _flatten_message, _participant_for_message); called by 1 (_channel_pages); 1 external calls (__init__).


##### `SlackConnector._enumerate`  (lines 350–370)

```
async def _enumerate(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This wraps top-level Slack listing calls, such as users and conversations, and treats missing permission as a skip rather than a crash. That matters because a workspace grant may not allow a whole stream to be read.

**Data flow**: It receives an HTTP client, an API path, and query parameters. It performs the Slack GET request, returns the decoded data if allowed, raises StreamSkipped for known permission refusals, and re-raises unexpected failures.

**Call relations**: iter_users and iter_conversations call this for their Slack list endpoints. It calls _slack_get, then translates Slack permission errors or HTTP 403 responses into a stream-level skip that the sync framework can record.

*Call graph*: calls 2 internal fn (__init__, _slack_get); called by 2 (iter_conversations, iter_users).


##### `SlackConnector._slack_get`  (lines 372–375)

```
async def _slack_get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This performs a Slack GET request and applies Slack’s special success check. Slack can return HTTP 200 while still saying `ok=false`, so this function makes sure that is treated as an error.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It uses the inherited REST GET helper to fetch data, passes the returned JSON-like dictionary through _ok_or_raise, and returns only confirmed successful data.

**Call relations**: SlackConnector._enumerate calls this for top-level list endpoints. It delegates the Slack-specific `ok` check to _ok_or_raise.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_enumerate).


##### `SlackConnector._slack_post`  (lines 377–380)

```
async def _slack_post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This performs a Slack POST request and applies the same Slack success check used for GET requests. It is used for conversation history because Slack’s history read is shaped as a POST here.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It sends the request through the inherited REST POST helper, checks the returned data with _ok_or_raise, and returns successful Slack data.

**Call relations**: SlackConnector._channel_pages calls this to read `/api/conversations.history`. It hands off response validation to _ok_or_raise so channel permission errors can be caught cleanly.

*Call graph*: calls 1 internal fn (_ok_or_raise); called by 1 (_channel_pages).


##### `_ok_or_raise`  (lines 383–388)

```
def _ok_or_raise(data: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This checks Slack’s `ok` field and turns Slack-declared failures into normal exceptions. It protects the rest of the code from accidentally treating a failed Slack response as usable data.

**Data flow**: It receives a decoded Slack response dictionary. If `ok` is explicitly false, it extracts the Slack error code and optional needed scope, raises SlackApiError, and otherwise returns the original data unchanged.

**Call relations**: SlackConnector._slack_get and SlackConnector._slack_post both call this after network requests. When it raises SlackApiError, higher-level code such as _enumerate or _channel_pages decides whether to skip or fail.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_slack_get, _slack_post).


##### `_next_cursor`  (lines 391–396)

```
def _next_cursor(data: dict[str, Any]) -> str | None
```

**Purpose**: This extracts Slack’s next-page token from a response. It is the small helper that lets the connector keep asking Slack for the next page until there are no more.

**Data flow**: It receives a Slack response dictionary. It looks inside `response_metadata.next_cursor`, returns the cursor if it is a non-empty string, and returns None otherwise.

**Call relations**: iter_users, iter_conversations, and _channel_pages call this after each Slack page. If it returns a cursor, they continue; if it returns None, that listing or history walk is finished.

*Call graph*: called by 3 (_channel_pages, iter_conversations, iter_users).


##### `_unix_to_iso`  (lines 399–406)

```
def _unix_to_iso(value: Any) -> str | None
```

**Purpose**: This converts Slack’s ordinary Unix time values into ISO date strings, which are easier for other systems to read consistently. Unix time means seconds since the start of 1970 UTC.

**Data flow**: It receives any value. It rejects booleans, tries to treat the value as seconds, converts valid seconds to a UTC ISO timestamp string, and returns None when the value cannot be converted.

**Call relations**: iter_conversations uses this for conversation creation times. _flatten_user uses it for user update times.

*Call graph*: called by 2 (iter_conversations, _flatten_user); 1 external calls (fromtimestamp).


##### `_slack_ts`  (lines 409–421)

```
def _slack_ts(value: datetime | None) -> str | None
```

**Purpose**: This converts a Python datetime into Slack’s message timestamp string format for history boundaries. It pads the number so string comparisons line up with Slack’s timestamp ordering.

**Data flow**: It receives an optional datetime. If there is no value, it returns None. If the time is before the Unix epoch, it returns None. Otherwise it turns the time into a fixed-width seconds-with-microseconds string.

**Call relations**: SlackConnector.paginate calls this when setting the floor for a backfill walk. The resulting string is passed into PartitionWalk so channel history reads stop at the intended oldest time.

*Call graph*: called by 1 (paginate); 1 external calls (timestamp).


##### `_slack_ts_to_iso`  (lines 424–430)

```
def _slack_ts_to_iso(value: str | None) -> str | None
```

**Purpose**: This converts Slack message timestamps, such as `1712345678.123456`, into readable UTC ISO date strings. It is used when creating message and thread records.

**Data flow**: It receives a Slack timestamp string or None. Empty values return None. Valid numeric strings are converted to UTC ISO strings, while invalid strings return None.

**Call relations**: _flatten_message calls this for a message’s sent time. _conversation_thread_from_message calls it for thread creation, update, and last-message times.

*Call graph*: called by 2 (_conversation_thread_from_message, _flatten_message); 1 external calls (fromtimestamp).


##### `_flatten_user`  (lines 433–460)

```
def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This turns Slack’s nested user object into one straightforward user record. It pulls useful profile fields, normalizes email casing, and chooses sensible display names.

**Data flow**: It receives one raw Slack user dictionary. It reads the nested profile if present, cleans the email, chooses the first available display and real name fields, converts update time, and returns a flat dictionary with user identity and profile details.

**Call relations**: SlackConnector.iter_users calls this for each member returned by Slack. It uses _first_text to choose names and _unix_to_iso to format the update timestamp.

*Call graph*: calls 2 internal fn (_first_text, _unix_to_iso); called by 1 (iter_users).


##### `_flatten_message`  (lines 463–504)

```
def _flatten_message(raw: dict[str, Any], *, conversation: dict[str, Any], users: dict[str, dict[str, Any]], self_user_id: str | None) -> dict[str, Any] | None
```

**Purpose**: This turns one raw Slack message into the system’s standard message record. It also filters out messages sent by the connector’s own Slack bot user so the sync does not index its own live surface messages.

**Data flow**: It receives a raw message, its conversation, the user lookup, and the connector’s own user id. It validates the message and channel timestamps, skips the self user, finds author details when available, chooses a thread id, formats sent time, creates a short snippet, and returns a flat message dictionary. If the message cannot be safely represented, it returns None.

**Call relations**: SlackConnector._message_page calls this for each raw history item. It uses _slack_ts_to_iso for dates, _snippet for preview text, and _first_text to choose the best author handle.

*Call graph*: calls 3 internal fn (_first_text, _slack_ts_to_iso, _snippet); called by 1 (_message_page).


##### `_conversation_thread_from_message`  (lines 507–537)

```
def _conversation_thread_from_message(message: dict[str, Any], *, raw: dict[str, Any], conversation: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: This derives a thread record from a Slack message when that message is part of a real thread. It ignores ordinary one-off messages that do not have replies and are not replies themselves.

**Data flow**: It receives a flattened message plus the original raw message and conversation. It checks whether the message is a thread root with replies or a reply inside a thread, calculates latest activity and counts when Slack provides them, and returns a thread dictionary. If the message does not represent a thread, it returns None.

**Call relations**: SlackConnector._message_page calls this after flattening each message. It uses _slack_ts_to_iso to format thread activity times before the page collects thread records.

*Call graph*: calls 1 internal fn (_slack_ts_to_iso); called by 1 (_message_page).


##### `_participant_for_message`  (lines 540–560)

```
def _participant_for_message(message: dict[str, Any], *, users: dict[str, dict[str, Any]]) -> dict[str, Any] | None
```

**Purpose**: This creates a participant record for the sender of a message. Participant records make it easier to answer questions like who took part in a message or thread.

**Data flow**: It receives a flattened message and the user lookup. It chooses a handle from the user email or Slack user id, returns None if no handle can be found, and otherwise creates a participant record tied to the message, channel, and thread.

**Call relations**: SlackConnector._message_page calls this for each flattened message. It uses _first_text to choose the best usable sender handle.

*Call graph*: calls 1 internal fn (_first_text); called by 1 (_message_page).


##### `_conversation_type`  (lines 563–570)

```
def _conversation_type(raw: dict[str, Any]) -> str
```

**Purpose**: This gives each Slack conversation a clear type name, such as direct message, multi-person direct message, private channel, or public channel. It hides Slack’s many boolean flags behind one simple label.

**Data flow**: It receives a raw Slack conversation dictionary. It checks Slack’s type flags in priority order and returns a single string describing the conversation type.

**Call relations**: SlackConnector.iter_conversations calls this while building normalized conversation records from Slack’s conversation list.

*Call graph*: called by 1 (iter_conversations).


##### `_nested_value`  (lines 573–579)

```
def _nested_value(raw: dict[str, Any], *path: str) -> Any
```

**Purpose**: This safely reads a value from inside nested dictionaries. It is used for Slack fields like a channel topic or purpose, where the useful text sits under another object.

**Data flow**: It receives a dictionary and a path of keys. It walks down the path one key at a time, returning None if the current value stops being a dictionary, and returning the final value if the path exists.

**Call relations**: SlackConnector.iter_conversations calls this to extract nested topic and purpose text without crashing when Slack omits or reshapes those fields.

*Call graph*: called by 1 (iter_conversations).


##### `_first_text`  (lines 582–586)

```
def _first_text(*values: Any) -> str | None
```

**Purpose**: This picks the first non-empty text value from a list of candidates. It is a simple way to choose the best available name or handle when Slack offers several possible fields.

**Data flow**: It receives any number of values. It returns the first value that is a string and still has content after trimming whitespace, or None if none qualify.

**Call relations**: _flatten_user uses this to choose display and real names. _flatten_message uses it for sender handles. _participant_for_message uses it to choose participant handles.

*Call graph*: called by 3 (_flatten_message, _flatten_user, _participant_for_message).


##### `_snippet`  (lines 589–593)

```
def _snippet(value: str | None) -> str | None
```

**Purpose**: This makes a short preview from message text. It removes messy spacing and cuts the result to the configured snippet length.

**Data flow**: It receives optional text. Empty input returns None. Non-empty text is split and rejoined with single spaces, then shortened to the snippet cap if needed.

**Call relations**: _flatten_message calls this while creating each message record, so stored messages can have a compact preview as well as the full text.

*Call graph*: called by 1 (_flatten_message).
