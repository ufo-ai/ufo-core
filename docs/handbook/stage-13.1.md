# Planning, delegation, automations, and monitors  `stage-13.1`

This stage is shared support for work that lasts longer than one chat turn. It gives the system a notebook, an alarm clock, and a watchman. The objectives tools and store let agents write plans, record attempts, blockers, and evidence, and delegate separate steps to subagents; the store rebuilds progress from saved facts, not from a worker’s memory. Scheduled task tools expose repeating jobs and “pause until later” workflows; cron code validates calendar-like rules and finds the next due time; schedules stores ownership and due times and lets background runners safely claim work; pauses does the same for sleeping conversations; scheduled_fire defines one common ID text for a particular scheduled run. The monitor package marker just makes the extension importable. Its monitor tool starts a one-shot watch by running a shell command now, saving it only if this first probe succeeds; monitor_kind shows armed watches as stoppable objects; monitors stores them and decides when probes run, fire, fail, or get skipped. Sweep uses these pieces to make private daily briefs: it gathers context, sends scout subagents, then schedules drafting after each member’s morning starts.

## Files in this stage

### Scheduled workflows
These files define user-facing scheduled tasks and workflow pauses, plus the shared identifiers, cron rules, and durable storage needed to run them safely later.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `request handling and scheduled-work setup`

This file is the public doorway for the scheduled-tasks extension. A scheduled task is treated like a workspace object: it has a name, a schedule, a prompt to run, an owner, visibility rules, and create/update/delete behavior. Without this file, users could not create recurring agent work through the normal object commands, and the system would not know how to show, protect, or modify those tasks.

The main model, ScheduledTaskSpec, describes what a task can contain: a cron schedule, prompt, description, expiry time, and paused flag. A cron schedule is a compact text pattern such as “run at 9 every weekday.” ScheduledTaskObjects is the adapter between the generic object system and the schedule database. It lists tasks, builds the rows users see, checks who may read or change them, validates schedules, creates new tasks, updates existing ones, and cancels tasks.

The file also defines pause_and_wait. This is not a normal workspace object because it is not something a member browses or edits. It is more like setting an alarm while leaving a note on the desk. The tool records a pause row with the conversation, agent, wake-up time, and instructions for the resumed turn, then tells the agent to reply and stop until either a member speaks or the timer fires.

#### Function details

##### `ScheduledTaskSpec.validate_utc_expiry`  (lines 103–106)

```
def validate_utc_expiry(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Checks that a task expiry time, if supplied, is written in UTC, the shared time standard used by the scheduler. This prevents a task from expiring at the wrong moment because of a local time zone.

**Data flow**: It receives the proposed expires_at value from the task specification. If there is no value, it passes it through. If there is a value, it checks that the timestamp has UTC time-zone information; otherwise it raises a validation error before the task can be saved.

**Call relations**: This runs automatically while ScheduledTaskSpec is being built or parsed by the data-validation layer. It protects later scheduling code from having to guess what time zone an expiry belongs to.

*Call graph*: 2 external calls (utcoffset, timedelta).


##### `_require_scheduler`  (lines 127–128)

```
def _require_scheduler(ext: ExtensionContext | None) -> ScheduleStore
```

**Purpose**: Turns the extension context into a ScheduleStore, which is the storage-facing object used to read and write scheduled tasks. It is a small safety gate that refuses to continue if the scheduled-tasks extension context is missing.

**Data flow**: It receives an optional ExtensionContext. It first requires that the context really exists, then wraps it in a ScheduleStore and returns that store to the caller.

**Call relations**: Most task operations call this right before touching schedule data: listing rows, showing conversation grants, reading status, creating or updating a task, deleting a task, or finding a task by name. It hands them the storage tool they need.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_apply_owned, _delete_owned, _find, _rows, _status, member_conversation_rows); 1 external calls (__init__).


##### `_require_ext`  (lines 131–134)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Ensures code that depends on this extension actually has an ExtensionContext to work with. If not, it fails loudly instead of letting later database or conversation calls break in confusing ways.

**Data flow**: It receives an optional ExtensionContext. If it is present, the same context comes back. If it is missing, the function raises a RuntimeError explaining that scheduled tasks require this context.

**Call relations**: _require_scheduler uses this before creating a ScheduleStore, and pause_and_wait uses it before writing a pause. It is the shared guardrail at the edge of extension-specific work.

*Call graph*: called by 2 (_require_scheduler, pause_and_wait).


##### `_summary`  (lines 137–138)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: Builds the short one-line label shown for a scheduled task in lists. It combines the schedule with either the description or, if there is no description, the prompt.

**Data flow**: It receives a ScheduledTask. It reads the task schedule, description, and prompt, joins them into a readable string, trims the result to the configured maximum length, and returns that text.

**Call relations**: ScheduledTaskObjects._rows calls this while building list entries. It provides the human-friendly summary that appears when the viewer is allowed to see the task content.

*Call graph*: called by 1 (_rows).


##### `_owner`  (lines 141–149)

```
def _owner(listed: ListedTask) -> GeneratedObjectOwner
```

**Purpose**: Describes who owns a listed task and how broadly it is shared. The ownership record lets the generic object system apply the same visibility and editing rules it uses for other objects.

**Data flow**: It receives a ListedTask, which includes the task and the audience of the conversation it reports into. It takes the creator member id, converts the conversation audience into a shared/not-shared marker, attaches the task id as the generation, and returns a GeneratedObjectOwner.

**Call relations**: Rows and conversation grants use this when they need to decide whether a member or admin can see a task. It bridges scheduled-task facts into the generic object permission machinery.

*Call graph*: called by 2 (_rows, member_conversation_rows); 2 external calls (__init__, subject_shared).


##### `ScheduledTaskObjects._admin_can_apply`  (lines 170–173)

```
def _admin_can_apply(self, _old: ScheduledTaskSpec, spec: ScheduledTaskSpec) -> bool
```

**Purpose**: Decides which updates an admin is allowed to make to someone else’s scheduled task. Admins may adjust operation details such as schedule, expiry, or pause state, but not rewrite the creator’s prompt or description.

**Data flow**: It receives the old spec and the proposed new spec. It looks at which fields the update is trying to set. If the update includes prompt or description, it returns false for admin-only permission; otherwise it returns true.

**Call relations**: The object framework consults this as part of applying updates through ScheduledTaskObjects. It supports the file’s core rule: cadence control can be administrative, but content stays with the creator.


##### `ScheduledTaskObjects.member_page`  (lines 175–197)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds a page of scheduled-task rows for a member, with special support for filtering by conversation. This lets a user see tasks tied to a particular conversation without exposing tasks they should not see.

**Data flow**: It receives the extension context, member identity, admin flag, and list query. If the query does not contain a conversation filter, it falls back to the base object listing behavior. If it does, it parses the conversation id, gathers rows for that conversation, removes rows not visible to this viewer, and returns a paged result.

**Call relations**: This is called by the generic object-listing path when a member asks for scheduled_task objects. It relies on _rows to assemble the raw task rows, then packages the visible ones into the standard object page format.

*Call graph*: calls 1 internal fn (_rows); 3 external calls (__init__, object_page, UUID).


##### `ScheduledTaskObjects.member_conversation_rows`  (lines 199–219)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Returns the scheduled tasks that should appear inside a conversation’s object area. It shows that a task reports into that conversation and whether its content is visible to the current member.

**Data flow**: It receives a conversation id, member identity, admin flag, and limit. It asks the schedule store for tasks reporting to that conversation, checks visibility, marks whether content can be shown, and returns compact conversation grants.

**Call relations**: Conversation views call this when they need to populate the scheduled-task slot for a conversation. It uses _owner for ownership/visibility facts and task_content_visible to decide whether prompt-like content can be displayed.

*Call graph*: calls 2 internal fn (_owner, _require_scheduler); 2 external calls (__init__, task_content_visible).


##### `ScheduledTaskObjects._member_rows`  (lines 221–224)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Provides the full set of task rows used when listing scheduled tasks for a member-level object view. It delegates the actual row construction to the shared row builder.

**Data flow**: It receives the extension context and an optional member id. It asks _rows for all reported tasks, with no prompt truncation limit, and returns the resulting owned rows.

**Call relations**: The base MemberReadableObjects flow calls this when it needs the rows for a member-readable object kind. It keeps the public listing path small by reusing _rows.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._owned_rows`  (lines 226–233)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the rows that are placed into an agent turn’s context when the agent lists objects. It intentionally limits prompt text to an excerpt so a large task prompt cannot flood the model’s context.

**Data flow**: It receives the current ToolContext, including the extension context and acting member id. It calls _rows with that member id and a prompt excerpt limit, then returns the owned rows.

