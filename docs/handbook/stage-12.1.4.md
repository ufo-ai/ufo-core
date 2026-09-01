# Project, Product, Developer, and Operations Connectors  `stage-12.1.4`

This stage is a set of behind-the-scenes connectors. Their job is to visit outside tools used by engineering, product, and operations teams, read their data through each tool’s API, and turn it into a common stream of records the rest of the system can store, search, and recall. An API is a structured doorway that software uses to ask another service for information.

The work-tracking connectors cover Asana, ClickUp, Jira, Linear, monday.com, and Wrike. They read things like projects, tasks, issues, comments, users, boards, teams, folders, goals, workflows, and custom fields. The knowledge and collaboration connectors read Confluence spaces and pages, Notion pages and databases, and GitHub repositories, issues, commits, users, and organizations. The operations connectors read PagerDuty incidents, services, schedules, and on-call data, plus Sentry projects, errors, events, members, and releases. Together, they act like adapters for different power outlets, making many tools feed one shared memory system.

## Files in this stage

### Project and Product Work Tracking
Connectors that read task, project, issue, board, team, comment, and workflow data from project-management and product-planning systems.

### `extensions/sources/ufo_ext_sources/providers/asana.py`

`io_transport` · `source sync`

Asana is a work-tracking service, and its API does not return everything at once. It sends records in pages, like a long photo album split across many envelopes. This file defines which Asana collections the system knows how to read, and how to keep asking Asana for the next envelope until there are no more.

The file first defines a small helper for creating stream descriptions. A stream is one kind of thing to read, such as tasks or projects. Each stream says what Asana object it maps to, which field uniquely identifies each record, and whether the stream can be synced incrementally. Incremental sync means “only ask for things changed since last time,” which Asana supports here for tasks and projects.

The main class, AsanaConnector, plugs into the shared REST connector framework. It names the provider, sets Asana’s API base address, and supplies the catalog of supported streams. Its key behavior is pagination: it calls Asana with a page size of 100, yields any records it finds, checks Asana’s `next_page.offset` token, and repeats with that token until Asana says there is no next page. This connector only reads data; it does not create or change anything in Asana.

#### Function details

##### `_stream`  (lines 24–38)

```
def _stream(name: str, *, cursor_field: str | None=None, updated_at_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a standard description of one Asana data stream, such as tasks or users. It keeps the stream list compact and consistent so every stream uses Asana’s `gid` field as its unique record key.

**Data flow**: It receives a stream name and optional details about date fields and whether the stream is especially important to recall. It packages those choices into a `StreamSpec`, which is the shared object the connector framework uses to know what to fetch and how to identify records.

**Call relations**: This function is used while the file defines `ASANA_STREAMS`, the catalog of Asana objects the connector can read. To do that, it hands the prepared values to `StreamSpec.__init__`, which creates the actual stream description consumed later by `AsanaConnector`.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 74–90)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This async function reads one Asana stream page by page. It is what lets the system fetch a large list, such as all tasks, without assuming Asana will return the full list in one response.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. It builds an Asana API path and starts with `limit=100`; if the stream is tasks or projects and a cursor exists, it asks Asana only for records modified since that cursor. For each response, it pulls the `data` list safely, yields records when present, then follows `next_page.offset` if Asana provides one. When there is no valid next offset, it stops.

**Call relations**: The shared REST connector framework calls this when it needs records for a particular Asana stream. Inside the loop, it relies on the inherited `_get` method to make the HTTP request, then uses `ufo.sdk.sources.list_or_empty` to turn Asana’s `data` value into a safe list before passing records back to the sync flow.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/providers/clickup.py`

`io_transport` · `source sync`

ClickUp organizes work like nested folders in a filing cabinet: teams contain spaces, spaces contain folders and lists, and lists contain tasks and other details. This file exists because the system cannot simply ask ClickUp for “everything” in one flat list. It has to walk down that hierarchy in the right order, remembering where each item came from.

The main class, ClickUpConnector, is a read-only connector. It uses ClickUp’s web API through authenticated HTTP requests and exposes several named streams, such as teams, tasks, and list comments. For parent-child data, it adds context like team_id, space_id, folder_id, or list_id so a task or comment can still be traced back to its place in ClickUp.

For large or changing data, it also supports incremental reading. For example, tasks are fetched page by page, and if the system already has a saved “cursor” value, only tasks updated after that value are returned. A cursor is like a bookmark that says, “next time, continue from here.”

The file also reshapes some records into a more consistent form. For example, user records get a standard name and created_at field, and comments get an author and body field. Without this connector, ClickUp data would not be available to the broader source-sync system.

#### Function details

##### `ClickUpConnector._teams`  (lines 58–60)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the top-level ClickUp teams available to the authenticated account. This is the starting point for most other ClickUp reads, because many other ClickUp objects live underneath a team.

**Data flow**: It receives an HTTP client that can make API calls. It asks ClickUp for the /team endpoint, pulls the teams list out of the response, and returns that list as plain record dictionaries.

**Call relations**: This is the first step used by several later reads. The spaces, users, and goals flows call it to find which teams to inspect, and the generic root-record helper calls it when the requested stream is teams.

*Call graph*: called by 4 (_goals, _root_records, _spaces, _users); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 62–70)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived spaces inside every team. A space is a major area of work inside ClickUp, so discovering spaces is needed before folders and some lists can be found.

**Data flow**: It starts with the HTTP client, calls _teams to get teams, then asks ClickUp for each team’s spaces. For every space it finds, it adds the parent team_id so the space keeps its context, and returns one combined list.

**Call relations**: This sits one level below teams in the hierarchy. Folder discovery and list discovery call it so they know which spaces to search, and the root-record helper uses it when syncing the spaces stream.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, _root_records); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 72–82)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived folders inside every ClickUp space. Folders are another layer in ClickUp’s hierarchy and often contain lists.

**Data flow**: It receives an HTTP client, calls _spaces to learn which spaces exist, then asks ClickUp for folders in each valid space. It attaches the parent space_id to each folder and returns the collected folder records.

**Call relations**: This is the bridge between spaces and folder-based lists. The list discovery flow calls it to find lists inside folders, and the root-record helper calls it for the folders stream.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, _root_records); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 84–100)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all non-archived lists, whether they live inside folders or directly inside spaces. Lists are especially important because tasks, comments, and custom fields are read from lists.

**Data flow**: It receives an HTTP client. First it gets folders and asks ClickUp for each folder’s lists, adding folder_id to those records. Then it also gets spaces and asks for lists that sit directly under each space, adding space_id to those records. It returns all found lists together.

**Call relations**: This is the launch point for leaf-level data. The task reader and list-child reader both call it because they need list IDs before they can fetch tasks, comments, or custom fields; the root-record helper calls it for the lists stream.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _root_records, _tasks); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 102–127)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads tasks from every discovered ClickUp list, including closed tasks and subtasks. It can skip older tasks by using a saved update cursor, which makes repeated syncs faster.

**Data flow**: It receives an HTTP client and an optional cursor. It calls _lists to find list IDs, then fetches tasks from each list one page at a time. It adds list_id and list_name to each task, filters out tasks whose date_updated is not newer than the cursor, and yields batches of tasks as they are found.

**Call relations**: The main paginate method calls this when the requested stream is tasks. It depends on _lists to know where to look and yields pages back to paginate so the source-sync system can process them incrementally.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 129–148)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads per-list side data, specifically list comments and list custom fields. These records do not have their own top-level ClickUp collection, so the connector must visit each list to retrieve them.

**Data flow**: It receives an HTTP client, the stream being requested, and an optional cursor. It calls _lists, chooses the correct ClickUp API path for either comments or fields, extracts the right records from the response, adds list_id and list_name, optionally filters by the stream’s cursor field, and yields non-empty batches.

**Call relations**: The main paginate method calls this for list_comments and list_custom_fields. It relies on _lists for the list IDs and hands batches back to paginate for the normal sync pipeline.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 150–176)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main dispatcher for reading any ClickUp stream. Given a stream name, it chooses the right helper and yields records in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching reader such as _root_records, _users, _tasks, _list_child_stream, or _goals, and yields any records those helpers produce. If the stream is unknown, it raises a StreamSkipped signal so the sync system knows this connector does not implement that stream.

**Call relations**: This is the central entry point used by the source-sync framework for ClickUp data. It fans out to the specialized helper methods depending on the requested stream and turns their results into the common paginated shape expected by the rest of the system.

*Call graph*: calls 6 internal fn (__init__, _goals, _list_child_stream, _root_records, _tasks, _users).


##### `ClickUpConnector._root_records`  (lines 178–189)

```
async def _root_records(self, client: httpx.AsyncClient, name: str) -> list[dict[str, Any]]
```

**Purpose**: Provides a small routing helper for the simple hierarchy streams: teams, spaces, folders, and lists. It keeps the main paginate method from repeating the same branching logic.

**Data flow**: It receives an HTTP client and a stream name. It matches that name to the correct hierarchy reader and returns the resulting list of records. If the name is not one of the supported root collections, it raises an error.

**Call relations**: The paginate method calls this for teams, spaces, folders, and lists. This helper then delegates to _teams, _spaces, _folders, or _lists, depending on which collection is being synced.

*Call graph*: calls 4 internal fn (_folders, _lists, _spaces, _teams); called by 1 (paginate).


##### `ClickUpConnector._users`  (lines 191–200)

```
async def _users(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]
```

**Purpose**: Builds a user list from the member information embedded inside teams. ClickUp does not expose users here as the same kind of simple top-level stream, so this function gathers them from team membership data.

**Data flow**: It receives an HTTP client and calls _teams. For each team, it looks through the team’s members, extracts the nested user object when present, adds the team_id, and stores the user by ID so duplicates collapse into one record per user ID. It returns a dictionary of users keyed by their IDs.

**Call relations**: The paginate method calls this when syncing the users stream. It depends on _teams because team records carry the membership data this connector uses to discover users.

*Call graph*: calls 1 internal fn (_teams); called by 1 (paginate).


##### `ClickUpConnector._goals`  (lines 202–210)

```
async def _goals(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches ClickUp goals for each team. Goals are treated as an extra, non-canonical stream, meaning they are collected but are not one of the main hierarchy objects.

**Data flow**: It receives an HTTP client, calls _teams to find team IDs, then asks ClickUp for each team’s goals. It extracts the goals list from each response, adds the parent team_id, and returns all goal records together.

**Call relations**: The paginate method calls this when the requested stream is goals. It uses _teams as its starting point because ClickUp goals are requested under a team.

*Call graph*: calls 1 internal fn (_teams); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 212–246)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes selected ClickUp records into fields the wider system expects, such as name, created_at, status, body, or author. This makes different ClickUp stream records easier to display, search, and compare.

**Data flow**: It receives one raw record and its stream description. Depending on the stream, it copies the original record and adds or rewrites a few common fields: users get name and created_at, spaces/folders/lists get an API URL, tasks get status and created_at, and comments get body, author, created_at, and parent_external_id. For streams without special rules, it returns the record unchanged.

**Call relations**: This function is part of the connector’s output-shaping step after records have been fetched. It uses a small helper to safely treat a comment’s user value as a dictionary, then returns the cleaned-up record to the broader source system.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/jira.py`

