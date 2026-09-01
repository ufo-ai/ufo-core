# Workspace Object Mutations and Domain Tools  `stage-10.4`

This stage is shared behind-the-scenes support for changing “workspace objects,” meaning saved records such as tasks, monitors, prompts, skills, and todos. The central piece is the object system, which acts like a front desk. It checks that each record has the right shape, applies visibility rules, and sends each create, update, delete, read, or action request to the extension that owns that object type.

Several domain tools plug into that front desk. Scheduled tasks expose reminders and timed workflows as normal objects, and can pause work until a person replies or a timer runs out. The monitor tool lets an agent watch an outside thing, like a build or inbox, by running a command once, saving that first result, and only continuing if it works. The todo extension gives conversations a visible checklist the agent can update.

Prompt governance protects agent instructions by requiring proposed changes and approval, and by blocking stale approvals from overwriting newer prompts. Object scope quietly records which agent an action is acting as. The skill package introduces runtime skills that agents can create, own, and use.

## Files in this stage

### Scheduled task bridge
User-facing scheduled task commands are exposed as workspace objects and connected to durable schedule and pause storage.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `request handling and scheduled workflow execution`

This file solves two related problems: recurring work and safe waiting. A scheduled task is like a calendar reminder for the agent. A member creates it with a cron schedule, which is a compact text pattern for repeated times, plus a prompt. The system then remembers it, shows it in object lists, lets the right people update or delete it, and later runs it back in the same conversation as the original creator.

The file defines the shape of a scheduled task, checks that expiry times are truly UTC, builds list and detail views, hides private prompt text from people who can see only limited metadata, and enforces permission rules. The creator controls the task content. Admins can change operational settings such as timing, expiry, pause state, or deletion, but they cannot rewrite another member’s prompt.

It also defines `pause_and_wait`, which is not a workspace object. A pause is more like putting a bookmark in a running workflow. The tool records where the conversation was, when to resume, and what instructions to use later. It deliberately does not try to decide whether a person has already replied at the moment of pausing, because that answer could become stale immediately. Instead, the later wake-up path decides safely under the conversation’s lock.

#### Function details

##### `ScheduledTaskSpec.validate_utc_expiry`  (lines 103–106)

```
def validate_utc_expiry(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This checks that a task expiry time, if supplied, is written as a UTC timestamp. UTC is the shared world clock used here so scheduled work does not depend on a local time zone.

**Data flow**: It receives the proposed `expires_at` value. If there is no value, it leaves it alone. If there is a value, it checks whether it has time zone information and whether its offset is exactly zero; if not, it rejects it with a clear error. A valid value is returned unchanged.

**Call relations**: This runs automatically when a `ScheduledTaskSpec` is built or validated. It protects later scheduling code from receiving an ambiguous expiry time.

*Call graph*: 2 external calls (utcoffset, timedelta).


##### `_require_scheduler`  (lines 123–124)

```
def _require_scheduler(ext: ExtensionContext | None) -> ScheduleStore
```

**Purpose**: This is a small helper that gives the rest of the file access to the schedule storage layer. It also makes sure the scheduled-tasks extension context is present before storage is used.

**Data flow**: It receives an optional extension context. It first passes that context through `_require_ext`, which either returns a real context or raises an error. Then it builds and returns a `ScheduleStore`, which is the object used to read and write scheduled task records.

**Call relations**: Most scheduled-task operations call this before touching stored schedules. Listing, status lookup, creation, update, deletion, and conversation row building all depend on it so they talk to the same schedule store in a consistent way.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_apply_owned, _delete_owned, _find, _rows, _status, member_conversation_rows); 1 external calls (__init__).


##### `_require_ext`  (lines 127–130)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This verifies that code is running with the extension context needed by scheduled tasks. Without that context, the file cannot reach its tables or extension-specific services.

**Data flow**: It receives an optional extension context. If the context is missing, it raises a runtime error explaining that scheduled tasks require it. If the context is present, it returns it unchanged.

**Call relations**: `_require_scheduler` uses this before creating a schedule store, and `pause_and_wait` uses it before writing a pause. It is the guardrail at the boundary between tool code and extension storage.

*Call graph*: called by 2 (_require_scheduler, pause_and_wait).


##### `_summary`  (lines 133–134)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: This creates a short human-readable one-line summary for a scheduled task. It is used in lists where readers need to recognize a task quickly.

**Data flow**: It receives a scheduled task. It combines the cron schedule with either the task description or, if there is no description, the prompt. Then it cuts the text down to the configured maximum length and returns that shortened summary.

**Call relations**: `ScheduledTaskObjects._rows` calls this when building visible list rows. It turns stored task data into the compact text shown on object surfaces.

*Call graph*: called by 1 (_rows).


##### `_owner`  (lines 137–145)

```
def _owner(listed: ListedTask) -> GeneratedObjectOwner
```

**Purpose**: This describes who owns a scheduled task and how far it is shared. The task’s visibility follows the conversation it reports into, much like a note pinned inside a room is visible to people who can enter that room.

**Data flow**: It receives a listed task, including its stored task and audience information. It creates an owner record containing the creator’s member id, a shared-visibility marker derived from the audience, and the task id as the generation marker. That owner record is returned.

**Call relations**: Listing and conversation-row code use this to feed the generic object permission system. `ScheduledTaskObjects._rows` attaches it to object rows, and `ScheduledTaskObjects.member_conversation_rows` uses it before deciding whether a member can see a task grant.

*Call graph*: called by 2 (_rows, member_conversation_rows); 2 external calls (__init__, subject_shared).


##### `ScheduledTaskObjects._admin_can_apply`  (lines 166–169)

```
def _admin_can_apply(self, _old: ScheduledTaskSpec, spec: ScheduledTaskSpec) -> bool
```

**Purpose**: This decides whether an admin is allowed to apply a particular update to someone else’s scheduled task. Admins may adjust timing controls, but not rewrite the task’s prompt or description.

**Data flow**: It receives the old task spec and the proposed new spec. It checks which fields the update actually set. If the update includes `prompt` or `description`, it returns false; otherwise it returns true.

**Call relations**: This supports the broader object update permission flow supplied by the base object class. It expresses this file’s special rule: content belongs to the creator, while cadence and pause controls can be administered.


##### `ScheduledTaskObjects.member_page`  (lines 171–193)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This builds a page of scheduled tasks for a member, with special support for filtering by conversation. It lets the object list show only tasks that report into a specific conversation when requested.

**Data flow**: It reads the query filters and looks for a `conversation` value. If there is no valid conversation id, it either falls back to the normal member-page behavior or returns an empty page. For a valid conversation id, it loads matching task rows, filters out rows the member cannot see, wraps them as plain object rows, and returns a paged result.

**Call relations**: This is called by the generic object listing surface when a member lists scheduled tasks. It delegates row construction to `_rows`, relies on visibility checks from the inherited object behavior, and packages the answer with `object_page`.

*Call graph*: calls 1 internal fn (_rows); 3 external calls (__init__, object_page, UUID).


##### `ScheduledTaskObjects.member_conversation_rows`  (lines 195–215)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: This returns the scheduled tasks that should appear in a conversation’s object slot. It tells the conversation view which tasks report there and whether each task’s content can be shown to the current member.

**Data flow**: It receives a conversation id, member identity, admin flag, and limit. It asks the schedule store for tasks reporting into that conversation. For each task, it builds an ownership record, checks whether the member may see it, marks whether the prompt content is visible, and returns conversation object grants.

**Call relations**: Conversation rendering calls this when it wants related objects for a conversation. It uses `_require_scheduler` to read schedules, `_owner` for permission facts, and `task_content_visible` to avoid exposing private prompt text.

*Call graph*: calls 2 internal fn (_owner, _require_scheduler); 2 external calls (__init__, task_content_visible).


##### `ScheduledTaskObjects._member_rows`  (lines 217–220)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This supplies all scheduled-task rows visible through the member-readable object system. It is the broad listing path for member access.

**Data flow**: It receives the extension context and an optional member id. It passes those into `_rows` without limiting prompt length, then returns the owned rows that `_rows` builds.

**Call relations**: The base `MemberReadableObjects` machinery calls this when it needs rows for a member-facing object listing. It exists mainly as the class-specific hook that points the generic machinery at `_rows`.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._owned_rows`  (lines 222–229)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This builds the rows a tool turn reads when it lists scheduled tasks. It trims prompts to an excerpt so a large set of tasks does not flood the model’s context.