**Call relations**: The object-tool system calls this when an agent reads scheduled_task listings during a turn. It uses the shared row builder but chooses a safer prompt length for model context.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._rows`  (lines 235–281)

```
async def _rows(self, ext: ExtensionContext | None, *, member_id: UUID | None, prompt_max: int | None, conversation_id: UUID | None=None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Creates the detailed list rows that represent scheduled tasks in object listings. This is the main formatting point where stored schedule records become readable fields such as next run time, owner email, paused state, and prompt visibility.

**Data flow**: It receives the extension context, the viewer’s member id if any, an optional prompt length limit, and optionally a conversation id. It reads listed tasks from the schedule store, looks up owner emails, inspects recent run status, checks whether each viewer may see content, and returns OwnedRow records with summaries and fields.

**Call relations**: Several listing paths call this: member_page, _member_rows, and _owned_rows. It gathers information from storage, owner lookup, run inspection, and visibility checks, then hands back rows that the generic object system can page and display.

*Call graph*: calls 3 internal fn (_owner, _require_scheduler, _summary); called by 3 (_member_rows, _owned_rows, member_page); 3 external calls (__init__, owner_emails, task_content_visible).


##### `ScheduledTaskObjects._member_object`  (lines 283–312)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ScheduledTaskSpec] | None
```

**Purpose**: Builds the detailed view of one scheduled task. It returns the saved task specification, timestamps, a link to the conversation it reports into, and whether the viewer may see the spec content.

**Data flow**: It receives the extension context, task name, expected owner record, and viewer member id. It finds the named task, confirms it is the same generation the caller expected, copies stored fields into a ScheduledTaskSpec, adds a reports_to conversation link, checks content visibility, and returns an ObjectDetail. If the task is missing or changed, it returns nothing.

**Call relations**: The object-get flow calls this when someone opens a specific scheduled_task object. It relies on _find to locate the task and uses task_content_visible so private prompts are not revealed to the wrong reader.

*Call graph*: calls 1 internal fn (_find); 5 external calls (__init__, __init__, __init__, __init__, task_content_visible).


##### `ScheduledTaskObjects._status`  (lines 314–346)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status information for a scheduled task, including whether it is paused, when it will run next, when it last ran, and a short excerpt of the last response when the viewer may see it.

**Data flow**: It receives the current ToolContext, task name, and expected owner. It finds the task, asks the schedule store to inspect its run state, builds a status dictionary, and includes last-run response text only if the acting member is allowed to see task content.

**Call relations**: The object status path calls this after a task has been identified. It combines _find, scheduler inspection, and visibility rules to produce safe operational status for the caller.

*Call graph*: calls 2 internal fn (_find, _require_scheduler); 1 external calls (task_content_visible).


##### `ScheduledTaskObjects._apply_owned`  (lines 348–400)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates a new scheduled task or updates an existing one after checking the schedule, identity, and permissions. This is where the user’s requested task definition becomes a durable recurring task in storage.

**Data flow**: It receives the current ToolContext, object name, proposed spec, previous spec if any, and owner if this is an update. It validates the cron schedule, requires an acting member for creation, checks whether the named task already exists, enforces edit rules, computes the next fire time, and writes either a create or update through the schedule store.

**Call relations**: The generic object-apply command calls this when a user applies a scheduled_task manifest. It uses _find to guard against stale edits, _require_scheduler to write storage, validate_cron and next_fire to prepare timing, and speaker_is_admin when permission depends on admin status.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_scheduler); 4 external calls (__init__, now, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 402–406)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Cancels a scheduled task that the object system has authorized for deletion. It also protects against deleting the wrong version if the task changed while the caller was editing.

**Data flow**: It receives the current ToolContext, task name, and expected owner. It finds the task, verifies that the stored task id matches the expected generation, and then tells the schedule store to cancel it. If the task is missing or changed, it raises an error.

**Call relations**: The generic object-delete path calls this for scheduled_task objects. It uses _find for the safety check and _require_scheduler for the actual cancellation.

*Call graph*: calls 2 internal fn (_find, _require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 408–416)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTask | None
```

**Purpose**: Looks up a scheduled task by its object name. It is a simple helper used whenever code needs to turn a name from the object system into the stored task record.

**Data flow**: It receives the extension context and the task name. It asks the schedule store for reported tasks, scans them for a matching name, and returns the first matching ListedTask or nothing if no match exists.

**Call relations**: Apply, delete, detail, and status operations all call this before acting on a named task. It centralizes the name lookup so those flows can focus on permissions and output.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


##### `pause_and_wait`  (lines 479–517)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: Pauses the current workflow until either a member sends a new message or a timer expires. It is useful when the agent must wait for something outside the system, such as an approval, verification email, or cooldown period.

**Data flow**: It receives the current ToolContext and pause arguments: the message to show now, how long to wait, resumed-turn instructions, reason, metadata, and user-facing description. It calculates the resume time, records a pause row with the conversation, agent, current turn position, arrival watermark, and resume prompt, then returns a ToolResult telling the agent what to say and that it must end its turn.

**Call relations**: The tool framework calls this when the PAUSE_AND_WAIT_TOOL handler is invoked. It requires the extension context, writes the durable pause through PauseStore, formats the resume payload as JSON, and wraps it in TextContent for the tool result.

*Call graph*: calls 1 internal fn (_require_ext); 6 external calls (__init__, __init__, __init__, now, timedelta, dumps).


### `core/src/ufo/ext/scheduled_fire.py`

`domain_logic` · `scheduled task admission and run lookup`

A scheduled task can fire many times, so the system needs a durable name for each specific firing. This file creates that name from two pieces: the task's unique ID and the exact time that occurrence was meant to run. Think of it like writing both a train number and departure time on a ticket; either one alone is not enough to identify the trip.

The key is used as an idempotency key, meaning it helps the system avoid admitting the same scheduled occurrence twice. Because these keys may be stored and compared across deploys, the exact spelling matters. The timestamp is kept in Python's normal ISO text form, including timezone text like `+00:00`, so old and new code continue to recognize the same occurrence.

The file also provides the reverse operation: given a key, try to recover the task ID at the front. If the key does not begin with a valid UUID (a standard unique identifier), it returns `None`. That matters because not every turn in the system comes from a scheduled task; for example, a resumed timer may use a different kind of key.

#### Function details

##### `scheduled_fire_key`  (lines 14–16)

```
def scheduled_fire_key(task_id: UUID, fire_at: datetime) -> str
```

**Purpose**: Builds the durable key for one scheduled occurrence of one task. Code uses this when it needs a stable label that says, in one string, which task fired and at what scheduled time.

**Data flow**: It receives a task ID and a scheduled datetime. It turns the datetime into ISO text, joins it to the task ID with a colon, and returns the finished key string. It does not change anything outside itself.

**Call relations**: This is the builder side of the shared contract. When the scheduled-task runner admits a fire, it should call this function so every scheduled occurrence is named in the same way. Inside, it relies on the datetime object's `isoformat` method to produce the timestamp text.

*Call graph*: 1 external calls (isoformat).


##### `scheduled_fire_task_id`  (lines 19–25)

```
def scheduled_fire_task_id(key: str) -> UUID | None
```

**Purpose**: Tries to read the task ID back out of a scheduled fire key. It returns the task ID when the key looks like a scheduled fire key, and `None` when the key belongs to some other kind of turn.

**Data flow**: It receives a key string. It takes the text before the first colon, tries to interpret that text as a UUID, and returns the UUID if that works. If the first part is not a valid UUID, it returns `None` instead of raising an error.

**Call relations**: This is the parser side of the shared contract. When another part of the system, such as a runs feed, needs to connect a run back to its scheduled task, it can call this function. It hands the extracted text to the UUID parser, which decides whether that text is a valid unique task ID.

*Call graph*: 1 external calls (UUID).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `scheduled task creation, update, and runner scheduling`

Scheduled tasks need a way to say “run every day at 9” or “run every five minutes.” This file provides that timing language using cron, a common five-part text format for repeated times. For example, a cron expression can describe minutes, hours, days of the month, months, and days of the week.

The rest of the scheduled-task system stores only the next concrete run time, such as “2026-08-17 09:00.” It does not need to understand cron itself. This file keeps that cron-specific knowledge in one place, like a translator between a repeating calendar rule and the next exact appointment time.

There are two main jobs here. First, `validate_cron` rejects schedules that are not exactly five fields or that the cron library cannot understand. This prevents bad schedules from entering the system. Second, `next_fire` asks the cron library for the next run time strictly after a given datetime. That detail matters: if the scheduler was late or offline, it does not create a separate run for every missed time slot. Instead, it moves forward to the next single catch-up point.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: Checks whether a schedule string is a valid five-field cron expression. It is used to stop malformed timing rules before they are saved or used to schedule work.

**Data flow**: It receives a text schedule. First it splits the text into fields and confirms there are exactly five parts. Then it asks the cron library whether the expression is valid. If either check fails, it raises an error explaining the problem. If everything is valid, it returns the original schedule unchanged.

**Call relations**: This function is the gatekeeper for cron input. When other scheduled-task code needs to accept or store a repeating schedule, it can call `validate_cron` first. Inside, it hands the detailed syntax check to `croniter.croniter.is_valid`, which is the external library’s validator.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: Calculates the next datetime when a cron schedule should run after a given moment. It turns a repeating rule into one concrete future run time.

**Data flow**: It receives a cron schedule and a datetime called `after`. It gives both to the cron library, then asks for the next datetime occurrence. The result is returned as the next fire time, without changing any stored data itself.

**Call relations**: This function is used when the scheduler needs to advance a task from its current or last-known time to its next planned run. It delegates the calendar math to `croniter.croniter`, which understands cron expressions and can compute the next matching datetime.

*Call graph*: 1 external calls (croniter).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pauses.py`

`domain_logic` · `workflow arming and scheduled pause runner ticks`

A scheduled task often needs to say, “wait until this time, then continue this conversation.” This file is the storage and safety layer for that wait. It defines a database table called `pause`, where each conversation can have at most one active pause. If the same conversation is armed again, the old wait is replaced, because the workflow is only waiting for one next thing.

The file also protects against two common timing problems. First, several worker processes may look for due pauses at the same time. To stop them from waking the same conversation twice, a worker must “claim” a pause for a short lease, like putting a temporary reserved sign on a library book. Second, a conversation can be re-armed while an older pause is being processed. The code checks that the worker still owns the same pause before firing it, and deletes only the exact claimed row afterward.

The `Pause` dataclass is the in-memory shape of one database row. `PauseStore` is the main tool used by the rest of the extension: it arms pauses, lists them, claims due ones, verifies claims, and retires completed waits. The file is careful to filter every database operation by workspace, because the database connection itself is not automatically limited to one workspace.

#### Function details

##### `_aware`  (lines 77–78)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: This helper makes sure a time value has a time zone. If the database gives back a time without one, it treats it as UTC, the shared standard time used by the system.

**Data flow**: It receives a `datetime`. If that value already says what time zone it belongs to, it returns it unchanged. If it has no time zone, it adds UTC and returns the adjusted value.

**Call relations**: When `_row` builds a `Pause` object from database data, it calls `_aware` for every stored time. This keeps the rest of the pause code from having to guess whether a time is safe to compare.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 81–97)

```
def _row(row: sa.RowMapping) -> Pause
```

**Purpose**: This function turns a raw database row into a `Pause` object that the Python code can use comfortably. It is the single doorway from database results into the in-memory pause shape.

**Data flow**: It receives a row mapping from SQLAlchemy, reads fields such as pause id, conversation id, due time, prompt, claim id, and timestamps, normalizes the time fields through `_aware`, and returns a new `Pause` value.

**Call relations**: After `PauseStore.arm`, `PauseStore.armed`, and `PauseStore.claim_due` read rows from the database, they hand each row to `_row`. That means all reads produce the same clean, predictable `Pause` objects.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 100–101)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This helper expresses the rule for whether a pause can be claimed by a worker. A pause is available if nobody has claimed it, or if its previous claim has expired.

**Data flow**: It receives the current time. It builds a database condition that says: `claimed_by` is empty, or `claim_expires_at` is earlier than now. The result is not a true-or-false Python value yet; it is a SQL condition used in database queries.

**Call relations**: The workspace finder and the claim operation both use this same helper, so they agree on what “available to claim” means. `due_pause_workspaces.due` uses it to find workspaces with runnable pauses, and `PauseStore.claim_due` uses it to lease the actual rows.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_pause_workspaces`  (lines 104–117)

```
def due_pause_workspaces() -> WorkspaceCandidates
```

**Purpose**: This function tells the job system how to find workspaces that have pause work ready to run. It is a bridge between the pause table and the broader background job scheduler.

**Data flow**: It defines an inner query-building function, `due`, that finds workspace ids with at least one due and claimable pause. It then passes that query builder to `owner_candidates`, which packages it in the format expected by the job ownership system.

**Call relations**: The pause runner uses this candidate source when deciding which workspaces may need attention. Inside it, `due_pause_workspaces.due` does the actual database query construction, and `owner_candidates` connects that query to the shared job framework.

*Call graph*: 1 external calls (owner_candidates).


##### `due_pause_workspaces.due`  (lines 109–115)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This nested function builds the database query for finding workspaces with pauses that should wake up now. It only includes pauses whose timer has arrived and whose claim is free or expired.

**Data flow**: It reads the current UTC time, builds a SQL query over the pause table, filters for `resume_at` at or before now, filters again using `_claim_available`, and asks for distinct workspace ids.

**Call relations**: It lives inside `due_pause_workspaces` because it is the query recipe handed to `owner_candidates`. `_claim_available` supplies the shared lease-availability rule, so this query matches the later claiming step.

*Call graph*: calls 1 internal fn (_claim_available); 2 external calls (now, select).


##### `PauseStore.arm`  (lines 126–183)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, resume_at: datetime, origin_seq: int, origin_arrival_seq: int, prompt: str, user_description: str, created_by_member_id: UUID | None) -> P
```

**Purpose**: This method creates or replaces the active pause for one conversation. It is used when a workflow says, “wake me up at this time with this prompt.”

**Data flow**: It receives the conversation, agent, due time, sequence markers, prompt text, user-facing description, and optional member id. It creates a fresh pause id, clears any old claim, and writes the row into the database. If that conversation already had a pause in this workspace, it overwrites it. It returns the newly stored pause as a `Pause` object.

**Call relations**: This is the entry point for arming a wait. It calls `uuid4` so every new wait has its own identity, even when it replaces an old row, and then sends the returned database row through `_row` before giving it back to the caller.

*Call graph*: calls 1 internal fn (_row); 1 external calls (uuid4).


##### `PauseStore.armed`  (lines 185–191)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Pause, ...]
```

**Purpose**: This method lists currently armed pauses in the workspace. It can show all pauses, or only the pause for one conversation.

**Data flow**: It receives an optional conversation id. It builds a database query for this workspace, adds a conversation filter if one was provided, orders the results by resume time, converts each row with `_row`, and returns them as a tuple.

**Call relations**: Other code can use this as a read-only view of the pause table. It relies on SQLAlchemy to fetch rows and `_row` to turn those rows into the same `Pause` objects used everywhere else.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `PauseStore.claim_due`  (lines 193–234)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_PAUSES) -> tuple[Pause, ...]
```

**Purpose**: This method leases a batch of pauses whose timers have arrived, so one worker can process them without another worker taking the same ones. The lease is temporary, which lets work be retried if a worker crashes.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of pauses to take. It creates a unique claim id, selects the oldest due and available pauses in this workspace, updates those rows with the claim id and expiry time, then returns the claimed rows as `Pause` objects.

**Call relations**: The pause runner calls this during a tick to get work. It uses `_claim_available` so it only takes unclaimed or expired rows, uses a database update so selecting and claiming happen together, and passes the returned rows through `_row` for processing.

*Call graph*: calls 2 internal fn (_claim_available, _row); 4 external calls (timedelta, select, update, uuid4).


##### `PauseStore.claim_holds`  (lines 236–261)

```
async def claim_holds(self, row: Pause) -> bool
```

**Purpose**: This method checks whether a worker still owns the pause it is about to fire. It prevents an old claimed pause from waking a workflow after that pause has already been replaced.

**Data flow**: It receives a `Pause` that should already have a claim id. If there is no claim id, it raises an error because an unclaimed pause must not be fired. Otherwise it looks for the same row id, in the same workspace, with the same claim id. It returns `true` if that exact claim is still present, or `false` if the row was replaced, cleared, or claimed by someone else.

**Call relations**: The pause runner’s `_fire` step calls this immediately before invoking the resume. This gives the runner one last safety check before doing something that cannot easily be undone.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `PauseStore.retire`  (lines 263–276)

```
async def retire(self, row: Pause) -> None
```

**Purpose**: This method removes a claimed pause after the runner has finished with it. It deletes only the exact pause still owned by the same claim, so it does not accidentally remove a newer replacement.

**Data flow**: It receives a claimed `Pause`. If the pause has no claim id, it raises an error because only claimed pauses can be retired. Otherwise it issues a database delete for the matching pause id, workspace id, and claim id. It returns nothing and changes the database by removing the row if the claim still matches.

**Call relations**: The pause runner’s `_fire` step calls this after a pause has fired or been considered finished. The claim check in the delete is the final guard: if the lease expired or the row was re-armed, this method leaves the newer or no-longer-owned row alone.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/schedules.py`

