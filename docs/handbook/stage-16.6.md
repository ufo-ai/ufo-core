# Work Tracking and Engineering Providers  `stage-16.6`

This stage is shared behind-the-scenes support for syncing work and engineering tools. It gives the system “readers” for outside services, so project work, code activity, incidents, and errors can be copied into the system as searchable records or readable memory pages. Each reader talks to a service’s API, meaning its official doorway for software, and breaks large results into batches the main sync system can store.

The project-management readers cover Asana, ClickUp, Jira, Linear, monday.com, and Wrike. They collect things like projects, tasks, issues, comments, users, teams, boards, lists, goals, custom fields, and workspace details. Together they let the system understand planned work across many common tools.

The engineering readers add operational context. GitHub discovers organizations and repositories, then reads issues, pull requests, commits, and users. PagerDuty reads incidents, services, schedules, and on-call assignments. Sentry reads organizations, projects, issues, events, members, and releases, without writing anything back. Together, these providers act like adapters for different plug shapes, making many services fit one sync pipeline.

## Files in this stage

### Project Management Readers
Readers for general work-management platforms that expose projects, tasks, boards, comments, users, and related workspace metadata.

### `extensions/sources/ufo_ext_sources/providers/asana.py`

`io_transport` · `source sync`

Asana is a work-tracking tool, and its API gives data back in pages rather than all at once. This file is the read-only connector for that API. Its job is to say which Asana collections can be synced, which field identifies each record, and how to keep asking Asana for the next page until there is nothing left.

The file first defines a small helper, `_stream`, that creates a `StreamSpec`. A stream is one kind of Asana data, like `tasks` or `users`. Each stream says the object name to request from Asana, the primary key field (`gid`), and sometimes the date field used for incremental syncing. Incremental syncing means “only ask for records changed since the last successful run,” like checking only the mail that arrived after yesterday instead of emptying the whole mailbox again.

`ASANA_STREAMS` lists the supported Asana streams. A few central streams, such as projects, tasks, stories, and users, are marked as canonical, meaning they are treated as especially important first-class records. Tasks and projects can use Asana’s `modified_since` filter; most other streams are refreshed in full.

`AsanaConnector` supplies the base Asana API URL and the paging behavior. It does not store or create credentials itself; authentication is supplied by the surrounding runner. It also has no write path, so it only reads from Asana.

#### Function details

##### `_stream`  (lines 24–38)

```
def _stream(name: str, *, cursor_field: str | None=None, updated_at_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds the standard description for one Asana data stream. It keeps the stream list short and consistent, so each stream does not have to repeat the same setup details by hand.

**Data flow**: It receives a stream name plus optional date fields and a flag saying whether the stream is canonical. It uses those values to create a `StreamSpec`, which is a small description object telling the sync system what endpoint to read, what field uniquely identifies records, and what date field can be used as a cursor. The result is that one stream definition is ready to be added to the connector’s catalog.

**Call relations**: This helper is used while building the `ASANA_STREAMS` list. Each call hands its settings to `StreamSpec.__init__`, which creates the object the rest of the source framework understands.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 74–90)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Asana stream page by page. It hides Asana’s paging details from the rest of the system, so callers can simply receive batches of records until the stream is finished.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It builds the Asana API path from the stream name and starts with a page size limit. If the stream supports incremental syncing and a cursor is present, it adds `modified_since` so Asana returns only newer changes. For each API response, it pulls the `data` list safely, yields that list if it has records, then checks `next_page.offset`. If there is another offset, it asks for the next page; if not, it stops.

**Call relations**: During a sync, the source framework calls this method for a chosen `StreamSpec`. The method relies on the connector’s inherited `_get` method to make the actual HTTP request, then uses `ufo.sdk.sources.list_or_empty` to turn the response’s `data` field into a dependable list before handing each batch back to the caller.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/providers/clickup.py`

`io_transport` · `source sync`

ClickUp does not expose one simple “give me everything” endpoint. Its data is arranged like a set of nested boxes: teams contain spaces, spaces contain folders and lists, and lists contain tasks, comments, and custom fields. This connector opens those boxes in the right order and keeps track of where each item came from.

The file defines the ClickUp streams the system knows about, such as teams, users, spaces, folders, lists, tasks, list comments, custom fields, and goals. A stream is one kind of data the sync system can ask for. The `ClickUpConnector` then provides the steps needed to fetch each stream from ClickUp’s HTTP API using an authenticated client.

For parent objects, it fetches the hierarchy top-down. For leaf objects like tasks and comments, it first discovers all lists and then asks ClickUp for the child records inside each list. When it adds records to the sync, it also stamps them with useful context, such as the parent list, folder, space, or team ID. That context matters because a task by itself is less useful if the system forgets which list it belonged to.

The connector is read-only. It does not create or update anything in ClickUp. It also supports incremental syncing for streams like tasks and comments by comparing ClickUp timestamps with a saved cursor, which means later syncs can skip older records instead of rereading everything.

#### Function details

##### `ClickUpConnector._teams`  (lines 58–60)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the ClickUp teams available to the authenticated account. Teams are the top of ClickUp’s hierarchy, so most other reads start here.

**Data flow**: It receives an HTTP client that is already ready to talk to ClickUp. It asks the `/team` endpoint for data, pulls the list stored under the `teams` field, and returns that list as plain dictionaries.

**Call relations**: This is the first step in the hierarchy. `_spaces` calls it before looking for spaces inside each team, and `paginate` calls it directly when the requested stream is teams or when it needs team data to build users or goals.

*Call graph*: called by 2 (_spaces, paginate); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 62–70)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived spaces inside all available teams. A space is a major grouping area inside a ClickUp team.

**Data flow**: It starts by fetching teams. For each team with a valid ID, it asks ClickUp for that team’s spaces, extracts the `spaces` records, adds the team ID to each space record, and returns one combined list.

**Call relations**: This function sits one level below teams. `_folders`, `_lists`, and `paginate` rely on it when they need to know which spaces exist before moving deeper into ClickUp’s structure.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 72–82)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived folders inside all discovered spaces. Folders are one optional layer that can contain lists.

**Data flow**: It first gets all spaces. For each space with a valid ID, it asks ClickUp for that space’s folders, extracts the `folders` records, adds the space ID to each folder record, and returns the combined result.

**Call relations**: This function follows `_spaces` in the top-down walk. `_lists` uses it to find lists that live inside folders, and `paginate` uses it when the folders stream is requested.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 84–100)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived lists, both lists inside folders and lists directly inside spaces. Lists are important because tasks, comments, and custom fields are fetched from each list.

**Data flow**: It first gets folders and asks ClickUp for the lists inside each valid folder, adding the folder ID to each list. Then it gets spaces and asks for lists that live directly under each valid space, adding the space ID. It returns all discovered lists together.

**Call relations**: This is the bridge between the hierarchy and the leaf data. `_tasks` and `_list_child_stream` call it before reading per-list data, while `paginate` calls it directly when the requested stream is lists.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _tasks, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 102–127)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches tasks from every discovered list, including closed tasks and subtasks. It can skip older tasks when a saved cursor is provided, so repeated syncs do less work.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It discovers all lists, then for each list it requests task pages one page number at a time. It adds the list ID and list name to each task, filters out tasks whose `date_updated` is not newer than the cursor, yields any remaining tasks in batches, and stops paging a list when ClickUp returns no task records.