`io_transport` · `source sync runs`

Jira data lives behind Atlassian’s cloud API, and one login token may grant access to several Jira sites. This file is the connector that discovers those sites, asks each one for its Jira data, and feeds that data back in tidy batches. Without it, this system would not know where a user’s Jira sites are, how Jira splits long result lists into pages, or how to turn Jira’s nested issue data into useful readable content.

The main class, JiraConnector, is a read-only bridge. First it asks Atlassian which cloud sites the current grant can reach. Then, depending on the stream being synced, it calls the right Jira endpoint. Most Jira lists use offset pagination, meaning the connector asks for “items 0-99,” then “100-199,” and so on until Jira says there are no more. Issues, comments, and sprints can be synced incrementally by using a saved “cursor,” which is a remembered timestamp so later runs only fetch newer changes.

The connector also adds context such as the cloud site ID and site URL to each record. That context is like writing the building address on every page pulled from an office file cabinet: later code can tell where the record came from. If Jira refuses access with an authorization error, the connector marks that stream as skipped rather than crashing the whole sync. Finally, it renders issues and comments into human-readable text instead of storing only raw JSON.

#### Function details

##### `JiraConnector.paginate`  (lines 73–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the front door for reading one Jira stream. Given a stream name such as issues or users, it chooses the matching reader and yields batches of records back to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor timestamp. It routes the request to the stream-specific method, passes the cursor where needed, and yields each page of records it gets back. If Jira says access is forbidden or unauthorized, it turns that into a controlled skip instead of a hard failure.

**Call relations**: The source framework calls this when it wants records for a Jira stream. paginate then delegates to _projects, _issues, _comments, _users, _boards, or _sprints. If the stream name is unknown or Jira refuses access, it raises StreamSkipped so the wider run can record a skip and continue safely.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 106–110)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Jira Cloud sites the current login grant can reach. Each site has a cloud ID, which Jira requires in almost every later API path.

**Data flow**: It uses the HTTP client to call Atlassian’s accessible-resources endpoint. It reads the JSON response, makes sure the result is a list, and returns an empty list if there is no usable content. The output is a list of site records, each potentially containing an ID and URL.

**Call relations**: _projects, _issues, _users, and _boards call this before contacting Jira site-specific endpoints. It is the connector’s map of which Jira sites to visit during a sync.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 112–132)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira list endpoints that are split into numbered pages. It hides the repeated “ask for the next page” work from the individual stream readers.

**Data flow**: It receives an API path, optional query parameters, and the name of the JSON field where the records live. It repeatedly sends requests with startAt and maxResults values, extracts the records from each response, yields non-empty pages, and stops when Jira says it is on the last page or when no more records are available.

**Call relations**: _projects, _issues, _comments, _boards, and _sprints rely on this helper whenever they read a paginated Jira collection. It calls records_at to safely pull the list of records out of Jira’s response envelope.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 134–141)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira projects from every accessible Jira Cloud site. Projects are the containers that issues belong to.

**Data flow**: It first gets the list of accessible sites. For each site with a valid cloud ID, it calls Jira’s project search endpoint through _offset_values. Each page of projects is returned with extra context added, including the cloud ID and site URL.

**Call relations**: paginate calls this when the projects stream is being synced. It depends on _sites to discover where to look, _offset_values to walk through Jira’s pages, and with_context to label each project with its source site.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 143–155)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira issues, optionally only those updated after the last sync cursor. Issues are the main work items people create, assign, discuss, and close in Jira.

**Data flow**: It builds a Jira Query Language string, which is Jira’s search syntax, ordering issues by update time and filtering past the cursor if one was supplied. It then visits each accessible site, requests issue pages with selected fields, and yields each page after adding site context.

**Call relations**: paginate calls this for the issues stream. _comments also calls it without a cursor so it can find issues whose comments should be checked. The method uses _sites for site discovery, _offset_values for Jira’s paged search results, and with_context to keep origin information attached.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 157–177)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads comments attached to Jira issues. Because comments are reached through an issue, it first finds issues and then asks Jira for each issue’s comments.

**Data flow**: It reads issues across accessible sites, looks at each issue’s ID and cloud ID, and skips any issue missing those basics. For each valid issue it pages through the issue’s comment endpoint. If a cursor is present, it keeps only comments updated after that timestamp, then yields the remaining comments with site and issue context added.

**Call relations**: paginate calls this for the issue_comments stream. This method uses _issues to find the parent issues, _offset_values to read each comment list, and with_context to attach details such as the issue ID and issue key.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 179–190)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira users from every accessible site. Unlike many Jira endpoints in this file, this user search is treated as a single returned JSON list.

**Data flow**: It gets accessible sites, skips any site without a usable cloud ID, and calls the users/search endpoint for each site. It converts the response into a list if possible, and yields the users only when the list is non-empty, adding the cloud ID and site URL to each record.

**Call relations**: paginate calls this when syncing the users stream. It uses _sites to know which sites to ask, list_or_empty to safely interpret Jira’s response as a list, and with_context to preserve where each user record came from.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 192–199)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira Agile boards from each accessible site. Boards are views used by teams to organize issues, often for Scrum or Kanban workflows.

**Data flow**: It discovers accessible sites, builds the Agile board endpoint for each valid cloud ID, and pages through the results. Each page of boards is yielded with site context attached.

**Call relations**: paginate calls this for the boards stream. _sprints also calls it because sprints are listed under boards. It uses _sites, _offset_values, and with_context in the same pattern as other site-wide streams.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 201–215)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads sprints from Jira Agile boards, optionally keeping only sprints updated after a saved cursor. A sprint is a planned time period of work, commonly used by Scrum teams.

**Data flow**: It first reads boards, then for each board with a usable ID and cloud ID it asks Jira for that board’s sprints. If a cursor exists, it filters out sprints whose updatedDate is not newer. It yields non-empty sprint pages with the cloud ID and board ID added.

**Call relations**: paginate calls this for the sprints stream. It depends on _boards to find where sprints live, then uses _offset_values to walk through each board’s sprint pages and with_context to keep board information attached.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 217–224)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This adjusts issue records so the sync system can find the issue update timestamp in a simple top-level place. Other streams already have their important fields at the top level, so they are left unchanged.

**Data flow**: It receives one record and its stream description. For issue records, it safely reads the nested fields object and copies fields.updated into a top-level updated value. It returns the modified record for issues, or the original record for all other stream types.

**Call relations**: The connector framework uses this after records are fetched and before cursor tracking. It calls _dict_or_empty so a missing or malformed fields value does not break the sync.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 226–252)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Jira records into readable page text. It gives issues and comments useful titles and bodies instead of exposing only deeply nested Jira JSON.

**Data flow**: It receives a record and its stream description. For issues, it pulls out the summary, status, priority, assignee, reporter, and description text, then builds a simple markdown-like page. For comments, it uses the author as the title and converts the comment body into plain text. Other streams are handed back to the parent connector’s normal rendering behavior.

**Call relations**: The source framework calls this when it needs human-readable content for stored records. render uses _dict_or_empty, _str, _person, _field_line, and _doc_text to safely extract text from Jira’s nested structures.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 255–256)

```
def _str(value: Any) -> str
```

**Purpose**: This small helper returns a value only if it is actually a string. It prevents accidental non-text values from showing up in titles or rendered pages.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string. Nothing else is changed.

**Call relations**: render uses this when reading issue fields, and _person uses it when choosing a display name or email address. It is a guardrail for messy API data.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 259–260)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This helper safely treats a value as a dictionary only when it really is one. It avoids crashes when Jira omits a nested object or returns something unexpected.

**Data flow**: It receives any value. If the value is a dictionary-like JSON object, it returns it; otherwise it returns an empty dictionary. Callers can then ask for keys without first checking the type.

**Call relations**: flatten, render, and _person call this before reading nested Jira fields. It keeps those higher-level functions focused on their job instead of repeating defensive checks.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 263–265)

```
def _person(value: Any) -> str
```

**Purpose**: This extracts a human-friendly name from a Jira user object. It prefers the display name and falls back to the email address if needed.

**Data flow**: It receives any value that might be a Jira person object. It safely turns that into a dictionary, reads displayName and emailAddress as strings, and returns the first non-empty choice. If neither exists, it returns an empty string.

**Call relations**: render calls this when showing an issue’s assignee, reporter, or a comment’s author. It uses _dict_or_empty and _str to cope with missing or oddly shaped person data.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 268–269)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: This formats one labeled metadata line, such as “Status: Done,” but only when there is a value worth showing. It keeps rendered issue pages from containing empty labels.

**Data flow**: It receives a label and a text value. If the value is non-empty, it returns the label and value joined with a colon; otherwise it returns an empty string. It does not change any outside data.

**Call relations**: render calls this while building the issue metadata block. The empty-string behavior lets render easily skip missing fields when it joins lines together.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 272–289)

```
def _doc_text(value: Any) -> str
```

**Purpose**: This converts Jira’s rich text format into plain readable text. Jira descriptions and comments can arrive as Atlassian Document Format trees, which are nested JSON structures rather than simple strings.

**Data flow**: It receives any value that might be a document tree. It walks through dictionaries and lists, collects every text field it finds, joins those text pieces with newlines, and returns the cleaned-up result. If the input is not a usable document tree, the result is empty text.

**Call relations**: render calls this for issue descriptions and comment bodies. Inside, it uses the nested walk function to visit every branch of the document tree, like checking every folder inside a folder for notes.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 277–286)

```
def walk(node: Any) -> None
```

**Purpose**: This is the recursive worker inside _doc_text. Its job is to visit every nested part of a Jira document and collect plain text leaves.

**Data flow**: It receives one node from the document tree. If the node is a dictionary, it saves its text field when present and then visits each child in its content list. If the node is a list, it visits each item. It adds found text into the surrounding chunks list and returns nothing directly.

**Call relations**: _doc_text starts this walker on the original document value. The walker repeatedly calls itself for child nodes so _doc_text can turn a whole nested document into one flat string.


### `extensions/sources/ufo_ext_sources/providers/linear.py`

`io_transport` · `source sync`

Linear is a work-tracking tool, and this connector is the bridge between Linear and this project’s source-sync framework. Without it, the system would not know what Linear objects exist, how to ask Linear for them, how to fetch more than one page of results, or how to turn important records into readable text.

Linear only exposes this data through GraphQL, which is an API style where the caller sends a query describing exactly which fields it wants. This file defines the set of Linear streams the system can sync, such as issues, projects, comments, users, cycles, labels, and statuses. For each stream it stores the matching GraphQL query and the response field where the returned records live.

