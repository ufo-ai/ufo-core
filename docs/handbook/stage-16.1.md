# Conversation wakeups and recurring work  `stage-16.1`

This stage is the system’s alarm clock and night watch. It is shared background support that wakes conversations when time, outside changes, or unfinished child work need attention. The scheduled task tools let agents create recurring jobs and pause a workflow until a reply or timeout. Schedules, cron, scheduled_fire, and the runner store those jobs, calculate their next times, label each firing, claim due work safely, and send it into the right conversation once. Pauses and the pause runner do the same for sleeping workflows. The conversation slot shows users the safe “Automations” view.

Monitors are saved watches that rerun a shell command, compare new output with a saved baseline, and wake the agent on change, repeated failure, or deadline. The monitor tool creates them, monitor_kind exposes them as objects, monitors stores their state, and monitor_runner performs the timed checks.

Sources triggers wake conversations when shared sources change. Sweep registers a daily brief job and its helpers. Candidates safely finds workspaces with pending work, while delivery retries lost child-task results so parent conversations do not wait forever.

## Files in this stage

### Scheduled and pause tools
Agent-facing tools expose recurring scheduled tasks and pause-until-reply-or-timeout waits as safe workflow primitives.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `request handling and tool execution`

This file is the bridge between the agent-facing world and the durable storage behind scheduled tasks. A scheduled task is like a recurring calendar reminder for the agent: it has a cron schedule, a prompt to run, and a conversation where the result should appear. The file defines the shape of that task, checks that its expiry time is valid, lists tasks a member is allowed to see, shows details and status, creates or updates tasks, and cancels them.

A major theme here is safety. Tasks run later using the creator’s authority, so not everyone who can see a task may edit its prompt. The creator can change the content. An admin can change timing, expiry, pause state, or delete the task, but cannot rewrite another member’s prompt or description. Visibility follows the conversation the task reports into: if the conversation is shared, the task can be seen more widely; if it is private, the prompt is hidden from others.

The second feature, `pause_and_wait`, is not exposed as a managed object. It records a waiting workflow in a pause table and returns instructions telling the agent to stop after replying. Later, either a new member message or a timer can resume the workflow. The careful part is race avoidance: the pause records both where the conversation turn was and where message arrival had reached, so the system can later decide correctly whether a new message arrived after the pause was armed.

#### Function details

##### `ScheduledTaskSpec.validate_utc_expiry`  (lines 103–106)

```
def validate_utc_expiry(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This checks that a task expiry time, if supplied, is written as a UTC timestamp. UTC is a single shared time standard, which avoids confusion when scheduled work runs across time zones.

**Data flow**: It receives the proposed `expires_at` value from the task specification. If there is no expiry, it leaves it alone. If there is an expiry, it checks the timestamp’s time zone offset; a missing time zone or any offset other than zero is rejected. The valid timestamp is returned unchanged.

**Call relations**: This runs automatically when a `ScheduledTaskSpec` is built or validated. It protects later scheduling code from receiving a local or ambiguous time that could make a task expire at the wrong moment.

*Call graph*: 2 external calls (utcoffset, timedelta).


##### `_require_scheduler`  (lines 127–128)

```
def _require_scheduler(ext: ExtensionContext | None) -> ScheduleStore
```

**Purpose**: This is a small doorway to the scheduled-task storage layer. It makes sure the scheduled-tasks extension context exists, then creates a `ScheduleStore`, which is the object used to read and write scheduled task records.

**Data flow**: It receives an optional extension context. It first passes that context through `_require_ext`, which either returns a real context or raises an error. With that context, it creates and returns a `ScheduleStore` connected to the extension’s storage.

**Call relations**: Most task operations call this right before they need stored task data: listing rows, reading status, finding a task, creating or updating one, deleting one, or listing tasks for a conversation. It keeps all those callers from each having to repeat the same context check.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_apply_owned, _delete_owned, _find, _rows, _status, member_conversation_rows); 1 external calls (__init__).


##### `_require_ext`  (lines 131–134)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This enforces that code using scheduled-task storage is actually running inside the scheduled-tasks extension. Without that context, the file would not know which database tables or extension services to use.

**Data flow**: It receives an optional extension context. If the value is missing, it raises a runtime error with a clear message. If the value is present, it returns it unchanged.

**Call relations**: `_require_scheduler` uses this before building a schedule store, and `pause_and_wait` uses it before writing a pause record. It is the shared guardrail for both features in this file.

*Call graph*: called by 2 (_require_scheduler, pause_and_wait).


##### `_summary`  (lines 137–138)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: This creates the short one-line text shown for a scheduled task in lists. It combines the cron schedule with either the task description or, if there is no description, the prompt.

**Data flow**: It receives a stored scheduled task. It builds text in the form `schedule — description or prompt`, then cuts it down to the configured maximum length. The result is a compact summary string.

**Call relations**: `ScheduledTaskObjects._rows` calls this when it is building list rows for viewers who are allowed to see the task’s content. If the content is private to the viewer, `_rows` uses a private placeholder instead.

*Call graph*: called by 1 (_rows).


##### `_owner`  (lines 141–149)

```
def _owner(listed: ListedTask) -> GeneratedObjectOwner
```

**Purpose**: This describes who owns a scheduled task and how widely it is shared. Ownership matters because the object framework uses it to decide who can see, edit, or delete the task.

**Data flow**: It receives a listed task, including the stored task and the audience of the conversation it reports into. It takes the creator’s member ID, turns the conversation audience into a shared-visibility value, and uses the task ID as the generation marker. It returns a `GeneratedObjectOwner` record.

**Call relations**: Task listing and conversation-row code call this when they need to plug scheduled tasks into the generic object permission system. It lets the base object machinery apply the same visibility rules consistently across list pages and conversation surfaces.

*Call graph*: called by 2 (_rows, member_conversation_rows); 2 external calls (__init__, subject_shared).


##### `ScheduledTaskObjects._admin_can_apply`  (lines 170–173)

```
def _admin_can_apply(self, _old: ScheduledTaskSpec, spec: ScheduledTaskSpec) -> bool
```

**Purpose**: This decides which updates an admin may make to someone else’s scheduled task. It allows admins to adjust timing-related settings, but blocks them from changing the creator’s prompt or description.

**Data flow**: It receives the old task specification and the proposed new specification. It looks at which fields the update actually set. If the update includes `prompt` or `description`, it returns false; otherwise it returns true.

**Call relations**: This method supports the object framework’s permission checks for applying changes. It expresses the file’s key safety rule: an admin can supervise cadence and expiry, but should not rewrite content that will later run as another member.


##### `ScheduledTaskObjects.member_page`  (lines 175–197)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This builds a page of scheduled tasks for a member, with special support for filtering to one conversation. It lets users browse only the tasks they are allowed to see.

**Data flow**: It receives the extension context, the requesting member, whether they are an admin, and a list query. If the query does not ask for a specific conversation, it hands the work to the base class. If it does, it parses the conversation ID, loads rows for that conversation, filters out rows the member cannot see, wraps the remaining rows for display, and returns a page.

**Call relations**: This is called by the generic object listing flow when a member asks for scheduled tasks. For conversation-specific lists, it uses `_rows` to gather task data and the inherited visibility check to remove tasks outside the requester’s reach.

*Call graph*: calls 1 internal fn (_rows); 3 external calls (__init__, object_page, UUID).


##### `ScheduledTaskObjects.member_conversation_rows`  (lines 199–219)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: This supplies the scheduled-task entries shown inside a conversation’s object area. It tells the conversation view which tasks report into that conversation and whether each task’s content should be visible to the current member.

**Data flow**: It receives a conversation ID, member information, admin status, and a limit. It asks the schedule store for tasks that report into that conversation, then turns visible tasks into `ConversationObjectGrant` records containing the task name, its generation ID, and a content-visibility flag.

**Call relations**: The conversation object surface calls this when it needs to show related objects for a conversation. It uses `_require_scheduler` to read tasks, `_owner` to apply object visibility, and `task_content_visible` to decide whether the prompt itself can be shown.

*Call graph*: calls 2 internal fn (_owner, _require_scheduler); 2 external calls (__init__, task_content_visible).


##### `ScheduledTaskObjects._member_rows`  (lines 221–224)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This provides the scheduled-task rows visible to a particular member, or all rows when no member is specified. It is the general row source used by the object framework.

**Data flow**: It receives the extension context and an optional member ID. It passes those values to `_rows` and asks for full prompt text rather than a shortened excerpt. The result is a tuple of owned task rows.

**Call relations**: The base member-readable object machinery calls this when it needs scheduled-task rows outside the special tool-context path. It delegates the real gathering and formatting work to `_rows`.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._owned_rows`  (lines 226–233)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This builds the task rows that an agent turn reads through object-listing tools. It deliberately shortens prompts in this context so a large page of tasks does not flood the model’s input.

**Data flow**: It receives the current tool context, including the extension context and acting member. It calls `_rows` with that member ID and a prompt excerpt limit. The result is a tuple of rows whose visible prompt text may be clipped for safe display in the agent context.