`domain_logic` · `cross-cutting: task creation/editing, background polling, firing, and status reads`

Scheduled tasks are recurring reminders for an agent: at a planned time, the system re-enters a conversation and sends a saved prompt. This file defines the database table for those tasks and a ScheduleStore class that reads and changes the rows in a careful way.

The main problem it solves is coordination. A task may be edited, cancelled, expire, or become due while background workers are polling for work. To avoid double-firing, the runner first claims a due task with a short lease, like putting a temporary “I am working on this” sticky note on it. Before firing, it checks that the sticky note still belongs to it and that the task has not changed. After a successful fire, it records when the task ran and schedules the next run.

Member-facing operations are narrower. They only touch tasks in the current workspace and current object-agent namespace, so one agent cannot accidentally edit another agent’s schedule. The file also normalizes timestamps to UTC, because different databases may return time values differently. Without this file, scheduled tasks would either not persist reliably, or worse, could fire twice, fire after cancellation, or leak across workspace and agent boundaries.

#### Function details

##### `_utc`  (lines 124–125)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: This helper makes sure a datetime has UTC timezone information. It is used so the rest of the code can compare times without guessing what timezone they mean.

**Data flow**: It receives one datetime. If the datetime already has timezone information, it returns it unchanged; if it is missing timezone information, it marks it as UTC. The output is always a datetime the code can treat as UTC-aware.

**Call relations**: Rows coming back from the database are passed through this helper by _task and ScheduleStore.inspect_many. _utc_opt also relies on it when the timestamp might be missing.

*Call graph*: called by 3 (inspect_many, _task, _utc_opt); 1 external calls (replace).


##### `_utc_opt`  (lines 128–129)

```
def _utc_opt(value: datetime | None) -> datetime | None
```

**Purpose**: This is the nullable version of _utc. It exists for timestamp fields that may be empty, such as a task that has never run before.

**Data flow**: It receives either a datetime or None. None stays None; a real datetime is passed to _utc and comes back as UTC-aware. The result is safe for optional time fields.

**Call relations**: The row builder _task uses it for optional task timestamps. ScheduleStore.inspect_many uses it when preparing inspection results for fields that may not exist yet.

*Call graph*: calls 1 internal fn (_utc); called by 2 (inspect_many, _task).


##### `_claim_available`  (lines 132–136)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for deciding whether a scheduled task can be claimed by a worker. A task is available if nobody has claimed it, or if its previous claim has expired.

**Data flow**: It receives the current time. It turns that time into a SQL condition that checks the task row’s claim fields. The output is not a true-or-false Python value, but a database filter used in later queries.

**Call relations**: The workspace candidate search uses this condition so it does not wake a worker for tasks already leased by someone else. ScheduleStore.claim_due uses the same rule when actually leasing due tasks.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_expired`  (lines 139–143)

```
def _expired(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for deciding whether a task has passed its expiry time. Expired tasks should be removed instead of fired.

**Data flow**: It receives the current time. It creates a SQL condition that matches rows with an expires_at value that is not empty and is at or before that time. The output is used inside database queries.

**Call relations**: The candidate search uses it to find workspaces with expired tasks to clean up. ScheduleStore.claim_due uses it to delete expired tasks before claiming due ones.

*Call graph*: called by 2 (claim_due, due); 1 external calls (and_).


##### `_task`  (lines 146–165)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: This turns a raw database row into a ScheduledTask object that the rest of the extension can use. It is the single place that translates stored task data into the in-memory shape.

**Data flow**: It receives a row mapping from the database. It copies out identifiers, schedule text, prompt text, claim information, pause state, and timestamps, normalizing all timestamp fields to UTC as needed. It returns a ScheduledTask value object.

**Call relations**: ScheduleStore.create, ScheduleStore.update, ScheduleStore.list, and ScheduleStore.claim_due all use this after reading rows. That keeps every task object consistent no matter which database operation produced it.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 4 (claim_due, create, list, update); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 168–191)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: This tells the job system which workspaces might have scheduled-task work waiting. It is a lightweight way to avoid scanning every workspace when only some have due or expired tasks.

**Data flow**: It defines a small database query factory for finding workspace IDs with claimable due or expired tasks. It gives that factory to the job ownership system, which turns it into workspace candidates for background workers.

**Call relations**: It hands its nested due query to owner_candidates. The scheduled-task runner can use the returned candidate source to decide where to poll for work.

*Call graph*: 1 external calls (owner_candidates).


##### `due_task_workspaces.due`  (lines 174–189)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This inner query finds workspaces that currently contain work for the scheduled-task runner. Work means either a due, unpaused task or an expired task that should be removed.

**Data flow**: It reads the current UTC time, builds database filters for available claims and expired rows, and selects distinct workspace IDs that match. The result is a SQL query, not the final rows themselves.

**Call relations**: due_task_workspaces gives this query builder to owner_candidates. It uses the same availability and expiry helpers as ScheduleStore.claim_due, so discovery and claiming agree about what counts as runnable work.

*Call graph*: calls 2 internal fn (_claim_available, _expired); 5 external calls (now, and_, not_, or_, select).


##### `ScheduleStore.workspace_id`  (lines 205–206)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property provides the workspace ID attached to the current extension context. It keeps every database operation tied to the right workspace.

**Data flow**: It reads ctx.workspace_id from the ScheduleStore’s context and returns that UUID. It does not change anything.

**Call relations**: The store’s methods use this workspace boundary whenever they read, insert, update, delete, claim, or inspect scheduled-task rows.


##### `ScheduleStore.create`  (lines 208–267)

```
async def create(self, conversation_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None, expires_at: datetime | None=None,
```

**Purpose**: This creates a new scheduled task for the current agent and conversation. It also prevents duplicate task names for the same workspace and agent.

**Data flow**: It receives the conversation, task name, schedule, prompt, description, first run time, optional creator, optional expiry, and paused flag. It checks that the conversation belongs to the same agent that will run the task, inserts a new row with a fresh ID, and returns the new ScheduledTask. If the name already exists, it raises an error instead of silently overwriting.

**Call relations**: This is used when member-facing code wants to save a new recurring task. It calls object_agent_id to bind the task to the current agent and _task to turn the inserted row into the standard task object.

*Call graph*: calls 1 internal fn (_task); 2 external calls (object_agent_id, uuid4).


##### `ScheduleStore.update`  (lines 269–323)

```
async def update(self, expected: ScheduledTask, schedule: str, prompt: str, description: str, next_run_at: datetime, expires_at: datetime | None=None, *, paused: bool) -> ScheduledTask
```

**Purpose**: This edits an existing scheduled task’s schedule, prompt, description, expiry, next run time, and paused state without changing its identity. It protects against editing the wrong row if the task changed meanwhile.

**Data flow**: It receives the task version the caller believes is current, plus the new task settings. It checks that the task still belongs to the current agent, then updates only the row that matches the expected ID, agent, conversation, name, and creator. It clears any active claim because the old leased version is no longer valid, and returns the updated ScheduledTask.

**Call relations**: Member-facing edit flows call this after loading a task. It uses _creator_matches to avoid mixing creator-owned and creatorless tasks, and _task to return the refreshed row.

*Call graph*: calls 2 internal fn (_creator_matches, _task); 2 external calls (update, object_agent_id).


##### `ScheduleStore.cancel`  (lines 325–341)

```
async def cancel(self, expected: ScheduledTask) -> None
```

**Purpose**: This deletes a scheduled task, but only if it still matches the exact task the caller meant to cancel. That prevents a stale screen or stale request from cancelling a different version.

**Data flow**: It receives the expected ScheduledTask. It checks the current agent, then deletes a row matching the workspace, task ID, agent, conversation, name, and creator. If no row was deleted, it raises an error telling the caller the task changed while cancelling.

**Call relations**: Cancellation flows use this to remove tasks. It shares the same creator-matching safeguard as update and uses the current object agent as a boundary.

*Call graph*: calls 1 internal fn (_creator_matches); 2 external calls (delete, object_agent_id).


##### `ScheduleStore._creator_matches`  (lines 343–348)

```
def _creator_matches(self, expected: ScheduledTask) -> sa.ColumnElement[bool]
```

**Purpose**: This creates the database condition for matching the task creator correctly. It matters because a task with no creator is different from a task created by a particular member.

**Data flow**: It receives the expected task. If the task has no creator, it produces an IS NULL database condition; otherwise, it produces an equality check against that member ID. The output is used as part of safer update and delete filters.

**Call relations**: ScheduleStore.update and ScheduleStore.cancel call this when they need to prove they are touching the same task version the caller saw.

*Call graph*: called by 2 (cancel, update).


##### `ScheduleStore._listing`  (lines 350–371)

```
def _listing(self, selected: tuple[sa.ColumnElement[Any], ...], *, conversation_id: UUID | None, names: tuple[str, ...] | None, visible_to_member_id: UUID | None, include_all_owners: bool, limit: int
```

**Purpose**: This builds the shared database query used for listing scheduled tasks. It applies workspace, agent, conversation, name, owner, sorting, and limit rules in one place.

**Data flow**: It receives the columns to select and optional filters such as conversation ID, task names, visible member, ownership mode, and limit. It starts with only tasks from the current workspace and current agent, then adds any requested filters and returns the finished SQL query.

**Call relations**: ScheduleStore.list calls this before executing the query. Keeping the filter construction here makes list and list_reported follow the same visibility and ordering rules.

*Call graph*: called by 1 (list); 2 external calls (select, object_agent_id).


##### `ScheduleStore.list`  (lines 373–392)

```
async def list(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -> tuple[S
```

**Purpose**: This reads scheduled tasks visible under the requested filters. It returns task definitions without extra conversation display information.

**Data flow**: It receives optional filters for conversation, names, member ownership, whether to include all owners, and result limit. It builds the query with _listing, runs it inside a transaction, converts each row with _task, and returns a tuple of ScheduledTask objects.

**Call relations**: Member-facing code can use this for plain task lookup. ScheduleStore.list_reported builds on it when it also needs conversation audience and label information.

*Call graph*: calls 2 internal fn (_listing, _task); called by 1 (list_reported).


##### `ScheduleStore.list_reported`  (lines 394–428)

```
async def list_reported(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -
```

**Purpose**: This lists tasks together with facts about the conversation they report into, such as audience and display label. It is used when a user-facing view needs to decide what should be visible and how to describe it.

**Data flow**: It first gets matching ScheduledTask objects from list. If there are tasks, it asks the context for live conversation facts for their conversation IDs, then pairs each task with the conversation audience and surface label. Tasks whose conversation no longer exists are left out.

**Call relations**: It depends on ScheduleStore.list for the task page itself, then adds conversation facts from the extension context. It creates ListedTask objects as the final member-facing result.

*Call graph*: calls 1 internal fn (list); 1 external calls (__init__).


