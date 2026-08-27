# Workspace Object APIs and Portal State Views  `stage-8`

This stage is shared support for the whole system. It is the “front desk” where tools, agents, and the portal ask to see or change workspace records in a safe way. The central object system routes each request, checks names, ownership, visibility, and allowed actions, while object names and object scope keep identities and agent attribution consistent.

Built-in object kinds cover the workspace itself, members, credentials, installed extensions, agents, and conversations. These give safe views of the roster, seats, secret slots, extension capabilities, agent settings, and past chats without exposing private data or allowing the wrong edits.

Extension files plug more record types into the same front desk. Memory exposes stored memories and profiles as read-only records. Monitors show command watches that can be deleted to stop them. Scheduled tasks and report digests expose recurring work, waiting tools, and generated digest results. Sites expose hosted websites and add a “Sites” section to conversations. Sources and gbrain sources expose synced pages and Markdown source collections. Todos expose conversation checklists. Together, these files make many different features feel like one consistent workspace object API.

## Files in this stage

### People and Conversation Objects
User-facing objects for agents, conversations, memories, and member profiles establish the main personal and conversational records exposed through the workspace API.

### `core/src/ufo/kinds/agents.py`

`domain_logic` · `request handling`

An agent in this system is not just a prompt. It is a saved workspace item with a name, model choice, internet policy, reasoning setting, sandbox size, portal visibility, icon, owner, and optional input/output rules for spawned runs. This file turns that idea into an object kind called "agent" so the rest of the system can treat agents like other workspace objects.

The main job here is to protect the agent table from unsafe or confusing changes. New agents must be created by a signed-in speaking member and must have a non-empty prompt. Edits are allowed only for the owner or a workspace admin. The main agent is special: everyone can reach it, its visibility cannot be changed away from workspace-wide, and it cannot be archived.

Deleting an agent does not erase its history. Instead, this file archives it: the agent disappears from the live name list and its old name becomes available, but its conversations, spending record, grants, and connected accounts remain tied to the same database row. That is like taking a shop sign down while keeping the business records in storage. A separate restore tool can bring the archived agent back by its stable id, under its old name or a new available name.

#### Function details

##### `_effective_model`  (lines 71–76)

```
def _effective_model(ctx: ToolContext, stored: str) -> str
```

**Purpose**: Reports the real model an agent is currently using when its saved setting says "auto". This matters because readers want to see the concrete model, while the stored setting must stay as "auto" so future runs can keep following deployment defaults.

**Data flow**: It receives the current tool context and the model value saved on the agent row. If the saved value is the special "auto" marker, it reads the already-resolved model from the current agent in the context; otherwise it returns the saved model unchanged.

**Call relations**: Agent summaries and status reports call this when showing model information. It keeps display code honest without changing the stored agent configuration.

*Call graph*: called by 2 (_status, _agent_summary).


##### `AgentSpec._declared_schema`  (lines 167–172)

```
def _declared_schema(cls, value: dict[str, JsonValue] | None, info: ValidationInfo) -> dict[str, JsonValue] | None
```

**Purpose**: Checks that a custom input or output schema is valid before it is accepted. A schema here is a JSON-based description of what shape data must have when an agent is spawned or when it returns a final answer.

**Data flow**: It receives a proposed schema value and validation context from Pydantic, the library used to check structured input. If the schema is present, it passes it to the contract checker; if the checker accepts it, the same schema comes back unchanged.

**Call relations**: This runs automatically while an AgentSpec is being built from user input. It hands the detailed schema rules to the shared contract-checking code so invalid spawn contracts are rejected before reaching the database.

*Call graph*: 1 external calls (check_declared_schema).


##### `_known_model`  (lines 175–192)

```
def _known_model(ctx: ToolContext, model: str, reasoning: ReasoningEffort) -> None
```

**Purpose**: Rejects an agent configuration that names a model this deployment cannot use, or that disables reasoning on a model that requires it. This prevents saving an agent that would fail every time someone tried to run it later.

**Data flow**: It receives the tool context, a requested model id, and a reasoning setting. It resolves "auto" to the deployment’s actual automatic model, checks the model registry in the context, and raises an error if the choice is not usable; otherwise it produces no output.

**Call relations**: Agent creation and editing both call this before writing model settings. It acts as the last safety check while the user can still get a clear explanation of what is wrong.

*Call graph*: called by 2 (_create, _mutate).


##### `_agent_summary`  (lines 195–200)

```
def _agent_summary(ctx: ToolContext, row: sa.Row) -> str
```

**Purpose**: Builds the short human-readable summary shown for an agent in object lists. It tells readers whether the agent is live or archived, what model it uses, and whether public internet is allowed.

**Data flow**: It receives the tool context and a database row. It first gets the display model through _effective_model, then turns row fields like archive time, main-agent flag, and internet permission into one short text string.

**Call relations**: AgentObjects._agent_rows calls this while turning database rows into object-list rows. It depends on _effective_model so "auto" agents are described using the model they currently resolve to.

*Call graph*: calls 1 internal fn (_effective_model); called by 1 (_agent_rows).


##### `AgentObjects._admin_can_apply`  (lines 213–214)

```
def _admin_can_apply(self, old: AgentSpec, spec: AgentSpec) -> bool
```

**Purpose**: Says that workspace admins are allowed to apply any valid agent spec change once the general permission gate has let them through. It is a policy hook used by the shared object framework.

**Data flow**: It receives the old and new agent specifications. It does not inspect them and always returns true, meaning there is no extra admin-only restriction inside this file beyond the other validation rules.

**Call relations**: The shared member-owned object machinery calls this when deciding whether an admin may apply a change. More specific checks, such as the main agent staying workspace-visible, happen later in mutation logic.


##### `AgentObjects.list`  (lines 216–230)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns the agents visible to the current caller, normally only live agents unless the caller explicitly asks for archived ones. This is what powers browsing agents as workspace objects.

**Data flow**: It receives a tool context and a list query. If the query does not mention archived status, it adds a filter for live agents, checks whether the speaker is an admin, reads agent rows, filters out rows the caller should not see, and returns a paged object list.

**Call relations**: This is the public list path for the agent object kind. It calls _agent_rows to fetch rows, asks the context whether the speaker is an admin, and wraps the visible results with the shared object_page helper.

*Call graph*: calls 2 internal fn (_agent_rows, speaker_is_admin); 3 external calls (__init__, replace, object_page).


##### `AgentObjects._owned_rows`  (lines 232–233)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Provides the shared ownership framework with the live agent rows it needs for permission checks. It deliberately excludes archived agents from normal ownership lookups.

**Data flow**: It receives the tool context and asks _agent_rows for live-only rows. The result is a tuple of rows that include each agent’s name, owner, sharing status, and summary.

**Call relations**: The parent MemberOwnedObjects behavior uses this kind of method when resolving objects and enforcing owner/admin rules. This method delegates the actual database reading to _agent_rows.

*Call graph*: calls 1 internal fn (_agent_rows).


##### `AgentObjects._agent_rows`  (lines 235–270)