**Call relations**: This function is called by `paginate` when the tasks stream is requested. It depends on `_lists` to know where to look, and it uses the shared record-extraction and context helpers to shape each batch before handing it back to the sync flow.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 129–148)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches per-list child data for streams that hang directly off a list, currently list comments and list custom fields. It keeps each child record tied to the list it came from.

**Data flow**: It receives an HTTP client, the stream being requested, and an optional cursor. It discovers all lists, chooses the correct ClickUp endpoint for either comments or custom fields, extracts the matching records, adds the list ID and list name, optionally filters by the stream’s cursor field, and yields non-empty batches.

**Call relations**: This helper is used by `paginate` for the `list_comments` and `list_custom_fields` streams. It reuses `_lists` so that it can fan out one stream request into many ClickUp list-level API calls.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 150–204)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main dispatcher for reading a requested ClickUp stream. The sync framework calls this when it wants records, and this method decides which helper should fetch them.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching helper or in-place logic, and yields batches of records. For users, it builds a deduplicated user list from team member data. For goals, it reads goals under each team. If a stream is unknown, it raises a skip signal instead of pretending it can sync it.

**Call relations**: This is the central read path for the connector. It calls `_teams`, `_spaces`, `_folders`, `_lists`, `_tasks`, and `_list_child_stream` depending on what the broader sync engine asks for, then hands batches back to that engine.

*Call graph*: calls 7 internal fn (__init__, _folders, _list_child_stream, _lists, _spaces, _tasks, _teams); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 206–240)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes ClickUp records into a more consistent shape for the rest of the system. It adds common fields like `name`, `created_at`, `author`, or `api_url` where ClickUp’s raw response uses different names or nested structures.

**Data flow**: It receives one raw record and the stream it belongs to. Depending on the stream, it copies the original fields and adds or rewrites a few easy-to-use fields: users get a display name and creation date, spaces/folders/lists get a URL, tasks get status and creation fields, and comments get body, author, creation time, and parent list ID. If no special shaping is needed, it returns the record unchanged.

