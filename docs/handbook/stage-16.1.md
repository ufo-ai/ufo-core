# Timers, pauses, monitors, and scheduled turns  `stage-16.1`

This stage is the system’s alarm clock and watchman. It runs behind the scenes after startup, during normal operation, to make sure future work happens at the right time and only once.

Scheduled task files store reminders and recurring prompts. schedules.py keeps them in the database and lets users create, edit, cancel, and list them. cron.py understands “cron” rules, a compact five-part way to say times like “every weekday morning.” runner.py wakes up on a clock tick, claims due tasks safely, fires them, and sets the next run time. scheduled_fire.py gives each planned run a shared name so different code agrees about what is firing.

Pause files support “wait until a person replies, or until time runs out.” tools.py exposes scheduling and pause actions to agents. pauses.py stores paused conversations and prevents double resumes. pause_runner.py wakes expired pauses and resumes the workflow if no human already did.

Monitor files handle one-time watches on outside state. monitor_tool.py creates a watch after testing its command. monitors.py stores and claims watches. monitor_runner.py checks them until they change, fail, expire, or stop. The __init__.py files simply make these extensions importable.

## Files in this stage

### Extension package markers
These package files make the monitor and scheduled-task extensions importable by the rest of the system.

### `extensions/monitors/ufo_ext_monitors/__init__.py`

`other` · `import/package discovery`

This file is intentionally empty, but it still has a job. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as a package: a named container for related code. Here, that container is `extensions/monitors/ufo_ext_monitors`, which suggests it holds monitor extensions for the project. Think of it like a label on a drawer. The drawer may contain useful tools in other files, and the label lets the rest of the program find them by name. There is no runtime logic here, no setup code, and no functions to call. If this file were missing in some Python setups, imports that expect `ufo_ext_monitors` to be a package might fail or behave differently.


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/__init__.py`

`other` · `import time`

This is an empty package-start file. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package, a bit like putting a label on a box so other code knows it can open it and find related parts inside. Here, the package is for the scheduled-tasks extension. There are no functions, settings, or startup actions in this file, so it does not perform any work by itself. Its value is structural: without it, some tooling or import paths may not recognize `ufo_ext_scheduled_tasks` as a normal package, which could make the extension harder or impossible to load in environments that expect traditional Python packages.


### Scheduled automation surfaces
These files expose scheduled tasks and pauses as user-facing tools and conversation-visible automation data.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `object operations and tool invocation during conversation turns`

This file solves two related waiting problems. First, it lets an agent create recurring tasks: a saved prompt plus a UTC cron schedule, which is a five-part calendar rule such as “9:00 every weekday.” Each task is tied to the conversation and member that created it, so when it fires later it re-enters the same conversation and acts with the original creator’s authority. Without this file, scheduled tasks would not have a public object shape, permission rules, listing details, or create/update/delete behavior.

The file also defines a pause tool for workflows that need to stop temporarily, such as waiting for an approval or an external email. Unlike scheduled tasks, a pause is not something users browse and edit as an object. It is more like setting an alarm while leaving a note on the desk: the system records when and how to resume, then returns instructions telling the agent to end its turn.

A key theme is safety. Task prompts may be hidden from people who can see the conversation but should not see private task content. Admins can adjust timing details, but they cannot rewrite another member’s prompt or force a “run now” under that member’s private access. The code also checks that expiry times are valid UTC timestamps and that a task will not expire before its next planned run.

#### Function details

##### `ScheduledTaskSpec.validate_utc_expiry`  (lines 114–117)

```
def validate_utc_expiry(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This validator makes sure a task expiry time is written in UTC, the shared time standard used by the scheduler. It prevents ambiguous local times from being saved.

**Data flow**: It receives the proposed expiry time from a task specification. If there is no expiry, it leaves it alone. If there is an expiry, it checks that the timestamp has timezone information and that its offset is exactly UTC; invalid values become a clear error, valid values pass through unchanged.

**Call relations**: This runs automatically when a ScheduledTaskSpec is built or checked. It is an early guard before the later create or update path uses the expiry to decide whether the task can safely be scheduled.

*Call graph*: 2 external calls (utcoffset, timedelta).


##### `_require_scheduler`  (lines 134–135)

```
def _require_scheduler(ext: ExtensionContext | None) -> ScheduleStore
```

**Purpose**: This helper turns the extension context into a ScheduleStore, which is the storage-facing object used to read and write scheduled tasks. It centralizes the “you must have the scheduled-tasks extension context” check.

**Data flow**: It receives an optional extension context. It first requires that the context exists, then wraps it in a ScheduleStore and returns that store for callers to use.

**Call relations**: Most task object operations call this before touching scheduled-task storage. It relies on _require_ext for the shared context check, then hands the resulting ScheduleStore to listing, status, create, update, delete, and lookup flows.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_apply_owned, _delete_owned, _find, _rows, _status, member_conversation_rows); 1 external calls (__init__).


##### `_require_ext`  (lines 138–141)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This helper rejects calls that try to use scheduled-task features without the extension context they depend on. It gives failures a clear message instead of letting later storage code fail mysteriously.

**Data flow**: It receives an optional extension context. If the context is missing, it raises an error; otherwise it returns the same context unchanged.

**Call relations**: _require_scheduler uses it before building a ScheduleStore, and pause_and_wait uses it before writing a pause. It is the small front-door check shared by both major features in this file.

*Call graph*: called by 2 (_require_scheduler, pause_and_wait).


##### `_summary`  (lines 144–145)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: This creates the short one-line label shown when a scheduled task appears in a list. It combines the schedule with either the description or the prompt, then trims it to a safe display length.

**Data flow**: It receives a ScheduledTask. It reads the task’s schedule, description, and prompt, builds a compact string, cuts it to the maximum summary length, and returns that string.

**Call relations**: The row-building code calls this when task content is visible to the reader. If the reader should not see the content, _rows uses a private placeholder instead.

*Call graph*: called by 1 (_rows).


##### `_validate_future_fire`  (lines 148–152)

```
def _validate_future_fire(next_run_at: datetime, expires_at: datetime | None, *, paused: bool) -> None
```

**Purpose**: This protects users from saving a running task whose expiry time is already too soon. A task that is not paused must have an expiry later than its next planned fire.

**Data flow**: It receives the next planned run time, an optional expiry time, and whether the task is paused. If the task is active and the expiry is at or before the next run, it raises an error. Otherwise it returns nothing and allows the save to continue.

**Call relations**: _apply_owned calls this during both task creation and task update. It sits just before storage writes, after the code has calculated the next run time.

*Call graph*: called by 1 (_apply_owned).


##### `_owner`  (lines 155–163)

```
def _owner(listed: ListedTask) -> GeneratedObjectOwner
```

**Purpose**: This describes who owns a listed scheduled task and how broadly it is shared. The task follows the visibility of the conversation it reports into.

**Data flow**: It receives a ListedTask, reads the creator member id, the task id, and the task’s audience, converts the audience into a shared-visibility marker, and returns a GeneratedObjectOwner record.

**Call relations**: Listing and conversation-row code use this owner record so the generic object system can apply its normal visibility rules. It is the place where scheduled tasks connect conversation sharing to object ownership.

*Call graph*: called by 2 (_rows, member_conversation_rows); 2 external calls (__init__, subject_shared).


##### `ScheduledTaskObjects._admin_can_apply`  (lines 185–189)

```
def _admin_can_apply(self, _old: ScheduledTaskSpec, spec: ScheduledTaskSpec) -> bool
```

**Purpose**: This answers whether an admin is allowed to apply a particular update to someone else’s scheduled task. Admins may change timing controls, but not the task’s actual content or a forced immediate run.

**Data flow**: It receives the old task spec and the proposed new spec. It looks at which fields the update explicitly set. If the update touches prompt, description, or run_now, it returns false; otherwise it returns true.

**Call relations**: This supports the permission gate inherited from the object framework. It explains the file’s policy split: admins can maintain cadence, expiry, and pause state, while creators control what the task says and whether it runs immediately.


##### `ScheduledTaskObjects.member_page`  (lines 191–213)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This builds a page of scheduled-task rows for a member, with special support for filtering by conversation. It is what lets a user see tasks attached to one specific conversation.

**Data flow**: It receives the extension context, the requesting member, whether they are an admin, and a list query. If the query has a valid conversation id filter, it fetches rows for that conversation, keeps only rows visible to the requester, wraps them for display, and returns a page. If the filter is absent or invalid, it falls back to the standard member listing behavior or returns an empty page.

**Call relations**: When object listing asks for scheduled tasks, this method either delegates to the base object behavior or uses _rows for the conversation-specific case. It then packages the result with object_page for the caller.

*Call graph*: calls 1 internal fn (_rows); 3 external calls (__init__, object_page, UUID).


##### `ScheduledTaskObjects.member_conversation_rows`  (lines 215–235)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: This supplies the scheduled-task entries that appear inside a conversation’s own object area. It shows which tasks report into that conversation and whether their content can be shown to the current member.

**Data flow**: It receives a conversation id, requester identity, admin flag, and row limit. It asks the ScheduleStore for tasks reporting to that conversation, computes ownership and content visibility for each, filters out tasks the requester may not see, and returns compact conversation grants.

**Call relations**: Conversation display code calls this when it wants object slots for scheduled tasks. The method gets raw tasks through _require_scheduler, uses _owner for visibility ownership, and uses task_content_visible to decide whether the prompt-like content should be exposed.

*Call graph*: calls 2 internal fn (_owner, _require_scheduler); 2 external calls (__init__, task_content_visible).


##### `ScheduledTaskObjects._member_rows`  (lines 237–240)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This returns the scheduled-task rows visible in a member-level listing. It is a thin adapter into the shared row-building logic.

**Data flow**: It receives the extension context and an optional member id. It asks _rows to build all reported scheduled-task rows for that member, without cutting prompt text for this particular path, and returns those owned rows.

