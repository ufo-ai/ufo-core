# Work management, recruiting, HR, and incident source providers  `stage-14.2.6`

This stage is shared behind-the-scenes support for bringing outside company data into the system. Each file is a connector, meaning a small adapter that knows how to talk to one web service, ask for data, follow result pages, and reshape the answers into records the rest of the product can store and search.

The work-management connectors cover Asana, ClickUp, Jira, Linear, monday.com, and Wrike. They read projects, tasks, issues, comments, boards, teams, goals, and related activity so the system can remember what work is happening and where. The recruiting connectors, Ashby, Greenhouse, and Recruitee, read candidates, jobs, applications, interviews, offers, and departments. The HR connectors, BambooHR, Deel, and Rippling, bring in employee, contract, time-off, timesheet, payroll, company, and team information. Calendly adds scheduling data such as users, event types, meetings, and invitees. PagerDuty and Sentry add engineering operations data, including incidents, on-call schedules, services, software errors, releases, and events. Together, these adapters act like import docks for many tools, all feeding the same sync pipeline.

## Files in this stage

### Work management connectors
Connectors that sync project, task, issue, workspace, and collaboration records from work-tracking platforms.

### `extensions/sources/ufo_ext_sources/providers/asana.py`

`io_transport` · `source sync`

Asana is a project and task tracking service. This connector is the adapter that lets UFO read from Asana without the rest of the system needing to know Asana’s API rules. It defines which Asana collections can be synced, what field identifies each record, and which streams can be synced incrementally instead of reread from scratch.

The file starts by describing Asana streams as `StreamSpec` objects. A stream is one kind of Asana object, like `tasks` or `projects`. Each stream uses Asana’s `gid` as its unique ID. Some streams, such as tasks and projects, can ask Asana for only records changed since a previous checkpoint. Others are read fully each time because Asana does not support that kind of filtering for them.

`AsanaConnector` is the actual connector. It knows Asana’s base API URL, the list of streams, and how to checkpoint progress using text timestamps. Its pagination method follows Asana’s page-by-page response style: ask for up to 100 records, yield them, then follow the `next_page.offset` token if Asana provides one. This is like reading a long document one numbered slip at a time, where each slip tells you where to find the next.

If Asana rejects a stream with a 401 or 403 status, the connector treats that stream as unavailable because the token lacks permission, rather than crashing the whole sync.

#### Function details

##### `_stream`  (lines 26–40)

```
def _stream(name: str, *, cursor_field: str | None=None, updated_at_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Asana stream, such as tasks or users. It keeps the stream list compact and consistent, so every stream uses the same naming and primary-key rules.

**Data flow**: It receives a stream name and optional details like which field acts as the cursor, which field marks updates, and whether the stream is a main canonical object. It packages those choices into a `StreamSpec`, using the stream name as both the public stream name and the Asana source object, and using `gid` as the unique record key. The result is a stream definition that the connector can later use during syncing.

**Call relations**: This helper is used while building the file’s `ASANA_STREAMS` catalog. It hands each completed stream definition to the connector setup, and internally it calls `StreamSpec.__init__` to create the object the wider source-sync machinery understands.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 77–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Asana stream page by page and yields batches of records to the sync system. It also applies incremental syncing for streams that support it, so tasks and projects can be fetched from a saved timestamp instead of always starting over.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor value. It builds an Asana API request with a page size of 100, adds `modified_since` when the stream supports incremental updates, then repeatedly asks Asana for data. From each response it pulls out the `data` list, turns missing or invalid lists into an empty list with `list_or_empty`, yields non-empty record batches, and follows `next_page.offset` until there is no next page. If Asana replies with 401 or 403, it turns that refusal into `StreamSkipped`; other HTTP errors are passed upward unchanged.

**Call relations**: The broader REST source runner calls this method when it is time to sync a specific Asana stream. During the loop, this method relies on the connector’s HTTP GET helper to fetch each page, uses `list_or_empty` to safely normalize the records, and raises `StreamSkipped` when Asana says the current credentials are not allowed to read that stream.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/providers/clickup.py`

`io_transport` · `source sync`

ClickUp stores work like a set of nested boxes: teams contain spaces, spaces contain folders and lists, and lists contain tasks, comments, and custom fields. This file is the map the system uses to open those boxes in the right order. Without it, the project would not know which ClickUp web addresses to call, how to follow the hierarchy, or how to add parent information such as “this task came from this list.”

The main class, ClickUpConnector, is a read-only connector. It uses an authenticated HTTP client to call ClickUp’s API. It first discovers broad objects like teams, then walks down to spaces, folders, and lists. Once it has the lists, it can fetch the leaf-level data: tasks, list comments, and list custom fields. For tasks and comments, it can also use a saved cursor, which is a checkpoint saying “only bring back records newer than this value.”

The file also defines the available streams, including their names, primary keys, and timestamp fields. The paginate method is the traffic director: given a requested stream, it chooses the right helper method and yields batches of records. If ClickUp refuses access with a 401 or 403 response, the stream is skipped instead of crashing the whole sync. Finally, flatten reshapes some ClickUp records into more consistent fields, such as name, status, author, or created_at.

#### Function details

##### `ClickUpConnector._teams`  (lines 63–65)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the ClickUp teams available to the authenticated account. Teams are the top level of ClickUp’s structure, so most other reads start here.

**Data flow**: It receives an HTTP client that already knows how to talk to ClickUp. It calls the /team endpoint, looks inside the response for the teams list, and returns that list of team records.

**Call relations**: This is the first step for several larger flows. _spaces uses it to find where spaces live, _users uses it to collect members, _goals uses it to fetch goals per team, and _root_records uses it when the requested stream is teams.

*Call graph*: called by 4 (_goals, _root_records, _spaces, _users); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 67–75)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches all non-archived spaces inside all available teams. A space is the next ClickUp container below a team.

**Data flow**: It starts with the HTTP client, asks _teams for the known teams, then loops through valid team IDs. For each team, it calls ClickUp’s team spaces endpoint, extracts the spaces, adds the parent team_id to each space record, and returns one combined list.

**Call relations**: This function builds on _teams and is itself used by _folders and _lists to continue walking down the ClickUp hierarchy. _root_records also calls it directly when the system is syncing the spaces stream.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, _root_records); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 77–87)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches all non-archived folders inside all discovered spaces. Folders are optional containers that can hold ClickUp lists.

**Data flow**: It receives the HTTP client, asks _spaces for all spaces, then visits each valid space ID. It calls the folders endpoint for that space, extracts the folders, adds the parent space_id to each record, and returns all folders together.

**Call relations**: This sits in the middle of the hierarchy walk. It relies on _spaces to know where to look, and _lists relies on it to find lists that are inside folders. _root_records calls it when the folders stream is requested.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, _root_records); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 89–105)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches all non-archived ClickUp lists, including lists inside folders and lists placed directly in a space. Lists matter because tasks, comments, and custom fields are read from them.

**Data flow**: It first asks _folders for every folder, then fetches lists inside each valid folder and adds folder_id to those list records. It then asks _spaces for every space, fetches lists that are directly under each valid space, adds space_id to those records, and returns the combined result.

**Call relations**: This is the gateway to the leaf data. _tasks and _list_child_stream call it before they can fetch tasks, comments, or custom fields. _root_records also calls it when the lists stream itself is being synced.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _root_records, _tasks); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 107–132)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches tasks from every discovered ClickUp list, page by page. It can skip older tasks when given a cursor, so repeated syncs do not need to reread everything.

**Data flow**: It receives an HTTP client and an optional cursor value. It asks _lists for all lists, then for each valid list ID it requests task pages from ClickUp, adding list_id and list_name to each task. If a cursor is present, it keeps only tasks whose date_updated is newer. It yields each non-empty batch of tasks as it goes.

**Call relations**: paginate calls this when the requested stream is tasks. This function depends on _lists to know which list endpoints to visit, and uses the shared record-extraction and context helpers to turn ClickUp responses into useful records.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 134–153)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches per-list child records that are not tasks: list comments and list custom fields. It lets the same traversal code serve both of those closely related streams.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It asks _lists for every list, chooses the correct ClickUp endpoint based on the stream name, extracts either comments or fields, adds list_id and list_name, optionally filters by the stream’s cursor field, and yields each non-empty group.

**Call relations**: paginate calls this for the list_comments and list_custom_fields streams. It sits after _lists in the hierarchy, because these records only make sense once a specific list is known.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 155–189)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main dispatcher for reading a ClickUp stream. Given a stream name, it chooses the correct helper and yields records in batches for the sync engine.

**Data flow**: It receives the HTTP client, the stream description, and an optional cursor. It checks the stream name, calls the matching method, and yields the records it gets back. If the stream is unknown, it raises StreamSkipped. If ClickUp returns a permission refusal, it turns that into StreamSkipped with a clear message instead of letting the whole process fail.

**Call relations**: This is the method the wider connector framework calls to get data. It hands root streams to _root_records, users to _users, tasks to _tasks, list comments and custom fields to _list_child_stream, and goals to _goals.

*Call graph*: calls 6 internal fn (__init__, _goals, _list_child_stream, _root_records, _tasks, _users).


##### `ClickUpConnector._root_records`  (lines 191–202)

```
async def _root_records(self, client: httpx.AsyncClient, name: str) -> list[dict[str, Any]]
```

**Purpose**: Routes simple top-level stream requests to the helper that knows how to fetch that kind of record. It covers teams, spaces, folders, and lists.

**Data flow**: It receives the HTTP client and a stream name. It matches the name to the corresponding method, returns that method’s records, or raises an error if the name is not one of the supported root collections.

**Call relations**: paginate calls this for the root hierarchy streams. It is a small switching point that keeps paginate simpler while reusing _teams, _spaces, _folders, and _lists.

*Call graph*: calls 4 internal fn (_folders, _lists, _spaces, _teams); called by 1 (paginate).


##### `ClickUpConnector._users`  (lines 204–213)

```
async def _users(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a user stream from the members embedded inside team records. ClickUp does not provide this connector with a separate flat user list, so this function collects users from teams.

**Data flow**: It receives the HTTP client, asks _teams for team records, then inspects each team’s members. When a member contains a valid user object, it stores that user by ID and adds the team_id. The result is a dictionary of unique users keyed by user ID.

**Call relations**: paginate calls this when syncing users. It depends on _teams because team responses contain the member information needed to assemble the user records.

*Call graph*: calls 1 internal fn (_teams); called by 1 (paginate).


##### `ClickUpConnector._goals`  (lines 215–223)

```
async def _goals(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches goals for each ClickUp team. Goals are treated as a separate non-canonical stream, meaning they are synced but are not one of the core hierarchy records.

**Data flow**: It receives the HTTP client, asks _teams for available teams, then calls the goals endpoint for each valid team ID. It extracts the goals, adds team_id to each one, and returns all goals in a single list.

**Call relations**: paginate calls this when the goals stream is requested. Like spaces and users, it begins from _teams because ClickUp goals are read per team.

*Call graph*: calls 1 internal fn (_teams); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 225–259)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes selected ClickUp records into fields the rest of the system can understand more consistently. For example, it turns different ClickUp names and timestamps into common fields like name, author, and created_at.