**Call relations**: This runs after records have been fetched by `paginate` and before they are stored or indexed by the wider source system. For list comments, it uses a helper that safely treats a missing or non-dictionary user field as an empty dictionary.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/jira.py`

`io_transport` · `source sync`

This connector is the bridge between Atlassian Cloud's Jira API and UFO's source syncing pipeline. Jira data is spread across one or more Atlassian sites, and the connector first asks Atlassian which sites the current permission grant can reach. It then reads each supported kind of Jira data from each site.

The file is read-only. It does not create or edit Jira data. Its job is like a librarian visiting every branch of a library, collecting catalog cards, and adding the branch name to each card so it can be traced back later.

Jira sends many results in pages, so this connector repeatedly asks for the next page until Jira says there is no more. For issues, comments, and sprints it also supports incremental syncing: it can ask only for items newer than the last saved timestamp, so later runs do not have to re-read everything.

Some Jira data is nested or stored in Atlassian Document Format, which is a tree-shaped rich-text format. The connector flattens the important issue update time and renders issue descriptions and comment bodies as plain readable text. If Jira says the token is not allowed to see a site or resource, the connector marks that stream as skipped rather than crashing the whole sync.

#### Function details

##### `JiraConnector.paginate`  (lines 73–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right Jira-reading routine for the requested stream, such as projects, issues, comments, or users. It also turns common permission failures into a clean skip so one inaccessible Jira area does not stop the whole run.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor timestamp. It checks the stream name, calls the matching helper, and yields each page of records. If Jira replies with 401 or 403, meaning unauthorized or forbidden, it raises a skip message instead of treating it as an unexpected failure.

**Call relations**: The sync framework calls this when it wants pages for a particular Jira stream. This function then hands the work to _projects, _issues, _comments, _users, _boards, or _sprints, depending on what is being synced.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 106–110)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds the Atlassian Cloud sites that the current OAuth grant can access. This matters because Jira API calls must include the site's cloud id in their path.

**Data flow**: It uses the HTTP client to call Atlassian's accessible-resources endpoint. It reads the JSON response and makes sure the result is a list, returning an empty list if there is nothing usable.

**Call relations**: _projects, _issues, _users, and _boards call this before reading site-specific data. Those helpers use the site id and URL it returns to make the later Jira requests and attach source context to records.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 112–132)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira API endpoints that return results in numbered pages. It hides the repeated 'ask for the next page' work from the stream-specific helpers.

**Data flow**: It receives a Jira API path, optional query parameters, and the JSON key where records live. Starting at offset 0, it requests up to 100 records at a time, extracts the records, yields non-empty pages, and stops when Jira says the last page has been reached or no more records appear.

**Call relations**: _projects, _issues, _comments, _boards, and _sprints use this common paging helper. It relies on records_at to pull the actual list of records out of Jira's response envelope.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 134–141)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira projects from every Atlassian site the token can reach. Projects are the top-level containers that issues belong to.

**Data flow**: It asks _sites for accessible sites, skips any site without a valid cloud id, then calls the Jira project search endpoint for each site through _offset_values. Before yielding each page, it adds context such as cloud_id and site_url to every record.

**Call relations**: paginate calls this when the sync asks for the projects stream. It depends on _sites to know where to look and _offset_values to walk through Jira's paged project results.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 143–155)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira issues, optionally only those updated after a saved cursor. Issues are the main work items in Jira, so this is one of the connector's central streams.

**Data flow**: It builds a Jira Query Language filter, which is Jira's search syntax, ordering issues by update time and adding an 'updated after cursor' condition when a cursor exists. For each accessible site, it requests issue pages with a limited set of useful fields, then adds site context before yielding them.

**Call relations**: paginate calls this for the issues stream. _comments also calls it with no cursor so it can discover issues and then fetch their comments.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 157–177)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads comments attached to Jira issues. It can filter comments to only those newer than a saved update timestamp.

**Data flow**: It first reads all issues through _issues so it knows which issue comment endpoints to visit. For each valid issue, it fetches paged comments, optionally removes comments whose updated time is not newer than the cursor, and adds context such as cloud id, issue id, issue key, and site URL.

**Call relations**: paginate calls this for the issue_comments stream. This function builds on _issues to find comment locations and _offset_values to read each issue's paged comment list.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 179–190)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira users from each accessible site. Users are returned by Jira as a simple list rather than the usual paged response envelope.

**Data flow**: It asks _sites for accessible sites, skips bad site ids, calls the users/search endpoint once per site with a page-size limit, and converts the JSON response into a safe list. If users are found, it adds cloud_id and site_url to each record before yielding the page.

**Call relations**: paginate calls this when syncing the users stream. Unlike most other streams, it uses list_or_empty directly instead of _offset_values because Jira returns users/search as a bare JSON array here.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 192–199)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Jira Agile boards from each accessible site. Boards are needed on their own and also act as the starting points for finding sprints.

**Data flow**: It gets accessible sites, keeps only sites with a valid cloud id, and reads the Agile board endpoint through the shared paging helper. It adds cloud_id and site_url to each board record before yielding it.

**Call relations**: paginate calls this for the boards stream. _sprints also calls it first, because sprint endpoints are nested under a board.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 201–215)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads sprints for each Jira Agile board, optionally only keeping sprints updated after a saved cursor. Sprints are read by walking through boards first.

**Data flow**: It asks _boards for board pages, then visits the sprint endpoint for each board with a valid id and cloud id. It filters by updatedDate when a cursor is present and adds context such as cloud_id and board_id before yielding sprint pages.

**Call relations**: paginate calls this for the sprints stream. It depends on _boards to discover where sprints live and _offset_values to read each board's paged sprint results.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 217–224)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records before the sync system stores cursor information. For issues, it copies the nested update timestamp into a top-level field where the generic sync machinery expects it.

**Data flow**: It receives one record and its stream description. If the stream is issues, it safely reads record.fields.updated and returns a copy of the record with an added top-level updated value; for other streams it returns the record unchanged.

**Call relations**: The source framework uses this after records are fetched and before cursor tracking. It calls _dict_or_empty so malformed or missing fields do not cause an error.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 226–252)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns selected Jira records into human-readable text for search or recall. It gives issues and comments a useful title and body instead of exposing raw nested JSON.

**Data flow**: It receives a Jira record and stream description. For issues, it pulls out summary, status, priority, assignee, reporter, and description text; for comments, it pulls out the author and comment body. It returns a title plus a Markdown-like text block with a heading and body.

**Call relations**: The source framework calls this when it needs a readable page from a record. This function uses _dict_or_empty, _str, _person, _field_line, and _doc_text to safely extract and format nested Jira values; for streams it does not customize, it hands rendering back to the parent connector.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 255–256)

```
def _str(value: Any) -> str
```

**Purpose**: Safely turns a value into a string only when it already is one. It avoids accidentally rendering numbers, objects, or missing values as misleading text.

**Data flow**: It receives any value. If the value is a string, it returns it; otherwise it returns an empty string.

**Call relations**: render uses this when pulling text fields out of Jira data, and _person uses it when choosing a display name or email address.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 259–260)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. This protects the connector from broken, missing, or unexpected nested Jira fields.

**Data flow**: It receives any value. If the value is a dictionary, it returns that dictionary; otherwise it returns an empty dictionary.

**Call relations**: flatten and render use this before reading nested fields. _person also uses it before looking for a person's display name or email address.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 263–265)

```
def _person(value: Any) -> str
```

**Purpose**: Extracts a readable name for a Jira person. It prefers the person's display name and falls back to their email address.

**Data flow**: It receives a value that should represent a person. It safely treats it as a dictionary, reads displayName first, then emailAddress, and returns whichever usable string it finds, or an empty string.

**Call relations**: render calls this when showing an issue assignee, issue reporter, or comment author. It relies on _dict_or_empty and _str to avoid errors when Jira omits or reshapes person data.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 268–269)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: Formats one metadata line, such as 'Status: Done', but only when there is a real value to show. This keeps rendered pages from being cluttered with empty labels.

**Data flow**: It receives a label and a value string. If the value is not empty, it returns 'label: value'; otherwise it returns an empty string.

**Call relations**: render uses this while building the issue metadata block for status, priority, assignee, and reporter.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 272–289)

```
def _doc_text(value: Any) -> str
```

**Purpose**: Extracts readable plain text from Atlassian Document Format, Jira's tree-shaped rich-text format. It is used so descriptions and comments become searchable human text instead of raw structured JSON.

**Data flow**: It receives any value, usually a nested document tree. It walks through dictionaries and lists, collects every string stored under a text key, joins those pieces with newlines, trims extra whitespace, and returns the final plain-text body.

**Call relations**: render calls this for issue descriptions and comment bodies. The real tree-walking work is done by the nested _doc_text.walk helper.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 277–286)

```
def walk(node: Any) -> None
```

**Purpose**: Walks through one node of an Atlassian Document Format tree and collects text leaves. It is the recursive helper that lets _doc_text handle deeply nested rich-text content.

**Data flow**: It receives one node. If the node is a dictionary, it saves its text value when present and then visits each child in its content list; if the node is a list, it visits each item. It changes the surrounding chunks list by adding found text, and returns nothing itself.

**Call relations**: _doc_text creates and calls this helper, starting with the full document value. Each call may call itself again for child nodes until the whole tree has been searched.


### `extensions/sources/ufo_ext_sources/providers/linear.py`

`io_transport` · `during source sync, while fetching Linear streams and converting records into pages`

Linear is a project and issue tracker, and its API is based on GraphQL, a query language where the client asks for exactly the fields it wants. This file is the connector that knows which Linear objects to ask for, how to page through them, and how to make the most important records readable later.

The file first defines the list of Linear streams the system can sync. A stream is one kind of thing, such as issues, projects, comments, or users. For each stream, it stores a GraphQL query and the name of the field in Linear’s response where that stream’s results will appear. Most streams support incremental syncing: the connector asks Linear for records updated since the last saved cursor, using the `updatedAt` timestamp. A few streams do not support that filter, so they are fetched fully each run.

`LinearConnector.paginate` is the main reader. It sends one GraphQL request at a time, yields the records from that page, then follows Linear’s `endCursor` to request the next page until there are no more. If Linear refuses access with a permission or token problem, it marks that stream as skipped instead of pretending the data synced.

`LinearConnector.render` improves the text for issues, projects, comments, and users. Instead of storing a raw data dump, it creates page-like prose with useful headings and labels, so the synced data is easier to recall and search.

#### Function details

##### `_stream`  (lines 35–45)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates the small description object for one Linear stream. It saves repeated setup, so each stream can be declared with just its name and whether it has a cursor for incremental syncing.

**Data flow**: It receives a stream name, an optional cursor field, and a flag saying whether the stream is canonical content. It fills in the common Linear timestamp fields, creates a `StreamSpec` object, and returns that object for the stream list.

**Call relations**: This is used while the module is being loaded to build `LINEAR_STREAMS`. It hands the stream details to `StreamSpec`, which is the shared source framework’s way of describing what can be synced.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 265–308)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the function that actually reads records from Linear. It sends GraphQL requests page by page, supports incremental syncing when Linear allows it, and yields batches of records for the rest of the sync system to process.

**Data flow**: It receives an HTTP client, a stream description, and the last saved cursor if one exists. It looks up the right GraphQL query, adds an `updatedAt` filter when possible, sends the request, checks for access refusal or GraphQL errors, extracts the `nodes` list, and yields those records. It then reads Linear’s page information and repeats with the next cursor until Linear says there are no more pages.

**Call relations**: The connector framework calls this when it wants data for one Linear stream. Inside the flow, it uses the shared POST behavior from the parent connector, turns missing or non-list node data into a safe empty list with `list_or_empty`, and raises `StreamSkipped` when Linear says the token or permissions are not good enough for that stream.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 310–352)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Linear records into readable page text. It gives issues, projects, comments, and users useful titles and bodies instead of leaving them as plain raw API data.

**Data flow**: It receives one Linear record and the stream it came from. Depending on the stream, it pulls out fields such as title, description, state, assignee, project lead, comment body, user name, or email. It builds a title and a Markdown-style body, falls back to the first non-empty body line if there is no title, and returns both the page title and the rendered text.

**Call relations**: The sync framework calls this after records have been fetched and need to become pages. It relies on `_str` to safely read text, `_ref_id` to turn nested Linear references into IDs, and `_labeled` to make short label-and-value sections. For streams it does not customize, it hands rendering back to the parent connector.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 355–356)

```
def _str(value: Any) -> str
```

**Purpose**: This tiny helper safely turns a value into text only when it is already a string. It prevents accidental output like `None` or a dictionary showing up in page text.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string.

**Call relations**: `LinearConnector.render` uses this whenever it pulls optional text from a Linear record. `_ref_id` also uses it after finding an `id` field, so nested references are cleaned in the same safe way.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 359–360)

```
def _ref_id(value: Any) -> str
```

**Purpose**: This helper extracts an ID from a nested Linear reference, such as an assignee or project lead. It is used when the rendered page should mention the linked object without expanding the whole nested object.

**Data flow**: It receives any value. If the value is a dictionary-like record, it reads its `id` field and passes that through `_str`; otherwise it returns an empty string.

**Call relations**: `LinearConnector.render` calls this while building readable metadata for issues and projects. `_ref_id` delegates the final text safety check to `_str`, keeping reference handling consistent with the rest of the renderer.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 363–364)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This helper formats small pieces of metadata as simple `label: value` lines. It keeps rendered Linear pages clean by skipping labels whose values are empty.

**Data flow**: It receives a list of label-and-value pairs. It keeps only the pairs with a non-empty value, joins them into separate lines, and returns the resulting text block.

**Call relations**: `LinearConnector.render` calls this when it needs compact metadata sections for issues, projects, and users. It acts like a small formatting tool that turns extracted fields into human-readable page text.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/monday.py`