**Call relations**: The generic object framework calls this as part of member-readable object listing. Rather than duplicating listing work, it hands off to _rows.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._owned_rows`  (lines 242–249)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This returns the scheduled-task rows that should be placed into a tool turn’s context. It deliberately shortens prompts so a single object list cannot flood the model with unlimited text.

**Data flow**: It receives the current tool context. It extracts the acting member id from the authority, asks _rows to build rows for that member, and requests prompt excerpts capped at the configured length. The result is a tuple of owned rows ready for the object tooling layer.

**Call relations**: The object tooling flow calls this when preparing objects for an agent turn. It uses authority_member_id to personalize visibility and then relies on _rows for the actual storage reads and formatting.

*Call graph*: calls 1 internal fn (_rows); 1 external calls (authority_member_id).


##### `ScheduledTaskObjects._rows`  (lines 251–297)

```
async def _rows(self, ext: ExtensionContext | None, *, member_id: UUID | None, prompt_max: int | None, conversation_id: UUID | None=None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This is the main row builder for scheduled-task lists. It gathers task data, owner emails, last-run information, visibility decisions, and display fields into one consistent shape.

**Data flow**: It receives the extension context, an optional member id, an optional prompt length cap, and optionally a conversation id. It reads reported tasks from ScheduleStore, looks up creator emails, inspects recent run status, decides whether each reader may see content, and returns OwnedRow records containing names, summaries, ownership, schedule state, last-run state, origin, and prompt or a private placeholder.

**Call relations**: Several listing paths call this: member_page for filtered pages, _member_rows for general listings, and _owned_rows for tool context. It calls helper functions such as _require_scheduler, _summary, _owner, owner_emails, and task_content_visible so every listing surface tells the same story.

*Call graph*: calls 3 internal fn (_owner, _require_scheduler, _summary); called by 3 (_member_rows, _owned_rows, member_page); 3 external calls (__init__, owner_emails, task_content_visible).


##### `ScheduledTaskObjects._member_object`  (lines 299–328)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ScheduledTaskSpec] | None
```

**Purpose**: This returns the detailed view of one scheduled task for a member. It includes the editable spec, timestamps, a link to the conversation it reports to, and whether the spec itself should be visible.

**Data flow**: It receives the extension context, object name, expected owner, and optional member id. It finds the named task, confirms it matches the expected generation, builds a ScheduledTaskSpec from the stored task, adds created and updated times plus a reports_to link, and marks whether the member may see the spec. If the task is missing or stale, it returns nothing.

**Call relations**: The object get flow calls this after ownership has been established. It uses _find to locate the current task and task_content_visible to avoid revealing private task content through the detail view.

*Call graph*: calls 1 internal fn (_find); 5 external calls (__init__, __init__, __init__, __init__, task_content_visible).


##### `ScheduledTaskObjects._status`  (lines 330–362)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This returns live status for one scheduled task, such as whether it is paused, when it runs next, when it last ran, and the latest run result. It gives users operational feedback without requiring them to inspect the storage layer.

**Data flow**: It receives the tool context, task name, and expected owner. It finds the task, confirms it is the same generation, asks the scheduler to inspect it, and builds a status dictionary. If there was a last run, it includes the turn id and turn status; if the requester may see task content, it may also include a shortened response excerpt.

**Call relations**: The object status/get machinery calls this when it needs runtime information beyond the saved spec. It uses _find for identity, _require_scheduler for inspection, and task_content_visible plus authority_member_id to decide whether to reveal the latest response.

*Call graph*: calls 2 internal fn (_find, _require_scheduler); 2 external calls (authority_member_id, task_content_visible).


##### `ScheduledTaskObjects._apply_owned`  (lines 364–433)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This creates or updates a scheduled task after checking schedule syntax, permissions, required fields, next-run timing, and expiry rules. It is the main write path for the scheduled-task object kind.

**Data flow**: It receives the current tool context, object name, proposed spec, previous spec if any, and owner if the object already exists. It validates the cron schedule when supplied, identifies the acting member, loads any existing task, and gets the current time. For a new task, it requires schedule and prompt, binds the task to the current conversation and member, calculates the first run time, checks expiry, and stores it. For an update, it verifies the object did not change underneath the caller, checks creator/admin permission, keeps omitted fields from the existing task, optionally schedules an immediate run, validates expiry, and writes the update.

**Call relations**: The generic object apply flow calls this when a user or agent applies a scheduled_task manifest. It coordinates many smaller pieces: _find for current state, _require_scheduler for storage, validate_cron and next_fire for schedule calculation, speaker_is_admin and authority_member_id for permission decisions, _validate_future_fire for safety, and log when run_now is requested.

*Call graph*: calls 4 internal fn (speaker_is_admin, _find, _require_scheduler, _validate_future_fire); 6 external calls (__init__, now, authority_member_id, log, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 435–439)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This cancels an existing scheduled task, after checking that the caller is deleting the same task generation they saw. That prevents accidental deletion if the task changed while someone was editing.

**Data flow**: It receives the tool context, task name, and expected owner. It finds the current task, compares its id to the expected generation, raises an error if they do not match, and otherwise asks the scheduler to cancel the task.

**Call relations**: The object delete flow calls this after the broader permission gate has allowed deletion. It uses _find to avoid stale writes and _require_scheduler to perform the actual cancellation.

*Call graph*: calls 2 internal fn (_find, _require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 441–449)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTask | None
```

**Purpose**: This looks up a scheduled task by its object name. It is the local search helper used before reading, updating, checking status, or deleting one task.

**Data flow**: It receives the extension context and a task name. It asks ScheduleStore for reported tasks, scans them for a matching name, and returns the first matching ListedTask or nothing if none exists.

**Call relations**: _apply_owned, _delete_owned, _member_object, and _status all call this to turn a user-facing name into the current stored task. It keeps name lookup behavior consistent across those flows.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


##### `pause_and_wait`  (lines 507–544)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: This tool pauses the current workflow and arranges for it to resume either when a member sends a new message or when a durable timer expires. It is useful when the agent must wait for something outside the current turn.

**Data flow**: It receives the tool context and pause arguments: the message to show now, how many minutes to wait, resume instructions, a reason, and optional metadata. It computes the resume time, builds a resume prompt, records a pause row with the conversation, agent, current turn markers, arrival marker, creator, and timer. It then returns a ToolResult containing a directive that tells the agent to reply with the waiting message and end its turn, plus a JSON payload describing the pause.

**Call relations**: The tool framework calls this when an agent invokes the pause_and_wait tool. It uses _require_ext to ensure pause storage is available, PauseStore to arm the durable pause, conversation_arrival_seq to record the member-message watermark, and TextContent/ToolResult to send the instruction back to the agent.

*Call graph*: calls 1 internal fn (_require_ext); 7 external calls (__init__, __init__, __init__, now, timedelta, dumps, authority_member_id).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/conversation_slot.py`

`domain_logic` · `conversation slot reading`

This file is the bridge between the scheduled-tasks extension and the conversation view. A scheduled task may exist in storage, but it should only appear in a conversation if the conversation is allowed to see it. This file checks that link, gathers current status details, trims long text so the slot stays small, and hides private content when needed.

Think of it like preparing a noticeboard: the full records live in a back office, but this file chooses which notices belong on this conversation’s board, shortens them to fit, and covers sensitive parts if the viewer should not see them.

The main public object is `AUTOMATIONS_SLOT`, a `ConversationSlotProvider`. A conversation slot is a named piece of extra conversation context. Here, the slot is called `automations`, labeled “Automations,” and uses a calendar icon. When the system wants the full slot contents, it calls `_read`. When it only needs a small summary count, it calls `_summarize`.

The important safety step is authorization. `_read` compares visible conversation items with stored scheduled tasks, and only includes tasks whose name and generation match. It also asks the scheduler for inspection details such as next run time, last run time, last status, and last response. Long fields are cut to fixed maximum lengths, and a `truncated` flag tells the caller that some information was shortened or omitted.

#### Function details

##### `_scheduler`  (lines 16–19)

```
def _scheduler(ctx: ConversationSlotContext) -> ScheduleStore
```

**Purpose**: This helper creates access to the scheduled-task store for the current conversation slot request. It also protects against being used without the scheduled-tasks extension context, because without that context it cannot read task data.

**Data flow**: It receives a `ConversationSlotContext`, which should contain extension-specific runtime information in `ctx.ext`. If that information is missing, it raises an error. If it is present, it builds and returns a `ScheduleStore`, which is the object used to query scheduled tasks.

**Call relations**: `_conversation` and `_read` call this when they need to talk to the schedule store. It hands them a `ScheduleStore` so they can list tasks or inspect their current run information.

*Call graph*: called by 2 (_conversation, _read); 1 external calls (__init__).


##### `_conversation`  (lines 22–28)

```
async def _conversation(ctx: ConversationSlotContext) -> tuple[ScheduledTask, ...]
```

**Purpose**: This function fetches the scheduled tasks that might belong to the current conversation. It limits the search to names that are already visible in the conversation context, which is the first step toward showing only relevant automations.

**Data flow**: It reads the visible item names from `ctx.visible_items`, gets a `ScheduleStore` through `_scheduler`, and asks the store for tasks matching the current conversation ID and those names. It requests one more than the display limit so the caller can know whether there are too many results to show fully. It returns the matching scheduled task rows.

**Call relations**: `_read` calls `_conversation` near the start of building the automation slot. `_conversation` delegates storage access to `_scheduler`, then gives `_read` the raw candidate tasks to filter and format.

*Call graph*: calls 1 internal fn (_scheduler); called by 1 (_read).


##### `_read`  (lines 31–90)

```
async def _read(ctx: ConversationSlotContext) -> AutomationsSlotPayload
```

**Purpose**: This is the main function that builds the full “Automations” slot content for a conversation. It decides which scheduled tasks are authorized to show, adds live status information, hides or shortens sensitive content, and returns a clean payload for display.

**Data flow**: It receives a conversation slot context. From that, it gets the schedule store, loads candidate tasks, and compares each task with the visible conversation items. A task is included only when its name matches a visible item and its stored ID matches that item’s generation, which prevents stale or unauthorized data from leaking in. For included tasks, it asks the scheduler for inspection details such as next run, last run, latest status, and latest response. It trims long descriptions, schedules, statuses, and responses to fixed sizes. If content is not visible, it hides the description and latest response. It returns an `AutomationsSlotPayload` containing a tuple of `ConversationAutomation` entries plus a `truncated` flag if anything was omitted or shortened.

**Call relations**: The `AUTOMATIONS_SLOT` provider uses `_read` when the system asks for the full automation slot. `_read` calls `_conversation` to get candidate tasks, calls `_scheduler` to inspect them, and creates `ConversationAutomation` objects that are finally wrapped in an `AutomationsSlotPayload`.

*Call graph*: calls 2 internal fn (_conversation, _scheduler); 2 external calls (__init__, __init__).


##### `_summarize`  (lines 93–95)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a small count summary for the automations slot without loading full task details. It is useful when the system only needs to know whether there are visible automations, and roughly how many can be shown.

**Data flow**: It receives the conversation slot context and counts the visible items, capped at the maximum number of automations the slot can display. If the count is zero, it returns `None`; otherwise it returns the count.

**Call relations**: The `AUTOMATIONS_SLOT` provider uses `_summarize` when it needs a lightweight summary instead of the full `_read` payload. Unlike `_read`, it does not call the schedule store or inspect task status.


### Monitor lifecycle
These files create monitors, run due monitor checks, and maintain the durable monitor records that make claiming, updating, and stopping safe.

### `extensions/monitors/ufo_ext_monitors/monitor_tool.py`

`domain_logic` · `tool call during a conversation turn`

This file is the front door for creating a monitor. A monitor is like asking the system, “Keep checking this thing for me, and wake me up once something changes or time runs out.” The thing being checked is a shell command run inside the conversation’s sandbox, meaning the isolated workspace where tools can safely run commands.

The file first describes the input the tool accepts: a short monitor name, the command to run, how often to check, the deadline, what message to show now, and what instructions to give the agent when the monitor fires later. It also defines limits, such as valid names and maximum deadlines, through the imported monitor settings.

The important behavior is that the command runs immediately, during the current live tool call. If the command fails now, the monitor is not saved. This protects the system from creating a background job that would only fail later with no useful setup. If the command succeeds, its output becomes the “baseline,” like taking a first photo before watching for changes. Future probe output is compared against that baseline.

The main `monitor` function also prevents too many monitors in one conversation and prevents duplicate names. When everything is valid, it stores the monitor with its timing, command, reason, next steps, metadata, and baseline. It returns both the user-facing response instruction and a JSON summary so the agent knows exactly what was armed.

#### Function details

##### `_require_ext`  (lines 77–80)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This function makes sure the monitor tool has the extension context it needs to reach the monitor storage. Without that context, the tool cannot save or read monitor records, so it stops with a clear error.

**Data flow**: It receives an optional extension context. If the context is present, it returns it unchanged. If it is missing, it raises an error before any monitor work continues.

**Call relations**: The `monitor` function calls this at the start of building a `MonitorStore`. It acts as a gatekeeper: only after this check passes can the main tool talk to the monitor database or storage layer.

*Call graph*: called by 1 (monitor).


##### `_refusal`  (lines 83–84)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: This function creates a standard error result for cases where the monitor cannot be armed. It is used for polite, structured refusals such as too many monitors, a duplicate name, or a failed probe command.

**Data flow**: It receives a plain text explanation. It wraps that text in a `TextContent` object, then puts it inside a `ToolResult` marked as an error, and returns that result to the caller.

**Call relations**: The `monitor` function calls this whenever it must stop before saving a monitor. Instead of each failure path building its own result shape, they all hand their message to `_refusal` and get back the same kind of tool error response.

*Call graph*: called by 1 (monitor); 2 external calls (__init__, __init__).


##### `monitor`  (lines 87–134)

```
async def monitor(ctx: ToolContext, args: MonitorInput) -> ToolResult
```

**Purpose**: This is the actual tool handler that arms a new monitor. It checks limits, runs the probe command once, saves the monitor only if that first run succeeds, and returns instructions telling the agent to reply and end the turn.

**Data flow**: It receives the tool context, which includes the conversation, sandbox, audience, authority, and extension context, plus validated monitor input from `MonitorInput`. It reads existing monitors for the conversation, rejects the request if the cap or name rules would be broken, then runs the requested shell command in the sandbox. If the command fails, it returns an error and saves nothing. If it succeeds, it records the command output as the baseline, calculates the next probe time and deadline, stores a monitor row, and returns a text result containing the monitor directive plus a JSON summary of what was armed.

**Call relations**: This function is registered as the handler for `MONITOR_TOOL`, so the tool system calls it when an agent uses the `monitor` action. It calls `_require_ext` before creating `MonitorStore`, calls `_refusal` for all early exits, asks the sandbox to run the first probe, asks the store to persist the new monitor, and finally hands a `ToolResult` back to the tool framework.

*Call graph*: calls 2 internal fn (_refusal, _require_ext); 10 external calls (__init__, __init__, __init__, now, timedelta, dumps, authority_member_id, capped, qualified_name, stderr_tail).


### `extensions/monitors/ufo_ext_monitors/monitor_runner.py`

`orchestration` · `recurring background monitor tick`

A monitor is like a watchman that periodically runs a command and compares the result with what it saw before. This file is the watchman’s clock-driven worker. On each run, it asks the monitor store for monitors that are due and temporarily claims them so another overlapping run does not check the same monitor at the same time.

For each claimed monitor, it first checks whether the monitor’s deadline has passed. If so, it fires the monitor immediately. Otherwise it runs the saved probe command using the same member authority that created the monitor, meaning it can only reach resources that member is still allowed to use. If that authority is unavailable, or the terminal is gone, the tick is counted as skipped rather than treated as a real failure.

A successful probe is compared with the saved baseline. Matching output means nothing has changed, so the monitor simply records a quiet tick and schedules the next probe. Different output fires the monitor. A failing command is tolerated for a while, but the third failure fires it. When a monitor fires, the file posts a structured message back to the agent and then retires the monitor so it cannot fire again. If the worker crashes after posting but before retiring, the post uses a repeat-safe key so the same fire is not duplicated.

#### Function details

##### `MonitorRunner.run`  (lines 54–64)

```
async def run(self) -> None
```

**Purpose**: This is the top-level tick for the monitor runner. It finds all monitors that are due right now, checks each one, and reports if any checks failed unexpectedly.

**Data flow**: It starts with the extension context stored on the runner. From that it builds a monitor store, reads the current time, and asks the store to claim due monitors for a short lease period. Each claimed monitor is passed into the per-monitor tick routine. If one monitor crashes with an unexpected error, the runner remembers that monitor’s name and keeps going; at the end it raises one combined error if anything failed.

**Call relations**: The recurring job calls this method when it is time to sweep monitors. It creates the storage helper, gets due rows, and hands each row to MonitorRunner._tick. It does not decide monitor outcomes itself; it coordinates the batch and gathers failures from the individual checks.

*Call graph*: calls 1 internal fn (_tick); 2 external calls (__init__, now).


##### `MonitorRunner._tick`  (lines 66–108)

```
async def _tick(self, store: MonitorStore, row: Monitor) -> None
```

**Purpose**: This checks one claimed monitor once. It decides whether the monitor should stay quiet, record a failure or skip, or fire because its output changed, its command failed too many times, or its deadline passed.

**Data flow**: It receives the monitor store and one monitor row. It reads the monitor’s interval, deadline, saved command, baseline output, failure count, and creator. If the deadline has passed, it fires immediately. Otherwise it runs the probe command under the creator’s authority. An unavailable authority or missing terminal becomes a skipped tick with the next probe scheduled later. A nonzero command exit code increases the failure streak, unless this is the threshold failure, in which case it fires with a short error message. A zero exit code is treated as useful output: matching the baseline records a quiet tick, while changed output fires the monitor, storing the full output separately if it is too large for the message.

**Call relations**: MonitorRunner.run calls this once for every due monitor it claimed. This method calls store methods to record quiet, failed, or skipped ticks, and calls MonitorRunner._fire when the monitor should notify the agent and retire. It also uses helper routines for member authority, output capping, and error-output trimming so the stored probe result becomes a safe monitor decision.

*Call graph*: calls 4 internal fn (_fire, failed_tick, quiet_tick, skipped_tick); called by 1 (run); 5 external calls (now, timedelta, authority_from_member_id, capped, stderr_tail).


##### `MonitorRunner._fire`  (lines 110–132)

```
async def _fire(self, store: MonitorStore, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> None
```

**Purpose**: This sends the actual monitor-fired message to the agent and then retires the monitor. It is careful to avoid double-firing if another worker has already taken over or if a crash causes the same fire to be retried.

**Data flow**: It receives the store, the monitor row, the reason for firing, a short payload, optional full output, and the probe count. First it asks the store whether this runner still holds the right to fire this monitor. If not, it stops. If yes, it builds the message body, invokes the agent in the monitor’s conversation using the creator’s authority, and supplies a stable idempotency key, which is a repeat-safe label that prevents the same fire from being posted twice. If the agent is archived, it gives up quietly. After a successful invoke, it retires the monitor in storage.

**Call relations**: MonitorRunner._tick calls this whenever a deadline, repeated failure, or output change should wake the agent. This method asks MonitorRunner._body to prepare the message text, then hands that message to the extension context’s invoke capability, and finally calls the store to retire the monitor.

*Call graph*: calls 3 internal fn (_body, claim_holds, retire); called by 1 (_tick); 1 external calls (authority_from_member_id).


##### `MonitorRunner._body`  (lines 134–153)

```
async def _body(self, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> str
```

**Purpose**: This builds the message the agent will read when a monitor fires. It includes the monitor’s reason, suggested next steps, metadata, counts, and any probe output in a form that is safe to include in the conversation.

**Data flow**: It receives the monitor row, the firing cause, a short payload, optional full output, and the number of probes run. It turns the monitor details into a structured text block, converts metadata to JSON text, and includes counters such as quiet and skipped ticks. If there is full output that was too large to inline, it asks MonitorRunner._spilled to write it to a file and puts the file path in the message. It also escapes the closing marker so command output cannot pretend to end the block and add instructions. If there is a payload, it wraps that untrusted command output with a safety helper before appending it.

**Call relations**: MonitorRunner._fire calls this just before invoking the agent. This method may call MonitorRunner._spilled for large output, uses JSON formatting for metadata, and uses the untrusted-output wall helper so probe text is clearly separated from instructions.

*Call graph*: calls 1 internal fn (_spilled); called by 1 (_fire); 2 external calls (dumps, wall).


##### `MonitorRunner._spilled`  (lines 155–163)

```
async def _spilled(self, row: Monitor, output: str) -> str
```

**Purpose**: This saves large probe output into the conversation’s runtime files and returns the path. It keeps the fired message readable while still letting the agent inspect the full output if needed.

**Data flow**: It receives the monitor row and the full output text. It checks that the extension has file-writing support; without it, it raises an error because large output has nowhere safe to go. It creates a timestamped filename using the monitor name, writes the output bytes under the monitor spill directory for that conversation, and returns the path produced by the file system helper.

**Call relations**: MonitorRunner._body calls this only when a monitor fire has output too large to fit directly in the message. The returned path is placed into the fired message so the agent can read the complete probe output later.

*Call graph*: called by 1 (_body); 1 external calls (now).


### `extensions/monitors/ufo_ext_monitors/monitors.py`

`domain_logic` · `background monitor scheduling and probe result recording`

A monitor is like an alarm clock with a clipboard. It remembers what command to run, when to run it next, what output counted as “normal,” who should be notified, and how many quiet, failed, or skipped checks have happened so far. This file defines the database table for that clipboard and the `MonitorStore` class that reads and writes it.

The important safety idea here is ownership by workspace and by temporary claim. Every database query filters by `workspace_id`, because the transaction connection is not automatically limited to one workspace. When the background runner is ready to check monitors, it does not just read due rows. It “claims” them for a short lease, like putting a sticky note on a task saying “I am working on this.” That stops two runners from probing or firing the same monitor at the same time.

After a probe runs, the store records one of three outcomes: quiet, failed, or skipped. Quiet and failed probes update streak counters; skipped probes count times the command could not run. If a monitor fires, it is retired only if the same claim still owns it. If a user stops watching, `disarm` deletes the row directly. This prevents most races between “the monitor fired” and “the user stopped it.”

#### Function details

##### `qualified_name`  (lines 73–84)

```
def qualified_name(conversation_id: UUID, slug: str) -> str
```

**Purpose**: Builds the stored name for a monitor by combining a short prefix from the conversation ID with the human-chosen slug. This makes monitor names unique across a workspace while still letting different conversations reuse simple names like `ci-run`.

**Data flow**: It receives a conversation ID and a short slug chosen by the agent. It takes the first part of the conversation ID, joins it to the slug with a dash, and returns that combined name. Nothing else is changed.

**Call relations**: Other monitor code can use this before saving or looking up a monitor name. It exists because the database enforces uniqueness by workspace and stored name, so the name must already include enough context to avoid collisions.


##### `capped`  (lines 87–97)

```
def capped(output: str) -> str
```

**Purpose**: Shrinks very large probe output to a safe size before it is stored or compared. This keeps one noisy command from creating huge monitor records or making every run look different just because the middle kept growing.

**Data flow**: It receives the command output as text. If the output is small enough, it returns it unchanged. If it is too large, it keeps the beginning and the end, inserts a message saying how many bytes were omitted, and returns that shortened text.

**Call relations**: This is a helper for the monitor probing flow. The stored baseline and later comparisons are based on this bounded version, so downstream firing logic can work with predictable-size text.


##### `stderr_tail`  (lines 100–105)

```
def stderr_tail(stderr: str) -> str
```

**Purpose**: Keeps only the final part of a failed command’s error output. The end of shell error text is usually where the clearest explanation appears.

**Data flow**: It receives standard error text from a command. If it fits under the limit, it returns the whole text. If it is too long, it returns only the last bytes, decoded back into text.

**Call relations**: This supports failure reporting elsewhere in the monitor runner. It prepares error details so a fire message can include useful information without carrying an unbounded log.


##### `_aware`  (lines 137–138)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a timestamp has a time zone, using UTC when the database returned a timestamp without one. This prevents later code from mixing time-zone-aware and time-zone-unaware dates.

**Data flow**: It receives a `datetime`. If that value already has time zone information, it returns it as-is. If it does not, it creates a new version marked as UTC and returns that.

**Call relations**: _row calls this while turning database rows into `Monitor` objects. It is a small normalization step that keeps the rest of the monitor code from repeating the same date cleanup.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 141–168)

```
def _row(row: sa.RowMapping) -> Monitor
```

**Purpose**: Turns one database result row into a `Monitor` object that the rest of the code can use safely. It also normalizes all stored timestamps so they consistently mean UTC time.

**Data flow**: It receives a row mapping from the database. It reads each monitor column, fixes timestamp values through `_aware`, preserves optional fields like metadata and last probe time, and returns a populated `Monitor` dataclass. It does not write back to the database.

**Call relations**: `MonitorStore.armed`, `MonitorStore.arm`, and `MonitorStore.claim_due` all use this after reading rows. That means every monitor object enters the application through one shared conversion path.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 171–172)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a monitor can be claimed. A monitor is available if nobody owns it right now, or if its previous temporary lease has expired.

**Data flow**: It receives the current time. It returns a SQL condition, not a Python yes-or-no answer, that the database can apply when selecting or updating monitor rows.

**Call relations**: Both workspace discovery and actual claiming use this same condition. That keeps the “is this monitor free?” rule identical when deciding which workspaces need attention and when taking specific monitors.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_due`  (lines 175–176)

```
def _due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a monitor needs attention now. A monitor is due if its next scheduled probe time has arrived or its deadline has arrived.

**Data flow**: It receives the current time. It returns a SQL condition that matches rows whose next probe or deadline is at or before that time.

**Call relations**: `due_monitor_workspaces.due` uses it to find workspaces with pending monitor work, and `MonitorStore.claim_due` uses it to choose the actual monitor rows to lease.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_monitor_workspaces`  (lines 179–196)

```
def due_monitor_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates the workspace-candidate source used by the monitor runner. In plain terms, it tells the job system, “these are the workspaces that appear to have monitor work ready.”

**Data flow**: It defines an inner database query that looks for workspaces containing due, claimable monitors whose agents are still live. It passes that query builder to `owner_candidates`, which wraps it in the job system’s expected candidate format.

**Call relations**: The background job scheduler calls on this kind of candidate source before opening work for a workspace. It delegates the actual candidate wrapping to `owner_candidates`, while the inner `due` function supplies the monitor-specific query.

*Call graph*: 1 external calls (owner_candidates).


##### `due_monitor_workspaces.due`  (lines 184–194)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query that finds workspaces with monitors ready to run. It filters out monitors that are already leased and monitors whose agents are not live.

**Data flow**: It reads the current UTC time, builds claim-availability and due-time conditions, and creates a SQL query selecting distinct workspace IDs. The result is a query object that can be executed by the job-candidate machinery.

**Call relations**: This function lives inside `due_monitor_workspaces` so the job system can ask for fresh candidates when needed. It uses `_claim_available`, `_due`, and `agent_is_live` so the scheduler only wakes workspaces where a runner can probably do useful work.

*Call graph*: calls 2 internal fn (_claim_available, _due); 3 external calls (now, select, agent_is_live).


##### `MonitorStore.armed`  (lines 205–211)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Monitor, ...]
```

**Purpose**: Lists the currently armed monitors in this store’s workspace. It can list all of them, or only those belonging to one conversation.

**Data flow**: It receives an optional conversation ID. It builds a database query limited to the current workspace, adds the conversation filter if provided, reads matching rows ordered by name, converts each row with `_row`, and returns the monitors as a tuple.

**Call relations**: Code that needs to show or inspect active watches calls this method. It relies on `_row` so callers receive clean `Monitor` objects instead of raw database rows.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `MonitorStore.arm`  (lines 213–268)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, name: str, audience: str, command: str, interval_minutes: int, deadline_at: datetime, reason: str, next_steps: str, metadata: dict[str, Js
```

**Purpose**: Creates a new armed monitor record. This is how the extension starts watching a command with a baseline, schedule, deadline, and notification details.

**Data flow**: It receives all monitor details, including conversation, agent, command, timing, reason, metadata, baseline output, and first probe time. It inserts a new row for the current workspace with fresh counters and no claim, reads back the inserted row, converts it with `_row`, and returns the new `Monitor`.

**Call relations**: Monitor setup code uses this when a user or agent asks to start watching something. It generates the monitor’s database ID with `uuid4`, writes through SQLAlchemy’s insert builder, and hands back the normalized object through `_row`.

*Call graph*: calls 1 internal fn (_row); 2 external calls (insert, uuid4).


##### `MonitorStore.claim_due`  (lines 270–313)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_MONITORS) -> tuple[Monitor, ...]
```

**Purpose**: Leases a batch of due monitors for this runner to work on. The lease is a temporary ownership marker that prevents overlapping runners from probing the same monitor.

**Data flow**: It receives the current time, a lease length in seconds, and an optional batch limit. It builds a query for due, available, live-agent monitors in the current workspace, marks up to the limit with a new claim ID and expiration time, reads back the updated rows, converts them with `_row`, and returns them.

**Call relations**: The monitor runner calls this before running probes. It shares `_claim_available` and `_due` with workspace discovery, checks `agent_is_live`, and uses one update-and-return operation so claiming and reading happen as one database step.

*Call graph*: calls 3 internal fn (_claim_available, _due, _row); 5 external calls (timedelta, select, update, agent_is_live, uuid4).


##### `MonitorStore.quiet_tick`  (lines 315–325)

```
async def quiet_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records a successful probe whose output still matches the baseline. This means there is nothing to report yet, but the monitor’s counters and next run time must move forward.

**Data flow**: It receives the claimed monitor row, the time the probe ran, and the next scheduled probe time. It increases the total probe count and quiet streak, resets the failure streak, keeps the skipped count, and passes those values to `_tick` for database writing.

**Call relations**: The monitor runner’s `_tick` method calls this after a normal probe result. This method does not write directly; it hands the final counter values to `MonitorStore._tick`, which performs the guarded update.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.failed_tick`  (lines 327–339)

```
async def failed_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records a probe that ran but exited with an error, when that error has not yet caused a fire. It tracks consecutive failures separately from quiet successes.

**Data flow**: It receives the claimed monitor row, the probe time, and the next scheduled probe time. It increases the total probe count and failure streak, resets the quiet streak, preserves skipped count, and sends the updated values to `_tick`.

**Call relations**: The monitor runner’s `_tick` method calls this when a command fails but the monitor should continue watching. It uses `MonitorStore._tick` for the actual database update so the same claim-safety rule applies as for quiet ticks.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.skipped_tick`  (lines 341–352)

```
async def skipped_tick(self, row: Monitor, next_probe_at: datetime) -> None
```

**Purpose**: Records that a probe could not be run at all, for example because the client sandbox was unreachable. A skipped probe is counted, but it is not treated as a command failure.

**Data flow**: It receives the claimed monitor row and the next scheduled probe time. It leaves probe count and streaks unchanged, increases the skipped count, preserves the last successful probe time, and passes everything to `_tick`.

**Call relations**: The monitor runner’s `_tick` method calls this when it cannot execute the command. Like the other tick helpers, it funnels the write through `MonitorStore._tick` so the lease is cleared and the row is rescheduled consistently.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore._tick`  (lines 354–386)

```
async def _tick(self, row: Monitor, *, probes_run: int, quiet_streak: int, failure_streak: int, skipped: int, last_probe_at: datetime | None, next_probe_at: datetime) -> None
```

**Purpose**: Writes the result of one non-firing monitor check back to the database. It also releases the runner’s claim so the monitor can be picked up again later.

**Data flow**: It receives a claimed monitor and the exact counter and timing values that should replace the old ones. If the monitor was not claimed, it raises an error. Otherwise, it updates only the row with the same ID, workspace, and claim ID, sets the new counters and schedule, clears the claim, and updates the modification time.

**Call relations**: `quiet_tick`, `failed_tick`, and `skipped_tick` all prepare outcome-specific values and then call this shared method. Centralizing the write here ensures every non-firing result follows the same guarded “only the current claimant may update” rule.

*Call graph*: called by 3 (failed_tick, quiet_tick, skipped_tick); 1 external calls (update).


##### `MonitorStore.claim_holds`  (lines 388–415)

```
async def claim_holds(self, row: Monitor) -> bool
```

**Purpose**: Checks whether this runner still owns the monitor immediately before firing it. This helps avoid sending a fire after someone has already stopped the monitor.

**Data flow**: It receives a monitor row that should have a claim ID. If there is no claim, it raises an error. Otherwise, it looks for a row with the same monitor ID, workspace, and claim ID, locks it for the moment of the check, and returns true if it still exists.

**Call relations**: The monitor runner’s `_fire` method calls this just before delivering a fire. If the row has been deleted by `disarm` or claimed by someone else after expiry, this check tells the runner not to continue as if it still owned the monitor.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `MonitorStore.retire`  (lines 417–429)

```
async def retire(self, row: Monitor) -> None
```

**Purpose**: Deletes a monitor after its fire has been delivered. It only removes the row if the same claim still owns it, so an expired or stolen lease cannot delete someone else’s current work.

**Data flow**: It receives a claimed monitor. If the monitor has no claim ID, it raises an error. Otherwise, it deletes the row matching the monitor ID, workspace, and claim ID. It does not return a value.

**Call relations**: The monitor runner’s `_fire` method calls this after a fire is sent. It is the normal end of an armed monitor, and its claim guard matches the rest of the leasing design.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `MonitorStore.disarm`  (lines 431–439)

```
async def disarm(self, row: Monitor) -> bool
```

**Purpose**: Stops watching a monitor because a member asked to stop it. It reports whether a row was actually removed.

**Data flow**: It receives a monitor object. It deletes the matching row by monitor ID and workspace, without requiring a claim, and returns true if exactly one row was deleted. If the row was already gone, it returns false.

**Call relations**: User-facing stop-watch behavior calls this method. It deliberately bypasses the claim requirement so a member can disarm a monitor even while a runner may be working, and later claim checks help the runner notice that removal.

*Call graph*: 1 external calls (delete).


### Pause wake-ups
These files persist pause-until-human-or-timeout workflows and safely resume expired pauses exactly once.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pause_runner.py`

`orchestration` · `recurring scheduled job`

A pause is a stored promise to continue a conversation later, unless a member speaks first. This file is the clock-driven worker that checks for those promises when they become due. Think of it like an alarm service: when an alarm rings, it tries to deliver the saved message that continues the workflow, but only if nobody has already answered the need for that alarm.

The main class, PauseRunner, is run repeatedly by some outside scheduler. On each run it asks the PauseStore for due pause rows and claims them for a short lease. A lease is a temporary hold that stops two overlapping runner ticks from working on the same pause at the same time.

For each claimed pause, it performs one more hold check, then calls the system context to invoke the saved prompt as a scheduled turn. It uses the member who originally created the pause as the authority, so the resumed action is done on that member’s behalf. It also passes two recorded “watermarks,” which are conversation positions from when the pause began. These tell the system: only resume if no member has spoken since then.

If the scheduled turn is accepted, the pause is retired. If the system says a member already spoke, the invocation produces no turn and the pause is still retired, because the wait is over either way. If the agent is archived, nothing can be invoked, so the pause is left in place for a future restore. Failures are collected and reported after the tick finishes trying all due pauses.

#### Function details

##### `PauseRunner.run`  (lines 34–43)

```
async def run(self) -> None
```

**Purpose**: This is the public tick of the pause runner. It finds pauses that are due now, tries to fire each one, and reports if any of them failed.

**Data flow**: It starts with the runner’s extension context and lease length. It creates a PauseStore, asks it for due pauses using the current UTC time, then feeds each returned pause row into PauseRunner._fire. If a pause fails with an exception, it records the conversation id and error type. At the end, it either returns normally when all attempts succeeded, or raises one combined error naming the failed pauses.

**Call relations**: An outside scheduler calls this method on a recurring clock. It prepares the store and due rows, then hands the real per-pause work to PauseRunner._fire. This lets one scheduler tick attempt many pauses while still reporting all failures together instead of stopping at the first one.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `PauseRunner._fire`  (lines 45–61)

```
async def _fire(self, store: PauseStore, row: Pause) -> None
```

**Purpose**: This tries to complete one due pause. It resumes the saved conversation turn if no member has spoken since the pause began, then retires the pause so it will not be processed again.

**Data flow**: It receives a PauseStore and one stored Pause row. First it asks the store to claim the pause’s holds; if that fails, it stops because another worker or condition has taken over. If the hold succeeds, it invokes the saved prompt through the extension context, using an idempotency key based on the pause id so retrying after a crash does not create a duplicate turn. It also supplies the original member authority and the recorded conversation watermarks. If the agent is archived, it leaves the pause untouched and returns. Otherwise, after the invocation finishes, it retires the pause in the store.

**Call relations**: PauseRunner.run calls this once for each due pause it claimed. This method coordinates with PauseStore.claim_holds before invoking the conversation system, uses authority_from_member_id to act on behalf of the member who armed the pause, and then calls PauseStore.retire after the wait has been settled.

*Call graph*: calls 2 internal fn (claim_holds, retire); called by 1 (run); 1 external calls (authority_from_member_id).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pauses.py`

`domain_logic` · `when pauses are armed and during scheduled runner ticks`

A pause is like a reminder note for a conversation: “at this time, restart this agent with this prompt, unless the situation has changed.” This file defines the database table for those notes and the small store object, `PauseStore`, that reads and writes them for one workspace.

Only one pause can exist per conversation. If the same workflow schedules another wait, the old one is overwritten. That matters because a conversation can only be waiting for one next event at a time. The file records two “watermarks,” `origin_seq` and `origin_arrival_seq`, which are counters used later to tell whether a member spoke after the pause was armed. It does not answer that question itself; it simply saves the exact counters needed so the pause runner can ask safely under the conversation lock.

When a pause becomes due, workers do not simply grab it and fire. They first “claim” it with a short lease, like putting a sticky note on a task saying “I’m working on this until 12:05.” This prevents overlapping workers from waking the same conversation. Before firing and before deleting, the code checks that the claim still belongs to the same pause, so a re-armed or expired pause is not accidentally removed.

#### Function details

##### `_aware`  (lines 76–77)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: This helper makes sure a time value has a timezone. It treats timezone-less times as UTC, so the rest of the pause system can compare times safely.

**Data flow**: It receives a `datetime`. If that value already says what timezone it belongs to, it returns it unchanged. If it has no timezone, it adds UTC and returns the adjusted value.

**Call relations**: The row-building helper `_row` calls this whenever it turns database values into a `Pause`. This keeps database quirks, especially SQLite returning timezone-less times, from leaking into the rest of the code.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 80–95)

```
def _row(row: sa.RowMapping) -> Pause
```

**Purpose**: This helper turns one database row into a `Pause` object that the rest of the code can use comfortably. It is the single place where raw database values become the in-memory pause shape.

**Data flow**: It receives a row from the pause table. It copies the row fields into a `Pause`, normalizes all stored times through `_aware`, and returns the finished `Pause` object.

**Call relations**: `PauseStore.arm`, `PauseStore.armed`, and `PauseStore.claim_due` all call this after reading from the database. By funneling reads through one builder, all callers get the same clean representation.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 98–99)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This helper builds the database condition for “this pause can be claimed now.” A pause is available if nobody has claimed it, or if the old claim has expired.

**Data flow**: It receives the current time. It creates a SQL condition that checks whether `claimed_by` is empty or `claim_expires_at` is earlier than that time, and returns that condition for a larger query to use.

**Call relations**: `PauseStore.claim_due` uses this condition when leasing actual due pauses. `due_pause_workspaces.due` uses the same condition when deciding which workspaces are worth waking up, so both places agree on what “available” means.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_pause_workspaces`  (lines 102–118)

```
def due_pause_workspaces() -> WorkspaceCandidates
```

**Purpose**: This function provides the job system with a way to find workspaces that have due, claimable pauses. It helps the scheduler avoid scanning or opening workspaces that have nothing ready to do.

**Data flow**: It defines a small query-maker function that can select workspace IDs from due pause rows. It passes that query-maker to `owner_candidates`, which wraps it in the job system’s workspace-candidate format.

**Call relations**: This is the handoff point between the pause table and the broader background job system. It delegates the actual candidate packaging to `owner_candidates`, while the nested `due` function supplies the pause-specific database query.

*Call graph*: 1 external calls (owner_candidates).


##### `due_pause_workspaces.due`  (lines 107–116)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This nested function builds the database query that finds workspaces with at least one pause whose timer has arrived and whose claim is free. It is the concrete query behind `due_pause_workspaces`.

**Data flow**: It reads the current UTC time, builds the shared “claim is available” condition, and creates a SQL query selecting distinct workspace IDs where `resume_at` is in the past or present. The output is a selectable database query, not the final rows themselves.

**Call relations**: `due_pause_workspaces` gives this query builder to `owner_candidates`. Inside the query, it calls `_claim_available` so workspace discovery uses the same lease rules as the later claim step.

*Call graph*: calls 1 internal fn (_claim_available); 2 external calls (now, select).


##### `PauseStore.arm`  (lines 127–183)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, resume_at: datetime, origin_seq: int, origin_arrival_seq: int, prompt: str, created_by_member_id: UUID | None) -> Pause
```

**Purpose**: This method schedules, or re-schedules, a pause for one conversation. If that conversation already had a pause, this replaces it because the workflow can only wait for one thing at a time.

**Data flow**: It receives the conversation, agent, due time, saved sequence counters, prompt text, and optional member who created it. It creates a fresh pause ID, clears any old claim, and writes the row into the current workspace’s pause table using an insert-or-update operation. It reads back the saved row and returns it as a `Pause`.

**Call relations**: This is used when a workflow arms a future resume. After the database returns the inserted or updated row, the method hands it to `_row` so callers receive a normalized `Pause` object. The fresh ID is important because a re-armed wait must not look like the older wait it replaced.

*Call graph*: calls 1 internal fn (_row); 1 external calls (uuid4).


##### `PauseStore.armed`  (lines 185–191)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Pause, ...]
```

**Purpose**: This method lists the currently armed pauses for the workspace, optionally narrowed to one conversation. It is useful for inspecting what waits are still scheduled.

**Data flow**: It receives an optional conversation ID. It builds a query for the current workspace, adds the conversation filter if provided, orders pauses by due time, reads the matching rows, turns each one into a `Pause`, and returns them as a tuple.

**Call relations**: This is a read-only path through the pause table. It uses SQLAlchemy’s select builder to fetch rows and `_row` to convert each database row into the same in-memory form used by the rest of the file.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `PauseStore.claim_due`  (lines 193–234)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_PAUSES) -> tuple[Pause, ...]
```

**Purpose**: This method leases a batch of due pauses so one worker can try to fire them. The lease is what prevents two workers from waking the same conversation at the same time.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of pauses to take. It creates a random claim ID, finds the oldest due pauses in this workspace whose claims are free, updates those rows with the claim and expiry time, and returns the updated rows as `Pause` objects.

**Call relations**: This is the main pickup step for the pause runner. It uses `_claim_available` to avoid live leases, database update logic to stamp the claim atomically, `timedelta` to compute the expiry, and `_row` to hand back clean `Pause` values for later firing.

*Call graph*: calls 2 internal fn (_claim_available, _row); 4 external calls (timedelta, select, update, uuid4).


##### `PauseStore.claim_holds`  (lines 236–261)

```
async def claim_holds(self, row: Pause) -> bool
```

**Purpose**: This method checks whether a leased pause still belongs to the same claim just before firing it. It avoids waking a workflow from an old pause after the workflow has already re-armed or replaced that pause.

**Data flow**: It receives a `Pause` that should already have a claim ID. If there is no claim, it raises an error because unclaimed pauses should not be fired. Otherwise it looks for a row with the same pause ID, workspace, and claim ID, locks that row for the check, and returns `true` if it still exists.

**Call relations**: `PauseRunner._fire` calls this immediately before sending the resume into the workflow. The method does not call other local helpers; it asks the database directly whether the claim is still valid.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `PauseStore.retire`  (lines 263–276)

```
async def retire(self, row: Pause) -> None
```

**Purpose**: This method deletes a pause after the claimed wait is finished, whether it fired or was decided to be obsolete. It only deletes the row if the same claim still owns it.

**Data flow**: It receives a claimed `Pause`. If the pause has no claim ID, it raises an error because deleting unclaimed work would be unsafe. Otherwise it runs a delete filtered by pause ID, workspace ID, and claim ID, so expired claims or replaced pauses are left untouched.

**Call relations**: `PauseRunner._fire` calls this after it has dealt with a claimed pause. The guarded delete is the cleanup partner to `claim_due`: a worker may remove only the exact pause it leased.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


### Scheduled task firing
These files define due-fire identity, cron timing, scheduled-task storage, and the runner that claims and advances recurring work.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `recurring scheduled-task tick`

This file is the part of the scheduled-tasks extension that wakes up at intervals and says, “What needs to run now?” It does not run because a schedule row changed; it runs because an outside clock calls it regularly.

Its main job is to avoid double-firing. Think of each due task like a library book: the runner first “checks it out” with a short lease, so another overlapping runner tick should not process the same task at the same time. Before firing, it checks whether the task has expired. If it has, the task is retired instead of run.

For each task that should run, the runner builds a special inbound message for the agent. That message includes the scheduled fire time and the task prompt. It also adds instructions: normally, the agent should publish a report only if there is something worth reporting; if this is the last allowed fire before expiry, the agent is told to finish and ask the user whether to continue, change, or stop.

The runner invokes the agent using an idempotency key, meaning a repeated delivery of the same scheduled occurrence should settle on the same turn instead of creating a duplicate. If the app is archived, the runner treats that as neither success nor failure; the task keeps its current occurrence and can run later when restored. If invocation succeeds and a turn is admitted, the task is advanced to its next cron occurrence.

#### Function details

##### `fire_body`  (lines 43–61)

```
def fire_body(task: ScheduledTask, runtime_instruction: str | None) -> tuple[str, str]
```

**Purpose**: Builds the message that will be delivered to the agent for one scheduled task fire, along with the stable key used to prevent duplicate fires. Someone uses this when a claimed scheduled task is ready to be invoked.

**Data flow**: It receives a ScheduledTask and an optional runtime instruction. It reads the task’s next planned run time and prompt, formats them into a small XML-like scheduled-task message, optionally appends extra instructions, and returns that message plus a scheduled-fire key based on the task id and exact occurrence time.

**Call relations**: ScheduledTaskRunner._fire calls this after it has confirmed the task is still claimed and not expired. fire_body hands back the inbound text and deduplication key that _fire passes into the extension context invocation.

*Call graph*: called by 1 (_fire); 1 external calls (scheduled_fire_key).


##### `ScheduledTaskRunner.run`  (lines 69–78)

```
async def run(self) -> None
```

**Purpose**: Runs one full scheduled-task polling pass. It claims every task due at the current time, tries to fire each one, and reports a combined error if any fires failed.

**Data flow**: It starts with the runner’s extension context. It creates a ScheduleStore for reading and updating schedules, captures the current time, asks the store for due tasks that can be leased, and sends each claimed task to _fire. If _fire returns failure names, run collects them and finally raises one RuntimeError listing the failed tasks; otherwise it finishes quietly.

**Call relations**: This is the top-level method called by the extension’s recurring job. It delegates the detailed work for each individual task to ScheduledTaskRunner._fire, while it owns the batch-level flow and final failure reporting.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `ScheduledTaskRunner._fire`  (lines 80–117)

```
async def _fire(self, store: ScheduleStore, task: ScheduledTask, tick_at: datetime, expiry_checked_at: datetime) -> str | None
```

**Purpose**: Attempts to fire one specific scheduled task occurrence. It checks expiry, confirms the lease still belongs to this runner, invokes the agent, and advances the schedule only after the turn is accepted.

**Data flow**: It receives the schedule store, the claimed task, the tick time, and the time used for expiry checking. First it asks the store to retire the task if it has expired. If not, it calculates the following cron fire time and chooses either normal reporting instructions or final-fire instructions. It then verifies the claim still holds. If the claim is valid, it builds the inbound message and key, invokes the agent with the creator’s authority and marks the turn as scheduled. If the app is archived or no turn is admitted, it leaves the task on its current occurrence. If invocation fails, it returns a short failure label. If invocation succeeds with a turn id, it reschedules the task to the next fire time and returns no failure.

**Call relations**: ScheduledTaskRunner.run calls this once for each due task it claimed. _fire uses ScheduleStore methods to retire, re-check, and reschedule rows; it uses next_fire to find the next cron occurrence; it uses fire_body to prepare the exact message and key; and it calls the extension context to actually admit the scheduled turn into the conversation.

*Call graph*: calls 4 internal fn (fire_body, claim_holds, reschedule, retire_if_expired); called by 1 (run); 2 external calls (authority_from_member_id, next_fire).


### `core/src/ufo/runtime/ext/scheduled_fire.py`

`domain_logic` · `scheduled task admission and run lookup`

Scheduled tasks need a durable way to connect a run back to the task and time that caused it. This file provides that link as a simple text key shaped like “task-id:time”. The task id is a UUID, which is a globally unique identifier, and the time is written using Python’s standard ISO date-time format. That exact spelling matters because the key is used for deduplication: it helps the system know whether it has already admitted this particular scheduled occurrence. If the spelling changed, old and new code might treat the same scheduled run as different events.

The file has two small pieces that act like the two sides of a label maker. `scheduled_fire_key` prints the label for one task occurrence. `scheduled_fire_task_id` reads a label and tries to recover the task id from it. If the label was not made by this scheduled-fire system, it returns `None` instead of pretending it knows what it means. This matters because other kinds of runs, such as timer resumes from durable pauses, may also have keys, but they are not scheduled-fire keys.

#### Function details

##### `scheduled_fire_key`  (lines 14–16)

```
def scheduled_fire_key(task_id: UUID, fire_at: datetime) -> str
```

**Purpose**: Creates the stable text key for one scheduled occurrence of one task. Code uses this when admitting a scheduled run so the same occurrence can be recognized later and not admitted twice.

**Data flow**: It receives a task UUID and the date-time when that task is supposed to fire. It turns the time into its standard ISO text form with `isoformat()`, joins the task id and time with a colon, and returns that combined string. It does not change anything outside itself.

**Call relations**: This is the writing side of the contract. When the scheduled-task runner needs a key for a fire, this function builds it using Python’s date-time formatting, and later code can depend on the parser understanding the same shape.

*Call graph*: 1 external calls (isoformat).


##### `scheduled_fire_task_id`  (lines 19–25)

```
def scheduled_fire_task_id(key: str) -> UUID | None
```

**Purpose**: Tries to read a scheduled-fire key and extract the task id from it. If the key is not in the expected form, it safely returns `None` so callers know it was not a scheduled task fire key.

**Data flow**: It receives a text key. It takes the part before the first colon and tries to interpret that part as a UUID. If that succeeds, the UUID is returned; if it fails because the text is not a valid UUID, the function returns `None`. It does not inspect or validate the time part of the key.

**Call relations**: This is the reading side of the contract. When something such as a portal runs feed needs to connect a run back to a scheduled task, it can pass the run’s key here. The function hands off UUID parsing to Python’s UUID library and uses failure there as the signal that the key belongs to some other kind of run.

*Call graph*: 1 external calls (UUID).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `scheduled task creation and runner polling`

Scheduled tasks need a way to say “run every day at midnight” or “run every five minutes.” This file keeps that timing language in one place, instead of making the main task store understand cron itself. Cron is a compact schedule format made of fields such as minute, hour, day of month, month, and day of week.

The file does two simple but important jobs. First, it validates a schedule string before the system saves or uses it. It requires exactly five cron fields, which avoids accepting a different cron dialect by accident. Then it asks the external `croniter` library to confirm that the expression is actually valid.

Second, it computes the next run time after a given moment. This is important for task runners that poll periodically. The next time is always strictly after the supplied time, so if the runner was late and missed several possible run windows, the system schedules one catch-up run instead of creating a burst of many overdue runs. In everyday terms, it behaves like checking the next bus after you arrive at the stop, not listing every bus you already missed.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: Checks whether a schedule string is a valid 5-field cron expression. It protects the rest of the scheduled-task system from saving or running unclear or malformed timing rules.

**Data flow**: It receives a text schedule. It first splits the text into fields and rejects it if there are not exactly five parts. If the field count looks right, it asks `croniter` to validate the cron expression. If anything is wrong, it raises a `ValueError`; if everything is acceptable, it returns the original schedule unchanged.

**Call relations**: This function is the gatekeeper before a cron schedule is trusted. Its only outside handoff is to `croniter.croniter.is_valid`, which knows the detailed cron syntax rules. Code that creates or updates scheduled tasks would call this before storing the schedule.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: Calculates the next time a cron schedule should run after a given datetime. This lets the scheduler store a concrete `next_run_at` time even though the user provided a repeating cron rule.

**Data flow**: It receives a schedule string and a datetime called `after`. It gives both to `croniter`, which builds a timeline for that cron rule, then asks for the next datetime on that timeline. It returns that next datetime and does not change any other state.

**Call relations**: This function is used when the system needs to move from a repeating rule to one specific next run time. It hands the schedule calculation to `croniter.croniter`, then returns the computed datetime so the scheduled-task store or runner can decide when the task should fire next.

*Call graph*: 1 external calls (croniter).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/schedules.py`

`domain_logic` · `request handling and background scheduled-task sweeps`

A scheduled task is like a calendar entry for an agent: it says which conversation to return to, which agent should act, what prompt to send, when it should run next, and when it should stop. This file defines the database table for those entries and a ScheduleStore object that is the main doorway for reading and changing them.

The store is careful about boundaries. Every database query includes the current workspace, so one workspace cannot see or change another workspace's tasks. Member-facing actions also stay inside the current object/agent namespace, so a task belongs to the agent that will later run it.

The file supports two main flows. In the user-facing flow, code can create a task, update its schedule or prompt, cancel it, list tasks, and inspect the latest run status. In the background-runner flow, the runner asks for due tasks, receives a short lease on each one, checks that the lease still holds just before firing, then either retires expired tasks or reschedules successful recurring tasks. The lease works like putting a temporary “reserved” sign on a task, so overlapping workers split the work instead of duplicating it.

#### Function details

##### `_utc`  (lines 124–125)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: This helper makes sure a date and time is treated as UTC, the shared time zone used for scheduled-task timing. It is mainly here because some databases can return times without an attached time zone label.

**Data flow**: It receives a datetime value. If the value already has a time zone, it leaves it alone; if it has no time zone, it labels it as UTC. It returns the normalized datetime.

**Call relations**: Rows from the database are converted through _task and inspect_many, and both rely on _utc so the rest of the scheduling code can compare times consistently. _utc_opt also delegates to it for optional datetime fields.

*Call graph*: called by 3 (inspect_many, _task, _utc_opt); 1 external calls (replace).


##### `_utc_opt`  (lines 128–129)

```
def _utc_opt(value: datetime | None) -> datetime | None
```

**Purpose**: This is the nullable version of _utc. It is used for time fields that may be missing, such as a task that has never run or never expires.

**Data flow**: It receives either a datetime or None. If it receives None, it returns None; otherwise it passes the value to _utc and returns the UTC-normalized result.

**Call relations**: The database-row builder _task and the status reader inspect_many use this helper for optional timing fields, so callers do not need to repeat the same None check.

*Call graph*: calls 1 internal fn (_utc); called by 2 (inspect_many, _task).


##### `_claim_available`  (lines 132–136)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for “this task can be claimed by a runner now.” A task is available if nobody has claimed it, or if its previous claim has timed out.

**Data flow**: It receives the current time. It creates a database expression that checks whether claimed_by is empty or claim_expires_at is earlier than that time, then returns that expression for use in larger queries.

**Call relations**: due_task_workspaces.due uses it to find workspaces worth waking up for, and ScheduleStore.claim_due uses it again when actually leasing tasks. Using the same condition keeps discovery and claiming in agreement.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_expired`  (lines 139–143)

```
def _expired(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for “this task has passed its expiry time.” Expired tasks should be removed instead of fired again.

**Data flow**: It receives the current time. It creates a database expression that requires expires_at to be present and less than or equal to now, then returns that expression.

**Call relations**: due_task_workspaces.due uses it to notice workspaces with expired tasks, and ScheduleStore.claim_due uses it to delete expired tasks before leasing due ones.

*Call graph*: called by 2 (claim_due, due); 1 external calls (and_).


##### `_task`  (lines 146–165)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: This turns one database row into a ScheduledTask value that the rest of the code can use safely. It also fixes database-returned datetime values so they consistently carry UTC time zone information.

**Data flow**: It receives a database row containing task columns. It copies IDs, names, prompt text, schedule text, claim information, and timing fields into a ScheduledTask object, normalizing all relevant times on the way out. The result is an immutable in-memory description of the task.

**Call relations**: Create, update, list, and claim_due all read rows from the database and hand them through _task before returning them. This makes _task the shared translation point between raw storage and the rest of the scheduled-task feature.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 4 (claim_due, create, list, update); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 168–191)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: This tells the job system which workspaces might have scheduled-task work ready. It is a cheap first pass so the runner does not need to scan every workspace blindly.

**Data flow**: It creates a small query factory that finds workspace IDs with claimable due tasks or claimable expired tasks. It wraps that query with owner_candidates, producing a WorkspaceCandidates object used by the job framework.

**Call relations**: The job framework calls on this candidate source before scheduling runner work. Inside it, the nested due query uses _claim_available and _expired so it wakes only workspaces where a runner can actually make progress.

*Call graph*: 1 external calls (owner_candidates).


##### `due_task_workspaces.due`  (lines 174–189)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This nested function builds the actual database query for workspaces that have scheduled tasks needing attention now. It includes both tasks ready to run and tasks ready to be cleaned up because they expired.

**Data flow**: It reads the current UTC time, then builds a select query for distinct workspace IDs. The query keeps only tasks whose claim is available and whose expiry has passed, or whose next run time is due and which are not paused. It returns that query to the job-candidate wrapper.

**Call relations**: due_task_workspaces hands this function to owner_candidates. It shares the same availability and expiry helpers as ScheduleStore.claim_due, so the workspace-discovery step and the actual claim step do not disagree.

*Call graph*: calls 2 internal fn (_claim_available, _expired); 5 external calls (now, and_, not_, or_, select).


##### `ScheduleStore.workspace_id`  (lines 205–206)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property gives the store's current workspace ID. The store uses it to keep every scheduled-task query scoped to one workspace.

**Data flow**: It reads workspace_id from the ExtensionContext stored on the ScheduleStore and returns that UUID. It does not change anything.

**Call relations**: The other ScheduleStore methods use this property when building database filters. It is the simple link between the ambient extension context and the task table.


##### `ScheduleStore.create`  (lines 208–267)

```
async def create(self, conversation_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None, expires_at: datetime | None=None,
```

**Purpose**: This creates a new scheduled task for the current agent and workspace. It refuses to create the task if the target conversation belongs to a different agent, because the later task run must re-enter the right agent/conversation pair.

**Data flow**: It receives the target conversation, task name, schedule text, prompt, description, first run time, optional creator, optional expiry, and paused flag. It reads the current object agent and checks the conversation's agent, then inserts a new row with a fresh ID and returns it as a ScheduledTask. If another task with the same workspace, agent, and name already exists, it raises an error instead of overwriting it.

**Call relations**: User-facing task creation flows call this store method. It uses object_agent_id to bind the task to the current agent, uuid4 for the new task ID, and _task to convert the inserted database row into the value returned to the caller.

*Call graph*: calls 1 internal fn (_task); 2 external calls (object_agent_id, uuid4).


##### `ScheduleStore.update`  (lines 269–323)

```
async def update(self, expected: ScheduledTask, schedule: str, prompt: str, description: str, next_run_at: datetime, expires_at: datetime | None=None, *, paused: bool) -> ScheduledTask
```

**Purpose**: This edits the changeable parts of an existing scheduled task, such as the schedule, prompt, description, next run time, expiry, and paused state. It deliberately keeps the task's identity and run history intact.

**Data flow**: It receives the ScheduledTask version the caller believes it is editing, plus the new task details. It checks that the current agent still matches the task's agent, then updates only the row that still matches the expected ID, agent, conversation, name, and creator. It clears any active claim so an old leased version will not fire, and returns the updated ScheduledTask; if the row no longer matches, it raises an error.

**Call relations**: Editing flows call this after first reading a task. It uses _creator_matches to avoid confusing member-owned and creatorless tasks, and _task to turn the updated row back into the object returned to the caller.

*Call graph*: calls 2 internal fn (_creator_matches, _task); 2 external calls (update, object_agent_id).


##### `ScheduleStore.cancel`  (lines 325–341)

```
async def cancel(self, expected: ScheduledTask) -> None
```

**Purpose**: This deletes an existing scheduled task, but only if it is still the exact task version the caller expected. That protects against accidentally cancelling a task that changed underneath the user.

**Data flow**: It receives the ScheduledTask the caller wants to cancel. It checks that the current object agent still matches, then deletes a row matching the workspace, ID, agent, conversation, name, and creator. If no row is deleted, it raises an error saying the task changed while cancelling.

**Call relations**: User-facing cancellation code calls this method. It uses object_agent_id for the current agent boundary and _creator_matches for the creator condition, mirroring the safety checks used by update.

*Call graph*: calls 1 internal fn (_creator_matches); 2 external calls (delete, object_agent_id).


##### `ScheduleStore._creator_matches`  (lines 343–348)

```
def _creator_matches(self, expected: ScheduledTask) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the correct database condition for matching the creator of a task. It matters because a task with no creator must be matched with “is empty,” not with normal equality.

**Data flow**: It receives the expected ScheduledTask. If the task has no created_by_member_id, it returns a database condition requiring the column to be NULL; otherwise it returns a condition requiring the stored creator ID to equal the expected creator ID.

**Call relations**: ScheduleStore.update and ScheduleStore.cancel both use this helper when making exact-version changes. It keeps their creator checks consistent and prevents a member-owned edit from landing on a creatorless task, or the reverse.

*Call graph*: called by 2 (cancel, update).


##### `ScheduleStore._listing`  (lines 350–371)

```
def _listing(self, selected: tuple[sa.ColumnElement[Any], ...], *, conversation_id: UUID | None, names: tuple[str, ...] | None, visible_to_member_id: UUID | None, include_all_owners: bool, limit: int
```

**Purpose**: This builds the shared database query used for listing scheduled tasks. It applies workspace, agent, conversation, name, owner, ordering, and limit filters in one place.

**Data flow**: It receives the columns to select and optional filters such as conversation ID, task names, visible member ID, whether to include all owners, and limit. It starts with current workspace and current agent filters, adds any requested narrowing conditions, orders by task name, and returns the select query.

**Call relations**: ScheduleStore.list calls this helper before executing the query. By centralizing the listing rules here, list and list_reported get the same task-selection behavior.

*Call graph*: called by 1 (list); 2 external calls (select, object_agent_id).


##### `ScheduleStore.list`  (lines 373–392)

```
async def list(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -> tuple[S
```

**Purpose**: This returns scheduled tasks visible under the requested filters. It is the basic read method for pages or commands that need task definitions.

**Data flow**: It receives optional filters for conversation, names, visible member, ownership, and result limit. It asks _listing to build the query, executes it inside a transaction, converts each database row with _task, and returns a tuple of ScheduledTask objects.

**Call relations**: Member-facing reads call this directly when they only need task data. ScheduleStore.list_reported builds on it when it also needs conversation audience and surface-label information.

*Call graph*: calls 2 internal fn (_listing, _task); called by 1 (list_reported).


##### `ScheduleStore.list_reported`  (lines 394–428)

```
async def list_reported(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -
```

**Purpose**: This lists tasks together with facts about the conversation each task reports into, such as its audience and surface label. That extra context lets user-facing screens decide who can see the task and what conversation/channel name to show.

**Data flow**: It receives the same filters as list. It first gets the matching ScheduledTask objects, then asks the ExtensionContext for live facts about their conversations. For each task whose conversation still exists in those facts, it returns a ListedTask containing the task plus audience and surface label.

**Call relations**: This method sits on top of ScheduleStore.list. It then combines the listed tasks with conversation facts from the surrounding system, creating ListedTask objects for display and visibility decisions.

*Call graph*: calls 1 internal fn (list); 1 external calls (__init__).


##### `ScheduleStore.claim_due`  (lines 430–486)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: This is the runner's main way to reserve tasks that should run now. It also cleans up expired tasks before claiming runnable ones.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of tasks. It creates a fresh claim ID, deletes expired tasks whose claims are available, then marks up to the limit of oldest due, unpaused, unexpired, claimable tasks as claimed until the lease expiry time. It returns those leased rows as ScheduledTask objects.

**Call relations**: The scheduled-task runner calls this during each sweep of a workspace. It uses _expired and _claim_available for the same rules used by workspace discovery, and _task to return clean in-memory task objects to the runner.

*Call graph*: calls 3 internal fn (_claim_available, _expired, _task); 6 external calls (timedelta, delete, not_, select, update, uuid4).


##### `ScheduleStore.claim_holds`  (lines 488–519)

```
async def claim_holds(self, task: ScheduledTask) -> bool
```

**Purpose**: This checks whether a runner still owns the exact task it claimed before it actually fires it. It prevents most cases where a task was edited, cancelled, or reclaimed during the lease window from still being invoked.

**Data flow**: It receives a claimed ScheduledTask. If the task has no claim ID, it raises an error. Otherwise it re-reads the database row under a lock, requiring the same workspace, ID, claim, conversation, agent, name, and schedule. It returns true if that exact row still exists, false otherwise.

**Call relations**: ScheduledTaskRunner._fire calls this immediately before invoking a task. It does not hand off to other local helpers; its job is to be the final safety check between leasing and firing.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `ScheduleStore.retire_if_expired`  (lines 521–535)

```
async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool
```

**Purpose**: This deletes a claimed task if its expiry time has already passed before the runner fires it. It is a last-minute cleanup check.

**Data flow**: It receives a claimed ScheduledTask and the current time. If there is no claim ID, it raises an error; if the task has no expiry or has not expired yet, it returns false. If it is expired, it deletes the matching claimed row and returns true.

**Call relations**: ScheduledTaskRunner._fire calls this as part of the firing path. If it returns true, the runner knows the task was retired instead of invoked.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `ScheduleStore.reschedule`  (lines 537–567)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: This advances a claimed recurring task after a run, records when it ran, optionally records the turn it created, and clears the claim so it can be claimed again at its next due time.

**Data flow**: It receives a claimed ScheduledTask, the next run time, the last run time, and optionally the last turn ID. It refuses unclaimed tasks. It updates the matching claimed row with the new timing information, clears claimed_by and claim_expires_at, stores the last turn ID if provided, and returns whether a row was actually updated.

**Call relations**: ScheduledTaskRunner._fire calls this after a task has been invoked and the next occurrence has been computed. The later inspect methods use the recorded last_turn_id and last_run_at to show status.

*Call graph*: called by 1 (_fire); 1 external calls (update).


##### `ScheduleStore.inspect`  (lines 569–573)

```
async def inspect(self, expected: ScheduledTask) -> TaskInspection | None
```

**Purpose**: This gets the live status picture for one scheduled task. It is a convenience wrapper around the batch inspection method.

**Data flow**: It receives one expected ScheduledTask. It calls inspect_many with a one-item tuple, then returns the TaskInspection for that task ID if present, or None if the task no longer matches.

**Call relations**: Status-rendering code can call this when it only needs one task. Internally it delegates to ScheduleStore.inspect_many so all inspection rules live in one place.

*Call graph*: calls 1 internal fn (inspect_many).


##### `ScheduleStore.inspect_many`  (lines 575–611)

```
async def inspect_many(self, expected: tuple[ScheduledTask, ...]) -> dict[UUID, TaskInspection]
```

**Purpose**: This gathers status information for several scheduled tasks at once, including next run time, last run time, expiry, latest turn ID, latest turn status, and latest response text. Batch reading avoids repeating the same database and outcome lookups one task at a time.

**Data flow**: It receives a tuple of expected ScheduledTask objects. If the tuple is empty, it returns an empty dictionary. Otherwise it reads matching rows for the current workspace and agent, fetches outcomes for any recorded last_turn_id values, skips rows whose name or conversation no longer match the expected task, and returns a dictionary from task ID to TaskInspection with normalized UTC times and latest outcome details.

**Call relations**: ScheduleStore.inspect calls this for a single task, while callers that need many statuses can call it directly. It uses object_agent_id to stay inside the current agent boundary, _utc and _utc_opt for time normalization, and the ExtensionContext's turn_outcomes data to attach the latest run result.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 1 (inspect); 3 external calls (__init__, select, object_agent_id).
