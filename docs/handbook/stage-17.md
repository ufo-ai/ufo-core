# Scheduled and Background Work Execution  `stage-17`

This stage is the system’s after-startup “night crew.” Once the service is running, it looks for work that should happen later or repeat on a schedule, then runs it safely inside the correct workspace so different customers or projects do not interfere with each other.

Scheduled tasks use cron rules, a simple five-part time pattern, to decide their next run. The schedules storage records recurring prompts, while the runner claims due tasks, sends them into the right conversation, and reschedules them. Pauses work similarly: they store a conversation wake-up time, then resume it once unless a human already replied. Shared firing keys keep task names and history aligned.

Monitors are like alarm clocks watching the outside world. The monitor tool creates a watch and records its first result. The monitor runner repeats the check and alerts the agent only on change, repeated failure, or deadline. Monitor storage keeps these watches safe.

Runtime helpers find which workspaces have waiting jobs, queue one execution per workspace, and run each job in extension context. Other background jobs deliver missed child-agent results, write report digests, test self-improvement proposals, and clean up old homepage bindings.

## Files in this stage

### Monitor watches
One-time monitor setup and recurring monitor ticks share storage for safely detecting changes, repeated failures, deadlines, and retirement.

### `extensions/monitors/ufo_ext_monitors/monitor_tool.py`

`orchestration` · `request handling`

This file solves a waiting problem. Instead of making an agent keep checking a command over and over, it lets the agent arm a durable monitor and end its turn. Think of it like setting an alarm that watches a thermometer: it stays quiet while the reading is the same, then rings once when the reading changes or the time limit is reached.

The tool takes a monitor name, a shell command to run, a checking interval, a deadline, and instructions for what to do when the monitor fires. Before saving anything, it runs the command immediately in the conversation's sandbox, which is the isolated environment where commands execute. This is important: if the command is broken, the tool fails right away and nothing is stored. That prevents an unattended background watch from failing later without useful feedback.

If the command succeeds, its standard output is stored as the baseline. Future probe runs compare their output against that baseline byte for byte, so callers are warned to make the command output stable, for example by removing timestamps or counters. The file also enforces safety limits, such as a maximum number of armed monitors per conversation and unique monitor names. When the monitor is armed successfully, the result tells the agent to show the provided response and end the turn.

#### Function details

##### `_require_ext`  (lines 73–76)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This helper makes sure the tool has the extension context it needs to store scheduled monitor data. If that context is missing, it stops immediately with a clear error instead of failing later in a more confusing way.

**Data flow**: It receives a possible extension context. If the value is present, it returns it unchanged. If the value is missing, it raises an error saying the monitor tool requires the scheduled-tasks extension context.

**Call relations**: The main `monitor` function calls this before creating a `MonitorStore`. This is the gatekeeper step: the monitor cannot read or write durable watch records until this check passes.

*Call graph*: called by 1 (monitor).


##### `_refusal`  (lines 79–80)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: This helper builds a tool result that says the monitor request was refused. It is used for expected user-facing problems, such as too many monitors, a duplicate name, or a probe command that failed.

**Data flow**: It receives a plain text explanation. It wraps that text in a text content object, marks the tool result as an error, and returns that result to the caller.

**Call relations**: The main `monitor` function calls this whenever it decides not to arm a monitor. Instead of saving anything or continuing, it hands back a clear failure message through the normal tool result format.

*Call graph*: called by 1 (monitor); 2 external calls (__init__, __init__).


##### `monitor`  (lines 83–130)

```
async def monitor(ctx: ToolContext, args: MonitorInput) -> ToolResult
```

**Purpose**: This is the actual tool action that arms a monitor. It checks whether arming is allowed, runs the probe command once now, saves the successful baseline, and returns instructions telling the agent to reply and end its turn.

**Data flow**: It receives the current tool context and the user's monitor settings. It reads the conversation and agent information from the context, checks existing armed monitors, rejects the request if the cap or name rules are violated, then runs the shell command in the sandbox with a timeout. If the command fails, it returns an error and stores nothing. If it succeeds, it records the command, timing, deadline, baseline output, reason, next steps, metadata, and ownership information in the monitor store. It then returns a tool result containing a directive plus a JSON payload showing what was armed and what baseline output is being watched.

**Call relations**: This function is registered as the handler for the `MONITOR_TOOL`, so it runs when the agent calls the `monitor` tool. It first calls `_require_ext` to get the storage context, then uses `MonitorStore` to look up and create monitor records. When it needs to reject a request, it calls `_refusal`. On success, it formats the saved monitor details into text content and returns them as the tool response.

*Call graph*: calls 2 internal fn (_refusal, _require_ext); 9 external calls (__init__, __init__, __init__, now, timedelta, dumps, capped, qualified_name, stderr_tail).


### `extensions/monitors/ufo_ext_monitors/monitor_runner.py`

`orchestration` · `recurring scheduled monitor tick`

A monitor is like a watch someone sets: “run this command every so often, compare the result with what I saw before, and tell the agent if the result changes or trouble happens.” This file is the worker that wakes up on a schedule and checks the watches that are due.

The runner first claims due monitors with a short lease, which is a temporary hold that stops overlapping runner jobs from checking the same monitor twice. For each claimed monitor, it checks the deadline first. If the deadline has passed, it sends the final monitor message and retires the monitor. Otherwise, it makes sure the member who created the monitor is still allowed to act in the workspace. If that member has lost their seat, the probe is skipped rather than run with old authority.

When a probe does run, the command is executed as the monitor’s creator. A clean result matching the baseline is recorded as quiet. A changed result fires the monitor. A failing command is counted, and the third failure fires it. If the sandbox or terminal is gone, the tick is counted as skipped.

When firing, the file builds a safe message for the agent, optionally writes very large output to a workspace file, invokes the agent using an idempotency key so crashes do not create duplicate fires, and then retires the monitor.

#### Function details

##### `MonitorRunner.run`  (lines 55–65)

```
async def run(self) -> None
```

**Purpose**: This is the top-level pass of the monitor runner. It finds monitors that are due now, claims them so another overlapping run will not also process them, and ticks each one.

**Data flow**: It starts with the extension context stored on the runner. From that it creates a monitor store, reads the current time, asks the store for due monitors under a lease, and sends each claimed monitor into the per-monitor tick logic. If any monitor tick crashes, it remembers the monitor name and error type; after trying all claimed monitors, it raises one combined error if there were failures.

**Call relations**: This is the entry into the file’s workflow. It calls MonitorRunner._tick for each claimed monitor, letting that helper decide whether to probe, skip, fire, or record a quiet/failing tick.

*Call graph*: calls 1 internal fn (_tick); 2 external calls (__init__, now).


##### `MonitorRunner._tick`  (lines 67–112)

```
async def _tick(self, store: MonitorStore, row: Monitor) -> None
```

**Purpose**: This processes one claimed monitor for one scheduled turn. It decides whether the monitor has expired, should be skipped, should run its command, should simply record no change, or should fire and retire.

**Data flow**: It receives the monitor store and one monitor row. It reads the monitor’s interval, deadline, creator, command, baseline, and current streak counts. If the deadline has passed, it fires immediately. If the creator no longer has a seat, it records a skipped tick. If probes are available, it runs the saved command, then uses the result to update the row: terminal loss becomes a skip, nonzero exit codes increase the failure streak or fire after the threshold, identical output becomes a quiet tick, and changed output becomes a fire. The next probe time is set based on when this probe finished, not when the batch began.

**Call relations**: MonitorRunner.run calls this once for each due monitor it claimed. During the tick, it asks MonitorRunner._acts_for_a_seated_member whether the creator may still act, updates the MonitorStore for skipped, failed, or quiet results, and hands off to MonitorRunner._fire when the monitor should notify the agent and end.

*Call graph*: calls 5 internal fn (_acts_for_a_seated_member, _fire, failed_tick, quiet_tick, skipped_tick); called by 1 (run); 4 external calls (now, timedelta, capped, stderr_tail).


##### `MonitorRunner._acts_for_a_seated_member`  (lines 114–126)

```
async def _acts_for_a_seated_member(self, row: Monitor) -> bool
```

**Purpose**: This checks whether a monitor is still allowed to act as the member who created it. It prevents a scheduled background command from continuing to use a person’s authority after that person has lost their workspace seat.

**Data flow**: It receives a monitor row and looks at its creator member id. If there is no creator member, it returns true because the monitor acts only with workspace-shared access. If there is a creator, it opens a database transaction and asks the seats service whether that member is still admitted to the workspace. The result is a simple yes or no.

**Call relations**: MonitorRunner._tick uses this before running a probe, so revoked members’ monitors stop probing and count as skipped. MonitorRunner._fire uses it again before invoking the agent, so a deadline fire for an unseated member still arrives but does not carry that member’s authority.

*Call graph*: called by 2 (_fire, _tick); 1 external calls (__init__).


##### `MonitorRunner._fire`  (lines 128–151)

```
async def _fire(self, store: MonitorStore, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> None
```

**Purpose**: This sends the final monitor notification to the agent and retires the monitor. It is used when a deadline arrives, output changes, or repeated probe failures cross the threshold.

**Data flow**: It receives the store, monitor row, cause, message payload, optional full output to spill to a file, and probe count. First it checks that this runner still holds the claim on the row; if not, it does nothing. It then decides whether the fire may act on behalf of the creator, builds the message body, and invokes the agent with an idempotency key so retrying the same fire does not create a duplicate. If the agent has been archived, it stops quietly. If the invoke succeeds, it marks the monitor retired in the store.

**Call relations**: MonitorRunner._tick calls this whenever a monitor should end. This function calls MonitorRunner._body to create the agent-readable message, calls MonitorRunner._acts_for_a_seated_member to choose safe authority, and finally asks the MonitorStore to retire the monitor after the fire is posted.

*Call graph*: calls 4 internal fn (_acts_for_a_seated_member, _body, claim_holds, retire); called by 1 (_tick).


##### `MonitorRunner._body`  (lines 153–172)

```
async def _body(self, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> str
```

**Purpose**: This builds the text that the agent receives when a monitor fires. It includes the reason, next steps, metadata, counts, and any changed or failed probe output in a form that is safe for the agent to read.

**Data flow**: It receives the monitor row, the fire cause, a payload such as changed output or an error summary, optional large output, and the probe count. It turns the monitor details into a structured text block, JSON-encodes the metadata, and escapes the closing monitor tag if it appears inside the content. If there is large output, it asks MonitorRunner._spilled to write the full text to a file and includes the path. If there is probe output to show, it wraps that output with the core untrusted-output wall so command output cannot pretend to be new instructions.

**Call relations**: MonitorRunner._fire calls this just before invoking the agent. This function may call MonitorRunner._spilled when the full probe output was too large to fit directly in the fire message.

*Call graph*: calls 1 internal fn (_spilled); called by 1 (_fire); 2 external calls (dumps, wall).


##### `MonitorRunner._spilled`  (lines 174–182)

```
async def _spilled(self, row: Monitor, output: str) -> str
```

**Purpose**: This writes oversized probe output to a conversation file and returns the file path. It lets the fire message stay small while still giving the agent access to the complete output.

**Data flow**: It receives the monitor row and the full output text. It checks that the extension context has file-writing support, creates a timestamped filename under the monitors spill directory, writes the output bytes into the conversation’s runtime files, and returns the path that was written.

**Call relations**: MonitorRunner._body calls this only when a monitor fire has output beyond the inline cap. The returned path is inserted into the body so the agent can find the full output after MonitorRunner._fire sends the message.

*Call graph*: called by 1 (_body); 1 external calls (now).


### `extensions/monitors/ufo_ext_monitors/monitors.py`

`domain_logic` · `cross-cutting: used when monitors are created, listed, claimed by the runner, updated after probes, fired, or stopped`

A monitor is like setting an alarm that keeps checking the same thing until something important happens. This file owns the monitor table for the extension: the command to run, when to run it next, what output counts as normal, how many quiet or failed checks have happened, and who currently has permission to work on it.

The most important job here is safety. Many workers may wake up at the same time looking for due monitors, so the file uses a short-lived claim, also called a lease, to make sure two workers do not run the same watch at once. It also always filters by workspace, because the database connection is not automatically limited to one workspace.

The `MonitorStore` class is the main doorway. It can list armed monitors, insert a new one, claim due work, record what happened after a probe, check whether a claim still belongs to the worker, retire a monitor after it fires, or disarm it when a user says to stop. Small helper functions keep names unique, trim oversized command output, and convert database rows into clean `Monitor` objects with timezone-aware times. Without this file, the monitor runner would not have a reliable memory of what is being watched or a safe way to divide work.

#### Function details

##### `qualified_name`  (lines 73–84)

```
def qualified_name(conversation_id: UUID, slug: str) -> str
```

**Purpose**: Builds the stored name for a monitor by combining a short prefix from the conversation ID with the human-chosen slug. This makes names unique across a workspace even when different conversations use the same friendly name.

**Data flow**: It receives a conversation UUID and a slug string. It takes the first part of the conversation’s hexadecimal ID, joins it to the slug with a dash, and returns that combined name.

**Call relations**: This is a naming helper for the monitor system. Other code can use it before storing or referring to a monitor so that the database’s workspace-wide uniqueness rule is satisfied.


##### `capped`  (lines 87–97)

```
def capped(output: str) -> str
```

**Purpose**: Limits probe output to a safe size before it is stored or compared. This prevents a command that prints huge output from making monitor records or fire messages too large.

**Data flow**: It receives the full text output from a probe. If it is already small enough, it returns it unchanged. If it is too large, it keeps the beginning and end, inserts a message saying how many bytes were omitted, and returns the shortened text.

