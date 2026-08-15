# Monitors and source-change wakeups  `stage-18.1`

This stage is behind-the-scenes support for waking a conversation when the outside world changes. Instead of making the agent keep checking manually, it sets up watches and trigger records, like alarm clocks tied to real events.

The monitor tool is the front door. When an agent asks to watch something, it runs a shell command once inside the conversation’s safe workspace. This first run proves the command works and records the starting result, so later checks have something to compare against. The monitor kind file teaches the system how monitors are displayed, inspected, and stopped. It is the control panel, not the creator.

The monitor runner is the timer-driven worker. It wakes up on a schedule, reruns saved monitor commands, compares the new output with the baseline, and notifies the agent if the output changes, failures repeat, or the deadline is reached.

Source triggers are similar wake-up rules for shared project sources. They record which conversations care about which source changes, so those conversations can be resumed when the shared material is updated.

## Files in this stage

### Scheduled monitors
Monitor support covers inspecting and stopping saved watches, running scheduled probes, and creating new live watches through the chat tool.

### `extensions/monitors/ufo_ext_monitors/monitor_kind.py`

`domain_logic` · `request handling`

A monitor is like a reminder with eyes: it periodically checks the output of a command, compares it with an initial “baseline,” and later wakes the agent if something important changes. This file defines how those monitors appear as UFO objects that users and agents can list, read, and delete.

The central idea is that arming a monitor is special. The system must run the command once inside an active conversation to record the first result. Because that needs a live chat turn, this object kind refuses normal “apply” creation and tells callers to use the monitor tool instead.

Once monitors exist, this file provides the object-facing view of them. It lists currently armed monitors, adds helpful summary fields such as the watched conversation, next probe time, deadline, owner email, and whether the monitor belongs to the current member. It can also return one monitor’s full specification, plus a link back to the conversation where its report will land.

Deletion means “stop watching.” Before disarming, the file checks that the monitor still matches the object being deleted, so a stale request cannot accidentally stop a different monitor with the same name after data changed. Visibility is based on who can read the watched conversation, with creator and admin rules supplied by the broader object system.

#### Function details

##### `_owner`  (lines 53–58)

```
def _owner(row: Monitor) -> GeneratedObjectOwner
```

**Purpose**: Builds the ownership record for a monitor, saying who created it, who it is shared with, and which exact monitor row it refers to. This lets the object system decide who can see or act on the monitor.

**Data flow**: It takes a monitor database row as input. It reads the creator member ID, audience, and monitor ID, converts the audience into a shared/private ownership fact, and returns a GeneratedObjectOwner that represents that ownership.

**Call relations**: When MonitorObjects._member_rows is preparing monitor list entries, it calls this helper for each row so every listed monitor carries the correct ownership information.

*Call graph*: called by 1 (_member_rows); 2 external calls (__init__, subject_shared).


##### `_require_ext`  (lines 61–64)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure the monitor code has the extension context it needs to reach monitor storage. If that context is missing, it stops immediately with a clear error instead of failing later in a confusing way.

**Data flow**: It receives an optional ExtensionContext. If one is present, it returns it unchanged; if not, it raises a RuntimeError explaining that this monitor kind needs the scheduled-tasks extension context.

**Call relations**: MonitorObjects._member_rows, MonitorObjects._delete_owned, and MonitorObjects._find call this before creating a MonitorStore, because the store cannot work without the extension context.

*Call graph*: called by 3 (_delete_owned, _find, _member_rows).


##### `MonitorObjects._member_rows`  (lines 78–97)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Creates the rows shown when a member lists monitors. Each row is a compact, readable summary of one currently armed watch.

**Data flow**: It receives the extension context and the current member ID. It loads all armed monitors from MonitorStore, looks up owner email addresses, then turns each monitor into an OwnedRow containing its name, summary, owner, watched conversation, next probe time, deadline, owner email, and whether it belongs to the current member.

**Call relations**: This is used by the object listing flow for the monitor kind. It relies on _require_ext to access storage, _owner to describe ownership, owner_emails to make creator IDs human-readable, and OwnedRow to package each list result.

*Call graph*: calls 2 internal fn (_owner, _require_ext); 3 external calls (__init__, __init__, owner_emails).