**Call relations**: The object-list tool calls this during an agent turn. It relies on `_rows` for the actual task lookup and visibility rules, but changes the prompt length policy to fit the model-facing listing use case.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._rows`  (lines 235–281)

```
async def _rows(self, ext: ExtensionContext | None, *, member_id: UUID | None, prompt_max: int | None, conversation_id: UUID | None=None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This is the main formatter for scheduled-task list rows. It gathers stored task records, adds owner information, status hints, email addresses, visibility-aware summaries, and fields useful for filtering or display.

**Data flow**: It receives an extension context, an optional member ID, an optional prompt length limit, and optionally a conversation ID. It reads reported tasks from the schedule store, looks up creator emails, inspects recent run information, and then builds one owned row per task. If the member may see the prompt, the row includes the real summary and prompt; otherwise it uses private placeholder text.

**Call relations**: Several listing paths depend on this: member pages, general member rows, and agent-owned rows. It calls `_require_scheduler` for storage, `_summary` for visible summaries, `_owner` for permission metadata, `owner_emails` for creator labels, and `task_content_visible` to avoid leaking private prompt content.

*Call graph*: calls 3 internal fn (_owner, _require_scheduler, _summary); called by 3 (_member_rows, _owned_rows, member_page); 3 external calls (__init__, owner_emails, task_content_visible).


##### `ScheduledTaskObjects._member_object`  (lines 283–312)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ScheduledTaskSpec] | None
```

**Purpose**: This returns the full object detail for one scheduled task when a member asks to inspect it. It includes the task specification, timestamps, a link to the conversation it reports into, and whether the full spec should be visible.

**Data flow**: It receives the extension context, task name, expected owner marker, and optional member ID. It finds the current task by name, verifies that its stored generation matches the owner marker, then builds an `ObjectDetail` with schedule, prompt, description, expiry, pause state, creation and update times, and a `reports_to` link. If the task is gone or changed, it returns nothing.

**Call relations**: The generic object `get` flow calls this for scheduled tasks. It uses `_find` to locate the task and `task_content_visible` to tell the caller whether the detailed spec may be shown.

*Call graph*: calls 1 internal fn (_find); 5 external calls (__init__, __init__, __init__, __init__, task_content_visible).


##### `ScheduledTaskObjects._status`  (lines 314–346)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This reports the live status of one scheduled task: whether it is paused, when it will run next, when it last ran, when it expires, and what happened on the latest run. It gives users a way to check whether a recurring task is healthy.

**Data flow**: It receives the tool context, task name, and expected owner marker. It finds and verifies the task, asks the schedule store to inspect its run state, and builds a status dictionary. If there was a last run, it includes the turn ID and status, and includes a shortened response only if the acting member is allowed to see the task content.

**Call relations**: The object status path calls this after a task has been identified. It uses `_find` for safe lookup, `_require_scheduler` for inspection data, and `task_content_visible` to keep private run output hidden from viewers who should not see it.

*Call graph*: calls 2 internal fn (_find, _require_scheduler); 1 external calls (task_content_visible).


##### `ScheduledTaskObjects._apply_owned`  (lines 348–400)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This creates a new scheduled task or updates an existing one. It enforces the important rules: creation needs a real member, cron text must be valid, required fields must be present on create, and only permitted people may change the task.

**Data flow**: It receives the current tool context, object name, proposed task spec, previous spec if any, and owner marker if any. It validates the cron schedule when supplied, checks the acting member, finds the current stored task, and opens the schedule store. For a new task, it requires schedule and prompt, binds the task to the current conversation and creator, calculates the next fire time, and stores it. For an update, it checks the task has not changed unexpectedly, preserves fields that were omitted, recalculates the next run, checks creator/admin permissions, and writes the update.

**Call relations**: The generic object apply/update flow calls this when an agent or member applies a scheduled-task manifest. It calls `_find` to detect the current record, `_require_scheduler` to write changes, `validate_cron` and `next_fire` for scheduling correctness, and `speaker_is_admin` when permission depends on admin status.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_scheduler); 4 external calls (__init__, now, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 402–406)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This cancels a scheduled task. It refuses to delete if the task no longer matches the expected owner generation, which prevents deleting the wrong record after a concurrent change.

**Data flow**: It receives the tool context, task name, and expected owner marker. It finds the stored task by name, confirms the stored task ID matches the owner marker, and then asks the schedule store to cancel it. If the task is missing or changed, it raises an error instead of guessing.

**Call relations**: The generic object delete flow calls this after permission checks. It uses `_find` for safe lookup and `_require_scheduler` to reach the cancellation operation in persistent storage.

*Call graph*: calls 2 internal fn (_find, _require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 408–416)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTask | None
```

**Purpose**: This looks up one scheduled task by its human-facing name. It is a small helper used whenever code needs to turn a name into the current stored task record.

**Data flow**: It receives the extension context and a task name. It asks the schedule store for all reported tasks, scans them for a matching name, and returns the first match. If none match, it returns nothing.

**Call relations**: Detail, status, apply, and delete operations all call this before acting on a named task. Those callers then compare the found task’s ID with an expected generation when they need to guard against stale edits.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


##### `pause_and_wait`  (lines 479–517)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: This is the tool handler that lets an agent pause a workflow until a person replies or a timer expires. It is useful for things like waiting for a verification email, an approval, or an external service cooldown.

**Data flow**: It receives the tool context and the pause request: the message to show now, how many minutes to wait, instructions for resuming, the reason, optional metadata, and a user-facing description. It calculates the resume time, writes a pause record with the conversation, agent, current turn position, current message-arrival watermark, resume prompt, and creator. It then returns a tool result containing a directive telling the agent to reply with the provided message and end its turn, plus a JSON payload describing the wait.

**Call relations**: The tool framework calls this when the agent invokes the `pause_and_wait` tool. It uses `_require_ext` to ensure extension services are available, writes through `PauseStore`, and returns `TextContent` inside a `ToolResult` so the agent receives clear instructions for stopping now and resuming later.

*Call graph*: calls 1 internal fn (_require_ext); 6 external calls (__init__, __init__, __init__, now, timedelta, dumps).


### Monitor wakeups
Monitor object support, probing, and setup let conversations arm watches that wake the agent when external command output changes or expires.

### `extensions/monitors/ufo_ext_monitors/monitor_kind.py`

`domain_logic` · `object listing, inspection, status checks, and deletion while monitors are armed`

A monitor is like setting a lookout: “run this command every so often, compare it with the first result, and tell the conversation if something changes.” This file is the bridge between those armed watches and UFO’s general object interface, where users and agents can list objects, get details, see status, and delete them.

The important boundary here is creation. A monitor cannot be safely created from a static manifest because it needs to run the command once inside the conversation’s sandbox to record its starting point, called the baseline. So this file explicitly refuses apply/create requests and tells callers to use the chat monitor tool instead.

For existing monitors, it reads the monitor store, turns each row into an object listing, adds owner information, and exposes useful fields such as the watched conversation, next probe time, deadline, owner email, and whether the current member created it. Getting one monitor returns its command, interval, deadline, reason, and a link back to the conversation where it will report.

Deletion is the “stop watching” action. Before deleting, the code checks that the named monitor is still the exact same generation the caller saw, so it does not accidentally stop a monitor that changed underneath them.

#### Function details

##### `_owner`  (lines 53–58)

```
def _owner(row: Monitor) -> GeneratedObjectOwner
```

**Purpose**: Builds the ownership label for a monitor. This label says who created the monitor, whether it is shared with others, and which exact monitor record it belongs to.

**Data flow**: It receives one monitor row from storage. It reads the creator member id, the monitor audience, and the monitor id, then turns them into a GeneratedObjectOwner. The result is used by the object system to decide who can see or act on that monitor.

**Call relations**: When MonitorObjects._member_rows prepares the list of visible monitors, it calls _owner for each stored monitor. _owner hands back the ownership information that is attached to each listed object.

*Call graph*: called by 1 (_member_rows); 2 external calls (__init__, subject_shared).


##### `_require_ext`  (lines 61–64)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure the monitor code has the extension context it needs to reach monitor storage and related services. If that context is missing, it fails early with a clear error instead of breaking later in a confusing way.

**Data flow**: It receives an ExtensionContext or None. If a real context is present, it returns it unchanged. If it is missing, it raises an error explaining that the monitor kind requires the scheduled-tasks extension context.

**Call relations**: Storage-facing functions call _require_ext before creating a MonitorStore. It is the small checkpoint used by listing, finding, and deleting monitors so they only continue when the needed runtime context exists.

*Call graph*: called by 3 (_delete_owned, _find, _member_rows).


##### `MonitorObjects._member_rows`  (lines 78–97)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Creates the rows shown when a member lists armed monitors. Each row is a short, readable summary with fields that can be displayed or filtered.

**Data flow**: It receives the extension context and optionally the current member id. It loads all armed monitors from MonitorStore, looks up creator email addresses, and converts each monitor into an OwnedRow with its name, short summary, owner, conversation id, next probe time, deadline, owner email, and whether it belongs to the current member. It returns the completed collection of rows.

**Call relations**: This is used by the broader object system when someone asks to list monitor objects. It relies on _require_ext to get usable runtime context, MonitorStore to read the live monitors, owner_emails to decorate the rows, and _owner to attach ownership and sharing information.

*Call graph*: calls 2 internal fn (_owner, _require_ext); 3 external calls (__init__, __init__, owner_emails).


##### `MonitorObjects._member_object`  (lines 99–125)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[MonitorSpec] | None
```

**Purpose**: Returns the full object details for one specific monitor, if the requested name and owner generation still match a real armed monitor. It is the “show me this monitor” path.

**Data flow**: It receives the extension context, a monitor name, the expected owner information, and optionally the current member id. It searches for the monitor by name, checks that its stored id matches the requested generation, then builds an ObjectDetail containing the monitor’s command, interval, deadline, reason, timestamps, and a link to the conversation it reports to. If the monitor is missing or no longer matches, it returns nothing.

**Call relations**: The object system calls this when a user or agent gets a monitor by name. It delegates the lookup to MonitorObjects._find, then packages the row as a MonitorSpec and adds an ObjectLink back to the conversation so readers can see where the monitor’s result will land.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `MonitorObjects._status`  (lines 127–142)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live running status for one monitor. This is separate from the monitor’s basic definition and answers questions like “when did it last probe?” and “how many times has it failed?”

**Data flow**: It receives the tool context, monitor name, and expected owner information. It finds the armed monitor, confirms it is the same generation, then returns a plain dictionary with timestamps, probe counters, skipped count, and a shortened excerpt of the baseline output. If the monitor is gone or no longer matches, it returns nothing.

**Call relations**: This is used when the object system or tool layer asks for a monitor’s current status. It calls MonitorObjects._find for the stored row and then formats the changing runtime facts into simple values.

*Call graph*: calls 1 internal fn (_find).


##### `MonitorObjects._apply_owned`  (lines 144–152)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: MonitorSpec, old: MonitorSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Refuses attempts to create or update a monitor through the normal object apply mechanism. This protects the monitor workflow because arming must happen in chat, where the command can be tested and the first baseline result can be recorded.

**Data flow**: It receives the requested name, desired monitor spec, any old spec, and owner information, but it does not use them to change storage. Instead, it raises VerbNotSupported with a message explaining that the monitor tool must be used.

**Call relations**: The general object system would call this for an apply operation. This monitor kind deliberately stops that flow here and points callers toward the chat monitor tool, which can do the required live probe before saving anything.

*Call graph*: 1 external calls (__init__).


##### `MonitorObjects._delete_owned`  (lines 154–159)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Stops an armed monitor. In user terms, deleting the monitor means “do not keep watching this anymore.”

**Data flow**: It receives the tool context, monitor name, and expected owner information. It finds the named monitor, checks that the stored monitor id still matches the requested generation, then asks MonitorStore to disarm it. If the monitor is missing, changed, or cannot be disarmed, it raises an error rather than pretending the stop succeeded.

**Call relations**: The object system calls this when an allowed user deletes a monitor. It uses MonitorObjects._find to locate the current row, _require_ext to get storage access, and MonitorStore.disarm to perform the actual stop.

*Call graph*: calls 2 internal fn (_find, _require_ext); 1 external calls (__init__).


##### `MonitorObjects._find`  (lines 161–165)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> Monitor | None
```

**Purpose**: Looks up one armed monitor by name. It is the shared helper used whenever the code needs to turn a user-facing monitor name into the stored monitor row.

**Data flow**: It receives an extension context and a monitor name. It loads the current armed monitors from MonitorStore and returns the first row whose name matches. If none match, it returns nothing.

**Call relations**: Detail lookup, status lookup, and deletion all call MonitorObjects._find before doing their own checks. It centralizes the simple “search the armed monitors by name” step so those higher-level actions can focus on formatting, reporting status, or disarming.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_delete_owned, _member_object, _status); 1 external calls (__init__).


### `extensions/monitors/ufo_ext_monitors/monitor_runner.py`

`domain_logic` · `recurring scheduled monitor tick`

A monitor is like a smoke alarm for a conversation: it was armed earlier with a command, an expected baseline, and instructions for what to do if the world changes. This file is the alarm checker. On each scheduled run, it claims monitors that are due so two overlapping runs do not check the same monitor twice. For each claimed monitor, it decides what should happen now.

If the monitor has passed its deadline, it fires a final message. If the person who armed it no longer has a seat in the workspace, the monitor does not run the command; it records a skipped tick instead. If it can run, it executes the stored command through the probe system using the same member authority that armed the watch. A successful command whose output matches the baseline is counted as quiet. A changed output fires the monitor. A failing command is tolerated briefly, but the third failure fires it.

When a monitor fires, this file first sends the agent a carefully formatted message and then retires the monitor. That order matters: if the process crashes after sending but before retiring, the resend uses the same idempotency key, meaning the system should not create a duplicate arrival. Large command output is written to conversation files and linked from the fire message.

#### Function details

##### `MonitorRunner.run`  (lines 55–65)

```
async def run(self) -> None
```

**Purpose**: This is the top-level pass over all monitors that are due right now. It claims due monitors, runs one tick for each, and reports at the end if any monitor tick failed.

**Data flow**: It starts with the extension context and the current time. It builds a monitor store, asks that store for due monitors under a temporary lease, then feeds each claimed monitor into the per-monitor tick routine. If any tick raises an error, it remembers the monitor name and error type; after trying all claimed monitors, it raises one combined error if there were failures.

**Call relations**: This is the recurring job's entry into the file. It creates the store and calls MonitorRunner._tick for each claimed monitor, so one bad monitor does not stop the whole batch immediately. The store decides which rows are due and safely claimed; this function coordinates the sweep.

*Call graph*: calls 1 internal fn (_tick); 2 external calls (__init__, now).


##### `MonitorRunner._tick`  (lines 67–112)

```
async def _tick(self, store: MonitorStore, row: Monitor) -> None
```

**Purpose**: This decides the next step for one claimed monitor: fire it, skip it, record a quiet check, record a failure, or run the probe command. It is the main decision point for the monitor's life cycle.

**Data flow**: It receives the stored monitor row and the monitor store. It reads the monitor's deadline, interval, creator, command, baseline output, and failure counters. If the deadline has passed, it fires. If the monitor cannot currently act as its creator, it records a skip and schedules the next probe. Otherwise it runs the command through the probe system, compares the result with the baseline, updates counters for quiet or failed runs, or fires when output changed or failures crossed the limit.

**Call relations**: MonitorRunner.run calls this once for each claimed due monitor. This function calls MonitorRunner._acts_for_a_seated_member before spending authority on a probe, calls store methods to record quiet, failed, or skipped ticks, and calls MonitorRunner._fire when the monitor has reached a final condition.

*Call graph*: calls 5 internal fn (_acts_for_a_seated_member, _fire, failed_tick, quiet_tick, skipped_tick); called by 1 (run); 4 external calls (now, timedelta, capped, stderr_tail).


##### `MonitorRunner._acts_for_a_seated_member`  (lines 114–126)

```
async def _acts_for_a_seated_member(self, row: Monitor) -> bool
```

**Purpose**: This checks whether the monitor is still allowed to act as the member who created it. If a watch was armed without a specific member, it is allowed to run using only workspace-shared access.

**Data flow**: It receives a monitor row and looks at the creator member id. If there is no creator member id, it returns true. If there is one, it opens a database transaction and asks the seat system whether that member still belongs to the workspace. The result is a yes-or-no answer used to decide whether member authority can be used.

**Call relations**: MonitorRunner._tick uses this before running a probe, so a removed member's connections are not used off-turn. MonitorRunner._fire uses it again just before sending the final message, so a deadline notification can still arrive but will not falsely carry authority from someone who no longer has a seat.

*Call graph*: called by 2 (_fire, _tick); 1 external calls (__init__).


##### `MonitorRunner._fire`  (lines 128–148)

```
async def _fire(self, store: MonitorStore, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> None
```

**Purpose**: This sends the final monitor notification to the agent and then retires the monitor so it will not keep running. It is used when the deadline arrives, output changes, or failures reach the threshold.

**Data flow**: It receives the store, monitor row, reason for firing, message payload, optional full output, and updated probe count. First it checks that this runner still holds the claim for the monitor. Then it decides whether the fire may act on behalf of the creator, builds the message body, invokes the agent with a stable idempotency key, and finally marks the monitor retired in storage.

**Call relations**: MonitorRunner._tick calls this whenever a monitor reaches a final condition. This function asks MonitorRunner._acts_for_a_seated_member about authority, asks MonitorRunner._body to create the text the agent will read, uses the context to invoke the agent, and then tells the store to retire the row.

*Call graph*: calls 4 internal fn (_acts_for_a_seated_member, _body, claim_holds, retire); called by 1 (_tick).


##### `MonitorRunner._body`  (lines 150–169)

```
async def _body(self, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> str
```

**Purpose**: This builds the text of the final monitor message that the agent receives. It includes the reason, next steps, metadata, counters, and any probe output in a form that is safe to place into the conversation.

**Data flow**: It receives the monitor row, firing cause, short payload, optional full output, and probe count. It turns the monitor's saved details into a structured text block. If there is large spilled output, it asks MonitorRunner._spilled to write that full output to a file and includes the path. It escapes the closing monitor tag inside the body and wraps command output with a safety boundary so the output is treated as data, not instructions.