**Data flow**: It receives the current tool context, including the acting member and extension context. It asks `_rows` for rows as that member, with prompt text limited to the configured excerpt size. It returns those shortened owned rows.

**Call relations**: The object tool flow calls this when an agent turn performs an object list. It relies on `_rows` for the real list-building work, but chooses the safer prompt length for model input.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._rows`  (lines 231–277)

```
async def _rows(self, ext: ExtensionContext | None, *, member_id: UUID | None, prompt_max: int | None, conversation_id: UUID | None=None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This is the main row builder for scheduled task listings. It gathers stored task data, owner information, last-run status, visibility decisions, and display fields into one object-list row per task.

**Data flow**: It receives an extension context, optional member id, optional prompt length limit, and optionally a conversation id. It loads reported tasks from the schedule store, looks up creator email addresses, asks the schedule store for inspection data such as last turn status, and then builds an `OwnedRow` for each task. If the member cannot see the task content, it replaces the prompt and summary text with a private placeholder.

**Call relations**: `member_page`, `_member_rows`, and `_owned_rows` all call this instead of each rebuilding list data themselves. It calls `_require_scheduler` for storage, `_summary` for display text, `_owner` for sharing facts, `owner_emails` for creator labels, and `task_content_visible` for privacy.

*Call graph*: calls 3 internal fn (_owner, _require_scheduler, _summary); called by 3 (_member_rows, _owned_rows, member_page); 3 external calls (__init__, owner_emails, task_content_visible).


##### `ScheduledTaskObjects._member_object`  (lines 279–308)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ScheduledTaskSpec] | None
```

**Purpose**: This builds the detailed view of one scheduled task for a member. It returns the task’s full spec when allowed, plus metadata such as creation time and the conversation it reports to.

**Data flow**: It receives a task name, expected owner record, and optional member id. It finds the task by name and confirms it is the same stored generation the caller expected. If not, it returns nothing. If it matches, it creates a `ScheduledTaskSpec`, adds timestamps, adds a link to the reporting conversation, marks whether the spec content is visible to this member, and returns the detail object.

**Call relations**: The generic object get/read flow calls this for a specific scheduled task. It uses `_find` to locate the stored task and `task_content_visible` to decide whether the prompt and description should be exposed.

*Call graph*: calls 1 internal fn (_find); 5 external calls (__init__, __init__, __init__, __init__, task_content_visible).


##### `ScheduledTaskObjects._status`  (lines 310–342)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This returns live status information for a scheduled task, such as whether it is paused, when it will run next, and what happened in the last run. It is the status panel rather than the editable task definition.

**Data flow**: It receives the tool context, task name, and expected owner record. It finds the task and verifies the generation. Then it asks the schedule store to inspect the task. If there was a last run, it includes the turn id and status, and includes an excerpt of the last response only if the acting member is allowed to see the task content. It returns a dictionary of status fields or nothing if the task cannot be found or inspected.

**Call relations**: Object status calls use this after identifying a scheduled task. It depends on `_find` for lookup, `_require_scheduler` for inspection, and `task_content_visible` for privacy around response text.

*Call graph*: calls 2 internal fn (_find, _require_scheduler); 1 external calls (task_content_visible).


##### `ScheduledTaskObjects._apply_owned`  (lines 344–396)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This creates a new scheduled task or updates an existing one. It validates the schedule, enforces who may change what, binds new tasks to the current conversation and acting member, and recalculates the next run time.

**Data flow**: It receives the tool context, object name, proposed spec, previous spec if any, and owner record if this is an existing object. It validates the cron schedule if one was supplied, checks that there is an acting member, finds any existing task, and gets the schedule store. For a new task, it requires both schedule and prompt, sets the first next-run time, and stores the task tied to the current conversation. For an update, it verifies the stored task still matches the expected generation, checks member/admin permissions, preserves omitted fields, recalculates the next fire time, and writes the update.

**Call relations**: The generic object apply/update command calls this when a user creates or changes a scheduled task. It calls `_find` to detect races, `_require_scheduler` to write storage, `validate_cron` and `next_fire` for schedule correctness, and `speaker_is_admin` when permission depends on admin status.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_scheduler); 4 external calls (__init__, now, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 398–402)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This cancels a scheduled task when the object delete flow has authorized the request. It protects against deleting a task that changed after the caller selected it.

**Data flow**: It receives the tool context, task name, and expected owner record. It finds the current stored task by name. If the task is missing or its generation no longer matches, it raises an error saying the task changed while cancelling. Otherwise, it asks the schedule store to cancel the task.

**Call relations**: The generic object delete command calls this after permission checks. It uses `_find` for a safe lookup and `_require_scheduler` to perform the cancellation in storage.

*Call graph*: calls 2 internal fn (_find, _require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 404–412)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTask | None
```

**Purpose**: This looks up a scheduled task by its object name. It is the local search helper used before reading, updating, checking status, or deleting one task.

**Data flow**: It receives an extension context and task name. It loads all reported scheduled tasks from the schedule store, scans for the first one whose stored name matches, and returns that listed task. If none match, it returns nothing.

**Call relations**: `_apply_owned`, `_delete_owned`, `_member_object`, and `_status` call this whenever they need to turn a user-facing name into the stored task record. It uses `_require_scheduler` to access the schedule store.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


##### `pause_and_wait`  (lines 475–512)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: This tool pauses the current workflow and arranges for it to resume later, either when a member sends a new message or when a durable timer fires. It is useful for waits that may outlive the current process, such as approvals, verification emails, or cooldown periods.

**Data flow**: It receives the current tool context and pause arguments: the message to show now, how many minutes to wait, resume instructions, a reason, and optional metadata. It computes the resume time, builds a wake-up prompt for the later turn, records the pause in `PauseStore` with the current conversation, agent, turn sequence, and arrival watermark, then returns a tool result telling the agent to reply with the provided `ai_response` and end the turn. The returned payload also includes the resume time and saved instructions.

**Call relations**: The `PAUSE_AND_WAIT_TOOL` definition exposes this function as a tool handler. Inside, it uses `_require_ext` to ensure extension services are available, writes the durable pause through `PauseStore`, and returns `TextContent` inside a `ToolResult` so the calling turn knows exactly what to say and do next.

*Call graph*: calls 1 internal fn (_require_ext); 6 external calls (__init__, __init__, __init__, now, timedelta, dumps).


### Object mutation core
The runtime object system validates workspace records, protects prompt changes, and tracks the agent scope used while object actions run.

### `core/src/ufo/runtime/objects.py`

`domain_logic` · `request handling and extension registration`

A “workspace object” is a named record, like a saved rule, report, grant, or integration item, stored under a kind and a name. This file makes those objects feel uniform even when different extensions store them in different tables. Without it, every extension would need its own naming rules, permissions checks, pagination, YAML parsing, schema validation, and tool wiring, which would make object behavior inconsistent and unsafe.

The file has three main jobs. First, it defines the shared vocabulary: object rows for lists, full object details, links between objects, ownership records, and the store interface that each kind must implement. Second, it provides reusable gates for member-owned objects, so private rows stay private, shared rows are visible, and only owners or admins can change the right things. Third, it exposes the public tool verbs: list, get, explain, apply, delete, and object actions.

A useful analogy is a courthouse clerk. Extensions own the actual filing cabinets, but this file is the clerk at the front desk. It checks that forms use the right format, verifies who is allowed to see or change a record, stamps changes into a journal, then sends the request to the correct cabinet. It also prevents risky specs from containing secrets, because specs may be shown back to users and transcripts.

#### Function details

##### `_ObjectCursor.validate_rank`  (lines 195–200)

```
def validate_rank(self) -> '_ObjectCursor'
```

**Purpose**: Checks that a saved list cursor contains the right kind of value for its sort type. This prevents a caller from sending a malformed continuation token that would make paging unreliable.

**Data flow**: It reads the cursor’s rank and value after Pydantic has parsed them. If the pair matches the allowed shape, it returns the cursor unchanged; otherwise it raises an error.

**Call relations**: It is used automatically while decoding an object-list cursor inside `object_page`. That means paging tokens are checked before they affect which rows are returned.


##### `object_page`  (lines 203–286)

```
def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Applies the shared listing rules for object rows: search, exact filters, sorting, and page cursors. Stores can hand over lightweight rows, and this function turns them into one consistent page.

**Data flow**: It receives all candidate rows and a listing query. It checks that row fields are declared, filters and searches the rows, sorts them, applies any cursor boundary, and returns an `ObjectPage` with up to the fixed page size plus a next cursor if more rows remain.

**Call relations**: Member-owned listing paths call this after they have already removed rows the caller may not see. It relies on `_sortable` to build safe sort keys and creates `_ObjectCursor` tokens so later list calls can continue from the same place.

*Call graph*: calls 1 internal fn (_sortable); called by 2 (list, member_page); 2 external calls (__init__, __init__).


##### `object_page.value`  (lines 231–236)

```
def value(row: ObjectRow, name: str) -> JsonValue
```

**Purpose**: Looks up the value of a named list field on one row. It treats `name` and `summary` as built-in fields and all other names as optional row fields.

**Data flow**: It receives a row and a field name. It returns the row’s name, summary, or the matching entry from the row’s extra fields, which may be missing.

**Call relations**: This helper lives inside `object_page` because it is only meaningful during filtering, searching, and sorting for a single page request.


##### `_sortable`  (lines 289–302)

```
def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]
```

**Purpose**: Turns a row field value into a safe, comparable sort key. It gives different simple value types a stable order so lists can be sorted predictably.

**Data flow**: It receives a JSON-like value and the field name being sorted. It converts null, booleans, numbers, and strings into ranked pairs; if the value is a list, object, or other non-simple value, it raises an error.

**Call relations**: `object_page` calls this whenever it sorts rows or builds and reads paging boundaries. It is the guard that keeps list ordering limited to simple values.

*Call graph*: called by 1 (object_page).


##### `ObjectStore.list`  (lines 317–317)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the required list operation for any object kind’s storage backend. A store implements it to return lightweight rows that are safe to show in a list.

**Data flow**: It receives the current tool context and an `ObjectListQuery`. An implementation reads its own storage and returns an `ObjectPage` of names, summaries, and declared list fields.

**Call relations**: `ObjectVerbs._list` calls this through the registered kind. The protocol makes every kind expose the same list shape even if its real data lives somewhere extension-specific.


##### `ObjectStore.get`  (lines 319–319)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines how an object kind reads one full object by name. A store implements it to return the saved spec, timestamps, links, and visibility information.

**Data flow**: It receives the current context and an object name. An implementation looks up that name and returns an `ObjectDetail`, or `None` if the object does not exist or should appear absent.

**Call relations**: `ObjectVerbs._get`, `_apply`, `_delete`, and action targeting use this method before showing, changing, deleting, or acting on an object.


##### `ObjectStore.status`  (lines 321–327)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Defines how an object kind reports live state beside the saved spec. This is for things like current sync state or next scheduled time that are not part of the authored object spec.

**Data flow**: It receives context, object name, and an expected generation value. An implementation checks the current object if needed and returns a JSON-like status mapping, or `None` when there is no status.

**Call relations**: `ObjectVerbs._get` and `ObjectVerbs.action_target` call this after reading an object. Generation-aware stores can refuse if the object changed between the read and the status check.


##### `ObjectStore.apply`  (lines 329–337)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how an object kind creates or updates an object after core validation has passed. The store owns the real domain decision about whether the mutation is allowed.

**Data flow**: It receives context, name, the new validated spec, the old spec if there was one, and an expected generation. An implementation writes the change or raises a meaningful refusal.

**Call relations**: `ObjectVerbs._apply` calls this after parsing YAML, validating the spec model, journaling the intended change, and checking cross-agent targeting.


##### `ObjectStore.delete`  (lines 339–345)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines how an object kind deletes one object. Each kind implements the actual deletion in the storage it owns.

**Data flow**: It receives context, object name, and the generation observed before deletion. An implementation removes the object or raises if deletion is unsupported, forbidden, or stale.

**Call relations**: `ObjectVerbs._delete` calls this after reading the object and writing a change journal entry, so failed deletes can be reported cleanly.


##### `owner_emails`  (lines 364–379)

```
async def owner_emails(owners: Iterable[UUID | None]) -> dict[UUID | None, str]
```

**Purpose**: Fetches email addresses for a batch of member owners. This lets object lists show a human-friendly owner email without querying one owner at a time.

**Data flow**: It receives a collection of member IDs, ignoring `None` owners. It opens a workspace database transaction, selects matching member emails, and returns a dictionary from member ID to email.

**Call relations**: Object kinds that display ownership information can call this while building their rows. It uses the shared workspace transaction helper so the lookup happens in the workspace database.

*Call graph*: 2 external calls (select, workspace_tx).


##### `MemberOwnedObjects.list`  (lines 424–432)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists member-owned objects while enforcing the shared visibility rule. Callers only see rows that are shared, owned by them, or visible because they are admins.

**Data flow**: It reads whether the speaker is an admin and who the acting member is. It asks the subclass for owned rows, filters out invisible or unlisted rows, converts them to public `ObjectRow`s, and returns a paged result.

**Call relations**: Store implementations can inherit this instead of rewriting permission checks. After filtering, it hands the rows to `object_page` for search, sort, and pagination.

*Call graph*: calls 5 internal fn (_listed, _owned_rows, _visible, object_page, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.get`  (lines 434–445)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Reads one member-owned object only if the caller may see it. Invisible objects are treated like missing objects, which avoids leaking their existence.