##### `MonitorObjects._member_object`  (lines 99–125)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[MonitorSpec] | None
```

**Purpose**: Returns the detailed view of one monitor when someone asks to get it by name. It includes the monitor’s command, timing, reason, timestamps, and a link to the conversation it reports into.

**Data flow**: It receives the extension context, object name, expected owner record, and current member ID. It finds a monitor with that name, confirms it is the same generation named by the owner record, and then returns an ObjectDetail containing a MonitorSpec and a reports_to link. If the monitor is missing or no longer matches, it returns None.

**Call relations**: This is part of the object get flow. It asks MonitorObjects._find to locate the live monitor, then packages the result using MonitorSpec, ObjectDetail, ObjectLink, and ObjectRef so the wider object system can display it.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `MonitorObjects._status`  (lines 127–142)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the changing runtime state of a monitor, such as when it was armed, how many probes have run, how many failures happened, and a short excerpt of the baseline output.

**Data flow**: It receives a tool context, monitor name, and expected owner. It finds the matching monitor, checks that its ID still matches the owner generation, and then returns a dictionary of status values. Time values are converted to text, missing last-probe time stays null, and the baseline is shortened so status output does not become huge.

**Call relations**: This is used when tool or object code needs live status for a monitor. It depends on MonitorObjects._find to fetch the row, then returns plain JSON-friendly values for the caller to show.

*Call graph*: calls 1 internal fn (_find).


##### `MonitorObjects._apply_owned`  (lines 144–152)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: MonitorSpec, old: MonitorSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Refuses attempts to create or update a monitor through the normal object apply path. This protects the monitor workflow, because arming requires a real probe run inside a live chat turn.

**Data flow**: It receives the requested name, desired monitor spec, any old spec, and owner information, but does not use them to change storage. Instead, it raises VerbNotSupported with a message telling the caller to use the monitor tool.

**Call relations**: This function is invoked by the broader object mutation flow when someone tries to apply a monitor manifest. It deliberately hands back an error rather than forwarding anything to MonitorStore.

*Call graph*: 1 external calls (__init__).


##### `MonitorObjects._delete_owned`  (lines 154–159)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Stops an armed monitor when its owner or a workspace admin deletes it. In this system, deleting the object means disarming the watch so it will not probe or fire again.

**Data flow**: It receives the tool context, monitor name, and expected owner. It finds the current monitor row, checks that the row still has the same ID as the owner generation, then asks MonitorStore to disarm it. If the row is missing, changed, or cannot be disarmed, it raises an error saying the monitor changed while stopping.

**Call relations**: This is called by the object delete flow after access rules allow deletion. It uses MonitorObjects._find to locate the watch and _require_ext plus MonitorStore to perform the actual disarm operation.

*Call graph*: calls 2 internal fn (_find, _require_ext); 1 external calls (__init__).


##### `MonitorObjects._find`  (lines 161–165)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> Monitor | None
```

**Purpose**: Looks up one armed monitor by its object name. It is a small shared helper so get, status, and delete all search in the same way.

**Data flow**: It receives an optional extension context and a monitor name. It requires a valid context, loads all armed monitors from MonitorStore, scans for the first row whose name matches, and returns that monitor row or None if no armed monitor has that name.

**Call relations**: MonitorObjects._member_object, MonitorObjects._status, and MonitorObjects._delete_owned call this whenever they need the current stored monitor before reading details, reporting status, or disarming it.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_delete_owned, _member_object, _status); 1 external calls (__init__).


### `extensions/monitors/ufo_ext_monitors/monitor_runner.py`

`orchestration` · `recurring scheduled job`

A monitor is like a smoke alarm for a command: it remembers what the command output looked like when the watch was armed, then checks again on a schedule. This file is the worker that performs those checks. On each run, it asks the monitor store for watches that are due, using a short lease so two overlapping workers do not check the same watch at the same time.

For each claimed monitor, it first checks whether the monitor has reached its deadline. If so, it fires the monitor and retires it. Otherwise it verifies that the member who created the monitor is still allowed to act in the workspace. If that member no longer has a seat, the probe is skipped rather than run with stale access.