**Data flow**: It receives one raw record and the stream description. Depending on the stream, it copies the original record and adds or adjusts friendly fields: users get name, email, and created_at; spaces, folders, and lists get an API URL; tasks get status, due_date, and created_at; comments get body, author, created_at, and a parent list ID. For streams without special rules, it returns the record unchanged.

**Call relations**: This is used by the connector framework after records have been fetched, so synced data is easier to search and display. Within this function, dict_or_empty is used when reading comment user data so missing or malformed user information does not break the transformation.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/jira.py`

`io_transport` · `source sync runs`

Jira data lives behind Atlassian’s web API, and one login token can reach more than one Jira site. This connector first asks Atlassian which sites the token can access, then visits each site and reads the supported Jira streams. Think of it like a courier who first gets a list of office buildings they are allowed to enter, then collects the right folders from each building.

Most Jira lists arrive in pages, so this file includes shared paging logic that keeps asking for the next batch until Jira says there is no more. For issues, comments, and sprints, it also supports incremental syncing: it uses a saved “watermark” value, such as the last updated time, so later runs only fetch newer changes instead of rereading everything.

The connector is read-only. It does not create or edit Jira data. If Jira refuses access with an authorization error, the stream is marked as skipped rather than crashing the whole sync. Finally, the file includes rendering logic for issues and comments. Jira descriptions and comments can be stored as nested Atlassian Document Format data, so this connector walks that tree and extracts plain text that people can actually read.

#### Function details

##### `JiraConnector.paginate`  (lines 75–106)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading a Jira stream. Given a requested stream, such as issues or users, it calls the matching reader and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor saying where the last sync left off. It chooses the correct stream-specific method, passes along the cursor when needed, and yields each page of Jira records. If Jira says access is forbidden or unauthorized, it turns that into a clean skip signal instead of a hard failure.

**Call relations**: The source framework calls this when it wants records for a Jira stream. It hands work to _projects, _issues, _comments, _users, _boards, or _sprints based on the stream name, and raises StreamSkipped when the stream is unknown or Jira refuses access.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 108–112)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Jira Cloud sites the current login token can reach. Every later Jira API call needs one of these site identifiers.

**Data flow**: It uses the HTTP client to call Atlassian’s accessible-resources endpoint. It reads the JSON response, makes sure the result is treated as a list, and returns site records containing details such as the cloud ID and URL.

**Call relations**: _projects, _issues, _users, and _boards call this before reading site-specific data. Those methods use each returned cloud ID to build the correct Jira API path.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 114–134)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira API lists that are split into numbered pages. It keeps fetching until Jira indicates there are no more records.

**Data flow**: It receives an API path, optional query parameters, and the JSON key where records are stored. It sends requests with startAt and maxResults values, extracts the records from each response, yields non-empty pages, and stops when the page is marked last, empty, or past the reported total.

**Call relations**: _projects, _issues, _comments, _boards, and _sprints use this as their common paging helper. It calls records_at to pull the actual list out of Jira’s response envelope.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 136–143)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira projects from every accessible Jira site. Projects are the top-level containers that issues belong to.

**Data flow**: It gets the accessible sites, skips any site without a usable cloud ID, builds the project-search API path for each site, and yields pages of project records. Before yielding, it adds context such as the cloud ID and site URL to every record.

**Call relations**: paginate calls this when the requested stream is projects. This method relies on _sites to discover where to look, _offset_values to read pages, and with_context to attach site information that later steps may need.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 145–157)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira issues, optionally only those updated after the previous sync. Issues are the main work items in Jira, such as bugs, tasks, or stories.

**Data flow**: It builds a Jira Query Language string, which is Jira’s search syntax, ordering issues by update time and adding an updated-after filter when a cursor exists. It then visits each accessible site, calls Jira’s search API, requests only the fields this connector needs, and yields pages with site context attached.

**Call relations**: paginate calls this for the issues stream, and _comments also calls it to find which issues may have comments. It uses _sites for site discovery, _offset_values for paged search results, and with_context to keep cloud ID and site URL with each issue.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 159–179)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads comments attached to Jira issues. Because Jira comments are found under each issue, it first walks through issues and then asks for comments issue by issue.

**Data flow**: It reads all issues without applying the comment cursor to the issue list. For each issue with a valid issue ID and cloud ID, it fetches comment pages from that issue’s comment endpoint. If a cursor is present, it filters out comments whose updated time is not newer, then yields remaining comments with cloud, site, issue ID, and issue key context added.

**Call relations**: paginate calls this for the issue_comments stream. It depends on _issues to find the parent issues, uses _offset_values to page through comments, and uses with_context so each comment still knows which issue and site it came from.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 181–192)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira users from each accessible site. Unlike several other Jira endpoints, this users endpoint returns a plain list rather than a wrapped page object.

**Data flow**: It gets all accessible sites, skips invalid cloud IDs, calls the users/search endpoint for each site, converts the JSON response into a safe list, and yields users if any are found. It adds the cloud ID and site URL to each user record.

**Call relations**: paginate calls this for the users stream. It uses _sites to find Jira sites, list_or_empty to safely treat the response as a list, and with_context to preserve where each user came from.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 194–201)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads agile boards from each accessible Jira site. Boards are Jira Agile views that organize issues for teams.

**Data flow**: It asks for accessible sites, skips any without a valid cloud ID, builds the agile board API path for each site, and yields paged board records. Each yielded record is tagged with the cloud ID and site URL.

**Call relations**: paginate calls this for the boards stream, and _sprints calls it because sprints are listed under boards. It uses _sites for site discovery, _offset_values for paging, and with_context for site metadata.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 203–217)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads sprints from Jira agile boards, optionally only sprints updated after the previous sync. A sprint is a time-boxed work period used by many agile teams.

**Data flow**: It first reads boards. For each board with a usable board ID and cloud ID, it calls that board’s sprint endpoint, pages through the results, filters by updatedDate when a cursor is present, and yields any remaining sprints with cloud and board context attached.

**Call relations**: paginate calls this for the sprints stream. It depends on _boards to find parent boards, uses _offset_values to read sprint pages, and uses with_context so each sprint can be traced back to its board and Jira site.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 219–226)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This adjusts issue records so the sync system can find the issue update time in a simple top-level field. Other streams already have their important fields in the expected place.

**Data flow**: It receives one record and its stream description. For issues, it reads the nested fields object and copies fields.updated to a top-level updated value; for every other stream, it returns the record unchanged.

**Call relations**: This is part of the connector interface used after records are fetched and before checkpointing. It calls _dict_or_empty so malformed or missing issue fields do not cause an error.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 228–254)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Jira records into readable text pages. It gives issues and comments human-friendly titles and bodies instead of leaving them as raw JSON.

**Data flow**: It receives a record and stream description. For issues, it pulls out the summary, status, priority, assignee, reporter, and description text; for comments, it uses the author and comment body. It returns a title plus a Markdown-like page body with a heading and readable text.

**Call relations**: The source framework uses this when it needs a searchable or recallable page representation. It calls helper functions to safely read strings and dictionaries, format people and field lines, and turn Atlassian Document Format trees into plain text; for streams it does not customize, it falls back to the parent connector’s rendering.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 257–258)

```
def _str(value: Any) -> str
```

**Purpose**: This safely turns a value into a string only if it already is one. It avoids accidentally showing non-text values as confusing text.

**Data flow**: It receives any value. If the value is a string, it returns it; otherwise it returns an empty string.

**Call relations**: render uses this when reading issue fields, and _person uses it when choosing a display name or email address. It is a small safety helper for messy API data.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 261–262)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This safely treats a value as a dictionary only when it really is one. It protects the connector from missing or unexpectedly shaped Jira fields.

**Data flow**: It receives any value. If the value is a dictionary, it returns it; otherwise it returns an empty dictionary that callers can safely read from.

**Call relations**: flatten, render, and _person call this before reading nested fields. It lets those functions ask for keys without first writing repeated type checks.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 265–267)

```
def _person(value: Any) -> str
```

**Purpose**: This extracts a readable person name from a Jira user object. It prefers the display name, and falls back to the email address.

**Data flow**: It receives a possible person record. It treats it as a dictionary if possible, reads displayName and emailAddress safely, and returns the first non-empty text value.

**Call relations**: render calls this when showing an issue assignee, issue reporter, or comment author. It relies on _dict_or_empty and _str to stay safe when Jira omits or reshapes user data.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 270–271)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: This formats one metadata line, such as “Status: Done”, but only when there is a value to show. It keeps rendered pages from filling with empty labels.

**Data flow**: It receives a label and a value. If the value is non-empty, it returns a formatted label-and-value line; otherwise it returns an empty string.

**Call relations**: render calls this while building the issue metadata block. Empty results are filtered out before the final page body is assembled.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 274–291)

```
def _doc_text(value: Any) -> str
```

**Purpose**: This converts Jira’s nested rich-text document format into plain readable text. It is used for issue descriptions and comment bodies.

**Data flow**: It receives any value, usually an Atlassian Document Format tree. It walks through dictionaries and lists, collects every text leaf it finds, joins the collected pieces with newlines, and returns the cleaned result. Plain strings or missing bodies produce empty text here.

**Call relations**: render calls this for issue descriptions and comment bodies. Inside the function, the nested walk helper does the tree traversal.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 279–288)

```
def walk(node: Any) -> None
```

**Purpose**: This is the small recursive walker inside _doc_text. It searches through nested Jira document nodes for actual text pieces.

**Data flow**: It receives one node from the document tree. If the node is a dictionary, it collects its text field when present and then visits its content children; if the node is a list, it visits each item. It does not return a value, but it adds found text to the surrounding chunks list.

**Call relations**: _doc_text starts this helper on the root value. The helper calls itself as it descends through child nodes, like opening folders inside folders until it finds the paper notes inside.


### `extensions/sources/ufo_ext_sources/providers/linear.py`

`io_transport` · `source sync`

Linear is a work-tracking tool, and this connector is the read-only bridge from Linear into UFO’s source system. Without this file, the system would not know which Linear objects exist, how to ask Linear for them, how to page through long result lists, or how to turn raw API records into readable page text.

Linear uses GraphQL, which is an API style where the caller sends one query describing exactly which fields it wants back. This file defines one query for each Linear stream, such as issues, projects, users, comments, labels, and workflow states. It also marks which streams can be synced incrementally by looking at their updatedAt time, and which streams must be fully reread every run because Linear does not support that filter for them.

The main class, LinearConnector, asks Linear for one page of results at a time. Think of it like reading a long report one sheet at a time: each response says whether another sheet exists and gives the bookmark for where to continue. If Linear rejects access with an authorization error, the stream is skipped cleanly. If Linear reports a GraphQL error, the connector stops loudly so it does not save partial or misleading data.

The file also improves how important records are shown. Instead of storing only raw JSON-like data, issues, projects, comments, and users are rendered into human-readable text with titles and useful details.

#### Function details

##### `_stream`  (lines 36–46)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates the description of one Linear stream, such as issues or projects. It records the stream name, which time fields are used for tracking changes, and whether the stream is considered canonical content.

**Data flow**: It receives a stream name, an optional cursor field, and a canonical flag. It packages those choices with standard Linear timestamp field names into a StreamSpec object. The result is used later to decide what to sync and how to track progress between runs.

**Call relations**: This helper is used while the file builds the list of Linear streams. It hands its settings to StreamSpec, which is the shared source framework’s object for describing a stream.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 267–310)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads records from Linear one page at a time for a chosen stream. It is the core network-reading loop that keeps asking Linear for more results until there are no more pages.

**Data flow**: It starts with an HTTP client, a stream description, and possibly a saved cursor from a previous run. It chooses the right GraphQL query, adds an updatedAt filter when the stream supports incremental syncing, sends requests to Linear, checks for access refusals or GraphQL errors, extracts the list of records, and yields each non-empty page. It also follows Linear’s page cursor so the next request continues where the previous one stopped.

**Call relations**: The source framework calls this when it needs records for a Linear stream. If Linear refuses access, it creates a StreamSkipped error so the wider run can record that this stream was not available instead of treating it like ordinary data. When a response contains nodes, it passes them through list_or_empty so the rest of the sync receives a clean list rather than a surprising null or malformed value.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 312–354)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns selected Linear records into readable page text. It gives issues, projects, comments, and users useful titles and bodies instead of leaving them as raw API data.

**Data flow**: It receives one Linear record and the stream it came from. For known streams, it picks the fields people care about, such as an issue title, state, priority, assignee, project lead, target date, comment body, or user email. It builds a title and a Markdown-like body, falling back to the first body line or the default renderer if no title is present. It returns the final page title and page text.

**Call relations**: After pagination has produced records, the connector framework can call this to shape each record for storage or recall. It relies on _str to safely read text fields, _ref_id to extract IDs from nested reference objects, and _labeled to format useful metadata lines.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 357–358)

```
def _str(value: Any) -> str
```

**Purpose**: This small safety helper returns a value only if it is actually text. It prevents non-text values from accidentally appearing in rendered page titles or bodies.

**Data flow**: It receives any value. If the value is a string, it returns that string. Otherwise it returns an empty string, so the caller can safely join or display it without extra checks.

**Call relations**: LinearConnector.render uses this whenever it reads text from a Linear record. _ref_id also uses it after pulling an id field from a nested object, so ID rendering follows the same safe text rule.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 361–362)

```
def _ref_id(value: Any) -> str
```

**Purpose**: This helper extracts an id from a nested Linear reference object, such as an assignee or lead. It keeps the render code simple when a record points to another Linear object.

**Data flow**: It receives any value. If the value is a dictionary-like object with an id field, it reads that id and passes it through _str to make sure it is text. If the value is not the expected shape, it returns an empty string.

**Call relations**: LinearConnector.render calls this when building human-readable metadata for records that reference another object. It delegates the final text check to _str.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 365–366)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This helper formats a short set of metadata lines, such as state, priority, or email. It leaves out empty values so the rendered page stays clean.

**Data flow**: It receives a list of label-and-value pairs. It keeps only the pairs with a non-empty value, turns each one into a line like label: value, and joins the lines together into one block of text. The output is ready to be placed near the top of a rendered page.

**Call relations**: LinearConnector.render calls this when preparing readable summaries for issues, projects, and users. It acts as the final formatting step for metadata already cleaned up by helpers like _str and _ref_id.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/monday.py`