**Call relations**: This is a utility for preparing probe output. The supplied call graph does not show a direct caller in this file, but it belongs to the monitor flow because monitor probes compare and report bounded output rather than unlimited raw command text.


##### `stderr_tail`  (lines 100–105)

```
def stderr_tail(stderr: str) -> str
```

**Purpose**: Keeps only the end of a failed command’s error output. The end of stderr, the stream where shell commands usually explain errors, is often the most useful part.

**Data flow**: It receives stderr text. If it fits within the allowed size, it returns it as-is. If it is too long, it returns only the final bytes, decoded back into text.

**Call relations**: This is a probe-reporting helper. The supplied call graph does not show a direct caller here, but it is meant for the failure path where the monitor runner needs a compact explanation of what went wrong.


##### `_aware`  (lines 137–138)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Ensures a datetime has timezone information. This matters because different databases can return stored times with or without timezone markers.

**Data flow**: It receives a datetime. If the datetime already has timezone information, it returns it unchanged. If it does not, it marks it as UTC and returns the corrected value.

**Call relations**: `_row` calls this whenever it turns database data into a `Monitor`. That way the rest of the monitor code can compare times without repeatedly fixing timezone details.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 141–168)

```
def _row(row: sa.RowMapping) -> Monitor
```

**Purpose**: Turns one database row into a `Monitor` object that the rest of the Python code can use. It is the single conversion point for monitor reads.

**Data flow**: It receives a row mapping from the database. It copies each stored field into a `Monitor`, converting deadline, probe, creation, and update times through `_aware`. The result is a clean in-memory monitor value.

**Call relations**: `MonitorStore.armed`, `MonitorStore.arm`, and `MonitorStore.claim_due` all call `_row` after reading rows from the database. This keeps all monitor objects shaped and normalized the same way before they are handed to callers.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 171–172)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a monitor is free to be claimed. A monitor is free if nobody has claimed it or if the previous claim has expired.

**Data flow**: It receives the current time. It produces a SQL condition that checks whether `claimed_by` is empty or `claim_expires_at` is earlier than that time.

**Call relations**: `due_monitor_workspaces.due` uses this to find workspaces with claimable due work. `MonitorStore.claim_due` uses the same rule when actually leasing specific monitors, so discovery and claiming agree.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_due`  (lines 175–176)

```
def _due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a monitor needs attention now. A monitor is due if its next scheduled probe time has arrived or its final deadline has arrived.

**Data flow**: It receives the current time. It produces a SQL condition that checks whether `next_probe_at` or `deadline_at` is at or before that time.

**Call relations**: `due_monitor_workspaces.due` uses this to find candidate workspaces for the runner. `MonitorStore.claim_due` uses it again when selecting the exact monitor rows to lease.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_monitor_workspaces`  (lines 179–196)

```
def due_monitor_workspaces() -> WorkspaceCandidates
```

**Purpose**: Gives the background job system a way to find workspaces that may have monitor work ready. It does not claim the work itself; it only identifies where work might exist.

**Data flow**: It defines a small query-building function that looks for distinct workspace IDs with due, claimable monitors whose agents are still live. It passes that query builder to `owner_candidates`, which wraps it in the job system’s workspace-candidate mechanism.

**Call relations**: The monitor runner’s scheduling layer can call this to decide which workspaces to open. Inside it, the nested `due` function performs the actual database query construction.

*Call graph*: 1 external calls (owner_candidates).


##### `due_monitor_workspaces.due`  (lines 184–194)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query for workspaces that have monitors ready to run. It narrows the search to monitors that are due, not locked by a live claim, and attached to a live agent.

**Data flow**: It reads the current UTC time, then creates a SQL select for workspace IDs. It applies the shared due and claim-available conditions and checks that the owning agent is live. The output is a SQL query, not the final list itself.

**Call relations**: This nested function is handed to `owner_candidates` by `due_monitor_workspaces`. It uses `_claim_available`, `_due`, and `agent_is_live` so the job scheduler only wakes workspaces where a runner has a realistic chance to claim work.

*Call graph*: calls 2 internal fn (_claim_available, _due); 3 external calls (now, select, agent_is_live).


##### `MonitorStore.armed`  (lines 205–211)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Monitor, ...]
```

**Purpose**: Lists the currently armed monitors in this store’s workspace. It can list all of them or only those belonging to one conversation.

**Data flow**: It starts with the workspace ID from the extension context and optionally a conversation ID. It queries the monitor table, orders rows by name, converts each row with `_row`, and returns a tuple of `Monitor` objects.

