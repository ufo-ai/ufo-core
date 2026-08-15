# Long-running automation, objectives, monitors, billing, and self-improvement  `stage-18`

This stage is the system’s long-term memory and alarm clock. It supports work that must continue after one chat turn ends: scheduled jobs, paused conversations, watched changes, multi-step objectives, billing, and offline prompt improvement.

Scheduled task files let agents create future or repeating work. The cron helper checks schedules and finds the next run time. The schedules store records what should run and lets workers safely claim it. The pauses store wakes a conversation later, without firing the same timer twice. The tools file exposes these actions to agents, while the conversation slot shows a short safe summary of active automations.

Monitors and source-change wakeups act like sensors. They rerun saved checks or notice source updates, then resume the right conversation when something changes. Objectives keep durable plans, steps, blockers, and completion checks so progress can be verified, not just claimed.

Metronome connects usage reporting and billing, with Stripe used for payment setup. Self-improvement runs in the background, replaying past failures, testing prompt changes, and only proposing careful, governed updates.

## Sub-stages

- [Monitors and source-change wakeups](stage-18.1.md) `stage-18.1` — 4 files
- [Durable objectives and checked progress](stage-18.2.md) `stage-18.2` — 3 files
- [Prompt self-improvement and governed prompt changes](stage-18.3.md) `stage-18.3` — 9 files

## Files in this stage

### Scheduled automation surfaces
User-facing tools and conversation views expose scheduled work and pauses while keeping durable task details behind safe interfaces.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `request handling`

This file lets the agent create and maintain recurring jobs in a way users can see, list, update, and delete. A scheduled task is like a calendar reminder for the agent: at a matching UTC cron time, it runs a saved prompt back in the same conversation where it was created. Without this file, the schedule database could exist, but members would not have the normal object interface for creating tasks, checking when they run next, seeing past run status, or cancelling them.

The file defines the shape of a task request, including the cron schedule, prompt, description, expiry time, and paused flag. It then implements `ScheduledTaskObjects`, the object-kind adapter that turns stored schedule rows into readable object rows and turns object updates into create, update, or cancel operations in `ScheduleStore`. It also enforces the important safety rules: a task needs a real member creator, only the creator can change the task content, and admins may adjust timing or delete tasks without rewriting another member’s prompt.

Visibility is treated carefully. A task is visible about as widely as the conversation it reports into, but private prompt text may be hidden when the reader should only know that a private task exists.

The final part, `pause_and_wait`, is not a recurring task. It records a one-off durable pause, then tells the agent to reply and stop. Later, either a member message or a timer resumes the workflow.

#### Function details

##### `ScheduledTaskSpec.validate_utc_expiry`  (lines 103–106)

```
def validate_utc_expiry(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This checks that a task expiry time is written as a UTC timestamp. It prevents unclear local-time expiry values, which could make a task stop at the wrong moment.

**Data flow**: It receives the proposed `expires_at` value from the task specification. If there is no expiry, it leaves it alone. If there is an expiry, it checks that the timestamp has UTC timezone information and rejects it with a clear error if not; otherwise it returns the same timestamp.

**Call relations**: This runs automatically while `ScheduledTaskSpec` is being validated. The object-apply flow relies on this earlier check so that `_apply_owned` can store expiry times without re-deciding what timezone they mean.

*Call graph*: 2 external calls (utcoffset, timedelta).


##### `_require_scheduler`  (lines 127–128)

```
def _require_scheduler(ext: ExtensionContext | None) -> ScheduleStore
```

**Purpose**: This helper gets the schedule store for this extension. It is used whenever the object logic needs to read or change scheduled task records.

**Data flow**: It receives an optional extension context. First it makes sure the context exists, then it wraps that context in a `ScheduleStore`, which is the storage access object for schedules. The result is a ready-to-use schedule store.

**Call relations**: Most task object methods call this before touching scheduled task data. It depends on `_require_ext` for the safety check, then hands back a `ScheduleStore` to listing, status, apply, delete, and find operations.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_apply_owned, _delete_owned, _find, _rows, _status, member_conversation_rows); 1 external calls (__init__).


##### `_require_ext`  (lines 131–134)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This helper makes sure scheduled-task code was given its required extension context. The context is the bundle of runtime services and storage access the extension needs.

**Data flow**: It receives an optional context. If the context is missing, it raises a runtime error explaining that scheduled tasks require this extension context. If present, it returns the context unchanged.

**Call relations**: Both `_require_scheduler` and `pause_and_wait` call this at the point where they need extension services. It acts like a guardrail before any storage-backed schedule or pause work begins.

*Call graph*: called by 2 (_require_scheduler, pause_and_wait).


##### `_summary`  (lines 137–138)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: This creates the short one-line label shown for a scheduled task in lists. It combines when the task runs with either its description or its prompt.

**Data flow**: It receives a stored scheduled task. It builds text in the form `schedule — description-or-prompt`, then cuts it down to the configured maximum length. The output is a compact summary string.

**Call relations**: `ScheduledTaskObjects._rows` uses this when task content is visible to the reader. If content is not visible, `_rows` uses a private placeholder instead.

*Call graph*: called by 1 (_rows).


##### `_owner`  (lines 141–149)

```
def _owner(listed: ListedTask) -> GeneratedObjectOwner
```

**Purpose**: This describes who owns a listed task and how broadly it is shared. That ownership information is what the generic object system uses to decide who can see or act on the object.

**Data flow**: It receives a listed task, reads the task creator and the audience of the conversation it reports into, converts that audience into a shared/not-shared ownership fact, and returns a generated owner record tied to the task’s unique generation id.

**Call relations**: `_rows` uses this owner record for normal list rows, and `member_conversation_rows` uses it before deciding whether a conversation should show a task grant. It calls `subject_shared` to translate conversation audience into the sharing flag the object system understands.

*Call graph*: called by 2 (_rows, member_conversation_rows); 2 external calls (__init__, subject_shared).


##### `ScheduledTaskObjects._admin_can_apply`  (lines 170–173)

```
def _admin_can_apply(self, _old: ScheduledTaskSpec, spec: ScheduledTaskSpec) -> bool
```

**Purpose**: This decides which updates an admin may make to someone else’s scheduled task. Admins may change timing-related settings, but not the prompt or description that would run as that member.

**Data flow**: It receives the old task spec and the proposed new spec. It checks which fields were actually included in the update. If the update includes `prompt` or `description`, it returns false for admin-only application; otherwise it returns true.

**Call relations**: The base object system asks this during permission checks for updates. This method supplies the scheduled-task-specific rule that separates cadence management from content editing.


##### `ScheduledTaskObjects.member_page`  (lines 175–197)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a page of scheduled task rows for a member, with special support for filtering by conversation. It lets users see tasks attached to one conversation instead of every visible task.

**Data flow**: It receives the extension context, the requesting member, whether they are an admin, and the list query. If the query does not contain a string `conversation` filter, it falls back to the parent class behavior. If the filter is present but not a valid UUID, it returns an empty page. Otherwise it loads rows for that conversation, keeps only rows visible to the requester, and returns them as an object page.

**Call relations**: This is called by the object listing surface when a member lists scheduled tasks. For the conversation-filtered case it delegates row-building to `_rows`, then uses the inherited visibility check before returning the final page.

*Call graph*: calls 1 internal fn (_rows); 3 external calls (__init__, object_page, UUID).


##### `ScheduledTaskObjects.member_conversation_rows`  (lines 199–219)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: This tells the conversation view which scheduled tasks report into that conversation. It returns small grants, not full task details, so the conversation can show linked task objects safely.

**Data flow**: It receives a conversation id, requester details, and a limit. It asks the schedule store for tasks that report into that conversation. For each task, it builds a grant containing the task name, task generation id, and whether the prompt content should be visible, then returns only the grants the requester is allowed to see.

**Call relations**: Conversation object surfaces call this when they need the scheduled-task slot for a conversation. It gets task rows through `_require_scheduler`, uses `_owner` for visibility decisions, and asks `task_content_visible` whether each task’s content can be shown.

*Call graph*: calls 2 internal fn (_owner, _require_scheduler); 2 external calls (__init__, task_content_visible).


##### `ScheduledTaskObjects._member_rows`  (lines 221–224)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This produces the scheduled task rows available to a member-level listing. It is the simple path for turning stored tasks into owned object rows.

**Data flow**: It receives the extension context and an optional member id. It forwards those values to `_rows` with no prompt length limit, then returns the resulting owned rows.

**Call relations**: The inherited member-readable object machinery calls this when it needs the task rows for listing or indexing. All of the real row construction is handed off to `_rows`.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._owned_rows`  (lines 226–233)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This produces the scheduled task rows that are placed into an agent turn’s context. It deliberately shortens prompts so a large list cannot flood the model with full task text.

