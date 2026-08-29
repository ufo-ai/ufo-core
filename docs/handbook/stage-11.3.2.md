# Planning, todos, scheduling, and monitors  `stage-11.3.2`

This stage is shared workflow support for agents that need to manage work over time, not just answer once and stop. It provides the “planner’s desk” for the system: goals, checklists, reminders, waiting points, and watches on outside changes.

The objectives tools help an agent break a larger goal into steps, track progress, and hand independent steps to subagents. They also separate a claimed attempt from real proof that a step is done, so the plan stays honest. The todos extension is a lighter checklist for one conversation. It stores tasks durably, meaning the list can still be shown and updated on later turns.

The scheduled tasks tools handle time-based work. They expose recurring tasks as workspace objects, and they let a workflow pause until either a person responds or a timer runs out. The monitor tool watches outside state once by rerunning a shell command later. Before arming the watch, it runs the command immediately and saves that first result as the baseline, like taking a “before” photo.

## Files in this stage

### External State Watches
Defines one-time monitors that safely baseline an external shell-command result before watching for later changes.

### `extensions/monitors/ufo_ext_monitors/monitor_tool.py`

`domain_logic` · `tool call / request handling`

This file is the front door for creating a durable monitor. A monitor is like asking someone to periodically check a window and call you only if the view changes, if checking keeps failing, or if a deadline arrives. Here, the “window” is a shell command run inside the conversation’s sandbox, and the “view” is the command’s standard output.

The file defines the expected input for the tool: a short name, the command to run, how often to check, when to give up, what message to show now, and what instructions to use later if the monitor fires. The important safety step is that the command runs once right away, during the current tool call. If it fails, no monitor is saved. That prevents a broken command from becoming a hidden background job that fails later with no useful setup information.

If the command succeeds, its output is shortened if needed and stored as the baseline. Future probe output is compared against this saved text. The file also enforces limits, such as only allowing a small number of monitors per conversation and preventing duplicate names. Finally, it returns a result telling the agent to reply with the provided waiting message and end its turn.

#### Function details

##### `_require_ext`  (lines 76–79)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure the monitor tool has access to the extension context it needs. Without that context, the tool cannot reach the monitor storage area, so it stops immediately with a clear error.

**Data flow**: It receives the extension context, which may be present or missing. If it is present, it returns it unchanged. If it is missing, it raises an error instead of letting the rest of the monitor setup fail later in a more confusing way.

**Call relations**: The main `monitor` function calls this before creating a `MonitorStore`. It acts as the checkpoint at the start of the storage path, making sure the rest of the tool has the environment it needs.

*Call graph*: called by 1 (monitor).


##### `_refusal`  (lines 82–83)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: This helper creates a standard error result when the tool refuses to arm a monitor. It is used for expected user-facing problems, such as too many monitors, a duplicate name, or a probe command that fails.

**Data flow**: It takes a plain text explanation of what went wrong. It wraps that text in a `TextContent` message and returns a `ToolResult` marked as an error. Nothing else is changed.

**Call relations**: The `monitor` function calls this whenever it decides that arming should stop safely. Instead of saving a broken or unwanted monitor, `monitor` hands the reason to `_refusal`, which formats the failure as the tool’s response.

*Call graph*: called by 1 (monitor); 2 external calls (__init__, __init__).


##### `monitor`  (lines 86–133)

```
async def monitor(ctx: ToolContext, args: MonitorInput) -> ToolResult
```

**Purpose**: This is the actual tool action that arms a monitor. It checks limits and names, runs the probe command once now, stores the monitor only if that probe succeeds, and returns the baseline information the agent should see before ending its turn.

**Data flow**: It receives the current tool context and the validated monitor input. First it gets the monitor store from the extension context and reads the monitors already armed for this conversation. If the conversation is at the cap, or the requested name is already taken, it returns an error result. Next it runs the requested shell command in the sandbox with a timeout. If the command exits with an error, it returns an error message, including the tail end of stderr when available. If the command succeeds, it records the current time, saves the command output as the baseline, calculates the deadline and next probe time, and writes a new monitor row to storage. It then returns a tool result containing the directive to end the turn plus a JSON payload with the armed monitor name, baseline, timing, reason, and next steps.