```
async def _agent_rows(self, ctx: ToolContext, *, live_only: bool) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Reads agent records from the workspace database and converts them into the common owned-row shape used by listing and permission logic. It includes archive metadata so archived agents can be shown and restored.

**Data flow**: It receives the tool context and a flag saying whether only live rows should be returned. It queries the current workspace’s agent table, optionally filters out archived rows, then turns each database row into an OwnedRow with a display name, summary, owner information, and fields such as id and archived_at.

**Call relations**: AgentObjects.list uses this for user-facing lists, and AgentObjects._owned_rows uses it for permission checks. It calls _agent_summary to produce readable summaries and uses the workspace transaction and current workspace id so it only reads the active workspace.

*Call graph*: calls 1 internal fn (_agent_summary); called by 2 (_owned_rows, list); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `AgentObjects._detail`  (lines 272–305)

```
async def _detail(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: Returns the full saved configuration for one live agent. This is what someone needs when they want to inspect an agent before changing it.

**Data flow**: It receives the context, an agent name, and owner information. It looks up the live row by name, returns nothing if it does not exist, otherwise copies the row’s settings into an AgentSpec and adds creation/update times plus a link to the main agent for non-main agents.

**Call relations**: The shared object framework calls this when a caller asks for object details. It depends on _row for the database lookup, then builds the standard ObjectDetail response used by object APIs.

*Call graph*: calls 1 internal fn (_row); 4 external calls (__init__, __init__, __init__, __init__).


##### `AgentObjects._status`  (lines 307–323)

```
async def _status(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a compact status view for one live agent, focused on identity and runtime facts rather than the whole editable spec. It is useful for quick checks such as whether an agent is the main one and what model it currently resolves to.

**Data flow**: It receives the context, an agent name, and owner information. It reads the row, returns nothing if the row is missing, and otherwise returns a small dictionary containing main-agent status, effective model, owner id, and provisioning metadata.

**Call relations**: The shared object system calls this for status-style reads. It uses _row to find the agent and _effective_model so status reports show the real model behind an "auto" setting.

*Call graph*: calls 2 internal fn (_row, _effective_model).


##### `AgentObjects._apply_owned`  (lines 325–336)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Chooses whether an object apply operation means "create a new agent" or "edit an existing agent". This is the single write path for agent objects after ownership checks have passed.

**Data flow**: It receives the context, requested name, new spec, optional old spec, and optional owner. If there is no old spec, it treats the request as a creation; otherwise it treats it as an update. It returns no value, but the database may be changed by the function it calls.

**Call relations**: The shared object framework calls this once it has decided the caller may apply the object. This method then hands off to _create or _mutate so each path can enforce its own rules.

*Call graph*: calls 2 internal fn (_create, _mutate).


##### `AgentObjects._mutate`  (lines 338–416)

```
async def _mutate(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Updates an existing live agent’s settings while preserving omitted fields that are meant to stay unchanged. It also enforces important rules, such as no empty prompt and no private visibility for the main agent.

**Data flow**: It receives the context, agent name, and requested spec. It loads the current row, validates the model, computes the next values by combining existing fields with supplied fields, checks whether anything actually changed, and writes the new values and update time to the database if needed.

**Call relations**: _apply_owned calls this for edits. It uses _row to find the current agent, _known_model to validate model settings, and the workspace transaction to perform the update in the current workspace.

*Call graph*: calls 2 internal fn (_row, _known_model); called by 1 (_apply_owned); 4 external calls (__init__, update, workspace_tx, ws_current).


##### `AgentObjects._create`  (lines 418–464)

```
async def _create(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Creates a brand-new non-main agent owned by the speaking member. It saves only the submitted configuration and does not copy grants, credentials, sources, or derived data from anywhere else.

**Data flow**: It receives the context, new name, and spec. It verifies there is a speaking member, requires a non-empty prompt, validates the model, chooses an icon if one was not supplied, inserts a new agent row with a fresh id, and reports name conflicts as a clear error.

**Call relations**: _apply_owned calls this when an apply operation names no existing agent. It uses _known_model for model safety, auto_agent_icon for a default portal icon, and the workspace database transaction to insert the row.

*Call graph*: calls 1 internal fn (_known_model); called by 1 (_apply_owned); 6 external calls (insert, select, workspace_tx, auto_agent_icon, ws_current, uuid4).


##### `AgentObjects.delete`  (lines 466–476)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Starts deletion of an agent object, but blocks deletion of the main agent. In this system, deleting an agent means archiving it rather than erasing it.

**Data flow**: It receives the context, agent name, and optional expected generation id. It looks up the row; if it is the main agent, it raises an unsupported-operation error. Otherwise it delegates to the shared delete flow, which will eventually archive the owned row.

**Call relations**: This overrides the shared delete behavior only to add the main-agent protection. After that check, it lets the parent object framework continue with permission checks and the owned delete path.

*Call graph*: calls 1 internal fn (_row); 1 external calls (__init__).


##### `AgentObjects._delete_owned`  (lines 478–506)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Archives a live agent after ownership or admin permission has been established. Archiving stops new turns from starting, removes the agent from live listings, and frees its old name while keeping its history.

**Data flow**: It receives the context, agent name, and owner information. It loads the row, rejects missing rows and the main agent, then updates the database row by replacing the live name with an archived internal name, storing the old name separately, setting archived_at, and updating the timestamp.

**Call relations**: The shared delete flow calls this once it knows the caller may delete the owned object. It relies on _row for the live lookup and writes the archive markers through the workspace transaction.

*Call graph*: calls 1 internal fn (_row); 5 external calls (__init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects._row`  (lines 508–548)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: Fetches the complete live database row for one agent name in the current workspace. Most read and write operations use it as their common way to find an agent.

**Data flow**: It receives an agent name. It queries the current workspace’s agent table for a non-archived row with that name, includes many saved settings and timestamps, and also includes the name of the workspace’s main agent as a related value. It returns the row or nothing if no live agent matches.

**Call relations**: Detail, status, mutation, deletion, and main-agent delete checks all call this. It is the central lookup helper that keeps those paths using the same definition of a live agent in the current workspace.

*Call graph*: called by 5 (_delete_owned, _detail, _mutate, _status, delete); 3 external calls (select, workspace_tx, ws_current).


##### `RestoreApplication.restore`  (lines 569–617)

```
async def restore(self, ctx: ToolContext, args: RestoreApplicationInput) -> ToolResult
```

**Purpose**: Brings an archived agent back into the live workspace under an available name. Only the archived agent’s owner or a workspace admin can do this.

**Data flow**: It receives the tool context and restore arguments containing an archived app id and target name. It checks that there is a speaking member, parses and validates the id and name, finds the archived row, verifies ownership or admin status, clears the archive fields, writes the new live name, and returns a short success message.

**Call relations**: This is the handler for the restore_application tool defined in the file. It uses the same workspace database table as archiving, checks admin status through the tool context, and returns a ToolResult with TextContent so the caller can see that the agent is live again.

*Call graph*: calls 1 internal fn (speaker_is_admin); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, validate_object_name, ws_current, UUID).


### `core/src/ufo/kinds/conversations.py`

`domain_logic` · `request handling`

A conversation in this system is not something users create through the object API. It is created by a “surface” — for example, a portal chat, Slack-like channel, or extension-provided chat area. This file gives those conversation records a standard object shape so other things, like artifacts or scheduled tasks, can link back to the chat they came from.

The main class, ConversationObjects, answers three kinds of read requests. It can list conversations visible inside a turn, fetch one conversation by its id, and provide portal-specific pages for a signed-in member outside a turn. Visibility is important: the code always checks the conversation’s audience, which is the set of people or shared scopes allowed to read it. Being an admin does not automatically reveal every private conversation here.

For details, the file turns database rows into friendly object rows and full object details. For status, it reads the stored transcript blob, converts messages into plain text lines like “user: hello”, and may write that text into the workspace as a small file. Like a library catalogue card, the object points to the conversation and its metadata, but it does not let callers rewrite the book. Create, update, and delete requests are rejected because surfaces own the lifetime of conversations.

#### Function details

##### `ConversationObjects.list`  (lines 73–75)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists conversation objects that the current tool context is allowed to read. It is used when a caller wants a page of past conversations for the selected agent.

**Data flow**: It receives a tool context, which carries the caller’s readable audience subjects, and a list query for paging or filtering. It asks the database for visible conversation rows, turns each row into a simple ObjectRow, then passes those rows through the common paging helper. The result is an ObjectPage ready to show to the caller.

**Call relations**: This is one of the public read entry points for the conversation object kind. It relies on _rows to get only visible database rows, uses _row to format each row, and then hands the formatted rows to object_page so the object system can return a normal paged listing.

*Call graph*: calls 2 internal fn (_rows, _row); 1 external calls (object_page).


##### `ConversationObjects.get`  (lines 77–79)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None
```

**Purpose**: Fetches the full details for one conversation by name, where the name is expected to be the conversation’s UUID string. It returns nothing if the id is invalid or the conversation is not visible to the caller.

**Data flow**: It receives the current tool context and an object name. It asks _find to parse the name and search among conversations visible to the context’s readable subjects. If a row is found, it converts that row into an ObjectDetail; otherwise it returns null.

**Call relations**: This is the single-object companion to list. It delegates the visibility and lookup work to _find, then uses _detail to build the richer view that includes the conversation spec, timestamps, and link to the agent.

*Call graph*: calls 2 internal fn (_find, _detail).


##### `ConversationObjects.member_page`  (lines 81–118)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the portal’s conversation list for a signed-in member. It shows the member’s own conversations and a limited set of other readable conversations, without widening access just because the member is an admin.

**Data flow**: It receives portal context information, the member id, an admin flag, and a page query. It opens a ConversationDirectory for the current workspace, looks up the selected agent id, asks for two groups of conversations: “mine” and “others”, skips entries without a title, marks which ones belong to the member, and formats them as rows. Finally it returns a paged ObjectPage.

**Call relations**: This is used by the portal-facing rail or chat list rather than by an in-turn tool. It gets raw conversation summaries from ConversationDirectory, formats each visible titled entry with _member_row, and lets object_page apply the requested page shape.

*Call graph*: calls 1 internal fn (_member_row); 4 external calls (__init__, object_agent_id, object_page, ws_current).


##### `ConversationObjects.member_detail`  (lines 120–135)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ConversationSpec] | None
```

**Purpose**: Fetches one conversation for a signed-in portal member outside an active turn. It only exposes conversations whose audience matches what that member’s own conversation is allowed to carry.

**Data flow**: It receives an optional extension context, a conversation name, the member id, and an admin flag. It builds the allowed audience subjects for that member, finds a matching visible row, and, if found, returns both a list-style row and a detailed object view wrapped in a MemberObject. If no matching row is found, it returns null.

**Call relations**: This is the portal-detail path matching member_page. It uses conversation_audience and audience_subjects to decide the member’s reading scope, then reuses _find for lookup, _row for the compact row, and _detail for the full object information.

*Call graph*: calls 3 internal fn (_find, _detail, _row); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ConversationObjects.status`  (lines 137–155)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports transcript status for a conversation and, when the transcript is small enough, writes a readable text copy into the caller’s workspace. This gives tools a safe way to inspect the message exchange without changing the conversation object.

**Data flow**: It receives a tool context, a conversation name, and an expected generation value that this implementation does not use. It finds the visible conversation row, reads and formats the transcript with _exchange, checks that the same row is still visible and unchanged, then counts messages and bytes. If there is non-empty transcript text under the materialization size limit, it writes a text file into the workspace and returns its path along with counts.

**Call relations**: This is a public status operation for conversation objects. It first uses _find, then _exchange for transcript content, then _unchanged_visible as a safety check before writing a workspace file. If the row disappears or its audience changes during the operation, it raises UnknownObject instead of leaking content.

*Call graph*: calls 3 internal fn (_exchange, _find, _unchanged_visible); 1 external calls (__init__).


##### `ConversationObjects.apply`  (lines 157–166)

```
async def apply(self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update a conversation through the object API. Conversations are made by chat surfaces, not authored through this object kind.

**Data flow**: It receives the usual apply inputs: context, name, desired spec, previous spec, and expected generation. It does not read or change any stored conversation data. It immediately raises a VerbNotSupported error explaining that conversations are surface-made.

**Call relations**: This is the write path that the object system would call for create or update. Instead of handing off to storage code, it stops the flow at once with VerbNotSupported so all mutation attempts follow the same read-only rule.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects.delete`  (lines 168–175)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete a conversation through the object API. Conversation cleanup is controlled elsewhere, such as by retention rules, not by object deletion.

**Data flow**: It receives a tool context, conversation name, and expected generation. It does not look up or alter the database row. It immediately raises a VerbNotSupported error with the shared explanation that conversations are created and closed outside this API.

**Call relations**: This is the deletion path that the object system would call. Like apply, it deliberately does not continue to any storage operation; it enforces the file’s central rule that conversation objects are read-only.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects._exchange`  (lines 177–197)

```
async def _exchange(self, ctx: ToolContext, conversation_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads a conversation transcript and converts it into simple plain-text message lines. It is the helper that turns stored transcript data into something status can count and optionally write to a workspace file.

**Data flow**: It receives a tool context and a conversation id. It builds the blob key for that transcript, reads the blob from storage, and decodes it. If the blob is missing, it returns an empty tuple. If the transcript cannot be decoded, it raises an error. For each message, it extracts text content and returns lines such as “assistant: ...” or “user: ...”.

**Call relations**: ConversationObjects.status calls this after it has found the conversation row. _exchange hands status the transcript text in a safe, simple form; status then decides whether to write that text into the workspace.

*Call graph*: called by 1 (status); 2 external calls (decode, transcript_key).


##### `ConversationObjects._unchanged_visible`  (lines 199–212)

```
async def _unchanged_visible(self, subjects: frozenset[str], row: sa.Row) -> bool
```

**Purpose**: Checks that a conversation row is still visible with the same audience after its transcript has been read. This prevents a race where access changes while status is preparing output.

**Data flow**: It receives the allowed audience subjects and the database row that was originally found. It opens a workspace database transaction and asks whether a visible conversation still exists with the same id and audience. It returns true if that exact visible row still exists, otherwise false.

**Call relations**: ConversationObjects.status calls this after reading the transcript but before returning or writing file information. It uses _visible to build the same visibility rule used elsewhere, so status does not accidentally expose data from a conversation whose permissions changed mid-request.

*Call graph*: calls 1 internal fn (_visible); called by 1 (status); 3 external calls (exists, select, workspace_tx).


##### `ConversationObjects._find`  (lines 214–220)

```
async def _find(self, subjects: frozenset[str], name: str) -> sa.Row | None
```

**Purpose**: Looks up one visible conversation by object name. It also rejects names that are not valid UUIDs, because conversation object names are stored as UUID strings.

**Data flow**: It receives a set of readable audience subjects and a name string. It tries to parse the name as a UUID; if parsing fails, it returns null. If parsing succeeds, it asks _rows for visible rows with that id and returns the first row if any exist.

**Call relations**: This helper is shared by get, member_detail, and status. It keeps the “parse the id, then search only visible rows” pattern in one place so all single-conversation reads apply the same access rule.

*Call graph*: calls 1 internal fn (_rows); called by 3 (get, member_detail, status); 1 external calls (UUID).


##### `ConversationObjects._rows`  (lines 222–229)

```
async def _rows(self, subjects: frozenset[str], *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Fetches conversation database rows visible to a given set of audience subjects. It can return all visible rows or narrow the search to one conversation id.

**Data flow**: It receives readable audience subjects and an optional conversation id. It starts with the common _visible database query, adds an id filter if one was provided, opens a workspace transaction, runs the query, and returns the resulting rows as a tuple.

**Call relations**: ConversationObjects.list uses this to gather all visible conversations, and _find uses it to find one. It relies on _visible for the shared workspace, agent, and audience filters before touching the database.

*Call graph*: calls 1 internal fn (_visible); called by 2 (_find, list); 1 external calls (workspace_tx).


##### `_visible`  (lines 232–254)

```
def _visible(subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the database query that defines which conversations are visible for the current workspace, selected agent, and allowed audience subjects. It is the central access filter for this file’s normal conversation reads.

**Data flow**: It receives a frozen set of audience subject strings. It creates a SQL query that selects conversation fields, joins to the agent table so the agent name can be shown, and filters by current workspace id, selected agent id, and audience membership. It returns the query object, not the rows themselves.

**Call relations**: _rows uses this query to fetch list and detail data, while _unchanged_visible uses it to re-check access during status. Because both paths share _visible, ordinary reads and safety checks follow the same visibility rule.

*Call graph*: called by 2 (_rows, _unchanged_visible); 3 external calls (select, object_agent_id, ws_current).


##### `_member_row`  (lines 257–275)

```
def _member_row(entry: ListedConversation, *, mine: bool) -> ObjectRow
```

**Purpose**: Turns a portal directory conversation entry into the compact row shown in a member’s conversation list. It adds portal-specific fields such as title, whether it is “mine”, speaker, surface, and last activity time.

**Data flow**: It receives a ListedConversation and a flag saying whether the entry belongs to the member. It picks speaker names or emails from the entry, chooses the latest useful timestamp, decides whether the surface is carried by the portal chat transport, and returns an ObjectRow with these fields.

**Call relations**: ConversationObjects.member_page calls this for each titled conversation returned by ConversationDirectory. It is the formatting step between the portal’s conversation directory data and the object system’s paged row format.

*Call graph*: called by 1 (member_page); 1 external calls (__init__).


##### `_row`  (lines 278–291)

```
def _row(row: sa.Row) -> ObjectRow
```

**Purpose**: Turns a database conversation row into a short object-list row. It gives the row a readable summary, such as where the conversation happened and when it was created.

**Data flow**: It receives a SQL row with conversation fields. It builds a human-friendly origin label from the surface and optional surface label, includes surface fields for filtering or display, and returns an ObjectRow named by the conversation id.

**Call relations**: ConversationObjects.list uses this for every row in a normal object listing, and member_detail uses it to include a compact row beside full detail. It is the standard formatter for database-backed conversation rows.

*Call graph*: called by 2 (list, member_detail); 1 external calls (__init__).


##### `_detail`  (lines 294–306)

```
def _detail(row: sa.Row) -> ObjectDetail[ConversationSpec]
```

**Purpose**: Builds the full object detail for a conversation. It includes the conversation’s spec, timestamps, and a link showing which agent the conversation is scoped to.

**Data flow**: It receives a database row. It copies the surface, surface label, and audience into a ConversationSpec, copies creation and update times into the detail object, and creates a scoped_to link pointing at the agent by name. The result is an ObjectDetail ready for get or member_detail responses.

**Call relations**: ConversationObjects.get and ConversationObjects.member_detail call this after they have found an allowed row. It is the final shaping step that turns stored conversation metadata into the object API’s detailed response.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


### `extensions/memory/ufo_ext_memory/objects.py`

`domain_logic` · `request handling`

This file is the doorway between the memory database and the system’s generic “object” interface. An object here means something a tool or user interface can list, open by name, and inspect in a standard shape. There are two kinds. A memory item is a saved fact or note, named by a durable id. A profile is a short “People” entry for a workspace member, with their role and current focus.

The file’s main job is to make reads safe and consistent. It checks the reader’s audience, meaning the set of subjects or privacy groups they are allowed to see. Shared workspace memory can be read by workspace members, but private or foreign-room memory is kept out. For memories that came from synced pages, it also checks that the source page is still readable and still at the same revision, so stale or no-longer-visible page-derived memories do not leak.

Listings show only live memory items. Items that were replaced by consolidation are hidden from lists, but can still be opened by id, much like an old address that forwards you to the new one. The detail view includes links such as “created_from” and “superseded_by” so callers can trace where a memory came from or what replaced it.

Both memory and profile objects are read-only here. Attempts to apply or delete them are rejected with clear messages because writes happen through separate memory-update, consolidation, and People-pass processes.

#### Function details

##### `_require_ext`  (lines 91–94)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that an extension context was provided before any memory or profile object work begins. The extension context is the bundle of database access and workspace information this file needs.

**Data flow**: It receives a possible extension context. If one is present, it returns it unchanged; if it is missing, it stops the operation by raising an error so later database reads do not fail in a confusing way.

**Call relations**: The public memory and profile methods call this first when they receive a tool or portal request. It acts like checking that you have the right key before trying to open the filing cabinet.

*Call graph*: called by 8 (get, list, member_detail, member_page, get, list, member_detail, member_page).


##### `_stamp`  (lines 97–101)

```
def _stamp(written: datetime) -> str
```

**Purpose**: Turns a stored date and time into one consistent text format. This matters because different databases may store timezone details differently.

**Data flow**: It receives a datetime value from the database, makes sure it is treated as an aware time using the store helper, then returns an ISO-8601 string that callers can read and sort consistently.

**Call relations**: Row-building helpers use this when they prepare memory and profile listings. It delegates the timezone cleanup to the shared store helper before producing the public-facing text.

*Call graph*: called by 2 (_profile_row, _row); 1 external calls (_aware).


##### `_row`  (lines 104–128)

```
def _row(name: str, body: str, subject: str, item_class: str, memory_kind: str, written: datetime | None, page_id: UUID | None, pages: Mapping[UUID, PageState]) -> ObjectRow
```

**Purpose**: Builds one listing row for a memory item. It turns the full database record into a shorter, display-friendly summary with key fields attached.

**Data flow**: It receives the memory id, body text, visibility subject, classification fields, write time, optional source page id, and any readable page information. It trims long text, formats the timestamp, adds source page title and stream when available, and returns an ObjectRow for listings.

**Call relations**: Memory listing and member-detail code call this after they have fetched memory data and any source page state. It uses the timestamp and text-clipping helpers so every memory row has the same compact shape.

*Call graph*: calls 1 internal fn (_stamp); called by 2 (_page, member_detail); 2 external calls (__init__, clip_to_word).


##### `_member_reader`  (lines 131–139)

```
def _member_reader(member_id: UUID) -> SourceReader
```

**Purpose**: Creates the reading identity used when a signed-in member views memory outside an active conversation turn. It says whose private and shared subjects that member may read.

**Data flow**: It receives a member id. It looks up the current agent, builds the audience for that member’s conversation, turns that audience into readable subjects, and returns a SourceReader containing all of that.

**Call relations**: Member-facing memory page and detail methods use this before reading memory. It hands the resulting reader to the same internal read paths used during tool requests, keeping portal reads and conversation reads consistent.

*Call graph*: called by 2 (member_detail, member_page); 4 external calls (__init__, audience_subjects, conversation_audience, agent_current).


##### `MemoryObjects.list`  (lines 148–149)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists live memory items visible to the current tool request. It is the standard object-list entry point for the memory kind.

**Data flow**: It receives a tool context and list query. It pulls the extension context and the caller’s source reader from the tool context, then asks the internal page builder to fetch and shape the visible rows.

**Call relations**: The object system calls this when someone lists memory objects. It mostly prepares the required context, then hands the real work to MemoryObjects._page.

*Call graph*: calls 3 internal fn (source_reader, _page, _require_ext).


##### `MemoryObjects.member_page`  (lines 151–164)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists live memory items for a signed-in member using that member’s normal reading audience. Admin status does not widen what memory appears here.

**Data flow**: It receives an optional extension context, member id, admin flag, and list query. It requires the extension context, builds a reader for the member, and returns the page produced for that reader.

**Call relations**: Member-facing screens use this instead of the tool-context list method. It relies on _member_reader to match portal visibility with conversation visibility, then delegates to MemoryObjects._page.

*Call graph*: calls 3 internal fn (_page, _member_reader, _require_ext).


##### `MemoryObjects.member_detail`  (lines 166–200)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemorySpec] | None
```

**Purpose**: Opens one memory item for a signed-in member and returns both its listing row and full detail. It still allows visible superseded items to be opened so old references can point to their replacement.

**Data flow**: It receives an extension context, memory name, member id, and admin flag. It builds the member reader, fetches the memory detail, finds any source page link, fetches readable source page state, builds a listing row, and returns both row and detail as a MemberObject. If the item is absent or not visible, it returns nothing.

**Call relations**: Portal detail views call this when a member opens a memory by id. It uses MemoryObjects._item for the full record and _row to produce the companion row shown beside it.

*Call graph*: calls 4 internal fn (_item, _member_reader, _require_ext, _row); 2 external calls (__init__, UUID).


##### `MemoryObjects.get`  (lines 202–203)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: Opens one memory item by id for the current tool request. It is the standard object-detail entry point for the memory kind.

**Data flow**: It receives a tool context and memory name. It extracts the extension context and source reader, then asks the internal item reader to fetch the visible detail or return nothing.

**Call relations**: The object system calls this when a memory reference is opened. It is a thin wrapper around MemoryObjects._item, which performs the id parsing, database lookup, visibility checks, and link construction.

*Call graph*: calls 3 internal fn (source_reader, _item, _require_ext).


##### `MemoryObjects._page`  (lines 205–262)

```
async def _page(self, ext: ExtensionContext, reader: SourceReader, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Fetches and prepares a page of live memory rows for a particular reader. It enforces both subject visibility and source-page visibility.

**Data flow**: It receives the extension context, a source reader, and a list query. It reads recent non-superseded and non-retired memory rows from the workspace database for the reader’s subjects, fetches source page states for page-derived memories, filters out rows whose source page is no longer readable or no longer matches, converts survivors into ObjectRows, and returns a paged result.

**Call relations**: MemoryObjects.list and MemoryObjects.member_page both use this as their shared listing engine. It talks to the database through the extension transaction, asks the extension which source pages are readable, uses _row to shape each row, and lets object_page apply the query’s paging behavior.

*Call graph*: calls 3 internal fn (readable_page_states, transaction, _row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `MemoryObjects._item`  (lines 264–330)

```
async def _item(self, ext: ExtensionContext, reader: SourceReader, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: Fetches the full detail for one visible memory item. Unlike listings, it can return superseded items so stale references still resolve.

**Data flow**: It receives the extension context, source reader, and memory name. It parses the name as a UUID, reads the matching workspace row if its subject is visible, verifies any source page is still readable and unchanged, builds provenance and replacement links, and returns an ObjectDetail with the memory body and metadata. Bad ids, missing rows, or hidden rows return nothing.

**Call relations**: MemoryObjects.get and MemoryObjects.member_detail call this whenever a single memory is opened. It builds the MemorySpec that callers see and attaches ObjectLink entries for created-from pages and superseded-by memories.

*Call graph*: calls 2 internal fn (readable_page_states, transaction); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, select, UUID).


##### `MemoryObjects.status`  (lines 332–339)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports no special write or sync status for memory objects. Memory items do not expose an editable status through this object interface.

**Data flow**: It receives the normal status inputs, including context, name, and expected generation. It ignores them and returns nothing, meaning there is no status payload to show.

**Call relations**: The object system may ask for status as part of its generic workflow. This memory implementation answers with no status because writes happen elsewhere.


##### `MemoryObjects.apply`  (lines 341–350)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses direct creation or editing of memory objects. Memories must be recorded through the memory_update path instead.

**Data flow**: It receives the requested name, new spec, old spec, and generation information. Rather than writing anything, it raises a not-supported error with an explanation.

**Call relations**: The generic object system may call apply for editable objects, but this memory store blocks it. The refusal points callers toward the proper writer instead of allowing inconsistent manual edits.

*Call graph*: 1 external calls (__init__).


##### `MemoryObjects.delete`  (lines 352–359)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses direct deletion of memory objects. Old memories are ended by consolidation or retirement rules, not by this object interface.

**Data flow**: It receives the context, memory name, and generation information. It does not touch the database and raises a not-supported error explaining that memories cannot be deleted here.

**Call relations**: The generic object system may offer delete for some kinds of objects. MemoryObjects.delete closes that route so search indexes and replacement links do not become inconsistent.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.list`  (lines 410–411)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists member profile rows visible to the current tool request. These profiles are the People-band summaries written from shared workspace facts.

**Data flow**: It receives a tool context and list query. It requires the extension context, reads the caller’s readable subjects from the context, and asks the internal page builder to return the visible profile rows.

**Call relations**: The object system calls this when someone lists profile objects. It delegates the visibility and database work to ProfileObjects._page.

*Call graph*: calls 2 internal fn (_page, _require_ext).


##### `ProfileObjects.member_page`  (lines 413–426)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists People-band profile rows for a signed-in member outside an active turn. Admin status does not reveal anything extra.

**Data flow**: It receives an extension context, member id, admin flag, and query. It builds the subject set for that member’s conversation audience, requires the extension context, and returns the profile page for those subjects.

**Call relations**: Member-facing profile pages use this path. It computes the member’s normal readable subjects and then uses ProfileObjects._page, the same shared listing engine used by tool requests.

*Call graph*: calls 2 internal fn (_page, _require_ext); 2 external calls (audience_subjects, conversation_audience).


##### `ProfileObjects.get`  (lines 428–430)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ProfileSpec] | None
```

**Purpose**: Opens one profile by member id for the current tool request. It returns only the full detail portion, not the member-page wrapper.

**Data flow**: It receives a tool context and profile name. It requires the extension context, uses the context’s readable subjects, asks the internal entry reader for the profile, and returns the detail if found.

**Call relations**: The object system calls this when a profile object is opened. It relies on ProfileObjects._entry to check shared-subject access, parse the member id, and read the database.

*Call graph*: calls 2 internal fn (_entry, _require_ext).


##### `ProfileObjects.member_detail`  (lines 432–442)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ProfileSpec] | None
```

**Purpose**: Opens one profile for a signed-in member and returns both its row and detail. This is used by member-facing views that need the same shape as member pages.

**Data flow**: It receives an extension context, profile name, member id, and admin flag. It builds the member’s readable subject set and asks the internal entry reader for the matching profile. It returns a MemberObject or nothing.

**Call relations**: Portal detail views call this for People-band entries. It uses the same ProfileObjects._entry helper as ProfileObjects.get, so portal and tool reads follow the same access rule.

*Call graph*: calls 2 internal fn (_entry, _require_ext); 2 external calls (audience_subjects, conversation_audience).


##### `ProfileObjects._page`  (lines 444–463)

```
async def _page(self, ext: ExtensionContext, subjects: frozenset[str], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Fetches and prepares a page of member profiles, but only for readers who can read workspace-shared facts. Profiles are shared summaries, so readers without the shared subject see none.

**Data flow**: It receives the extension context, readable subjects, and query. If the shared subject is absent, it returns an empty page. Otherwise it reads recent profile rows for the workspace, converts each to an ObjectRow, and returns the paged result.

**Call relations**: ProfileObjects.list and ProfileObjects.member_page both use this as their shared listing engine. It performs the shared-subject gate, reads through the extension transaction, formats rows with _profile_row, and wraps them with object_page.

*Call graph*: calls 2 internal fn (transaction, _profile_row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `ProfileObjects._entry`  (lines 465–498)

```
async def _entry(self, ext: ExtensionContext, subjects: frozenset[str], name: str) -> MemberObject[ProfileSpec] | None
```

**Purpose**: Fetches one member profile and packages it for display. It enforces the rule that only readers with access to workspace-shared facts can see profiles.

**Data flow**: It receives the extension context, readable subjects, and profile name. It returns nothing if the shared subject is missing or the name is not a UUID. Otherwise it reads the profile row for that member, normalizes the written time, builds a row and ObjectDetail, and returns them together as a MemberObject.

**Call relations**: ProfileObjects.get and ProfileObjects.member_detail call this when one profile is opened. It uses _profile_row for the list-style row and creates the ProfileSpec used in the detailed view.

*Call graph*: calls 2 internal fn (transaction, _profile_row); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, select, _aware, UUID).


##### `ProfileObjects.status`  (lines 500–507)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports no special write or sync status for profile objects. Profiles are generated by a background People pass, not edited here.

**Data flow**: It receives the normal status inputs and returns nothing. No database data is read and no status object is produced.

**Call relations**: The generic object system may ask for status, but this profile object kind has no editable status to report.


##### `ProfileObjects.apply`  (lines 509–518)

```
async def apply(self, ctx: ToolContext, name: str, spec: ProfileSpec, old: ProfileSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses direct creation or editing of profile objects. Profiles are written by the People pass from shared workspace facts.

**Data flow**: It receives the requested profile change, including new and old specs. It does not write anything and raises a not-supported error explaining the correct write path.

**Call relations**: If the generic object system tries to apply a profile change, this method blocks it. That keeps generated People-band summaries from being manually overwritten through the object API.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.delete`  (lines 520–527)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses direct deletion of profile objects. The profile list reflects what the People pass can derive from shared facts.

**Data flow**: It receives the context, profile name, and generation information. It leaves storage unchanged and raises a not-supported error.

**Call relations**: The generic object system may call delete for some object kinds. ProfileObjects.delete makes clear that profile lifecycle is controlled by the People pass, not by direct object deletion.

*Call graph*: 1 external calls (__init__).


##### `_profile_row`  (lines 530–540)

```
def _profile_row(row: sa.Row) -> ObjectRow
```

**Purpose**: Builds one listing row for a member profile. It creates a compact summary from the person’s role and focus.

**Data flow**: It receives a database row with member id, role, focus, and written time. It trims the combined role-and-focus summary, formats the written time, and returns an ObjectRow with profile fields.

**Call relations**: ProfileObjects._page uses this for each row in a profile listing, and ProfileObjects._entry uses it for the row that accompanies a profile detail. It uses the same clipping and timestamp helpers as memory rows for consistent presentation.

*Call graph*: calls 1 internal fn (_stamp); called by 2 (_entry, _page); 2 external calls (__init__, clip_to_word).


### Automation Objects
Automation-related object handlers expose monitors, digest runs, and scheduled tasks as inspectable or manageable workspace records.

### `extensions/monitors/ufo_ext_monitors/monitor_kind.py`

`domain_logic` · `request handling`

This file is the bridge between the monitor system and UFO's general “objects” interface. In plain terms, it makes active watches visible in a structured way, like a dashboard of alarms that have been set but have not fired yet.

A monitor watches a command run inside a conversation's sandbox. It periodically compares fresh command output with an earlier baseline, and it can fire when something changes, when probes keep failing, or when a deadline arrives. This file does not create those monitors. That matters: arming a monitor needs a live chat turn so the system can run the first probe and record the starting baseline. Because of that, this object kind refuses normal “apply” requests and tells callers to use the monitor tool instead.

What it does provide is the read-and-stop side. It lists armed monitors with useful summary fields, shows the details and status of one monitor, and deletes a monitor to disarm it. It also records ownership and sharing rules: a monitor is visible to people who can read the conversation it watches, and stopping it is limited to its creator or a workspace administrator. Think of this file as the registry desk for active watches: it does not install the alarm, but it can tell you what alarms exist, who owns them, what they are watching, and it can cancel one safely.

#### Function details

##### `_owner`  (lines 53–58)

```
def _owner(row: Monitor) -> GeneratedObjectOwner
```

**Purpose**: Builds the ownership record for a monitor. This tells the object system who created the monitor, how broadly it is shared, and which exact monitor generation it refers to.

**Data flow**: It receives one monitor row from storage. It reads the creator member ID, the audience for the watched conversation, and the monitor's unique ID. It turns the audience into a shared/not-shared ownership fact, then returns a GeneratedObjectOwner that other object code can use for visibility and safety checks.

**Call relations**: When MonitorObjects._member_rows prepares the list of visible monitors, it calls _owner for each stored monitor. The returned owner record is attached to each listed row so later reads or deletes can prove they are talking about the same monitor.

*Call graph*: called by 1 (_member_rows); 2 external calls (__init__, subject_shared).


##### `_require_ext`  (lines 61–64)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that the monitor code has the extension context it needs to talk to monitor storage. The extension context is the runtime handle that gives this extension access to its stored data and services.

**Data flow**: It receives an ExtensionContext value, or nothing. If a context is present, it returns it unchanged. If it is missing, it raises an error instead of letting later storage code fail in a more confusing way.

**Call relations**: MonitorObjects._member_rows, MonitorObjects._delete_owned, and MonitorObjects._find call this before creating a MonitorStore. It acts like a guard at the door: no monitor storage access is allowed unless the required runtime context is available.

*Call graph*: called by 3 (_delete_owned, _find, _member_rows).


##### `MonitorObjects._member_rows`  (lines 78–97)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the list view of currently armed monitors for a member. This is what lets a user or agent see what is being watched, when the next probe will run, who owns the watch, and whether it belongs to them.

**Data flow**: It receives the extension context and, optionally, the current member's ID. It loads all armed monitor rows from MonitorStore, looks up email addresses for the creators, and turns each monitor into an OwnedRow with a name, a short summary, ownership information, and listable fields such as conversation ID, next probe time, deadline, owner email, and whether the monitor is mine. It returns the rows as a tuple.

**Call relations**: The object framework calls this when someone lists monitor objects. Inside that flow, it uses _require_ext to get safe access to storage, MonitorStore to read armed monitors, owner_emails to make creator information human-readable, and _owner to attach the ownership facts that the wider object system relies on.

*Call graph*: calls 2 internal fn (_owner, _require_ext); 3 external calls (__init__, __init__, owner_emails).


##### `MonitorObjects._member_object`  (lines 99–125)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[MonitorSpec] | None
```

**Purpose**: Builds the detailed view for one specific monitor. This is used when someone asks to inspect a monitor rather than just seeing it in a list.

**Data flow**: It receives the extension context, the monitor name, the expected owner record, and optionally the current member ID. It searches for the named armed monitor, then checks that the stored monitor ID still matches the owner generation the caller supplied. If the monitor is missing or has changed, it returns nothing. Otherwise, it returns an ObjectDetail containing the monitor's command, interval, deadline, reason, timestamps, and a link back to the conversation where reports will land.

**Call relations**: The object framework calls this for a get/read operation on a monitor object. It delegates the lookup to MonitorObjects._find, then packages the found row into MonitorSpec and ObjectDetail values so the rest of the system can present it consistently with other object kinds.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `MonitorObjects._status`  (lines 127–142)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status counters for one monitor. This answers questions like when it was armed, how many probes have run, how many failed, and what baseline the next probe is comparing against.

**Data flow**: It receives a tool context, a monitor name, and the expected owner record. It finds the armed monitor and verifies that its unique ID still matches the owner generation. If not, it returns nothing. If it matches, it returns a dictionary with JSON-friendly values: timestamps as text, probe counters, skipped count, and a shortened baseline excerpt.

**Call relations**: This is used when the object system or a tool needs status information beyond the main monitor specification. It calls MonitorObjects._find for the stored row, then formats runtime state into simple values that can be shown to an agent or user.

*Call graph*: calls 1 internal fn (_find).


##### `MonitorObjects._apply_owned`  (lines 144–152)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: MonitorSpec, old: MonitorSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Refuses attempts to create or update monitors through the normal object “apply” path. This protects an important rule: monitors must be armed through the chat monitor tool, because that tool runs the first probe and records the baseline.

**Data flow**: It receives the tool context, desired name and spec, the old spec if one exists, and owner information. It does not use them to change storage. Instead, it raises VerbNotSupported with a message explaining that arming must happen through the monitor tool.

**Call relations**: The object framework would call this for an apply/create/update operation. Rather than handing anything off to storage, it stops the flow immediately so callers do not create a monitor without the required live baseline.

*Call graph*: 1 external calls (__init__).


##### `MonitorObjects._delete_owned`  (lines 154–159)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Disarms a monitor when a permitted user deletes the monitor object. This is the supported way to stop a watch before it fires.

**Data flow**: It receives a tool context, a monitor name, and the owner record that identifies the monitor expected by the caller. It finds the current armed monitor, checks that its unique ID still matches the requested generation, and then asks MonitorStore to disarm it. If the monitor is gone or has changed during the operation, it raises an error instead of silently stopping the wrong watch.

**Call relations**: The object framework calls this during a delete operation after the broader permission gate has allowed the request. It uses MonitorObjects._find to locate the monitor, _require_ext to access storage, and MonitorStore.disarm to actually stop the watch.

*Call graph*: calls 2 internal fn (_find, _require_ext); 1 external calls (__init__).


##### `MonitorObjects._find`  (lines 161–165)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> Monitor | None
```

**Purpose**: Looks up one armed monitor by name. It is a small shared helper that keeps the get, status, and delete paths using the same lookup behavior.

**Data flow**: It receives the extension context and the monitor name to search for. It requires a valid context, loads the current armed monitors from MonitorStore, scans for a row with the matching name, and returns that monitor row if found. If no armed monitor has that name, it returns nothing.

**Call relations**: MonitorObjects._member_object, MonitorObjects._status, and MonitorObjects._delete_owned call this whenever they need the current stored monitor before reading details, reporting status, or disarming it. It centralizes the storage lookup so those flows do not each repeat the same search.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_delete_owned, _member_object, _status); 1 external calls (__init__).


### `extensions/report_digest/ufo_ext_report_digest/objects.py`

`domain_logic` · `request handling`

A “report” here is not something a user creates by filling in a form. It is the trace left behind when a scheduled task fires, publishes a report, or fails while trying. This file exposes those traces through the project’s object system, so a user can ask, “What reports has my radar produced?” or “What happened in this run?”

The key idea is safety: reports are only visible if the reader could already read the scheduled run’s conversation audience. The file does not widen access for admins or anyone else. It is more like a window onto existing runs than a separate store of authority.

The `ReportObjects` class provides the read paths. Listing asks the extension context for recent scheduled runs, enriches them with digest entries from the report digest table, looks up the scheduled task names when possible, and formats each run as an object row. Getting one report parses the requested name as a turn ID, fetches that one run, and returns fuller detail with a link back to the conversation where it was created.

Create, update, status, and delete are deliberately not real operations here. Reports “exist” because scheduled tasks ran. If this file were missing, report runs would still happen, but users and tools would not have this object-shaped view of their history, outputs, failures, digest entries, and artifacts.

#### Function details

##### `ReportObjects.list`  (lines 65–74)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the report objects visible to the current tool user. If there is no acting member, it returns an empty page rather than exposing anything.

**Data flow**: It receives a tool context and a list query. It checks who the acting member is, extracts the extension context, and asks the shared paging helper for rows limited to the current agent and the member’s readable subjects. It returns an object page ready for the caller to display or process.

**Call relations**: This is one of the public read entry points for the report object kind. When a list request arrives, it uses `_ext` to find the extension services and then hands the real fetching and formatting work to `ReportObjects._page`; if there is no member, it uses `object_page` to return an empty result.

*Call graph*: calls 2 internal fn (_page, _ext); 1 external calls (object_page).


##### `ReportObjects.get`  (lines 76–82)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ReportSpec] | None
```

**Purpose**: Fetches one report object by name for the current tool user. The name is expected to be the scheduled run’s turn ID.

**Data flow**: It receives a tool context and a report name. If there is no acting member, it returns nothing. Otherwise it gets the extension context, asks `_one` to find and build the matching report detail under the member’s read permissions, and returns the detail if found.

**Call relations**: This is the single-object counterpart to `ReportObjects.list`. It uses `_ext` to locate the extension context and delegates the lookup and object construction to `ReportObjects._one`.

*Call graph*: calls 2 internal fn (_one, _ext).


##### `ReportObjects.member_page`  (lines 84–92)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists reports for a specific member from a member-facing object view. It uses the general object agent identity rather than the current turn’s agent.

**Data flow**: It receives an optional extension context, a member ID, an admin flag, and a page query. It normalizes the extension context, chooses the object agent ID, and asks `_page` to fetch and format the member’s reports. The admin flag is accepted by the interface but does not broaden what is read here.

**Call relations**: This path is used when report rows are requested through member object browsing rather than a tool turn. It calls `_ext` for context, `object_agent_id` for the agent scope, and then relies on `ReportObjects._page` for the shared listing work.

*Call graph*: calls 2 internal fn (_page, _ext); 1 external calls (object_agent_id).


##### `ReportObjects.member_detail`  (lines 94–102)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ReportSpec] | None
```

**Purpose**: Fetches one report for a specific member from a member-facing object view. Like the other report reads, it does not use the admin flag to expand access.

**Data flow**: It receives an optional extension context, a report name, a member ID, and an admin flag. It turns the carrier into an extension context and asks `_one` to locate the report by name for that member. It returns the member object if the run exists and is readable, otherwise nothing.

**Call relations**: This is the member-view version of `ReportObjects.get`. It calls `_ext` and then hands the lookup to `ReportObjects._one`.

*Call graph*: calls 2 internal fn (_one, _ext).


##### `ReportObjects.status`  (lines 104–111)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports have no separate live status operation in this object kind, so this function always says there is no status document. The run status is already included in the report row fields.

**Data flow**: It receives a tool context, a name, and an optional expected generation value. It ignores those inputs and returns `None`, meaning there is no extra status response to provide.

**Call relations**: This method satisfies the object-kind interface. Unlike listing and getting, it does not call into the database or scheduled-run system because report status is represented as data on the run itself.


##### `ReportObjects.apply`  (lines 113–122)

```
async def apply(self, ctx: ToolContext, name: str, spec: ReportSpec, old: ReportSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a report object. Reports are produced by scheduled tasks running, not by direct user edits.

**Data flow**: It receives the requested name, new spec, old spec, context, and optional generation check. Instead of changing stored data, it raises a `VerbNotSupported` error with a message explaining that reports come from scheduled runs.

**Call relations**: This is called when the object system tries to apply a desired report spec. It stops that flow immediately by constructing `VerbNotSupported`, so no write is handed off to storage.

*Call graph*: 1 external calls (__init__).


##### `ReportObjects.delete`  (lines 124–131)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a report object through this object kind. The report view reflects scheduled-run history rather than owning that history.

**Data flow**: It receives a tool context, report name, and optional generation check. It does not remove anything; it raises `VerbNotSupported` with the standard explanation that reports exist because tasks ran.

**Call relations**: This is called when the object system asks the report kind to delete something. It ends that request by creating `VerbNotSupported`, matching the same read-only rule enforced by `ReportObjects.apply`.

*Call graph*: 1 external calls (__init__).


##### `ReportObjects._page`  (lines 133–146)

```
async def _page(self, ext: ExtensionContext, member_id: UUID, *, agent_id: UUID, query: ObjectListQuery, subjects: frozenset[str] | None=None) -> ObjectPage
```

**Purpose**: Builds a page of report rows from the scheduled runs a member is allowed to read. It is the shared helper behind both normal listing and member-page listing.

**Data flow**: It receives an extension context, member ID, agent ID, page query, and optional readable subjects. It asks the extension for recent scheduled runs, limited by the report list maximum and the supplied visibility rules. It enriches those runs through `_rows` and then shapes them into an `ObjectPage` using the query’s paging and filtering rules.

**Call relations**: `ReportObjects.list` and `ReportObjects.member_page` call this when they need multiple reports. `_page` gathers raw scheduled runs from `ExtensionContext.scheduled_runs`, sends them to `ReportObjects._rows` for conversion, and wraps the resulting rows with `object_page`.

*Call graph*: calls 2 internal fn (scheduled_runs, _rows); called by 2 (list, member_page); 1 external calls (object_page).


##### `ReportObjects._one`  (lines 148–180)

```
async def _one(self, ext: ExtensionContext, name: str, *, member_id: UUID, subjects: frozenset[str] | None=None) -> MemberObject[ReportSpec] | None
```

**Purpose**: Finds and builds the detailed object view for exactly one report. It treats the report name as a UUID turn ID, because each report is named after the scheduled turn that produced it.

**Data flow**: It receives an extension context, a name, a member ID, and optional readable subjects. It first tries to parse the name as a UUID; if that fails, there can be no matching run. It fetches at most one scheduled run with that turn ID, converts it to an object row, and wraps it with detail data such as creation time, update time, an empty `ReportSpec`, and a link to the conversation where the run happened.

**Call relations**: `ReportObjects.get` and `ReportObjects.member_detail` call this for single-report reads. It uses `ExtensionContext.scheduled_runs` to find the run, `ReportObjects._rows` to build the row, and then constructs the member object detail with `MemberObject`, `ObjectDetail`, `ObjectLink`, `ObjectRef`, and `ReportSpec`.

*Call graph*: calls 2 internal fn (scheduled_runs, _rows); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, __init__, UUID).


##### `ReportObjects._rows`  (lines 182–197)

```
async def _rows(self, ext: ExtensionContext, runs: tuple[ScheduledRun, ...]) -> tuple[ObjectRow, ...]
```

**Purpose**: Turns scheduled runs into object rows, adding digest entries and task names along the way. This is where plain run records become useful report summaries.

**Data flow**: It receives a tuple of scheduled runs. It collects their turn IDs to fetch digest entries, extracts scheduled task IDs from their idempotency keys when possible, fetches the task names, and then calls `_row` once per run. It returns a tuple of completed `ObjectRow` values.

**Call relations**: `ReportObjects._page` and `ReportObjects._one` call this after they have fetched runs. It coordinates `ReportObjects._entries`, `ReportObjects._task_names`, `scheduled_fire_task_id`, and `ReportObjects._row` so each row has the extra context needed for display.

*Call graph*: calls 3 internal fn (_entries, _row, _task_names); called by 2 (_one, _page); 1 external calls (scheduled_fire_task_id).


##### `ReportObjects._row`  (lines 199–241)

```
def _row(self, ext: ExtensionContext, run: ScheduledRun, entry: dict[str, JsonValue] | None, tasks: dict[UUID, str]) -> ObjectRow
```

**Purpose**: Formats one scheduled run as one report object row. It chooses a readable summary and fills in fields such as conversation, status, digest entry, and shared files.

**Data flow**: It receives the extension context, one scheduled run, that run’s digest entry if any, and a map of task IDs to task names. It derives the firing task ID when possible, picks the digest title as the summary when available, otherwise falls back to task/status/time text, and builds fields for the run and its artifacts. For each artifact, it asks the extension for signed links and preview links, then returns an `ObjectRow`.

**Call relations**: `ReportObjects._rows` calls this for each run after gathering digest and task lookup data. `_row` uses `scheduled_fire_task_id` to connect a run back to its task and uses `ExtensionContext.artifact_link` and `ExtensionContext.artifact_preview_link` to turn stored artifact records into usable links.

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 1 (_rows); 2 external calls (__init__, scheduled_fire_task_id).


##### `ReportObjects._entries`  (lines 243–271)

```
async def _entries(self, ext: ExtensionContext, turn_ids: tuple[UUID, ...]) -> dict[UUID, dict[str, JsonValue]]
```

**Purpose**: Reads the digest text already written for a set of report runs. These entries provide the title, summary, and bullet points shown inside report objects.

**Data flow**: It receives an extension context and a tuple of turn IDs. If there are no IDs, it returns an empty dictionary. Otherwise it opens a database transaction, selects matching rows from the report digest entry table for the current workspace, and returns a dictionary keyed by turn ID with title, summary, and simplified point data.

**Call relations**: `ReportObjects._rows` calls this before formatting rows. It uses `ExtensionContext.transaction` and `sqlalchemy.select` to read the digest table, then hands the resulting lookup back so `_row` can include the entry for each run.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_rows); 1 external calls (select).


##### `ReportObjects._task_names`  (lines 273–291)

```
async def _task_names(self, ext: ExtensionContext, task_ids: tuple[UUID, ...]) -> dict[UUID, str]
```

**Purpose**: Looks up the object names of scheduled tasks that fired the visible runs. If a task has been deleted, the report can still be shown, just without a task name.

**Data flow**: It receives an extension context and a tuple of task IDs. If the tuple is empty, it returns an empty dictionary. Otherwise it opens a database transaction, selects task IDs and names in the current workspace, and returns a dictionary from task ID to task name.

**Call relations**: `ReportObjects._rows` calls this after extracting task IDs from scheduled runs. It uses `ExtensionContext.transaction` and `sqlalchemy.select` to read the scheduled task table, then gives `_rows` the name lookup used by `_row`.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_rows); 1 external calls (select).


##### `_ext`  (lines 294–299)

```
def _ext(carrier: ToolContext | ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Finds the `ExtensionContext`, which is the object that gives this code access to workspace data, scheduled runs, transactions, and artifact links. It accepts either the context directly or a tool context that contains it.

**Data flow**: It receives a carrier that may be an extension context, a tool context, or `None`. If the carrier already is an extension context, it returns it. If the carrier has an attached extension context, it returns that. If neither is true, it raises a runtime error because report objects cannot work without extension services.

**Call relations**: `ReportObjects.list`, `ReportObjects.get`, `ReportObjects.member_page`, and `ReportObjects.member_detail` call this at the edge of their workflows. It is the small adapter that lets both tool-based and member-based read paths feed the same lower-level helpers.

*Call graph*: called by 4 (get, list, member_detail, member_page).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `request handling and scheduled workflow control`

This file solves two related problems: recurring work and safe waiting. A scheduled task is treated like a workspace object, so agents can create, list, inspect, update, and delete it through the same object system used elsewhere. The task stores a cron schedule, which is a compact text pattern for repeated times, plus the prompt to run, the conversation it reports back to, and the member it acts for. Without this file, the scheduler might still store rows, but members and agents would not have a controlled, permission-aware way to work with them.

The file also defines visibility and editing rules. A task is visible about as far as the conversation it reports into is visible. The creator can change the task content. Admins can adjust operational settings like schedule, expiry, pause, or deletion, but they cannot rewrite another member’s private prompt. This matters because a future run acts with the creator’s authority.

Finally, `pause_and_wait` is a tool for workflows that must stop and resume later, like waiting for approval or an email code. It records a durable pause in a pause table, then tells the agent to reply and end its turn. Later, either a new member message or the timer can resume the workflow, with saved instructions and metadata.

#### Function details

##### `ScheduledTaskSpec.validate_utc_expiry`  (lines 103–106)

```
def validate_utc_expiry(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Checks that an expiry time is written in UTC, the standard time zone used by this scheduler. This prevents a task from expiring at the wrong real-world moment because of an ambiguous or local timestamp.

**Data flow**: It receives an optional expiry timestamp. If there is no timestamp, it leaves it alone. If there is one, it checks that the timestamp has timezone information and that its offset is exactly UTC; invalid values raise an error, and valid values are returned unchanged.

**Call relations**: This runs automatically when a `ScheduledTaskSpec` is built or validated. It protects later create and update work, especially `ScheduledTaskObjects._apply_owned`, from receiving an expiry time the scheduler cannot safely interpret.

*Call graph*: 2 external calls (utcoffset, timedelta).


##### `_require_scheduler`  (lines 123–124)

```
def _require_scheduler(ext: ExtensionContext | None) -> ScheduleStore
```

**Purpose**: Builds the schedule store used to read and write scheduled tasks. It is a small guardrail that makes sure the scheduled-tasks extension context exists before touching task storage.

**Data flow**: It receives an optional extension context. It first requires that context to be present, then wraps it in a `ScheduleStore`, which is the storage access object for scheduled tasks. The result is a ready-to-use scheduler store.

**Call relations**: Most task operations call this before they list, inspect, create, update, or cancel tasks. It delegates the context check to `_require_ext`, then hands the resulting store to methods such as `_rows`, `_status`, `_apply_owned`, `_delete_owned`, `_find`, and `member_conversation_rows`.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_apply_owned, _delete_owned, _find, _rows, _status, member_conversation_rows); 1 external calls (__init__).


##### `_require_ext`  (lines 127–130)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure this code is running with the scheduled-tasks extension context. If the context is missing, it stops immediately with a clear error instead of failing later in a confusing way.

**Data flow**: It receives an optional extension context. If the value is missing, it raises a runtime error. If it is present, it returns the same context unchanged.

**Call relations**: `_require_scheduler` uses this before creating a schedule store, and `pause_and_wait` uses it before writing a durable pause. It is the shared front-door check for extension-specific storage and services.

*Call graph*: called by 2 (_require_scheduler, pause_and_wait).


##### `_summary`  (lines 133–134)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: Creates a short one-line summary for a scheduled task. This gives listings a readable label like “schedule — description” without exposing an unlimited amount of prompt text.

**Data flow**: It receives a scheduled task. It combines the task’s schedule with its description, or with its prompt if no description exists, then trims the text to the configured maximum length. The output is a short string for display.

**Call relations**: `ScheduledTaskObjects._rows` calls this while building list rows. It is used only when the viewer is allowed to see the task’s content; otherwise the row shows a private placeholder instead.

*Call graph*: called by 1 (_rows).


##### `_owner`  (lines 137–145)

```
def _owner(listed: ListedTask) -> GeneratedObjectOwner
```

**Purpose**: Describes who owns a listed task and how broadly it is shared. This lets the generic object system decide who may see or act on the task.

**Data flow**: It receives a listed task, which includes the task and its audience information. It records the creator as the owner, converts the audience into a shared/not-shared visibility fact, and uses the task id as the generation marker. The output is an ownership record.

**Call relations**: `_rows` uses this ownership record for every listed row, and `member_conversation_rows` uses it before granting a task inside a conversation view. The object framework then uses the owner information for visibility checks.

*Call graph*: called by 2 (_rows, member_conversation_rows); 2 external calls (__init__, subject_shared).


##### `ScheduledTaskObjects._admin_can_apply`  (lines 166–169)

```
def _admin_can_apply(self, _old: ScheduledTaskSpec, spec: ScheduledTaskSpec) -> bool
```

**Purpose**: Decides which updates an admin may make to someone else’s scheduled task. Admins may change timing-related settings, but not the prompt or description that will run as the original creator.

**Data flow**: It receives the previous task spec and the new spec. It looks at which fields the update is trying to set. If the update includes `prompt` or `description`, it returns false; otherwise it returns true.

**Call relations**: This method plugs into the generic object permission flow for `ScheduledTaskObjects`. It supports the broader rule later enforced by `_apply_owned`: admins can operate the schedule, expiry, and pause controls, while content remains the creator’s responsibility.


##### `ScheduledTaskObjects.member_page`  (lines 171–193)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds a page of scheduled tasks visible to a member, with special support for filtering by conversation. This is what powers object listing when someone asks for tasks tied to a particular conversation.

**Data flow**: It receives the extension context, the requesting member id, whether that requester is an admin, and a list query. If the query does not contain a conversation filter, it lets the parent object logic handle the page. If there is a valid conversation id, it gathers rows for that conversation, filters out rows the requester cannot see, converts them into plain object rows, and returns a paged result.

**Call relations**: The object system calls this during member-facing list requests. For conversation-filtered requests it calls `_rows` to gather task data, creates display rows, and passes them through `object_page`; for other requests it hands control back to the base class.

*Call graph*: calls 1 internal fn (_rows); 3 external calls (__init__, object_page, UUID).


##### `ScheduledTaskObjects.member_conversation_rows`  (lines 195–215)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Lists the scheduled tasks that should appear inside a specific conversation’s object area. It also marks whether the viewer may see each task’s private content.

**Data flow**: It receives a conversation id, member id, admin flag, and row limit. It asks the schedule store for tasks that report into that conversation. For each visible task, it creates a conversation grant with the task name, task generation id, and a content-visible flag. The output is a tuple of grants.

**Call relations**: Conversation views call this when they need embedded object entries. It uses `_require_scheduler` to read tasks, `_owner` to describe visibility, and `task_content_visible` to decide whether the prompt-like content can be shown.

*Call graph*: calls 2 internal fn (_owner, _require_scheduler); 2 external calls (__init__, task_content_visible).


##### `ScheduledTaskObjects._member_rows`  (lines 217–220)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Returns all scheduled-task rows relevant to member-readable listing. It is the class’s simple hook for the generic object system when it needs rows with ownership information.

**Data flow**: It receives an extension context and an optional member id. It asks `_rows` to build the full row set without trimming prompts. The output is a tuple of owned rows.

**Call relations**: The generic member-readable object base calls this as part of listing or access checks. It does not assemble rows itself; it delegates the real work to `_rows`.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._owned_rows`  (lines 222–229)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the rows an active tool turn can read, with long prompts shortened to a safe excerpt. This avoids flooding the model’s context with very large task prompts during a list operation.

**Data flow**: It receives the current tool context, including the acting member id and extension context. It asks `_rows` for scheduled-task rows and requests prompt trimming to the configured excerpt length. The output is a tuple of owned rows for that turn.

**Call relations**: The object tooling calls this when a turn lists objects. It relies on `_rows` for the actual schedule data and uses the acting member from the context so private prompts are only shown to the right person.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._rows`  (lines 231–277)

```
async def _rows(self, ext: ExtensionContext | None, *, member_id: UUID | None, prompt_max: int | None, conversation_id: UUID | None=None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Assembles the display-ready list rows for scheduled tasks. It combines stored task data, ownership, visibility, creator email, and recent run status into one shape the object system can show.

**Data flow**: It receives an extension context, an optional member id, an optional prompt length limit, and optionally a conversation id. It reads matching scheduled tasks, looks up creator emails, inspects recent run information, and then builds one owned row per task. If the viewer may see the content, the row includes the real summary and prompt; otherwise it substitutes a private placeholder. The output is a tuple of owned rows.

**Call relations**: This is the central row-building helper for listing. `member_page`, `_member_rows`, and `_owned_rows` call it. It calls `_require_scheduler` for storage access, `_summary` for readable labels, `_owner` for ownership, and `task_content_visible` for privacy decisions.

*Call graph*: calls 3 internal fn (_owner, _require_scheduler, _summary); called by 3 (_member_rows, _owned_rows, member_page); 3 external calls (__init__, owner_emails, task_content_visible).


##### `ScheduledTaskObjects._member_object`  (lines 279–308)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ScheduledTaskSpec] | None
```

**Purpose**: Builds the detailed view of one scheduled task for a member. It returns the saved schedule settings, timestamps, and a link to the conversation the task reports into, while also saying whether the spec content is visible.

**Data flow**: It receives an extension context, task name, expected owner record, and optional member id. It finds the task by name and checks that its id still matches the owner generation. If it matches, it copies the task fields into a `ScheduledTaskSpec`, adds created and updated times, adds a `reports_to` link to the conversation, and marks content visibility. If not found or stale, it returns nothing.

**Call relations**: The object system calls this for a get/detail request. It uses `_find` to locate the current task and `task_content_visible` to decide whether the caller should see the full spec.

*Call graph*: calls 1 internal fn (_find); 5 external calls (__init__, __init__, __init__, __init__, task_content_visible).


##### `ScheduledTaskObjects._status`  (lines 310–342)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns operational status for one scheduled task, such as whether it is paused, when it will run next, and what happened on the latest run. It gives users and admins a quick health check without editing the task.

**Data flow**: It receives the current tool context, task name, and expected owner record. It finds the task, verifies the generation id, asks the scheduler to inspect it, and builds a status dictionary. If there was a latest run, it includes the turn id and status; if the viewer may see task content, it may also include a shortened response excerpt. Missing or stale tasks return nothing.

**Call relations**: The object tooling calls this when status information is requested. It calls `_find` to identify the task, `_require_scheduler` to inspect scheduler state, and `task_content_visible` before including the latest response text.

*Call graph*: calls 2 internal fn (_find, _require_scheduler); 1 external calls (task_content_visible).


##### `ScheduledTaskObjects._apply_owned`  (lines 344–396)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates a new scheduled task or updates an existing one, while enforcing the important safety rules. It validates cron schedules, requires a real member creator, keeps a task tied to its original conversation, and limits who may change private content.

**Data flow**: It receives the tool context, task name, desired spec, old spec if editing, and owner information if the task already exists. It validates any supplied schedule and checks the acting member. For a new task, it requires both schedule and prompt, calculates the first run time, and writes the task to the schedule store. For an update, it verifies the task has not changed underneath the edit, checks creator/admin permissions, merges omitted fields with existing values, recalculates the next run time, and saves the update.

**Call relations**: The generic object apply flow calls this when an agent creates or updates a `scheduled_task`. It uses `_find` to compare against current storage, `_require_scheduler` to write changes, `validate_cron` and `next_fire` for schedule correctness, and `speaker_is_admin` when permission depends on admin status.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_scheduler); 4 external calls (__init__, now, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 398–402)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Cancels an existing scheduled task after confirming it is still the same task the caller meant to delete. This avoids deleting a newly replaced task with the same name by accident.

**Data flow**: It receives the tool context, task name, and expected owner record. It finds the current task by name and checks that its id matches the expected generation. If the check fails, it raises an error about the task changing during cancellation. If it matches, it asks the schedule store to cancel the task.

**Call relations**: The object delete flow calls this after permission checks. It uses `_find` to guard against stale edits and `_require_scheduler` to perform the actual cancellation.

*Call graph*: calls 2 internal fn (_find, _require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 404–412)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTask | None
```

**Purpose**: Looks up a scheduled task by its object name. It is a small helper used whenever the code needs to compare a requested object with the current stored task.

**Data flow**: It receives an extension context and a task name. It asks the schedule store for reported tasks, scans them for the matching name, and returns the matching listed task if found. If no task has that name, it returns nothing.

**Call relations**: `_apply_owned`, `_delete_owned`, `_member_object`, and `_status` all call this before acting on a named task. It centralizes the name lookup so those methods can focus on create, update, delete, or display behavior.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


##### `pause_and_wait`  (lines 475–512)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: Pauses the current workflow in a durable way, then tells the agent exactly what to say before ending its turn. The workflow can resume later either when a member sends a new message or when the timer fires.

**Data flow**: It receives the current tool context and pause arguments: the message to show now, wait length, resume instructions, reason, and optional metadata. It calculates the resume time, builds a wake-up prompt containing the reason, next steps, and metadata, and writes a pause row with the current conversation, agent, turn sequence, and arrival watermark. It then returns a tool result containing a directive plus a JSON payload for the agent’s reply.

**Call relations**: The `PAUSE_AND_WAIT_TOOL` definition exposes this as a callable tool. When invoked during a turn, it uses `_require_ext` to access extension services, writes through `PauseStore`, and returns `TextContent` inside a `ToolResult` so the agent knows to reply and stop.

*Call graph*: calls 1 internal fn (_require_ext); 6 external calls (__init__, __init__, __init__, now, timedelta, dumps).


### Published and Synced Content
Content object handlers bridge hosted sites and synced source material into the shared workspace object model.

### `extensions/sites/ufo_ext_sites/objects.py`

`domain_logic` · `request handling`

A deployed site is more than a running web page. The rest of the workspace needs a stable way to refer to it, show it in lists, check who may see it, and remove it when requested. This file supplies that layer.

Each site object is named from the site’s own name plus a short fingerprint of the conversation that created it. That matters because two different conversations can both deploy a site called “dashboard”; the fingerprint keeps those objects separate, like adding an apartment number to a street address.

The main class, SiteObjects, plugs hosted sites into the shared object framework. When asked to list sites, it reads the site registry, adds useful fields such as the creator, conversation, hosted URL, preview image URL, and visibility, then returns rows the workspace can display. When asked for one site, it returns its current visibility and a link back to the conversation that created it. When asked for status, it can also restore the deployed source files into the sandbox so they can be edited and redeployed.

The important rule is visibility. Normal sites use their own visibility setting: private, workspace, or public. But if a site is bound as an agent’s homepage, the site follows the agent’s visibility instead, and this file refuses attempts to change the site directly. Deleting a site unregisters it, so its link stops resolving.

#### Function details

##### `site_object_name`  (lines 81–85)

```
def site_object_name(conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the stable workspace object name for a hosted site. It combines the site name with a short digest, meaning a shortened fingerprint, of the conversation id so two conversations can safely use the same site name.

**Data flow**: It receives a conversation id and a site name. It turns the conversation id into text, hashes it, keeps the first few characters, and appends that suffix to the site name. The result is a unique-looking object name such as a site name followed by a conversation fingerprint.

**Call relations**: This is the naming rule used across the file. _named uses it when building lookup tables, member_conversation_rows uses it when granting conversation access, and site_name_from_object calls it to verify that a proposed reverse lookup matches the official format.

*Call graph*: called by 3 (member_conversation_rows, _named, site_name_from_object); 1 external calls (sha256).


##### `site_name_from_object`  (lines 88–94)

```
def site_name_from_object(conversation_id: UUID, object_name: str) -> str | None
```

**Purpose**: Tries to recover the original site name from a workspace object name for a specific conversation. It is useful when code has the object-style name and needs to know which hosted site it refers to.

**Data flow**: It receives a conversation id and an object name. It computes the expected conversation suffix, checks whether the object name ends with that suffix, removes it if present, and then verifies the rebuilt name exactly matches the official naming rule. It returns the plain site name when valid, or nothing when the name does not belong to that conversation.

**Call relations**: This function depends on the same hashing rule as site_object_name, and calls site_object_name as a final safety check. It is a reverse parser for the object names created elsewhere in this file.

*Call graph*: calls 1 internal fn (site_object_name); 1 external calls (sha256).


##### `_named`  (lines 97–98)

```
def _named(sites: Iterable[HostedSite]) -> dict[str, HostedSite]
```

**Purpose**: Turns a group of hosted site records into a dictionary keyed by their workspace object names. This makes later lookups by object name simple and consistent.

**Data flow**: It receives an iterable of HostedSite records. For each one, it calculates the object name from the site’s conversation id and site name, then stores the full site record under that key. It returns a dictionary from object name to hosted site.

**Call relations**: _member_rows calls this when preparing all list rows, and _find calls it when looking for one object by name. It centralizes the naming rule by relying on site_object_name instead of rebuilding names by hand.

*Call graph*: calls 1 internal fn (site_object_name); called by 2 (_find, _member_rows).


##### `_workspace`  (lines 101–104)

```
def _workspace(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that the object code was given an extension context, which is the request-scoped access point for workspace data and services. Without that context, this file cannot read the site registry or workspace settings.

**Data flow**: It receives an ExtensionContext or nothing. If the context is missing, it raises an error explaining that site objects must read through their ExtensionContext. If it is present, it returns the same context unchanged.

**Call relations**: Most operations pass through this guard before touching workspace data. _sites uses it to create a HostedSites store, and methods such as _member_rows, member_conversation_rows, _member_object, _status, and _apply_owned use it when they need workspace information like public URLs or agent visibility.

*Call graph*: called by 6 (_apply_owned, _member_object, _member_rows, _status, member_conversation_rows, _sites).


##### `_sites`  (lines 107–109)

```
def _sites(ext: ExtensionContext | None) -> HostedSites
```

**Purpose**: Creates the store object used to read or update hosted site records for the current workspace. It is the file’s small doorway into the persistent site registry.

**Data flow**: It receives the request’s extension context, verifies it with _workspace, then uses the workspace id and current transaction from that context to create a HostedSites store. The output is an object that can list, update, or unregister sites.

**Call relations**: SiteObjects methods call this whenever they need site data. _member_rows and member_conversation_rows read from it, _apply_owned changes visibility through it, _delete_owned unregisters a site through it, and _find uses it to search all registered sites.

*Call graph*: calls 1 internal fn (_workspace); called by 5 (_apply_owned, _delete_owned, _find, _member_rows, member_conversation_rows); 1 external calls (__init__).


##### `effective_visibility`  (lines 112–118)

```
def effective_visibility(site: HostedSite, agents: Mapping[UUID, str]) -> Visibility
```

**Purpose**: Decides which visibility rule actually applies to a site. A normal site uses its own setting, while an agent homepage uses the agent’s visibility instead.

**Data flow**: It receives a HostedSite record and a mapping from agent ids to their visibility levels. If the site is not bound to an agent, it returns the site’s own visibility. If it is bound to an agent, it looks up that agent and converts the agent’s visibility into the site visibility scale, then returns that value.

**Call relations**: _member_rows uses this when showing list rows, _member_object uses it when returning object details, and _apply_owned uses it to decide whether a requested visibility change would actually change anything. It calls visibility_level for the agent-bound case.

*Call graph*: called by 3 (_apply_owned, _member_object, _member_rows); 1 external calls (visibility_level).


##### `_summary`  (lines 121–122)

```
def _summary(site: HostedSite, visibility: Visibility) -> str
```

**Purpose**: Creates a short human-readable summary for a listed site. It gives enough information to recognize the site quickly: its name, visibility, and sandbox port.

**Data flow**: It receives a HostedSite and the visibility that should be shown. It formats those values with the site’s port into one compact text string. The output is used as the row summary in object listings.

**Call relations**: _member_rows calls this while building each OwnedRow for list results. It is a presentation helper, not a place where permissions are decided.

*Call graph*: called by 1 (_member_rows).


##### `_preview_url`  (lines 125–132)

```
def _preview_url(scoped: ExtensionContext, site: HostedSite) -> str | None
```

**Purpose**: Returns a temporary signed URL for the site’s preview image, if the deployment captured one. A signed URL is a link that carries permission to view a protected image for this purpose.

**Data flow**: It receives the extension context and a HostedSite. If the site has no preview blob key or no recorded preview size, it returns nothing. Otherwise it asks the context to create an image preview URL from the stored blob key and size, and returns that link.

**Call relations**: _member_rows calls this when adding optional fields to site list rows. The actual URL creation is handed off to ExtensionContext.image_preview_url, so this helper only decides whether enough preview data exists.

*Call graph*: calls 1 internal fn (image_preview_url); called by 1 (_member_rows).


##### `SiteObjects._admin_can_apply`  (lines 153–154)

```
def _admin_can_apply(self, old: SiteSpec, spec: SiteSpec) -> bool
```

**Purpose**: Defines the special rule for workspace administrators changing site visibility. An admin may make a non-private site private, but may not broaden access.

**Data flow**: It receives the old site specification and the requested new one. It compares their visibility values and returns true only when the old value was not private and the requested value is private. It does not change any data itself.

**Call relations**: This is a policy hook used by the shared MemberReadableObjects machinery when deciding whether an administrator may apply a change. The later _apply_owned method performs the actual update only after the framework has allowed the request.


##### `SiteObjects._listed`  (lines 156–157)

```
def _listed(self, row: OwnedRow[GeneratedObjectOwner], query: ObjectListQuery) -> bool
```

**Purpose**: Decides whether a row should appear in ordinary listings. Agent homepage sites are hidden unless the caller explicitly filters for homepage bindings.

**Data flow**: It receives an already-built owned row and the list query. If the row has no homepage_agent field, it is listed normally. If it does have that field, it is listed only when the query includes a homepage_agent filter.

**Call relations**: This is another hook for the object listing framework. It works with _member_rows, which adds the homepage_agent field for agent-bound sites, and with list, which can translate the special 'mine' filter before the framework applies listing rules.


##### `SiteObjects.list`  (lines 159–169)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Customizes site listing so an agent can ask for its own homepage using the word 'mine'. This is needed because a running turn may know the current agent from context even if the query did not include its id.

**Data flow**: It receives the tool context and an object list query. If the query filter says homepage_agent is 'mine', it replaces that filter value with the current turn’s agent id from the context. It then passes the adjusted query to the parent listing implementation and returns that result.

**Call relations**: This method is called at the start of a site list request. After rewriting the viewer-relative 'mine' filter, it hands control back to the shared MemberReadableObjects list flow, where hooks like _member_rows and _listed do the detailed row building and filtering.

*Call graph*: 1 external calls (replace).


##### `SiteObjects._member_rows`  (lines 171–217)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the full set of site rows a member-readable object list can work from. Each row includes ownership, visibility, links, preview information, and other fields used for filtering and display.

**Data flow**: It receives the extension context and the member id, if known. It reads all hosted sites, gets agent visibility values, asks for owner email addresses, and computes each site’s object name and effective visibility. It returns a tuple of OwnedRow objects, each carrying a summary, owner metadata, and fields such as conversation id, created time, visibility, URL, preview URL, creator email, and homepage agent when relevant.

**Call relations**: The shared listing and object framework calls this to obtain raw rows. It uses _workspace and _sites to reach workspace data, _named and site_object_name indirectly for names, effective_visibility for permission-facing visibility, _summary for display text, _preview_url for thumbnails, and owner_emails to decorate rows with creator addresses.

*Call graph*: calls 6 internal fn (_named, _preview_url, _sites, _summary, _workspace, effective_visibility); 4 external calls (__init__, __init__, owner_emails, site_url).


##### `SiteObjects.member_conversation_rows`  (lines 219–244)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Reports which site objects should be visible from a particular conversation for a particular member. These grants let chat or conversation views see the same site objects that the hosting system knows about.

**Data flow**: It receives the extension context, conversation id, member id, admin flag, and result limit. It reads agent visibility values, asks the HostedSites store for sites visible in that conversation under those rules, and converts each visible site into a ConversationObjectGrant with the generated site object name, generation number, and a flag saying the content is visible.

**Call relations**: This method is called when the conversation object system needs per-conversation grants. It uses _workspace for agent visibility, _sites for the site registry query, and site_object_name to name each granted object consistently.

*Call graph*: calls 3 internal fn (_sites, _workspace, site_object_name); 1 external calls (__init__).


##### `SiteObjects._member_object`  (lines 246–269)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SiteSpec] | None
```

**Purpose**: Builds the detailed object view for one site. This is what a caller gets when asking for a specific site object’s stored specification and links.

**Data flow**: It receives the context, object name, owner metadata, and optional member id. It looks up the hosted site by name. If none exists, it returns nothing. If found, it computes the effective visibility, creates a SiteSpec with that visibility, and returns ObjectDetail containing creation and update times plus a link to the conversation where the site was created.

**Call relations**: The object framework calls this during an object get/read operation. It relies on _find to locate the site, _workspace to read agent visibility, effective_visibility to report the right access level, and object model classes such as ObjectDetail and ObjectLink to package the response.

*Call graph*: calls 3 internal fn (_find, _workspace, effective_visibility); 4 external calls (__init__, __init__, __init__, __init__).


##### `SiteObjects._status`  (lines 271–298)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns operational details for a hosted site, including its live URL and, when available, the source files that can be edited. This is more practical than the object spec: it helps someone update or inspect the deployed page.

**Data flow**: It receives the tool context, object name, and owner metadata. It finds the site and returns nothing if it no longer exists. Otherwise it builds a status dictionary with the site name, port, creator id, hosted URL, deploy generation, homepage agent id, and placeholders for source path and files. If the site has a stored source manifest, it materializes those source files into the sandbox and fills in the path and file list.

**Call relations**: This runs when the object system asks for status information. It uses _find for lookup, _workspace and site_url to build the public link, and materialize_source to restore static source files into the current sandbox when they exist.

*Call graph*: calls 2 internal fn (_find, _workspace); 2 external calls (materialize_source, site_url).


##### `SiteObjects._apply_owned`  (lines 300–336)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SiteSpec, old: SiteSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Applies a permitted visibility change to an existing site. It refuses object creation here because sites can only come into existence through deployment, where the serving port is known.

**Data flow**: It receives the tool context, object name, requested SiteSpec, old spec, and owner information. If there is no old object or owner, it raises an error saying sites must be deployed. It finds the current site, compares the requested visibility with the effective current visibility, refuses direct changes for agent homepage sites, and updates the site registry when a real change is needed. If the site becomes public and has a stored preview but no share card yet, it asks another helper to draw the public sharing card from that preview.

**Call relations**: The object framework calls this after permission checks decide a user may apply a change. It uses _find to guard against races where the site was unhosted, _workspace and effective_visibility to understand the current rule, _sites to write the new visibility, and draw_from_stored_shot to prepare public link previews when needed.

*Call graph*: calls 4 internal fn (_find, _sites, _workspace, effective_visibility); 2 external calls (__init__, draw_from_stored_shot).


##### `SiteObjects._delete_owned`  (lines 338–342)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Unhosts an existing site by removing it from the hosted site registry. After this, the permanent link for that registered site stops resolving.

**Data flow**: It receives the tool context, object name, and owner metadata. It finds the site by object name. If the site has already disappeared, it raises an error. If found, it tells the HostedSites store to unregister the site using its conversation id and site name.

**Call relations**: The object framework calls this for an allowed delete operation. It depends on _find to translate the object name into a HostedSite record, then uses _sites to perform the unregister operation in the registry.

*Call graph*: calls 2 internal fn (_find, _sites).


##### `SiteObjects._find`  (lines 344–345)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> HostedSite | None
```

**Purpose**: Looks up one hosted site by its workspace object name. It is the common search helper used before reading, changing, showing status for, or deleting a site.

**Data flow**: It receives the extension context and an object name. It reads all hosted sites through the HostedSites store, converts them into a name-keyed dictionary with _named, and returns the matching HostedSite if present. If no matching object name exists, it returns nothing.

**Call relations**: _member_object, _status, _apply_owned, and _delete_owned all call this before doing their main work. It uses _sites to read the registry and _named to apply the same object naming rule used by listings and grants.

*Call graph*: calls 2 internal fn (_named, _sites); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `request handling for page object list/get/delete operations`

A “page” here is one document that the content sync system copied in from an outside source, such as an issue tracker or another provider stream. This file gives those synced documents a safe object interface: users can browse them, open one by its id, and see its metadata and a bounded slice of its body. They cannot create or update pages, because the outside source and the sync driver own that job.

The main idea is like a library reading room. The sync driver brings books in and catalogs them. This file lets visitors see the catalog and read a copy, but not rewrite the book. If an admin deletes a page, it is really “forgotten” or tombstoned, so the rest of the indexing pipeline can clean up derived search state.

The private `_Page` class is a tidy internal wrapper around the database-style page record. It formats names, summaries, links back to the source that synced the page, and the public `PageSpec` shown to callers. `PageObjects` is the object-store face: list pages, get one page with its body from blob storage, refuse create/update, and admin-gate deletion. An important safety detail is that `get` rechecks the page after reading the body, so it does not return stale body text if the page changed mid-read.

#### Function details

##### `_require_ext`  (lines 62–65)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small guard makes sure the tool request has an `ExtensionContext`, which is the project’s gateway to extension-owned data such as sources and synced pages. Without it, page operations would have no safe way to reach the page store.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns that. If not, it stops the operation by raising an error, because continuing would mean the page object was dispatched without the data access it needs.

**Call relations**: PageObjects._pages uses it before reading sources and pages, PageObjects.get uses it before rechecking readable page state, and PageObjects.delete uses it before forgetting a page. It is the early checkpoint those flows pass through before touching extension data.

*Call graph*: called by 3 (_pages, delete, get).


##### `_page_timestamp`  (lines 68–78)

```
def _page_timestamp(provider_value: str | None, row_value: datetime) -> str
```

**Purpose**: This function turns page timestamps into one consistent UTC text format. It accepts either the provider’s original timestamp string or, when that is missing, the row’s stored timestamp.

**Data flow**: It receives an optional provider timestamp and a stored datetime. If the provider timestamp is absent, it uses the stored value and adds UTC when needed. If the provider timestamp is present, it parses it, requires that it include a timezone, converts it to UTC, and returns an ISO-formatted string with microseconds. Bad or timezone-less provider timestamps become clear errors.

**Call relations**: _Page.spec and _Page.fields call this when preparing page details and list fields. That keeps timestamps shown in both the list view and the detail view consistent.

*Call graph*: called by 2 (fields, spec); 2 external calls (fromisoformat, replace).


##### `_Page.name`  (lines 99–100)

```
def name(self) -> str
```

**Purpose**: This property gives the page its public object name. The name is simply the page row’s UUID written as text.

**Data flow**: It reads the internal UUID stored on the `_Page`. It converts that UUID into a string and returns it; it does not change anything.

**Call relations**: Page listing and lookup rely on page names to identify objects. When higher-level code asks a `_Page` for its name, this property supplies the stable id that callers use with object_get and delete.


##### `_Page.links`  (lines 102–110)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: This builds a link from a page back to the source binding that synced it, when that source name is known. The link helps readers understand where the page came from.

**Data flow**: It reads the page’s optional source name. If there is no source name, it returns no links. If there is one, it creates an object link with relation `synced_by` pointing at the matching source object.

**Call relations**: When a page detail is returned, this method supplies the relationship information attached to that detail. It hands off to the object-link and object-reference types so the wider object system can display the connection in its standard form.

*Call graph*: 2 external calls (__init__, __init__).


##### `_Page.spec`  (lines 112–125)

```
def spec(self, body: str, body_truncated: bool) -> PageSpec
```

**Purpose**: This turns an internal `_Page` plus already-read body text into the public `PageSpec` that callers see. It gathers the source, stream, title, timestamps, visibility subject, digest, body reference, truncation flag, and body.

**Data flow**: It receives the page body text and a flag saying whether the body was cut short. It reads the `_Page` metadata, normalizes the created and updated timestamps through `_page_timestamp`, and returns a `PageSpec` object. It does not fetch the body itself; the caller has already done that.

**Call relations**: PageObjects.get reads and bounds the body first, then uses this method to package the final page detail. `_Page.spec` in turn relies on `_page_timestamp` so detail responses use the same timestamp rules as list rows.

*Call graph*: calls 1 internal fn (_page_timestamp); 1 external calls (__init__).


##### `_Page.summary`  (lines 127–128)

```
def summary(self) -> str
```

**Purpose**: This creates a short human-readable line for a page in list results. It combines the title, source provider, stream, and visibility subject.

**Data flow**: It reads the page’s title, backend, stream, and subject. It joins them into one sentence-like string and cuts it to the configured maximum length so list rows stay compact.

**Call relations**: PageObjects.list uses this kind of summary when turning pages into object rows. It is the quick label a user sees before opening the full page detail.


##### `_Page.fields`  (lines 130–138)

```
def fields(self) -> dict[str, JsonValue]
```

**Purpose**: This prepares the searchable and sortable metadata fields shown in page listings. It includes the source id, source provider, stream, title, and normalized timestamps.

**Data flow**: It reads selected metadata from the `_Page`, converts the source id to text, normalizes the created and updated timestamps with `_page_timestamp`, and returns a dictionary of JSON-friendly values.

**Call relations**: PageObjects.list uses these fields when building list rows, and the object kind declares these as the fields callers can filter or order by. This keeps the list view aligned with the object kind’s advertised behavior.

*Call graph*: calls 1 internal fn (_page_timestamp).


##### `PageObjects.list`  (lines 149–154)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a page of visible synced pages for the caller. It is the list operation behind the `page` object kind.

**Data flow**: It receives the tool context and a list query containing things like filtering, ordering, or pagination. It asks `_pages` for the synced pages the caller may see, turns each one into an object row with a name, summary, and fields, then passes those rows through `object_page` to apply the query and produce the final list response.

**Call relations**: This is called when someone lists `page` objects. It depends on PageObjects._pages to gather readable page records, then hands the formatted rows to the common object paging helper so page listing behaves like other object kinds.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, object_page).


##### `PageObjects.get`  (lines 156–201)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None
```

**Purpose**: This returns the full detail for one synced page, including a safely bounded amount of body text. It also protects against returning a mismatched body if the page changes while it is being read.

**Data flow**: It receives the tool context and the page name. It finds the matching visible page, streams the body bytes from blob storage up to one byte past the maximum limit, decodes a valid UTF-8 string, and notes whether the body was truncated. Then it rechecks the current readable page state using the caller’s source-reader permissions. If the page disappeared or its subject, revision, digest, or body reference changed during the read, it returns nothing; otherwise it returns an object detail with the spec, timestamps, and links.

**Call relations**: This is called when someone opens a page by name. It first uses PageObjects._find for the visible page lookup, uses ToolContext.source_reader when checking permissions/state, calls _require_ext to reach extension state, and finally packages the result as an ObjectDetail.

*Call graph*: calls 3 internal fn (source_reader, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects.status`  (lines 203–210)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no separate apply/status information for pages. Synced pages are not user-authored resources with an in-progress desired state, so there is nothing meaningful to report here.

**Data flow**: It receives the context, name, and optional expected generation. It ignores them and returns `None`, meaning there is no status object for this page kind.

**Call relations**: The object framework can ask object kinds for status. For pages, this method is the deliberate no-op branch because the sync driver, not this object interface, owns page creation and updates.


##### `PageObjects.apply`  (lines 212–221)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to create or update a page through the object interface. Pages come from source sync, so accepting user-written specs here would create a false second source of truth.

**Data flow**: It receives the context, target name, desired page spec, optional old spec, and optional generation check. Instead of changing anything, it raises `VerbNotSupported` with a message explaining that pages are landed by the sync driver.

**Call relations**: The object framework calls this for create/update-style operations. This method immediately stops that flow and hands back the standard unsupported-verb error.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 223–235)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This lets a workspace admin forget one synced page. Forgetting tombstones the page so downstream cleanup can remove derived index data, instead of directly editing content.

**Data flow**: It receives the context, page name, and optional expected generation. It first asks whether the speaker is an admin. If not, it raises `AdminRequired`. If the speaker is an admin, it finds the visible page by name; if no such page exists, it raises an error. When the page is found, it asks the extension context to forget that page id.

**Call relations**: This is called for page delete operations. It uses ToolContext.speaker_is_admin for the permission gate, PageObjects._find for the page lookup, and _require_ext to call the extension’s `forget_page` action.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 237–238)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This searches the caller-visible page list for one page with the requested object name. It is the shared lookup used before reading or deleting a page.

**Data flow**: It receives the context and a page name. It asks `_pages` for all pages currently visible to the caller, compares each page’s name to the requested name, and returns the first match. If nothing matches, it returns `None`.

**Call relations**: PageObjects.get calls this before reading a page body, and PageObjects.delete calls it before forgetting a page. It delegates the real visibility-aware collection step to PageObjects._pages.

*Call graph*: calls 1 internal fn (_pages); called by 2 (delete, get).


##### `PageObjects._pages`  (lines 240–268)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This gathers the live synced pages the caller is allowed to read and enriches them with source information. It is the common source of page records for list and lookup operations.

**Data flow**: It receives the tool context. It gets the extension context, reads registered sources, builds a map from source id to backend name, and builds friendly source object names for known connector sources. Then it asks for readable source pages using the caller’s source-reader permissions, converts each raw record into a `_Page`, and returns them as a tuple.

**Call relations**: PageObjects.list calls this to build list rows, and PageObjects._find calls it when get or delete needs one named page. Inside, it uses ToolContext.source_reader to respect caller visibility, validates connector source configuration, and uses `binding_name` to create links back to source objects.

*Call graph*: calls 2 internal fn (source_reader, _require_ext); called by 2 (_find, list); 3 external calls (__init__, model_validate, binding_name).


### `extensions/gbrain/ufo_ext_gbrain/objects.py`

`domain_logic` · `request handling`

A gbrain source is like a subscription to a pile of Markdown pages. The pile can come from a GitHub repository, or from a local folder configured by the system operator. This file makes that subscription available through UFO’s object verbs, such as apply, get, list, status, and delete.

The most important rule is that the object name is not chosen by the user. It is calculated from the source’s real origin: repository, branch, or folder path. That prevents two people from accidentally giving the same source different names, and it makes re-applying the same source safe.

The file also protects ownership. A source is private to the member who registered it unless it is explicitly shared with the whole workspace. Only the original registering member can turn a private source into a shared one. A shared source cannot be made private again; it must be deleted and recreated. Resyncing is treated as an action, not lasting state: asking for resync schedules an immediate sync but does not change the stored source.

Local folder sources are special. They read the server’s own filesystem, so regular users are not allowed to create them through chat. They can only arrive from operator configuration at startup. Without this file, gbrain sources would lack consistent names, access rules, and safe registration behavior.

#### Function details

##### `gbrain_source_name`  (lines 57–64)

```
def gbrain_source_name(repo: str | None, branch: str | None, root: str | None) -> str
```

**Purpose**: Creates the official object name for a gbrain source from its origin. This keeps names stable and prevents users from inventing conflicting names for the same repository, branch, or folder.

**Data flow**: It receives a repository name, a branch name, and a folder root, where some may be empty. It turns those values into a sorted JSON string, hashes that string, takes the first short piece of the hash, and returns a name like “gbrain-1a2b3c4d”.

**Call relations**: When the code needs to know what a source should be called, _origin and _Registered.name call this helper. That lets both new specs and already-stored rows follow the same naming rule.

*Call graph*: called by 2 (name, _origin); 2 external calls (sha256, dumps).


##### `GbrainSpec.validate_origin`  (lines 97–102)

```
def validate_origin(self) -> 'GbrainSpec'
```

**Purpose**: Checks that a requested gbrain source describes exactly one origin. A source must be either a GitHub repository or a local folder, not both and not neither.

**Data flow**: It reads the fields in a GbrainSpec after they have been filled in. If repo and root are both set or both missing, it rejects the spec. If a branch is given without a repository, it also rejects the spec. If the values make sense, it returns the same spec unchanged.

**Call relations**: This validation runs as part of building or reading a GbrainSpec. It protects later code, such as _origin and GbrainObjects._apply_owned, from having to guess what kind of source the user meant.


##### `_origin`  (lines 112–126)

```
def _origin(spec: GbrainSpec) -> _Origin
```

**Purpose**: Turns a valid gbrain spec into the concrete backend information needed to register it. In plain terms, it decides whether this is a GitHub source or a folder source, builds the matching configuration, and calculates the official name.

**Data flow**: It receives a GbrainSpec. If the spec has a root, it builds a folder configuration and a folder-based name. If the spec has a repo, it builds a Git configuration using the repo and optional branch, then makes a repo-based name. It returns an _Origin containing the backend type, backend config, and derived object name.

**Call relations**: GbrainObjects.apply uses this while checking whether another private registration already exists. GbrainObjects._apply_owned uses it just before registering or updating a source, so the stored source and the object name agree.

*Call graph*: calls 1 internal fn (gbrain_source_name); called by 2 (_apply_owned, apply); 3 external calls (__init__, __init__, __init__).


##### `_identity`  (lines 129–130)

```
def _identity(spec: GbrainSpec) -> tuple[str | None, str | None, str | None, bool]
```

**Purpose**: Extracts the parts of a spec that define whether two source requests are the same. It includes the origin and whether the source is shared.

**Data flow**: It receives a GbrainSpec and returns a simple tuple containing repo, branch, root, and shared. That tuple is easy to compare with another spec’s tuple.

**Call relations**: GbrainObjects.apply uses this to recognize a no-op re-apply of the same source. GbrainObjects._resync uses it to make sure a resync request is not secretly trying to change the source at the same time.

*Call graph*: called by 2 (_resync, apply).


##### `_Registered.name`  (lines 147–148)

```
def name(self) -> str
```

**Purpose**: Provides the official object name for a source row that is already registered. It keeps stored records aligned with the same naming rule used for new requests.

**Data flow**: It reads the registered row’s repo, branch, and root values. It passes them to gbrain_source_name and returns the calculated name.

**Call relations**: _registered_from_ext creates _Registered records from stored source rows, and code that lists or finds those records uses this property to compare them with object names.

*Call graph*: calls 1 internal fn (gbrain_source_name).


##### `_Registered.spec`  (lines 150–156)

```
def spec(self) -> GbrainSpec
```

**Purpose**: Turns a stored source row back into the user-facing spec shape. This is what allows get/read operations to show the source as a clean GbrainSpec.

**Data flow**: It reads the registered row’s repo, branch, root, and subject. It converts the subject into a shared true-or-false value and returns a GbrainSpec. The resync flag is not set, because resync is an action rather than stored state.

**Call relations**: GbrainObjects._member_object calls this when returning details for one named gbrain source. It bridges the internal stored row and the object API’s public view.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Registered.summary`  (lines 158–162)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable label for a registered source. This helps list views show what each source points to without dumping all details.

**Data flow**: It reads the stored origin. For a folder, it returns text like “server directory …”. For a repository, it returns text like “github repository owner/name” or includes “@branch” if a branch is set. The result is trimmed to a fixed maximum length.

**Call relations**: GbrainObjects._member_rows uses this when building list entries. Error messages in GbrainObjects.apply also use it to explain when a private source is already registered by someone else.


##### `_require_ext`  (lines 165–168)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure the gbrain object code has its extension context before doing work. The extension context is the object that knows how to read and change registered sources.

**Data flow**: It receives either an ExtensionContext or nothing. If the context is present, it returns it. If it is missing, it raises a runtime error because source operations cannot safely continue.

**Call relations**: Many helpers and object methods call this before reading sources, granting access, scheduling syncs, registering sources, or deleting sources. It is a small guardrail that catches incorrect dispatch early.

*Call graph*: called by 6 (_apply_owned, _delete_owned, _grant_settled, _member_rows, _resync, _registered_named).


##### `_registered_from_ext`  (lines 171–196)

```
async def _registered_from_ext(ext: ExtensionContext) -> tuple[_Registered, ...]
```

**Purpose**: Reads all registered gbrain-compatible sources from the extension system and converts them into a local, easy-to-use shape. It filters out sources that belong to other backends.

**Data flow**: It asks the extension context for all source records. For Git-backed records, it validates the Git config and extracts repo and branch. For folder-backed records, it validates the folder config and extracts root. It wraps each recognized record as a _Registered value and returns them as a tuple.

**Call relations**: _member_rows uses this to list all gbrain sources. _registered_named uses it to find one source by its derived object name.

*Call graph*: calls 1 internal fn (sources); called by 2 (_member_rows, _registered_named); 3 external calls (__init__, model_validate, model_validate).


##### `_registered_named`  (lines 199–203)

```
async def _registered_named(ext: ExtensionContext | None, name: str) -> _Registered | None
```

**Purpose**: Finds the registered gbrain source with a particular object name, if one exists. It is the central lookup helper for operations that start from a name.

**Data flow**: It receives an optional extension context and a name. It first requires a real context, then gets all registered gbrain sources, compares each source’s derived name with the requested name, and returns the matching _Registered row or nothing.

**Call relations**: Apply, get, status, grant, resync, update, and delete paths all call this when they need to turn a user-facing object name into the stored source row behind it.

*Call graph*: calls 2 internal fn (_registered_from_ext, _require_ext); called by 7 (_apply_owned, _delete_owned, _grant_settled, _member_object, _resync, _status, apply).


##### `GbrainObjects.apply`  (lines 221–252)

```
async def apply(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Implements the high-level apply behavior for gbrain sources. It decides whether the request is a resync, a harmless re-apply, a new registration, or a sharing change, then enforces the special rules before handing normal changes to the base object system.

**Data flow**: It receives the tool context, requested object name, desired spec, any visible old spec, and an expected generation value used by the object framework. If resync is true, it routes to _resync. If the submitted spec is identical to the visible old one, it grants the current agent access and stops. If this is a new visible object, it checks whether the same origin is already privately registered by someone else and refuses if the caller should not use it. Otherwise it continues through the parent apply flow.

**Call relations**: This is the first gbrain-specific stop in the apply path. It calls _resync for immediate sync requests, _grant_settled for no-op re-applies, and relies on _origin, _identity, and _registered_named for the checks that happen before the base class performs ownership-gated mutation.

*Call graph*: calls 6 internal fn (speaker_is_admin, _grant_settled, _resync, _identity, _origin, _registered_named); 2 external calls (__init__, subject_shared).


##### `GbrainObjects._grant_settled`  (lines 254–269)

```
async def _grant_settled(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Grants the current agent access to an already-settled source when the user re-applies the exact same spec. This matters because no new registration happens in that case, but the agent may still need permission to read the source’s synced pages.

**Data flow**: It reads the speaking member, the source owner, and the registered source row. If there is no live speaker, no owner, or the speaker is not allowed to use the source, it does nothing. Otherwise it asks the extension context to grant that source to the current agent.

**Call relations**: GbrainObjects.apply calls this only for identical re-applies. It uses _registered_named to find the stored row and _require_ext to perform the grant through the extension system.

*Call graph*: calls 2 internal fn (_registered_named, _require_ext); called by 1 (apply).


##### `GbrainObjects._resync`  (lines 271–291)

```
async def _resync(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None) -> None
```

**Purpose**: Schedules an immediate sync for an existing source. It is deliberately narrow: the request must match the current spec and may not also change the source’s origin or sharing.

**Data flow**: It receives context, name, submitted spec, and the old visible spec. It rejects the request if the old source is missing or the submitted identity differs. It checks whether the caller can see the object, then checks whether the caller is the owner or an admin. If allowed, it finds the registered source row and asks the extension context to schedule a sync now.

**Call relations**: GbrainObjects.apply sends resync requests here when spec.resync is true. This method uses the same lookup helpers as other operations, then hands off to the extension context to actually schedule the background syncing work.

*Call graph*: calls 4 internal fn (speaker_is_admin, _identity, _registered_named, _require_ext); called by 1 (apply); 3 external calls (__init__, __init__, __init__).


##### `GbrainObjects._member_rows`  (lines 293–306)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the rows shown when a member lists gbrain source objects. Each row contains the object name, a short summary, and ownership information.

**Data flow**: It receives the extension context and an optional member id. It reads all registered gbrain sources, converts each one into an OwnedRow with a derived name, summary text, and an ObjectOwner that says whether it is shared and who owns it. It returns all rows as a tuple.

**Call relations**: The base MemberReadableObjects machinery calls this when it needs the raw set of gbrain objects to filter and show. It relies on _registered_from_ext to read the source records from the extension system.

*Call graph*: calls 2 internal fn (_registered_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `GbrainObjects._member_object`  (lines 308–323)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[GbrainSpec] | None
```

**Purpose**: Builds the detailed object view for one named gbrain source. This is the data behind a get/read operation.

**Data flow**: It receives the extension context, object name, known owner, and optional member id. It looks up the registered source by name. If none exists, it returns nothing. If found, it returns an ObjectDetail containing the user-facing spec plus the creation and update timestamps.

**Call relations**: The base readable-object flow calls this after ownership and visibility have been considered. It uses _registered_named for lookup and _Registered.spec to turn the stored row into public object details.

*Call graph*: calls 1 internal fn (_registered_named); 1 external calls (__init__).


##### `GbrainObjects._status`  (lines 325–339)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns operational status for a gbrain source, such as when it will sync next and how many sync errors have happened in a row. This is useful for understanding whether a source is healthy.

**Data flow**: It receives the tool context, source name, and owner. It looks up the registered row. If it is missing, it returns nothing. Otherwise it builds a dictionary with shared status, next sync time as text, and consecutive error count. For private sources with an owner, it also includes the owner member id.

**Call relations**: The object framework calls this when status information is requested. It uses _registered_named to find the source and reads fields from the registered row without changing anything.

*Call graph*: calls 1 internal fn (_registered_named); 1 external calls (subject_shared).


##### `GbrainObjects._apply_owned`  (lines 341–379)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Performs the actual create or update once the base object system has decided the caller is allowed to mutate this owned object. It registers new GitHub sources, shares private sources when allowed, refuses user-created folder roots, and grants the current agent access.

**Data flow**: It receives context, name, desired spec, old spec, and owner. It requires a speaking member, refuses specs that name a local root, calculates the official origin and name, and rejects the request if the supplied name is wrong. If no source is registered yet, it registers the source with either a member-private subject or the shared subject. If it already exists, it refuses changing shared back to private, optionally flips private to shared, and grants the source to the current agent.

**Call relations**: This method is called by the parent apply flow after GbrainObjects.apply has done its special pre-checks. It uses _origin and _registered_named to connect the requested object to the stored source, then delegates real storage changes to the extension context.

*Call graph*: calls 3 internal fn (_origin, _registered_named, _require_ext); 3 external calls (__init__, member_subject, subject_shared).


##### `GbrainObjects._delete_owned`  (lines 381–385)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a registered gbrain source once the object framework has confirmed the caller has deletion rights. Removing the source also lets the wider page cleanup pipeline tombstone the synced pages.

**Data flow**: It receives the context, object name, and owner. It looks up the registered source by name. If none exists, it reports that the object is unknown. If found, it asks the extension context to remove that source by id.

**Call relations**: The base object delete flow calls this after applying the registrar-or-admin delete rule. It uses _registered_named for the name-to-row lookup and _require_ext to perform the removal through the extension system.

*Call graph*: calls 2 internal fn (_registered_named, _require_ext); 1 external calls (__init__).


### Platform and Workspace Administration
Administrative object kinds describe installed extensions, credential slots, workspace members, and the workspace itself without exposing unsafe internals.

### `core/src/ufo/ext/extension_kind.py`

`domain_logic` · `startup and object request handling`

This file turns the deploy’s loaded extension manifests into an object kind called `extension`. A manifest is the extension’s declaration: its name, version, and what it adds to the system. This is like a public directory card for each plugin: it says what the plugin offers and what it asks for, but it does not contain private keys or live stored data.

The file first defines how extension names become object names. Manifest names are lowercased and changed into hyphen-style names, so something like `scheduled_tasks` becomes `scheduled-tasks`. If two manifests would produce the same object name, startup fails loudly instead of hiding one behind the other.

The main data shape is `ExtensionSpec`, which is the readable summary of an extension: tools, object kinds, credential slot names, surfaces, jobs, hook events, sources, and subagent profiles. The main handler, `ExtensionObjects`, can list extensions, get one extension’s detail, and report status fields such as whether sandbox tools need internet access and what other extension seams are required.

Importantly, this object kind is read-only. Creating, updating, or deleting an extension is refused because installing or removing extensions is a deployment action done through the lockfile and `ufoctl`, not something a chat turn or object write should change.

#### Function details

##### `named_extensions`  (lines 46–61)

```
def named_extensions(manifests: tuple[Manifest, ...]) -> dict[str, Manifest]
```

**Purpose**: This function gives each loaded extension manifest its object name. It exists so the rest of the system can address extensions using the same lowercase, hyphenated naming rules as other objects.

**Data flow**: It takes a tuple of extension manifests. For each manifest, it lowercases the manifest name, replaces non-letter-or-number runs with hyphens, trims extra hyphens, and checks that the result is a valid object name. It returns a dictionary from that object name to the original manifest. If two manifests collapse to the same object name, it raises an error instead of choosing one silently.

**Call relations**: This is used when the active manifest set is being prepared for exposure as `extension` objects. It relies on regular-expression text replacement to shape the name, then hands the result to the shared object-name validator so extension names follow the same rules as the rest of the workspace.

*Call graph*: 2 external calls (sub, validate_object_name).


##### `ExtensionObjects.list`  (lines 90–107)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This function returns a paged list of all active extensions. Each row gives a quick human summary: the extension’s version, how many tools it adds, and how many credential slots it declares.

**Data flow**: It reads the handler’s stored mapping of extension names to manifests, sorts them by name, and turns each manifest into an `ExtensionSpec`. From each spec it builds an object-list row with a short summary and searchable fields such as version, tool count, and credential slot count. It then passes those rows plus the caller’s list query into the paging helper, which returns the final page.

**Call relations**: When someone lists objects of kind `extension`, this method is the read path. It calls `ExtensionObjects._spec` to convert raw manifests into public declarations, builds `ObjectRow` entries for the listing, and hands everything to `object_page` so normal object-list filtering, ordering, and paging behavior is applied.

*Call graph*: calls 1 internal fn (_spec); 2 external calls (__init__, object_page).


##### `ExtensionObjects.get`  (lines 109–113)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ExtensionSpec] | None
```

**Purpose**: This function returns the full readable declaration for one extension. It is used when a caller wants to inspect exactly what a specific extension contributes.

**Data flow**: It receives an object name and looks it up in the extension mapping. If no matching extension exists, it returns `None`. If it finds one, it converts the manifest into an `ExtensionSpec` and wraps it in an `ObjectDetail` with no creation or update timestamps, because these are deployment declarations rather than database rows.

**Call relations**: When someone reads one `extension` object by name, this method performs that lookup. It delegates the manifest-to-spec conversion to `ExtensionObjects._spec`, then packages the result as object detail for the object system to return.

*Call graph*: calls 1 internal fn (_spec); 1 external calls (__init__).


##### `ExtensionObjects.status`  (lines 115–128)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This function returns the deploy-facing status request for one extension. It shows what the extension asks from the deployment environment, such as sandbox internet access and required seams from other extensions.

**Data flow**: It receives an object name and checks the extension mapping. If the extension is absent, it returns `None`. If present, it reads `sandbox_internet` and `requires` from the manifest and returns them as a plain dictionary suitable for JSON-style output. It does not change anything.

**Call relations**: This is called when the object system asks for the status of an `extension` object. Unlike `get`, which describes what the extension contributes to users, this method describes what the extension needs from the deployment.


##### `ExtensionObjects.apply`  (lines 130–139)

```
async def apply(self, ctx: ToolContext, name: str, spec: ExtensionSpec, old: ExtensionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function deliberately refuses to create or update an extension object. It protects the rule that extensions are installed or changed through deployment tooling, not through object writes.

**Data flow**: It accepts the usual apply inputs: context, object name, new spec, optional old spec, and optional expected generation. It ignores those values for mutation purposes and raises a `VerbNotSupported` error explaining that installation and removal are deploy actions done through the lockfile.

**Call relations**: This is reached if someone tries to create or update an `extension` object. Instead of handing off to storage or changing a manifest, it stops the flow immediately with a clear error.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects.delete`  (lines 141–148)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This function deliberately refuses to delete an extension object. It exists so the object kind can participate in the common object interface while still remaining read-only.

**Data flow**: It receives the context, object name, and optional expected generation. It does not remove anything from the extension mapping. It raises a `VerbNotSupported` error that points the caller toward deployment-time extension removal instead.

**Call relations**: This is reached if someone tries to delete an `extension` object. Like `ExtensionObjects.apply`, it blocks mutation and reinforces that extension removal belongs to the deploy lockfile workflow.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects._spec`  (lines 150–168)

```
def _spec(self, manifest: Manifest) -> ExtensionSpec
```

**Purpose**: This helper turns a raw extension manifest into the public `ExtensionSpec` shown to callers. It gathers only names and declarations, not secret values or stored runtime data.

**Data flow**: It takes one manifest and reads its declared name, version, tools, connector tools, object kinds, credential slot names, surfaces, jobs, hook events, source backends, and subagent profile names. It places those values into an `ExtensionSpec`. For hooks, it keeps the unique event names and sorts them so the output is stable.

**Call relations**: This is the shared translation step used by both `ExtensionObjects.list` and `ExtensionObjects.get`. The listing path uses it to compute summaries and counts, while the get path uses it to return the full extension declaration.

*Call graph*: called by 2 (get, list); 1 external calls (__init__).


### `core/src/ufo/kinds/credential_kind.py`

`domain_logic` · `request handling`

Extensions can declare that they need a credential, such as an API key. This file turns those declarations into readable workspace objects: every declared slot is listed, whether or not someone has filled it yet. Think of it like a row of labeled locked boxes. Everyone can see the labels and whether each box is empty or full, but nobody can see what is inside through this object view.

The key safety rule is that secret values are never returned here. Reads show only the declaration: the slot name, description, extension, and any host information used for credential injection. The database row, when present, only proves that a value has been stored and gives timestamps. If no row exists, the slot still appears because the declaration comes from the extension manifest, not from the database.

Creating or updating a credential object is deliberately refused. Filling or rotating a secret must go through `request_credentials`, which is a separate private handoff flow. Deleting is allowed only for workspace admins, and it clears the stored value while keeping the declared slot visible as empty. This keeps the workspace inventory honest without turning the object API into a place where secrets can leak.

#### Function details

##### `CredentialObjects.list`  (lines 77–78)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the workspace’s credential slots as a paged list. It is used when a tool or API caller wants the overview: each declared slot, which extension declared it, and whether it is filled.

**Data flow**: It receives a tool context and a list query with paging or filtering information. It asks `_rows` to build the safe list entries, then passes those entries to `object_page` so the caller gets a standard page-shaped result. No credential value is read or returned.

**Call relations**: This is the main list path for credential objects. It relies on `_rows` to combine extension declarations with database fill state, then hands the result to the shared object paging helper so credentials behave like other object kinds.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.member_page`  (lines 80–90)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the same credential slot overview to a signed-in portal member. The view is workspace-wide because slot declarations are not scoped to individual members and do not reveal secrets.

**Data flow**: It receives optional extension context, member identity, admin status, and a list query. It ignores member-specific permissions for the content itself, builds rows through `_rows`, and wraps them into a standard object page. The output is only labels and filled-or-empty state.

**Call relations**: This is the member-facing counterpart to `CredentialObjects.list`. It uses the same `_rows` helper and the same paging helper, which keeps the portal view consistent with the tool-facing object list.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (object_page).


##### `CredentialObjects.get`  (lines 92–93)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Returns the safe detail view for one credential slot. It is used when a caller wants to inspect a slot’s declaration and timestamps without seeing the stored secret.

**Data flow**: It receives a tool context and a slot name. It passes the name to `_detail`, which looks up the declaration and any database timestamps. The result is either a safe object detail or `None` if no extension declared that slot.

**Call relations**: This is the tool-facing detail path. It delegates the real lookup and formatting work to `_detail`, so the same detail-building rule is shared with member portal reads.

*Call graph*: calls 1 internal fn (_detail).


##### `CredentialObjects.member_detail`  (lines 95–110)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[CredentialSpec] | None
```

**Purpose**: Returns the member portal’s detail view for one credential slot. It pairs the slot’s list-row summary with its detailed declaration, still without revealing any secret value.

**Data flow**: It receives optional extension context, a slot name, member identity, and admin status. It first asks `_detail` for the safe declaration and timestamps; if the slot is unknown, it returns `None`. Otherwise it rebuilds the rows, picks the row with the matching name, and returns a `MemberObject` containing both the row and the detail.

**Call relations**: This is the member-facing counterpart to `CredentialObjects.get`, with extra packaging for the portal. It depends on `_detail` for the detailed declaration and `_rows` for the list-style row that the portal expects beside it.

*Call graph*: calls 2 internal fn (_detail, _rows); 1 external calls (__init__).


##### `CredentialObjects.status`  (lines 112–136)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the live status of one credential slot: mainly whether it has been filled. If the slot has host information and a credential store is available, it can also report the selected or resolved host.

**Data flow**: It receives a context, a slot name, and an optional expected generation value. It checks the declared slot list; if the name is not declared, it returns `None`. For a real slot, it opens a workspace database transaction, looks for a credential row for the current workspace and slot, and returns a dictionary such as `filled: true` or `filled: false`. When possible, it also asks the credential store for host information and adds that to the result.

**Call relations**: This function is used when callers need a compact status rather than a full object detail. It uses `_named` to validate the slot name, the workspace database to check whether a stored value exists, and `credential_host` when host information must be resolved.

*Call graph*: calls 1 internal fn (_named); 4 external calls (select, credential_host, workspace_tx, ws_current).


##### `CredentialObjects.apply`  (lines 138–147)

```
async def apply(self, ctx: ToolContext, name: str, spec: CredentialSpec, old: CredentialSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a credential through the normal object API. This protects secrets by forcing fills and rotations through the private `request_credentials` flow.

**Data flow**: It receives the requested slot name, new specification, old specification, context, and optional generation check. Instead of saving anything, it immediately raises a `VerbNotSupported` error with a message explaining the correct route. Nothing is written and no credential value is accepted here.

**Call relations**: This is the safety gate for create/update behavior. When the object framework tries to apply a credential change, this function stops the flow before any database write and points callers to the credential handoff mechanism.

*Call graph*: 1 external calls (__init__).


##### `CredentialObjects.delete`  (lines 149–165)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Clears the stored value for a credential slot. Only a workspace admin can do this, and the slot declaration remains visible afterward as an empty slot.

**Data flow**: It receives a context, slot name, and optional generation check. It asks the context whether the speaker is a workspace admin; if not, it raises `AdminRequired`. If allowed, it finds the declared slot, opens a workspace transaction, and deletes the matching credential row for the current workspace. The result is no returned value, but the database no longer has a stored secret for that slot.

**Call relations**: This is the only mutating operation allowed in this object kind. It uses `_named` to map the public slot name to its declaration, checks admin permission through the tool context, and then performs the database delete inside the current workspace.

*Call graph*: calls 2 internal fn (_named, speaker_is_admin); 4 external calls (__init__, delete, workspace_tx, ws_current).


##### `CredentialObjects._rows`  (lines 167–179)

```
async def _rows(self) -> tuple[ObjectRow, ...]
```

**Purpose**: Builds the safe list rows for all declared credential slots. Each row says which extension owns the slot and whether it is filled, but never includes the secret.

**Data flow**: It first asks `_filled_slots` for the set of slot names that currently have stored database rows. It then asks `_named` for all declared slots, sorts them by name, and turns each one into an `ObjectRow` with a readable summary and fields for extension and filled state. The output is a tuple of rows ready for paging or portal display.

**Call relations**: This helper feeds all list-style views. `CredentialObjects.list`, `CredentialObjects.member_page`, and `CredentialObjects.member_detail` call it whenever they need the overview row for one or many credential slots.

*Call graph*: calls 2 internal fn (_filled_slots, _named); called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `CredentialObjects._detail`  (lines 181–205)

```
async def _detail(self, name: str) -> ObjectDetail[CredentialSpec] | None
```

**Purpose**: Builds the safe detail view for one declared credential slot. It describes the slot and includes creation/update timestamps if a stored value exists.

**Data flow**: It receives a slot name. It checks `_named` to see whether any active extension declared that slot; if not, it returns `None`. For a declared slot, it queries the current workspace’s credential table for timestamps, then creates a `CredentialSpec` containing the public declaration details and wraps it in an `ObjectDetail`. The stored credential value is not selected from the database.

**Call relations**: This helper is shared by the tool and member detail paths. `CredentialObjects.get` uses it directly, and `CredentialObjects.member_detail` uses it before adding the portal row wrapper.

*Call graph*: calls 1 internal fn (_named); called by 2 (get, member_detail); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


##### `CredentialObjects._named`  (lines 207–208)

```
def _named(self) -> dict[str, DeclaredSlot]
```

**Purpose**: Creates a name-to-slot lookup table from the declared credential slots. This makes it easy for other methods to validate a requested slot name and find its declaration.

**Data flow**: It reads the `slots` stored on the `CredentialObjects` instance. It passes them to `named_slots`, which returns a dictionary keyed by slot name. The output is used for fast lookup; it does not read the database or touch secret values.

**Call relations**: This is a small shared helper used anywhere the file needs to interpret a slot name. Detail, row building, status checks, and deletion all call it before deciding what to show or change.

*Call graph*: called by 4 (_detail, _rows, delete, status); 1 external calls (named_slots).


##### `CredentialObjects._filled_slots`  (lines 210–219)

```
async def _filled_slots(self) -> frozenset[str]
```

**Purpose**: Finds which credential slots currently have stored values in this workspace. It returns only slot names, not the values themselves.

**Data flow**: It opens a workspace database transaction and selects the slot column from credential rows belonging to the current workspace. It collects those names into a frozen set. The output lets callers mark declared slots as filled or empty without exposing the credential contents.

**Call relations**: This helper supports `_rows`. `_rows` combines its set of filled slot names with the manifest-declared slot list so list pages can show every slot’s fill state.

*Call graph*: called by 1 (_rows); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/kinds/members.py`

`domain_logic` · `request handling`

This file is the rulebook for workspace membership. A “member” here means a person in a workspace, along with two important switches: whether they are an admin, and whether they are seated. A seated member can use the workspace; an unseated member is still known to the workspace but is refused access when they try to enter.

The file solves two related problems. First, it lets agents and portal pages show member information without leaking the full roster in the wrong place. The main agent can show the workspace roster in an internal conversation. A child agent, or a channel shared with another organization, only shows the signed-in person’s own row. Think of it like an office directory: visible inside headquarters, but not printed on a visitor badge.

Second, it controls changes. Existing members cannot be deleted through this object system. Only a workspace admin using the main agent can change another member’s admin role or seat. The code also prevents dangerous states, such as removing the last admin or the last seated admin.

Finally, it defines the `add_member` tool. This lets an admin add someone by email before that person has arrived, including people outside the workspace’s usual email domain, such as contractors. It checks that the requester is allowed, prevents duplicates, creates the member, and optionally sends an invitation email.

#### Function details

##### `MemberObjects.list`  (lines 65–69)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of member objects that the current speaker is allowed to see. It is used when the object system asks for a member list.

**Data flow**: It receives the tool context and a paging or filtering query. It asks `_visible_rows` for the database rows this speaker may see, turns each row into a simple list item with `_row`, then passes those items through `object_page` so the caller gets a properly shaped page.

**Call relations**: This is the public list entry for `MemberObjects`. It relies on `_visible_rows` to enforce the visibility rules, then hands the safe rows to `_row` and `object_page` for presentation.

*Call graph*: calls 2 internal fn (_visible_rows, _row); 1 external calls (object_page).


##### `MemberObjects.member_page`  (lines 71–87)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of members for portal-style reads, where a signed-in member is looking outside a normal agent turn. It applies the same main-agent visibility rule as the in-chat listing.

**Data flow**: It receives the signed-in member’s id, whether they are an admin, and a page query. It fetches the rows that member may see with `_member_rows`, converts them into list items with `_row`, and packages them as an object page.

**Call relations**: Portal reads call this instead of the normal turn-based `list`. It still funnels through `_member_rows`, `_row`, and `object_page` so the portal and agent views stay consistent.

*Call graph*: calls 2 internal fn (_member_rows, _row); 1 external calls (object_page).


##### `MemberObjects.get`  (lines 89–91)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberSpec] | None
```

**Purpose**: Looks up one visible member object by its name, which is the member id as text. It returns the detailed member data only if the current speaker is allowed to see that member.

**Data flow**: It receives the tool context and requested member name. It asks `_visible_row` to find a matching visible row; if none is found it returns nothing, otherwise it turns the row into a detailed object with `_detail`.

**Call relations**: This is the single-object companion to `list`. It depends on `_visible_row`, which in turn depends on the same visibility rules as listing, then uses `_detail` to format the allowed result.

*Call graph*: calls 2 internal fn (_visible_row, _detail).


##### `MemberObjects.member_detail`  (lines 93–108)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemberSpec] | None
```

**Purpose**: Returns one member’s row and detailed data for portal-style reads. It only returns the member if that signed-in reader is allowed to see it.

**Data flow**: It receives a member name and the signed-in member’s id. It fetches all rows visible to that reader with `_member_rows`, finds the one whose id matches the requested name, and returns nothing if there is no match. If found, it combines `_row` and `_detail` into a `MemberObject`.

**Call relations**: Portal detail pages use this path. It shares `_member_rows` with `member_page`, which keeps portal list and detail views under the same visibility rule.

*Call graph*: calls 3 internal fn (_member_rows, _detail, _row); 1 external calls (__init__).


##### `MemberObjects.status`  (lines 110–125)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Provides a small status snapshot for a visible member: their email address and whether they are seated. This is useful when a caller needs lightweight state rather than full object details.

**Data flow**: It receives the tool context and member name. It uses `_visible_row` to find a matching row the speaker may see; if none exists it returns nothing. If found, it returns a small dictionary containing the email and seated flag.

**Call relations**: This function follows the same visibility path as `get`, through `_visible_row`. It does not call the formatting helpers because it returns a compact status dictionary rather than a full object response.

*Call graph*: calls 1 internal fn (_visible_row).


##### `MemberObjects.apply`  (lines 127–206)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemberSpec, old: MemberSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Changes an existing member’s admin role and/or seat status. It is the controlled path for membership edits, and it refuses changes unless a workspace admin is using the main agent.

**Data flow**: It receives the tool context, the member id as text, the desired member settings, and the previous settings. It first checks that this is not a create operation and that the speaker is a member using the main agent. It converts the name into a UUID, opens a workspace database transaction, locks the workspace row to avoid conflicting membership changes, verifies the speaker is truly an admin, loads the target member, changes their seat if needed through the seat system, checks that removing admin rights would not leave the workspace without an admin or seated admin, and finally updates the admin flag if needed. It returns no value, but it may change database rows and seat state.

**Call relations**: The object system calls this when someone applies a new `MemberSpec` to an existing member. It calls into the context to check the agent, into the database transaction layer for safe writes, into admin-checking logic for authority, and into `Seats` when access needs to be granted or revoked.

*Call graph*: calls 1 internal fn (agent_is_main); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, member_is_admin, ws_current, UUID).


##### `MemberObjects.delete`  (lines 208–215)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a workspace member through the object interface. Membership access is changed by seating or unseating, not by deleting the member object here.

**Data flow**: It receives the context, member name, and expected generation value, but does not inspect or change the database. It always raises an error explaining that member deletion is not supported through objects.

**Call relations**: The object system may call this for a delete verb. This implementation deliberately stops the flow immediately by raising `VerbNotSupported`.

*Call graph*: 1 external calls (__init__).


##### `MemberObjects._visible_rows`  (lines 217–222)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows the current tool speaker is allowed to see. It is the central visibility rule for in-conversation member listing and lookup.

**Data flow**: It reads the tool context: who the speaker is, what audience the conversation is in, and whether the current agent is the main agent. If there is no signed-in member, it returns no rows. If the audience is shared with another organization, it returns only the speaker’s own row. Otherwise it returns the whole roster only from the main agent, and just the speaker’s row from non-main agents.

**Call relations**: `list` and `_visible_row` call this before showing member data. It delegates the actual database query to `_roster`, passing either `whole=True` or `whole=False` depending on the visibility decision.

*Call graph*: calls 2 internal fn (_roster, agent_is_main); called by 2 (_visible_row, list).


##### `MemberObjects._visible_row`  (lines 224–228)

```
async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one member row by name within the rows the speaker is allowed to see. It prevents direct lookup from bypassing the list visibility rules.

**Data flow**: It receives the context and requested member name. It gets all visible rows from `_visible_rows`, compares each row’s id to the requested name, and returns the matching row or nothing.

**Call relations**: `get` and `status` use this helper. Its main job is to make sure single-member reads are no more permissive than member listing.

*Call graph*: calls 1 internal fn (_visible_rows); called by 2 (get, status).


##### `MemberObjects._member_rows`  (lines 230–242)

```
async def _member_rows(self, member_id: UUID) -> tuple[sa.Row, ...]
```

**Purpose**: Finds the member rows visible to a signed-in member when reading through the portal rather than inside a tool turn. It checks whether the currently named agent is the main agent and applies the roster rule from there.

**Data flow**: It receives the signed-in member id. It opens a workspace database transaction, looks up whether the current agent is marked as the main agent, then calls `_roster` with `whole=True` for the main agent or `whole=False` otherwise. It returns the resulting member rows.

**Call relations**: `member_page` and `member_detail` use this portal-specific visibility path. It still ends in `_roster`, so portal and turn-based reads share the same underlying roster query.

*Call graph*: calls 1 internal fn (_roster); called by 2 (member_detail, member_page); 4 external calls (select, agent_current, workspace_tx, ws_current).


##### `MemberObjects._roster`  (lines 244–264)

```
async def _roster(self, member_id: UUID, *, whole: bool) -> tuple[sa.Row, ...]
```

**Purpose**: Runs the database query that fetches member rows for the current workspace. It can return either the full workspace roster or only one member’s own row.

**Data flow**: It receives a member id and a `whole` flag. It builds a database query for members in the current workspace, ordered by email. If `whole` is false, it narrows the query to the given member id. It runs the query in a workspace transaction and returns the rows.

**Call relations**: This is the shared database reader behind `_visible_rows` and `_member_rows`. Higher-level functions decide what should be visible; `_roster` performs the actual fetch.

*Call graph*: called by 2 (_member_rows, _visible_rows); 3 external calls (select, workspace_tx, ws_current).


##### `_row`  (lines 267–280)

```
def _row(row: sa.Row) -> ObjectRow
```

**Purpose**: Turns a database member row into a short list item suitable for object listings. It gives callers a readable summary plus key fields.

**Data flow**: It receives a database row containing email, id, admin flag, and seat information. It creates an `ObjectRow` whose name is the member id, whose summary says the email, role, and seat state, and whose fields expose email, admin, and seated values.

**Call relations**: `list`, `member_page`, and `member_detail` call this whenever they need the compact row representation shown beside or inside member object results.

*Call graph*: called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `_detail`  (lines 283–288)

```
def _detail(row: sa.Row) -> ObjectDetail[MemberSpec]
```

**Purpose**: Turns a database member row into detailed object data. It captures the editable member settings and the row’s creation and update timestamps.

**Data flow**: It receives a database row. It builds a `MemberSpec` from the row’s admin flag and seat state, then wraps that spec with created-at and updated-at times in an `ObjectDetail`.

**Call relations**: `get` and `member_detail` call this after visibility has already been checked. It supplies the detailed part of a member object response.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `AddMember.add`  (lines 320–352)

```
async def add(self, ctx: ToolContext, args: AddMemberInput) -> ToolResult
```

**Purpose**: Adds a new person to the workspace by email before they have contacted the agent. It is meant for admins who need to invite colleagues, contractors, or advisors directly.

**Data flow**: It receives the tool context and input containing email, admin choice, and whether to notify. It verifies that the speaker is a signed-in admin using the main agent and not speaking in an externally shared channel. It normalizes and validates the email, opens a locked workspace transaction, checks the speaker’s admin status, confirms the email is not already a member through `_absent`, and creates the member. It returns a tool result message saying the person can now speak to the agent and, if requested, that they will receive a sign-in email.

**Call relations**: The `add_member` tool definition points to this function as its handler. During the flow it uses `_absent` for the duplicate check, then hands creation to `create_member` and wraps the final human-readable message in tool response objects.

*Call graph*: calls 2 internal fn (_absent, agent_is_main); 9 external calls (__init__, __init__, __init__, select, workspace_tx, create_member, email_domain, member_is_admin, ws_current).


##### `AddMember._absent`  (lines 354–367)

```
async def _absent(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Checks that an email address is not already a member of the current workspace. It prevents the add-member tool from creating duplicates.

**Data flow**: It receives an open database connection and a normalized email address. It queries the current workspace for any member whose email matches case-insensitively. If it finds one, it raises an error telling the caller to edit that existing member instead; otherwise it returns normally and changes nothing.

**Call relations**: `AddMember.add` calls this inside its workspace transaction just before creating the new member. It acts like a guard at the door: only emails not already on the roster are allowed through to `create_member`.

*Call graph*: called by 1 (add); 3 external calls (execute, select, ws_current).


### `core/src/ufo/kinds/workspace_kind.py`

`domain_logic` · `request handling`

A workspace is the top-level home for members. This file makes that workspace visible as an object, but only for reading. There is exactly one workspace object, named by the workspace id. It has no editable settings of its own: the number of members, number of seated members, and roster are all calculated from member records. In everyday terms, this file is like a front desk sign that shows the current occupancy list, not a form where you can add or remove people.

The main class, `WorkspaceObjects`, implements the actions the object system expects: list, get, status, apply, and delete. Listing returns the one workspace row with member and seat counts. Getting returns its timestamps and an empty spec, because there are no fields a user may fill in. Status returns the counts plus a roster, but with privacy rules: in an external shared audience it returns nothing; in an internal main-agent conversation it can show the whole roster; otherwise it shows only the speaker’s own member row.

Attempts to change or delete the workspace are refused with clear messages. The helper `_shape` does the actual database reading: it loads the workspace timestamps and asks the seating system for the current member snapshot. Without this file, callers would not have a safe, consistent way to inspect workspace membership status through the object API.

#### Function details

##### `WorkspaceObjects.list`  (lines 73–86)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the workspace in list form, including how many members exist and how many are seated. It returns nothing for external audiences, because workspace membership is only exposed inside the workspace.

**Data flow**: It receives a tool context and a list query. First it checks the audience; if the request comes from a foreign or external audience, it returns an empty page. Otherwise it reads the current workspace id, asks `_shape` for the latest member and seat counts, builds one list row named with the workspace id, and wraps that row in a paged result using the caller’s query options.

**Call relations**: This is called when the object system needs to list objects of kind `workspace`. It relies on `_shape` to gather the live workspace facts, uses `ws_current` to know which workspace is active, builds an `ObjectRow`, and hands the row to `object_page` so it fits the standard listing format.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, object_page, ws_current).


##### `WorkspaceObjects.get`  (lines 88–98)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WorkspaceSpec] | None
```

**Purpose**: Reads the single workspace object by name. It confirms the caller asked for the current workspace id, then returns the workspace’s timestamps and an empty spec because there are no editable workspace fields.

**Data flow**: It receives a tool context and an object name. If the audience is external, or if the requested name is not the current workspace id, it returns `None`. If the name matches, it calls `_shape` to get the workspace timestamps, creates an empty `WorkspaceSpec`, and returns an `ObjectDetail` containing that spec plus creation and update times.

**Call relations**: This is used when the object system wants the main record for one workspace object. It calls `ws_current` to check the requested name against the active workspace, then calls `_shape` for database-backed details, and finally packages the answer as an `ObjectDetail`.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, __init__, ws_current).


##### `WorkspaceObjects.status`  (lines 100–121)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status of the workspace: member count, seated count, and the visible part of the roster. It also enforces who is allowed to see the whole roster versus only their own entry.

**Data flow**: It receives a tool context, an object name, and an optional expected generation value. It ignores the generation value because this read-only status is computed fresh. If the audience is external or the name is not the current workspace id, it returns `None`. Otherwise it gathers the current shape, checks whether the speaker is a member talking to the main agent, and returns counts plus roster entries. The roster is either all members or only the entry matching the speaker’s member id.

**Call relations**: This is called when a caller asks for the workspace object’s status rather than its empty editable spec. It uses `_shape` for the current roster snapshot, asks `ToolContext.agent_is_main` whether the main agent is answering, and uses `ws_current` to ensure the requested object is the active workspace.

*Call graph*: calls 2 internal fn (_shape, agent_is_main); 1 external calls (ws_current).


##### `WorkspaceObjects.apply`  (lines 123–132)

```
async def apply(self, ctx: ToolContext, name: str, spec: WorkspaceSpec, old: WorkspaceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update a workspace object. The workspace itself has no authored fields, and seat changes must be made on member objects instead.

**Data flow**: It receives the proposed workspace spec, the previous spec if any, the object name, context, and an optional expected generation. Instead of changing anything, it immediately raises `VerbNotSupported` with a message explaining that seating and unseating belong to the `member` kind.

**Call relations**: This method exists because the object system expects every object store to have an apply operation. When someone tries to apply a change to `workspace`, it stops the flow here and does not hand off to any database write or seating operation.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects.delete`  (lines 134–141)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete the workspace object. A workspace is treated as permanent once created.

**Data flow**: It receives the tool context, object name, and optional expected generation. It does not inspect or modify stored data. It raises `VerbNotSupported` with a message saying the workspace is created at first run and is never deleted.

**Call relations**: This is the delete hook required by the object system. If a caller tries to delete a `workspace` object, this method ends that path immediately by reporting that deletion is not supported.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects._shape`  (lines 143–161)

```
async def _shape(self) -> WorkspaceShape
```

**Purpose**: Builds the complete current picture of the workspace that the public read methods need. It gathers the workspace timestamps from the database and the member seating snapshot from the seats subsystem.

**Data flow**: It starts with the current workspace id. Inside a workspace database transaction, it selects the workspace row’s creation and update times, then asks `Seats` for a snapshot of members and seating. It turns those raw pieces into a `WorkspaceShape` containing member count, seated count, roster entries, and timestamps, and returns that shape to the caller.

**Call relations**: `list`, `get`, and `status` all call this helper when they need current workspace facts. `_shape` is the shared reader beneath those methods: it uses `workspace_tx` for database access, `sqlalchemy.select` to fetch the workspace row, `Seats.snapshot` to get roster information, and then hands back one compact object the higher-level methods can format for their different responses.

*Call graph*: called by 3 (get, list, status); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


### Object System Core
The core object framework validates object requests, normalizes object identity, and tracks agent attribution for object-level operations.

### `core/src/ufo/objects.py`

`domain_logic` · `startup and object tool request handling`

A workspace object is like a labeled card in a shared filing cabinet: it has a kind, a name, and a structured spec. Extensions can add new kinds of cards, but this file makes sure every kind follows the same rules before the system serves it. Without this file, object tools would disagree about names, schemas, permissions, paging, and change history.

The file does three big jobs. First, it defines the common data shapes: an object detail, a lightweight listing row, links between objects, ownership records, and the store interface that every object kind must implement. Second, it provides shared behavior for member-owned objects, such as deciding whether a row is visible because it is shared, owned by the acting member, or visible to an admin. It also protects generated rows with a generation value, which works like a ticket number: if the object changed since it was read, an update is refused instead of overwriting the wrong thing.

Third, ObjectVerbs exposes the five object tools. These tools resolve the requested kind, bind the call to the extension that owns it, validate YAML manifests and Pydantic schemas, optionally target another agent, journal mutations before they happen, and return plain JSON or YAML results. The registry checks at startup are especially important: object specs may be shown in transcripts and returned by object_get, so secret fields and loose unknown keys are rejected early.

#### Function details

##### `_ObjectCursor.validate_rank`  (lines 176–181)

```
def validate_rank(self) -> '_ObjectCursor'
```

**Purpose**: Checks that a saved listing cursor contains the right kind of value for the way it is sorting. This prevents a bad or tampered cursor from confusing pagination.

**Data flow**: It reads the cursor's rank and value. If the value matches the expected type for that rank, it leaves the cursor unchanged; otherwise it raises an error.

**Call relations**: This validation runs when object_page rebuilds a cursor from the caller's continuation token. It protects the later sorting comparison from receiving a value that cannot be compared safely.


##### `object_page`  (lines 184–267)

```
def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Applies the shared list behavior for all object kinds: search, exact filters, sorting, and paging. A kind can hand over simple rows, and this function turns them into a consistent page of results.

**Data flow**: It receives lightweight object rows and a query. It checks that fields are declared correctly, filters and searches rows, sorts them with _sortable, applies any cursor, then returns an ObjectPage with up to the configured page size and possibly a next cursor.

**Call relations**: MemberOwnedObjects.list and MemberReadableObjects.member_page use this after they have already applied visibility rules. It calls _sortable to build stable ordering keys and creates _ObjectCursor values when there are more rows to fetch.

*Call graph*: calls 1 internal fn (_sortable); called by 2 (list, member_page); 2 external calls (__init__, __init__).


##### `object_page.value`  (lines 212–217)

```
def value(row: ObjectRow, name: str) -> JsonValue
```

**Purpose**: Looks up one sortable or filterable value from a listing row. It gives name and summary special treatment because they live directly on the row, not inside the extra fields map.

**Data flow**: It receives a row and a field name. It returns the row name, row summary, or the matching extra field value, with missing extra fields coming back as null-like None.

**Call relations**: This helper is used inside object_page while searching, filtering, and sorting. It keeps the rest of the paging code from repeating the same name-versus-field lookup rules.


##### `_sortable`  (lines 270–283)

```
def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]
```

**Purpose**: Converts a field value into a safe sorting key. It only allows simple values that can be ordered predictably.

**Data flow**: It receives a JSON-like value and the field name it came from. It returns a rank plus a normalized value for None, booleans, numbers, and strings; if the value is a list or object, it raises an error.

**Call relations**: object_page calls this whenever it sorts rows or compares a cursor boundary. It is the small guardrail that stops complex data from becoming an ambiguous sort key.

*Call graph*: called by 1 (object_page).


##### `ObjectStore.list`  (lines 298–298)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the required list operation for any object kind's storage layer. Implementers use it to return a page of lightweight rows for that kind.

**Data flow**: It receives the current tool context and a list query. The implementing store reads its own data and returns an ObjectPage.

**Call relations**: ObjectVerbs._list ultimately calls this after resolving the requested kind and setting the right extension context. This protocol method is a contract; each real object kind supplies the behavior.


##### `ObjectStore.get`  (lines 300–300)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines the required read operation for one named object. Implementers use it to return the object's full spec and metadata, or say it is absent.

**Data flow**: It receives the current tool context and an object name. The implementing store reads its storage and returns an ObjectDetail or None.

**Call relations**: ObjectVerbs._get, ObjectVerbs._apply, and ObjectVerbs._delete rely on this before showing, changing, or deleting an object. The method is supplied by each registered kind.


##### `ObjectStore.status`  (lines 302–308)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Defines how a kind reports live state beside the saved spec. Status is for changing facts such as last sync time or next scheduled action.

**Data flow**: It receives context, name, and an expected generation value. The implementing store checks the current row and returns a JSON-like status map or None.

**Call relations**: ObjectVerbs._get calls this after get so the response can include both stored spec and live state. Generation-aware stores use the expected value to avoid mixing a status read from one version with a spec from another.


##### `ObjectStore.apply`  (lines 310–318)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how a kind creates or updates an object after core validation has succeeded. This is where the kind performs its real domain-specific write.

**Data flow**: It receives context, name, the new validated spec, the previous spec if any, and an expected generation. The implementing store writes the change or raises a clear refusal.

**Call relations**: ObjectVerbs._apply calls this after parsing YAML, validating the spec, checking create-only behavior, and journaling the intended change. Each object kind owns the actual mutation logic.


##### `ObjectStore.delete`  (lines 320–326)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how a kind deletes one object. It lets each kind decide what deletion means in its own storage.

**Data flow**: It receives context, name, and an expected generation. The implementing store removes or disables the object, or raises a refusal.

**Call relations**: ObjectVerbs._delete calls this after it reads the old object and journals the delete. Generation-aware stores can reject stale deletes.


##### `owner_emails`  (lines 345–360)

```
async def owner_emails(owners: Iterable[UUID | None]) -> dict[UUID | None, str]
```

**Purpose**: Looks up email addresses for a set of member owners in one database trip. Object listings can use this to show who owns each row without doing one query per row.

**Data flow**: It receives member IDs, ignores missing owners, queries the member table for matching emails, and returns a map from member ID to email.

**Call relations**: Object kinds can call this while building their listing rows. It uses the workspace database transaction helper and SQLAlchemy query building to read the member table.

*Call graph*: 2 external calls (select, workspace_tx).


##### `MemberOwnedObjects.list`  (lines 402–410)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists objects for a member-owned kind while respecting who is allowed to see each row. It hides private rows before applying the shared search, filter, sort, and page rules.

**Data flow**: It reads whether the speaker is an admin and the acting member ID from the context. It asks the subclass for owned rows, keeps only visible and listable ones, converts them to ObjectRow values, then returns object_page's result.

**Call relations**: ObjectVerbs._list reaches this when the registered store is a MemberOwnedObjects subclass. The method delegates data gathering to _owned_rows, permission checks to _visible, optional browse filtering to _listed, and final paging to object_page.

*Call graph*: calls 5 internal fn (_listed, _owned_rows, _visible, object_page, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.get`  (lines 412–423)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Reads one member-owned object only if the acting user may see it. To callers, an invisible object looks the same as a missing one.

**Data flow**: It finds the object's owner, checks visibility using the acting member and admin status, then asks the subclass for full detail. If the owner carries a generation, it copies that generation into the returned detail.

**Call relations**: ObjectVerbs._get, _apply, and _delete may use store get behavior through subclasses. This method relies on _owner, _visible, and _detail so subclasses only supply storage-specific data.

*Call graph*: calls 4 internal fn (_detail, _owner, _visible, speaker_is_admin); 1 external calls (replace).


##### `MemberOwnedObjects.status`  (lines 425–445)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reads live status for a member-owned object without leaking information about invisible rows or stale versions. It double-checks generation so status cannot be paired with the wrong spec.

**Data flow**: It finds the owner, verifies the expected generation, checks visibility, asks the subclass for status, then reads the owner again and repeats the generation and visibility checks. It returns the status, None for a gone row, or raises a not-found-style error.

**Call relations**: ObjectVerbs._get calls status after reading detail. This method coordinates _owner, _require_current_generation, _visible, and _status to make the read safe.

*Call graph*: calls 5 internal fn (_owner, _require_current_generation, _status, _visible, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.apply`  (lines 447–473)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a member-owned object while enforcing ownership, admin, speaker, and generation rules. It keeps subclasses from accidentally skipping the common permission gate.

**Data flow**: It reads the current owner. For a new row, it checks generation expectations and hands off creation. For an existing row, it checks visibility, freshness, ownership or admin rights, optional live-speaker requirements, then calls the subclass write method.

**Call relations**: ObjectVerbs._apply reaches this through the store interface after core has validated the manifest. This method uses _owner, _visible, _owned, _admin_can_apply, _require_current_generation, and _apply_owned before the actual storage write.

*Call graph*: calls 7 internal fn (_admin_can_apply, _apply_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects.delete`  (lines 475–493)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a member-owned object only when the caller can see it and has the right to remove it. It treats invisible rows as not found.

**Data flow**: It finds the owner, checks that the generation still matches, rejects missing or invisible rows, checks ownership or admin permission, applies any live-speaker requirement, then calls the subclass delete method.

**Call relations**: ObjectVerbs._delete reaches this through the store interface. The method centralizes the common gate before handing the actual deletion to _delete_owned.

*Call graph*: calls 6 internal fn (_delete_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects._owned`  (lines 495–499)

```
def _owned(self, owner: OwnerT, acting: UUID | None) -> bool
```

**Purpose**: Answers whether the acting member is the explicit owner of a row. Admin-only rows, which have no member owner, are never considered owned by a normal member.

**Data flow**: It receives an owner record and an acting member ID. It returns true only when the row has a member_id and it matches the acting member.

**Call relations**: _visible uses this for read access, while apply and delete use it to decide whether admin-level permission is needed.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 501–502)

```
def _visible(self, owner: OwnerT, acting: UUID | None, is_admin: bool) -> bool
```

**Purpose**: Decides whether a row can be seen by the current actor. A row is visible if it is shared, owned by the actor, or the actor is an admin.

**Data flow**: It receives the owner, acting member ID, and admin flag. It returns a simple yes-or-no visibility result.

**Call relations**: List, get, status, apply, and delete all use this so hidden objects consistently disappear or refuse without leaking details. It calls _owned for the owner check.

*Call graph*: calls 1 internal fn (_owned); called by 5 (apply, delete, get, list, status).


##### `MemberOwnedObjects._listed`  (lines 504–512)

```
def _listed(self, row: OwnedRow[OwnerT], query: ObjectListQuery) -> bool
```

**Purpose**: Lets a subclass hide some otherwise visible rows from browsing while still allowing direct access by name. The default says every visible row may be listed.

**Data flow**: It receives an owned row and the list query. By default it ignores the query and returns true.

**Call relations**: MemberOwnedObjects.list calls this after visibility checks and before paging. Subclasses can override it when a kind has addressable rows that should not appear in the index.

*Call graph*: called by 1 (list).


##### `MemberOwnedObjects._admin_can_apply`  (lines 514–515)

```
def _admin_can_apply(self, old: SpecT, spec: SpecT) -> bool
```

**Purpose**: Lets a subclass allow a limited admin edit to someone else's row. The default is conservative and allows no such special edit.

**Data flow**: It receives the old spec and proposed new spec. It returns false unless a subclass overrides it.

**Call relations**: MemberOwnedObjects.apply calls this when an admin is changing a row owned by another member. It is the extension point for carefully permitted admin-side updates.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._require_current_generation`  (lines 517–534)

```
def _require_current_generation(self, name: str, owner: OwnerT | None, expected_generation: UUID | None, action: str) -> None
```

**Purpose**: Rejects an action if the object name now points to a different generated row than the caller read. This prevents stale reads from overwriting or exposing a newer replacement.

**Data flow**: It receives the name, current owner, expected generation, and action text. It compares the current generation with the expected one, and raises an error if they do not match.

**Call relations**: Status, apply, and delete call this around sensitive reads and writes. It is especially important for rows using GeneratedObjectOwner; normal owners without generations remain last-write-wins.

*Call graph*: called by 3 (apply, delete, status).


##### `MemberOwnedObjects._owner`  (lines 536–537)

```
async def _owner(self, ctx: ToolContext, name: str) -> OwnerT | None
```

**Purpose**: Finds the ownership record for a named object. It is a small shared lookup built on the subclass's row listing.

**Data flow**: It receives context and a name. It asks _owned_rows for all rows and returns the owner for the matching name, or None if no row matches.

**Call relations**: Get, status, apply, and delete use this as their first step. It depends on subclasses implementing _owned_rows.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 539–540)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Declares that subclasses must provide the lightweight rows and ownership data for their kind. The base class cannot know where each extension stores its objects.

**Data flow**: It receives context and is expected to return owned rows. In the base class it raises NotImplementedError, meaning a subclass must replace it.

**Call relations**: MemberOwnedObjects.list and _owner call this. It is one of the main storage hooks that real object kinds implement.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._detail`  (lines 542–545)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Declares that subclasses must provide the full object detail once the base class has allowed the read. This keeps permission logic in one place and storage logic in the subclass.

**Data flow**: It receives context, name, and owner. A subclass returns ObjectDetail or None; the base method only signals that it must be implemented.

**Call relations**: MemberOwnedObjects.get calls this after checking visibility. Real stores override it to read their own tables.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 547–550)

```
async def _status(self, ctx: ToolContext, name: str, owner: OwnerT) -> dict[str, JsonValue] | None
```

**Purpose**: Declares that subclasses must provide live status for an object. The base class wraps it with visibility and generation checks.

**Data flow**: It receives context, name, and owner. A subclass returns a JSON-like status map or None; the base method raises until implemented.

**Call relations**: MemberOwnedObjects.status calls this between two safety checks. Subclasses supply the kind-specific live state.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 552–560)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: OwnerT | None) -> None
```

**Purpose**: Declares that subclasses must perform the actual create or update after common gates have passed. This is where the kind writes to its own storage.

**Data flow**: It receives context, name, new spec, old spec if any, and current owner if any. A subclass writes the change; the base method raises until implemented.

**Call relations**: MemberOwnedObjects.apply calls this only after validating visibility, ownership, speaker, and generation rules.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 562–563)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: OwnerT) -> None
```

**Purpose**: Declares that subclasses must perform the actual delete after common gates have passed. The base class does not know each kind's storage layout.

**Data flow**: It receives context, name, and owner. A subclass removes the row or performs its kind-specific deletion; the base method raises until implemented.

**Call relations**: MemberOwnedObjects.delete calls this after permission and freshness checks.

*Call graph*: called by 1 (delete).


##### `MemberReadable.member_detail`  (lines 585–592)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject | None
```

**Purpose**: Defines the opt-in contract for object kinds that can show a member-facing detail page outside an agent turn. A kind that cannot answer safely simply does not implement it.

**Data flow**: It receives an extension context, object name, member ID, and admin flag. The implementer returns a MemberObject or None if not visible or absent.

**Call relations**: Portal-style code can check this protocol before reading object details for a signed-in member. It is a contract rather than a concrete implementation in this file.


##### `MemberListable.member_page`  (lines 600–607)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the opt-in contract for object kinds that can show a member-facing list page. It extends member detail support with search, filter, sort, and paging.

**Data flow**: It receives an extension context, member ID, admin flag, and ObjectListQuery. The implementer returns an ObjectPage.

**Call relations**: Portal index routes can rely on this protocol for kinds that support listing. MemberReadableObjects provides a reusable implementation for member-owned kinds.


##### `ConversationMemberListable.member_conversation_rows`  (lines 619–627)

```
async def member_conversation_rows(self, ext: 'ExtensionContext | None', conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Defines how a kind can list object grants connected to a conversation for one member. This supports conversation-specific object views without exposing all objects.

**Data flow**: It receives extension context, conversation ID, member ID, admin flag, and a limit. The implementer returns grant records containing object names, generations, and whether content is visible.

**Call relations**: Conversation-facing code can call this protocol for kinds that implement it. This file only states the shape of that behavior.


##### `MemberReadableObjects.member_page`  (lines 640–653)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Provides a member-facing listing for member-owned object kinds. It applies the same visibility and listing rules used by tool-side listing.

**Data flow**: It receives extension context, member ID, admin flag, and query. It asks _member_rows for rows, keeps visible and listable ones, converts them to ObjectRow values, and returns object_page's result.

**Call relations**: This implements the MemberListable contract for subclasses. It shares object_page with MemberOwnedObjects.list so portal and tool listings behave alike.

*Call graph*: calls 2 internal fn (_member_rows, object_page); 1 external calls (__init__).


##### `MemberReadableObjects.member_detail`  (lines 655–673)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject[SpecT] | None
```

**Purpose**: Provides a member-facing detail read for member-owned object kinds. It returns both the lightweight row and the full detail when the member may see the object.

**Data flow**: It receives extension context, name, member ID, and admin flag. It finds the row, checks visibility, asks _member_object for detail, and returns a MemberObject or None.

**Call relations**: This implements the MemberReadable contract for subclasses. It delegates storage-specific work to _member_rows and _member_object.

*Call graph*: calls 2 internal fn (_member_object, _member_rows); 2 external calls (__init__, __init__).


##### `MemberReadableObjects._owned_rows`  (lines 675–676)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Connects the tool-side member-owned base class to the member-facing row source. This avoids having two separate row implementations that could drift apart.

**Data flow**: It receives a ToolContext, extracts the extension context and acting member ID, then returns _member_rows.

**Call relations**: MemberOwnedObjects.list and _owner call this through the inherited flow. It hands off to _member_rows, which subclasses implement.

*Call graph*: calls 1 internal fn (_member_rows).


##### `MemberReadableObjects._detail`  (lines 678–681)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Connects the tool-side detail read to the member-facing object read. This keeps detail behavior consistent between agent tools and portal reads.

**Data flow**: It receives ToolContext, name, and owner. It passes the extension context and acting member ID to _member_object and returns that result.

**Call relations**: MemberOwnedObjects.get calls this through inheritance. It delegates the actual storage read to _member_object.

*Call graph*: calls 1 internal fn (_member_object).


##### `MemberReadableObjects._member_rows`  (lines 683–686)

```
async def _member_rows(self, ext: 'ExtensionContext | None', *, member_id: UUID | None) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Declares that subclasses must provide rows for a member-facing view. These rows include ownership data so the shared visibility gate can be applied.

**Data flow**: It receives an extension context and member ID. A subclass returns owned rows; the base method raises until implemented.

**Call relations**: member_page, member_detail, and _owned_rows all depend on this. It is the shared row source for portal and tool paths.

*Call graph*: called by 3 (_owned_rows, member_detail, member_page).


##### `MemberReadableObjects._member_object`  (lines 688–696)

```
async def _member_object(self, ext: 'ExtensionContext | None', name: str, owner: OwnerT, *, member_id: UUID | None) -> ObjectDetail[SpecT] | None
```

**Purpose**: Declares that subclasses must provide the full object detail for a member-facing read. The shared class handles visibility before calling it.

**Data flow**: It receives extension context, name, owner, and member ID. A subclass returns ObjectDetail or None; the base method raises until implemented.

**Call relations**: member_detail and _detail call this. It is the storage-specific detail hook for member-readable kinds.

*Call graph*: called by 2 (_detail, member_detail).


##### `object_registry`  (lines 731–754)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: Builds and validates the global registry of object kinds at startup. It catches bad extension registrations before the system begins serving requests.

**Data flow**: It receives bound kinds from core and extensions. It checks kind-name syntax, duplicate names, allowed agent-target verbs, and spec model safety, then returns a dictionary keyed by kind name.

**Call relations**: Startup code uses this as the boot gate. It calls _validate_spec_model for schema safety and produces the registry later used by ObjectVerbs.

*Call graph*: calls 1 internal fn (_validate_spec_model); 1 external calls (fullmatch).


##### `_validate_spec_model`  (lines 757–777)

```
def _validate_spec_model(owner: str, kind: ObjectKind) -> None
```

**Purpose**: Checks that an object kind's spec model is safe to store, show, and echo back. It rejects loose models, secret fields, and schemas that cannot be represented as JSON.

**Data flow**: It receives the owning extension name and ObjectKind. It walks the root spec model and nested models, inspects field annotations, and asks Pydantic for a JSON schema; failures become clear ValueErrors.

**Call relations**: object_registry calls this for every kind during startup. It uses _reachable_models and _annotation_types to inspect nested Pydantic models.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 1 (object_registry).


##### `_reachable_models`  (lines 780–794)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: Finds all nested Pydantic models that can appear inside a spec model. This lets validation cover not just the top-level spec but also embedded objects.

**Data flow**: It receives a Pydantic model class. It walks field annotations, follows nested BaseModel types, avoids repeats, and returns all discovered models.

**Call relations**: _validate_spec_model calls this before checking extra-field policy and secret-bearing fields. It uses _annotation_types to understand container and union annotations.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 797–804)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: Flattens a type annotation into the concrete pieces it contains. For example, it can look through optional, union, or container types.

**Data flow**: It receives an annotation object. It asks Python typing for its arguments and recursively returns all nested annotation parts, or the annotation itself if there are no parts.

**Call relations**: _validate_spec_model and _reachable_models use this while inspecting model fields. It is a small utility for schema safety checks.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectVerbs.tools`  (lines 857–927)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: Creates the five tool definitions that agents can call for workspace objects. These are list, get, explain, apply, and delete.

**Data flow**: It reads the ObjectVerbs instance and returns ToolDef objects with names, human-facing descriptions, input models, handlers, and safety flags.

**Call relations**: Tool registration code uses this to expose object operations. Each ToolDef points back to one of ObjectVerbs' private handler methods.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 929–961)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: Implements object_list. It either lists registered object kinds or lists instances of one kind with search, filters, sorting, and paging.

**Data flow**: It receives tool context and list arguments. With no kind, it returns kind descriptions; with a kind, it resolves the kind, checks any agent target, builds an ObjectListQuery, calls the store list method, and returns JSON rows.

**Call relations**: The object_list ToolDef calls this. It uses _resolve, _target, _bound_ctx, object_agent scoping, and _json_result to turn a tool call into the correct store call and response.

*Call graph*: calls 4 internal fn (_bound_ctx, _resolve, _target, _json_result); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._get`  (lines 963–1001)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: Implements object_get. It reads one object's saved spec, live status, links, timestamps, and generation if present.

**Data flow**: It receives context and get arguments. It resolves the kind, checks any agent target, reads detail and status from the store, renders links and timestamps, and returns YAML text.

**Call relations**: The object_get ToolDef calls this. It uses _resolve, _bound_ctx, _target, and object_agent scoping before calling the kind's get and status methods.

*Call graph*: calls 3 internal fn (_bound_ctx, _resolve, _target); 5 external calls (__init__, __init__, __init__, object_agent, safe_dump).


##### `ObjectVerbs._explain`  (lines 1003–1016)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: Implements object_explain. It tells a caller how to author a valid object of a given kind.

**Data flow**: It receives context and a kind name. It resolves the kind and returns JSON containing the description, guidance, allowed agent-target verbs, naming rule, and JSON schema for the spec.

**Call relations**: The object_explain ToolDef calls this before users or agents write manifests. It relies on _resolve and _json_result.

*Call graph*: calls 2 internal fn (_resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 1018–1083)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: Implements object_apply. It creates or updates an object from a YAML manifest after validating the envelope, name, schema, agent target, and freshness expectations.

**Data flow**: It receives context and apply arguments. It parses the manifest, resolves the kind, validates the object name and spec, reads any existing object, journals the intended change, calls the store apply method, withdraws the journal entry if the store refuses, and returns a created-or-updated JSON result.

**Call relations**: The object_apply ToolDef calls this. It coordinates _parse_envelope, _resolve, _target, _bound_ctx, _journal_object_change, the store apply call, _withdraw_object_change on failure, and _json_result.

*Call graph*: calls 7 internal fn (_bound_ctx, _resolve, _target, _journal_object_change, _json_result, _parse_envelope, _withdraw_object_change); 4 external calls (__init__, __init__, validate_object_name, object_agent).


##### `ObjectVerbs._delete`  (lines 1085–1120)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: Implements object_delete. It removes one object and returns the deleted spec when the spec may be shown.

**Data flow**: It receives context and delete arguments. It resolves the kind, checks any agent target, reads the old object, journals the delete, calls the store delete method with the observed generation, withdraws the journal entry on failure, and returns JSON.

**Call relations**: The object_delete ToolDef calls this. It uses _resolve, _bound_ctx, _target, object_agent scoping, _journal_object_change, _withdraw_object_change, and _json_result.

*Call graph*: calls 6 internal fn (_bound_ctx, _resolve, _target, _journal_object_change, _json_result, _withdraw_object_change); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._resolve`  (lines 1122–1127)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: Finds a registered object kind by name or raises a helpful unknown-kind error. This keeps all verb handlers using the same lookup behavior.

**Data flow**: It receives a kind string. It looks in the registry and returns the BoundKind, or raises an error that lists the registered kinds.

**Call relations**: _list, _get, _explain, _apply, and _delete all call this before touching a kind's store.

*Call graph*: called by 5 (_apply, _delete, _explain, _get, _list); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 1129–1130)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: Rebinds a tool call to the extension context that owns the object kind. This makes the same tool machinery work for both core and extension-defined objects.

**Data flow**: It receives the current ToolContext and a BoundKind. It returns a copy of the context with the extension context replaced by the kind's owning context.

**Call relations**: _list, _get, _apply, and _delete use this before calling a store. It keeps store code seeing its own extension environment.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 1 external calls (replace).


##### `ObjectVerbs._target`  (lines 1132–1193)

```
async def _target(self, ctx: ToolContext, bound: BoundKind, name: str, verbs: frozenset[AgentTargetVerb]) -> ObjectAgent | None
```

**Purpose**: Checks and resolves an optional target agent for agent-scoped object kinds. It enforces that only allowed verbs and allowed callers can act across agents.

**Data flow**: It receives context, bound kind, target agent name, and the verbs being attempted. If no name is given it returns None. Otherwise it verifies the kind allows targeting, checks the current agent and speaker permissions in the database, finds the target agent, and returns an ObjectAgent.

**Call relations**: _list, _get, _apply, and _delete call this before entering object_agent scope. It uses the workspace database and member_is_admin to enforce cross-agent boundaries.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 5 external calls (__init__, or_, select, workspace_tx, member_is_admin).


##### `_journal_object_change`  (lines 1196–1250)

```
async def _journal_object_change(ctx: ToolContext, kind: str, name: str, verb: Literal['create', 'update', 'delete'], before: BaseModel | None, after: BaseModel | None, agent_id: UUID) -> UUID | None
```

**Purpose**: Records a create, update, or delete before the actual mutation runs. This gives the system a reliable audit trail and makes retries safer.

**Data flow**: It receives context, object identity, verb, before and after specs, and the agent ID. It derives or creates a change ID, checks whether that ID was already recorded, inserts the change row if not, and returns the inserted ID or None for an existing retry record.

**Call relations**: ObjectVerbs._apply and _delete call this before writing to a store. If the later store operation fails, those handlers call _withdraw_object_change only for a journal row inserted by the current attempt.

*Call graph*: called by 2 (_apply, _delete); 7 external calls (dumps, model_dump, insert, select, workspace_tx, uuid4, uuid5).


##### `_withdraw_object_change`  (lines 1253–1260)

```
async def _withdraw_object_change(ctx: ToolContext, change_id: UUID) -> None
```

**Purpose**: Removes a just-created journal entry when the actual object mutation fails. This keeps the audit log from claiming a failed write happened.

**Data flow**: It receives context and a change ID. It opens a workspace transaction and deletes that object_change row for the current workspace.

**Call relations**: ObjectVerbs._apply and _delete call this inside their exception paths after a store refusal or error. It is paired with _journal_object_change.

*Call graph*: called by 2 (_apply, _delete); 2 external calls (delete, workspace_tx).


##### `_parse_envelope`  (lines 1263–1289)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object], UUID | None]
```

**Purpose**: Parses and validates the YAML wrapper used by object_apply. It makes sure the manifest has exactly the expected top-level shape before schema validation begins.

**Data flow**: It receives manifest text. It checks byte size, safely loads YAML, requires kind, name, and spec plus optional generation, verifies their basic types, parses the generation as a UUID when present, and returns the pieces.

**Call relations**: ObjectVerbs._apply calls this as its first real step. Later apply logic validates the object name and the spec model after this envelope check succeeds.

*Call graph*: called by 1 (_apply); 3 external calls (__init__, UUID, safe_load).


##### `_json_result`  (lines 1292–1293)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: Wraps a JSON-serializable payload into the tool result format. It is the common helper for object tools that return compact JSON.

**Data flow**: It receives a mapping. It JSON-encodes it, places the text in TextContent, and returns a ToolResult containing that content.

**Call relations**: _list, _explain, _apply, and _delete use this to build their responses. _get is different because it returns YAML for a richer object detail view.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### `core/src/ufo/object_name.py`

`data_model` · `cross-cutting`

This file is the naming rulebook for objects in the system. An object has a “kind”, like a category, and a “name”, like the label within that category. Together they form a stable reference such as `note/today` or `user/alice`. Without this file, different parts of the project could invent slightly different naming rules, which would make stored objects hard to find, link to, or validate safely.

The file sets two simple grammars using regular expressions, which are patterns for checking text. Kinds must start with a lowercase letter and then use lowercase letters, numbers, or underscores. Object names can use lowercase letters, numbers, and hyphens, but must start and end with a letter or number and cannot be longer than 64 characters.

`validate_object_name` is a standalone check for places that need to reject a bad object name before saving it. `ObjectRef` is a small Pydantic model, meaning a data object that validates its fields when it is created. It is frozen, so once made it cannot be changed, and it forbids unexpected extra fields. That makes it a reliable “address label” for an object. It also has an optional `agent` field for cases where the same object reference needs to remember which agent context it belongs to, without hiding that information inside the name itself.

#### Function details

##### `validate_object_name`  (lines 22–30)

```
def validate_object_name(name: str) -> None
```

**Purpose**: This function checks whether a caller-supplied object name follows the shared naming rules. It is used to reject a bad name at the moment someone tries to use or save it, instead of letting an invalid reference appear later.

**Data flow**: It takes a text name as input. It checks that the name is no more than 64 characters and matches the allowed pattern for object names. If the name is valid, nothing is returned and the caller can continue; if it is invalid, the function raises `InvalidName`, a clear error saying what rule was broken.

**Call relations**: This is the standalone gatekeeper for raw object names outside the `ObjectRef` model. When a write path or name-making path needs to confirm a name before storing it, it can call this function directly; on failure it creates an `InvalidName` error rather than handing the bad name further into the object system.

*Call graph*: 1 external calls (__init__).


##### `ObjectRef.validate_kind`  (lines 46–49)

```
def validate_kind(cls, value: str) -> str
```

**Purpose**: This validator checks the `kind` part of an `ObjectRef`. It makes sure object categories use the one shared format, so every reference can be parsed and displayed consistently.

**Data flow**: It receives the proposed `kind` text while an `ObjectRef` is being built. It compares that text with the allowed kind pattern. If the text is valid, it passes the same value through; if not, it raises an error explaining the required pattern.

**Call relations**: Pydantic calls this automatically during `ObjectRef` creation. It runs before the completed reference is accepted, so any code that receives an `ObjectRef` can trust that the kind field has already passed the project-wide rule.


##### `ObjectRef.validate_name`  (lines 53–59)

```
def validate_name(cls, value: str) -> str
```

**Purpose**: This validator checks the `name` part of an `ObjectRef`. It protects the object reference from containing names that are too long or that use characters the rest of the system does not support.

**Data flow**: It receives the proposed object name while an `ObjectRef` is being created. It checks both the maximum length and the allowed name pattern. A valid name is returned unchanged; an invalid one stops model creation with a clear validation error.

**Call relations**: Pydantic invokes this as part of building an `ObjectRef`. Together with `ObjectRef.validate_kind`, it means the reference object is validated at the boundary, before other parts of the system store it, compare it, or render it as text.


##### `ObjectRef.__str__`  (lines 61–62)

```
def __str__(self) -> str
```

**Purpose**: This function gives an `ObjectRef` its simple human-readable form: `kind/name`. It is useful when the reference needs to be printed, logged, or shown as a compact link-like label.

**Data flow**: It reads the already-validated `kind` and `name` fields from the object. It joins them with a slash and returns the resulting string. It does not include the optional `agent` field, because the agent is kept as separate context rather than encoded into the object name.

**Call relations**: Python calls this method whenever code turns an `ObjectRef` into text, such as with `str(ref)`. It relies on the validators having already made the two visible parts safe and predictable.


### `core/src/ufo/object_scope.py`

`util` · `cross-cutting during object dispatch and audited object handling`

Some operations need to know “which agent is responsible for this?” Most of the time, the answer comes from the normal current agent. But object dispatch can select a more specific agent namespace for one audited object handler. This file provides that temporary selection.

It defines a small immutable record, `ObjectAgent`, containing an agent’s unique ID and name. It then stores the currently selected object agent in a `ContextVar`, which is Python’s way of keeping a value local to the current task or request. That matters because two requests may run at the same time, and one request must not accidentally see another request’s agent. Think of it like each waiter carrying their own notepad instead of everyone writing on the same sheet.

The `object_agent` context manager is the safe “enter this temporary agent scope, then restore the old one” tool. Code inside its block sees the selected object agent. Code outside goes back to whatever was there before. If no object agent is supplied, it simply does nothing.

Finally, `object_agent_id` answers the practical question: “what agent ID should this object operation use?” It returns the temporary object agent ID when one is active; otherwise it falls back to the normal current agent.

#### Function details

##### `object_agent`  (lines 24–32)

```
def object_agent(target: ObjectAgent | None) -> Iterator[None]
```

**Purpose**: Temporarily marks the current task as working on behalf of a specific object-selected agent. Someone would use it around code that should be audited or attributed to that object agent, while making sure the setting is automatically undone afterward.

**Data flow**: It receives either an `ObjectAgent` or `None`. If it gets `None`, it yields control without changing anything. If it gets an agent, it stores that agent in the task-local variable before the wrapped code runs, then restores the previous value afterward, even if the wrapped code exits with an error.

**Call relations**: This function is meant to surround object-handler work. While that work runs, later calls to `object_agent_id` can see the temporary object agent. When the block ends, it resets the task-local value so later code does not accidentally inherit the wrong agent.


##### `object_agent_id`  (lines 35–37)

```
def object_agent_id() -> UUID
```

**Purpose**: Returns the agent ID that should be used for the current object operation. It chooses the temporary object agent when one has been set, and otherwise uses the normal current agent.

**Data flow**: It reads the task-local object-agent value. If an object agent is present, it returns that agent’s UUID. If no object agent is present, it asks `ufo.agent_scope.agent_current` for the current agent and returns that agent’s ID.

**Call relations**: This function is called when code needs the correct agent identity for auditing or object-related decisions. It depends on `object_agent` having set a temporary target when object dispatch selected one; if no such target exists, it hands the decision off to the broader agent scope through `agent_current`.

*Call graph*: 1 external calls (agent_current).


### Conversation Portal Views
Conversation-facing extensions add portal state for hosted site links and durable todo checklists tied to ongoing work.

### `extensions/sites/ufo_ext_sites/conversation_slot.py`

`domain_logic` · `request handling`

A conversation may have hosted sites attached to it, but the system must not simply show every site in storage. This file acts like a careful receptionist: it checks the visitor’s permission slips before revealing which site rooms exist.

The main idea is a “conversation slot,” meaning a small panel of extra information that can appear alongside a conversation. This file creates the Sites slot. When the slot is only being summarized, it gives a small count-like signal so the interface can decide whether there is anything to show. When the full slot is read, it first looks at the conversation’s visible authorization items. These are the objects the current viewer is allowed to know about. It converts those object names into site names, asks the site store for matching hosted site records, then filters the results again to make sure the site’s saved generation still matches the visible authorization generation. That second check matters because permissions or objects may have changed.

For each allowed site, it builds a public URL and returns a payload containing display-ready site entries. It also respects a maximum count, returning a “truncated” flag if there were more authorized sites than can be shown.

#### Function details

##### `_read`  (lines 13–46)

```
async def _read(ctx: ConversationSlotContext) -> SitesSlotPayload
```

**Purpose**: This function builds the full list of sites that should appear in the conversation’s Sites slot. It protects privacy by only returning sites that match the viewer’s visible authorization objects and their current generation.

**Data flow**: It receives a conversation slot context, which includes the conversation id, visible authorization items, workspace information, a transaction, and the public base URL. It turns visible object names into expected site names, reads matching hosted site rows from storage, filters out anything that is not still authorized, builds public site links, and returns a SitesSlotPayload containing the visible sites plus a flag saying whether the list was cut short.

**Call relations**: When the Sites slot provider needs the full content, it uses this function as its reader. Inside, _read asks site_name_from_object to understand which visible objects refer to sites, uses HostedSites to fetch stored site records, checks object names again with site_object_name, builds user-facing links with site_url, wraps each allowed row in a ConversationSite, and finally packages them in a SitesSlotPayload.

*Call graph*: 6 external calls (__init__, __init__, __init__, site_name_from_object, site_object_name, site_url).


##### `_summarize`  (lines 49–51)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a lightweight summary for the Sites slot without reading full site records. It answers the simple question: is there anything visible enough to make the Sites slot worth showing?

**Data flow**: It receives the conversation slot context and looks only at the number of visible items already present there. It caps that number at the configured maximum, then returns the count if it is nonzero; if there are no visible items, it returns None, which means there is no summary to show.

**Call relations**: The Sites slot provider uses this function when the conversation system wants a quick summary instead of the full site list. Unlike _read, it does not call into storage or build URLs; it gives the provider a cheap early signal based on the visible items already supplied in the context.


### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `request handling and conversation display`

This file solves a simple but important problem: multi-step work is easy to lose track of. Instead of relying only on the agent's current text context, it stores a checklist in the extension's own saved storage, keyed to the current conversation. That means a later turn in the same conversation can read the same list back.

There are two main tools. One replaces the whole checklist with a new title and set of tasks. The other changes the status of existing tasks, such as moving a task from pending to in_progress or completed. Think of it like a small whiteboard beside the conversation: the agent rewrites the board when planning, then ticks items off as work proceeds.

The file also defines the shapes of the data using Pydantic models, which are validation classes that check the incoming data has the expected fields and values. It refuses unsafe updates, such as changing a task before any list exists or using a task number outside the list. Finally, it registers the tools, the prompt text that teaches the agent when to use them, and a conversation “slot” that lets the UI read a trimmed summary of the checklist without showing overly long titles or task descriptions.

#### Function details

##### `_require_ext`  (lines 87–90)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This function makes sure a tool call has access to the todos extension context. That context is needed because it contains the extension's saved storage.

**Data flow**: It receives the tool call context. If the extension context is present, it returns it; if it is missing, it stops immediately with an error instead of letting later code fail in a confusing way.

**Call relations**: When update_todo_list or update_todo_status starts, each first calls _require_ext. This is the gatekeeper step before either tool tries to read from or write to the saved todo board.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 93–94)

```
def _board_key(conversation_id: UUID) -> str
```

**Purpose**: This function builds the storage name used for one conversation's todo board. It keeps each conversation's checklist separate from every other conversation.

**Data flow**: It takes a conversation identifier, adds the todo key prefix to it, and returns the resulting text key. Nothing is saved here; it only creates the address where the board will be saved or looked up.

**Call relations**: The write path uses _board_key when update_todo_list saves a new board. The update and display paths use the same helper in update_todo_status, _summarize_tasks, and _read_tasks so they all look in the exact same place.

*Call graph*: called by 4 (_read_tasks, _summarize_tasks, update_todo_list, update_todo_status).


##### `_board_result`  (lines 97–98)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This function turns the current todo board into the standard result returned by a tool. It lets the agent immediately see the latest checklist after creating or updating it.

**Data flow**: It receives a TodoBoard, converts it to JSON text, wraps that text as tool content, and returns a ToolResult containing it. The board itself is not changed.

**Call relations**: After update_todo_list creates a board, and after update_todo_status edits one, they both call _board_result. This is the final packaging step before the updated checklist is handed back to the model.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 101–103)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This function loads a saved todo board from the extension's storage. It hides the details of turning stored raw data back into a validated TodoBoard.

**Data flow**: It receives the extension context and a storage key. It asks the store for the saved value; if nothing is there, it returns None. If data is found, it validates it as a TodoBoard and returns that board.

**Call relations**: update_todo_status uses _read_board before applying status changes, because it must edit the existing list. _summarize_tasks and _read_tasks use it when the conversation UI needs a count or a display-ready version of the checklist.

*Call graph*: called by 3 (_read_tasks, _summarize_tasks, update_todo_status).


##### `update_todo_list`  (lines 106–110)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates or completely replaces the conversation's todo checklist. It is meant to be used at the start of complex work, or when the full plan needs to be revised.

**Data flow**: It receives the current tool context and the requested title and tasks. It confirms the extension context exists, builds a TodoBoard, saves it under the current conversation's storage key, and returns the full board as JSON text.

**Call relations**: This is one of the two public tools registered by manifest. Inside the flow, it relies on _require_ext for access to storage, _board_key to choose the right saved location, and _board_result to return the finished checklist.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 113–124)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that changes the status of one or more existing tasks. It is used while work is underway to mark tasks as started or completed.

**Data flow**: It receives the current tool context and a list of task-number updates. It loads the board for the current conversation, rejects the request if no list exists, checks that each 1-based task number is valid, changes the requested statuses, saves the revised board, and returns the updated board.

**Call relations**: This is the second public tool registered by manifest. It calls _require_ext and _board_key to find the right stored board, _read_board to load it, and _board_result to report the new state back after saving.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `_summarize_tasks`  (lines 127–129)

```
async def _summarize_tasks(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick count of tasks for the conversation slot system. It is a lightweight way for the surrounding system to know whether there is a checklist and how large it is.

**Data flow**: It receives a conversation slot context, builds the storage key for that conversation, and tries to read the board. If there is no board, it returns None; otherwise, it returns the number of tasks on the board.

**Call relations**: The TASKS_SLOT provider uses _summarize_tasks when it needs a compact summary. The function shares the same storage lookup helpers as the tool functions, so the UI summary reflects the same board the tools update.

*Call graph*: calls 2 internal fn (_board_key, _read_board).


##### `_read_tasks`  (lines 132–162)

```
async def _read_tasks(ctx: ConversationSlotContext) -> TasksSlotPayload
```

**Purpose**: This function prepares the saved checklist for display in the conversation UI. It trims long text and limits the number of tasks so the display payload stays within known size limits.

**Data flow**: It receives a conversation slot context and reads the board for that conversation. If none exists, it returns an empty task payload. If a board exists, it shortens the title and task descriptions as needed, counts total and completed tasks, notes whether anything was truncated, and returns a TasksSlotPayload.

**Call relations**: The TASKS_SLOT provider calls _read_tasks when the UI or host needs the actual task data. It uses _board_key and _read_board to fetch the same stored board that update_todo_list and update_todo_status write.

*Call graph*: calls 2 internal fn (_board_key, _read_board); 2 external calls (__init__, __init__).


##### `manifest`  (lines 175–197)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the todos extension to the host system. It tells the host what the extension is called, which tools it offers, what prompt guidance to include, and what conversation display slot it provides.

**Data flow**: It takes no input. It builds and returns a Manifest containing two tool definitions, one prompt section loaded from the companion markdown file, and the tasks conversation slot.

**Call relations**: The host calls manifest when loading the extension. The returned manifest connects the public tool names to update_todo_list and update_todo_status, and also makes the checklist visible through the TASKS_SLOT provider.

*Call graph*: 3 external calls (__init__, __init__, __init__).

## 📊 State Registers Touched

- `reg-extension-capability-registry` — The live catalog of everything enabled extensions add, such as tools, routes, jobs, credentials, hooks, and backends.
- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-workspace-directory` — The durable list of workspaces and their core ownership, admin, billing, and setup state.
- `reg-member-auth-principals` — The shared answer to who the current person or service is and what member identity they are acting as.
- `reg-agent-profiles` — The saved assistant definitions, including each agent's model, tools, visibility, setup needs, internet access, and identity.
- `reg-conversation-records` — The durable conversation list, including titles, audience, surface labels, sandbox links, and visibility rules.
- `reg-schedule-monitor-store` — The saved recurring prompts, pauses, and outside-world watches that can wake conversations later.
- `reg-credential-vault` — The encrypted store of API keys, connected accounts, grants, and approvals that lets tools use outside services without exposing secrets.
- `reg-object-registry` — The shared object front desk that gives stable names, views, permissions, and change history for workspace records.
- `reg-artifact-blob-store` — The shared file storage for generated artifacts, downloads, document previews, screenshots, and other saved output bytes.
- `reg-source-sync-state` — The saved state of connected content sources, including pages, checkpoints, errors, ownership, and read grants.
- `reg-memory-store` — The long-term saved facts, profiles, notes, and summaries that can be recalled in later conversations.
- `reg-extension-data-store` — Durable extension-scoped key/value or JSON state used by installed extensions beyond their manifest capabilities and lockfile selection.
- `reg-workspace-change-store` — Saved per-conversation file-change summaries produced from workspace git state at turn teardown and later shown in portal slots or audits.
- `reg-hosted-site-store` — Saved hosted-site records, published bindings, homepage mappings, build metadata, and site preview state used by public routes and site tools.
- `reg-research-reference-store` — Conversation-scoped research findings, cited links, fetched-page metadata, and source-panel references created by browser or research workflows.
- `reg-conversation-todo-store` — Durable conversation checklist/todo state exposed as workspace objects and updated by tools or agents across turns.
- `reg-report-digest-store` — Saved generated report digest results and related background-report state separate from the schedule that triggered them.
- `reg-workspace-membership-roster` — Durable workspace member records, roles/admin flags, invitations, inviter stamps, seating history, and member-local profile fields such as timezone or email lookup data.
