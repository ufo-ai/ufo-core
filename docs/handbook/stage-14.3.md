# Work management and developer operations source connectors  `stage-14.3`

This stage is shared behind-the-scenes support for syncing work and engineering tools into the system’s searchable memory. Each file is a connector, meaning a small adapter that knows how to talk to one outside service, read its web API, and turn the replies into standard streams of records the rest of the system can store, search, and resume later.

The project-management connectors cover Asana, ClickUp, Jira, Linear, monday.com, and Wrike. They fetch things like workspaces, teams, projects, boards, tasks, issues, comments, users, labels, workflows, and custom fields. Each one understands that service’s shape: ClickUp walks from teams down to lists, while Linear and monday.com use GraphQL, a query language for asking an API for selected data.

The developer-operations connectors cover GitHub, PagerDuty, and Sentry. GitHub brings in repositories, issues, pull requests, commits, and comments. PagerDuty brings in incidents, services, schedules, and on-call records. Sentry brings in error reports, events, releases, and projects. Together, these connectors act like translators for the main sync engine.

## Files in this stage

### Project and task tracking
Connectors that pull projects, tasks, issues, boards, comments, users, and related work-management metadata from planning platforms.

### `extensions/sources/ufo_ext_sources/asana.py`

`io_transport` · `source sync`

Asana stores work information behind a web API, and it does not send everything at once. Instead, each request returns a page shaped like an envelope: the real records are inside a `data` list, and a `next_page` field says whether there is another page to fetch. This file is the Asana-specific adapter that understands that envelope.

The file first defines the Asana streams the system knows about. A stream is one kind of Asana object, such as `tasks`, `projects`, or `users`. The most important work-tracking streams are marked as canonical, meaning they are central records the system especially cares about. Some streams can be synced incrementally: for tasks and projects, Asana supports asking “only give me things changed since this time.” Other streams are read in full each run because Asana does not offer the same efficient filter for them.

`AsanaConnector` then supplies the shared REST connector framework with Asana’s name, base web address, stream list, and paging rules. It does not store or create an Asana token itself; authentication is expected to be supplied by the surrounding runner. It also only reads data. Without this file, the system might still know how to make generic REST calls, but it would not know which Asana objects to collect or how to walk through Asana’s cursor-based pages safely.

#### Function details

##### `_stream`  (lines 24–38)

```
def _stream(name: str, *, cursor_field: str | None=None, updated_at_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This small helper creates a stream description for one Asana object type. It keeps the long stream list readable by filling in the repeated details, such as using Asana’s `gid` field as the record identifier.

**Data flow**: It receives a stream name and optional information about cursor fields, update-time fields, and whether the stream is canonical. It packages those choices into a `StreamSpec`, which is the standard description the rest of the source framework uses to know what to fetch and how to track progress.

**Call relations**: The file uses this helper while building the Asana stream catalog. The helper hands each completed stream description to `StreamSpec`, so the generic source framework later has a consistent map of Asana objects to sync.

*Call graph*: 1 external calls (__init__).


##### `AsanaConnector.paginate`  (lines 74–90)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Asana stream page by page. It knows how to ask Asana for up to 100 records at a time, follow Asana’s `next_page.offset` cursor, and optionally request only recently modified tasks or projects.

**Data flow**: It starts with an HTTP client, a stream description, and an optional saved cursor from a previous sync. It builds request parameters, adds `modified_since` only when Asana supports it for that stream, then repeatedly fetches a page. From each response it pulls the `data` list, yields it if there are records, checks for the next page offset, and stops when Asana says there is no next page.

**Call relations**: The broader REST connector framework calls this method when it needs records from an Asana stream. Inside the loop, it relies on the connector’s `_get` request helper to fetch from Asana and uses `list_or_empty` to treat missing or non-list `data` safely, so downstream sync code receives clean batches of records rather than raw API envelopes.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/clickup.py`

`io_transport` · `source sync`

ClickUp data is not stored as one simple list. It is more like a set of nested folders: teams contain spaces, spaces contain folders and lists, and lists contain tasks, comments, and fields. This connector is the map-reader for that structure. Without it, the system would not know which ClickUp API addresses to call, how to find all the lower-level items, or how to remember which parent team, space, folder, or list each item came from.

The file defines the ClickUp streams, meaning the kinds of records that can be synced: teams, users, spaces, folders, lists, tasks, list comments, list custom fields, and goals. The `ClickUpConnector` then provides the steps for fetching each stream. It starts at teams, uses those team IDs to find spaces, uses spaces to find folders and folderless lists, and finally uses lists to fetch tasks and list-level child data.

For tasks and comments, it can use a cursor, which is a saved “last seen” value, so future syncs only bring in records updated after that point. It also adds context such as `team_id`, `space_id`, `folder_id`, or `list_id` so records do not lose their place in the ClickUp hierarchy. The `flatten` step then reshapes some ClickUp-specific fields into common names like `name`, `created_at`, `status`, and `author`, making the records easier for the rest of the system to use.

#### Function details

##### `ClickUpConnector._teams`  (lines 58–60)

```
async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the top-level ClickUp teams that the connected account can see. This is the starting point for almost every other ClickUp read, because spaces, goals, and users are all discovered through teams.

**Data flow**: It receives an HTTP client that can talk to ClickUp. It calls the ClickUp `/team` endpoint, looks inside the response for the `teams` list, and returns that list as plain records. It does not change anything in ClickUp; it only reads.

**Call relations**: This is the first step in the hierarchy. `_spaces` calls it so it can find spaces under each team, and `paginate` calls it directly when syncing teams, users, or goals.

*Call graph*: called by 2 (_spaces, paginate); 1 external calls (records_at).


##### `ClickUpConnector._spaces`  (lines 62–70)

```
async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all active spaces inside all ClickUp teams. A space is a major container in ClickUp, so this function opens the next level down after teams.

**Data flow**: It starts with the HTTP client, asks `_teams` for the available teams, and loops through valid team IDs. For each team, it requests that team’s non-archived spaces from ClickUp, extracts the `spaces` records, adds the parent `team_id` to each one, and returns the combined list.

**Call relations**: It depends on `_teams` because spaces cannot be discovered without team IDs. `_folders`, `_lists`, and `paginate` call it when they need either the space records themselves or the space IDs needed to go deeper.

