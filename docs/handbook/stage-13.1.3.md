# Project and work-management connectors  `stage-13.1.3`

This stage is shared behind-the-scenes support for bringing work-management data into the system. It is not the main user-facing work loop. Instead, it acts like a set of adapters for different project tools, so the rest of the product can read tasks, comments, teams, boards, and activity in one consistent way.

Each file knows how to talk to one outside service. The Asana connector reads projects, tasks, stories, users, and workspace details from Asana’s web API, which is a structured way for software to request data. ClickUp’s connector walks through its nested setup of teams, spaces, folders, lists, tasks, comments, fields, and goals. Jira gathers projects, issues, users, comments, boards, and sprints across accessible Jira sites. Linear reads issues, projects, teams, comments, and workflow information through GraphQL, another style of data request. monday.com brings in users, boards, items, updates, activity logs, and tags. Wrike covers contacts, folders, tasks, comments, workflows, and custom fields. Together, these connectors turn many different tool shapes into steady streams of records the system can store, search, and recall.

## Files in this stage

### Task and Workspace Streams
Connectors that turn task-centric workspace APIs into synced records for projects, users, tasks, comments, and related metadata.

### `extensions/sources/ufo_ext_sources/asana.py`

`io_transport` · `source sync`

Asana stores work-tracking information behind a web API. This file is the read-only bridge between that API and the UFO source framework. Without it, the system would not know which Asana objects to fetch, how to identify each record, or how to keep asking Asana for the next page of results.

The file first defines a small helper for describing an Asana “stream,” meaning one kind of object to sync, such as tasks or projects. Each stream says which Asana object it comes from, that records are keyed by Asana’s `gid`, and whether the stream is one of the main, useful work-tracking collections.

It then lists the Asana streams the connector supports. Some streams, like tasks and projects, can be fetched incrementally by asking Asana for records changed since a previous timestamp. Others are refreshed in full each time because Asana does not support the same incremental filter for them.

The `AsanaConnector` class provides the actual connector identity, base API address, and pagination behavior. Pagination is like reading a book one page at a time: the connector asks for up to 100 records, yields them to the caller, then follows Asana’s `next_page.offset` token until there are no more pages. The connector does not store credentials or write anything back to Asana; it only reads through the shared REST source machinery.

#### Function details

##### `_stream`  (lines 24–38)

```
def _stream(name: str, *, cursor_field: str | None=None, updated_at_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard stream description for one kind of Asana object, such as tasks or users. This keeps the stream list short and consistent, so every Asana collection is described in the same way.

**Data flow**: It receives the stream name plus optional timestamp fields and a flag saying whether the stream is a main, canonical one. It builds a `StreamSpec`, which is the source framework’s plain description of what to fetch, what field uniquely identifies each record, and what timestamp can be used for syncing. The result is returned and placed into the connector’s stream catalog.

**Call relations**: This helper is used while the module defines `ASANA_STREAMS`. It hands its inputs to `StreamSpec` so the broader source framework can later discover which Asana streams exist and how each should be read.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 74–90)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one Asana stream page by page and yields batches of records to the sync system. It also applies incremental fetching for the Asana streams that support a `modified_since` filter.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor timestamp. It builds the Asana API path and query parameters, asks Asana for a page, safely pulls the `data` list out of the response, and yields that list if it contains records. Then it reads Asana’s `next_page.offset`; if there is another offset, it repeats with that offset, and if not, it stops. The function outputs batches of dictionaries and does not change Asana.

**Call relations**: The REST connector framework calls this method when it needs records for a particular Asana stream. Inside the loop, it relies on the inherited `_get` request helper to make the HTTP call, and it uses `list_or_empty` so a missing or non-list `data` field becomes an empty batch instead of breaking the sync.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/clickup.py`

`io_transport` · `during ClickUp sync`

ClickUp stores work like a set of nested boxes: teams contain spaces, spaces contain folders and lists, and lists contain tasks, comments, and custom fields. This connector walks through those boxes in the right order so nothing lower down is missed. Without this file, the system would not know how to find ClickUp lists or tasks, because there is no single flat ClickUp endpoint that returns everything at once.

The file defines the ClickUp streams the system can read, then implements `ClickUpConnector`, a read-only connector based on `RestConnector`. It uses ClickUp’s HTTP API with an OAuth bearer token supplied elsewhere by the credential system. The connector starts at teams, then discovers spaces, folders, and lists. Once it has lists, it can fetch the leaf-level data: tasks, list comments, and list custom fields. As it goes, it adds parent information such as `team_id`, `space_id`, `folder_id`, or `list_id`, so later code can tell where each record came from.