**Call relations**: MonitorRunner._fire calls this just before invoking the agent. This function may call MonitorRunner._spilled when the output was too large to fit directly, and it uses the shared wall helper to safely include untrusted command output.

*Call graph*: calls 1 internal fn (_spilled); called by 1 (_fire); 2 external calls (dumps, wall).


##### `MonitorRunner._spilled`  (lines 171–179)

```
async def _spilled(self, row: Monitor, output: str) -> str
```

**Purpose**: This saves full probe output to a conversation file when it is too large to include directly in the fire message. The fire message can then point the agent to the saved file.

**Data flow**: It receives the monitor row and the full output text. It checks that file storage is available, creates a timestamped path under the monitor spill directory, writes the output bytes into the conversation's files, and returns the written path.

**Call relations**: MonitorRunner._body calls this only when the short fire payload was capped and the complete output still needs to be preserved. It relies on the extension context's file service and hands the resulting file path back to the message builder.

*Call graph*: called by 1 (_body); 1 external calls (now).


### `extensions/monitors/ufo_ext_monitors/monitor_tool.py`

`domain_logic` · `request handling`

This file is the front door for creating a durable monitor. A monitor is like asking someone to check the mailbox every few minutes and call you only when something appears, when checking keeps failing, or when a fixed time limit is reached. Without this tool, an agent would have to keep polling manually or risk forgetting what it was waiting for.

The file defines the shape of the tool input with `MonitorInput`: the monitor name, shell command to run, checking interval, deadline, explanation, and instructions for the future turn. The command is important: its standard output is compared byte-for-byte with the first run, so the output must be stable and not include clocks or changing counters unless those changes are what the agent wants to detect.

When `monitor` is called, it first checks that the scheduled-task extension is available. It then looks up existing monitors for the conversation, refuses to create more than the allowed cap, and refuses duplicate names. Next it runs the probe command right away in the conversation sandbox. If the command fails now, no monitor is saved; this prevents a broken watch from being armed and silently failing later. If it succeeds, the output is saved as the baseline, the deadline and next probe time are calculated, and a row is stored through `MonitorStore`. The result tells the agent to reply with the provided message and end its turn, because the monitor will take over from there.

#### Function details

##### `_require_ext`  (lines 76–79)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure the monitor tool has the extension context it needs to store scheduled work. If that context is missing, it stops immediately with a clear error instead of failing later in a confusing way.

**Data flow**: It receives an optional extension context. If the context is present, it returns it unchanged. If it is missing, it raises an error saying the monitor tool requires the scheduled-tasks extension context.

**Call relations**: The main `monitor` function calls this before creating a `MonitorStore`. It acts as the checkpoint that confirms the rest of the monitor-arming flow has the storage and scheduling support it needs.

*Call graph*: called by 1 (monitor).


##### `_refusal`  (lines 82–83)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: This helper builds a standard error result for cases where the monitor should not be armed. It is used when the request is validly understood but must be rejected, such as too many monitors, a duplicate name, or a failing probe command.

**Data flow**: It receives a plain text explanation. It wraps that text in a `TextContent` message and returns a `ToolResult` marked as an error, so the caller sees a clear refusal instead of a successful monitor setup.

**Call relations**: The `monitor` function calls this whenever it decides not to save a monitor. This keeps all refusal responses shaped the same way, while letting `monitor` focus on the actual checks.

*Call graph*: called by 1 (monitor); 2 external calls (__init__, __init__).


##### `monitor`  (lines 86–134)

```
async def monitor(ctx: ToolContext, args: MonitorInput) -> ToolResult
```

**Purpose**: This is the tool handler that arms a new monitor for a conversation. It checks whether the monitor is allowed, runs the first probe command immediately, saves the baseline output, and records when future checks should happen.

**Data flow**: It receives the current tool context and the requested monitor settings. It reads the conversation and agent information from the context, checks existing stored monitors, runs the requested shell command in the sandbox, and refuses the request if the cap is reached, the name is already used, or the command fails. If everything succeeds, it stores a new monitor row with the command, baseline output, interval, deadline, reason, next steps, metadata, and timeline description. It returns a tool result containing instructions to end the turn plus a JSON summary of the armed monitor.

**Call relations**: This is called as the handler for the `MONITOR_TOOL` definition when an agent invokes the `monitor` tool. Inside its flow it calls `_require_ext` to get the extension context, uses `MonitorStore` to read and write monitor records, calls `_refusal` for rejected requests, runs the sandbox command to seed the baseline, and finally builds a `ToolResult` for the agent.

*Call graph*: calls 2 internal fn (_refusal, _require_ext); 9 external calls (__init__, __init__, __init__, now, timedelta, dumps, capped, qualified_name, stderr_tail).


### Automation display
Conversation-level presentation code packages scheduled tasks into a safe Automations panel for users.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/conversation_slot.py`

`domain_logic` · `conversation display`

This file is the bridge between the scheduled-tasks extension and the conversation user interface. A conversation may have background automations attached to it, such as tasks that run later or repeat on a schedule. The UI needs a compact, safe summary of those automations: what they are called, when they run, whether they are paused, and what happened most recently.

The important safety idea here is authorization. The file does not simply show every scheduled task it finds. It compares stored tasks against the conversation’s visible items, and only includes a task when its name and generation match what the conversation says is visible. You can think of this like checking both a ticket name and a ticket number before letting someone into a room.

It also protects the display from becoming too large or revealing hidden content. Descriptions, schedules, status text, and latest responses are shortened to fixed maximum lengths. If the conversation says the task content is not visible, the description and latest response are hidden. When anything is omitted, too long, missing, or over the maximum count, the returned payload marks itself as truncated so the UI knows it is not seeing the full story.

At the bottom, the file registers a ConversationSlotProvider named “automations”. That provider tells the host app how to count and read this slot.

#### Function details

##### `_scheduler`  (lines 16–19)

```
def _scheduler(ctx: ConversationSlotContext) -> ScheduleStore
```

**Purpose**: This helper creates access to the scheduled-task storage for the current conversation-slot request. It also makes sure the request includes the extension context needed to read scheduled-task data.

**Data flow**: It receives a ConversationSlotContext. If the context has no extension data, it stops with an error because there is nowhere safe to read schedules from. Otherwise, it passes that extension context into ScheduleStore and returns the resulting store object.

**Call relations**: _conversation and _read call this when they need to talk to the scheduled-task store. It is the small doorway between the conversation-slot code and the underlying schedule storage.

*Call graph*: called by 2 (_conversation, _read); 1 external calls (__init__).


##### `_conversation`  (lines 22–28)

```
async def _conversation(ctx: ConversationSlotContext) -> tuple[ScheduledTask, ...]
```

**Purpose**: This function asks the schedule store for tasks that belong to the current conversation and match the names of items the conversation says are visible. It fetches one more than the display limit so the caller can tell whether the list was cut short.

**Data flow**: It reads the visible item names and the conversation ID from the context. It creates a ScheduleStore through _scheduler, then asks that store for matching ScheduledTask rows with a limit of CONVERSATION_AUTOMATIONS_MAX plus one. It returns those scheduled tasks as a tuple.

**Call relations**: _read calls this near the start of building the automations payload. _conversation does the first broad lookup, then _read performs the stricter authorization check and formatting.

*Call graph*: calls 1 internal fn (_scheduler); called by 1 (_read).


##### `_read`  (lines 31–90)

```
async def _read(ctx: ConversationSlotContext) -> AutomationsSlotPayload
```

**Purpose**: This is the main reader for the Automations slot. It builds the complete payload that the conversation UI can show, including each visible automation’s schedule, pause state, recent run information, and whether the data had to be shortened.

**Data flow**: It receives a ConversationSlotContext. It opens the schedule store, fetches candidate tasks for the conversation, and compares each one with the context’s visible items. Only tasks whose name and authorization generation match are allowed through. It inspects those allowed tasks to get runtime details such as next run time, last run time, last status, and last response. Then it shortens long text fields, hides sensitive content when content_visible is false, records whether anything was truncated or missing, creates ConversationAutomation objects, and wraps them in an AutomationsSlotPayload.

**Call relations**: The ConversationSlotProvider uses this function when the host app wants to read the contents of the “automations” slot. It calls _scheduler for storage access and _conversation for the initial task list, then creates the payload objects that are handed back to the conversation system.

*Call graph*: calls 2 internal fn (_conversation, _scheduler); 2 external calls (__init__, __init__).


##### `_summarize`  (lines 93–95)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives the conversation UI a simple count for the Automations slot. It returns how many visible automations can be summarized, capped at the maximum number the slot is allowed to show.

**Data flow**: It reads the number of visible items from the context. It limits that number to CONVERSATION_AUTOMATIONS_MAX. If the result is zero, it returns None instead of 0, which likely means “do not show a count”; otherwise it returns the count.

**Call relations**: The ConversationSlotProvider uses this when the host app only needs a lightweight summary, such as a badge count, rather than the full automation details produced by _read.


### Background firing and recovery
Clock runners and core helpers admit due background work, identify scheduled firings, and retry missed child-result deliveries.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pause_runner.py`

`orchestration` · `recurring scheduled job`

A “pause” here is a stored reminder to resume a conversation later. This file is the worker that checks for pauses whose time has arrived, claims them so another worker does not do the same job, and asks the main system to continue the conversation using the saved prompt.

The important idea is that a pause can end in two ways: the timer expires, or a member speaks first. Those two paths meet here. Before firing, the runner uses stored “watermarks” — remembered conversation positions from when the pause began — to ask: “Has a member spoken since then?” If no one has, the scheduled turn is allowed. If someone has, there is nothing to resume, because the human message already ended the wait.

The runner is careful about crashes and repeated ticks. It claims due pauses under a lease, which is like putting a temporary “I’m working on this” note on each row. It also uses a stable idempotency key, meaning a retry can be recognized as the same scheduled action rather than a new duplicate. It invokes first and retires the pause second, so if the process crashes in between, a later retry can safely re-check and settle the same pause.

#### Function details

##### `PauseRunner.run`  (lines 32–41)

```
async def run(self) -> None
```

**Purpose**: This is the main tick of the pause runner. It finds pauses that are due now, tries to fire each one, and reports any failures together at the end.

**Data flow**: It starts with the runner’s extension context and lease length. It creates a PauseStore, asks the current UTC time, and claims all pause rows that are due under a temporary lease. For each claimed row, it passes the row to _fire. If a row fails, it records the conversation id and error type instead of stopping immediately. At the end, it returns normally if all rows succeeded, or raises one RuntimeError naming the failed pauses.

**Call relations**: This function is the outer loop for the file. On each scheduled tick, it builds the store, asks time for “what is due now,” and hands each claimed pause to PauseRunner._fire. It does not do the detailed firing itself; it coordinates the batch and turns individual failures into one clear job-level error.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `PauseRunner._fire`  (lines 43–56)

```
async def _fire(self, store: PauseStore, row: Pause) -> None
```

**Purpose**: This fires one claimed pause if it is still safe to do so, then retires it so the same wait is not processed again. It also quietly stops if the claim no longer holds.

**Data flow**: It receives a PauseStore and one stored Pause row. First it asks the store whether this runner still holds the claim for that row. If not, nothing changes and it returns. If the claim is valid, it invokes the conversation using the stored conversation id, agent id, prompt, creator member id, and a stable firing key. The invocation includes the saved watermarks that prevent the scheduled turn if a member has spoken since the pause began. After the invocation finishes, it tells the store to retire the pause row.

**Call relations**: PauseRunner.run calls this once for each due pause it claimed. Inside, _fire relies on PauseStore.claim_holds to avoid acting on a stale claim, then uses the extension context to schedule the conversation turn, and finally calls PauseStore.retire to mark the wait as ended. This is the point where the timer path and the human-message path converge.

*Call graph*: calls 2 internal fn (claim_holds, retire); called by 1 (run).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `recurring scheduled-task tick`

This file is the clock-driven worker for scheduled tasks. Think of it like a careful mail carrier: on each round, it picks up only the letters that are due, puts a temporary hold on them so another carrier does not deliver the same one, checks whether any are too old to send, and then delivers each one to the correct conversation.

The runner uses a schedule store to find tasks whose next run time has arrived. It gives each claimed task a short lease, which is a temporary ownership marker. That matters because two runner ticks could overlap; the lease helps prevent the same scheduled task from firing twice.

Before firing, it checks whether the task has expired. If this is the last allowed run before expiry, it adds special instructions telling the agent to finish the task and ask the user whether to continue, change, or stop. Otherwise, it adds ordinary reporting instructions.

The actual message sent to the conversation is built by `fire_body`. It includes the exact scheduled time and an idempotency key, which is a repeat-safe label. If the same fire is retried after a restart or deploy, the system can recognize it as the same occurrence instead of creating a duplicate. After a fire is accepted, the runner advances the stored schedule to the next cron time. If invocation fails, it leaves the occurrence available for retry and raises a summary of failed task names.

#### Function details

##### `fire_body`  (lines 41–59)

```
def fire_body(task: ScheduledTask, runtime_instruction: str | None) -> tuple[str, str]
```

**Purpose**: Builds the message that will be delivered for one scheduled task fire, plus the repeat-safe key used to admit it only once. Someone uses this when a task has been claimed and is ready to be sent into its conversation.

**Data flow**: It takes a `ScheduledTask` and an optional runtime instruction. It reads the task's next run time and prompt, formats them into a small tagged message, optionally appends extra instructions, and creates a scheduled-fire key from the task id and exact fire time. It returns two strings: the inbound message and the idempotency key that identifies this exact scheduled occurrence.

**Call relations**: `ScheduledTaskRunner._fire` calls this after it has confirmed the task is still claimed and not expired. `fire_body` hands back the prepared message and key, using `scheduled_fire_key` so the later conversation invocation can be safely retried without becoming a second fire.