**Call relations**: This is a read operation used by code that needs to show or inspect active watches. It relies on `_row` so callers receive normalized monitor objects instead of raw database rows.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `MonitorStore.arm`  (lines 213–268)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, name: str, audience: str, command: str, interval_minutes: int, deadline_at: datetime, reason: str, next_steps: str, metadata: dict[str, Js
```

**Purpose**: Creates a new armed monitor in the database. This is what records a user or agent’s request to start watching something.

**Data flow**: It receives the monitor’s conversation, agent, name, command, schedule, deadline, explanation, metadata, creator, baseline output, and first probe time. It inserts a new row with fresh counters, no claim, and timestamps, then converts the returned row into a `Monitor`.

**Call relations**: This is the write path for creating monitor records. After inserting with a new UUID, it calls `_row` before returning so the caller immediately gets the same kind of object that later reads would produce.

*Call graph*: calls 1 internal fn (_row); 2 external calls (insert, uuid4).


##### `MonitorStore.claim_due`  (lines 270–313)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_MONITORS) -> tuple[Monitor, ...]
```

**Purpose**: Leases a batch of monitors that are ready to be checked. The lease is a temporary ownership mark that prevents overlapping runner ticks from probing the same monitor at the same time.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of monitors. It creates a fresh claim ID, selects the oldest due and claimable monitor rows for this workspace whose agents are live, updates those rows with the claim and expiry time, and returns them as `Monitor` objects.

**Call relations**: The monitor runner calls this when it is ready to do work. It uses `_due` and `_claim_available` to find safe candidates, updates them atomically, and then uses `_row` to hand the claimed monitors back for probing.

*Call graph*: calls 3 internal fn (_claim_available, _due, _row); 5 external calls (timedelta, select, update, agent_is_live, uuid4).


##### `MonitorStore.quiet_tick`  (lines 315–325)

```
async def quiet_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records that a monitor probe ran successfully and produced the same output as the baseline. In plain terms, the watch checked and nothing changed.

**Data flow**: It receives the claimed monitor, the time the probe ran, and the next time it should run. It increases the total probe count and quiet streak, clears the failure streak, keeps the skipped count, and passes those new values to `_tick`.

**Call relations**: `MonitorRunner._tick` calls this after a successful unchanged probe. This method translates that result into the correct counter changes, then hands the actual database update to `MonitorStore._tick`.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.failed_tick`  (lines 327–339)

```
async def failed_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records that a monitor probe ran but the shell command failed, without yet firing the monitor. It advances the failure streak and breaks the quiet streak.

**Data flow**: It receives the claimed monitor, the probe time, and the next scheduled probe time. It increases the total probe count and failure streak, resets the quiet streak to zero, keeps the skipped count, and sends the updated values to `_tick`.

**Call relations**: `MonitorRunner._tick` calls this when a command exits unsuccessfully but the monitor should keep watching. This method decides the counter changes and delegates the shared database update to `MonitorStore._tick`.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.skipped_tick`  (lines 341–352)

```
async def skipped_tick(self, row: Monitor, next_probe_at: datetime) -> None
```

**Purpose**: Records that a scheduled probe could not run, for example because the sandbox was unreachable. A skipped probe is counted separately from a command failure.

**Data flow**: It receives the claimed monitor and the next scheduled probe time. It leaves probe and streak counts unchanged, increases the skipped count, keeps the previous last-probe time, and passes everything to `_tick`.

**Call relations**: `MonitorRunner._tick` calls this when the runner could not execute the probe. This method preserves the distinction between “the command failed” and “the command never ran,” then uses `MonitorStore._tick` for the database write.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore._tick`  (lines 354–386)

```
async def _tick(self, row: Monitor, *, probes_run: int, quiet_streak: int, failure_streak: int, skipped: int, last_probe_at: datetime | None, next_probe_at: datetime) -> None
```

**Purpose**: Performs the shared database update after a monitor tick. It records the new counters and schedule, then releases the claim so the monitor can be picked up again later.

**Data flow**: It receives a claimed monitor plus the counter values, last probe time, and next probe time to store. It first rejects unclaimed monitors, then updates only the row with the same monitor ID, workspace ID, and claim ID. The row ends with new counts, new timing, no active claim, and a fresh update timestamp.

**Call relations**: `quiet_tick`, `failed_tick`, and `skipped_tick` all call this after deciding what the new values should be. `_tick` is the common final step that writes those values safely, guarded by the current claim.

*Call graph*: called by 3 (failed_tick, quiet_tick, skipped_tick); 1 external calls (update).


##### `MonitorStore.claim_holds`  (lines 388–415)

```
async def claim_holds(self, row: Monitor) -> bool
```

**Purpose**: Checks whether this worker still owns the monitor claim before firing it. This helps avoid posting a fire for a monitor that was stopped while the probe was running.

**Data flow**: It receives a monitor that should already have a claim ID. It rejects unclaimed monitors, then looks for a row with the same monitor ID, workspace ID, and claim ID, locking it for the check. It returns `true` if that row still exists under the claim and `false` otherwise.

**Call relations**: `MonitorRunner._fire` calls this immediately before delivering a fire. If the claim no longer holds, the runner can avoid acting on stale work; if it still holds, firing can continue.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `MonitorStore.retire`  (lines 417–429)

```
async def retire(self, row: Monitor) -> None
```

**Purpose**: Deletes a monitor after its fire has been delivered. Retiring means this armed watch is finished and should not run again.

**Data flow**: It receives a claimed monitor. It rejects unclaimed monitors, then deletes the row only if the monitor ID, workspace ID, and claim ID all match. It does not return a monitor; the change is the removal of the row.

**Call relations**: `MonitorRunner._fire` calls this after a fire is delivered. The claim guard means an expired or lost lease cannot delete a monitor that another runner has since claimed.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `MonitorStore.disarm`  (lines 431–439)

```
async def disarm(self, row: Monitor) -> bool
```

**Purpose**: Stops a monitor because someone asked to stop watching. It reports whether the stop actually removed a row.

**Data flow**: It receives a monitor object. It deletes the row matching that monitor ID in the current workspace, regardless of claim, and returns `true` if exactly one row was deleted or `false` if there was nothing to remove.

**Call relations**: This is the user-driven stop path, separate from `retire`, which is the fire-driven finish path. By deleting the row directly, it can interrupt future runner work and lets callers know whether the monitor was still armed.

*Call graph*: 1 external calls (delete).


### Scheduled prompts and pauses
Scheduled tasks and workflow pauses are claimed, fired, rescheduled, or removed using shared key, cron, and storage helpers.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pause_runner.py`

`orchestration` · `recurring scheduled job`

A pause is a saved instruction to continue an agent workflow later, unless a member speaks first. This file is the clock-driven worker that checks for pauses whose time has arrived and tries to finish each one in the correct way.

On each run, `PauseRunner` asks the pause store for due pauses and claims them with a short lease. A lease is like putting a temporary “I’m working on this” sticky note on a row, so two overlapping runs do not fire the same pause at the same time. For each claimed pause, it tries to fire the saved prompt back into the conversation as a scheduled turn, acting on behalf of the member who originally created the pause.

The important safety rule is that the scheduled turn is admitted only if no member has spoken since the pause began. The pause stores two watermarks, which are saved conversation positions. They let the system check, under the conversation lock, whether a human reply already ended the wait. If a member did speak, there is nothing left for the timer to do.

After the invoke attempt, the pause is retired so it will not run again. If the app or agent has been archived, the runner does not retire the pause; it leaves it in place so restoring the app can still serve it later. If some pauses fail, the runner reports which conversations failed after trying the rest.

#### Function details

##### `PauseRunner.run`  (lines 33–42)

```
async def run(self) -> None
```

**Purpose**: This is the main tick of the pause runner. It finds pauses that are due, asks each one to fire, and reports if any of them failed.

**Data flow**: It starts with the runner’s extension context and current lease length. It creates a `PauseStore`, reads the current UTC time, and asks the store for pause rows that are due and can be claimed. Each row is passed to `_fire`. If any `_fire` call raises an error, this function records the conversation and error type, keeps going for the remaining rows, and finally raises one combined error if there were failures. If all rows succeed or are harmlessly skipped, it returns nothing.

**Call relations**: This function is called by the recurring scheduled-task machinery outside this file. During each clock tick, it creates the store it needs, gets due work from storage, and delegates the actual per-pause decision to `PauseRunner._fire`.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `PauseRunner._fire`  (lines 44–60)

```
async def _fire(self, store: PauseStore, row: Pause) -> None
```

**Purpose**: This tries to complete one stored pause. It either resumes the agent workflow, skips because another process owns the needed holds, leaves the pause alone if the agent is archived, or retires the pause once the wait has ended.

**Data flow**: It receives a `PauseStore` and one `Pause` row. First it asks the store to claim the pause’s holds, meaning it checks that this runner is still allowed to act on the pause’s stored guard conditions. If that fails, it returns without changing the row. If the holds are claimed, it invokes the saved prompt in the conversation using a stable idempotency key built from the pause id, so a retry does not create a duplicate turn. The invoke is guarded by the saved member-activity watermarks, so it only resumes if no member message already ended the wait. If the agent is archived, it returns and leaves the pause available for later restoration. Otherwise, it retires the pause in the store.

**Call relations**: `PauseRunner.run` calls this once for each due pause it claimed. This function is where storage, conversation admission, and cleanup meet: it asks `PauseStore.claim_holds` before invoking, then calls `PauseStore.retire` after the pause has been settled.

*Call graph*: calls 2 internal fn (claim_holds, retire); called by 1 (run).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `recurring scheduled-task tick`

This file is the clock-driven worker for scheduled tasks. Think of it like a careful alarm clerk: on each tick, it looks for alarms that are due, temporarily marks them as claimed so another clerk will not ring the same alarm, and then rings each one exactly once if it is still valid.

The runner first asks the schedule store for tasks due now. For each task, it checks whether the task has already expired. If so, it retires it instead of running it. If the task may run, the runner calculates the next time it should fire using the task’s cron schedule. A cron schedule is a compact way to say things like “every day at 9am” or “every Monday.”

Before invoking the task, the runner checks that its claim still holds. This matters because overlapping runner ticks could otherwise both try to run the same task. It then builds the message that will be sent into the task’s conversation, along with an idempotency key, which is a repeat-safe label that lets the system recognize “this exact scheduled occurrence” if delivery is retried.

If the conversation accepts the scheduled turn, the task is rescheduled to its following cron occurrence. If the app is archived, nothing is treated as failed; the task simply remains where it is and can try again later. Other invocation errors are collected and reported after the tick finishes.

#### Function details

##### `fire_body`  (lines 42–60)

```
def fire_body(task: ScheduledTask, runtime_instruction: str | None) -> tuple[str, str]
```

**Purpose**: This function builds the actual message that a scheduled task sends into its conversation. It also creates the repeat-safe key used to identify this exact task occurrence, so retries do not accidentally create duplicate scheduled turns.

**Data flow**: It receives a scheduled task and an optional instruction for how this run should behave. It reads the task’s next run time, prompt, and id, formats the run time for the message, adds the task prompt and any extra instruction, then returns two things: the message text to send and the idempotency key for that exact scheduled fire.

**Call relations**: ScheduledTaskRunner._fire calls this after it has confirmed the task should still run and that its claim is valid. fire_body hands back the prepared conversation input and key, and _fire passes both into the extension context so the scheduled turn can be admitted safely.

*Call graph*: called by 1 (_fire); 1 external calls (scheduled_fire_key).


##### `ScheduledTaskRunner.run`  (lines 68–77)

```
async def run(self) -> None
```

**Purpose**: This is the top-level tick for the scheduled-task runner. Each time it runs, it finds tasks that are due, tries to fire them, and reports if any task failed for a real error.

**Data flow**: It starts with the extension context stored on the runner. It creates a schedule store, captures the current time, asks the store to claim due tasks for a short lease period, then sends each claimed task to _fire. Any failure names returned by _fire are collected; if the list is not empty, run raises an error describing the failed scheduled fires.

**Call relations**: This method is the entry for the recurring job. It sets up the store and timing for the tick, then delegates the detailed decision-making for each individual task to ScheduledTaskRunner._fire.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `ScheduledTaskRunner._fire`  (lines 79–116)

```
async def _fire(self, store: ScheduleStore, task: ScheduledTask, tick_at: datetime, expiry_checked_at: datetime) -> str | None
```

**Purpose**: This function decides what should happen to one claimed scheduled task. It may retire it, skip it, invoke it into its conversation, reschedule it after success, or report a failure.

**Data flow**: It receives the schedule store, one claimed task, the tick time, and the time at which expiry is being checked. First it asks the store to retire the task if it has expired. If not expired, it calculates the task’s next scheduled fire time. It chooses either the normal reporting instruction or a final-run instruction if the next occurrence would be past the expiry limit. It then verifies the lease still belongs to this runner, builds the message and key, and invokes the task through the extension context. If invocation is accepted and returns a turn id, it writes the next scheduled time back to the store. If the app is archived or the turn is not accepted, it leaves the task as-is. If an unexpected error occurs, it returns a readable failure label.

**Call relations**: ScheduledTaskRunner.run calls this once for each due task it claimed. _fire relies on ScheduleStore methods to retire, verify, and reschedule task rows; it uses next_fire to compute the following cron occurrence; and it uses fire_body to prepare the scheduled message before handing it to the extension context for actual conversation invocation.

*Call graph*: calls 4 internal fn (fire_body, claim_holds, reschedule, retire_if_expired); called by 1 (run); 1 external calls (next_fire).


### `core/src/ufo/ext/scheduled_fire.py`

`domain_logic` · `request handling`

A scheduled task may run many times, so the system needs a durable label for one exact occurrence of one exact task. This file creates that label. Think of it like writing a reservation card that says “task 123, at 9:00.” Later, another part of the system can read the card and know which task caused that run.

The key is built from two pieces: the task’s unique ID and the scheduled time. They are joined with a colon. The time is written using Python’s standard ISO format, including details such as the `+00:00` timezone ending. That exact spelling matters because this key is used for deduplication, meaning it helps the system avoid admitting the same scheduled run twice. If old runs were recorded with this exact text, changing the format could make the system forget that they already happened.

The file also provides the reverse operation: given a key, try to recover the task ID from the part before the colon. If the key was not made for a scheduled task, such as a different kind of timer resume, it returns `None` instead of pretending it understands it. This makes scheduled-fire keys explicit and safe to recognize.

#### Function details

##### `scheduled_fire_key`  (lines 14–16)

```
def scheduled_fire_key(task_id: UUID, fire_at: datetime) -> str
```

**Purpose**: Creates the stable text key used to identify one scheduled firing of one task. Someone uses this when admitting a scheduled run so the same occurrence can be recognized later and not duplicated.

**Data flow**: It receives a task ID and the date-time when that task should fire. It turns the date-time into its standard ISO text form, joins it to the task ID with a colon, and returns the combined string. It does not change any stored data by itself; it only produces the key text.

**Call relations**: This is the builder side of the shared contract. The scheduled-task runner uses this kind of key when it records or admits a fire, and it relies on `datetime.datetime.isoformat` to spell the occurrence time consistently.

*Call graph*: 1 external calls (isoformat).


##### `scheduled_fire_task_id`  (lines 19–25)

```
def scheduled_fire_task_id(key: str) -> UUID | None
```

**Purpose**: Tries to read a scheduled-fire key and extract the task ID it names. Someone uses this when looking at a recorded run and trying to link it back to the scheduled task that caused it.

**Data flow**: It receives a key string. It looks only at the part before the first colon, tries to treat that part as a UUID, which is a standard unique identifier, and returns that UUID if it is valid. If the first part is not a valid UUID, it returns `None`, meaning this key was probably not a scheduled-fire key.

**Call relations**: This is the parser side of the same contract created by `scheduled_fire_key`. A runs feed or similar lookup can call it when it sees an admission key; it hands off the first key segment to `uuid.UUID` to validate and convert it, and uses `None` to signal keys that belong to some other mechanism.

*Call graph*: 1 external calls (UUID).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `scheduled task creation and run-time rescheduling`

Scheduled tasks need a way to say “run every day at 9” or “run every 5 minutes.” Cron is a compact text format for that kind of repeating schedule. This file keeps that cron-specific knowledge inside the scheduled-tasks extension, instead of mixing it into the main task storage system.

The main store only cares about a concrete date and time called `next_run_at`. It does not understand cron expressions itself. This file acts like a translator: it takes a cron expression, checks whether it is valid, and turns it into the next real `datetime` when the task should fire.

It deliberately accepts only the common five-field cron form, such as minute, hour, day of month, month, and day of week. If someone supplies too many or too few fields, or a schedule the cron library cannot understand, it raises an error early. That prevents bad schedules from being saved and failing later.

The `next_fire` function always asks for the next time strictly after a given moment. This matters when a runner is late: instead of trying to replay every missed run in a burst, it collapses missed windows into one catch-up point and then continues from there.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: This function checks that a schedule string is a valid five-field cron expression. It is used before accepting a schedule, so invalid timing rules are rejected immediately with a clear error.

**Data flow**: It receives a schedule as text. First it splits the text into space-separated parts and confirms there are exactly five. Then it asks the cron library whether the expression is valid. If either check fails, it raises a `ValueError`; if both pass, it returns the original schedule unchanged.

**Call relations**: When code wants to accept or store a cron schedule, it should call this function first. Inside the check, it relies on `croniter.croniter.is_valid` from the external cron library to verify the actual cron meaning after this file has enforced the project’s five-field rule.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: This function calculates the next date and time when a cron schedule should run after a given moment. It is what turns a repeating rule into the concrete `next_run_at` time the scheduler can store and compare.

**Data flow**: It receives a cron schedule string and an `after` datetime. It gives both to the cron library, asks for the next matching datetime, and returns that datetime. It does not change the schedule or the input time.

**Call relations**: After a task has run, or when the system needs to know when it should run next, this function is the cron-to-datetime bridge. It hands the calculation to `croniter.croniter`, then returns the next concrete firing time for the rest of the scheduled-task system to use.

*Call graph*: 1 external calls (croniter).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pauses.py`

`domain_logic` · `pause arming, background runner scans, and pause firing`

A “pause” here means a workflow has said, in effect, “wake me up later with this prompt.” This file defines the pause database table and the only ways the scheduled-tasks extension reads from or writes to it. There is one pause row per conversation, because a conversation can only be waiting for one scheduled resume at a time; arming a new pause replaces the old one.

The file also protects against race conditions, which are moments when two things happen at nearly the same time and could produce the wrong result. For example, two background workers might both notice the same overdue pause. To avoid both firing it, `claim_due` gives a short lease to one worker, like putting a sticky note on a library book saying “I’m checking this out.” Later, before firing, `claim_holds` checks that the sticky note is still valid. After the pause is fired or no longer needed, `retire` deletes it, but only if the same lease still owns it.

The stored pause includes two sequence numbers from the original conversation state. Those are used elsewhere to decide whether a human message arrived after the pause was armed. This file does not make that decision itself; it preserves the evidence needed to make it safely under the conversation lock.

#### Function details

##### `_aware`  (lines 76–77)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: This small helper makes sure a time value has a timezone. If the database gives back a plain timestamp with no timezone attached, it treats it as UTC, the shared time standard used here.

**Data flow**: It receives a `datetime`. If that time already says what timezone it belongs to, it returns it unchanged. If not, it adds UTC information and returns the corrected time.

**Call relations**: `_row` calls this whenever it turns database rows into `Pause` objects, so the rest of the pause system can compare times without each caller fixing timezone details separately.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 80–95)

```
def _row(row: sa.RowMapping) -> Pause
```

**Purpose**: This converts one raw database row into a `Pause` object that the rest of the code can use. It is the single doorway from database-shaped data into application-shaped data.

**Data flow**: It receives a row from the pause table. It pulls out the pause id, conversation id, agent id, resume time, sequence numbers, prompt, creator, claim id, and timestamps. It normalizes the time fields through `_aware`, then returns a `Pause` value.

**Call relations**: `PauseStore.arm`, `PauseStore.armed`, and `PauseStore.claim_due` all call `_row` after reading from the database. This keeps their results consistent, especially around timestamp handling.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 98–99)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for “this pause can be claimed now.” A pause is available if nobody has claimed it, or if an old claim has expired.

**Data flow**: It receives the current time. It creates a SQL condition that checks whether `claimed_by` is empty or `claim_expires_at` is earlier than that time. The output is not a yes-or-no value yet; it is a database expression used inside later queries.

**Call relations**: `PauseStore.claim_due` uses this when leasing actual pause rows. The nested `due_pause_workspaces.due` query uses the same condition when looking for workspaces that have runnable pauses, so discovery and claiming follow the same rule.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_pause_workspaces`  (lines 102–118)

```
def due_pause_workspaces() -> WorkspaceCandidates
```

**Purpose**: This tells the job system how to find workspaces that may have pause work ready to run. It is a candidate finder for the pause background runner.

**Data flow**: It creates a small query function that looks for distinct workspace ids with at least one due pause whose claim is free or expired. It then passes that query function to `owner_candidates`, which wraps it in the job-system format for assigning work.

**Call relations**: The background job machinery calls on this when deciding which workspaces should be opened for pause processing. Inside it, the nested `due` function does the actual database query.

*Call graph*: 1 external calls (owner_candidates).


##### `due_pause_workspaces.due`  (lines 107–116)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This inner function builds the query that finds workspaces with pauses ready to resume. It deliberately ignores pauses that are due but still under a live lease, so workers do not reopen work that another worker is already doing.

**Data flow**: It reads the current UTC time, asks `_claim_available` for the claim-free condition, and builds a database query for workspace ids whose pause time has arrived. The result is a SQL select statement, not the final list itself.

**Call relations**: `due_pause_workspaces` hands this function to `owner_candidates`. When the job system needs candidates, this query is run to find workspaces that deserve a pause-runner tick.

*Call graph*: calls 1 internal fn (_claim_available); 2 external calls (now, select).


##### `PauseStore.arm`  (lines 127–183)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, resume_at: datetime, origin_seq: int, origin_arrival_seq: int, prompt: str, created_by_member_id: UUID | None) -> Pause
```

**Purpose**: This creates or replaces the pause for one conversation. It is used when a workflow schedules itself to resume later.

**Data flow**: It receives the conversation, agent, wake-up time, original sequence numbers, prompt text, and optional member who created it. It writes a pause row for the current workspace. If that conversation already has a pause, it overwrites it, clears any old claim, and gives the new wait a fresh id. It returns the saved pause as a `Pause` object.

**Call relations**: Workflow code calls this when it arms a wait. After the database insert-or-update finishes, it hands the returned row to `_row` so callers receive the normal `Pause` shape. The fresh id is important because the identity belongs to this particular wait, not just to the conversation’s reusable row.

*Call graph*: calls 1 internal fn (_row); 1 external calls (uuid4).


##### `PauseStore.armed`  (lines 185–191)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Pause, ...]
```

**Purpose**: This lists the currently armed pauses in the workspace, optionally limited to one conversation. It is useful for inspecting what is waiting.

**Data flow**: It receives an optional conversation id. It builds a query for the current workspace, adds the conversation filter if one was supplied, orders results by wake-up time, reads the rows, converts each through `_row`, and returns them as a tuple.

**Call relations**: Other code can call this when it needs a snapshot of scheduled waits. It does not claim or change anything; it only reads and translates rows.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `PauseStore.claim_due`  (lines 193–234)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_PAUSES) -> tuple[Pause, ...]
```

**Purpose**: This leases a batch of overdue pauses for one worker to process. The lease prevents overlapping background ticks from firing the same pause twice.

**Data flow**: It receives the current time, a lease length in seconds, and an optional maximum number of pauses to claim. It creates a random claim id, selects the oldest due and available rows in this workspace, updates them with the claim id and expiry time, and returns the claimed rows as `Pause` objects.

**Call relations**: The pause runner calls this during a tick to get work. It uses `_claim_available` so only unclaimed or expired rows are chosen, and `_row` so the runner receives clean `Pause` objects. The database update and return happen together, which is what makes the lease safe.

*Call graph*: calls 2 internal fn (_claim_available, _row); 4 external calls (timedelta, select, update, uuid4).


##### `PauseStore.claim_holds`  (lines 236–261)

```
async def claim_holds(self, row: Pause) -> bool
```

**Purpose**: This checks whether a worker still owns the pause it is about to fire. It is a last safety check before sending the resume action.

**Data flow**: It receives a `Pause` that should already have a claim id. If there is no claim id, it raises an error because an unclaimed pause must not be fired. Otherwise it looks for the same pause id in the current workspace with the same claim id and returns true if it still exists.

**Call relations**: `PauseRunner._fire` calls this immediately before firing a pause. This protects against cases where the pause was re-armed or replaced after it was leased but before the worker actually resumed the workflow.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `PauseStore.retire`  (lines 263–276)

```
async def retire(self, row: Pause) -> None
```

**Purpose**: This removes a pause after the worker has finished with it. It only deletes the row if the same claim still owns it.

**Data flow**: It receives a claimed `Pause`. If the pause has no claim id, it raises an error because unclaimed pauses cannot be retired this way. Otherwise it deletes the matching row by pause id, workspace id, and claim id. It returns nothing.

**Call relations**: `PauseRunner._fire` calls this after a pause has been fired or otherwise settled. The claim check means a worker with an expired or superseded lease cannot accidentally delete a newer pause that replaced the old one.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/schedules.py`

