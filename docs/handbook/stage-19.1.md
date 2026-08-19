# Workspace object system and built-in object kinds  `stage-19.1`

This stage is shared behind-the-scenes support for the workspace. It gives the system a safe way to treat important things as named objects that can be listed, opened, described, or sometimes changed. The main object system defines the common rules: object names, simple YAML-based descriptions, visibility checks, validation, and routing each request to the code that owns that kind of object. The naming file keeps object identities consistent, and the scope helper records which agent should get credit for an audited action. The SDK file gives extension authors a stable public place to import these object tools.

On top of that shared machinery, several built-in object kinds are plugged in. Agent objects can be read, created, and updated under strict rules, but not deleted. Conversation objects expose past chats as read-only records, including transcripts only when allowed. Memory objects let callers safely list and open stored memories, while edits happen through memory tools instead. Extension objects show what installed extensions provide, but cannot be changed here. Workspace objects report basic member information and are also read-only.

## Files in this stage

### Built-in object kinds
These files expose the main user-visible workspace resources as safe object kinds with their own read, write, visibility, and deletion rules.

### `core/src/ufo/agents.py`

`domain_logic` · `request handling for workspace object operations`

An agent here is not just code that talks. It is also a saved workspace object with a prompt, model choice, reasoning setting, sandbox size, internet policy, portal visibility, icon, and optional input/output schemas. This file makes those settings visible and editable through the project’s general object system.

The main problem it solves is safety and consistency. Without this file, different parts of the system could create or edit agents in different ways, accidentally allow the wrong person to change an agent, accept a broken model name, or let the main workspace agent become private. The file centralizes those decisions.

The AgentSpec class describes the allowed shape of an agent’s settings. It forbids unknown fields and checks that any declared input or output schema is valid JSON Schema for the system’s agent-spawn contract. The AgentObjects class is the working part. It lists agents, reads one agent, returns status, applies changes, creates new agents when needed, refuses deletion, and fetches rows from the database.

The access rules are important. Any speaking workspace member may create an agent and becomes its owner. The owner or a workspace admin may edit it. Ownerless agents, such as the main or provisioned agent, can only be edited by admins. The main agent is always visible to the whole workspace. New agents do not inherit grants, credentials, sources, or memory; they start as their own clean object.

#### Function details

##### `_effective_model`  (lines 57–62)

```
def _effective_model(ctx: ToolContext, stored: str) -> str
```

**Purpose**: Shows the model an agent is actually using when someone reads agent information. If the saved value is the special "auto" choice, it reports the concrete model chosen for the current run instead of showing the placeholder.

**Data flow**: It receives the current tool context and the model value stored in the database. If the stored value is the automatic model marker, it reads the resolved model from the current agent in the context; otherwise it keeps the stored value. It returns a model name string for display or status output, without changing the saved agent settings.

**Call relations**: The listing and status paths call this helper when they need to describe an agent to a member. It lets AgentObjects.list and AgentObjects.status show the real model being used while still preserving "auto" in the stored configuration.

*Call graph*: called by 2 (list, status).


##### `AgentSpec._declared_schema`  (lines 136–141)

```
def _declared_schema(cls, value: dict[str, JsonValue] | None, info: ValidationInfo) -> dict[str, JsonValue] | None
```

**Purpose**: Checks that an agent’s optional input or output schema is acceptable before the spec is used. A schema here means a JSON-based description of what data a spawned agent may receive or return.

**Data flow**: It receives one schema value from the AgentSpec validation process. If the value is present, it passes it to the shared schema checker along with the field name, so invalid contracts are rejected early. It returns the same schema unchanged when it is valid, or no value when no schema was provided.

**Call relations**: This function runs automatically as part of building an AgentSpec. It hands the detailed validation work to ufo.contracts.check_declared_schema, so the same contract rules are reused instead of being reimplemented here.

*Call graph*: 1 external calls (check_declared_schema).


##### `_known_model`  (lines 144–161)

```
def _known_model(ctx: ToolContext, model: str, reasoning: ReasoningEffort) -> None
```

**Purpose**: Stops an agent from being saved with a model choice that the current deployment cannot run. It also prevents turning reasoning off for a model that requires reasoning to stay on.

**Data flow**: It receives the tool context, the requested model name, and the requested reasoning setting. It resolves the special automatic model choice to the deployment’s actual default, checks the deployment’s known model list and model details, and either finishes silently or raises an error explaining what is invalid. It does not return data or write anything.

**Call relations**: AgentObjects.apply calls this before updating an existing agent, and AgentObjects._create calls it before inserting a new one. This keeps bad model settings out of the database, because later conversation setup would fail if an unknown or incompatible model were saved.

*Call graph*: called by 2 (_create, apply).


##### `AgentObjects.list`  (lines 169–196)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the list of agent objects visible through the object system. Each row gets a short human-readable summary, such as whether it is the main agent and whether public internet is allowed.

**Data flow**: It receives the current context and a list query. It opens a workspace database transaction, reads agent names and a few summary fields for the current workspace, converts database rows into ObjectRow entries, and returns an ObjectPage shaped according to the query. The database is only read, not changed.

**Call relations**: This is called when the object system needs to show available agent objects. It uses ws_current to limit the query to the active workspace, uses _effective_model so automatic models display as concrete models, and hands the finished rows to object_page for paging and formatting.

*Call graph*: calls 1 internal fn (_effective_model); 5 external calls (__init__, select, workspace_tx, object_page, ws_current).


##### `AgentObjects.get`  (lines 198–227)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: Reads the full saved configuration for one named agent. This is the detailed view someone would use before deciding what to change.

**Data flow**: It receives the current context and an agent name. It asks _row for the matching database row; if none exists, it returns nothing. If a row exists, it builds an AgentSpec from the saved fields and wraps it in an ObjectDetail with timestamps and, for non-main agents, a link showing which main agent they are scoped under.

**Call relations**: The object system calls this when a user or tool asks for one agent object. It relies on AgentObjects._row for the database lookup, then creates the standard object-detail shape used by the wider object framework.

*Call graph*: calls 1 internal fn (_row); 4 external calls (__init__, __init__, __init__, __init__).


##### `AgentObjects.status`  (lines 229–246)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a compact status snapshot for one agent, focused on runtime and ownership facts rather than the full editable spec. It answers questions like: is this the main agent, which model is it effectively using, and who owns or provisioned it?

**Data flow**: It receives the current context, an agent name, and an optional expected generation value. It reads the row for that name, returns nothing if the agent is missing, and otherwise returns a small dictionary with booleans and strings suitable for status reporting. It converts UUID ownership data to a string and uses the effective model for display.

**Call relations**: The object status path calls this for quick inspection. It uses AgentObjects._row for the read and _effective_model so an agent saved as "auto" reports the actual model chosen for the current context.

*Call graph*: calls 2 internal fn (_row, _effective_model).


##### `AgentObjects.apply`  (lines 248–328)

```
async def apply(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates an agent object from a submitted AgentSpec. It is the main write path and enforces ownership, admin permission, main-agent visibility rules, model validity, and non-empty prompts.

**Data flow**: It receives the current context, target name, desired spec, and the previous spec if one exists. If there is no old object, it sends the work to _create. Otherwise it reloads the current row, checks that the speaker is the owner or an admin, validates the model and reasoning, fills in omitted fields such as prompt or icon from the existing row, checks what actually changed, and writes the new values to the database only when needed. It raises clear errors for missing objects, forbidden edits, empty prompts, or illegal main-agent visibility changes.

**Call relations**: This is the central update story for agent objects. It calls _create when an apply operation is really a new object creation, calls _row to read the current database state, calls _known_model before saving model settings, asks the ToolContext whether the speaker is an admin when ownership is not enough, and finally uses a workspace transaction to update the agent table.

*Call graph*: calls 4 internal fn (_create, _row, _known_model, speaker_is_admin); 5 external calls (__init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects._create`  (lines 330–374)

```
async def _create(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Inserts a brand-new, member-owned agent row. It deliberately creates only the submitted configuration and does not copy permissions, credentials, data sources, or derived state from another agent.

**Data flow**: It receives the current context, the new agent name, and the requested spec. It requires a speaking member, requires a non-empty prompt, validates the model and reasoning, reads existing icons in the workspace so it can choose a reasonable default icon if none was provided, and inserts a new non-main agent row with a fresh UUID and the speaker as owner. If another agent with the same name already exists, it turns the database uniqueness failure into a clear name-taken error.

**Call relations**: AgentObjects.apply calls this when applying a spec to a name that did not already have an agent. It reuses _known_model for model safety, uses auto_agent_icon for default icon selection, and writes through a workspace transaction scoped by ws_current.

*Call graph*: calls 1 internal fn (_known_model); called by 1 (apply); 6 external calls (insert, select, workspace_tx, auto_agent_icon, ws_current, uuid4).


##### `AgentObjects.delete`  (lines 376–383)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Always refuses deletion of agent objects through this object interface. This protects agents from being removed by a generic object delete command.

**Data flow**: It receives the current context, name, and optional generation value, but it does not look up or change anything. It immediately raises a VerbNotSupported error with the message that agents cannot be deleted through objects.

**Call relations**: The object system calls this when someone tries to delete an agent object. Instead of handing off to any database operation, it stops the flow at once by raising the standard unsupported-verb error.

*Call graph*: 1 external calls (__init__).


##### `AgentObjects._row`  (lines 385–421)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: Fetches the full database row for one named agent in the current workspace. It is the shared lookup helper used by the read and update paths.

**Data flow**: It receives an agent name. It opens a workspace database transaction, queries the agent table for that name and workspace, includes all fields needed by the other methods, and also includes the name of the workspace’s main agent as a related value. It returns one row when found or nothing when no such agent exists.

**Call relations**: AgentObjects.get, AgentObjects.status, and AgentObjects.apply all call this before they can build details, build status, or decide how to update. It uses ws_current to stay inside the active workspace and SQLAlchemy select statements to read from the database.

*Call graph*: called by 3 (apply, get, status); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/conversations.py`