*Call graph*: called by 1 (_fire); 1 external calls (scheduled_fire_key).


##### `ScheduledTaskRunner.run`  (lines 67–76)

```
async def run(self) -> None
```

**Purpose**: Performs one full scheduled-task polling pass. It finds tasks due right now, tries to fire each one, and raises an error at the end if any fires failed.

**Data flow**: It starts with the runner's extension context and current time. It creates a `ScheduleStore`, asks it for due tasks to claim under a temporary lease, and then sends each claimed task through `_fire`. It collects the names of any failed fires. If none fail, it finishes quietly; if some fail, it raises a `RuntimeError` naming them.

**Call relations**: This is the top-level method for a runner tick. It sets up the store and timing, then delegates the per-task work to `ScheduledTaskRunner._fire`. `_fire` returns either no failure or a failure label, and `run` turns those labels into one final error report for the scheduler that called it.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `ScheduledTaskRunner._fire`  (lines 78–113)

```
async def _fire(self, store: ScheduleStore, task: ScheduledTask, tick_at: datetime, expiry_checked_at: datetime) -> str | None
```

**Purpose**: Carries out one claimed scheduled task occurrence. It checks whether the task should still run, prepares the right instructions, invokes the conversation, and advances the schedule if the run is accepted.

**Data flow**: It receives the schedule store, the claimed task, the tick time, and the time used for expiry checking. First it asks the store to retire the task if it has expired. If not expired, it calculates the following cron occurrence. Based on whether that following time would pass the task's expiry, it chooses either normal reporting instructions or final-run instructions. It then checks that the lease still belongs to this runner. If the claim is still valid, it builds the inbound message and key with `fire_body`, invokes the task's conversation as a scheduled turn, and either records a failure, does nothing if the turn was already admitted, or reschedules the task to its next fire time.

**Call relations**: `ScheduledTaskRunner.run` calls this once for each task it claimed. Inside, `_fire` coordinates the schedule store checks (`retire_if_expired`, `claim_holds`, and `reschedule`), asks `next_fire` for the next cron time, and calls `fire_body` to prepare the exact message. Its result flows back to `run`, which uses it to decide whether the whole tick should report failures.

*Call graph*: calls 4 internal fn (fire_body, claim_holds, reschedule, retire_if_expired); called by 1 (run); 1 external calls (next_fire).


### `core/src/ufo/candidates.py`

`domain_logic` · `scheduler tick before job dispatch`

In this system, jobs must not run “in the open” across all customer data. They need to be tied to a specific workspace before they touch real rows. This file provides the narrow doorway for deciding which workspaces need attention.

Normally, database access is protected by row-level security, or RLS, which means the database only shows rows belonging to the current workspace. But to schedule work, the system first needs to ask a cross-workspace question: “Which workspaces have something due?” This file allows that one special read, using `owner_tx`, a database transaction that can bypass workspace filtering. Importantly, it only reads workspace IDs, not the actual tenant data.

Extensions do not get direct access to this powerful cross-workspace read. Instead, they provide a small query builder that says how to select distinct workspace IDs from their own tables. `owner_candidates` wraps that builder in a safe callable. Each time the scheduler checks for work, the query is built fresh, so time-based checks like “due before now” use the current time rather than a stale time from startup.

The result is like a receptionist reading only room numbers from a clipboard: the receptionist may see which rooms need service, but the actual service happens only after entering one room with the proper key.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: This function turns a query-building function into a safe workspace-candidate function. It gives extensions a way to say “these workspaces have pending work” without giving them direct access to the cross-workspace database connection.

**Data flow**: It receives `due`, a no-argument function that builds a database `SELECT` query returning workspace IDs. It wraps that builder inside an async `candidates` function. The output is that async function, which can later be called by the dispatcher to get a tuple of workspace IDs.

**Call relations**: This is the public seam for declaring job candidates. An extension supplies the query builder here; later, the dispatcher or scheduling code calls the returned `owner_candidates.candidates` function when it needs to know which workspaces should run the job.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner async function actually performs the special cross-workspace read and returns only workspace IDs. It is the controlled point where the system briefly bypasses workspace filtering to decide where work exists.

**Data flow**: It starts with no direct inputs, but it closes over the `due` query builder provided to `owner_candidates`. When called, it opens an `owner_tx` database transaction, builds and runs the current query, collects the first column from each returned row, and returns those values as a tuple of workspace UUIDs. It does not return the underlying rows or tenant data.

**Call relations**: This function is the callable produced by `owner_candidates`. When scheduling code asks for candidates, it runs this function; the function calls `ufo.db.owner_tx` to perform the one allowed RLS-bypassing read, then hands back workspace IDs so later job execution can re-enter each workspace safely before doing real work.

*Call graph*: 1 external calls (owner_tx).


### `core/src/ufo/ext/scheduled_fire.py`

`domain_logic` · `scheduled task admission and run lookup`

Scheduled tasks need a durable “admission key”: a small piece of text that says, “this run was allowed because this task fired at this time.” This matters because the system may retry work, restart, or be deployed again, and it must still recognize the same scheduled occurrence instead of treating it as new. The key is built by joining the task’s unique ID and the scheduled time with a colon, like putting a name and appointment time on the same ticket.

The file keeps the two sides of that ticket together. `scheduled_fire_key` creates the key in one fixed format. `scheduled_fire_task_id` reads a key back and extracts the task ID when the key looks like a scheduled fire key. If the key came from some other source, such as a pause timer resuming work, the parser returns `None` instead of pretending it is scheduled-task data.

One subtle but important detail is that the timestamp is kept exactly as Python’s `isoformat()` writes it, including timezone text like `+00:00`. That exact spelling is part of the system’s long-term promise. If older runs were recorded with this format, changing it would make the system fail to recognize them as the same occurrence.

#### Function details

##### `scheduled_fire_key`  (lines 14–16)

```
def scheduled_fire_key(task_id: UUID, fire_at: datetime) -> str
```

**Purpose**: Creates the stable text key for one scheduled occurrence of one task. Code uses this when admitting a scheduled run, so retries and later lookups can recognize the same scheduled fire.

**Data flow**: It receives a task ID and the time that task is supposed to fire. It turns the time into its standard ISO text form, joins the task ID and time with a colon, and returns that combined string as the key. It does not change any outside state.

**Call relations**: When the scheduled-task runner needs to record or deduplicate a fire, this function is the builder it should use. Inside, it relies on `datetime.datetime.isoformat` to spell the time consistently, because that spelling becomes part of the durable key other parts of the system will later read.

*Call graph*: 1 external calls (isoformat).


##### `scheduled_fire_task_id`  (lines 19–25)

```
def scheduled_fire_task_id(key: str) -> UUID | None
```

**Purpose**: Tries to read a scheduled-task ID out of an admission key. It is used when the system sees a run key and needs to know whether it belongs to a scheduled task.

**Data flow**: It receives a key string. It takes the text before the first colon and tries to treat that text as a UUID, which is a standard unique identifier. If that succeeds, it returns the UUID as the task ID; if it fails, it returns `None`, meaning this key was not a scheduled fire key.

**Call relations**: When a runs feed or similar lookup needs to connect a run back to a scheduled task, this function is the reader paired with `scheduled_fire_key`. It hands the leading part of the key to `uuid.UUID` to validate and convert it, and it safely rejects keys from other mechanisms by returning `None`.

*Call graph*: 1 external calls (UUID).


### `core/src/ufo/loop/delivery.py`

`orchestration` · `background scheduled sweep`

A parent conversation can delegate work to a child turn. Normally, when the child finishes, its result is delivered back to the parent right away. But some endings happen outside the child’s normal flow, such as cancellation from another process or a crash at an unlucky moment. In those cases, the database may show that the child is finished, but the parent was never woken up.

This file defines DeliverySweep, a background sweep that fixes that gap. Think of it like a postal worker checking the depot for packages marked “ready” but not yet handed to the recipient. It searches for child turns that are terminal, meaning finished, and still marked as needing result delivery. It groups them by the parent conversation, so if several children finished for the same parent, they can be delivered together instead of waking the parent over and over.

It also uses a short cooldown. If a conversation was already woken recently by another result delivery path, this sweep skips it for now and leaves the remaining children for the next tick. That prevents a busy parent from being repeatedly reawakened in a tight loop. The actual handoff goes through SubagentResult, the same result-delivery mechanism used by the normal event path, so duplicate attempts are safe: both paths may try, but only one delivery should land.

#### Function details

##### `DeliverySweep.run`  (lines 50–65)

```
async def run(self) -> None
```

**Purpose**: Runs one pass of the delivery safety sweep. It finds finished child turns whose results are still waiting, skips parent conversations that were just woken recently, and sends the remaining child results through the normal subagent-result delivery path.

**Data flow**: It starts with no direct input beyond the DeliverySweep object’s stored invoker factory and registry. It asks _outstanding for pending finished children, calculates a recent-time cutoff using the current time, asks _woken_since which parent conversations were already woken after that cutoff, builds a SubagentResult for the current workspace, and then delivers each eligible child. The result is no returned value; the important change is that child results may be posted back to their parent conversations.

**Call relations**: This is the main body of the sweep. It calls _outstanding first to learn what work exists, then _woken_since to avoid waking recently touched conversations again. It uses ws_current to build the right TurnInvoker through invoker_for, and then hands each child to SubagentResult so the same delivery machinery is used as in the normal event-driven path.

*Call graph*: calls 2 internal fn (_outstanding, _woken_since); 4 external calls (__init__, now, timedelta, ws_current).


##### `DeliverySweep.candidate_workspaces`  (lines 67–79)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces have at least one finished child turn still waiting for result delivery. A scheduler can use this to decide where the sweep needs to run, instead of scanning every workspace blindly.

**Data flow**: It reads from the owner-level database connection, looking for distinct workspace IDs on turns whose result delivery is still pending and whose terminal field says they are finished. It turns those database rows into a tuple of workspace UUIDs. It does not change any data.

**Call relations**: This supports the wider job system that decides where the result-delivery sweep should be scheduled. It uses owner_tx to read across workspace ownership data and SQLAlchemy’s select builder to form the query.

*Call graph*: 2 external calls (select, owner_tx).


##### `DeliverySweep._outstanding`  (lines 81–114)

```
async def _outstanding(self) -> dict[UUID, list[Turn]]
```

**Purpose**: Collects the actual child turns in the current workspace that are finished but whose results have not yet been delivered. It groups them under the parent conversation that should receive the results.

**Data flow**: It opens a workspace database transaction, joins each child turn to its parent turn, and selects children marked as pending delivery and terminal. It orders them by parent conversation and by when the child was updated, then caps the batch size. After reading the rows, it converts each database row into a Turn record and returns a dictionary: parent conversation ID to a list of child turns waiting to be delivered.

**Call relations**: DeliverySweep.run calls this at the start of a sweep pass. Its output decides whether there is anything to do at all, and later gives run the child turns it will pass into the SubagentResult delivery path.

*Call graph*: called by 1 (run); 3 external calls (model_validate, select, workspace_tx).


##### `DeliverySweep._woken_since`  (lines 116–143)