**Data flow**: It looks up the object’s owner, checks visibility using the acting member and admin status, then asks the subclass for the full detail. If the owner carries a generation, it attaches that generation to the returned detail.

**Call relations**: This is the read gate used by member-owned stores. It relies on subclass hooks for the real data and uses `_visible` to keep access rules consistent with listing.

*Call graph*: calls 4 internal fn (_detail, _owner, _visible, speaker_is_admin); 1 external calls (replace).


##### `MemberOwnedObjects.status`  (lines 447–467)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reads live status for a member-owned object while protecting against stale or newly invisible data. It rechecks generation and visibility before and after the status read.

**Data flow**: It finds the owner, checks that the expected generation still matches, confirms visibility, asks the subclass for status, then checks the owner and visibility again. It returns status, `None`, or raises a not-found style error.

**Call relations**: `ObjectStore.status` implementations can be built from this gate. The double-check matters when live status is read separately from the spec, so status is not shown beside the wrong version of an object.

*Call graph*: calls 5 internal fn (_owner, _require_current_generation, _status, _visible, speaker_is_admin); 1 external calls (__init__).


##### `MemberOwnedObjects.apply`  (lines 469–495)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a member-owned object while enforcing ownership, admin, speaker, and generation rules. It centralizes the sensitive decision of who may change a row.

**Data flow**: It looks up the current owner, checks whether the object exists, whether the caller can see it, whether the generation is current, whether a live speaker is required, and whether the actor owns it or has admin permission. If all gates pass, it calls the subclass’s write hook.

**Call relations**: `ObjectVerbs._apply` reaches this through a kind store. This method performs common access control, while `_apply_owned` performs the kind-specific write.