**Call relations**: This function is the handler attached to the `MONITOR_TOOL` definition, so it runs when the agent calls the `monitor` tool. It relies on `_require_ext` before using `MonitorStore`, uses `_refusal` for safe early exits, asks the sandbox to run the probe command, and then hands the successful monitor details to the store so later background probing can continue from the saved baseline.

*Call graph*: calls 2 internal fn (_refusal, _require_ext); 9 external calls (__init__, __init__, __init__, now, timedelta, dumps, capped, qualified_name, stderr_tail).


### Objective Planning
Provides durable objective-management tools for planning work, tracking attempts, verifying evidence, and delegating independent steps.

### `extensions/objectives/ufo_ext_objectives/tools.py`

`domain_logic` · `request handling`

This file is the public tool layer for the objectives extension. An objective is a named piece of work that may last beyond one turn of conversation. It has ordered steps, and each step can name acceptance conditions: real checks such as “this file exists,” “this file contains this text,” or “this command succeeds.”

The important idea is that a step is not automatically finished when an agent records that it did the work. The tool re-checks the promised conditions against the live sandbox, like a teacher checking the answer rather than accepting “I finished” at face value. If the checks fail, the step stays unmet and the tool reports which conditions failed.

The file also protects against weak plans. When planning, it refuses file-based conditions that are already true before the work starts, because those would prove nothing. Command checks are allowed to already pass, because a test suite can be green before a change and still be the right guard against later breakage.

Finally, the file can read back an objective, refresh condition results, and spawn subagents for steps marked as independent. The tool definitions at the bottom expose these actions to the rest of the system.

#### Function details

##### `_require_ext`  (lines 95–98)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small guard makes sure the tool was called with the objectives extension context attached. Without that context, the tool cannot open transactions or read and write objective records.

**Data flow**: It receives the current tool context. If the context contains an extension object, it returns that object. If not, it stops immediately by raising an error, because continuing would mean trying to use storage that is not available.

**Call relations**: The main tool actions call this first before touching objective storage. It acts like a front-desk check: plan, read, record, and delegation flows all need the same extension access before they can proceed.

*Call graph*: called by 4 (plan_objective, read_objective, record_step, run_independent_steps).


##### `render`  (lines 101–116)

```
def render(view: ObjectiveView) -> str
```

**Purpose**: This turns an objective into a readable text report for the agent. It shows the objective, the step states, the acceptance conditions, recent evidence, and any failed checks.

**Data flow**: It receives an ObjectiveView, which is a snapshot of one objective and its steps. It builds a list of plain text lines, summarizes each condition, includes unmet verdicts and recent events, then returns one joined string.

**Call relations**: After planning, recording, or reading an objective, the tool uses this function to turn the stored state back into a message. It relies on condition_summary to describe each acceptance condition in human-friendly words.

*Call graph*: called by 3 (plan_objective, read_objective, record_step); 1 external calls (condition_summary).


##### `evaluate`  (lines 119–123)

```
async def evaluate(ctx: ToolContext, step: StepView) -> tuple[ConditionVerdict, ...]
```

**Purpose**: This checks all acceptance conditions for one step and collects the results. It is used when the system needs to decide whether a claimed step is actually complete.

**Data flow**: It receives the tool context and a StepView. For each acceptance condition on the step, it asks _verdict to test that condition in the sandbox. It returns a tuple of verdicts saying which conditions held and which did not.

**Call relations**: record_step calls this after an agent says it did a step. read_objective also calls it to refresh the status of already attempted steps. It delegates the actual per-condition checking to _verdict.

*Call graph*: calls 1 internal fn (_verdict); called by 2 (read_objective, record_step).


##### `_verdict`  (lines 126–150)

```
async def _verdict(ctx: ToolContext, condition: Condition, phase: str) -> ConditionVerdict
```

**Purpose**: This runs one acceptance condition against the real sandbox and reports whether it is true. It also records a metric, so operators can see how often planned or recorded checks pass or fail.

**Data flow**: It receives the tool context, one condition, and a phase name such as planning or recording. It converts the condition into a shell command: for example, a file-exists condition becomes a test command, and a file-contains condition becomes a grep command. It runs that command with a timeout, treats exit code zero as success, emits a measurement, and returns a ConditionVerdict with the condition, the true-or-false result, and a short detail string.

