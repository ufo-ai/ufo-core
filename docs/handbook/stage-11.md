# Tool catalog, dispatch, and built-in work actions  `stage-11`

This stage is the system’s tool room during the main work loop. When the AI asks to use a tool, the registry checks that the tool exists and that its input has the right shape. The context then gives the tool only the powers it is allowed to use, like workspace access, account choices, cleanup hooks, or permission checks. The built-in tools use that context to run safe shell commands, edit files, share files, ask the user questions, load skills, connect accounts, or hand work to subagents.

Several tool families plug into this same catalog. Connector tools act as guarded adapters to outside services and apps. Research and memory tools look up web pages, papers, search results, or stored knowledge. Document tools inspect, repair, comment on, and export office files and PDFs. The todo extension keeps a per-conversation checklist so longer tasks can continue across turns. The object system lets extensions expose named workspace records in a controlled way, while conversations are exposed only as read-only metadata. Together these parts turn a model’s request into a checked, limited, recorded action.

## Sub-stages

- [External connector and brokered app tools](stage-11.1.md) `stage-11.1` — 18 files
- [Research, search, recall, and knowledge lookup tools](stage-11.2.md) `stage-11.2` — 4 files
- [Document, office, PDF, and artifact automation tools](stage-11.3.md) `stage-11.3` — 23 files

## Files in this stage

### Todo and object extensions
Extension-facing workspace records let agents manage persistent todo checklists and expose conversations as safe read-only objects through the shared object system.

### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `extension registration and request handling`

This file solves a simple but important problem: when an agent is doing several steps, both the user and the agent need a visible progress board. Without this file, the agent would have no durable checklist tool for saying what it plans to do, what is currently being worked on, and what is finished.

The file defines two tools. The first, `update_todo_list`, creates or replaces the whole checklist. It is meant to be used at the start of complex work. The second, `update_todo_status`, changes the status of one or more existing tasks, such as moving a task from `pending` to `in_progress` or `completed`.

The checklist is stored in the extension’s own store, keyed by the conversation ID. In plain terms, each conversation gets its own little clipboard. The agent does not have to keep the list only in its chat memory; the extension can read it back later.

The file also defines the shapes of the data using Pydantic models, which are validation classes that make sure the tool inputs and saved board have the expected fields. The `manifest` function announces the extension’s name, tools, input formats, and prompt text so the larger system knows how to offer these tools to the agent.

#### Function details

##### `_require_ext`  (lines 82–85)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a tool call has access to the todos extension context. The extension context is the part that gives the tool its private storage area, so the todo tools cannot work without it.

**Data flow**: It receives the current tool context. If the context contains an extension object, it returns that object. If not, it stops immediately with an error, because there would be nowhere reliable to save or read the checklist.

**Call relations**: Both `update_todo_list` and `update_todo_status` call this first because they need the extension store. It acts like checking that you have the right notebook before trying to write or edit the todo list.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 88–89)

```
def _board_key(ctx: ToolContext) -> str
```

**Purpose**: This helper builds the storage key used to save the todo board for the current conversation. It keeps different conversations from overwriting each other’s checklists.

**Data flow**: It receives the tool context, reads the current conversation ID from the turn information, and combines it with the `todo/` prefix. The result is a string key that points to this conversation’s saved todo board.

**Call relations**: `update_todo_list` uses this key when writing a new board, and `update_todo_status` uses it when reading and writing an existing board. It is the shared address system that lets both tools find the same checklist.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_result`  (lines 92–93)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This helper turns the current todo board into the standard tool response returned to the agent. Returning the full board lets the model immediately see the latest checklist state after every change.

**Data flow**: It receives a `TodoBoard`, converts it to JSON text, wraps that text in a text content object, and then wraps that in a tool result. The output is the formatted response expected by the tool system.

**Call relations**: Both todo tools call this after they have created or updated the board. It hands the finished board back to the larger tool framework in the format that framework expects.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 96–98)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This helper reads a saved todo board from the extension store. It returns either a validated board or nothing if no board has been created yet.

**Data flow**: It receives the extension context and a storage key. It asks the extension store for the saved data at that key. If nothing is found, it returns `None`; otherwise, it validates the stored data as a `TodoBoard` and returns that board.

**Call relations**: `update_todo_status` calls this before applying status changes. That status tool needs to start from the current saved checklist, rather than inventing a new one.

*Call graph*: called by 1 (update_todo_status).


##### `update_todo_list`  (lines 101–105)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates or replaces the complete todo checklist for a conversation. It is used when the agent starts or revises a multi-step plan.

**Data flow**: It receives the tool context and the requested list details: a title, the full set of tasks, and a short user-facing description. It checks that extension storage is available, builds a `TodoBoard`, saves that board under the conversation-specific key, and returns the board as JSON text in a tool result. Because the task list replaces the existing list entirely, the saved board after the call matches the new input.

**Call relations**: When the agent needs a checklist, the tool framework calls this function. It relies on `_require_ext` for storage access, `_board_key` to choose the right conversation slot, and `_board_result` to return the final board to the agent and user interface.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 108–119)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that updates the status of existing todo items. It is used while work is underway, so the checklist can show what is pending, in progress, or completed.

**Data flow**: It receives the tool context and one or more status updates. It checks that extension storage is available, finds the saved board for the current conversation, and refuses to continue if no list exists yet. For each update, it checks that the given task number is valid, converts the user-facing 1-based number to the internal list position, changes that task’s status, saves the whole updated board back to storage, and returns the current board.

**Call relations**: The tool framework calls this whenever the agent reports progress on checklist items. It depends on `_read_board` to load the existing list, `_board_key` to locate the right conversation’s board, `_require_ext` to ensure storage exists, and `_board_result` to return the updated checklist.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `manifest`  (lines 122–141)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the todos extension to the larger system. It names the extension, lists the tools it provides, explains their inputs, and includes prompt text that teaches the agent how to use the checklist.

**Data flow**: It takes no input. It builds a manifest object containing the extension name and version, two tool definitions, and one prompt section loaded from the nearby markdown file. The result is a complete registration record for this extension.

**Call relations**: The host system calls this during extension discovery or startup so it can learn that `update_todo_list` and `update_todo_status` are available. The manifest connects the human-facing tool descriptions, input validation models, and actual handler functions into one package.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `core/src/ufo/conversations.py`

`domain_logic` · `request handling`

A conversation can be the place where an artifact was created or where a scheduled task reports back. This file gives the object system a safe way to resolve those links. It is like a directory card for a meeting: it tells you which room the meeting is in, who it belongs to if it is private, and when it was created, but it does not reveal what was said inside.

The main rule is visibility. Shared conversations, with no specific member attached, can be seen by anyone in the workspace. Private conversations can only be seen by the matching audience member. The file builds database queries that enforce that rule before returning anything.

The file defines `ConversationSpec`, the small public shape of a conversation: its surface, such as the chat area it lives on, and an optional member ID. `ConversationObjects` is the read-only store used by the object system. It can list visible conversations, fetch one visible conversation by its ID, and report no separate status. It deliberately rejects apply and delete operations because conversations are created by chat surfaces and removed by retention cleanup, not authored through this object interface.

At the bottom, `CONVERSATION_OBJECT` registers this behavior as the core `conversation` object kind, including guidance that transcripts are not exposed.

#### Function details

##### `ConversationObjects.list`  (lines 50–59)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows a page of conversations the caller is allowed to see. Each item is only a summary: the conversation ID, the chat surface, and the creation date.

**Data flow**: It receives the caller context and a list query with paging or filtering choices. It asks for all database rows visible to that caller, turns each row into a simple object-list entry, and then passes those entries through the object paging helper. The result is an `ObjectPage` containing only safe, visible conversation summaries.

**Call relations**: This is the list path for the registered conversation object. It relies on `_visible_rows` to do the privacy-aware database read, then hands the formatted rows to the shared object paging helper so conversation listing behaves like other object kinds.

*Call graph*: calls 1 internal fn (_visible_rows); 2 external calls (__init__, object_page).


##### `ConversationObjects.get`  (lines 61–72)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None
```

