# Work management, knowledge, developer, and incident sources  `stage-15.1.2`

This stage is the system’s set of “adapters” for outside work and knowledge tools. It runs behind the scenes during syncing, when the product pulls information from other services so it can later search, remember, or summarize it. Each file knows how to speak one service’s web API, meaning its online interface for reading data.

The Git connector treats Markdown files in a GitHub repository as pages, checking for changes before downloading. The Asana, ClickUp, Linear, monday.com, Wrike, and Jira connectors read project-management data such as tasks, issues, comments, users, boards, folders, goals, and sprints. The Confluence and Notion connectors turn wiki pages, databases, comments, and blocks into readable searchable text. The GitHub connector reads repositories, issues, comments, and users across organizations and repositories. PagerDuty and Sentry bring in operational data, such as incidents, on-call schedules, services, error issues, events, and releases.

Together, these connectors act like translators at many doors: each opens a different service, walks through its pages safely, and turns what it finds into common records the rest of the system can store and use.

## Files in this stage

### Git-backed repository pages
Treats a GitHub repository's Markdown files as page content, checking for changes before downloading and converting matching files into records.

### `extensions/gbrain/ufo_ext_gbrain/git.py`

`io_transport` · `source sync polling`

This file is the GitHub-backed source connector for gbrain. Its job is to keep a workspace in sync with Markdown files stored in a GitHub repository. Without it, users could not point gbrain at a repo and have its `.md` files appear as pages.

The file is careful because GitHub calls are limited. It first asks GitHub for the current commit SHA, which is like checking the version number on a document. If the SHA has not changed, it returns no pages and avoids downloading the whole repository. If the caller has no stored GitHub token, it also slows down repeated checks so anonymous users do not burn through GitHub's small unauthenticated request limit.

When the repository has changed, it downloads a compressed tarball archive into a temporary file instead of holding the whole archive in memory. It then opens that archive, finds Markdown files, reads their contents, and converts them into pages using the shared page helpers. It enforces size limits both while downloading the compressed archive and while reading decompressed Markdown, so a very large repository cannot quietly consume too much memory or disk. The saved cursor records the last seen SHA, GitHub ETag, and last check time, so the next run knows what changed.

#### Function details

##### `_Cursor.encoded`  (lines 56–59)

```
def encoded(self) -> str
```

**Purpose**: This turns the cursor, which remembers the last GitHub state seen by the source, into a JSON string that can be saved between sync runs.

**Data flow**: It starts with the cursor's SHA, optional ETag, and optional last-check time. It packs those values into a small dictionary and converts that dictionary into stable JSON text. The output is a string that the driver can store and later pass back into this file.

**Call relations**: After `GbrainGitSource.fetch` learns what GitHub commit it has checked, it uses this method to produce the next cursor. That saved cursor becomes the memory for a future sync run.

*Call graph*: 1 external calls (dumps).


##### `_prior_cursor`  (lines 62–68)

```
def _prior_cursor(cursor: str | None) -> _Cursor | None
```

**Purpose**: This reads a saved cursor string from a previous run and turns it back into a `_Cursor` object. If the saved value is missing or damaged, it safely treats it as if there were no prior state.

**Data flow**: It receives either JSON text or `None`. If there is no text, it returns `None`; if there is text, it tries to validate and decode it as a cursor. Valid text becomes a `_Cursor`, while invalid text becomes `None` instead of crashing the sync.

**Call relations**: `GbrainGitSource.fetch` calls this at the start of every sync. The result decides whether the file can do a cheap change check, apply anonymous-rate-limit spacing, or must behave like a first run.

*Call graph*: called by 1 (fetch).


##### `_probe_due`  (lines 71–78)

```
def _probe_due(prior: _Cursor) -> bool
```

**Purpose**: This decides whether an unauthenticated GitHub check is allowed yet. It protects anonymous users from checking too often and running into GitHub's low hourly request limit.

**Data flow**: It receives the previous cursor and reads its `checked_at` time. If the time is missing or unreadable, it says a probe is due. Otherwise, it compares that time with the current UTC time and returns true only after the configured waiting period has passed.

**Call relations**: `GbrainGitSource.fetch` uses this only when there is no stored GitHub token. If it says the probe is not due, the fetch returns immediately with no pages and keeps the old cursor.

*Call graph*: called by 1 (fetch); 2 external calls (fromisoformat, now).


##### `GbrainGitSource.fetch`  (lines 91–127)

```
async def fetch(self, config: GbrainGitConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This is the main sync routine for a GitHub repository source. It checks whether the repo changed, downloads it only when necessary, extracts Markdown files, and returns them as pages.

**Data flow**: It receives a repository configuration, the previous cursor text, and auth context. It decodes the cursor, checks whether a GitHub token exists, possibly skips early for anonymous rate limiting, builds HTTP headers, asks GitHub for the current commit SHA, and compares that SHA with the prior one. If nothing changed, it returns an empty non-snapshot result. If the repo is empty, it returns an empty snapshot. If the repo changed, it downloads the tarball to a temporary file, extracts Markdown entries in a worker thread, deletes the temporary file, converts each Markdown file into a page, and returns a snapshot result with a new cursor.

**Call relations**: This is the method the source driver calls during polling. It delegates cursor parsing to `_prior_cursor`, anonymous timing to `_probe_due`, request header creation to `_headers`, commit checking to `_head`, archive downloading to `_spool_tarball`, and archive reading to `_markdown_entries`. It hands the final pages and next cursor back to the broader sync system through `SyncResult`.

*Call graph*: calls 5 internal fn (_head, _headers, _spool_tarball, _prior_cursor, _probe_due); 6 external calls (__init__, to_thread, now, AsyncClient, decoded, markdown_page).


##### `GbrainGitSource._headers`  (lines 129–136)

```
async def _headers(self, token_stored: bool) -> dict[str, str]
```

**Purpose**: This builds the HTTP headers used for GitHub API requests. If a GitHub token is stored, it adds it so private repositories can be read and higher rate limits apply.

**Data flow**: It receives a yes-or-no value saying whether a token is stored. It always creates headers for GitHub's JSON API and API version. If a token exists, it retrieves the token from the credential store and adds an authorization header. The result is a dictionary of headers for the HTTP client.

**Call relations**: `GbrainGitSource.fetch` calls this while setting up the GitHub client. The headers are then used by later calls to `_head` and `_spool_tarball` through that client.

*Call graph*: called by 1 (fetch).


##### `GbrainGitSource._head`  (lines 138–151)

```
async def _head(self, client: httpx.AsyncClient, config: GbrainGitConfig, prior: _Cursor | None) -> _Cursor | None
```

**Purpose**: This asks GitHub for the current commit SHA of the configured branch or default branch. It is the cheap check that tells the connector whether the full repository archive needs to be downloaded.

**Data flow**: It receives an HTTP client, the repo configuration, and the prior cursor if one exists. It chooses the branch name or `HEAD`, adds an `If-None-Match` header when it has a prior ETag, and sends a GitHub request that asks for only the SHA. A `304 Not Modified` response becomes `None`, an empty-repository response becomes a cursor with an empty SHA, and a successful response becomes a `_Cursor` containing the SHA and any new ETag.

**Call relations**: `GbrainGitSource.fetch` calls this after creating the HTTP client. `_head` uses `_refuse_client_error` to turn GitHub client-side errors into source sync faults, then gives `fetch` enough information to decide whether to skip, report an empty snapshot, or download the tarball.

*Call graph*: calls 1 internal fn (_refuse_client_error); called by 1 (fetch); 2 external calls (__init__, get).


##### `GbrainGitSource._spool_tarball`  (lines 153–171)

```
async def _spool_tarball(self, client: httpx.AsyncClient, config: GbrainGitConfig, sha: str) -> str
```

**Purpose**: This downloads the repository archive for a specific commit into a temporary file. It streams the data in chunks so the whole compressed archive does not have to sit in memory.

**Data flow**: It receives an HTTP client, the repo configuration, and the exact commit SHA to download. It creates a temporary `.tar.gz` file, streams GitHub's tarball response chunk by chunk, counts the bytes received, and writes each chunk to disk. If the download exceeds the size limit or an error happens, it closes and removes the temporary file. On success, it closes the file and returns the file path.

**Call relations**: `GbrainGitSource.fetch` calls this only after `_head` reports a new non-empty SHA. It uses `_refuse_client_error` to reject bad GitHub responses, and it hands the finished temporary archive path back to `fetch`, which passes it to `_markdown_entries` and later deletes it.

*Call graph*: calls 2 internal fn (__init__, _refuse_client_error); called by 1 (fetch); 2 external calls (to_thread, stream).


##### `GbrainGitSource._markdown_entries`  (lines 174–192)

```
def _markdown_entries(repo: str, spool: str, max_bytes: int) -> tuple[tuple[str, bytes], ...]
```

**Purpose**: This opens a downloaded repository archive and pulls out only the Markdown files. It returns their repository paths and raw bytes so they can be turned into pages.

**Data flow**: It receives the repository name, the path to the temporary tarball, and a maximum byte limit. It walks through the archive entries, skips folders and non-Markdown files, strips off the archive's top-level directory prefix, reads matching file contents, and keeps a running total of decompressed Markdown bytes. If the total is too large, it raises a stream fault. Otherwise, it returns a sorted tuple of `(path, bytes)` entries.

**Call relations**: `GbrainGitSource.fetch` runs this in a worker thread after downloading a changed repository. Its output is then decoded and passed to the shared Markdown page builder so the sync result can contain real pages.

*Call graph*: calls 1 internal fn (__init__); 2 external calls (open, is_markdown_path).


##### `_refuse_client_error`  (lines 195–198)

```
def _refuse_client_error(response: httpx.Response, what: str) -> None
```

**Purpose**: This turns bad GitHub HTTP responses into clear sync failures. It gives a friendlier source-specific error for client errors such as missing repositories or denied access.

**Data flow**: It receives an HTTP response and a short label describing what was being requested. If the response is a client error, it raises a `StreamFault` with the GitHub status code and context. Otherwise, it asks the HTTP library to raise for any remaining unsuccessful status, and returns nothing when the response is acceptable.

**Call relations**: `GbrainGitSource._head` and `GbrainGitSource._spool_tarball` call this immediately after GitHub responds. This keeps error handling consistent for both the cheap SHA check and the larger tarball download.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_head, _spool_tarball); 1 external calls (raise_for_status).


### Task workspace APIs
Reads work-management structures from Asana and ClickUp, turning projects, tasks, comments, users, and nested workspace metadata into syncable streams.

### `extensions/sources/ufo_ext_sources/providers/asana.py`

`io_transport` · `source sync`

Asana is a work-tracking tool, and its API returns information in pages rather than all at once. This file defines an Asana connector: a read-only bridge between Asana and the project’s source-sync system. Without it, the system would not know which Asana objects exist, where to request them, or how to keep asking for the next page of results.

The file first defines a small helper for describing an Asana “stream,” meaning one kind of object to fetch, such as tasks or projects. Each stream says what its name is, which field uniquely identifies each record, and which timestamp can be used to tell what changed since the last sync. Tasks and projects can be fetched incrementally using Asana’s `modified_since` option, so the connector can avoid re-reading everything for those streams. Other streams are read fully each time.

The main class, `AsanaConnector`, supplies the Asana base API address, the full list of supported streams, and the paging behavior. Asana wraps results inside a `data` list and gives a `next_page.offset` token when more records are available. The connector follows those tokens like turning pages in a book: read one page, yield its records, then request the next page until no token remains. It does not store or create authentication tokens itself; credentials come from the wider runner/auth system.

#### Function details

##### `_stream`  (lines 24–38)

```
def _stream(name: str, *, cursor_field: str | None=None, updated_at_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Asana object type that can be synced. It keeps the stream definitions short and consistent, so every Asana collection uses the same primary key field and naming pattern.

**Data flow**: It receives the stream name and optional timestamp fields that describe how changes are tracked. It packages those values into a `StreamSpec`, using Asana’s `gid` as the unique record ID, and returns that stream description for the connector’s catalog.

**Call relations**: This function is used while the file is loaded to build the `ASANA_STREAMS` list. It hands each finished stream description to the connector class, which later uses those descriptions to decide which API path to call and whether a cursor can be used.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 74–90)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Asana stream page by page and yields batches of records to the sync system. It knows Asana’s paging style and, for tasks and projects, adds the saved cursor so only recently changed items are requested.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It builds request parameters, asks Asana for a page, safely extracts the `data` list, yields records when present, then checks `next_page.offset` to decide whether to request another page. The output is an asynchronous sequence of record batches; it does not write back to Asana.