When a probe does run, the runner compares the command output with the saved baseline. Same output means a quiet tick. Different output means the monitor fires. A non-zero command exit code counts as a failure, and the third failure fires the monitor. If the sandbox or terminal is unavailable, the tick is counted as skipped.

When firing, the runner posts a carefully formatted message back to the agent and then retires the monitor. It uses an idempotency key, meaning that if a crash happens mid-fire, retrying should not create a duplicate notification. Very large output is written to a file and referenced by path.

#### Function details

##### `MonitorRunner.run`  (lines 54–64)

```
async def run(self) -> None
```

**Purpose**: This is the top-level sweep for the monitor worker. It finds monitors that are due now, checks each one once, and reports if any of those checks failed unexpectedly.

**Data flow**: It starts with the extension context already stored on the runner. It creates a monitor store, reads the current time, asks the store to claim due monitors under a lease, and sends each claimed monitor to `_tick`. It collects the names of monitors whose tick raised an error, and if any failed, it raises one combined error at the end.

**Call relations**: This is the entry point used by the extension's recurring job. It does not decide the detailed outcome of a monitor itself; it delegates each claimed row to `MonitorRunner._tick` and only coordinates the sweep and error summary.

*Call graph*: calls 1 internal fn (_tick); 2 external calls (__init__, now).


##### `MonitorRunner._tick`  (lines 66–111)

```
async def _tick(self, store: MonitorStore, row: Monitor) -> None
```

**Purpose**: This checks one claimed monitor and decides what should happen next: skip it, record a quiet check, record a failed check, or fire and retire it. It is the main decision point for a single monitor tick.

**Data flow**: It receives the monitor store and one monitor row. It reads the monitor's interval, deadline, saved baseline, failure count, command, and creator. It may run the probe command through the context, compare the result to the baseline, and then write the new state back through the store. The outcome is a store update, a fired agent message, or a skipped tick; it does not return a separate value.

**Call relations**: `MonitorRunner.run` calls this once for each due monitor it has claimed. Inside the tick, it asks `_acts_for_a_seated_member` whether the probe may still run as the creator, uses monitor-store methods to record quiet, failed, or skipped ticks, and calls `_fire` when a deadline, output change, or repeated failure means the monitor should notify the agent.

*Call graph*: calls 5 internal fn (_acts_for_a_seated_member, _fire, failed_tick, quiet_tick, skipped_tick); called by 1 (run); 4 external calls (now, timedelta, capped, stderr_tail).


##### `MonitorRunner._acts_for_a_seated_member`  (lines 113–124)

```
async def _acts_for_a_seated_member(self, row: Monitor) -> bool
```

**Purpose**: This checks whether the monitor is still allowed to run as the member who created it. It prevents a scheduled probe from continuing to use a person's connections after that person has lost their seat or access.

**Data flow**: It receives a monitor row. If the row has no creator member, it immediately allows the probe because it only uses workspace-shared access. If there is a creator, it opens a transaction and asks the seating system whether that member is still admitted. It returns `true` when the probe may run and `false` when the tick should be skipped.

**Call relations**: `MonitorRunner._tick` calls this before running a probe. If it returns false, `_tick` records a skipped tick through the monitor store instead of calling the probe system.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `MonitorRunner._fire`  (lines 126–145)

```
async def _fire(self, store: MonitorStore, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> None
```

**Purpose**: This sends the final monitor notification to the agent and then retires the monitor so it will not keep checking. It is used when a monitor reaches its deadline, detects changed output, or hits the failure threshold.

**Data flow**: It receives the store, monitor row, fire cause, message payload, optional full output spill text, and updated probe count. It first asks the store whether it still holds the right to fire this monitor. If so, it builds the message body, invokes the agent with a stable idempotency key, and then tells the store to retire the monitor.

**Call relations**: `MonitorRunner._tick` calls this whenever the monitor should end with a notification. `_fire` calls `_body` to prepare the text the agent will read, uses the context to invoke the agent, and then calls the monitor store's retire operation to close the watch.

*Call graph*: calls 3 internal fn (_body, claim_holds, retire); called by 1 (_tick).


##### `MonitorRunner._body`  (lines 147–166)

```
async def _body(self, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> str
```