```
async def _woken_since(self, cutoff: datetime, conversations: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: Checks which of the candidate parent conversations were already woken by a delivered child after a given time. This is the cooldown check that prevents one conversation from being woken again too soon.

**Data flow**: It receives a cutoff time and a tuple of conversation IDs. It reads the workspace database, joining parent turns to delivered child turns, and looks for children marked delivered whose update time is newer than the cutoff. It returns a frozen set of conversation IDs that should be skipped for this sweep pass.

**Call relations**: DeliverySweep.run calls this after _outstanding has found candidate conversations. run then uses the returned set as a do-not-disturb list: conversations in the set are left alone until a later sweep tick.

*Call graph*: called by 1 (run); 2 external calls (select, workspace_tx).


### Durable wakeup stores
Private storage and timing helpers persist armed monitors, paused workflows, cron schedules, and safely claim due work.

### `extensions/monitors/ufo_ext_monitors/monitors.py`

`domain_logic` · `background monitor polling and monitor arm/disarm requests`

A monitor is like an alarm clock attached to a shell command. It remembers what command to run, how often to run it, what output counts as “normal,” when it must stop waiting, and which conversation and agent should be re-entered if the watch fires. This file defines the database table for those armed watches and gives the rest of the extension one safe place to read and update it.

The important safety rule is that every database query filters by workspace. The transaction object used here is not automatically scoped, so this file must make sure one workspace never sees or changes another workspace’s monitors.

The file also uses short-lived leases, called claims. A background runner claims due monitors before probing them, much like putting a “currently being checked” sticky note on a task. That prevents two runners from probing and firing the same monitor at the same time. After a probe, the store records whether the output stayed quiet, failed, or could not run, then releases the claim and schedules the next probe. If the monitor fires, it is retired only by the runner that still owns the claim. If a user stops watching, the row is deleted immediately.

#### Function details

##### `qualified_name`  (lines 73–84)

```
def qualified_name(conversation_id: UUID, slug: str) -> str
```

**Purpose**: Builds the stored monitor name by combining a short prefix from the conversation ID with the human-chosen slug. This keeps names unique across a workspace while still letting different conversations reuse the same friendly slug.

**Data flow**: It receives a conversation ID and a slug such as “ci-run.” It takes the first few hexadecimal characters from the conversation ID, joins them with the slug, and returns a single name like “abcd1234-ci-run.” It does not read or change the database.

**Call relations**: This helper is used when a monitor needs a workspace-wide object name. It supports the table’s uniqueness rule so later operations, such as finding or deleting a monitor by name, do not accidentally hit another conversation’s monitor.


##### `capped`  (lines 87–97)

```
def capped(output: str) -> str
```

**Purpose**: Shrinks probe output to a safe maximum size before it is stored or compared. This prevents a command that prints a huge amount of text from making fires too large or from constantly changing only because the middle of the output grew.

**Data flow**: It receives a text output string. If the encoded text is small enough, it returns it unchanged. If it is too large, it keeps the beginning and end, inserts a message saying how many bytes were omitted, and returns that shortened text.

**Call relations**: Monitor logic can use this before saving a baseline or comparing probe output. It acts like trimming a long receipt by keeping the top and bottom, where the most useful clues often are.


##### `stderr_tail`  (lines 100–105)

```
def stderr_tail(stderr: str) -> str
```

**Purpose**: Keeps only the final part of a failed command’s error output. Shell commands usually put the most useful error message near the end, so this preserves the likely explanation without carrying unlimited text.

**Data flow**: It receives standard error text from a command. If it is under the limit, it returns the whole text. If it is too long, it returns only the last allowed bytes, decoded back into text.

**Call relations**: This is useful when reporting failed probes. It gives fire messages enough error detail to explain what happened without flooding storage or the conversation.


##### `_aware`  (lines 138–139)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Ensures a time value has timezone information. It treats timezone-less database times as UTC, so later comparisons are consistent.

**Data flow**: It receives a datetime. If the datetime already says which timezone it belongs to, it is returned as-is. If it has no timezone, the function returns a copy marked as UTC.

**Call relations**: _row calls this while turning database rows into Monitor objects. That means the rest of the monitor code can work with clear, timezone-aware times instead of each caller doing its own cleanup.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 142–170)

```
def _row(row: sa.RowMapping) -> Monitor
```

**Purpose**: Turns one raw database row into a Monitor object that the rest of the extension can use. It also normalizes all stored times so consumers do not have to worry about database-specific datetime quirks.

**Data flow**: It receives a database row mapping with monitor columns. It reads each field, converts stored datetime values through _aware, handles a missing last-probe time, and returns a filled Monitor value object.

**Call relations**: All read paths in this file pass through _row: listing armed monitors, creating a new monitor, and claiming due monitors. It is the doorway between SQL rows and the in-memory monitor shape used by the runner and handlers.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 173–174)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this monitor is not currently leased by another runner.” A monitor is available if nobody claimed it, or if the old claim has expired.

**Data flow**: It receives the current time. It produces a SQL condition that checks whether claimed_by is empty or claim_expires_at is earlier than that time. The result is not a boolean yet; it is a database expression used inside a query.

**Call relations**: The workspace candidate finder and claim_due both use this same condition. Sharing it keeps the definition of “available to claim” consistent between deciding which workspaces need attention and actually leasing monitors.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_due`  (lines 177–178)

```
def _due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this monitor needs attention now.” A monitor is due if its next probe time has arrived or its deadline has arrived.

**Data flow**: It receives the current time. It produces a SQL condition that checks next_probe_at and deadline_at against that time. The database later evaluates that condition when selecting rows.

**Call relations**: Both due_monitor_workspaces.due and MonitorStore.claim_due rely on this helper. That keeps the broad workspace scan and the per-workspace claim step using the same meaning of “due.”

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_monitor_workspaces`  (lines 181–190)

```
def due_monitor_workspaces() -> WorkspaceCandidates
```

**Purpose**: Tells the job system which workspaces might have monitor work ready. It does not claim monitors itself; it supplies a query the scheduler can use to find candidate workspaces.

**Data flow**: It defines an inner query factory that looks for distinct workspace IDs with due, claimable monitors. It passes that query factory to the job helper and returns a WorkspaceCandidates object for the runner infrastructure.

**Call relations**: This is the bridge from the monitor table to the background job system. The job system calls the inner due query when deciding which workspace owners should be woken up for monitor polling.

*Call graph*: 1 external calls (owner_candidates).


##### `due_monitor_workspaces.due`  (lines 186–188)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual SQL query that finds workspaces with monitor rows ready to be processed. It checks both timing and lease availability.

**Data flow**: It gets the current UTC time, builds conditions for claim availability and due-ness, and returns a SELECT statement for distinct workspace IDs matching those conditions.

**Call relations**: This inner function is handed to owner_candidates by due_monitor_workspaces. When the scheduler wants candidates, this query is what points it toward workspaces that have due monitors not already held by live claims.

*Call graph*: calls 2 internal fn (_claim_available, _due); 2 external calls (now, select).


##### `MonitorStore.armed`  (lines 199–205)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Monitor, ...]
```

**Purpose**: Lists the armed monitors in this store’s workspace, optionally limited to one conversation. It is used when code needs to show or inspect the watches that are currently active.

**Data flow**: It starts with the workspace ID from the ExtensionContext. If a conversation ID is provided, it adds that filter. It runs a SELECT ordered by monitor name, converts each row through _row, and returns a tuple of Monitor objects.

**Call relations**: This is a read path for callers that need current monitor state. It uses _row so callers receive clean Monitor values rather than raw database rows.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `MonitorStore.arm`  (lines 207–263)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, name: str, audience: str, command: str, interval_minutes: int, deadline_at: datetime, reason: str, next_steps: str, metadata: dict[str, Js
```

**Purpose**: Creates a new armed monitor in the workspace. This is the write path used when an agent or member says “start watching this command.”

**Data flow**: It receives all monitor details: conversation, agent, name, command, interval, deadline, explanation text, metadata, baseline output, and first probe time. It inserts a new row with fresh counters, no claim, and timestamps, then converts the inserted row into a Monitor object and returns it.

**Call relations**: This function starts a monitor’s life in the table. It hands the new row through _row so the caller immediately gets the same Monitor shape used by later polling and firing code.

*Call graph*: calls 1 internal fn (_row); 2 external calls (insert, uuid4).


##### `MonitorStore.claim_due`  (lines 265–303)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_MONITORS) -> tuple[Monitor, ...]
```

**Purpose**: Leases a batch of due monitors so one runner can safely probe them. The lease prevents overlapping runners from doing the same work at the same time.

**Data flow**: It receives the current time, a lease length, and a maximum batch size. It finds due monitors in this workspace whose claims are free or expired, picks the oldest ones up to the limit, stamps them with a new claim ID and expiration time, and returns the claimed rows as Monitor objects.

**Call relations**: The monitor runner uses this before probing. It relies on _due and _claim_available for the selection rules, and on _row to hand back normal Monitor objects. Later tick or fire operations must present the same claim ID to update or remove the row.

*Call graph*: calls 3 internal fn (_claim_available, _due, _row); 4 external calls (timedelta, select, update, uuid4).


##### `MonitorStore.quiet_tick`  (lines 305–315)

```
async def quiet_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records that a claimed monitor probe ran and matched the saved baseline. This means nothing needs to be posted, but the monitor should count the successful quiet run and schedule its next check.

**Data flow**: It receives the claimed Monitor, the time the probe ran, and the next probe time. It increases probes_run and quiet_streak, resets failure_streak to zero, keeps skipped unchanged, and passes the new values to _tick.

**Call relations**: MonitorRunner._tick calls this when the command output is unchanged. This function prepares the quiet-case counter changes, then hands the actual database update to _tick.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.failed_tick`  (lines 317–329)

```
async def failed_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records that a claimed probe ran but the command exited with an error, without yet reaching the threshold that would fire the monitor. It tracks consecutive failures separately from quiet runs.

**Data flow**: It receives the claimed Monitor, the probe time, and the next probe time. It increases probes_run and failure_streak, resets quiet_streak to zero, keeps skipped unchanged, and sends those updated counts to _tick.

**Call relations**: MonitorRunner._tick calls this for a nonzero command exit that should not fire yet. This function decides the failure-case counter changes and delegates the shared write mechanics to _tick.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.skipped_tick`  (lines 331–342)

```
async def skipped_tick(self, row: Monitor, next_probe_at: datetime) -> None
```

**Purpose**: Records that a probe could not be run at all, for example because the client sandbox was unreachable. A skip is reported separately: it is not counted as a successful probe and not counted as a command failure.

**Data flow**: It receives the claimed Monitor and the next probe time. It leaves probes_run, quiet_streak, failure_streak, and last_probe_at as they were, increases skipped by one, and sends the updated values to _tick.

**Call relations**: MonitorRunner._tick calls this when it cannot run the probe command. This function prepares the skip-specific state change, then _tick performs the guarded database update and releases the claim.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore._tick`  (lines 344–376)

```
async def _tick(self, row: Monitor, *, probes_run: int, quiet_streak: int, failure_streak: int, skipped: int, last_probe_at: datetime | None, next_probe_at: datetime) -> None
```

**Purpose**: Writes the result of one non-firing monitor check back to the database. It also releases the runner’s claim so the monitor can be picked up again later.

**Data flow**: It receives a claimed Monitor plus the new counters and timing fields. If the monitor has no claim ID, it raises an error because unclaimed rows must not be updated this way. Otherwise it updates only the row with the same ID, workspace, and claim ID, sets the new counts and times, clears the claim fields, and updates the timestamp.

**Call relations**: quiet_tick, failed_tick, and skipped_tick all funnel into this one method. The claim check is the important guard: it means a runner can only finish work it actually leased.

*Call graph*: called by 3 (failed_tick, quiet_tick, skipped_tick); 1 external calls (update).


##### `MonitorStore.claim_holds`  (lines 378–405)

```
async def claim_holds(self, row: Monitor) -> bool
```

**Purpose**: Checks whether a claimed monitor row still belongs to this runner just before firing. This avoids posting a fire for a monitor that was stopped or taken over after the probe began.

**Data flow**: It receives a Monitor that should have a claim ID. If no claim exists, it raises an error. Otherwise it looks for the row with the same ID, workspace, and claim ID, locking it while checking. It returns true if the row is still present and claimed by this runner, false if not.

**Call relations**: MonitorRunner._fire calls this immediately before invoking the fire behavior. If it returns false, the runner knows the monitor was removed or no longer belongs to it and should not continue as if it still owns the watch.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `MonitorStore.retire`  (lines 407–419)

```
async def retire(self, row: Monitor) -> None
```

**Purpose**: Deletes a monitor after its fire has been delivered. It only deletes the row if the same runner still holds the claim, which prevents an expired or stale runner from removing another runner’s work.

**Data flow**: It receives a claimed Monitor. If there is no claim ID, it raises an error. Otherwise it deletes the row matching the monitor ID, workspace ID, and claim ID. It returns nothing.

**Call relations**: MonitorRunner._fire calls this after the fire has been sent. This is the normal end of an armed monitor’s life: one monitor leads to one fire, then the row is retired.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `MonitorStore.disarm`  (lines 421–429)

```
async def disarm(self, row: Monitor) -> bool
```

**Purpose**: Stops watching a monitor because a member or caller requested it. Unlike retire, it does not require a claim; it simply removes the monitor from the workspace if it still exists.

**Data flow**: It receives a Monitor. It deletes the row with the same monitor ID and workspace ID. It returns true if exactly one row was removed, or false if the monitor was already gone.

**Call relations**: This is the manual stop path, separate from the runner’s fire path. By deleting the row immediately, it can prevent later probe or fire work from continuing, and claim_holds gives the runner a final check before firing.

*Call graph*: 1 external calls (delete).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `scheduled task creation and scheduler polling`

Scheduled tasks need a way to say “run every day at 9” or “run every 5 minutes.” This file keeps that cron-specific knowledge in one place, instead of spreading it through the task storage or runner code. A cron expression is a compact text pattern for repeating times; here it must have five fields, the common format for minute, hour, day of month, month, and day of week.

The rest of the system stores and sorts tasks by a concrete date and time called `next_run_at`. It does not need to understand cron rules. This file acts like a translator: `validate_cron` makes sure the user’s text is shaped like a supported cron schedule, and `next_fire` turns that schedule plus a current reference time into the next actual datetime.

One important detail is that `next_fire` asks for the next time strictly after the given `after` time. That means if a runner was delayed and missed several scheduled moments, it computes one catch-up run rather than creating a burst of every missed run. This keeps the scheduler from flooding itself after downtime or slow polling.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: Checks whether a schedule string is a supported 5-field cron expression. It is used to reject bad schedules early, before they are saved or used by the runner.

**Data flow**: It receives a text schedule. First it splits the text into space-separated parts and makes sure there are exactly five. Then it asks the cron parsing library whether the expression is valid. If anything is wrong, it raises a clear error; if it is valid, it returns the original schedule unchanged.

**Call relations**: This function relies on `croniter.croniter.is_valid` to understand the detailed cron rules after doing the project’s own five-field check. It fits before scheduling work is stored or acted on, so later code can assume the schedule text is usable.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: Calculates the next date and time when a cron schedule should run after a given moment. The scheduler uses this to turn a repeating rule into a concrete next run time.

**Data flow**: It receives a cron schedule string and an `after` datetime. It gives both to the cron library, which walks forward through the schedule, and returns the next matching datetime after that reference point. It does not change any stored task itself; it only computes the answer.

**Call relations**: This function delegates the cron math to `croniter.croniter`. In the larger scheduled-task flow, runner or update code can call it after a task fires, or when preparing the first run, to decide the next `next_run_at` value.