For large or changing data, it supports cursor filtering. A cursor is a saved “last seen” value, like a bookmark in a book. Tasks are filtered by `date_updated`, and comments can be filtered by their date. The connector only reads data; it deliberately does not create or update anything in ClickUp.

#### Function details

##### `ClickUpConnector._teams`  (lines 58–60)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the ClickUp teams available to the authenticated account. Teams are the starting point for almost every other ClickUp lookup, so this is the connector’s first step into the hierarchy.

**Data flow**: It receives an HTTP client that can call ClickUp. It asks ClickUp for `/team`, then pulls the list stored under the `teams` key from the response. It returns that list of team records, or an empty list if the response does not contain usable team data.

**Call relations**: This is the root helper for hierarchy discovery. `_spaces` calls it when it needs team IDs before looking for spaces, and `paginate` calls it directly when the requested stream is teams, users, or goals.

*Call graph*: called by 2 (_spaces, paginate); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 62–70)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived spaces inside all available ClickUp teams. A space is the next level down from a team, so this function expands the sync from teams into the areas where work is organized.

**Data flow**: It starts with an HTTP client, calls `_teams` to get team records, and reads each valid team ID. For each team, it asks ClickUp for that team’s spaces, ignores archived spaces, extracts the `spaces` records, and adds the parent `team_id` to each one. It returns one combined list of space records.

**Call relations**: This function continues the top-down walk started by `_teams`. `_folders`, `_lists`, and `paginate` call it when they need spaces either as their own stream or as the parent level for deeper ClickUp objects.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 72–82)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived folders inside all discovered ClickUp spaces. Folders are one of the paths that lead to lists, so this function helps uncover where tasks may live.

**Data flow**: It receives an HTTP client, calls `_spaces` to get space records, and reads each valid space ID. For every space, it requests that space’s folders from ClickUp, extracts the `folders` records, and adds the parent `space_id` to each record. It returns all discovered folders as one list.

**Call relations**: This function sits between spaces and lists in the hierarchy. `_lists` calls it to discover folder-based lists, and `paginate` calls it directly when the folders stream is being synced.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 84–100)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived lists, both inside folders and directly inside spaces. Lists matter because tasks, comments, and custom fields are fetched per list.

**Data flow**: It receives an HTTP client and builds a combined output list. First it calls `_folders`, reads each folder ID, requests that folder’s lists, and stamps each list with its `folder_id`. Then it calls `_spaces`, reads each space ID, requests lists that live directly in the space without a folder, and stamps those records with `space_id`. It returns all list records it found.

**Call relations**: This is the bridge from ClickUp’s container structure to the actual work items. `_tasks` and `_list_child_stream` depend on it because they must know list IDs before fetching tasks, comments, or fields. `paginate` also calls it when syncing the lists stream itself.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _tasks, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 102–127)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches tasks from every discovered ClickUp list, page by page. It can skip tasks that are not newer than a saved cursor, so repeated syncs do not have to resend old task data.

**Data flow**: It receives an HTTP client and an optional cursor value. It calls `_lists` to find every list, then requests tasks from each valid list ID using ClickUp’s page number parameter. Each task is stamped with the list ID and list name. If a cursor is present, only tasks whose `date_updated` is later than that cursor are kept. It yields batches of task records as they are found, and stops paging a list when ClickUp returns no task records.

**Call relations**: This helper is used by `paginate` when the tasks stream is requested. It relies on `_lists` for the list IDs, uses shared helpers to extract records and add list context, and hands completed task batches back to the sync loop through asynchronous yields.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 129–148)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches list-level child data, currently list comments and list custom fields. These are not fetched globally in ClickUp, so the connector must ask for them one list at a time.

**Data flow**: It receives an HTTP client, the stream being synced, and an optional cursor. It calls `_lists` to get list IDs, chooses the correct ClickUp endpoint for either comments or custom fields, extracts the right response key, and adds the list ID and list name to each record. If the stream has a cursor field and a cursor was provided, it keeps only records newer than that cursor. It yields each non-empty batch of records.