The main worker is `LinearConnector.paginate`. It asks Linear for one page at a time, follows Linear’s `pageInfo.endCursor` marker to get the next page, and stops when Linear says there are no more pages. For streams that support change tracking, it also sends an `updatedAt` filter so later syncs only request records changed since the last run. Some Linear collections cannot be filtered this way, so they are fully reread each time.

The connector also gives special treatment to records that should become human-readable pages. Issues, projects, comments, and users are rendered into clear text with useful labels instead of a raw data dump.

#### Function details

##### `_stream`  (lines 35–45)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: This small helper creates a stream description for one kind of Linear object, such as issues or projects. It keeps the repeated setup for Linear streams in one place so the stream list is easier to read and less error-prone.

**Data flow**: It receives a stream name, an optional cursor field, and a flag saying whether the stream is a main content stream. It uses those values, plus Linear’s standard `createdAt` and `updatedAt` fields, to build a `StreamSpec` object. The result is a compact description the sync framework can use to know what to sync and how to track progress.

**Call relations**: This helper is used while the file defines `LINEAR_STREAMS`. Each call produces one stream entry, and the resulting list is attached to `LinearConnector` so the wider source framework knows which Linear collections are available.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 265–308)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches records from Linear one page at a time. Someone uses it during a sync run when they need all records for a stream, or only the records changed since a saved cursor.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It looks up the GraphQL query for that stream, builds variables such as `after` for pagination and `updatedAt` filtering for incremental syncs, sends requests to Linear, checks for refusal or GraphQL errors, extracts the `nodes` list from the response, and yields each non-empty page of records. It advances through pages using Linear’s `endCursor` until there is no next page, the response shape is not usable, or Linear gives no valid cursor.

**Call relations**: The source framework calls this when syncing a Linear stream. Inside, it relies on the base connector’s POST helper to actually send the GraphQL request, uses `list_or_empty` to safely normalize the returned records, and raises `StreamSkipped` when Linear rejects access with an authorization-style response so the run can record the stream as skipped instead of treating it like normal data.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 310–352)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns selected Linear records into readable page text. It exists so important content, like an issue or project, reads like a useful note rather than a raw API object full of nested fields.

**Data flow**: It receives one Linear record and the stream it came from. For issues, projects, comments, and users, it pulls out human-friendly fields such as title, description, state, assignee, lead, email, or comment body. It builds a title and a page body, falling back to the first body line or the parent renderer if the record has no obvious title. It returns the final title and formatted text. For other streams, it hands the record back to the base class renderer.

**Call relations**: The sync framework calls this when converting fetched Linear records into pages. It uses `_str` to safely read string fields, `_ref_id` to pull IDs out of small nested reference objects, and `_labeled` to format metadata lines. If the stream is not one of the specially formatted types, it delegates to the inherited default rendering behavior.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 355–356)

```
def _str(value: Any) -> str
```

**Purpose**: This helper safely turns a value into a string only when it already is one. It prevents accidental display of non-string values where the renderer expects plain text.

**Data flow**: It receives any value. If the value is a string, it returns that string unchanged; otherwise it returns an empty string. It does not modify anything.

**Call relations**: `LinearConnector.render` uses this throughout when reading optional fields from Linear records. `_ref_id` also uses it after extracting an `id`, so nested references get the same safe treatment.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 359–360)

```
def _ref_id(value: Any) -> str
```

**Purpose**: This helper extracts an `id` from a small nested Linear reference, such as an assignee or project lead. It is used when the page should mention the related object without expanding the whole nested object.

**Data flow**: It receives any value. If the value is a dictionary-like object, it reads its `id` field and passes that through `_str`; otherwise it returns an empty string. The output is either a safe ID string or nothing.

**Call relations**: `LinearConnector.render` calls this while building readable metadata for issues and projects. `_ref_id` depends on `_str` so that even if the nested `id` is missing or not text, the rendered page stays clean.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 363–364)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This helper formats a set of label-and-value pairs into simple text lines, such as `state: started` or `email: person@example.com`. It keeps the rendered pages tidy by skipping blank values.

**Data flow**: It receives a list of pairs, where each pair has a label and a string value. It keeps only pairs with a non-empty value, turns each one into `label: value`, and joins those lines with newline characters. The result is a short block of readable metadata.

**Call relations**: `LinearConnector.render` uses this when building the metadata sections for issues, projects, and users. It does not call other project-specific helpers; it simply formats the cleaned values that the renderer has already prepared.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/monday.py`

`io_transport` · `source sync`

monday.com exposes its data through GraphQL, which is an API style where the caller sends a query describing exactly what fields it wants. This file is the read-only connector for that API. Without it, the system would not know how to ask monday for pages of data, how to follow monday’s different paging styles, or how to shape the results into useful pages.

The connector defines the monday streams first: each stream is a kind of thing to sync, such as boards or items, with an id field and sometimes a time field used for incremental syncing. Incremental syncing means “only keep records newer than the last saved timestamp.” monday does not filter by that timestamp on the server, so this file fetches pages and filters them afterward.

The main helper, `_graphql`, sends one GraphQL request and rejects error responses cleanly. Most top-level monday objects use simple page numbers, handled by `_paged_root`. Items are trickier: the connector first lists boards, then asks each board for its items, following monday’s item cursor like a bookmark to the next page. Activity logs also fan out board by board.

Finally, `flatten` normalizes different monday shapes into common fields like name, body, author, created_at, and parent id, so later parts of the source system can treat them consistently.

#### Function details

##### `_extract_person_ids`  (lines 70–98)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: This helper finds the people assigned to a monday item. monday hides assignees inside board-specific column data, so this function looks for columns marked as people columns and pulls out stable person ids.

**Data flow**: It receives the raw `column_values` from an item. It ignores anything that is not a list, skips non-people columns, parses the people column value if it is stored as JSON text, and collects entries whose kind is `person`. It returns a simple list of person id strings and does not change anything outside itself.

**Call relations**: When `MondayConnector._items` fetches item records, it calls this helper for each item. The helper hands back `assignee_ids`, which `_items` adds to the item before yielding it to the rest of the sync.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 106–118)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This is the connector’s single doorway to the monday GraphQL API. It sends a query, unwraps the useful `data` part of the response, and turns monday GraphQL errors into a controlled stream skip.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional variables. It posts them to monday’s API root, checks whether monday returned an `errors` list, and raises `StreamSkipped` if the query was refused or unavailable. If the response has a data object, it returns that object; otherwise it returns an empty dictionary.

**Call relations**: `_paged_root`, `_items`, `_activity_logs`, and `_single_root` all use this method whenever they need to talk to monday. By centralizing the request and error handling here, the rest of the connector can focus on which monday objects to fetch.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, _single_root).


##### `MondayConnector._paged_root`  (lines 120–143)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads monday collections that use ordinary page numbers, such as users, workspaces, boards, and updates. It keeps asking for page 1, page 2, and so on until monday stops returning records.

**Data flow**: It receives the API client, the monday field to query, the fields to select, and optionally a saved cursor value with the record field to compare against it. For each page, it asks `_graphql` for up to 100 records, safely treats missing or malformed data as an empty list, optionally removes records at or before the saved cursor, and yields each non-empty page. It stops when there are no records left after filtering.

**Call relations**: `_boards` uses this helper to collect all boards. `_stream_pages` also uses it directly for streams that fit monday’s page-number pattern. `_paged_root` delegates the actual network request to `_graphql`.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, _stream_pages); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 145–156)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper fetches all boards with the board details needed by other streams. It exists because item and activity-log syncing both start by knowing which boards to inspect.

**Data flow**: It receives the API client. It asks `_paged_root` for every page of boards, including workspace information and timestamps, appends those pages into one list, and returns the full list of board dictionaries.

**Call relations**: `_items` and `_activity_logs` call this first so they can loop over boards one by one. `_boards` itself relies on `_paged_root` for the repeated monday requests.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 158–217)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads monday items from every board. It also adds a plain `assignee_ids` field, because monday stores assignments inside nested column data that is hard for later code to use directly.

**Data flow**: It receives the API client and an optional saved update-time cursor. First it fetches all boards through `_boards`. For each board, it requests that board’s first item page, then follows monday’s item-page cursor to request later pages. For every item, it extracts assignee ids from `column_values`, filters out old items when a cursor is present, and yields pages of item records.

**Call relations**: `_stream_pages` calls this when the requested stream is `items`. Inside, it calls `_boards` to know where to look, `_graphql` to fetch each item page, `_extract_person_ids` to simplify assignee data, and the safe dictionary/list helpers to avoid crashing on missing API fields.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (_stream_pages); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 219–247)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads recent activity log entries from each monday board. Activity logs describe changes and events, so they are tied back to the board they came from.

**Data flow**: It receives the API client and an optional saved creation-time cursor. It fetches all boards, asks monday for up to 100 activity logs for each board, adds the board id onto each log record, filters out records that are not newer than the cursor, and yields any remaining records in pages.

**Call relations**: `_stream_pages` calls this when syncing `activity_logs`. It depends on `_boards` to find boards and `_graphql` to fetch each board’s logs, then passes shaped log records back to the general pagination flow.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (_stream_pages); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 249–265)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the public paging method the source framework uses to read one monday stream. It wraps the stream-specific readers and translates permission failures into a clear skipped-stream result.

**Data flow**: It receives the HTTP client, the stream description, and the saved cursor. It asks `_stream_pages` for pages and yields them unchanged. If monday responds with HTTP 401 or 403, meaning unauthorized or forbidden, it raises `StreamSkipped` with a message explaining that the token or permission scope is not enough; other HTTP errors are allowed to bubble up normally.

**Call relations**: The source framework calls `paginate` during a sync. `paginate` then asks `_stream_pages` to choose the correct stream reader, while adding the outer safety net for monday permission refusals.

*Call graph*: calls 2 internal fn (__init__, _stream_pages).


##### `MondayConnector._stream_pages`  (lines 267–305)

```
def _stream_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function is the router for monday streams. Given a stream name, it chooses the right fetching strategy for that kind of monday data.

**Data flow**: It receives the API client, a stream name, and an optional cursor. For simple page-number streams, it returns `_paged_root` configured with the right GraphQL field and selected fields. For teams and tags, it returns `_single_root`. For items and activity logs, it returns their special fan-out readers. If the name is unknown, it raises `StreamSkipped` instead of pretending it can sync the stream.

**Call relations**: `paginate` calls this once it knows which stream is being synced. `_stream_pages` hands the work off to `_paged_root`, `_single_root`, `_items`, or `_activity_logs`, depending on the data shape monday uses for that stream.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _items, _paged_root, _single_root); called by 1 (paginate).


##### `MondayConnector._single_root`  (lines 307–318)