*Call graph*: calls 7 internal fn (_admin_can_apply, _apply_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `MemberOwnedObjects.delete`  (lines 497–515)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a member-owned object only when the caller is allowed to delete it. It hides invisible rows as missing and prevents non-owners from deleting unless they are admins.

**Data flow**: It finds the owner, checks generation, confirms the row exists and is visible, enforces any live-speaker requirement, then checks ownership or admin status. If allowed, it calls the subclass deletion hook.

**Call relations**: `ObjectVerbs._delete` reaches this through a kind store. The shared gate runs before `_delete_owned`, so subclasses do not need to repeat the same permission checks.

*Call graph*: calls 6 internal fn (_delete_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `MemberOwnedObjects._owned`  (lines 517–521)

```
def _owned(self, owner: OwnerT, acting: UUID | None) -> bool
```

**Purpose**: Answers whether the acting member is the actual owner of a row. Admin-only rows with no member owner are deliberately owned by nobody.

**Data flow**: It receives an owner record and an acting member ID. It returns true only when the owner has a member ID and it exactly matches the acting member.

**Call relations**: `_visible`, `apply`, and `delete` use this to distinguish ownership from admin power or shared visibility.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 523–524)

```
def _visible(self, owner: OwnerT, acting: UUID | None, is_admin: bool) -> bool
```

**Purpose**: Answers whether a member-owned row can be seen by this caller. A row is visible if it is shared, owned by the acting member, or the caller is an admin.

**Data flow**: It receives the owner record, acting member ID, and admin flag. It combines those facts into one true-or-false visibility answer.

**Call relations**: The list, get, status, apply, and delete gates all use this same test, so a row does not become visible through one operation but hidden through another.

*Call graph*: calls 1 internal fn (_owned); called by 5 (apply, delete, get, list, status).


##### `MemberOwnedObjects._listed`  (lines 526–534)

```
def _listed(self, row: OwnedRow[OwnerT], query: ObjectListQuery) -> bool
```

**Purpose**: Lets a kind hide some otherwise visible rows from browsing while keeping them addressable by name. The default is to list every visible row.

**Data flow**: It receives an owned row and the list query. The base version always returns true; subclasses may override it to apply kind-specific browse rules.

**Call relations**: `MemberOwnedObjects.list` calls this after visibility has passed and before pagination. It is a small escape hatch for kinds with special list behavior.

*Call graph*: called by 1 (list).


##### `MemberOwnedObjects._admin_can_apply`  (lines 536–537)

```
def _admin_can_apply(self, old: SpecT, spec: SpecT) -> bool
```

**Purpose**: Lets a kind allow a limited admin update to a row the admin does not own. The default is no such special allowance.

**Data flow**: It receives the old and new specs. The base version returns false, meaning admin status alone does not permit that particular non-owner update path unless the subclass opts in.

**Call relations**: `MemberOwnedObjects.apply` calls this when deciding whether a visible but non-owned row may be changed by an admin.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._require_current_generation`  (lines 539–556)

```
def _require_current_generation(self, name: str, owner: OwnerT | None, expected_generation: UUID | None, action: str) -> None
```

**Purpose**: Prevents a write or status read from using an outdated view of a generated row. This is a safety fence against overwriting or showing data for the wrong replacement.

**Data flow**: It receives the object name, current owner, expected generation, and action label. If the current generation does not match what the caller read, it raises an error; otherwise it returns with no change.

**Call relations**: `status`, `apply`, and `delete` call this before sensitive work. It only fences kinds whose owners carry a generation.

*Call graph*: called by 3 (apply, delete, status).


##### `MemberOwnedObjects._owner`  (lines 558–559)

```
async def _owner(self, ctx: ToolContext, name: str) -> OwnerT | None
```

**Purpose**: Finds the owner record for a named object. This is the lightweight lookup used before deciding visibility or mutation rights.

**Data flow**: It asks the subclass for all owned rows visible to this store context and searches for the matching name. It returns that row’s owner or `None` if no row matches.

**Call relations**: The member-owned get, status, apply, and delete flows call this before moving to detail reads or writes. It depends on `_owned_rows`, which subclasses provide.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 561–562)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Abstract hook for subclasses to provide the rows and owner records for their kind. The base class cannot know where each extension stores its rows.

**Data flow**: It receives the tool context. A subclass implementation returns owned rows with names, summaries, owners, and list fields.

**Call relations**: `list` and `_owner` call this. Subclasses must implement it for the shared ownership gate to work.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._detail`  (lines 564–567)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Abstract hook for subclasses to read the full detail of one owned object. The shared gate calls it only after checking that the caller may see the row.

**Data flow**: It receives context, name, and the already found owner. A subclass returns an `ObjectDetail` or `None` if the row disappeared or cannot be read.

**Call relations**: `MemberOwnedObjects.get` calls this after `_owner` and `_visible`. It separates permission logic from kind-specific storage logic.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 569–572)

```
async def _status(self, ctx: ToolContext, name: str, owner: OwnerT) -> dict[str, JsonValue] | None
```

**Purpose**: Abstract hook for subclasses to read live status for one owned object. The base class surrounds it with generation and visibility checks.

**Data flow**: It receives context, name, and owner. A subclass returns a JSON-like status mapping or `None`.

**Call relations**: `MemberOwnedObjects.status` calls this between two safety checks. Subclasses provide the actual status data.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 574–582)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: OwnerT | None) -> None
```

**Purpose**: Abstract hook for subclasses to perform the actual create or update. The base class handles common permission gates before calling it.

**Data flow**: It receives context, object name, new spec, old spec if any, and the current owner if any. A subclass writes the change or raises a domain-specific refusal.

**Call relations**: `MemberOwnedObjects.apply` calls this only after validation, visibility, ownership, speaker, and generation rules have passed.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 584–585)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: OwnerT) -> None
```

**Purpose**: Abstract hook for subclasses to perform the actual deletion of an owned row. The base class checks that deletion is allowed first.

**Data flow**: It receives context, name, and owner. A subclass removes the row from its storage or raises if it cannot.

**Call relations**: `MemberOwnedObjects.delete` calls this at the end of the shared delete gate.

*Call graph*: called by 1 (delete).