**Purpose**: Fetches one conversation by its name, where the name is expected to be the conversation's UUID-style ID. It returns only the public metadata if the conversation exists and is visible to the caller.

**Data flow**: It receives the caller context and a conversation name string. It asks `_find` to parse the name and look up a matching visible row. If no row is found, it returns `None`; otherwise it builds a `ConversationSpec` with the surface and optional member ID, wraps it with creation and update timestamps, and returns that detail object.

**Call relations**: This is the detail path for conversation links. When another object points to a conversation, this method is what can resolve that pointer into safe metadata, while `_find` performs the actual permission-filtered lookup.

*Call graph*: calls 1 internal fn (_find); 2 external calls (__init__, __init__).


##### `ConversationObjects.status`  (lines 74–75)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Reports that conversations have no separate live status through this object interface. The metadata returned by `get` is all this object kind exposes.

**Data flow**: It receives the caller context and conversation name, but does not read the database or inspect the name. It always returns `None`, meaning there is no additional status document.

**Call relations**: This fits the standard object-store shape, where object kinds may offer a status view. For conversations, the file intentionally leaves that view empty so callers do not expect transcript or runtime state here.


##### `ConversationObjects.apply`  (lines 77–80)

```
async def apply(self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None) -> None
```

**Purpose**: Rejects attempts to create or update a conversation through the object system. This protects the rule that conversations are made by chat surfaces, not by general object writes.

**Data flow**: It receives the caller context, target name, desired conversation spec, and possibly the old spec. Instead of saving anything, it raises a `VerbNotSupported` error with a message explaining that conversations are surface-made.

**Call relations**: This is called when the object framework tries to apply a desired state. Rather than handing off to storage, it stops the flow immediately so no caller can author or edit conversation rows through this route.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects.delete`  (lines 82–83)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Rejects attempts to delete a conversation through the object system. Conversation cleanup is left to retention rules, not manual object deletion.

**Data flow**: It receives the caller context and target name. It does not look up or change any row; it raises a `VerbNotSupported` error with the standard explanation.

**Call relations**: This is the delete path for the object framework. It deliberately mirrors `apply`: any mutation request is refused so this file stays a read-only link resolver.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects._find`  (lines 85–95)

```
async def _find(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Looks up one visible conversation row by ID. It also quietly rejects names that are not valid UUIDs, because conversation names are stored as UUID-style identifiers.

**Data flow**: It receives the caller context and a name string. First it tries to turn the name into a UUID; if that fails, it returns `None`. If parsing succeeds, it opens a workspace database transaction, builds the standard visibility-filtered query, adds a condition for the specific conversation ID, and returns either one matching row or `None`.

**Call relations**: `get` uses this helper so the public detail method does not have to repeat ID parsing or database access. `_find` in turn uses `_visible` to make sure the same privacy rule is applied for single-item lookup as for listing.

*Call graph*: calls 1 internal fn (_visible); called by 1 (get); 2 external calls (workspace_tx, UUID).


##### `ConversationObjects._visible_rows`  (lines 97–100)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Reads all conversation rows that the caller is allowed to see in the current workspace. It is the database-reading helper behind the public list operation.

**Data flow**: It receives the caller context. It opens a workspace database transaction, runs the visibility-filtered select query from `_visible`, collects all returned rows, and turns them into an immutable tuple before returning them.

**Call relations**: `list` calls this before formatting conversations for display. By relying on `_visible`, it shares the same workspace and member-privacy rules used by single conversation lookup.

*Call graph*: calls 1 internal fn (_visible); called by 1 (list); 1 external calls (workspace_tx).


##### `ConversationObjects._visible`  (lines 102–121)

```
def _visible(self, ctx: ToolContext) -> sa.Select
```

**Purpose**: Builds the database query that defines which conversations are visible to the caller. This is the central privacy gate for this file.

**Data flow**: It receives the caller context and reads the caller's audience member ID, if any, plus the current workspace ID. It creates a database select for conversation ID, surface, member ID, and timestamps. The query is limited to the current workspace and to rows that are shared, or, when there is a caller member ID, rows belonging to that member.

**Call relations**: Both `_find` and `_visible_rows` call this helper before touching conversation data. That means every public read path goes through the same rule: shared conversations are visible broadly, while member-bound conversations are only visible to that member.

*Call graph*: called by 2 (_find, _visible_rows); 3 external calls (or_, select, ws_current).


### `core/src/ufo/objects.py`

`domain_logic` · `startup for object-kind registration; request handling for object tools`

A workspace object is a durable item addressed by a kind and a name, like `calendar/reminders` or `note/project-plan`. This file gives the project one common way to work with those objects instead of every extension inventing its own rules. Without it, object names, YAML shape, permissions, paging, and tool responses could drift apart and become unsafe or confusing.

The file has three main jobs. First, it defines the basic shapes: object references, links between objects, list rows, pages, specs, and store interfaces. A store is the extension-owned code that actually reads and writes its own tables. Second, it validates registrations at startup. Each object kind must have a unique kind name, a strict Pydantic spec model, JSON-friendly fields, and no secret fields, because specs are stored and shown back to users. Third, it exposes five tools through `ObjectVerbs`: list, get, explain, apply, and delete.

A useful analogy is a building front desk. The front desk checks that the visitor wrote the right room number and filled out the form correctly, then sends them to the right office. The office still decides the real domain action. This file is that front desk: strict about shared rules, but it lets each object kind decide what creation, updates, deletion, and refusal mean.

#### Function details

##### `ObjectRef.validate_kind`  (lines 67–70)

```
def validate_kind(cls, value: str) -> str
```

**Purpose**: Checks that an object reference uses a valid kind name. This prevents references such as `Bad Kind` or names with punctuation from entering the system.

**Data flow**: It receives the proposed kind string, compares it with the allowed pattern, and either returns the same string unchanged or raises an error explaining the rule.

**Call relations**: Pydantic calls this automatically when an `ObjectRef` is built. It protects every later use of the reference, including links, search hits, and object lookups.


##### `ObjectRef.validate_name`  (lines 74–80)

```
def validate_name(cls, value: str) -> str
```

**Purpose**: Checks that an object reference uses a valid object name. The name must be short enough and use the shared lowercase-and-dashes style.

**Data flow**: It receives the proposed name, checks both its length and its pattern, and returns it unchanged if valid. If not, it raises an error that includes the allowed rule.

**Call relations**: Pydantic calls this while constructing an `ObjectRef`. This keeps object links and references using the same name grammar that `object_apply` enforces for new objects.


##### `ObjectRef.__str__`  (lines 82–83)

```
def __str__(self) -> str
```

**Purpose**: Turns an object reference into the human-readable form `kind/name`. This is the compact address used in messages and displays.

**Data flow**: It reads the reference's `kind` and `name` fields and joins them with a slash. The result is a plain string.

**Call relations**: It is used whenever code or logs need a simple text version of an object reference rather than the structured model.


##### `_ObjectCursor.validate_rank`  (lines 180–185)

```
def validate_rank(self) -> '_ObjectCursor'
```

**Purpose**: Checks that a paging cursor's stored value matches the type category it claims to have. This stops broken or tampered cursor tokens from producing confusing list results.

**Data flow**: It reads the cursor's rank and value. If the pair makes sense, such as numeric rank with a number, it returns the cursor; otherwise it raises an error.

**Call relations**: Pydantic calls this when `object_page` decodes a cursor from a previous listing. It helps `object_page` safely continue from the correct place.


##### `object_page`  (lines 188–267)

```
def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Applies the shared list behavior for one object kind: searching, exact filters, sorting, and paging. Store implementations can hand it lightweight rows and get consistent list results.

**Data flow**: It receives rows plus a query. It rejects invalid fields, filters rows by search text and exact filter values, sorts them with `_sortable`, trims the result to the page size, and returns an `ObjectPage` with rows and possibly a next cursor.

**Call relations**: Member-owned object stores call it from `MemberOwnedObjects.list` after visibility has already been checked. It calls `_sortable` to make different field types sort predictably and creates `_ObjectCursor` values for follow-up pages.

*Call graph*: calls 1 internal fn (_sortable); called by 1 (list); 2 external calls (__init__, __init__).


##### `object_page.value`  (lines 212–217)