`io_transport` · `sync run / source pagination`

monday.com exposes its data through GraphQL, which is a way to ask for exactly-shaped data using a query sent over HTTP. This connector is the project’s read-only bridge to that API. Without it, monday.com content could not be synced into the system as recallable pages.

The file defines the monday.com streams the system knows about, such as boards, items, and updates. A stream is one category of records to sync. Some streams are simple top-level lists, like users or workspaces. Others need extra steps: items are fetched board by board, and activity logs are also fetched per board.

The connector pays special attention to paging. monday.com only returns a limited number of records at once, so the code repeatedly asks for the next page until there is nothing left. For incremental syncs, monday.com does not offer a true “only since this time” filter, so the connector fetches pages and then locally keeps only records newer than the saved cursor, such as `updated_at` or `created_at`.

If monday.com refuses a request, or GraphQL returns an error, the connector raises `StreamSkipped`. That tells the larger sync run, “this stream could not be safely read,” instead of silently saving a partial or misleading result.

#### Function details

##### `_extract_person_ids`  (lines 71–99)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: This helper pulls assigned person IDs out of monday.com item column data. monday stores assignees inside board-specific “people” columns, so this function finds those columns by type instead of relying on a fixed column name.

**Data flow**: It receives the raw `column_values` from an item. It looks for columns whose type is `people`, parses their stored JSON value if needed, keeps entries marked as actual people rather than teams, and returns a flat list of person IDs as strings. Bad, missing, or unexpected data is ignored rather than causing the sync to fail.

**Call relations**: When `MondayConnector._items` fetches item records, it calls this helper for each item so the final item record has an easy-to-use `assignee_ids` field. The helper uses JSON parsing only when monday.com has stored the people data as a JSON string.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 108–120)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This is the connector’s shared doorway to monday.com’s GraphQL API. It sends a query, checks whether monday.com reported GraphQL errors, and returns the useful `data` section.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional variables. It posts them to monday.com’s API root path, reads the response body, raises `StreamSkipped` if monday.com returned GraphQL errors, and otherwise returns the response’s `data` object or an empty dictionary if the shape is not usable.

**Call relations**: All the lower-level fetchers use this function whenever they need data from monday.com: paged root streams, item pages, activity logs, and single-query streams. By centralizing error checking here, those callers do not each need to remember how monday.com reports refused or unavailable GraphQL queries.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, _single_root).


##### `MondayConnector._paged_root`  (lines 122–145)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads monday.com collections that use simple numbered pages, such as page 1, page 2, and so on. It also applies the saved cursor locally when the stream is incremental.

**Data flow**: It receives the API client, the name of a top-level monday.com field to query, the fields to request for each record, and optionally a cursor plus the record field to compare against it. It repeatedly asks monday.com for 100 records at a time, converts the response into a list, removes records at or before the cursor when needed, yields each non-empty page, and stops when no records remain.

**Call relations**: This is the common paging engine used by `_boards` and by `_stream_pages` for streams such as users, workspaces, boards, and updates. It relies on `_graphql` for the actual API call and on a safe list conversion helper so unexpected response shapes do not crash normal processing.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, _stream_pages); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 147–158)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function gathers the full list of boards visible to the current monday.com credentials. Other parts of the connector need that board list before they can fetch board-specific data.

**Data flow**: It receives the API client, asks `_paged_root` for every page of boards with key board and workspace fields, collects those pages into one list, and returns that list. It does not apply an incremental cursor because it is often used as a directory for finding other records.

**Call relations**: `_items` and `_activity_logs` call this first because monday.com requires board IDs before item pages or activity logs can be queried. `_boards` itself delegates the page-by-page work to `_paged_root`.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 160–219)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads monday.com items from every board. Items are not fetched as one simple global list, so the connector first finds boards and then walks through each board’s item pages.

**Data flow**: It receives the API client and an optional saved cursor. It fetches all boards, skips any board without an ID, then asks monday.com for the first item page on each board and follows monday.com’s item-page cursor for later pages. For each item, it adds `assignee_ids` by reading people columns, filters out old items when a cursor is present, yields non-empty pages, and moves on when there is no next item cursor.

**Call relations**: `_stream_pages` calls this when the requested stream is `items`. This function depends on `_boards` to know which boards to scan, `_graphql` to query monday.com, `_extract_person_ids` to make assignees easier to use, and safe dictionary/list helpers to tolerate missing or oddly shaped API data.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (_stream_pages); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 221–249)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads activity log entries for each monday.com board. These logs describe actions or events on boards and are attached back to the board they came from.

**Data flow**: It receives the API client and an optional cursor. It gets the list of boards, asks monday.com for up to 100 activity logs for each board, adds the board ID to every log entry, filters out entries at or before the saved `created_at` cursor when needed, and yields the remaining records.

**Call relations**: `_stream_pages` calls this for the `activity_logs` stream. Like item syncing, it first relies on `_boards` because activity logs are requested per board, then uses `_graphql` for the actual GraphQL request and a safe list helper for response cleanup.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (_stream_pages); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 251–267)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the public paging method the source framework uses to get pages from a monday.com stream. It also turns common permission failures into a clean “stream skipped” result.

**Data flow**: It receives the HTTP client, the stream description, and an optional cursor. It asks `_stream_pages` to produce pages and yields them outward one by one. If monday.com responds with HTTP 401 or 403, meaning unauthenticated or not allowed, it raises `StreamSkipped` with a clear message; other HTTP errors are allowed to bubble up.

**Call relations**: The wider sync framework calls this when it wants records for a stream. `paginate` then hands the request to `_stream_pages`, but wraps that flow with monday-specific refusal handling so a missing scope or bad token does not look like a successful partial sync.

*Call graph*: calls 2 internal fn (__init__, _stream_pages).


##### `MondayConnector._stream_pages`  (lines 269–307)