`io_transport` · `source sync runtime`

monday.com exposes its data through GraphQL, which means the connector sends a query describing exactly what it wants and receives matching data back. This file is the read-only bridge between monday.com and the project’s source-sync framework. Without it, monday data could not be imported into the system as recallable pages.

The file first defines the available streams, such as users, boards, and items. A stream is simply one category of data that can be fetched and saved independently. Some streams have a cursor field, such as updated_at or created_at. A cursor is a saved “last seen” value, like a bookmark, used to avoid re-reading older records.

The MondayConnector class does most of the work. It sends GraphQL requests, turns monday’s responses into plain lists of dictionaries, and skips streams cleanly when monday refuses a query or reports a GraphQL error. Top-level monday collections use page numbers. Board items are different: monday returns a special next-page cursor, so the connector follows that cursor until no more items remain.

A few streams need extra steps. Items are fetched board by board, and the connector extracts assignee user IDs from monday’s people columns. Activity logs are also fetched per board and tagged with the board ID. Finally, flatten reshapes records into common fields like name, body, author, and created_at so downstream code can treat different monday objects more consistently.

#### Function details

##### `_extract_person_ids`  (lines 70–98)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: This helper pulls monday user IDs out of item column data for people-assignment columns. It exists because monday stores assignees inside board-specific column values, not in one simple assignee field.

**Data flow**: It receives the raw column_values value from a monday item. It checks each column, keeps only columns marked as type people, parses the stored JSON value when needed, and collects entries whose kind is person. It returns a simple list of user IDs as strings, and ignores malformed or missing data instead of failing the whole sync.

**Call relations**: When MondayConnector._items fetches board items, it calls this helper for each item. The returned IDs are added onto the item record as assignee_ids so later parts of the system can see who is assigned without understanding monday’s nested column format.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 106–118)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This method sends one GraphQL request to monday.com and returns the data section of the response. It also turns monday GraphQL errors into a controlled stream skip, so the sync does not save a half-broken page.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional query variables. It posts them to monday’s API endpoint, looks for an errors field in the response, and raises StreamSkipped if monday reports a problem. If the response contains a data object, it returns that object; otherwise it returns an empty dictionary.

**Call relations**: This is the common doorway used by the connector’s fetch methods. MondayConnector._paged_root, MondayConnector._items, MondayConnector._activity_logs, and MondayConnector.paginate all rely on it whenever they need to ask monday for data.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, paginate).


##### `MondayConnector._paged_root`  (lines 120–143)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method fetches monday collections that use simple numbered pages, such as users, boards, workspaces, or updates. It hides the repeated “ask for page 1, then page 2, then page 3” loop from the rest of the connector.

**Data flow**: It receives the monday field to query, the fields to request for each record, and optionally a saved cursor value plus the record field to compare against it. For each page, it asks monday for up to 100 records, safely treats missing or non-list results as an empty list, filters out old records when a cursor is supplied, and yields each non-empty page. When a page has no usable records, it stops.

**Call relations**: MondayConnector._boards uses this to collect all boards for other board-based work. MondayConnector.paginate also uses it directly for streams that fit monday’s numbered-page style.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, paginate); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 145–156)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This method fetches all boards from monday.com with the board details needed by item and activity-log syncing. It is a shared helper because several monday data types are organized underneath boards.

**Data flow**: It starts with an empty list, reads every page of boards through MondayConnector._paged_root, and appends each page of board records to the list. It returns one combined list of board dictionaries.

**Call relations**: MondayConnector._items calls this before fetching items, because items must be requested board by board. MondayConnector._activity_logs calls it for the same reason, since activity logs are also tied to boards.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 158–217)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method fetches monday items from every board. It also enriches each item with assignee_ids, which are extracted from monday’s people columns.

**Data flow**: It receives an HTTP client and an optional cursor showing the newest item previously synced. It first gets all boards, then for each board asks monday for the first page of items. If monday returns an item-page cursor, it follows that cursor to fetch later pages. For every item, it adds assignee_ids from the column_values data, filters out older items when a cursor is present, and yields pages of remaining item records.

**Call relations**: MondayConnector.paginate uses this when the requested stream is items. Inside the process, this method depends on MondayConnector._boards to find boards, MondayConnector._graphql to talk to monday, and _extract_person_ids to make assignment data easier for downstream code to use.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (paginate); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 219–247)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method fetches recent activity-log entries for each monday board. It adds the board ID to every log entry so the log can later be connected back to the board it came from.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all boards, asks monday for up to 100 activity logs for each board, copies each log into a shared list while adding board_id, filters out logs older than the cursor when one is provided, and yields any non-empty batch.

**Call relations**: MondayConnector.paginate calls this for the activity_logs stream. The method first relies on MondayConnector._boards to know which boards exist, then uses MondayConnector._graphql to fetch each board’s activity-log data.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (paginate); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 249–323)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher that decides how to fetch each monday stream. The sync framework calls it to receive pages of records for users, boards, items, updates, and the other supported monday data types.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. Based on the stream name, it chooses the right query or helper method, then yields pages of records back to the caller. If the stream is unknown, or if monday refuses access with an authorization-style HTTP status, it raises StreamSkipped so the run records a skip instead of crashing or saving partial data.

**Call relations**: This method is the connector’s public paging path used by the source framework. For simple streams it calls MondayConnector._paged_root or MondayConnector._graphql directly; for board-shaped streams it hands off to MondayConnector._items or MondayConnector._activity_logs.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _graphql, _items, _paged_root).