```
def value(row: ObjectRow, name: str) -> JsonValue
```

**Purpose**: Looks up the value of one sortable or filterable field from a list row. It hides the difference between built-in fields and custom row fields.

**Data flow**: It receives a row and a field name. For `name` and `summary` it returns the row's direct values; otherwise it reads the value from the row's extra fields.

**Call relations**: This helper lives inside `object_page` and is used while searching, filtering, ordering, and building the paging cursor.


##### `_sortable`  (lines 270–283)

```
def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]
```

**Purpose**: Converts a list field value into a form that can be sorted consistently. It allows only simple scalar values, such as strings and numbers, because complex objects do not have an obvious order.

**Data flow**: It receives a JSON-like value and the field name being sorted. It returns a rank plus a comparable value, or raises an error if the value is a list, object, or another non-sortable shape.

**Call relations**: Only `object_page` calls it. It is the small rulebook that lets `object_page` sort names, summaries, booleans, numbers, strings, and missing values in a stable way.

*Call graph*: called by 1 (object_page).


##### `ObjectStore.list`  (lines 294–294)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the required list operation for every object kind's storage layer. A concrete store uses it to return a page of lightweight object rows.

**Data flow**: It receives a tool context and an `ObjectListQuery`, then should read that kind's storage and return an `ObjectPage`. The protocol itself does not implement the work; it states the contract.

**Call relations**: `ObjectVerbs._list` calls this on the resolved kind's store. Implementations may use helpers such as `object_page` to follow the shared listing rules.


##### `ObjectStore.get`  (lines 296–296)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines the required read operation for one object instance. A concrete store uses it to return the saved spec, timestamps, and links, or report that the object is missing.

**Data flow**: It receives a tool context and object name. The expected output is an `ObjectDetail` or `None` if no visible matching object exists.

**Call relations**: `ObjectVerbs._get`, `_apply`, and `_delete` call this before reading, updating, or deleting. Store implementations decide how to retrieve the row from their own tables.


##### `ObjectStore.status`  (lines 298–298)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Defines how an object kind can report live state beside the stored spec. This is for changing information such as last sync time or next scheduled run.

**Data flow**: It receives a tool context and object name, then should return a JSON-like dictionary of status values or `None`. It does not change the stored object.

**Call relations**: `ObjectVerbs._get` calls it after fetching the object detail so the tool response can include both the durable spec and current state.


##### `ObjectStore.apply`  (lines 300–300)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None) -> None
```

**Purpose**: Defines the create-or-update operation for an object kind. The concrete store receives a spec that core has already validated.

**Data flow**: It receives a context, object name, new spec, and the old spec if the object already existed. It performs the kind-specific write or raises a clear refusal/error.

**Call relations**: `ObjectVerbs._apply` calls this after parsing YAML, validating the name, validating the spec, and reading any existing object.


##### `ObjectStore.delete`  (lines 302–302)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Defines the delete operation for an object kind. The concrete store removes or deactivates the named object according to that kind's rules.

**Data flow**: It receives a context and object name. It changes storage as appropriate and returns no payload, or raises an error if deletion is not allowed.

**Call relations**: `ObjectVerbs._delete` calls this only after confirming the object exists and saving its old spec for the response.


##### `MemberOwnedObjects.list`  (lines 343–351)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists only the member-owned objects that the current actor is allowed to see. It keeps private rows from appearing in list results.

**Data flow**: It reads whether the speaker is a workspace owner and who the acting member is. It asks `_owned_rows` for all rows, keeps only rows allowed by `_visible`, converts them to simple list rows, and passes them to `object_page`.

**Call relations**: Concrete member-owned stores inherit this method instead of rewriting visibility checks. It calls subclass data via `_owned_rows`, uses `_visible` for the gate, and delegates search/sort/page behavior to `object_page`.

*Call graph*: calls 4 internal fn (_owned_rows, _visible, object_page, speaker_is_owner); 1 external calls (__init__).


##### `MemberOwnedObjects.get`  (lines 353–359)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Reads one member-owned object only if it is visible to the current actor. Invisible objects look the same as missing objects.

**Data flow**: It looks up the object's owner with `_owner`, checks visibility using the acting member and owner status, and returns `None` if the actor should not see it. If visible, it asks `_detail` for the full object detail.

**Call relations**: `ObjectVerbs._get` reaches this through a concrete store. The method calls subclass code only after the shared permission check has passed.

*Call graph*: calls 4 internal fn (_detail, _owner, _visible, speaker_is_owner).


##### `MemberOwnedObjects.status`  (lines 361–367)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Reads live status for a member-owned object only if the current actor may see that object. It applies the same privacy rule as `get`.

**Data flow**: It finds the owner, checks whether the actor can see the row, and returns `None` when missing or hidden. If visible, it asks `_status` for the kind-specific status values.

**Call relations**: `ObjectVerbs._get` can reach this through the store's `status` method. It uses `_owner` and `_visible` before handing off to subclass-specific `_status` code.

*Call graph*: calls 4 internal fn (_owner, _status, _visible, speaker_is_owner).


##### `MemberOwnedObjects.apply`  (lines 369–379)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None) -> None
```

**Purpose**: Creates or updates a member-owned object while enforcing who may change an existing row. It lets owners edit their own rows, workspace owners edit visible rows, and blocks everyone else.

**Data flow**: It looks up any current owner. If the row exists, it checks visibility, ownership, workspace-owner status, and optional live-speaker requirements. If the checks pass, it calls `_apply_owned` with the new spec, old spec, and owner information.

**Call relations**: `ObjectVerbs._apply` reaches this after core validation. This method performs the common access gate, then hands the actual mutation to the subclass through `_apply_owned`.

*Call graph*: calls 5 internal fn (_apply_owned, _owned, _owner, _visible, speaker_is_owner); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects.delete`  (lines 381–392)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Deletes a member-owned object only when the current actor is allowed to remove it. Hidden rows are reported as not found, while visible-but-not-owned rows are refused as owner-required.

**Data flow**: It finds the owner, rejects missing or invisible rows, checks whether the actor owns the row or is the workspace owner, checks any live-speaker requirement, and then calls `_delete_owned`.

**Call relations**: `ObjectVerbs._delete` reaches this through a concrete store. It uses `_owner`, `_visible`, and `_owned` for the shared gate before delegating the deletion itself.

*Call graph*: calls 5 internal fn (_delete_owned, _owned, _owner, _visible, speaker_is_owner); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects._owned`  (lines 394–398)

```
def _owned(self, owner: ObjectOwner, acting: UUID | None) -> bool
```

**Purpose**: Answers whether the acting member is the member-owner of a row. Owner-only rows, which have no member id, are never counted as member-owned.

**Data flow**: It receives an `ObjectOwner` and the acting member id. It returns true only when the row has a member id and that id equals the acting member id.

**Call relations**: `_visible`, `apply`, and `delete` call this as part of access decisions. Workspace-owner authority is checked separately, so this function stays focused on member ownership.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 400–401)

```
def _visible(self, owner: ObjectOwner, acting: UUID | None, is_owner: bool) -> bool
```

**Purpose**: Decides whether a row should be visible to the current actor. A row is visible if it is shared, owned by the acting member, or the speaker is a workspace owner.

**Data flow**: It receives the row owner, acting member id, and a boolean saying whether the speaker is a workspace owner. It combines those facts and returns true or false.

**Call relations**: `list`, `get`, `status`, `apply`, and `delete` call this before showing or changing member-owned rows. It calls `_owned` for the member-owner part of the rule.

*Call graph*: calls 1 internal fn (_owned); called by 5 (apply, delete, get, list, status).


##### `MemberOwnedObjects._owner`  (lines 403–404)

```
async def _owner(self, ctx: ToolContext, name: str) -> ObjectOwner | None
```

**Purpose**: Finds the ownership record for one named member-owned object. This lets the shared gate make a decision before fetching full details or changing storage.

**Data flow**: It asks `_owned_rows` for the available rows, searches for the requested name, and returns that row's `ObjectOwner` or `None` if there is no match.