**Call relations**: The broader `RestConnector` machinery calls this method when it needs records for an Asana stream. Inside the loop, this method relies on the connector’s HTTP GET helper to fetch each API page and uses `list_or_empty` to turn the response’s `data` value into a safe list before passing records back to the sync pipeline.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/providers/clickup.py`

`io_transport` · `source sync / data ingestion`

ClickUp organizes work like a set of nested boxes: teams contain spaces, spaces contain folders, folders contain lists, and lists contain tasks and related details. This connector walks through those boxes from the top down so it can find every useful record. Without this file, the system would not know how to discover ClickUp lists, pull their tasks, attach parent context such as team or list IDs, or resume task syncing from a previous point.

The file defines the ClickUp streams the system can read, then implements `ClickUpConnector`, a read-only connector built on a shared REST connector base. REST means it talks to ClickUp over ordinary web API requests. The connector first fetches broad collections like teams and spaces, then uses those results to fetch deeper collections like folders and lists. Once it reaches lists, it can fan out into leaf data such as tasks, list comments, and custom fields.

A key detail is context stamping: when records are found under a parent, the connector adds fields like `team_id`, `space_id`, `folder_id`, or `list_id`. This is like labeling every paper from a filing cabinet with the drawer it came from, so it still makes sense after being pulled out. The connector also supports cursors, which are saved “last seen” values used to avoid rereading old task or comment updates.

#### Function details

##### `ClickUpConnector._teams`  (lines 58–60)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the top-level ClickUp teams available to the authenticated account. Teams are the starting point for discovering almost everything else in ClickUp.

**Data flow**: It receives an HTTP client that can make authenticated API requests. It asks ClickUp for `/team`, pulls the `teams` list out of the response, and returns that list as plain record dictionaries.

**Call relations**: This is the first step in several larger walks through ClickUp. `_spaces`, `_users`, and `_goals` call it when they need team IDs or team membership, and `_root_records` calls it when the requested stream is teams.

*Call graph*: called by 4 (_goals, _root_records, _spaces, _users); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 62–70)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived spaces inside every ClickUp team. Spaces are needed before the connector can discover folders and lists.

**Data flow**: It starts by asking `_teams` for all teams. For each valid team ID, it requests that team’s spaces from ClickUp, extracts the `spaces` records, adds the parent `team_id` to each one, and returns one combined list.

**Call relations**: This function builds on `_teams` and becomes the next rung in the hierarchy. `_folders` uses it to find spaces that may contain folders, `_lists` uses it to find folderless lists, and `_root_records` uses it when syncing the spaces stream directly.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, _root_records); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 72–82)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived folders inside every ClickUp space. Folders are one possible container for ClickUp lists.

**Data flow**: It asks `_spaces` for all spaces, checks each space for a usable ID, then calls ClickUp for that space’s folders. It extracts the `folders` list, adds the parent `space_id` to each folder record, and returns all folders together.

**Call relations**: This sits between spaces and lists in the ClickUp hierarchy. `_lists` calls it to discover lists that live inside folders, while `_root_records` calls it when the system is syncing folders as their own stream.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, _root_records); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 84–100)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived ClickUp lists, whether they live inside folders or directly inside spaces. Lists matter because tasks, comments, and custom fields are fetched per list.

**Data flow**: It first asks `_folders` for folder-based locations, then fetches lists for each valid folder and adds `folder_id` context. It then asks `_spaces` for spaces and fetches lists that are directly under each space, adding `space_id` context. The output is one combined list of list records.

**Call relations**: This is the gateway to the most detailed ClickUp data. `_tasks` and `_list_child_stream` call it before reading per-list data, and `_root_records` calls it when the requested stream is lists.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _root_records, _tasks); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 102–127)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads tasks from every discovered ClickUp list, including closed tasks and subtasks. It also supports incremental syncing by skipping tasks whose update time is not newer than the saved cursor.

**Data flow**: It gets all lists from `_lists`, then for each valid list ID requests task pages from ClickUp using `page=0`, `page=1`, and so on. It extracts the `tasks` records, adds the list ID and list name, filters by `date_updated` if a cursor was provided, and yields batches of tasks as they are found.

**Call relations**: `paginate` calls this when the active stream is `tasks`. `_tasks` depends on `_lists` to know which task endpoints to visit, and it uses the shared record extraction and context helpers before handing task batches back to the sync flow.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 129–148)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads list-level child data: comments on a list and custom fields defined for a list. It lets these smaller per-list details be synced as their own streams.

**Data flow**: It gets all lists from `_lists`, picks the correct ClickUp endpoint based on the stream name, and fetches either comments or fields for each list. It extracts the right response list, adds list ID and list name, optionally filters by the stream’s cursor field, and yields non-empty batches.

**Call relations**: `paginate` calls this for the `list_comments` and `list_custom_fields` streams. Like `_tasks`, it starts with `_lists` because ClickUp only exposes this data once a specific list is known.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 150–176)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the traffic director for ClickUp syncing. Given a stream name, it chooses the right reading method and yields batches of records to the rest of the source system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching helper, and yields records if any are returned. If the stream name is unknown or unsupported, it raises `StreamSkipped`, which tells the sync system not to process that stream.

**Call relations**: This is the main method the connector framework uses to pull records. It delegates root hierarchy streams to `_root_records`, users to `_users`, tasks to `_tasks`, list child streams to `_list_child_stream`, and goals to `_goals`.

*Call graph*: calls 6 internal fn (__init__, _goals, _list_child_stream, _root_records, _tasks, _users).


##### `ClickUpConnector._root_records`  (lines 178–189)

```
async def _root_records(self, client: httpx.AsyncClient, name: str) -> list[dict[str, Any]]
```

**Purpose**: Provides one simple doorway for fetching ClickUp’s main hierarchy streams: teams, spaces, folders, and lists. It keeps `paginate` from needing to repeat that branching logic.

**Data flow**: It receives a stream name and chooses the matching helper. For a known root collection, it returns that collection’s records; for an invalid name, it raises an error saying ClickUp has no such root collection.

**Call relations**: `paginate` calls this when syncing `teams`, `spaces`, `folders`, or `lists`. It then hands off to `_teams`, `_spaces`, `_folders`, or `_lists`, depending on the requested collection.

*Call graph*: calls 4 internal fn (_folders, _lists, _spaces, _teams); called by 1 (paginate).


##### `ClickUpConnector._users`  (lines 191–200)

```
async def _users(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a unique set of ClickUp users from the members embedded inside team records. ClickUp does not provide this connector with a separate flat user walk, so users are gathered from teams.

**Data flow**: It asks `_teams` for team records, looks through each team’s `members`, and pulls out the nested `user` object when present. It stores users in a dictionary keyed by user ID, adds the related `team_id`, and returns that dictionary so duplicate users collapse into one entry per ID.

**Call relations**: `paginate` calls this for the `users` stream. `_users` relies on `_teams` because team membership is where the connector finds user information.

*Call graph*: calls 1 internal fn (_teams); called by 1 (paginate).


##### `ClickUpConnector._goals`  (lines 202–210)

```
async def _goals(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches ClickUp goals for every team. Goals are included as a non-canonical stream, meaning they are synced as useful related data but are not treated as one of the main core object types.

**Data flow**: It asks `_teams` for available teams, skips any team without a valid ID, then requests goals for each team from ClickUp. It extracts the `goals` records, adds the parent `team_id`, and returns all goals in one list.

**Call relations**: `paginate` calls this when the selected stream is `goals`. It starts from `_teams` because ClickUp goals are requested through team-specific endpoints.

*Call graph*: calls 1 internal fn (_teams); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 212–246)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes ClickUp records into friendlier fields the rest of the system can rely on, such as `name`, `created_at`, `status`, `author`, or `parent_external_id`. It leaves records mostly intact while adding common labels across streams.

**Data flow**: It receives one raw record and the stream it came from. Depending on the stream name, it copies the record and fills in standard fields from ClickUp-specific fields, such as turning a user’s `username` into `name` or a task’s nested status object into a simple `status` value. For comment users, it safely treats missing or malformed user data as an empty dictionary.

**Call relations**: This function is part of the connector’s output cleanup step. After records are fetched through methods such as `paginate`, the broader source framework can call `flatten` so downstream storage and search see consistent, easy-to-use fields.

*Call graph*: 1 external calls (dict_or_empty).


### Atlassian and developer ecosystems
Connects to Confluence, GitHub, and Jira to ingest collaborative pages, repositories, issues, comments, projects, boards, users, and related metadata.

### `extensions/sources/ufo_ext_sources/providers/confluence.py`

`io_transport` · `source sync`

Confluence does not store page bodies as simple plain text. It stores them as XHTML-like “storage format”, which is text mixed with markup tags for headings, tables, macros, and layout. If the system saved that raw markup, recalled pages would be noisy and hard to read. This file fixes that by fetching Confluence records, flattening them into consistent fields, and rendering readable prose.

The connector first asks Atlassian which Confluence sites the current OAuth grant can access. A single grant can cover more than one site, so each stream fans out across those sites. For each site, it calls the right Confluence API path and walks through results in pages, using `start` and `limit` like turning pages in a catalog. Some streams are incremental: the connector still reads through Confluence results, but only keeps records newer than the saved cursor because Confluence does not offer a true server-side “since this time” filter here.

The file also protects record identity. Since two Confluence sites can both have a page with the same ID, most IDs are prefixed with the site’s cloud ID. Finally, `_StorageTextExtractor` strips Confluence’s storage-format markup down to human-readable text, adding line breaks around block-like tags so pages read more like the original document.

#### Function details

##### `_body_text`  (lines 114–116)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: Finds the best available body text field inside a Confluence record. It prefers the storage-format body, then falls back to the view-format body, and returns nothing if neither is usable text.

**Data flow**: It receives one Confluence record as a dictionary-like object. It reads nested fields such as `body.storage.value` and `body.view.value`, checks whether the found value is a non-empty string, and returns that string or `None`.

**Call relations**: During flattening, `ConfluenceConnector.flatten` calls this helper when it is preparing pages, blog posts, and comments. The helper uses `get_path` so the flattening code does not have to manually dig through nested dictionaries.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 124–150)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches batches of records for one Confluence stream, such as pages or comments. It also spreads the work across every Confluence site the current grant can access.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It looks up the API path for that stream, asks `_sites` for accessible sites, builds a site-specific Confluence API path for each site, and then yields batches returned by `_offset_results`. Before yielding, it adds context such as the site cloud ID and site URL to each batch. If Confluence refuses access with a permission-style error, it turns that into a skipped stream instead of a failed run.

**Call relations**: The source sync framework calls this when it needs records from a Confluence stream. This method is the main traffic director: it asks `_sites` where to go, delegates page-by-page fetching to `_offset_results`, adds site context with `with_context`, and raises `StreamSkipped` when a stream cannot be read because the grant lacks access.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 152–156)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Discovers which Atlassian sites are available to the current OAuth grant. Confluence API calls need a site-specific cloud ID, so the connector cannot fetch pages until it knows these sites.

**Data flow**: It receives an HTTP client and calls Atlassian’s accessible-resources endpoint. It reads the JSON response if there is content, makes sure the result is a list-like collection using `list_or_empty`, and returns a list of site records.

**Call relations**: `ConfluenceConnector.paginate` calls this before fetching any stream data. The returned site records supply the cloud IDs that `paginate` uses to build the `/ex/confluence/{cloud_id}/...` API paths.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 158–182)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Walks through one Confluence collection page by page. It also applies the connector’s incremental filtering, keeping only records newer than the saved cursor when a cursor is available.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, an optional cursor value, and the name of the field to compare against that cursor. It repeatedly sends requests with `start` and `limit`, extracts the `results` list from each response, filters records whose cursor field is not newer than the saved cursor, and yields non-empty batches. It stops when there are no records left or the response no longer includes a next-page link.

**Call relations**: `ConfluenceConnector.paginate` calls this for each accessible site and stream. This method relies on `records_at` to find the result list and `get_path` to read nested cursor fields and next-page markers.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 184–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns Confluence’s raw API records into a more consistent shape for syncing and recall. It adds common fields like title, URL, body, timestamps, and parent links where those make sense.

**Data flow**: It receives one raw record and the stream description. Depending on the stream, it copies the record and adds clearer fields: spaces get names and API URLs, pages and blog posts get titles, kind labels, web URLs, body text, and timestamps, and comments get body text, author IDs, URLs, creation time, and parent page or blog IDs. It also prefixes most primary keys with the site cloud ID to avoid ID collisions across sites, and it lifts nested cursor fields into flat fields when needed.

**Call relations**: The sync framework uses this after records have been fetched. It calls `_body_text` for content-bearing records and uses `get_path` to safely read nested Confluence fields, preparing the record for later storage, cursor tracking, and rendering.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 234–250)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Creates a readable title and text body for a Confluence record. This is especially important for pages, blog posts, comments, and spaces, where raw Confluence markup would be unpleasant to search or recall.

**Data flow**: It receives a flattened record and stream description. For pages, blog posts, and comments, it reads the title and sends the body through `_StorageTextExtractor`. For spaces, it chooses the name or key as the title and extracts readable text from the space description. For other streams, it falls back to the base connector’s rendering. It returns a pair: the chosen title and a markdown-like text block headed with the Confluence stream name.

**Call relations**: The source framework calls this when it needs human-readable text from a synced record. It uses `_str` to safely treat only real strings as strings, uses `get_path` to find nested descriptions, and relies on `_StorageTextExtractor` to turn Confluence XHTML into plain prose.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 259–261)

```
def __init__(self) -> None
```

**Purpose**: Sets up a small HTML parser that will collect readable text from Confluence storage-format markup. It starts with an empty list of text pieces.

**Data flow**: It receives no outside data beyond the new parser instance being created. It initializes the parent HTML parser with automatic character-reference conversion, so things like HTML entities become normal characters, and prepares an internal list where text and line-break markers will be stored.

**Call relations**: `_StorageTextExtractor.extract` creates an instance of this class whenever it needs to parse a raw Confluence body. The other parser callbacks then fill the internal list as parsing proceeds.


##### `_StorageTextExtractor.extract`  (lines 264–269)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: Converts a raw Confluence storage-format string into plain readable text. It is the public shortcut for using `_StorageTextExtractor` without manually creating a parser.

**Data flow**: It receives any value. If the value is not a non-empty string, it returns an empty string. Otherwise, it creates a parser, feeds the raw markup into it, asks the parser to assemble cleaned text, and returns that result.

**Call relations**: `ConfluenceConnector.render` calls this when rendering pages, blog posts, comments, and space descriptions. This class method coordinates the parser lifecycle: create, feed, and collect final text.


##### `_StorageTextExtractor.handle_data`  (lines 271–272)

```
def handle_data(self, data: str) -> None
```

**Purpose**: Collects plain character data found inside the markup. In simple terms, this keeps the words and sentences while ignoring the surrounding tags.

**Data flow**: The HTML parser calls it with a chunk of text found between tags. The method appends that text to the parser’s internal list. It does not return anything.

**Call relations**: This is called automatically by Python’s `HTMLParser` while `_StorageTextExtractor.extract` feeds it Confluence markup. The collected pieces are later cleaned and joined by `_StorageTextExtractor._text`.


##### `_StorageTextExtractor.handle_starttag`  (lines 274–276)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: Adds a line break when a block-like HTML tag begins. This helps preserve the readable shape of paragraphs, table cells, headings, and list items after tags are removed.

**Data flow**: The HTML parser calls it with a tag name and that tag’s attributes. If the tag is one of the known block tags, the method appends a newline marker to the internal list. Attributes are ignored because they are not useful for plain text recall.

**Call relations**: This callback runs automatically during parsing started by `_StorageTextExtractor.extract`. Its newline markers are later normalized by `_StorageTextExtractor._text`, so the final text has sensible line breaks instead of one long run-on paragraph.


##### `_StorageTextExtractor.handle_endtag`  (lines 278–280)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: Adds a line break when a block-like HTML tag ends. This gives the extracted text another chance to separate paragraphs, headings, table rows, and similar visual blocks.

**Data flow**: The HTML parser calls it with a closing tag name. If the tag is in the block-tag set, the method appends a newline marker to the internal list. It does not return anything.

**Call relations**: Like `handle_starttag`, this is called automatically while `_StorageTextExtractor.extract` parses markup. The newline markers it adds are cleaned up by `_StorageTextExtractor._text` before the final text is returned.


##### `_StorageTextExtractor._text`  (lines 282–285)

```
def _text(self) -> str
```

**Purpose**: Builds the final clean text from everything the parser collected. It removes extra spacing and blank lines while keeping meaningful line breaks.

**Data flow**: It reads the parser’s internal list of text chunks and newline markers, joins them into one string, splits that string into lines, collapses repeated whitespace within each line, drops empty lines, and returns the cleaned result.

**Call relations**: `_StorageTextExtractor.extract` calls this after feeding in the raw markup. It is the final cleanup step after `handle_data`, `handle_starttag`, and `handle_endtag` have collected words and line breaks.


##### `_str`  (lines 288–289)

```
def _str(value: Any) -> str
```

**Purpose**: Safely returns a value only if it is already a string. It prevents non-string values from accidentally being used as titles.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged; otherwise, it returns an empty string.

**Call relations**: `ConfluenceConnector.render` calls this when choosing titles for pages, blog posts, comments, and spaces. It keeps rendering simple and predictable by avoiding unexpected value types.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/github.py`

`io_transport` · `source sync / data ingestion`

This connector is the bridge between GitHub’s web API and the project’s source-sync system. Its job is to ask GitHub for many kinds of records, such as repositories, issues, commits, tags, stargazers, teams, and users, and feed them back in a steady shape the rest of the system can store and recall.

A key idea in this file is “fan-out.” Instead of asking for one fixed repository, the connector first asks GitHub which organizations the credential can access. It then lists repositories in those organizations and runs repository-based streams once per repo. This matters because many GitHub IDs are only unique inside a repo. For example, many repositories have a branch called `main`. The connector stamps each record with its repo or organization, then folds that stamp into the record’s identity so each repo’s `main` stays separate.

The file also deals with GitHub pagination, which is like reading a long book one page at a time using GitHub’s “next page” links. Some streams can resume from a time cursor, some are newest-first feeds, and some are full refreshes. The connector chooses the right walking style for each. If an organization or repository is inaccessible, it usually skips that part instead of failing the whole sync. There is no write path here; this file only reads from GitHub.

#### Function details

##### `_stream`  (lines 74–94)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', ordering: Ordering=Ordering.none, canonical:
```

**Purpose**: This helper creates a stream description, which is the connector’s recipe for one kind of GitHub data, such as issues or commits. It keeps the long stream list readable by filling in common defaults.

**Data flow**: It receives the stream name and optional details like the GitHub object name, primary key, cursor field, and ordering style. It passes those details into a `StreamSpec`, which is the system’s standard stream recipe, and returns that recipe for later use.

**Call relations**: The module uses this helper while building the GitHub stream catalog. Each returned `StreamSpec` is later inspected by the connector when deciding which API path to call and how to resume a sync.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 198–201)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This returns only the GitHub streams that are actually wired to an API path. It lets the catalog include future or parity streams without pretending they can run today.

**Data flow**: It reads the connector’s full stream list and the local path table. It filters out any stream whose name has no path entry, then returns the runnable subset.

**Call relations**: The broader sync system calls this when it asks the connector what it can read. The result controls which streams move on to pagination and fetching.


##### `GitHubConnector._make_client`  (lines 203–207)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to GitHub and adds GitHub-specific headers. Those headers tell GitHub which API format and API version the connector expects.

**Data flow**: It receives a base URL and credential, asks the parent connector to create the authenticated client, then adds the `Accept` and GitHub API version headers. It returns the prepared asynchronous HTTP client.

**Call relations**: The source framework uses this during setup before any GitHub requests are made. Later pagination helpers reuse this client for all API calls.


##### `GitHubConnector.flatten`  (lines 209–258)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes certain GitHub records and makes their identity safe across repositories or organizations. Without this, records with the same local key, such as two repos both having a tag named `v1.0`, could collide and overwrite each other.

**Data flow**: It receives one GitHub record and its stream description. It may reshape special records, such as merging stargazer user fields or removing bulky nested repo objects from pull requests. Then it finds whether the stream belongs to a repo or organization, reads the record’s key, prefixes that key with the repo or org stamp, and returns the updated record.

**Call relations**: The adapter calls this before it reads the primary key for storage. It uses `_partition_field` to know which context stamp should exist and `get_path` when the key lives inside nested data.

*Call graph*: calls 1 internal fn (_partition_field); 1 external calls (get_path).


##### `GitHubConnector.record_identity`  (lines 260–267)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This gives the system a stable identity string for a record. It has a special path for contributor activity because that stream’s useful ID is nested under `author.id` rather than sitting in the normal primary-key field.

**Data flow**: It receives a record and stream description. For most streams, it delegates to the parent connector. For contributor activity, it reads the repo stamp and nested author ID; if both are present, it combines them into one identity string, otherwise it returns nothing.

**Call relations**: The sync/storage layer uses this when deciding whether a row is already known. It relies on `get_path` to read the nested author ID in the contributor activity record.

*Call graph*: 1 external calls (get_path).


##### `GitHubConnector.render`  (lines 269–274)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This prepares a record’s visible page content. For pull requests, it keeps the update timestamp as metadata rather than repeating it in the page body.

**Data flow**: It receives a record and stream description. Non-pull-request records go straight to the parent renderer. Pull request records are copied without the stream’s update field, then passed to the parent renderer, which returns the page title and content.

**Call relations**: The system calls this when turning synced records into recallable pages. It mostly reuses the base renderer, with one pull-request-specific cleanup step.


##### `GitHubConnector.paginate_source`  (lines 276–287)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the connector’s public pagination entry for the source framework. It accepts the usual cursor plus an optional backfill floor, which is used for newest-first repository streams.

**Data flow**: It receives the HTTP client, stream description, current cursor, self user ID, and optional backfill cutoff time. It forwards the relevant values to `GitHubConnector.paginate` and returns its asynchronous stream of pages.

**Call relations**: The source framework calls this when it needs records for a stream. It hands off immediately to `GitHubConnector.paginate`, which chooses the correct GitHub walking strategy.

*Call graph*: calls 1 internal fn (paginate).


##### `GitHubConnector.paginate`  (lines 289–323)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for reading a GitHub stream. It looks at the stream name and API path, then sends the work to the right kind of walker: repository-wide, organization-wide, or simple linked-page pagination.

**Data flow**: It receives a client, stream description, cursor, and optional backfill cutoff. It finds the stream’s path, builds basic query parameters, adds `state=all` where GitHub needs it, then yields pages from the appropriate helper. If no path is known, it raises an error because the stream is cataloged but not runnable.

**Call relations**: It is called by `GitHubConnector.paginate_source`. Depending on the path shape, it calls `_repository_pages`, `_repo_stream_pages`, `_org_stream_pages`, or `_paginate_link_header` to do the actual reading.

*Call graph*: calls 4 internal fn (_org_stream_pages, _paginate_link_header, _repo_stream_pages, _repository_pages); called by 1 (paginate_source).


##### `GitHubConnector._repository_pages`  (lines 325–329)

```
async def _repository_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads repository records for all organizations the credential can access. It stamps each repository page with the organization it came from.

**Data flow**: It receives the HTTP client. It asks `_iter_granted_org_repo_pages` for pages of repositories grouped by organization, adds the organization context to each record in the page, and yields those pages.

**Call relations**: It is used by `GitHubConnector.paginate` for the `repositories` stream. It relies on `with_context` so downstream identity and rendering code can tell which organization produced each repository.

*Call graph*: calls 1 internal fn (_iter_granted_org_repo_pages); called by 1 (paginate); 1 external calls (with_context).


##### `GitHubConnector._repo_partitions`  (lines 331–333)

```
async def _repo_partitions(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This produces the list of repository partitions to walk. A partition is one bounded slice of work, here one repository such as `owner/name`.

**Data flow**: It receives the HTTP client. It asks `_iter_user_repos` for owner and repo name pairs, joins each pair into a single `owner/repo` string, and yields those strings one by one.

**Call relations**: It is passed into `PartitionWalk` by `_repo_stream_pages`. That walk uses these repo keys to run the same stream separately for each repository.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector._repo_stream_pages`  (lines 335–359)

```
async def _repo_stream_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, cursor: str | None, backfill_after: datetime | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This sets up the per-repository walking engine for repo-scoped streams like issues, commits, and tags. It lets the shared `PartitionWalk` object keep track of resume state across many repositories.

**Data flow**: It receives the client, stream, path, saved cursor, and optional backfill cutoff. It converts the cutoff time into GitHub’s timestamp string, creates a `PartitionWalk` with repository partitions and page-fetching callbacks, then yields the stream pages that the walk produces. When finished, it closes the async generator if needed.

**Call relations**: It is called by `GitHubConnector.paginate` whenever a path contains both owner and repo placeholders. It wires `_repo_partitions` and `_repo_pages` into `PartitionWalk`, which controls cursor windows and resume behavior.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, astimezone, partial).


##### `GitHubConnector._org_stream_pages`  (lines 361–379)

```
async def _org_stream_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, params: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads organization-scoped streams, such as organization members or teams, once for each organization the credential can see. For users, it also fetches richer public profile information.

**Data flow**: It receives the client, stream, path template, and query parameters. It walks organizations from `_iter_user_orgs`, fills the organization into the path, reads linked pages, optionally enriches user records, stamps each page with the organization login, and yields it. If an individual organization is gone or inaccessible, it skips that organization.

**Call relations**: It is called by `GitHubConnector.paginate` for paths with an organization placeholder. It calls `_paginate_link_header` for the actual page requests, `_enrich_users` for the users stream, and `with_context` so later code can scope record identities.

*Call graph*: calls 3 internal fn (_enrich_users, _iter_user_orgs, _paginate_link_header); called by 1 (paginate); 2 external calls (Semaphore, with_context).


##### `GitHubConnector._repo_pages`  (lines 381–452)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: This fetches one repository’s pages for one stream, applying the right time bounds and filters for that stream. It is the place where GitHub query parameters and the sync engine’s resume rules meet.

**Data flow**: It receives the client, stream, path template, repo key, and a partition bound that says where to resume or stop. It builds the concrete GitHub path and query parameters, reads pages through `_paginate_link_header`, filters records when needed, calculates the highest and lowest cursor values on each page, stamps records with the repo key, and yields `WalkPage` objects. If the repo cannot be read because it is missing, empty, or gone, it raises a skip signal for just that partition.

**Call relations**: This function is supplied to `PartitionWalk` by `_repo_stream_pages`. It calls `_cursor_bounds` so the walk can update its watermark, and `with_context` so flattened records later get repo-scoped identities.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); 3 external calls (__init__, __init__, with_context).