**Call relations**: This function is called by `paginate` for the `list_comments` and `list_custom_fields` streams. It shares the same list-discovery path as tasks, then returns the child records in batches for the main sync machinery to consume.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 150–204)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses how to read each ClickUp stream and yields records in batches. It is the main dispatcher the wider sync system calls when it wants data from ClickUp.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name and sends the work to the matching helper: teams, spaces, folders, lists, tasks, list child streams, or goals. For users, it builds a user list by looking inside each team’s members and de-duplicating by user ID. For goals, it fetches goals per team and adds the team ID. It yields batches when records exist. If the stream name is unknown, it raises `StreamSkipped`, meaning this connector does not implement that stream.

**Call relations**: This is the connector’s central routing point. The sync framework calls it for a stream, and it calls the smaller discovery and fetch helpers in the order ClickUp requires. Those helpers return or yield records, and `paginate` passes them outward to the rest of the source-sync pipeline.

*Call graph*: calls 7 internal fn (__init__, _folders, _list_child_stream, _lists, _spaces, _tasks, _teams); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 206–240)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes ClickUp records into fields the rest of the system expects, such as `name`, `email`, `created_at`, `status`, or `body`. It makes records from different ClickUp endpoints easier to display, search, and compare.

**Data flow**: It receives one raw ClickUp record and the stream it belongs to. For users, it chooses a display name and creation time. For spaces, folders, and lists, it ensures there is a name and API URL. For tasks, it simplifies the status and creation date. For list comments, it extracts the message body, author, creation time, and parent list ID. For streams that need no special cleanup, it returns the record unchanged.

**Call relations**: After `paginate` has fetched raw records, the broader connector machinery can call `flatten` to shape each record into a more consistent form. It uses `dict_or_empty` only when reading a comment’s user object, so missing or malformed user data does not break comment normalization.

*Call graph*: 1 external calls (dict_or_empty).


### Issue and Agile Tracking
Connectors for issue-tracking systems that expose projects, issues, teams, workflows, boards, sprints, comments, and readable work-item text.

### `extensions/sources/ufo_ext_sources/jira.py`

`io_transport` · `source sync runs`

This file is the Jira “source connector.” Its job is to bring information out of Atlassian Jira Cloud and into the larger system as pages that can later be recalled or searched. Without it, the system would not know where Jira data lives, how Jira splits results into pages, or how to turn Jira’s nested JSON into readable text.

The connector starts from the OAuth grant, which is the user-approved permission token. Atlassian first tells it which Jira sites that grant can reach. For each site, the connector calls the right Jira API paths for projects, issues, comments, users, boards, and sprints. Most Jira lists arrive in chunks, like reading a long book a page at a time, so the connector repeatedly asks for the next page until Jira says there is no more.

Some streams are incremental. For issues, comments, and sprints, the connector can use a saved “cursor,” meaning the last update time seen before, so it only asks for newer items where possible. If Jira says the grant is not allowed to see a site or resource, the connector marks that stream as skipped instead of failing the whole sync.

The file also makes Jira records easier for people to read. Issues become a title plus status, priority, assignee, reporter, and description text. Comments become the author and comment body. Jira stores rich text as Atlassian Document Format, a tree-shaped document, and this file walks that tree to extract plain text.

#### Function details

##### `JiraConnector.paginate`  (lines 73–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading a Jira stream. Given a stream name such as issues or users, it calls the matching helper that knows how to fetch that kind of Jira data.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor time. It checks the stream name, asks the right helper to produce pages of records, and yields those pages back to the sync runner. If Jira replies with “not allowed” or “not logged in,” it turns that into a clean skipped-stream result instead of a hard failure.

**Call relations**: The wider sync system calls this when it wants records for one Jira stream. This function then hands off to _projects, _issues, _comments, _users, _boards, or _sprints. If the stream is unknown, or Jira refuses access, it uses StreamSkipped so the rest of the run can continue safely.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 106–110)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Jira Cloud sites the current approved account can reach. Those site IDs are needed before any project, issue, user, or board API call can be made.

**Data flow**: It receives an HTTP client and calls Atlassian’s accessible-resources endpoint. It reads the JSON response, makes sure it is treated as a list, and returns that list of site records. Each usable site record may contain an ID, also called a cloud ID, and a URL.

**Call relations**: _projects, _issues, _users, and _boards call this before reading their own data. It acts like the map at the entrance of a building: first find which Jira sites are available, then visit each one.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 112–132)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira API lists that are split into numbered pages. It keeps asking for the next chunk until Jira indicates the list is finished.

**Data flow**: It receives an HTTP client, an API path, optional query settings, and the response field where records are expected. It sends requests with startAt and maxResults values, extracts records from the response, yields non-empty batches, and stops when there are no more records or Jira says the last page has been reached.