**Call relations**: evaluate uses this during step completion checks. plan_objective also uses it during planning to reject file-based conditions that are already true. It calls condition_summary for readable details and emit_metric for observability.

*Call graph*: called by 2 (evaluate, plan_objective); 4 external calls (__init__, quote, emit_metric, condition_summary).


##### `plan_objective`  (lines 153–208)

```
async def plan_objective(ctx: ToolContext, args: PlanObjectiveInput) -> ToolResult
```

**Purpose**: This records or revises an objective plan, but first rejects file-based acceptance conditions that would be meaningless because they are already true. It is the tool an agent uses to create durable work steps that later turns can resume.

**Data flow**: It receives the current tool context and the proposed objective name, directive, and steps. It loads any existing objective with the same name, compares old and new steps, and checks new file_exists or file_contains conditions before saving. If any such condition is already true, it returns an error explaining which conditions are vacuous. Otherwise, it stores the plan in the objectives database and returns a rendered view of the saved objective.

**Call relations**: This is one of the exposed tool handlers. It first gets extension access through _require_ext, may call _verdict to test proposed conditions, uses Objectives storage to read and save the plan, and uses render to show the result back to the caller.

*Call graph*: calls 3 internal fn (_require_ext, _verdict, render); 5 external calls (__init__, __init__, __init__, agent_current, condition_summary).


##### `record_step`  (lines 211–259)

```
async def record_step(ctx: ToolContext, args: RecordStepInput) -> ToolResult
```

**Purpose**: This records that a step was attempted or blocked. For an attempted step, it does not simply mark it done; it re-checks the step’s acceptance conditions and reports the real state.

**Data flow**: It receives the tool context plus the objective name, exact step title, kind of record, and evidence text. It loads the objective, finds the named step, and records the event. If the step is blocked, it stores or reports the block. If the step was attempted, it evaluates the step’s conditions, saves the check results, refreshes the objective, adds the fresh verdicts to the returned view, emits a metric about the final state, and returns a readable report.

**Call relations**: This is the core completion tool. It calls _require_ext for storage access, Objectives methods to find and update the step, evaluate to test acceptance conditions, _with_verdicts to overlay fresh results for display, render to format the answer, and emit_metric to count what happened.

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 5 external calls (__init__, __init__, __init__, agent_current, emit_metric).


##### `run_independent_steps`  (lines 262–308)

```
async def run_independent_steps(ctx: ToolContext, args: RunIndependentStepsInput) -> ToolResult
```

**Purpose**: This starts subagents for every step that the stored plan says can run independently. It saves the current agent from re-deciding which steps can be parallelized and from launching them one by one.

**Data flow**: It receives the tool context, the objective name, and the subagent profile to use. It loads the objective, looks at its runnable independent steps, and if any are ready, spawns one background subagent per step with a task containing the objective directive and that step title. It returns a message listing which subagent turn was created for each step. If nothing is runnable, it returns a message explaining that no independent step is ready.

**Call relations**: This tool handler uses _require_ext and Objectives storage to read the plan. It then calls ToolContext.spawn to create background workers and emit_metric for each dispatch. It does not record completion itself; the spawned subagents later return results, and the caller is expected to use record_step when those results arrive.

*Call graph*: calls 1 internal fn (_require_ext); 6 external calls (__init__, __init__, __init__, spawn, agent_current, emit_metric).


##### `read_objective`  (lines 311–330)

```
async def read_objective(ctx: ToolContext, args: ReadObjectiveInput) -> ToolResult
```

**Purpose**: This shows the current state of an objective, and refreshes checks for attempted steps before displaying it. It helps an agent see what remains to be done and which conditions are still unmet.

**Data flow**: It receives the tool context and an objective name. It loads the objective from storage. If it does not exist, it returns an error. For each step that has been attempted and has acceptance conditions, it re-evaluates those conditions, saves the new check results, overlays them into the displayed view, and finally returns the rendered objective report.

**Call relations**: This exposed tool handler starts with _require_ext, reads and updates through Objectives storage, uses evaluate for fresh condition checks, uses _with_verdicts to update the displayed snapshot, and uses render to produce the final text.

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 4 external calls (__init__, __init__, __init__, agent_current).