##### `GitHubConnector._iter_user_repos`  (lines 454–462)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: This discovers the repositories that should be synced by looking through granted organizations. It deliberately avoids GitHub’s broad user-repos endpoint so the sync stays tied to organization access.

**Data flow**: It receives the HTTP client. It gets repository pages from `_iter_granted_org_repo_pages`, extracts a clean owner/repo pair from each repository record using `_repo_identity`, and yields only records with a usable identity.

**Call relations**: It is called by `_repo_partitions`, which turns its output into partition keys. It depends on `_iter_granted_org_repo_pages` for discovery and `_repo_identity` for reliable parsing.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (_repo_partitions).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 464–483)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: This lists repositories for every organization the credential can access, while ignoring archived repositories and forks. That keeps repository-scoped syncs focused on active, organization-owned repositories.

**Data flow**: It receives the client. It walks organizations from `_iter_user_orgs`, reads each organization’s repository pages through `_paginate_link_header`, filters out archived and forked repos, and yields only non-empty filtered pages along with the organization login. If one organization refuses access or no longer exists, it skips that organization.

**Call relations**: It is used by `_repository_pages` to produce the repositories stream and by `_iter_user_repos` to build repo partitions. It depends on `_iter_user_orgs` as the root organization discovery step.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, _repository_pages).