**Call relations**: The stream-specific readers use this whenever Jira returns a paged collection. _projects, _issues, _comments, _boards, and _sprints rely on it so they do not each have to repeat the same paging loop.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 134–141)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira projects from every reachable Jira site. A project is a workspace or container where Jira issues live.

**Data flow**: It asks _sites for all accessible Jira sites. For each site with a valid cloud ID, it calls the project search API through _offset_values. Each page of project records is then enriched with the site’s cloud ID and URL before being yielded.

**Call relations**: paginate calls this when the sync runner asks for the projects stream. It uses _sites to know where to look, _offset_values to read paged results, and with_context to attach site information that later steps may need.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 143–155)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira issues, optionally only those updated after the last saved cursor. An issue is the core Jira item, such as a bug, task, or story.

**Data flow**: It receives an HTTP client and optional cursor. It builds a Jira Query Language request, which is Jira’s search syntax, ordering issues by update time and filtering after the cursor when present. It then visits every accessible site, reads issue pages from Jira search, asks for selected useful fields, adds site context, and yields pages of issues.

**Call relations**: paginate calls this for the issues stream. _comments also calls it without a cursor so it can discover the issues whose comments should be checked. It relies on _sites for site discovery, _offset_values for paging, and with_context to keep each issue tied to its Jira site.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 157–177)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads comments attached to Jira issues. Because Jira comments are found under individual issues, it first finds issues and then visits each issue’s comment list.

**Data flow**: It receives an HTTP client and optional cursor. It reads all issues, takes each issue ID and cloud ID, calls that issue’s comments endpoint page by page, filters out comments not newer than the cursor when a cursor exists, adds context such as site, issue ID, and issue key, and yields comment batches.

**Call relations**: paginate calls this for the issue_comments stream. It depends on _issues to find the parent issues and _offset_values to read each issue’s paged comments. It uses with_context so a comment does not become detached from the issue it belongs to.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 179–190)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira users from every reachable site. Users identify people such as assignees, reporters, and comment authors.

**Data flow**: It asks _sites for accessible Jira sites. For each valid site, it calls Jira’s users/search endpoint once with a page size limit, reads the JSON array of users, converts missing or invalid content into an empty list, adds site context, and yields the users if any were returned.

**Call relations**: paginate calls this for the users stream. Unlike most other Jira collections in this file, this endpoint is treated as a plain JSON array rather than a wrapped paged response, so it uses list_or_empty directly instead of _offset_values.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 192–199)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira agile boards from every reachable site. Boards are Jira’s visual planning surfaces, such as Scrum or Kanban boards.

**Data flow**: It asks _sites for accessible Jira sites. For each site with a valid cloud ID, it calls the agile board API using _offset_values, adds the site cloud ID and URL to each page of board records, and yields those pages.

**Call relations**: paginate calls this for the boards stream. _sprints also calls it because sprints are found under boards. The function uses _sites for site discovery, _offset_values for paging, and with_context to preserve where each board came from.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 201–215)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads sprints from Jira boards, optionally keeping only sprints updated after a saved cursor. A sprint is a time-boxed work period used by agile teams.

**Data flow**: It receives an HTTP client and optional cursor. It first reads boards, then for each board with a usable board ID and cloud ID, it calls the board’s sprint endpoint. It filters sprints by updatedDate when a cursor is present, adds the cloud ID and board ID, and yields non-empty sprint batches.

**Call relations**: paginate calls this for the sprints stream. It depends on _boards to find where sprints live and on _offset_values to read each board’s sprint pages. It adds board context so each sprint remains linked to its source board.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 217–224)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This normalizes Jira records before the sync system tracks them. Its special job is to copy an issue’s nested updated time to the top level where the cursor logic expects it.

**Data flow**: It receives one record and its stream description. For issue records, it safely reads the fields object and returns a copy of the record with an added top-level updated value. For all other streams, it returns the record unchanged.

**Call relations**: The connector framework calls this while preparing records for storage and cursor tracking. It uses _dict_or_empty to avoid errors if Jira sends an unexpected fields value.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 226–252)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Jira records into human-readable page text. It gives issues and comments useful titles and bodies instead of leaving them as raw nested JSON.

**Data flow**: It receives a record and its stream description. For issues, it extracts the summary, status, priority, assignee, reporter, and rich-text description, then builds a readable page. For issue comments, it extracts the author and comment body. Other streams are passed to the parent connector’s default renderer.