##### `_with_verdicts`  (lines 333–343)

```
def _with_verdicts(view: ObjectiveView, title: str, verdicts: tuple[ConditionVerdict, ...]) -> ObjectiveView
```

**Purpose**: This creates a copy of an objective view where one named step has updated verdicts. It is a display helper, used so the returned report can show the freshest check results without mutating the original object.

**Data flow**: It receives an ObjectiveView, a step title, and a tuple of verdicts. It builds a replacement ObjectiveView whose steps are the same except that the matching step has its verdicts replaced. It returns that new view.

**Call relations**: record_step uses this after checking an attempted step, and read_objective uses it while refreshing attempted steps. It relies on dataclasses.replace, which makes a changed copy of a data object rather than editing it in place.

*Call graph*: called by 2 (read_objective, record_step); 1 external calls (replace).


### Scheduled Workflows and Checklists
Covers durable time-based workflow controls and conversation-level todo boards for tracking ongoing work across turns.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `object operations and workflow pauses`

This file is the public face of the scheduled-tasks extension. It turns a recurring task into a normal workspace object, so the rest of the system can use the usual object actions such as list, get, update, and delete. A scheduled task has a cron schedule, which is a compact text pattern for recurring times, plus a prompt that is sent to the agent whenever the schedule fires. The task stays tied to the conversation and member that created it, so later runs return to the same place and act as the original creator.

The file also protects privacy and permissions. A task is visible roughly as far as the conversation it reports into is visible. Its prompt may be hidden from people who can see only management details. The creator can change the task content, while admins can change timing controls such as the schedule, expiry, paused state, or delete the task.

The second feature is `pause_and_wait`. This is not a managed object. It is more like placing a bookmark and setting an alarm: the workflow tells the user what it is waiting for, stores enough information to resume later, and wakes either when a new message arrives or when the timer fires.

#### Function details

##### `ScheduledTaskSpec.validate_utc_expiry`  (lines 103–106)

