# Workspace, member, agent, conversation, and object resolution  `stage-6`

After a request has proved who it comes from, this stage gathers the long-lived workspace facts that the rest of the system will rely on. It is like opening the right filing cabinet before doing any work. The code resolves who belongs to the workspace, what agents exist, what conversations and objects can be seen, and which rules apply.

The member and workspace files describe the roster: who is a member, who is an admin, who has a seat, and how admins can add someone. The agents file manages editable workspace agents, while provisioning brings in default agents from installed extensions without overwriting local changes. The conversations file exposes old conversations as read-only records and can turn an allowed transcript into workspace material. The objects file is the central gate for durable named items owned by extensions: it checks names, data, permissions, and sends each request to the right owner. The object scope file tracks which agent should get credit while object work is happening. Together, these pieces build the safe request-time map used for routing and authorization.

## Files in this stage

### Built-in workspace objects
These files expose the main durable workspace entities—agents, conversations, members, and workspace metadata—as object-like resources.

### `core/src/ufo/agents.py`

`domain_logic` · `request handling`

An agent in this project is more than a running chat bot. It is a saved workspace object with a prompt, a model choice, reasoning settings, sandbox size, internet policy, and optional input/output rules for spawned runs. This file is the rulebook and doorway for reading and changing those saved agent records.

The main idea is ownership. Any speaking workspace member can create a new agent, and that member owns it. The owner or a workspace admin can edit it. Agents with no owner, such as the main agent or provisioned agents, can only be edited by admins. No agent can be deleted through this object system. Without these checks, one member could silently change another member’s agent, or the main workspace agent could be damaged by an ordinary edit.

The file also protects the shape of agent settings. It checks that custom input and output schemas are valid JSON Schema objects, refuses unknown model names when the deployment has a model registry, and prevents empty prompts. Updates are careful: leaving the prompt out means “keep the old prompt,” and leaving some optional fields out means “keep the old value.” One subtle point is the special model value `auto`: it is stored as `auto`, but when shown in list or status views it is reported as the actual model chosen for the current turn.

#### Function details

##### `_effective_model`  (lines 51–56)

```
def _effective_model(ctx: ToolContext, stored: str) -> str
```

**Purpose**: Shows the model an agent is actually using when someone reads agent information. If the saved value is the special `auto` setting, it reports the concrete model already chosen for the current turn instead.

**Data flow**: It receives the current tool context and the model value stored in the database. If the stored value is `auto`, it reads the resolved model from the current agent in the context; otherwise it keeps the stored value. It returns the model name that should be displayed to the reader.

**Call relations**: The list and status views call this helper when presenting an agent. It keeps display output useful without changing what is saved, so reading an `auto` agent and applying it back later does not accidentally freeze it to today’s concrete model.

*Call graph*: called by 2 (list, status).


##### `AgentSpec._declared_schema`  (lines 112–117)

```
def _declared_schema(cls, value: dict[str, JsonValue] | None, info: ValidationInfo) -> dict[str, JsonValue] | None
```

**Purpose**: Checks that a user-provided input or output schema is acceptable before it becomes part of an agent spec. A schema here means a JSON-based rule describing what data a spawned agent may receive or return.

**Data flow**: It receives one schema value, or nothing, during validation of `input_schema` and `output_schema`. If a schema is present, it sends it to the shared schema checker with the field name. If the schema passes, the same value comes out unchanged; if it fails, validation stops with an error.

**Call relations**: This runs automatically when an `AgentSpec` is built. It hands the real checking to `ufo.contracts.check_declared_schema`, so the agent object uses the same contract rules as the rest of the system.

*Call graph*: 1 external calls (check_declared_schema).


##### `_known_model`  (lines 120–128)

```
def _known_model(ctx: ToolContext, model: str) -> None
```

**Purpose**: Refuses to save an agent with a model name this deployment does not know about. This catches the mistake at edit time instead of leaving behind an agent that would fail every time it later runs.

**Data flow**: It receives the tool context and the requested model name. If the context has a model registry and the name is not in it, it raises an error. If there is no registry or the model is known, it returns without changing anything.

**Call relations**: Agent creation and update both call this before writing the model to the database. It is a gatekeeper placed at the only moment a member can fix the value cleanly.

*Call graph*: called by 2 (_create, apply).


##### `AgentObjects.list`  (lines 136–163)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the page of agent objects visible in the current workspace. Each row gives the agent name and a short human-readable summary of its role, model, and internet setting.

**Data flow**: It reads the current workspace id, opens a workspace database transaction, and selects agent rows for that workspace ordered by name. It turns those database rows into object-list rows, using `_effective_model` so `auto` models are shown as the resolved model. It returns a paged object result using the incoming list query.

**Call relations**: The object system calls this when someone asks to browse agents. It relies on the database transaction helper for safe reading and on `object_page` to package the rows in the standard object-list format.

*Call graph*: calls 1 internal fn (_effective_model); 5 external calls (__init__, select, workspace_tx, object_page, ws_current).


##### `AgentObjects.get`  (lines 165–192)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: Returns the full saved specification for one named agent, or says there is no such agent. This is the detailed view used before someone inspects or edits an agent.

**Data flow**: It receives a context and an agent name, then asks `_row` for the matching database row. If no row exists, it returns nothing. If a row exists, it builds an `AgentSpec` from the stored settings and wraps it with creation and update times; non-main agents also get a link showing they are scoped under the main agent.

**Call relations**: The object system calls this for detailed reads. It depends on `_row` for the database lookup, then shapes the result into the shared `ObjectDetail` format used by workspace objects.

*Call graph*: calls 1 internal fn (_row); 4 external calls (__init__, __init__, __init__, __init__).


##### `AgentObjects.status`  (lines 194–211)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a compact status snapshot for one agent. This is lighter than the full spec and focuses on facts such as whether it is the main agent, which model it currently resolves to, and who owns or provisioned it.

**Data flow**: It receives a context, an agent name, and an optional expected generation value. It looks up the database row through `_row`. If the row is missing, it returns nothing; otherwise it returns a dictionary with simple JSON-friendly values, converting the owner id to text when present and resolving `auto` through `_effective_model`.

**Call relations**: The object system calls this when it needs status rather than the editable spec. It uses `_row` for the shared lookup path and `_effective_model` so status reports show the concrete model users care about.

*Call graph*: calls 2 internal fn (_row, _effective_model).


##### `AgentObjects.apply`  (lines 213–279)

```
async def apply(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates a new agent or updates an existing one, while enforcing ownership, admin rights, valid models, and non-empty prompts. This is the main write path for agent objects.

**Data flow**: It receives the current context, target name, desired spec, and the previous spec if one exists. If there was no old object, it delegates to `_create`. Otherwise it reloads the current row, checks that the agent exists, then verifies the speaker is either the owner or an admin. It validates the model, keeps old values for fields that were intentionally omitted, refuses an empty prompt, skips the database write if nothing changed, and otherwise updates the agent row and timestamp.

**Call relations**: The object system calls this whenever an agent object is applied. It calls `_create` for new objects, `_row` to get the current database state, `_known_model` to prevent unusable model names, and `speaker_is_admin` when ownership alone is not enough.

*Call graph*: calls 4 internal fn (_create, _row, _known_model, speaker_is_admin); 5 external calls (__init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects._create`  (lines 281–312)

```
async def _create(self, ctx: ToolContext, name: str, spec: AgentSpec) -> None
```

**Purpose**: Inserts a brand-new non-main agent owned by the member who requested it. It deliberately copies no grants, credentials, sources, memory, or other derived data from anywhere else.

**Data flow**: It receives the context, new agent name, and requested spec. It checks that there is a speaking member, that the prompt exists and is not blank, and that the model is known. It then opens a workspace transaction and inserts a new agent row with a fresh id, the current workspace id, submitted settings, ownership information, and timestamps. If another row with the same name already exists, it turns the database uniqueness failure into a clear name-conflict error.

**Call relations**: `AgentObjects.apply` calls this when applying a name that does not already have an agent. It uses `_known_model` before writing, `uuid4` for the new id, and the workspace transaction helper for the database insert.

*Call graph*: calls 1 internal fn (_known_model); called by 1 (apply); 4 external calls (insert, workspace_tx, ws_current, uuid4).


##### `AgentObjects.delete`  (lines 314–321)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Always refuses deletion of agents through the workspace object interface. Agents are treated as protected records that cannot be removed by this route.

**Data flow**: It receives the context, agent name, and optional expected generation value, but does not look anything up or change the database. It immediately raises a “verb not supported” error with the standard agent deletion message.

**Call relations**: The object system calls this if someone tries to delete an agent object. Instead of handing off to database code, it stops the flow at once by raising `VerbNotSupported`.

*Call graph*: 1 external calls (__init__).


##### `AgentObjects._row`  (lines 323–357)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: Fetches the complete database row for one agent in the current workspace. It is the shared lookup used by detailed reads, status reads, and updates.

**Data flow**: It receives an agent name, reads the current workspace id, and opens a workspace database transaction. It selects the agent’s stored fields, ownership and provisioning facts, timestamps, and also includes the name of the workspace’s main agent through a small subquery. It returns one matching row or nothing if the name is not found.

**Call relations**: `get`, `status`, and `apply` all call this so they use the same workspace-scoped lookup. It centralizes the database query, which keeps the read and update paths consistent about what an agent row contains.