**Data flow**: It receives the current tool context, including the extension context and acting member id. It asks `_rows` for rows visible to that member, but with a prompt excerpt limit. The result is a tuple of owned rows suitable for the agent to read during the turn.

**Call relations**: The object tool flow calls this when an agent lists objects inside a turn. It delegates to `_rows` and chooses the safer prompt limit for model context.

*Call graph*: calls 1 internal fn (_rows).


##### `ScheduledTaskObjects._rows`  (lines 235–270)

```
async def _rows(self, ext: ExtensionContext | None, *, member_id: UUID | None, prompt_max: int | None, conversation_id: UUID | None=None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This is the main row builder for scheduled tasks. It turns raw stored schedule records into the object-list entries that users and agents can read.

**Data flow**: It receives the extension context, the reader’s member id, an optional prompt length limit, and optionally a conversation id. It loads scheduled tasks from the schedule store, looks up creator email addresses, checks whether each reader may see the task content, and builds owned rows with fields such as id, conversation, next run time, pause state, owner email, origin, whether it is mine, and either the visible prompt excerpt or a private placeholder.

**Call relations**: Several higher-level listing paths call this: `_member_rows`, `_owned_rows`, and the conversation-filtered branch of `member_page`. It is where `_summary`, `_owner`, `owner_emails`, and `task_content_visible` come together to make one readable object row per task.

*Call graph*: calls 3 internal fn (_owner, _require_scheduler, _summary); called by 3 (_member_rows, _owned_rows, member_page); 3 external calls (__init__, owner_emails, task_content_visible).


##### `ScheduledTaskObjects._member_object`  (lines 272–301)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ScheduledTaskSpec] | None
```

**Purpose**: This returns the full object detail for one scheduled task, if the requested task still matches the expected owner generation. It is used when someone opens or gets a specific scheduled task.

**Data flow**: It receives the extension context, object name, expected owner record, and optional member id. It finds the stored task by name, rejects it if the stored generation no longer matches the requested one, and otherwise builds an object detail containing the task spec, creation/update timestamps, a link to the reporting conversation, and whether the spec itself should be visible.

**Call relations**: The object get/read flow calls this for a single scheduled task. It relies on `_find` to locate the task and on `task_content_visible` to decide whether the prompt-bearing spec should be shown to this reader.

*Call graph*: calls 1 internal fn (_find); 5 external calls (__init__, __init__, __init__, __init__, task_content_visible).


##### `ScheduledTaskObjects._status`  (lines 303–335)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This returns runtime status for a scheduled task, such as whether it is paused, when it runs next, and what happened on the last run. It gives users operational information beyond the saved task definition.

**Data flow**: It receives the tool context, task name, and expected owner. It finds the matching task, asks the schedule store to inspect it, and returns status fields including paused state, next run time, last run time, expiry time, and last turn details. If the last response exists and the reader may see content, it includes a shortened response excerpt.

**Call relations**: The object status flow calls this after a task has been identified. It uses `_find` for name/generation safety, `_require_scheduler` for inspection, and `task_content_visible` to avoid leaking private run output.

*Call graph*: calls 2 internal fn (_find, _require_scheduler); 1 external calls (task_content_visible).


##### `ScheduledTaskObjects._apply_owned`  (lines 337–389)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This creates a new scheduled task or updates an existing one. It is the central write path for applying a scheduled-task manifest.

**Data flow**: It receives the tool context, task name, proposed spec, previous spec if any, and owner information if updating. It validates the cron schedule if provided, requires an acting member, checks whether the stored task has changed while editing, and then either creates a new task or updates an existing one. Creation records the current conversation, prompt, next fire time, creator, expiry, and paused state. Updates preserve the original conversation and creator, recalculate the next fire time, and apply only the allowed changed fields.

**Call relations**: The generic object apply command calls this when a user or agent applies a scheduled task object. It calls `_find` to compare with current storage, `_require_scheduler` to write through `ScheduleStore`, `validate_cron` and `next_fire` for schedule correctness, and `speaker_is_admin` when permission checks require admin status.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_scheduler); 4 external calls (__init__, now, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 391–395)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This cancels a scheduled task. It protects against deleting the wrong version of a task by checking the stored generation first.

**Data flow**: It receives the tool context, task name, and expected owner. It finds the task by name, verifies that the stored task id matches the owner generation, and then asks the schedule store to cancel that task. If the task changed in the meantime, it raises an error instead of cancelling.

**Call relations**: The object delete flow calls this after permission checks. It uses `_find` for the current row and `_require_scheduler` to perform the cancellation in storage.

*Call graph*: calls 2 internal fn (_find, _require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 397–405)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTask | None
```

**Purpose**: This looks up one scheduled task by its object name. It is a small safety helper used before reads, status checks, updates, and deletes.

**Data flow**: It receives the extension context and a task name. It loads the reported scheduled tasks from the schedule store and returns the first listed task whose stored name matches. If none match, it returns nothing.

**Call relations**: `_member_object`, `_status`, `_apply_owned`, and `_delete_owned` all call this before acting on a single task. It centralizes the name lookup so each caller can then apply its own generation and permission checks.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


##### `pause_and_wait`  (lines 456–494)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: This tool records a durable pause for the current workflow and tells the agent exactly how to respond before stopping. The workflow later resumes when a member message arrives or when the timer fires.

**Data flow**: It receives the current tool context and pause arguments such as the message to show, wait length, resume instructions, reason, metadata, and user-facing description. It calculates the resume time, builds a resume prompt containing the reason, next steps, and metadata, records the pause in `PauseStore` with the current conversation and turn markers, then returns a tool result telling the agent to reply with the supplied `ai_response` and end the turn.

**Call relations**: This is the handler for the `pause_and_wait` tool definition. It calls `_require_ext` to get extension services, writes the pause through `PauseStore`, reads the conversation arrival sequence to avoid races between messages and timers, and returns `ToolResult` content that instructs the agent how to pause cleanly.

*Call graph*: calls 1 internal fn (_require_ext); 6 external calls (__init__, __init__, __init__, now, timedelta, dumps).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/conversation_slot.py`

`domain_logic` · `request handling`

A conversation can have scheduled tasks attached to it, such as reminders or recurring automations. This file is the bridge between the task scheduler and the conversation view. Without it, the conversation would not have a clean way to ask, “What automations belong here, and what is their current state?”

The file defines an automations slot provider named `AUTOMATIONS_SLOT`. A slot is like a labeled compartment in the conversation sidebar or context: it has an id, a display label, an icon, and two main actions. One action gives a quick count for summaries. The other reads the full slot content.

When reading, the code first opens the schedule store using the extension context. It then asks for tasks whose names match the conversation’s visible items. It does an important safety check: a task is only included if the visible item’s generation matches the task id. In plain terms, it confirms that the conversation is still authorized to see that exact version of the automation.

The file also trims long descriptions, schedules, statuses, and responses to fixed maximum sizes. This keeps the slot compact and prevents too much text from being exposed. If anything is omitted or shortened, the returned payload is marked as truncated. If an item’s content is not visible, sensitive fields like the description and latest response are hidden.

#### Function details

##### `_scheduler`  (lines 16–19)

```
def _scheduler(ctx: ConversationSlotContext) -> ScheduleStore
```

**Purpose**: This helper creates the connection to the scheduled-task storage for the current conversation slot request. It also makes sure the slot was called with the extension context it needs; otherwise it fails loudly instead of returning misleading data.

**Data flow**: It receives a conversation slot context. It reads `ctx.ext`, which is the extension-specific context needed to access scheduled tasks. If that context is missing, it raises an error. If it is present, it creates and returns a `ScheduleStore`, which is the object used to look up and inspect scheduled tasks.

**Call relations**: _conversation` and `_read` call this helper when they need access to stored schedules. The helper hands them a `ScheduleStore`, so the rest of the file does not have to repeat the same setup and safety check.

*Call graph*: called by 2 (_conversation, _read); 1 external calls (__init__).


##### `_conversation`  (lines 22–28)

```
async def _conversation(ctx: ConversationSlotContext) -> tuple[ScheduledTask, ...]
```

**Purpose**: This function fetches scheduled tasks that may belong to the current conversation. It limits the search to task names that are already visible in the conversation context.

**Data flow**: It receives the conversation slot context and extracts the names of the visible items. It then gets a `ScheduleStore` through `_scheduler` and asks the store to list tasks for the current conversation id, matching those names. It requests one more than the display limit so the caller can tell whether there are too many results to show fully. It returns the matching scheduled-task rows.

**Call relations**: _read` calls this function as the first step in building the automations slot. `_conversation` relies on `_scheduler` to reach the schedule store, then passes the raw task list back to `_read` for filtering, inspection, and formatting.

*Call graph*: calls 1 internal fn (_scheduler); called by 1 (_read).


##### `_read`  (lines 31–90)