**Call relations**: The connector framework calls this when it needs display text for stored records. It uses helper functions to safely read strings and dictionaries, format people and field lines, and convert Jira’s document tree into plain text.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 255–256)

```
def _str(value: Any) -> str
```

**Purpose**: This small helper returns a value only if it is actually a string. It prevents accidental numbers, dictionaries, or missing values from being treated as display text.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string. Nothing outside the return value is changed.

**Call relations**: render and _person use this when building readable titles and metadata. It is a safety guard around data coming from Jira, where fields may be missing or shaped differently than expected.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 259–260)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This helper returns a dictionary only when the input really is a dictionary. It lets the rest of the file safely look inside nested Jira objects.

**Data flow**: It receives any value. If the value is a dictionary, it returns it; otherwise it returns an empty dictionary. This means later code can call .get without crashing.

**Call relations**: flatten, render, and _person call this before reading nested fields such as issue fields, status, priority, assignee, or reporter.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 263–265)

```
def _person(value: Any) -> str
```

**Purpose**: This turns a Jira person object into a readable name. It prefers the display name and falls back to the email address.

**Data flow**: It receives a value that may or may not be a Jira user object. It safely treats it as a dictionary, reads displayName first, then emailAddress, and returns the first usable string or an empty string.

**Call relations**: render uses this when showing an issue’s assignee and reporter, and when titling a comment by its author. It relies on _dict_or_empty and _str so malformed or missing person data does not break rendering.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 268–269)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: This formats one labeled metadata line, such as “Status: Done,” but only when there is a value to show. It keeps rendered pages from filling up with empty labels.

**Data flow**: It receives a label and a value string. If the value is not empty, it returns the label and value joined in a simple line; if the value is empty, it returns an empty string.

**Call relations**: render calls this while building the issue metadata block. The returned lines are then joined together above the issue description.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 272–289)

```
def _doc_text(value: Any) -> str
```

**Purpose**: This extracts readable plain text from Jira’s rich-text document format. Jira descriptions and comments are stored as a tree of nodes, so this function walks the tree and collects the text pieces.

**Data flow**: It receives any value that might be an Atlassian Document Format document. It walks through dictionaries and lists, collects every string found in a text field, joins the collected pieces with newlines, trims the result, and returns the plain text. If the input is missing or not a document tree, the result is empty text.

**Call relations**: render calls this for issue descriptions and comment bodies. Inside it, the nested walk function does the actual tree traversal, like checking every branch of a tree for leaves with words on them.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 277–286)

```
def walk(node: Any) -> None
```

**Purpose**: This inner helper does the recursive walk through a Jira rich-text document. Recursive means it can call itself to inspect smaller pieces inside the current piece.

**Data flow**: It receives one node from the document tree. If the node is a dictionary, it collects its text field when present and then walks each child in its content list. If the node is a list, it walks each item in the list. It adds found text into the surrounding _doc_text chunks list and does not return a separate value.

**Call relations**: _doc_text starts the process by calling walk on the whole input value. walk then keeps handing off to itself for child nodes until every nested part has been checked.


### `extensions/sources/ufo_ext_sources/linear.py`

`io_transport` · `source sync runs`

Linear is a project and issue tracking tool. This connector is the bridge between Linear and the rest of the source-sync system: it knows which Linear collections exist, which GraphQL query to use for each one, how to page through results, and how to avoid rereading everything when Linear supports filtering by update time.

The file defines the Linear stream catalog, including main content streams like issues, projects, comments, users, and project milestones, plus supporting metadata streams like labels, workflow states, teams, and customer tiers. Most streams can be synced incrementally, meaning the connector asks Linear for only records updated since the previous run. A few Linear collections do not support that filter, so those are read in full each time.

The main class, `LinearConnector`, sends GraphQL requests to Linear’s `/graphql` endpoint. It asks for one page of records, yields those records to the sync system, then follows Linear’s `pageInfo.endCursor` to request the next page. If Linear refuses access with an authorization error, the stream is marked as skipped instead of crashing the whole source run. If Linear reports GraphQL errors, the connector stops loudly so bad or partial data is not silently accepted.

The file also improves how important records are displayed. Instead of storing only raw API-shaped data, it renders issues, projects, comments, and users as simple text with useful headings and labels.

#### Function details

##### `_stream`  (lines 32–42)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard stream description for one Linear collection. It saves the file from repeating the same setup details, such as which field marks creation time and which field tracks updates.