##### `GitHubConnector._iter_user_orgs`  (lines 485–506)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This discovers which GitHub organizations the credential can see. Because all runnable streams start from organizations, a root refusal here means the whole stream should be recorded as skipped rather than treated as a crash.

**Data flow**: It receives the HTTP client. It reads `/user/orgs` using linked-page pagination, pulls out each non-empty organization login, and yields it. If GitHub returns a forbidden response at this root step, it raises `StreamSkipped` to say the credential lacks the needed organization access.

**Call relations**: It is called by `_iter_granted_org_repo_pages` for repository discovery and by `_org_stream_pages` for organization-scoped streams. It uses `_paginate_link_header` to walk GitHub’s pages.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, _org_stream_pages).


##### `GitHubConnector._enrich_users`  (lines 508–528)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: This replaces simple organization-member records with fuller public GitHub user records when possible. It helps the users stream include public fields like name or email, not just login and ID.

**Data flow**: It receives a client, a page of member records, and a semaphore, which is a small gate that limits how many requests run at once. It launches one enrichment task per member, waits for them all with `asyncio.gather`, and returns the enriched page in list form.

**Call relations**: It is called by `_org_stream_pages` only for the users stream. It uses the nested `GitHubConnector._enrich_users.one` helper to fetch each individual profile.

*Call graph*: called by 1 (_org_stream_pages); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 514–526)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This enriches a single organization member by fetching that user’s public GitHub profile. If the profile is missing, it keeps the original member record instead of failing the whole page.

**Data flow**: It receives one member record from the surrounding `_enrich_users` call. It reads the member’s login, waits for permission from the semaphore, asks GitHub for `/users/{login}`, and returns the JSON object if it is a dictionary. If there is no login, a missing user, or an unexpected body shape, it returns the original member.

**Call relations**: This helper is created and used inside `_enrich_users`. Many copies of it run concurrently, but the semaphore prevents the connector from sending too many profile requests at once.


##### `GitHubConnector._paginate_link_header`  (lines 530–538)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the common GitHub pagination helper. It follows GitHub’s `Link` header, which points to the next page of results, and turns each response into a list of records.

**Data flow**: It receives a client, path, and optional query parameters. It delegates to the parent link-header pagination routine with the GitHub page size and `_parse_records` as the response parser, then yields each parsed page.

**Call relations**: Most fetching helpers call this: organization discovery, repository listing, org-scoped streams, repo-scoped pages, and simple streams from `paginate`. It centralizes the “keep following next page links” behavior.

*Call graph*: called by 5 (_iter_granted_org_repo_pages, _iter_user_orgs, _org_stream_pages, _repo_pages, paginate).


##### `_partition_field`  (lines 541–549)

```
def _partition_field(path: str) -> str | None
```

**Purpose**: This tells the connector which context field should be present for a path. Repo-scoped paths need a repo stamp, org-scoped paths need an organization stamp, and unscoped paths need neither.

**Data flow**: It receives an API path template. If the path contains a repo placeholder, it returns the repo context field name; if it contains an organization placeholder, it returns the organization context field name; otherwise it returns nothing.

**Call relations**: It is called by `GitHubConnector.flatten` before record identity is scoped. That lets flattening know whether to expect `repo_full_name`, `org_login`, or no partition stamp.

*Call graph*: called by 1 (flatten).


##### `_parse_records`  (lines 552–556)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: This converts a GitHub HTTP response into the list of records the connector expects. GitHub list endpoints should return JSON arrays, and anything else is treated as no records.

**Data flow**: It receives an HTTP response. If the response has no body, it returns an empty list. Otherwise it parses the JSON body and returns it only if it is a list; non-list bodies also become an empty list.

**Call relations**: It is supplied to the shared link-header pagination routine through `_paginate_link_header`. That routine uses it on each GitHub response before yielding a page.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 559–574)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: This extracts a dependable owner and repository name from a GitHub repository record. GitHub records usually include this clearly, but the helper has fallbacks for slightly different shapes.

**Data flow**: It receives a repository record and an optional fallback owner. It first tries `full_name` like `owner/repo`, then tries the nested owner login plus repo name, then tries the fallback owner plus repo name. If none are valid, it returns nothing.

**Call relations**: It is called by `_iter_user_repos` while building the repository list for partitioned syncs. Its output becomes the owner/repo keys used by later repo-scoped API calls.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 577–587)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This finds the newest and oldest cursor values on one page of records. The sync walker uses those bounds to know how far it has moved through time and where it can resume later.

**Data flow**: It receives a page of records and the cursor field name. If there is no cursor field, it returns two empty values. Otherwise it reads the cursor value from each record, including nested fields, keeps string values, and returns the maximum and minimum values found.

**Call relations**: It is called by `_repo_pages` after each GitHub page is fetched or filtered. The resulting high and low values are placed into `WalkPage` objects so `PartitionWalk` can track watermarks and stop conditions.