```
def validate_utc_expiry(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Checks that a task expiry time is written as a UTC timestamp. This matters because scheduled tasks run by clock time, and mixed or missing time zones can make a task expire at the wrong moment.

**Data flow**: It receives the proposed `expires_at` value. If there is no expiry, it leaves it alone. If there is an expiry, it checks that the value has timezone information and that its offset is exactly UTC, then returns the accepted value or raises an error before the task can be saved.

**Call relations**: This runs automatically when a `ScheduledTaskSpec` is validated. It is an early guardrail before the scheduling code later stores or updates a task.

*Call graph*: 2 external calls (utcoffset, timedelta).


##### `_require_scheduler`  (lines 123–124)

```
def _require_scheduler(ext: ExtensionContext | None) -> ScheduleStore
```

**Purpose**: Builds the storage helper used to read and write scheduled tasks. It also makes sure the scheduled-tasks extension context is present before any schedule data is touched.

**Data flow**: It receives an optional extension context. It first passes that context through `_require_ext`; if the context is missing, an error is raised. With a valid context, it creates and returns a `ScheduleStore`, which is the object used for schedule database operations.

**Call relations**: Most task operations call this when they need the schedule store: listing rows, showing conversation grants, checking status, creating or updating tasks, deleting tasks, and finding a task by name. It is the small doorway between this file's object logic and the lower-level schedule storage.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_apply_owned, _delete_owned, _find, _rows, _status, member_conversation_rows); 1 external calls (__init__).


##### `_require_ext`  (lines 127–130)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Confirms that code is running with the extension context it needs. The extension context is the bundle of services and storage access supplied to this extension.

**Data flow**: It receives an optional context. If the value is present, it returns it unchanged. If it is missing, it raises a runtime error explaining that scheduled tasks need the scheduled-tasks extension context.

**Call relations**: Both `_require_scheduler` and `pause_and_wait` use this before touching extension-owned storage or conversation state. It prevents later code from failing in less obvious ways.

*Call graph*: called by 2 (_require_scheduler, pause_and_wait).


##### `_summary`  (lines 133–134)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: Creates a short one-line label for a scheduled task. This is what people see in lists, where there is not room for the full prompt.

**Data flow**: It receives a scheduled task. It combines the task's schedule with its description, or with its prompt if there is no description, then cuts the text to the maximum summary length. The result is a compact display string.

**Call relations**: `ScheduledTaskObjects._rows` uses this when building list rows for readers who are allowed to see the task content. If content is hidden, `_rows` uses a private placeholder instead.

*Call graph*: called by 1 (_rows).


##### `_owner`  (lines 137–145)

```
def _owner(listed: ListedTask) -> GeneratedObjectOwner
```

**Purpose**: Describes who owns a scheduled task and how widely it is shared. This ownership record is used by the generic object system to decide who may see or act on the task.

**Data flow**: It receives a listed task, including the task itself and the audience of the conversation it reports into. It takes the creating member as the owner, converts the conversation audience into a shared/not-shared fact, and uses the task id as the generation marker so stale edits can be detected.

**Call relations**: Listing and conversation-row code call this whenever they need to attach object ownership to a task. The result feeds the visibility checks in the inherited object machinery.

*Call graph*: called by 2 (_rows, member_conversation_rows); 2 external calls (__init__, subject_shared).


##### `ScheduledTaskObjects._admin_can_apply`  (lines 166–169)

```
def _admin_can_apply(self, _old: ScheduledTaskSpec, spec: ScheduledTaskSpec) -> bool
```

**Purpose**: Decides whether an admin is allowed to apply a particular update. Admins may adjust timing controls, but they may not rewrite another member's prompt or description.

**Data flow**: It receives the old task spec and the proposed new spec. It looks at which fields the update actually sets. If the update includes `prompt` or `description`, it returns false for admin-only application; otherwise it returns true.

**Call relations**: This supports the broader permission rules used by the object framework around `ScheduledTaskObjects`. It backs up the file's key safety rule: changing when a task runs is different from changing what it says as a member.


##### `ScheduledTaskObjects.member_page`  (lines 171–193)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds a page of scheduled tasks visible to a member, with special support for filtering tasks by the conversation they report into.

**Data flow**: It receives the extension context, member identity, admin flag, and list query. If the query does not contain a conversation filter, it delegates to the base object listing behavior. If there is a conversation filter, it parses the conversation id, loads task rows for that conversation, keeps only rows visible to the member, wraps them as object rows, and returns a paged result.

**Call relations**: This is called by the object-listing surface when a member asks to see scheduled tasks. For conversation-specific listings it uses `_rows`; otherwise it lets the parent class run the standard member-readable object flow.

*Call graph*: calls 1 internal fn (_rows); 3 external calls (__init__, object_page, UUID).


##### `ScheduledTaskObjects.member_conversation_rows`  (lines 195–215)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Supplies the small scheduled-task entries that appear inside a conversation's object area. It tells the conversation view which tasks report into that conversation and whether their content should be visible.

**Data flow**: It receives a conversation id, member identity, admin flag, and limit. It asks the schedule store for tasks reporting to that conversation. For each task the member is allowed to see, it returns a grant containing the task name, generation id, and a flag saying whether the prompt/content is visible.

**Call relations**: Conversation object displays call this when building the set of objects attached to a conversation. It uses `_require_scheduler` to read tasks, `_owner` to apply shared ownership, and the visibility helper to avoid exposing private content.

*Call graph*: calls 2 internal fn (_owner, _require_scheduler); 2 external calls (__init__, task_content_visible).


##### `ScheduledTaskObjects._member_rows`  (lines 217–220)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Returns the scheduled-task rows available in a member-level listing. It is a thin wrapper that asks the shared row builder for all reported tasks.

**Data flow**: It receives the extension context and an optional member id. It passes those into `_rows` with no prompt truncation limit, then returns the owned rows produced there.

**Call relations**: The generic object framework calls this as part of member-readable listing. It relies on `_rows` to do the real work of loading tasks, adding owner information, and shaping list fields.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._owned_rows`  (lines 222–229)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Returns the task rows that are placed into an agent turn's context. It deliberately shortens prompts there so one large scheduled task cannot flood the model's working context.

**Data flow**: It receives the current tool context, including the extension context and acting member id. It calls `_rows` with the acting member and a prompt excerpt limit. The returned rows may contain shortened prompt text, while still preserving visibility rules.