**Data flow**: It receives a stream name, an optional cursor field, and a flag saying whether this is a main content stream. It packages those choices with Linear’s common timestamp fields into a `StreamSpec`, which the rest of the sync system uses as the stream’s instruction card.

**Call relations**: At module load time, the Linear stream list is built by calling this helper for each supported Linear collection. The helper hands each finished stream description to the connector framework through `StreamSpec`, so later sync code can treat all Linear streams in a consistent way.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 269–312)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the reader for Linear data. Given one stream, it repeatedly asks Linear’s GraphQL API for pages of records until there are no more pages to read.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. It looks up the GraphQL query for that stream, adds update-time filtering when Linear supports it, sends a request, checks for access refusals or GraphQL errors, pulls the record list out of the response, and yields each non-empty page. After each page, it reads Linear’s next-page cursor and uses it in the next request; when Linear says there is no next page, it stops.

**Call relations**: The connector framework calls this method when it wants records for a Linear stream. Inside the flow, it relies on the shared REST connector’s POST behavior for the actual network request, uses `list_or_empty` to safely normalize the returned node list, and raises `StreamSkipped` when Linear says the token is invalid or lacks permission so the larger run can record a clean skip.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 314–353)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Linear records into readable page text. It gives issues, projects, comments, and users clear titles and human-friendly summaries instead of leaving them as raw nested API data.

**Data flow**: It receives one Linear record and the stream it came from. For issues and projects, it extracts useful labels such as state, priority, assignee, lead, or target date, then combines them with the description. For comments, it uses the comment body. For users, it builds a small contact-style summary. It returns a title plus the final page text; for streams it does not customize, it falls back to the base connector’s rendering.

**Call relations**: The sync system calls this after records are fetched, when it needs content that can be stored or searched as a page. This method delegates small cleanup tasks to `_str`, `_ref_id`, and `_labeled`, which keep missing or oddly shaped fields from leaking into the rendered text.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 356–357)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely turns a value into text only when it is already a string. It prevents missing values, numbers, dictionaries, or other shapes from appearing as unwanted Python-style text in rendered pages.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged; otherwise it returns an empty string. Nothing outside the return value is changed.

**Call relations**: `LinearConnector.render` uses this whenever it pulls optional text from a Linear record. `_ref_id` also uses it after extracting an `id`, so reference fields get the same safe text cleanup.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 360–361)

```
def _ref_id(value: Any) -> str
```

**Purpose**: This helper extracts the `id` from a small referenced object, such as an assignee or project lead. Linear often represents links to other records as nested objects, and this turns those links into a simple identifier for display.

**Data flow**: It receives any value. If the value is a dictionary-like record reference, it reads its `id` field and passes that through `_str`; if not, it returns an empty string. The output is either a clean id string or nothing.

**Call relations**: `LinearConnector.render` calls this when building readable labels for linked people or records. `_ref_id` depends on `_str` so that even an unexpected non-text id is safely ignored.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 364–365)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This helper formats a short list of label-and-value pairs into simple lines like `state: started`. It skips empty values so the rendered text does not contain blank or misleading labels.

**Data flow**: It receives a list of pairs, where each pair has a label and a text value. It keeps only pairs with a non-empty value, formats each as `label: value`, joins them with newlines, and returns the resulting block of text.

**Call relations**: `LinearConnector.render` uses this to build the metadata sections for issues, projects, and users. It acts like a small formatting step between raw fields from Linear and the final page text returned to the sync system.

*Call graph*: called by 1 (render).


### Board and Work Management Suites
Connectors for broader work-management platforms that sync boards, folders, tasks, items, updates, activity, workflows, teams, and custom fields.

### `extensions/sources/ufo_ext_sources/monday.py`

`io_transport` · `during monday.com source sync`

monday.com exposes its data through GraphQL, which is a query language where the client asks for exactly the fields it wants. This connector is the project’s read-only bridge to that API. Without it, monday.com content could not be pulled into the system as recallable pages.

The file defines the monday streams the sync system knows about, such as boards, items, and updates. Each stream says what kind of object it reads, what field uniquely identifies a record, and, for changing data, which timestamp acts like a bookmark for incremental syncing.

The connector sends GraphQL queries, unwraps the returned data, and treats monday error responses as skipped streams instead of pretending a partial result is complete. It also treats permission failures, like missing access or an invalid token, as skips with a clear reason.

Pagination is a major part of the work. Some monday collections use simple page numbers, while board items use a special cursor, like a “next page ticket” handed back by the API. For streams that support incremental sync, monday does not filter by date on the server, so this file filters each received page locally after comparing timestamps to the saved cursor.