```
def _stream_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function chooses the right fetching strategy for each named monday.com stream. It is like a dispatcher that says, “users use this query, items need the board-by-board path, activity logs need their own path.”

**Data flow**: It receives the API client, a stream name, and an optional cursor. Based on the stream name, it returns an async page source: simple paged root queries for users, workspaces, boards, and updates; a one-shot root query for teams and tags; the item-specific fetcher for items; or the activity-log fetcher for logs. If the name is unknown, it raises `StreamSkipped`.

**Call relations**: `paginate` calls this after the framework asks for a stream. `_stream_pages` then delegates to `_paged_root`, `_single_root`, `_items`, or `_activity_logs`, depending on what monday.com requires for that data shape.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _items, _paged_root, _single_root); called by 1 (paginate).


##### `MondayConnector._single_root`  (lines 309–320)

```
async def _single_root(self, client: httpx.AsyncClient, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads monday.com streams that can be fetched with one simple query instead of page-by-page looping. In this file, that means teams and tags.

**Data flow**: It receives the API client and the stream name. It builds the correct GraphQL query for teams or tags, sends it through `_graphql`, reads the list under that stream name, and yields it only if there are records.

**Call relations**: `_stream_pages` calls this for the `teams` and `tags` streams. It still uses `_graphql`, so GraphQL errors are handled the same way as the larger paged streams.

*Call graph*: calls 1 internal fn (_graphql); called by 1 (_stream_pages).


##### `MondayConnector.flatten`  (lines 322–363)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes raw monday.com records into a more consistent form for the rest of the source system. It keeps the original data but adds or normalizes common fields such as title-like names, body text, authors, timestamps, and parent links.

**Data flow**: It receives one raw record and the stream it came from. For known streams, it copies the record and fills standardized fields: users keep name and email, boards and workspaces get an API URL, items get status, updates get readable body and author information, and activity logs get subject, body, author, and parent board ID. If the stream has no special rule, the record is returned unchanged.

**Call relations**: After records have been fetched by the pagination flow, the broader connector framework can call `flatten` before storing or indexing them. Inside the updates case, it uses a safe dictionary helper so missing creator information does not break the transformation.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/wrike.py`

`io_transport` · `source sync`

Wrike’s API returns data in pages, like a long paper form split across several sheets. This connector knows how to keep asking for the next sheet until there are no more. It also knows which Wrike collections are supported and which field should be treated as the record’s identity, update time, or display name.

The file matters because the rest of the system expects each source to behave in a common way: list its streams, fetch records page by page, skip streams it cannot access, and shape raw records into more useful records. Without this file, Wrike data would either not be imported or would arrive in Wrike’s raw format, which is less consistent with other sources.

One important detail is incremental syncing. Wrike does not provide a dependable “only give me records changed since this time” filter. So this connector reads pages and then locally drops records whose updated date is not newer than the saved checkpoint, also called a watermark. Another important detail is access errors: if Wrike returns 401 or 403, meaning the key is invalid or does not have permission, the connector skips that stream instead of crashing the whole sync. Credentials are not stored here; they are supplied by the wider runner through its authentication path.

#### Function details

##### `_profile_email`  (lines 55–65)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: Finds the first usable email address inside a Wrike contact record. Wrike stores emails inside a nested profiles list, so this helper gives the rest of the connector a simple email value or nothing.

**Data flow**: It receives one Wrike record as a dictionary. It looks for a profiles field, checks that it is a list, then walks through each profile and returns the first non-empty email string it finds. If the profiles field is missing, malformed, or has no email, it returns null.

**Call relations**: This helper is used when WrikeConnector.flatten prepares contact records. The flattening step asks this helper to pull an email out of Wrike’s nested contact shape so the final record has a straightforward email field.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 74–98)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Wrike records page by page for one stream, such as tasks or folders. It also applies the saved update checkpoint when possible, so older records are not sent onward again.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value that represents the last saved update point. It first checks whether the stream is one this connector can actually read. Then it asks the shared REST paging helper for pages from the matching Wrike API path, using Wrike’s nextPageToken to move through the result set. If the stream has an update-date field and a cursor was supplied, it keeps only records newer than that cursor. It yields each non-empty batch of records. If Wrike refuses access with 401 or 403, it turns that into a StreamSkipped signal; other HTTP errors are allowed to rise normally.

**Call relations**: During a sync, the wider source framework calls this method to read each Wrike stream. This method relies on the base REST connector’s cursor-page reader to do the repeated HTTP requests. When a stream is not implemented or Wrike says access is forbidden, it creates a StreamSkipped exception so the runner can skip that stream cleanly rather than treating it as a total failure.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 100–138)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns raw Wrike records into records with common, easy-to-use fields such as name, email, status, due_date, created_at, body, and parent_external_id. This makes Wrike data more consistent with data from other sources.

**Data flow**: It receives one raw Wrike record and the stream description that says what kind of record it is. For contacts, it builds a display name from first and last name when possible, extracts the email with _profile_email, and copies the creation date. For folders, it uses the title as the name and builds a Wrike API URL. For tasks, it uses the title as the name, chooses a status, reads due-date information from the nested dates object when present, and copies the creation date. For comments, it maps text to body, authorId to author, and taskId to the parent record link. For streams without special shaping, it returns the record unchanged.

**Call relations**: After WrikeConnector.paginate has produced raw records, the source framework can call this method to normalize each one. For contact records it hands part of the work to _profile_email, and for task records it uses dict_or_empty so a missing or invalid dates field can be treated safely as an empty dictionary.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).


### Recruiting connectors
Connectors that sync candidates, jobs, applications, interviews, offers, departments, and recruiting lookup data.

### `extensions/sources/ufo_ext_sources/providers/ashby.py`

`io_transport` · `source sync pagination`

Ashby exposes recruiting data through a web API, but it does not send everything in one response. Each list request returns one page of results and, if more data exists, a cursor that works like a bookmark for the next page. This file wraps that pattern so the rest of the project can simply ask for an Ashby stream and receive batches of records.

The file first defines the Ashby streams the connector knows about. Each stream says which Ashby endpoint to call, which field identifies a record, and which date field can be used as a progress marker for incremental syncing. Incremental syncing means reading only records changed since the last successful run, instead of rereading everything every time.

Authentication has one Ashby-specific twist: Ashby expects HTTP Basic authentication, where the API key is used as the username and the password is blank. The connector converts the stored API key into that header unless a credential broker has already provided a custom transport.

Most streams use the same paging loop: POST a JSON body with a limit, optionally include the previous sync token, yield records, then follow Ashby's next cursor. One stream is different: criteria evaluations belong under individual applications, so the connector first lists applications, then asks Ashby for evaluations for each application. If Ashby returns 401 or 403, meaning the key is unauthorized or lacks permission, the stream is skipped rather than crashing the whole sync.

#### Function details

##### `_stream`  (lines 30–48)

```
def _stream(name: str, *, path: str, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a small description of one Ashby stream, such as candidates or jobs. This description tells the shared sync machinery what endpoint to call, how to identify records, and which timestamp can track progress.

**Data flow**: It receives a stream name, an Ashby API path, and optional details such as the primary key and cursor field. It packages those details into a StreamSpec object, adding common Ashby defaults like createdAt and updatedAt field names. The result is a reusable stream definition used later by the connector.

**Call relations**: This helper is used while building the ASHBY_STREAMS list at import time. It hands stream metadata to StreamSpec so the RestConnector base class and AshbyConnector know what each Ashby stream is supposed to read.

*Call graph*: 1 external calls (__init__).


##### `AshbyConnector._make_client`  (lines 85–93)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Ashby, with Ashby's required authentication format. It makes sure a plain API key is converted into the Basic Authorization header Ashby expects.

**Data flow**: It receives a base URL and a Credential object. If the credential already includes a custom transport, it leaves that alone and delegates to the parent connector. Otherwise, it reads the bearer API key, encodes it as 'api_key:' using Base64, creates an Authorization header, and asks the parent connector to build the final HTTP client. If no API key is present, it raises an error because it cannot authenticate.

**Call relations**: The broader connector setup calls this when it needs a network client for Ashby. This method does only the Ashby-specific authentication conversion, then hands client creation back to the shared RestConnector behavior.

*Call graph*: 2 external calls (__init__, b64encode).


##### `AshbyConnector.paginate`  (lines 95–111)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right paging strategy for an Ashby stream and yields pages of records. It also turns permission failures into a clean 'skip this stream' signal.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor from a previous run. For most streams, it passes those inputs to the normal Ashby paging loop. For application criteria evaluations, it uses the special per-application loop. It yields each non-empty page of records to the caller. If Ashby replies with 401 or 403, it raises StreamSkipped with a message explaining that the API key lacks access.

**Call relations**: The sync engine calls this when it is reading a stream. This method acts like a traffic director: ordinary streams go to AshbyConnector._paginate_default, while the special criteria stream goes to AshbyConnector._paginate_application_criteria. Any refused stream is reported through StreamSkipped so the larger run can continue where appropriate.

*Call graph*: calls 3 internal fn (__init__, _paginate_application_criteria, _paginate_default).


##### `AshbyConnector._paginate_default`  (lines 113–134)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a normal Ashby list endpoint page by page. It is the standard loop for streams where one endpoint directly returns the records.

**Data flow**: It starts with a JSON request body containing the page size. If a previous run cursor exists, it sends that as Ashby's syncToken so Ashby can return changed records. It repeatedly posts to the stream's endpoint, yields the 'results' records when present, and follows 'nextCursor' while 'moreDataAvailable' is true. The output is a sequence of record batches, and the loop stops when Ashby says there is no more data or fails to provide the next cursor.

**Call relations**: AshbyConnector.paginate calls this for every regular stream. It relies on the inherited _post helper to do the actual HTTP POST request, while this method focuses on Ashby's cursor-and-page rules.

*Call graph*: called by 1 (paginate).


##### `AshbyConnector._paginate_application_criteria`  (lines 136–172)

```
async def _paginate_application_criteria(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads criteria evaluations, which Ashby stores underneath individual applications rather than as one simple list. It first finds applications, then asks for the evaluations attached to each application.

**Data flow**: It pages through '/application.list' to get application records. For each application with an id, it posts to '/application.listCriteriaEvaluations' with that id. It copies each returned evaluation, ensures it includes the applicationId, groups the evaluations for that application, and yields the group when there is anything to return. It continues through all application pages using Ashby's cursor fields.

**Call relations**: AshbyConnector.paginate calls this only for the 'application_criteria_evaluations' stream. This method performs the fan-out pattern: one application list request leads to many detail requests, one per application, because that is how Ashby's API exposes these records.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/greenhouse.py`

`io_transport` · `source sync / API pagination`

Greenhouse stores recruiting information such as candidates, jobs, applications, interviews, offers, users, departments, scorecards, and many smaller lookup lists. This connector turns those Greenhouse API endpoints into named “streams,” meaning repeatable sets of records the rest of the system can sync and remember.

Most Greenhouse endpoints return a simple list of JSON objects, like a stack of forms with no wrapper around them. For those streams, the connector asks one API path, follows Greenhouse’s pagination links until there are no more pages, and yields each page of records. Some streams are nested under a parent record, such as interviews under an application or openings under a job. For those, the connector first walks through the parent list, then fetches each parent’s child records, and stamps each child with the parent ID so the relationship is not lost later.

The file also handles Greenhouse’s authentication style. Greenhouse uses HTTP Basic authentication, where the API key is sent as the username and the password is blank. If authentication is being supplied by a brokered proxy instead, this connector leaves the base client alone.

For ongoing syncs, some streams can be filtered by a cursor, such as “updated after this time.” If Greenhouse says the key is unauthorized for a stream, the connector marks that stream as skipped instead of failing the whole run.

#### Function details

##### `_stream`  (lines 66–84)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', ca
```

**Purpose**: This helper creates a stream description in one compact call. A stream description tells the rest of the sync system what the stream is called, which Greenhouse object it comes from, what field identifies each record, and which timestamp field can be used to continue from a previous sync.

**Data flow**: It receives stream settings such as the public stream name, optional source object name, primary key field, cursor field, created and updated timestamp fields, and whether the stream is a main canonical stream. It fills in sensible defaults, such as using the stream name as the source object when no separate source object is given. It returns a StreamSpec object that the connector later publishes in its stream list.

**Call relations**: This helper is used while the module is being loaded to build all the Greenhouse stream definitions. Those StreamSpec objects are gathered into the connector’s stream list, and later the connector’s pagination logic uses their names and cursor fields to decide which API path and query parameters to use.

*Call graph*: 1 external calls (__init__).


##### `GreenhouseConnector._make_client`  (lines 232–241)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method prepares the HTTP client that will talk to Greenhouse. Its main job is to apply Greenhouse’s required authentication format when the API key is available directly to this process.

**Data flow**: It starts with a base HTTP client created by the parent RestConnector. It then looks at the resolved credential. If the credential contains a bearer value, this method treats that value as the Greenhouse API key, installs HTTP Basic authentication with the key as the username and an empty password, and removes the normal Authorization header so the wrong auth style is not sent. It returns the adjusted client.

**Call relations**: The broader source runner asks the connector for a client before syncing streams. This method builds on the generic REST client setup from the parent class, then specializes it for Greenhouse. If authentication is handled outside the process by a proxy, it does not add Basic auth and lets the proxying transport inject credentials instead.

*Call graph*: 1 external calls (BasicAuth).


##### `GreenhouseConnector._cursor_param`  (lines 244–247)

```
def _cursor_param(stream_name: str) -> str
```

**Purpose**: This small helper chooses the Greenhouse query parameter used for incremental syncing. Most streams use updated_after, but a few Greenhouse endpoints use different names.

**Data flow**: It receives a stream name. It checks the connector’s table of exceptions, such as applications and eeoc. If the stream is listed there, it returns that special parameter name; otherwise it returns updated_after.

**Call relations**: The paginate method calls this helper when it has both a cursor-capable stream and a saved cursor value. The returned parameter name is added to the API request so Greenhouse only returns records newer than the last sync point.

*Call graph*: called by 1 (paginate).


##### `GreenhouseConnector.paginate`  (lines 249–280)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for a Greenhouse stream. Given a stream and an optional saved cursor, it yields pages of records from the correct Greenhouse endpoint.

**Data flow**: It receives an HTTP client, a stream description, and possibly a cursor value from a previous sync. First it checks whether the stream is a nested child stream, such as job openings under jobs. If so, it delegates to the per-parent pagination path. Otherwise it looks up the simple top-level API path for the stream, builds request parameters with the page size and optional cursor filter, then yields each page returned by the link-header pagination helper. If Greenhouse returns 401 or 403, meaning the key is invalid or lacks permission, it turns that into a StreamSkipped result rather than a general failure.

**Call relations**: The sync engine calls this method whenever it needs records for one Greenhouse stream. This method is the dispatcher: it decides between the simple endpoint flow and the parent-child flow. It calls _cursor_param to name incremental filters, _paginate_link_header to walk ordinary paginated endpoints, and _paginate_per_parent for streams that must fetch child records from each parent.

*Call graph*: calls 4 internal fn (__init__, _cursor_param, _paginate_link_header, _paginate_per_parent).


##### `GreenhouseConnector._paginate_link_header`  (lines 282–289)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper walks through a Greenhouse endpoint that uses HTTP Link headers for pagination. A Link header is like a “next page” sign posted in the API response.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the shared REST pagination utility to fetch pages using the configured page size of 500 and to follow each next-page link Greenhouse provides. It yields each page of records as it arrives.

**Call relations**: The main paginate method uses this helper for normal top-level streams. The per-parent pagination method also uses it twice: once to list parent records and again to list each parent’s child records. This keeps all Greenhouse Link-header behavior in one place.

*Call graph*: called by 2 (_paginate_per_parent, paginate).


##### `GreenhouseConnector._paginate_per_parent`  (lines 291–312)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str, stamp_key: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads nested Greenhouse data that only exists underneath another record. For example, it can list jobs first, then fetch the openings belonging to each job.

**Data flow**: It receives an HTTP client, the parent API path, a child-path template containing a parent ID placeholder, and the field name that should store the parent ID on each child. It pages through the parent records, pulls each parent’s id, fetches the child collection for that id, and adds the parent id to each child record if it is not already present. It yields the child pages after stamping them.

**Call relations**: The main paginate method calls this helper when a stream is listed as a per-parent stream. This helper relies on _paginate_link_header for both parent and child API calls. Its output goes back to paginate, and then onward to the sync engine with the parent-child relationship preserved.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/recruitee.py`

`io_transport` · `source sync`

This connector is a small adapter between UFO and the Recruitee web API. Recruitee exposes recruiting data through web addresses such as `/candidates`, `/offers`, and `/departments`, and it returns results in pages. This file tells UFO which Recruitee lists exist, what each record’s main ID field is, and how to walk through all the pages until there is nothing more to read.

The class does not contain a Recruitee token or company address by itself. The tenant-specific base URL is supplied by the sync runner, which is safer than guessing and accidentally calling the wrong company’s API. It also only reads data; there is no write path here.

The main behavior is pagination, meaning “keep asking for the next batch.” Each request asks for up to 100 records. Recruitee wraps rows under a key named after the stream, such as `{"candidates": [...]}`, so the connector tells the shared REST machinery where to find the actual records. If Recruitee replies with 401 or 403, meaning “not allowed” or “not authenticated,” the connector does not crash the whole idea of syncing every stream. It raises a clear `StreamSkipped` error for that stream, explaining that the key or permission scope is probably wrong.

#### Function details

##### `RecruiteeConnector.paginate`  (lines 34–51)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one Recruitee stream, such as candidates or offers, page by page. It exists so the rest of the sync system can ask for records without needing to know Recruitee’s page-number API format.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. The cursor is accepted because the shared connector interface includes it, but Recruitee streams here are full-refresh, so the function simply starts paging through the stream’s endpoint. For each page returned by Recruitee, it yields a list of record dictionaries. If Recruitee refuses access with a 401 or 403 response, it turns that low-level web error into a clearer skipped-stream message; other HTTP errors are passed upward unchanged.

**Call relations**: During a sync, the source runtime calls this method when it needs records for one configured Recruitee stream. The method delegates the repetitive page-number work to the shared REST connector helper, then hands each page back to the caller as it arrives. If access is denied, it creates a `StreamSkipped` error so the larger sync flow can report that this particular stream could not be read because of missing permissions or bad credentials.

*Call graph*: calls 1 internal fn (__init__).


### HR and workforce connectors
Connectors that sync employee, contractor, company, payroll-adjacent, time-off, timesheet, and team data from HR systems.

### `extensions/sources/ufo_ext_sources/providers/bamboohr.py`

`io_transport` · `source sync`

BambooHR is an HR system, and its API does not behave like a neat page-by-page feed. Many BambooHR endpoints return everything at once, and different endpoints wrap their records in different shapes. This file hides those differences behind one connector so the rest of the sync system can ask for a stream of records without caring about BambooHR’s quirks.

The file first defines the BambooHR streams the system knows about, such as the employee directory, detailed employee records, time-off requests, timesheet entries, metadata fields, and a custom employee report. A stream is like a named lane of data that can be synced separately.

`BambooHRConnector` then builds an HTTP client for the correct BambooHR tenant URL and authentication style. BambooHR expects HTTP Basic authentication, using the API key as the username and the literal password `x`, and it must be told to return JSON instead of its XML default.

When a sync asks for records, `paginate` acts like a traffic director. It checks which stream is being requested and sends the work to the matching fetch method. Some streams make one API call. Detailed employees first read the directory, then fetch each employee one by one. Time-based streams build a start/end date window from the saved cursor so repeat syncs can resume from a known point. If BambooHR refuses access with a 401 or 403, the connector reports that stream as skipped rather than crashing the whole source.

#### Function details

##### `_stream`  (lines 31–49)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None=None, updated_at_field: str | None=None, canonical: bool=Fa
```

**Purpose**: Creates a `StreamSpec`, which is the system’s small description card for one BambooHR data stream. It records things like the stream name, the main record key, and which field can be used as a cursor for incremental syncing.

**Data flow**: It receives the stream’s name plus optional details such as the BambooHR object name, primary key, cursor field, created/updated timestamps, and whether this is a canonical stream. It fills in sensible defaults, then returns a `StreamSpec` object that the connector later advertises as available to sync.

**Call relations**: This helper is used while the file is loaded to build the BambooHR stream list. It hands the finished settings to `StreamSpec.__init__`, so the rest of the connector can refer to consistent stream definitions instead of repeating those details by hand.

*Call graph*: 1 external calls (__init__).


##### `BambooHRConnector._make_client`  (lines 73–88)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to BambooHR. It makes sure requests go to the right tenant URL, use JSON headers, apply timeouts, and authenticate correctly.

**Data flow**: It receives a base URL and a resolved credential. It trims the URL, prepares request headers, and sets connection and read time limits. If the credential provides a custom transport, it uses that unchanged. If the credential provides an API key, it wraps that key in BambooHR’s required Basic authentication format. It returns an `httpx.AsyncClient`, or raises an error if no usable authentication is present.

**Call relations**: The broader REST source framework calls this when it needs a network client for a BambooHR run. This function delegates the low-level pieces to `httpx.Timeout`, `httpx.BasicAuth`, and `httpx.AsyncClient`, then the returned client is used by the fetch methods during syncing.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `BambooHRConnector.paginate`  (lines 90–125)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right BambooHR fetch routine for the requested stream and yields batches of records back to the sync engine. Although it is called `paginate`, BambooHR usually does not provide normal pagination, so this function mainly dispatches between endpoint-specific shapes.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching private fetch method, and passes each returned batch onward. If the stream name is unknown, it raises an error. If BambooHR returns a refusal status such as unauthorized or forbidden, it turns that into a `StreamSkipped` message explaining that the key or permission scope is not enough.

**Call relations**: This is the central route through the connector during a sync. The source framework calls it for each BambooHR stream, and it then calls `_fetch_directory`, `_fetch_employees`, `_fetch_time_off`, `_fetch_timesheets`, `_fetch_meta_fields`, or `_fetch_custom_reports` as needed. It also creates `StreamSkipped` when BambooHR blocks access to a stream.

*Call graph*: calls 7 internal fn (__init__, _fetch_custom_reports, _fetch_directory, _fetch_employees, _fetch_meta_fields, _fetch_time_off, _fetch_timesheets).


##### `BambooHRConnector._fetch_directory`  (lines 127–133)

```
async def _fetch_directory(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads BambooHR’s employee directory and returns the employee rows as one batch. This gives the system the broad list of employees available in the account.

**Data flow**: It receives the HTTP client, performs a GET request to the employee directory endpoint, and looks for records under the `employees` field in the response. If there are any records, it yields them as a list. If the response has no employees, it yields nothing.

**Call relations**: `paginate` calls this when the requested stream is `employees_directory`. It relies on the connector’s inherited GET helper to make the actual network request, then hands the resulting employee list back to `paginate` for the sync engine to consume.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_employees`  (lines 135–151)

```
async def _fetch_employees(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches full details for each employee, not just the summary information in the directory. It uses the directory as an index, then asks BambooHR for each employee’s individual record.

**Data flow**: It first downloads the employee directory. For each directory row that is a dictionary and has an `id`, it requests `/v1/employees/{id}`. If BambooHR returns a detail object, the function makes sure that object contains the employee id, then yields it as a one-record batch. Invalid rows or rows without ids are skipped.

**Call relations**: `paginate` calls this for the `employees` stream. This method performs a fan-out pattern: one directory request becomes many per-employee detail requests. Yielding one employee at a time lets the sync process make progress and checkpoint promptly instead of waiting for every employee detail request to finish.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_time_off`  (lines 153–160)

```
async def _fetch_time_off(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads time-off request records from BambooHR for a date range. The date range lets the connector support repeat syncs that start from the last known cursor instead of always asking from the beginning.

**Data flow**: It receives the HTTP client and an optional cursor. It turns the cursor into BambooHR `start` and `end` query parameters, sends a GET request to the time-off endpoint, and accepts either a raw list response or a response with records under `requests`. If records are found, it yields them as one batch.

**Call relations**: `paginate` calls this when syncing `time_off_requests`. Before making the request, it calls `_date_window_params` to translate the saved cursor into BambooHR’s required date-window format.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_timesheets`  (lines 162–169)

```
async def _fetch_timesheets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads timesheet entry records from BambooHR for a date range. This is used to bring in tracked work-time data while respecting the saved sync cursor.

**Data flow**: It receives the HTTP client and optional cursor, converts that cursor into `start` and `end` query parameters, then calls the timesheet entries endpoint. It accepts either a raw list or a response with records under `entries`. If entries exist, it yields them as a batch.

**Call relations**: `paginate` calls this for the `timesheet_entries` stream. Like the time-off fetcher, it calls `_date_window_params` first because BambooHR requires a date window for this endpoint.

*Call graph*: calls 1 internal fn (_date_window_params); called by 1 (paginate).


##### `BambooHRConnector._fetch_meta_fields`  (lines 171–177)

```
async def _fetch_meta_fields(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads BambooHR’s field catalog, which describes the fields available in the account. This helps the system know what employee-related data fields BambooHR exposes.

**Data flow**: It receives the HTTP client and sends a GET request to the metadata fields endpoint. It accepts either a raw list response or a response with records under `fields`. If any field records are present, it yields them as one batch.

**Call relations**: `paginate` calls this when the stream is `meta_fields`. The method does one endpoint-specific read and passes the normalized list of field records back into the common sync flow.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._fetch_custom_reports`  (lines 179–201)

```
async def _fetch_custom_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs a BambooHR custom report and returns its employee rows. This lets the connector request a chosen set of useful employee fields in one report-shaped response.

**Data flow**: It builds a JSON body with a report title and a fixed list of fields such as name, email, job title, department, supervisor, hire date, and employment status. It sends that body with a POST request to the custom reports endpoint, reads returned rows from the `employees` field, and yields them if any exist.

**Call relations**: `paginate` calls this for the `custom_reports` stream. Instead of a simple GET, this method uses the connector’s inherited POST helper because BambooHR requires the desired report fields to be sent in the request body.

*Call graph*: called by 1 (paginate).


##### `BambooHRConnector._date_window_params`  (lines 204–214)

```
def _date_window_params(cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the `start` and `end` date parameters required by BambooHR’s time-off and timesheet endpoints. It turns the saved cursor into the simple `YYYY-MM-DD` date format BambooHR expects.

**Data flow**: It receives an optional cursor. If the cursor is missing or blank, it uses `1970-01-01` so a first sync asks for everything. If a cursor is present, it trims it and takes the first ten characters, which turns an ISO timestamp into just its date. It returns a dictionary with that start date and a far-future end date of `2100-01-01`.

**Call relations**: `_fetch_time_off` and `_fetch_timesheets` call this right before requesting their BambooHR endpoints. It gives both fetchers the same date-window behavior, so the cursor logic is not duplicated.

*Call graph*: called by 2 (_fetch_time_off, _fetch_timesheets).


### `extensions/sources/ufo_ext_sources/providers/deel.py`

`io_transport` · `source sync`

Deel is an HR platform, and this connector is the read-only bridge between Deel and this project’s source-sync system. Without it, the system would not know which Deel objects exist, how to ask Deel for them, how to move through paged results, or how to react when an account is not allowed to read a stream.

The file first defines a small helper for describing a Deel “stream,” meaning one kind of object to sync, like contracts or payslips. It then lists the five Deel streams this connector supports. Most streams can be synced incrementally: the connector asks only for records updated after the last saved checkpoint. Forms are different because they do not have an update timestamp, so they are fully re-read each time.

The main class, DeelConnector, inherits the common REST connector used by source integrations. It sets Deel’s API base address, says which streams are available, and uses a text checkpoint function to remember progress. During a sync, it builds query parameters, requests Deel’s REST v2 endpoints in chunks of 100 records, extracts usable records from the response, and yields each chunk onward. If Deel replies with “unauthorized” or “forbidden,” the connector skips that stream with a clear message instead of crashing the whole sync. This is like checking each filing cabinet drawer: if one drawer is locked, the system notes that and continues where it can.

#### Function details

##### `_stream`  (lines 23–37)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard description of one Deel data stream, such as contracts or payslips. This keeps the stream list short and consistent, so each stream has a name, source API object, primary key, update cursor, and optional canonical status.

**Data flow**: It receives basic stream details, with sensible defaults like primary key "id" and cursor field "updated_at". It fills in the source object from the name when one is not supplied, then returns a StreamSpec object that the connector can use later to know what to request and how to track progress.

**Call relations**: This helper is used while building the file’s DEEL_STREAMS list. It hands each completed StreamSpec to the connector class through that list, so later sync logic can treat all Deel streams in the same structured way.

*Call graph*: 1 external calls (__init__).


##### `DeelConnector._initial_params`  (lines 58–62)

```
def _initial_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the first set of query parameters for a Deel API request. It always asks for up to 100 records, and when possible it adds an incremental-sync filter so Deel only returns records updated after the saved cursor.

**Data flow**: It takes a stream description and an optional cursor value from a previous sync. It starts with a limit of 100 records; if the cursor exists and the stream supports an update field, it adds an "updated_after" parameter. It returns this parameter dictionary for use in HTTP requests.

**Call relations**: DeelConnector.paginate calls this before it starts requesting pages. The result becomes the base request settings that paginate copies and extends with the current offset for each page.

*Call graph*: called by 1 (paginate).


##### `DeelConnector._extract_records`  (lines 65–72)

```
def _extract_records(data: Any) -> list[dict[str, Any]]
```

**Purpose**: Pulls the actual record objects out of Deel’s API response. It accepts Deel’s usual response shape, where records are inside a "data" list, and also tolerates a plain list response.

**Data flow**: It receives raw response data from an API call. If the data is a dictionary with a list under "data", it keeps only the list items that are dictionaries; if the data itself is a list, it does the same filtering. It returns a clean list of record dictionaries, or an empty list when nothing usable is found.

**Call relations**: DeelConnector.paginate calls this after every HTTP request. Its output decides what gets yielded to the sync pipeline and also tells pagination when there are no more records to fetch.

*Call graph*: called by 1 (paginate).


##### `DeelConnector.paginate`  (lines 74–98)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Deel stream page by page and yields batches of records to the rest of the sync system. It also turns Deel permission failures into a controlled stream skip, which prevents one missing API scope from stopping everything.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It builds the REST path for that stream, creates base parameters, then repeatedly requests pages using limit and offset. After each response, it extracts records and yields them as a batch. It stops when a page is empty or shorter than 100 records. If Deel returns HTTP 401 or 403, it raises StreamSkipped with an explanation; other HTTP errors are passed upward unchanged.

**Call relations**: This is the connector’s main read loop for Deel streams. It calls _initial_params to prepare request filters, uses the inherited REST request method to fetch data, calls _extract_records to clean each response, and hands each batch onward to the broader source-sync machinery. When Deel refuses access, it creates a StreamSkipped error so the surrounding sync process can record the skipped stream and continue appropriately.

*Call graph*: calls 3 internal fn (__init__, _extract_records, _initial_params).


### `extensions/sources/ufo_ext_sources/providers/rippling.py`

`io_transport` · `source sync pagination`

Rippling is an external HR and workforce system, and its API sends large lists in pages instead of all at once. This file defines a read-only connector for that API. Without it, the project would not know which Rippling resources are available, how to ask for only recently changed workers and teams, or how to keep following Rippling’s “next page” links until a stream is complete.

The file first describes three streams: companies, workers, and teams. A stream is a named feed of records. Workers and teams have an update time field, so they can be synced incrementally, meaning “only fetch things changed after the last saved point.” Companies do not have that cursor, so they are refreshed from the beginning.

The main class, RipplingConnector, supplies the API base address, the stream list, and a checkpoint function that stores progress as text. Its pagination method is the heart of the file. It builds the first request, asks the API for a page, pulls records out of the response no matter whether Rippling used a named field or a generic data field, yields the records, then follows the next link. Think of it like reading a book by following “continued on page…” notes until there are no more. If Rippling rejects the request with an authorization error, the stream is skipped with a clear message instead of crashing as an unknown failure.

#### Function details

##### `RipplingConnector._next_path`  (lines 54–65)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper turns Rippling’s next-page link into a path the connector can request next. It accepts both full web addresses and shorter relative paths, because APIs may return either form.

**Data flow**: It receives a next-link string, or nothing. If there is no link, it returns nothing, which means pagination is finished. If the link is a full URL, it uses URL parsing to keep only the path and query part; if it is already a relative path, it returns it as-is.

**Call relations**: During pagination, RipplingConnector.paginate calls this after each page is read. The result becomes the next request path, so this function is what lets the connector move from one page of Rippling results to the next.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RipplingConnector._initial_query`  (lines 68–72)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query settings for the first API request in a stream. It always asks for a fixed page size, and for incremental streams it can also ask Rippling for records updated after a saved cursor.

**Data flow**: It receives the stream description and the last saved cursor value, if one exists. It creates a small dictionary of request parameters with a limit of 100 records. If the stream supports an update cursor and a cursor was provided, it adds updatedAfter with that cursor value. The finished parameter dictionary is returned to the caller.

**Call relations**: RipplingConnector.paginate calls this before making the first request for a stream. After that first request, pagination follows Rippling’s own next links, so these initial parameters are not reused.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector._extract_records`  (lines 75–85)

```
def _extract_records(data: Any, stream: StreamSpec) -> list[dict[str, Any]]
```

**Purpose**: This helper finds the actual list of records inside a Rippling API response. It protects the rest of the connector from small differences in response shape, such as records appearing under workers, teams, companies, or a generic data field.

**Data flow**: It receives the decoded response data and the stream description. If the response is a dictionary, it first looks for a list under the stream’s name, then under data. If the whole response is already a list, it uses that. In every case, it keeps only items that are dictionaries, because those are record-like objects, and returns the cleaned list.

**Call relations**: RipplingConnector.paginate calls this after each API response arrives. The records it returns are the batches that paginate yields onward to the source-sync machinery.

*Call graph*: called by 1 (paginate).


##### `RipplingConnector.paginate`  (lines 87–106)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for a Rippling stream. It requests pages from Rippling, yields each non-empty batch of records, and stops when Rippling no longer provides a next-page link.

**Data flow**: It receives an asynchronous HTTP client, a stream description, and an optional cursor from a previous sync. It starts with the stream’s API path and builds the first query parameters. For each page, it fetches data from Rippling, clears the initial parameters so later pages use the next link as given, extracts valid records, yields them if any exist, and computes the next path. If Rippling returns 401 or 403, meaning unauthorized or forbidden, it raises StreamSkipped with a helpful explanation; other HTTP errors are allowed to continue upward as real failures.

**Call relations**: The wider source system calls this when it wants to sync one Rippling stream. Inside its loop, it relies on _initial_query to prepare the first request, _extract_records to turn each response into usable rows, and _next_path to continue through pagination. When authorization is refused, it hands control back by raising StreamSkipped so the runner can skip that stream cleanly.

*Call graph*: calls 4 internal fn (__init__, _extract_records, _initial_query, _next_path).


### Scheduling connector
Connector that syncs Calendly users, event types, groups, memberships, scheduled events, and invitees.

### `extensions/sources/ufo_ext_sources/providers/calendly.py`

`io_transport` · `during Calendly source sync`

This file is the Calendly “source connector”: the part of the project that knows how to fetch Calendly data safely and consistently. Without it, the system would not know which Calendly API endpoints to call, how to page through long result lists, how to resume from a previous sync point, or how to turn Calendly’s nested responses into simple searchable records.

The connector starts by asking Calendly who the current API user is. That user record contains the current organization, and most Calendly collections must be requested inside that organization. Think of the organization as the building, and the connector must first find the building address before it can visit each room.

For each stream, such as event types or scheduled events, the connector builds the right API request, follows Calendly’s pagination tokens, and yields batches of records. Some streams use a cursor, which is a saved “last seen” value, so future syncs only ask for newer or relevant items instead of starting from scratch.

Invitees are a special case: Calendly exposes them underneath each scheduled event, so the connector first lists events, extracts each event’s ID from its URI, then asks for that event’s invitees. If Calendly refuses access with a 401 or 403 status, the stream is skipped rather than crashing the whole sync, because that usually means the connected account lacks permission for that data.

#### Function details

##### `_uuid_from_uri`  (lines 64–67)

```
def _uuid_from_uri(uri: Any) -> str | None
```

**Purpose**: This small helper pulls the final ID-like part out of a Calendly URI. It is used when the connector needs the scheduled event’s UUID so it can call Calendly’s invitees endpoint for that event.

**Data flow**: It receives any value that might be a URI. If the value is a non-empty string, it removes any trailing slash and returns the text after the final slash. If the input is missing or not a string, it returns nothing.

**Call relations**: The invitee sync calls this while walking through scheduled events. If it cannot extract an event UUID, that event is skipped for invitee lookup because the connector cannot build the child API URL.

*Call graph*: called by 1 (_invitees).


##### `CalendlyConnector._current_user`  (lines 76–79)

```
async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]
```

**Purpose**: This asks Calendly for the authenticated API user. The connector needs this user mainly to discover the account’s current organization, which is required for most other Calendly lists.

**Data flow**: It receives an HTTP client that is already prepared to talk to Calendly. It requests `/users/me`, looks inside the response for the `resource` object, and returns that object if it is a dictionary. If the response is not shaped as expected, it returns an empty dictionary.

**Call relations**: The organization-based stream helper calls this before fetching organization data. The main pagination function also calls it directly for the `api_user` stream, where the current user itself is the record being synced.

*Call graph*: called by 2 (_org_stream, paginate).


##### `CalendlyConnector._paginate_collection`  (lines 81–94)

```
async def _paginate_collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared paging helper for Calendly list endpoints. It hides the repeated work of asking for one page, reading Calendly’s next-page token, and continuing until there are no more pages.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It calls the connector’s lower-level cursor paging method using Calendly’s response shape: records live under `collection`, and the next token lives under `pagination.next_page_token`. It yields one list of records at a time, up to 100 records per page.

**Call relations**: Organization streams use this for top-level Calendly collections such as groups and events. The invitee sync also uses it for each event’s invitee list, so all collection-style Calendly API calls share the same paging behavior.

*Call graph*: called by 2 (_invitees, _org_stream).


##### `CalendlyConnector._org_stream`  (lines 96–112)

```
async def _org_stream(self, client: httpx.AsyncClient, path: str, *, cursor: str | None=None, cursor_param: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches a Calendly collection that belongs to the current organization. It adds the required organization parameter and, when needed, adds a cursor parameter so the sync can resume from a saved point.

**Data flow**: It receives an HTTP client, an API path, and optional cursor information. First it gets the current user, then reads `current_organization` from that user. If there is no usable organization, it skips the stream. Otherwise it requests pages from the given path with `organization=<organization URI>` and adds that organization value as context to every record batch it yields.

**Call relations**: The main pagination function uses this for event types, groups, memberships, and scheduled events. The invitee sync uses it first to find scheduled events before fetching invitees. It hands actual page fetching to `_paginate_collection` and adds context through `with_context`.

*Call graph*: calls 3 internal fn (__init__, _current_user, _paginate_collection); called by 2 (_invitees, paginate); 1 external calls (with_context).


##### `CalendlyConnector._invitees`  (lines 114–132)

```
async def _invitees(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches invitees for scheduled Calendly events. Calendly does not provide invitees as one simple organization-wide list, so this function first finds events and then asks for invitees under each event.

**Data flow**: It receives an HTTP client and an optional saved cursor. It reads scheduled events through `_org_stream`, extracts each event UUID from the event URI, then pages through `/scheduled_events/{uuid}/invitees`. If a cursor is present, it keeps only invitees whose `created_at` value is newer than that cursor. It yields non-empty batches, adding the parent scheduled event URI and UUID as context.

**Call relations**: The main pagination function calls this only for the `event_invitees` stream. It relies on `_org_stream` for the parent events, `_uuid_from_uri` to build child URLs, `_paginate_collection` to read invitee pages, and `with_context` to keep each invitee tied back to its event.

*Call graph*: calls 3 internal fn (_org_stream, _paginate_collection, _uuid_from_uri); called by 1 (paginate); 1 external calls (with_context).


##### `CalendlyConnector.paginate`  (lines 134–174)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher that decides how to fetch each Calendly stream. Given a stream definition, it chooses the right endpoint and cursor behavior, then yields batches of raw Calendly records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. For `api_user`, it returns the current user. For organization-based streams, it calls `_org_stream` with the correct Calendly path and, when appropriate, the correct cursor parameter such as `updated_since` or `min_start_time`. For invitees, it calls `_invitees`. If the stream is unknown, it skips it. If Calendly returns 401 or 403, it turns that refusal into a skipped stream instead of letting the whole sync fail.

**Call relations**: The broader source-sync framework calls this when it needs records for a particular Calendly stream. This function then routes the work to `_current_user`, `_org_stream`, or `_invitees`, depending on the stream name, and reports permission problems as `StreamSkipped` so the sync can continue with other streams.

*Call graph*: calls 4 internal fn (__init__, _current_user, _invitees, _org_stream).


##### `CalendlyConnector.flatten`  (lines 176–216)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes Calendly records into simpler records with important fields promoted to predictable names. It makes the downstream storage and search system less dependent on Calendly’s sometimes nested response format.

**Data flow**: It receives one raw record and the stream it came from. For each known stream, it copies the original fields and adds or normalizes useful fields such as `name`, `email`, `title`, `start_at`, `end_at`, `api_url`, or a plain location value. For organization memberships, it pulls the member’s name and email out of the nested `user` object and then removes that nested user object. Unknown streams are returned unchanged.

**Call relations**: After `paginate` yields raw records, the source framework can call this to prepare each record for storage. It uses `dict_or_empty` when reading nested membership user data so missing or malformed user data does not break the flattening step.

*Call graph*: 1 external calls (dict_or_empty).


### Incident and engineering operations connectors
Connectors that sync operational records for incidents, on-call schedules, services, software errors, releases, and engineering activity.

### `extensions/sources/ufo_ext_sources/providers/pagerduty.py`

`io_transport` · `source sync run`

PagerDuty is an incident-response service, and its API returns information in separate lists called streams: users, teams, services, incidents, notes, and more. This file defines a PagerDuty connector, which is the adapter between that API and this project’s generic source-sync machinery. Without it, the system would not know which PagerDuty endpoints exist, how to page through long lists, how to continue from the last synced item, or how to treat permission problems.

The file first describes the available streams using StreamSpec objects. Each one says the public stream name, where records live in PagerDuty’s JSON response, and which field uniquely identifies a record. Incidents and incident notes also name time fields used as cursors, meaning bookmarks that let later runs fetch only newer changes.

The PagerDutyConnector then supplies the PagerDuty-specific rules. It creates an HTTP client with PagerDuty’s required Accept header. It reads normal list endpoints using PagerDuty’s offset-and-limit paging style, where each response says whether more pages exist. Incidents get special treatment: they are sorted by update time and can ask PagerDuty for records since the saved cursor. Incident notes are also special because they are not one big list; the connector must first read incidents, then ask for notes under each incident, like checking each folder in a filing cabinet.

If PagerDuty refuses access with a 401 or 403 status, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 76–79)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function builds the HTTP client used to talk to PagerDuty and adds the exact API version header PagerDuty expects. Someone would use it indirectly whenever the connector starts making PagerDuty requests.

**Data flow**: It receives a base URL and a credential object supplied by the auth system. It asks the parent RestConnector to create the normal authenticated client, then adds PagerDuty’s versioned Accept header to that client. It returns the prepared client, ready to make requests to PagerDuty.

**Call relations**: This is part of the connector setup path inherited from RestConnector. Before any stream is read, the broader source runner needs a client; this method customizes that client so later calls such as pagination and note fetching speak PagerDuty’s expected API format.


##### `PagerDutyConnector._offset_pages`  (lines 81–101)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one PagerDuty list endpoint page by page. It is the shared helper for streams where PagerDuty returns records using offset and limit values, like turning pages in a book until PagerDuty says there are no more.

**Data flow**: It receives an HTTP client, a stream description, optional request parameters, and an optional cursor bookmark. It asks the base REST helper to fetch pages from the endpoint named by the stream, using PagerDuty’s `more` flag and returned `limit` value to know how to advance. If a cursor and cursor field are present, it drops records that are not newer than the cursor. It yields only non-empty batches of records.

**Call relations**: This is the common paging engine for this connector. PagerDutyConnector._incidents uses it with incident-specific sorting and since parameters, while PagerDutyConnector.paginate uses it directly for ordinary streams such as users, teams, services, schedules, and on-calls.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 103–115)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads PagerDuty incidents in update-time order so the system can resume cleanly from the last synced incident change. It is used when syncing the incidents stream and also as the starting point for finding incident notes.

**Data flow**: It receives an HTTP client and an optional cursor. It builds request parameters that tell PagerDuty to sort incidents by `updated_at` from oldest to newest. If a cursor exists, it adds it as PagerDuty’s `since` parameter, then passes the request to the shared offset paging helper. It yields batches of incident records that are newer than the cursor.

**Call relations**: PagerDutyConnector.paginate calls this when the requested stream is incidents. PagerDutyConnector._incident_notes also calls it to discover which incidents exist before asking PagerDuty for each incident’s notes. Internally, it hands the actual page fetching to PagerDutyConnector._offset_pages.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 117–130)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads notes attached to PagerDuty incidents. PagerDuty does not expose these as one simple global list here, so the connector first walks through incidents and then asks for the notes on each incident.

**Data flow**: It receives an HTTP client and an optional cursor for note creation time. It reads incidents without applying the note cursor, checks each incident for a usable string ID, then requests `/incidents/{id}/notes` for that incident. It extracts the `notes` list from the response, filters out notes older than or equal to the cursor when needed, adds the incident ID as extra context on each note, and yields non-empty batches.

**Call relations**: PagerDutyConnector.paginate calls this when the requested stream is incident_notes. This function depends on PagerDutyConnector._incidents to supply the incident IDs, uses records_at to pull the notes list out of PagerDuty’s response, and uses with_context to attach the parent incident ID so each note keeps its connection to the incident it came from.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 132–166)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main routing function for reading a PagerDuty stream. Given a stream name, it chooses the right fetching strategy and yields batches of records to the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. For incidents, it delegates to the incident-specific reader. For incident notes, it delegates to the note reader. For ordinary PagerDuty lists, it delegates to the offset paging helper. If the stream is unknown, it reports the stream as skipped. If PagerDuty returns 401 or 403, it converts that refusal into a skipped stream with a clear explanation; other HTTP errors are allowed to continue as real failures.

**Call relations**: The broader source runner calls this when it wants records for a particular PagerDuty stream. This function then fans out to PagerDutyConnector._incidents, PagerDutyConnector._incident_notes, or PagerDutyConnector._offset_pages depending on what kind of stream is being synced, and it uses StreamSkipped to tell the runner that a missing permission should be recorded as a skip rather than stopping the whole run.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/providers/sentry.py`

`io_transport` · `source sync run`

Sentry’s data is spread across several levels: organizations contain projects, projects contain issues and events, and organizations also have members and releases. This connector acts like a careful tour guide through that tree. It first knows what kinds of Sentry records are available, then asks the Sentry API for each kind in the right order.

The file only reads from Sentry; it never writes anything back. It relies on the wider runner to provide authentication, so no token is stored here. When Sentry refuses access with a 401 or 403 response, the connector treats that stream as skipped instead of crashing the whole sync. That matters because a user’s Sentry grant may allow some data but not all of it.

Sentry sends long result sets in pages. Instead of putting the next page number in the response body, it puts a cursor in the HTTP Link header. This file extracts that cursor and keeps asking for more pages until there are no more. For nested data, such as project issues, it also stamps each record with useful context like organization_slug and project_slug, so the record still makes sense after it leaves Sentry.

#### Function details

##### `_sentry_next_cursor`  (lines 77–82)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper looks at Sentry’s HTTP response headers and finds the cursor for the next page of results. A cursor is like a bookmark that tells Sentry where to continue reading.

**Data flow**: It receives HTTP headers from a Sentry response. It looks for a Link header, searches it for the specific marker that means “there is another page with results,” and returns the cursor text if it finds one. If there is no Link header or no usable next-page marker, it returns nothing.

**Call relations**: The page-reading loop in SentryConnector._paged_list calls this after every Sentry API request. If this helper returns a cursor, _paged_list asks Sentry for another page; if it returns nothing, the loop ends.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.record_ref`  (lines 91–95)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This chooses the human-friendly reference used for a synced Sentry record. For organizations, it prefers the organization slug, because that is more recognizable than an internal numeric ID.

**Data flow**: It receives one Sentry record and the stream description for that record. If the stream is organizations, it reads the slug field and returns it as text when possible. For all other streams, it falls back to the standard reference behavior provided by the base REST connector.

**Call relations**: The wider source system calls this when it needs a stable label or reference for a record. This connector only customizes the organization case and lets the shared connector logic handle everything else.


##### `SentryConnector.paginate`  (lines 97–109)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry point for reading one Sentry stream page by page. It also turns permission failures into a clean “skip this stream” signal.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It asks SentryConnector._stream_pages to produce batches of records and yields each batch onward. If Sentry replies with 401 or 403, meaning the credentials are invalid or lack permission, it raises StreamSkipped with an explanation; other HTTP errors are allowed to bubble up.

**Call relations**: The source runner calls this when it wants records for a Sentry stream. paginate delegates the stream-specific route choice to SentryConnector._stream_pages, then protects the larger sync from expected access-denied cases by converting them into skipped streams.

*Call graph*: calls 2 internal fn (__init__, _stream_pages).


##### `SentryConnector._stream_pages`  (lines 111–124)

```
def _stream_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This decides which internal reader should be used for a requested Sentry stream. It is the connector’s switchboard.

**Data flow**: It receives the HTTP client, the stream name, and an optional cursor. Based on the stream name, it returns the matching page generator: root-level organizations or projects, organization members, project issues, project events, or organization releases. If the name is not one this file implements, it raises StreamSkipped.

**Call relations**: SentryConnector.paginate calls this before records are read. _stream_pages then hands the work to the correct specialist method, such as SentryConnector._issues for issues or SentryConnector._members for members.

*Call graph*: calls 6 internal fn (__init__, _events, _issues, _members, _releases, _root_pages); called by 1 (paginate).


##### `SentryConnector._root_pages`  (lines 126–139)

```
async def _root_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the top-level Sentry streams that do not need a parent loop: organizations and projects. It also applies a simple date cursor filter for projects.

**Data flow**: It receives the HTTP client, either the organizations or projects stream name, and an optional cursor. It loads all organizations or all projects, filters projects to only those created after the cursor when a cursor is present, and yields the remaining records as one batch if there are any.

**Call relations**: SentryConnector._stream_pages uses this for the organizations and projects streams. It relies on SentryConnector._organizations and SentryConnector._projects to collect the actual API results.

*Call graph*: calls 2 internal fn (_organizations, _projects); called by 1 (_stream_pages).


##### `SentryConnector._paged_list`  (lines 141–158)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the reusable page reader for Sentry API endpoints. It keeps following Sentry’s next-page cursor until the endpoint has no more results.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It sends a request, reads the JSON response, keeps only list items that are dictionary-like records, and yields them as a page. Then it uses _sentry_next_cursor to find the next cursor in the response headers and repeats until there is no cursor.

**Call relations**: All the stream-specific readers use this as their low-level fetch loop. It hides the repeated work of making requests, reading JSON, and following pagination, so methods like SentryConnector._issues and SentryConnector._releases can focus on which endpoint to call.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 160–164)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This gathers all organizations visible to the Sentry credential. Organizations are the top level needed before reading organization-scoped data like members and releases.

**Data flow**: It receives the HTTP client. It asks SentryConnector._paged_list for every page from the organizations endpoint, appends all pages into one list, and returns that full list.

**Call relations**: SentryConnector._root_pages uses this for the organizations stream. SentryConnector._members and SentryConnector._releases also call it first so they know which organization slugs to use in their API paths.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, _root_pages).