##### `MemberReadable.member_detail`  (lines 607–614)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject | None
```

**Purpose**: Defines the opt-in method for object kinds that can be read by a signed-in member outside a live tool turn. This supports portal-style detail pages.

**Data flow**: It receives an extension context, object name, member ID, and admin flag. An implementation returns a `MemberObject` for a visible object or `None` when absent or hidden.

**Call relations**: Portal routes can call this on kinds that implement the protocol. Kinds that cannot safely answer outside a turn simply do not implement it.


##### `MemberListable.member_page`  (lines 622–629)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the opt-in method for object kinds that can list pages for a signed-in member outside a live tool turn. This supports portal indexes.

**Data flow**: It receives extension context, member ID, admin flag, and an object list query. An implementation returns a page of visible rows.

**Call relations**: It extends `MemberReadable`, so listable kinds also support details. Portal listing code can use this uniform method when a kind offers it.


##### `ConversationMemberListable.member_conversation_rows`  (lines 641–649)

```
async def member_conversation_rows(self, ext: 'ExtensionContext | None', conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Defines how a kind can list object grants connected to a conversation for one member. This is for conversation views that need to show related object access.

**Data flow**: It receives extension context, conversation ID, member ID, admin flag, and a limit. An implementation returns grant records containing names, generations, and whether content is visible.

**Call relations**: Kinds that expose conversation-related object grants implement this protocol so higher-level conversation pages can ask for those rows uniformly.


##### `MemberReadableObjects.member_page`  (lines 662–675)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Provides a portal listing implementation for member-owned, member-readable objects. It applies the same visibility and listing rules used during tool turns.

**Data flow**: It asks the subclass for member rows, filters them by visibility and `_listed`, converts them to `ObjectRow`s, and sends them through `object_page`. It returns the final page.

**Call relations**: This method connects the outside-a-turn portal path to the same paging helper used by `MemberOwnedObjects.list`, keeping user-facing lists consistent.

*Call graph*: calls 2 internal fn (_member_rows, object_page); 1 external calls (__init__).


##### `MemberReadableObjects.member_detail`  (lines 677–695)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject[SpecT] | None
```

**Purpose**: Provides a portal detail implementation for one member-owned object. It returns both the list row and full detail needed for a detail page.

**Data flow**: It asks the subclass for member rows, finds the requested name, checks visibility, then asks for the full member object detail. If found, it packages the row and detail into a `MemberObject`.

**Call relations**: Portal detail routes use this method on readable kinds. It delegates storage-specific work to `_member_rows` and `_member_object`.

*Call graph*: calls 2 internal fn (_member_object, _member_rows); 2 external calls (__init__, __init__).


##### `MemberReadableObjects._owned_rows`  (lines 697–698)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Bridges turn-time object listing to the member-readable row source. It lets one subclass method feed both portal and tool-turn paths.

**Data flow**: It receives a tool context and passes the bound extension context plus acting member ID to `_member_rows`. It returns the owned rows from that member-oriented hook.

**Call relations**: `MemberOwnedObjects.list` and `_owner` use this inherited method, so the same rows power both normal object tools and member portal views.

*Call graph*: calls 1 internal fn (_member_rows).


##### `MemberReadableObjects._detail`  (lines 700–703)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Bridges turn-time object detail reads to the member-readable detail source. It keeps portal detail behavior and tool detail behavior aligned.

**Data flow**: It receives context, name, and owner, then calls `_member_object` with the extension context and acting member ID. It returns the resulting `ObjectDetail` or `None`.

**Call relations**: `MemberOwnedObjects.get` calls this after visibility checks. Subclasses implement `_member_object`, not this bridge.

*Call graph*: calls 1 internal fn (_member_object).


##### `MemberReadableObjects._member_rows`  (lines 705–708)

```
async def _member_rows(self, ext: 'ExtensionContext | None', *, member_id: UUID | None) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Abstract hook for subclasses to produce rows for one member in portal-style reads. This is where the kind gathers names, summaries, owners, and list fields.

**Data flow**: It receives extension context and a member ID, which may be absent in some turn contexts. A subclass returns owned rows for that member-facing view.

**Call relations**: `member_page`, `member_detail`, and `_owned_rows` all call this, so it is the single row source for readable member-owned kinds.

*Call graph*: called by 3 (_owned_rows, member_detail, member_page).


##### `MemberReadableObjects._member_object`  (lines 710–718)

```
async def _member_object(self, ext: 'ExtensionContext | None', name: str, owner: OwnerT, *, member_id: UUID | None) -> ObjectDetail[SpecT] | None
```

**Purpose**: Abstract hook for subclasses to read the full object detail for one member-facing object. It is called only after the row has been found and visibility has been checked.

**Data flow**: It receives extension context, name, owner, and member ID. A subclass returns the full `ObjectDetail` or `None` if the object cannot be read.

**Call relations**: `member_detail` and `_detail` call this. Subclasses use it to supply storage-specific detail while inheriting the shared gates.

*Call graph*: called by 2 (_detail, member_detail).


##### `action_registry`  (lines 766–811)

```
def action_registry(bound: tuple[BoundAction, ...], kinds: Mapping[str, BoundKind]) -> dict[str, dict[str, BoundAction]]
```

**Purpose**: Validates and indexes all registered object actions at startup. It catches bad action declarations before the system starts serving requests.

**Data flow**: It receives bound action declarations and the registered object kinds. It checks names, target kind existence, duplicate actions, reserved input fields, tool declaration rules, and JSON-safe input models, then returns a kind-to-action registry.

**Call relations**: Deployment setup calls this after collecting manifests. It uses `_validate_spec_model` and the shared tool validator so bad extensions fail fast.

*Call graph*: calls 1 internal fn (_validate_spec_model); 2 external calls (fullmatch, validate_tool_declaration).


##### `object_registry`  (lines 814–837)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: Validates and indexes all registered object kinds at startup. It protects the global object namespace from collisions and unsafe spec models.

**Data flow**: It receives bound kind declarations. It checks kind name grammar, duplicate names, allowed agent-target verbs, and each spec model’s safety, then returns a mapping from kind name to bound kind.

**Call relations**: Startup code uses this as the boot gate before `ObjectVerbs` can dispatch requests. It calls `_validate_spec_model` for the deeper schema safety checks.

*Call graph*: calls 1 internal fn (_validate_spec_model); 1 external calls (fullmatch).


##### `_validate_spec_model`  (lines 840–859)

```
def _validate_spec_model(label: str, spec_model: type[BaseModel]) -> None
```

**Purpose**: Checks that a Pydantic spec model is safe to store, show, and echo back. In plain terms, it ensures object specs are strict, JSON-shaped, and do not contain secret fields.

**Data flow**: It walks the model and nested models, checks that unknown fields are forbidden, rejects secret string or byte fields, and asks Pydantic to produce a JSON schema. It raises a clear error if any check fails.

**Call relations**: `object_registry` uses this for object specs, and `action_registry` uses it for action inputs. It relies on `_reachable_models` and `_annotation_types` to inspect nested types.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 2 (action_registry, object_registry).


##### `_reachable_models`  (lines 862–876)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: Finds a Pydantic model and any nested Pydantic models referenced by its fields. This lets validation rules apply beyond the top-level spec.

**Data flow**: It starts with one model, follows field annotations that refer to other Pydantic models, avoids revisiting models, and returns all discovered models.

**Call relations**: `_validate_spec_model` calls this before checking strictness and secret-bearing fields. It uses `_annotation_types` to look through container and union type annotations.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 879–886)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: Breaks a type annotation into the concrete types inside it. For example, it can look through optional, list, or union-style annotations.

**Data flow**: It receives an annotation, reads its type arguments, recursively flattens nested arguments, and returns a tuple of discovered pieces.

**Call relations**: `_reachable_models` and `_validate_spec_model` use this to inspect field types deeply enough to find nested models and secret-bearing types.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectVerbs.tools`  (lines 970–1053)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: Builds the public tool definitions for object operations. These definitions tell the tool engine each tool’s name, description, input shape, handler, and whether it changes data.

**Data flow**: It uses the `ObjectVerbs` instance as the handler owner and creates six `ToolDef` objects: list, get, explain, apply, delete, and object action. It returns them as a tuple.

**Call relations**: Tool registration code calls this to expose object verbs to the runtime. Each returned tool points back to a corresponding method such as `_list` or `_apply`.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 1055–1092)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: Implements the `object_list` tool. It either lists available object kinds or lists instances of one kind with search, filters, ordering, paging, actions, and optional agent targeting.

**Data flow**: It receives tool context and list arguments. With no kind, it returns registered kinds; with a kind, it resolves the kind, checks any agent target, builds a query, calls the kind store’s list method, adds action templates and cursors, and returns JSON.

**Call relations**: This is called by the tool engine through the `ToolDef` from `tools`. It uses `_resolve`, `_target`, `_bound_ctx`, `_action_views`, and `_json_result` to assemble the response.

*Call graph*: calls 6 internal fn (_action_views, _bound_ctx, _granted_actions, _resolve, _target, _json_result); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._get`  (lines 1094–1141)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: Implements the `object_get` tool. It reads one object’s full spec, live status, links, timestamps, generation, and available instance actions.

**Data flow**: It resolves the kind, binds the extension context, checks any agent target, reads the object detail, asks the store for status using the observed generation, renders links and actions, and returns YAML text.

**Call relations**: The tool engine calls this for object reads. It depends on the kind store for existence, visibility, and status, and on `_action_views` to publish only actions the current turn is granted.

*Call graph*: calls 4 internal fn (_action_views, _bound_ctx, _resolve, _target); 5 external calls (__init__, __init__, __init__, object_agent, safe_dump).


##### `ObjectVerbs._explain`  (lines 1143–1158)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: Implements the `object_explain` tool. It tells a caller how to author and use a kind before creating or editing an object.

**Data flow**: It resolves the kind and returns its description, guidance, supported agent-target verbs, object name rule, JSON schema for the spec, and available collection and instance actions.

**Call relations**: The tool engine calls this when a caller needs documentation for a kind. It uses `_resolve`, `_action_views`, and `_json_result`.

*Call graph*: calls 3 internal fn (_action_views, _resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 1160–1225)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: Implements the `object_apply` tool for creating or updating objects from a YAML manifest. It validates the envelope and spec before any store write happens.

**Data flow**: It parses the manifest, resolves the kind, checks agent targeting, validates the object name and spec model, reads any existing object, journals the intended create or update, calls the store’s apply method, withdraws the journal entry if the store refuses, and returns a created-or-updated result.

**Call relations**: This is the main write path for objects. It combines `_parse_envelope`, `_target`, `_journal_object_change`, the kind store’s `apply`, `_withdraw_object_change`, and `_json_result`.

*Call graph*: calls 7 internal fn (_bound_ctx, _resolve, _target, _journal_object_change, _json_result, _parse_envelope, _withdraw_object_change); 4 external calls (__init__, __init__, validate_object_name, object_agent).


##### `ObjectVerbs._delete`  (lines 1227–1262)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: Implements the `object_delete` tool. It deletes one object and returns the deleted spec when the spec is allowed to be visible.

**Data flow**: It resolves the kind, checks any agent target, reads the old object, journals the intended deletion, calls the store’s delete method with the observed generation, withdraws the journal entry on failure, and returns a JSON deletion result.

**Call relations**: The tool engine calls this for object deletions. Like `_apply`, it records the intended change before mutating so failures and retries behave predictably.