`domain_logic` · `cross-cutting: member task changes, listings, scheduled runner sweeps, and status reads`

A scheduled task is like a calendar reminder for an agent: at a certain time, in a certain conversation, deliver this prompt, then schedule the next reminder. This file owns the database table shape for those reminders and provides ScheduleStore, the safe doorway used by the rest of the extension to work with them.

The important problem here is coordination. Many workers may be looking for due tasks at the same time, while members may also edit or cancel tasks. Without this file’s checks, the same task could fire twice, a cancelled task could keep running for too long, or one workspace or agent could accidentally see another’s tasks.

Every database query is explicitly limited to the current workspace. Member-facing operations are also limited to the current object agent, which means a task belongs to the agent that will execute it. Creating and editing preserve identity carefully: the task’s id, agent, conversation, name, and creator are checked so an old copy cannot overwrite a newer one by mistake.

For background runners, the file uses a lease: a short claim saying “this worker is responsible for this task right now.” That lease prevents two workers from firing the same due task. After a fire, the row is rescheduled and the claim is cleared. Expired tasks are removed instead of fired.

#### Function details

##### `_utc`  (lines 124–125)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: This helper makes sure a datetime has UTC timezone information. It matters because different databases can return times with or without timezone labels, and the rest of the code needs one consistent meaning.

**Data flow**: It receives one datetime value. If the value already says what timezone it uses, it is returned unchanged; if it has no timezone label, the function marks it as UTC. The output is always a datetime the code can treat as UTC-aware.

**Call relations**: Rows are converted through _task and status data is converted in ScheduleStore.inspect_many, and both rely on _utc so callers do not have to remember to normalize times themselves. _utc_opt also delegates to it for optional time fields.

*Call graph*: called by 3 (inspect_many, _task, _utc_opt); 1 external calls (replace).


##### `_utc_opt`  (lines 128–129)

```
def _utc_opt(value: datetime | None) -> datetime | None
```

**Purpose**: This helper is the optional version of _utc. It normalizes a datetime when one exists, but safely leaves missing values as missing.

**Data flow**: It receives either a datetime or None. If it gets None, it returns None; otherwise it passes the datetime through _utc and returns the normalized result.

**Call relations**: _task and ScheduleStore.inspect_many use this for fields such as last run time and expiry time, where a scheduled task may not have a value yet.

*Call graph*: calls 1 internal fn (_utc); called by 2 (inspect_many, _task).


##### `_claim_available`  (lines 132–136)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for “a task is free to be claimed.” A task is free if nobody has claimed it, or if an old claim has timed out.

**Data flow**: It receives the current time. It turns that into a SQL condition that matches rows with no claimant or with a claim expiry earlier than that time. The output is not data itself, but a filter used inside database queries.

**Call relations**: The workspace candidate search and ScheduleStore.claim_due both use this same rule, so the system first chooses workspaces and then leases tasks using matching ideas of what is available.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_expired`  (lines 139–143)

```
def _expired(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for “a task has passed its expiry time.” It lets the runner remove tasks that should no longer fire.

**Data flow**: It receives the current time. It creates a SQL condition matching rows that have an expires_at value and where that value is at or before now. The output is used as part of larger database queries.

**Call relations**: due_task_workspaces.due uses it to wake the runner for workspaces with expired tasks, and ScheduleStore.claim_due uses it to delete expired tasks before claiming due ones.

*Call graph*: called by 2 (claim_due, due); 1 external calls (and_).


##### `_task`  (lines 146–165)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: This turns a raw database row into a ScheduledTask object that the rest of the code can use. It also normalizes all stored time values to UTC-aware datetimes.

**Data flow**: It receives a row returned from the scheduled_task table. It copies each important column into a ScheduledTask value, converting required and optional datetime fields through _utc and _utc_opt. The output is a clean in-memory ScheduledTask.

**Call relations**: Create, update, list, and claim_due all receive database rows and pass them through _task before handing them back to callers. This makes _task the single translation point between storage and application code.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 4 (claim_due, create, list, update); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 168–191)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: This tells the background job system which workspaces might have scheduled-task work to do. It exists so the runner does not have to scan every workspace blindly.

**Data flow**: It defines a query factory that finds workspaces containing tasks that are either due to run or expired and ready to remove. It wraps that query with owner_candidates, producing a WorkspaceCandidates object for the job scheduler.

**Call relations**: The scheduled-task runner uses this as its candidate source. Inside it, the nested due query applies the same availability and expiry rules used later when tasks are actually claimed.

*Call graph*: 1 external calls (owner_candidates).


##### `due_task_workspaces.due`  (lines 174–189)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the actual database query for workspaces that need attention. A workspace qualifies if it has at least one claimable expired task, or at least one claimable unpaused task whose next run time has arrived.

**Data flow**: It reads the current UTC time, then creates a SQL select for distinct workspace ids from the scheduled_task table. The query filters out tasks under live claims and includes expired tasks or due unpaused tasks. The result is a database query, not yet the final rows.

**Call relations**: due_task_workspaces hands this query builder to owner_candidates. It relies on _claim_available and _expired so candidate discovery agrees with ScheduleStore.claim_due.

*Call graph*: calls 2 internal fn (_claim_available, _expired); 5 external calls (now, and_, not_, or_, select).


##### `ScheduleStore.workspace_id`  (lines 205–206)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property exposes the workspace id from the extension context. It is used to keep every database operation inside the correct workspace.

**Data flow**: It reads ctx.workspace_id from the store’s ExtensionContext and returns that UUID. It does not change anything.

**Call relations**: Almost every ScheduleStore database method uses this value when building its WHERE clauses, because the transaction connection itself is not automatically scoped to a workspace.


##### `ScheduleStore.create`  (lines 208–267)

```
async def create(self, conversation_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None, expires_at: datetime | None=None,
```

**Purpose**: This creates a new recurring scheduled task for the current agent and a specific conversation. It also prevents duplicate task names for the same workspace and agent.

**Data flow**: It receives the conversation, task name, schedule text, prompt, description, next run time, optional creator, optional expiry, and paused flag. It checks that the conversation belongs to the agent that will run the task, inserts a new database row with a fresh id, and returns the inserted row as a ScheduledTask. If the name already exists, it raises an error.

**Call relations**: Member-facing creation code calls this when a user or object asks to schedule recurring work. It uses object_agent_id to bind the task to the current agent and _task to turn the inserted row into the returned value.

*Call graph*: calls 1 internal fn (_task); 2 external calls (object_agent_id, uuid4).


##### `ScheduleStore.update`  (lines 269–323)

```
async def update(self, expected: ScheduledTask, schedule: str, prompt: str, description: str, next_run_at: datetime, expires_at: datetime | None=None, *, paused: bool) -> ScheduledTask
```

**Purpose**: This edits an existing scheduled task without changing who owns it or where it reports. It protects against overwriting a task that changed since the caller last read it.

**Data flow**: It receives the caller’s expected ScheduledTask plus new schedule, prompt, description, next run time, expiry, and paused state. It checks that the task is still for the current agent, then updates only the matching row, including creator matching. It clears any current claim so an old leased copy will not fire, and returns the updated ScheduledTask. If no row matches, it raises an error.

**Call relations**: Editing flows call this after reading a task. It uses _creator_matches to avoid confusing creatorless tasks with member-created tasks, and _task to return a normalized result.

*Call graph*: calls 2 internal fn (_creator_matches, _task); 2 external calls (update, object_agent_id).


##### `ScheduleStore.cancel`  (lines 325–341)

```
async def cancel(self, expected: ScheduledTask) -> None
```

**Purpose**: This deletes a scheduled task, but only if it still matches the exact task the caller expected. That prevents cancelling the wrong task after a concurrent change.

**Data flow**: It receives an expected ScheduledTask. It verifies the task still belongs to the current agent, then deletes the row matching the workspace, id, agent, conversation, name, and creator. If nothing was deleted, it raises an error saying the task changed while cancelling.

**Call relations**: Member-facing cancellation flows call this. It uses object_agent_id for the current agent boundary and _creator_matches for safe creator comparison.

*Call graph*: calls 1 internal fn (_creator_matches); 2 external calls (delete, object_agent_id).


##### `ScheduleStore._creator_matches`  (lines 343–348)