*Call graph*: called by 3 (apply, get, status); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/conversations.py`

`domain_logic` · `request handling`

A conversation in this system is not something a tool creates directly. It is created by a “surface”, such as a chat app or other user-facing place where people talk to an agent. This file registers conversations as an object kind so that other things, like artifacts and scheduled tasks, can point back to the conversation they came from. Think of it like a library catalog card for a meeting: it tells you which room it happened in, who was allowed in, when it was created, and can point you to the notes, but you cannot rewrite the meeting through the card. The main class, ConversationObjects, answers list, get, member_detail, and status requests. It only returns conversations for the current workspace, current selected agent, and audiences the caller is allowed to read. The status call is the one that reaches into stored transcript data. It turns the transcript into simple text lines such as “user: ...” and “assistant: ...”, and if the text is small enough, writes it into the caller’s workspace as a file. Before exposing that file, it checks that the conversation is still visible, so a permission change during the read does not leak content. Attempts to create, update, or delete conversations are rejected because conversations are controlled by surfaces and retention cleanup, not by this object API.

#### Function details

##### `ConversationObjects.list`  (lines 68–70)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the caller a page of conversations they are allowed to see. Someone uses this when they need an overview of past conversations for the selected agent.

**Data flow**: It receives a tool context, which includes the caller’s readable audience subjects, and a listing query, such as paging or ordering wishes. It fetches all visible database rows, turns each row into a short object summary, and then shapes those summaries into an ObjectPage. The result is a page of conversation entries, not full transcript content.

**Call relations**: This is the public listing path. It asks ConversationObjects._rows for the visible database rows, passes each row through _row to make user-facing summaries, and hands the collection to object_page so the object system can return a normal paged result.

*Call graph*: calls 2 internal fn (_rows, _row); 1 external calls (object_page).


##### `ConversationObjects.get`  (lines 72–74)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None
```

**Purpose**: Looks up one conversation by name and returns its detailed metadata if the caller may see it. It is used when a caller already has a conversation id and wants its surface, audience, timestamps, and links.

**Data flow**: It receives the tool context and a name string. It treats the name as a possible conversation UUID, searches only among conversations visible to the caller, and either returns nothing or converts the matching row into a detailed object record. It does not read or expose the transcript.

**Call relations**: This is the public single-object read path. It relies on ConversationObjects._find to validate and locate the row, then uses _detail to build the object detail returned to the object API.

*Call graph*: calls 2 internal fn (_find, _detail).


##### `ConversationObjects.member_detail`  (lines 76–91)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ConversationSpec] | None
```

**Purpose**: Returns one conversation for the portal when a signed-in member is outside an active turn. It is deliberately narrower than an admin-wide view: it only uses the audience attached to that member’s own conversation and shared workspace subjects.

**Data flow**: It receives an optional extension context, a conversation name, a member id, and whether the member is an admin. It builds the set of audience subjects for that member, searches for the named conversation within those subjects, and returns nothing if it is not visible. If visible, it returns both the short row view and the detailed metadata together as a MemberObject.

**Call relations**: This path supports portal-style reads rather than tool calls inside a turn. It uses conversation_audience and audience_subjects to decide what the member can see, then follows the same internal lookup and formatting path as get and list by calling _find, _row, and _detail.

*Call graph*: calls 3 internal fn (_find, _detail, _row); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ConversationObjects.status`  (lines 93–111)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports transcript status for a visible conversation and may write a readable transcript file into the workspace. This gives callers a safe way to inspect the text exchange without changing the conversation object.

**Data flow**: It receives the tool context, a conversation name, and an expected generation value that this implementation does not use. It finds the visible conversation, loads and decodes its transcript, checks again that the same conversation is still visible, and joins the messages into text. It returns the number of messages, the byte size of the text, and, if the transcript is non-empty and small enough, the workspace file path where it wrote the transcript.

**Call relations**: This is the only public method here that reads transcript content. It first uses _find, then asks _exchange to load the transcript from blob storage, and then calls _unchanged_visible before writing a workspace file. If visibility disappeared during the process, it raises UnknownObject instead of exposing the transcript.

*Call graph*: calls 3 internal fn (_exchange, _find, _unchanged_visible); 1 external calls (__init__).


##### `ConversationObjects.apply`  (lines 113–122)

```
async def apply(self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update a conversation through the object API. This protects the rule that conversations come from surfaces, not from tools editing object records.

**Data flow**: It receives the requested name, desired ConversationSpec, optional old spec, and expected generation. It ignores those change details because no mutation is allowed, and immediately raises a VerbNotSupported error with a message explaining that surfaces make conversations.

**Call relations**: The object system calls this when someone tries an apply-style mutation. Instead of handing off to storage or validation, it stops the flow by constructing VerbNotSupported.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects.delete`  (lines 124–131)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete a conversation through the object API. Deletion or closing is left to retention and surface-owned lifecycle rules.

**Data flow**: It receives the tool context, conversation name, and expected generation. It does not inspect or remove any row. It immediately raises a VerbNotSupported error explaining that conversations are surface-made and not authored through this API.