Finally, records are flattened into friendlier shapes. For example, item assignees are extracted from monday’s board-specific column data, and updates get a plain author and parent item id.

#### Function details

##### `_extract_person_ids`  (lines 70–98)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: This helper pulls user ids out of monday.com item columns that represent assigned people. monday stores assignments inside a JSON blob in columns whose exact names vary by board, so this function looks for the stable clue: columns marked as people columns.

**Data flow**: It receives the raw column values from a monday item. It ignores anything that is not a list, skips columns that are not people columns, parses the column value if it is JSON text, and collects entries marked as individual people rather than teams. It returns a simple list of person ids as strings and does not change anything outside itself.

**Call relations**: When MondayConnector._items reads item records, it calls this helper for each item so the final item record includes a clear assignee_ids list. Internally, this helper uses JSON parsing when monday has stored the column value as text.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 106–118)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This is the connector’s common doorway for sending a GraphQL request to monday.com. It hides the repeated work of posting a query, checking for GraphQL errors, and returning only the useful data section.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional variables for that query. It sends them as a POST request to monday.com’s API root, checks whether monday returned an errors list, and raises a skipped-stream signal if so. If the response contains a data object, it returns that object; otherwise it returns an empty dictionary.

**Call relations**: The pagination and stream-specific readers call this whenever they need data from monday.com. If monday refuses or cannot provide a query result, this function turns that into StreamSkipped so the larger sync can record a clean skip instead of saving incomplete data.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, paginate).


##### `MondayConnector._paged_root`  (lines 120–143)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads top-level monday.com collections that use ordinary page numbers, such as users, workspaces, boards, and updates. It keeps asking for page 1, page 2, and so on until monday returns no usable records.

**Data flow**: It receives the GraphQL field to read, the field selection to request, and optionally a saved cursor timestamp plus the record field to compare against it. For each page, it fetches up to 100 records, normalizes missing or malformed results into an empty list, optionally removes records older than or equal to the cursor, and yields each non-empty page. When no records remain after filtering, it stops.

**Call relations**: MondayConnector._boards uses this to gather all boards. MondayConnector.paginate uses it directly for several streams. It relies on MondayConnector._graphql for the actual API call and on list_or_empty to safely treat unexpected response shapes as empty lists.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, paginate); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 145–156)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper collects all boards from monday.com with the fields needed by other streams. Boards are the starting point for board-specific data such as items and activity logs.

**Data flow**: It receives an HTTP client. It asks _paged_root to fetch every page of boards, adds each page’s records to one output list, and returns that complete list. It does not apply an incremental cursor because callers need the full board list to know where to look next.

**Call relations**: MondayConnector._items and MondayConnector._activity_logs call this first so they can loop through each board. It delegates the actual page-by-page GraphQL work to MondayConnector._paged_root.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 158–217)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads items from every monday.com board. Items use monday’s cursor-style pagination, where the API returns a token that must be passed back to get the next batch.

**Data flow**: It receives an HTTP client and an optional saved timestamp cursor. First it fetches all boards, then for each board it requests the first item page. If monday returns a next-page cursor, it keeps requesting more item pages with that cursor. For every item, it extracts assignee ids from the column values, optionally filters out items that are not newer than the saved cursor, and yields each non-empty batch of item records.

**Call relations**: MondayConnector.paginate calls this when the selected stream is items. This function depends on MondayConnector._boards to know which boards to inspect, MondayConnector._graphql to fetch each item page, _extract_person_ids to make assignments easier to use, and small safety helpers to treat missing objects or lists as empty rather than crashing.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (paginate); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 219–247)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads recent activity log entries from each monday.com board. Activity logs describe events that happened on a board, such as changes or other tracked actions.

**Data flow**: It receives an HTTP client and an optional saved timestamp cursor. It gets the full board list, queries activity logs for each board, attaches the board id to every log entry so the log keeps its context, filters out old entries when a cursor is supplied, and yields any remaining records.

**Call relations**: MondayConnector.paginate calls this for the activity_logs stream. It uses MondayConnector._boards to find the boards to inspect and MondayConnector._graphql to ask monday.com for each board’s logs.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (paginate); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 249–323)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing function for reading monday.com streams. Given a stream name, it chooses the right query and yields records in pages so the sync runner can process them steadily instead of needing everything at once.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks the stream name, runs the matching GraphQL query or helper, applies cursor-aware helpers where needed, and yields pages of records. If a stream is unknown, it raises a skipped-stream signal. If monday returns an HTTP 401 or 403, meaning unauthorized or forbidden, it raises a skipped-stream signal explaining that the token or grant likely lacks access.