**Purpose**: This builds the text sent to the agent when a monitor fires. It includes the reason for the watch, suggested next steps, metadata, probe counts, and any output that explains why the monitor fired.

**Data flow**: It receives a monitor row, the cause of the fire, a short payload, optional large output, and the probe count. It turns the monitor details into a structured text block, escapes the closing marker so command output cannot fake the end of the message, and wraps probe output with the system's safety wall for untrusted text. If the output is too large and was passed as a spill, it asks `_spilled` to write it to a file and includes the file path.

**Call relations**: `MonitorRunner._fire` calls this just before invoking the agent. `_body` may call `_spilled` when the full command output should live in a file, and it uses the shared `wall` helper so raw command output is clearly separated from instructions.

*Call graph*: calls 1 internal fn (_spilled); called by 1 (_fire); 2 external calls (dumps, wall).


##### `MonitorRunner._spilled`  (lines 168–176)

```
async def _spilled(self, row: Monitor, output: str) -> str
```

**Purpose**: This writes oversized probe output to a conversation file and returns the path to that file. It keeps the fired message readable while still preserving the full output for the agent to inspect.

**Data flow**: It receives the monitor row and the full output text. It checks that file storage is available, creates a timestamped filename under the monitor spill directory, writes the output bytes into the conversation's files area, and returns the written path. If file storage is not available, it raises an error because there is nowhere safe to put the large output.

**Call relations**: `MonitorRunner._body` calls this only when a monitor fire needs to reference output larger than the inline cap. The returned path is inserted into the body that `_fire` will send to the agent.

*Call graph*: called by 1 (_body); 1 external calls (now).


### `extensions/monitors/ufo_ext_monitors/monitor_tool.py`

`orchestration` · `tool call during a live turn; later enables scheduled monitoring`

This file solves a waiting problem. Instead of keeping an agent active while it waits for a CI job, deployment, or other external state to change, the agent can arm a durable monitor and end its turn. The monitor is like setting a kitchen timer that also peeks at the oven: it stays quiet while nothing changes, then rings once when something important happens.

The main input shape is `MonitorInput`. It asks for a short name, the shell command to run, how often to check, the deadline, what message to show now, and instructions for the future turn when the monitor fires. The command's standard output is important: later checks compare their output byte-for-byte against the first run, so commands should avoid changing timestamps or counters unless those changes matter.

The `monitor` function is the heart of the file. It first checks that the scheduled-tasks extension is available, then looks up already armed monitors for this conversation. It refuses to arm too many monitors or duplicate names. Next it runs the probe command immediately in the current sandbox. If the command fails, no monitor is saved; this prevents a broken watch from quietly failing later. If it succeeds, the output is trimmed to a safe size, stored as the baseline, and the monitor row is persisted with its timing, reason, next steps, and metadata. The tool then returns a directive telling the agent to reply with the provided message and end the turn.

#### Function details

##### `_require_ext`  (lines 76–79)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This helper makes sure the monitor tool has the extension context it needs to save scheduled work. Without that context, the tool cannot persist a monitor for later.

**Data flow**: It receives an optional extension context. If the context is present, it returns it unchanged. If it is missing, it raises an error explaining that the monitor tool requires the scheduled-tasks extension context.

**Call relations**: The `monitor` function calls this before creating a `MonitorStore`. This is the gate that prevents the rest of the arming flow from running without the storage and scheduling support it depends on.

*Call graph*: called by 1 (monitor).


##### `_refusal`  (lines 82–83)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: This helper builds a standard error result when the tool declines to arm a monitor. It keeps all refusal responses in the same shape for the caller.

**Data flow**: It receives a plain text explanation. It wraps that text in a text content object, then wraps that in a tool result marked as an error. The result goes back to the agent instead of saving any monitor.

**Call relations**: The `monitor` function calls this whenever arming should stop safely: when the conversation already has too many monitors, when the requested name is already used, or when the initial probe command fails.

*Call graph*: called by 1 (monitor); 2 external calls (__init__, __init__).


##### `monitor`  (lines 86–134)

```
async def monitor(ctx: ToolContext, args: MonitorInput) -> ToolResult
```

**Purpose**: This is the tool handler that arms a new monitor. It checks limits, runs the first probe command immediately, saves the successful watch, and returns the starting state so the agent knows what it is watching.