**Call relations**: The object system calls this when someone asks to delete a conversation object. This method ends that mutation path by raising VerbNotSupported rather than calling any database delete operation.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects._exchange`  (lines 133–153)

```
async def _exchange(self, ctx: ToolContext, conversation_id: UUID) -> tuple[str, ...]
```

**Purpose**: Loads a conversation transcript and turns it into simple readable lines. It exists so status can report transcript size and optionally write the exchange as a plain text file.

**Data flow**: It receives the tool context and conversation UUID. It builds the blob-storage key for that transcript, fetches the blob, decodes it, and walks through each message. String messages are used directly; structured messages are reduced to their text blocks. It returns a tuple of lines like “role: text”; if no transcript blob exists, it returns an empty tuple, and if the transcript cannot be decoded, it raises an error.

**Call relations**: ConversationObjects.status calls this after finding a visible conversation. This helper uses transcript_key to locate the stored data and decode to turn the stored transcript format into message objects that status can count and write.

*Call graph*: called by 1 (status); 2 external calls (decode, transcript_key).


##### `ConversationObjects._unchanged_visible`  (lines 155–168)

```
async def _unchanged_visible(self, subjects: frozenset[str], row: sa.Row) -> bool
```

**Purpose**: Checks that a conversation row is still visible after transcript content has been read. This is a safety check against leaking a transcript if permissions or audience changed during the request.

**Data flow**: It receives the caller’s audience subjects and the database row found earlier. It opens a workspace database transaction and asks whether a row still exists with the same id and audience inside the visible-conversations query. It returns true if that exact visible row is still present, false otherwise.

**Call relations**: ConversationObjects.status calls this after _exchange. It reuses _visible to build the same visibility boundary used for normal reads, then wraps it in a database exists check so status can decide whether to continue or raise UnknownObject.

*Call graph*: calls 1 internal fn (_visible); called by 1 (status); 3 external calls (exists, select, workspace_tx).


##### `ConversationObjects._find`  (lines 170–176)

```
async def _find(self, subjects: frozenset[str], name: str) -> sa.Row | None
```

**Purpose**: Finds one visible conversation by its object name. It treats the object name as a UUID, because conversation objects are named by their database ids.

**Data flow**: It receives a set of readable audience subjects and a name string. It first tries to parse the name as a UUID; if that fails, it returns nothing. If parsing succeeds, it asks _rows for visible rows with that id and returns the first match, or nothing if none are visible.

**Call relations**: This is the shared lookup helper for get, member_detail, and status. It keeps UUID parsing and visibility-aware row lookup in one place before those public methods format details or load transcript content.

*Call graph*: calls 1 internal fn (_rows); called by 3 (get, member_detail, status); 1 external calls (UUID).


##### `ConversationObjects._rows`  (lines 178–185)

```
async def _rows(self, subjects: frozenset[str], *, conversation_id: UUID | None) -> tuple[sa.Row, ...]
```

**Purpose**: Fetches conversation database rows that match the current workspace, selected agent, and allowed audiences. It is the central database read used by both listing and single-object lookup.

**Data flow**: It receives readable audience subjects and, optionally, a specific conversation UUID. It starts with the standard visible-conversations query, narrows it to one id if provided, opens a workspace database transaction, and returns all matching rows as a tuple.

**Call relations**: ConversationObjects.list calls this with no id to get all visible rows. ConversationObjects._find calls it with a specific id to look up one conversation. In both cases, _rows depends on _visible to build the permission-aware SQL query.

*Call graph*: calls 1 internal fn (_visible); called by 2 (_find, list); 1 external calls (workspace_tx).


##### `_visible`  (lines 188–209)

```
def _visible(subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the database query that defines which conversations are visible. This is the permission fence for this file: only rows in the current workspace, for the selected agent, and in an allowed audience can pass through.

**Data flow**: It receives a set of audience subject strings. It creates a SQL query that selects conversation fields plus the linked agent name, joins conversations to agents, and filters by current workspace id, current object agent id, and audience membership. It returns the query object; it does not execute it by itself.

**Call relations**: ConversationObjects._rows uses this query to fetch visible conversations. ConversationObjects._unchanged_visible uses it again inside an exists check, so the status path can confirm that the same visibility rules still hold before exposing transcript text.

*Call graph*: called by 2 (_rows, _unchanged_visible); 3 external calls (select, object_agent_id, ws_current).


##### `_row`  (lines 212–225)

```
def _row(row: sa.Row) -> ObjectRow
```

**Purpose**: Turns a database conversation row into the short summary shown in listings. It gives people a human-readable origin, such as a labeled channel on a surface, without exposing every detail.

**Data flow**: It receives a database row with fields like id, surface, surface_label, and creation time. It builds a friendly summary string, includes listable fields such as surface and surface_label when present, and returns an ObjectRow named by the conversation id.

**Call relations**: ConversationObjects.list uses this for every row in a listing. ConversationObjects.member_detail also uses it so the portal can show the same compact row beside the fuller detail.

*Call graph*: called by 2 (list, member_detail); 1 external calls (__init__).


##### `_detail`  (lines 228–240)

```
def _detail(row: sa.Row) -> ObjectDetail[ConversationSpec]
```

**Purpose**: Turns a database conversation row into the full object detail record. This includes the conversation’s formal spec, timestamps, and a link back to the agent it belongs to.

**Data flow**: It receives a database row. It copies surface, surface_label, and audience into a ConversationSpec, attaches created and updated timestamps, and adds a scoped_to link pointing at the agent name. It returns an ObjectDetail ready for the object API or portal.

**Call relations**: ConversationObjects.get calls this for normal single-object reads. ConversationObjects.member_detail calls it for portal reads. It also creates ObjectRef and ObjectLink values so other object views can understand that the conversation is scoped to an agent.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


### `core/src/ufo/members.py`

`domain_logic` · `request handling`

This file is the rulebook for workspace members. A member is a person known to a workspace. They may be an admin, meaning they can change workspace membership, and they may be seated, meaning they are allowed to use the agent. If someone is unseated, their access is effectively removed without deleting their membership record.

The file does two main jobs. First, `MemberObjects` exposes members as an object type that can be listed, viewed, and updated. It carefully limits what a reader can see. In the main internal agent, a member can see the workspace roster. In a child agent or a channel shared with another organization, the reader only sees their own row. This is like a staff directory that is visible in the office, but shows only your own badge information when you are in a public meeting room.

Second, `AddMember` provides the `add_member` tool. Only a current workspace admin, speaking through the main agent and not in a shared external channel, can use it. It accepts any valid email domain, so contractors or advisors can be added too. The file also protects important safety rules: members cannot be deleted through this object system, the last admin cannot be removed, and the workspace must keep at least one seated admin.

#### Function details

##### `MemberObjects.list`  (lines 65–69)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the member rows that the current speaker is allowed to see. It is used when the object system needs a page of member entries, such as a roster view.

**Data flow**: It receives the current tool context and a list query. It asks `_visible_rows` for the database rows that this speaker may see, turns each row into a short display row with `_row`, then passes those rows and the query into `object_page` to produce a paged result.

**Call relations**: This is the public listing path for member objects. It relies on `_visible_rows` to enforce privacy first, then uses `_row` and `object_page` to shape the allowed rows into the standard object-list format.

*Call graph*: calls 2 internal fn (_visible_rows, _row); 1 external calls (object_page).


##### `MemberObjects.member_page`  (lines 71–87)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds a member list for portal-style reads, where the signed-in member is reading outside a normal tool turn. It follows the same visibility rule as normal listing: whole roster from the main agent, only self from other agents.

**Data flow**: It receives the signed-in member id, whether the reader is an admin, and a paging query. It asks `_member_rows` what rows this member may see from the current agent, converts each row into a short object row with `_row`, and returns a paged object list.

**Call relations**: This is the portal counterpart to `MemberObjects.list`. Instead of using a `ToolContext`, it starts from the signed-in member id, delegates the visibility decision to `_member_rows`, then formats the result through `_row` and `object_page`.

*Call graph*: calls 2 internal fn (_member_rows, _row); 1 external calls (object_page).


##### `MemberObjects.get`  (lines 89–91)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemberSpec] | None
```

**Purpose**: Fetches the detailed editable information for one visible member object. It returns nothing if the named member is outside what the current speaker is allowed to see.

**Data flow**: It receives the current tool context and a member object name, which should be a member id as text. It asks `_visible_row` for that one allowed row. If found, it turns the database fields into an `ObjectDetail` with `_detail`; if not found, it returns `None`.

**Call relations**: This is the single-object read path used after visibility has to be checked. It depends on `_visible_row` to avoid leaking hidden members, then hands the row to `_detail` for the standard object detail shape.

*Call graph*: calls 2 internal fn (_visible_row, _detail).


##### `MemberObjects.member_detail`  (lines 93–108)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemberSpec] | None
```

**Purpose**: Builds the portal view for one member, including both the short row and the detailed admin/seat fields. It hides colleagues when the current agent is not allowed to show the full roster.

**Data flow**: It receives a member object name and the signed-in member id. It asks `_member_rows` for the rows this reader may see, looks for the row whose id matches the requested name, and either returns `None` or builds a `MemberObject` containing `_row` and `_detail` output.

**Call relations**: This is the portal counterpart to `get`, but it returns both summary and detail together. It uses `_member_rows` for the portal visibility rule, then uses `_row` and `_detail` to make the same presentation pieces used elsewhere.

*Call graph*: calls 3 internal fn (_member_rows, _detail, _row); 1 external calls (__init__).


##### `MemberObjects.status`  (lines 110–125)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small status snapshot for a visible member: their email and whether they currently have a seat. This is useful when another part of the object system needs a lightweight state check instead of the full detail object.

**Data flow**: It receives the current tool context, a member name, and an expected generation value. It looks up the visible row with `_visible_row`. If no row is visible, it returns `None`; otherwise it returns a plain dictionary with the member email and seated state.

**Call relations**: This follows the same privacy gate as `get`. It calls `_visible_row` first, then exposes only a minimal status result instead of using `_detail`.

*Call graph*: calls 1 internal fn (_visible_row).


##### `MemberObjects.apply`  (lines 127–206)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemberSpec, old: MemberSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Changes an existing member's admin role or seated access. It enforces that only a speaking workspace admin using the main agent can make these changes.

**Data flow**: It receives the current context, the member id as text, the desired `MemberSpec`, and the previous spec if one exists. It first rejects creation attempts and non-admin contexts. It parses the member id, opens a workspace database transaction, locks the workspace row to keep membership changes orderly, confirms the speaker is really an admin, loads the target member, changes their seat if needed, then updates their admin flag if allowed. It may raise clear errors if the member is unknown, the action is not allowed, or the change would leave the workspace without an admin or seated admin.

**Call relations**: This is the write path for existing member objects. It uses `ToolContext.agent_is_main` and `member_is_admin` as permission checks, uses `Seats` to grant or revoke access, and uses database updates to save role changes. It deliberately refuses creation through `apply`; new members must go through `AddMember.add` or other joining paths.

*Call graph*: calls 1 internal fn (agent_is_main); 10 external calls (__init__, __init__, __init__, __init__, select, update, workspace_tx, member_is_admin, ws_current, UUID).


##### `MemberObjects.delete`  (lines 208–215)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete member objects. Membership can be disabled by unseating someone, but this object interface does not remove the member record.

**Data flow**: It receives the current context and member name, but does not inspect or change stored data. It immediately raises a `VerbNotSupported` error explaining that workspace members cannot be deleted through objects.

**Call relations**: This completes the object interface by making the delete rule explicit. Any caller trying the standard object delete operation is stopped here and directed away from deletion.

*Call graph*: 1 external calls (__init__).


##### `MemberObjects._visible_rows`  (lines 217–222)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows the current tool speaker may see. It is the central privacy filter for normal in-turn member listing and lookup.

**Data flow**: It receives a `ToolContext`. If there is no signed-in speaker, it returns no rows. If the conversation is with a foreign audience, meaning a channel shared with another organization, it asks `_roster` for only the speaker's own row. Otherwise it asks whether the current agent is the main agent; main-agent readers get the whole roster, while other readers get only themselves.

**Call relations**: This helper is called by `list` and `_visible_row`. It tells `_roster` whether to fetch the whole workspace or only the current member, so the rest of the read flow never has to repeat the privacy rules.

*Call graph*: calls 2 internal fn (_roster, agent_is_main); called by 2 (_visible_row, list).


##### `MemberObjects._visible_row`  (lines 224–228)

```
async def _visible_row(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one named member row, but only within the rows the current speaker is allowed to see. It prevents direct lookups from bypassing the roster visibility rule.

**Data flow**: It receives a context and a member name. It first gets all visible rows from `_visible_rows`, then searches that small allowed set for a row whose id matches the name. It returns the row if found, or `None` if the member is hidden or does not exist.

**Call relations**: This helper sits between single-object callers and the broader visibility logic. `get` and `status` call it so both full detail reads and lightweight status reads obey the same access rule as listing.

*Call graph*: calls 1 internal fn (_visible_rows); called by 2 (get, status).


##### `MemberObjects._member_rows`  (lines 230–242)

```
async def _member_rows(self, member_id: UUID) -> tuple[sa.Row, ...]
```

**Purpose**: Decides which member rows a signed-in portal reader may see for the current agent. It adapts the same roster rule for code that does not have a full `ToolContext`.

**Data flow**: It receives the signed-in member id. It opens a workspace transaction, checks whether the current agent is marked as the main agent in the database, then asks `_roster` for either the whole roster or just that member's row.

**Call relations**: This helper is used by `member_page` and `member_detail`, the portal-facing read methods. It bridges portal reads to `_roster`, using `agent_current` and `ws_current` to find the current agent and workspace.

*Call graph*: calls 1 internal fn (_roster); called by 2 (member_detail, member_page); 4 external calls (select, agent_current, workspace_tx, ws_current).


##### `MemberObjects._roster`  (lines 244–264)

```
async def _roster(self, member_id: UUID, *, whole: bool) -> tuple[sa.Row, ...]
```

**Purpose**: Reads member rows from the database for the current workspace. It can return either the full roster or only one reader's own row.

**Data flow**: It receives a member id and a `whole` flag. It builds a database query for member email, id, admin status, seated status, and timestamps in the current workspace, ordered by email. If `whole` is false, it adds a filter for the given member id. It runs the query inside a workspace transaction and returns the rows as a tuple.

**Call relations**: This is the shared database read used by both normal tool reads and portal reads. `_visible_rows` and `_member_rows` decide the visibility policy, then `_roster` performs the actual fetch.

*Call graph*: called by 2 (_member_rows, _visible_rows); 3 external calls (select, workspace_tx, ws_current).


##### `_row`  (lines 267–275)

```
def _row(row: sa.Row) -> ObjectRow
```

**Purpose**: Turns a database member row into a short display row for object lists. It gives humans a compact summary of who the member is and their current role/access state.

**Data flow**: It receives a database row containing a member id, email, admin flag, and seated timestamp. It converts the id to the object name and builds a summary string such as email, workspace admin or member, seated or unseated. It returns an `ObjectRow`.

**Call relations**: This formatter is used wherever member rows are shown in list-like form: `list`, `member_page`, and `member_detail`. It keeps the wording of member summaries consistent across normal and portal reads.

*Call graph*: called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `_detail`  (lines 278–283)

```
def _detail(row: sa.Row) -> ObjectDetail[MemberSpec]
```

**Purpose**: Turns a database member row into the detailed object form used for reading or applying changes. The detail contains the editable membership facts: admin and seated.

**Data flow**: It receives a database row with role, seat, and timestamp fields. It builds a `MemberSpec` from the admin flag and whether `seated_at` is present, then wraps that spec with the row's creation and update times in an `ObjectDetail`.

**Call relations**: This formatter is used by `get` and `member_detail`. It is the companion to `_row`: `_row` is for summaries, while `_detail` is for the structured data a caller can inspect or change.

*Call graph*: called by 2 (get, member_detail); 2 external calls (__init__, __init__).


##### `AddMember.add`  (lines 309–332)

```
async def add(self, ctx: ToolContext, args: AddMemberInput) -> ToolResult
```

**Purpose**: Adds a new workspace member by email before they have ever contacted the agent. It is for admins who need to staff a workspace with colleagues, contractors, or advisors.

**Data flow**: It receives the current tool context and an `AddMemberInput` containing email, admin choice, and a user-facing description. It rejects callers who are not signed-in admins using the main agent, and rejects shared foreign channels. It normalizes and validates the email, opens a workspace transaction, locks the workspace row, confirms the speaker is an admin, checks with `_absent` that the email is not already a member, creates the member, then returns a text result saying the person can now speak to the agent.

**Call relations**: This is the handler registered by `ADD_MEMBER_TOOL_DEF`. It uses `ToolContext.agent_is_main` and `member_is_admin` for permission, `_absent` to avoid duplicates, and `create_member` to write the new member. Existing members are not changed here; their role or seat is changed through `MemberObjects.apply`.

*Call graph*: calls 2 internal fn (_absent, agent_is_main); 9 external calls (__init__, __init__, __init__, select, workspace_tx, create_member, email_domain, member_is_admin, ws_current).


##### `AddMember._absent`  (lines 334–347)

```
async def _absent(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Checks that an email address is not already a member of the current workspace. It prevents accidentally adding the same person twice.

**Data flow**: It receives an open database connection and a normalized email address. It queries the current workspace for an existing member with the same email, ignoring case. If none exists, it returns normally; if one exists, it raises an error telling the caller to change that existing member instead.

**Call relations**: This helper is called by `AddMember.add` just before creating a member. It keeps duplicate-checking separate from the larger permission and creation flow, while using the same workspace context as the add operation.

*Call graph*: called by 1 (add); 3 external calls (execute, select, ws_current).


### `core/src/ufo/workspace_kind.py`

`domain_logic` · `request handling`

A workspace is the top-level container for people using the system. This file makes that workspace visible through the project’s object system, but only as something to inspect, not something to edit. Think of it like a building lobby directory: it can tell you who belongs in the building and who currently has access, but you do not renovate the building by writing on the directory.

The main object here is `WORKSPACE_OBJECT`, which registers a kind named `workspace`. There is exactly one workspace object, named by the current workspace’s id. Its “spec” is empty because there is nothing a caller can author directly on the workspace. The useful information is all status: member count, seated count, and the roster.

The `WorkspaceObjects` class supplies the behavior. `list` returns one row for internal users, with member and seat counts. `get` returns the workspace’s timestamps and empty spec. `status` returns the counts plus roster entries, but with an important privacy rule: foreign audiences see nothing, a main internal agent can show the whole roster, and a child agent only shows the speaking member’s own row.

The file also refuses mutations on purpose. Applying changes raises an error explaining that seats are changed on member objects. Deleting raises an error because the workspace is permanent.

#### Function details

##### `WorkspaceObjects.list`  (lines 73–86)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns the workspace as a single list row, showing how many members exist and how many are seated. It is used when someone asks to list workspace objects.

**Data flow**: It receives the tool context and a list query. If the caller is from a foreign audience, it returns an empty page. Otherwise it reads the current workspace id, asks `_shape` for the latest counts, builds one row with a short summary and count fields, and passes that row through the normal paging helper so filtering and paging rules are applied.

**Call relations**: This is one of the public object-kind operations supplied by `WorkspaceObjects`. When listing is allowed, it depends on `_shape` to gather the real database-backed workspace facts, then hands the row to `object_page` so the rest of the object system gets the usual page-shaped response.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, object_page, ws_current).


##### `WorkspaceObjects.get`  (lines 88–98)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WorkspaceSpec] | None
```

**Purpose**: Returns the single workspace object’s basic detail if the requested name matches the current workspace id. The detail contains an empty spec plus creation and update timestamps.

**Data flow**: It receives the tool context and the requested object name. Foreign callers get no result, and a name that is not the current workspace id also gets no result. For the correct internal request, it reads the workspace shape, creates an empty `WorkspaceSpec`, attaches the workspace timestamps, and returns an object detail.

**Call relations**: This function is used when the object system asks for one workspace by name. Like `list`, it calls `_shape` for the database-backed facts, but it returns a detailed object view rather than a list row.

*Call graph*: calls 1 internal fn (_shape); 3 external calls (__init__, __init__, ws_current).


##### `WorkspaceObjects.status`  (lines 100–121)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status of the workspace: member count, seated count, and roster information. It also enforces who is allowed to see the full roster.

**Data flow**: It receives the tool context, the requested workspace name, and an optional expected generation value. Foreign callers or requests for another workspace id get no result. For the current workspace, it reads the shape, checks whether the speaker is a member talking to the main agent, and then returns counts plus roster entries. If the caller is allowed to see the whole roster, every member is included; otherwise only the speaking member’s own entry is included.

**Call relations**: This is the richer read path for workspace information. It calls `_shape` to collect counts and roster data, and it asks `ctx.agent_is_main` to decide whether the full roster can be shown or whether the answer must be narrowed to one member.

*Call graph*: calls 2 internal fn (agent_is_main, _shape); 1 external calls (ws_current).


##### `WorkspaceObjects.apply`  (lines 123–132)

```
async def apply(self, ctx: ToolContext, name: str, spec: WorkspaceSpec, old: WorkspaceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update the workspace object. This protects the rule that workspace seats are changed on member objects, not on the workspace itself.

**Data flow**: It receives the usual apply inputs: context, object name, new spec, old spec, and an optional expected generation. It does not inspect or save them. Instead, it immediately raises a `VerbNotSupported` error with a message telling the caller to grant or revoke a seat through the member object.

**Call relations**: The object system calls this when someone tries to apply a desired workspace spec. This implementation deliberately stops the flow there, rather than handing off to any database update, because workspace data in this file is read-only.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects.delete`  (lines 134–141)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete the workspace. The workspace is treated as permanent once created.

**Data flow**: It receives the context, object name, and optional expected generation. It does not delete anything. It raises a `VerbNotSupported` error explaining that the workspace is created at first run and is never deleted.

**Call relations**: The object system calls this when a delete is requested for a workspace object. This function is the guardrail that prevents that request from reaching any storage-changing code.

*Call graph*: 1 external calls (__init__).


##### `WorkspaceObjects._shape`  (lines 143–161)

```
async def _shape(self) -> WorkspaceShape
```

**Purpose**: Collects the current workspace facts that the read operations all need: timestamps, member count, seated count, and roster. It is the shared snapshot reader for this file.

**Data flow**: It reads the current workspace id, opens a workspace database transaction, and fetches the workspace row’s creation and update times. In the same transaction, it asks the seats subsystem for a snapshot of the members and who is seated. It then packages those pieces into a `WorkspaceShape` value and returns it.

**Call relations**: `list`, `get`, and `status` all call this helper instead of duplicating database reads. It hands back one clean bundle of workspace information, so each public operation can focus on shaping that information for its own response.

*Call graph*: called by 3 (get, list, status); 5 external calls (__init__, __init__, select, workspace_tx, ws_current).


### Object system core
These files implement the shared object registry, access checks, routing, validation, and scoped agent attribution used by all workspace object kinds.

### `core/src/ufo/objects.py`

`domain_logic` · `startup registration and object tool request handling`

Workspace objects are like labeled records in a shared filing cabinet. Each record has a kind, a name, and a spec, where the spec is the structured content of the record. This file makes sure every record follows the same outer rules, even though each extension stores and edits its own kinds of records.

The file has three main jobs. First, it defines the common shapes: object references, links between objects, list rows, pages, owners, and registered object kinds. Second, it provides shared behavior that many kinds need, such as searching, filtering, sorting, paging, and member visibility checks. For member-owned objects, it centralizes the rule that people can see their own objects, shared objects, and objects visible to admins, while hidden objects look as if they do not exist. Third, it exposes the five object tools: list, get, explain, apply, and delete.

A key safety idea is that specs are shown back to users and can appear in transcripts, so this file refuses spec models that allow unknown fields, cannot be represented as JSON, or contain secret-bearing fields. It also supports “generation” checks, a simple version fence that prevents editing or showing live status for a row that changed between reading and writing. Without this file, every extension would have to invent its own object rules, and mistakes could leak private records, accept malformed data, or silently overwrite newer changes.

#### Function details

##### `ObjectRef.validate_kind`  (lines 92–95)

```
def validate_kind(cls, value: str) -> str
```

**Purpose**: Checks that an object reference uses a valid kind name. This keeps object kind names predictable and prevents alternate spellings or unsafe characters from entering stored references.

**Data flow**: It receives the proposed kind string, compares it with the allowed lowercase snake_case pattern, and returns the same string if it is valid. If the string does not match, it raises an error before the object reference can be created.

**Call relations**: This runs automatically when an ObjectRef is built. Other code can then trust that any ObjectRef kind field has already passed the shared naming rule.


##### `ObjectRef.validate_name`  (lines 99–105)

```
def validate_name(cls, value: str) -> str
```

**Purpose**: Checks that an object reference uses a valid object name. It protects the system from names that are too long or do not follow the project’s object-name grammar.

**Data flow**: It receives the proposed name, checks both its length and its pattern, and returns it unchanged when valid. If it is too long or malformed, object creation stops with a clear error.

**Call relations**: This is part of ObjectRef validation. It relies on the central object name pattern so references follow the same rule as created objects.

*Call graph*: 1 external calls (fullmatch).


##### `ObjectRef.__str__`  (lines 107–108)

```
def __str__(self) -> str
```

**Purpose**: Turns an object reference into a compact human-readable label. This is useful for messages and logs where “kind/name” is easier to read than a full structured object.

**Data flow**: It reads the reference’s kind and name fields and joins them with a slash. It does not change the reference.

**Call relations**: Anything that prints or formats an ObjectRef can use this method implicitly to show the canonical kind/name form.


##### `_ObjectCursor.validate_rank`  (lines 206–211)

```
def validate_rank(self) -> '_ObjectCursor'
```

**Purpose**: Checks that a paging cursor stores a sort value in the expected shape. This prevents a corrupted or mismatched cursor from being used to skip through a list incorrectly.

**Data flow**: It reads the cursor’s rank and value, verifies that rank 0 means no value, rank 1 means a boolean stored as an integer, rank 2 means a number, and rank 3 means text. It returns the cursor when consistent, or raises an error when not.

**Call relations**: This runs when object_page decodes a cursor supplied by a caller. It guards the shared paging flow before the cursor is used as a boundary.


##### `object_page`  (lines 214–293)

```
def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Applies the shared list behavior for workspace objects: search, exact filters, ordering, and pagination. Object kinds can hand it lightweight rows and get consistent list results without each kind reimplementing the same rules.

**Data flow**: It receives rows and a list query. It checks that row fields do not collide with reserved names, filters rows by search text and exact field matches, sorts them using _sortable, slices one page, and returns an ObjectPage with rows plus an optional cursor for the next page.

**Call relations**: MemberOwnedObjects.list and MemberReadableObjects.member_page call this after they have already chosen which rows the caller may see. object_page delegates sort-value normalization to _sortable and builds _ObjectCursor tokens when more results remain.

*Call graph*: calls 1 internal fn (_sortable); called by 2 (list, member_page); 2 external calls (__init__, __init__).


##### `object_page.value`  (lines 238–243)

```
def value(row: ObjectRow, name: str) -> JsonValue
```

**Purpose**: Looks up one sortable or filterable value from a list row. It hides the difference between built-in fields like name and summary and extra fields supplied by a kind.

**Data flow**: It receives a row and a field name. For name or summary it returns the row’s direct field; otherwise it returns the matching value from the row’s extra fields, or none if that field is absent.

**Call relations**: This helper is used inside object_page while searching, filtering, and ordering rows. It is local to that workflow.


##### `_sortable`  (lines 296–309)

```
def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]
```

**Purpose**: Converts a list field value into a form that can be safely sorted. It gives different basic value types a stable order so pagination can remember exactly where a page ended.

**Data flow**: It receives a value and the field name it came from. It maps null, boolean, number, and string values into ranked sortable pairs, and rejects complex values such as lists or objects because those cannot be ordered clearly.

**Call relations**: object_page calls this while sorting rows and when building or comparing paging cursors. It is the small rulebook for what list fields may be ordered.

*Call graph*: called by 1 (object_page).


##### `ObjectStore.list`  (lines 324–324)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the contract for listing objects of one kind. Each object kind implements this to return lightweight rows the shared tools can show and page through.

**Data flow**: It receives a tool context and an ObjectListQuery, reads whatever storage belongs to that kind, and returns an ObjectPage. The protocol does not implement the work itself; it states what implementers must provide.

**Call relations**: ObjectVerbs._list calls the concrete store’s list method after resolving the kind and binding the right extension context.


##### `ObjectStore.get`  (lines 326–326)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines the contract for reading one full object. Each object kind implements this to return the saved spec, timestamps, links, visibility flag, and optional generation marker.

**Data flow**: It receives a tool context and object name, looks up that object in the kind’s own storage, and returns ObjectDetail or nothing if the object is absent or invisible. This protocol only describes the expected behavior.

**Call relations**: ObjectVerbs._get, ObjectVerbs._apply, and ObjectVerbs._delete use the concrete get method before reading, editing, or deleting an object.


##### `ObjectStore.status`  (lines 328–334)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Defines the contract for reading live, kind-specific status beside an object’s saved spec. Status is separate from the durable spec, like showing whether a scheduled item has a next run time.

**Data flow**: It receives context, name, and an expected generation. The concrete store returns a plain JSON-like dictionary, or nothing if no status exists or the object is gone.

**Call relations**: ObjectVerbs._get calls status after get so the response can include both saved configuration and current state. The expected generation lets stores refuse stale reads.


##### `ObjectStore.apply`  (lines 336–344)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines the contract for creating or updating one object after the shared layer has validated its outer envelope and spec. The kind-specific store performs the real domain change.

**Data flow**: It receives context, name, the new validated spec, the old spec if one existed, and an expected generation. The concrete store writes or updates its own tables, or raises an error if the mutation is not allowed.

**Call relations**: ObjectVerbs._apply calls this after parsing YAML, checking names, validating the spec model, and reading the existing object.


##### `ObjectStore.delete`  (lines 346–352)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines the contract for deleting one object. The concrete kind decides what deletion means in its own storage.

**Data flow**: It receives context, name, and an expected generation. The concrete store removes or deactivates the object, or raises an error if deletion is unsupported or unsafe.

**Call relations**: ObjectVerbs._delete calls this after resolving the kind and reading the existing object so it can fence against stale deletes.


##### `owner_emails`  (lines 371–386)

```
async def owner_emails(owners: Iterable[UUID | None]) -> dict[UUID | None, str]
```

**Purpose**: Looks up email addresses for object owners in one database query. This helps listings show an owner_email field without making one query per row.

**Data flow**: It receives a collection of member IDs, ignores empty owner values, queries the workspace member table for the remaining IDs, and returns a dictionary from member ID to email. If there are no real member IDs, it returns an empty dictionary without touching the database.

**Call relations**: Object kinds can call this while building their list rows. It uses workspace_tx to read the database safely inside a workspace transaction.

*Call graph*: 2 external calls (select, workspace_tx).


##### `MemberOwnedObjects.list`  (lines 428–436)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists member-owned objects while applying the shared visibility rule. It makes sure callers only see rows that are shared, owned by them, or visible because they are admins.

**Data flow**: It receives a tool context and list query, asks the subclass for all owned rows, filters them by the acting member and admin status, converts visible rows to ObjectRow values, and passes them to object_page for search, sort, and pagination.

**Call relations**: Concrete member-owned stores inherit this instead of writing their own visibility gate. It calls _owned_rows for data, _visible for access decisions, and object_page for final list behavior.

*Call graph*: calls 4 internal fn (_owned_rows, _visible, object_page, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.get`  (lines 438–449)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Reads one member-owned object only if the caller is allowed to see it. Hidden objects are returned as missing rather than exposing that they exist.

**Data flow**: It receives context and name, finds the row owner, checks visibility, and then asks the subclass for full detail. If the owner includes a generation marker, it copies that marker onto the returned detail so later operations can detect stale reads.

**Call relations**: ObjectVerbs._get and other store users call this through the ObjectStore interface when a subclass uses MemberOwnedObjects. It delegates owner lookup to _owner and detailed reading to _detail.

*Call graph*: calls 4 internal fn (_detail, _owner, _visible, speaker_is_admin); 1 external calls (replace).


##### `MemberOwnedObjects.status`  (lines 451–471)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reads live status for a member-owned object without leaking private data or mixing status from one version with the spec from another. It double-checks visibility and generation around the status read.

**Data flow**: It receives context, object name, and the generation seen by the earlier get. It checks that the current row still matches, verifies visibility, reads status from the subclass, then checks the row again before returning the status.

**Call relations**: ObjectVerbs._get calls store.status after store.get. This method uses _owner, _require_current_generation, _visible, and _status to make that second read safe.

*Call graph*: calls 5 internal fn (_owner, _require_current_generation, _status, _visible, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.apply`  (lines 473–499)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a member-owned object while enforcing ownership, admin, speaker, and stale-change rules. It is the central write gate for member-owned object kinds.

**Data flow**: It receives context, name, new spec, old spec if present, and expected generation. It looks up the current owner, checks visibility and version freshness, decides whether the actor owns the row or needs admin permission, optionally requires a live speaker, and then hands the actual write to _apply_owned.

**Call relations**: ObjectVerbs._apply reaches this through the store interface for member-owned kinds. Subclasses only implement the domain write; this method supplies the shared permission checks.

*Call graph*: calls 7 internal fn (_admin_can_apply, _apply_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects.delete`  (lines 501–519)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a member-owned object only when the caller has the right to remove it. It prevents hidden objects from being confirmed and protects against deleting a row that changed after it was read.

**Data flow**: It receives context, name, and expected generation. It finds the owner, checks generation, rejects missing or invisible rows, checks ownership or admin permission, optionally requires a live speaker, and then calls _delete_owned.

**Call relations**: ObjectVerbs._delete reaches this through the store interface. It uses the shared helper methods before handing the concrete delete to the subclass.

*Call graph*: calls 6 internal fn (_delete_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects._owned`  (lines 521–525)

```
def _owned(self, owner: OwnerT, acting: UUID | None) -> bool
```

**Purpose**: Answers whether the acting member is the actual member owner of a row. Admin-only rows have no member owner, so this never treats a missing member ID as ownership.

**Data flow**: It receives an owner record and the acting member ID. It returns true only when the row has a non-empty member_id and it equals the acting member ID.

**Call relations**: _visible uses this for read access, while apply and delete use it to decide whether admin permission is needed.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 527–528)

```
def _visible(self, owner: OwnerT, acting: UUID | None, is_admin: bool) -> bool
```

**Purpose**: Applies the read-visibility rule for member-owned rows. A row is visible if it is shared, owned by the acting member, or the speaker is an admin.

**Data flow**: It receives an owner record, acting member ID, and admin flag. It combines the row’s shared flag, the _owned check, and the admin flag into a single true or false answer.

**Call relations**: list, get, status, apply, and delete all call this before showing or touching existing rows. It is the common gate that keeps read and write behavior consistent.

*Call graph*: calls 1 internal fn (_owned); called by 5 (apply, delete, get, list, status).


##### `MemberOwnedObjects._admin_can_apply`  (lines 530–531)

```
def _admin_can_apply(self, old: SpecT, spec: SpecT) -> bool
```

**Purpose**: Lets a subclass optionally allow admins to make certain updates to someone else’s object. The default is intentionally strict and allows no such update.

**Data flow**: It receives the old and new specs and returns false. A subclass can override it to inspect the change and return true for safe admin edits.

**Call relations**: MemberOwnedObjects.apply calls this when the actor is not the owner. Without an override, admins still cannot edit another member’s owned row except in cases handled by the broader apply logic.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._require_current_generation`  (lines 533–550)

```
def _require_current_generation(self, name: str, owner: OwnerT | None, expected_generation: UUID | None, action: str) -> None
```

**Purpose**: Refuses an operation when the row under a name is no longer the same version that was read earlier. This is like checking that a document has not changed since you opened it before saving over it.

**Data flow**: It receives the object name, current owner, expected generation, and action label. If the current generated owner does not match the expected generation, or an unexpected generation appears or disappears, it raises an error; otherwise it returns without changing anything.

**Call relations**: status, apply, and delete call this to fence reads and writes. It only has an effect for generated owners that carry a generation value.

*Call graph*: called by 3 (apply, delete, status).


##### `MemberOwnedObjects._owner`  (lines 552–553)

```
async def _owner(self, ctx: ToolContext, name: str) -> OwnerT | None
```

**Purpose**: Finds the owner record for one named object by scanning the subclass’s owned rows. It gives the shared gate enough information to decide visibility and write permission.

**Data flow**: It receives context and object name, asks _owned_rows for all rows, and returns the owner for the matching name or nothing if none exists.

**Call relations**: get, status, apply, and delete call this before deciding what the caller may do. It depends on the subclass-provided _owned_rows method.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 555–556)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Placeholder method for subclasses to provide the list of member-owned rows. It is where each concrete kind supplies its own data.

**Data flow**: It receives a tool context and is expected to return owned rows with names, summaries, owners, and lightweight fields. In this base class it raises NotImplementedError because the base class has no storage of its own.

**Call relations**: MemberOwnedObjects.list and _owner call this. Concrete object kinds must override it for the shared visibility gate to work.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._detail`  (lines 558–561)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Placeholder method for subclasses to read the full detail for a visible member-owned object. The base class leaves storage-specific reading to the concrete kind.

**Data flow**: It receives context, name, and owner, and should return ObjectDetail or nothing. In the base class it raises NotImplementedError.

**Call relations**: MemberOwnedObjects.get calls this only after the shared owner and visibility checks pass. Subclasses override it to fetch the real spec and metadata.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 563–566)

```
async def _status(self, ctx: ToolContext, name: str, owner: OwnerT) -> dict[str, JsonValue] | None
```

**Purpose**: Placeholder method for subclasses to read live status for a member-owned object. The base class cannot know what status means for each kind.

**Data flow**: It receives context, name, and owner, and should return a JSON-like status dictionary or nothing. In this base class it raises NotImplementedError.

**Call relations**: MemberOwnedObjects.status calls this between generation and visibility checks. Subclasses override it to provide kind-specific live state.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 568–576)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: OwnerT | None) -> None
```

**Purpose**: Placeholder method for subclasses to create or update the actual member-owned row. The shared base class performs the permission checks before this is called.

**Data flow**: It receives context, name, new spec, old spec if present, and the current owner if one exists. A concrete subclass writes the change; the base class raises NotImplementedError.

**Call relations**: MemberOwnedObjects.apply calls this after validation, visibility, ownership, and generation checks. Subclasses override it with their domain-specific mutation.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 578–579)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: OwnerT) -> None
```