```
def _creator_matches(self, expected: ScheduledTask) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition that says whether a row has the same creator as the expected task. It treats “no creator” carefully, because SQL needs a special check for missing values.

**Data flow**: It receives an expected ScheduledTask. If the task has no creator, it returns a condition requiring the database field to be NULL; otherwise it returns a condition requiring the creator id to be equal. The output is a SQL filter.

**Call relations**: ScheduleStore.update and ScheduleStore.cancel use this when they need to prove they are changing the exact task the caller saw, not another task with similar identity.

*Call graph*: called by 2 (cancel, update).


##### `ScheduleStore._listing`  (lines 350–371)

```
def _listing(self, selected: tuple[sa.ColumnElement[Any], ...], *, conversation_id: UUID | None, names: tuple[str, ...] | None, visible_to_member_id: UUID | None, include_all_owners: bool, limit: int
```

**Purpose**: This builds the shared query used to list scheduled tasks. It applies workspace, agent, conversation, name, owner, ordering, and limit filters in one place.

**Data flow**: It receives the columns to select and optional filters such as conversation id, task names, visible member id, whether to include all owners, and limit. It creates a SQL select query scoped to the current workspace and current agent, then adds the requested filters. The output is the query that another method will execute.

**Call relations**: ScheduleStore.list calls this to avoid duplicating query-building rules. It uses object_agent_id so listings only show tasks for the selected object agent.

*Call graph*: called by 1 (list); 2 external calls (select, object_agent_id).


##### `ScheduleStore.list`  (lines 373–392)

```
async def list(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -> tuple[S
```

**Purpose**: This returns scheduled tasks visible under the requested filters. It is the basic read operation for pages or tools that need task definitions.

**Data flow**: It receives optional filters for conversation, names, visible member, owner inclusion, and limit. It asks _listing to build the query, executes it in a transaction, converts each row with _task, and returns a tuple of ScheduledTask objects.

**Call relations**: Member-facing reads can call this directly when they only need task data. ScheduleStore.list_reported builds on it when it also needs conversation audience and surface-label information.

*Call graph*: calls 2 internal fn (_listing, _task); called by 1 (list_reported).


##### `ScheduleStore.list_reported`  (lines 394–428)

```
async def list_reported(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -
```

**Purpose**: This lists scheduled tasks together with information about the conversation they report into. That extra information lets user-facing surfaces decide and show where a task is visible.

**Data flow**: It receives the same filters as list. First it gets matching ScheduledTask objects, then asks the extension context for live facts about their conversations. It returns ListedTask objects containing each task plus the conversation audience and surface label, skipping tasks whose conversation no longer exists.

**Call relations**: User-facing listing code uses this when it must show or check reporting context. It calls ScheduleStore.list for the task rows, then enriches them with conversation facts from the context.

*Call graph*: calls 1 internal fn (list); 1 external calls (__init__).


##### `ScheduleStore.claim_due`  (lines 430–486)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: This is the runner’s way to take responsibility for due scheduled tasks. It removes expired tasks, then leases a limited batch of due tasks so other workers will not fire the same ones.

**Data flow**: It receives the current time, lease length, and maximum number of tasks to claim. It creates a fresh claim id, deletes claimable expired rows, finds the oldest due unpaused claimable rows, stamps them with the claim id and claim expiry, and returns them as ScheduledTask objects. Rows not claimed remain due for a later sweep.

**Call relations**: The scheduled-task runner calls this during a workspace sweep. It uses _expired and _claim_available to match the candidate rules, SQL delete and update operations for the lease, and _task to return usable claimed tasks.

*Call graph*: calls 3 internal fn (_claim_available, _expired, _task); 6 external calls (timedelta, delete, not_, select, update, uuid4).


##### `ScheduleStore.claim_holds`  (lines 488–519)

```
async def claim_holds(self, task: ScheduledTask) -> bool
```

**Purpose**: This checks whether a claimed task is still exactly the same task right before firing it. It narrows the chance that an edited or cancelled task fires from an old lease.

**Data flow**: It receives a ScheduledTask that must already have a claim id. It re-reads the database row under a lock and checks the workspace, id, claim id, conversation, agent, name, and schedule. It returns true if that exact claimed version still exists, otherwise false; it raises an error if the task was not claimed.

**Call relations**: ScheduledTaskRunner._fire calls this immediately before invoking the scheduled prompt. If this returns false, the runner can skip firing because the lease no longer represents the live task.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `ScheduleStore.retire_if_expired`  (lines 521–535)

```
async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool
```

**Purpose**: This deletes a claimed task if its expiry time has passed before it is invoked. It prevents an already-expired task from firing just because it was picked up by a runner.

**Data flow**: It receives a claimed ScheduledTask and the current time. If the task has no claim id, it raises an error; if it has no expiry or expires in the future, it returns false. If it is expired, it deletes the matching claimed row and returns true.

**Call relations**: ScheduledTaskRunner._fire calls this as part of firing preparation. It can stop the flow early by retiring the task instead of handing it off to the agent.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `ScheduleStore.reschedule`  (lines 537–567)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: This records that a claimed task ran and moves it to its next planned run time. It also clears the lease so the task can be claimed again in the future.

**Data flow**: It receives the claimed task, the next run time, the time it just ran, and optionally the id of the turn created by the fire. It updates the matching claimed row with the new timing information, clears claim fields, optionally records the last turn id, and returns true if a row was updated.

**Call relations**: ScheduledTaskRunner._fire calls this after a task has been invoked or admitted as a turn. Later, ScheduleStore.inspect_many can use the recorded last_turn_id to show the latest outcome.

*Call graph*: called by 1 (_fire); 1 external calls (update).


##### `ScheduleStore.inspect`  (lines 569–573)

```
async def inspect(self, expected: ScheduledTask) -> TaskInspection | None
```

**Purpose**: This reads the live status for one scheduled task. It is a convenience wrapper around the batch inspection method.

**Data flow**: It receives one expected ScheduledTask. It calls inspect_many with a one-item tuple, then returns the inspection for that task id or None if it is no longer found or no longer matches.

**Call relations**: Status-rendering code can call this for a single task. It delegates all real work to ScheduleStore.inspect_many so the same rules are used for one task and many tasks.

*Call graph*: calls 1 internal fn (inspect_many).


##### `ScheduleStore.inspect_many`  (lines 575–611)

```
async def inspect_many(self, expected: tuple[ScheduledTask, ...]) -> dict[UUID, TaskInspection]
```

**Purpose**: This reads status details for several scheduled tasks, including when they next run and what happened in their latest recorded turn. It is used to render a task’s current condition without changing it.

**Data flow**: It receives expected ScheduledTask objects. It queries matching rows in the current workspace and current agent, collects any last turn ids, asks the context for those turn outcomes, and builds TaskInspection objects with normalized times, last turn status, and last response text. It skips rows whose name or conversation no longer match the expected task.

**Call relations**: ScheduleStore.inspect calls this for a single task, while other status code can use it in batches. It uses object_agent_id to stay inside the current agent, _utc and _utc_opt for time normalization, and context turn outcomes to attach the latest result text.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 1 (inspect); 3 external calls (__init__, select, object_agent_id).


### Runtime job dispatch
The runtime discovers workspace-scoped background work, queues safe per-workspace executions, runs them in extension context, and recovers missed child-result delivery.

### `core/src/ufo/runtime/candidates.py`

`domain_logic` · `job scheduling`

In this system, work is scoped to a workspace, much like mail should be delivered to one apartment rather than opened in the building lobby. Most database reads are protected by row-level security, which means a task can only see data for the workspace it is currently bound to. But before a task can be bound, the dispatcher needs to know which workspaces have work due. This file defines that narrow exception.

The key idea is: a candidate lookup may look across workspaces, but it may only return workspace IDs, not business data. The `owner_candidates` function takes a small query-building function from an extension. That builder creates a database query selecting distinct `workspace_id` values from the extension’s own tables. `owner_candidates` wraps it in an async callable that runs the query through `owner_tx`, the special cross-workspace database path.

The query is rebuilt each time the scheduler checks for work. That matters for time-based jobs: “due now” depends on the current time, so the cutoff must be freshly calculated instead of frozen when the extension was loaded. The result is a tuple of workspace IDs. Later, the dispatcher opens each workspace context and runs the actual handler there, so the privileged read is only used to decide where work exists, not to perform the work itself.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: This function turns a workspace-ID query builder into a safe candidate finder for scheduled jobs. Extension code can say “these are the workspaces that may have work” without being allowed to directly use the privileged cross-workspace database connection.

**Data flow**: It receives `due`, a function that can build a database query selecting one value per row: a workspace ID. It returns a new async function, `candidates`, which will build and run that query later whenever the scheduler asks. Nothing is read immediately; the output is a reusable callable that produces workspace IDs on demand.

**Call relations**: This is the public seam used by code that needs to declare job candidates. It creates and returns `owner_candidates.candidates`, which is the function the scheduler can call on each tick to get the current set of workspaces with pending work.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner async function actually performs the candidate lookup. It runs the freshly built query through the special owner-level database path and returns only the workspace IDs from the first column of the result rows.

**Data flow**: When called, it opens `owner_tx`, the privileged database transaction that can read across workspaces. It asks `due()` to build the current query, executes it, collects all returned rows, closes the privileged transaction, and turns the first value from each row into a tuple of workspace UUIDs. It does not return any tenant row data beyond those IDs.

**Call relations**: This function is produced by `owner_candidates` and later called by the dispatcher or scheduler when it needs to know where a job has work. Its only direct handoff is to `ufo.db.owner_tx`, which provides the cross-workspace read channel; after this function returns IDs, later code is expected to bind each workspace before running any job handler.

*Call graph*: 1 external calls (owner_tx).


### `core/src/ufo/runtime/jobs.py`

`orchestration` · `startup and scheduled background job execution`

This file is the background-jobs hub for the system. A job might sync external sources, dispatch waiting conversation turns, process changed pages, deliver finished subagent results, or run extension-provided work. Without this file, those tasks would either never run, run in the wrong workspace, or pile up duplicate work after restarts.

The code uses DBOS, a durable workflow system that stores queued work so it survives process crashes. At startup, JobRunner.launch registers recurring schedules and enqueues one-time jobs. When a schedule fires, job_tick asks the right job which workspaces actually have work. It then queues a separate job_workflow for each workspace. This is like a mail sorter: first decide which buildings have mail, then send one carrier to each building instead of making one carrier visit the whole city.

JobRunner.fire is the single path that actually runs a job handler. It binds the current workspace, provisions agents if needed, builds an ExtensionContext with storage, models, blobs, page feeds, and other tools, then calls the handler. TurnDispatcher focuses on stuck or parked conversation turns. PageChangeRunner focuses on changed pages and gives each page-change hook its own cursor, so one slow hook does not block another.

#### Function details

##### `ResultDeliverer.run`  (lines 75–75)

```
async def run(self) -> None
```

**Purpose**: This is the promised shape of a result-delivery sweep. Anything used as a ResultDeliverer must provide this method to deliver completed child or subagent results back into the main conversation flow.

**Data flow**: It takes no explicit input beyond the object implementing the protocol. When called, the implementer is expected to look for finished results and post or record their arrival; this protocol method itself does not define the details or return a value.

**Call relations**: core_jobs wraps this method in a core scheduled job. JobRunner later runs that job like any other workspace-scoped background job, without this file needing to import the turn-loop implementation directly.


##### `ResultDeliverer.candidate_workspaces`  (lines 77–77)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This is the promised way for a result-delivery sweep to say which workspaces have results waiting. It lets the job runner avoid opening workspaces with nothing to do.

**Data flow**: It reads whatever state the concrete deliverer owns, finds workspace IDs that may need result delivery, and returns them as a tuple. The protocol only states the contract; the actual lookup lives in the implementing object.

**Call relations**: core_jobs uses this as the candidate finder for the result-delivery job. When DBOS fires the job tick, JobRunner asks this method where to enqueue per-workspace executions.


##### `TurnDispatcher.run`  (lines 142–176)

```
async def run(self) -> None
```

**Purpose**: This scans for conversation turns that are ready, stuck, or parked and safely offers them to the turn-processing queue. It prevents waiting turns from being forgotten while also respecting seats, spending rules, and account balance gates.

**Data flow**: It first reads a small batch of dispatchable turn rows. For parked turns, it checks whether the relevant members have seats, whether spending is allowed, and whether the balance gate admits the run. Turns that pass are stamped and enqueued; turns that fail remain waiting.

**Call relations**: The scheduled turn-dispatch core job calls this method through core_jobs. It relies on _dispatchable_turns to find possible work and _enqueue to claim and offer each turn safely.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 6 external calls (__init__, __init__, __init__, select, workspace_tx, gate_member).


##### `TurnDispatcher.candidate_workspaces`  (lines 178–186)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This finds workspaces that currently contain at least one turn the dispatcher might need to recover or resume. It keeps the scheduled job focused on workspaces with real pending turn work.

**Data flow**: It computes a cutoff time for stale dispatch stamps, queries the owner-level database view for distinct workspace IDs with eligible queued or parked turns, and returns those IDs.

**Call relations**: JobRunner.tick calls this through the JobSpec candidates hook for the turn-dispatch job. The eligibility test is shared with the workspace-local scan so the broad fleet scan and the actual dispatcher agree.

*Call graph*: calls 1 internal fn (_eligible); 4 external calls (now, timedelta, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 188–227)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: This picks the next turns in the current workspace that are eligible to be dispatched. It limits the batch so one sweep cannot monopolize the worker.

**Data flow**: It reads turn and conversation rows inside the current workspace, filters to stale queued or parked turns that are first in their conversation for that status, orders them fairly, and converts the rows into _DispatchTurn objects.

**Call relations**: TurnDispatcher.run calls this before making gate checks and enqueue decisions. It uses the same _eligible rule as candidate_workspaces so a workspace selected by the outer job has matching local work.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 6 external calls (__init__, now, timedelta, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 229–259)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: This safely claims one turn for dispatch and offers it to the durable turn queue. Its main job is to stop two sweepers from enqueueing the same turn at the same time.

**Data flow**: It receives a _DispatchTurn, checks that the database row is still in the same status, still stale, and still first in order, then stamps it as enqueued. If the stamp succeeds, it sends a DBOS enqueue request with a workflow ID and a conversation partition key; if not, it does nothing.

**Call relations**: TurnDispatcher.run calls this after any needed gate checks pass. The turn worker later claims the queued workflow, while this method’s database stamp protects against duplicate external offers.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 1 (run); 5 external calls (now, timedelta, update, workspace_tx, uuid4).


##### `TurnDispatcher._eligible`  (lines 261–269)

```
def _eligible(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for turns that may be dispatched. It encodes the rule that only stale queued or parked turns, and only the earliest such turn in each conversation, are eligible.

**Data flow**: It receives a cutoff time and returns a SQL condition. That condition combines status checks, stale-dispatch checks, and conversation-order checks into one filter used by database queries.

**Call relations**: candidate_workspaces uses it for the fleet-wide workspace scan, and _dispatchable_turns uses it inside a workspace. Sharing this rule keeps the broad scan and the actual dispatch pass consistent.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 2 external calls (and_, or_).


##### `TurnDispatcher._stale`  (lines 271–275)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database test for a dispatch offer that is missing or old enough to retry. It is the grace-window safety valve after a process crashes between stamping and enqueueing.

**Data flow**: It receives a cutoff time and returns a SQL condition that is true when the turn has no dispatch timestamp or has one older than the cutoff.

**Call relations**: _eligible uses it to decide which rows can be found, and _enqueue uses it again when claiming a specific row. The second check closes the race where another process updated the row after the scan.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 277–286)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database rule that a turn cannot overtake an earlier turn in the same conversation and same status. It preserves conversation order.

**Data flow**: It receives a turn status and returns a SQL condition that says no earlier turn with that status and a lower sequence number exists in the same workspace and conversation.

**Call relations**: _eligible uses this to select only the first queued or parked turn. _enqueue repeats the same guard during the claim so a stale scan cannot accidentally skip ahead.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `_page_beyond_cursor`  (lines 289–294)

```
def _page_beyond_cursor(revision: int, page_id: UUID, cursor: object) -> bool
```

**Purpose**: This answers whether a page change comes after a saved page-change cursor. It is used to decide if a workspace has new page work for a specific consumer.

**Data flow**: It receives a page revision, page ID, and stored cursor. With no cursor, it says the page is pending. With a cursor, it parses the cursor and compares revision first, then page ID, returning true only when the page is newer.

**Call relations**: PageChangeRunner.workspaces_with_changes calls this while checking each workspace’s newest page against that consumer’s saved cursor.

*Call graph*: called by 1 (workspaces_with_changes); 1 external calls (page_cursor).


##### `PageChangeConsumer.spec_name`  (lines 315–317)

```
def spec_name(self) -> str
```

**Purpose**: This creates the job name for one page-change consumer. The name includes the extension and handler discriminator so each hook gets its own scheduled job.

**Data flow**: It reads the consumer’s extension name and discriminator and formats them into a string such as a page-change job key segment. It does not change state.

**Call relations**: core_jobs uses this property when building JobSpec objects for page-change hooks. The separate names let two hooks from the same extension run independently.


##### `PageChangeConsumer.job`  (lines 320–325)

```
def job(self) -> str
```

**Purpose**: This creates the full attribution key for a page-change consumer’s model and job activity. It marks the runner as core-owned while still naming the extension hook being driven.

**Data flow**: It reads the consumer’s generated spec name and prefixes it with the core extension namespace. The result is a stable string used as the job key in contexts and metrics.

**Call relations**: PageChangeRunner._context_for uses this value when building the ExtensionContext for a hook. That lets spending, latency, and model-use reporting distinguish one page-change hook from another.


##### `PageChangeRunner.consumers`  (lines 368–392)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: This discovers all registered page-change hooks from the active extension manifests. It turns hook declarations into PageChangeConsumer records that can each become a scheduled job.

**Data flow**: It scans every manifest, keeps only hooks whose event is page_change, records each extension’s declared credential slots, and checks that two hooks in the same extension do not share the same handler name. It returns the resulting consumers.

**Call relations**: core_jobs calls this when assembling the always-on job list. If it finds duplicate hook discriminators, it raises early so cursor and job names cannot collide later.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 394–459)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: This finds which workspaces have page changes that a particular consumer has not processed yet. It avoids launching page-change work for quiet workspaces.

**Data flow**: It reads each workspace’s newest page and that consumer’s stored cursor from the database. For each workspace, it compares the newest page to the cursor; if the cursor is missing, invalid, or behind, the workspace is returned as pending.

**Call relations**: The per-consumer JobSpec uses this as its candidate finder. JobRunner.tick calls it before enqueueing per-workspace page-change workflows, and it relies on _page_beyond_cursor for the cursor comparison.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 3 external calls (select, owner_tx, warn).


##### `PageChangeRunner.drive`  (lines 461–511)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: This runs one page-change consumer inside the currently bound workspace. It replays changed pages in batches, calls the hook, and advances that hook’s cursor only after the hook succeeds.

**Data flow**: It builds the extension context, reads the stored cursor, asks the page feed for changes after that cursor, and passes each batch to the hook as a PageChangeBatch. After a successful handler call, it updates the cursor with a compare-and-set write; on failure, it logs and leaves the cursor unchanged for retry.

**Call relations**: core_jobs creates a wrapper handler that calls this for each PageChangeConsumer. JobRunner.fire supplies the workspace binding first, so drive only works on one workspace at a time.

*Call graph*: calls 1 internal fn (_context_for); 6 external calls (__init__, __init__, emit_metric, formatted_stack, log_error, ws_current).


##### `PageChangeRunner._context_for`  (lines 513–531)

```
def _context_for(self, consumer: PageChangeConsumer) -> ExtensionContext
```

**Purpose**: This builds the ExtensionContext used by a page-change hook. The context is the hook’s toolbox: storage, pages, model access, blob access, optional turn invocation, and observability probes.

**Data flow**: It reads the current workspace ID, optionally creates a turn invoker for that workspace, adjusts the model registry for background jobs if configured, and passes all pieces into context_for. It returns the finished ExtensionContext.

**Call relations**: PageChangeRunner.drive calls this before invoking a hook. It delegates model-default adjustment to _background_registry so page-change jobs use the same background-model rule as other jobs.

*Call graph*: calls 1 internal fn (_background_registry); called by 1 (drive); 2 external calls (context_for, ws_current).


##### `_background_registry`  (lines 534–545)

```
def _background_registry(registry: ModelRegistry | None, background_model: str | None) -> ModelRegistry | None
```

**Purpose**: This returns the model registry a background job should use. If a background model is configured, it replaces the registry’s automatic default model with that background model.

**Data flow**: It receives an optional registry and optional background model name. If either is missing, it returns the original registry; otherwise it returns a copy of the registry with its auto model changed.

**Call relations**: JobRunner.fire uses this for most jobs, while PageChangeRunner._context_for uses it for page-change hooks. Jobs that explicitly need the deploy’s normal model bypass this adjustment.

*Call graph*: called by 2 (fire, _context_for); 1 external calls (replace).


##### `core_jobs`  (lines 548–640)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, page_change_runner: PageChangeRunner, delivery_sweep: ResultDeliverer, preview_renderer: PreviewRenderer | None) -> tuple[JobSpe
```

**Purpose**: This builds the list of built-in jobs that every deployment should know about. These include source sync, page-change consumers, turn dispatch, result delivery, and optionally preview rendering.

**Data flow**: It receives the concrete services that do the work, wraps each service method in a JobSpec handler and candidate finder, adds one page-change JobSpec for each discovered consumer, and returns the complete tuple of core JobSpec objects.

**Call relations**: Startup code can pass this result into bindings_from along with extension jobs. The nested wrapper functions are later called by JobRunner.fire through their JobSpec handlers.

*Call graph*: calls 1 internal fn (consumers); 1 external calls (__init__).


##### `core_jobs._sync_sources`  (lines 568–569)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: This small wrapper runs the source synchronization driver as a job handler. It lets source sync fit the common JobSpec handler shape.

**Data flow**: It receives an ExtensionContext because all job handlers have that shape, but it does not use it. It calls the sync driver’s run method and returns when syncing finishes.

**Call relations**: core_jobs installs this wrapper in the source-sync JobSpec. JobRunner.fire eventually calls it inside each candidate workspace.


##### `core_jobs._dispatch_turns`  (lines 571–572)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: This small wrapper runs the turn dispatcher as a job handler. It adapts TurnDispatcher.run to the common job interface.

**Data flow**: It receives the job context, ignores it, calls turn_dispatcher.run, and returns when the dispatch sweep is done.

**Call relations**: core_jobs installs it in the turn-dispatch JobSpec. JobRunner.fire calls it after binding a workspace selected by TurnDispatcher.candidate_workspaces.


##### `core_jobs._deliver_results`  (lines 574–575)

```
async def _deliver_results(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs the result-delivery sweep as a job handler. It makes the external deliverer implementation look like a normal core job.

**Data flow**: It receives but does not use the ExtensionContext, calls delivery_sweep.run, and completes when result delivery has been attempted.

**Call relations**: core_jobs puts this into the result-delivery JobSpec. The actual work lives behind the ResultDeliverer protocol, keeping this file from depending on the turn-loop internals.


##### `core_jobs._render_previews`  (lines 577–579)

```
async def _render_previews(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs preview rendering when preview rendering is enabled. It fits the preview renderer into the shared job system.

**Data flow**: It receives the standard job context, checks that a preview renderer exists, calls its run method, and returns after preview work is done.

**Call relations**: core_jobs includes this handler only when a PreviewRenderer was provided. JobRunner.fire calls it for workspaces returned by the matching candidate function.


##### `core_jobs._preview_candidates`  (lines 581–583)

```
async def _preview_candidates() -> tuple[UUID, ...]
```

**Purpose**: This wrapper asks the preview renderer which workspaces need preview work. It exists so preview rendering can participate in the same candidate-driven fan-out as other jobs.

**Data flow**: It checks that the preview renderer exists, asks it for candidate workspace IDs, and returns those IDs.

**Call relations**: core_jobs uses this as the candidate finder for the optional preview-rendering JobSpec. JobRunner.tick calls it before enqueueing preview workflows.


##### `core_jobs._drive_consumer`  (lines 585–591)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: This creates a job handler for one page-change consumer. It captures which consumer should be driven when the scheduled job runs.

**Data flow**: It receives a PageChangeConsumer and returns an async handler function. That returned handler will later call PageChangeRunner.drive for the captured consumer.

**Call relations**: core_jobs calls this while building one JobSpec per page-change hook. The returned handler is what JobRunner.fire invokes inside a workspace.


##### `core_jobs._drive_consumer._handler`  (lines 588–589)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: This is the actual handler produced for a specific page-change consumer. It tells PageChangeRunner to process that consumer’s pending page changes in the current workspace.

**Data flow**: It receives the standard ExtensionContext but relies on PageChangeRunner.drive to build the hook-specific context. It passes along the captured consumer and returns when that consumer’s cursor loop finishes.

**Call relations**: JobRunner.fire calls this through the page-change JobSpec. It hands control to PageChangeRunner.drive, which performs batching, hook invocation, and cursor advancement.


##### `core_jobs._consumer_candidates`  (lines 593–597)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: This creates a candidate finder for one page-change consumer. It captures which hook’s cursor should be checked.

**Data flow**: It receives a PageChangeConsumer and returns an async function. That returned function will ask PageChangeRunner which workspaces have changes beyond this consumer’s cursor.

**Call relations**: core_jobs uses it when building page-change JobSpecs. JobRunner.tick later calls the returned candidate function before enqueueing per-workspace page-change work.


##### `core_jobs._consumer_candidates._candidates`  (lines 594–595)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: This is the actual candidate finder for a specific page-change consumer. It returns only workspaces where that consumer has pending page changes.

**Data flow**: It takes no explicit input, calls PageChangeRunner.workspaces_with_changes for the captured consumer, and returns the workspace IDs.

**Call relations**: JobRunner.tick calls this through the JobSpec candidates field. It hands the workspace list back to the job runner, which enqueues one workflow per workspace.


##### `bindings_from`  (lines 652–683)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...], disabled: frozenset[str]=frozenset()) -> tuple[_Binding, ...]
```

**Purpose**: This combines core jobs and extension jobs into a single keyed list the runner can use. It also applies the disabled-jobs list and catches disabled names that do not exist.

**Data flow**: It receives manifests, core JobSpecs, and an optional set of disabled job keys. It creates core bindings under the core namespace and extension bindings under each extension’s namespace, validates disabled keys, removes disabled bindings, and returns the rest.

**Call relations**: Startup code uses this before constructing JobRunner. JobRunner later looks up these bindings by key whenever a schedule ticks or a workflow fires.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 713–737)

```
def launch(self) -> None
```

**Purpose**: This publishes all jobs to DBOS at process startup. Recurring jobs become schedules, while one-shot jobs are enqueued once with deduplication so two replicas do not start duplicates.

**Data flow**: It stores this JobRunner in the module-level firing pointer, walks every binding, and either enqueues an immediate tick for unscheduled jobs or builds schedule records for cron-style jobs. It then asks DBOS to apply the schedules.

**Call relations**: The server startup path calls this before job_tick or job_workflow can run. Those DBOS workflow functions later use the stored runner to route each firing back into this instance.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 739–761)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: This handles one scheduled firing of a job key. It fans the job out into one durable workflow per workspace that actually has work.

**Data flow**: It receives the scheduled time and job key. If this process does not know that key, it logs a warning and stops. Otherwise it asks for candidate workspaces, then enqueues job_workflow for each workspace with a deduplication ID based on job and workspace.

**Call relations**: job_tick calls this when DBOS fires a schedule. It uses candidates to get the workspace list and relies on DBOS queue deduplication so a still-running workspace execution absorbs overlapping ticks.

*Call graph*: calls 2 internal fn (_registered, candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 763–764)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: This asks the registered job for the workspaces it should run in. It is a thin lookup layer around the JobSpec’s candidate function.

**Data flow**: It receives a job key, finds the matching binding, calls that binding’s candidate function, and returns the workspace IDs it produced.

**Call relations**: JobRunner.tick calls this after confirming the key is registered. If the key is not bound, _binding raises because the runner cannot safely decide what to do.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 766–800)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: This is the only place a job handler actually runs. It binds the workspace, prepares the extension context, provisions agents once per workspace per process, and calls the handler.

**Data flow**: It receives a job key and workspace ID, finds the binding, enters that workspace context, applies agent provisioning if this process has not done so for the workspace, builds the ExtensionContext, and awaits the job handler. If the handler fails, it logs the job key, error class, stack, and any failed SQL statement information, then re-raises the error.

**Call relations**: job_workflow calls this for each queued per-workspace execution. It uses _binding for the job definition and _background_registry unless the job explicitly needs the deploy’s normal model.

*Call graph*: calls 2 internal fn (_binding, _background_registry); 6 external calls (__init__, failed_statement, context_for, formatted_stack, log_error, ws).


##### `JobRunner._registered`  (lines 802–803)

```
def _registered(self, key: str) -> _Binding | None
```

**Purpose**: This checks whether the current process has a binding for a job key. It is the forgiving lookup used when an old shared schedule names a job this process no longer runs.

**Data flow**: It receives a key, searches the runner’s bindings, and returns the matching binding or None. It does not change state.

**Call relations**: JobRunner.tick uses this to skip unknown schedule firings safely. JobRunner._binding uses it as the first step for stricter lookups.

*Call graph*: called by 2 (_binding, tick).


##### `JobRunner._binding`  (lines 805–812)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: This returns the binding for a job key and treats a missing binding as a real error. It is used once the code is on a path that must have a runnable job.

**Data flow**: It receives a key, calls _registered, and returns the binding if found. If not found, it raises an error explaining that no job is registered for that key.

**Call relations**: JobRunner.candidates and JobRunner.fire call this because they cannot proceed without the JobSpec and extension information. JobRunner.tick uses the softer _registered check before reaching these paths.

*Call graph*: calls 1 internal fn (_registered); called by 2 (candidates, fire).


##### `job_tick`  (lines 819–823)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: This is the DBOS durable workflow entry for a scheduled job tick. It bridges DBOS schedule firing into the in-memory JobRunner.

**Data flow**: It receives the scheduled time and job key from DBOS, reads the module-level runner set by JobRunner.launch, and calls runner.tick. If launch has not happened, it raises an error.

**Call relations**: JobRunner.launch registers this function with DBOS schedules and one-shot enqueues. DBOS calls it, and it hands control to JobRunner.tick for candidate fan-out.


##### `job_workflow`  (lines 827–831)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: This is the DBOS durable workflow entry for one job running in one workspace. It bridges queued per-workspace work into JobRunner.fire.

**Data flow**: It receives the scheduled time, job key, and workspace ID as a string. It finds the launched runner, converts the workspace ID into a UUID, and calls runner.fire; if no runner was launched, it raises an error.

**Call relations**: JobRunner.tick enqueues this workflow for each candidate workspace. DBOS later runs it from the jobs queue, and JobRunner.fire performs the actual workspace-scoped handler call.

*Call graph*: 1 external calls (UUID).


### `core/src/ufo/loop/delivery.py`

`orchestration` · `periodic background sweep`

When one agent delegates work to a child agent, the parent expects to be woken when the child finishes. Normally that happens as part of the child’s own execution. But some endings happen from the outside, such as another process cancelling the child, or a crash happening after the child’s final state was saved but before the wake-up was posted. Without this file, the parent could wait forever even though the child’s result is already safely stored.

`DeliverySweep` is the periodic backstop. Think of it like a mailroom clerk checking for completed letters that were stamped but never actually delivered. It looks in the database for child turns that are terminal, meaning finished, but still marked as waiting for result delivery. It groups them by the parent conversation, so if several child jobs finished around the same time, the parent can be woken once with the whole batch rather than repeatedly.

It also avoids thrashing. If a conversation was already woken recently, the sweep skips it for this pass and leaves its pending children for the next tick. If the parent agent has been archived, delivery is skipped without blocking other conversations; if that app is restored later, the pending result can still be delivered.

#### Function details

##### `DeliverySweep.run`  (lines 54–72)

```
async def run(self) -> None
```

**Purpose**: Runs one pass of the result-delivery safety sweep. It finds child turns whose finished results have not reached their parent conversation, skips conversations that were woken very recently, and asks `SubagentResult` to deliver each remaining child result.

**Data flow**: It starts with no direct input other than the current workspace and the sweep’s stored helpers. It reads outstanding finished child turns from the database, checks which parent conversations were already woken within the cooldown window, builds a result-delivery helper for the current workspace, and then attempts delivery child by child. The outcome is database and conversation state being updated through the delivery path; if there is nothing to deliver, it simply returns.

**Call relations**: This is the main body of the sweep. It first calls `DeliverySweep._outstanding` to learn what needs delivery, then calls `DeliverySweep._woken_since` to avoid waking the same conversation too often. For each safe child turn, it hands off to `SubagentResult.deliver`. If delivery finds that the parent agent was archived, the exception is swallowed so the sweep can continue with other work.

*Call graph*: calls 2 internal fn (_outstanding, _woken_since); 4 external calls (__init__, now, timedelta, ws_current).


##### `DeliverySweep.candidate_workspaces`  (lines 74–93)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces have at least one finished child result waiting to be delivered to an active, non-archived parent agent. A scheduler can use this so it only runs the sweep where there is likely work to do.

**Data flow**: It opens an owner-level database transaction, which can see across workspaces. It searches turn records for child turns marked as pending delivery and already finished, joins them to their parent turns and parent agents, filters out archived agents, and returns the distinct workspace IDs that match.

**Call relations**: This supports the job scheduling side of the sweep. Rather than running `DeliverySweep.run` blindly everywhere, outside job code can ask this function which workspaces are candidates. It uses SQLAlchemy to build the query and `owner_tx` to run it against the owner-wide database view.

*Call graph*: 2 external calls (select, owner_tx).


##### `DeliverySweep._outstanding`  (lines 95–131)

```
async def _outstanding(self) -> dict[UUID, list[Turn]]
```

**Purpose**: Collects the actual child turns in the current workspace that are finished but whose results are still marked as not delivered. It groups them under the parent conversation that should be woken.

**Data flow**: It opens a workspace database transaction and selects child turn rows whose delivery status is pending and whose terminal state is present. It joins each child to its parent turn and parent agent, ignores archived parent agents, orders the rows by parent conversation and child finish time, and limits the batch size. It then turns each database row into a `Turn` object and returns a dictionary from parent conversation ID to a list of child turns.

**Call relations**: `DeliverySweep.run` calls this at the start of a sweep pass. The grouped result lets `run` treat a parent conversation’s outstanding child results as a set, which helps combine a fan-out of many children into fewer parent wake-ups.

*Call graph*: called by 1 (run); 3 external calls (model_validate, select, workspace_tx).


##### `DeliverySweep._woken_since`  (lines 133–160)

```
async def _woken_since(self, cutoff: datetime, conversations: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: Checks which of a given set of parent conversations have already received a delivered child result recently. This prevents the sweep from waking the same conversation again too soon.

**Data flow**: It receives a cutoff time and a tuple of conversation IDs. It queries the workspace database for delivered child turns whose parent conversation is in that set and whose update time is newer than the cutoff. It returns those conversation IDs as a frozen set, meaning a read-only collection.

**Call relations**: `DeliverySweep.run` calls this after finding outstanding work. The answer tells `run` which conversations to skip for this pass. The check works whether the recent wake came from the normal event path or from an earlier sweep, because both paths mark child turns as delivered with an updated timestamp.

*Call graph*: called by 1 (run); 2 external calls (select, workspace_tx).


### Extension job bodies
Individual extensions provide the recurring work performed by the scheduler, including report digest writing, self-improvement checks, and homepage cleanup.

### `extensions/report_digest/ufo_ext_report_digest/writer.py`

`domain_logic` · `scheduled job tick and rebuild`

Scheduled app runs can publish Markdown reports. This file is the worker that reads those reports, asks a language model to summarize what changed, and stores the result as a digest row. Without it, the feed would only know that a report exists; it would not have a concise title, summary, or bullet points explaining why the report matters.

The main class, DigestWriter, works like a careful mail sorter. On each scheduled tick it asks the database for a small batch of recent reports that have not already been read. It only looks at successful scheduled runs, only Markdown reports, and only reports from the last seven days. That window prevents old broken reports from being retried forever.

For each report, it reads only a capped amount of bytes from blob storage, then trims the text again before sending it to the model. It builds a plain description of who the reader is, asks the model to return a structured DigestEntry, and stores either the digest or a note saying the report contained no real change. That “unchanged” note matters because it stops the same quiet report from costing another model call every tick.

Failures are intentionally isolated. If one blob is missing or one model answer is unusable, the writer skips that report and continues with the rest of the batch. DigestRebuild is a reset tool: it deletes recent digest rows and unchanged notes so they can be regenerated under a new standard.

#### Function details

##### `DigestWriter.run`  (lines 117–126)

```
async def run(self) -> None
```

**Purpose**: Runs one digest-writing tick. It finds reports that still need digest entries and tries to process each one without letting one failure stop the rest.

**Data flow**: It starts with the writer's context, model access, and blob store. It asks _unwritten for a small list of report records, then sends each report to _digest. It returns nothing, but may cause new digest rows or unchanged-marker rows to be written to the database.

**Call relations**: _unwritten supplies the due work for this tick. For each report, run calls _digest. If _digest raises an error, run catches it and moves on, so a single bad report does not keep every later report from being processed.

*Call graph*: calls 2 internal fn (_digest, _unwritten).


##### `DigestWriter._digest`  (lines 128–139)

```
async def _digest(self, report: Report) -> None
```

**Purpose**: Processes one report from start to finish. It reads the report, asks the model for a digest, and stores either a digest entry or a marker saying there was no meaningful change.

**Data flow**: It receives a Report containing the turn id, blob key, app name, audience, and possible owner email. It reads the report body through _body, decides who the digest is for through _reader, asks _written for a structured model result, then writes either a digest row with _store or an unchanged row with _store_unchanged. If the body is missing or the model does not return a usable digest, it writes nothing.

**Call relations**: DigestWriter.run calls this for each candidate report. _digest is the central handoff point: it gets text from blob storage through _body, prepares reader context through _reader, gets the model result through _written, and then chooses the proper database write.

*Call graph*: calls 5 internal fn (_body, _reader, _store, _store_unchanged, _written); called by 1 (run).


##### `DigestWriter._unwritten`  (lines 141–213)

```
async def _unwritten(self) -> tuple[Report, ...]
```

**Purpose**: Finds the reports that are eligible to be digested right now. It filters for recent successful scheduled runs with Markdown reports that have not already received a digest or an unchanged marker.

**Data flow**: It reads the current workspace id from the extension context and builds a database query. The query looks across turns, conversations, agents, members, and shared artifacts, picks the first Markdown report for each run, skips old or already-read reports, orders newest first, and limits the batch size. It returns a tuple of Report objects ready for processing.

**Call relations**: DigestWriter.run calls this at the start of a tick to learn what work exists. The Report objects it creates are then passed one by one into _digest.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `DigestWriter._body`  (lines 215–228)

```
async def _body(self, report: Report) -> str | None
```

**Purpose**: Reads the report text from blob storage safely. It protects the worker and the model bill from huge files by reading only a fixed maximum amount.

**Data flow**: It receives a Report with a blob key. It streams bytes from the blob store until it reaches the read limit, decodes those bytes into text while replacing invalid characters, and passes the text through bounded to trim it to the model payload limit. It returns the trimmed text, or None if the blob is missing.

**Call relations**: _digest calls this before doing anything else with a report. If _body returns None, _digest stops, because there is no report text to summarize.

*Call graph*: called by 1 (_digest); 1 external calls (bounded).


##### `DigestWriter._reader`  (lines 230–239)

```
def _reader(self, report: Report) -> str
```

**Purpose**: Creates a short plain-language description of who will read the digest. This helps the model write the summary for the right audience.

**Data flow**: It receives a Report and looks at its audience and owner email. If the report belongs to a specific member conversation, it describes that member as the reader. Otherwise, it describes the whole workspace as the reader. It returns that description as a string.

**Call relations**: _digest calls this after reading the report body. The returned reader description is passed into _written and later stored with the digest by _store.

*Call graph*: called by 1 (_digest).


##### `DigestWriter._written`  (lines 241–278)

```
async def _written(self, body: str, reader: str) -> DigestEntry | None
```

**Purpose**: Asks the language model to turn a report into a structured digest entry. It accepts only the model's tool-style structured answer, not free-form prose.

**Data flow**: It receives the report body and reader description. It builds a model request containing the writing instructions, the report and reader as JSON, a token limit, and a required tool schema based on DigestEntry. It sends that request through model.turn. If the model returns a valid tool call, it validates the tool input into a DigestEntry and returns it; otherwise it returns None.

**Call relations**: _digest calls this after preparing the body and reader. It relies on writing_standard for instructions and DigestEntry's schema for the shape of the answer. Its result tells _digest whether to store a digest, store an unchanged marker, or skip the report for now.

*Call graph*: called by 1 (_digest); 7 external calls (__init__, __init__, __init__, model_json_schema, model_validate, dumps, writing_standard).


##### `DigestWriter._store_unchanged`  (lines 280–286)

```
async def _store_unchanged(self, report: Report) -> None
```

**Purpose**: Records that a report was read but did not contain a change worth digesting. This saves future ticks from paying to read and summarize the same quiet report again.

**Data flow**: It receives a Report. Inside a database transaction, it inserts the workspace id and turn id into the report_digest_unchanged table. It returns nothing, but the database now remembers that this report is settled.

**Call relations**: _digest calls this when the model returns a DigestEntry whose holds_a_change flag is false. Later, _unwritten and undigested_workspaces use this marker to skip the report.

*Call graph*: called by 1 (_digest); 1 external calls (insert).


##### `DigestWriter._store`  (lines 288–301)

```
async def _store(self, report: Report, entry: DigestEntry, reader: str) -> None
```

**Purpose**: Stores a completed digest entry for a report. This is what makes the summarized report available to the feed.

**Data flow**: It receives the original Report, the validated DigestEntry, and the reader description. Inside a database transaction, it inserts the workspace id, turn id, title, summary, bullet points, reader, model name, and current write time into the report_digest_entry table. It returns nothing, but a new digest row is saved.

**Call relations**: _digest calls this when the model says the report contains a meaningful change. Later candidate searches treat this row as proof that the report has already been read.

*Call graph*: called by 1 (_digest); 2 external calls (now, insert).


##### `DigestRebuild.run`  (lines 321–339)

```
async def run(self) -> int
```

**Purpose**: Makes recent reports eligible to be digested again. This is useful when the digest-writing rules or model instructions change and recent entries should be rebuilt.

**Data flow**: It reads the workspace id from the context and defines the same recent time window used by the writer. In one transaction, it deletes digest rows and unchanged-marker rows for turns in that window. It returns the total number of rows deleted.

**Call relations**: This is separate from the normal DigestWriter tick. After it removes the writer's own records, the next writer run can discover those reports again through _unwritten and regenerate their digest entries.

*Call graph*: 3 external calls (now, delete, select).


##### `undigested_workspaces`  (lines 342–366)

```
def undigested_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds a database query that finds workspaces with at least one report still needing a digest. A scheduler can use this to avoid waking the writer for quiet workspaces.

**Data flow**: It creates a SQL query over turns and shared artifacts. The query looks for recent successful scheduled runs with Markdown reports, excludes turns that already have digest entries or unchanged markers, groups the results by workspace id, and returns the query object rather than executing it.

**Call relations**: This function mirrors the same due-work rules used by DigestWriter._unwritten, but at the workspace level. A higher-level scheduler can run this query first, then start DigestWriter only for workspaces that actually have pending reports.

*Call graph*: 2 external calls (now, select).


### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled background tick`

This file is the safety gate for automatic prompt improvement. A prompt is the instruction text an agent follows. The code here does not directly change an agent’s prompt. Instead, it periodically reviews saved agent activity, asks a proposer to suggest a better prompt, tests that suggestion on held-back examples, and then opens a governed proposal that a human or member process must approve.

The main object is `ImproveCron`, a scheduled worker. On each run, it gathers trajectories, meaning records of past agent conversations and results, and groups them by agent. For each agent, it keeps at most one candidate prompt for the current prompt version. That candidate is stored in the extension’s private store, like a notebook that remembers what is already being tested.

A candidate must pass evaluation more than once in a row before it can be promoted. This avoids changing prompts because of one lucky test result. If the candidate fails, it is marked rejected. If it passes enough times, this file submits an `AgentChange` proposal. A rejected or promoted candidate is not proposed again for the same original prompt digest, which is a fingerprint of the prompt version. This makes the loop conservative: it can suggest improvements, but it cannot silently rewrite the agent.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: Runs one full scheduled self-improvement pass. It collects all available agent trajectories, groups them by agent, and starts the improvement check for each agent separately.

**Data flow**: It asks the extension context for stored trajectories. It turns that flat list into groups keyed by agent ID. For each agent group, it passes the agent ID and that agent’s trajectories onward. It does not return a value; its effect is to advance candidate prompt testing and possibly open proposals through later steps.

**Call relations**: This is the top-level method for the cron tick. It calls `_by_agent` to sort the raw trajectory list into per-agent piles, then calls `ImproveCron._advance` once for each pile so each agent is considered independently.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Moves one agent’s self-improvement process forward by one step. It either finds or opens a candidate prompt for the agent’s current prompt version, then tests that candidate if one exists.

**Data flow**: It receives an agent ID and that agent’s trajectories. It reads the current prompt digest from the first trajectory and builds the store key used to remember this agent’s candidate. It then asks `_active_or_open` for an active candidate. If there is none, it stops. If there is one, it sends the candidate to `_gate` for evaluation and possible promotion.

**Call relations**: `ImproveCron.run` calls this after grouping trajectories by agent. `_advance` is the bridge between finding a candidate through `ImproveCron._active_or_open` and judging it through `ImproveCron._gate`.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: Finds the current candidate prompt for an agent, or creates a new one if appropriate. It prevents repeated proposals for a candidate that was already rejected or promoted for the same prompt version.

**Data flow**: It receives a store key, the current prompt digest, and the agent’s trajectories. First it checks the extension store for an existing candidate. If that candidate belongs to the same prompt digest and is still being evaluated, it returns it. If the stored candidate was already rejected or promoted, it returns nothing. If there is no usable candidate, it groups trajectories into task classes, asks the prompt proposer for a new prompt based on the current prompt and one task class, saves the new candidate with its held-out example IDs, and returns it.

**Call relations**: `ImproveCron._advance` calls this before any evaluation happens. It uses `task_classes` to find meaningful groups of past work and relies on the `PromptProposer` to suggest a possible improved prompt. When it creates a candidate, it records it in the scoped store so later cron ticks continue the same test rather than starting over.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Tests a candidate prompt and decides whether it should keep being tested, be rejected, or be sent forward as a formal change proposal. This is the main safety checkpoint in the file.

**Data flow**: It receives the agent ID, store key, original prompt digest, candidate state, and trajectories. It builds two test sets: the candidate’s own held-out examples, and held-out examples from other task classes to check that the new prompt does not hurt broader behavior. It sends these to the evaluator along with the candidate prompt and current prompt. If the evaluator says the candidate failed, it saves the candidate as rejected. If it passed but has not passed enough consecutive ticks yet, it saves the increased pass count and keeps evaluating. If it has passed enough times, it opens an `AgentChange` proposal and saves the candidate as promoted with the proposal ID.

**Call relations**: `ImproveCron._advance` calls this after a candidate is available. `_gate` calls `_held_out` to rebuild the candidate’s saved held-out examples from current trajectories, calls `task_classes` to gather broader comparison examples, calls the evaluation service to judge the candidate, and uses `ImproveCron._save` to persist the result. On final success, it hands the proposed prompt to the platform through `ctx.propose_change`.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: Writes an updated candidate state to the extension store. It is used whenever a candidate’s status, pass count, or proposal ID changes.

**Data flow**: It receives the store key, the existing candidate, a new status, a new gate-pass count, and optionally a proposal ID. It creates an updated copy of the candidate with those fields changed, converts it to JSON-friendly data, and stores it under the same key. It returns nothing, but the persisted record changes for future cron ticks.

**Call relations**: `ImproveCron._gate` calls this after each evaluation outcome. This keeps the candidate’s history consistent across scheduled runs, so the system remembers whether it is still evaluating, rejected, or already promoted.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: Groups a list of trajectories by the agent that produced them. This lets the self-improvement loop judge each agent separately instead of mixing their histories together.

**Data flow**: It receives a tuple of trajectories. It reads each trajectory’s agent ID, collects trajectories with the same agent ID into a group, and returns a mapping from each agent ID to that agent’s tuple of trajectories. It does not change the trajectories themselves.

**Call relations**: `ImproveCron.run` calls this at the start of a cron tick. The grouped result determines how many times `ImproveCron._advance` runs and what data each agent’s improvement check receives.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: Rebuilds the candidate’s saved held-out test examples from the current trajectories. Held-out examples are past cases kept aside for testing rather than for proposing the change.

**Data flow**: It receives the available trajectories and a tuple of saved conversation ID strings. It makes a lookup table from conversation ID to trajectory, then visits the saved IDs in order. If a matching trajectory still exists, it asks `bad_trajectory` whether that trajectory represents a useful failure example. When it does, it collects the corresponding `TaskExample`. It returns all collected examples as a tuple.

**Call relations**: `ImproveCron._gate` calls this when preparing the candidate-specific evaluation set. `_held_out` delegates the judgment of whether a trajectory is a bad example to `bad_trajectory`, then gives the resulting examples back to the gate for evaluation.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).