**Data flow**: It receives the current tool context and the user's monitor settings. It reads the conversation and agent information from the context, checks existing monitors from storage, runs the requested shell command in the sandbox, and either returns an error result or saves a new monitor row. On success, it returns text containing a directive to end the turn plus a JSON payload with the monitor name, baseline output, deadline, interval, reason, and next steps.

**Call relations**: This function is the main flow for the file and is registered as the handler for `MONITOR_TOOL`. It calls `_require_ext` to get the needed extension context, uses `MonitorStore` to read and save monitor records, calls `_refusal` for safe early exits, runs the sandbox probe before saving anything, and finally constructs the tool result that tells the agent what to say and that the turn should end.

*Call graph*: calls 2 internal fn (_refusal, _require_ext); 9 external calls (__init__, __init__, __init__, now, timedelta, dumps, capped, qualified_name, stderr_tail).


### Source-change triggers
Source triggers record which conversations should wake when shared source state changes.

### `extensions/sources/ufo_ext_sources/triggers.py`

`domain_logic` · `request handling and background alert sweep`

A source trigger is like a standing reminder: “when this source changes, tell this conversation.” This file defines the database table for those reminders and a store, `SourceTriggerStore`, that is the safe way to work with them.

The important boundary here is the workspace. A workspace is the shared area where sources and conversations live. The database connection this code receives is not automatically limited to one workspace, so every query explicitly includes the current `workspace_id`. Without that, one workspace could accidentally see or change another workspace’s triggers.

When a trigger is created, the file checks that the target conversation belongs to the agent currently running. That prevents an agent from setting up a trigger that wakes a conversation owned by someone else. It also prevents duplicates: the same conversation cannot watch the same source binding twice.

The store supports several use cases. A source-change alert asks `waking` for every trigger watching a binding, across all agents in the workspace. A user-facing screen asks `list_reported` for this agent’s triggers, enriched with the conversation’s audience and display label so the UI can decide what the member is allowed to see. Cleanup paths can remove one exact trigger or remove all triggers tied to a deleted source binding.

#### Function details

##### `_utc`  (lines 85–86)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: This helper makes sure a timestamp has timezone information. If the database gives back a plain timestamp, it treats it as UTC, the standard shared time zone.

**Data flow**: It receives a `datetime` value. If the value already says what timezone it belongs to, it returns it unchanged. If it has no timezone, it adds UTC and returns the adjusted timestamp.

**Call relations**: `_trigger` calls this when turning database rows into `SourceTrigger` objects, so the rest of the code can rely on trigger times being timezone-aware.

*Call graph*: called by 1 (_trigger); 1 external calls (replace).


##### `_trigger`  (lines 89–104)

```
def _trigger(row: sa.RowMapping) -> SourceTrigger
```

**Purpose**: This turns one database row into a `SourceTrigger` value that the rest of the code can use safely. It also rejects any unknown delivery mode instead of silently accepting bad stored data.

**Data flow**: It receives a row read from the `source_trigger` table. It checks that `delivery` is either `current` or `per_page`, normalizes the timestamps through `_utc`, and builds a `SourceTrigger` object containing the row’s important fields. If the delivery value is not recognized, it raises an error.

**Call relations**: `create`, `waking`, and `list_reported` all call `_trigger` after reading rows from the database. It is the shared translation step between raw database data and the in-memory trigger objects used by alert delivery and listing code.

*Call graph*: calls 1 internal fn (_utc); called by 3 (create, list_reported, waking); 1 external calls (__init__).


##### `SourceTriggerStore.workspace_id`  (lines 114–115)

```
def workspace_id(self) -> UUID
```

**Purpose**: This gives the store the current workspace identifier from its extension context. It keeps all database work tied to the workspace the current operation belongs to.

**Data flow**: It reads `workspace_id` from `self.ctx` and returns it. It does not change anything.

**Call relations**: The store’s database methods use this property when filtering rows. That is how create, delete, list, and wake-up reads stay inside the correct workspace.


##### `SourceTriggerStore.create`  (lines 117–166)

```
async def create(self, conversation_id: UUID, binding: str, delivery: SourceTriggerDelivery, created_by_member_id: UUID | None=None) -> SourceTrigger
```