**Purpose**: Placeholder method for subclasses to perform the actual deletion of a member-owned row. The base class handles who is allowed to delete first.

**Data flow**: It receives context, name, and owner. A concrete subclass removes the row or marks it deleted; the base class raises NotImplementedError.

**Call relations**: MemberOwnedObjects.delete calls this only after shared delete checks pass. Subclasses override it to touch their own storage.

*Call graph*: called by 1 (delete).


##### `MemberReadable.member_detail`  (lines 601–608)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject | None
```

**Purpose**: Defines the opt-in contract for object kinds that can show one object to a signed-in member outside an active tool turn. This supports portal-style pages where a member browses objects directly.

**Data flow**: It receives an extension context, object name, member ID, and admin flag. An implementation returns a MemberObject if visible, or nothing if absent or not visible.

**Call relations**: Portal routes or similar member-facing code can call this on kinds that implement MemberReadable. Kinds that cannot answer outside a turn simply do not implement it.


##### `MemberListable.member_page`  (lines 616–623)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the opt-in contract for member-facing object listings. It is the list-page companion to MemberReadable.member_detail.

**Data flow**: It receives an extension context, member ID, admin flag, and ObjectListQuery. An implementation returns an ObjectPage containing only objects the member may see.

**Call relations**: Member-facing index routes can call this on kinds that implement MemberListable. It extends the detail-reading contract with page listing behavior.


##### `ConversationMemberListable.member_conversation_rows`  (lines 635–643)

```
async def member_conversation_rows(self, ext: 'ExtensionContext | None', conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Defines a contract for listing object grants related to a conversation for one member. It lets a kind expose conversation-linked objects with generation and content visibility information.

**Data flow**: It receives an extension context, conversation ID, member ID, admin flag, and limit. An implementation returns a tuple of ConversationObjectGrant records.

**Call relations**: Conversation-facing code can use this protocol when a kind supports conversation object rows. The file defines the shape but leaves the data lookup to implementers.


##### `MemberReadableObjects.member_page`  (lines 656–669)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Provides a ready-made member-facing list page for member-owned object kinds. It uses the same visibility rule as turn-side listing so portal and tool views do not drift apart.

**Data flow**: It receives extension context, member ID, admin flag, and query. It asks _member_rows for rows, keeps only visible ones, converts them to ObjectRow values, and returns the paged result from object_page.

**Call relations**: Kinds that inherit MemberReadableObjects get this member-facing listing automatically once they implement _member_rows. It calls object_page for shared search, filter, sort, and cursor behavior.

*Call graph*: calls 2 internal fn (_member_rows, object_page); 1 external calls (__init__).


##### `MemberReadableObjects.member_detail`  (lines 671–689)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject[SpecT] | None
```

**Purpose**: Provides a ready-made member-facing detail read for member-owned object kinds. It returns both the list row information and the full object detail when the member may see it.

**Data flow**: It receives extension context, object name, member ID, and admin flag. It finds the row from _member_rows, checks visibility, reads full detail from _member_object, and wraps both pieces in a MemberObject.

**Call relations**: Member-facing detail routes can use this on MemberReadableObjects subclasses. It shares the same underlying row and detail methods that the tool-side path delegates to.

*Call graph*: calls 2 internal fn (_member_object, _member_rows); 2 external calls (__init__, __init__).


##### `MemberReadableObjects._owned_rows`  (lines 691–692)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Adapts the member-facing row provider to the tool-side MemberOwnedObjects interface. This keeps turn-side and portal-side row sources the same.

**Data flow**: It receives a ToolContext, takes the extension context and acting member ID from it, and returns the rows from _member_rows.

**Call relations**: MemberOwnedObjects.list and _owner call this through inheritance. It delegates to _member_rows so subclasses only need one row-reading implementation.

*Call graph*: calls 1 internal fn (_member_rows).


##### `MemberReadableObjects._detail`  (lines 694–697)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Adapts the member-facing object reader to the tool-side MemberOwnedObjects detail interface. This keeps detail rendering consistent across access paths.

**Data flow**: It receives a ToolContext, name, and owner, then calls _member_object with the context’s extension and acting member ID. It returns the resulting ObjectDetail or nothing.

**Call relations**: MemberOwnedObjects.get calls this through inheritance. It delegates to _member_object, which subclasses implement.

*Call graph*: calls 1 internal fn (_member_object).


##### `MemberReadableObjects._member_rows`  (lines 699–702)

```
async def _member_rows(self, ext: 'ExtensionContext | None', *, member_id: UUID | None) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Placeholder method for subclasses to provide rows for a member-readable object kind. It is the single row source used by both member-facing pages and tool-side owned-object gates.

**Data flow**: It receives an extension context and optional member ID, and should return owned rows. In this base class it raises NotImplementedError.

**Call relations**: member_page, member_detail, and _owned_rows all call this. Concrete subclasses must override it to make listing possible.

*Call graph*: called by 3 (_owned_rows, member_detail, member_page).


##### `MemberReadableObjects._member_object`  (lines 704–712)

```
async def _member_object(self, ext: 'ExtensionContext | None', name: str, owner: OwnerT, *, member_id: UUID | None) -> ObjectDetail[SpecT] | None
```

**Purpose**: Placeholder method for subclasses to provide full object detail for a member-readable object kind. It is the shared detail source for portal and tool reads.

**Data flow**: It receives extension context, object name, owner, and optional member ID, and should return ObjectDetail or nothing. In the base class it raises NotImplementedError.

**Call relations**: member_detail and _detail call this. Concrete subclasses override it to read their own stored object content.

*Call graph*: called by 2 (_detail, member_detail).


##### `object_registry`  (lines 747–770)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: Validates and indexes all object kinds at startup. It is the boot-time gate that prevents conflicting kind names and unsafe spec definitions from reaching a running system.

**Data flow**: It receives bound object kinds, checks each kind name, checks for duplicate names, verifies declared cross-agent verbs, validates each spec model, and returns a dictionary keyed by kind name. If anything is invalid, it raises an error before serving requests.

**Call relations**: Startup wiring calls this after core and extensions declare their object kinds. It delegates spec safety checks to _validate_spec_model.

*Call graph*: calls 1 internal fn (_validate_spec_model).


##### `_validate_spec_model`  (lines 773–793)

```
def _validate_spec_model(owner: str, kind: ObjectKind) -> None
```

**Purpose**: Checks that an object kind’s spec model is safe to store, show, and echo back. It blocks unknown extra fields, secret-bearing fields, and models that cannot be represented as JSON.

**Data flow**: It receives the owner label and object kind, walks the main spec model and nested models, inspects each field, and asks Pydantic to build a JSON schema. It raises clear errors for unsafe model choices.

**Call relations**: object_registry calls this for every registered kind during startup. It uses _reachable_models and _annotation_types to inspect nested field types.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 1 (object_registry).


##### `_reachable_models`  (lines 796–810)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: Finds all Pydantic models nested inside a spec model. This lets validation check not just the top-level spec, but also embedded objects inside it.

**Data flow**: It receives a model class, walks through its fields, follows annotations that point to other Pydantic models, avoids repeats, and returns the full set it found.

**Call relations**: _validate_spec_model calls this before checking field rules. It uses _annotation_types to look through wrapper types such as lists or unions.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 813–820)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: Breaks a type annotation into the concrete pieces inside it. For example, it can look through a container or union and find the inner types that matter for validation.