##### `ScheduleStore.claim_due`  (lines 430–486)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: This is the runner’s main pickup operation. It removes expired tasks and leases a limited batch of due tasks so one worker can fire them without other workers grabbing the same rows.

**Data flow**: It receives the current time, lease length in seconds, and a maximum batch size. Inside one transaction, it deletes expired claimable rows, then selects the oldest due, unpaused, claim-available tasks and stamps them with a new claim ID and claim expiry. It returns those claimed tasks as ScheduledTask objects.

**Call relations**: Background polling code calls this when a workspace has possible scheduled-task work. It uses _expired and _claim_available so claiming follows the same rules as workspace discovery, and _task to return usable task objects.

*Call graph*: calls 3 internal fn (_claim_available, _expired, _task); 6 external calls (timedelta, delete, not_, select, update, uuid4).


##### `ScheduleStore.claim_holds`  (lines 488–519)

```
async def claim_holds(self, task: ScheduledTask) -> bool
```

**Purpose**: This checks whether a worker’s lease still owns the exact task it is about to fire. It is a last-minute safety check against edits, cancellations, or reclaims.

**Data flow**: It receives a claimed ScheduledTask. If the task has no claim ID, it raises an error. Otherwise it rereads the database row under a lock and checks the workspace, task ID, claim ID, conversation, agent, name, and schedule. It returns true only if the same claimed version is still present.

**Call relations**: ScheduledTaskRunner._fire calls this immediately before invoking the scheduled task. If it returns false, the runner can skip firing because the task changed or no longer belongs to this claim.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `ScheduleStore.retire_if_expired`  (lines 521–535)

```
async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool
```

**Purpose**: This deletes a claimed task if its expiry time has passed before it is invoked. It prevents an expired task from firing just because it was claimed earlier.

**Data flow**: It receives a claimed task and the current time. If there is no claim, it raises an error; if the task has no expiry or has not expired yet, it returns false. If the task is expired, it deletes the matching claimed row and returns true.

**Call relations**: ScheduledTaskRunner._fire calls this during the firing flow. It lets the runner stop cleanly when a task becomes expired between claiming and invocation.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `ScheduleStore.reschedule`  (lines 537–567)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: This advances a claimed task after it has fired. It records the latest run time, optionally records the turn that was created, sets the next run time, and releases the claim.

**Data flow**: It receives the claimed task, the next run time, the last run time, and optionally the ID of the turn caused by the fire. It updates the matching claimed row with those values, clears the claim fields, and returns whether a row was actually updated.

**Call relations**: ScheduledTaskRunner._fire calls this after a successful fire. The later inspection path can use the recorded last_turn_id to show the most recent result.

*Call graph*: called by 1 (_fire); 1 external calls (update).


##### `ScheduleStore.inspect`  (lines 569–573)

```
async def inspect(self, expected: ScheduledTask) -> TaskInspection | None
```

**Purpose**: This fetches the live status picture for one scheduled task. It is a convenience wrapper around the batch inspection method.

**Data flow**: It receives one expected ScheduledTask. It asks inspect_many for that single task, then returns the matching TaskInspection if present, or None if the task no longer matches.

**Call relations**: Status-rendering code can call this for a single task. It delegates all real work to ScheduleStore.inspect_many so single-task and multi-task inspection behave the same way.

*Call graph*: calls 1 internal fn (inspect_many).


##### `ScheduleStore.inspect_many`  (lines 575–611)

```
async def inspect_many(self, expected: tuple[ScheduledTask, ...]) -> dict[UUID, TaskInspection]
```

**Purpose**: This fetches live status information for several scheduled tasks at once. It shows timing marks and, when available, the latest turn outcome from the last fire.

**Data flow**: It receives expected ScheduledTask objects. It reads matching rows for the current workspace and current agent, checks that each row still has the same name and conversation, then collects any last_turn_id values. It asks the context for those turn outcomes and returns a dictionary from task ID to TaskInspection, with UTC-normalized times and optional last status and response text.

**Call relations**: ScheduleStore.inspect calls this for the one-task case. It uses object_agent_id to stay inside the current agent’s namespace, SQL selection to read task state, and the context’s turn outcome lookup to add the latest fire result.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 1 (inspect); 3 external calls (__init__, select, object_agent_id).


### Monitor watches
These files package the monitor extension, expose monitor objects and the monitor tool, and back them with persistent watch storage and due-run handling.

### `extensions/monitors/ufo_ext_monitors/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is like a label on a folder saying, “treat this folder as a package of code.” This particular file is empty, so it does not create objects, run setup code, or change behavior directly. Its value is structural: it lets other parts of the project import modules from `extensions/monitors/ufo_ext_monitors` using normal Python package paths. Without it, some Python environments or tooling might not recognize this directory as an importable package, which could make the monitor extension unavailable or harder to load reliably. Think of it like a blank cover page in a binder: it does not contain instructions itself, but it helps identify the binder as a coherent section of the system.


### `extensions/monitors/ufo_ext_monitors/monitor_kind.py`

`domain_logic` · `request handling`

A monitor is a watch on a shell command inside a conversation's sandbox. It checks the command from time to time, compares the result with an original baseline, and later wakes the agent if something changes, repeated failures happen, or a deadline arrives. This file is the bridge between those stored monitor records and UFO's general object system, so users and agents can ask, “what monitors exist?”, “what is this one watching?”, “what is its current status?”, and “stop this one.”

The file defines `MonitorSpec`, the user-facing shape of a monitor: the command, the interval, the deadline, and the reason for watching. It also defines `MonitorObjects`, which knows how to read armed monitors from `MonitorStore`, turn them into list rows, show detailed information, report counters such as probe failures, and delete a monitor to disarm it.

A key rule is that applying or creating a monitor through this object kind is refused. That is intentional: arming a monitor needs a live chat turn, like setting a stopwatch only after checking the starting reading. The agent's monitor tool runs the probe once, records that baseline, and only then creates the watch. This object kind is mainly the read-and-stop interface. At the bottom, `MONITOR_OBJECT` registers this behavior with the wider system and explains which actions agents may use: list, get, and delete.

#### Function details

##### `_owner`  (lines 53–58)

```
def _owner(row: Monitor) -> GeneratedObjectOwner
```

**Purpose**: Builds the ownership information for one monitor. This tells the object system who created the monitor, who it is shared with, and which exact stored monitor record it refers to.

**Data flow**: It receives a stored `Monitor` row. It reads the creator member ID, the audience snapshot, and the monitor ID. It turns the audience into a shared-or-private ownership marker and returns a `GeneratedObjectOwner` that can be attached to list rows and later checked when fetching or deleting the monitor.

**Call relations**: `MonitorObjects._member_rows` calls this while turning stored monitors into object-list entries. The returned owner is what lets later operations confirm they are still talking about the same monitor, not just another monitor with the same name.

*Call graph*: called by 1 (_member_rows); 2 external calls (__init__, subject_shared).


##### `_require_ext`  (lines 61–64)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure this monitor object kind has the extension context it needs to talk to monitor storage. If the context is missing, it fails early with a clear error instead of letting a later storage call break in a confusing way.

**Data flow**: It receives an optional extension context. If the context is present, it returns it unchanged. If it is missing, it raises an error explaining that the monitor kind requires the scheduled-tasks extension context.

**Call relations**: `MonitorObjects._member_rows`, `MonitorObjects._delete_owned`, and `MonitorObjects._find` call this before creating a `MonitorStore`. It is the small guard at the door before any code tries to read or change stored monitor records.

*Call graph*: called by 3 (_delete_owned, _find, _member_rows).


##### `MonitorObjects._member_rows`  (lines 78–97)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Creates the rows shown when a member lists monitors. Each row gives a short summary of what is being watched, when it will next run, when it expires, who owns it, and whether it belongs to the requesting member.

**Data flow**: It receives the extension context and the member ID of the person asking, if known. It reads all currently armed monitors from `MonitorStore`, looks up owner email addresses, and builds `OwnedRow` objects. Each output row contains the monitor name, a shortened command-and-reason summary, ownership information, and list fields such as conversation ID, next probe time, deadline, owner email, and `mine`.

**Call relations**: The object system calls this when someone lists monitor objects. It asks `_require_ext` for a usable context, asks `MonitorStore` for armed monitors, uses `_owner` to describe ownership for each one, and returns rows that the broader object system can filter, sort, and show.

*Call graph*: calls 2 internal fn (_owner, _require_ext); 3 external calls (__init__, __init__, owner_emails).


##### `MonitorObjects._member_object`  (lines 99–125)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[MonitorSpec] | None
```

**Purpose**: Builds the detailed view for one monitor. This is used when someone asks to inspect a specific monitor and needs its command, interval, deadline, reason, timestamps, and link back to the conversation it reports into.

**Data flow**: It receives the extension context, the monitor name, the expected owner information, and the requesting member ID. It looks up a currently armed monitor by name. If no monitor is found, or if the stored monitor ID does not match the owner generation, it returns nothing. Otherwise it returns an `ObjectDetail` containing a `MonitorSpec`, creation and update times, and a `reports_to` link to the watched conversation.

**Call relations**: The object system calls this for a get/read operation on one monitor. It delegates the lookup to `_find`, then packages the result into the standard object-detail format so other parts of UFO can display it consistently with other object kinds.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `MonitorObjects._status`  (lines 127–142)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status information for one monitor, such as when it was armed, how many probes have run, how many failed, and a short excerpt of the baseline output. This gives a user more than the static setup; it shows how the watch has behaved so far.

**Data flow**: It receives a tool context, a monitor name, and the expected owner information. It looks up the monitor by name. If the monitor is missing or no longer matches the owner generation, it returns nothing. Otherwise it returns a dictionary of JSON-friendly values: timestamps as text, counters, skipped count, and a shortened baseline excerpt.

**Call relations**: This is called when the tool/object layer needs status for a specific monitor. It uses `_find` for the stored row and then translates internal fields into simple values that can be shown to an agent or user.

*Call graph*: calls 1 internal fn (_find).


##### `MonitorObjects._apply_owned`  (lines 144–152)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: MonitorSpec, old: MonitorSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Refuses attempts to create or update monitors through the generic object apply path. This protects an important rule: monitors must be armed through the live monitor tool so the first probe can run and establish the baseline.

**Data flow**: It receives the tool context, requested name, requested spec, any old spec, and owner information. It does not use those values to change storage. Instead, it raises `VerbNotSupported` with a message telling the caller to use the monitor tool.

**Call relations**: The generic object system may call this when an apply-style operation is attempted. Rather than handing off to storage, it stops the flow immediately and explains why this object kind is read/delete only from that path.

*Call graph*: 1 external calls (__init__).


##### `MonitorObjects._delete_owned`  (lines 154–159)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Stops an armed monitor. Deleting the object is the user's way to disarm the watch so it will not fire later.

**Data flow**: It receives the tool context, monitor name, and expected owner information. It finds the current stored monitor. If the monitor is missing or its ID no longer matches the owner generation, it raises an error because the target changed while being stopped. If it matches, it asks `MonitorStore` to disarm it. If disarming fails, it raises the same kind of changed-while-stopping error.

**Call relations**: The object system calls this after its permission checks allow a delete. The function uses `_find` to locate the monitor, `_require_ext` to get storage access, and `MonitorStore.disarm` to perform the actual stop.

*Call graph*: calls 2 internal fn (_find, _require_ext); 1 external calls (__init__).


##### `MonitorObjects._find`  (lines 161–165)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> Monitor | None
```

**Purpose**: Looks up one armed monitor by name. It is a shared helper for the detailed view, status view, and delete operation.

**Data flow**: It receives an optional extension context and a monitor name. It checks that the context exists, reads the current armed monitors from `MonitorStore`, scans for the first row whose name matches, and returns that row. If none match, it returns nothing.

**Call relations**: `MonitorObjects._member_object`, `MonitorObjects._status`, and `MonitorObjects._delete_owned` all call this instead of repeating the same lookup logic. It is the common “find the current monitor record” step before showing details, reporting status, or disarming.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_delete_owned, _member_object, _status); 1 external calls (__init__).


### `extensions/monitors/ufo_ext_monitors/monitor_tool.py`

`orchestration` · `tool call / request handling`