```
async def _single_root(self, client: httpx.AsyncClient, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches small monday collections that are returned in one GraphQL response rather than through repeated pages. In this file, that means teams and tags.

**Data flow**: It receives the API client and the requested name, either `teams` or `tags`. It builds the matching GraphQL query, sends it through `_graphql`, reads the list with that name from the returned data, and yields it if it contains records.

**Call relations**: `_stream_pages` calls this for the `teams` and `tags` streams. `_single_root` relies on `_graphql` for the actual monday request and then returns one page to the normal sync pipeline.

*Call graph*: calls 1 internal fn (_graphql); called by 1 (_stream_pages).


##### `MondayConnector.flatten`  (lines 320–361)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes monday records into common fields the rest of the source system expects. It keeps the original record data but adds or standardizes fields such as `body`, `author`, `status`, and `parent_external_id` where useful.

**Data flow**: It receives one monday record and its stream description. Depending on the stream, it copies the record and fills normalized fields from monday-specific fields: users keep name and email, boards and workspaces get an API URL, items get status, updates get body and author, and activity logs get subject, body, author, and parent board id. For streams without special rules, it returns the record unchanged.

**Call relations**: After pages have been fetched through `paginate` and its stream readers, the source framework can call `flatten` to prepare each record for storage or indexing. It uses the safe dictionary helper when reading an update creator, because monday may omit or reshape that nested value.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/wrike.py`

`io_transport` · `during source sync`

Wrike exposes its data through a web API, but the rest of this project needs a steady stream of ordinary records it can store, search, and recall later. This file is the adapter between those two worlds. It defines which Wrike collections can be read, how to move through Wrike’s paged responses, and how to rename or enrich fields so records look more consistent inside the system.

Wrike sends lists inside a wrapper that includes a page of data and, sometimes, a token for the next page. The connector follows those tokens until there is nothing left. For streams that track updates, such as tasks or folders, Wrike does not provide a dependable “only send changes since this time” option. So the connector reads pages and filters out records whose updated date is not newer than the saved cursor, like sorting mail after it has already arrived rather than asking the post office to pre-filter it.

If Wrike refuses access with an authentication or permission error, the connector does not crash the whole sync. It raises a skip signal so that stream can be ignored with a clear explanation. The file only reads from Wrike; it does not create or update anything there.

#### Function details

##### `_profile_email`  (lines 54–64)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper looks inside a Wrike contact record and finds the first usable email address from its profile list. It exists because Wrike stores email addresses in nested profile objects rather than as one simple top-level field.

**Data flow**: It receives one Wrike record as a dictionary. It checks whether the record has a profiles list, walks through each profile that is itself a dictionary, and looks for a non-empty email string. It returns that email address if it finds one, or returns nothing if the record does not contain a usable email.

**Call relations**: When `WrikeConnector.flatten` is preparing a contact for the rest of the system, it calls `_profile_email` to pull the contact’s email out of Wrike’s nested shape. The helper gives back a simple value that can be placed directly on the flattened contact record.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 72–96)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Wrike stream page by page and yields batches of records to the sync system. It also skips unsupported streams and turns permission failures into a clear “skip this stream” signal instead of letting the whole run fail.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor value. First it checks whether this stream is one this connector knows how to read. Then it asks Wrike for pages of data, follows Wrike’s next-page token, and optionally removes records whose cursor field is not newer than the saved cursor. It yields only non-empty batches. If Wrike responds with a refusal such as missing permission or bad credentials, it raises `StreamSkipped`; other HTTP errors are allowed to continue upward as real failures.

**Call relations**: The source syncing machinery calls this method when it needs records for a Wrike stream. Inside the flow, `paginate` relies on the shared REST connector paging behavior to fetch Wrike’s cursor-based pages. If a stream is not implemented or Wrike refuses access, it hands back a `StreamSkipped` signal so the broader sync runner can continue sensibly.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 98–136)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method reshapes raw Wrike records into simpler records with common fields such as name, status, body, author, created date, and parent task. That makes different Wrike objects easier for the rest of the system to store and search consistently.

**Data flow**: It receives one raw Wrike record and the stream it came from. For contacts, it builds a display name, extracts an email with `_profile_email`, and maps the created date. For folders, it uses the title as the name and adds a direct API URL. For tasks, it uses the title, chooses a status, reads due-date information from the nested dates object using `dict_or_empty` to avoid errors, and maps the created date. For comments, it maps text to body, author ID to author, and task ID to the parent record link. Streams without special rules are returned unchanged.

**Call relations**: After `paginate` has supplied raw Wrike records, the connector’s normal source pipeline can call `flatten` before records are saved or indexed. `flatten` delegates contact email extraction to `_profile_email` and uses `dict_or_empty` when reading task dates so a missing or malformed dates field does not break the transformation.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).


### Knowledge and Repository Collaboration
Connectors that read documentation, workspace content, repository activity, and collaborative knowledge from Confluence, GitHub, and Notion.

### `extensions/sources/ufo_ext_sources/providers/confluence.py`

`io_transport` · `source sync`

Confluence pages are not stored as simple text. Their bodies come back from the API as storage-format XHTML, which is HTML-like markup full of tags, macros, and structure. If the system saved that raw markup, a person searching or recalling the synced content would see noisy code instead of the page they expected. This file solves that by fetching Confluence records and turning the important parts into plain, useful fields and readable prose.

The connector first asks Atlassian which Confluence sites the current OAuth grant can reach. A grant is the permission given by a user or organization; one grant can cover more than one site. For each site, it calls the right Confluence API path for each stream, such as pages or comments. Results are read in pages of 50 items at a time. For streams that can be updated over time, it compares each record’s timestamp with the saved cursor, which is like a bookmark saying “we already synced up to here.”

Each record is then flattened into common fields such as title, body, URL, author, and created time. Record IDs are prefixed with the Confluence site ID so two different sites cannot accidentally produce the same ID. For readable output, page bodies and space descriptions are passed through a small HTML text extractor that keeps visible text, adds line breaks around block elements, and drops tags and attributes. If Confluence refuses access because the grant lacks permission, the stream is skipped cleanly instead of failing the whole run.

#### Function details

##### `_body_text`  (lines 114–116)

```
def _body_text(record: Mapping[str, Any]) -> str | None
```

**Purpose**: This helper finds the readable body content inside a Confluence record. It looks for the storage-format body first, then falls back to the view-format body, because either may contain the text that should be rendered later.

**Data flow**: It receives one Confluence record as a dictionary-like object. It reads nested fields for body text, checks that the value is a non-empty string, and returns that string. If no usable body text is present, it returns nothing.

**Call relations**: It is used during ConfluenceConnector.flatten when page-like records are being shaped into the system’s standard fields. It relies on get_path to safely read nested data without assuming every field exists.

*Call graph*: called by 1 (flatten); 1 external calls (get_path).


##### `ConfluenceConnector.paginate`  (lines 124–150)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Confluence streams. It decides which API endpoint belongs to the requested stream, visits every accessible Confluence site, and yields batches of records for the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. It finds the API path for the stream, asks which sites are accessible, then reads batches from each site. Before yielding each batch, it adds site context such as the cloud ID and site URL. If Confluence returns a permission-related error, it turns that into a clean stream skip.

**Call relations**: The wider source framework calls this when it needs records from a Confluence stream. It calls _sites to discover which Confluence sites to read, _offset_results to walk through each site’s paged API results, and with_context to attach site information to each returned record. If a stream is not implemented or permission is refused, it raises StreamSkipped so the run records a skip rather than treating it as a crash.

*Call graph*: calls 3 internal fn (__init__, _offset_results, _sites); 1 external calls (with_context).


##### `ConfluenceConnector._sites`  (lines 152–156)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Confluence sites the current OAuth permission grant can access. The connector needs this because Confluence API calls are scoped to a specific cloud site ID.

**Data flow**: It receives an HTTP client, calls Atlassian’s accessible-resources endpoint, reads the JSON response, and returns it as a list of site dictionaries. If the response is empty or not already list-like, it safely returns an empty list.

**Call relations**: ConfluenceConnector.paginate calls this before reading any stream data. The returned site IDs become part of the API paths used by _offset_results, and the site information is later attached to records so IDs and URLs can be interpreted correctly.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `ConfluenceConnector._offset_results`  (lines 158–182)