*Call graph*: 1 external calls (croniter).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pauses.py`

`domain_logic` · `pause arming and scheduled runner processing`

A scheduled pause is like putting a sticky note on a conversation: “wake this workflow up at this time, with this message.” This file defines the database table for those sticky notes and the rules for reading, writing, claiming, and deleting them.

There is only one pause row per conversation. If a workflow arms a new pause for the same conversation, the old one is replaced. That matters because a workflow can only be waiting for one scheduled resume at a time.

The file also protects against race conditions, meaning awkward timing overlaps where two things happen at once. A background runner may be looking for due pauses while an agent re-arms or updates one. To avoid double-firing, due pauses are “claimed” with a temporary lease. A lease is like putting your name on a library book cart: other workers can see someone is already processing those rows until the lease expires.

The stored pause records include two sequence numbers from the original conversation. These are later used by the runner, under the conversation lock, to decide whether a member spoke after the pause was armed. This file deliberately does not answer that question itself; it only preserves the needed markers.

All database access is scoped by workspace ID because the transaction connection is not automatically limited to one workspace.

#### Function details

##### `_aware`  (lines 77–78)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: This small helper makes sure a time value has a timezone. If the database gives back a time without timezone information, it treats it as UTC, the shared standard time used here.

**Data flow**: It receives a datetime value. If that value already says what timezone it belongs to, it is returned unchanged. If it has no timezone, the function adds UTC and returns the corrected value.

**Call relations**: It is used by _row whenever database rows are turned into Pause objects. That keeps the rest of the pause code from having to repeatedly check and fix time values.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 81–97)

```
def _row(row: sa.RowMapping) -> Pause
```

**Purpose**: This function converts one raw database row into a Pause object that the rest of the code can use safely. It also normalizes stored times so callers always receive timezone-aware UTC-style datetimes.

**Data flow**: It receives a row mapping from the database. It pulls out the pause ID, conversation, agent, resume time, sequence markers, prompt text, creator, claim information, and timestamps. It runs the datetime fields through _aware, then returns a Pause value object.

**Call relations**: PauseStore.arm, PauseStore.armed, and PauseStore.claim_due all read rows from the database and then hand them to _row. This makes _row the single doorway from database-shaped data into application-shaped pause data.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 100–101)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition that says whether a pause can be claimed by a worker. A pause is available if nobody has claimed it, or if its previous claim has expired.

**Data flow**: It receives the current time. It produces a SQL condition that checks two possibilities: the claim field is empty, or the claim expiration time is earlier than now.

**Call relations**: PauseStore.claim_due uses this condition when actually leasing due pauses. due_pause_workspaces.due uses the same condition when deciding which workspaces have runnable pause work, so discovery and claiming follow the same rule.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_pause_workspaces`  (lines 104–117)

```
def due_pause_workspaces() -> WorkspaceCandidates
```

**Purpose**: This tells the job system how to find workspaces that have pauses ready to run. It does not claim the pauses itself; it only provides a candidate-finding query to the shared job ownership system.

**Data flow**: It defines an inner query function that looks for distinct workspace IDs with due, claimable pauses. It passes that query builder to owner_candidates, which wraps it in the project’s normal workspace-job selection mechanism.

**Call relations**: The scheduled pause runner uses this as its candidate seam: it asks which workspaces might need attention. The real database query is produced by due_pause_workspaces.due, and owner_candidates turns that query into workspace candidates for the runner.

*Call graph*: 1 external calls (owner_candidates).


##### `due_pause_workspaces.due`  (lines 109–115)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This inner function builds the actual database query for workspaces with due pauses. It looks only for pauses whose timer has arrived and whose lease is free or expired.

**Data flow**: It reads the current UTC time. It builds a SQL SELECT that returns workspace IDs from pause rows where resume_at is at or before now and _claim_available says the pause is not under a live claim. It returns distinct workspace IDs so each workspace appears once.

**Call relations**: It is created inside due_pause_workspaces and handed to owner_candidates. It reuses _claim_available so the candidate search agrees with PauseStore.claim_due about which pauses are claimable.

*Call graph*: calls 1 internal fn (_claim_available); 2 external calls (now, select).


##### `PauseStore.arm`  (lines 126–183)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, resume_at: datetime, origin_seq: int, origin_arrival_seq: int, prompt: str, user_description: str, created_by_member_id: UUID | None) -> P
```

**Purpose**: This creates or replaces the pause for one conversation. It is used when a workflow decides to sleep until a later time and needs the system to remember how to resume it.

**Data flow**: It receives the conversation ID, agent ID, resume time, original sequence markers, prompt text, user-facing description, and optional creator member ID. It opens a workspace transaction, inserts a new pause row, or updates the existing row for that workspace and conversation. It always gives the armed pause a fresh ID and clears any existing claim. It returns the saved pause as a Pause object.

**Call relations**: This is the write path for arming a scheduled resume. It calls uuid4 to create a new identity for the specific wait, writes through the database upsert operation, and then passes the returned row to _row. Clearing claims is important because a newly armed wait must not be treated as work already leased by an older runner tick.

*Call graph*: calls 1 internal fn (_row); 1 external calls (uuid4).


##### `PauseStore.armed`  (lines 185–191)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Pause, ...]
```

**Purpose**: This reads currently armed pauses for the workspace. It can return all pauses, or only the pause for one conversation.

**Data flow**: It receives an optional conversation ID. It builds a database query limited to the current workspace, adds a conversation filter if one was supplied, orders results by resume time, and converts each returned row into a Pause object. It returns a tuple of Pause objects.

**Call relations**: This is the simple read path for inspecting stored pauses. Like the other read methods, it relies on _row so callers receive consistent Pause objects rather than raw database rows.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `PauseStore.claim_due`  (lines 193–234)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_PAUSES) -> tuple[Pause, ...]
```

**Purpose**: This leases a batch of due pauses for one worker to process. The lease prevents multiple background ticks from resuming the same pause at the same time.

**Data flow**: It receives the current time, the lease length in seconds, and a maximum number of pauses to claim. It creates a random claim ID, selects the oldest due and available pause rows for the current workspace, updates those rows with the claim ID and expiration time, and returns the updated rows as Pause objects.

**Call relations**: The pause runner calls this when it is ready to process due work for a workspace. It uses _claim_available to avoid live claims, uuid4 to name this claim, timedelta to compute the expiration, and _row to convert claimed rows into Pause objects. The update-and-return step is atomic, meaning overlapping workers split the work instead of both taking the same rows.

*Call graph*: calls 2 internal fn (_claim_available, _row); 4 external calls (timedelta, select, update, uuid4).


##### `PauseStore.claim_holds`  (lines 236–261)

```
async def claim_holds(self, row: Pause) -> bool
```

**Purpose**: This checks whether a worker’s claim is still valid immediately before firing a pause. It stops an old worker from resuming a pause that was replaced or claimed differently after it was leased.

**Data flow**: It receives a Pause that should already contain a claim ID. If there is no claim ID, it raises an error because an unclaimed pause is not safe to fire. Otherwise it queries the database for the same pause ID, workspace ID, and claim ID, locking the matching row while checking. It returns true if the claim still owns the row, or false if not.

**Call relations**: PauseRunner._fire calls this just before invoking the resume. It acts as a final guard between leasing and firing. If an agent re-armed the pause and cleared the old claim, this check lets the runner notice and avoid firing stale work.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `PauseStore.retire`  (lines 263–276)

```
async def retire(self, row: Pause) -> None
```

**Purpose**: This deletes a pause after the claimed work is finished or no longer needed. It only deletes the row if the same claim still owns it, so an old worker cannot remove a newer re-armed pause.

**Data flow**: It receives a claimed Pause. If the pause has no claim ID, it raises an error because only claimed pauses may be retired. It opens a transaction and deletes the row matching the pause ID, workspace ID, and claim ID. It returns nothing and changes the database only if that exact claimed row still exists.

**Call relations**: PauseRunner._fire calls this after handling a pause. It complements claim_holds: claim_holds checks before firing, and retire cleans up afterward. The claim guard means expired, replaced, or re-armed pauses are not accidentally deleted by the wrong worker.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/schedules.py`

`domain_logic` · `request handling and background scheduling`

A scheduled task is a row in the database: it has a name, a cron-style schedule, a prompt to send, the agent that should run it, the conversation it reports into, and timing marks such as the next run time. This file defines that table and wraps it in ScheduleStore, a small service object that always works inside the current workspace and, for member-facing actions, the current object agent. Without this store, scheduled tasks could be created in the wrong place, listed for the wrong user or agent, fired twice by competing runners, or edited while an old worker still thinks it owns the previous version.

The store has two main audiences. Member-facing code uses it to create, update, cancel, list, and inspect tasks. The background runner uses it to find due work, lease it for a short time, check that the lease still holds, delete expired tasks, and move successful tasks to their next run time. The lease is like putting a temporary “I’m working on this” sticky note on a row. If another worker sees the sticky note before it expires, it leaves the row alone. Important safeguards appear throughout: every database statement filters by workspace, edits match the exact task identity that was read earlier, and datetimes are normalized to UTC so different database engines do not confuse time handling.

#### Function details

##### `_utc`  (lines 124–125)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: Makes sure a datetime value is marked as UTC time. This keeps later code from mixing “timezone-aware” and “timezone-missing” timestamps.

**Data flow**: It receives a datetime. If the datetime already has timezone information, it passes it through unchanged; if it has no timezone, it adds UTC as the timezone. The returned value is always safe to treat as UTC.

**Call relations**: Rows built by _task and status results built by ScheduleStore.inspect_many use this helper before handing times to the rest of the system. _utc_opt uses it for optional timestamps.

*Call graph*: called by 3 (inspect_many, _task, _utc_opt); 1 external calls (replace).


##### `_utc_opt`  (lines 128–129)

```
def _utc_opt(value: datetime | None) -> datetime | None
```

**Purpose**: Does the same UTC cleanup as _utc, but for fields that may be empty. It avoids forcing callers to check for None themselves.

**Data flow**: It receives either a datetime or None. None stays None; a real datetime is passed to _utc and comes back marked as UTC if needed.

**Call relations**: The row builder _task and ScheduleStore.inspect_many use this for optional fields such as last run time and expiration time.

*Call graph*: calls 1 internal fn (_utc); called by 2 (inspect_many, _task).


##### `_claim_available`  (lines 132–136)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this task is free to be claimed.” A task is free if nobody has claimed it, or if the old claim has expired.

**Data flow**: It receives the current time. It returns a SQL condition that checks whether the claim field is empty or the claim expiration time is earlier than now.

**Call relations**: due_task_workspaces.due uses this to decide which workspaces have runnable work. ScheduleStore.claim_due uses the same rule when actually leasing tasks, so discovery and claiming agree.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_expired`  (lines 139–143)

```
def _expired(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this task has passed its expiration date.” Expired tasks should be removed rather than fired.

**Data flow**: It receives the current time. It returns a SQL condition requiring an expiration timestamp to exist and be at or before now.

**Call relations**: due_task_workspaces.due uses this so workspaces with expired tasks are visited for cleanup. ScheduleStore.claim_due uses it to delete expired rows before leasing due tasks.

*Call graph*: called by 2 (claim_due, due); 1 external calls (and_).


##### `_task`  (lines 146–165)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: Turns a raw database row into a ScheduledTask object that the rest of the code can use. It also fixes timestamp timezone details in one central place.

**Data flow**: It receives a database row with scheduled-task columns. It copies the row fields into a ScheduledTask value object, converting all datetime fields to UTC-aware values along the way.

**Call relations**: Create, update, list, and claim_due all funnel their database results through this function before returning tasks. That means callers receive one consistent shape no matter which database query produced the row.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 4 (claim_due, create, list, update); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 168–191)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: Provides the background job system with a way to find workspaces that might need scheduled-task attention. It points the runner toward workspaces with due tasks or expired tasks to clean up.

**Data flow**: It creates an inner query function that selects distinct workspace IDs from the scheduled-task table. It wraps that query with the job system’s owner-candidate helper and returns the resulting candidate source.

**Call relations**: The scheduled-task runner uses this as its discovery seam. The inner due query does the actual database filtering, while owner_candidates adapts that query to the broader job ownership system.

*Call graph*: 1 external calls (owner_candidates).


##### `due_task_workspaces.due`  (lines 174–189)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces containing available scheduled-task work. It includes both runnable tasks and expired tasks that should be swept away.

**Data flow**: It reads the current UTC time, then builds a SQL select for workspace IDs where a task is claimable and either expired or due to run while not paused. The output is a distinct list of workspace IDs.

**Call relations**: due_task_workspaces hands this query builder to owner_candidates. It relies on _claim_available and _expired so its definition of runnable work matches ScheduleStore.claim_due.

*Call graph*: calls 2 internal fn (_claim_available, _expired); 5 external calls (now, and_, not_, or_, select).


##### `ScheduleStore.workspace_id`  (lines 205–206)

```
def workspace_id(self) -> UUID
```

**Purpose**: Exposes the workspace ID from the current extension context. This keeps all store operations tied to the workspace they are supposed to affect.

**Data flow**: It reads ctx.workspace_id from the ScheduleStore instance and returns it. It does not change anything.

**Call relations**: Most ScheduleStore database methods use this property when adding workspace filters to their queries, which prevents one workspace’s tasks from leaking into another.


##### `ScheduleStore.create`  (lines 208–267)

```
async def create(self, conversation_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None, expires_at: datetime | None=None,
```

**Purpose**: Creates a new recurring scheduled task. It also checks that the task reports to a conversation owned by the same agent that will run it.

**Data flow**: It receives the conversation, task name, schedule, prompt, description, next run time, optional creator, optional expiration, and pause state. It looks up the current object agent, verifies the conversation belongs to that agent, inserts a new row, and returns the inserted row as a ScheduledTask. If another task with the same workspace, agent, and name already exists, it raises an error.

**Call relations**: Member-facing task creation code calls this when a user or tool defines a new scheduled task. It uses object_agent_id to bind the task to the current agent, uuid4 to create a new ID, and _task to convert the inserted database row into the returned object.

*Call graph*: calls 1 internal fn (_task); 2 external calls (object_agent_id, uuid4).