`domain_logic` · `request handling`

Conversations in this system are not created by tools. They are created by outside chat surfaces, such as Slack or another user-facing place where people talk to the agent. This file gives the rest of the object system a safe way to read those conversations without letting anyone edit or delete them.

The main class, ConversationObjects, acts like a librarian for conversation records. It can list visible conversations, fetch one by its id, and show member-facing details in the portal. Visibility matters: a caller only sees conversations whose audience matches the subjects they are allowed to read. In plain terms, a private chat should not appear to someone else just because they know its id.

The file also supports a status view that turns the stored transcript into simple text lines like "user: hello" and "assistant: hi". If the transcript is small enough, it writes a readable text copy into the current workspace so other tools can inspect it. It double-checks that the conversation is still visible after reading the transcript, which avoids showing transcript content for a row whose visibility changed mid-request.

Attempts to create, update, or delete conversations through this object are always refused. That preserves the rule that chat surfaces make conversations, while retention policy closes or removes them.

#### Function details

##### `ConversationObjects.list`  (lines 68–70)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of conversation objects that the current tool request is allowed to read. Someone would use it to browse past conversations for the currently selected agent.

**Data flow**: It receives a tool context, including the caller's readable audience subjects, plus a list query describing paging, sorting, or filtering. It asks the database for matching conversation rows, turns each raw database row into a simple object summary, and then packages those summaries into an object page.

**Call relations**: This is the front door for listing conversations. It relies on ConversationObjects._rows to fetch only visible rows, uses _row to make each result friendly to the object system, and then hands the results to object_page so the wider object API can return a normal paged response.

*Call graph*: calls 2 internal fn (_rows, _row); 1 external calls (object_page).


##### `ConversationObjects.get`  (lines 72–74)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None
```

**Purpose**: Looks up one visible conversation by name, where the name is expected to be the conversation's id. It returns the detailed object view, or nothing if the id is invalid or hidden from the caller.

**Data flow**: It receives the tool context and the requested name. It uses the context's readable subjects to search for a matching conversation row; if one is found, it converts that row into detailed metadata with the conversation's surface, audience, timestamps, and agent link.

**Call relations**: This is the normal object-detail path used after a caller chooses a conversation. It delegates the safe lookup to ConversationObjects._find and the formatting of the answer to _detail.

*Call graph*: calls 2 internal fn (_find, _detail).


##### `ConversationObjects.member_detail`  (lines 76–91)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ConversationSpec] | None
```

**Purpose**: Provides the portal's outside-a-turn view of one conversation for a signed-in member. It deliberately limits visibility to conversations tied to that member's own audience and workspace-shared audience, even for admins.

**Data flow**: It receives an optional extension context, a conversation name, and the signed-in member's id and admin flag. It builds the member's allowed audience subjects, finds the matching row if visible, and returns both the row-style summary and the detailed object information together.

**Call relations**: This is similar to get, but it is used by the member portal rather than a running tool turn. It asks conversation_audience and audience_subjects what this member may see, uses ConversationObjects._find for the database lookup, and combines _row and _detail into a MemberObject.