**Call relations**: `get`, `status`, `apply`, and `delete` call this. The actual row list comes from the subclass implementation of `_owned_rows`.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 406–407)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]
```

**Purpose**: Placeholder method that subclasses must implement to provide the names, summaries, and owners of their rows. The base class needs this data to enforce visibility.

**Data flow**: It receives a tool context and is expected to return a tuple of `OwnedRow` values. In the base class it raises `NotImplementedError`, meaning concrete stores must supply it.

**Call relations**: `MemberOwnedObjects.list` and `_owner` call this. Subclasses provide the storage-specific read while the base class provides the permission logic.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._detail`  (lines 409–410)

```
async def _detail(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Placeholder method that subclasses must implement to fetch full object details after access has been approved.

**Data flow**: It receives a context and object name and should return an `ObjectDetail` or `None`. The base class raises `NotImplementedError` because it does not know the subclass's storage.

**Call relations**: `MemberOwnedObjects.get` calls this only after `_visible` says the actor may see the object.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 412–413)

```
async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Placeholder method that subclasses must implement to fetch live status for an approved object.

**Data flow**: It receives a context and object name and should return a JSON-like status dictionary or `None`. The base class raises `NotImplementedError` until a concrete kind supplies the behavior.

**Call relations**: `MemberOwnedObjects.status` calls this after the common owner and visibility checks pass.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 415–423)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Placeholder method that subclasses must implement to actually create or update a member-owned object. The shared base class performs the gate; this method performs the domain write.

**Data flow**: It receives the context, name, validated spec, old spec, and owner information. A concrete implementation writes to storage or raises a domain-specific error; the base version raises `NotImplementedError`.

**Call relations**: `MemberOwnedObjects.apply` calls this after checking visibility, ownership, and any live-speaker requirement.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 425–426)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Placeholder method that subclasses must implement to actually delete a member-owned object. It runs only after the shared delete permission checks pass.

**Data flow**: It receives the context, object name, and owner record. A concrete implementation removes the row or performs the kind-specific delete action; the base version raises `NotImplementedError`.

**Call relations**: `MemberOwnedObjects.delete` calls this after confirming the row exists, is visible, and may be deleted by the actor.

*Call graph*: called by 1 (delete).


##### `object_registry`  (lines 456–473)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: Builds the lookup table of registered object kinds for one deployment and rejects unsafe or conflicting registrations. This is the startup gate for the object system.

**Data flow**: It receives bound object kinds from core and extensions. It checks each kind name, detects duplicates, validates the spec model with `_validate_spec_model`, and returns a dictionary keyed by kind name.

**Call relations**: Startup code uses this before serving tools. It calls `_validate_spec_model` so bad extensions fail early rather than causing unsafe behavior during a user request.

*Call graph*: calls 1 internal fn (_validate_spec_model).


##### `_validate_spec_model`  (lines 476–499)

```
def _validate_spec_model(owner: str, kind: ObjectKind) -> None
```

**Purpose**: Checks that an object kind's spec model is safe to store and show back to users. It blocks unknown fields, undeclared list fields, secret-bearing fields, and values that cannot be represented as JSON.

**Data flow**: It receives the registering owner name and object kind. It compares list fields with model fields, walks nested models, checks Pydantic configuration and annotations, and asks Pydantic to produce a JSON schema. It raises a clear error if any rule fails.

**Call relations**: `object_registry` calls this for every kind. It uses `_reachable_models` and `_annotation_types` to inspect nested Pydantic models, not just the top-level spec.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 1 (object_registry).


##### `_reachable_models`  (lines 502–516)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: Finds all Pydantic models nested inside a spec model. This matters because safety rules must apply to nested structures too.

**Data flow**: It starts with one model, follows field type annotations to discover nested Pydantic models, avoids repeats, and returns the full set it found.

**Call relations**: `_validate_spec_model` calls this before checking strictness and secret fields. It relies on `_annotation_types` to unpack types such as lists, unions, or optional fields.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 519–526)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: Flattens a type annotation into the concrete pieces inside it. For example, it can look through container or union types so validators can inspect the real field types.

**Data flow**: It receives a type annotation, asks Python's typing system for its arguments, recursively expands any nested arguments, and returns a tuple of discovered pieces.

**Call relations**: `_validate_spec_model` and `_reachable_models` call this while inspecting spec fields. It calls `typing.get_args` to understand compound type hints.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectVerbs.tools`  (lines 564–627)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: Creates the five tool definitions exposed to the model: list, get, explain, apply, and delete. These definitions describe what each tool does, what input shape it expects, and whether it can change state.

**Data flow**: It reads the `ObjectVerbs` instance and returns a tuple of `ToolDef` objects. Each tool definition points to one private handler method on the same object.

**Call relations**: Tool registration code calls this to make object operations available. The returned handlers later call `_list`, `_get`, `_explain`, `_apply`, and `_delete` during requests.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 629–655)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: Implements the `object_list` tool. It either lists registered kinds or lists instances of one kind with search, filters, sorting, and paging.

**Data flow**: It receives the current tool context and list arguments. With no kind, it returns kind names and descriptions; with a kind, it resolves the kind, binds the context to its extension, builds an `ObjectListQuery`, calls the store's list method, and returns JSON.

**Call relations**: This is the handler installed by `ObjectVerbs.tools`. It uses `_resolve` to find the kind, `_bound_ctx` to run under the owning extension context, and `_json_result` to format the response.

*Call graph*: calls 3 internal fn (_bound_ctx, _resolve, _json_result); 1 external calls (__init__).


##### `ObjectVerbs._get`  (lines 657–672)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: Implements the `object_get` tool. It reads one object and returns its spec, live status, links, and timestamps.

**Data flow**: It receives the context plus kind and name. It resolves the kind, binds the context, asks the store for details, raises `UnknownObject` if missing, asks for status, converts the result into YAML text, and returns a tool result.

**Call relations**: This is the get handler installed by `ObjectVerbs.tools`. It depends on `_resolve` and `_bound_ctx`, then hands the actual read to the kind's store.

*Call graph*: calls 2 internal fn (_bound_ctx, _resolve); 4 external calls (__init__, __init__, __init__, safe_dump).


##### `ObjectVerbs._explain`  (lines 674–686)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: Implements the `object_explain` tool. It tells a caller how to author objects of a kind before they try to apply one.

**Data flow**: It receives a kind name, resolves it, and returns JSON containing the kind description, guidance, name rule, and generated JSON schema for the spec.

**Call relations**: This handler is installed by `ObjectVerbs.tools`. It calls `_resolve` and `_json_result`; it does not call the store because it explains the registered kind, not a stored instance.

*Call graph*: calls 2 internal fn (_resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 688–709)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: Implements the `object_apply` tool for creating or updating an object from a YAML manifest. It validates the shared envelope and the kind-specific spec before any store write happens.

**Data flow**: It receives manifest text, parses it with `_parse_envelope`, resolves the kind, validates the name, validates the spec model, reads any existing object, calls the store's apply method with the old spec if present, and returns whether the result was created or updated.

**Call relations**: This handler is installed by `ObjectVerbs.tools`. It uses `_parse_envelope`, `_validate_name`, `_resolve`, `_bound_ctx`, and `_json_result`, then delegates the actual mutation to the kind's store.

*Call graph*: calls 5 internal fn (_bound_ctx, _resolve, _json_result, _parse_envelope, _validate_name); 1 external calls (__init__).


##### `ObjectVerbs._delete`  (lines 711–725)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: Implements the `object_delete` tool. It deletes one object and echoes the deleted spec so the user can recreate it if deletion was accidental and the kind supports creation.

**Data flow**: It receives kind and name, resolves the kind, binds the context, reads the old object, raises `UnknownObject` if missing, calls the store's delete method, and returns JSON with the deleted spec.

**Call relations**: This handler is installed by `ObjectVerbs.tools`. It uses `_resolve`, `_bound_ctx`, and `_json_result`, and relies on the store for the actual delete operation.

*Call graph*: calls 3 internal fn (_bound_ctx, _resolve, _json_result); 1 external calls (__init__).


##### `ObjectVerbs._resolve`  (lines 727–732)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: Looks up an object kind in the registry and gives a helpful error if it is not registered. This turns a bare kind string into the full registered kind plus its owning context.

**Data flow**: It receives a kind name and reads the registry mapping. If found, it returns the `BoundKind`; if not, it raises `UnknownKind` with the list of available kinds.

**Call relations**: All five object handlers call this before doing kind-specific work. It is the common doorway from user-supplied kind names to registered object implementations.

*Call graph*: called by 5 (_apply, _delete, _explain, _get, _list); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 734–735)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: Rebinds the current tool context to the extension that owns the object kind. This ensures the store runs with the right extension-specific context.

**Data flow**: It receives the current `ToolContext` and a `BoundKind`. It returns a copied context whose extension context is replaced with the bound kind's context.

**Call relations**: `_list`, `_get`, `_apply`, and `_delete` call this before calling a store method. It lets shared object verbs dispatch safely into extension-owned storage code.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 1 external calls (replace).


##### `_parse_envelope`  (lines 738–756)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object]]
```