##### `MondayConnector.flatten`  (lines 325–366)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method reshapes monday records into a more consistent form for the rest of the system. It keeps the original data but adds or normalizes common fields such as name, body, author, status, created_at, and parent_external_id.

**Data flow**: It receives one monday record and the stream it came from. Depending on the stream, it copies the record and fills standard fields from monday-specific fields; for example, an update’s text becomes body, its creator becomes author, and its item_id becomes parent_external_id. If the stream does not need special treatment, it returns the record unchanged.

**Call relations**: After MondayConnector.paginate and its helper methods have fetched raw records, the wider connector framework can use this method to produce records in a shape that downstream indexing or storage code understands. It uses a safe dictionary helper when reading nested creator data so missing creator information does not break flattening.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/wrike.py`

`io_transport` · `source sync run`

Wrike’s API sends data back in pages, like a long list split across several envelopes. This connector knows which Wrike lists are available, where to ask for them, and how to keep following Wrike’s “next page” token until there is no more data. It is read-only: it never creates or edits anything in Wrike.

The file defines the streams the system can sync, such as tasks and folders. Some streams include an update time field, so the connector can do a practical form of incremental syncing. Wrike does not provide a dependable “only send me records changed since this time” filter, so the connector fetches pages and then drops records whose update time is not newer than the saved cursor. This is less elegant than server-side filtering, but it keeps repeat syncs from re-processing old records when possible.

It also normalizes Wrike’s raw records into fields the rest of the system can understand. For example, contacts get a readable name and email, tasks get a name, status, and due date, and comments get a body and parent task id. If Wrike refuses access because the token is invalid or lacks permission, the connector skips that stream with a clear reason instead of crashing the whole source run.

#### Function details

##### `_profile_email`  (lines 54–64)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper looks inside a Wrike contact record and finds the first usable email address. Wrike stores emails inside a list of profile objects, so this keeps that lookup in one small, safe place.

**Data flow**: It receives one Wrike record as a dictionary. It checks whether the record has a profiles list, walks through that list, ignores anything that is not shaped like a profile, and returns the first non-empty email string it finds. If there is no valid email, it returns nothing.

**Call relations**: WrikeConnector.flatten calls this when it is preparing contact records. The helper supplies a clean email value so the flattened contact can expose an easy-to-use email field instead of forcing later code to understand Wrike’s nested profile layout.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 72–96)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Wrike stream page by page. It follows Wrike’s next-page token, optionally filters out records that are not newer than the saved cursor, and yields batches of records for the sync system to process.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. It first checks whether this stream is actually implemented. Then it asks Wrike for pages under the stream’s API path, reads records from Wrike’s data wrapper, follows the nextPageToken when present, and removes old records if the stream has an update field and a cursor was supplied. It outputs only non-empty batches. If Wrike answers with a permission or authentication refusal, it turns that into a stream-skipped result with a human-readable explanation.

**Call relations**: During a sync, the shared source framework calls this method to collect raw Wrike records for a particular stream. Internally it relies on the base REST connector’s cursor-page reader to do the repeated HTTP paging. When a stream is unsupported or Wrike refuses access, it raises StreamSkipped so the wider run can continue with other streams instead of failing completely.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 98–136)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method turns Wrike’s raw API records into records with common, predictable fields. It gives the rest of the system names like name, email, status, due_date, body, and created_at without making every downstream component learn Wrike’s exact field names.

**Data flow**: It receives one raw Wrike record and the stream it came from. For contacts, it builds a display name from first and last name, adds an email found by _profile_email, and maps the creation date. For folders, it copies the title into name and adds a Wrike API URL. For tasks, it reads nested date information safely with dict_or_empty, then fills name, status, due date, and creation time. For comments, it maps text to body, author id to author, and task id to parent_external_id. For streams without special rules, it returns the record unchanged.

**Call relations**: After paginate has supplied raw Wrike records, the source framework uses flatten to prepare each record for indexing or storage. This method calls _profile_email for contact email extraction and dict_or_empty when reading task dates, so missing or oddly shaped nested data does not break the normalization step.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).


### Source Control Reader
The GitHub reader discovers organizations and repositories, then syncs issues, pull requests, commits, users, and related repository records.

### `extensions/sources/ufo_ext_sources/providers/github.py`

`io_transport` · `during GitHub source sync`

This connector is the bridge between GitHub and the rest of the source-sync system. Without it, the project would not know which GitHub API addresses to call, how to page through long result lists, or how to keep records from different repositories from overwriting each other.

The file starts by defining the GitHub streams the system understands, such as repositories, issues, commits, releases, users, and teams. A stream is a named kind of data to read. Some streams can be resumed from a time cursor, meaning the next sync can start near where the last one stopped instead of rereading everything.

The main class, `GitHubConnector`, builds an authenticated GitHub client with the required GitHub headers. It first discovers the organizations the credential can see, then lists repositories inside those organizations. Most repository-level streams are then read once per repository. Each record is stamped with the repository or organization it came from. This matters because many GitHub IDs, such as branch names like `main`, are only unique inside one repository. The stamp acts like writing the building name on every apartment number.

The connector also knows GitHub’s pagination style, where the next page is advertised in a `Link` header. It treats missing or inaccessible organizations and repositories carefully: some are skipped, while a credential that cannot list organizations at all causes the stream to be marked skipped rather than failed.

#### Function details

##### `_stream`  (lines 73–93)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', ordering: Ordering=Ordering.none, canonical:
```

**Purpose**: This helper creates a stream description for one kind of GitHub data, such as issues or commits. It keeps the large stream list readable by filling in common defaults and only requiring the differences to be named.

**Data flow**: It receives a stream name plus optional details like the GitHub object name, primary key, cursor field, and ordering style. It packages those choices into a `StreamSpec`, which is the sync system’s recipe for reading that stream.

**Call relations**: The file uses this helper while building `ALL_STREAMS`. It hands the finished stream descriptions to the connector, which later uses them to decide what API path to call, how to resume, and how to label records.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 197–200)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This returns only the GitHub streams that are actually runnable today. Some streams are listed for future compatibility, but this method filters out any stream that does not yet have an API path wired up.

**Data flow**: It reads the connector’s full stream list and the path table in this file. It returns a shorter list containing only stream names found in the path table.

**Call relations**: The source-sync framework asks the connector which streams it can read. This method is the gatekeeper that prevents unfinished catalog entries from being attempted during a sync.


##### `GitHubConnector._make_client`  (lines 202–206)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to GitHub and adds the GitHub-specific headers GitHub expects. The headers request the correct API format and version.

**Data flow**: It receives a base URL and a credential. It asks the parent connector to build the authenticated client, then adds GitHub’s `Accept` and API-version headers before returning the client.

**Call relations**: The wider connector framework calls this during setup. After this, every API request made by the connector carries the authentication and GitHub version information needed for consistent responses.


##### `GitHubConnector.flatten`  (lines 208–248)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes a raw GitHub record before it becomes a page and makes its main identifier safe across repositories or organizations. It prevents records with local-only keys, like branch name `main`, from colliding between repositories.