**Call relations**: The source sync framework calls this to fetch data for each configured monday stream. It hands work to _paged_root for simple page-number streams, to _items for board items, to _activity_logs for board logs, and to _graphql for one-shot streams like teams and tags.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _graphql, _items, _paged_root).


##### `MondayConnector.flatten`  (lines 325–366)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes raw monday.com records into a more consistent form for the rest of the system. It keeps the original fields but adds or standardizes useful fields such as name, body, author, status, parent id, and created time.

**Data flow**: It receives one raw record and the stream it came from. Depending on the stream, it copies the record and adds normalized fields: users get name and email, boards and workspaces get an API URL, items get status, updates get a plain body and author, and activity logs get subject, body, author, and parent board id. For streams without special rules, it returns the record unchanged.

**Call relations**: After paginate has produced raw pages from monday.com, the broader connector framework can call this to prepare each record for storage or indexing. It uses dict_or_empty when reading an update creator so missing creator data becomes an empty dictionary instead of causing an error.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/wrike.py`

`io_transport` · `source sync`

Wrike’s API sends results in pages, like a long report split across many sheets. This connector knows how to keep asking for the next sheet until there are no more pages left. It also knows which Wrike collections are supported and how to name their important fields in a way the rest of the system understands.

The file is read-only. It does not create or update anything in Wrike. It relies on the wider runner to provide authentication, so it never stores a Wrike access token itself.

A key detail is incremental syncing. Wrike does not offer a dependable “only give me records changed since this time” option. Instead, this connector reads pages normally, then locally filters out records whose `updatedDate` is not newer than the saved cursor. In plain terms: it still opens the filing cabinet drawer, but it only keeps papers newer than the bookmark.

If Wrike refuses a request with an authorization error, the connector marks that stream as skipped instead of crashing the whole sync. That usually means the credential is missing permission for that type of data, or the key is invalid.

Finally, the connector reshapes some records. For example, it builds a contact name from first and last name, extracts an email from contact profiles, maps task due dates, and links comments back to their parent task.

#### Function details

##### `_profile_email`  (lines 54–64)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper looks inside a Wrike contact record and finds the first usable email address in its list of profiles. It exists because Wrike stores email addresses one level down, not as a simple top-level contact field.

**Data flow**: It receives one contact record as a dictionary. It checks whether the record has a `profiles` list, walks through each profile that is itself a dictionary, and returns the first non-empty string found under `email`. If there is no valid profile email, it returns nothing.

**Call relations**: When contact records are being reshaped for the rest of the system, `WrikeConnector.flatten` asks this helper to pull out a clean email value. The helper does only that small extraction job and hands the result back to be included in the flattened contact record.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 72–96)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Wrike stream page by page and yields batches of records for syncing. It also skips streams that are not implemented or cannot be accessed with the current permission.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor value. It asks Wrike for pages under the stream’s API path, following Wrike’s `nextPageToken` until the collection is exhausted. If a cursor is present and the stream has an update-time field, it keeps only records newer than that cursor. It yields each non-empty filtered batch. If Wrike replies with a permission refusal, it turns that into a stream-skip signal rather than returning records.

**Call relations**: During a sync, the shared source runner calls this method when it needs records from a Wrike collection. The method delegates the page-following work to the base REST connector’s cursor-page reader. If a stream is unsupported or Wrike refuses access, it raises `StreamSkipped` so the larger sync can move on cleanly instead of treating that one stream as a fatal failure.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 98–136)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method converts raw Wrike records into friendlier, more consistent records for the rest of the system. It adds common fields such as `name`, `created_at`, `status`, `due_date`, or parent links where Wrike uses different field names.

**Data flow**: It receives a raw Wrike record and the stream it came from. For contacts, it builds a display name and extracts an email. For folders, it maps the title to a name and adds a Wrike API URL. For tasks, it maps title, status, due date, and creation time. For comments, it maps text to body, author ID to author, creation time, and the related task ID. For streams without special treatment, it returns the record unchanged.

**Call relations**: After records have been fetched by the connector, the sync flow calls this method to prepare each item for storage or indexing. For contact email extraction it calls `_profile_email`; for task date details it uses `dict_or_empty` so missing or malformed date data does not break the conversion.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).