This file exists so an agent can wait for something without staying active the whole time. For example, it can watch a build, deployment, inbox, or log by running a shell command every few minutes and comparing the output with the first run. Think of it like taking a photo now, then asking someone to take another photo later and wake you only if the scene changes.

The main input shape is `MonitorInput`. It asks for a short monitor name, the shell command to run, how often to check, a deadline, a message to show now, and instructions for the future turn that will happen if the monitor fires. The command output must be stable: if it includes clocks or counters, the monitor will think something changed every time.

The `monitor` function is the tool’s workhorse. It first makes sure the scheduled-task extension is available, then checks that the conversation has not reached its monitor limit and that the requested name is not already used. Before saving anything, it runs the probe command once in the current sandbox. If that command fails, the tool refuses to arm the monitor, so a broken watch is not left behind. If it succeeds, the stdout becomes the baseline, the monitor row is stored, and the result includes the baseline plus a directive telling the agent to reply and end the turn. The `MONITOR_TOOL` object at the end exposes this behavior to the wider tool system.

#### Function details

##### `_require_ext`  (lines 76–79)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure the monitor tool was given the scheduled-task extension it needs. Without that extension, the tool cannot save a durable monitor to be checked later.

**Data flow**: It receives the extension context, which may be missing. If the context is present, it returns it unchanged. If it is missing, it stops the operation by raising an error that explains the monitor tool cannot run without it.

**Call relations**: The `monitor` function calls this at the start of its work before creating a `MonitorStore`. This makes the missing-extension problem fail early and clearly, instead of failing later while trying to save the watch.

*Call graph*: called by 1 (monitor).


##### `_refusal`  (lines 82–83)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: This creates a standard error-style tool result when the monitor cannot be armed. It is used for expected refusals, such as too many monitors, a duplicate name, or a probe command that failed.

**Data flow**: It receives a plain text explanation. It wraps that explanation in `TextContent`, puts it inside a `ToolResult`, marks the result as an error, and returns it to the caller.

**Call relations**: The `monitor` function calls this whenever it needs to reject the request without saving anything. It hands the reason back to the tool system in the normal response format, so the agent can see what went wrong and fix the request.

*Call graph*: called by 1 (monitor); 2 external calls (__init__, __init__).


##### `monitor`  (lines 86–134)

```
async def monitor(ctx: ToolContext, args: MonitorInput) -> ToolResult
```

**Purpose**: This is the handler that arms a new monitor. It validates the request, runs the watch command once now, saves the monitor only if that first run succeeds, and returns instructions telling the agent what was armed and what baseline output it is watching.

**Data flow**: It receives the current tool context and a `MonitorInput` request. It reads the conversation and agent information from the context, checks the existing armed monitors from `MonitorStore`, builds the full monitor name, and runs the requested shell command in the sandbox with a timeout. If any rule fails, it returns an error result and changes nothing. If the probe succeeds, it records the current time, trims the baseline output to a safe size, stores a monitor row with the deadline and next probe time, then returns a text result containing a directive and a JSON payload with the armed monitor details.

**Call relations**: This function is the central path used by `MONITOR_TOOL` when the agent calls the monitor tool. It relies on `_require_ext` before it talks to the monitor store, uses `_refusal` for safe early exits, calls the sandbox to test the command immediately, and then asks `MonitorStore` to persist the durable watch for future scheduled checking.

*Call graph*: calls 2 internal fn (_refusal, _require_ext); 9 external calls (__init__, __init__, __init__, now, timedelta, dumps, capped, qualified_name, stderr_tail).


### `extensions/monitors/ufo_ext_monitors/monitors.py`

`domain_logic` · `monitor setup, scheduled runner ticks, fire delivery, and user stop requests`

A monitor is like a scheduled lookout. It remembers a shell command to run, what output counts as normal, when to check again, when the watch must end, and which conversation and agent should be re-entered if something changes. This file defines the monitor table and the small set of operations allowed on it.

The important safety idea is leasing. A background runner periodically asks for due monitors. Instead of simply reading them, it claims them for a short time. That claim is like putting a sticky note on a task: “I am working on this.” Other runners then skip it until the claim expires. This prevents two workers from probing or firing the same monitor at once.

The file also keeps every database query scoped to one workspace, because the transaction connection it receives is not automatically limited for it. Without these filters, one workspace could accidentally read or delete another workspace’s monitors.

After each probe, the store records what happened: quiet output, command failure, or skipped execution. Quiet and failure streaks are tracked separately so the system can decide when a monitor should fire. When a fire is delivered, the monitor is retired. When a user asks to stop watching, it is disarmed immediately.

#### Function details

##### `qualified_name`  (lines 73–84)

```
def qualified_name(conversation_id: UUID, slug: str) -> str
```

**Purpose**: Builds the stored monitor name from a conversation identifier and a user-chosen slug. This keeps names unique across a workspace while still letting different conversations reuse friendly names like “ci-run”.

**Data flow**: It takes a conversation UUID and a short name chosen by the agent. It uses the first few hexadecimal characters of the conversation ID as a prefix, joins that with the slug, and returns the combined name that is safe to store and look up.

**Call relations**: This helper is used when creating or referring to monitor objects so the database’s workspace-wide unique name rule has a stable name to enforce. It does not call other project functions; it is a naming convention in one place.


##### `capped`  (lines 87–97)

```
def capped(output: str) -> str
```

**Purpose**: Shortens probe output so a monitor fire cannot carry an unbounded amount of text. It preserves the beginning and end, which are usually the most useful parts, and marks how much was omitted.

**Data flow**: It receives a text output string. If the encoded bytes fit under the configured limit, it returns the string unchanged. If it is too large, it keeps the first half and last half, inserts an omission marker in the middle, and returns that bounded version.

**Call relations**: This function protects later monitor comparison and fire reporting from huge command output. It stands alone and is meant to be used before storing or comparing probe output.


##### `stderr_tail`  (lines 100–105)

```
def stderr_tail(stderr: str) -> str
```

**Purpose**: Keeps only the final part of a failed command’s error output. This is useful because command errors usually end with the most specific explanation of what went wrong.

**Data flow**: It receives stderr text from a shell command. If it is small enough, it returns it all. If it is too large, it returns only the last configured number of bytes, decoded back into text.

**Call relations**: This is a small support helper for failure reporting. It does not call other project functions and exists to keep monitor fires readable and bounded.


##### `_aware`  (lines 138–139)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a datetime value has timezone information. In this file, times are treated as UTC so scheduling and deadlines are compared consistently.

**Data flow**: It receives a datetime. If the datetime already has a timezone, it returns it as-is. If it has no timezone attached, it adds UTC and returns the corrected value.

**Call relations**: _row calls this whenever it builds a Monitor from database data. This matters because some database backends may return timezone-less datetimes even though the system expects timezone-aware ones.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 142–170)

```
def _row(row: sa.RowMapping) -> Monitor
```

**Purpose**: Turns a raw database row into a Monitor object that the rest of the code can use safely. It is the single conversion point for monitor records read from the database.

**Data flow**: It receives a row mapping from SQLAlchemy, reads each monitor column, normalizes datetime fields through _aware, handles a missing last-probe time, and returns a Monitor value object with named fields.

**Call relations**: MonitorStore.armed, MonitorStore.arm, and MonitorStore.claim_due all call _row after reading or writing database rows. This keeps the rest of the monitor code from needing to know database row details.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 173–174)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a monitor can be claimed by a runner. A monitor is available if nobody has claimed it or if its old claim has expired.

**Data flow**: It receives the current time. It returns a SQL condition that checks whether the claim field is empty or the claim expiration is earlier than that time.

**Call relations**: MonitorStore.claim_due uses this condition when leasing work, and due_monitor_workspaces.due uses the same condition when deciding which workspaces need attention. Sharing the condition keeps both decisions aligned.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_due`  (lines 177–178)

```
def _due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a monitor needs attention now. A monitor is due if its next probe time has arrived or its final deadline has arrived.

**Data flow**: It receives the current time. It returns a SQL condition that checks whether either next_probe_at or deadline_at is at or before that time.

**Call relations**: MonitorStore.claim_due uses this when selecting monitors to lease, and due_monitor_workspaces.due uses it when finding candidate workspaces for the runner. This keeps workspace discovery and actual claiming based on the same idea of “due”.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_monitor_workspaces`  (lines 181–190)

```
def due_monitor_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates the job-system hook that finds workspaces with monitor work ready to run. It helps the background runner avoid scanning every workspace blindly.

**Data flow**: It defines a query-producing inner function that finds distinct workspace IDs with due, claimable monitors. It passes that function to owner_candidates, which wraps it in the job system’s workspace-candidate format.

**Call relations**: The monitor runner’s scheduling system can call the returned WorkspaceCandidates object to discover where work exists. Inside, the nested due function does the actual database selection.

*Call graph*: 1 external calls (owner_candidates).


##### `due_monitor_workspaces.due`  (lines 186–188)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the concrete database query for workspaces that currently have monitor work. It looks only for monitors that are both due and free to claim.

**Data flow**: It reads the current UTC time, builds the shared “claim available” and “due” conditions, selects workspace IDs from the monitor table, removes duplicates, and returns that SQL query.

**Call relations**: due_monitor_workspaces hands this function to owner_candidates. It calls _claim_available and _due so workspace discovery matches the later MonitorStore.claim_due leasing rules.

*Call graph*: calls 2 internal fn (_claim_available, _due); 2 external calls (now, select).


##### `MonitorStore.armed`  (lines 199–205)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Monitor, ...]
```

**Purpose**: Lists the currently armed monitors in this store’s workspace. It can list all monitors or only those belonging to one conversation.

**Data flow**: It receives an optional conversation ID. It builds a workspace-filtered SELECT query, adds the conversation filter if provided, reads matching rows ordered by name, converts each row with _row, and returns them as an immutable tuple.

**Call relations**: Callers use this when they need to show or inspect active watches. It reads through the ExtensionContext transaction and relies on _row to turn database rows into Monitor objects.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `MonitorStore.arm`  (lines 207–263)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, name: str, audience: str, command: str, interval_minutes: int, deadline_at: datetime, reason: str, next_steps: str, metadata: dict[str, Js
```

**Purpose**: Creates a new armed monitor in the database. This is what records a new watch after the command, baseline, schedule, and fire instructions have been chosen.

**Data flow**: It receives all monitor details, including conversation, agent, command, interval, deadline, baseline, and next probe time. It inserts a new row with fresh IDs, zeroed counters, no active claim, timestamps, and returns the inserted row as a Monitor.

**Call relations**: This is the write path for arming a monitor. It calls uuid4 to create the monitor ID, uses SQLAlchemy insert to store it, and passes the returned row through _row before handing it back.

*Call graph*: calls 1 internal fn (_row); 2 external calls (insert, uuid4).


##### `MonitorStore.claim_due`  (lines 265–303)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_MONITORS) -> tuple[Monitor, ...]
```

**Purpose**: Leases a batch of due monitors for this runner to process. Leasing prevents overlapping runners from doing the same monitor work at the same time.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum batch size. It finds due monitors in this workspace whose claims are free or expired, chooses the oldest due ones, stamps them with a new claim ID and expiration time, and returns the claimed monitors.

**Call relations**: The scheduled monitor runner calls this when it is ready to probe monitors. It uses _claim_available and _due to select only valid work, updates the database atomically, and converts returned rows through _row.

*Call graph*: calls 3 internal fn (_claim_available, _due, _row); 4 external calls (timedelta, select, update, uuid4).


##### `MonitorStore.quiet_tick`  (lines 305–315)

```
async def quiet_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records that a claimed monitor’s probe ran successfully and matched the baseline. No fire is posted; the monitor is simply counted and scheduled for its next check.

**Data flow**: It receives the claimed Monitor, the probe time, and the next probe time. It increases probes_run and quiet_streak, resets failure_streak to zero, keeps the skipped count unchanged, and passes the new values to _tick.

**Call relations**: MonitorRunner._tick calls this after a normal probe result. This function is a readable wrapper around _tick for the quiet case.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.failed_tick`  (lines 317–329)

```
async def failed_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records that a claimed monitor’s probe ran but the shell command failed, without yet firing the monitor. It advances the failure streak and breaks the quiet streak.

**Data flow**: It receives the claimed Monitor, the probe time, and the next probe time. It increases probes_run and failure_streak, resets quiet_streak to zero, keeps skipped unchanged, and sends those updated counts to _tick.