```
async def _read(ctx: ConversationSlotContext) -> AutomationsSlotPayload
```

**Purpose**: This is the main reader for the automations slot. It builds the user-facing payload that describes each authorized automation, including its schedule, pause state, next run time, last run details, and whether anything had to be shortened or hidden.

**Data flow**: It receives the conversation slot context. It opens the schedule store, loads possible tasks through `_conversation`, and compares each task against the visible items in the context. Only tasks whose name and authorization generation match are kept. It inspects those tasks for run-time details such as next run time and last response. For each allowed task, it creates a `ConversationAutomation` object, trimming long text fields and hiding content when the visible item says content should not be shown. Finally, it returns an `AutomationsSlotPayload` containing the automation list and a `truncated` flag that says whether anything was left out or shortened.

**Call relations**: The slot provider calls `_read` when full automations content is needed. `_read` coordinates the whole flow: it asks `_conversation` for candidate tasks, uses `_scheduler` for deeper inspection, creates `ConversationAutomation` entries for safe display, and wraps them in an `AutomationsSlotPayload` for the conversation system.

*Call graph*: calls 2 internal fn (_conversation, _scheduler); 2 external calls (__init__, __init__).


##### `_summarize`  (lines 93–95)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick count of visible automations for the slot summary. It returns nothing when there are no visible items, so an empty slot does not need to show a count.

**Data flow**: It receives the conversation slot context and counts the visible items, capped at the maximum number of automations the slot is allowed to show. If the count is greater than zero, it returns that number. If the count is zero, it returns `None`.

**Call relations**: The slot provider calls `_summarize` when it only needs a lightweight summary instead of the full automation details. Unlike `_read`, it does not contact the schedule store; it only uses the visible items already present in the context.


### Billing integration
Metronome and Stripe integration lets workspace administrators configure payment methods and report usage from conversation workflows.

### `extensions/metronome/ufo_ext_metronome.py`

`domain_logic` · `scheduled billing and usage jobs, plus chat-triggered billing tool calls`

This extension is the bridge between the product and the billing providers. It sends two kinds of facts to Metronome: settled model usage and a daily count of workspace members. Usage is sent only after the product has finalized it, and each sent item gets a stable transaction id. That matters because if the job crashes and sends the same item again, Metronome can recognize it as the same event instead of charging twice. The daily seat count works the same way: one snapshot per workspace per day, useful for reporting and outreach, not for limiting access.

The file also supports billing setup through chat. An admin can ask the agent to set up billing. The tool creates or reuses a Stripe Customer, returns a short-lived Stripe portal link where the admin can save a card, and records enough information for a background job to finish activation later. That job waits until Stripe says a default payment method exists, then creates or finds the matching Metronome customer and contract. Stable provider identities are used throughout, like writing a permanent name tag on each external object, so retries and conflicts recover the original object instead of creating duplicates.

Finally, the manifest at the bottom tells UFO which jobs, tool, prompt text, and credential slot this extension provides.

#### Function details

##### `UsageShipper.run`  (lines 158–173)

```
async def run(self) -> None
```

**Purpose**: Sends one workspace's settled usage records to Metronome in safe batches. It is careful to acknowledge records only after Metronome accepts them, so a crash causes a harmless retry instead of lost usage.

**Data flow**: It reads the Metronome token from the environment and the workspace's fixed backfill floor from storage. It asks the core system for pending usage exports, turns them into Metronome events, posts them, logs success, and then marks those exports as acknowledged. It repeats until there is no more work or the last batch was smaller than the batch limit.

**Call relations**: The scheduled job wrapper calls this method for each metered workspace. It relies on UsageShipper._floor to decide how far back to look, UsageShipper._events to shape records for Metronome, _ingest to send them, and _require_env to fail early if the token is missing.

*Call graph*: calls 4 internal fn (_events, _floor, _ingest, _require_env); 1 external calls (log).


##### `UsageShipper._floor`  (lines 175–185)

```
async def _floor(self) -> datetime
```

**Purpose**: Chooses the oldest usage this workspace is allowed to ship. On the first run it stores a cutoff date, and later runs reuse that same cutoff instead of sliding it forward.

**Data flow**: It reads a saved timestamp from the workspace store. If none exists, it creates one equal to now minus the allowed backfill window, saves it, and returns it. If one exists, it parses the stored text back into a date and time.

**Call relations**: UsageShipper.run calls this before asking for pending exports. This floor protects Metronome from an unlimited historical backfill while still allowing delayed settled usage after the first run to be sent later.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._events`  (lines 187–206)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns internal usage export records into the exact event shape Metronome expects. Each event includes a stable transaction id, the workspace customer id, usage details, pricing details, and whether the workspace used its own provider key.

**Data flow**: It takes a tuple of UsageExport objects and reads the workspace id from the extension context. For each export, it builds a dictionary with timestamps, model and dimension names, amounts as strings, and the BYOK flag. It returns a list of event dictionaries ready to post.

**Call relations**: UsageShipper.run calls this immediately before _ingest. It uses _rfc3339 so event timestamps are sent in a standard date-time text format.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 209–210)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: Small scheduled-job entry point for usage shipping. It creates a UsageShipper for the current workspace and starts it.

**Data flow**: It receives an ExtensionContext from the job runner. It passes that context and the configured test transport seam into UsageShipper, then waits for the shipper to finish. It returns nothing.

**Call relations**: The manifest registers this as the handler for the usage shipping job. It exists so the job system has a simple function to call while the real work stays inside UsageShipper.run.

*Call graph*: 1 external calls (__init__).


##### `SeatShipper.run`  (lines 224–238)

```
async def run(self) -> None
```

**Purpose**: Sends one daily member-count snapshot for a workspace to Metronome. It avoids sending more than once per day for the same workspace.

**Data flow**: It reads the Metronome token, computes today's date, and checks the workspace store to see whether today's seat snapshot was already shipped. If not, it opens a transaction, asks the Seats service for the current roster snapshot, sends one Metronome event, logs the count, and records today's date as shipped.

**Call relations**: The scheduled seat job wrapper calls this for workspaces with members. It uses _require_env for configuration, Seats.snapshot for the count, SeatShipper._event to build the event, and _ingest to send it.

*Call graph*: calls 3 internal fn (_event, _ingest, _require_env); 3 external calls (__init__, now, log).


##### `SeatShipper._event`  (lines 240–248)

```
def _event(self, snapshot: SeatSnapshot, today: str) -> dict[str, object]
```

**Purpose**: Builds the Metronome event for a single daily seat snapshot. The transaction id combines the workspace and date so retries for the same day are recognized as duplicates.

**Data flow**: It takes a SeatSnapshot and today's date string. It reads the workspace id, counts members in the snapshot, adds the current timestamp, and returns a dictionary representing one seat-count event.

**Call relations**: SeatShipper.run calls this right before sending the event through _ingest. It uses _rfc3339 to format the event timestamp.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 1 external calls (now).


##### `_ship_seats`  (lines 251–252)

```
async def _ship_seats(ctx: ExtensionContext) -> None
```

**Purpose**: Small scheduled-job entry point for daily seat reporting. It creates a SeatShipper for the current workspace and runs it.

**Data flow**: It receives an ExtensionContext, passes it into SeatShipper along with the configured ingest transport, and waits for completion. It produces no direct return value.

**Call relations**: The manifest registers this as the handler for the seat shipping job. It keeps the job runner interface simple while SeatShipper.run contains the actual behavior.

*Call graph*: 1 external calls (__init__).


##### `BillingConfig.from_env`  (lines 271–290)

```
def from_env(cls) -> 'BillingConfig'
```

**Purpose**: Reads and validates all environment variables needed for billing setup and activation. It reports all missing settings at once so a half-configured deployment fails before creating provider objects.

**Data flow**: It looks up the Stripe secret key, Stripe portal configuration id, Metronome bearer token, and Metronome package alias from environment variables. If any are missing, it raises an error naming them. If all are present, it returns a frozen BillingConfig object.

**Call relations**: Billing tool calls and the billing activation job call this before talking to Stripe or Metronome. Usage and seat shipping do not use it because they only need the Metronome token.


##### `BillingActivation.run`  (lines 327–354)

```
async def run(self) -> None
```

**Purpose**: Finishes billing setup after an admin has saved a payment method in Stripe. It turns the stored billing intent into a live Metronome customer and contract, then notifies the original conversation.

**Data flow**: It reads the workspace billing record. If there is no pending record, or it is already activated, it stops. Otherwise it loads billing config, checks Stripe for a default payment method, creates or finds the Metronome customer, stores that id, creates or finds the Metronome contract, stores that id, and finally sends the activation notice.

**Call relations**: The scheduled billing activation wrapper calls this for member workspaces. It coordinates _billing_record, _has_default_payment_method, _metronome_customer, _metronome_contract, _contract_key, _store, and _notify so each external step can be retried safely.

*Call graph*: calls 7 internal fn (_notify, _store, _billing_record, _contract_key, _has_default_payment_method, _metronome_contract, _metronome_customer).


##### `BillingActivation._store`  (lines 356–358)

```
async def _store(self, record: BillingRecord) -> BillingRecord
```

**Purpose**: Saves the current billing record for the workspace. It is used after each successful provider step so a later retry can resume from the right place.

**Data flow**: It receives a BillingRecord, converts it into JSON-friendly data, writes it under the billing key in the workspace store, and returns the same record.

**Call relations**: BillingActivation.run calls this after recording Metronome ids. BillingActivation._notify calls it after marking activation complete.

*Call graph*: called by 2 (_notify, run); 1 external calls (model_dump).


##### `BillingActivation._notify`  (lines 360–373)

```
async def _notify(self, record: BillingRecord) -> None
```

**Purpose**: Tells the conversation that started billing setup that the payment method is saved and the plan is live. It also marks the billing record as activated only after the notification request is accepted.

**Data flow**: It reads the notification conversation and agent ids from the BillingRecord, sends a prompt through the extension context with a stable idempotency key, updates the record with the current activation time, stores it, and logs the activation.

**Call relations**: BillingActivation.run calls this after the Metronome contract exists. It hands the final record update to BillingActivation._store so future job ticks know the activation is done.

*Call graph*: calls 1 internal fn (_store); called by 1 (run); 3 external calls (now, model_copy, log).


##### `_activate_billing`  (lines 376–377)

```
async def _activate_billing(ctx: ExtensionContext) -> None
```

**Purpose**: Small scheduled-job entry point for billing activation. It creates a BillingActivation worker and runs it for the current workspace.

**Data flow**: It receives an ExtensionContext from the job system, builds BillingActivation with that context and the billing transport seam, and waits for the worker to complete.

**Call relations**: The manifest registers this as the billing activation job handler. The detailed provider workflow lives in BillingActivation.run.

*Call graph*: 1 external calls (__init__).


##### `_billing_record`  (lines 380–382)

```
async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None
```

**Purpose**: Loads the workspace's saved billing setup state, if one exists. This is the shared way the tool and activation job learn whether billing has been started and what provider ids are known.

**Data flow**: It reads the billing key from the workspace store. If nothing is saved, it returns None. If data is present, it validates it into a BillingRecord object and returns that.

**Call relations**: BillingActivation.run uses it to find pending work. The setup, status, and portal tool paths use it to decide whether billing exists and which Stripe or Metronome objects to query.

*Call graph*: called by 4 (run, _billing_portal, _billing_setup, _billing_status).


##### `_contract_key`  (lines 385–389)

```
def _contract_key(workspace_id: UUID) -> str
```

**Purpose**: Creates the permanent unique name used to identify this workspace's Metronome contract. The key is based on the workspace id and is never rotated.

**Data flow**: It receives a workspace UUID and returns a text key in the form used by Metronome contract creation and lookup.

**Call relations**: BillingActivation.run uses it when creating or finding the contract. _billing_status uses the same key so status reports only this workspace's own contract, not another contract on the same customer.

*Call graph*: called by 2 (run, _billing_status).


##### `manage_billing`  (lines 406–415)

```
async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Implements the chat tool that admins use to set up billing, check billing status, or open the Stripe portal. It is the main conversation-facing billing entry point.