*Call graph*: called by 1 (_repo_pages); 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/providers/jira.py`

`io_transport` · `source sync`

This connector is the bridge between a user’s Jira account and the project’s sync system. Jira data is spread across one or more Atlassian Cloud sites, so the connector first asks Atlassian which sites the user’s permission token can reach. It then visits each site and pulls the requested kind of data, such as projects or issues.

Most Jira lists arrive in pages, like a long catalog split into batches of 100. The connector keeps asking for the next batch until Jira says there is no more. Some streams are incremental, meaning they only fetch records updated after the last saved point in time. That keeps later syncs smaller and faster.

A few streams depend on others. Comments are fetched by first listing issues, then asking for each issue’s comments. Sprints are fetched by first listing boards, then asking for each board’s sprints. Each returned record is tagged with helpful context, such as the Jira cloud site ID, so later code knows where it came from.

The file also turns Jira’s nested issue and comment content into readable text. Jira descriptions and comments use Atlassian Document Format, a tree-shaped document structure, so the helper code walks that tree and extracts plain text. If Jira refuses access with a permission error, the connector marks that stream as skipped instead of treating the whole sync as broken.

#### Function details

##### `JiraConnector.paginate`  (lines 73–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading one Jira stream. Given a stream name, it calls the right reader for projects, issues, comments, users, boards, or sprints, and turns permission refusals into clean skip messages.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It chooses the matching private method, yields each page of records from that method, and if Jira returns a 401 or 403 permission response, it changes that into a StreamSkipped result instead of letting the error stop the whole run.

**Call relations**: The source runner calls this when it wants records for a Jira stream. This function then hands the work to _projects, _issues, _comments, _users, _boards, or _sprints; if the stream name is unknown or access is refused, it reports that the stream should be skipped.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 106–110)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Jira Cloud sites the current permission grant can access. It is the starting point for site-specific streams because Jira API paths need a cloud site ID.

**Data flow**: It uses the HTTP client to request Atlassian’s accessible-resources endpoint. The JSON response is normalized into a list, so missing or oddly shaped content becomes an empty list rather than surprising later code.

**Call relations**: _projects, _issues, _users, and _boards call this before reading site-level data. Those readers use the returned site IDs to build Jira API paths under the correct cloud site.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 112–132)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira endpoints that return long lists in numbered pages. It hides the repeated work of asking for page 1, page 2, and so on.

**Data flow**: It receives an API path, optional query parameters, and the JSON key where the record list lives. It repeatedly sends requests with startAt and maxResults, extracts the records, yields non-empty pages, and stops when Jira says the page is last, there are no records, or the total count has been reached.

**Call relations**: _projects, _issues, _comments, _boards, and _sprints use this shared pager whenever they read a Jira collection wrapped in a JSON envelope. It passes each extracted batch back to the stream-specific reader that called it.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 134–141)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira projects from every accessible cloud site. A project is a container for Jira issues, such as a software team’s backlog.

**Data flow**: It asks _sites for reachable Jira sites, ignores site entries without a usable cloud ID, then reads the project search endpoint through _offset_values. Each page of projects is returned with added context showing which cloud site and site URL it came from.

**Call relations**: paginate calls this when the requested stream is projects. It depends on _sites to find where to look, uses _offset_values to move through Jira’s pages, and uses with_context so downstream sync code can keep the site information attached.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 143–155)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira issues, optionally only those updated after a saved cursor. Issues are the main Jira work items, such as bugs, tasks, or stories.

**Data flow**: It builds a Jira Query Language string, which is Jira’s search syntax, ordering issues by update time and filtering after the cursor when one is provided. For each accessible site, it requests issue search pages with a selected set of useful fields, then adds cloud site context to each returned page.

**Call relations**: paginate calls this for the issues stream. _comments also calls it with no cursor so it can discover issues before fetching their comments. The function relies on _sites for site discovery, _offset_values for paging, and with_context for preserving origin details.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 157–177)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads comments attached to Jira issues. Because Jira comments live under individual issues, it first has to find issues and then visit each issue’s comment endpoint.

**Data flow**: It reads all issues through _issues, then for each issue with a valid issue ID and cloud ID it pages through that issue’s comments. If a cursor is present, it keeps only comments whose updated timestamp is newer than that cursor, then adds context such as cloud ID, site URL, issue ID, and issue key.

**Call relations**: paginate calls this for the issue_comments stream. This function builds on _issues to know which issue comment lists exist, uses _offset_values to read each comment list, and returns enriched comment pages for the sync system.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 179–190)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira users from each accessible cloud site. It covers people known to Jira, using their Atlassian account IDs as stable identifiers.

**Data flow**: It asks _sites for reachable sites, skips entries without a valid cloud ID, then calls Jira’s users/search endpoint once per site with a page size. The endpoint returns a bare JSON array, so the function normalizes that array and yields it with cloud site context when users are present.

**Call relations**: paginate calls this when the requested stream is users. Unlike the other list readers, it does not use _offset_values because this Jira endpoint returns a direct array rather than the usual paged envelope; it still uses _sites and with_context like the other site-based streams.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 192–199)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira Agile boards from every accessible site. Boards are the Jira Agile views that organize work for teams, and they are needed before sprints can be fetched.

**Data flow**: It gets reachable sites from _sites, builds the Agile board API path for each valid cloud ID, and pages through the board list with _offset_values. Each page is tagged with the cloud ID and site URL before being yielded.

**Call relations**: paginate calls this for the boards stream. _sprints also calls it first because sprints are nested under boards in Jira’s Agile API.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 201–215)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads sprints from Jira Agile boards, optionally only those updated after a saved cursor. A sprint is a time-boxed period of planned work in Agile teams.

**Data flow**: It first reads boards through _boards. For each board with a usable board ID and cloud ID, it pages through that board’s sprint endpoint, filters by updatedDate when a cursor is provided, and yields remaining sprints with cloud ID and board ID attached.

**Call relations**: paginate calls this for the sprints stream. It depends on _boards to discover where sprints live, then uses _offset_values to read each board’s sprint pages.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 217–224)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This prepares records for the sync system’s cursor tracking. In particular, Jira issue update times are nested inside fields, but the sync machinery expects the cursor value at the top level.

**Data flow**: It receives one record and its stream description. If the stream is issues, it safely reads the fields object and returns a copy of the record with a top-level updated value; for all other streams it returns the record unchanged.

**Call relations**: The broader source framework uses this after records are fetched and before cursor bookkeeping. It calls _dict_or_empty so malformed or missing issue fields do not crash the flattening step.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 226–252)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Jira records into readable page text. Instead of storing raw JSON for issues and comments, it creates a human-friendly title and body.

**Data flow**: It receives a record and stream description. For issues, it extracts the summary, status, priority, assignee, reporter, and description text; for comments, it extracts the author and comment body. It returns a title plus a Markdown-like page body with a heading and readable content, while other stream types fall back to the parent connector’s default rendering.

**Call relations**: The source framework calls this when it needs text to index or display for a synced record. It uses _dict_or_empty, _str, _person, _field_line, and _doc_text to safely turn Jira’s nested fields into plain text.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 255–256)

```
def _str(value: Any) -> str
```

**Purpose**: This small safety helper returns a value only if it is actually a string. It prevents accidental non-text values from leaking into rendered page text.

**Data flow**: It receives any value. If the value is a string, it returns it; otherwise it returns an empty string.

**Call relations**: JiraConnector.render uses this while building issue titles and metadata. _person also uses it when choosing a display name or email address from a user object.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 259–260)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This small safety helper treats non-dictionary values as empty dictionaries. It lets the connector read nested Jira JSON without crashing when a field is missing or shaped unexpectedly.

**Data flow**: It receives any value. If the value is a dictionary, it returns that dictionary; otherwise it returns an empty dictionary that callers can safely ask for keys.

**Call relations**: JiraConnector.flatten, JiraConnector.render, and _person call this before reading nested fields from Jira records.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 263–265)

```
def _person(value: Any) -> str
```

**Purpose**: This extracts a readable name for a Jira person. It prefers the person’s display name and falls back to their email address.

**Data flow**: It receives a possible Jira user object. It safely treats it as a dictionary, reads displayName and emailAddress as strings, and returns the first non-empty value.

**Call relations**: JiraConnector.render calls this when showing an issue assignee, issue reporter, or comment author. It uses _dict_or_empty and _str to avoid failures on missing or unexpected user data.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 268–269)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: This formats one metadata line, such as Status or Priority, only when there is a real value to show. It keeps the rendered page from containing empty labels.

**Data flow**: It receives a label and a text value. If the value is non-empty, it returns a line like "Status: Done"; otherwise it returns an empty string.

**Call relations**: JiraConnector.render calls this while assembling the issue metadata block. The render method then drops empty lines before joining the visible metadata together.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 272–289)

```
def _doc_text(value: Any) -> str
```

**Purpose**: This turns Jira’s Atlassian Document Format into plain text. Atlassian Document Format is a tree-like JSON structure for rich text, and this helper pulls out the readable text leaves.

**Data flow**: It receives any possible document value. It walks through dictionaries and lists, collects every string found under a text key, joins the collected pieces with newlines, trims extra whitespace, and returns the resulting plain text.

**Call relations**: JiraConnector.render calls this for issue descriptions and comment bodies. Inside the function, the nested walk helper does the tree traversal.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 277–286)

```
def walk(node: Any) -> None
```

**Purpose**: This is the recursive worker inside _doc_text. It visits each branch of a Jira rich-text document and collects the actual text pieces.

**Data flow**: It receives one node from the document tree. If the node is a dictionary, it saves its text value when present and then visits its content children; if the node is a list, it visits each item in the list; other values are ignored.

**Call relations**: _doc_text starts the traversal by calling walk on the original document value. walk then calls itself for child nodes until the whole document tree has been searched.


### Modern work and knowledge workspaces
Reads project, board, item, page, database, comment, and block data from Linear, monday.com, and Notion into searchable records.

### `extensions/sources/ufo_ext_sources/providers/linear.py`

`io_transport` · `source sync`

Linear is a work-tracking tool, and its API is built around GraphQL, a query language where the client asks for exactly the fields it wants. This file is the Linear-specific adapter for the project’s connector framework. Without it, the system would not know which Linear objects to request, how to page through long result lists, or how to turn raw issue and project data into readable text.

The file first defines the streams of data Linear can provide. A stream is one category of records, like issues or users. Most streams can be synced incrementally: after the first run, the connector asks Linear only for records updated since the last saved timestamp. A few Linear collections do not support that kind of updated-time filter, so they are fully reread each run.

The large query strings are GraphQL requests for each stream. The connector sends them to Linear’s `/graphql` endpoint, follows Linear’s pagination cursor from page to page, and stops when Linear says there are no more pages. If Linear refuses access, the stream is marked as skipped instead of pretending it succeeded. If Linear reports GraphQL errors, the connector fails loudly so partial or misleading data is not saved.

Finally, the file customizes how important records are rendered. Issues, projects, comments, and users become human-readable notes with useful titles and short labeled fields instead of raw JSON dumps.

#### Function details

##### `_stream`  (lines 35–45)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one kind of Linear data, such as issues or projects. It keeps the stream setup consistent, especially around timestamps and whether the stream is considered a main searchable content stream.

**Data flow**: It receives a stream name, an optional cursor field, and a flag saying whether the stream is canonical. It fills in the standard Linear timestamp fields and returns a `StreamSpec`, which is the connector framework’s description of what this stream is and how it should be tracked.

**Call relations**: This function is used while building the file’s Linear stream catalog. It hands the finished stream description to the wider source framework by constructing a `StreamSpec`, so later sync code can treat all Linear streams in a uniform way.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 265–308)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Linear data. It asks Linear for one page of records at a time, follows the next-page cursor, and yields batches of records to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous run. It looks up the right GraphQL query, adds an `updatedAt` filter when the stream supports incremental syncing, sends requests to Linear, checks for refusal or GraphQL errors, extracts the returned `nodes`, and yields each non-empty page. It also reads `pageInfo.endCursor` to decide what page to request next, stopping when there is no valid next page.

**Call relations**: The connector framework calls this when it needs records for a Linear stream. Inside the loop, it relies on the shared connector posting behavior to talk to Linear, uses `list_or_empty` so missing node lists become a safe empty list, and raises `StreamSkipped` when Linear returns an access refusal so the larger run can record that this stream was skipped rather than successfully synced.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 310–352)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Linear records into readable page text. Instead of saving only raw API-shaped data, it gives issues, projects, comments, and users clear titles and simple human-friendly bodies.

**Data flow**: It receives one Linear record and the stream it came from. For issues and projects, it pulls out names, descriptions, state, owner-like references, and dates; for comments, it uses the comment body; for users, it uses names and email fields. It cleans unsafe values with `_str`, extracts referenced object IDs with `_ref_id`, builds labeled text with `_labeled`, and returns a title plus a Markdown-style body. If the record has no obvious title, it uses the first non-empty body line or falls back to the base connector’s default rendering.

**Call relations**: The sync system calls this after records have been fetched so they can become recallable pages. It delegates small formatting chores to `_str`, `_ref_id`, and `_labeled`, and for streams it does not specially understand, it hands the work back to the parent connector’s default renderer.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 355–356)

```
def _str(value: Any) -> str
```

**Purpose**: This small safety helper returns a value only if it is actually a string. It prevents accidental numbers, objects, or missing values from being inserted into rendered text.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged; otherwise it returns an empty string.

**Call relations**: `LinearConnector.render` uses this whenever it pulls text fields out of Linear records. `_ref_id` also uses it after finding an `id`, so referenced IDs are treated with the same safety rule.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 359–360)

```
def _ref_id(value: Any) -> str
```

**Purpose**: This helper extracts the `id` from a nested Linear reference, such as an assignee or project lead. It is used when the rendered page should mention the linked object without expanding the whole nested object.

**Data flow**: It receives any value. If the value is a dictionary-like record, it reads its `id` field and passes that through `_str`; otherwise it returns an empty string. The result is either a clean ID string or nothing.

**Call relations**: `LinearConnector.render` calls this while building labeled metadata for issues and projects. `_ref_id` relies on `_str` so a missing or non-text ID does not leak an unsuitable value into the rendered page.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 363–364)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This helper formats small pieces of metadata as simple `label: value` lines. It keeps rendered Linear pages tidy and skips labels whose value is empty.

**Data flow**: It receives a list of label-and-value pairs. It keeps only pairs with a non-empty value, turns each one into a line of text, and joins those lines with newline characters. The output is a compact block of readable metadata.

**Call relations**: `LinearConnector.render` calls this when it builds the metadata sections for issues, projects, and users. It supplies the small formatted blocks that are then placed above descriptions or other body text.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/monday.py`