*Call graph*: calls 6 internal fn (_bound_ctx, _resolve, _target, _journal_object_change, _json_result, _withdraw_object_change); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._object_action`  (lines 1264–1265)

```
async def _object_action(self, ctx: ToolContext, args: ObjectActionInput) -> ToolResult
```

**Purpose**: Exists only as the declared handler for the generic `object_action` tool. Normal action dispatch is performed earlier by the engine, not by this method.

**Data flow**: If it is ever called, it immediately raises a runtime error. It produces no normal result and changes nothing.

**Call relations**: `tools` attaches this as the handler so the tool schema can be exposed. The comment and error make clear that real object action dispatch uses `action_target` and engine-level routing instead.


##### `ObjectVerbs._granted_actions`  (lines 1267–1275)

```
def _granted_actions(self, ctx: ToolContext, kind: str) -> dict[str, BoundAction]
```

**Purpose**: Finds which registered actions for a kind are actually granted to the current tool context. This prevents the system from advertising actions the current turn cannot invoke.

**Data flow**: It looks up actions for the kind, compares each action’s canonical ID to `ctx.granted_actions`, and returns only the allowed actions.

**Call relations**: `_list` uses it to mark kinds that have available actions, and `_action_views` uses it to build the detailed action templates shown in list, get, and explain responses.

*Call graph*: called by 2 (_action_views, _list).


##### `ObjectVerbs._action_views`  (lines 1277–1307)

```
def _action_views(self, ctx: ToolContext, kind: str, binding: str, *, name: str | None=None, agent: str | None=None, generation: UUID | None=None) -> list[JsonValue]
```

**Purpose**: Builds the action templates shown to callers for a kind or object instance. These templates pre-fill the target fields so the caller can invoke the action correctly.

**Data flow**: It starts from granted actions for a kind, filters them by collection versus instance binding, optional fixed name, and agent-target support, then renders each allowed action view as JSON-like data.

**Call relations**: `_list`, `_get`, and `_explain` call this when publishing available actions. It delegates final view formatting to `action_view`.

*Call graph*: calls 1 internal fn (_granted_actions); called by 3 (_explain, _get, _list); 1 external calls (action_view).


##### `ObjectVerbs.action_target`  (lines 1309–1353)

```
async def action_target(self, ctx: ToolContext, action: ToolDef, wire: ObjectActionInput) -> ObjectActionTarget
```

**Purpose**: Resolves what an object action will act on before the action handler runs. It checks cross-agent targeting and, for instance actions, confirms the object exists through the owning kind’s store.

**Data flow**: It receives context, the action definition, and the wire input. It validates the action binding, resolves any agent target, returns a collection target directly, or reads the instance detail and status before returning an `ObjectActionTarget` with live and expected generation information.

**Call relations**: The engine uses this during object action dispatch. It deliberately asks the kind owner’s store, not the action contributor, so visibility and existence remain controlled by the object kind.

*Call graph*: calls 3 internal fn (_agent_gate, _bound_ctx, _resolve); 3 external calls (__init__, __init__, object_agent).


##### `ObjectVerbs._resolve`  (lines 1355–1360)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: Looks up a registered object kind by name and produces a helpful error if it is unknown. This is the common first step for kind-specific operations.

**Data flow**: It receives a kind name, checks the registry, and returns the matching `BoundKind`. If missing, it raises `UnknownKind` with the list of registered kinds.

**Call relations**: List, get, explain, apply, delete, and action targeting all call this before touching a kind’s store.

*Call graph*: called by 6 (_apply, _delete, _explain, _get, _list, action_target); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 1362–1363)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: Rebinds a tool context to the extension context that owns a kind. This lets a store run with its own extension’s workspace-scoped capabilities.

**Data flow**: It receives the current `ToolContext` and a bound kind. It returns a copy of the context with `ext` replaced by the kind’s bound extension context.

**Call relations**: `_list`, `_get`, `_apply`, `_delete`, and `action_target` use this before calling store methods. It is the handoff point from core dispatch to extension-owned storage code.

*Call graph*: called by 5 (_apply, _delete, _get, _list, action_target); 1 external calls (replace).


##### `ObjectVerbs._target`  (lines 1365–1378)

```
async def _target(self, ctx: ToolContext, bound: BoundKind, name: str, verbs: frozenset[AgentTargetVerb]) -> ObjectAgent | None
```

**Purpose**: Checks whether a CRUD verb may target another agent and resolves that agent if requested. It keeps cross-agent access opt-in per object kind and per verb.

**Data flow**: It receives context, bound kind, requested agent name, and the verbs being attempted. If no agent name is given, it returns `None`; otherwise it verifies the kind allows targeting for those verbs and delegates to `_agent_gate`.

**Call relations**: The list, get, apply, and delete handlers call this before running a store operation under an agent scope.

*Call graph*: calls 1 internal fn (_agent_gate); called by 4 (_apply, _delete, _get, _list).


##### `ObjectVerbs._agent_gate`  (lines 1380–1429)

```
async def _agent_gate(self, ctx: ToolContext, name: str) -> ObjectAgent | None
```

**Purpose**: Enforces the rules for targeting another agent’s object namespace. Only the workspace main agent, on a live member-requested call, may target another visible agent.

**Data flow**: It reads the current agent from the database, compares the requested name, checks main-agent and subagent restrictions, requires a speaker when crossing agents, checks admin or visibility rules, and returns an `ObjectAgent` for the target or `None` for the current agent.

**Call relations**: `_target` and `action_target` call this whenever an agent name is supplied. It uses the workspace database and member admin check to make the cross-agent boundary explicit.

*Call graph*: called by 2 (_target, action_target); 6 external calls (__init__, __init__, or_, select, workspace_tx, member_is_admin).


##### `_journal_object_change`  (lines 1432–1486)

```
async def _journal_object_change(ctx: ToolContext, kind: str, name: str, verb: Literal['create', 'update', 'delete'], before: BaseModel | None, after: BaseModel | None, agent_id: UUID) -> UUID | None
```

**Purpose**: Records an intended object create, update, or delete before the actual mutation happens. This makes object writes auditable and helps retries avoid duplicating or misreporting changes.

**Data flow**: It builds a change ID from the idempotency key when present, chooses a caller label, checks whether the journal row already exists, and inserts the before and after specs into `object_change`. It returns the inserted change ID, or `None` if the row already existed.

**Call relations**: `ObjectVerbs._apply` and `_delete` call this before store writes. If the following store operation fails and this attempt inserted the journal row, the caller removes it with `_withdraw_object_change`.

*Call graph*: called by 2 (_apply, _delete); 7 external calls (dumps, model_dump, insert, select, workspace_tx, uuid4, uuid5).


##### `_withdraw_object_change`  (lines 1489–1496)

```
async def _withdraw_object_change(ctx: ToolContext, change_id: UUID) -> None
```

**Purpose**: Removes a journal entry that was written for a mutation that did not actually complete. This keeps the change log from claiming a failed write happened.

**Data flow**: It receives the tool context and change ID, opens a workspace transaction, and deletes the matching row for the current workspace.

**Call relations**: `ObjectVerbs._apply` and `_delete` call this only after a store refusal or exception, and only for journal rows created by the current attempt.

*Call graph*: called by 2 (_apply, _delete); 2 external calls (delete, workspace_tx).


##### `_parse_envelope`  (lines 1499–1525)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object], UUID | None]
```

**Purpose**: Parses and validates the YAML wrapper used by `object_apply`. It makes sure the request contains exactly the object kind, name, spec, and optional generation.

**Data flow**: It checks byte size, safely loads YAML, requires a mapping with the right keys, verifies kind and name are strings and spec is a mapping, parses generation as a UUID when present, and returns the four parsed pieces.

**Call relations**: `ObjectVerbs._apply` calls this before resolving the kind or validating the spec. It raises `InvalidManifest` with user-facing explanations for malformed input.

*Call graph*: called by 1 (_apply); 3 external calls (__init__, UUID, safe_load).


##### `_json_result`  (lines 1528–1529)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: Wraps a Python mapping as a JSON text tool result. It is a small helper for object verbs that return compact JSON responses.

**Data flow**: It receives a payload mapping, serializes it with `json.dumps`, wraps the text in `TextContent`, and returns a `ToolResult`.

**Call relations**: `_list`, `_explain`, `_apply`, and `_delete` use this to return consistent JSON-shaped tool output.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### `core/src/ufo/runtime/kinds/governance.py`

`domain_logic` · `proposal creation and approval`

This file is a safety gate for changing an agent's prompt, which is the text that guides how the agent behaves. Instead of letting code edit the prompt immediately, it creates a proposal that says, in effect: “change the prompt from this exact old version to this new version.” The old version is identified by a digest, which is a short fingerprint made from the prompt text.