*Call graph*: calls 1 internal fn (_teams); called by 3 (_folders, _lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._folders`  (lines 72–82)

```
async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all active folders inside all ClickUp spaces. Folders are one of the places where ClickUp lists can live, so this function prepares the path toward list and task syncing.

**Data flow**: It receives an HTTP client, asks `_spaces` for all spaces, and keeps only spaces with usable IDs. For each space, it requests non-archived folders, extracts the `folders` records, adds the parent `space_id`, and returns all folders together.

**Call relations**: It builds on `_spaces` and is used by `_lists` to find lists stored inside folders. `paginate` also calls it directly when the current stream being synced is folders.

*Call graph*: calls 1 internal fn (_spaces); called by 2 (_lists, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._lists`  (lines 84–100)

```
async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Finds all active ClickUp lists, whether they are inside folders or directly inside spaces. Lists matter because tasks, comments, and custom fields are fetched from lists.

**Data flow**: It receives an HTTP client and first asks `_folders` for all folders. For each valid folder, it fetches non-archived lists and adds the parent `folder_id`. Then it asks `_spaces` for all spaces and fetches non-archived lists that live directly under those spaces, adding the parent `space_id`. It returns one combined list of list records.

**Call relations**: This function is the bridge between the upper hierarchy and leaf data. `_tasks` uses it to find where tasks live, `_list_child_stream` uses it to find comments and custom fields, and `paginate` calls it directly when syncing lists.

*Call graph*: calls 2 internal fn (_folders, _spaces); called by 3 (_list_child_stream, _tasks, paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._tasks`  (lines 102–127)

```
async def _tasks(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads tasks from every ClickUp list, page by page. It supports incremental syncing, meaning it can skip tasks that were not updated since the last saved cursor.

**Data flow**: It receives an HTTP client and an optional cursor value. It asks `_lists` for all lists, then for each valid list ID it requests tasks from ClickUp using numbered pages. Each batch of tasks is tagged with the list ID and list name. If a cursor is present, it keeps only tasks whose `date_updated` value is newer than that cursor. It yields each non-empty batch instead of waiting to collect everything at once.

**Call relations**: It relies on `_lists` because ClickUp tasks are fetched per list. `paginate` calls it when the selected stream is `tasks`, and `_tasks` hands back batches that the broader sync engine can store or index.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector._list_child_stream`  (lines 129–148)

```
async def _list_child_stream(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads extra list-level data: comments on lists and custom fields defined for lists. It exists because these records are not fetched from the same endpoint as lists or tasks.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It asks `_lists` for all lists, chooses the correct ClickUp endpoint based on whether the stream is list comments or list custom fields, extracts either `comments` or `fields`, and adds the list ID and list name to each record. If the stream has a cursor field and a cursor was provided, it filters out older records. It yields each list’s non-empty batch.

**Call relations**: It sits under `paginate`, which calls it for the `list_comments` and `list_custom_fields` streams. It uses `_lists` to know which ClickUp lists to visit, then passes the resulting batches back up to the sync flow.

*Call graph*: calls 1 internal fn (_lists); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `ClickUpConnector.paginate`  (lines 150–204)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct fetching routine for the stream currently being synced. In plain terms, it is the traffic director that says, “If we are syncing tasks, use the task reader; if we are syncing spaces, use the space reader,” and so on.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching helper, and yields records in batches when there is data. For users, it builds a unique user list from the members embedded in team records. For goals, it loops through teams and fetches goals per team. If the stream name is not supported, it raises a skip signal instead of pretending it can sync it.

**Call relations**: This is the main method the shared REST sync framework calls to get ClickUp records. It delegates to `_teams`, `_spaces`, `_folders`, `_lists`, `_tasks`, and `_list_child_stream` as needed, and it raises `StreamSkipped` when no implementation exists for a requested stream.

*Call graph*: calls 7 internal fn (__init__, _folders, _list_child_stream, _lists, _spaces, _tasks, _teams); 2 external calls (records_at, with_context).


##### `ClickUpConnector.flatten`  (lines 206–240)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns ClickUp’s raw records into shapes that are easier for the rest of the system to understand. It keeps the original data but adds or normalizes common fields such as names, dates, status, body text, and author.

**Data flow**: It receives one record and the stream it came from. For users, spaces, folders, lists, tasks, and list comments, it copies the record and adds cleaner common fields. For comments, it safely reads the nested user object and creates fields like `body`, `author`, `created_at`, and `parent_external_id`. For streams that do not need special shaping, it returns the record unchanged.

**Call relations**: After `paginate` has fetched records, the broader connector framework can call `flatten` before saving or presenting them. It uses `dict_or_empty` to safely deal with comment records where the nested user data might be missing or not shaped as expected.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/jira.py`

`io_transport` · `during Jira source sync runs`

Jira Cloud accounts can reach one or more Atlassian sites, and each site has its own internal cloud id. This connector first asks Atlassian which sites the current login can access, then visits each site and reads the Jira objects the system cares about. Without this file, Jira would just be a remote service full of JSON; the rest of the system would not know which URLs to call, how to page through long lists, or how to turn an issue into useful text.

The file defines the available Jira streams, such as issues and comments. A stream is one kind of thing to sync. The main connector chooses the right reader for each stream. Most Jira lists are read in chunks using startAt and maxResults, like reading a long book one page at a time. Issues, comments, and sprints can use a cursor, which is a saved timestamp that says, “only fetch things updated after this point.”

The connector is read-only. It never creates or edits Jira data. If Jira says the account is not allowed to see something, the connector records that stream as skipped instead of crashing the whole sync. It also cleans up important records for search: issue fields are flattened where needed, and Jira’s rich document format is walked to extract plain readable text.

#### Function details

##### `JiraConnector.paginate`  (lines 73–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for Jira streams. Given a requested stream, it calls the specific reader for projects, issues, comments, users, boards, or sprints, and yields batches of records back to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks the stream name, sends the request to the matching helper, and passes each returned page onward. If Jira refuses access with an authorization error, it changes that failure into a clean “stream skipped” result; other HTTP errors still bubble up.

**Call relations**: The wider connector framework calls this when it wants records for a Jira stream. It hands the work to _projects, _issues, _comments, _users, _boards, or _sprints depending on the stream name. When a stream is unknown or access is refused, it uses StreamSkipped so the sync can continue in a controlled way.

*Call graph*: calls 7 internal fn (__init__, _boards, _comments, _issues, _projects, _sprints, _users).


##### `JiraConnector._sites`  (lines 106–110)

```
async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Atlassian which Jira sites the connected account can access. That matters because every later Jira API path needs the site’s cloud id.

**Data flow**: It receives an HTTP client, calls Atlassian’s accessible-resources endpoint, reads the JSON response, and returns it as a list. If the response is empty or not shaped like a list, list_or_empty turns it into a safe empty list.

**Call relations**: _projects, _issues, _users, and _boards call this before reading site-specific data. It acts like looking up the buildings a key can open before trying to visit rooms inside them.

*Call graph*: called by 4 (_boards, _issues, _projects, _users); 1 external calls (list_or_empty).


##### `JiraConnector._offset_values`  (lines 112–132)

```
async def _offset_values(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, result_key: str='values') -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira endpoints that return long lists in numbered pages. It keeps asking for the next chunk until Jira says there is nothing left.

**Data flow**: It receives an HTTP client, an API path, optional query parameters, and the JSON field where records live. It repeatedly sends requests with startAt and maxResults, pulls records out with records_at, yields non-empty pages, and stops when the response says it is the last page or the count reaches the total.

**Call relations**: _projects, _issues, _comments, _boards, and _sprints use this shared paging helper. Those stream-specific functions decide what path to read; this function supplies the repeated page-turning behavior.

*Call graph*: called by 5 (_boards, _comments, _issues, _projects, _sprints); 1 external calls (records_at).


##### `JiraConnector._projects`  (lines 134–141)

```
async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira projects from every accessible site. Projects are the containers that issues belong to.

**Data flow**: It asks _sites for accessible sites, skips any site without a usable cloud id, then reads the project search endpoint page by page through _offset_values. Before yielding each page, it adds context such as cloud_id and site_url to every project record.

**Call relations**: paginate calls this when the sync requests the projects stream. It depends on _sites to know where to look and on _offset_values to read all pages, then uses with_context so downstream code knows which Jira site each project came from.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._issues`  (lines 143–155)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira issues, optionally only those updated after the last saved cursor. Issues are the main work items people usually search for later.

**Data flow**: It builds a Jira Query Language filter, which is Jira’s search syntax, using the cursor if one is present. It then loops through accessible sites, reads the issue search endpoint in pages, requests only the needed fields, and adds site context to each issue before yielding it.

**Call relations**: paginate calls this for the issues stream. _comments also calls it to discover which issues exist before fetching their comments. It uses _sites for site discovery, _offset_values for paging, and with_context to attach the source site information.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_comments, paginate); 1 external calls (with_context).


##### `JiraConnector._comments`  (lines 157–177)

```
async def _comments(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads comments attached to Jira issues. Comments are fetched by first finding issues, then asking Jira for each issue’s comment list.

**Data flow**: It reads all issues through _issues with no issue cursor, then for each issue with a valid issue id and cloud id it calls the comments endpoint. It pages through comments, filters out comments older than the comment cursor when one is provided, adds issue and site context, and yields only non-empty comment batches.

**Call relations**: paginate calls this for the issue_comments stream. It relies on _issues to find the parent issues and _offset_values to page through each issue’s comments. It uses with_context so each comment still carries its Jira site and parent issue information.

*Call graph*: calls 2 internal fn (_issues, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector._users`  (lines 179–190)

```
async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira users from each accessible site. Users are useful for showing names, ownership, and participation around issues.

**Data flow**: It asks _sites for site access, skips invalid cloud ids, calls Jira’s users search endpoint once per site, turns the JSON response into a safe list, and yields the users with cloud_id and site_url added.

**Call relations**: paginate calls this for the users stream. Unlike most other stream readers, it does not use _offset_values because this endpoint returns a bare JSON array rather than the usual paged envelope.

*Call graph*: calls 1 internal fn (_sites); called by 1 (paginate); 2 external calls (list_or_empty, with_context).


##### `JiraConnector._boards`  (lines 192–199)

```
async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Jira Agile boards from every accessible site. Boards are later used as the starting point for finding sprints.

**Data flow**: It gets accessible sites from _sites, keeps only sites with a valid cloud id, reads the Agile board endpoint page by page, and adds site context to the board records before yielding them.

**Call relations**: paginate calls this for the boards stream, and _sprints calls it first because sprints are listed under boards. It uses _offset_values for page-by-page reading and with_context to preserve the Jira site each board belongs to.

*Call graph*: calls 2 internal fn (_offset_values, _sites); called by 2 (_sprints, paginate); 1 external calls (with_context).


##### `JiraConnector._sprints`  (lines 201–215)

```
async def _sprints(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads sprints from Jira Agile boards, optionally only those updated after a saved cursor. Sprints are fetched through their boards because Jira organizes them that way in the API.

**Data flow**: It first reads boards through _boards. For each board with a valid id and cloud id, it reads that board’s sprint endpoint in pages, filters by updatedDate if a cursor is present, adds the cloud id and board id, and yields the remaining sprint records.

**Call relations**: paginate calls this for the sprints stream. It depends on _boards to find where sprints live and on _offset_values to read each board’s sprint list.

*Call graph*: calls 2 internal fn (_boards, _offset_values); called by 1 (paginate); 1 external calls (with_context).


##### `JiraConnector.flatten`  (lines 217–224)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This normalizes Jira issue records so the sync engine can track their update timestamp in a simple top-level field. Other streams already have their key fields in the expected place and pass through unchanged.

**Data flow**: It receives one record and its stream description. If the record is an issue, it safely reads the nested fields object and copies fields.updated into a top-level updated value. It returns the adjusted record, or the original record for non-issue streams.

**Call relations**: No direct caller is shown in the provided graph, but in the connector lifecycle this is the shape-cleaning step used after records are fetched. It calls _dict_or_empty so malformed or missing fields do not break the sync.

*Call graph*: calls 1 internal fn (_dict_or_empty).


##### `JiraConnector.render`  (lines 226–252)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns selected Jira records into readable text for humans and search. It gives issues and comments a useful title and body instead of exposing raw Jira JSON.

**Data flow**: It receives a record and stream description. For issues, it extracts the summary, status, priority, assignee, reporter, and description text. For comments, it extracts the author name and comment body. It returns a title plus a Markdown-like page body; for other streams it falls back to the base connector’s rendering.

**Call relations**: No direct caller is shown in the graph, but this is the presentation step the source framework uses when making synced records recallable. It calls small helpers such as _dict_or_empty, _str, _person, _field_line, and _doc_text to safely turn Jira’s nested data into plain text.

*Call graph*: calls 5 internal fn (_dict_or_empty, _doc_text, _field_line, _person, _str).


##### `_str`  (lines 255–256)

```
def _str(value: Any) -> str
```

**Purpose**: This small safety helper returns a value only if it is actually a string. It prevents unexpected data shapes from leaking into rendered text.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns an empty string.

**Call relations**: JiraConnector.render uses this while building issue text, and _person uses it when choosing a display name or email address. It is one of the guardrails that keeps odd Jira responses from causing noisy output.

*Call graph*: called by 2 (render, _person).


##### `_dict_or_empty`  (lines 259–260)

```
def _dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: This small safety helper returns a dictionary only when the input is truly a dictionary. It lets the rest of the code read nested fields without repeatedly checking for bad shapes.

**Data flow**: It receives any value. If the value is a dictionary, it returns it; otherwise it returns an empty dictionary that is safe to read from.

**Call relations**: flatten uses it to read issue fields, render uses it for issue details, and _person uses it for user-like objects. It keeps these higher-level steps focused on meaning rather than defensive type checks.

*Call graph*: called by 3 (flatten, render, _person).


##### `_person`  (lines 263–265)

```
def _person(value: Any) -> str
```

**Purpose**: This extracts a readable person name from a Jira user object. It prefers the display name and falls back to the email address.

**Data flow**: It receives a possible person object, safely treats it as a dictionary, reads displayName and emailAddress as strings, and returns the best available label. If neither exists, it returns an empty string.

**Call relations**: JiraConnector.render calls this when showing issue assignees, reporters, and comment authors. It uses _dict_or_empty and _str so missing or oddly shaped user data does not break rendering.

*Call graph*: calls 2 internal fn (_dict_or_empty, _str); called by 1 (render).


##### `_field_line`  (lines 268–269)

```
def _field_line(label: str, value: str) -> str
```

**Purpose**: This formats one label-and-value pair for the readable issue body. It avoids adding empty lines when a value is missing.

**Data flow**: It receives a label such as “Status” and a text value. If the value is present, it returns a line like “Status: Done”; otherwise it returns an empty string.

**Call relations**: JiraConnector.render calls this while building the issue metadata block. It helps render include useful fields without cluttering the page with blank labels.

*Call graph*: called by 1 (render).


##### `_doc_text`  (lines 272–289)

```
def _doc_text(value: Any) -> str
```

**Purpose**: This converts Jira’s rich document format into plain text. Jira descriptions and comments are stored as nested trees, so this function walks the tree and gathers the actual text leaves.

**Data flow**: It receives any value, usually an Atlassian Document Format object. It creates an empty list of text chunks, walks through dictionaries and lists inside the value, collects every string found under a text key, joins the chunks with newlines, trims the result, and returns plain text.

**Call relations**: JiraConnector.render calls this for issue descriptions and comment bodies. Inside itself, it uses the nested _doc_text.walk helper to do the tree traversal.

*Call graph*: called by 1 (render).


##### `_doc_text.walk`  (lines 277–286)

```
def walk(node: Any) -> None
```

**Purpose**: This is the recursive walker inside _doc_text. Its job is to move through a nested Jira document and collect all text fragments.

**Data flow**: It receives one node from the document tree. If the node is a dictionary, it saves its text value when present and then visits each child in its content list. If the node is a list, it visits each item. It does not return a value; it changes the surrounding chunks list by adding found text.

**Call relations**: _doc_text calls this starting with the whole document value. The helper then calls itself for child nodes, like checking every folder and subfolder until all notes have been found.


### `extensions/sources/ufo_ext_sources/linear.py`

`io_transport` · `sync run`

Linear is a work-tracking tool, and its API speaks GraphQL, which is a request style where the client asks for exactly the fields it wants. This file is the Linear “source connector”: it knows which Linear collections exist, what query to send for each one, how to page through long result sets, and how to make important records readable to humans.

The file defines a catalog of Linear streams, such as issues, projects, comments, users, teams, workflow states, and customer data. Most streams can be synced incrementally: after the first run, the connector asks Linear only for records whose updatedAt time is newer than the last saved cursor. A few Linear collections do not support that kind of filtering, so they are fully re-read each run.

The main class, LinearConnector, sends POST requests to Linear’s /graphql endpoint. It follows Linear’s pageInfo instructions, much like turning pages in a book until there are no more pages. If Linear refuses access because the token is invalid or missing permission, the stream is marked as skipped instead of crashing the whole sync. If Linear returns GraphQL errors, the connector fails loudly so it does not save incomplete data.

It also customizes how issues, projects, comments, and users are rendered, producing plain text with useful headings and key details instead of raw API-shaped data.

#### Function details

##### `_stream`  (lines 32–42)

```
def _stream(name: str, *, cursor_field: str | None=ORDER_BY_UPDATED_AT, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a StreamSpec, which is the system’s small description of one Linear collection to sync. It keeps the repeated stream setup short and consistent.

**Data flow**: It receives a stream name, an optional cursor field, and whether the stream is considered canonical content. It fills in the standard Linear fields, such as createdAt and updatedAt, then returns a StreamSpec object that the connector framework can use during syncing.

**Call relations**: This helper is used while building the Linear stream catalog near module load time. It hands each completed stream description to the larger connector setup, which later uses those descriptions when LinearConnector.paginate decides what to fetch.

*Call graph*: 1 external calls (__init__).


##### `LinearConnector.paginate`  (lines 269–312)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous function fetches records for one Linear stream, one page at a time. It is the core read path that turns Linear’s paged GraphQL responses into batches of records for the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous run. It chooses the right GraphQL query, adds an updatedAt filter when the stream supports incremental syncing, posts the request to Linear, checks for refusal or GraphQL errors, extracts the nodes list from the response, yields any records it found, then follows Linear’s endCursor to request the next page until there are no more pages.

**Call relations**: The connector framework calls this when it needs data for a Linear stream. Inside the loop it relies on the base connector’s POST behavior, uses list_or_empty to safely turn the response’s nodes into a list, and raises StreamSkipped when Linear responds with an authorization refusal so the broader run can record a skip rather than treat it as ordinary data.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


##### `LinearConnector.render`  (lines 314–353)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns selected Linear records into readable text pages. It makes issues, projects, comments, and users easier for people to search and understand than raw GraphQL JSON would be.

**Data flow**: It receives one Linear record and the stream it came from. For issues, projects, comments, and users, it pulls out human-friendly fields such as title, description, state, assignee, lead, target date, email, or comment body, formats them into a heading and body text, and returns both a title and rendered page content. For other stream types, it falls back to the parent connector’s default rendering.

**Call relations**: After records have been fetched, the sync framework can call this to prepare content for storage or recall. It uses _str to safely read string fields, _ref_id to display linked object IDs, and _labeled to build compact labeled metadata blocks.

*Call graph*: calls 3 internal fn (_labeled, _ref_id, _str).


##### `_str`  (lines 356–357)

```
def _str(value: Any) -> str
```

**Purpose**: This helper safely extracts a string value. It prevents non-text values from accidentally appearing in rendered pages.

**Data flow**: It receives any value. If the value is already a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: LinearConnector.render calls this whenever it pulls text from a Linear record. _ref_id also uses it after reading an id field, so linked references get the same safe string treatment.

*Call graph*: called by 2 (render, _ref_id).


##### `_ref_id`  (lines 360–361)

```
def _ref_id(value: Any) -> str
```

**Purpose**: This helper reads the id from a nested Linear reference, such as an assignee or lead. It gives the renderer a simple way to show linked object IDs without assuming the reference is always present.

**Data flow**: It receives any value. If that value is a dictionary-like Linear object, it reads its id field and passes it through _str; otherwise it returns an empty string.

**Call relations**: LinearConnector.render uses this when building metadata for records that point to other Linear objects. It delegates the final text safety check to _str.

*Call graph*: calls 1 internal fn (_str); called by 1 (render).


##### `_labeled`  (lines 364–365)

```
def _labeled(pairs: list[tuple[str, str]]) -> str
```

**Purpose**: This helper formats a small list of labels and values into readable lines, skipping empty values. It is used to make record metadata look clean in rendered pages.

**Data flow**: It receives pairs such as label and value. It keeps only the pairs with a non-empty value, turns each into text like 'state: started', joins those lines with newlines, and returns the combined text.

**Call relations**: LinearConnector.render calls this when it creates metadata blocks for issues, projects, and users. It acts like the small formatting step between raw record fields and the final page text.

*Call graph*: called by 1 (render).


### `extensions/sources/ufo_ext_sources/monday.py`

`io_transport` · `source sync run`

monday.com does not expose these objects through many simple web addresses. Instead, it uses GraphQL, a query language where the client asks for exactly the fields it wants. This connector is the translator between the project’s source-sync framework and monday’s GraphQL API.

The file defines the monday streams the system knows about, such as boards, items, updates, and activity logs. A stream is one kind of thing to import. For each stream, the connector knows how to ask monday for pages of records, how to continue to the next page, and how to skip records that are older than the last saved checkpoint. monday does not provide a true “give me only changes since this time” option here, so the connector fetches pages and filters them locally by dates like `updated_at` or `created_at`.

The connector also copes with monday-specific quirks. Board items use a cursor-based page token, while top-level lists use page numbers. Assigned people are hidden inside board-specific column values, so the helper `_extract_person_ids` digs those IDs out and adds a simple `assignee_ids` list. If monday refuses a query or the token lacks permission, the connector raises `StreamSkipped`, meaning this stream is recorded as skipped instead of saving incomplete data.

#### Function details

##### `_extract_person_ids`  (lines 70–98)

```
def _extract_person_ids(column_values: Any) -> list[str]
```

**Purpose**: This helper pulls monday user IDs out of item column data. It exists because monday stores people assignments inside flexible, board-specific columns rather than in one simple field.

**Data flow**: It receives a value that should be a list of monday column records. It looks only at columns whose type is `people`, reads their stored JSON value, finds entries marked as real people rather than teams, and converts their IDs to strings. It returns a flat list of person IDs; if the input is missing, malformed, or not a people column, it quietly ignores it.

**Call relations**: When `MondayConnector._items` reads board items, it calls this helper for each item’s `column_values`. The result is added back onto the item as `assignee_ids`, giving later parts of the sync a stable, easy-to-use assignment field.

*Call graph*: called by 1 (_items); 1 external calls (loads).


##### `MondayConnector._graphql`  (lines 106–118)

```
async def _graphql(self, client: httpx.AsyncClient, query: str, *, variables: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: This is the connector’s common way to send a GraphQL request to monday.com and get back the useful `data` part. It also turns monday GraphQL errors into a controlled stream skip, so the sync does not treat refused or unavailable data as valid partial results.

**Data flow**: It receives an HTTP client, a GraphQL query string, and optional variables. It posts those to monday’s API root path, checks whether the response contains an `errors` list, and raises `StreamSkipped` if so. Otherwise it returns the response’s `data` object as a dictionary, or an empty dictionary if monday returned something unexpected.

**Call relations**: This is the low-level doorway used by the monday-specific readers. `MondayConnector._paged_root`, `MondayConnector._items`, `MondayConnector._activity_logs`, and `MondayConnector.paginate` all call it whenever they need to ask monday for data.

*Call graph*: calls 1 internal fn (__init__); called by 4 (_activity_logs, _items, _paged_root, paginate).


##### `MondayConnector._paged_root`  (lines 120–143)

```
async def _paged_root(self, client: httpx.AsyncClient, *, field: str, selection: str, cursor: str | None=None, cursor_field: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads monday collections that use simple page numbers, such as users, workspaces, boards, and updates. It keeps asking for page 1, page 2, and so on until monday returns no more records.

**Data flow**: It receives the GraphQL field name to fetch, the fields to request for each record, and optionally a saved cursor date. For each page, it asks monday for up to 100 records, normalizes the result into a list, filters out records at or before the saved cursor when requested, and yields each non-empty page. When a page produces no records, it stops.

**Call relations**: Higher-level stream readers use this whenever monday’s normal numbered paging is enough. `MondayConnector._boards` uses it to collect all boards, and `MondayConnector.paginate` uses it directly for streams like users, workspaces, boards, and updates.

*Call graph*: calls 1 internal fn (_graphql); called by 2 (_boards, paginate); 1 external calls (list_or_empty).


##### `MondayConnector._boards`  (lines 145–156)

```
async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper gathers all boards from monday.com. Other monday data, such as items and activity logs, must be requested board by board, so this function provides the list of boards to visit.

**Data flow**: It starts with an empty list, then asks `_paged_root` for every page of boards with their main fields and workspace details. It appends all returned board records into one list and returns that full list.

**Call relations**: `MondayConnector._items` and `MondayConnector._activity_logs` call this before they can do their own work. In that flow, boards act like a map: first find every board, then visit each board to fetch its child records.

*Call graph*: calls 1 internal fn (_paged_root); called by 2 (_activity_logs, _items).


##### `MondayConnector._items`  (lines 158–217)

```
async def _items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads all items from all monday boards. It also enriches each item with a simple list of assigned person IDs, which monday otherwise hides inside column data.

**Data flow**: It receives an HTTP client and an optional cursor date. First it gets all boards. For each board, it requests the first page of items, then follows monday’s item-page cursor to request later pages. Each item is cleaned into a list, gets `assignee_ids` extracted from its column values, and is filtered by `updated_at` if a cursor was supplied. Non-empty batches are yielded to the caller.

**Call relations**: `MondayConnector.paginate` calls this when the current stream is `items`. Inside, it relies on `_boards` to know which boards to scan, `_graphql` to make monday requests, and `_extract_person_ids` to turn monday’s people-column format into a simpler field.

*Call graph*: calls 3 internal fn (_boards, _graphql, _extract_person_ids); called by 1 (paginate); 2 external calls (dict_or_empty, list_or_empty).


##### `MondayConnector._activity_logs`  (lines 219–247)

```
async def _activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads activity log entries from each monday board. Activity logs describe changes and events on a board, so they are collected board by board.

**Data flow**: It receives an HTTP client and an optional cursor date. It first gets the board list, then asks monday for recent activity logs for each board. For every log it finds, it adds the board ID so the log can be tied back to its board, filters by `created_at` when a cursor is present, and yields non-empty groups of logs.

**Call relations**: `MondayConnector.paginate` calls this for the `activity_logs` stream. It uses `_boards` as the starting list of places to inspect, and `_graphql` for the actual monday API queries.

*Call graph*: calls 2 internal fn (_boards, _graphql); called by 1 (paginate); 1 external calls (list_or_empty).


##### `MondayConnector.paginate`  (lines 249–323)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher that knows how to fetch each monday stream. Given a stream name, it chooses the right query pattern and yields pages of records for the sync framework to process.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, runs the matching monday query or helper, applies paging and cursor filtering where appropriate, and yields lists of records. If the stream is unknown, or if monday returns a 401 or 403 permission failure, it raises `StreamSkipped` so the run records a safe skip instead of importing incomplete data.

**Call relations**: The source framework calls this when it wants records for a monday stream. This function then hands work to `_paged_root` for simple paged streams, `_items` for board items, `_activity_logs` for board events, and `_graphql` for one-shot streams like teams and tags.

*Call graph*: calls 5 internal fn (__init__, _activity_logs, _graphql, _items, _paged_root).


##### `MondayConnector.flatten`  (lines 325–366)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes raw monday records into a more consistent form for the rest of the system. It gives common fields predictable names, such as `body`, `author`, `created_at`, and `parent_external_id` where they make sense.

**Data flow**: It receives one raw record and the stream it came from. Depending on the stream, it copies the original fields and adds or standardizes fields: users keep name and email, boards and workspaces get an API-style URL, items expose their state as `status`, updates choose a readable body and author, and activity logs point back to their board. It returns the adjusted record without changing external state.

**Call relations**: After `paginate` has yielded raw monday records, the wider connector framework can call this to prepare each record for storage or indexing. It uses a small safe-dictionary helper when reading nested creator information from updates.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/wrike.py`

`io_transport` · `source sync`

Wrike’s API sends results in pages, much like a long report split across several sheets. This connector knows which Wrike endpoints to visit, how to move from one page to the next, and how to shape the returned records so the rest of the system can understand them consistently.

The file first defines the Wrike streams: contacts, folders, tasks, comments, workflows, and custom fields. A stream is one kind of thing to sync. Some streams also name an update date field, which lets the connector skip older records during an incremental sync. Wrike does not provide a dependable “only send changes since this time” filter, so the connector fetches pages and then locally drops records whose update time is not newer than the saved cursor.

The main class, `WrikeConnector`, extends the shared REST connector used by source integrations. Its `paginate` method reads Wrike’s `{data: [...], nextPageToken}` response shape and follows `nextPageToken` until there are no more pages. If Wrike refuses access with a 401 or 403 response, the stream is skipped with a clear explanation instead of crashing the whole sync.

The `flatten` method then adds common, friendly fields such as `name`, `email`, `created_at`, `due_date`, and parent links. This makes Wrike’s varied record shapes easier for downstream indexing and recall.

#### Function details

##### `_profile_email`  (lines 54–64)

```
def _profile_email(record: dict[str, Any]) -> str | None
```

**Purpose**: This helper pulls the first usable email address out of a Wrike contact record. Wrike stores emails inside a list of profile objects, so this function hides that nested shape from the rest of the connector.

**Data flow**: It receives one Wrike record as a dictionary. It looks for a `profiles` value, checks that it is a list, then scans each profile for a non-empty string called `email`. It returns the first email it finds, or `None` if the record has no usable email.

**Call relations**: When `WrikeConnector.flatten` prepares a contact record, it calls `_profile_email` so the final flattened contact has a simple top-level `email` field instead of making later code understand Wrike’s nested profile format.

*Call graph*: called by 1 (flatten).


##### `WrikeConnector.paginate`  (lines 72–96)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Wrike stream page by page. It also applies the saved update cursor when possible, so incremental syncs keep only records that look newer than the last successful run.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. It checks that the stream is one this connector can actually read, asks the shared REST paging helper to fetch Wrike pages from the right endpoint, optionally filters each page by the stream’s cursor field, and yields non-empty batches of records. If Wrike returns 401 or 403, it turns that refusal into a `StreamSkipped` signal; other HTTP errors are allowed to continue upward.

**Call relations**: The broader sync runner calls this method when it needs records for a Wrike stream. `paginate` delegates the low-level page fetching to the shared cursor-page helper supplied by the base REST connector. If the requested stream is not implemented, or Wrike refuses access, it raises `StreamSkipped` so the runner can skip that stream rather than treating it as a normal data batch.

*Call graph*: calls 1 internal fn (__init__).


##### `WrikeConnector.flatten`  (lines 98–136)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method reshapes raw Wrike records into a more uniform form for indexing and recall. It keeps the original data, but adds commonly expected fields such as a readable name, creation time, status, due date, author, or parent task link depending on the stream.

**Data flow**: It receives one Wrike record and the stream it came from. For contacts, it builds a display name and extracts an email. For folders, it uses the title as the name and adds an API URL. For tasks, it pulls task title, status, due date, and creation time, including looking inside the nested `dates` object safely. For comments, it maps comment text, author, creation time, and related task ID into simpler field names. For streams without special rules, it returns the record unchanged.

**Call relations**: After records are fetched by the sync flow, this method is used to make each record easier for the rest of the system to consume. It calls `_profile_email` for contact emails and `dict_or_empty` when reading task dates, so missing or oddly shaped nested data does not break the flattening step.

*Call graph*: calls 1 internal fn (_profile_email); 1 external calls (dict_or_empty).


### Repository collaboration
Connector support for syncing GitHub organizations, repositories, issues, pull requests, commits, comments, and users.

### `extensions/sources/ufo_ext_sources/github.py`

`io_transport` · `during GitHub source sync`

This connector is the bridge between GitHub and the project’s source-sync system. Without it, the system would not know which GitHub API addresses to call, how to follow GitHub’s page-by-page responses, or how to resume a long sync without rereading everything from the beginning.

The file first defines the GitHub streams the system knows about, such as repositories, issues, users, commits, and workflows. A stream is a named kind of data, with details like its unique ID field and, when possible, a time field used as a bookmark. It then maps runnable streams to GitHub API paths.

The main class, `GitHubConnector`, builds a GitHub-ready HTTP client, lists the runnable streams, and fetches records. Most GitHub data is discovered through organizations: it asks GitHub which organizations the credential can see, lists those organizations’ repositories, then reads repo-specific streams for each repository. This is like finding all rooms in a building before collecting papers from each room.

The connector is careful about long-running syncs. Some streams can be read from oldest changed record to newest using GitHub’s `since` filter. Others, like commits and events, arrive newest first, so the connector uses bounds and watermarks to avoid losing records if new ones appear during a run. If GitHub says a repository or organization is inaccessible, the connector skips only that piece when safe; if the credential cannot list organizations at all, it skips the stream rather than treating the whole run as broken.

#### Function details

##### `_stream`  (lines 60–78)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='created_at', ordering: Ordering=Ordering.none, canonical:
```

**Purpose**: Creates a small stream description for one kind of GitHub data, such as issues or commits. This keeps the long stream catalog compact and consistent.

**Data flow**: It receives a stream name and optional details such as the GitHub object name, primary key, bookmark field, creation-time field, ordering style, and whether it is a main supported stream. It fills in sensible defaults, then returns a `StreamSpec`, which is the system’s standard description of a syncable stream.

**Call relations**: This helper is used while building the `ALL_STREAMS` catalog near the top of the file. It hands its gathered stream settings to `StreamSpec`, so later methods such as `GitHubConnector.streams` and `GitHubConnector.paginate` can decide what can be read and how to read it.

*Call graph*: 1 external calls (__init__).


##### `GitHubConnector.streams`  (lines 171–174)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns only the GitHub streams that this connector can actually fetch today. Some streams are listed for future compatibility, but are not runnable until an API path is wired in.

**Data flow**: It reads the connector’s full stream list and the `_PATHS` table of implemented GitHub API paths. It filters out any stream whose name has no path, and returns the remaining stream descriptions.

**Call relations**: The sync framework asks this method what streams are available. This method acts as the gate between the broad catalog and the fetch logic in `GitHubConnector.paginate`, which expects every runnable stream to have a known GitHub path.


##### `GitHubConnector._make_client`  (lines 176–180)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds an HTTP client that is ready to talk to GitHub’s API. It adds the GitHub-specific headers GitHub expects for the API version and response format.

**Data flow**: It receives a base URL and a resolved credential. It first asks the parent `RestConnector` to create the authenticated client, then adds GitHub’s `Accept` and API version headers. It returns the prepared asynchronous HTTP client.

**Call relations**: The base connector provides the general authenticated client. This method adds the GitHub-specific finishing touches so later calls from pagination helpers use the correct GitHub API behavior.


##### `GitHubConnector.flatten`  (lines 182–198)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Tidies certain GitHub records before they are stored or rendered. It removes bulky nested repository objects from pull requests and makes stargazer user data easier to read.

**Data flow**: It receives one GitHub record and the stream it belongs to. For `stargazers`, it merges the nested `user` object into the top-level record when possible. For `pull_requests`, it keeps the `head` and `base` information but drops their nested `repo` objects. For all other streams, it returns the record unchanged.

**Call relations**: The broader sync system calls this after records are fetched. It sits between raw GitHub API responses and the downstream storage or page-rendering layer, making selected records flatter and less noisy.


##### `GitHubConnector.paginate`  (lines 200–256)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Fetches pages of records for a chosen GitHub stream. This is the connector’s main reading routine: it decides whether to read organization data, repository data, or a simple direct endpoint.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is a saved bookmark from an earlier sync. It looks up the stream’s API path, builds page parameters, then chooses a route: repository catalogs come from organization repo pages; repo-scoped streams are walked repository by repository with resume support; organization-scoped streams are read once per organization; simple streams are read directly. It yields pages of records, or richer stream pages when the partition walker is tracking per-repository progress.

**Call relations**: The sync runner calls this when it needs data for a stream. Inside, it calls helpers such as `_iter_granted_org_repo_pages`, `_iter_user_orgs`, `_paginate_link_header`, and `_enrich_users`. For repo-scoped streams it creates a `PartitionWalk`, which coordinates cursor state across many repositories.

*Call graph*: calls 4 internal fn (_enrich_users, _iter_granted_org_repo_pages, _iter_user_orgs, _paginate_link_header); 2 external calls (__init__, Semaphore).


##### `GitHubConnector.paginate.repos`  (lines 218–220)

```
async def repos() -> AsyncIterator[str]
```

**Purpose**: Provides the list of repositories for a repository-scoped sync. It gives `PartitionWalk` one repository key at a time.

**Data flow**: It takes no direct outside input beyond the surrounding `paginate` client. It asks `_iter_user_repos` for `(owner, repo)` pairs, joins each pair into an `owner/repo` string, and yields those strings.

**Call relations**: This nested helper exists only inside `GitHubConnector.paginate`. `PartitionWalk` calls on it when it needs to know which repository partitions to walk, and it delegates discovery to `_iter_user_repos`.

*Call graph*: calls 1 internal fn (_iter_user_repos).


##### `GitHubConnector.paginate.repo_pages`  (lines 222–223)

```
def repo_pages(repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Connects one repository partition to the actual page-fetching logic. It is a small adapter that matches what `PartitionWalk` expects.

**Data flow**: It receives a repository key such as `owner/repo` and a partition bound, which is the current resume window for that repository. It passes the client, stream, path, repository key, and bound into `_repo_pages`, then returns that asynchronous page iterator.

**Call relations**: This nested helper is supplied to `PartitionWalk` by `GitHubConnector.paginate`. When the walker is ready to read one repository, it calls this helper, which hands off the real work to `_repo_pages`.

*Call graph*: calls 1 internal fn (_repo_pages).


##### `GitHubConnector._repo_pages`  (lines 258–306)

```
async def _repo_pages(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]
```

**Purpose**: Reads one repository’s pages for one stream, while applying the right resume rules. It is where repo-specific filters, skip behavior, and cursor bounds are turned into concrete GitHub API requests.

**Data flow**: It receives the client, stream, API path template, repository key, and a `PartitionBound`, which says where this repository should resume. It fills in the owner and repo in the path, adds parameters such as `state=all`, `since`, or `until` when appropriate, and fetches GitHub pages through `_paginate_link_header`. It filters issues to exclude pull requests, trims newest-first event pages when GitHub cannot filter them server-side, calculates the page’s highest and lowest cursor values, and yields `WalkPage` objects. If GitHub reports that this repository is gone or unreadable in expected ways, it raises `PartitionSkipped` so the sync can move on.

**Call relations**: It is called through the nested `GitHubConnector.paginate.repo_pages` adapter as part of a `PartitionWalk`. It relies on `_paginate_link_header` for network paging and `_cursor_bounds` to report progress back to the walker.

*Call graph*: calls 2 internal fn (_paginate_link_header, _cursor_bounds); called by 1 (repo_pages); 2 external calls (__init__, __init__).


##### `GitHubConnector._iter_user_repos`  (lines 308–316)

```
async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]
```

**Purpose**: Finds the repositories this connector should sync by looking through the organizations the credential can access. It avoids using GitHub’s broader personal repository list because this connector treats the organization grant as the source of truth.

**Data flow**: It receives the HTTP client. It asks `_iter_granted_org_repo_pages` for pages of repository records, extracts a clean `(owner, repo)` pair from each record with `_repo_identity`, and yields only records that can be identified.

**Call relations**: The nested `GitHubConnector.paginate.repos` helper calls this when repo-scoped streams need their partition list. This function depends on `_iter_granted_org_repo_pages` for discovery and `_repo_identity` for safely interpreting GitHub’s repository records.

*Call graph*: calls 2 internal fn (_iter_granted_org_repo_pages, _repo_identity); called by 1 (repos).


##### `GitHubConnector._iter_granted_org_repo_pages`  (lines 318–337)

```
async def _iter_granted_org_repo_pages(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]
```

**Purpose**: Lists repository pages for each organization the credential can see, while skipping archived repositories and forks. This keeps the sync focused on active organization-owned repositories.

**Data flow**: It receives the HTTP client. It first gets organization logins from `_iter_user_orgs`, then calls each organization’s repository endpoint through `_paginate_link_header`. From each page, it removes archived repositories and forks, yields the remaining repositories with the organization name, and skips organizations that GitHub says are forbidden, missing, or gone.

**Call relations**: This helper is used both by `GitHubConnector.paginate` for the `repositories` stream and by `_iter_user_repos` for repo-scoped streams. It builds on `_iter_user_orgs` for the organization list and `_paginate_link_header` for following GitHub pages.

*Call graph*: calls 2 internal fn (_iter_user_orgs, _paginate_link_header); called by 2 (_iter_user_repos, paginate).


##### `GitHubConnector._iter_user_orgs`  (lines 339–360)

```
async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Lists the organization logins available to the current GitHub credential. Because most streams start from organizations, this is the root discovery step for the connector.

**Data flow**: It receives the HTTP client and calls `/user/orgs` through `_paginate_link_header`. From each returned organization record, it reads the `login` field and yields it if it is a non-empty string. If GitHub refuses organization enumeration with a scope-related forbidden response, it raises `StreamSkipped` so the run records a controlled skip instead of a hard failure.

**Call relations**: `GitHubConnector.paginate` calls this for organization-scoped streams, and `_iter_granted_org_repo_pages` calls it before listing repositories. It hands organization names to the rest of the connector’s fan-out process.

*Call graph*: calls 2 internal fn (__init__, _paginate_link_header); called by 2 (_iter_granted_org_repo_pages, paginate).


##### `GitHubConnector._enrich_users`  (lines 362–382)

```
async def _enrich_users(self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore) -> list[dict[str, Any]]
```

**Purpose**: Replaces simple organization-member records with fuller public GitHub user records when possible. This can add useful public fields such as name or email.

**Data flow**: It receives the client, a page of user-like member records, and a semaphore, which is a limit that stops too many user lookups from running at once. It starts one lookup task per member using its inner `one` function, waits for them all with `asyncio.gather`, and returns a new list where each member is replaced by the fuller user record when GitHub provides one.

**Call relations**: `GitHubConnector.paginate` calls this only for the `users` stream after reading organization members. It uses the nested `GitHubConnector._enrich_users.one` function to do each individual lookup while the semaphore keeps the amount of parallel work controlled.

*Call graph*: called by 1 (paginate); 1 external calls (gather).


##### `GitHubConnector._enrich_users.one`  (lines 368–380)

```
async def one(member: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Fetches the fuller public profile for one GitHub member record. If the member has no usable login, or GitHub cannot find the user, it keeps the original record.

**Data flow**: It receives one member dictionary from the surrounding `_enrich_users` call. It reads the `login` value, and if it is valid, waits for permission from the semaphore before calling `/users/{login}`. If the response body is a dictionary, it returns that fuller user record; otherwise it returns the original member. A 404 response is treated as harmless and also returns the original member.

**Call relations**: This inner helper is launched many times by `_enrich_users`, one task per user in the page. It performs the actual per-user network lookup that lets the outer function return enriched user pages to `GitHubConnector.paginate`.


##### `GitHubConnector._paginate_link_header`  (lines 384–392)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Follows GitHub’s standard page links and yields each page as a list of records. GitHub uses `Link` headers to point to the next page, and this helper centralizes that behavior.

**Data flow**: It receives the client, an API path, and optional query parameters. It delegates to the base link-header paging helper with the GitHub page size and `_parse_records` as the response parser. For each parsed page, it yields the list of records.

**Call relations**: Many higher-level readers call this: `paginate`, `_repo_pages`, `_iter_user_orgs`, and `_iter_granted_org_repo_pages`. It is the shared doorway from connector logic into GitHub’s paginated HTTP responses.

*Call graph*: called by 4 (_iter_granted_org_repo_pages, _iter_user_orgs, _repo_pages, paginate).


##### `_parse_records`  (lines 395–399)

```
def _parse_records(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Turns a GitHub HTTP response into a list of record dictionaries. It quietly treats empty responses or non-list responses as no records.

**Data flow**: It receives an HTTP response. If the response body is empty, it returns an empty list. Otherwise it parses the JSON body, returns it when it is a list, and returns an empty list for any other JSON shape.

**Call relations**: `GitHubConnector._paginate_link_header` gives this function to the base paging helper as the record parser. It keeps the rest of the connector working with a predictable list-of-records shape.

*Call graph*: 1 external calls (json).


##### `_repo_identity`  (lines 402–417)

```
def _repo_identity(record: dict[str, Any], *, fallback_owner: str | None=None) -> tuple[str, str] | None
```

**Purpose**: Extracts a repository’s owner and name from a GitHub repository record. It accepts the different shapes GitHub may return and falls back to the organization name when needed.

**Data flow**: It receives a repository record and an optional fallback owner. It first tries `full_name`, such as `owner/repo`. If that is not usable, it tries the nested owner login plus the repository `name`. If that also fails but there is a repository name and fallback owner, it uses those. It returns an `(owner, repo)` pair or `None` if the record cannot be safely identified.

**Call relations**: `GitHubConnector._iter_user_repos` calls this for every repository record found through organization discovery. Its output becomes the repository partition key later used by `GitHubConnector.paginate.repos` and `_repo_pages`.

*Call graph*: called by 1 (_iter_user_repos).


##### `_cursor_bounds`  (lines 420–430)

```
def _cursor_bounds(page: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]
```

**Purpose**: Finds the newest and oldest bookmark values on a page of records. These bounds help the sync system remember progress and resume safely.

**Data flow**: It receives a page of records and the name of the cursor field, which may be a nested path like `commit.committer.date`. If there is no cursor field, it returns `(None, None)`. Otherwise it reads string cursor values from the page using `get_path`, then returns the maximum and minimum values found. If no usable values exist, it returns `(None, None)`.

**Call relations**: `GitHubConnector._repo_pages` calls this after preparing a non-empty page. The resulting high and low values are placed into a `WalkPage`, which lets `PartitionWalk` update watermarks and resume windows for each repository.

*Call graph*: called by 1 (_repo_pages); 1 external calls (get_path).


### Operational incidents and errors
Connectors that ingest incident-response, on-call, service, error-tracking, release, and event records from operations systems.

### `extensions/sources/ufo_ext_sources/pagerduty.py`

`io_transport` · `during source sync when PagerDuty streams are read`

PagerDuty is an external incident-management service, so the system needs a connector that knows PagerDuty’s API rules. This file is that connector. It lists the PagerDuty record types the system can import, including incidents, services, users, teams, escalation policies, schedules, on-calls, and incident notes.

The main class, `PagerDutyConnector`, is read-only. It does not create or update anything in PagerDuty. It only asks PagerDuty for data and yields it in batches. PagerDuty sends long result sets in pages, like a book split into numbered chunks. This connector follows PagerDuty’s `offset` and `limit` paging style and keeps reading while PagerDuty says there is more data.

Some streams need special treatment. Incidents can be read incrementally using a cursor, which is a saved timestamp that means “only give me things updated after this point.” Incident notes are not a top-level list in PagerDuty, so the connector first reads incidents, then asks PagerDuty for the notes attached to each incident.

If PagerDuty refuses access with an authentication or permission error, the connector marks that stream as skipped instead of crashing the whole sync. It also sets PagerDuty’s required `Accept` header so the API responds using the expected version.

#### Function details

##### `PagerDutyConnector._make_client`  (lines 74–77)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to PagerDuty and adds PagerDuty’s required API version header. The header tells PagerDuty which format of responses this connector expects.

**Data flow**: It receives a base URL and a credential supplied by the surrounding runner. It asks the parent connector class to create the normal authenticated HTTP client, then adds the PagerDuty-specific `Accept` header. It returns the prepared client, ready to make API requests.

**Call relations**: This is part of the connector setup before any stream is read. The rest of the paging functions rely on the client it prepares so their requests go to PagerDuty with the right authentication and API version.


##### `PagerDutyConnector._offset_pages`  (lines 79–99)

```
async def _offset_pages(self, client: httpx.AsyncClient, stream: StreamSpec, *, params: dict[str, Any] | None=None, cursor: str | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one PagerDuty list endpoint page by page. It is the shared helper for streams that use PagerDuty’s normal offset-based pagination.

**Data flow**: It receives an HTTP client, a stream description, optional request parameters, and an optional cursor timestamp. It requests pages from the endpoint named by the stream, asks for up to 100 records per page, and follows PagerDuty’s `more` flag to continue. If a cursor is present and the stream has a cursor field, it filters out records at or before that cursor. It yields only non-empty batches of records.

**Call relations**: This is the common paging path used by `PagerDutyConnector._incidents` and by `PagerDutyConnector.paginate` for ordinary streams like users, teams, services, schedules, and on-calls. It hands batches upward so the sync engine can process them without caring about PagerDuty’s pagination details.

*Call graph*: called by 2 (_incidents, paginate).


##### `PagerDutyConnector._incidents`  (lines 101–113)

```
async def _incidents(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads PagerDuty incidents in update-time order, optionally starting after a saved cursor. It exists because incidents support incremental syncing, which avoids rereading everything every time.

**Data flow**: It receives an HTTP client and an optional cursor. It builds request parameters that sort incidents by `updated_at` from oldest to newest, and if a cursor exists, sends it to PagerDuty as `since`. It then delegates the actual page fetching and final cursor filtering to `_offset_pages`. It yields pages of incident records.

**Call relations**: This is called directly by `PagerDutyConnector.paginate` when the selected stream is incidents. It is also called by `PagerDutyConnector._incident_notes`, because notes must be discovered by walking through incidents first.

*Call graph*: calls 1 internal fn (_offset_pages); called by 2 (_incident_notes, paginate).


##### `PagerDutyConnector._incident_notes`  (lines 115–128)

```
async def _incident_notes(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads notes attached to PagerDuty incidents. PagerDuty does not expose these as one simple global list, so the connector must visit each incident and then fetch that incident’s notes.

**Data flow**: It receives an HTTP client and an optional cursor. First it reads incidents without using the note cursor. For each incident with a valid ID, it calls PagerDuty’s incident-notes endpoint. It extracts the `notes` list from the response, filters notes whose `created_at` value is not newer than the cursor, and adds the incident ID as context so each note can still be traced back to its incident. It yields non-empty batches of note records.

**Call relations**: This is called by `PagerDutyConnector.paginate` when the requested stream is `incident_notes`. It relies on `_incidents` to find incidents, uses `records_at` to pull the notes list out of PagerDuty’s response, and uses `with_context` to attach the parent incident ID before handing the notes back to the sync flow.

*Call graph*: calls 1 internal fn (_incidents); called by 1 (paginate); 2 external calls (records_at, with_context).


##### `PagerDutyConnector.paginate`  (lines 130–164)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main routing point for reading a PagerDuty stream. Given a stream name, it chooses the right reading strategy and yields record batches to the rest of the system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If the stream is incidents, it uses the incident-specific incremental reader. If it is incident notes, it uses the note reader that fans out through incidents. If it is one of the ordinary PagerDuty list streams, it uses the shared offset-page reader. If the stream is unknown, it reports the stream as skipped. If PagerDuty responds with a 401 or 403 refusal, it converts that into a skip with a clear permission-related message; other HTTP errors are allowed to fail normally.

**Call relations**: The broader sync framework calls this when it wants records for a PagerDuty stream. This function then hands work to `_incidents`, `_incident_notes`, or `_offset_pages` depending on the stream. It also uses `StreamSkipped` to tell the runner that a stream could not be read because it is unsupported or PagerDuty refused access, rather than treating every refusal as a full connector failure.

*Call graph*: calls 4 internal fn (__init__, _incident_notes, _incidents, _offset_pages).


### `extensions/sources/ufo_ext_sources/sentry.py`

`io_transport` · `source sync / API fetching`

This connector is the system’s map for walking through a Sentry account. Sentry data is arranged like a tree: organizations contain projects, and projects contain things like issues and events. Some data, such as members and releases, belongs to an organization. This file knows that shape and fetches each kind of data from the right Sentry API endpoint.

The main entry point is `SentryConnector.paginate`, which is asked for one stream of records at a time, such as “issues” or “projects.” It then calls a helper suited to that stream. For streams that depend on context, it first discovers the parent objects. For example, to fetch issues, it first lists projects, then asks Sentry for issues inside each project. It adds helpful labels like `organization_slug` and `project_slug` to each returned row, so later parts of the system know where that row came from.

Sentry sends large result sets in pages. Like a bookmark in a long book, each response may include a cursor in the HTTP `Link` header telling the connector where the next page starts. `_paged_list` follows those cursors until there are no more pages. If Sentry refuses access because the credential is missing permission or invalid, the connector skips that stream instead of crashing the whole sync.

#### Function details

##### `_sentry_next_cursor`  (lines 76–81)

```
def _sentry_next_cursor(headers: httpx.Headers) -> str | None
```

**Purpose**: This small helper looks at Sentry’s HTTP response headers and finds the cursor for the next page of results, if one exists. A cursor is like a bookmark that tells the next request where to continue.

**Data flow**: It receives the response headers from Sentry. It reads the `link` or `Link` header, searches for the special `rel="next"` entry that also says more results are available, and extracts the cursor text. It returns that cursor string, or `None` if there is no next page.

**Call relations**: It is used by `SentryConnector._paged_list` after each API response. `_paged_list` depends on this helper to know whether it should make another request or stop.

*Call graph*: called by 1 (_paged_list); 1 external calls (get).


##### `SentryConnector.paginate`  (lines 89–128)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s main dispatcher for reading one Sentry stream. Given a requested stream, it chooses the right helper to fetch that kind of Sentry data and yields records in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching helper, filters some streams by the cursor when needed, and yields pages of records. If the stream is unknown or Sentry denies access with an authorization error, it raises `StreamSkipped`, which tells the rest of the system to move on without treating it as a full failure.

**Call relations**: The sync framework calls this when it wants records from Sentry. `paginate` then hands work to `_organizations`, `_projects`, `_members`, `_issues`, `_events`, or `_releases` depending on the requested stream. It is the front door that keeps the rest of the system from needing to know Sentry’s endpoint layout.

*Call graph*: calls 7 internal fn (__init__, _events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._paged_list`  (lines 130–147)

```
async def _paged_list(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches every page from one Sentry API list endpoint. It hides the repeated work of making a request, reading the JSON response, yielding valid records, and following the next-page cursor.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It repeatedly sends a GET request to that path, adding a cursor parameter when Sentry has supplied one. From each response, it keeps only list items that are dictionaries, yields them as a page, then asks `_sentry_next_cursor` whether another page exists. It stops when no cursor is found.

**Call relations**: All stream-specific helpers use this function as their common page reader. `_organizations`, `_projects`, `_members`, `_issues`, `_events`, and `_releases` each decide which endpoint to call, then rely on `_paged_list` to do the actual repeated HTTP paging.

*Call graph*: calls 1 internal fn (_sentry_next_cursor); called by 6 (_events, _issues, _members, _organizations, _projects, _releases).


##### `SentryConnector._organizations`  (lines 149–153)

```
async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches all organizations visible to the Sentry credential. Organizations are the top-level containers that other Sentry data often depends on.

**Data flow**: It receives an HTTP client. It asks `_paged_list` for every page from Sentry’s organizations endpoint, collects all returned organization records into one list, and returns that list.

**Call relations**: `paginate` calls this directly when syncing the organizations stream. `_members` and `_releases` also call it first because they need to know which organizations to visit before fetching organization-specific data.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_members, _releases, paginate).


##### `SentryConnector._projects`  (lines 155–159)

```
async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches all projects visible to the Sentry credential. Projects are needed before the connector can fetch project-level data such as issues and events.

**Data flow**: It receives an HTTP client. It asks `_paged_list` for every page from Sentry’s projects endpoint, gathers the project records into one list, and returns that list.

**Call relations**: `paginate` calls this directly for the projects stream. `_issues` and `_events` call it first so they can loop through each project and request the data that belongs to that project.

*Call graph*: calls 1 internal fn (_paged_list); called by 3 (_events, _issues, paginate).


##### `SentryConnector._members`  (lines 161–167)

```
async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches organization members from Sentry. Because members are listed under each organization, it first finds the organizations and then reads each organization’s member list.

**Data flow**: It receives an HTTP client. It calls `_organizations`, looks at each organization’s slug, skips any organization without a usable slug, and then reads that organization’s members through `_paged_list`. Before yielding each page, it adds `organization_slug` to every member record so the record keeps its source context.

**Call relations**: `paginate` calls this when the members stream is requested. This function depends on `_organizations` for the list of places to visit and on `_paged_list` for the actual Sentry paging. It uses `with_context` to attach the organization label before handing records back upward.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._issues`  (lines 169–183)

```
async def _issues(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches Sentry issues for every accessible project. Issues are project-level records, so the connector must first discover projects and then query each project’s issue endpoint.

**Data flow**: It receives an HTTP client and an optional cursor. It calls `_projects`, extracts each project’s organization slug and project slug, and skips projects where those labels are missing. If a cursor is present, it asks Sentry only for issues whose `lastSeen` value is newer than that cursor. It yields each page of issue records after adding both `organization_slug` and `project_slug`.

**Call relations**: `paginate` calls this for the issues stream. `_issues` relies on `_projects` to know which project endpoints exist, `_paged_list` to fetch each endpoint page by page, and `with_context` to preserve where each issue came from.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._events`  (lines 185–199)

```
async def _events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches Sentry event records for every accessible project. Events are also project-level data, so the function walks through projects before reading their events.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all projects from `_projects`, extracts the needed organization and project slugs, and skips entries that do not provide them. If a cursor is present, it asks Sentry for events with an `event.timestamp` newer than that cursor. It yields pages of events with `organization_slug` and `project_slug` attached.

**Call relations**: `paginate` calls this for the events stream. This helper follows the same project-by-project pattern as `_issues`, using `_paged_list` for HTTP paging and `with_context` to label each event with its parent organization and project.

*Call graph*: calls 2 internal fn (_paged_list, _projects); called by 1 (paginate); 1 external calls (with_context).


##### `SentryConnector._releases`  (lines 201–212)

```
async def _releases(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches Sentry releases for every accessible organization. Releases are organization-level data, so the connector visits each organization’s releases endpoint.

**Data flow**: It receives an HTTP client and an optional cursor. It calls `_organizations`, reads the slug for each organization, skips organizations without a valid slug, and fetches releases through `_paged_list`. If a cursor is present, it keeps only releases whose `dateCreated` value is newer than that cursor. It yields non-empty pages after adding `organization_slug` to each release record.

**Call relations**: `paginate` calls this when the releases stream is requested. It uses `_organizations` to find the organizations to visit, `_paged_list` to read each organization’s releases, and `with_context` so later stages know which organization each release belongs to.

*Call graph*: calls 2 internal fn (_organizations, _paged_list); called by 1 (paginate); 1 external calls (with_context).