**Data flow**: It receives the tool context and parsed input. It verifies the speaker is an admin, loads billing configuration, looks at the requested action, and delegates to the setup, status, or portal helper. It returns a ToolResult containing JSON text for the agent to show or use.

**Call relations**: The ToolDef registered in the manifest points to this function. It first calls _admin_billing, then hands the request to _billing_setup, _billing_status, or _billing_portal.

*Call graph*: calls 4 internal fn (_admin_billing, _billing_portal, _billing_setup, _billing_status).


##### `_admin_billing`  (lines 418–424)

```
async def _admin_billing(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Checks that the billing tool is being used by a real workspace member who is an admin. Billing changes are rejected unless this check passes.

**Data flow**: It reads the speaker member id and extension context from the ToolContext, then asks the context whether the speaker is an admin. If the speaker is missing or not an admin, it raises an error. Otherwise it returns the ExtensionContext.

**Call relations**: manage_billing calls this before doing any billing work. This keeps setup, status, and portal access behind the same permission gate.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 1 (manage_billing).


##### `_billing_setup`  (lines 427–467)

```
async def _billing_setup(ctx: ToolContext, ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Starts billing setup for a workspace and returns a Stripe link where the admin can save a payment method. It records the intended Metronome package before returning the link, so the background job can finish activation later.

**Data flow**: It reads any existing billing record. If none exists, it creates a Stripe Customer, builds a BillingRecord with the package, start time, and notification target, and stores it. Then it creates a Stripe portal session focused on payment-method update and returns the portal URL, customer id, and package as a text result.

**Call relations**: manage_billing calls this for the 'setup' action. It uses _billing_record to avoid overwriting active progress, _stripe_customer to create the Stripe identity, _portal_session for the link, _text_result for the tool response, and logging for observability.

*Call graph*: calls 4 internal fn (_billing_record, _portal_session, _stripe_customer, _text_result); called by 1 (manage_billing); 3 external calls (__init__, now, log).


##### `_billing_status`  (lines 470–498)

```
async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Reports the current billing truth from Stripe and Metronome. It does not rely only on the local record; it asks the providers whether a card and this workspace's contract actually exist.

**Data flow**: It loads the billing record. If there is none, it returns configured false. If there is one, it asks Stripe whether a default payment method exists and, when a Metronome customer id is known, asks Metronome whether the contract with this workspace's unique key exists. It returns those facts as JSON text.

**Call relations**: manage_billing calls this for the 'status' action. It uses _has_default_payment_method, _contract_key, _contract_for, and _text_result to produce a concise status report.

*Call graph*: calls 5 internal fn (_billing_record, _contract_for, _contract_key, _has_default_payment_method, _text_result); called by 1 (manage_billing).


##### `_billing_portal`  (lines 501–509)

```
async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Returns a fresh Stripe Customer Portal link for an already configured workspace. This link is for invoices, payment methods, and billing details.

**Data flow**: It loads the billing record. If billing was never set up, it raises an error telling the admin to run setup first. Otherwise it asks Stripe for a general portal session and returns the URL in a text result.

**Call relations**: manage_billing calls this for the 'portal' action. It uses _billing_record to find the Stripe Customer id, _portal_session to create the link, and _text_result to format the tool response.

*Call graph*: calls 3 internal fn (_billing_record, _portal_session, _text_result); called by 1 (manage_billing).


##### `_text_result`  (lines 512–513)

```
def _text_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a small dictionary as the text content expected by the tool system. It keeps billing tool responses in a consistent JSON format.

**Data flow**: It receives a payload dictionary, serializes it to JSON text, wraps that in TextContent, and returns a ToolResult containing that content.

**Call relations**: _billing_setup, _billing_status, and _billing_portal use this helper to return data to the agent in the same shape.

*Call graph*: called by 3 (_billing_portal, _billing_setup, _billing_status); 3 external calls (__init__, __init__, dumps).


##### `_require_env`  (lines 525–529)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads one required environment variable and fails clearly if it is missing. This prevents silent billing or metering failures caused by missing secrets.

**Data flow**: It receives an environment variable name, looks it up, and returns its value if present. If the value is empty or missing, it raises a RuntimeError naming the missing setting.

**Call relations**: UsageShipper.run and SeatShipper.run call this before sending events to Metronome. Billing setup uses BillingConfig.from_env instead because it needs several settings at once.

*Call graph*: called by 2 (run, run).


##### `_stripe_customer`  (lines 532–549)

```
async def _stripe_customer(config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or reuses the one Stripe Customer for a workspace. It uses a stable idempotency key so retries do not create duplicate Stripe customers.

**Data flow**: It receives billing config, a workspace id, and an optional HTTP transport. It sends a Stripe customer creation request with the workspace id in the description and metadata, then extracts and returns the customer id from Stripe's response.

**Call relations**: _billing_setup calls this when the workspace has no billing record yet. It uses _stripe for the HTTP call and _as_str to make sure Stripe returned a usable id.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_setup).


##### `_portal_session`  (lines 552–568)

```
async def _portal_session(config: BillingConfig, customer_id: str, flow: str | None, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates a short-lived Stripe Customer Portal URL. Depending on the flow argument, the portal is either limited to saving a payment method or opened for general billing management.

**Data flow**: It receives config, a Stripe customer id, an optional flow name, and an optional transport. It builds form data with the customer and portal configuration, adds the flow when provided, posts to Stripe, and returns the session URL.

**Call relations**: _billing_setup uses this with the payment-method update flow. _billing_portal uses it without a flow for the broader portal. It relies on _stripe and _as_str.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 2 (_billing_portal, _billing_setup).


##### `_has_default_payment_method`  (lines 571–581)

```
async def _has_default_payment_method(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> bool
```

**Purpose**: Checks whether Stripe says the customer has a default payment method on file. This is the one condition that allows billing activation to move forward.

**Data flow**: It receives config, a Stripe customer id, and an optional transport. It fetches the Stripe customer and looks inside invoice settings for a default payment method string. It returns true if one is present, otherwise false.

**Call relations**: BillingActivation.run calls this before creating Metronome billing objects. _billing_status calls it to report whether a card is currently on file.

*Call graph*: calls 1 internal fn (_stripe); called by 2 (run, _billing_status).


##### `_stripe`  (lines 584–602)

```
async def _stripe(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, data: dict[str, str] | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Sends low-level HTTP requests to Stripe and turns Stripe errors into clear exceptions. It centralizes Stripe authentication, API version pinning, timeout, and optional idempotency keys.

**Data flow**: It receives config, an HTTP method, a Stripe path, optional form data, optional idempotency key, and optional test transport. It builds headers, sends the request, raises StripeError if Stripe returns a failure status, and returns the JSON response body as a dictionary.

**Call relations**: _stripe_customer, _portal_session, and _has_default_payment_method all use this instead of each building their own Stripe request.

*Call graph*: called by 3 (_has_default_payment_method, _portal_session, _stripe_customer); 2 external calls (__init__, AsyncClient).


##### `_metronome_customer`  (lines 605–650)

```
async def _metronome_customer(config: BillingConfig, alias: str, stripe_customer_id: str, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or finds the Metronome customer that represents the workspace. The workspace id is used as the ingest alias, which is the same id attached to usage events.

**Data flow**: It first asks Metronome whether a customer already exists with the workspace alias. If found, it returns that id. If not, it sends a create request that links Metronome billing to the Stripe customer. If Metronome reports a conflict, it looks up the alias again and returns the existing customer when possible.

**Call relations**: BillingActivation.run calls this after Stripe has a default payment method. It uses _customer_by_alias for lookup, _metronome for API calls, and raises MetronomeError if creation succeeds but no id is returned.

*Call graph*: calls 2 internal fn (_customer_by_alias, _metronome); called by 1 (run); 1 external calls (__init__).


##### `_customer_by_alias`  (lines 653–662)

```
async def _customer_by_alias(config: BillingConfig, alias: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Looks up a Metronome customer by its ingest alias. In this extension, that alias is the workspace UUID used on usage events.

**Data flow**: It receives config, an alias, and an optional transport. It asks Metronome for customers matching that alias and returns the first customer id if one is present. If none are found, it returns None.

**Call relations**: _metronome_customer uses this before creating a customer and again after a conflict. It delegates the actual HTTP request to _metronome.

*Call graph*: calls 1 internal fn (_metronome); called by 1 (_metronome_customer).


##### `_metronome_contract`  (lines 665–701)

```
async def _metronome_contract(config: BillingConfig, customer_id: str, record: BillingRecord, uniqueness_key: str, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or finds the Metronome contract that makes the workspace's plan live. The contract is identified by a stable uniqueness key, so repeated attempts converge on the same contract.

**Data flow**: It first checks whether a contract with the workspace's uniqueness key already exists for the customer. If found, it returns that id. If not, it posts a contract creation request using the package and start time saved in the BillingRecord. If a conflict occurs, it checks again and returns the existing contract when possible.

**Call relations**: BillingActivation.run calls this after the Metronome customer id is known. It uses _contract_for for lookup, _metronome for the create call, and _rfc3339 to format the contract start time.

*Call graph*: calls 3 internal fn (_contract_for, _metronome, _rfc3339); called by 1 (run); 1 external calls (__init__).


##### `_contract_for`  (lines 704–727)

```
async def _contract_for(config: BillingConfig, customer_id: str, uniqueness_key: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Finds this workspace's own live Metronome contract on a customer. It matches by the uniqueness key instead of assuming the first listed contract is the right one.

**Data flow**: It receives config, a Metronome customer id, a uniqueness key, and an optional transport. It asks Metronome to list contracts for that customer, scans the returned contracts, and returns the id of the one with the matching key. If none match, it returns None.

**Call relations**: _metronome_contract uses this before creation and after conflicts. _billing_status uses it so the status report only counts the contract this extension intended to create.

*Call graph*: calls 1 internal fn (_metronome); called by 2 (_billing_status, _metronome_contract).


##### `_metronome`  (lines 730–750)

```
async def _metronome(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, body: dict[str, object] | None=None, params: dict[str, str] | None=None, idempotency_key
```

**Purpose**: Sends low-level HTTP requests to Metronome's billing APIs and turns non-success responses into clear errors. It also treats a conflict response specially so callers can reconcile with existing objects.

**Data flow**: It receives config, method, path, optional JSON body, optional query parameters, optional idempotency key, and optional transport. It builds authorization headers, sends the request, raises MetronomeConflict on HTTP 409, raises MetronomeError on other failures, and returns the JSON response body on success.

**Call relations**: _customer_by_alias, _metronome_customer, _metronome_contract, and _contract_for all use this shared API helper.

*Call graph*: called by 4 (_contract_for, _customer_by_alias, _metronome_contract, _metronome_customer); 3 external calls (__init__, __init__, AsyncClient).


##### `_as_str`  (lines 753–757)

```
def _as_str(value: object, field: str) -> str
```

**Purpose**: Checks that a provider response field is a non-empty string. It prevents later code from treating missing provider ids or URLs as valid.

**Data flow**: It receives an arbitrary value and a human-readable field name. If the value is a non-empty string, it returns it. Otherwise it raises a ValueError naming the missing field.

**Call relations**: _stripe_customer uses this to validate the Stripe customer id. _portal_session uses it to validate the Stripe portal URL.

*Call graph*: called by 2 (_portal_session, _stripe_customer).


##### `_ingest`  (lines 760–768)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Posts usage or seat events to Metronome's ingest endpoint. This is the shared sender for metering events.

**Data flow**: It receives a bearer token, a list of event dictionaries, and an optional transport. It posts the events as JSON with authorization. If Metronome rejects the request, it raises MetronomeError; otherwise it returns nothing.

**Call relations**: UsageShipper.run calls this for usage batches, and SeatShipper.run calls it for daily seat snapshots.

*Call graph*: called by 2 (run, run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 771–773)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: Formats a date and time as standard timestamp text for provider APIs. If the input has no timezone, it assumes UTC.

**Data flow**: It receives a datetime object. If the object is timezone-naive, it attaches UTC; otherwise it keeps the existing timezone. It returns the ISO/RFC3339-style text form.

**Call relations**: UsageShipper._events and SeatShipper._event use this for Metronome ingest timestamps. _metronome_contract uses it for the contract starting time.

*Call graph*: called by 3 (_event, _events, _metronome_contract); 1 external calls (replace).


##### `manifest`  (lines 776–812)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the UFO runtime. It tells the system the extension name, version, chat tool, scheduled jobs, prompt guidance, and credential slot it provides.

**Data flow**: It builds and returns a Manifest object. That manifest includes the manage_billing tool, three scheduled jobs with their candidate workspace selectors, a billing prompt section for the agent, and an Anthropic API key credential slot for bring-your-own-key usage labeling.

**Call relations**: The extension loader calls this to discover what the file contributes. The returned manifest wires _ship, _ship_seats, _activate_billing, and manage_billing into the rest of the system.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, metered_workspaces, member_workspaces).


### Scheduling storage primitives
Cron validation, pause timers, and durable schedule records provide the underlying machinery for claiming, waking, expiring, and rescheduling long-running work.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `scheduled task setup and runner scheduling`

Scheduled tasks need a simple way to say “run this every day at 9” or “run this every 5 minutes.” This file uses cron expressions for that. A cron expression is a compact time pattern, split into fields like minute, hour, day, month, and weekday. This extension expects the common 5-field form.

The main system stores tasks by their next run time. It does not try to understand cron rules itself. That keeps the central task store simpler, like a calendar that only needs to know the next appointment time, not the rule that created it. This file is where that rule is checked and turned into the next appointment.

There are two jobs here. First, `validate_cron` makes sure a schedule has exactly five parts and that the cron library accepts it as a real pattern. Second, `next_fire` asks the cron library for the next run time after a given moment.

An important detail is that the next run is strictly after the supplied time. If a worker falls behind, it does not create one run for every missed slot. Instead, the missed windows collapse into one next catch-up run, preventing a sudden burst of old work.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: Checks whether a proposed schedule is a valid 5-field cron expression. It is used to reject confusing or unsupported schedules before they are stored or used.

**Data flow**: A schedule string goes in. The function first splits it into space-separated parts and makes sure there are exactly five. Then it asks the cron library whether the expression is valid. If anything is wrong, it raises an error with a clear message; if it is acceptable, it returns the same schedule string unchanged.

**Call relations**: This is the gatekeeper before a cron schedule is trusted. Inside that check, it hands the expression to `croniter.croniter.is_valid`, which is the outside library’s validator, so this file does not have to reimplement all cron parsing rules itself.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: Calculates the next time a scheduled task should run after a given moment. This is what turns a repeating cron rule into one concrete datetime the task store can order and compare.

**Data flow**: A cron schedule and a datetime called `after` go in. The function gives both to the cron library, which walks forward from that datetime until it finds the next matching time. The function returns that next datetime and does not change anything else.

**Call relations**: When the scheduler needs the next concrete run time, this function delegates the cron math to `croniter.croniter`. The result can then be stored as the task’s next run time, while the wider system remains unaware of the details of cron syntax.

*Call graph*: 1 external calls (croniter).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pauses.py`

`domain_logic` · `background scheduled-task polling and pause/resume request handling`

A scheduled task needs a safe place to remember: “resume this conversation at this time, with this prompt.” This file is that place. It defines the extension’s own database table for pauses and a PauseStore object that reads and writes rows for one workspace.

The main rule is simple: one conversation can only be waiting on one pause at a time. If the same conversation is armed again, the old pause is replaced. The replacement gets a fresh identity so an old worker cannot confuse the new wait with the old one.

The file also supports background workers that look for timers that are due. A worker first “claims” a batch of due pauses, like putting a temporary sticky note on them saying “I am working on these.” The claim has an expiry time, so another worker can pick them up later if the first one dies. Before actually firing a pause, the worker checks that its claim still holds. After the pause is fired, it retires the row, but only if it still owns the same claim.

The code is careful about workspaces: every database query filters by workspace_id because the transaction connection is not automatically scoped. It is also careful about time zones, normalizing database timestamps so callers always see timezone-aware UTC times.

#### Function details

##### `_aware`  (lines 77–78)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: This helper makes sure a datetime has a timezone. If the database gives back a time without timezone information, it treats it as UTC so later time comparisons are consistent.

**Data flow**: It receives a datetime value. If the value already says what timezone it is in, it returns it unchanged; if not, it attaches UTC to it. The output is always safe for code that expects timezone-aware times.

**Call relations**: The row-building helper _row calls this whenever it turns database fields into a Pause object. This keeps all callers of PauseStore from having to repeat the same time cleanup.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 81–97)

```
def _row(row: sa.RowMapping) -> Pause
```

**Purpose**: This helper converts one raw database row into a Pause value object. It gives the rest of the code a clean, predictable Python object instead of database-specific row data.

**Data flow**: It receives a row mapping from the database, reads each pause column, normalizes the datetime fields through _aware, and builds a Pause object. The result is an in-memory description of one armed pause.

**Call relations**: PauseStore.arm, PauseStore.armed, and PauseStore.claim_due all funnel their database results through _row. That makes _row the single doorway between stored pause rows and the objects used by the runner.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 100–101)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This helper describes when a pause can be claimed by a worker. A pause is available if nobody has claimed it, or if its previous claim has expired.

**Data flow**: It receives the current time. It builds a database condition that checks whether claimed_by is empty or claim_expires_at is older than that time. The output is not a true or false value yet; it is a SQL condition used inside a query.

**Call relations**: PauseStore.claim_due uses this condition when leasing actual pauses, and due_pause_workspaces.due uses the same condition when deciding which workspaces are worth waking up. This keeps both decisions aligned.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_pause_workspaces`  (lines 104–117)

```
def due_pause_workspaces() -> WorkspaceCandidates
```

**Purpose**: This function tells the job system which workspaces might have pauses ready to run. It is a lightweight “where should we look next?” hook for the pause runner.

**Data flow**: It creates an inner database query factory, then hands that factory to owner_candidates. The result is a WorkspaceCandidates object that the job system can use to schedule work for owners of due pauses.

**Call relations**: The pause runner’s scheduling layer calls this rather than scanning every workspace blindly. It hands off to owner_candidates so the shared job system can decide which worker should inspect those candidate workspaces.

*Call graph*: 1 external calls (owner_candidates).


##### `due_pause_workspaces.due`  (lines 109–115)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This inner function builds the actual query for workspaces with due, claimable pauses. It looks for at least one timer whose resume time has arrived and whose lease is free.

**Data flow**: It reads the current UTC time, builds the same claim-availability condition used by real claiming, and selects distinct workspace IDs from matching pause rows. The output is a SQL select statement, not the final list itself.

**Call relations**: due_pause_workspaces passes this query builder to owner_candidates. The job system can then run the query when it needs fresh workspace candidates.

*Call graph*: calls 1 internal fn (_claim_available); 2 external calls (now, select).


##### `PauseStore.arm`  (lines 126–183)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, resume_at: datetime, origin_seq: int, origin_arrival_seq: int, prompt: str, user_description: str, created_by_member_id: UUID | None) -> P
```

**Purpose**: This method arms a pause for one conversation, replacing any existing pause for that same conversation. It is used when a workflow says, in effect, “wake me up later.”

**Data flow**: It receives the conversation, agent, resume time, recorded sequence numbers, prompt text, user-facing description, and optional creator member. It writes a row for this workspace and conversation; if one already exists, it updates it, clears any old claim, and gives the wait a new ID. It returns the saved pause as a Pause object.

**Call relations**: Code that creates scheduled waits calls arm to persist them. After the database write, arm sends the returned row through _row so callers get the same normalized Pause shape as all other reads.

*Call graph*: calls 1 internal fn (_row); 1 external calls (uuid4).


##### `PauseStore.armed`  (lines 185–191)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Pause, ...]
```

**Purpose**: This method lists currently armed pauses for the workspace. It can return all pauses or just the pause for one conversation.

**Data flow**: It optionally receives a conversation ID. It queries pause rows for the current workspace, narrows to that conversation if requested, orders them by resume time, converts each row through _row, and returns a tuple of Pause objects.

**Call relations**: This is the straightforward read side of PauseStore. It does not claim or change rows; it simply gives other code a snapshot of what is currently waiting.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `PauseStore.claim_due`  (lines 193–234)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_PAUSES) -> tuple[Pause, ...]
```

**Purpose**: This method leases a limited batch of pauses whose timers are due. Leasing lets multiple workers share the queue without firing the same pause at the same time.

**Data flow**: It receives the current time, a lease length in seconds, and an optional limit. It finds due rows in this workspace whose claims are empty or expired, stamps them with a fresh claim ID and expiry time, and returns the rows it successfully claimed as Pause objects.

**Call relations**: A background tick calls claim_due before trying to resume workflows. It uses _claim_available to match the candidate logic and _row to turn the claimed database rows into Pause objects for the firing step.

*Call graph*: calls 2 internal fn (_claim_available, _row); 4 external calls (timedelta, select, update, uuid4).


##### `PauseStore.claim_holds`  (lines 236–261)

```
async def claim_holds(self, row: Pause) -> bool
```

**Purpose**: This method checks whether a worker still owns the pause it is about to fire. It prevents an old claim from firing a pause that was replaced or taken over.

**Data flow**: It receives a Pause that should already contain a claim ID. If there is no claim ID, it raises an error because unclaimed pauses are not allowed to fire. Otherwise it looks for the same row, in the same workspace, with the same claim, and returns true if it still exists.

**Call relations**: PauseRunner._fire calls this immediately before invoking the resume action. If the claim no longer holds, the runner can avoid firing stale work that another update has superseded.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `PauseStore.retire`  (lines 263–276)

```
async def retire(self, row: Pause) -> None
```

**Purpose**: This method removes a pause after the worker is done with it. It only deletes the row if the same claim still owns it, so a newer replacement is not accidentally removed.

**Data flow**: It receives a claimed Pause. If the pause has no claim ID, it raises an error. Otherwise it deletes the row matching that pause ID, workspace, and claim ID; if the claim expired or the row was replaced, nothing is deleted.

**Call relations**: PauseRunner._fire calls retire after a pause has been fired or otherwise settled. The claim guard means the runner can clean up its own work without damaging a newer pause armed by another part of the system.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/schedules.py`

`domain_logic` · `request handling and background scheduler runs`

Scheduled tasks need to survive process restarts and be shared safely between background workers. This file solves that by defining the `scheduled_task` database table and a `ScheduleStore`, which is the main doorway for reading and changing rows in that table. Think of it like a shared calendar plus a checkout desk: members can add or edit events, while the background runner checks out due events so only one worker performs each one.

Each task row stores the workspace, conversation, agent, task name, cron-style schedule text, prompt, next run time, optional expiry time, and claim information. A claim is a short lease that says, “this worker is currently responsible for this task.” That lease prevents two workers from firing the same task at the same time.

The store is careful about boundaries. Every database query filters by workspace, because the transaction connection is not automatically scoped. Member-facing actions also filter by the current object agent, so one agent cannot accidentally edit another agent’s tasks. Updates and cancels check the exact task identity they were given; if the row changed meanwhile, they fail instead of silently changing the wrong thing.

The file also normalizes timestamps to UTC, removes expired tasks, lists tasks with conversation visibility details, and inspects the latest run outcome for status displays.

#### Function details

##### `_utc`  (lines 124–125)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: Makes sure a datetime value is treated as UTC time. This matters because some databases may return timestamps without timezone information, and the rest of the scheduled-task code expects a clear, timezone-aware value.

**Data flow**: It receives one datetime. If the datetime already has timezone information, it passes it through unchanged; if not, it labels it as UTC. The result is a datetime that consumers can compare safely.

**Call relations**: It is used by `_task`, `_utc_opt`, and `ScheduleStore.inspect_many` whenever database time values are turned into application values. It delegates the small adjustment to the datetime object's `replace` method when a timezone label is missing.

*Call graph*: called by 3 (inspect_many, _task, _utc_opt); 1 external calls (replace).


##### `_utc_opt`  (lines 128–129)

```
def _utc_opt(value: datetime | None) -> datetime | None
```

**Purpose**: Does the same UTC cleanup as `_utc`, but for values that may be absent. It is used for optional times such as last run time or expiry time.

**Data flow**: It receives either a datetime or `None`. If the value is missing, it returns `None`; otherwise it sends the datetime to `_utc` and returns the cleaned-up result.

**Call relations**: It sits between row-building code and `_utc`. `_task` and `ScheduleStore.inspect_many` call it for nullable timestamp fields so callers do not each need to repeat the same `None` check.

*Call graph*: calls 1 internal fn (_utc); called by 2 (inspect_many, _task).


##### `_claim_available`  (lines 132–136)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this task is free to be claimed.” A task is free if no worker has claimed it, or if its previous claim lease has expired.

**Data flow**: It receives the current time. It creates a SQL condition that matches rows where `claimed_by` is empty or `claim_expires_at` is earlier than that time. The output is not task data; it is a filter used in later database queries.

**Call relations**: Both `due_task_workspaces.due` and `ScheduleStore.claim_due` use this helper so they agree on what “claimable” means. It relies on SQLAlchemy’s `or_` builder to express the database-side choice.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_expired`  (lines 139–143)

```
def _expired(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this task has reached its expiry time.” Expired tasks should be removed instead of fired again.

**Data flow**: It receives the current time. It creates a SQL condition that matches rows with an `expires_at` value that is not empty and is at or before now. The result is a database filter.

**Call relations**: The workspace candidate finder and the claim process both call this helper, so the system consistently recognizes expired tasks. It uses SQLAlchemy’s `and_` builder to combine the expiry checks.

*Call graph*: called by 2 (claim_due, due); 1 external calls (and_).


##### `_task`  (lines 146–165)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: Turns a raw database row into a `ScheduledTask` value that the rest of the code can work with. It is the single conversion point for scheduled-task rows.

**Data flow**: It receives a row mapping from the database. It pulls out IDs, names, prompt text, schedule text, claim state, and timing fields, normalizes the timing fields to UTC, and returns a `ScheduledTask` object.

**Call relations**: `ScheduleStore.create`, `ScheduleStore.update`, `ScheduleStore.list`, and `ScheduleStore.claim_due` all call this after reading rows. By funneling through `_task`, those callers all get the same clean timestamp behavior.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 4 (claim_due, create, list, update); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 168–191)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: Tells the background job system which workspaces might have scheduled-task work ready. This lets the runner avoid scanning every workspace when only some contain due or expired tasks.

**Data flow**: It defines a database query factory that finds distinct workspace IDs with claimable due tasks or claimable expired tasks. It then hands that query factory to the job framework, which returns a `WorkspaceCandidates` object.

**Call relations**: This is the scheduled-task runner’s entry point for finding candidate workspaces. It hands the inner `due` query to `owner_candidates`, which plugs the query into the broader workspace job ownership system.

*Call graph*: 1 external calls (owner_candidates).


##### `due_task_workspaces.due`  (lines 174–189)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query used to find workspaces with scheduled-task work ready now. It counts both tasks ready to run and tasks ready to be deleted because they expired.

**Data flow**: It reads the current UTC time, then builds a SQL query selecting workspace IDs from tasks that are claimable and either expired or due and not paused. It returns that query to the job candidate machinery.

**Call relations**: This inner function is supplied by `due_task_workspaces` to `owner_candidates`. It uses `_claim_available` and `_expired` so the candidate search matches the later claiming logic in `ScheduleStore.claim_due`.

*Call graph*: calls 2 internal fn (_claim_available, _expired); 5 external calls (now, and_, not_, or_, select).


##### `ScheduleStore.workspace_id`  (lines 205–206)

```
def workspace_id(self) -> UUID
```

**Purpose**: Provides the current workspace ID from the extension context. Almost every database operation needs this so tasks from different workspaces stay separated.

**Data flow**: It reads `workspace_id` from `self.ctx` and returns it. It does not change anything.

**Call relations**: The store’s methods use this property while building database filters and inserted rows. It keeps workspace access centralized instead of repeating `self.ctx.workspace_id` everywhere.


##### `ScheduleStore.create`  (lines 208–267)

```
async def create(self, conversation_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None, expires_at: datetime | None=None,
```

**Purpose**: Creates a new scheduled task for the current agent and conversation. It also prevents duplicate task names for the same workspace and agent.

**Data flow**: It receives the conversation, task name, schedule, prompt, description, first run time, optional creator, optional expiry, and paused state. It checks that the conversation belongs to the current executing agent, inserts a new database row, and returns it as a `ScheduledTask`; if a matching task name already exists, it raises an error.

**Call relations**: Member-facing code uses this when a user or agent defines a new recurring task. It calls `object_agent_id` to identify the current agent, uses `uuid4` for the new task ID, and sends the returned row through `_task` before handing it back.

*Call graph*: calls 1 internal fn (_task); 2 external calls (object_agent_id, uuid4).


##### `ScheduleStore.update`  (lines 269–319)

```
async def update(self, expected: ScheduledTask, schedule: str, prompt: str, description: str, next_run_at: datetime, expires_at: datetime | None=None, *, paused: bool) -> ScheduledTask
```

**Purpose**: Edits an existing scheduled task without changing its core identity. It protects against overwriting a task that was changed or replaced while the caller was editing.

**Data flow**: It receives the task version the caller expects plus new schedule, prompt, description, next run time, expiry, and paused flag. It checks that the current agent still matches, updates only the exact matching row, clears old run and claim information, and returns the updated `ScheduledTask`; if no exact row matches, it raises an error.

**Call relations**: This is used when a task definition is changed. It calls `_creator_matches` so creator identity is checked correctly, uses `object_agent_id` to stay inside the current agent boundary, and converts the updated row with `_task`.

*Call graph*: calls 2 internal fn (_creator_matches, _task); 2 external calls (update, object_agent_id).


##### `ScheduleStore.cancel`  (lines 321–337)

```
async def cancel(self, expected: ScheduledTask) -> None
```

**Purpose**: Deletes a scheduled task, but only if it still matches the exact task the caller intended to cancel. This prevents a stale view from deleting the wrong task.

**Data flow**: It receives the expected `ScheduledTask`. It checks that the current agent is still the task’s agent, then deletes the row matching the workspace, ID, agent, conversation, name, and creator. If nothing was deleted, it raises an error because the task changed meanwhile.

**Call relations**: Member-facing cancellation flows call this. It uses `object_agent_id` for the agent boundary and `_creator_matches` to distinguish creator-owned tasks from creatorless tasks.

*Call graph*: calls 1 internal fn (_creator_matches); 2 external calls (delete, object_agent_id).


##### `ScheduleStore._creator_matches`  (lines 339–344)

```
def _creator_matches(self, expected: ScheduledTask) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that checks whether a row has the same creator as the expected task. It handles creatorless tasks carefully, because SQL treats missing values differently from ordinary values.

**Data flow**: It receives an expected task. If the task has no creator, it returns a SQL condition requiring the database field to be `NULL`; otherwise it returns a condition requiring the creator ID to equal the expected creator ID.

**Call relations**: `ScheduleStore.update` and `ScheduleStore.cancel` use this as part of their exact-match safety checks. It ensures an operation approved for one creator’s task does not accidentally apply to an unowned task or another creator’s task.

*Call graph*: called by 2 (cancel, update).


##### `ScheduleStore._listing`  (lines 346–367)

```
def _listing(self, selected: tuple[sa.ColumnElement[Any], ...], *, conversation_id: UUID | None, names: tuple[str, ...] | None, visible_to_member_id: UUID | None, include_all_owners: bool, limit: int
```

**Purpose**: Builds the shared database query for listing scheduled tasks. It applies workspace, agent, conversation, name, owner, sorting, and limit filters in one place.

**Data flow**: It receives the columns to select and optional filters such as conversation ID, names, visible member, owner inclusion, and limit. It builds a SQL select query for tasks in the current workspace and current agent, adds the requested filters, orders by task name, and returns the query.

**Call relations**: `ScheduleStore.list` calls this helper before executing the query. It uses `object_agent_id` so member-facing listings stay scoped to the current object agent.

*Call graph*: called by 1 (list); 2 external calls (select, object_agent_id).


##### `ScheduleStore.list`  (lines 369–388)

```
async def list(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -> tuple[S
```

**Purpose**: Returns scheduled tasks that match optional filters. This is the basic read path for task definitions.

**Data flow**: It receives optional filters for conversation, names, visible member, owner inclusion, and limit. It asks `_listing` to build the query, executes it in a transaction, converts each returned row with `_task`, and returns a tuple of `ScheduledTask` objects.

**Call relations**: User-facing or object-facing code calls this when it needs task rows. `ScheduleStore.list_reported` builds on it when it also needs conversation visibility details.

*Call graph*: calls 2 internal fn (_listing, _task); called by 1 (list_reported).


##### `ScheduleStore.list_reported`  (lines 390–424)

```
async def list_reported(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -
```

**Purpose**: Lists scheduled tasks together with the audience and surface label of the conversation they report into. This is useful for screens or APIs that must decide what a member is allowed to see.

**Data flow**: It receives the same filters as `list`. It first gets the matching tasks, then asks the extension context for live facts about the referenced conversations, and returns `ListedTask` objects for tasks whose conversations still exist.

**Call relations**: This method calls `ScheduleStore.list` for the task rows, then enriches them with conversation facts from the context. It creates `ListedTask` objects so callers can make visibility decisions using both task data and conversation data.

*Call graph*: calls 1 internal fn (list); 1 external calls (__init__).


##### `ScheduleStore.claim_due`  (lines 426–482)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: Lets a background worker claim a small batch of due tasks so it can fire them without another worker doing the same work. It also deletes expired tasks that are free to remove.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of tasks. It creates a unique claim ID, deletes claimable expired rows, selects the oldest claimable due non-paused rows up to the limit, stamps them with the claim and lease expiry, and returns them as `ScheduledTask` objects.

**Call relations**: The scheduled-task runner calls this during polling. It uses `_expired` and `_claim_available` to match the workspace-candidate logic, and `_task` to turn the claimed rows into work items.

*Call graph*: calls 3 internal fn (_claim_available, _expired, _task); 6 external calls (timedelta, delete, not_, select, update, uuid4).


##### `ScheduleStore.claim_holds`  (lines 484–515)

```
async def claim_holds(self, task: ScheduledTask) -> bool
```

**Purpose**: Checks whether a worker still owns the exact task it claimed before it fires the task. This reduces the chance of firing a task that was cancelled or edited during the lease window.

**Data flow**: It receives a claimed `ScheduledTask`. If the task has no claim ID, it raises an error. Otherwise it re-reads the matching row under a database lock and checks the workspace, ID, claim, conversation, agent, name, and schedule; it returns `true` only if that exact row still exists.

**Call relations**: `ScheduledTaskRunner._fire` calls this immediately before invoking the task. It uses a database select with a lock so the runner can make a final safety check before delivering the scheduled prompt.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `ScheduleStore.retire_if_expired`  (lines 517–531)

```
async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool
```

**Purpose**: Deletes a claimed task if its expiry time has passed before it is invoked. This prevents an overdue worker from firing a task that should no longer exist.

**Data flow**: It receives a claimed task and the current time. If the task has no claim, it raises an error; if it has no expiry or expires in the future, it returns `false`. If it is expired, it deletes the matching claimed row and returns `true`.

**Call relations**: `ScheduledTaskRunner._fire` calls this during the fire flow. It is a last expiry gate after claiming but before actual invocation.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `ScheduleStore.reschedule`  (lines 533–563)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: Marks a claimed task as having run and schedules its next run. It also releases the claim so the task can be claimed again in the future.

**Data flow**: It receives the claimed task, the next run time, the last run time, and optionally the turn ID created by the fire. It updates the matching claimed row with the new timing data, clears the claim fields, records the turn ID if present, and returns whether a row was actually updated.

**Call relations**: `ScheduledTaskRunner._fire` calls this after a task has fired. It uses a database update to advance only the row still owned by the current claim.

*Call graph*: called by 1 (_fire); 1 external calls (update).


##### `ScheduleStore.inspect`  (lines 565–569)

```
async def inspect(self, expected: ScheduledTask) -> TaskInspection | None
```

**Purpose**: Gets the live status details for one scheduled task. It is a convenience wrapper around the multi-task inspection path.

**Data flow**: It receives one expected task, calls `inspect_many` with that task in a one-item tuple, and returns the matching `TaskInspection` if present. If the task is gone or no longer matches, it returns `None`.

**Call relations**: Status-rendering code can call this for a single task. It hands the real work to `ScheduleStore.inspect_many` so the single-task and batch paths stay consistent.

*Call graph*: calls 1 internal fn (inspect_many).


##### `ScheduleStore.inspect_many`  (lines 571–607)

```
async def inspect_many(self, expected: tuple[ScheduledTask, ...]) -> dict[UUID, TaskInspection]
```

**Purpose**: Gets live status details for several scheduled tasks at once, including their latest run outcome when available. This supports efficient status displays.

**Data flow**: It receives expected scheduled tasks. It queries current rows in the same workspace and current agent, keeps only rows that still match the expected name and conversation, gathers any last turn IDs, asks the context for those turn outcomes, and returns a dictionary from task ID to `TaskInspection`.

**Call relations**: `ScheduleStore.inspect` calls this for one task, and other code can use it directly for batches. It uses `object_agent_id` for scoping, `_utc` and `_utc_opt` for timestamp cleanup, and `turn_outcomes` from the context to attach the latest run status and response text.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 1 (inspect); 3 external calls (__init__, select, object_agent_id).

## 📊 State Registers Touched

- `reg-workspace-membership` — The roster of workspaces, members, admins, seats, and which people belong where.
- `reg-agent-directory` — The saved assistants in each workspace and their settings, such as model behavior, sandbox size, and internet access.
- `reg-extension-pack-registry` — The selected packs and loaded extensions that decide which features, tools, routes, jobs, and backends exist.
- `reg-model-catalog` — The lookup table of available AI models, providers, routing details, capabilities, and pricing metadata.
- `reg-tool-catalog` — The runtime menu of tools the agent may call, including built-ins and extension-provided tools.
- `reg-connection-grants` — Connected third-party accounts and permissions saying which agents may use which external accounts.
- `reg-conversation-transcript` — The durable history of conversations, messages, speakers, titles, context, and results.
- `reg-turn-queue` — The durable waiting line and status record for each unit of agent work, from queued to running to finished or failed.
- `reg-background-job-schedule` — The durable list of background jobs and dispatch rules used to retry and run work outside user requests.
- `reg-scheduled-automation` — Future and repeating tasks, pauses, wakeups, and their last-run state for long-running automation.
- `reg-source-sync-state` — External source records, sync cursors, saved pages, deletion markers, and retry or backoff status.
- `reg-memory-store` — Saved facts, memory pages, confidence, provenance, and audience labels that let the assistant remember useful context.
- `reg-automation-objectives` — Durable objectives, steps, monitors, checks, blockers, and evidence for work that continues across turns.
- `reg-accounting-ledger` — Usage, cost, spend limits, prepaid balances, billing exports, and price versions for workspace spending.
- `reg-conversation-slots` — Side-panel data shown beside a conversation, such as tasks, sources, sites, automations, and workspace changes.
- `reg-coding-review-state` — Coding review inboxes, review runs, source bindings, and conversation links used by the code-review workflow.
- `reg-self-improvement-state` — Offline replay, failure-analysis, prompt-experiment, and governed update-proposal state used by self-improvement jobs.
- `reg-prompt-version-state` — Prompt-render metadata such as system-prompt digests, contributed sections, cutoff/version information, and replay identifiers attached to turns for change detection and evaluation.
- `reg-provider-client-pools` — Per-process reusable transport/client state for external providers such as model APIs, search and embedding services, connector brokers, browser providers, billing services, and related retry or throttle windows.
- `reg-source-trigger-state` — Durable source-change trigger subscriptions and wakeup markers that connect synced-record updates to conversations, reviews, monitors, or automation resumes.
- `reg-payment-provider-state` — Stripe or billing-provider setup state such as customer identifiers, checkout/payment-session progress, and subscription or purchase linkage for workspaces.