`io_transport` · `source sync runs`

monday.com exposes its data through GraphQL, which is a query language where the client asks for exactly the fields it wants. This connector is the adapter between that GraphQL API and this project’s standard source framework. Without it, the system would not know how to page through monday.com data, recover from missing permissions, or shape monday records into the common form used by recallable pages.

The file defines the available monday.com streams, such as users, boards, items, updates, and activity logs. Each stream says what kind of object it reads, what field uniquely identifies a record, and, where possible, what timestamp is used for incremental syncing. Incremental syncing means “only bring back records newer than the last saved point.” monday.com does not provide a server-side “since this time” filter for these queries, so this connector fetches pages and filters old records locally.

The connector posts GraphQL queries to monday.com, unwraps the response, and treats GraphQL errors or permission refusals as skipped streams rather than silently saving incomplete data. Some data needs extra work: items are fetched board by board, activity logs are fetched per board, and people assigned to items are hidden inside monday column JSON, so the connector extracts those person IDs into a simpler list. Finally, `flatten` normalizes records by adding common fields like name, body, author, and parent IDs.

#### Function details

##### `_extract_person_ids`  (lines 70–98)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: This helper pulls assigned-person IDs out of monday.com item column data. monday stores assignments inside flexible board-specific columns, so this function looks for columns whose type is “people” and extracts only real person entries.

**Data flow**: It receives a value that should be a list of column records. It ignores anything that is not a list, skips columns that are not people columns, parses the column’s JSON value when needed, and collects entries marked as people. It returns a simple list of person IDs as strings and does not change the original input.

**Call relations**: When item records are being read, `MondayConnector._items` calls this helper for each item. The extracted IDs are then added to the item record as `assignee_ids`, making assignments easier for later parts of the system to use.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 106–118)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This is the connector’s basic GraphQL request helper. It sends one query to monday.com and returns the useful `data` part of the response, while turning monday GraphQL errors into a clean stream skip.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional variables. It posts that information to monday.com’s API endpoint, checks whether the response contains an `errors` array, and raises `StreamSkipped` if monday refused or could not answer the query. If the response is valid, it returns the `data` dictionary, or an empty dictionary if the data is missing or not shaped as expected.

**Call relations**: Most monday-specific fetchers rely on this helper instead of making raw HTTP calls themselves. `_paged_root`, `_items`, `_activity_logs`, and `_single_root` call it whenever they need to ask monday.com for a page or collection.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, _single_root).


##### `MondayConnector._paged_root`  (lines 120–143)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads top-level monday.com collections that use simple page numbers, such as users, workspaces, boards, and updates. It keeps asking for pages until monday.com returns no records.

**Data flow**: It receives the API client, the monday field to query, the field selection to request, and optionally a saved cursor plus the timestamp field to compare against. It asks for page 1, then page 2, and so on, turning the response into a safe list each time. If a cursor is provided, it keeps only records whose timestamp is newer than that cursor. It yields each non-empty page of records and stops when there are no records left after filtering.

**Call relations**: `_boards` uses this to gather all boards, and `_stream_pages` uses it directly for streams with straightforward paging. It delegates the actual GraphQL call to `_graphql`, so error handling stays consistent.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, _stream_pages); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 145–156)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper fetches the full list of monday.com boards needed by other streams. Boards are central in monday.com, so item and activity-log reads first need to know which boards exist.

**Data flow**: It receives the API client, asks `_paged_root` for all board pages with useful board and workspace fields, and appends every page into one list. It returns that combined board list.

**Call relations**: `_items` and `_activity_logs` call this before they fan out across boards. In other words, it is the “get the map of all boards first” step before reading board-specific data.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 158–217)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads monday.com items from every board. Items require special paging because monday.com uses a cursor, an opaque “next page” token, rather than simple page numbers.

**Data flow**: It receives the API client and an optional saved cursor for incremental syncing. First it fetches all boards. For each board, it asks monday.com for the first item page, then follows monday.com’s returned item cursor to fetch later pages. For every item, it extracts assignee IDs from column values. If a saved cursor is present, it keeps only items with a newer `updated_at` timestamp. It yields each non-empty batch of item records.

**Call relations**: `_stream_pages` calls this when the requested stream is `items`. This function uses `_boards` to decide which boards to scan, `_graphql` to make each monday.com request, and `_extract_person_ids` to simplify assignment data before records move on.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (_stream_pages); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 219–247)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads activity log entries from each monday.com board. Activity logs are board-specific, so it gathers them board by board and labels each log with the board it came from.

**Data flow**: It receives the API client and an optional saved cursor. It first fetches all boards, then queries monday.com for up to 100 activity logs for each board. It adds `board_id` to each log entry so the log can be linked back to its board. If a cursor is present, it keeps only logs whose `created_at` value is newer. It yields non-empty groups of log records.

**Call relations**: `_stream_pages` calls this for the `activity_logs` stream. It depends on `_boards` for the list of boards to inspect and on `_graphql` for each board query.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (_stream_pages); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 249–265)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the public paging entry used by the source framework for monday.com streams. It chooses the right stream reader and adds friendly handling for permission or authentication failures.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor from the last sync. It asks `_stream_pages` for pages belonging to that stream and yields them onward. If monday.com responds with HTTP 401 or 403, meaning the token is invalid or lacks permission, it raises `StreamSkipped` with a clear message instead of treating the whole run as a mysterious crash. Other HTTP errors are allowed to continue upward.

**Call relations**: The wider source-sync runner calls this when it wants records from a monday stream. `paginate` then hands the work to `_stream_pages`, while wrapping the process with monday-specific refusal handling.

*Call graph*: calls 2 internal fn (__init__, _stream_pages).


##### `MondayConnector._stream_pages`  (lines 267–305)