The important idea is similar to signing for a package only if the label still matches what you expected. When a proposal is opened, the file stores the agent, the proposed new prompt, who proposed it, and the fingerprint of the prompt the proposer believed was current. Later, when someone approves the proposal, the code checks the agent's prompt again. If the prompt fingerprint still matches, the new prompt is written and the proposal is marked approved. If the prompt has changed in the meantime, the proposal is rejected instead of blindly overwriting newer work.

All database work happens inside a workspace transaction, meaning the checks and writes are grouped together for one workspace. The approval path also locks the agent row while checking it, so two approvals cannot safely race past each other and both write conflicting prompt changes.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: Creates a stable fingerprint for a prompt. This lets the system compare prompt versions without storing or comparing long text everywhere.

**Data flow**: It receives a prompt string, turns it into bytes, runs it through SHA-256, a standard hashing method that produces a fixed-size fingerprint, and returns that fingerprint as text. It does not change anything outside itself.

**Call relations**: When a change is proposed, Governance.propose_change uses this to record the fingerprint of the proposed new prompt. When a proposal is approved, Governance.approve_proposal uses it again to check whether the agent's current prompt still matches the version the proposal was based on.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Opens a new prompt-change proposal for an agent in this workspace. It records the requested change but does not apply it yet.

**Data flow**: It receives an AgentChange containing the target agent, the expected old prompt fingerprint, and the new prompt text. It creates a new proposal ID, checks in the database that the agent exists in this workspace, computes the fingerprint of the new prompt, and inserts a pending proposal row with the prompt stored in the proposal body. It returns a ProposalRef containing the new proposal ID. If the agent is not found in the workspace, it raises an error instead.

**Call relations**: This is the first half of the governance flow. Code that wants to change an agent calls this instead of editing the agent directly. It relies on workspace_tx to group the database work safely, uses prompt_digest to fingerprint the new prompt, and returns a ProposalRef so later code can ask Governance.approve_proposal to approve that exact proposal.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: Approves a pending proposal only if the agent's prompt has not changed since the proposal was made. If the prompt has changed, it rejects the proposal rather than overwriting newer state.

**Data flow**: It receives a proposal ID and looks up that proposal inside the current workspace. It verifies that the proposal exists and is still pending. It then reads and locks the agent's current prompt, computes its fingerprint, and compares it with the proposal's recorded starting fingerprint. If they differ, it marks the proposal rejected and logs that outcome. If they match, it writes the proposed prompt into the agent row, marks the proposal approved, and logs the approval. Errors are raised for missing proposals or proposals that are no longer pending.

**Call relations**: This is the second half of the governance flow. It is called when a pending proposal is being accepted. It uses prompt_digest to perform the “is this still the same prompt?” check, uses workspace_tx so the database reads and writes happen as one unit, updates the proposal and agent tables as needed, and sends a log message through ufo.harness.o11y.log so the approval or rejection is visible to observability tools.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).


### `core/src/ufo/runtime/object_scope.py`

`domain_logic` · `request handling`

Some object actions may run on behalf of a specific agent, and that agent may be different from the general agent currently running the task. This file provides a small “name tag” system for that situation. Like putting a temporary badge on a worker before they enter a room, the runtime can temporarily mark the current task with the object-selected agent, then remove that mark when the action is done.

The two model classes describe the resolved target of an object action. ObjectAgent stores the exact agent identity chosen for the action. ObjectActionTarget describes what the action is acting on: the object kind, optional instance name, optional agent target, and generation IDs used to understand which version of an object was seen or expected. These models are frozen, meaning they are meant to be read-only once created.

The active object agent is stored in a ContextVar, which is Python’s way of keeping data local to the current async task or execution context. That matters because many actions can run at the same time; one action’s selected agent must not leak into another action. The object_agent context manager sets this temporary target, and object_agent_id reads it. If no object-specific agent is set, object_agent_id falls back to the normal current agent from the broader agent scope.

#### Function details

##### `object_agent`  (lines 44–52)

```
def object_agent(target: ObjectAgent | None) -> Iterator[None]
```

**Purpose**: Temporarily marks the current task as acting for a specific object-selected agent. Code uses it around an object action so any deeper code can ask “which agent should this object action use?” without carrying that value through every call.

**Data flow**: It receives an ObjectAgent or None. If the input is None, it leaves the current task’s object-agent setting unchanged and simply runs the enclosed block. If an ObjectAgent is provided, it stores that agent in the task-local variable, runs the enclosed block, and then restores the previous value afterward so the temporary choice does not leak into later work.

**Call relations**: The call graph excerpt shows no direct caller inside this file. In the larger runtime flow, object dispatch code is expected to enter this context before running a bound object handler, so later calls such as object_agent_id can see the temporary object-specific agent.


##### `object_agent_id`  (lines 55–57)

```
def object_agent_id() -> UUID
```

**Purpose**: Returns the agent ID that should be used for the current object action. It prefers the object-specific agent set by object_agent, and if none is set it uses the normal current agent.

**Data flow**: It reads the task-local object-agent target. If that target exists, it returns the target’s UUID. If no target exists, it asks the broader agent scope for the current agent and returns that agent’s ID.

**Call relations**: This function is the read side of the temporary scope created by object_agent. When there is no object-specific target, it hands off to ufo.runtime.agent_scope.agent_current to get the regular current agent, so object-related code still has a valid agent identity even outside a special object dispatch scope.

*Call graph*: 1 external calls (agent_current).


### External monitors
The monitor tool lets agents establish one-time watches on external state after validating the initial command output.

### `extensions/monitors/ufo_ext_monitors/monitor_tool.py`

`domain_logic` · `request handling, when an agent calls the monitor tool during a turn`

This file lets an agent say, in effect, “check this command every few minutes, and wake me once something changes or time runs out.” The first check happens right away inside the current turn. That matters because a bad command fails while the agent is still present, instead of silently creating a broken background watch. The first successful output becomes the baseline, like taking a “before” photo so later checks can spot a difference.

The `MonitorInput` model describes exactly what the agent must provide: a short name, the shell command to run, how often to check, the deadline, what message to show now, why the watch exists, what to do when it fires, and optional extra state. The file refuses invalid or unsafe situations: no extension context, too many active monitors in the same conversation, a duplicate name, or a command that exits with an error.

If everything is acceptable, `monitor` records the watch in `MonitorStore`. It saves who created it, the command, the schedule, the deadline, the baseline output, and instructions for the future turn. The returned result tells the agent to reply with the provided `ai_response` and end the turn. Later, another part of the monitor system will run the saved command until output changes, repeated failures happen, or the deadline arrives.

#### Function details

##### `_require_ext`  (lines 76–79)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This is a small safety check that makes sure the monitor tool has the extension context it needs. The extension context is the connection to extension-owned services and storage; without it, the monitor cannot be saved.

**Data flow**: It receives an optional extension context. If the context is present, it passes it through unchanged. If it is missing, it stops the operation by raising an error, because arming a monitor without the monitor extension would be impossible.

**Call relations**: The main `monitor` function calls this before creating a `MonitorStore`. It acts like checking that the key to the storage room exists before trying to put a new watch record inside.

*Call graph*: called by 1 (monitor).


##### `_refusal`  (lines 82–83)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: This builds a standard error result for cases where the tool refuses to arm a monitor. It gives the agent a clear text explanation instead of creating a watch that should not exist.

**Data flow**: It receives a plain text reason. It wraps that text in a `TextContent` message, then places it inside a `ToolResult` marked as an error. The output is a complete tool response saying the request failed.

**Call relations**: The main `monitor` function uses this whenever it needs to say no: too many monitors are already armed, the name is already taken, or the first probe command failed. This keeps all refusal responses shaped the same way.

*Call graph*: called by 1 (monitor); 2 external calls (__init__, __init__).


##### `monitor`  (lines 86–133)

```
async def monitor(ctx: ToolContext, args: MonitorInput) -> ToolResult
```

**Purpose**: This is the actual tool action that arms a monitor. It checks the request, runs the probe command once, saves the watch if the first run succeeds, and returns instructions telling the agent to end the turn.