**Call relations**: MonitorRunner._tick calls this when a probe exits with a failure but has not crossed the fire threshold. It delegates the shared database update and claim release to _tick.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.skipped_tick`  (lines 331–342)

```
async def skipped_tick(self, row: Monitor, next_probe_at: datetime) -> None
```

**Purpose**: Records that a probe could not be run, for example because the client sandbox was unreachable. It counts the skip without treating it as a successful probe or a command failure.

**Data flow**: It receives the claimed Monitor and the next probe time. It leaves probe and streak counts as they were, increases skipped by one, keeps the previous last_probe_at value, and passes the updated state to _tick.

**Call relations**: MonitorRunner._tick calls this when it cannot execute the probe at all. Like the other tick helpers, it uses _tick for the actual database write.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore._tick`  (lines 344–376)

```
async def _tick(self, row: Monitor, *, probes_run: int, quiet_streak: int, failure_streak: int, skipped: int, last_probe_at: datetime | None, next_probe_at: datetime) -> None
```

**Purpose**: Performs the shared database update after a claimed monitor has been processed but did not fire. It also releases the claim so the monitor can be picked up again later.

**Data flow**: It receives the old Monitor plus the new counters and schedule fields. It first refuses to proceed if the monitor was not claimed. Then it updates only the row with the same monitor ID, workspace ID, and claim ID, writes the new counts and times, clears the claim, and updates the timestamp.

**Call relations**: quiet_tick, failed_tick, and skipped_tick all funnel into this function. The claim check in the WHERE clause makes sure a stale runner cannot overwrite a monitor that another runner has since claimed.

*Call graph*: called by 3 (failed_tick, quiet_tick, skipped_tick); 1 external calls (update).


##### `MonitorStore.claim_holds`  (lines 378–405)

```
async def claim_holds(self, row: Monitor) -> bool
```

**Purpose**: Checks whether a runner still owns the monitor it is about to fire. This prevents a fire from being delivered after a user has already disarmed the monitor, when the row is gone.

**Data flow**: It receives a claimed Monitor and rejects unclaimed ones. It looks for a row with the same monitor ID, workspace ID, and claim ID, locking it while checking, and returns true if that row still exists.

**Call relations**: MonitorRunner._fire calls this immediately before invoking the fire behavior. It does not retire or change the monitor; it is a final safety check before handing off to the firing flow.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `MonitorStore.retire`  (lines 407–419)

```
async def retire(self, row: Monitor) -> None
```

**Purpose**: Deletes a monitor after its fire has been delivered. This enforces the rule that an armed monitor ends in exactly one fire.

**Data flow**: It receives a claimed Monitor and rejects unclaimed ones. It deletes the database row only if the monitor ID, workspace ID, and claim ID all match, so an expired or stolen claim cannot remove someone else’s current work.

**Call relations**: MonitorRunner._fire calls this after successfully delivering a fire. The claim guard ties deletion to the runner that actually held the lease.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `MonitorStore.disarm`  (lines 421–429)

```
async def disarm(self, row: Monitor) -> bool
```

**Purpose**: Stops watching a monitor because a member requested it. It reports whether a row was actually removed.

**Data flow**: It receives a Monitor. It deletes the matching row in this workspace without requiring a claim, then returns true if exactly one row was deleted and false otherwise.

**Call relations**: This is the user-driven stop path, separate from retire, which is the fire-driven removal path. Because it does not require the runner’s claim, a user can stop a monitor even while a runner may be working on it.

*Call graph*: 1 external calls (delete).


### Objective planning
These files package the objectives extension and provide tools and durable records for plans, progress, blockers, evidence checks, and delegation.

### `extensions/objectives/ufo_ext_objectives/__init__.py`

`other` · `import time`

This is the package marker for the objectives extension. In Python, a folder with an `__init__.py` file can be imported as a package, which means other parts of the project can refer to this extension by name. Here, the file contains only a short documentation string: “The objectives extension.”

Its job is mostly structural, like a label on a folder. Without it, depending on the Python version and packaging setup, tools or import code might not recognize this directory in the expected way. The actual work of the objectives extension lives in other files in this package. This file simply announces that the package exists and provides a human-readable hint about what it is for.


### `extensions/objectives/ufo_ext_objectives/tools.py`

`domain_logic` · `tool calls during objective planning, progress recording, delegation, and status checks`

This file is the user-facing toolbox for the objectives extension. An objective is a piece of work that may last longer than one turn. It has named steps, and each step can include acceptance conditions: checks against real state, such as “this file exists,” “this file contains this text,” or “this command succeeds.” Without this file, agents could write plans and claim progress, but there would be no consistent way to prove that a step is actually complete.

The main idea is simple: a step does not close just because someone says it is done. When `record_step` is called with `did`, the file re-runs the step’s conditions in the sandbox and stores the result. If a condition fails, the response says exactly what is still unmet. This is like a checklist at a construction site: signing your name is not enough; the inspector still checks the doors, wiring, and smoke alarms.

The file also protects against weak plans. When a plan says a future step will create a file or text, `plan_objective` refuses that condition if it is already true, because it would prove nothing about the new work. Commands are treated differently because a test suite may already pass before work starts and still be a useful guard later. The file can also read an objective, refresh its checks, and launch independent steps in parallel subagents.

#### Function details

##### `_require_ext`  (lines 107–110)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small guard makes sure the tool call has the extension context it needs. The extension context is the object that gives access to shared extension services, such as database transactions.

**Data flow**: It receives the current tool context. If the context contains an extension context, it returns it unchanged. If not, it stops the call by raising an error, because the objective tools cannot safely work without that shared state.

**Call relations**: The main tool functions call this at their start before they touch stored objectives. It acts like checking that you have the right key before trying to open the filing cabinet.

*Call graph*: called by 4 (plan_objective, read_objective, record_step, run_independent_steps).


##### `render`  (lines 113–128)

```
def render(view: ObjectiveView) -> str
```

**Purpose**: This turns an objective’s stored state into a readable text report. It is used whenever a tool needs to show the agent what the objective currently looks like.

**Data flow**: It receives an `ObjectiveView`, which is a snapshot of one objective. It reads the objective name, directive, attempt counts, each step’s state, its required conditions, recent events, and any failed verdicts. It returns one plain text block summarizing all of that.

**Call relations**: After planning, recording, or reading an objective, the tool functions call `render` to turn internal data into a response the agent can act on. For condition text, it asks `condition_summary` to describe each check in human-readable form.

*Call graph*: called by 3 (plan_objective, read_objective, record_step); 1 external calls (condition_summary).


##### `evaluate`  (lines 131–135)

```
async def evaluate(ctx: ToolContext, step: StepView) -> tuple[ConditionVerdict, ...]
```

**Purpose**: This checks all acceptance conditions for one step and returns the results. It is the bridge between a claimed attempt and proof from real state.

**Data flow**: It receives the tool context and a step view. For each condition attached to the step, it calls `_verdict`, gathers the verdicts, and returns them as an ordered tuple. It does not itself change stored objective state.

**Call relations**: `record_step` uses this when someone says a step was done, and `read_objective` uses it to refresh attempted steps. It delegates the actual one-condition check to `_verdict`.

*Call graph*: calls 1 internal fn (_verdict); called by 2 (read_objective, record_step).


##### `_verdict`  (lines 138–162)

```
async def _verdict(ctx: ToolContext, condition: Condition, phase: str) -> ConditionVerdict
```

**Purpose**: This tests one acceptance condition against the sandbox and says whether it currently holds. It also records a metric so the system can later see how often checks passed or failed.

**Data flow**: It receives a tool context, one condition, and a phase name such as planning or recording. It converts the condition into a shell command: for example, checking whether a path exists, whether a file contains exact text, or whether a supplied command exits successfully. It runs that command in the sandbox with a timeout, turns the exit code into true or false, emits a measurement, and returns a `ConditionVerdict` with the condition, the result, and a short explanation.

**Call relations**: `evaluate` calls this for normal step checks. `plan_objective` also calls it during planning to reject file-based conditions that are already true before the work starts. It uses `condition_summary` to make messages understandable, and `shlex.quote` to safely place paths and text into shell commands.

*Call graph*: called by 2 (evaluate, plan_objective); 4 external calls (__init__, quote, emit_metric, condition_summary).


##### `plan_objective`  (lines 165–220)

```
async def plan_objective(ctx: ToolContext, args: PlanObjectiveInput) -> ToolResult
```

**Purpose**: This records or revises an objective plan, while rejecting certain acceptance checks that would be meaningless. It is used when an agent wants longer-running work to survive across turns.

**Data flow**: It receives the current tool context and a planning request containing a name, directive, steps, and timeline description. It first loads any existing objective with that name. For each new or unattempted step, it checks file-based conditions that claim the work will produce a file or text. If such a condition is already true, it returns an error explaining why the plan is weak. Otherwise, it stores the plan through the objectives store and returns a rendered view of the saved objective.

**Call relations**: This is the handler behind the `plan_objective` tool definition. It calls `_require_ext` to get extension services, `_verdict` to test proposed conditions during planning, and `render` to show the final saved plan. It works with the `Objectives` store to read and write the persistent objective record.

*Call graph*: calls 3 internal fn (_require_ext, _verdict, render); 5 external calls (__init__, __init__, __init__, agent_current, condition_summary).


##### `record_step`  (lines 223–271)

```
async def record_step(ctx: ToolContext, args: RecordStepInput) -> ToolResult
```

**Purpose**: This records that a step was attempted or is blocked. If the step was attempted, it checks the step’s acceptance conditions before treating it as complete.

**Data flow**: It receives the tool context and a record request naming the objective, step, kind of record, and evidence. It loads the objective, verifies that the named step exists, and writes the event. If the kind is `blocked`, it records the block and returns a message, including a special message if the same block was already recorded. If the kind is `did`, it evaluates the step’s conditions, stores the check results, refreshes the objective, adds the latest verdicts to the displayed view, emits a metric, and returns the rendered objective.

**Call relations**: This is the handler behind the `record_step` tool definition. It relies on `_require_ext` for extension access, the `Objectives` store for persistence, `evaluate` for proof checks, `_with_verdicts` to prepare the response view, and `render` to show the result. It is the key place where a claim of progress is tested against reality.

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 5 external calls (__init__, __init__, __init__, agent_current, emit_metric).


##### `run_independent_steps`  (lines 274–320)

```
async def run_independent_steps(ctx: ToolContext, args: RunIndependentStepsInput) -> ToolResult
```

**Purpose**: This launches every ready independent step of an objective in separate background subagents. It helps parallel work happen without the current turn manually spawning each task one by one.

**Data flow**: It receives the tool context and a request naming the objective and subagent profile. It loads the objective, finds steps marked runnable, and returns an error if the objective does not exist. If no independent step is ready, it explains that there is nothing to dispatch. Otherwise, for each runnable step it starts a background subagent with the objective directive and that step title, records the dispatched turn id, emits a metric, and returns instructions saying that results will come back later.

**Call relations**: This is the handler behind the `run_independent_steps` tool definition. It calls `_require_ext` and reads from the `Objectives` store, then uses `ToolContext.spawn` to create subagent work. It does not record completion itself; after subagents report back, `record_step` is expected to capture each result.

*Call graph*: calls 1 internal fn (_require_ext); 6 external calls (__init__, __init__, __init__, spawn, agent_current, emit_metric).


##### `read_objective`  (lines 323–342)

```
async def read_objective(ctx: ToolContext, args: ReadObjectiveInput) -> ToolResult
```

**Purpose**: This shows the current state of an objective and refreshes checks for steps that have already been attempted. It lets an agent see what is done, what is blocked, and what still fails.

**Data flow**: It receives the tool context and a request naming the objective. It loads the objective from storage and returns an error if it cannot be found. For every attempted step that has acceptance conditions, it re-evaluates those conditions, stores the latest verdicts, updates the displayed view, and finally returns a rendered text summary.

**Call relations**: This is the handler behind the `read_objective` tool definition. It uses `_require_ext` and the `Objectives` store to access persisted state, `evaluate` to refresh real-world checks, `_with_verdicts` to attach the fresh results to the response view, and `render` to make the status readable.

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 4 external calls (__init__, __init__, __init__, agent_current).