##### `SentryConnector._projects`  (lines 166–170)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This gathers all projects visible to the Sentry credential. Projects are needed before the connector can read project-scoped issues and events.

**Data flow**: It receives the HTTP client. It asks SentryConnector._paged_list for every page from the projects endpoint, combines the pages into one list, and returns that list.

**Call relations**: SentryConnector._root_pages uses this for the projects stream. SentryConnector._issues and SentryConnector._events call it first so they can visit each project’s issue and event endpoints.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, _root_pages).


##### `SentryConnector._members`  (lines 172–178)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the members for every accessible Sentry organization. It adds the organization slug to each member record so the member can be traced back to its organization later.

**Data flow**: It receives the HTTP client. It first loads organizations, then for each organization with a valid slug, it reads the members endpoint page by page. Before yielding each page, it adds organization_slug context to every record in that page.

**Call relations**: SentryConnector._stream_pages calls this when the requested stream is members. This method uses SentryConnector._organizations to find where to look, SentryConnector._paged_list to fetch pages, and with_context to attach the parent organization information.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (_stream_pages); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 180–194)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads issues for every accessible Sentry project. When a cursor is provided, it asks Sentry for only issues seen after that point, which keeps later syncs from rereading old issue data.

**Data flow**: It receives the HTTP client and an optional cursor. It loads projects, extracts each project’s organization slug and project slug, skips projects missing either value, and builds the project issues API path. If there is a cursor, it adds a lastSeen filter to the request. It yields each page after adding organization_slug and project_slug to the records.