**Data flow**: It receives a type annotation, asks Python’s typing system for its arguments, and recursively flattens those arguments. If there are no arguments, it returns the annotation itself.

**Call relations**: _reachable_models uses this to find nested Pydantic models, and _validate_spec_model uses it to detect secret-bearing field types.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectVerbs.tools`  (lines 890–958)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: Builds the five tool definitions that expose the object system: list, get, explain, apply, and delete. These ToolDefs tell the tool registry what each tool is called, what input shape it accepts, and which method runs it.

**Data flow**: It reads the ObjectVerbs registry and returns a tuple of ToolDef objects. Each ToolDef includes a name, user-facing description, input model, handler method, and safety flag such as parallel-safe or side-effecting.

**Call relations**: Tool registration code calls this to make object verbs available. The handlers it names are ObjectVerbs._list, _get, _explain, _apply, and _delete.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 960–992)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: Implements the object_list tool. It either lists all registered object kinds or lists instances of one kind with search, filters, ordering, and pagination.

**Data flow**: It receives tool context and list arguments. With no kind, it returns kind names and descriptions; with a kind, it resolves the kind, checks any agent target, builds an ObjectListQuery, calls the store’s list method, and returns JSON containing rows, optional agent name, and optional next cursor.

**Call relations**: The ToolDef from tools points object_list here. It uses _resolve, _target, _bound_ctx, object_agent, and _json_result to route the request safely.

*Call graph*: calls 4 internal fn (_bound_ctx, _resolve, _target, _json_result); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._get`  (lines 994–1030)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: Implements the object_get tool. It reads one object’s spec, live status, links, and timestamps, while respecting kind routing and optional agent targeting.