*Call graph*: calls 3 internal fn (_find, _detail, _row); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ConversationObjects.status`  (lines 93–111)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status information about a conversation's transcript, including how many text lines it has, how large it is, and where a readable copy was written if it is small enough. It is the path that turns stored chat history into a workspace file for inspection.

**Data flow**: It receives a tool context, a conversation name, and an optional expected generation value. It finds the visible conversation, reads and formats its transcript, checks that the same conversation is still visible, and then optionally writes the transcript text into the sandbox workspace. The result is a small dictionary with message count, byte size, and the workspace path or null.

**Call relations**: This function ties together object lookup, transcript reading, and workspace materialization. It calls ConversationObjects._find first, ConversationObjects._exchange to read the transcript, and ConversationObjects._unchanged_visible as a safety check before returning content; if the row disappeared from view, it raises UnknownObject instead of leaking transcript data.

*Call graph*: calls 3 internal fn (_exchange, _find, _unchanged_visible); 1 external calls (__init__).


##### `ConversationObjects.apply`  (lines 113–122)

```
async def apply(self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or change a conversation through the object API. This protects the rule that conversations come from chat surfaces, not from object writes.

**Data flow**: It receives the requested name, new conversation spec, previous spec if any, and generation information. Instead of changing anything, it immediately raises a clear "verb not supported" error with the explanation that conversations are surface-made.

**Call relations**: This is called when the wider object system tries to apply a create or update operation. It does not hand off to storage or validation because mutation is never allowed here; it stops the flow with VerbNotSupported.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects.delete`  (lines 124–131)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a conversation through the object API. Deletion or closure is left to retention and surface-owned lifecycle rules.

**Data flow**: It receives the tool context, conversation name, and optional generation check. It makes no database change and raises a "verb not supported" error explaining that conversations are not authored through objects.

**Call relations**: This is the delete counterpart to ConversationObjects.apply. When the object system asks the conversation store to delete something, this function ends the operation immediately with VerbNotSupported.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects._exchange`  (lines 133–153)

```
async def _exchange(self, ctx: ToolContext, conversation_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads a conversation transcript and turns it into plain text lines, one line per visible message role and text body. It exists so status can report or write the transcript in a simple human-readable form.

**Data flow**: It receives the tool context and a conversation id. It builds the blob key for that transcript, reads the stored blob, decodes it, extracts string text or text blocks from each message, and returns a tuple of lines such as "user: ...". If the blob is missing, it returns an empty tuple; if the transcript cannot be decoded, it raises an error saying the transcript is unreadable.

**Call relations**: ConversationObjects.status calls this after it has found the conversation row. This helper talks to transcript_key and decode for the storage format, then hands status clean text lines that can be counted and written to the workspace.

*Call graph*: called by 1 (status); 2 external calls (decode, transcript_key).


##### `ConversationObjects._unchanged_visible`  (lines 155–168)

```
async def _unchanged_visible(self, subjects: frozenset[str], row: sa.Row) -> bool
```

**Purpose**: Checks that a conversation row is still visible to the same audience subjects and still has the same audience after transcript reading. This is a safety guard against a race, where permissions change while a request is in progress.

**Data flow**: It receives the readable subjects and the database row that was previously found. It opens a workspace database transaction, asks whether a matching visible row still exists with the same id and audience, and returns true or false.

**Call relations**: ConversationObjects.status uses this after reading the transcript but before returning or writing the final result. It builds on _visible, adding extra checks for the exact row, so status can refuse with UnknownObject if visibility changed.

*Call graph*: calls 1 internal fn (_visible); called by 1 (status); 3 external calls (exists, select, workspace_tx).


##### `ConversationObjects._find`  (lines 170–176)

```
async def _find(self, subjects: frozenset[str], name: str) -> sa.Row | None
```

**Purpose**: Finds one conversation row by id, but only if it is visible to the supplied audience subjects. It also quietly rejects names that are not valid UUIDs, since conversation names are stored as UUID-style ids.

**Data flow**: It receives allowed audience subjects and a name string. It tries to parse the name as a UUID; if parsing fails, it returns nothing. If parsing succeeds, it asks _rows for that specific id and returns the first matching row or nothing.

**Call relations**: This is the shared lookup helper behind get, member_detail, and status. It keeps id parsing and visibility-aware fetching in one place so those public methods do not duplicate the same checks.

*Call graph*: calls 1 internal fn (_rows); called by 3 (get, member_detail, status); 1 external calls (UUID).


##### `ConversationObjects._rows`  (lines 178–185)

```
async def _rows(self, subjects: frozenset[str], *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Fetches conversation rows from the database that are visible to a given set of audience subjects, optionally narrowed to one conversation id. It is the main database read helper for this object kind.

**Data flow**: It receives a set of readable subjects and, optionally, a conversation id. It starts with the shared visible-conversations query, adds an id filter if needed, opens a workspace database transaction, executes the query, and returns the resulting rows as a tuple.

**Call relations**: ConversationObjects.list calls this to get all visible rows for a page, while ConversationObjects._find calls it to search for one id. It depends on _visible to build the base query with workspace, agent, and audience restrictions.

*Call graph*: calls 1 internal fn (_visible); called by 2 (_find, list); 1 external calls (workspace_tx).


##### `_visible`  (lines 188–209)

```
def _visible(subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the database query for conversations the caller is allowed to see. It is the central definition of "visible conversation" for this file.

**Data flow**: It receives a set of audience subjects. It creates a SQL query that selects conversation fields and the agent name, joins conversations to their agent row, and filters to the current workspace, the currently selected object agent, and conversations whose audience is in the allowed subjects.

**Call relations**: ConversationObjects._rows uses this as the base query for normal fetching, and ConversationObjects._unchanged_visible uses it for the later safety check. By keeping these rules in one helper, listing, lookup, and status all share the same visibility wall.

*Call graph*: called by 2 (_rows, _unchanged_visible); 3 external calls (select, object_agent_id, ws_current).


##### `_row`  (lines 212–225)

```
def _row(row: sa.Row) -> ObjectRow
```

**Purpose**: Turns a raw database conversation row into a short object-list entry. It gives the object system a friendly name, summary, and small set of fields for browsing.

**Data flow**: It receives a database row. It builds a human-readable origin label from the surface and optional surface label, includes the surface fields, and returns an ObjectRow whose name is the conversation id and whose summary includes the creation date.

**Call relations**: ConversationObjects.list uses this for every listed conversation, and ConversationObjects.member_detail uses it for the portal's row-style part of the response. It is the lightweight formatter, while _detail provides the fuller view.

*Call graph*: called by 2 (list, member_detail); 1 external calls (__init__).


##### `_detail`  (lines 228–240)

```
def _detail(row: sa.Row) -> ObjectDetail[ConversationSpec]
```

**Purpose**: Turns a raw database conversation row into the full object-detail response. It includes the conversation's declared data plus timestamps and a link to the agent it belongs to.

**Data flow**: It receives a database row. It creates a ConversationSpec from the row's surface, surface label, and audience, attaches created and updated times, and adds a scoped-to link pointing at the agent name from the joined agent row.

**Call relations**: ConversationObjects.get uses this for normal detail reads, and ConversationObjects.member_detail uses it for portal detail reads. It packages the row into the standard ObjectDetail shape expected by the object system.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


### `extensions/memory/ufo_ext_memory/objects.py`

`domain_logic` · `request handling`

A memory item is a saved piece of information, such as a fact, preference, decision, event, or task. Other tools can search memory and return a reference. This file defines what happens when someone opens that reference: it fetches the full memory text, its visibility audience, recall-related fields, and links back to where it came from or what replaced it.

The file is careful about privacy. Every read is checked against the caller's “subjects,” meaning the audiences or rooms the caller is allowed to see. If a memory was distilled from a page, this code also checks that the reader can still read that exact page revision. That prevents a shared memory from leaking information from a page that has become private or changed audience.

Listing and reading behave differently on purpose. Listing shows only live memories, meaning records that have not been superseded, and returns newest ones first. Reading by exact id can still return a superseded memory, because an old search result or saved reference may point to it. In that case the result includes a `superseded_by` link, like a forwarding address telling the caller where the newer version lives.

The file also registers the `memory` object kind. It says memory is read-only here: direct apply and delete are refused. Writes happen through `memory_update`, and cleanup happens by marking memories as superseded rather than deleting them.

#### Function details

##### `_require_ext`  (lines 60–63)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure the memory object code has its extension context, which is the object that gives it access to the memory store and extension services. Without that context, the memory object cannot safely read from the database.

**Data flow**: It receives an optional extension context. If the context is present, it returns it unchanged. If it is missing, it raises an error immediately instead of letting later database code fail in a confusing way.

**Call relations**: The public read paths call this first before doing real work. `MemoryObjects.list`, `MemoryObjects.get`, `MemoryObjects.member_page`, and `MemoryObjects.member_detail` use it to confirm they have the extension context before handing the request to `_page` or `_item`.

*Call graph*: called by 4 (get, list, member_detail, member_page).


##### `_row`  (lines 66–71)

```
def _row(name: str, body: str, subject: str, item_class: str, memory_kind: str) -> ObjectRow
```

**Purpose**: This helper turns one memory record into the short row format used in object lists. It keeps the list compact by showing a shortened summary plus a few filterable fields.

**Data flow**: It receives the memory id, body text, subject, item class, and memory kind. It trims the body to a short summary and packages the subject, item class, and memory kind into list fields. It returns an `ObjectRow`, which is the list-view version of a memory.

**Call relations**: When `_page` builds a list of memories, it calls `_row` for each visible memory. `member_detail` also calls `_row` so the portal can show the same list-style row next to the full detail.

*Call graph*: called by 2 (_page, member_detail); 1 external calls (__init__).


##### `_member_reader`  (lines 74–82)

```
def _member_reader(member_id: UUID) -> SourceReader
```

**Purpose**: This helper builds the reading identity for a signed-in member who is looking at memory outside an active conversation turn. It decides which audiences that member is allowed to read as.

**Data flow**: It receives a member id. It looks up the current agent, builds the conversation audience for that member, turns that audience into readable subjects, and returns a `SourceReader`, which is the bundle of identity and allowed subjects used for read checks.

**Call relations**: The portal-facing methods `member_page` and `member_detail` call this before reading memory. Those methods then pass the resulting reader into `_page` or `_item`, so the same privacy rules are applied as if the member were reading during a normal turn.

*Call graph*: called by 2 (member_detail, member_page); 4 external calls (__init__, audience_subjects, conversation_audience, agent_current).


##### `MemoryObjects.list`  (lines 91–92)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This is the normal object-list entry point for memory during a tool or conversation request. It returns the visible, live memory items for the current caller.

**Data flow**: It receives a tool context and a list query. From the context it gets the extension context and the caller's source reader, then passes both into `_page`. The result is an object page: a filtered and paged list of memory rows.

**Call relations**: This method is called by the object system when someone lists `memory` objects in a normal tool context. It does only the setup work, then hands the real database and visibility filtering to `_page`.

*Call graph*: calls 3 internal fn (source_reader, _page, _require_ext).


##### `MemoryObjects.member_page`  (lines 94–107)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This is the portal-style list entry point for a signed-in member outside an active turn. It shows that member the live memories they are allowed to see.

**Data flow**: It receives the extension context, member id, admin flag, and list query. It builds a reader for that member, checks the extension context, and asks `_page` to fetch and filter the memory list. The admin flag is accepted but does not widen memory visibility here.

**Call relations**: Portal code calls this when it needs a member-facing memory list. It prepares the member-specific read identity with `_member_reader`, then relies on `_page` for the same listing logic used by `list`.

*Call graph*: calls 3 internal fn (_page, _member_reader, _require_ext).


##### `MemoryObjects.member_detail`  (lines 109–127)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemorySpec] | None
```

**Purpose**: This is the portal-style detail entry point for one memory item. It lets a signed-in member open a specific memory id if that memory is visible to them.

**Data flow**: It receives the extension context, memory name, member id, and admin flag. It builds the member's reader, asks `_item` for the full memory detail, and returns nothing if the item is not visible or does not exist. If it is visible, it wraps the full detail together with a short row summary in a `MemberObject`.

**Call relations**: Portal code calls this when a member opens one memory item. It delegates the actual lookup and privacy checks to `_item`, then uses `_row` to add the list-style summary expected by member-facing object views.

*Call graph*: calls 4 internal fn (_item, _member_reader, _require_ext, _row); 1 external calls (__init__).


##### `MemoryObjects.get`  (lines 129–130)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This is the normal object-detail entry point for memory during a tool or conversation request. It opens one memory by id and returns the full readable record if the caller is allowed to see it.

**Data flow**: It receives a tool context and a memory name. It gets the extension context and source reader from the tool context, then passes them to `_item`. The output is either a full `ObjectDetail` for the memory or `None` if the name is invalid, missing, or not visible.

**Call relations**: The object system calls this when someone opens a `memory` reference, such as one returned by memory search. This method prepares the context and lets `_item` perform the database lookup, page-origin check, and link building.

*Call graph*: calls 3 internal fn (source_reader, _item, _require_ext).


##### `MemoryObjects._page`  (lines 132–184)

```
async def _page(self, ext: ExtensionContext, reader: SourceReader, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This is the shared engine for listing memory rows. It finds live memory items for the reader's allowed audiences and removes any item whose source page is no longer readable in the same way.

**Data flow**: It receives the extension context, a reader with allowed subjects, and a list query. It queries the memory table for this workspace, only for the reader's subjects, only for records that are not superseded, newest first, up to a fixed maximum. Then it checks source page states for memories derived from pages and keeps only those whose page is still readable, has the same subject, and is at the same revision. Finally it converts surviving rows into object rows and applies the object paging query.

**Call relations**: `MemoryObjects.list` and `MemoryObjects.member_page` both call this after they have built the right reader. `_page` talks to the database through the extension transaction, asks the extension which source pages are readable, uses `_row` to shape each visible memory, and hands the rows to `object_page` so the object system gets a standard paged response.

*Call graph*: calls 3 internal fn (readable_page_states, transaction, _row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `MemoryObjects._item`  (lines 186–252)

```
async def _item(self, ext: ExtensionContext, reader: SourceReader, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This is the shared engine for opening one memory item. It verifies the id, checks audience rules, checks source-page visibility when needed, and builds the full detail response.

**Data flow**: It receives the extension context, a reader, and the memory name. First it tries to parse the name as a UUID, which is the stored memory id; if that fails, it returns nothing. It then reads the matching memory row for this workspace and the reader's allowed subjects. If the memory came from a page, it checks that the page is still readable with the same subject and revision. If all checks pass, it creates a `MemorySpec` with the body and recall fields, adds a `created_from` link when there is a source page, adds a `superseded_by` link when this memory has been replaced, and returns an `ObjectDetail`.

**Call relations**: `MemoryObjects.get` and `MemoryObjects.member_detail` call this whenever a caller opens a specific memory. It performs the important privacy and provenance checks, then hands back a standard object detail that the caller can display or follow through its links.

*Call graph*: calls 2 internal fn (readable_page_states, transaction); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, select, UUID).


##### `MemoryObjects.status`  (lines 254–261)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This method answers status checks for memory objects, but memory does not expose a separate mutable status here. It always reports no status.

**Data flow**: It receives the tool context, memory name, and optional expected generation value. It does not read or change anything and simply returns `None`.

**Call relations**: The object interface may ask object stores for status information. For memory objects, this method is intentionally quiet because direct object editing is not the way memory is maintained.


##### `MemoryObjects.apply`  (lines 263–272)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This method blocks attempts to create or update memory through the generic object apply path. It points callers toward the proper write path: `memory_update`.

**Data flow**: It receives the target name, proposed memory spec, old spec if any, and optional generation check. Instead of saving anything, it raises a `VerbNotSupported` error with a clear refusal message. No memory row is changed.

**Call relations**: The object system calls `apply` for object writes in kinds that support editing. This memory store rejects that flow, because writes need to go through the memory-specific update tool rather than bypassing recall and consolidation rules.

*Call graph*: 1 external calls (__init__).


##### `MemoryObjects.delete`  (lines 274–281)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This method blocks attempts to delete memory through the generic object delete path. Memories are retired by being superseded, not removed directly.

**Data flow**: It receives the tool context, memory name, and optional generation check. It does not look up or remove the memory. It raises a `VerbNotSupported` error explaining that memories cannot be deleted this way.

**Call relations**: The object system calls `delete` for object kinds that allow removal. This memory store refuses because the memory subsystem depends on superseding old items and dropping superseded items from recall, rather than deleting individual records and their derived index chunks.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/ext/extension_kind.py`

`domain_logic` · `startup and object read handling`

This file turns the set of extensions loaded by the server into an object kind named `extension`. In plain terms, it is like a read-only directory of plugins: you can look up what is installed, what version it is, and what it adds to the system, but you cannot install or remove anything here.

That separation matters because extensions are part of the deploy setup. They are pinned in a lockfile and take effect when the service starts. If chat-time object edits could add or remove them, the running system could change underneath users in unsafe or confusing ways.

The file first defines how extension names become object names: lower-case, hyphen-separated names such as `scheduled-tasks`. It checks that these names are valid and that two extensions do not collapse to the same object name.

The main read shape is `ExtensionSpec`, which lists only names: tool names, object kind names, credential slot names, surfaces, jobs, hook events, source backends, and subagent profiles. It never exposes secret credential values. `ExtensionObjects` then provides the object operations. Listing gives a compact table with version and counts. Getting one extension gives the full declaration. Status shows what the extension asks from the deploy, such as sandbox internet access or required seams from other extensions. Apply and delete always refuse with a message explaining that extension changes must go through deploy tooling.

#### Function details

##### `named_extensions`  (lines 46–61)

```
def named_extensions(manifests: tuple[Manifest, ...]) -> dict[str, Manifest]
```

**Purpose**: Builds the public object names for all active extension manifests. It also catches name clashes early, so two similarly named extensions cannot accidentally hide each other.

**Data flow**: It receives the loaded extension manifests. For each manifest, it turns the manifest name into a lower-case, hyphenated object name, checks that the result follows the workspace object naming rules, and stores it in a dictionary. It returns that dictionary, or raises an error if two manifests produce the same object name.

**Call relations**: This is used when the extension set is being prepared for exposure as objects. It relies on a text replacement step to normalize names and on the shared object-name validator to enforce the same naming rules used elsewhere.

*Call graph*: 2 external calls (sub, validate_object_name).


##### `ExtensionObjects.list`  (lines 90–107)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a paged list of installed extensions, with enough summary information to scan what each one contributes. It is the list view for the read-only `extension` object kind.

**Data flow**: It reads the stored mapping of extension object names to manifests and converts each manifest into an `ExtensionSpec`. From each spec it builds a row containing the name, version, number of tools, and number of credential slots. It then passes those rows plus the user's list query into the paging helper, which returns the final page.

**Call relations**: When someone lists `extension` objects, this method does the work. It calls `ExtensionObjects._spec` to translate each manifest into the public shape, creates object rows for display and filtering, then hands them to `object_page` so normal listing behavior such as paging and ordering applies.

*Call graph*: calls 1 internal fn (_spec); 2 external calls (__init__, object_page).


##### `ExtensionObjects.get`  (lines 109–113)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ExtensionSpec] | None
```

**Purpose**: Returns the full read-only declaration for one installed extension. A caller uses it to inspect exactly what that extension adds to the workspace.

**Data flow**: It receives an object name and looks it up in the extension mapping. If there is no matching extension, it returns `None`. If it finds one, it converts the manifest into an `ExtensionSpec` and wraps it in an object detail record with no creation or update timestamps, because these are deploy declarations rather than database rows.

**Call relations**: When someone asks for one `extension` object by name, this method answers. It uses `ExtensionObjects._spec` for the translation and returns an `ObjectDetail` so it fits the same object-reading interface as other kinds.

*Call graph*: calls 1 internal fn (_spec); 1 external calls (__init__).


##### `ExtensionObjects.status`  (lines 115–128)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Shows the deploy-level needs of one extension. This is separate from the spec: the spec says what the extension contributes, while status says what it asks the deploy to provide.

**Data flow**: It receives an extension object name and looks up the matching manifest. If none exists, it returns `None`. If found, it returns a plain dictionary containing whether the extension's sandbox tools need internet access and which required seams must be served by other extensions.

**Call relations**: This is called when the object system asks for the status of an `extension` object. Unlike `get`, it does not build the full declaration; it reports only the operational requirements recorded in the manifest.


##### `ExtensionObjects.apply`  (lines 130–139)

```
async def apply(self, ctx: ToolContext, name: str, spec: ExtensionSpec, old: ExtensionSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update an extension object. This protects the rule that extensions are installed or removed only through deploy tooling and the lockfile.

**Data flow**: It receives the proposed new spec, optional old spec, object name, context, and generation check information. It ignores those details because no mutation is allowed, and raises a `VerbNotSupported` error with an explanation.

**Call relations**: When the object system tries to apply a create or update to an `extension`, this method is the guardrail. It hands back a refusal instead of changing the manifest set.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects.delete`  (lines 141–148)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete an extension object. Removing an extension must happen as a deploy change, not as a workspace object edit.

**Data flow**: It receives the object name, context, and generation check information. It does not remove anything from the extension mapping, and instead raises a `VerbNotSupported` error explaining the correct route.

**Call relations**: When the object system tries to delete an `extension`, this method stops it. Like `apply`, it enforces that the active extension set belongs to deployment setup, not chat-time object mutation.

*Call graph*: 1 external calls (__init__).


##### `ExtensionObjects._spec`  (lines 150–168)

```
def _spec(self, manifest: Manifest) -> ExtensionSpec
```

**Purpose**: Translates an internal extension manifest into the public declaration users are allowed to read. It lists names of contributed features and credential slots, but never secret credential values.

**Data flow**: It receives one manifest. It pulls out the extension name, version, tool names, object kind names, credential slot names, surface names, job names, hook event names, source backend names, and subagent profile names. It returns an `ExtensionSpec` containing those public fields.

**Call relations**: This is the shared translator used by both `ExtensionObjects.list` and `ExtensionObjects.get`. Listing uses it to make summaries and counts; getting uses it to return the full declaration.

*Call graph*: called by 2 (get, list); 1 external calls (__init__).


### `core/src/ufo/workspace_kind.py`

`domain_logic` · `request handling`

A workspace is the shared place that members belong to. This file makes that workspace visible through the system’s object interface, but only as something you can read, not change. Think of it like a building directory in a lobby: it tells you who is in the building and who has access, but you cannot remodel the building by writing on the directory.

The workspace object has no editable settings of its own. Its information is derived from other records, especially member records. The number of members is counted from the member list, and the number of seated members comes from seat information. A “seat” means a member currently has access.

The file defines an empty `WorkspaceSpec`, because callers have nothing to fill in when describing a workspace. It defines `WorkspaceShape`, a small bundle of the facts read from storage. The main class, `WorkspaceObjects`, answers list, get, and status requests. It refuses apply and delete requests with clear messages, because seats are changed on member objects and the workspace itself is permanent.

There is also an important privacy rule. External audiences see nothing. Internal users can see workspace information, but the roster is limited: a member talking to the main agent can see the whole roster, while other agent contexts only see the speaker’s own member row.

#### Function details

##### `WorkspaceObjects.list`  (lines 73–86)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns the one workspace row that represents the current workspace, including member and seat counts. It hides the workspace completely from foreign, externally shared audiences.

**Data flow**: It receives a tool context and a list query. First it checks the audience in the context; if the audience is foreign, it returns an empty page. Otherwise it reads the current workspace id, asks `_shape` for the latest counts, builds one row with the workspace id as its name and the counts as fields, and returns that row inside a paged result shaped by the query.

**Call relations**: This is called when someone asks to list objects of kind `workspace`. It relies on `_shape` to gather the real workspace facts from the database and seat snapshot, then hands the result to the shared object paging helper so it looks like other object listings in the system.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, object_page, ws_current).


##### `WorkspaceObjects.get`  (lines 88–98)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WorkspaceSpec] | None
```

**Purpose**: Returns the basic details for the current workspace object, if the requested name is exactly this workspace’s id. The editable spec is empty because there is nothing about a workspace that can be authored here.

**Data flow**: It receives a context and an object name. It rejects foreign audiences and rejects names that do not match the current workspace id. For the matching workspace, it reads the stored timestamps through `_shape`, creates an empty `WorkspaceSpec`, and returns an object detail containing that empty spec plus creation and update times.

**Call relations**: This runs when a caller asks for one specific workspace object. It uses `_shape` for the stored metadata, but unlike `status`, it does not return the roster or counts; it returns the formal object detail used by the object system.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, __init__, ws_current).


##### `WorkspaceObjects.status`  (lines 100–121)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status for the workspace: member count, seated count, and a roster showing each visible member’s email, seat state, and admin state. It applies the file’s privacy rule about who may see the whole roster.

**Data flow**: It receives a context, workspace object name, and an optional expected generation value. It ignores the generation value because this object is read-only here. It returns nothing for foreign audiences or wrong workspace names. For the current workspace, it reads the full shape, checks whether the speaker is a member talking to the main agent, and then returns counts plus either the whole roster or only the speaker’s own roster entry.

**Call relations**: This is the richer read path used when a caller wants operational state rather than just the object’s empty spec. It depends on `_shape` for the source data and on the context’s `agent_is_main` check to decide whether to reveal the full roster.

*Call graph*: calls 2 internal fn (agent_is_main, _shape); 1 external calls (ws_current).


##### `WorkspaceObjects.apply`  (lines 123–132)

```
async def apply(self, ctx: ToolContext, name: str, spec: WorkspaceSpec, old: WorkspaceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update the workspace object. The reason is that workspace information is derived, and seat changes belong on member objects instead.

**Data flow**: It receives the usual apply inputs: context, name, proposed spec, old spec, and optional generation check. It does not inspect or save them. Instead, it immediately raises a `VerbNotSupported` error with a message explaining that seating and unseating are done through member objects.

**Call relations**: This is invoked by the object system when someone tries to apply changes to a workspace. Rather than passing work onward, it stops the request at the boundary and tells the caller which object kind should be used for seat changes.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects.delete`  (lines 134–141)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete the workspace object. A workspace is treated as permanent once created.

**Data flow**: It receives the context, workspace name, and optional generation check. It does not look up or modify anything. It raises a `VerbNotSupported` error saying the workspace is created at first run and is never deleted.

**Call relations**: This is called when the object system receives a delete request for a workspace. It acts as a guardrail, stopping the request immediately instead of touching storage.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects._shape`  (lines 143–161)

```
async def _shape(self) -> WorkspaceShape
```

**Purpose**: Collects the actual workspace facts used by list, get, and status. It gathers timestamps from the workspace row and member/seat information from the seat snapshot.

**Data flow**: It reads the current workspace id, opens a workspace database transaction, and selects the workspace row’s creation and update timestamps. In the same database connection, it asks the seat system for a snapshot of members and seated count. It then packages those facts into a `WorkspaceShape` containing member count, seated count, roster, and timestamps.

**Call relations**: This is the shared helper behind the read operations. `list`, `get`, and `status` call it so they all answer from the same fresh view of the database and seat records, instead of each duplicating its own database-reading code.

*Call graph*: called by 3 (get, list, status); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


### Object system core
These files provide the shared naming, validation, routing, and audit context machinery used by every workspace object kind.

### `core/src/ufo/objects.py`

`domain_logic` · `startup and request handling`

Workspace objects are durable named records, a bit like labeled folders in a shared office. Each folder has a kind, a name, and a spec, where the spec is the structured content supplied by a user or agent. This file makes sure every object follows the same outer rules before any extension-specific code runs.

It does three main jobs. First, it defines the common shapes: object rows for lists, full object details, links between objects, owners, pages, and store interfaces. Second, it provides reusable behavior for common cases, especially member-owned objects. That gate decides who can see, edit, or delete something based on ownership, sharing, and admin status. It also protects generated objects from being changed after a stale read, like refusing to edit a document after someone swapped it underneath you. Third, it exposes the five tool verbs: list, get, explain, apply, and delete. These verbs parse user input, find the registered kind, bind the request to the right extension context, and call the kind’s store.

The file matters because object specs are stored, shown in transcripts, and returned by reads. Without these checks, one extension could register unsafe schemas, leak secrets, collide with another kind name, or mutate objects the speaker should not control.

#### Function details

##### `_ObjectCursor.validate_rank`  (lines 173–178)

```
def validate_rank(self) -> '_ObjectCursor'
```

**Purpose**: Checks that a saved list cursor contains the right kind of value for the way the list was sorted. This keeps pagination tokens from being malformed or misleading.

**Data flow**: It reads the cursor’s rank and value after the cursor object has been built. If the rank and value match the allowed pairings, the cursor is kept. If not, it raises an error instead of letting a bad page boundary be used.

**Call relations**: This validation runs when list pagination decodes a cursor. It protects object_page before that function uses the cursor to decide which rows come after the previous page.


##### `object_page`  (lines 181–260)

```
def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Applies the shared listing rules for object rows: search, exact filters, sorting, and pagination. Kinds can provide simple rows, and this function turns them into a consistent page for callers.

**Data flow**: It receives lightweight rows and a query. It rejects row fields that collide with reserved names or were not declared, filters rows by search text and exact field values, sorts them using _sortable, then returns up to one page of rows plus a cursor if more rows remain.

**Call relations**: MemberOwnedObjects.list and MemberReadableObjects.member_page call this after they have gathered only the rows the caller may see. object_page then handles the common page mechanics and uses _ObjectCursor to create or read continuation tokens.

*Call graph*: calls 1 internal fn (_sortable); called by 2 (list, member_page); 2 external calls (__init__, __init__).


##### `object_page.value`  (lines 205–210)

```
def value(row: ObjectRow, name: str) -> JsonValue
```

**Purpose**: Looks up the value of one sortable or filterable field on a list row. It hides the difference between built-in fields like name and custom fields supplied by a kind.

**Data flow**: It receives one row and a field name. For name or summary it returns the row’s direct value; otherwise it looks in the row’s extra fields and returns that value, or null-like None if absent.

**Call relations**: This helper lives inside object_page and is used while searching, filtering, and sorting rows. It keeps object_page’s main flow from repeating the same field lookup rules.


##### `_sortable`  (lines 263–276)

```
def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]
```

**Purpose**: Turns a list field value into a safe sorting key. It allows only simple values that can be ordered predictably.

**Data flow**: It receives a JSON-like value and the field name being sorted. It turns None, booleans, numbers, and strings into ranked sortable pairs, and rejects complex values such as objects or arrays because ordering those would be unclear.

**Call relations**: object_page calls this while ordering rows and while comparing pagination boundaries. It is the small rulebook that keeps object list sorting stable.

*Call graph*: called by 1 (object_page).


##### `ObjectStore.list`  (lines 291–291)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the method every object kind’s storage layer must provide to list its objects. It is a contract, not an implementation.

**Data flow**: A store receives the current tool context and a list query. It should return an ObjectPage containing lightweight rows and, if needed, a next cursor.

**Call relations**: ObjectVerbs._list ultimately calls this on the resolved kind’s store. Concrete extensions implement it with their own tables or data source.


##### `ObjectStore.get`  (lines 293–293)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines how a store reads one full object by name. It is the contract used before showing, updating, or deleting an object.

**Data flow**: A store receives the current context and object name. It should return ObjectDetail if the object exists and is readable, or None if it is absent.

**Call relations**: ObjectVerbs._get, ObjectVerbs._apply, and ObjectVerbs._delete rely on this read before doing their work. Implementing stores supply the real lookup.


##### `ObjectStore.status`  (lines 295–301)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Defines how a store reports live, kind-specific status for an object. Status is extra information beside the saved spec, such as current state or timing.

**Data flow**: A store receives context, object name, and an optional expected generation used as a freshness check. It should return a JSON-like status dictionary or None.

**Call relations**: ObjectVerbs._get calls this after reading the object detail, passing along the generation it saw. Stores that support generation checks can refuse if the row changed between reads.


##### `ObjectStore.apply`  (lines 303–311)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how a store creates or updates an object after the shared envelope and spec validation have already passed. The store owns the actual domain change.

**Data flow**: A store receives context, name, the new validated spec, the old spec if one existed, and an optional expected generation. It performs the create or update and returns nothing on success.

**Call relations**: ObjectVerbs._apply calls this after parsing YAML, validating the object name, validating the spec model, and reading any existing object. Concrete stores decide whether the mutation is allowed.


##### `ObjectStore.delete`  (lines 313–319)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how a store deletes one object. The shared layer finds the object first, but the store performs the actual removal.

**Data flow**: A store receives context, object name, and an optional expected generation. It removes the object or raises an error if deletion is not allowed or the object changed.

**Call relations**: ObjectVerbs._delete calls this after resolving the kind and reading the old object. Concrete stores implement the deletion in their own storage.


##### `owner_emails`  (lines 338–353)

```
async def owner_emails(owners: Iterable[UUID | None]) -> dict[UUID | None, str]
```

**Purpose**: Looks up email addresses for a group of member owner IDs in one database query. List views can use this to show who owns each member-owned object.

**Data flow**: It receives owner IDs, ignores missing owners, queries the workspace member table for the remaining IDs, and returns a mapping from member ID to email address.

**Call relations**: This is a helper for object kinds that need owner email fields in their listings. It uses workspace_tx to read the workspace database safely.

*Call graph*: 2 external calls (select, workspace_tx).


##### `MemberOwnedObjects.list`  (lines 395–403)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists only the member-owned objects the current actor is allowed to see. It prevents private or admin-only rows from appearing in ordinary lists.

**Data flow**: It reads whether the speaker is an admin and the acting member ID from the context. It asks the subclass for owned rows, filters them through the visibility rule, turns them into ObjectRows, and sends them to object_page for search, sort, and pagination.

**Call relations**: ObjectVerbs._list can reach this through a kind’s store. The subclass supplies _owned_rows, while this base class supplies the common visibility gate.

*Call graph*: calls 4 internal fn (_owned_rows, _visible, object_page, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.get`  (lines 405–416)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Reads one member-owned object only if the caller may see it. If the row belongs to a generated object, it attaches the generation so later operations can detect stale reads.

**Data flow**: It finds the owner for the name, checks visibility using the acting member and admin status, then asks the subclass for full detail. If the owner carries a generation ID, it copies that generation into the returned detail.

**Call relations**: ObjectVerbs._get and other object flows can call this through the store interface. It relies on _owner, _visible, and _detail to combine generic access rules with kind-specific data.

*Call graph*: calls 4 internal fn (_detail, _owner, _visible, speaker_is_admin); 1 external calls (replace).


##### `MemberOwnedObjects.status`  (lines 418–438)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status for a visible member-owned object while guarding against races. It checks that the object has not changed before and after reading status.

**Data flow**: It finds the owner, compares its generation with the expected one, checks visibility, asks the subclass for status, then re-checks the owner and generation. It returns the status, None for an object that disappeared, or raises an unknown-object style error when visibility is lost.

**Call relations**: ObjectVerbs._get calls store.status after store.get. This method uses _require_current_generation to make sure status is not shown next to the wrong spec.

*Call graph*: calls 5 internal fn (_owner, _require_current_generation, _status, _visible, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.apply`  (lines 440–466)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a member-owned object while enforcing ownership, admin, speaker, and freshness rules. It is the reusable write gate for member-owned kinds.

**Data flow**: It checks the current owner for the name. For new rows, it verifies generation expectations and delegates creation. For existing rows, it checks visibility, freshness, whether the actor owns it or has enough admin rights, and whether a live speaker is required, then delegates the real write.

**Call relations**: ObjectVerbs._apply reaches this through a kind’s store. The method calls subclass-provided _apply_owned only after the shared access rules pass.

*Call graph*: calls 7 internal fn (_admin_can_apply, _apply_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects.delete`  (lines 468–486)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a member-owned object only when the caller is allowed to remove it. It treats invisible objects as not found, which avoids revealing private names.

**Data flow**: It finds the owner, checks the generation, rejects missing objects, checks visibility, verifies that the actor owns the row or is an admin, optionally requires a live speaker, and then asks the subclass to delete it.

**Call relations**: ObjectVerbs._delete reaches this through the store interface. The base method enforces the common gate, and _delete_owned performs the kind-specific removal.

*Call graph*: calls 6 internal fn (_delete_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects._owned`  (lines 488–492)

```
def _owned(self, owner: OwnerT, acting: UUID | None) -> bool
```

**Purpose**: Answers whether the acting member is the actual member-owner of a row. Admin-only rows are deliberately owned by no member.

**Data flow**: It receives an owner record and an acting member ID. It returns true only when the row has a member_id and that ID matches the actor.

**Call relations**: _visible uses this to decide read access, and apply and delete use it to decide whether stricter admin checks are needed.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 494–495)

```
def _visible(self, owner: OwnerT, acting: UUID | None, is_admin: bool) -> bool
```

**Purpose**: Decides whether a member-owned row is visible to the current actor. A row is visible if it is shared, owned by the actor, or the speaker is an admin.

**Data flow**: It receives an owner, acting member ID, and admin flag. It combines the shared flag, the ownership test, and the admin flag into one yes-or-no answer.

**Call relations**: List, get, status, apply, and delete all use this gate before exposing or changing a row. It calls _owned for the ownership part.

*Call graph*: calls 1 internal fn (_owned); called by 5 (apply, delete, get, list, status).


##### `MemberOwnedObjects._admin_can_apply`  (lines 497–498)

```
def _admin_can_apply(self, old: SpecT, spec: SpecT) -> bool
```

**Purpose**: Gives subclasses a narrow way to allow admins to update someone else’s object without taking full ownership. By default, it says no.

**Data flow**: It receives the old spec and the proposed new spec. The base implementation ignores both and returns false.

**Call relations**: MemberOwnedObjects.apply calls this only when an admin is trying to change a visible object that they do not own. Subclasses may override it to allow safe admin edits.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._require_current_generation`  (lines 500–517)

```
def _require_current_generation(self, name: str, owner: OwnerT | None, expected_generation: UUID | None, action: str) -> None
```

**Purpose**: Checks that the object under a name is still the same generated row that an earlier read saw. This prevents acting on stale information.

**Data flow**: It receives the object name, current owner, expected generation, and a word for the action being attempted. If the generations do not match, or an unfenced row is paired with an unexpected generation, it raises an error; otherwise it returns silently.

**Call relations**: Status, apply, and delete call this before sensitive reads or writes. It is the fence that keeps generated rows from being mixed up when names are reused.

*Call graph*: called by 3 (apply, delete, status).


##### `MemberOwnedObjects._owner`  (lines 519–520)

```
async def _owner(self, ctx: ToolContext, name: str) -> OwnerT | None
```

**Purpose**: Finds the owner record for one named object by scanning the kind’s owned rows. It is the base class’s simple way to ask, “who owns this name?”

**Data flow**: It asks _owned_rows for all rows visible to the kind’s storage path, looks for the requested name, and returns that row’s owner or None if not found.

**Call relations**: Get, status, apply, and delete call this before making access decisions. The actual row list comes from a subclass implementation of _owned_rows.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 522–523)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Defines the subclass hook that must return all owned rows for this object kind. The base class cannot know where a kind stores its rows.

**Data flow**: It receives the current tool context and is expected to return owned row summaries. The base implementation raises NotImplementedError because subclasses must provide the data.

**Call relations**: MemberOwnedObjects.list and _owner call this. Concrete object kinds implement it to feed the common access gate.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._detail`  (lines 525–528)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines the subclass hook that must return the full detail for one owned object. It separates generic permission checks from kind-specific storage reads.

**Data flow**: It receives context, object name, and the owner already found by the gate. A subclass should return ObjectDetail or None; the base version raises NotImplementedError.

**Call relations**: MemberOwnedObjects.get calls this after visibility is approved. Concrete kinds implement it to read their spec, timestamps, links, and other detail.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 530–533)

```
async def _status(self, ctx: ToolContext, name: str, owner: OwnerT) -> dict[str, JsonValue] | None
```

**Purpose**: Defines the subclass hook that must return live status for one owned object. The base class surrounds it with visibility and generation checks.

**Data flow**: It receives context, object name, and owner. A subclass should return a JSON-like status dictionary or None; the base implementation raises NotImplementedError.

**Call relations**: MemberOwnedObjects.status calls this only after the first access and freshness checks. Concrete kinds provide the status itself.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 535–543)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: OwnerT | None) -> None
```

**Purpose**: Defines the subclass hook that actually creates or updates a member-owned object. The base class performs the gatekeeping first.

**Data flow**: It receives context, object name, new spec, old spec if any, and the current owner or None for creation. A subclass writes the change; the base method raises NotImplementedError.

**Call relations**: MemberOwnedObjects.apply calls this after checking visibility, ownership, admin rules, speaker requirements, and generation freshness.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 545–546)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: OwnerT) -> None
```

**Purpose**: Defines the subclass hook that actually removes a member-owned object. The base class decides whether deletion is allowed before this is called.

**Data flow**: It receives context, object name, and owner. A subclass deletes the row; the base method raises NotImplementedError.

**Call relations**: MemberOwnedObjects.delete calls this only after the common delete gate passes.

*Call graph*: called by 1 (delete).


##### `MemberReadable.member_detail`  (lines 568–575)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject | None
```