```
async def _offset_results(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: This walks through a Confluence collection that is split into pages by start position and page size. It also applies local cursor filtering for incremental streams, because Confluence does not offer a simple server-side “only changed since this time” option here.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, an optional cursor value, and the name of the cursor field. It repeatedly requests results with a start offset and limit, extracts the records from the response, removes records older than or equal to the cursor when needed, and yields any remaining records. It stops when there are no records to yield or Confluence does not provide a next-page link.

**Call relations**: ConfluenceConnector.paginate uses this after it has chosen a site-specific API path. This function uses records_at to pull the list of records out of the API response and get_path to read nested cursor fields and the next-page marker.

*Call graph*: called by 1 (paginate); 2 external calls (get_path, records_at).


##### `ConfluenceConnector.flatten`  (lines 184–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This reshapes raw Confluence API records into the common shape expected by the rest of the source system. It gives records useful fields like title, body, URL, author, creation time, and stable external ID.

**Data flow**: It receives one raw record and the stream it belongs to. Depending on the stream, it copies the original data and adds or normalizes fields such as body text, kind, URL, parent ID, and timestamps. It prefixes most primary keys with the Confluence cloud ID so records from different sites do not collide. For nested cursor fields such as version.createdAt, it also lifts the cursor value into the flattened record.

**Call relations**: The source framework calls this after records are fetched and before they are stored or rendered. It calls _body_text to extract body content from page-like records and get_path to safely read nested values such as links, version timestamps, authors, and descriptions.

*Call graph*: calls 1 internal fn (_body_text); 1 external calls (get_path).


##### `ConfluenceConnector.render`  (lines 234–250)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a flattened Confluence record into a human-readable title and text body. It exists because the default rendering would not be good enough for Confluence’s XHTML-style page content.

**Data flow**: It receives a record and its stream description. For pages, blog posts, and comments, it gets a title and converts the body markup into plain text. For spaces, it uses the space name or key and extracts text from the description. For other streams, it falls back to the parent connector’s normal rendering. It returns a pair: the chosen title and a readable text block with a heading.

**Call relations**: The source system calls this when it needs recallable text from a synced record. It uses _str to safely turn possible title values into strings, get_path to read nested description fields, and _StorageTextExtractor.extract to remove Confluence markup while keeping readable words.

*Call graph*: calls 1 internal fn (_str); 1 external calls (get_path).


##### `_StorageTextExtractor.__init__`  (lines 259–261)

```
def __init__(self) -> None
```

**Purpose**: This sets up a fresh text extractor for one piece of Confluence storage-format XHTML. It prepares a place to collect the readable text fragments found while parsing.

**Data flow**: It receives no outside data except the new object being created. It initializes the HTML parser with automatic character-reference conversion, then creates an empty list for collected text parts. The result is a parser ready to be fed raw markup.

**Call relations**: _StorageTextExtractor.extract creates an instance of this class when it needs to convert raw Confluence markup into plain text. The parser’s later callback methods add text and line breaks into the list prepared here.


##### `_StorageTextExtractor.extract`  (lines 264–269)

```
def extract(cls, raw: Any) -> str
```

**Purpose**: This is the simple public doorway for converting Confluence XHTML into readable plain text. Callers can pass any value, and it safely returns an empty string when the value is not usable text.

**Data flow**: It receives a raw value that might be a markup string. If the value is not a non-empty string, it returns an empty string. Otherwise it creates a parser, feeds the markup into it, asks the parser to clean up the collected text, and returns the final plain-text result.

**Call relations**: ConfluenceConnector.render uses this when preparing pages, blog posts, comments, and space descriptions for recall. Inside, it depends on the parser callbacks handle_data, handle_starttag, and handle_endtag being invoked as the HTML parser reads the markup, then finishes through _text.


##### `_StorageTextExtractor.handle_data`  (lines 271–272)

```
def handle_data(self, data: str) -> None
```

**Purpose**: This receives visible text found inside the Confluence markup and keeps it. It ignores tags themselves and preserves only the character data that a reader would actually see.

**Data flow**: The HTML parser passes in a text fragment. The function appends that fragment to the extractor’s internal list. It does not return a separate value; it changes the extractor’s collected text.

**Call relations**: This is called automatically by Python’s HTMLParser while _StorageTextExtractor.extract feeds it markup. The saved fragments are later joined and cleaned by _StorageTextExtractor._text.


##### `_StorageTextExtractor.handle_starttag`  (lines 274–276)

```
def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None
```

**Purpose**: This adds a line break when the parser enters a block-style tag, such as a paragraph, heading, table cell, or list item. That helps the final plain text keep a readable shape instead of becoming one long run-on sentence.

**Data flow**: The HTML parser passes in a tag name and its attributes. The function checks whether the tag is one of the known block tags. If so, it appends a newline marker to the internal text parts; otherwise it ignores the tag.

**Call relations**: This is called automatically during _StorageTextExtractor.extract as markup is parsed. The newline markers it adds are later normalized by _StorageTextExtractor._text into clean line breaks.


##### `_StorageTextExtractor.handle_endtag`  (lines 278–280)

```
def handle_endtag(self, tag: str) -> None
```

**Purpose**: This adds a line break when the parser leaves a block-style tag. It gives the extracted text natural separation around paragraphs, headings, table rows, and similar page structure.

**Data flow**: The HTML parser passes in the closing tag name. The function checks whether that tag is a known block tag and, if it is, appends a newline marker to the collected parts. It returns nothing directly.

**Call relations**: This works alongside handle_starttag while _StorageTextExtractor.extract parses Confluence markup. The extra separators are cleaned up later by _StorageTextExtractor._text so the output has readable spacing without excessive blank lines.


##### `_StorageTextExtractor._text`  (lines 282–285)

```
def _text(self) -> str
```

**Purpose**: This turns the extractor’s collected fragments and newline markers into final clean text. It trims extra spaces, removes empty lines, and produces the readable body used by rendering.

**Data flow**: It reads the extractor’s internal list of text pieces, joins them, splits them by newline markers, collapses repeated whitespace within each line, removes blank lines, and returns the cleaned string. It does not read the original markup directly; it works from what the parser already collected.

**Call relations**: _StorageTextExtractor.extract calls this after the HTML parser has finished reading the raw markup. It is the final cleanup step that makes the parser callbacks’ raw collected pieces suitable for display and recall.


##### `_str`  (lines 288–289)

```
def _str(value: Any) -> str
```

**Purpose**: This small safety helper returns a value only if it is already a string. It prevents titles or names from accidentally becoming confusing text like numbers, dictionaries, or null values.

**Data flow**: It receives any value. If the value is a string, it returns that same string. Otherwise it returns an empty string.

**Call relations**: ConfluenceConnector.render calls this when choosing titles from records. That lets render safely fall back to body text or default rendering when Confluence did not provide a usable string title.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/providers/github.py`

`io_transport` · `source sync`

This connector is the bridge between GitHub and UFO’s source-sync system. GitHub data is spread across many API endpoints, and many records are only unique inside one repository. For example, many repositories can have a branch called `main`. This file prevents those records from overwriting each other by stamping each record with the organization or repository it came from, like putting each document into a clearly labeled folder before filing it.

The connector first defines the GitHub streams it knows about, such as repositories, issues, commits, releases, users, teams, and workflows. Only streams with a known GitHub API path are actually runnable. It builds an authenticated HTTP client with GitHub’s required headers, discovers organizations through `/user/orgs`, then discovers repositories through each organization’s repo list. Repository-based streams are then run once per repository.

Pagination is a major part of the file. GitHub returns large lists page by page, using a `Link` header to point to the next page. Some streams can resume from a timestamp, while newest-first streams such as commits and events need careful windowing so new records arriving during a sync do not cause older records to be skipped. If a repository or organization cannot be read, the connector skips just that part when safe; if organization discovery itself is forbidden, it skips the whole stream instead of treating it as a system failure.

#### Function details

##### `_stream`  (lines 74–94)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', ordering: Ordering=Ordering.none, canonical:
```

**Purpose**: Creates a `StreamSpec`, which is the system’s description of one kind of GitHub data to sync. It keeps the stream list compact by filling in common defaults such as the primary key and creation timestamp field.

**Data flow**: It receives a stream name plus optional details like the GitHub object name, primary key, cursor field, ordering style, and backfill window. It packages those choices into a `StreamSpec` object. The result is later used by the connector to decide how to fetch, resume, identify, and render records from that stream.

**Call relations**: This helper is used while building the module’s `ALL_STREAMS` catalog. It hands the finished stream descriptions to the rest of the connector, which later filters them through known API paths and uses them during pagination and record shaping.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 198–201)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the GitHub streams that this connector can actually run today. Some streams are listed for catalog compatibility, but only streams with a wired API path are exposed here.

**Data flow**: It reads the connector’s full stream list and the `_PATHS` table. It keeps only stream specs whose names appear in `_PATHS`. The output is a list of runnable stream definitions.

**Call relations**: The broader source-sync framework asks the connector what streams it supports. This method acts as the gatekeeper, so adding a path to `_PATHS` automatically promotes a catalogued stream into an active one.


##### `GitHubConnector._make_client`  (lines 203–207)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to GitHub and adds GitHub-specific request headers. These headers tell GitHub which API format and API version the connector expects.

**Data flow**: It receives a base URL and a resolved credential. It first lets the parent `RestConnector` create the authenticated client, then adds the `Accept` and `X-GitHub-Api-Version` headers. It returns the ready-to-use asynchronous HTTP client.

**Call relations**: This is part of connector setup. The base connector supplies the general authenticated client, and this method adds the GitHub-specific rules before any pagination or data fetching begins.


##### `GitHubConnector.flatten`  (lines 209–258)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Prepares one GitHub record for storage and makes its identity safe across repositories or organizations. This is what stops two repos’ `main` branches, tags, or commit IDs from being treated as the same page.

**Data flow**: It receives a raw GitHub record and its stream description. It may reshape special cases: stargazers are merged with their nested user object, and pull requests have large nested repository objects removed from `head` and `base`. It then finds whether the stream is repo-scoped or org-scoped, reads the record’s primary key, and prefixes that key with the repo or org label. The output is the shaped record, usually with a partition-qualified primary key.

**Call relations**: This runs before the adapter reads the primary key for page references and titles. It uses `_partition_field` to decide which partition label should exist and `get_path` when the key is nested. If the fan-out stamp is missing, it raises an error because the record cannot be filed safely.

*Call graph*: calls 1 internal fn (_partition_field); 1 external calls (get_path).


##### `GitHubConnector.record_identity`  (lines 260–267)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Computes the stable identity for a record, with a special rule for contributor activity. Contributor activity uses a nested author ID, and anonymous contributor rows may not have one.

**Data flow**: It receives a record and stream description. For normal streams it delegates to the parent connector’s identity logic. For `contributor_activity`, it reads the repository partition and the nested `author.id`; if both exist, it combines them into one identity string. If either is missing, it returns no identity.

**Call relations**: The sync system uses this identity to recognize records. This method mirrors the partition-scoping idea used in `flatten`, but it exists because contributor activity declares its key inside `author.id` and can have rows without a reliable author.

*Call graph*: 1 external calls (get_path).


##### `GitHubConnector.render`  (lines 269–274)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Controls what becomes page content for a record. For pull requests, it keeps the update timestamp as metadata rather than repeating it in the rendered body.

**Data flow**: It receives a record and stream description. For most streams it passes the record straight to the parent renderer. For pull requests, it removes the update cursor field from the content before rendering, then returns the title and body produced by the parent renderer.

**Call relations**: The source framework calls this when turning fetched records into pages. It only customizes pull requests; every other stream follows the standard rendering path.


##### `GitHubConnector.paginate_source`  (lines 276–287)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] |
```

**Purpose**: Provides the framework-facing pagination entry point for GitHub streams. It adds support for a pinned backfill floor, which is used when walking newest-first repository streams.

**Data flow**: It receives the HTTP client, stream, current cursor, the current user ID, and an optional backfill cutoff time. It ignores the user ID here and forwards the rest to `paginate`. The output is an async stream of pages or structured stream pages.

**Call relations**: The source-sync framework calls this as the public pagination seam. It immediately hands the work to `GitHubConnector.paginate`, adding the extra backfill information that repository window walks need.

*Call graph*: calls 1 internal fn (paginate).


##### `GitHubConnector.paginate`  (lines 289–323)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, backfill_after: datetime | None=None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the right fetching strategy for a stream. GitHub has organization-wide streams, repository-scoped streams, and simple direct streams, and this method routes each one to the right walker.

**Data flow**: It receives a client, stream, cursor, and optional backfill cutoff. It looks up the stream’s API path, prepares common query parameters such as `per_page`, and adds `state=all` for issues and pull requests. It then yields pages from repository discovery, per-repo walking, per-org walking, or direct link-header pagination depending on the path.

**Call relations**: This is called by `paginate_source`. It delegates to `_repository_pages`, `_repo_stream_pages`, `_org_stream_pages`, or `_paginate_link_header`, so the rest of the connector can keep each kind of fan-out separate.

*Call graph*: calls 4 internal fn (_org_stream_pages, _paginate_link_header, _repo_stream_pages, _repository_pages); called by 1 (paginate_source).


##### `GitHubConnector._repository_pages`  (lines 325–329)