##### `_with_verdicts`  (lines 345–355)

```
def _with_verdicts(view: ObjectiveView, title: str, verdicts: tuple[ConditionVerdict, ...]) -> ObjectiveView
```

**Purpose**: This creates a copy of an objective view with fresh verdicts attached to one named step. It is used for display, so the response can show the latest check results immediately.

**Data flow**: It receives an objective view, a step title, and a tuple of verdicts. It builds a new objective view where the matching step has those verdicts, while all other steps stay the same. It returns the new view and does not mutate the original object.

**Call relations**: `record_step` uses this after checking a just-attempted step, and `read_objective` uses it while refreshing attempted steps. It relies on `dataclasses.replace` to make updated copies rather than editing the existing view in place.

*Call graph*: called by 2 (read_objective, record_step); 1 external calls (replace).


### `extensions/objectives/ufo_ext_objectives/store.py`

`domain_logic` · `active during objective planning, turn startup, progress recording, and condition checking`

An objective is a goal broken into steps. This file stores those goals, their steps, and the history of what happened to each step in database tables. The important idea is that events are appended, not edited. Like a lab notebook, each attempt, block, and check result is kept as evidence, so later turns can see what really happened instead of trusting a summary that may have changed.

The file defines the allowed completion conditions for a step: a file exists, a file contains text, or a command succeeds. These conditions are written into the plan. Once a step has been attempted, its conditions are frozen, so a worker cannot make the test easier after discovering the work is hard.

It also builds read-only views of objectives and steps. These views calculate useful states such as pending, attempted, done, blocked, or unmet from the stored events and latest check results. The `Objectives` store is the main interface: it can find an objective, create or revise a plan, record an attempt or block, record condition-check results, and rebuild a complete view from the database. Without this file, the system would lose the reliable audit trail that lets objectives continue safely across turns, heartbeats, and subagent hand-backs.

#### Function details

##### `StepView.attempted`  (lines 157–158)

```
def attempted(self) -> bool
```

**Purpose**: Tells whether anyone has recorded actually doing work on this step. This matters because a step with completion conditions should not be treated as failed or done before it has even been tried.

**Data flow**: It reads the step’s stored events. If any event is a `did` event, meaning an attempt was recorded, it returns true; otherwise it returns false. It does not change anything.

**Call relations**: Other step decisions use this as a basic fact. `StepView.state` uses it to separate untouched steps from attempted ones, and `ObjectiveView.runnable` uses it to avoid dispatching independent work that has already been tried.


##### `StepView.open_block`  (lines 161–166)

```
def open_block(self) -> StepEvent | None
```

**Purpose**: Finds the currently active block, if the step’s latest event says the step is blocked. A block is like an unanswered question: it should not be asked again and again just because the system wakes up repeatedly.

**Data flow**: It looks at the step’s event history and checks only the newest event. If that newest event is a `blocked` event, it returns that event; otherwise it returns nothing. It does not write to storage.

**Call relations**: The step state calculation uses this idea indirectly by checking the latest event. `ObjectiveView.runnable` uses it to avoid launching a blocked step, and `Objectives.record` uses it to avoid recording the same open block repeatedly.


##### `StepView.state`  (lines 169–184)

```
def state(self) -> str
```

**Purpose**: Turns a step’s event history and check results into a plain status such as pending, attempted, done, blocked, or unmet. This is the central rule that decides what progress means.

**Data flow**: It reads the step’s events, declared conditions, and latest verdicts. A latest block makes the step blocked; no attempt makes it pending; an attempted step with no conditions is done; an attempted step with unchecked or incomplete conditions is still attempted; checked conditions make it done only if every verdict passes, otherwise unmet.

**Call relations**: Objective-level views depend on this property. `ObjectiveView.confirmed` counts steps whose state is done, and `ObjectiveView.frontier` uses it to find all unfinished steps.


##### `ObjectiveView.attempts`  (lines 199–200)

```
def attempts(self) -> int
```

**Purpose**: Counts how many recorded work attempts exist across all steps in the objective. It gives a simple sign of effort spent.

**Data flow**: It scans every step and every event inside those steps. Each `did` event adds one to the count. The result is a number, and nothing is changed.

**Call relations**: This is a reporting view on top of the stored event history. It helps callers notice patterns such as many attempts without more confirmed completed steps.


##### `ObjectiveView.confirmed`  (lines 203–204)

```
def confirmed(self) -> int
```

**Purpose**: Counts how many steps are currently confirmed as done. This is the counterpart to attempts: it shows verified progress, not just activity.

**Data flow**: It asks each step for its computed state. Every step whose state is `done` adds one to the count. The result is a number, with no database writes.

**Call relations**: It relies on `StepView.state`, which applies the rules about attempts, blocks, and condition verdicts. Callers can compare this with `ObjectiveView.attempts` to understand whether work is closing steps or just repeating.


##### `ObjectiveView.runnable`  (lines 207–215)

```
def runnable(self) -> tuple[StepView, ...]
```

**Purpose**: Identifies unfinished steps that can be started in parallel right now. It only includes steps that the plan marked as independent, that have not yet been attempted, and that are not currently blocked.

**Data flow**: It starts from the objective’s unfinished `frontier`. From there it keeps only steps marked independent, not attempted, and without an open block. It returns those steps as a tuple and changes nothing.

**Call relations**: This uses `ObjectiveView.frontier`, `StepView.attempted`, and `StepView.open_block` to decide what a dispatcher may safely fan out. It is meant for the part of the system that chooses which steps to run next.


##### `ObjectiveView.frontier`  (lines 218–219)

```
def frontier(self) -> tuple[StepView, ...]
```

**Purpose**: Returns all steps that are not yet done. This is the objective’s current work queue in plain form.

**Data flow**: It reads every step and asks for its state. Any step whose state is not `done` is included in the returned tuple. No storage is changed.

**Call relations**: This property sits above `StepView.state`. `ObjectiveView.runnable` narrows this frontier further to find unfinished independent steps that can be dispatched immediately.


##### `condition_summary`  (lines 222–229)

```
def condition_summary(condition: Condition) -> str
```

**Purpose**: Turns a completion condition into a short human-readable sentence. This is useful when showing people or agents what must be true for a step to count as complete.

**Data flow**: It receives one condition object. Depending on whether the condition is file existence, file content, or command success, it formats the relevant path, text, or command into a readable string. It returns that string and changes nothing.

**Call relations**: This is a small helper for presenting condition data. It does not write to the store; it translates the same condition types that planning and checking use.


##### `Objectives.named`  (lines 239–253)

```
async def named(self, conversation_id: UUID, name: str) -> ObjectiveView | None
```

**Purpose**: Finds one objective by conversation and name within the current workspace. Scoping by conversation prevents a subagent or another conversation from accidentally picking up someone else’s objective with the same name.

**Data flow**: It receives a conversation identifier and an objective name, then queries the objective table for a matching row in this workspace. If none is found, it returns nothing. If one is found, it passes the row to `_view` to build a full objective view with steps, events, and checks.

**Call relations**: This is called directly by `Objectives.plan` before creating or revising a plan. It hands database results to `Objectives._view`, which performs the fuller reconstruction of the objective.

*Call graph*: calls 1 internal fn (_view); called by 1 (plan); 1 external calls (select).


##### `Objectives.on_conversation`  (lines 255–267)

```
async def on_conversation(self, conversation_id: UUID) -> ObjectiveView | None
```

**Purpose**: Finds the most recently created objective for a conversation in the current workspace. This gives callers a simple way to resume the current objective without already knowing its name.

**Data flow**: It receives a conversation identifier and queries the database for objectives in that conversation, newest first, taking only one. If there is no objective, it returns nothing. If there is one, it asks `_view` to expand the row into a complete readable objective.

**Call relations**: Like `Objectives.named`, this is a lookup entry into the store. It relies on `Objectives._view` to gather the related steps, events, and latest checks after the initial objective row is found.

*Call graph*: calls 1 internal fn (_view); 1 external calls (select).


##### `Objectives.plan`  (lines 269–332)

```
async def plan(self, conversation_id: UUID, name: str, directive: str, steps: tuple[StepPlan, ...]) -> ObjectiveView
```

**Purpose**: Creates a new objective plan or revises an existing one. It preserves the integrity of already-attempted steps by keeping their original completion conditions.

**Data flow**: It receives the conversation, objective name, directive, and planned steps. First it looks for an existing objective with `Objectives.named`. If none exists, it inserts a new objective row. If one exists, it updates the directive, keeps steps that already have event history, removes unstarted steps that are no longer in the plan, and freezes conditions for attempted steps. Then it updates or inserts each planned step and finally reloads the objective view to return the current stored version.

**Call relations**: This is the main write path for planning. It calls `Objectives.named` before and after the database changes, and uses database insert, update, and delete operations to make the durable record match the new plan without erasing important history.

*Call graph*: calls 1 internal fn (named); 5 external calls (delete, insert, true, update, uuid4).


##### `Objectives.record`  (lines 334–354)

```
async def record(self, step: StepView, kind: str, actor_turn_id: UUID, evidence: str) -> bool
```

**Purpose**: Adds an event to a step, such as an attempt or a block. It refuses to duplicate the same still-open block, so repeated wake-ups do not spam the history with the same unanswered issue.

**Data flow**: It receives a step view, event kind, actor turn identifier, and evidence text. It trims the evidence to the maximum stored length. If the event is a block identical to the current open block, it writes nothing and returns false. Otherwise it inserts a new event row and returns true.

**Call relations**: This is how work outcomes become durable facts. It uses the step’s `open_block` property before writing, then appends to the event table so later calls to `Objectives._view` can reconstruct the step’s history.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives.checked`  (lines 356–383)

```
async def checked(self, step: StepView, verdicts: tuple[ConditionVerdict, ...], actor_turn_id: UUID) -> None
```

**Purpose**: Stores the extension’s own verdicts after it evaluates a step’s completion conditions. These are observations by the system, not claims made by the worker.

**Data flow**: It receives a step, a tuple of condition verdicts, and the actor turn identifier. It converts each verdict into JSON-friendly data containing the condition, whether it held, and the detail message. Then it inserts a new check row. It does not replace older checks; the history remains visible, while the latest check is used when reading.

**Call relations**: Condition-checking code calls this after evaluating whether files exist, text appears, or commands succeed. `Objectives._view` later reads these stored checks and uses the latest one for each step.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives._view`  (lines 385–436)

```
async def _view(self, row: sa.Row[tuple[object, ...]]) -> ObjectiveView
```

**Purpose**: Rebuilds a complete in-memory picture of an objective from database rows. It turns scattered stored records into the `ObjectiveView` and `StepView` objects that the rest of the extension can reason about.

**Data flow**: It receives an objective database row. It loads that objective’s steps, then all events for those steps, then all condition checks for those steps. It groups events by step, keeps the latest check per step, parses stored condition JSON into condition objects, parses stored verdict JSON into verdict objects, and returns a full `ObjectiveView` containing ordered `StepView` entries.

**Call relations**: `Objectives.named` and `Objectives.on_conversation` both call this after finding an objective row. It calls `_conditions` and `_verdicts` to turn raw JSON from the database back into typed Python objects.

*Call graph*: calls 2 internal fn (_conditions, _verdicts); called by 2 (named, on_conversation); 4 external calls (__init__, __init__, __init__, select).


##### `_conditions`  (lines 439–453)

```
def _conditions(payload: object) -> tuple[Condition, ...]
```

**Purpose**: Parses stored condition data back into the correct condition objects. This protects the rest of the code from dealing with raw JSON dictionaries.

**Data flow**: It receives an unknown payload, usually read from the database. If the payload is not a list, it returns an empty tuple. For each list item, it looks at the `kind` field and validates it as a file-exists, file-contains, or command-succeeds condition. Unknown kinds raise an error instead of being silently ignored.

**Call relations**: `Objectives._view` uses this when rebuilding steps from stored plans. `_verdicts` also uses it when rebuilding the condition attached to each stored verdict.

*Call graph*: called by 2 (_view, _verdicts).


##### `_verdicts`  (lines 456–467)

```
def _verdicts(payload: object) -> tuple[ConditionVerdict, ...]
```

**Purpose**: Parses stored condition-check results back into `ConditionVerdict` objects. This lets step state calculations use the latest saved check as structured data.