**Data flow**: It receives tool context and get arguments, resolves the kind, binds the extension context, checks the agent target, reads the detail, then reads status using the detail’s generation. It renders the result as YAML text, hiding the spec if the store marked it not visible.

**Call relations**: The ToolDef from tools points object_get here. It calls the concrete store’s get and status methods inside the object_agent scope and raises UnknownObject when the object is not found.

*Call graph*: calls 3 internal fn (_bound_ctx, _resolve, _target); 5 external calls (__init__, __init__, __init__, object_agent, safe_dump).


##### `ObjectVerbs._explain`  (lines 1032–1045)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: Implements the object_explain tool. It tells a caller how to author objects of a kind before they create or update one.

**Data flow**: It receives context and a kind name, resolves the registered kind, and returns JSON with the kind description, guidance, allowed cross-agent verbs, name rule, and JSON schema for the spec.

**Call relations**: The ToolDef from tools points object_explain here. It uses _resolve and _json_result; it does not call the object store because it explains the kind declaration, not a stored instance.

*Call graph*: calls 2 internal fn (_resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 1047–1092)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: Implements the object_apply tool, which creates or updates an object from a YAML manifest. It validates the manifest and spec before allowing the kind-specific store to write anything.

**Data flow**: It receives context and apply arguments, parses the YAML envelope, resolves the kind, checks the optional agent target, validates the object name and spec model, reads any existing object, enforces create_only and cross-agent create/update support, calls the store’s apply method, and returns JSON saying whether it created or updated.