```
async def _repository_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches repository records from all granted organizations and labels them with the organization they came from. This powers the `repositories` stream itself.

**Data flow**: It asks `_iter_granted_org_repo_pages` for pages of repositories grouped by organization. For each page, it adds an `org_login` field to every record using `with_context`. It yields those labeled repository pages.

**Call relations**: The main `paginate` method uses this for the `repositories` stream. It relies on the same organization and repository discovery flow that later repo-scoped streams use.

*Call graph*: calls 1 internal fn (_iter_granted_org_repo_pages); called by 1 (paginate); 1 external calls (with_context).


##### `GitHubConnector._repo_partitions`  (lines 331–333)

```
async def _repo_partitions(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Produces the list of repository partitions that repository-scoped streams should walk. A partition is one repository, written as `owner/repo`.

**Data flow**: It reads repository identities from `_iter_user_repos`. For each `(owner, repo)` pair, it turns the pair into a single `owner/repo` string and yields it.

**Call relations**: This is supplied to `PartitionWalk` by `_repo_stream_pages`. `PartitionWalk` then asks for one partition at a time so each repo can have its own cursor and resume state.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector._repo_stream_pages`  (lines 335–359)

```
async def _repo_stream_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, cursor: str | None, backfill_after: datetime | None) -> AsyncIterator[StreamPage]
```

**Purpose**: Runs a repository-scoped stream across all discovered repositories. It uses `PartitionWalk`, a helper that tracks progress separately for each repository, so a pause or failure can resume cleanly.

**Data flow**: It receives the client, stream, API path, cursor, and optional backfill cutoff. It formats the cutoff as a GitHub-style UTC timestamp if present. It creates a `PartitionWalk` with the repository list and a page-fetching function for one repo, then yields each page the walk produces. When done, it closes the async generator if needed.

**Call relations**: The main `paginate` method calls this when a stream path contains both `{owner}` and `{repo}`. It wires `_repo_partitions` together with `_repo_pages`, letting `PartitionWalk` coordinate the full multi-repository walk.

*Call graph*: called by 1 (paginate); 3 external calls (__init__, astimezone, partial).


##### `GitHubConnector._org_stream_pages`  (lines 361–379)

```
async def _org_stream_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, params: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs an organization-scoped stream once for each organization the credential can see. For the `users` stream, it also upgrades simple member records with richer public user details.

**Data flow**: It receives the client, stream, path template, and query parameters. It loops through organization logins from `_iter_user_orgs`, fills the org into the path, paginates that endpoint, optionally enriches user records, stamps every record with `org_login`, and yields the page. If one organization returns a not-found or gone response, it skips that organization and continues.

**Call relations**: The main `paginate` method calls this for paths containing `{org}`. It uses `_paginate_link_header` for GitHub pagination and `_enrich_users` when member records need extra public profile fields.

*Call graph*: calls 3 internal fn (_enrich_users, _iter_user_orgs, _paginate_link_header); called by 1 (paginate); 2 external calls (Semaphore, with_context).


##### `GitHubConnector._repo_pages`  (lines 381–452)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Fetches bounded pages for one repository and reports the timestamp range on each page. This is the per-repository worker that lets the sync resume safely and avoid losing records when GitHub feeds change during a run.

**Data flow**: It receives one repository key, the stream, the API path, and a `PartitionBound` describing where to start or stop. It builds GitHub query parameters for ascending streams, newest-first streams, and stateful issue-like streams. It paginates the endpoint, removes pull requests from the issues stream, applies client-side time filtering for feeds that lack server-side filters, stamps records with `repo_full_name`, calculates high and low cursor values, and yields a `WalkPage`. If the repository is unavailable with a known skip status, it raises `PartitionSkipped` instead of failing the whole run.

**Call relations**: This function is handed to `PartitionWalk` by `_repo_stream_pages`. It uses `_paginate_link_header` to read GitHub pages, `_cursor_bounds` to tell the walk how far the page spans, and `with_context` so later `flatten` can build repo-scoped identities.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); 3 external calls (__init__, __init__, with_context).


##### `GitHubConnector._iter_user_repos`  (lines 454–462)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: Discovers repositories that belong to organizations the credential can access. It intentionally avoids `/user/repos` because that endpoint can include personal, collaborator, archived, and forked repositories outside the intended organization grant.

**Data flow**: It receives the client and loops through organization repository pages from `_iter_granted_org_repo_pages`. For each repository record, it asks `_repo_identity` to find a reliable `(owner, repo)` pair. It yields only records whose identity can be understood.

**Call relations**: `_repo_partitions` calls this to build the repository partition list for `PartitionWalk`. It depends on `_iter_granted_org_repo_pages` for scoped discovery and `_repo_identity` for parsing each repository record.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (_repo_partitions).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 464–483)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: Fetches pages of usable repositories for each granted organization. It filters out archived repositories and forks, because the connector only wants active, organization-owned source repositories.

**Data flow**: It receives the client and loops through organization logins from `_iter_user_orgs`. For each org, it paginates `/orgs/{org}/repos` with repository-list parameters, removes records marked archived or forked, and yields the org plus any remaining repository page. If a particular org is forbidden, missing, or gone, it skips that org.

**Call relations**: Both `_repository_pages` and `_iter_user_repos` use this discovery routine. It sits between root organization discovery and all repository-based fan-out.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, _repository_pages).


##### `GitHubConnector._iter_user_orgs`  (lines 485–506)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Lists the organization logins visible to the GitHub credential. Since almost every runnable stream fans out from organizations, this is the root discovery step.

**Data flow**: It paginates `/user/orgs` and reads the `login` field from each organization record. Valid non-empty logins are yielded. If GitHub refuses this root request with a permission error, it raises `StreamSkipped` so the sync records a skipped stream rather than a broken run.

**Call relations**: `_iter_granted_org_repo_pages` uses this before repository discovery, and `_org_stream_pages` uses it for organization-scoped streams. A refusal here stops the stream because there is no safe organization or repository list to walk.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, _org_stream_pages).


##### `GitHubConnector._enrich_users`  (lines 508–528)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: Turns GitHub organization member records into richer public user records when possible. Organization member endpoints often return only basic fields, while `/users/{login}` can include public name and email.

**Data flow**: It receives a page of member records, the client, and a semaphore, which is a small traffic light that limits how many user-detail requests run at once. It starts one enrichment task per member, waits for all of them, and returns a new list containing detailed user records where available and original records where not.

**Call relations**: `_org_stream_pages` calls this only for the `users` stream. It coordinates many calls to the nested `one` helper and uses `asyncio.gather` to wait for all enrichments in the page.

*Call graph*: called by 1 (_org_stream_pages); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 514–526)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Fetches the public GitHub profile for one organization member. If the member has no usable login or the profile is missing, it keeps the original member record.

**Data flow**: It receives one member record from the surrounding `_enrich_users` function. It reads the `login`, waits for permission from the semaphore, requests `/users/{login}`, and returns the response body if it is a dictionary. A 404 response falls back to the original member record; other HTTP errors are allowed to stop the process.

**Call relations**: This helper is created and used inside `_enrich_users`. `_enrich_users` runs many of these helpers concurrently but limits the number in flight so the connector does not flood GitHub with profile requests.


##### `GitHubConnector._paginate_link_header`  (lines 530–538)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads GitHub list endpoints page by page using GitHub’s `Link` header. This hides the repeated work of following the “next page” pointer.

**Data flow**: It receives the client, a path, and optional query parameters. It asks the base REST connector for link-header pages using a fixed page size and `_parse_records` as the JSON parser. It yields each parsed list of records; empty responses simply produce no records.

**Call relations**: This is the shared low-level pagination helper used by direct streams, organization discovery, organization streams, repository discovery, and per-repository pages. Higher-level methods decide what path to call; this method performs the repeated page-walking pattern.

*Call graph*: called by 5 (_iter_granted_org_repo_pages, _iter_user_orgs, _org_stream_pages, _repo_pages, paginate).


##### `_partition_field`  (lines 541–549)

```
def _partition_field(path: str) -> str | None
```

**Purpose**: Figures out which context label a stream’s records need based on the API path. Repository paths need `repo_full_name`, organization paths need `org_login`, and unscoped paths need no extra label.

**Data flow**: It receives an API path template. If the template contains `{repo}`, it returns the repository partition field name. If it contains `{org}`, it returns the organization partition field name. Otherwise it returns nothing.

**Call relations**: `flatten` calls this before scoping a primary key. It is the small rule that connects the path fan-out shape to the record identity shape.

*Call graph*: called by 1 (flatten).


##### `_parse_records`  (lines 552–556)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Extracts a list of records from a GitHub HTTP response. GitHub list endpoints should return JSON arrays, and this function ignores anything else.

**Data flow**: It receives an HTTP response. If the response body is empty, it returns an empty list. Otherwise it parses JSON and returns it only if it is a list; non-list JSON becomes an empty list.

**Call relations**: `_paginate_link_header` passes this parser into the base link-header pagination helper. That keeps all GitHub list parsing consistent across streams.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 559–574)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: Finds the `(owner, repo)` identity in a GitHub repository record. It supports several shapes because GitHub records may include `full_name`, an `owner` object, or only a repo name plus a known organization.

**Data flow**: It receives a repository record and an optional fallback owner. It first tries to split `full_name` like `owner/repo`. If that is not available, it looks for `owner.login` and `name`. If only `name` exists and a fallback owner was provided, it uses that. If none of these are reliable, it returns nothing.

**Call relations**: `_iter_user_repos` calls this while turning repository records into partition identities. The result becomes the `owner/repo` keys that drive repository-scoped syncs.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 577–587)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: Finds the newest and oldest cursor values present on a page. These bounds let the repository walk know how far a page reached in time.

**Data flow**: It receives a page of records and the cursor field name, which may be a dotted path such as `commit.committer.date`. If there is no cursor field, it returns no bounds. Otherwise it reads string cursor values from the page, returns the maximum as the high value and the minimum as the low value.

**Call relations**: `_repo_pages` calls this for each fetched page. The resulting high and low values are put into `WalkPage` so `PartitionWalk` can update watermarks, resume windows, and stopping decisions.