```
def _stream_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function is the stream router. Given a stream name like `users`, `items`, or `boards`, it picks the correct fetching method and query shape.

**Data flow**: It receives the API client, a stream name, and an optional cursor. It matches the name to the appropriate reader: simple paged roots for users, workspaces, boards, and updates; single-shot roots for teams and tags; board-by-board readers for items and activity logs. It returns an asynchronous page iterator that will produce lists of records. If the stream name is unknown, it raises `StreamSkipped` so the system records that the stream is not implemented.

**Call relations**: `paginate` calls this after the framework asks for a particular stream. `_stream_pages` then hands off to `_paged_root`, `_single_root`, `_items`, or `_activity_logs`, depending on what kind of monday.com data is being read.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _items, _paged_root, _single_root); called by 1 (paginate).


##### `MondayConnector._single_root`  (lines 307–318)

```
async def _single_root(self, client: httpx.AsyncClient, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads small monday.com collections that come back in one response rather than many pages. In this file, it is used for teams and tags.

**Data flow**: It receives the API client and the collection name. It builds the right GraphQL query for either teams or tags, sends it through `_graphql`, and checks that the returned value is a list. If there are records, it yields them as one page; if not, it yields nothing.

**Call relations**: `_stream_pages` calls this when the requested stream is `teams` or `tags`. It uses `_graphql` so it gets the same response unwrapping and GraphQL error behavior as the paged readers.

*Call graph*: calls 1 internal fn (_graphql); called by 1 (_stream_pages).


##### `MondayConnector.flatten`  (lines 320–361)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes monday.com records into a more common, searchable form used by the rest of the source system. It keeps the original fields but adds or standardizes useful fields like title, body, author, timestamps, and parent links.

**Data flow**: It receives one record and the stream definition it belongs to. Based on the stream name, it copies the record and fills in normalized fields: users keep name and email, boards and workspaces get an API URL, items get status, updates get body and author information, and activity logs get subject, body, author, and parent board ID. It returns the reshaped dictionary and does not write to monday.com.

**Call relations**: After pages have been fetched by `paginate` and its stream-specific readers, the source framework can call `flatten` to prepare each record for storage or indexing. For updates, it uses a safe dictionary helper to read creator details without failing on missing or malformed data.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/notion.py`

`io_transport` · `during source sync`

Notion stores useful writing in a nested, structured way: a page has properties, its body is made of blocks, blocks can contain child blocks, comments live separately, and text is often split into small “rich text” pieces. If the system simply saved Notion’s raw JSON, later recall would feel like reading a machine dump instead of a document. This connector solves that by fetching Notion records through the Notion API and rendering the important parts as prose.

The connector defines the streams it can read, such as users, pages, comments, and blocks. For normal Notion collections, it follows Notion’s paging system, asking for one batch at a time until there are no more results. For pages and data sources, it uses Notion search and filters by edit time so repeated syncs can pick up only newer changes. For blocks, it first finds pages, then walks down each page’s block tree, like opening folders inside folders, while stopping at a safe maximum depth and not descending into child pages or databases that should be treated as their own records. For comments, it finds pages and then asks for comments on each page.

It also adds the required Notion API version header to every client. If Notion refuses access because the integration lacks permission, the connector marks that stream as skipped instead of failing the whole run.

#### Function details

##### `NotionConnector._make_client`  (lines 80–83)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Notion. It adds the required Notion API version header so Notion knows which version of its API rules to use.

**Data flow**: It receives a base URL and a credential reference. It asks the parent connector to build the basic web client, then adds the Notion-Version header. It returns a ready-to-use asynchronous HTTP client for Notion requests.

**Call relations**: This is part of the connector setup before any records are fetched. Later fetching methods use the client it prepares, so they do not each have to remember to add the Notion version header.


##### `NotionConnector.paginate`  (lines 85–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main router for reading each Notion stream. Given a stream name, it chooses the right fetching method and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor that marks the last synced point. It checks which stream is being requested, calls the matching helper, and passes through each batch of records. If Notion says access is forbidden or unauthorized, it turns that into a clean “stream skipped” result instead of a crash.

**Call relations**: The sync system calls this when it wants records from Notion. It hands users to _collection, pages and data sources to _search, comments to _comments, and blocks to _blocks. If the stream is unknown or Notion refuses access, it raises StreamSkipped so the wider run can record the problem without treating it as a total failure.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 117–140)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads pages or data sources through Notion’s search endpoint. It asks Notion for results in edit-time order so incremental syncs can ignore records that are not newer than the saved cursor.

**Data flow**: It receives the HTTP client, the Notion object type to search for, and an optional cursor time. It repeatedly sends search requests, collects the results list safely, filters out records whose last edited time is not newer than the cursor, yields non-empty batches, and follows Notion’s next cursor until the search is done.

**Call relations**: paginate uses this directly for pages and data sources. _blocks and _comments also use it first to discover the pages whose block bodies or comments need to be read.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 142–152)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers the body blocks for every Notion page. It exists because a page’s readable body is not included as one flat page field; it must be fetched by walking the page’s block tree.

**Data flow**: It receives the HTTP client and an optional cursor time. It searches all pages, takes each valid page id, then asks _block_children to read that page’s child blocks. It yields each batch of blocks found under those pages.

**Call relations**: paginate calls this when the sync asks for the blocks stream. It depends on _search to find page ids first, then hands each page id to _block_children to do the recursive block walk.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 154–173)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through the children of a Notion block or page and yields the blocks it finds. It follows nested blocks, but stops before going too deep or entering special block types that should not be expanded here.

**Data flow**: It receives the HTTP client, a block id, the current nesting depth, and an optional cursor time. It fetches child blocks in paged batches, filters each batch by last edited time when a cursor is present, yields the newer blocks, then recursively visits children of blocks that are allowed to be expanded. It produces batches of block records and does not return a single combined list.

**Call relations**: _blocks starts this process for each page. This function calls _collection to fetch each page of child blocks, and then calls itself for deeper children, like a careful tour guide moving through a nested outline.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 175–191)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers comments attached to Notion pages. It reads pages first because Notion comments are requested page by page.

**Data flow**: It receives the HTTP client and an optional cursor time. It searches for pages, takes each valid page id, fetches comments for that page, filters comments by created time if a cursor is present, and yields any non-empty comment batches.

**Call relations**: paginate calls this for the comments stream. It uses _search to find pages and _collection to page through comments for each page.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 193–207)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared helper for Notion endpoints that return a paged collection of results. It hides the repeated work of following Notion’s start_cursor and next_cursor pagination fields.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It calls the connector’s generic cursor-paging helper with Notion’s field names, then yields each page of result records. Its output is a stream of record batches.

**Call relations**: paginate uses this for users, while _block_children uses it for block children and _comments uses it for page comments. It is the common paging adapter for Notion GET endpoints.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 209–231)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns raw Notion records into readable text for recall and search. Without it, pages, blocks, comments, and users would mostly appear as nested API data instead of the words a person actually sees in Notion.

**Data flow**: It receives one raw record and the stream it came from. Depending on the stream, it extracts a title, body text, property text, block text, comment text, or user details using helper functions. It returns a short title and a formatted text body headed with the Notion stream name.

**Call relations**: After records are fetched, the broader source system can call this to produce recallable content. It delegates the Notion-specific text extraction to _page_title, _properties_text, _block_text, _rich_text_text, _str, and _user_text, and falls back to the parent renderer for unknown streams or missing titles.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 234–235)

```
def _str(value: Any) -> str
```

**Purpose**: This small safety helper returns a value only if it is already a string. It prevents the rendering code from accidentally treating numbers, dictionaries, or missing values as usable text.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string. It does not change anything outside itself.

**Call relations**: Several text-extraction helpers call this when reading optional Notion fields. render, _property_text, _block_text, and _user_text use it to keep their output clean and predictable.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 238–246)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: This converts Notion rich text into normal readable text. Notion often stores a sentence as a list of small text runs, and this joins their plain_text pieces together.

**Data flow**: It receives any value that might be a Notion rich-text list. If it is a list, it takes the plain_text string from each valid item, joins them, trims extra space at the ends, and returns the result. If the input is not the expected shape, it returns an empty string.

**Call relations**: render and the helper functions rely on this whenever they need to read Notion text fields. _page_title, _property_text, and _block_text all use it to turn Notion’s structured text into prose.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 249–258)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: This finds the visible title of a Notion page. Page titles are stored inside the page’s properties, so this helper searches those properties for the title field.

**Data flow**: It receives a raw page record. It looks at the properties dictionary, finds the property whose type is title, extracts its rich text using _rich_text_text, and returns the first non-empty title it finds. If the page has no readable title, it returns an empty string.

**Call relations**: render calls this when formatting page records. It hands the actual rich-text extraction to _rich_text_text so title handling matches other Notion text handling.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 261–270)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion page’s properties into simple lines of text. It helps page records recall not just their title, but useful fields like status, dates, people, and custom text properties.

**Data flow**: It receives a page record. It reads the properties dictionary, converts each supported property value with _property_text, formats non-empty values as “name: value” lines, and joins those lines with newlines. If there are no usable properties, it returns an empty string.

**Call relations**: render calls this for page bodies. It relies on _property_text to understand each individual Notion property type.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 273–292)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: This extracts readable text from one Notion property. It knows the common property shapes, such as rich text, select options, dates, numbers, checkboxes, people, email, and URLs.

**Data flow**: It receives one property dictionary. It checks the property’s type, pulls the matching value field, converts supported kinds into a string, and returns an empty string for unsupported or missing values. For rich text it joins rich-text runs, and for names or emails it safely accepts only strings.

**Call relations**: _properties_text calls this for each property on a page. It uses _rich_text_text for Notion text runs and _str for optional string fields.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 295–305)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: This extracts the readable content from a Notion block. Blocks are the pieces of a page body, such as paragraphs, headings, to-do items, child page links, and similar content.

**Data flow**: It receives a raw block record. It finds the block’s type-specific content, returns a child page or child database title when that is the block type, joins rich text for normal text blocks, and formats to-do blocks with a checked or unchecked marker. If the block has no readable content, it returns an empty string.

**Call relations**: render calls this for records in the blocks stream. It uses _rich_text_text for block text and _str for child page or database titles.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 308–311)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: This formats a Notion user as simple readable text. It includes the user’s name and, when available, their email address.

**Data flow**: It receives a raw user record. It looks for the top-level name and the nested person.email value, keeps only values that are strings, and joins the present parts with a newline. It returns that short user description.

**Call relations**: render calls this for user records. It uses _str to avoid adding non-text or missing fields to the rendered output.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


### Incident and error operations
Ingests operational data from PagerDuty and Sentry, including incidents, services, schedules, projects, issues, events, releases, and members.

### `extensions/sources/ufo_ext_sources/providers/pagerduty.py`

`io_transport` · `source sync`

PagerDuty is an incident-management service, and its API returns information in separate “streams” such as users, incidents, and schedules. This file defines those streams and provides a connector that knows how to ask PagerDuty for each one. Without it, the system would not know which PagerDuty endpoints to call, how to page through large result sets, or how to continue from the last successful sync instead of rereading everything.

The connector is read-only. It does not create or change anything in PagerDuty. It uses an HTTP client, adds PagerDuty’s required API version header, then fetches records page by page. PagerDuty uses offset-based pagination, meaning the connector asks for a chunk of results, then moves forward by the page size until PagerDuty says there are no more pages.

Incidents get special treatment because they can be synced incrementally using their `updated_at` time. Incident notes are also special: PagerDuty exposes notes under each individual incident, so the connector first reads incidents, then asks for notes for each incident and attaches the incident id as context. If PagerDuty refuses access with a 401 or 403 response, the connector reports that stream as skipped rather than treating the whole run as broken. That matters because one token may be allowed to read some PagerDuty data but not all of it.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 74–77)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to PagerDuty and adds PagerDuty’s required `Accept` header, which tells the API which version and response format the connector expects.

**Data flow**: It receives a base API address and a credential reference. It asks the shared REST connector code to build the normal authenticated client, then adds the PagerDuty media-type header. It returns that prepared client, ready to make PagerDuty requests.

**Call relations**: This is part of the connector setup before any stream is read. The base REST connector supplies the common client behavior, and this method adds the PagerDuty-specific requirement so later pagination calls receive responses in the expected format.


##### `PagerDutyConnector._offset_pages`  (lines 79–99)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a normal PagerDuty collection endpoint one page at a time. It also filters out older records when a cursor is provided, so the sync can continue from a previous point.

**Data flow**: It takes an HTTP client, a stream description, optional query parameters, and an optional cursor value. It calls the shared offset-pagination helper with PagerDuty’s rules: records live under the stream’s response key, `more` says whether another page exists, and `limit` tells how far to advance. For each page, it optionally keeps only records whose cursor field is newer than the saved cursor, then yields non-empty pages of records.

**Call relations**: This is the common paging worker for most PagerDuty streams. `PagerDutyConnector._incidents` uses it with incident-specific sorting and filtering, while `PagerDutyConnector.paginate` uses it directly for simpler streams such as users, teams, services, schedules, and on-calls.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 101–113)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads PagerDuty incidents in update-time order so the system can sync them incrementally and reliably.

**Data flow**: It receives an HTTP client and an optional cursor. It builds request parameters that sort incidents by `updated_at` from oldest to newest. If a cursor is present, it also sends it as PagerDuty’s `since` parameter. It then uses `_offset_pages` to fetch incident pages and yields each page onward.

**Call relations**: This is the special incident reader used when `PagerDutyConnector.paginate` is asked for the incidents stream. `PagerDutyConnector._incident_notes` also calls it, because notes can only be fetched after the connector knows which incidents exist.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 115–128)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads notes attached to incidents. PagerDuty does not provide these as one simple global list, so the connector visits each incident and asks for that incident’s notes.

**Data flow**: It takes an HTTP client and an optional note cursor. First it reads incidents through `_incidents`, deliberately without an incident cursor so it can discover incident ids. For each valid incident id, it requests `/incidents/{id}/notes`, pulls the `notes` list out of the response, filters notes newer than the cursor when needed, and adds the incident id to each note so the note can still be traced back to its incident. It yields pages of enriched note records.

**Call relations**: This method is called by `PagerDutyConnector.paginate` when the requested stream is `incident_notes`. It relies on `_incidents` to find the parent incidents, uses `records_at` to extract the notes list from PagerDuty’s response, and uses `with_context` to attach the parent incident id before handing records back to the sync flow.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 130–164)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing method for reading a PagerDuty stream. Given a stream name, it chooses the right fetching strategy and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If the stream is incidents, it delegates to `_incidents`. If it is incident notes, it delegates to `_incident_notes`. If it is one of the regular collection streams, it delegates to `_offset_pages`. If the stream is unknown, it raises `StreamSkipped`, meaning the run should record that this stream was not read rather than pretending it succeeded.

**Call relations**: The wider source-sync runner calls this method when it wants records for a PagerDuty stream. This method then hands the work to the correct helper. If PagerDuty returns an HTTP 401 or 403 refusal, it converts that into `StreamSkipped` with a clear message, so a missing permission or invalid token is reported as a skipped stream instead of crashing the whole connector run.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/providers/sentry.py`

`io_transport` · `source sync`

This connector is the bridge between UFO’s source-sync system and Sentry’s web API. Without it, the system would not know which Sentry endpoints to call, how to follow Sentry’s page-by-page results, or how to label records with the organization and project they came from.

The file first defines the Sentry streams: the kinds of things that can be imported, such as organizations, projects, issues, and releases. Each stream says which field uniquely identifies a record and, when possible, which date field can be used as a cursor. A cursor is like a bookmark: it lets later syncs ask for only newer records instead of rereading everything.

The main class, `SentryConnector`, inherits shared REST connector behavior. It knows Sentry’s base API address and routes each requested stream to the right helper. Some streams are simple top-level lists, like organizations and projects. Others need a first pass to discover context: members and releases are fetched per organization, while issues and events are fetched per project. The connector adds that context back onto each record, so later parts of the system can tell where the record came from.

Sentry uses a `Link` response header to say whether another page exists. This file reads that header and keeps requesting pages until there are no more. If Sentry refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of treating the whole sync as a mysterious failure.

#### Function details

##### `_sentry_next_cursor`  (lines 76–81)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper reads Sentry’s pagination header and extracts the cursor for the next page, if there is one. It exists because Sentry does not put the “next page” marker in the response body; it hides it in an HTTP header.

**Data flow**: It receives the response headers from Sentry. It looks for a `link` or `Link` header, searches that text for a “next page with results” cursor, and returns the cursor string if found. If the header is missing or does not contain a usable next cursor, it returns `None`.

**Call relations**: During page fetching, `SentryConnector._paged_list` calls this after each Sentry response. The returned cursor tells `_paged_list` whether to ask Sentry for another page or stop.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.record_ref`  (lines 89–93)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This chooses a human-friendly reference for a Sentry record. For organizations, it prefers the organization slug because that is the short name people recognize; for other streams, it falls back to the shared connector behavior.

**Data flow**: It receives one record and the stream definition for that record. If the stream is `organizations`, it reads the record’s `slug` field and returns it as text when possible. For every other stream, it lets the parent REST connector decide the reference.

**Call relations**: This is used by the broader source system when it needs a stable, readable label for a record. It customizes only the Sentry organization case and otherwise stays aligned with the common REST connector behavior.


##### `SentryConnector.paginate`  (lines 95–107)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the public page-producing method for a Sentry stream. It asks the right lower-level helper for pages, and it turns Sentry permission failures into a clear “skip this stream” signal.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It delegates to `_stream_pages`, yields each list of records it gets back, and watches for HTTP status errors. If Sentry replies with 401 or 403, it raises `StreamSkipped` with an explanation; other errors are allowed to continue upward.

**Call relations**: The shared sync runner calls this when it wants records for a stream. `paginate` then hands the work to `_stream_pages`, while also protecting the rest of the sync from expected authorization refusals.

*Call graph*: calls 2 internal fn (__init__, _stream_pages).


##### `SentryConnector._stream_pages`  (lines 109–122)

```
def _stream_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the router that decides which Sentry-reading helper should serve a requested stream. It keeps the public pagination method simple by mapping stream names to the right fetching strategy.

**Data flow**: It receives an HTTP client, a stream name, and an optional cursor. For known stream names, it returns the matching async page iterator, such as `_members`, `_issues`, or `_root_pages`. If the stream name is unknown, it raises `StreamSkipped` to say this connector does not implement it.

**Call relations**: `SentryConnector.paginate` calls this after a stream is requested. `_stream_pages` then starts the appropriate branch of the connector: top-level lists, organization-based lists, or project-based lists.

*Call graph*: calls 6 internal fn (__init__, _events, _issues, _members, _releases, _root_pages); called by 1 (paginate).


##### `SentryConnector._root_pages`  (lines 124–137)

```
async def _root_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches the simple top-level Sentry streams: organizations and projects. These are called “root” pages because they do not need another object, like a project or organization, to be discovered first.

**Data flow**: It receives an HTTP client, the root stream name, and an optional cursor. It loads either all organizations or all projects, filters projects by `dateCreated` when a cursor is present, and yields the resulting list if there is anything to return.

**Call relations**: `SentryConnector._stream_pages` uses this for the `organizations` and `projects` streams. It relies on `_organizations` and `_projects` to do the actual API reading.

*Call graph*: calls 2 internal fn (_organizations, _projects); called by 1 (_stream_pages).


##### `SentryConnector._paged_list`  (lines 139–156)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the common loop for reading any Sentry endpoint that returns results over multiple pages. It is like repeatedly turning pages in a book until Sentry says there is no next page.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It sends a request, turns a list-shaped JSON response into a list of dictionary records, yields non-empty pages, then checks the response headers for the next cursor. If a cursor exists, it adds it to the next request; if not, it stops.

**Call relations**: Nearly every Sentry-specific reader calls this so they do not each need to repeat pagination logic. After each response, it asks `_sentry_next_cursor` whether another page should be fetched.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 158–162)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This collects all organizations visible to the Sentry credential. Other streams need this list because members and releases are fetched separately for each organization.

**Data flow**: It receives an HTTP client. It reads every page from the `/organizations/` endpoint through `_paged_list`, appends all records into one list, and returns that complete list.

**Call relations**: `SentryConnector._root_pages` calls this for the organizations stream. `_members` and `_releases` also call it first so they know which organization-specific endpoints to visit.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, _root_pages).


##### `SentryConnector._projects`  (lines 164–168)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This collects all projects visible to the Sentry credential. Issues and events are project-specific in Sentry, so this list is the starting map for those deeper reads.

**Data flow**: It receives an HTTP client. It reads every page from the `/projects/` endpoint through `_paged_list`, combines all pages into one list, and returns that complete list.

**Call relations**: `SentryConnector._root_pages` calls this for the projects stream. `_issues` and `_events` also call it first so they can visit each project’s issue or event endpoint.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, _root_pages).


##### `SentryConnector._members`  (lines 170–176)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches members for every accessible Sentry organization. It also stamps each member record with the organization slug so the record is not separated from its source organization later.

**Data flow**: It receives an HTTP client. It first loads organizations, skips any organization without a valid slug, then reads `/organizations/{slug}/members/` for each valid organization. Each yielded page is enriched with `organization_slug` before being passed onward.

**Call relations**: `SentryConnector._stream_pages` calls this when the requested stream is `members`. It depends on `_organizations` to discover where to look and `_paged_list` to read each organization’s member pages.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (_stream_pages); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 178–192)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches issues for every accessible Sentry project. If a cursor is available, it asks Sentry for only issues whose `lastSeen` value is newer than that cursor.

**Data flow**: It receives an HTTP client and an optional cursor. It loads all projects, extracts each project’s organization slug and project slug, skips projects that do not have both, and requests that project’s issues endpoint. Each page is enriched with both `organization_slug` and `project_slug` before it is yielded.

**Call relations**: `SentryConnector._stream_pages` calls this for the `issues` stream. It uses `_projects` to find the project endpoints and `_paged_list` to read them, then uses context tagging so later processing knows which project each issue belongs to.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (_stream_pages); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 194–208)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches events for every accessible Sentry project. Events are marked as non-canonical in the stream list, but they can still be read and recalled by the system.

**Data flow**: It receives an HTTP client and an optional cursor. It loads projects, finds each project’s organization slug and project slug, and skips incomplete project records. If a cursor exists, it asks Sentry for events with `event.timestamp` newer than that cursor, then yields each returned page with organization and project context added.

**Call relations**: `SentryConnector._stream_pages` calls this for the `events` stream. It follows the same project-discovery pattern as `_issues`, using `_projects` first and `_paged_list` for each project endpoint.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (_stream_pages); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 210–221)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches releases for every accessible Sentry organization. It filters by creation date locally when a cursor is present, because the code does not send that cursor as a Sentry query for releases.

**Data flow**: It receives an HTTP client and an optional cursor. It loads organizations, skips any without a valid slug, reads each organization’s releases endpoint, and when a cursor exists keeps only releases whose `dateCreated` value is newer. Non-empty release pages are enriched with `organization_slug` and yielded.

**Call relations**: `SentryConnector._stream_pages` calls this for the `releases` stream. It uses `_organizations` to find organization-specific release endpoints and `_paged_list` to walk through each endpoint’s pages.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (_stream_pages); 1 external calls (with_context).


### Wrike work management
Reads Wrike contacts, folders, tasks, comments, workflows, and custom fields as consistent source records.

### `extensions/sources/ufo_ext_sources/providers/wrike.py`

`io_transport` · `during Wrike source sync`

Wrike’s API returns information in pages, like a long list split across many screens. This file defines a Wrike connector that knows which Wrike lists can be read, how to ask for each page, and how to reshape some Wrike-specific fields into names the wider system understands.

The connector is read-only. It does not create or update anything in Wrike. It also does not hold the access token itself; authentication is supplied by the surrounding runner through an auth proxy. That keeps this file focused on reading Wrike data safely.

A key detail is pagination. Wrike wraps results inside a `data` list and provides a `nextPageToken` when more results are available. The connector follows that token until there are no more pages. Wrike does not provide a reliable “only send me recently changed items” filter, so the connector fetches pages and then locally drops records whose `updatedDate` is not newer than the stored cursor, also called a watermark.

If Wrike refuses access with a 401 or 403 response, the connector skips that stream with a clear reason instead of crashing the whole sync. Finally, `flatten` lightly standardizes records, for example turning a task title into `name` or pulling a contact email from nested profile data.

#### Function details

##### `_profile_email`  (lines 54–64)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper looks inside a Wrike contact record and finds the first usable email address in its profile list. It exists because Wrike stores email addresses inside nested profile objects rather than as one simple top-level field.

**Data flow**: It receives one contact record as a dictionary. It checks whether the record has a `profiles` list, walks through each profile that is itself a dictionary, and returns the first non-empty string found under `email`. If the structure is missing or no email is found, it returns `None`.

**Call relations**: This helper is used by `WrikeConnector.flatten` when contact records are being reshaped. The larger connector does the API reading, then hands each contact record to `flatten`, which calls `_profile_email` to copy a useful email value into a simple `email` field.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 72–96)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Wrike stream page by page and yields batches of records to the sync engine. It also skips streams that this connector does not actually implement, and turns common permission failures into a controlled “skip this stream” outcome.

**Data flow**: It receives an async HTTP client, a stream description, and an optional cursor value showing the newest record already seen before. It verifies that the stream is one of the supported Wrike streams, asks Wrike for pages using `nextPageToken`, extracts records from the `data` field, and filters out older records when the stream has an `updatedDate` cursor. It yields only non-empty batches. If Wrike responds with 401 or 403, it raises `StreamSkipped` with an explanation; other HTTP errors are allowed to continue upward as real failures.

**Call relations**: The surrounding source sync machinery calls this method when it wants records for a Wrike stream. Inside the method, the shared REST connector paging helper does the repeated HTTP requests. If a stream is not runnable or Wrike refuses access, `paginate` hands back a `StreamSkipped` signal so the wider sync can move on instead of treating the whole run as broken.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 98–136)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method reshapes raw Wrike records into friendlier, more consistent records for the rest of the system. It adds common fields such as `name`, `created_at`, `body`, or `parent_external_id` depending on whether the record is a contact, folder, task, or comment.

**Data flow**: It receives one raw Wrike record and the stream description that says what kind of record it is. For contacts, it builds a display name from first and last name and uses `_profile_email` to find an email. For folders, it maps the title to `name` and builds an API URL. For tasks, it reads nested date data safely with `dict_or_empty`, sets a name, status, due date, and creation time. For comments, it maps Wrike’s text and author fields into simpler fields. For streams without special rules, it returns the record unchanged.

**Call relations**: After `WrikeConnector.paginate` supplies raw batches from the Wrike API, the connector framework calls `flatten` to make each record easier for downstream indexing and recall. `flatten` delegates the contact email lookup to `_profile_email` and uses `dict_or_empty` so task date handling stays safe even when Wrike omits or changes the nested `dates` object.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).