**Purpose**: Defines the portal-facing contract for reading one object as a signed-in member outside an agent turn. A kind implements this only if it can safely answer that kind of read.

**Data flow**: It receives an extension context, object name, member ID, and admin flag. It should return a MemberObject when readable, or None when absent or hidden.

**Call relations**: Portal routes can use this protocol to read details without going through the tool turn machinery. Implementing it is an explicit opt-in by an object kind.


##### `MemberListable.member_page`  (lines 583–590)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the portal-facing contract for listing a page of objects for a signed-in member. It extends member detail reading with page search and ordering.

**Data flow**: It receives an extension context, member ID, admin flag, and ObjectListQuery. It should return an ObjectPage containing only rows that member may see.

**Call relations**: Portal index routes can call this on kinds that support member listing. Kinds that only support detail reading do not have to implement it.


##### `ConversationMemberListable.member_conversation_rows`  (lines 602–610)

```
async def member_conversation_rows(self, ext: 'ExtensionContext | None', conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Defines a contract for listing object grants connected to a conversation for one member. This is used when conversation-related object access needs a compact member-facing view.

**Data flow**: It receives an extension context, conversation ID, member ID, admin flag, and limit. It should return grant records that name objects, their generation, and whether content is visible.

**Call relations**: Conversation-facing code can call this protocol on kinds that opt into conversation object listings. The file only defines the shape; concrete kinds provide the rows.


##### `MemberReadableObjects.member_page`  (lines 623–636)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists member-readable objects for the portal using the same visibility rule as tool-side listing. It keeps the portal index from drifting away from turn-time behavior.

**Data flow**: It asks _member_rows for rows for the given member, filters them by shared/owned/admin visibility, converts them to ObjectRows, and sends them through object_page.

**Call relations**: Portal code can call this through the MemberListable protocol. It depends on subclass _member_rows and reuses object_page for the final list mechanics.

*Call graph*: calls 2 internal fn (_member_rows, object_page); 1 external calls (__init__).


##### `MemberReadableObjects.member_detail`  (lines 638–656)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject[SpecT] | None
```