### `extensions/sites/ufo_ext_sites/main_homepage.py`

`domain_logic` · `scheduled background cleanup`

This file exists because of a subtle upgrade problem. Older setup code gave every agent, including a workspace’s main agent, its own hosted homepage row. Later, the chat app became the real owner of the main agent, and its homepage is served from the app bundle instead of from a database row. But if an old row is still bound to the main agent, the system finds that row first and shows it instead of the chat screen.

The file defines a scheduled sweep, like a janitor that walks through workspaces every few minutes. It looks only for workspaces where the main agent has clearly become the chat app’s declared agent, still has a bound homepage, and has not already been cleaned up. When it finds one, it releases that homepage binding so the bundled chat homepage can appear. Then it writes a small marker in extension storage saying this workspace has already been released.

The marker matters because members may later choose a homepage of their own. Without the marker, the sweep could keep removing pages that users deliberately set. The file also carefully chooses the visibility of the released page. Visibility means who can see it. It keeps the stricter of the page’s old setting and the agent’s setting, so cleanup never accidentally makes a private or workspace-only page more public.

#### Function details

##### `_the_chat_main_agent`  (lines 67–75)

```
def _the_chat_main_agent() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for the exact agent this cleanup cares about: the workspace’s main agent after it has been taken over by the chat app. This prevents the job from touching ordinary agents or a main agent before chat provisioning has actually landed.

**Data flow**: It reads no live data by itself. It creates a database condition saying: the agent must be marked as main, must have been provisioned by the chat extension, and must have the chat provisioned name. The result is a reusable filter that other database queries can include.

**Call relations**: The workspace candidate query uses this filter when looking for bound homepages to clean up. The release job uses the same filter before changing anything, so both the search step and the actual cleanup agree on which agent counts.

*Call graph*: called by 2 (release_main_homepage, with_a_bound_main_homepage); 1 external calls (and_).


##### `unreleased_main_homepage_workspaces`  (lines 78–97)

```
def unreleased_main_homepage_workspaces(extension: str) -> WorkspaceCandidates
```

**Purpose**: Declares which workspaces should be offered to the scheduled cleanup job. A workspace qualifies only if it still has a homepage bound to the chat-owned main agent and has not already been marked as released.

**Data flow**: It receives the extension name used for the release marker. It builds a candidate-producing database query through its inner helper, then wraps that query as workspace-owner candidates for the job system. The output is a job-friendly description of workspaces that still need this cleanup.

**Call relations**: This is the doorway between the job scheduler and the cleanup logic. It hands the job system a shrinking list: once `release_main_homepage` releases a binding and writes the marker, the workspace no longer appears as a candidate.

*Call graph*: 1 external calls (owner_candidates).


##### `unreleased_main_homepage_workspaces.with_a_bound_main_homepage`  (lines 84–95)

```
def with_a_bound_main_homepage() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query that finds workspaces with an old bound homepage on the chat-owned main agent. It also excludes workspaces that already carry the release marker.