##### `ScheduleStore.update`  (lines 269–323)

```
async def update(self, expected: ScheduledTask, schedule: str, prompt: str, description: str, next_run_at: datetime, expires_at: datetime | None=None, *, paused: bool) -> ScheduledTask
```

**Purpose**: Edits an existing scheduled task’s schedule, prompt, description, next run time, expiration, and paused state without rewriting its run history. It is careful to update only the exact task version the caller previously read.

**Data flow**: It receives the previously-read ScheduledTask plus the new editable fields. It checks that the current agent still matches, builds a guarded database update using the task’s ID, agent, conversation, name, and creator, clears any existing claim, and returns the updated row as a ScheduledTask. If no row matches, it raises an error because the task changed or disappeared during editing.

**Call relations**: Member-facing edit paths call this after loading a task. It uses _creator_matches to preserve creator ownership, SQL update to change the row, and _task to return the updated result.

*Call graph*: calls 2 internal fn (_creator_matches, _task); 2 external calls (update, object_agent_id).


##### `ScheduleStore.cancel`  (lines 325–341)

```
async def cancel(self, expected: ScheduledTask) -> None
```

**Purpose**: Deletes a scheduled task, but only if it still matches the task the caller intended to cancel. This prevents accidentally deleting a different task after a race or edit.

**Data flow**: It receives the previously-read ScheduledTask. It checks the current agent, then deletes a row matching the workspace, ID, agent, conversation, name, and creator. If no row was deleted, it raises an error because the task changed while cancellation was happening.

**Call relations**: Member-facing cancel paths call this. It shares the same exact-match style as update, including _creator_matches, so cancellation respects the same ownership boundary.

*Call graph*: calls 1 internal fn (_creator_matches); 2 external calls (delete, object_agent_id).


##### `ScheduleStore._creator_matches`  (lines 343–348)