**Data flow**: It receives the current tool context and the validated monitor request. It reads the conversation and agent information from the context, checks existing armed monitors in storage, builds the full monitor name, and runs the requested shell command in the conversation sandbox. If the command fails or the request would exceed limits, it returns an error result and saves nothing. If the command succeeds, it records the command, timing, deadline, baseline output, reason, next steps, and metadata in `MonitorStore`. It then returns a text result containing the monitor directive plus a JSON summary with the armed name, baseline, deadline, interval, reason, and next steps.

**Call relations**: This function is registered as the handler for the `MONITOR_TOOL`, so it runs when the agent calls the monitor action. It relies on `_require_ext` to get usable extension storage, `_refusal` to produce clear failures, `MonitorStore` to read and save monitor rows, the sandbox `bash` call to test the probe command, and date/time helpers to set the first future probe and the deadline.

*Call graph*: calls 2 internal fn (_refusal, _require_ext); 9 external calls (__init__, __init__, __init__, now, timedelta, dumps, capped, qualified_name, stderr_tail).


### Skill extension package
The skill creation package marker introduces runtime-created, agent-owned skills as an extension area.

### `extensions/skill_create/ufo_ext_skill_create/__init__.py`

`other` · `cross-cutting`

This is the package entry file for the skill-creation extension. In Python, an `__init__.py` file tells the language that a folder should be treated as an importable package, like putting a label on a drawer so other parts of the program can find what is inside. Here, the file does not define any functions or classes. Its only content is a short docstring, which says this package is about skills authored by an agent and skills available during each turn of execution. In plain terms, this package is meant to hold code related to letting an agent create reusable abilities and then use those abilities while it is running. Without this file, depending on the Python setup, other code might not reliably recognize this directory as a package, and the package would also lack this small built-in description of its purpose.


### Conversation todos
The built-in todo extension provides visible conversation checklists that agents can create and update as work progresses.

### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `request handling and conversation UI refresh`

This file gives the agent a small, durable checklist, like a notepad that stays attached to one conversation. That matters because multi-step requests can otherwise become hard to follow: the agent may lose track of what it has done, and the user interface would have no clear progress display.

The file defines two tools. The first, `update_todo_list`, creates or replaces the whole checklist with a title and tasks. The second, `update_todo_status`, changes the status of specific tasks, using human-friendly 1-based task numbers. The checklist is saved in the extension's scoped store, which is a persistent storage area belonging to this extension. The key includes the conversation ID, so each conversation gets its own board.

The file also exposes the checklist to the conversation UI through a `ConversationSlotProvider`. That provider can quickly summarize how many tasks exist and can read a display-safe version of the board. Long titles, long descriptions, and very large task lists are trimmed before being shown, and the payload records whether trimming happened.

Finally, `manifest()` packages everything: the tool definitions, prompt instructions, and UI slot. Without this file, the agent would not have the todo tools or the conversation-level task display.

#### Function details

##### `_require_ext`  (lines 87–90)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a tool call has access to the todos extension context. That context is needed because it contains the extension's persistent store.

**Data flow**: It receives a tool context. If the context includes an extension object, it returns that object. If not, it stops immediately by raising an error, because the todo tools cannot save or read anything without it.

**Call relations**: When `update_todo_list` or `update_todo_status` starts, each asks `_require_ext` for the extension context before touching stored checklist data. This keeps the main tool functions from silently running in an unusable state.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 93–94)

```
def _board_key(conversation_id: UUID) -> str
```

**Purpose**: This helper builds the storage key for one conversation's todo board. It keeps todo lists from different conversations separate.

**Data flow**: It receives a conversation ID. It adds the fixed `todo/` prefix in front of that ID and returns the resulting string, which is used as the lookup name in storage.

**Call relations**: `update_todo_list`, `update_todo_status`, `_summarize_tasks`, and `_read_tasks` all call this before reading or writing the board. It is the shared rule that makes every part of the extension look in the same place for a conversation's checklist.

*Call graph*: called by 4 (_read_tasks, _summarize_tasks, update_todo_list, update_todo_status).


##### `_board_result`  (lines 97–98)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This helper turns the current todo board into the standard result format returned by a tool. It ensures the model sees the latest checklist after every change.

**Data flow**: It receives a `TodoBoard`. It converts the board into JSON text, wraps that text in a `TextContent` item, then wraps that in a `ToolResult`. The returned result contains the board as readable structured text.

**Call relations**: After `update_todo_list` creates a board, and after `update_todo_status` edits one, both call `_board_result` to send the updated state back to the caller in the same format.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 101–103)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This helper reads a saved todo board from the extension store and validates its shape. It is the safe doorway from raw stored data back into a usable checklist object.

**Data flow**: It receives the extension context and a storage key. It asks the store for the saved value. If nothing is found, it returns `None`; otherwise it converts the stored data into a `TodoBoard` object.

**Call relations**: `update_todo_status` uses `_read_board` before applying status changes, because it needs an existing list. `_summarize_tasks` and `_read_tasks` also use it when the UI asks what tasks exist for the current conversation.

*Call graph*: called by 3 (_read_tasks, _summarize_tasks, update_todo_status).


##### `update_todo_list`  (lines 106–110)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates or completely replaces the conversation's todo checklist. The agent uses it near the start of a multi-step request so progress can be tracked visibly.

**Data flow**: It receives the tool context and an input object containing a title and a complete task list. It checks for the extension context, builds a `TodoBoard`, stores that board under the current conversation's key, and returns the newly saved board as a tool result.

**Call relations**: This is one of the public tools registered by `manifest()`. During a tool call, it relies on `_require_ext` for storage access, `_board_key` to choose the correct conversation-specific storage location, and `_board_result` to return the fresh checklist to the model.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 113–124)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that changes the status of existing tasks, such as marking a task as `in_progress` or `completed`. It prevents status updates before a list exists and rejects task numbers outside the list.

**Data flow**: It receives the tool context and one or more status updates. It finds the current conversation's board in storage, checks that the board exists and has tasks, checks each requested 1-based task index, changes the matching task statuses, saves the updated board, and returns the current board as the result.

**Call relations**: This public tool is registered by `manifest()`. It calls `_require_ext` to get storage, `_board_key` to find the right conversation's board, `_read_board` to load it, and `_board_result` to return the updated checklist after saving.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `_summarize_tasks`  (lines 127–129)

```
async def _summarize_tasks(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This helper gives a lightweight summary of the current conversation's checklist for the conversation slot system. It answers only how many tasks exist, without sending the full list.

**Data flow**: It receives a conversation slot context, builds the storage key for that conversation, and reads the board. If there is no board, it returns `None`; otherwise it returns the number of tasks on the board.

**Call relations**: The `TASKS_SLOT` provider uses `_summarize_tasks` when the system needs a quick summary for the tasks area. It uses `_board_key` and `_read_board`, the same storage path used by the tools, so the summary reflects the saved checklist.

*Call graph*: calls 2 internal fn (_board_key, _read_board).


##### `_read_tasks`  (lines 132–162)

```
async def _read_tasks(ctx: ConversationSlotContext) -> TasksSlotPayload
```

**Purpose**: This helper prepares the todo board for display in the conversation UI. It returns a clean payload with task counts, completed counts, and trimmed text when needed.

**Data flow**: It receives a conversation slot context, reads the board for that conversation, and returns an empty task payload if no board exists. If a board exists, it copies up to the allowed number of tasks, trims long titles and descriptions to the allowed limits, counts completed tasks, notes whether anything was truncated, and returns a `TasksSlotPayload`.

**Call relations**: The `TASKS_SLOT` provider calls `_read_tasks` when the UI or conversation surface needs the full task display. It uses `_board_key` and `_read_board` to load the same stored board that the todo tools update.

*Call graph*: calls 2 internal fn (_board_key, _read_board); 2 external calls (__init__, __init__).


##### `manifest`  (lines 175–197)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host system what the todos extension offers. It registers the two tools, the prompt instructions, and the conversation task display slot.

**Data flow**: It takes no input. It builds a `Manifest` containing the extension name and version, two `ToolDef` entries connected to `update_todo_list` and `update_todo_status`, one prompt section read from the markdown file, and the tasks conversation slot. It returns that manifest to the extension loader.

**Call relations**: The extension system calls `manifest()` when it loads this file. The returned manifest is what makes the todo tools callable and what connects `_summarize_tasks` and `_read_tasks` to the conversation UI through `TASKS_SLOT`.

*Call graph*: 3 external calls (__init__, __init__, __init__).