**Data flow**: It starts from the hosted-site table, joins each hosted page to its agent, applies the chat-main-agent filter, and checks that no release marker exists in extension storage for that workspace. It returns workspace IDs, grouped so each workspace appears only once even if the table shape could otherwise repeat it.

**Call relations**: It is the detailed search used inside `unreleased_main_homepage_workspaces`. It relies on `_the_chat_main_agent` so the candidate search matches the later safety check done by `release_main_homepage`.

*Call graph*: calls 1 internal fn (_the_chat_main_agent); 4 external calls (exists, literal, select, join).


##### `released_visibility`  (lines 100–104)

```
def released_visibility(site: str, agent: str) -> Visibility
```

**Purpose**: Chooses how visible a page should be after it is unbound from the main agent. It keeps the narrower, safer visibility between the page’s own stored setting and the agent’s setting.

**Data flow**: It receives two visibility names: one from the hosted page and one from the agent. It converts each name into an ordered visibility level, compares them, and returns the more restrictive one. The result is used as the page’s visibility after release.

**Call relations**: The release job calls this just before unbinding the homepage. Its job is to make sure `release_main_homepage` does not accidentally publish a page to more people than could see it while it was attached to the agent.

*Call graph*: called by 1 (release_main_homepage); 1 external calls (visibility_level).