*Call graph*: called by 1 (_repo_pages); 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/providers/notion.py`

`io_transport` · `source sync`

Notion stores documents in a shape that is easy for software to query but not easy for a person to read directly. A page is not just one text field; its title, properties, comments, and body blocks are spread across different API endpoints and nested objects. This connector is the translator between Notion and the rest of the system.

During a sync, it asks Notion for different “streams” of records: users, pages, data sources, comments, and blocks. A stream is just one category of things to collect. Pages and data sources come from Notion search. Users come from a users endpoint. Comments and blocks are found by first listing pages, then asking for each page’s comments or child blocks. Blocks can contain more blocks, so the connector walks down the tree, like opening folders inside folders, but stops at a safe depth and does not descend into child pages or databases because those are treated as their own records.

The file also turns raw Notion data into recallable prose. Instead of saving a hard-to-read JSON blob, it pulls out titles, rich text, property values, checkbox state, user names, and emails. If Notion refuses access because the integration lacks permission, the stream is marked as skipped rather than crashing the whole run.

#### Function details

##### `NotionConnector._make_client`  (lines 80–83)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Notion and adds the required Notion API version header. Notion expects this header so it knows which version of its API rules to use.

**Data flow**: It receives the base API address and a credential object. It asks the parent REST connector to build the authenticated client, then adds the Notion-Version header. It returns a ready-to-use asynchronous HTTP client.

**Call relations**: This is part of the connector setup before any Notion requests are made. Later paging and collection functions use the client it prepares, so every Notion request carries the required version information.


##### `NotionConnector.paginate`  (lines 85–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading a Notion stream. Given a requested stream, it chooses the right Notion-reading routine and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor that marks the last item previously synced. It checks the stream name, calls the matching helper, and passes along each batch of records. If Notion says access is forbidden or unauthorized, it turns that into a clean “stream skipped” result instead of a hard failure.

**Call relations**: The sync framework calls this when it wants records from Notion. It hands users to _collection, pages and data sources to _search, comments to _comments, and blocks to _blocks. If the stream is unknown or Notion refuses access, it raises StreamSkipped so the wider run can record the skip.

*Call graph*: calls 5 internal fn (__init__, _blocks, _collection, _comments, _search).


##### `NotionConnector._search`  (lines 117–140)

```
async def _search(self, client: httpx.AsyncClient, *, object_type: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads searchable Notion objects, such as pages or data sources, from Notion’s search endpoint. It also supports incremental syncing by only returning records edited after the saved cursor.

**Data flow**: It receives the HTTP client, the kind of object to search for, and an optional last-edited timestamp cursor. It repeatedly sends search requests sorted from oldest edit to newest, extracts the results list, filters out records that are not newer than the cursor, and yields non-empty batches. It stops when Notion says there are no more pages of results.

**Call relations**: paginate uses this directly for pages and data sources. _blocks and _comments also call it first because they need the list of pages before they can fetch each page’s blocks or comments.

*Call graph*: called by 3 (_blocks, _comments, paginate); 1 external calls (list_or_empty).


##### `NotionConnector._blocks`  (lines 142–152)

```
async def _blocks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This finds the block content that makes up the body of each Notion page. It is needed because Notion page text lives in nested blocks, not directly on the page record.

**Data flow**: It receives the HTTP client and an optional block update cursor. It first reads all pages, then for each valid page ID it starts walking that page’s block tree. It yields batches of blocks found under those pages.

**Call relations**: paginate calls this when the blocks stream is requested. It relies on _search to discover pages, then delegates the recursive block walking to _block_children.

*Call graph*: calls 2 internal fn (_block_children, _search); called by 1 (paginate).


##### `NotionConnector._block_children`  (lines 154–173)

```
async def _block_children(self, client: httpx.AsyncClient, *, block_id: str, depth: int, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through a Notion block tree and returns child blocks, including nested children. It is the part that lets the connector capture page body text beyond the first level.

**Data flow**: It receives an HTTP client, a block ID to inspect, the current nesting depth, and an optional cursor. If the nesting is too deep, it stops. Otherwise it fetches the block’s children, filters out blocks that are not newer than the cursor, yields the filtered batch, then visits each child that has its own children and is safe to descend into.

**Call relations**: _blocks starts this at each page ID. This function calls _collection to fetch each page of children and then calls itself again for deeper children, stopping before child pages, child databases, AI blocks, or the maximum depth.

*Call graph*: calls 1 internal fn (_collection); called by 1 (_blocks).


##### `NotionConnector._comments`  (lines 175–191)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This gathers comments attached to Notion pages. Comments are fetched page by page because Notion exposes them through a page or block ID.

**Data flow**: It receives the HTTP client and an optional created-time cursor. It first reads all pages, then for each valid page ID asks Notion for comments on that page. If a cursor is present, it keeps only comments created after it, and yields non-empty batches.

**Call relations**: paginate calls this for the comments stream. It uses _search to find the pages to inspect and _collection to page through Notion’s comment results for each page.

*Call graph*: calls 2 internal fn (_collection, _search); called by 1 (paginate).


##### `NotionConnector._collection`  (lines 193–207)

```
async def _collection(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared helper for Notion endpoints that return a paged list of results. Paging means Notion returns records in chunks and gives a marker for the next chunk.

**Data flow**: It receives the HTTP client, an API path, and optional query parameters. It asks the parent REST machinery to fetch pages of results using Notion’s cursor fields and page size setting. It yields each returned list of records.

**Call relations**: paginate uses this directly for users. _block_children uses it for block children, and _comments uses it for page comments. It hides the repeated cursor-paging details so the higher-level functions can focus on what they are collecting.

*Call graph*: called by 3 (_block_children, _comments, paginate).


##### `NotionConnector.render`  (lines 209–231)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns a raw Notion record into readable text for recall or indexing. Without it, many Notion records would appear as nested technical data instead of the words a person sees in Notion.

**Data flow**: It receives one Notion record and the stream it came from. Depending on the stream, it extracts a title and body text from page properties, data source titles and descriptions, block text, comment rich text, or user information. It then returns a plain title and a small markdown-like text block headed with the Notion stream name.

**Call relations**: The source framework calls this after records are fetched. It delegates the actual text extraction to helpers such as _page_title, _properties_text, _block_text, _rich_text_text, _str, and _user_text, and falls back to the parent renderer for unknown streams or missing titles.

*Call graph*: calls 6 internal fn (_block_text, _page_title, _properties_text, _rich_text_text, _str, _user_text).


##### `_str`  (lines 234–235)

```
def _str(value: Any) -> str
```

**Purpose**: This safely returns a value only if it is already a string. It prevents accidental use of numbers, dictionaries, or missing values where text is expected.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string. It does not change anything outside itself.

**Call relations**: render and several text helpers call this whenever they pull optional text-like fields from Notion data. It acts like a small safety gate before text is included in rendered output.

*Call graph*: called by 4 (render, _block_text, _property_text, _user_text).


##### `_rich_text_text`  (lines 238–246)

```
def _rich_text_text(value: Any) -> str
```

**Purpose**: This extracts human-readable text from Notion’s rich-text format. Notion stores styled text as a list of small runs, and this joins their plain text together.

**Data flow**: It receives a value that should be a list of rich-text parts. If it is not a list, it returns empty text. Otherwise it reads each part’s plain_text field when present, joins the pieces, trims surrounding whitespace, and returns the result.

**Call relations**: render uses this for comments and data source text. _page_title, _property_text, and _block_text also call it whenever they need to convert Notion rich text into ordinary text.

*Call graph*: called by 4 (render, _block_text, _page_title, _property_text).


##### `_page_title`  (lines 249–258)

```
def _page_title(page: dict[str, Any]) -> str
```

**Purpose**: This finds the visible title of a Notion page. Page titles live inside the page’s properties, so they need special extraction.

**Data flow**: It receives a page record. It looks through the page properties for the property whose type is title, converts that rich-text title into plain text, and returns the first non-empty title it finds. If the page has no usable title, it returns empty text.

**Call relations**: render calls this when rendering page records. It uses _rich_text_text to convert Notion’s title runs into normal text.

*Call graph*: calls 1 internal fn (_rich_text_text); called by 1 (render).


##### `_properties_text`  (lines 261–270)

```
def _properties_text(page: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion page’s properties into readable lines like “Status: In progress”. It gives page metadata a form that can be searched or recalled by a person.

**Data flow**: It receives a page record. It checks that the page has a properties dictionary, asks _property_text to read each property value, and builds one line per property that has visible text. It returns all lines joined with newlines.

**Call relations**: render calls this for page records after it finds the page title. It delegates the many different Notion property formats to _property_text.

*Call graph*: calls 1 internal fn (_property_text); called by 1 (render).


##### `_property_text`  (lines 273–292)

```
def _property_text(prop: dict[str, Any]) -> str
```

**Purpose**: This converts one Notion property value into plain text. It knows the common property types that people use, such as titles, select fields, dates, checkboxes, emails, and people.

**Data flow**: It receives one property dictionary. It checks the property’s type, reads the matching value field, and converts supported types into a string. Unsupported or malformed properties become empty text.

**Call relations**: _properties_text calls this for each property on a page. This function uses _rich_text_text for rich text and _str for optional names, dates, and other string fields.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (_properties_text).


##### `_block_text`  (lines 295–305)

```
def _block_text(block: dict[str, Any]) -> str
```

**Purpose**: This extracts the readable text from one Notion block. Blocks are the building pieces of a page body, such as paragraphs, headings, list items, to-dos, code blocks, and child page links.

**Data flow**: It receives a block record. It looks up the block-specific content based on the block type, then returns the title for child pages or databases, the rich text for normal text blocks, or a checkbox-style line for to-do blocks. If the block has no readable content, it returns empty text.

**Call relations**: render calls this for records in the blocks stream. It uses _rich_text_text for ordinary block text and _str for child page or database titles.

*Call graph*: calls 2 internal fn (_rich_text_text, _str); called by 1 (render).


##### `_user_text`  (lines 308–311)

```
def _user_text(record: dict[str, Any]) -> str
```

**Purpose**: This turns a Notion user record into simple text containing the user’s name and email when available. It makes user records useful to read instead of leaving them as nested data.

**Data flow**: It receives a user record. It reads the top-level name and, if present, the nested person email field. It keeps only real strings and joins the available pieces with a newline.

**Call relations**: render calls this for the users stream. It uses _str to safely include only actual text values.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


### Incident and Observability Sources
Connectors that read incident response, on-call, service, error-tracking, release, and event data for operational visibility.

### `extensions/sources/ufo_ext_sources/providers/pagerduty.py`

`io_transport` · `source sync`

PagerDuty is an incident-management service, and this connector is the read-only bridge from PagerDuty into this project. Without it, the system would not know which PagerDuty web addresses to call, how to walk through multiple result pages, or how to resume an incident sync from the last known update time.

The file defines several streams, which are named kinds of data the sync can collect: users, teams, services, incidents, incident notes, escalation policies, schedules, and on-calls. Each stream says where its records live in PagerDuty’s response and which field identifies a record uniquely.

The main class, `PagerDutyConnector`, builds on a shared `RestConnector`, meaning it reuses common web-request behavior but adds PagerDuty-specific rules. PagerDuty returns long lists in pages, like flipping through a catalog a hundred items at a time. This connector follows PagerDuty’s `more` flag and `limit` value to keep asking for the next page until there is no more data.

Incidents get special treatment because they can be synced incrementally. The connector asks PagerDuty for incidents sorted by update time and can pass a cursor, which is simply the last timestamp already seen. Incident notes are fetched by first reading incidents, then asking PagerDuty for notes attached to each incident. If PagerDuty refuses access with a 401 or 403 response, the connector marks that stream as skipped rather than treating the whole run as broken.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 74–77)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the web client used to talk to PagerDuty and adds the PagerDuty-specific `Accept` header. That header tells PagerDuty which version of its API response format the connector expects.

**Data flow**: It receives a base web address and a credential object. It asks the parent connector to build the basic authenticated HTTP client, then adds PagerDuty’s versioned media type header to every request. It returns the prepared client, ready to make PagerDuty API calls.

**Call relations**: This is part of the connector setup before data pages are requested. The shared `RestConnector` does the general client creation, and this function adds the small PagerDuty-specific detail needed so later calls receive the expected API version.


##### `PagerDutyConnector._offset_pages`  (lines 79–99)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads a PagerDuty list endpoint page by page. It also filters out old records when a stream has a timestamp cursor, so the caller only sees newer data.

**Data flow**: It receives an HTTP client, a stream description, optional query parameters, and an optional cursor timestamp. It calls the shared offset-page reader using PagerDuty’s paging rules: records are under the stream’s response key, `more` says whether to continue, and `limit` says how far to advance. If a cursor is present and the stream has a cursor field, it keeps only records whose timestamp is newer than that cursor. It yields non-empty batches of records.

**Call relations**: This is the common paging worker for most PagerDuty streams. `PagerDutyConnector._incidents` uses it with incident-specific sorting and filtering, while `PagerDutyConnector.paginate` uses it directly for simpler streams like users, teams, services, schedules, and on-calls.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 101–113)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads incident records from PagerDuty in update-time order. It supports incremental syncing, so a later run can continue from the last incident update already processed.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It builds query parameters that ask PagerDuty to sort incidents by `updated_at` from oldest to newest, and if a cursor exists, it sends that timestamp as `since`. It then delegates the actual page-by-page reading to `_offset_pages` and yields each page of incident records.

**Call relations**: This is the incident-specific path used by `PagerDutyConnector.paginate` when the requested stream is incidents. `PagerDutyConnector._incident_notes` also calls it as a first step, because notes are fetched by visiting each incident and then asking for that incident’s notes.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 115–128)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads notes attached to PagerDuty incidents. PagerDuty does not expose notes as one simple global list here, so the connector first finds incidents and then fetches notes for each incident one by one.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It reads incidents without applying the note cursor, takes each valid incident ID, and requests `/incidents/{incident_id}/notes`. It extracts the `notes` list from the response, removes notes older than or equal to the cursor when a cursor is present, and adds the incident ID as context to each note so the note can still be traced back to its incident. It yields batches of notes when any are found.