**Call relations**: The object tooling calls this when an agent reads owned objects during a turn. It uses the same `_rows` builder as other listings, but chooses a safer prompt size for model context.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._rows`  (lines 231–277)

```
async def _rows(self, ext: ExtensionContext | None, *, member_id: UUID | None, prompt_max: int | None, conversation_id: UUID | None=None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the full list-row records for scheduled tasks. This is the main formatter that turns stored task records into readable object rows with status, ownership, visibility, and display fields.

**Data flow**: It receives the extension context, optional member id, optional prompt length limit, and optional conversation id. It loads matching tasks from the schedule store, fetches owner email addresses, inspects recent run information, and then creates one owned row per task. Each row includes fields such as next run time, last run time, paused state, owner email, origin, whether it belongs to the reader, and either the visible prompt or a private placeholder.

**Call relations**: Member listings, conversation-filtered pages, and turn-context object reads all flow through this function. It gathers data from storage and helper functions, then hands back rows that the object system can page, filter, and display.

*Call graph*: calls 3 internal fn (_owner, _require_scheduler, _summary); called by 3 (_member_rows, _owned_rows, member_page); 3 external calls (__init__, owner_emails, task_content_visible).


##### `ScheduledTaskObjects._member_object`  (lines 279–308)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ScheduledTaskSpec] | None
```

**Purpose**: Returns the detailed view of one scheduled task for a member. It includes the saved task definition, timestamps, a link back to the reporting conversation, and whether the full spec may be shown.

**Data flow**: It receives the extension context, task name, expected owner record, and optional member id. It finds the task by name, confirms it is the same generation the caller expects, and then builds an object detail containing schedule, prompt, description, expiry, paused state, created/updated times, and a `reports_to` conversation link. If the task is missing or stale, it returns nothing.

**Call relations**: The generic object `get` flow calls this when someone opens a scheduled task. It uses `_find` to locate the stored task and the visibility helper to decide whether the spec content should be exposed.

*Call graph*: calls 1 internal fn (_find); 5 external calls (__init__, __init__, __init__, __init__, task_content_visible).


##### `ScheduledTaskObjects._status`  (lines 310–342)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status information for one scheduled task, including whether it is paused, when it will run next, and what happened during its latest run.

**Data flow**: It receives the current tool context, task name, and expected owner record. It finds and verifies the task, asks the schedule store to inspect runtime state, and then builds a plain status dictionary. If the latest run has a response and the acting member may see the task content, it includes a shortened response excerpt.

**Call relations**: The object framework calls this when it needs status beyond the saved spec. It bridges from a named object to schedule inspection data, while respecting the same content visibility rule used elsewhere.

*Call graph*: calls 2 internal fn (_find, _require_scheduler); 1 external calls (task_content_visible).


##### `ScheduledTaskObjects._apply_owned`  (lines 344–396)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates a new scheduled task or updates an existing one. It enforces the key safety rules: tasks need a member creator, cron text must be valid, stale edits are rejected, and admins cannot rewrite another member's task content.

**Data flow**: It receives the current tool context, object name, proposed spec, previous spec, and previous owner if one exists. It validates any provided schedule. For a new task, it requires an acting member, a schedule, and a prompt, then stores the task tied to the current conversation with its first next-run time. For an update, it confirms the stored task still matches the expected owner, checks member/admin permission, preserves omitted fields, recalculates the next run, and writes the update.

**Call relations**: The object apply/update flow calls this when a manifest is applied. It uses `_find` to detect existing tasks, `_require_scheduler` to write storage changes, cron helpers to validate and schedule the next fire, and the tool context to ask whether the speaker is an admin when needed.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_scheduler); 4 external calls (__init__, now, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 398–402)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Cancels a scheduled task when the object system has approved deletion. It also protects against deleting the wrong version if the task changed while someone was editing.

**Data flow**: It receives the current tool context, task name, and expected owner record. It finds the task, checks that the stored task id matches the expected generation, and then asks the schedule store to cancel it. If the task is gone or changed, it raises an error instead of deleting blindly.

**Call relations**: The generic object delete flow calls this after permission checks. It uses `_find` for the stale-change check and `_require_scheduler` to reach the cancellation operation.

*Call graph*: calls 2 internal fn (_find, _require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 404–412)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTask | None
```

**Purpose**: Finds a scheduled task by its object name. It is a simple lookup helper used before showing, updating, deleting, or checking status.

**Data flow**: It receives the extension context and task name. It loads the reported scheduled tasks from the schedule store, scans them for a matching name, and returns the matching listed task if found. If none match, it returns nothing.

**Call relations**: Detailed object reads, status reads, apply/update, and delete all call this before acting on a specific task. It centralizes the name lookup so those higher-level functions can focus on permissions and formatting.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


##### `pause_and_wait`  (lines 475–512)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: Pauses the current workflow and arranges for it to resume either when a member sends a new message or when a durable timer expires. It is useful for things like waiting for approval, an email verification, or an outside system cooldown.

**Data flow**: It receives the tool context and pause request, including the message to show now, the wait length, resume instructions, reason, and optional metadata. It calculates the resume time, stores a pause record with the conversation, agent, current turn position, arrival watermark, and resume prompt, then returns a tool result telling the agent exactly what to reply and when the workflow is waiting until.

**Call relations**: This is the handler behind `PAUSE_AND_WAIT_TOOL`. It uses `_require_ext` to access extension services and `PauseStore` to arm the durable pause. Later, separate ingress or timer code can use the stored record to resume the conversation safely without this function needing to decide who wins a race between a message and a timer.

*Call graph*: calls 1 internal fn (_require_ext); 6 external calls (__init__, __init__, __init__, now, timedelta, dumps).


### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `request handling and conversation display`

This file is the “progress checklist” feature for multi-step assistant work. When a user asks for something that takes several steps, the agent can call one tool to create or replace a todo list, then call another tool to mark items as pending, in progress, or completed. Without this file, the agent would have no shared, stored checklist to show progress across tool calls or later turns in the same conversation.

The file defines the shapes of todo data using Pydantic models, which are validation classes that make sure incoming data has the expected fields and values. A todo board has a title and a list of tasks. Each task has text and a status.

The board is saved in the extension’s scoped store, which is like a small key-value notebook belonging to this extension. The key includes the conversation ID, so each conversation gets its own checklist. Creating a list replaces the whole saved board. Updating statuses reads the saved board first, checks that it exists, checks that each requested task number is valid, changes the requested statuses, and writes the board back.

The file also exposes the saved tasks as a conversation slot, meaning the user interface can ask for a compact view of the current checklist. Long titles, long task text, or very large task lists are trimmed for display and marked as truncated.

#### Function details

##### `_require_ext`  (lines 87–90)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a tool call has access to the todos extension context. The extension context is needed because it contains the durable store where the checklist is saved.

**Data flow**: It receives the current tool context. If that context includes an extension object, it returns it. If not, it stops the operation by raising an error, because the todo tools cannot read or write their saved board without it.

**Call relations**: When update_todo_list or update_todo_status starts, each first calls this helper to get the extension storage area. This keeps both tools from silently failing or writing nowhere if they are called outside the extension environment.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 93–94)