##### `release_main_homepage`  (lines 107–130)

```
async def release_main_homepage(ctx: ExtensionContext) -> None
```

**Purpose**: Performs the cleanup for one workspace: find the chat-owned main agent, release its bound homepage if one exists, and record that the workspace has been processed. This is the action run by the scheduled sweep.

**Data flow**: It receives an extension context, which gives it the current workspace, database transaction access, and extension storage. First it looks up the workspace’s chat-owned main agent. If there is none, it stops. If there is one, it asks `HostedSites` for the page currently bound as that agent’s homepage. If a bound page exists, it releases the binding and sets the page’s resumed visibility using `released_visibility`. Finally, it writes a release marker containing the main agent ID.

**Call relations**: This is the worker that acts on workspaces selected by `unreleased_main_homepage_workspaces`. It uses `_the_chat_main_agent` as a safety gate, calls into `HostedSites` to read and release the homepage binding, and delegates the visibility decision to `released_visibility`. The marker it writes is what keeps future candidate searches from repeatedly cleaning the same workspace.

*Call graph*: calls 3 internal fn (transaction, _the_chat_main_agent, released_visibility); 2 external calls (__init__, select).

## 📊 State Registers Touched

- `reg-pack-and-feature-selection` — The chosen product packs and feature switches that decide which parts of the system are enabled.
- `reg-extension-capability-registry` — The live catalog of everything enabled extensions add, such as tools, routes, jobs, credentials, hooks, and backends.
- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-workspace-directory` — The durable list of workspaces and their core ownership, admin, billing, and setup state.
- `reg-agent-profiles` — The saved assistant definitions, including each agent's model, tools, visibility, setup needs, internet access, and identity.
- `reg-conversation-records` — The durable conversation list, including titles, audience, surface labels, sandbox links, and visibility rules.
- `reg-inbound-admission-queue` — The saved queue of incoming messages or intents waiting to become safe conversation turns.
- `reg-turn-state` — The shared status record for each unit of agent work, including claiming, running, completion, failure, parent-child links, and billing markers.
- `reg-cancellation-flags` — The shared stop signals and cleanup markers used to cancel turns, child work, sandboxes, and stuck jobs safely.
- `reg-runtime-instance-fleet` — The record of which server processes are alive and which shared listeners or jobs they currently own.
- `reg-background-job-queue` — The shared pool of delayed or recurring work that workers claim, run, retry, and clean up.
- `reg-schedule-monitor-store` — The saved recurring prompts, pauses, and outside-world watches that can wake conversations later.
- `reg-billing-ledger` — The shared money and usage record for tokens, images, videos, sandbox use, egress, balances, caps, and exports.
- `reg-source-sync-state` — The saved state of connected content sources, including pages, checkpoints, errors, ownership, and read grants.
- `reg-subagent-objectives` — The shared plan and delegation state for child agents, objectives, steps, evidence, attempts, and result delivery.
- `reg-observability-context` — The shared tracing, logging, metrics, health, and redaction context used to understand what happened safely.
- `reg-extension-data-store` — Durable extension-scoped key/value or JSON state used by installed extensions beyond their manifest capabilities and lockfile selection.
- `reg-hosted-site-store` — Saved hosted-site records, published bindings, homepage mappings, build metadata, and site preview state used by public routes and site tools.
- `reg-report-digest-store` — Saved generated report digest results and related background-report state separate from the schedule that triggered them.
- `reg-evaluation-run-store` — Durable evaluation test cases, replay runs, comparison results, and self-improvement validation state used to accept or reject changes.
- `reg-source-trigger-subscriptions` — Rules that map source changes or sync events to the conversations, agents, or turns that should be woken or admitted.