**Call relations**: The ToolDef from tools points object_apply here. It relies on _parse_envelope, _resolve, _target, _bound_ctx, validate_object_name, the store’s get/apply methods, and _json_result.

*Call graph*: calls 5 internal fn (_bound_ctx, _resolve, _target, _json_result, _parse_envelope); 4 external calls (__init__, __init__, validate_object_name, object_agent).


##### `ObjectVerbs._delete`  (lines 1094–1115)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: Implements the object_delete tool. It removes one object and echoes the deleted spec when the spec is visible, which helps recover from accidental deletes on recreatable kinds.

**Data flow**: It receives context and delete arguments, resolves the kind, binds the extension context, checks any agent target, reads the existing object, refuses if missing, calls the store’s delete method with the observed generation, and returns JSON confirming deletion plus the old spec when allowed.

**Call relations**: The ToolDef from tools points object_delete here. It uses _resolve, _target, _bound_ctx, object_agent, the store’s get/delete methods, and _json_result.

*Call graph*: calls 4 internal fn (_bound_ctx, _resolve, _target, _json_result); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._resolve`  (lines 1117–1122)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: Finds the registered object kind for a kind name. It gives tool handlers a single place to turn a user-supplied kind string into the bound kind and extension context.

**Data flow**: It receives a kind name, looks it up in the registry, and returns the BoundKind when found. If not found, it raises UnknownKind with the list of registered kinds.

**Call relations**: _list, _get, _explain, _apply, and _delete all call this before dispatching to a store. It is the common unknown-kind error path.

*Call graph*: called by 5 (_apply, _delete, _explain, _get, _list); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 1124–1125)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: Rebinds a tool context to the extension that owns the selected object kind. This makes the same tool request run with the correct extension-specific context.

**Data flow**: It receives the current ToolContext and a BoundKind, copies the context while replacing its extension field with the bound kind’s context, and returns the copy. It does not mutate the original context.

**Call relations**: _list, _get, _apply, and _delete call this before invoking a store. Store handlers then see the extension context they were declared with.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 1 external calls (replace).


##### `ObjectVerbs._target`  (lines 1127–1173)

```
async def _target(self, ctx: ToolContext, bound: BoundKind, name: str, verbs: frozenset[AgentTargetVerb]) -> ObjectAgent | None
```

**Purpose**: Checks and resolves optional cross-agent targeting. It allows the workspace main agent, on an exact live member-requested call, to operate on an agent-scoped object kind for another named agent when the kind permits that verb.

**Data flow**: It receives context, bound kind, target agent name, and allowed verbs. If no name is provided it returns nothing; otherwise it checks the kind’s target support, reads the current agent from the database, enforces main-agent and live-speaker rules, looks up the named target agent, and returns an ObjectAgent.

**Call relations**: _list, _get, _apply, and _delete call this before entering the object_agent scope. It uses workspace_tx and database queries to confirm both the current and target agents.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 3 external calls (__init__, select, workspace_tx).


##### `_parse_envelope`  (lines 1176–1194)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object]]
```

**Purpose**: Parses and validates the YAML wrapper used by object_apply. It makes sure the input is exactly one small document with kind, name, and spec.

**Data flow**: It receives the manifest text, checks its byte size, parses it as safe YAML, requires a mapping with exactly the keys kind, name, and spec, checks that kind and name are strings and spec is a mapping, and returns those three values.

**Call relations**: ObjectVerbs._apply calls this before resolving the kind or validating the spec. It raises InvalidManifest for malformed input so bad writes stop early.

*Call graph*: called by 1 (_apply); 2 external calls (__init__, safe_load).


##### `_json_result`  (lines 1197–1198)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: Wraps a JSON payload in the standard tool result format. It is a small helper that keeps JSON-returning object tools consistent.

**Data flow**: It receives a mapping, converts it to a JSON string, wraps that string in TextContent, and returns a ToolResult containing it.

**Call relations**: ObjectVerbs._list, _explain, _apply, and _delete call this for JSON responses. ObjectVerbs._get uses YAML instead, so it builds its ToolResult directly.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### `core/src/ufo/object_scope.py`