**Purpose**: Parses and checks the YAML manifest used by `object_apply`. It enforces the required top-level shape: exactly `kind`, `name`, and `spec`.

**Data flow**: It receives manifest text, rejects it if it is too large, parses YAML safely, checks that the result is a mapping with exactly the required keys, checks that kind and name are strings and spec is a mapping, then returns those three pieces.

**Call relations**: `ObjectVerbs._apply` calls this before resolving the kind or validating the spec. It uses YAML parsing and raises `InvalidManifest` for malformed input.

*Call graph*: called by 1 (_apply); 2 external calls (__init__, safe_load).


##### `_validate_name`  (lines 759–764)

```
def _validate_name(name: str) -> None
```

**Purpose**: Checks that a new or updated object's name follows the shared object-name rule. This keeps all kinds using the same address format.

**Data flow**: It receives a name string and checks length plus pattern. If the name is valid it returns nothing; otherwise it raises `InvalidName` with the rule.

**Call relations**: `ObjectVerbs._apply` calls this after parsing the manifest and before validating or writing the spec.

*Call graph*: called by 1 (_apply); 1 external calls (__init__).


##### `_json_result`  (lines 767–768)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: Wraps a plain mapping as a JSON tool response. It is the small common formatter for object tool handlers that return JSON.

**Data flow**: It receives a payload mapping, serializes it with `json.dumps`, places the text in a `TextContent`, and returns a `ToolResult` containing that text.

**Call relations**: `ObjectVerbs._list`, `_explain`, `_apply`, and `_delete` call this to format their responses consistently. `_get` uses YAML instead because it returns a fuller object document.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### Built-in tool handlers
The built-in tools bridge model requests to protected shell, file, sharing, question, skill, account connection, and subagent actions.

### `core/src/ufo/tools/builtins.py`

`orchestration` · `tool execution during request handling`

This file is like the agent’s toolbox catalog plus the instructions for using each tool safely. When the agent asks to read a file, run a command, share an artifact, or delegate work to a subagent, the tool system calls one of these handlers.

The most important rule here is safety around the workspace. File discovery, reading, editing, and searching happen inside the sandbox, which is an isolated container rather than the host machine. That means large files, PDFs, images, and searches are processed where the files live, and only bounded results come back. The file also remembers which paths have been read in the current turn, so edits and overwrites cannot blindly change a file the model has not seen.

For sharing files, it streams the file from the sandbox into blob storage and returns a temporary download link. That is the controlled doorway from private workspace files to the outside user.

The file also supports human interaction and delegation. It can ask the user for missing information, request secrets through a private channel, start OAuth account connection, load reusable skill instructions into the workspace, and coordinate background subagents. At the end, all these handlers are registered as `BUILTIN_TOOLS`, which tells the tool runtime their names, input shapes, descriptions, and whether they are safe to run in parallel.

#### Function details

##### `bash_handler`  (lines 275–285)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command inside the sandboxed workspace and returns what the command printed. It marks the tool result as an error if the command exits unsuccessfully.

**Data flow**: It receives the tool context and a command with an optional timeout. It caps the timeout to the allowed maximum, asks the sandbox to run the command, combines standard output and standard error, and returns that text. If the command failed, it adds the exit code and marks the result as an error.

**Call relations**: This is the handler behind the built-in `bash` tool. The tool runtime calls it when the agent chooses to run a command, and it hands the actual execution to the sandbox so the command cannot escape the protected workspace.

*Call graph*: 2 external calls (__init__, __init__).


##### `_require_str`  (lines 288–291)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Checks that a value returned by the sandbox is a real, non-empty string. It is used when building image or document results where missing fields would make the response invalid.

**Data flow**: It receives a value and the name of the field being checked. If the value is a non-empty string, it returns it unchanged. Otherwise it raises an error explaining that the sandbox response was missing that field.

**Call relations**: This is a small guard used by `read_handler` and `_pdf_result`. Those functions rely on it before creating image blocks, so malformed sandbox output fails clearly instead of producing confusing partial content.

*Call graph*: called by 2 (_pdf_result, read_handler).


##### `_pdf_result`  (lines 294–339)