**Purpose**: This creates a new rule saying that one conversation wants updates for one source binding. It checks that the current agent really owns the conversation, and it gives a clear error if the same conversation is already watching that binding.

**Data flow**: It receives a conversation ID, a source binding name, a delivery mode, and optionally the member who requested it. It reads the current agent ID and verifies the conversation belongs to that agent. Then it inserts a new trigger row with a fresh UUID and timestamps. If the insert succeeds, it converts the returned row into a `SourceTrigger`; if a duplicate already exists, it raises a `ValueError`.

**Call relations**: This is used when a member or agent asks to start watching a source. It calls `object_agent_id` to know who is executing, uses `uuid4` to create the trigger ID, writes through the database transaction from the context, and hands the saved row to `_trigger` for conversion.

*Call graph*: calls 1 internal fn (_trigger); 2 external calls (object_agent_id, uuid4).


##### `SourceTriggerStore.remove`  (lines 168–183)

```
async def remove(self, expected: SourceTrigger) -> None
```

**Purpose**: This removes one specific trigger, but only if it still matches the trigger the caller expected. That protects against deleting the wrong rule if something changed in the meantime.

**Data flow**: It receives an expected `SourceTrigger`. It checks that the trigger’s agent matches the current executing agent. Then it deletes the row only when the workspace, trigger ID, agent ID, conversation ID, and binding all match. If no row was deleted, it reports that the trigger changed while removal was attempted.

**Call relations**: This is used when code wants to stop one known trigger. It calls `object_agent_id` for the current agent and SQLAlchemy’s delete builder to remove the row. Unlike `remove_binding`, it is careful and exact because it is removing a single user-visible rule.

*Call graph*: 2 external calls (delete, object_agent_id).


##### `SourceTriggerStore.remove_binding`  (lines 185–194)

```
async def remove_binding(self, binding: str) -> None
```

**Purpose**: This removes every trigger in the workspace for one source binding. It is used when the source itself is gone, so no conversation should keep watching it.

**Data flow**: It receives a binding string. It opens a transaction and deletes all rows in the current workspace whose binding matches that string. It does not return the deleted rows.

**Call relations**: This is a cleanup operation for source removal. It uses SQLAlchemy’s delete builder directly and intentionally spans all agents in the workspace, because a shared source can have subscribers from different agents.

*Call graph*: 1 external calls (delete).


##### `SourceTriggerStore.waking`  (lines 196–209)

```
async def waking(self, binding: str) -> tuple[SourceTrigger, ...]
```

**Purpose**: This finds all trigger rules that should wake up when a particular source binding changes. It is the read path used by the alert sweep that delivers source updates.

**Data flow**: It receives a binding string. It selects all matching rows in the current workspace, ordered by creation time and ID for stable processing. It converts each row into a `SourceTrigger` and returns them as a tuple.

**Call relations**: The alerting flow calls this when a source has new data. It uses SQLAlchemy’s select builder to read the table, then calls `_trigger` for each row before handing the triggers back to whatever will wake the conversations.

*Call graph*: calls 1 internal fn (_trigger); 1 external calls (select).


##### `SourceTriggerStore.list_reported`  (lines 211–245)

```
async def list_reported(self, *, conversation_id: UUID | None=None) -> tuple[ListedTrigger, ...]
```

**Purpose**: This lists the current agent’s triggers for a member-facing view, optionally limited to one conversation. It also adds the conversation’s audience and display label so the caller can decide what should be visible to the member.

**Data flow**: It optionally receives a conversation ID filter. It reads trigger rows in the current workspace for the current agent, converts them into `SourceTrigger` objects, then asks the context for live conversation facts for those conversations. It returns `ListedTrigger` objects pairing each trigger with its conversation audience and surface label, skipping triggers whose conversation no longer exists.

**Call relations**: This is used by listing or reporting surfaces, not by the alert sweep. It calls `object_agent_id` to restrict the list to the current agent, uses SQLAlchemy to read rows, calls `_trigger` to build trigger objects, and wraps them in `ListedTrigger` after fetching conversation facts from the context.

*Call graph*: calls 1 internal fn (_trigger); 3 external calls (__init__, select, object_agent_id).