`domain_logic` · `request handling`

Some parts of the system need to know “which agent is responsible for this action?” Usually that answer comes from the current agent stored elsewhere. But object dispatch can narrow the work to one exact agent namespace, and audited object handlers need to report that more specific identity. This file provides that temporary override.

Think of it like putting a sticky note on a task: “for this part only, treat Alice as the responsible agent.” The sticky note is task-local, meaning it belongs to the current flow of work and does not leak into unrelated tasks running at the same time.

The `ObjectAgent` data class is the small label on that sticky note: it stores the agent’s unique ID and human-readable name. The private `_target` context variable holds the current object-specific target, if one has been set.

The `object_agent` context manager sets this target for a block of code and then always restores the previous value afterward, even if something goes wrong. The `object_agent_id` function is the read side: it returns the object-specific agent ID when one is active, otherwise it falls back to the normal current agent. Without this file, audited object handlers could accidentally attribute work to the broader current agent instead of the exact object-selected agent.

#### Function details

##### `object_agent`  (lines 24–32)

```
def object_agent(target: ObjectAgent | None) -> Iterator[None]
```

**Purpose**: This function temporarily marks a block of work as belonging to a particular object-selected agent. Someone uses it when object dispatch has chosen a specific agent and the code inside the block should be audited under that agent.

**Data flow**: It receives either an `ObjectAgent` label or `None`. If it gets `None`, it simply runs the wrapped block without changing anything. If it gets an agent label, it stores that label in a task-local slot before the block runs, then restores the previous value afterward, so the change does not leak beyond the block.

**Call relations**: Object dispatch code can wrap audited object-handler work in `object_agent` when it wants a precise agent target. Later, code inside that wrapped work can call `object_agent_id` and see the temporary target that this function placed in the current task context.


##### `object_agent_id`  (lines 35–37)

```
def object_agent_id() -> UUID
```

**Purpose**: This function answers the question: “Which agent ID should this object-related action be credited to?” It returns the temporary object-selected agent ID if one exists, or the ordinary current agent ID otherwise.

**Data flow**: It reads the task-local object agent target. If that target is present, it returns the target’s UUID. If no target has been set, it asks `ufo.agent_scope.agent_current` for the normal current agent and returns that agent’s ID.

**Call relations**: Audited object code calls `object_agent_id` when it needs the right identity for logging, authorization, or attribution. It depends on `object_agent` to have set a temporary target when object dispatch selected one; if no such target was set, it hands off to `ufo.agent_scope.agent_current` to use the wider agent context.

*Call graph*: 1 external calls (agent_current).


### Agent provisioning
This file initializes extension-provided agents into a workspace without overwriting owner-customized definitions.

### `core/src/ufo/provisioning.py`

`domain_logic` · `workspace provisioning, often during request handling or first use of extension agents`

Extensions can come with ready-made agents. This file is the bridge between those packaged agent recipes and the workspace’s real agent table in the database. The important rule is: the extension gets to suggest an agent, but the workspace owns the copy after it is created.

When provisioning runs, it walks through every active extension manifest and every agent that manifest declares. For each one, it first checks whether this exact extension has already placed this exact declared agent in the workspace. If so, it leaves it alone. This matters because a later extension version should not silently rewrite an agent that a member may have edited.

If no shipped copy exists, it checks for a special case: maybe the workspace already has an ordinary, user-owned agent with the same name and the exact same settings. In that case, it “adopts” that row by marking where it came from instead of creating a duplicate.

If the name is already taken by something different, it finds a safe alternate name, like adding the extension name as a suffix. This is like putting a label on two boxes that would otherwise have the same name. Finally, it inserts the new agent row. The insert is deliberately tolerant of races, so two requests trying to provision at once do not crash the user’s turn.

#### Function details

##### `AgentProvisioning.apply`  (lines 47–55)

```
async def apply(self, workspace_id: UUID) -> tuple[ProvisionOutcome, ...]
```

**Purpose**: Applies all agent provisions from all active extension manifests to one workspace. Someone uses this when they want a workspace to receive any agents that installed extensions ship with, without disturbing existing workspace-owned configuration.

**Data flow**: It receives a workspace ID and reads the manifests stored on the AgentProvisioning object. It enters the workspace context, then sends each manifest-and-agent pair to AgentProvisioning._one. It returns a tuple of ProvisionOutcome records saying, for each shipped agent, whether it was already present, adopted, or newly created.

**Call relations**: This is the public starting point for this file’s work. It sets the workspace context through ufo.workspace.ws, then repeatedly delegates the detailed decision-making to AgentProvisioning._one.

*Call graph*: calls 1 internal fn (_one); 1 external calls (ws).


##### `AgentProvisioning._one`  (lines 57–107)

```
async def _one(self, workspace_id: UUID, manifest: Manifest, provision: AgentProvision) -> ProvisionOutcome
```

**Purpose**: Processes one declared agent from one extension for one workspace. It decides whether that agent is already there, can be adopted from an identical user-created row, or must be created under a safe name.

**Data flow**: It receives the workspace ID, an extension manifest, and one agent provision. Inside a workspace database transaction, it first looks for an existing row marked as provisioned by the same extension under the same declared name. If found, it returns a PRESENT outcome. If not, it looks for an unprovisioned agent with the declared name and compares its settings to the provision. If they match, it updates that row with provisioning metadata and returns ADOPTED. Otherwise, it asks for a free name, creates a new row, and returns CREATED with the name that was actually used.

**Call relations**: AgentProvisioning.apply calls this once for every shipped agent. This function is the main decision point: it uses AgentProvisioning._identical to decide whether adoption is safe, AgentProvisioning._free_name to avoid name collisions, and AgentProvisioning._create to write a new agent when needed.

*Call graph*: calls 3 internal fn (_create, _free_name, _identical); called by 1 (apply); 4 external calls (__init__, select, update, workspace_tx).


##### `AgentProvisioning._free_name`  (lines 109–131)

```
async def _free_name(self, connection: AsyncConnection, workspace_id: UUID, extension: str, declared: str) -> str
```

**Purpose**: Finds a name that is not already used by any agent in the workspace. It lets existing agents keep their names and gives the shipped agent a clear alternate name if there is a conflict.

**Data flow**: It receives an open database connection, the workspace ID, the extension name, and the agent name declared by the extension. It reads all current agent names in that workspace, then tries the declared name first, then a version with the extension name added, then numbered versions. It returns the first unused name. If it cannot find one within the limit, it raises an error.

**Call relations**: AgentProvisioning._one calls this only when it needs to create a provisioned agent and the original name may not be safe. It does not write anything itself; it simply chooses the name that AgentProvisioning._create will use.

*Call graph*: called by 1 (_one); 2 external calls (execute, select).


##### `AgentProvisioning._create`  (lines 133–173)

```
async def _create(self, connection: AsyncConnection, workspace_id: UUID, manifest: Manifest, provision: AgentProvision, name: str) -> None
```

**Purpose**: Creates the actual agent database row for a shipped extension agent. It records the agent’s starting settings and where it came from, but does not grant extra powers beyond what the workspace later allows.

**Data flow**: It receives an open database connection, the workspace ID, the manifest, the agent provision, and the final chosen name. It builds an insert using the provision’s prompt, model, reasoning settings, internet access flag, sandbox size, tools, setup data, and extension metadata. It writes a new row with a fresh UUID and timestamps. If another concurrent process already inserted the same provisioned agent, the database is told to do nothing instead of failing.

**Call relations**: AgentProvisioning._one calls this after deciding a new row is needed and after AgentProvisioning._free_name has chosen the name. This function is the final write step, and its conflict-tolerant insert is what keeps simultaneous provisioning attempts from breaking a user request.

*Call graph*: called by 1 (_one); 2 external calls (execute, uuid4).


##### `AgentProvisioning._identical`  (lines 175–187)

```
def _identical(self, row: sa.Row, provision: AgentProvision) -> bool
```

**Purpose**: Checks whether an existing unprovisioned agent has the same meaningful settings as the agent an extension wants to ship. This allows the system to adopt that row instead of creating a duplicate.

**Data flow**: It receives a database row and an agent provision. It compares the row’s prompt, model, reasoning setting, internet access permission, sandbox size, and tools with the provision’s specification. It returns true only if all of those values match.

**Call relations**: AgentProvisioning._one calls this after finding an ordinary workspace agent with the same declared name. If it returns true, AgentProvisioning._one updates that existing row as adopted; if it returns false, AgentProvisioning._one looks for a free name and creates a separate shipped agent.

*Call graph*: called by 1 (_one).

## 📊 State Registers Touched

- `reg-db-session` — The active database connection, transaction, and workspace-safe persistence context used while work is running.
- `reg-workspace-roster` — The saved list of workspaces, members, admins, seats, and membership rules.
- `reg-agent-definitions` — The saved assistant agents for each workspace, including their settings, tools, model choices, and provisioning source.
- `reg-identity-context` — The current answer to who is acting, in which workspace, and on behalf of which member or agent.
- `reg-connection-grants` — The saved outside-service account connections and the grants saying which agents may use them.
- `reg-conversation-state` — The durable conversation records, titles, audience, surface labels, sandbox links, and visible thread metadata.
- `reg-transcript-state` — The saved message history and transcript snapshots that are read, compacted, updated, audited, and shown later.
- `reg-object-store` — The durable named workspace objects owned by extensions, with their names, data, permissions, and owner routing.
- `reg-surface-ingress` — The shared records that connect external surfaces like web, Slack, shell, OAuth, and inbound messages to conversations and replies.
- `reg-visibility-policy` — The shared audience, sharing, governance, and permission rules that decide who may see or change private data.