**Data flow**: It receives one GitHub record and its stream description. It may simplify special records, such as stargazers or pull requests, then looks up whether the stream was read per repository or per organization. If so, it prefixes the record’s primary key with that repository or organization stamp and returns the adjusted record.

**Call relations**: The page-building adapter uses this before reading the stream’s primary key. It relies on `_partition_field` to decide whether a repository or organization stamp is required, then hands back a record whose page reference can be safely unique.

*Call graph*: calls 1 internal fn (_partition_field).


##### `GitHubConnector.render`  (lines 250–255)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This prepares a GitHub record for display as page content. It has one special rule: for pull requests, the update timestamp is kept as metadata rather than repeated inside the visible page body.

**Data flow**: It receives a record and stream description. For most streams it passes the record through the normal renderer. For pull requests it removes the update field from the content copy, then sends that cleaned copy to the normal renderer and returns the rendered title and body.

**Call relations**: The source framework uses this after records have been fetched and flattened. It fits into the final step where raw API data becomes readable stored content.


##### `GitHubConnector.paginate_source`  (lines 257–268)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: This is the connector’s entry point for producing pages of raw GitHub records for a stream. It widens the usual pagination call so repository backfills can receive a pinned lower time limit.

**Data flow**: It receives the HTTP client, stream, current cursor, optional user information, and optional backfill floor. It forwards the useful pieces to `GitHubConnector.paginate` and yields whatever pages that method produces.

**Call relations**: The sync framework calls this when it wants records for a stream. This method immediately hands the real work to `GitHubConnector.paginate`, adding support for the backfill time used by newest-first repository streams.

*Call graph*: calls 1 internal fn (paginate).


##### `GitHubConnector.paginate`  (lines 270–341)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main traffic director for reading a GitHub stream. It decides whether the stream should be read directly, once per organization, or once per repository, and then yields pages of records.

**Data flow**: It receives a client, stream, cursor, and optional backfill floor. It finds the stream’s API path, builds GitHub query parameters, discovers organizations or repositories when needed, calls the right page reader, stamps records with their organization or repository context, and yields lists of records or structured stream pages.

**Call relations**: It is called by `GitHubConnector.paginate_source`. For repository streams it creates small helper functions, then gives them to `PartitionWalk`, which controls resume behavior across repositories. For organization streams it calls `_iter_user_orgs`, `_paginate_link_header`, and sometimes `_enrich_users`; for repository catalog pages it calls `_iter_granted_org_repo_pages`.

*Call graph*: calls 4 internal fn (_enrich_users, _iter_granted_org_repo_pages, _iter_user_orgs, _paginate_link_header); called by 1 (paginate_source); 4 external calls (__init__, Semaphore, astimezone, with_context).


##### `GitHubConnector.paginate.repos`  (lines 293–295)

```
async def repos() -> AsyncIterator[str]
```

**Purpose**: This small inner helper supplies repository names to the partition walker. Each repository becomes one partition, meaning one separate slice of the sync.

**Data flow**: It reads repositories from `_iter_user_repos`. For each `(owner, repo)` pair it turns the pair into a single `owner/repo` string and yields that string onward.

**Call relations**: It exists only inside `GitHubConnector.paginate`. `PartitionWalk` calls on it when it needs to know which repositories to walk, and it delegates repository discovery to `_iter_user_repos`.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector.paginate.repo_pages`  (lines 297–298)

```
def repo_pages(repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: This small inner helper tells the partition walker how to fetch one repository’s pages for the chosen stream. It connects the generic walking logic to GitHub’s actual repository API calls.

**Data flow**: It receives a repository key like `owner/repo` and a time boundary from the partition walker. It calls `_repo_pages` with the client, stream, API path, repository key, and boundary, then returns that async page stream.

**Call relations**: It exists only inside `GitHubConnector.paginate`. `PartitionWalk` uses it after choosing a repository and deciding what time range or resume point should apply.

*Call graph*: calls 1 internal fn (_repo_pages).


##### `GitHubConnector._repo_pages`  (lines 343–414)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: This reads one stream for one repository, applying the correct GitHub filters and resume boundaries. It also stamps each returned record with the repository it came from.

**Data flow**: It receives a client, stream, API path, repository key, and partition boundary. It formats the GitHub path, adds parameters such as `since`, `until`, sorting, or state, reads pages through `_paginate_link_header`, filters special cases like pull requests inside the issues endpoint, computes high and low cursor values, and yields `WalkPage` objects containing stamped records. If a repository is gone or inaccessible, it raises a partition skip instead of failing the whole sync.

**Call relations**: It is called through `GitHubConnector.paginate.repo_pages` as part of the `PartitionWalk` flow. It hands cursor bounds back to the walker so the walker can resume later without losing newly inserted GitHub records.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); called by 1 (repo_pages); 3 external calls (__init__, __init__, with_context).


##### `GitHubConnector._iter_user_repos`  (lines 416–424)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: This discovers repositories that belong to organizations the credential can access. It avoids using GitHub’s broader personal repository listing because this connector treats granted organizations as the intended scope.

**Data flow**: It reads organization repository pages from `_iter_granted_org_repo_pages`. For each repository record, it extracts an `(owner, repo)` identity with `_repo_identity` and yields only records where that identity can be found.

**Call relations**: It is called by the inner `GitHubConnector.paginate.repos` helper. Its output becomes the repository partition list used by `PartitionWalk` for repository-scoped streams.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (repos).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 426–445)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: This lists repository pages for every accessible organization, skipping archived repositories and forks. It gives the sync a clean catalog of active organization-owned repositories.

**Data flow**: It gets organization logins from `_iter_user_orgs`, calls GitHub’s organization repositories endpoint for each one, filters each page to remove archived and forked repositories, and yields the organization login together with the filtered repository page. If one organization cannot be read because of expected access or deletion statuses, it skips that organization.

**Call relations**: It is used in two places: `GitHubConnector.paginate` uses it for the repositories stream, and `_iter_user_repos` uses it to build the repository list for all repository-scoped streams.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, paginate).


##### `GitHubConnector._iter_user_orgs`  (lines 447–468)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This lists the GitHub organizations visible to the credential. Since all runnable streams depend on organization discovery, it treats a root organization-listing refusal as a stream skip rather than a crash.

**Data flow**: It calls GitHub’s `/user/orgs` endpoint through `_paginate_link_header`, reads each organization record, and yields valid login names. If GitHub returns a permission denial for this root listing, it raises `StreamSkipped` to say the grant lacks the needed organization scope.

**Call relations**: Both `GitHubConnector.paginate` and `_iter_granted_org_repo_pages` call this before they can fan out over organizations. It is the first discovery step for nearly every real GitHub read in this connector.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, paginate).


##### `GitHubConnector._enrich_users`  (lines 470–490)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: This replaces simple organization member records with fuller public GitHub user profiles when possible. It is used so the users stream can include public details such as name or email when GitHub exposes them.

**Data flow**: It receives a page of member records plus a semaphore, which is a small traffic light that limits how many user lookups run at once. It launches one lookup task per member, waits for all of them with `asyncio.gather`, and returns a list where each member is either enriched or left unchanged.

**Call relations**: It is called by `GitHubConnector.paginate` only for the `users` stream. It coordinates many calls to the nested `GitHubConnector._enrich_users.one` helper while respecting the concurrency limit created in `paginate`.