**Data flow**: It receives an unknown payload, usually a JSON list from the check table. If the payload is not a list, it returns an empty tuple. For each dictionary item, it parses the embedded condition with `_conditions`, converts the held flag to a boolean, converts the detail to text, and returns the resulting verdict objects.

**Call relations**: `Objectives._view` calls this when attaching the latest stored check to each step. It depends on `_conditions` so verdicts and planned conditions are interpreted by the same rules.

*Call graph*: calls 1 internal fn (_conditions); called by 1 (_view); 1 external calls (__init__).


### Daily sweep briefs
This file orchestrates private daily briefs by gathering member context, delegating scout investigations, and scheduling the drafting turn.

### `extensions/sweep/ufo_ext_sweep/manifest.py`

`orchestration` · `scheduled daily job and agent tool use`

This file is the control center for the daily-brief feature. Its job is to make sure each member gets at most one useful brief per local day, based on what has changed since the last successful brief. Without it, the system would not know when to start a brief, what information to collect, how to avoid repeating old items, or how to keep the scheduled agent from making real changes without approval.

It keeps a small database ledger called `sweep_edition`. Think of it like a delivery log for a newspaper route: for each member and date, it records whether today’s edition is pending, completed, or failed, which conversation turn produced it, and which source items and findings were already used.

When the scheduled job runs, `_tick` checks every seated member. If it is at least 8 AM in that member’s timezone, it creates or retries that day’s edition and invokes the `daily-brief` agent. That agent can call the `sweep_newspaper` tool, implemented by `_sweep`. `_sweep` collects changed private context, optionally fetches public pages mentioned in that context, splits the material into four scout categories, and runs bounded subagents so no scout receives too much input. It then removes repeated findings, records what was safely processed, and returns structured findings for the final brief.

A safety hook, `_draft_only`, blocks scheduled briefs from directly updating tasks or memory. They may propose drafts, but a member must approve changes.

#### Function details

##### `local_edition_date`  (lines 125–127)

```
def local_edition_date(now: datetime, timezone: str) -> date | None
```

**Purpose**: Decides whether a member is ready to receive today’s daily brief. A brief is only eligible once it is at least 8 AM in that member’s own timezone.

**Data flow**: It receives the current time and a timezone name. It converts the current time into that timezone, checks the local clock, and returns the local date if the time is 8 AM or later. If it is still before 8 AM, it returns nothing.

**Call relations**: The scheduled job `_tick` calls this for each member before creating or retrying an edition. This keeps briefs from being sent too early for people in different parts of the world.

*Call graph*: called by 1 (_tick); 3 external calls (astimezone, time, ZoneInfo).


##### `_profile`  (lines 130–141)

```
def _profile(name: str) -> SubagentProfile
```

**Purpose**: Builds the definition for one scout subagent. Each scout is a small, focused helper that reads a slice of context and returns possible findings for the daily brief.

**Data flow**: It receives a scout name, such as `work` or `public-context`. It turns that name into a subagent profile with a prompt, input and output shapes, model choice, and safety settings. The result is a ready-to-register scout description.

**Call relations**: The `manifest` function calls `_profile` once for each scout name when registering the extension. Later, `_sweep` refers to those registered scout profiles when it spawns scout runs.

*Call graph*: called by 1 (manifest); 1 external calls (__init__).


##### `_bounded`  (lines 144–163)

```
def _bounded(records: tuple[MemberContextRecord, ...]) -> tuple[ContextRecord, ...]
```

**Purpose**: Shrinks a list of member context records so a scout receives only a safe, limited amount of text. This prevents overloading the model with too much input.

**Data flow**: It receives member context records. For each record, it trims the text, counts roughly how much space the title, text, and reference use, and stops once the shared size limit would be exceeded. It returns simplified `ContextRecord` objects that fit inside the limit.

**Call relations**: `_sweep` calls this after dividing records into scout groups. The bounded records are what the inner `_sweep.scout` helper sends to each scout subagent.

*Call graph*: called by 1 (_sweep); 1 external calls (__init__).


##### `_digest`  (lines 166–167)

```
def _digest(value: str) -> str
```

**Purpose**: Creates a stable fingerprint for a piece of text. This is used to make a repeatable key for fetched public material.

**Data flow**: It receives a string, encodes it, runs it through SHA-256 hashing, and returns the hash as text. The output is a compact identifier that changes if the input text changes.

**Call relations**: `_public_records` calls this when it turns fetched public web pages into context records. The digest helps create `stable_subject_key` values so the ledger can tell whether public content is new or repeated.

*Call graph*: called by 1 (_public_records); 1 external calls (sha256).


##### `_prior_ledgers`  (lines 170–204)

```
async def _prior_ledgers(ext: ExtensionContext, member_id: UUID, now: datetime) -> tuple[dict[str, datetime], dict[str, datetime]]
```

**Purpose**: Reads recent completed editions from the database so the brief does not repeat the same source material or findings too often.

**Data flow**: It receives the extension context, a member ID, and the current time. It queries completed editions from the recent retention window, then builds two lookup tables: one for source input keys and one for finding keys, each pointing to when that key was last completed. It returns both lookup tables.

**Call relations**: `_sweep` calls this before choosing changed records and before deciding which scout findings to emit. The information from `_prior_ledgers` is the memory that keeps daily briefs from sounding like yesterday’s brief.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_sweep); 1 external calls (select).


##### `_changed_records`  (lines 207–217)

```
def _changed_records(records: tuple[MemberContextRecord, ...], prior: dict[str, datetime], now: datetime) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Chooses which member context records are worth showing to the scouts today. It filters out items that were already covered recently, while allowing open tasks and objectives to reappear after a shorter waiting period.

**Data flow**: It receives context records, a lookup of previously used input keys, and the current time. It keeps only one record per stable subject key, checks when that subject was last seen, applies the correct waiting period, and returns the remaining records sorted newest first.

**Call relations**: `_sweep` calls this after loading recent member context and prior ledger data. Its output becomes the private material that is divided among the scouts.

*Call graph*: called by 1 (_sweep).


##### `_public_records`  (lines 220–259)

```
async def _public_records(ctx: ToolContext, records: tuple[MemberContextRecord, ...], now: datetime) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Finds public web links mentioned in the member’s changed context and fetches short summaries for them when a search provider is available. This gives the brief limited outside context without letting private data leak into broad search.

**Data flow**: It receives the tool context, changed member records, and the current time. It scans references and text for web URLs, rejects unsafe or private-looking hosts, normalizes each URL to the public site root, and fetches a small amount of content for a limited number of sources. It returns those fetched pages as public `MemberContextRecord` objects.

**Call relations**: `_sweep` calls this after finding changed private records. The returned public records feed only the `public-context` scout, separate from the private work, conversation, page, and artifact groups.

*Call graph*: calls 1 internal fn (_digest); called by 1 (_sweep); 6 external calls (__init__, __init__, gather, ip_address, urlsplit, urlunsplit).


##### `_sweep`  (lines 262–420)

```
async def _sweep(ctx: ToolContext, _args: SweepInput) -> ToolResult
```

**Purpose**: Implements the `sweep_newspaper` tool that the scheduled daily-brief agent calls. It gathers the day’s candidate material, runs scouts, filters repeated findings, updates the edition ledger, and returns structured brief ingredients.

**Data flow**: It starts with a tool call from a scheduled member. It verifies that the call belongs to a registered scheduled edition, finds the last completed cursor, loads recent member context, removes already-covered records, fetches safe public context, and splits everything into scout groups. It runs the scouts in parallel, accepts the edition if enough scouts return, removes duplicate or too-recent findings, writes candidate input and finding keys back to the database, and returns JSON containing findings, coverage notes, and any missing scouts.

**Call relations**: The `manifest` function registers `_sweep` as the handler for the `sweep_newspaper` tool. During an agent run started by `_tick`, the daily-brief agent calls this tool once to obtain the raw ingredients for the final private brief. `_sweep` relies on `_prior_ledgers`, `_changed_records`, `_public_records`, `_bounded`, and its inner scout helper to do the work in stages.

*Call graph*: calls 4 internal fn (_bounded, _changed_records, _prior_ledgers, _public_records); 7 external calls (__init__, __init__, gather, now, dumps, select, update).


##### `_sweep.scout`  (lines 306–338)

```
async def scout(name: str) -> tuple[ScoutOutput, bool]
```

**Purpose**: Runs one named scout subagent and cleans up its answer. It makes sure a scout only cites references that were actually included in that scout’s input.

**Data flow**: It receives a scout name from the surrounding `_sweep` process. It looks up that scout’s bounded records, sends them to the matching subagent profile, validates the returned findings and coverage text, removes any references that were not in the supplied records, and returns the cleaned scout output plus a flag saying whether the result is safe to use for ledger progress.

**Call relations**: `_sweep` launches this helper in parallel for each scout category. Its cleaned outputs are later combined by `_sweep` into one set of candidate findings for the final brief.

*Call graph*: 2 external calls (__init__, __init__).


##### `_finalize`  (lines 423–459)

```
async def _finalize(ctx: ExtensionContext, now: datetime) -> None
```

**Purpose**: Closes out pending editions whose agent turns have already ended. It marks them completed only if the turn finished successfully and the sweep tool advanced its cursor.

**Data flow**: It receives the extension context and the current time. It reads pending edition rows joined to their conversation turns, checks whether each turn is done, failed, or cancelled, and updates the edition status to either completed or failed. Completed editions also receive a completion timestamp.

**Call relations**: `_tick` calls `_finalize` at the start of each scheduled run. This cleanup step makes sure old pending rows do not block future daily briefs or retries.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_tick); 2 external calls (select, update).


##### `_draft_only`  (lines 462–476)

```
async def _draft_only(ctx: HookContext) -> HookOutcome
```

**Purpose**: Protects members from automatic changes during a scheduled daily brief. It blocks the scheduled brief turn from directly using tools that update tasks or memory.

**Data flow**: It receives a hook context before a tool is used. It checks whether there is an active turn, whether the payload is a pre-tool-use event, and whether that turn belongs to a scheduled sweep edition. If so, it returns a denial explaining that the brief can only propose drafts for approval. Otherwise it returns nothing and lets the tool call proceed.

**Call relations**: The `manifest` function registers `_draft_only` as a pre-tool-use hook for the todo-list and memory update tools. It is consulted whenever the scheduled daily-brief agent tries to use those tools.

*Call graph*: 2 external calls (__init__, select).


##### `_tick`  (lines 479–576)

```
async def _tick(ctx: ExtensionContext, now: datetime | None=None) -> None
```

**Purpose**: Runs the daily scheduling loop for Sweep. It decides which members need today’s brief, records an edition row, and starts the daily-brief agent for each eligible member.

**Data flow**: It receives the extension context and optionally a current time. It first finalizes older pending editions, then pages through seated members. For each member, it checks the member’s local date and time, skips members who are too early or already completed, creates or resets the edition database row, and invokes the daily-brief agent with an idempotency key so repeated scheduler runs do not create duplicate work. If agent invocation fails because it is not allowed or invalid, it marks the edition failed.

**Call relations**: The `manifest` function registers `_tick` as the scheduled job handler. `_tick` calls `_finalize` for cleanup, `local_edition_date` for timezone-aware eligibility, database operations for edition state, and the extension context’s agent invocation method to start the actual brief-making conversation.

*Call graph*: calls 5 internal fn (invoke_agent_for_member, seated_members, transaction, _finalize, local_edition_date); 4 external calls (now, insert, select, update).


##### `manifest`  (lines 579–627)

```
def manifest() -> Manifest
```

**Purpose**: Declares the Sweep extension to the host system. It tells the platform what agent, tool, scheduled job, hook, scouts, skill files, and permissions this extension needs.

**Data flow**: It takes no input. It constructs a `Manifest` object containing the daily-brief agent specification, the `sweep_newspaper` tool, the scheduled daily job, the safety hook, four scout subagent profiles, the daily-brief skill path, and required capabilities such as member-context reading and search providers. It returns that manifest for the platform to load.

**Call relations**: This is the file’s registration point. The platform calls `manifest` when loading the extension, and the returned object wires `_tick`, `_sweep`, `_draft_only`, and `_profile`-built scouts into the larger system.

*Call graph*: calls 1 internal fn (_profile); 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, seated_member_workspaces).