**Purpose**: Reads one member-readable object for the portal and returns both its list row and full detail. It applies the same visibility idea used elsewhere.

**Data flow**: It fetches member rows, finds the requested name, checks visibility, asks _member_object for full detail, and returns a MemberObject with both row summary and detail. If anything is missing or hidden, it returns None.

**Call relations**: Portal detail routes can call this through MemberReadable. It uses subclass _member_rows and _member_object so each kind supplies its own data while sharing the gate.

*Call graph*: calls 2 internal fn (_member_object, _member_rows); 2 external calls (__init__, __init__).


##### `MemberReadableObjects._owned_rows`  (lines 658–659)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Adapts the portal-style member row hook to the tool-side member-owned base class. This prevents two separate row implementations from diverging.

**Data flow**: It receives a ToolContext, takes the extension context and acting member ID from it, and returns the rows from _member_rows.

**Call relations**: MemberOwnedObjects.list and _owner call this inherited hook. It delegates to _member_rows, which subclasses implement once for both portal and tool paths.

*Call graph*: calls 1 internal fn (_member_rows).


##### `MemberReadableObjects._detail`  (lines 661–664)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Adapts the portal-style object detail hook to the tool-side member-owned base class. It lets one kind implementation serve both access paths.

**Data flow**: It receives a ToolContext, object name, and owner. It passes the extension context and acting member ID to _member_object and returns that detail.