**Call relations**: This function is called by `PagerDutyConnector.paginate` for the `incident_notes` stream. It relies on `_incidents` to discover which incidents to inspect, uses `records_at` to pull the notes list out of PagerDuty’s response, and uses `with_context` to attach the parent incident ID before handing the notes back to the sync flow.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 130–164)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing function that decides how to fetch each PagerDuty stream. It chooses the right paging strategy for incidents, incident notes, and ordinary list-style streams.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If the stream is incidents, it yields pages from `_incidents`; if it is incident notes, it yields pages from `_incident_notes`; if it is one of the supported ordinary streams, it yields pages from `_offset_pages`. If the stream name is unknown, it raises `StreamSkipped`. If PagerDuty responds with 401 or 403, meaning the token is invalid or lacks permission, it turns that into a skipped stream instead of a fatal sync failure. Other HTTP errors are re-raised.

**Call relations**: The wider sync system calls this function when it wants records for a particular PagerDuty stream. `paginate` then hands the work to the helper that matches the stream’s shape and reports permission refusals through `StreamSkipped`, so the run can continue cleanly with other streams.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/providers/sentry.py`

`io_transport` · `source sync`

Sentry stores useful engineering history, but its data is spread across several API endpoints and split into pages. This file is the adapter that knows how to walk through that layout safely. It defines which Sentry streams exist, which field identifies each record, and which date field can be used to fetch only newer data on later syncs.

The main class, SentryConnector, is read-only. It does not keep an API token itself; the wider runner supplies credentials through the shared authentication path. When asked for a stream, it chooses the right route. Simple top-level streams, such as organizations and projects, are fetched directly. Other streams need context first: members and releases are fetched per organization, while issues and events are fetched per project. Each returned record is stamped with helpful context such as organization_slug and project_slug, like adding a return address to each envelope.

Sentry uses a Link response header to say whether another page exists. The helper _sentry_next_cursor reads that header and finds the next cursor token. If Sentry refuses a request with an authorization error, this connector marks that stream as skipped instead of treating the whole sync as broken. That matters because one Sentry grant may allow some data but not all of it.

#### Function details

##### `_sentry_next_cursor`  (lines 76–81)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This helper looks at Sentry's HTTP response headers and finds the cursor for the next page of results. A cursor is a small marker the API gives back so the next request can continue where the last one stopped.

**Data flow**: It receives HTTP headers from a Sentry response. It reads the Link header, searches for the part that means there is a next page with real results, and extracts the cursor text. It returns that cursor string, or returns nothing if there is no next page.

**Call relations**: The page-reading loop in SentryConnector._paged_list calls this after each Sentry request. If this helper finds another cursor, _paged_list keeps going; if it does not, the stream is finished.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.record_ref`  (lines 89–93)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This chooses a human-friendly reference for a Sentry record. For organizations, it uses the organization slug instead of the generic primary key, because the slug is the name people usually recognize.

**Data flow**: It receives one record and the stream definition for that record. If the stream is organizations, it reads the slug field and returns it as text when possible. For every other stream, it falls back to the standard reference behavior from the base connector.

**Call relations**: The wider source framework uses this when it needs a stable, readable label for a synced record. This method only customizes organization records; all other records continue through the normal RestConnector path.


##### `SentryConnector.paginate`  (lines 95–107)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the public paging entry for a Sentry stream. It yields batches of records and converts Sentry permission failures into a clear 'skip this stream' signal.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It asks _stream_pages to produce pages for the named stream and passes those pages onward. If Sentry replies with a 401 or 403 status, meaning unauthenticated or not allowed, it raises StreamSkipped with an explanation; other HTTP errors are left as real failures.

**Call relations**: The source syncing framework calls this when it wants records from a Sentry stream. paginate delegates the actual stream choice to _stream_pages, then protects the larger sync from expected authorization refusals.

*Call graph*: calls 2 internal fn (__init__, _stream_pages).


##### `SentryConnector._stream_pages`  (lines 109–122)

```
def _stream_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the switchboard for Sentry stream names. It decides which internal reader should be used for organizations, projects, members, issues, events, or releases.

**Data flow**: It receives an HTTP client, the requested stream name, and an optional cursor. It maps the name to the matching reader method and returns that reader's asynchronous stream of pages. If the name is not one this connector implements, it raises StreamSkipped.

**Call relations**: paginate calls this after the framework asks for a stream. _stream_pages then hands the work to _root_pages, _members, _issues, _events, or _releases depending on what kind of Sentry data is being requested.

*Call graph*: calls 6 internal fn (__init__, _events, _issues, _members, _releases, _root_pages); called by 1 (paginate).


##### `SentryConnector._root_pages`  (lines 124–137)

```
async def _root_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the top-level Sentry streams that do not need a parent object first: organizations and projects. It packages the collected records as one page when there is anything to return.

**Data flow**: It receives an HTTP client, a stream name, and an optional cursor. For organizations it fetches all organizations; for projects it fetches all projects. If projects are being synced with a cursor, it keeps only projects whose dateCreated value is newer than that cursor. It yields the remaining records if the list is not empty.

**Call relations**: _stream_pages calls this for the organizations and projects streams. It relies on _organizations and _projects to do the actual API paging, then applies the small amount of stream-specific filtering needed for projects.

*Call graph*: calls 2 internal fn (_organizations, _projects); called by 1 (_stream_pages).


##### `SentryConnector._paged_list`  (lines 139–156)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the common page-by-page reader for Sentry list endpoints. It repeatedly asks Sentry for a list, yields the valid record dictionaries, and follows Sentry's next-page cursor until there are no more pages.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. For each request, it adds the current cursor if one exists, calls the inherited raw GET request helper, reads the JSON response, and keeps only list items that are dictionaries. It yields each non-empty page and then uses _sentry_next_cursor to decide whether to request another page.

**Call relations**: Most other readers use this as their engine: _organizations, _projects, _members, _issues, _events, and _releases all call it for their specific Sentry endpoint. It calls _sentry_next_cursor after each response so the connector can move through Sentry's pagination correctly.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 158–162)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches all organizations visible to the current Sentry grant. Organizations are the top-level containers that other Sentry data often hangs under.

**Data flow**: It receives an HTTP client. It asks _paged_list to read the /organizations/ endpoint page by page, appends every page into one list, and returns the full list of organization records.

**Call relations**: _root_pages calls this when syncing the organizations stream. _members and _releases also call it first because those streams must be fetched separately for each organization.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, _root_pages).


##### `SentryConnector._projects`  (lines 164–168)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches all projects visible to the current Sentry grant. Projects are needed before the connector can read project-specific data such as issues and events.

**Data flow**: It receives an HTTP client. It asks _paged_list to read the /projects/ endpoint page by page, gathers all returned pages into one list, and returns that list.

**Call relations**: _root_pages calls this when syncing projects directly. _issues and _events call it first so they can loop over each project and ask Sentry for that project's detailed data.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, _root_pages).


##### `SentryConnector._members`  (lines 170–176)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches Sentry organization members for every organization the grant can see. It adds the organization slug to each member record so the record still shows where it came from after all pages are combined.

**Data flow**: It receives an HTTP client. It first gets all organizations, skips any without a usable slug, then reads the members endpoint for each organization. Before yielding each page, it adds organization_slug to every record in that page.

**Call relations**: _stream_pages calls this for the members stream. This function depends on _organizations to find the parent organizations, uses _paged_list for the actual Sentry API pages, and uses with_context to attach the organization information.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (_stream_pages); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 178–192)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches Sentry issues project by project. When a cursor is provided, it asks Sentry to return only issues whose lastSeen time is newer than that cursor.

**Data flow**: It receives an HTTP client and an optional cursor. It first gets all projects, extracts each project's organization slug and project slug, and skips projects missing those names. For each usable project it builds the project issues path, optionally adds a lastSeen query filter, reads pages from Sentry, and adds organization_slug and project_slug to each returned issue.

**Call relations**: _stream_pages calls this for the issues stream. It uses _projects to discover where to look, _paged_list to fetch each project's issue pages, and with_context to preserve which organization and project each issue belongs to.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (_stream_pages); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 194–208)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches Sentry events project by project. Events can be numerous, so when a cursor is available it asks Sentry only for events newer than that event timestamp.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all projects, pulls out each project and organization slug, and skips any project without usable names. For each project it builds the events endpoint, optionally adds an event.timestamp query filter, reads pages, and stamps each event with organization_slug and project_slug.

**Call relations**: _stream_pages calls this for the events stream. Like _issues, it starts with _projects, reads the Sentry API through _paged_list, and uses with_context so later stages can tell which project produced each event.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (_stream_pages); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 210–221)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches Sentry releases for every visible organization. If a cursor is provided, it keeps only releases whose dateCreated value is newer than that cursor.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all organizations, skips any without a usable slug, then reads each organization's releases endpoint. When a cursor is present, it filters out older releases. It yields non-empty pages after adding organization_slug to each release.

**Call relations**: _stream_pages calls this for the releases stream. It relies on _organizations to find the organizations, _paged_list to read the release pages, and with_context to keep the organization name attached to the release records.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (_stream_pages); 1 external calls (with_context).