```
def _board_key(conversation_id: UUID) -> str
```

**Purpose**: This helper builds the storage key used to save and find a todo board for one conversation. It keeps different conversations from overwriting each other’s checklists.

**Data flow**: It receives a conversation ID. It prefixes that ID with the todo storage prefix and returns a single string key, like putting a label on a folder before filing it away.

**Call relations**: The create, update, summary, and read-display flows all use this helper before touching storage. That means every part of the feature agrees on exactly where a conversation’s todo board lives.

*Call graph*: called by 4 (_read_tasks, _summarize_tasks, update_todo_list, update_todo_status).


##### `_board_result`  (lines 97–98)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This helper turns the current todo board into the standard tool response sent back to the agent. It makes sure the agent sees the latest checklist after creating or updating it.

**Data flow**: It receives a TodoBoard object. It converts that board into JSON text, wraps the text as tool content, and returns a ToolResult containing that content. It does not change the board or storage.

**Call relations**: After update_todo_list creates a board, and after update_todo_status changes one, both call this helper to return the fresh board in the same response format. It hands off to the SDK response types that carry tool output back to the caller.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 101–103)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This helper reads a saved todo board from extension storage and turns it back into a validated TodoBoard object. It gives callers either a real board or a clear “nothing saved yet” result.

**Data flow**: It receives the extension context and a storage key. It asks the extension store for the raw saved value. If nothing is found, it returns None. If data is found, it validates that data as a TodoBoard and returns the resulting object.