**Call relations**: MemberOwnedObjects.get calls this after access checks. It delegates to _member_object, the subclass-provided detail reader.

*Call graph*: calls 1 internal fn (_member_object).


##### `MemberReadableObjects._member_rows`  (lines 666–669)

```
async def _member_rows(self, ext: 'ExtensionContext | None', *, member_id: UUID | None) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Defines the subclass hook for rows used by both portal listing and tool listing. A kind provides the actual row summaries here.

**Data flow**: It receives an extension context and an optional member ID. It should return owned row summaries; the base implementation raises NotImplementedError.

**Call relations**: member_page, member_detail, and _owned_rows all call this. That makes it the single source of row data for member-readable object kinds.

*Call graph*: called by 3 (_owned_rows, member_detail, member_page).


##### `MemberReadableObjects._member_object`  (lines 671–679)

```
async def _member_object(self, ext: 'ExtensionContext | None', name: str, owner: OwnerT, *, member_id: UUID | None) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines the subclass hook for reading one full member-readable object. A kind provides the actual detail lookup here.

**Data flow**: It receives an extension context, object name, owner, and optional member ID. It should return ObjectDetail or None; the base implementation raises NotImplementedError.

**Call relations**: member_detail and _detail call this. That makes it the shared detail reader for both portal and tool access.