```
def _creator_matches(self, expected: ScheduledTask) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says whether a row has the same creator as an expected task. It treats “no creator” carefully, because SQL needs a special check for empty values.

**Data flow**: It receives an expected ScheduledTask. If the task has no creator member ID, it returns a condition requiring the database field to be NULL; otherwise it returns a condition requiring equality with that creator ID.

**Call relations**: ScheduleStore.update and ScheduleStore.cancel use this as part of their safety check. It helps ensure an edit or delete meant for one member’s task does not land on an unowned task or someone else’s task.

*Call graph*: called by 2 (cancel, update).


##### `ScheduleStore._listing`  (lines 350–371)

```
def _listing(self, selected: tuple[sa.ColumnElement[Any], ...], *, conversation_id: UUID | None, names: tuple[str, ...] | None, visible_to_member_id: UUID | None, include_all_owners: bool, limit: int
```

**Purpose**: Builds the shared database query used to list scheduled tasks. It applies workspace, agent, conversation, name, owner, ordering, and limit filters in one place.

**Data flow**: It receives the columns to select and optional filters such as conversation ID, names, visible member ID, owner inclusion, and limit. It returns a SQL select query sorted by task name, narrowed to the current workspace and current object agent.

**Call relations**: ScheduleStore.list calls this to avoid duplicating listing rules. It uses object_agent_id so member-facing listings stay inside the selected agent’s task namespace.

*Call graph*: called by 1 (list); 2 external calls (select, object_agent_id).


##### `ScheduleStore.list`  (lines 373–392)

```
async def list(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -> tuple[S
```

**Purpose**: Returns scheduled tasks visible under the requested filters. This is the basic read operation for task pages and lookups.

**Data flow**: It receives optional filters for conversation, names, member visibility, owner inclusion, and result limit. It builds a query with _listing, runs it in a transaction, converts each row through _task, and returns a tuple of ScheduledTask objects.

**Call relations**: Member-facing code can call this directly when it only needs task rows. ScheduleStore.list_reported builds on it when it also needs conversation audience and label information.

*Call graph*: calls 2 internal fn (_listing, _task); called by 1 (list_reported).


##### `ScheduleStore.list_reported`  (lines 394–428)

```
async def list_reported(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -
```

**Purpose**: Lists tasks together with facts about the conversation each task reports into. This supports user-facing displays that need to decide visibility or show the conversation label.

**Data flow**: It first gets tasks by calling ScheduleStore.list with the supplied filters. If there are tasks, it asks the context for live conversation facts for their conversation IDs, then returns ListedTask objects combining each task with the conversation audience and surface label. Tasks whose conversation facts are missing are left out.

**Call relations**: This is the richer listing path for member-facing surfaces. It reuses list for task filtering, then calls the context for conversation facts so labels stay current if a channel or surface was renamed.

*Call graph*: calls 1 internal fn (list); 1 external calls (__init__).


##### `ScheduleStore.claim_due`  (lines 430–486)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: Lets the background runner safely lease a batch of tasks that are ready to run. It also deletes expired tasks that no live worker currently owns.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of tasks. It creates a unique claim ID, deletes claim-available expired rows, selects the oldest due unpaused claim-available rows up to the limit, stamps them with the claim and claim expiration, and returns them as ScheduledTask objects.

**Call relations**: The scheduled-task runner calls this when polling a workspace for work. It uses _expired and _claim_available to match the workspace discovery rules, SQL delete for cleanup, SQL update for atomic leasing, and _task to hand claimed tasks back to the runner.

*Call graph*: calls 3 internal fn (_claim_available, _expired, _task); 6 external calls (timedelta, delete, not_, select, update, uuid4).


##### `ScheduleStore.claim_holds`  (lines 488–519)

```
async def claim_holds(self, task: ScheduledTask) -> bool
```

**Purpose**: Checks whether a previously claimed task is still the same task and still owned by this claim. This is a last-second safety check before firing the task.

**Data flow**: It receives a ScheduledTask that must contain a claim ID. It re-reads the database row under a lock, matching the workspace, task ID, claim ID, conversation, agent, name, and schedule. It returns true if such a row still exists, false otherwise.

**Call relations**: ScheduledTaskRunner._fire calls this immediately before invoking the scheduled prompt. If a user edited, cancelled, or another worker reclaimed the row, this check fails and the runner skips the fire.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `ScheduleStore.retire_if_expired`  (lines 521–535)

```
async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool
```

**Purpose**: Deletes a claimed task if its expiration time has already passed before it is invoked. This prevents an expired task from running just because it was leased earlier.

**Data flow**: It receives a claimed ScheduledTask and the current time. If the task has no expiration or expires in the future, it returns false. If it is expired, it deletes the matching claimed row and returns true.

**Call relations**: ScheduledTaskRunner._fire calls this as part of the firing path. It is a cleanup gate: when it returns true, the runner knows the task was retired instead of fired.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `ScheduleStore.reschedule`  (lines 537–567)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: Moves a successfully handled claimed task to its next run time and releases its lease. It also records when it last ran and, if available, which turn was created by the fire.

**Data flow**: It receives a claimed ScheduledTask, the next run time, the last run time, and an optional last turn ID. It updates the matching claimed row with the new timing data, clears the claim fields, stores the turn ID if provided, and returns whether a row was actually updated.

**Call relations**: ScheduledTaskRunner._fire calls this after a task has fired and the next occurrence has been calculated. Later, inspect and inspect_many use the stored last turn ID to show the latest outcome.

*Call graph*: called by 1 (_fire); 1 external calls (update).


##### `ScheduleStore.inspect`  (lines 569–573)

```
async def inspect(self, expected: ScheduledTask) -> TaskInspection | None
```

**Purpose**: Gets the live status picture for one scheduled task. It is a convenience wrapper around the multi-task inspection path.

**Data flow**: It receives one expected ScheduledTask, calls inspect_many with that single task, and returns the matching TaskInspection if present. If the task is gone or no longer matches, it returns None.

**Call relations**: Status-rendering code can call this for a single task. It delegates all real work to ScheduleStore.inspect_many so single and batch inspection follow the same rules.

*Call graph*: calls 1 internal fn (inspect_many).


##### `ScheduleStore.inspect_many`  (lines 575–611)

```
async def inspect_many(self, expected: tuple[ScheduledTask, ...]) -> dict[UUID, TaskInspection]
```

**Purpose**: Builds status information for several scheduled tasks at once, including timing marks and the latest run outcome. This is what lets a UI say when a task will run next and what happened last time.

**Data flow**: It receives a tuple of expected ScheduledTask objects. It queries matching rows in the current workspace and current agent, gathers any recorded last turn IDs, asks the context for those turn outcomes, and returns a dictionary from task ID to TaskInspection. It skips rows whose name or conversation no longer match the expected task.

**Call relations**: ScheduleStore.inspect calls this for one task, and batch status code can call it directly. It uses object_agent_id to stay in the current agent scope, _utc and _utc_opt to normalize times, and context turn_outcomes to attach the latest status text.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 1 (inspect); 3 external calls (__init__, select, object_agent_id).


### Source and brief triggers
Source-change triggers and the Sweep extension create additional recurring or event-driven conversation wakeups.

### `extensions/sources/ufo_ext_sources/triggers.py`

`domain_logic` · `request handling and background alert sweep`

A source trigger is like a standing notification rule: “when this source binding changes, send the update to this conversation.” This file defines the database table for those rules, the small Python objects used to read them, and a SourceTriggerStore that performs the real work.

The important safety rule is workspace isolation. The database connection is not automatically limited to one workspace, so every query in this file explicitly checks the workspace_id. Without that, one workspace could accidentally see or delete another workspace’s triggers.

Creating a trigger also checks that the conversation belongs to the agent currently running. That prevents an agent from setting up a rule that wakes a conversation owned by another agent. If two requests try to create the same trigger at the same time, the database conflict is turned into a clear ValueError rather than leaking a low-level database error.

There are two main reading paths. The alert path asks “which triggers wake for this binding?” and reads workspace-wide, because a source belongs to the workspace and different agents may subscribe to it. The member-facing listing asks “what can this user-facing surface report?” and adds live conversation facts such as audience and channel label, so renamed channels show the current name.

#### Function details

##### `_utc`  (lines 85–86)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: Makes sure a datetime value has timezone information. If the database returned a plain time with no timezone attached, this treats it as UTC, which is the shared standard time used by the system.

**Data flow**: It receives a datetime. If that datetime already says what timezone it is in, it is returned unchanged. If it has no timezone, the function adds UTC and returns the adjusted datetime.

**Call relations**: _trigger calls this while turning database rows into SourceTrigger objects. That means every trigger object leaving this file has predictable timestamp values instead of a mix of timezone-aware and timezone-missing times.

*Call graph*: called by 1 (_trigger); 1 external calls (replace).


##### `_trigger`  (lines 89–104)

```
def _trigger(row: sa.RowMapping) -> SourceTrigger
```

**Purpose**: Turns one database row into a SourceTrigger object that the rest of the code can use safely. It also rejects unknown delivery modes, so bad stored data is caught immediately instead of spreading further.

**Data flow**: It receives a row read from the source_trigger table. It checks that the delivery value is one of the supported choices, converts the created and updated times through _utc, and returns a SourceTrigger containing the row’s important fields. If the delivery value is unknown, it raises an error.

**Call relations**: The store methods create, waking, and list_reported all call this after reading from the database. It is the translation point between raw database results and the plain in-memory trigger object used by alerting and listing code.

*Call graph*: calls 1 internal fn (_utc); called by 3 (create, list_reported, waking); 1 external calls (__init__).


##### `SourceTriggerStore.workspace_id`  (lines 114–115)

```
def workspace_id(self) -> UUID
```

**Purpose**: Provides the workspace id from the surrounding extension context. This is a convenience property, but it is important because every database operation in this file must stay inside the current workspace.

**Data flow**: It reads the workspace_id from the store’s ExtensionContext and returns it. It does not change anything.

**Call relations**: The store methods use this value when building their database queries. It is how create, remove, remove_binding, waking, and list_reported keep their reads and writes scoped to the right workspace.


##### `SourceTriggerStore.create`  (lines 117–166)

```
async def create(self, conversation_id: UUID, binding: str, delivery: SourceTriggerDelivery, created_by_member_id: UUID | None=None) -> SourceTrigger
```

**Purpose**: Creates a new rule saying that a particular conversation wants updates from a particular source binding. It refuses to create the rule if the conversation is not owned by the currently executing agent, or if the same conversation already watches the same binding.

**Data flow**: It receives a conversation id, a source binding name, a delivery style, and optionally the member who asked for it. It looks up the current agent, checks that the conversation belongs to that agent, inserts a new database row with a fresh id and timestamps, and returns the new SourceTrigger. If the row already exists or the ownership check fails, it raises a clear ValueError.

**Call relations**: This is called when something sets up a new source subscription. It calls object_agent_id to identify the current agent, uses uuid4 to make the trigger id, writes the row to the database, and then hands the returned row to _trigger so callers receive a normal SourceTrigger object.

*Call graph*: calls 1 internal fn (_trigger); 2 external calls (object_agent_id, uuid4).


##### `SourceTriggerStore.remove`  (lines 168–183)

```
async def remove(self, expected: SourceTrigger) -> None
```

**Purpose**: Removes one specific trigger, but only if it still matches the trigger the caller expected to remove. This protects against deleting the wrong rule if the stored row changed meanwhile.

**Data flow**: It receives an expected SourceTrigger. It checks that the trigger’s agent matches the currently executing agent, then deletes the database row only if the workspace, trigger id, agent id, conversation id, and binding all match. If nothing was deleted, it reports that the trigger changed while removal was attempted.

**Call relations**: This is used when a caller is removing a known trigger object. It calls object_agent_id for the current agent and uses a database delete statement. Unlike remove_binding, it is precise and cautious: it removes one row only when the stored facts still match the caller’s copy.

*Call graph*: 2 external calls (delete, object_agent_id).


##### `SourceTriggerStore.remove_binding`  (lines 185–194)

```
async def remove_binding(self, binding: str) -> None
```

**Purpose**: Deletes all triggers in the current workspace for one source binding. This is used when the source itself goes away, so no conversation should continue watching it.

**Data flow**: It receives a binding string. It deletes every source_trigger row in the current workspace with that binding. It does not return the deleted rows and does not care which agents owned them, because the source is workspace-wide.

**Call relations**: This is the cleanup path for source removal. It uses a database delete statement directly and intentionally works across agents within the same workspace, because any trigger pointing at the removed binding is now useless.

*Call graph*: 1 external calls (delete).


##### `SourceTriggerStore.waking`  (lines 196–209)

```
async def waking(self, binding: str) -> tuple[SourceTrigger, ...]
```

**Purpose**: Finds every trigger in the workspace that should wake when a given source binding changes. This is the read used by the alert sweep before delivering updates.

**Data flow**: It receives a binding string. It selects matching trigger rows for the current workspace, ordered by creation time and id for stable processing, converts each row with _trigger, and returns them as a tuple.

**Call relations**: The alerting flow calls this when a source reports changes. It builds a database select query, reads all matching rows, and passes each row through _trigger so the alerting code receives clean SourceTrigger objects with checked delivery modes.

*Call graph*: calls 1 internal fn (_trigger); 1 external calls (select).


##### `SourceTriggerStore.list_reported`  (lines 211–245)

```
async def list_reported(self, *, conversation_id: UUID | None=None) -> tuple[ListedTrigger, ...]
```

**Purpose**: Lists this agent’s triggers in a form suitable for showing or reporting to a member. Each trigger is paired with live conversation details, such as who can see that conversation and its current surface label.

**Data flow**: It optionally receives a conversation id to narrow the listing. It reads triggers for the current workspace and current agent, optionally filters to one conversation, converts rows into SourceTrigger objects, then asks the context for conversation facts. It returns ListedTrigger objects only for conversations that still exist in those facts.

**Call relations**: Member-facing code calls this when it needs to show what source watches exist. It calls object_agent_id to stay within the current agent’s namespace, uses a database select to find trigger rows, uses _trigger to turn them into objects, then combines them with conversation facts before returning ListedTrigger entries.

*Call graph*: calls 1 internal fn (_trigger); 3 external calls (__init__, select, object_agent_id).


### `extensions/sweep/ufo_ext_sweep/manifest.py`

`orchestration` · `scheduled daily job and tool execution`

This file is the control center for the daily brief feature. Its job is to make sure each member gets at most one daily brief per local date, based on what has changed in their private workspace since the last completed brief. Without it, the system would not know when to start a brief, what context to collect, how to avoid repeating old items, or how to stop a scheduled brief from directly changing member-facing state.

The file keeps a small database ledger called `sweep_edition`. Think of it like a delivery log: it records which member/date brief is pending, completed, failed, or being retried, plus what input and findings were already used. A scheduled job, `_tick`, walks through seated members, checks whether it is after 8 AM in each member’s time zone, registers or retries that day’s edition, and asks the daily-brief agent to run.

When the agent calls the `sweep_newspaper` tool, `_sweep` gathers changed member context, optionally fetches public web summaries from safe-looking URLs, splits the material into four scout groups, and runs four small subagents. Each scout returns structured findings. The tool filters unsupported references, removes repeated findings, updates the ledger, and returns compact JSON for the final brief.

A hook, `_draft_only`, acts as a guardrail. Scheduled briefs may propose drafts, but they are blocked from directly updating tasks, memories, or a homepage.

#### Function details

##### `local_edition_date`  (lines 126–128)

```
def local_edition_date(now: datetime, timezone: str) -> date | None
```

**Purpose**: Decides whether a member is ready to receive today’s daily brief. It uses the member’s time zone and only returns a date once the local time is at least 8 AM.

**Data flow**: It receives the current time and a time zone name. It converts the current time into that local time zone, checks the local clock, and returns the local calendar date if it is 8 AM or later; otherwise it returns nothing.

**Call relations**: _tick calls this while walking through members. It acts like the clock check before the scheduler creates or retries an edition for that member.

*Call graph*: called by 1 (_tick); 3 external calls (astimezone, time, ZoneInfo).


##### `_profile`  (lines 131–142)

```
def _profile(name: str) -> SubagentProfile
```

**Purpose**: Builds the configuration for one scout subagent. A scout is a small helper agent focused on one slice of the daily brief, such as work items or public context.

**Data flow**: It receives a scout name. It turns that name into a `SubagentProfile` with a prompt, input and output shapes, model choice, and safety settings, then returns that profile for registration.

**Call relations**: manifest calls this once for each scout name when building the extension manifest. The resulting profiles are later used by `_sweep` when it asks the platform to spawn those scouts.

*Call graph*: called by 1 (manifest); 1 external calls (__init__).


##### `_bounded`  (lines 145–164)

```
def _bounded(records: tuple[MemberContextRecord, ...]) -> tuple[ContextRecord, ...]
```

**Purpose**: Shrinks a list of member context records so a scout receives a safe, limited amount of text. This prevents oversized prompts and keeps scout work focused.

**Data flow**: It receives member context records. For each record, it trims the text, counts roughly how much input has been used, stops when the shared size limit would be exceeded, and returns simplified `ContextRecord` objects.

**Call relations**: _sweep` calls this after splitting changed records into scout groups. The bounded records become the exact material each scout is allowed to read and cite.

*Call graph*: called by 1 (_sweep); 1 external calls (__init__).


##### `_digest`  (lines 167–168)

```
def _digest(value: str) -> str
```

**Purpose**: Creates a stable fingerprint for a piece of text. This is used to make repeatable keys for public web records.

**Data flow**: It receives a string. It encodes the string, runs it through SHA-256, which is a standard one-way hashing method, and returns the hexadecimal fingerprint.

**Call relations**: _public_records calls this when it builds a stable subject key for fetched public pages, so later ledger checks can recognize the same public source content.

*Call graph*: called by 1 (_public_records); 1 external calls (sha256).


##### `_prior_ledgers`  (lines 171–205)

```
async def _prior_ledgers(ext: ExtensionContext, member_id: UUID, now: datetime) -> tuple[dict[str, datetime], dict[str, datetime]]
```

**Purpose**: Looks up what this member’s recent completed daily briefs already used. This helps the new brief avoid repeating the same inputs and findings too soon.

**Data flow**: It receives the extension context, a member ID, and the current time. It reads completed edition rows from the database within the retention window, collects remembered input keys and finding keys with their completion times, and returns two lookup tables.

**Call relations**: _sweep calls this before choosing changed records and before emitting findings. The returned ledgers guide `_changed_records` and the final de-duplication step.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_sweep); 1 external calls (select).


##### `_changed_records`  (lines 208–218)

```
def _changed_records(records: tuple[MemberContextRecord, ...], prior: dict[str, datetime], now: datetime) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Filters member context down to records that are new enough or old enough to mention again. It treats open work items more gently, allowing them to repeat after a shorter wait.

**Data flow**: It receives context records, a lookup of previously used input keys, and the current time. It keeps one record per stable subject key, checks when that subject was last used, applies the right waiting period, and returns the remaining records sorted newest first.

**Call relations**: _sweep calls this after loading prior input history. Its output becomes the private source material that is split among the work, missed-items, and pages-artifacts scouts.

*Call graph*: called by 1 (_sweep).


##### `_public_records`  (lines 221–260)

```
async def _public_records(ctx: ToolContext, records: tuple[MemberContextRecord, ...], now: datetime) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Finds safe public URLs mentioned in private context and fetches short summaries for them. This gives the public-context scout outside background without exposing it to unsafe internal addresses.

**Data flow**: It receives the tool context, changed private records, and the current time. It scans record text and references for web links, rejects links with credentials or private/internal-looking hosts, normalizes each accepted site URL, fetches a limited number through the search provider, and returns public `MemberContextRecord` objects with summaries and stable keys.

**Call relations**: _sweep calls this after private changes are selected. It calls `_digest` to make stable keys and uses the search provider’s fetch support when available; the resulting public records feed only the public-context scout.

*Call graph*: calls 1 internal fn (_digest); called by 1 (_sweep); 6 external calls (__init__, __init__, gather, ip_address, urlsplit, urlunsplit).


##### `_sweep`  (lines 263–421)

```
async def _sweep(ctx: ToolContext, _args: SweepInput) -> ToolResult
```

**Purpose**: Runs the main daily-brief collection tool. It gathers changed context, sends bounded slices to four scouts, filters and de-duplicates their findings, records progress, and returns JSON for the daily-brief agent.

**Data flow**: It starts with the tool context and the tool’s simple input. It verifies this is a scheduled member run, reads the registered edition row and last completed cursor, fetches member context, reads prior ledgers, selects changed records, gathers public context, splits everything into scout groups, runs the scouts, removes unsupported references and repeated findings, updates the edition ledger, and returns a `ToolResult` containing compact JSON with findings, coverage notes, and any missing scouts.

**Call relations**: The daily-brief agent calls this tool during a scheduled turn. Inside, it depends on `_prior_ledgers`, `_changed_records`, `_public_records`, and `_bounded`, then uses the nested `_sweep.scout` helper through concurrent tasks. `_tick` is the earlier scheduler that registered the edition this tool expects to find.

*Call graph*: calls 4 internal fn (_bounded, _changed_records, _prior_ledgers, _public_records); 7 external calls (__init__, __init__, gather, now, dumps, select, update).


##### `_sweep.scout`  (lines 307–339)

```
async def scout(name: str) -> tuple[ScoutOutput, bool]
```

**Purpose**: Runs one named scout and cleans up its answer. It makes sure a scout only cites references that were actually provided to it.

**Data flow**: It receives a scout name from the surrounding `_sweep` function. It takes that scout’s bounded records, asks the platform to spawn the matching subagent, validates the structured output, removes any references not in the allowed input set, adjusts the coverage note if anything was removed, and returns the cleaned scout output plus a flag saying whether the result is safe to count in the input ledger.

**Call relations**: _sweep uses this helper for each scout name and runs the helpers together with `asyncio.gather`, meaning the scouts work in parallel. The cleaned outputs then flow back into `_sweep` for final de-duplication and ledger updates.

*Call graph*: 2 external calls (__init__, __init__).


##### `_finalize`  (lines 424–460)

```
async def _finalize(ctx: ExtensionContext, now: datetime) -> None
```

**Purpose**: Closes out editions whose agent turns have finished. It marks a pending edition as completed only if the turn ended successfully and the sweep tool recorded a usable cursor.

**Data flow**: It receives the extension context and the current time. It reads pending edition rows joined to their turn status, decides whether each is truly completed or failed, and updates the edition row with the final status, completion time if appropriate, and updated timestamp.

**Call relations**: _tick calls this at the start of each scheduled run. That keeps the ledger clean before `_tick` decides whether today’s edition should be created, skipped, or retried.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_tick); 2 external calls (select, update).


##### `_draft_only`  (lines 463–484)

```
async def _draft_only(ctx: HookContext) -> HookOutcome
```

**Purpose**: Blocks scheduled daily-brief turns from directly changing member-visible data. The brief may propose drafts, but member-facing actions need approval in a normal turn.

**Data flow**: It receives a hook context before a tool is used. It checks whether the current turn belongs to a scheduled sweep edition; if not, it does nothing. If it is scheduled, it returns a denial message for task, memory, or homepage tools, with a specific message for homepage binding.

**Call relations**: manifest registers this as a pre-tool-use hook for the update-todo-list, memory-update, and set-homepage tools. The platform calls it before those tools run, so it can stop unsafe scheduled actions early.

*Call graph*: 2 external calls (__init__, select).


##### `_tick`  (lines 487–584)

```
async def _tick(ctx: ExtensionContext, now: datetime | None=None) -> None
```

**Purpose**: Performs the scheduled daily check for all eligible members. It decides who needs today’s brief, records the attempt, invokes the daily-brief agent, and stores the resulting conversation and turn IDs.

**Data flow**: It receives the extension context and optionally a current time. It first finalizes old pending editions, then pages through seated members. For each member whose local time is after 8 AM, it checks the edition table, skips completed or already-running work, inserts or retries a pending row, invokes the daily-brief agent with an idempotency key so duplicate starts are avoided, and updates the row with the scheduled turn details or marks it failed if invocation is not allowed.

**Call relations**: The job registered by manifest calls `_tick` on the schedule. `_tick` relies on `_finalize` to settle old work and `local_edition_date` to respect each member’s time zone, then uses the extension context to list members, write the ledger, and start the agent.

*Call graph*: calls 5 internal fn (invoke_agent_for_member, seated_members, transaction, _finalize, local_edition_date); 4 external calls (now, insert, select, update).


##### `manifest`  (lines 587–642)

```
def manifest() -> Manifest
```

**Purpose**: Declares the Sweep extension to the host system. It tells the platform what agent, tool, job, hook, subagents, skill, permissions, and outside services this extension needs.

**Data flow**: It takes no input. It builds a `Manifest` object containing the daily-brief agent setup, the `sweep_newspaper` tool wired to `_sweep`, the scheduled job wired to `_tick`, the draft-only hook wired to `_draft_only`, the scout profiles created by `_profile`, and the daily-brief skill path, then returns that manifest.

**Call relations**: The host loads this function when installing or starting the extension. Everything else in the file becomes active because manifest registers it: `_tick` as the scheduled job, `_sweep` as the tool handler, `_draft_only` as the safety hook, and `_profile` outputs as spawnable scouts.

*Call graph*: calls 1 internal fn (_profile); 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, seated_member_workspaces).