*Call graph*: called by 1 (paginate); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 476–488)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This enriches a single organization member by asking GitHub for that user’s public profile. If the profile cannot be found, it keeps the original member record.

**Data flow**: It receives one member record. It reads the member’s login, waits for permission from the semaphore, fetches `/users/{login}`, and returns the JSON profile if it is a dictionary. If the login is missing, the profile is missing, or GitHub returns 404, it returns the original member.

**Call relations**: It is the per-record worker inside `GitHubConnector._enrich_users`. The outer function runs many of these workers together, so a whole page of users can be enriched without doing the requests strictly one after another.


##### `GitHubConnector._paginate_link_header`  (lines 492–500)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This wraps the common GitHub pagination pattern. GitHub sends long lists one page at a time and points to the next page using a `Link` header; this helper follows those links.

**Data flow**: It receives a client, API path, and optional query parameters. It asks the parent REST connector to fetch link-header pages, using this file’s parser to turn each response into a list of records, and yields each list. Empty responses produce no records.

**Call relations**: This is the low-level page reader used by `paginate`, `_repo_pages`, `_iter_user_orgs`, and `_iter_granted_org_repo_pages`. It centralizes GitHub’s page size and response parsing so the higher-level methods can focus on discovery and filtering.

*Call graph*: called by 4 (_iter_granted_org_repo_pages, _iter_user_orgs, _repo_pages, paginate).


##### `_partition_field`  (lines 503–511)

```
def _partition_field(path: str) -> str | None
```

**Purpose**: This decides which context stamp a stream should have: repository, organization, or none. It is a small safety helper for avoiding cross-repository or cross-organization key collisions.

**Data flow**: It receives an API path template. If the path contains a repository placeholder, it returns the repository stamp field name; if it contains an organization placeholder, it returns the organization stamp field name; otherwise it returns nothing.

**Call relations**: `GitHubConnector.flatten` calls this before changing a record’s primary key. The returned field name tells `flatten` where to find the partition label that should be prefixed onto the key.

*Call graph*: called by 1 (flatten).


##### `_parse_records`  (lines 514–518)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: This turns a GitHub HTTP response into the list of records the connector expects. GitHub list endpoints normally return a plain JSON array, and this helper accepts only that shape.

**Data flow**: It receives an HTTP response. If the response has no body, it returns an empty list. Otherwise it parses the JSON and returns it only if it is a list; any non-list body is treated as no records.

**Call relations**: `GitHubConnector._paginate_link_header` passes this parser into the parent REST pagination helper. That keeps response-shape checking in one place for all GitHub list reads.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 521–536)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: This extracts an `(owner, repository)` pair from a GitHub repository record. It supports several possible shapes because GitHub records may carry the owner in `full_name`, in an `owner` object, or implicitly from the organization being listed.

**Data flow**: It receives a repository record and an optional fallback owner. It first tries `full_name` such as `octo-org/example`, then tries the nested owner login plus repository name, then tries the fallback owner plus repository name. If none of those are valid, it returns nothing.

**Call relations**: `GitHubConnector._iter_user_repos` calls this while turning organization repository pages into repository partitions. Only records with a usable identity are yielded for later syncing.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 539–549)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: This finds the newest and oldest cursor values on one page of records. Those bounds help the partition walker know what time range has just been seen.

**Data flow**: It receives a page of records and the name of a cursor field, which may point inside nested data such as `commit.committer.date`. It reads all string cursor values, then returns the maximum and minimum value. If there is no cursor field or no valid values, it returns two empty results.

**Call relations**: `GitHubConnector._repo_pages` calls this for both the records that landed and the full provider page. The resulting high and low values are placed into `WalkPage` so `PartitionWalk` can update watermarks and resume windows.

*Call graph*: called by 1 (_repo_pages); 1 external calls (get_path).


### Incident and Observability Readers
Readers for operational engineering systems that capture incidents, on-call context, errors, events, releases, and service health data.

### `extensions/sources/ufo_ext_sources/providers/pagerduty.py`

`io_transport` · `source sync`

PagerDuty is an incident management service, and its API returns information in chunks rather than all at once. This file defines a PagerDuty connector: a read-only bridge between PagerDuty and the system’s source-sync framework. Without it, the system would not know which PagerDuty objects are available, how to ask for them, how to move through API pages, or how to continue from the last synced update instead of rereading everything.

The file starts by declaring the available streams, such as users, services, incidents, and incident notes. A stream is one kind of thing to fetch, like a labeled drawer in a filing cabinet. Some streams have a cursor field, which is a timestamp used as a bookmark so future syncs can ask only for newer records.

The `PagerDutyConnector` builds an HTTP client with PagerDuty’s required API version header. It then provides different fetching paths for different stream types. Most streams use PagerDuty’s normal offset-and-limit pagination. Incidents get special treatment because they can be sorted and filtered by update time. Incident notes are even more special: PagerDuty exposes them under each individual incident, so the connector first reads incidents, then asks for notes for each one.

If PagerDuty rejects access with HTTP 401 or 403, the connector reports the stream as skipped rather than crashing the whole sync. That usually means the token is missing permission for that part of PagerDuty.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 74–77)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to PagerDuty and adds PagerDuty’s required versioned `Accept` header. That header tells PagerDuty which version of its API response format the connector expects.

**Data flow**: It receives a base URL and a credential reference. It asks the parent `RestConnector` to build the normal authenticated HTTP client, then adds the PagerDuty-specific `Accept` header to that client. It returns the prepared client, ready to make PagerDuty API calls.

**Call relations**: This is part of the connector setup inherited from the shared REST source framework. Before any stream is paged through, the framework needs a client; this method customizes that client just enough for PagerDuty’s API rules.


##### `PagerDutyConnector._offset_pages`  (lines 79–99)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads ordinary PagerDuty streams that use offset-based pagination, meaning the API returns one batch at a time and says whether there is more to fetch. It can also filter out records that are not newer than the saved cursor bookmark.

**Data flow**: It receives an HTTP client, a stream description, optional request parameters, and an optional cursor timestamp. It asks the shared REST helper to fetch pages from the endpoint for that stream, using PagerDuty’s `more` and `limit` fields to keep moving forward. For streams with a cursor field, it removes records whose cursor value is not newer than the saved cursor, then yields only non-empty batches of records.

**Call relations**: This is the common paging worker. `PagerDutyConnector._incidents` uses it with incident-specific sorting and filtering, while `PagerDutyConnector.paginate` uses it directly for regular streams such as users, teams, services, schedules, escalation policies, and on-calls.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 101–113)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads PagerDuty incidents in update-time order so the sync can resume cleanly from a previous bookmark. It is the special incident-specific path rather than treating incidents like a completely generic stream.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds API parameters that sort incidents by `updated_at` from oldest to newest, and if a cursor exists, it adds PagerDuty’s `since` parameter to ask for incidents updated after that time. It then yields each page produced by `_offset_pages`.