**Call relations**: update_todo_status uses this before changing task statuses, because it must start from the existing board. The conversation summary and read-display functions also use it so the UI can show whatever checklist is currently saved.

*Call graph*: called by 3 (_read_tasks, _summarize_tasks, update_todo_status).


##### `update_todo_list`  (lines 106–110)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates a new checklist or replaces the existing one for the current conversation. An agent uses it at the start of multi-step work, or when the whole plan changes.

**Data flow**: It receives the tool context and the requested title and tasks. It confirms extension storage is available, builds a TodoBoard from the input, saves it under the current conversation’s board key, and returns the saved board as JSON text. The previous board for that conversation, if any, is replaced entirely.

**Call relations**: This is one of the public tools registered in manifest. It calls _require_ext to get storage access, _board_key to choose the right conversation-specific storage location, and _board_result to send the final board back to the agent.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 113–124)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that changes the status of one or more existing tasks. An agent uses it while working, for example to mark a task as in progress when it starts and completed when it finishes.

**Data flow**: It receives the tool context and a list of task-number/status changes. It confirms extension storage is available, finds the saved board for the current conversation, and refuses to continue if no list exists. For each update, it checks that the 1-based task number points to a real task, changes that task’s status, saves the updated board, and returns the new board as JSON text.

**Call relations**: This is the second public tool registered in manifest. It depends on _read_board to load the existing checklist, _board_key to find the right stored board, _require_ext for storage access, and _board_result to return the updated state to the agent.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `_summarize_tasks`  (lines 127–129)

```
async def _summarize_tasks(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This helper gives a quick summary count of how many tasks are saved for a conversation. It is used when the system only needs to know whether there is task content and how large it is, not the full details.

**Data flow**: It receives a conversation-slot context, which includes the extension and conversation ID. It reads the saved board for that conversation. If no board exists, it returns None. If a board exists, it returns the number of tasks on it.

**Call relations**: The TASKS_SLOT provider uses this as its summary function. It calls _board_key and _read_board to look up the same stored board used by the tools, keeping the display layer tied to the real saved checklist.

*Call graph*: calls 2 internal fn (_board_key, _read_board).


##### `_read_tasks`  (lines 132–162)

```
async def _read_tasks(ctx: ConversationSlotContext) -> TasksSlotPayload
```

**Purpose**: This helper prepares the saved checklist for display in the conversation UI. It returns a safe, compact payload with counts, task statuses, and trimmed text when needed.

**Data flow**: It receives a conversation-slot context. It reads the stored board for that conversation. If there is no board, it returns an empty task payload. If there is a board, it copies up to the allowed maximum number of tasks, trims long titles and descriptions, counts completed tasks, and marks the payload as truncated if anything had to be shortened or left out.

**Call relations**: The TASKS_SLOT provider uses this as its full read function. It relies on _board_key and _read_board to fetch the stored board, then builds ConversationTask and TasksSlotPayload objects that the surrounding SDK can show as a task panel or similar UI element.

*Call graph*: calls 2 internal fn (_board_key, _read_board); 2 external calls (__init__, __init__).


##### `manifest`  (lines 175–197)

```
def manifest() -> Manifest
```

**Purpose**: This function declares the todos extension to the host system. It tells the system the extension’s name, version, tools, prompt instructions, and conversation display slot.

**Data flow**: It takes no input. It builds a Manifest object that includes two tool definitions, one prompt section loaded from the companion markdown file, and the tasks conversation slot. The returned manifest is the package label and instruction sheet the host uses to install and expose this extension.

**Call relations**: The host calls manifest when loading the extension. Inside it, the update_todo_list and update_todo_status functions are registered as callable tools, the prompt section is attached so the agent knows how to use them, and TASKS_SLOT is attached so the UI can read checklist state.

*Call graph*: 3 external calls (__init__, __init__, __init__).