*Call graph*: called by 2 (_detail, member_detail).


##### `object_registry`  (lines 714–737)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: Builds and validates the deployment’s object kind registry at startup. It is the boot-time gate that catches unsafe or conflicting object kinds before the system serves requests.

**Data flow**: It receives bound object kinds, checks each kind name format, rejects duplicate names, checks declared agent-target verbs, validates the spec model, and returns a dictionary keyed by kind name.

**Call relations**: Startup code uses this to create the registry passed into ObjectVerbs. It calls _validate_spec_model so unsafe schemas fail early.

*Call graph*: calls 1 internal fn (_validate_spec_model); 1 external calls (fullmatch).


##### `_validate_spec_model`  (lines 740–760)

```
def _validate_spec_model(owner: str, kind: ObjectKind) -> None
```

**Purpose**: Checks that an object kind’s spec model is safe to store and show back to users. It rejects models that allow unknown keys, contain secret fields, or cannot be represented as JSON.

**Data flow**: It walks the spec model and nested models, checks each model’s configuration and fields, then asks Pydantic to produce a JSON schema. It raises a clear startup error if any rule fails.

**Call relations**: object_registry calls this for every registered kind. It relies on _reachable_models and _annotation_types to inspect nested model types.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 1 (object_registry).


##### `_reachable_models`  (lines 763–777)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: Finds all nested Pydantic models used inside a spec model. This lets validation rules apply not only to the top-level spec but also to embedded pieces.

**Data flow**: It starts with one model, follows field annotations that point to other Pydantic models, skips models already seen, and returns the full set it found.

**Call relations**: _validate_spec_model calls this before checking model configuration and fields. It uses _annotation_types to unwrap types such as containers or unions.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 780–787)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: Breaks a type annotation into the concrete types inside it. For example, it can look through wrapper types so nested models or secret types are not missed.

**Data flow**: It receives a type annotation. If the annotation has no type arguments, it returns it as a single item; otherwise it recursively flattens all argument types.

**Call relations**: _reachable_models uses this to find nested Pydantic models, and _validate_spec_model uses it to detect secret-bearing field types.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectVerbs.tools`  (lines 857–925)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: Creates the five tool definitions that expose the object system to agents: list, get, explain, apply, and delete. Each definition includes its input shape, description, and handler.

**Data flow**: It reads the ObjectVerbs registry-bound instance and returns ToolDef objects. Some tools are marked safe to run in parallel, while apply and delete are marked as side-effecting because they change data.

**Call relations**: The tool registry uses these definitions to make object verbs available. Each ToolDef points back to one of this class’s private handler methods.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 927–959)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: Implements the object_list tool. It either lists available object kinds or lists instances of one kind with search, filters, sorting, and pagination.

**Data flow**: It receives tool context and list arguments. Without a kind, it returns registered kind names and descriptions; with a kind, it resolves the kind, checks any agent target, builds an ObjectListQuery, calls the store’s list method, and returns JSON rows plus cursor or agent name when relevant.

**Call relations**: The object_list ToolDef calls this. It uses _resolve, _target, and _bound_ctx before handing off to the kind’s store, then formats the response with _json_result.

*Call graph*: calls 4 internal fn (_bound_ctx, _resolve, _target, _json_result); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._get`  (lines 961–997)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: Implements the object_get tool. It reads one object’s spec, status, links, and timestamps, while respecting kind lookup and optional agent targeting.

**Data flow**: It resolves the kind, binds the context to the owning extension, checks the target agent, reads detail from the store, raises if missing, reads status using the observed generation, adjusts eligible links for the target agent, and returns YAML text.

**Call relations**: The object_get ToolDef calls this. It coordinates _resolve, _target, _bound_ctx, the store’s get and status methods, and object_agent so store code runs under the right agent scope.

*Call graph*: calls 3 internal fn (_bound_ctx, _resolve, _target); 5 external calls (__init__, __init__, __init__, object_agent, safe_dump).


##### `ObjectVerbs._explain`  (lines 999–1012)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: Implements the object_explain tool. It tells a caller how to author or use a kind before creating an object.

**Data flow**: It resolves the kind and returns JSON containing the kind name, description, guidance, supported agent-target verbs, object name rule, and the spec model’s JSON schema.

**Call relations**: The object_explain ToolDef calls this. It uses _resolve to find the kind and _json_result to format the answer.

*Call graph*: calls 2 internal fn (_resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 1014–1059)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: Implements the object_apply tool for creating or updating objects from a YAML manifest. It validates the request before allowing the kind’s store to change anything.

**Data flow**: It parses the manifest into kind, name, and spec mapping; resolves the kind; checks target-agent rules; validates the object name and spec; reads any existing object; enforces create_only and cross-agent create/update support; calls the store’s apply method; and returns whether the object was created or updated.

**Call relations**: The object_apply ToolDef calls this. It uses _parse_envelope, _resolve, _target, _bound_ctx, validate_object_name, the kind’s spec model, and finally the store’s get and apply methods.

*Call graph*: calls 5 internal fn (_bound_ctx, _resolve, _target, _json_result, _parse_envelope); 4 external calls (__init__, __init__, validate_object_name, object_agent).


##### `ObjectVerbs._delete`  (lines 1061–1082)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: Implements the object_delete tool. It deletes one object and echoes the old spec when it is safe to show, so accidental deletion can often be repaired by reapplying it.

**Data flow**: It resolves the kind, binds the context, checks any target agent, reads the old object, raises if it is missing, calls the store’s delete method with the observed generation, and returns JSON saying the delete succeeded plus the old spec when visible.

**Call relations**: The object_delete ToolDef calls this. It uses _resolve, _target, _bound_ctx, object_agent, and the store’s get and delete methods before formatting with _json_result.