```
def _pdf_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns the sandbox’s PDF or PowerPoint read result into model-readable content. It combines extracted text, page or slide progress notes, and rendered page images when available.

**Data flow**: It receives a dictionary from the sandbox describing a PDF or PPTX read. It gathers any text, page counts, notes, and rendered images, validates required image fields, then returns a tool result made of text and image blocks. If nothing usable is present, it raises an error.

**Call relations**: `read_handler` calls this when the sandbox says the file is a PDF or PPTX. `_pdf_result` uses `_require_str` to validate image data before wrapping it in content blocks for the model.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 342–379)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a workspace file through the sandbox and returns content in a form the model can use. It supports text files, images, PDFs, and PowerPoint files, with paging for large documents.

**Data flow**: It receives a file path and optional offset and limit. It asks the sandbox file tool to read that slice, records the path as having been read this turn, then formats the result as text, image content, or a document preview. For text files, it adds a footer showing which lines were returned and how to continue reading if more remains.

**Call relations**: This is the handler behind the `read` tool. It calls `_pdf_result` for paginated documents and `_require_str` for image fields. Its record of read paths is later used by `write_handler` and `edit_handler` to prevent blind file changes.

*Call graph*: calls 2 internal fn (_pdf_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 382–403)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Creates or overwrites a text file in the workspace, while protecting existing files from being changed before they have been read. It returns a short JSON summary of what was written.

**Data flow**: It receives a target path and text content. It checks whether the file already exists; if it does and the path has not been read this turn, it refuses to write. Otherwise it writes the bytes into the sandbox, marks the path as read, counts size and lines, and returns those facts.

**Call relations**: This is the handler behind the `write` tool. It depends on the read-path tracking set by `read_handler`, so the normal flow is: read an existing file first, then write or edit it.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `edit_handler`  (lines 406–414)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact string replacements to a file that has already been read in the current turn. This helps ensure the agent edits content it has actually inspected.

**Data flow**: It receives a file path and one or more requested replacements. It refuses to continue if the file path has not been read. Then it converts the edits into simple dictionaries, sends them to the sandbox file tool, and returns the sandbox’s JSON result.

**Call relations**: This is the handler behind the `edit` tool. It is meant to follow `read_handler`; after the model sees the file, this handler delegates the careful replacement work to the sandbox-side editor.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 417–423)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds files whose names match a pattern, such as `**/*.py`, inside the sandboxed workspace. It is the safer built-in alternative to using shell commands like `find` or `ls` for discovery.

**Data flow**: It receives a glob pattern and an optional starting directory. It asks the sandbox file tool to match paths from that directory, defaulting to the workspace root, then returns the matches as JSON text.

**Call relations**: This is the handler behind the `glob` tool. The tool runtime calls it for file discovery, and it keeps the directory traversal inside the sandbox so only the matched path list comes back.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 426–444)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches workspace file contents for a regular expression, meaning a text pattern with matching rules. It limits the number of results so broad searches do not flood the model.

**Data flow**: It receives the search pattern plus optional file filters, context lines, case handling, output mode, and result limit. It builds a sandbox search request, fills in a default result cap if none is provided, sends it to the sandbox file tool, and returns the matches as JSON text.

**Call relations**: This is the handler behind the `grep` tool. It delegates the heavy scanning to sandbox-side ripgrep, so the host does not pull every file across just to search it.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `share_file_handler`  (lines 447–524)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Makes a workspace file available to the user through a temporary download link. This is the controlled path for moving a produced file out of the sandbox.

**Data flow**: It receives a workspace file path, an optional download name, and an optional caption. It first checks that artifact sharing is configured, then runs a small sandbox preflight to compute file size, digest, and whether it looks like text. It chooses a safe filename, streams the file into blob storage, records the shared artifact in the database, creates a time-limited token, and returns the URL and file metadata.

**Call relations**: This is the handler behind the `share_file` tool. It talks to the sandbox for file inspection and export, to blob storage for durable file bytes, to the database for the shared-artifact record, and to the artifact token code to create the download link.

*Call graph*: 15 external calls (__init__, __init__, now, dumps, loads, guess_type, PurePosixPath, quote, insert, select (+5 more)).


##### `spawn_subagent_handler`  (lines 527–538)

```
async def spawn_subagent_handler(ctx: ToolContext, args: SpawnSubagentInput) -> ToolResult
```

**Purpose**: Starts a child agent to work on a typed subtask. It can either wait for the child’s validated answer or return immediately with the child turn id for background work.

**Data flow**: It receives a subagent profile name, a payload, and a background flag. It asks the tool context to spawn the subagent. If the profile is unknown, it returns a recoverable error; if the child is running in the background, it returns the child turn id; otherwise it returns the child’s structured output.

**Call relations**: This is the handler behind `spawn_subagent`. It hands the actual delegation to `ToolContext.spawn`, and its results can later be followed up through the wait, cancel, and message subagent tools.

*Call graph*: 3 external calls (__init__, __init__, spawn).


##### `load_sessions_handler`  (lines 541–601)

```
async def load_sessions_handler(ctx: ToolContext, args: LoadSessionsInput) -> ToolResult
```

**Purpose**: Loads selected past conversation transcripts that belong to the same workspace and the appropriate audience member. It reports bad or inaccessible session ids without failing the whole request.

**Data flow**: It receives a list of session id strings. It separates malformed UUIDs, queries the database for conversations that are in scope, fetches each transcript from blob storage, decodes it, extracts user and assistant text, and returns successful sessions plus a list of failed ids.

**Call relations**: This is the handler behind `load_sessions`. It combines database scoping, blob transcript lookup, and transcript decoding so the agent can recall specific prior conversations without getting access to unrelated ones.

*Call graph*: 8 external calls (__init__, __init__, dumps, select, workspace_tx, decode, transcript_key, UUID).


##### `ask_user_handler`  (lines 609–619)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserInput) -> ToolResult
```

**Purpose**: Packages one or more questions for the agent to ask in its normal chat reply. It tells the agent to end the turn so the user’s answer can arrive as the next message.

**Data flow**: It receives a structured question request. It turns the title and questions into JSON, prefixes it with a clear instruction to ask and wait, and returns that text as the tool result.

**Call relations**: This is the handler behind `ask_user`. It does not open a separate prompt or side channel; instead it gives the model and chat surface the structured content needed to ask the user in the conversation.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 622–630)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a named skill and any skills it depends on into the workspace. A skill is a bundle of instructions and supporting files that helps the agent follow a specialized workflow.

**Data flow**: It receives a skill name. It asks the skill registry for the full dependency chain, mounts each skill’s files into the sandbox workspace, builds the combined skill context, and returns the instructions and mounted file tree as text.

**Call relations**: This is the handler behind `load_skill`. It uses the skills runtime to both place files where the agent can read them and produce the written workflow the model should follow.

*Call graph*: 4 external calls (__init__, __init__, loaded_context, mount_skill).


##### `connect_account_handler`  (lines 639–647)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Starts a private account-connection flow for an external provider such as GitHub or Google. It validates the provider and returns instructions telling the member to use a private connection control, not a chat-visible URL.

**Data flow**: It receives a provider name and whether the account should be shared with the workspace. It requires a speaking member, validates that the provider is known, builds a connection request, and returns a directive plus the request JSON.

**Call relations**: This is the handler behind `connect_account`. It relies on the installed connection-flow service to validate providers, then hands structured instructions back to the chat surface so authorization happens privately.

*Call graph*: 4 external calls (__init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 656–681)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Requests secret values, such as API keys, through a private channel instead of the chat transcript. It only allows the workspace owner to fill these credential slots.

**Data flow**: It receives a reason and a small list of credential prompts. It checks that there is a speaking member, that the request is in that member’s private audience, that credential storage is configured, and that the speaker is the owner. It seals the requested slots into a signed request and returns a directive plus structured credential-request JSON.

**Call relations**: This is the handler behind `request_credentials`. It calls the context to confirm owner status, uses the configured credential sealer to protect the request, and returns information a capable user surface can turn into private prompts.

*Call graph*: calls 1 internal fn (speaker_is_owner); 3 external calls (__init__, __init__, __init__).


##### `wait_for_subagents_handler`  (lines 684–697)

```
async def wait_for_subagents_handler(ctx: ToolContext, args: WaitForSubagentsInput) -> ToolResult
```

**Purpose**: Waits for one or more background subagents to finish and reports their final status and answer. It also marks the result as untrusted if any child result is untrusted.

**Data flow**: It receives subagent id strings. It checks that subagent control exists, converts the ids to UUIDs, asks the subagent controller to wait for them, then returns a JSON list with each child’s id, status, and output text.

**Call relations**: This is the handler behind `wait_for_subagents`. It is used after `spawn_subagent_handler` starts background work, giving the parent agent a way to pause until those children complete.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `cancel_subagent_handler`  (lines 700–712)

```
async def cancel_subagent_handler(ctx: ToolContext, args: CancelSubagentInput) -> ToolResult
```

**Purpose**: Cancels a running background subagent and reports its current status. If the subagent has already finished, the operation leaves its final state alone.

**Data flow**: It receives a subagent id string. It checks that subagent control is available, converts the id to a UUID, asks the subagent controller to cancel it, and returns the subagent id and resulting status as JSON.

**Call relations**: This is the handler behind `cancel_subagent`. It is part of the same subagent-control flow as spawning and waiting, and it lets the parent stop work it no longer needs.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_subagent_handler`  (lines 715–728)

```
async def message_subagent_handler(ctx: ToolContext, args: MessageSubagentInput) -> ToolResult
```

**Purpose**: Sends a follow-up message to a background subagent. The message becomes the subagent’s next turn after its current work reaches a stopping point.

**Data flow**: It receives a subagent id and a message. It checks that subagent control is available, converts the id to a UUID, queues the message through the subagent controller, and returns the id and status for the queued follow-up.

**Call relations**: This is the handler behind `message_subagent`. It complements `spawn_subagent_handler` and `wait_for_subagents_handler` by letting the parent agent steer a child that is already running in the background.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### Tool package foundations
The tools package defines the controlled execution context, standardized outputs and cleanup, and the registry used to describe and dispatch callable tools.

### `core/src/ufo/tools/__init__.py`

`other` · `import/package discovery`

This is a package marker file. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package, like a labeled drawer in a filing cabinet. Here, the drawer is `ufo.tools`. The only content is a short documentation string explaining what the package is about: tools, including the registry that keeps track of available tools, the context passed to tool handlers, and the built-in set of tools shipped with the project. There is no executable code here and no functions to call. Its value is organizational: without this file, depending on the Python version and packaging setup, imports from this folder could be less explicit or fail in some environments, and newcomers would have one less signpost for understanding what the `tools` package contains.


### `core/src/ufo/tools/context.py`

`domain_logic` · `tool execution during a turn, with cleanup at turn end`

A tool in this system should not be able to freely touch the whole program. This file is the boundary box it receives instead. Think of it like a visitor badge: it says which files, accounts, browser session, search service, blob storage, credentials, and child agents the tool may use for this one turn.

The file defines simple output blocks, such as text and images, and wraps them in ToolResult so the rest of the system can tell whether a tool succeeded, failed, or returned untrusted outside content. It also defines protocols for spawning subagents and controlling background subagents. A protocol is a promised shape: the real implementation lives elsewhere, but tool code can rely on these methods existing.

The central object is ToolContext. It carries the current turn, agent, sandbox, permissions, extension workspace, selected browser/search providers, connector registry, and a per-turn cleanup list. Its helper methods enforce important safety rules. For example, credential authorization only works for the speaking workspace owner, in that speaker’s private audience, and only for credential slots declared by the extension. Connector account lookup only returns accounts granted to this turn’s agent and allowed for the acting member. Without this file, tools would either lack the information they need or would have to duplicate delicate permission checks in many places.

#### Function details

##### `Spawn.__call__`  (lines 120–126)

```
async def __call__(self, profile: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None) -> SpawnResult
```

**Purpose**: This is the promised interface for starting a child agent, called a subagent, to work on a smaller task. A tool uses it when it wants to delegate work and optionally wait for a typed, validated answer.

**Data flow**: The caller provides a profile name, an input payload, and choices such as whether the child should run in the background and whether a repeat call should reuse the same child. The real implementation checks the profile and payload, starts or reconnects to the child turn, and returns a SpawnResult containing the child turn id and, if waited for, its output.

**Call relations**: Tool handlers call this through ToolContext.spawn when they need another agent to do part of the work. This file only defines the callable shape; the subagent workflow elsewhere supplies the actual behavior.


##### `SubagentControl.wait`  (lines 135–135)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: This is the promised interface for waiting until one or more background subagents finish. A tool uses it after it previously started subagents in the background and now needs their final results.

**Data flow**: The caller gives child turn ids. The implementation waits for those child turns to reach a finished state and returns a status record for each one, including its final text and whether the output should be treated as untrusted.

**Call relations**: Tools reach this through ToolContext.subagents. The actual waiting logic belongs to the subagent system; this protocol lets tool code use it without knowing how subagents are scheduled internally.


##### `SubagentControl.cancel`  (lines 137–137)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: This is the promised interface for stopping a running background subagent. A tool uses it when a delegated task is no longer needed or should not continue.

**Data flow**: The caller gives one child turn id. The implementation asks that child turn to stop and returns its terminal status, including the final message explaining what happened.

**Call relations**: Tools call this through ToolContext.subagents. The file defines the contract, while the subagent workflow elsewhere performs the cancellation.


##### `SubagentControl.message`  (lines 139–139)

```
async def message(self, turn_id: UUID, text: str) -> SubagentStatus
```

**Purpose**: This is the promised interface for sending a follow-up message to a background subagent. A tool uses it when an already-spawned child needs more instructions or clarification.

**Data flow**: The caller gives the child turn id and the text message. The implementation delivers that message as the child’s next turn and returns the resulting terminal status when that follow-up completes.

**Call relations**: Tools access this through ToolContext.subagents. The implementation is supplied by the subagent system, so this file acts as the shared agreement between tools and that system.


##### `TurnCleanup.register`  (lines 153–154)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: This records an asynchronous cleanup action to run when the current turn ends. Tools use it when they open a per-turn resource, such as a browser connection, that must be closed later.

**Data flow**: A caller passes in an async close function. The function is added to an internal list; nothing is closed immediately.

**Call relations**: A tool registers cleanup when it first creates a resource for the turn. Later, the turn loop calls TurnCleanup.drain to run all registered close functions.


##### `TurnCleanup.drain`  (lines 156–162)

```
async def drain(self) -> None
```

**Purpose**: This closes all resources that were registered for the turn, even if the turn ended with an error or cancellation. It helps prevent leaked network connections, browser sessions, or other temporary leases.

**Data flow**: It reads the stored list of close functions, removes them one by one in reverse order, and awaits each close. If one close fails, it logs the failure and continues closing the rest.

**Call relations**: The turn-running code calls this at turn end. It uses the logging system when a cleanup action throws an exception, so one bad cleanup does not stop the remaining cleanup work.

*Call graph*: 1 external calls (log).


##### `ToolContext.acting_member_id`  (lines 191–204)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: This chooses which workspace member the turn is acting for when using private resources. Usually that is the speaking member, but scheduled work or delegated subagents may carry an on-behalf-of member instead.

**Data flow**: It reads speaker_member_id first. If there is a speaker, it returns that id; otherwise it returns on_behalf_of_member_id, which may also be absent.

**Call relations**: Connector account lookup uses this identity to decide which private connections are available. It keeps scheduled jobs and subagents tied to the member who delegated them, while anonymous internal turns get no private member identity.


##### `ToolContext.speaker_is_owner`  (lines 206–215)

```
async def speaker_is_owner(self) -> bool
```

**Purpose**: This checks whether the current speaking member is the workspace owner. It is used before actions that affect shared workspace-wide resources, such as authorizing an extension credential slot.

**Data flow**: If there is no speaking member, it immediately returns false. Otherwise it opens a workspace database transaction, asks for the workspace owner member id, compares it to the speaker, and returns true only if they match.

**Call relations**: Several object and credential operations call this before allowing owner-only actions. The credential authorization helper also calls it so only the workspace owner can approve stored extension credentials.

*Call graph*: called by 16 (apply, delete, apply, delete, get, list, status, request_credentials_handler, _credential_authorization, connect_github (+6 more)); 2 external calls (workspace_tx, owner_member_id).


##### `ToolContext.begin_credential_authorization`  (lines 217–219)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: This starts the process of authorizing a credential slot for an extension. It creates a sealed authorization token or link-like value that can later be opened or fulfilled.

**Data flow**: The caller provides the credential slot name and a payload. The function first asks _credential_authorization to verify that this turn is allowed to request the credential, then calls the credential request service to create the authorization value and returns it.

**Call relations**: Extension code such as GitHub and Slack connection flows call this when they need the workspace owner to approve a credential. It relies on _credential_authorization to enforce the safety checks before handing off to the credential request service.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 2 (connect_github, _oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 221–223)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: This opens an existing sealed credential authorization so the flow can continue safely. It is part of the controlled path for handling secrets rather than letting tools read or write them freely.

**Data flow**: The caller gives the credential slot and sealed authorization value. The function verifies permission through _credential_authorization, then asks the credential request service to open the sealed value for this workspace, member, and slot, returning the opened payload.

**Call relations**: It is a companion to begin_credential_authorization. Both route through the same permission helper so every credential step follows the same owner, speaker, audience, extension, and deployment checks.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext.fulfill_credential_authorization`  (lines 225–230)

```
async def fulfill_credential_authorization(self, slot: str, sealed: str, plaintext: str) -> None
```

**Purpose**: This completes an approved credential authorization by storing the plaintext secret in the workspace credential store. It is the point where an allowed secret actually gets saved.

**Data flow**: The caller provides the slot, sealed authorization value, and plaintext secret. The function verifies the authorization rules, opens the sealed value to confirm it matches this workspace, member, and slot, then writes the plaintext credential into the current workspace store.

**Call relations**: It calls _credential_authorization for permission checks and then uses ws_current to reach the active workspace storage. It is used after an authorization flow has proved that the workspace owner approved storing the credential.

*Call graph*: calls 1 internal fn (_credential_authorization); 1 external calls (ws_current).


##### `ToolContext._credential_authorization`  (lines 232–243)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: This is the shared gatekeeper for all credential authorization steps. It prevents tools from requesting or storing secrets unless the current turn is exactly allowed to do so.

**Data flow**: It reads the speaker, audience, extension declaration, credential request service, and workspace ownership. It raises clear errors if there is no speaker, the audience is not the speaker’s private audience, the extension did not declare the slot, the deployment cannot store secrets, or the speaker is not the owner. If all checks pass, it returns the credential request service and speaker member id.

**Call relations**: begin_credential_authorization, open_credential_authorization, and fulfill_credential_authorization all call this before doing their work. It calls speaker_is_owner as the final owner-only check, keeping the credential rules centralized in one place.

*Call graph*: calls 1 internal fn (speaker_is_owner); called by 3 (begin_credential_authorization, fulfill_credential_authorization, open_credential_authorization).


##### `ToolContext.connector_account`  (lines 245–272)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: This picks one connected external account for a connector tool to use, such as an account held by a broker service. It makes sure the tool can only use accounts granted to this turn’s workspace and agent.

**Data flow**: The caller gives a provider name and optionally a specific account id. The function asks _connector_account_tiers for private and shared accounts. If a specific id was requested, it returns it only if available. If no id was requested, it prefers the acting member’s private accounts, falls back to shared accounts, and requires exactly one choice; otherwise it raises an error explaining what is missing or ambiguous.

**Call relations**: Connector extensions call this before executing external tools through the broker. It delegates grant lookup to _connector_account_tiers, then applies the user-facing selection rules so the extension gets one safe account id.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 2 (call_external_tool, _connector_execute).


##### `ToolContext.connector_accounts`  (lines 274–282)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: This lists all connected account ids that the turn may use for one provider. It is useful when a tool needs to show or resolve the available choices instead of picking exactly one.

**Data flow**: The caller gives a provider name. The function asks _connector_account_tiers for private and shared accounts, combines them, removes duplicates, sorts them, and returns them as a tuple.

**Call relations**: Source-related extension code calls this when resolving which external account is available. It uses the same lower-level grant lookup as connector_account so listing and selecting accounts follow the same permission model.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 1 (_resolved_account).


##### `ToolContext._connector_account_tiers`  (lines 284–297)

```
async def _connector_account_tiers(self, provider: str) -> tuple[list[str], list[str]]
```

**Purpose**: This is the shared grant lookup for connector accounts. It separates accounts into private accounts for the acting member and shared accounts for the agent.

**Data flow**: It first checks that a grant store exists; if not, it raises ConnectUnavailable because account grants cannot be checked. It reads the acting member id, asks the grant store for active grants in this workspace and agent, filters them by provider, then returns two sorted lists: private matching grants for the acting member and shared matching grants.

**Call relations**: connector_account and connector_accounts both call this so they use the same source of truth. It is the part that talks to the grant system; the public methods then decide whether to choose one account or return all allowed accounts.

*Call graph*: called by 2 (connector_account, connector_accounts); 1 external calls (__init__).


### `core/src/ufo/tools/registry.py`

`data_model` · `startup and tool dispatch`

This file solves a simple but important problem: when the model asks to use a tool, the engine needs a trustworthy catalog that says which tools exist, what arguments they accept, and which function should run. Without this registry, the engine could not reliably show tool definitions to the model or dispatch a requested tool name to the right code.

A ToolDef is one tool’s “business card.” It stores the tool name, a human-readable description, the Pydantic input model that describes valid arguments, and the async handler function that actually runs the tool. It also carries safety flags. For example, untrusted means the tool may return attacker-controlled text, such as a web page, so the engine should treat that text as data rather than instructions. side_effecting means the tool can change something outside the system, such as making a POST request or writing durable data, so the engine may attach an idempotency key, a repeat-safe label that helps avoid doing the same external action twice.

ToolRegistry is the frozen catalog of ToolDef entries. When it is created, it refuses duplicate tool names, because two tools with the same name would make dispatch ambiguous. Later, it can produce wire schemas for the model or find a tool by name when the engine needs to run it.

#### Function details

##### `ToolDef.schema`  (lines 37–42)

```
def schema(self) -> ToolSchema
```

**Purpose**: Builds the public schema for one tool, which is the compact description sent to the model client. Someone uses this when they need to tell the model what the tool is called, what it does, and what input format it must use.

**Data flow**: It starts with a ToolDef that already knows its name, description, and Pydantic input model. It asks the input model for its JSON schema, which is a standard machine-readable description of valid fields, then wraps the name, description, and input schema into a ToolSchema object. The result is a wire-ready tool description; the ToolDef itself is not changed.

**Call relations**: This function is used by ToolRegistry.schemas when the system needs the full list of tool descriptions. It hands the finished ToolSchema to the registry, which gathers schemas for all registered tools.

*Call graph*: 1 external calls (__init__).


##### `ToolRegistry.__post_init__`  (lines 49–53)

```
def __post_init__(self) -> None
```

**Purpose**: Checks the registry right after it is built to make sure no two tools share the same name. This prevents a later tool call from becoming a guessing game.

**Data flow**: It reads the names from every ToolDef in the registry. It looks for any name that appears more than once. If all names are unique, nothing changes and construction succeeds; if duplicates exist, it raises a ValueError with the duplicate names.

**Call relations**: This runs automatically after a ToolRegistry is created. It is an early safety gate, so later code such as tool dispatch can assume each tool name points to exactly one tool.


##### `ToolRegistry.schemas`  (lines 55–56)

```
def schemas(self) -> tuple[ToolSchema, ...]
```

**Purpose**: Returns the model-facing descriptions for every registered tool. This is used when the system needs to advertise its available tools in a form the model client understands.

**Data flow**: It starts with the registry’s tuple of ToolDef objects. For each tool, it calls ToolDef.schema to turn that internal definition into a ToolSchema. It returns a tuple of those schemas and does not modify the registry.

**Call relations**: This is the registry’s bulk export path. It relies on each ToolDef to describe itself, then packages those descriptions together for whichever part of the system is preparing tool information for the model.


##### `ToolRegistry.get`  (lines 58–62)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: Finds the registered tool definition for a requested tool name. The engine uses this when the model has asked to call a tool and the system must locate the exact handler to run.

**Data flow**: It receives a name string and scans the registry’s tools in order. If it finds a ToolDef with a matching name, it returns that definition, including its input model, handler, and safety flags. If no match exists, it raises a KeyError so the mistake is loud rather than silently ignored.

**Call relations**: core/src/ufo/loop/engine._dispatch_segments calls this during tool dispatch. In that flow, the engine receives or identifies a requested tool name, asks the registry for the matching ToolDef, and then can use that definition to validate inputs and run the correct handler.

*Call graph*: called by 1 (_dispatch_segments).

## 📊 State Registers Touched

- `reg-extension-set` — The saved and loaded set of extensions, packs, manifests, and contributed capabilities available to the runtime.
- `reg-credential-store` — The encrypted secrets and credential slots used to let tools and connectors act for a workspace without exposing raw secrets.
- `reg-connection-grants` — The saved approvals and safe account handles for connected external accounts such as Slack, GitHub, Composio, and Pipedream.
- `reg-conversation-transcript` — The stored conversation history, messages, files, speakers, and outcomes that later stages read and append to.
- `reg-tool-catalog` — The shared catalog of tools the model may call, including their names, schemas, handlers, and safety properties.
- `reg-tool-context` — The per-run authority envelope that gives tools only the workspace, credentials, cleanup hooks, and permissions they are allowed to use.
- `reg-sandbox-session` — The sandbox handle and lifecycle state for the safe workspace where code, files, browsers, and commands run.
- `reg-workspace-storage` — The shared file, blob, artifact, and mount state that stores workspace bytes and files shared back to users.
- `reg-egress-policy` — The network access and proxy state that decides which sandbox traffic is allowed, audited, billed, or given injected secrets.
- `reg-browser-session` — The browser automation connection state used when tools need a controlled browser for a turn.
- `reg-skill-inventory` — The built-in and user-created skill folders, metadata, dependencies, and workspace-specific skill records.
- `reg-source-pages` — The source connections, sync cursors, imported pages, removal markers, and page-change records from outside systems.
- `reg-search-index` — The searchable text chunks, embeddings, and selected index backend used to find relevant stored content.
- `reg-memory-store` — The durable memories, memory pages, recall events, and consolidation state used for long-term recall.
- `reg-extension-store` — The per-workspace extension-owned storage where plugins keep their own durable records without private tables.
- `reg-todo-checklist` — The per-conversation persistent checklist or task-progress state maintained by the todo extension across agent turns.
- `reg-eval-environment-fixtures` — Workspace-scoped fake email and calendar records used by the evaluation environment connectors and tools.
- `reg-turn-admission-context` — Durable per-turn requester/speaker/on-behalf-of, timezone, surface context, and authorization-link metadata used to attribute, resume, and safely handle work.
- `reg-turn-replay-journal` — Durable per-turn execution checkpoints for model calls, tool results, and side-effect/idempotency markers used to resume work without duplicating paid calls or irreversible actions.