**Call relations**: When the main `paginate` method is asked for the incidents stream, it delegates here. `PagerDutyConnector._incident_notes` also calls this function, because notes must be discovered by first walking through incidents and then fetching each incident’s notes.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 115–128)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads notes attached to PagerDuty incidents. PagerDuty does not provide these notes as one simple top-level list, so the connector must first find incidents and then fetch notes for each incident.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It reads incidents without using the notes cursor, takes each valid incident ID, and requests that incident’s notes endpoint. It extracts the `notes` list from the response, optionally keeps only notes created after the cursor, adds the incident ID as context to each note, and yields non-empty batches.

**Call relations**: The main `paginate` method calls this when the requested stream is `incident_notes`. Inside, it depends on `_incidents` to discover which incidents to inspect, uses `records_at` to pull the notes list out of the API response, and uses `with_context` to attach the parent incident ID so each note can be traced back to its incident.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 130–164)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading any PagerDuty stream. Given a stream name, it chooses the correct fetching method and turns PagerDuty’s API responses into batches of records for the sync framework.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. If the stream is incidents, it yields pages from `_incidents`; if it is incident notes, it yields pages from `_incident_notes`; if it is one of the regular top-level streams, it yields pages from `_offset_pages`. If the stream is unknown, it raises `StreamSkipped`. If PagerDuty returns HTTP 401 or 403, it turns that refusal into `StreamSkipped` with a clear permission-related message; other HTTP errors are allowed to keep failing normally.

**Call relations**: The source-sync framework calls this method when it is time to fetch a particular PagerDuty stream. This method then hands the work to the right helper: `_incidents` for incident syncing, `_incident_notes` for note fan-out, or `_offset_pages` for standard PagerDuty lists. It also protects the wider run from permission problems by marking refused streams as skipped instead of treating them as full connector failures.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/providers/sentry.py`

`io_transport` · `source sync`

Sentry stores useful project history across several related areas: organizations contain projects, projects contain issues and events, and organizations also have members and releases. This file is the map and courier for that structure. Without it, the larger system would not know which Sentry API addresses to call, how to follow Sentry’s page-by-page results, or how to label records with the organization or project they came from.

The file starts by defining the Sentry streams, which are the kinds of records the system can sync. Each stream says what field identifies a record and, when possible, what date field can be used as a cursor. A cursor is a saved “last seen point” so later syncs can ask only for newer data.

The SentryConnector then provides the actual reading behavior. Its main entry point, paginate, chooses the right helper for the requested stream. The helpers first fetch broad lists, such as organizations or projects, then use those lists to visit narrower endpoints, such as a project’s issues. When Sentry returns many results, _paged_list follows the “next page” cursor hidden in Sentry’s Link response header, like following a “continued on next page” note in a book.

If Sentry refuses access with an authorization error, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `_sentry_next_cursor`  (lines 76–81)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This small helper reads Sentry’s pagination hint from an HTTP response header. It looks for the cursor that means “there is another page of results, and this is how to ask for it.”

**Data flow**: It receives the response headers from Sentry. It looks for a Link header, searches that text for a next-page cursor marked as having more results, and returns the cursor text if it finds one. If there is no usable next-page hint, it returns nothing.

**Call relations**: SentryConnector._paged_list calls this after each API response. The answer decides whether _paged_list asks Sentry for another page or stops reading that endpoint.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.paginate`  (lines 89–128)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s dispatcher for a requested Sentry stream. Given a stream name, it chooses the right reading path and yields pages of records back to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching helper, optionally filters records newer than the cursor, and yields batches of Sentry records. If the stream is unknown or Sentry denies access with a 401 or 403 status, it turns that into a StreamSkipped result so the wider sync can move on safely.

**Call relations**: The source framework calls paginate when it wants records for one Sentry stream. paginate then hands work to _organizations, _projects, _members, _issues, _events, or _releases depending on what was requested, and reports skipped streams through StreamSkipped.

*Call graph*: calls 7 internal fn (__init__, _events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._paged_list`  (lines 130–147)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads every page from one Sentry list endpoint. It hides the repetitive work of sending the request, parsing JSON, and following Sentry’s next-page cursor.

**Data flow**: It receives an HTTP client, an API path such as an organizations or issues path, and optional query parameters. It sends a request, keeps only JSON list items that are objects, yields those records as a page, then checks the response headers for the next cursor. It repeats until Sentry no longer says there is another page.

**Call relations**: All the specific stream helpers rely on _paged_list whenever they need to read a Sentry endpoint. After every response, _paged_list calls _sentry_next_cursor to know whether to continue.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 149–153)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This reads all organizations available to the current Sentry credential. Organizations are the top-level containers needed before fetching organization-specific data like members and releases.

**Data flow**: It receives an HTTP client. It asks _paged_list for every page from Sentry’s organizations endpoint, gathers all organization records into one list, and returns that list.

**Call relations**: paginate calls this directly for the organizations stream. _members and _releases also call it first because they need each organization slug before they can ask Sentry for members or releases inside that organization.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, paginate).


##### `SentryConnector._projects`  (lines 155–159)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This reads all projects available to the current Sentry credential. Projects are needed both as their own stream and as starting points for project-level data such as issues and events.

**Data flow**: It receives an HTTP client. It asks _paged_list for every page from Sentry’s projects endpoint, gathers all project records into one list, and returns that list.

**Call relations**: paginate calls this directly for the projects stream. _issues and _events call it first because each issue or event request must be made for a specific organization and project.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, paginate).


##### `SentryConnector._members`  (lines 161–167)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the members of every available Sentry organization. It also labels each member record with the organization slug so the record keeps its source context after syncing.

**Data flow**: It receives an HTTP client. It first gets all organizations, skips any organization without a usable slug, then reads that organization’s members page by page. Before yielding each page, it adds organization_slug to every record in that page.

**Call relations**: paginate calls _members when the members stream is requested. _members depends on _organizations to find where to look, uses _paged_list to fetch each organization’s members, and uses with_context to attach the organization label.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 169–183)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads issues for every available Sentry project. If a cursor is provided, it asks Sentry for only issues whose last-seen time is newer than that saved point.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all projects, extracts the organization slug and project slug from each usable project, builds a Sentry query when a cursor exists, and reads issue pages for that project. It yields each page after adding both organization_slug and project_slug to the records.

**Call relations**: paginate calls _issues for the issues stream. _issues first relies on _projects to discover project locations, then uses _paged_list for each project’s issues endpoint, and uses with_context so later parts of the system know which project each issue came from.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 185–199)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads events for every available Sentry project. Events are not marked as a canonical stream here, but they can still be synced as detailed project activity.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all projects, finds each project’s organization slug and project slug, and, when a cursor exists, asks Sentry for events newer than that event timestamp. It reads event pages and adds organization_slug and project_slug before yielding them.

**Call relations**: paginate calls _events when the events stream is requested. _events follows the same project-first pattern as _issues: discover projects with _projects, fetch project-specific pages with _paged_list, then attach context with with_context.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 201–212)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads releases for every available Sentry organization. It can filter locally to return only releases created after the saved cursor.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all organizations, skips any without a valid slug, then reads that organization’s releases page by page. If a cursor is present, it keeps only releases whose dateCreated value is newer, and yields non-empty pages with organization_slug added.

**Call relations**: paginate calls _releases for the releases stream. _releases uses _organizations to find each organization, _paged_list to fetch the releases endpoint, and with_context to preserve which organization each release belongs to.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).