*Call graph*: calls 4 internal fn (_bound_ctx, _resolve, _target, _json_result); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._resolve`  (lines 1084–1089)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: Finds a registered object kind by name. It gives a helpful error listing valid kinds when the requested kind is unknown.

**Data flow**: It receives a kind name, looks it up in the registry, and returns the BoundKind if found. If not found, it raises UnknownKind with the registered names.

**Call relations**: All five object verb handlers call this near the start. It is the common doorway from a user-supplied kind string to the registered kind object.

*Call graph*: called by 5 (_apply, _delete, _explain, _get, _list); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 1091–1092)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: Rebinds a tool context to the extension context that owns the selected object kind. This makes store handlers run with their own extension’s workspace view.

**Data flow**: It receives the current ToolContext and a BoundKind. It returns a copy of the context with the ext field replaced by the bound extension context.

**Call relations**: _list, _get, _apply, and _delete call this before invoking a kind’s store. It keeps core dispatch separate from extension-specific execution context.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 1 external calls (replace).


##### `ObjectVerbs._target`  (lines 1094–1140)

```
async def _target(self, ctx: ToolContext, bound: BoundKind, name: str, verbs: frozenset[AgentTargetVerb]) -> ObjectAgent | None
```

**Purpose**: Validates and resolves an optional target agent name for agent-scoped object kinds. It enforces the rule that only the workspace main agent, during an exact live member request, can target another agent.

**Data flow**: It receives context, bound kind, requested agent name, and the verbs being attempted. If no name is given or it names the current agent, it returns None. Otherwise it checks the kind supports targeting for that verb, reads the current and target agents from the database, enforces main-agent and live-speaker rules, and returns an ObjectAgent for the target.

**Call relations**: _list, _get, _apply, and _delete call this before entering object_agent scope. Store calls then run as if addressed to the resolved agent namespace.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 3 external calls (__init__, select, workspace_tx).


##### `_parse_envelope`  (lines 1143–1161)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object]]
```

**Purpose**: Parses and checks the YAML envelope used by object_apply. It ensures the manifest has exactly the expected outer shape before spec validation begins.

**Data flow**: It receives the manifest text, rejects it if it is too large, parses it as YAML, requires a mapping with exactly kind, name, and spec, checks kind and name are strings and spec is a mapping, then returns those three pieces.

**Call relations**: ObjectVerbs._apply calls this first. Later validation handles the object name grammar and kind-specific spec model.

*Call graph*: called by 1 (_apply); 2 external calls (__init__, safe_load).


##### `_json_result`  (lines 1164–1165)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: Wraps a plain mapping as a JSON tool result. It is the common response formatter for object verbs that return JSON rather than YAML.

**Data flow**: It receives a payload mapping, serializes it with json.dumps, wraps the text in TextContent, and returns a ToolResult.

**Call relations**: _list, _explain, _apply, and _delete use this after they finish their work. It keeps their response packaging consistent.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### `core/src/ufo/object_name.py`

`data_model` · `cross-cutting`

This file is the project’s “name tag rulebook” for objects. Many parts of the system need to point at an object before they know anything else about it. To make those pointers safe and predictable, every object kind and object name must follow one common grammar.

The file defines two patterns. A kind, such as a category of object, must start with a lowercase letter and then use lowercase letters, digits, or underscores. An object name is more like a URL-friendly label: lowercase letters, digits, and hyphens, with a maximum length of 64 characters. These limits matter because object names may be stored, shown in links, or passed between parts of the system. If one part accepted strange names and another part later tried to display or parse them, things could break far away from the original mistake.

The standalone validate_object_name function checks caller-supplied names before they are written anywhere permanent. The ObjectRef model is the canonical “address label” for one object: it stores the kind, the name, and optionally an agent name when the reference crosses an agent boundary. The model is frozen, meaning it cannot be changed after creation, and it rejects unexpected fields. That makes object references stable and hard to misuse.

#### Function details

##### `validate_object_name`  (lines 22–30)

```
def validate_object_name(name: str) -> None
```

**Purpose**: Checks whether a plain object name follows the project’s shared naming rules. It is used before saving a caller-supplied name, so bad names are rejected at the point where they enter the system.

**Data flow**: It receives a string name. It checks the name’s length and whether it matches the allowed pattern of lowercase letters, digits, and hyphens. If the name is valid, nothing is returned; if it is invalid, it raises InvalidName with a clear explanation of the rule that was broken.

**Call relations**: This function is the lightweight checker for places that only have a name string, not a full ObjectRef. When validation fails, it creates an InvalidName error so the caller can stop the write before an unusable object identity is stored.

*Call graph*: 1 external calls (__init__).


##### `ObjectRef.validate_kind`  (lines 46–49)

```
def validate_kind(cls, value: str) -> str
```

**Purpose**: Checks the kind part of an ObjectRef. A kind is the category or type name for an object, and this function makes sure it uses the shared kind grammar.

**Data flow**: It receives the proposed kind string while an ObjectRef is being created. It compares that string with the allowed kind pattern. If the kind is valid, it passes the same string through; if not, it raises a ValueError explaining the expected format.

**Call relations**: Pydantic, the data validation library used for ObjectRef, calls this automatically when someone builds an ObjectRef. It acts as the gatekeeper for the kind field before the reference is accepted as a valid object identity.


##### `ObjectRef.validate_name`  (lines 53–59)

```
def validate_name(cls, value: str) -> str
```

**Purpose**: Checks the name part of an ObjectRef. It enforces the same object-name rule used elsewhere, including the 64-character limit.

**Data flow**: It receives the proposed object name while an ObjectRef is being created. It checks both the maximum length and the allowed characters and shape. If the name passes, it returns the same value; if it fails, it raises a ValueError with the rule in plain form.

**Call relations**: Pydantic calls this automatically during ObjectRef creation. Together with ObjectRef.validate_kind, it makes sure no invalid object reference can be constructed through the model.


##### `ObjectRef.__str__`  (lines 61–62)

```
def __str__(self) -> str
```

**Purpose**: Turns an ObjectRef into a short human-readable label. The label uses the common form kind/name.

**Data flow**: It reads the ObjectRef’s kind and name fields. It joins them with a slash. The result is a string like a simple path, useful for logs, messages, or display.

**Call relations**: Python calls this when code asks for the ObjectRef as text, such as with str(ref) or in formatted output. It does not include the optional agent field, because the basic display form is just the object’s kind and name.


### `core/src/ufo/object_scope.py`

`util` · `cross-cutting during audited object dispatch`

Most of the time, the system can ask the normal agent scope, “who is the current agent?” But object dispatch sometimes needs to run code on behalf of a more specific agent namespace. This file provides that temporary target.

It defines `ObjectAgent`, a small record containing an agent UUID and name. It also creates a `ContextVar`, which is like a per-task sticky note: each running task can have its own value without interfering with other tasks. That matters in asynchronous or concurrent code, where many operations may be active at once.

The `object_agent` context manager is the doorway. Code enters it with a chosen `ObjectAgent`, and for the duration of that `with` block, calls to `object_agent_id` return that object agent’s ID. When the block ends, the old value is restored, even if an error happened. If no target is provided, it simply does nothing special.

The result is a small but important routing aid: audited object handlers can attribute work to the right agent without permanently changing global state or leaking that choice into unrelated work.

#### Function details

##### `object_agent`  (lines 24–32)

```
def object_agent(target: ObjectAgent | None) -> Iterator[None]
```

**Purpose**: This function temporarily selects an object-specific agent for the current task. It is used around a block of code so that any work inside that block can be attributed to that selected agent.

**Data flow**: It receives either an `ObjectAgent` or `None`. If it gets `None`, it leaves the current task unchanged and simply runs the block. If it gets an agent, it stores that agent in the task-local context before the block runs, then restores the previous value afterward, so the temporary choice does not leak into later work.

**Call relations**: Object dispatch code uses this as a wrapper around audited object handling when it needs to point at a specific agent namespace. While the wrapper is active, `object_agent_id` can see the temporary target; after the wrapper exits, `object_agent_id` falls back to the normal current agent again.


##### `object_agent_id`  (lines 35–37)

```
def object_agent_id() -> UUID
```

**Purpose**: This function answers the question, “which agent ID should this object operation use right now?” It returns the temporary object agent ID if one has been set, otherwise it uses the normal current agent.

**Data flow**: It reads the task-local object-agent target. If a target is present, it returns that target’s UUID. If no target is present, it calls `ufo.agent_scope.agent_current` to get the regular current agent and returns that agent’s ID.

**Call relations**: Code that needs to record or route an audited object operation calls this function instead of directly asking for the normal current agent. It bridges the temporary override created by `object_agent` with the wider agent-scope system provided by `ufo.agent_scope.agent_current`.

*Call graph*: 1 external calls (agent_current).


### SDK object doorway
This file re-exports the stable object-system API surface for extension authors.

### `core/src/ufo/sdk/objects.py`

`other` · `cross-cutting; used when extensions or SDK users import object-related public APIs`

This module exists to keep a clean boundary between people building on top of UFO and the internal layout of the core code. Instead of asking extension code to import from many places like `ufo.objects`, `ufo.agents`, or `ufo.conversations`, this file gathers the object-related public pieces into one named SDK module.

It does not define new behavior. It simply re-exports selected names: object kinds, object store interfaces, ownership and permission-related types, list and page structures, and a few helper functions. Think of it like a reception desk in a large building: visitors do not need to know which office each person works in; they use the front desk as the stable point of contact.

This matters because internal files can be reorganized over time without breaking extensions, as long as this public SDK surface stays the same. The opening comment also explains a project rule: `ufo.sdk` keeps its package initializer empty, so public SDK names live in explicit modules like this one. That makes the public API easier to audit and avoids hidden side effects when importing the package.