**Call relations**: SentryConnector._stream_pages calls this for the issues stream. This method depends on SentryConnector._projects to discover projects, SentryConnector._paged_list to read each project’s issue pages, and with_context to preserve where each issue came from.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (_stream_pages); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 196–210)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads event records for every accessible Sentry project. Events are filtered by timestamp when a cursor is available, so the connector can continue from the last saved point.

**Data flow**: It receives the HTTP client and an optional cursor. It loads projects, finds the organization and project slugs for each one, skips incomplete project records, and calls the project events endpoint. If a cursor exists, it adds an event.timestamp filter. It yields each page with organization_slug and project_slug added to every event.

**Call relations**: SentryConnector._stream_pages calls this for the events stream. It follows the same project-by-project pattern as SentryConnector._issues, using SentryConnector._projects for discovery, SentryConnector._paged_list for API paging, and with_context for parent project context.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (_stream_pages); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 212–223)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads releases for every accessible Sentry organization. It can filter out releases that are not newer than the saved cursor.

**Data flow**: It receives the HTTP client and an optional cursor. It loads organizations, skips any without a valid slug, and reads each organization’s releases endpoint page by page. If a cursor is present, it keeps only releases whose dateCreated value is greater than the cursor, then yields non-empty pages with organization_slug added.

**Call relations**: SentryConnector._stream_pages calls this for the releases stream. It uses SentryConnector._organizations to find organization slugs, SentryConnector._paged_list to fetch releases, and with_context to label each release with its organization.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (_stream_pages); 1 external calls (with_context).
