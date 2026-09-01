# User-Visible Scheduled Work  `stage-14.1`

This stage is the system’s alarm clock and watchman. It is behind-the-scenes support, but its results are visible to users because it wakes conversations, agents, or workflows at the right moment. Scheduled tasks are recurring prompts. Their storage code creates, edits, cancels, lists, claims, runs, and reschedules them, while the runner checks the clock, finds due tasks, fires each one once, and moves it to its next time. The cron helper reads “cron” schedules, a compact calendar format, and calculates the next run. The scheduled-fire helper gives each run a clear identity, so the system knows which task and time caused it.

Pauses are one-time waits for a conversation. Their storage claims due pauses safely, and the pause runner wakes them or clears them if a human already replied. Monitors repeatedly run saved shell checks until something changes, fails too often, or times out; their runner decides when to probe and when to send the final alert. Source triggers wake conversations when shared sources change. Visibility rules protect private task details. The package files simply make these extensions importable.

## Files in this stage

### Scheduled monitors
Monitor runners and state storage repeatedly probe saved checks and wake the agent when a change, failure limit, or deadline produces a final alert.

### `extensions/monitors/ufo_ext_monitors/monitor_runner.py`

`orchestration` · `recurring scheduled monitor tick`

A monitor is like a watchperson who periodically checks something on behalf of a user. This file is the watchperson’s shift runner. On each scheduled tick, it claims monitors that are due so two overlapping runs do not check the same monitor twice. For each claimed monitor, it either fires immediately if the deadline has passed, skips if the original user no longer has a seat, or runs the saved probe command.

The result of the command decides what happens next. If the command cannot run because the terminal is gone, the monitor records a skip and waits for the next interval. If the command fails, the monitor counts the failure; the third failure fires the monitor. If the command succeeds and its output matches the saved baseline, the monitor records a quiet tick. If the output changed, the monitor fires and sends the changed output to the agent.

Firing means sending a message back into the conversation, then retiring the monitor so it will not run again. The code deliberately invokes first and retires second, using an idempotency key (a repeat-safe identifier) so a crash cannot create duplicate meaningful fires. Large output is stored in conversation files, and the message includes a path to it.

#### Function details

##### `MonitorRunner.run`  (lines 55–65)

```
async def run(self) -> None
```

**Purpose**: This is the top-level scheduled job for monitors. It finds monitors that are due, runs one tick for each, and reports if any monitor tick crashed.

**Data flow**: It starts with the extension context stored on the runner. It builds a monitor store, reads the current time, asks the store to claim due monitors under a short lease, then sends each claimed monitor row to the per-monitor tick logic. If individual ticks fail, it collects their monitor names and exception types; after trying all rows, it raises one combined error if anything went wrong.

**Call relations**: The extension’s recurring scheduler calls this method. It creates the storage helper and hands each claimed monitor to MonitorRunner._tick, so the detailed decision-making stays in one place while this method remains the outer sweep.

*Call graph*: calls 1 internal fn (_tick); 2 external calls (__init__, now).


##### `MonitorRunner._tick`  (lines 67–112)

```
async def _tick(self, store: MonitorStore, row: Monitor) -> None
```

**Purpose**: This performs one scheduled check for one claimed monitor. It decides whether the monitor should fire, skip, record a quiet run, record a failed run, or run the probe command and compare the output.

**Data flow**: It receives the monitor store and one monitor row. It reads the monitor’s interval, deadline, creator, command, baseline output, and counters. It may update the store with a skip, quiet tick, or failed tick; or it may call the firing path with a cause such as deadline, failure, or changed output. It also computes the next probe time from when this probe finishes, so a slow batch does not make monitors immediately due again.

**Call relations**: MonitorRunner.run calls this once for each claimed due monitor. Inside, it asks MonitorRunner._acts_for_a_seated_member whether the saved authority is still allowed, uses the probe capability from the extension context to run the command, uses monitor helpers to cap output and summarize errors, records ordinary ticks through MonitorStore methods, and hands final events to MonitorRunner._fire.

*Call graph*: calls 5 internal fn (_acts_for_a_seated_member, _fire, failed_tick, quiet_tick, skipped_tick); called by 1 (run); 4 external calls (now, timedelta, capped, stderr_tail).


##### `MonitorRunner._acts_for_a_seated_member`  (lines 114–126)

```
async def _acts_for_a_seated_member(self, row: Monitor) -> bool
```

**Purpose**: This checks whether the monitor may still act as the member who originally created it. If the monitor was created without a member, it is allowed to act only with workspace-wide access and no seat check is needed.

**Data flow**: It receives a monitor row and looks at its creator member id. If there is no creator id, it returns true. Otherwise it opens a database transaction and asks the seats system whether that member still has a seat in the workspace, returning true or false.

**Call relations**: MonitorRunner._tick calls this before running probes, because probes spend the creator’s connected authority. MonitorRunner._fire calls it again before sending the final message, so a revoked member’s monitor can still finish but does not carry authority that the member no longer has.

*Call graph*: called by 2 (_fire, _tick); 1 external calls (__init__).


##### `MonitorRunner._fire`  (lines 128–151)

```
async def _fire(self, store: MonitorStore, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> None
```

**Purpose**: This sends the final monitor-fired message to the agent and then retires the monitor. It is used when a deadline arrives, output changes, or repeated probe failures cross the threshold.

**Data flow**: It receives the store, monitor row, fire cause, message payload, optional full output to spill into a file, and the number of probes run. First it checks that this runner still holds the claim for the row. Then it decides whether the fire may act as the original member, builds the message body, invokes the agent with a repeat-safe key, and finally marks the monitor retired. If the target agent has been archived, it stops without retiring through the normal success path.

**Call relations**: MonitorRunner._tick calls this whenever a monitor has reached a final condition. This method uses MonitorStore.claim_holds to avoid stale work, MonitorRunner._acts_for_a_seated_member to choose the acting identity, MonitorRunner._body to construct the message, the extension context to invoke the agent, and MonitorStore.retire to remove the monitor from future ticks.

*Call graph*: calls 4 internal fn (_acts_for_a_seated_member, _body, claim_holds, retire); called by 1 (_tick).


##### `MonitorRunner._body`  (lines 153–172)

```
async def _body(self, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> str
```

**Purpose**: This builds the text message that the agent reads when a monitor fires. It includes the monitor’s reason, next steps, metadata, counters, and any output that explains why it fired.

**Data flow**: It receives the monitor row, cause, output payload, optional full output, and probe count. It turns monitor metadata into JSON text, writes a compact report between monitor-fired tags, optionally stores oversized output and includes its file path, escapes any accidental closing tag inside the body, and adds the probe output behind a safety wrapper that marks it as untrusted command output. It returns the final message string.

**Call relations**: MonitorRunner._fire calls this just before invoking the agent. If there is oversized output, this method calls MonitorRunner._spilled to place the full text in conversation files, and it uses the shared wall helper so command output cannot masquerade as instructions.

*Call graph*: calls 1 internal fn (_spilled); called by 1 (_fire); 2 external calls (dumps, wall).


##### `MonitorRunner._spilled`  (lines 174–182)

```
async def _spilled(self, row: Monitor, output: str) -> str
```

**Purpose**: This saves full probe output when it is too large to fit directly in the fire message. It lets the agent receive a short message with a file path while still being able to inspect the complete output.

**Data flow**: It receives the monitor row and the full output text. It checks that conversation file storage is available, creates a timestamped filename under the monitors directory, writes the output bytes into runtime files for that conversation, and returns the path or identifier produced by the file system.

**Call relations**: MonitorRunner._body calls this only when the displayed output was capped and the full version needs to be preserved. The returned path is inserted into the fired message that MonitorRunner._fire sends to the agent.

*Call graph*: called by 1 (_body); 1 external calls (now).


### `extensions/monitors/ufo_ext_monitors/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. That matters because other parts of the project can then refer to modules inside `extensions/monitors/ufo_ext_monitors` using normal Python import paths. Think of it like putting a label on a folder in a filing cabinet: the label does not contain the documents, but it makes the folder recognizable and usable by the system. Because this file is empty, it does not run setup code, expose shortcuts, or change behavior directly. Its value is structural: without it, some Python versions or packaging tools might not reliably recognize this directory as part of the project’s import tree.


### `extensions/monitors/ufo_ext_monitors/monitors.py`

`domain_logic` · `monitor creation, periodic runner ticks, firing, and disarming`

A monitor is like an alarm clock with a clipboard. It remembers what command to run, what the original output looked like, when to check again, when the final deadline is, and how many checks have been quiet, failed, or skipped. This file defines the database table for those records and the small store object, MonitorStore, that reads and changes them.

The important problem it solves is coordination. A background runner may wake up every minute, and there may be more than one runner trying to do work. To prevent two runners from probing the same monitor, this file uses a lease: a temporary claim saying “this runner owns this row until this time.” Updates and deletes check that claim before changing the row.

The file also keeps monitor data safe and bounded. Probe output is capped so a huge command result cannot flood later messages, and failed command stderr is trimmed to the useful tail. Every database read is converted into a Monitor value object with time values normalized to UTC. Because extension database transactions are not automatically limited to one workspace, every query here explicitly filters by workspace_id. Without this file, monitors could be duplicated, fired after being stopped, or accidentally mixed between workspaces.

#### Function details

##### `qualified_name`  (lines 73–84)

```
def qualified_name(conversation_id: UUID, slug: str) -> str
```

**Purpose**: Builds the stored name for a monitor by combining a short prefix from the conversation ID with the human-chosen slug. This lets different conversations reuse friendly names while still keeping the actual stored name unique within a workspace.

**Data flow**: It receives a conversation UUID and a short slug. It takes the first hex characters of the conversation ID, joins them to the slug with a dash, and returns that combined name. It does not change anything outside itself.

**Call relations**: Other monitor code can use this before storing or referring to a monitor, so the database and object layer see a workspace-unique name rather than only the user’s reusable nickname.


##### `capped`  (lines 87–97)

```
def capped(output: str) -> str
```

**Purpose**: Limits probe output to a safe size before it is stored or compared. This prevents one command that prints too much text from making monitor fires huge or from causing meaningless changes in the middle of an ever-growing output.

**Data flow**: It receives a text output string. If the encoded bytes fit under the configured limit, it returns the text unchanged. If it is too large, it keeps the beginning and end, inserts an omission marker in the middle, and returns that shortened text.

**Call relations**: Probe-running code can use this before comparing output to a monitor baseline or before including output in a fire message. It is a protective helper for the monitor workflow.


##### `stderr_tail`  (lines 100–105)

```
def stderr_tail(stderr: str) -> str
```

**Purpose**: Keeps only the end of stderr, the error text produced by a failed shell command. The end is usually where the most useful failure message appears.

**Data flow**: It receives stderr text. If it is small enough, it returns it unchanged. If it is too large, it returns only the last configured number of bytes, decoded back into text.

**Call relations**: Monitor firing or reporting code can use this when a probe fails, so the alert includes the useful part of the error without carrying an unbounded log.


##### `_aware`  (lines 137–138)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Makes sure a datetime has timezone information, using UTC when the database gave back a timezone-less value. This avoids confusing comparisons between times that do and do not say what timezone they are in.

**Data flow**: It receives a datetime. If the datetime already has timezone information, it returns it as-is. If not, it returns a copy marked as UTC.

**Call relations**: _row calls this while turning database rows into Monitor objects. It is a small normalization step so later monitor logic does not have to repeat the same timezone cleanup.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 141–168)

```
def _row(row: sa.RowMapping) -> Monitor
```

**Purpose**: Turns one raw database row into a Monitor object that the rest of the extension can use safely. It also normalizes all stored times to UTC-aware datetimes.

**Data flow**: It receives a row mapping from a SQL query. It reads each monitor column, converts relevant datetime fields through _aware, preserves optional fields such as metadata and last_probe_at, and returns a populated Monitor value object.

**Call relations**: MonitorStore.armed, MonitorStore.arm, and MonitorStore.claim_due all pass their returned database rows through this builder. That makes this the single doorway from database records into in-process monitor objects.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 171–172)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this monitor can be claimed now.” A monitor is available if nobody has claimed it or if the previous claim has expired.

**Data flow**: It receives the current time. It produces a SQL condition that checks for either no claimant or an expired claim timestamp. It does not query the database by itself.

**Call relations**: due_monitor_workspaces.due and MonitorStore.claim_due both use this same condition, so workspace discovery and actual claiming agree about what “available” means.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_due`  (lines 175–176)

```
def _due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this monitor needs attention now.” A monitor is due if its next scheduled probe time has arrived or if its final deadline has arrived.

**Data flow**: It receives the current time. It produces a SQL condition comparing that time to next_probe_at and deadline_at. It returns the condition for another query to use.

**Call relations**: due_monitor_workspaces.due uses it to find workspaces that may need runner attention, and MonitorStore.claim_due uses it to claim the actual monitor rows.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_monitor_workspaces`  (lines 179–196)

```
def due_monitor_workspaces() -> WorkspaceCandidates
```

**Purpose**: Gives the job system a way to find workspaces that have monitor work ready to run. It looks for at least one due, claimable monitor whose agent is still live.

**Data flow**: It creates a small query-building function and passes it to the job helper that turns matching workspace IDs into runnable candidates. The result is a WorkspaceCandidates object used by the scheduler.

**Call relations**: This is the seam between the monitor subsystem and the broader job runner. It hands the job system a query, through owner_candidates, so the runner can wake only workspaces that actually have monitor work.

*Call graph*: 1 external calls (owner_candidates).


##### `due_monitor_workspaces.due`  (lines 184–194)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual SQL query used to find workspace IDs with due monitor work. It checks timing, claim availability, and whether the agent assigned to the monitor is live.

**Data flow**: It reads the current UTC time, builds SQL conditions with _claim_available and _due, adds an agent_is_live check, and returns a query selecting distinct workspace IDs.

**Call relations**: due_monitor_workspaces gives this nested function to owner_candidates. The job system later uses the query it builds to decide which workspaces should be opened for monitor processing.

*Call graph*: calls 2 internal fn (_claim_available, _due); 3 external calls (now, select, agent_is_live).


##### `MonitorStore.armed`  (lines 205–211)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Monitor, ...]
```

**Purpose**: Lists the monitors currently armed in this store’s workspace, optionally limited to one conversation. This is how code asks, “what watches are active right now?”

**Data flow**: It starts with the workspace ID from the ExtensionContext, optionally adds a conversation ID filter, queries the monitor table ordered by name, and converts each row through _row. It returns a tuple of Monitor objects.

**Call relations**: This read method is used by callers that need to show or inspect active monitors. It relies on _row so callers receive clean Monitor objects rather than raw database rows.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `MonitorStore.arm`  (lines 213–268)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, name: str, audience: str, command: str, interval_minutes: int, deadline_at: datetime, reason: str, next_steps: str, metadata: dict[str, Js
```

**Purpose**: Creates a new armed monitor in the workspace. It records the command to run, the baseline output to compare against, the schedule, the deadline, and the conversation and agent that should receive the future fire.

**Data flow**: It receives all details needed for a monitor. Inside a transaction, it inserts a new row with a fresh UUID, zeroed counters, no claim, and creation/update timestamps. It returns the inserted row converted into a Monitor object.

**Call relations**: This is the write path used when an agent or member starts watching something. It hands the inserted row to _row, keeping the returned object in the same shape as objects read later by armed or claim_due.

*Call graph*: calls 1 internal fn (_row); 2 external calls (insert, uuid4).


##### `MonitorStore.claim_due`  (lines 270–313)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_MONITORS) -> tuple[Monitor, ...]
```

**Purpose**: Claims a batch of due monitors for one runner to process. The claim is a short lease, like writing your name on a task card so nobody else picks it up at the same time.

**Data flow**: It receives the current time, a lease length, and a maximum number of monitors. It finds due, unclaimed-or-expired rows in this workspace whose agents are live, updates them with a new claim ID and expiry time, and returns the claimed rows as Monitor objects.

**Call relations**: The periodic monitor runner uses this to get its work. It shares the same _claim_available and _due rules as workspace discovery, and uses _row to return usable Monitor objects after the database update.

*Call graph*: calls 3 internal fn (_claim_available, _due, _row); 5 external calls (timedelta, select, update, agent_is_live, uuid4).


##### `MonitorStore.quiet_tick`  (lines 315–325)

```
async def quiet_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records a probe that ran successfully and matched the baseline, meaning nothing changed. It advances the quiet streak, clears the failure streak, and schedules the next probe.

**Data flow**: It receives the claimed Monitor, the time the probe ran, and the next probe time. It computes the new counters and passes them to _tick, which writes the update and releases the claim.

**Call relations**: MonitorRunner._tick calls this after a normal probe that found no change. This method is a readable wrapper around _tick for the “all quiet” outcome.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.failed_tick`  (lines 327–339)

```
async def failed_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records a probe that ran but exited with an error, as long as that failure has not yet caused the monitor to fire. It advances the failure streak and clears the quiet streak.

**Data flow**: It receives the claimed Monitor, the probe time, and the next scheduled probe time. It increases probes_run and failure_streak, resets quiet_streak to zero, and sends those values to _tick for storage.

**Call relations**: MonitorRunner._tick calls this when a command fails but the monitor should continue watching. It funnels that failure outcome into the shared _tick update path.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.skipped_tick`  (lines 341–352)

```
async def skipped_tick(self, row: Monitor, next_probe_at: datetime) -> None
```

**Purpose**: Records that a probe could not be run at all, for example because the client sandbox was unreachable. It counts the skip but does not count it as a successful probe or a command failure.

**Data flow**: It receives the claimed Monitor and the next probe time. It leaves probe and streak counts mostly unchanged, increases skipped by one, keeps the last successful probe time, and passes everything to _tick.

**Call relations**: MonitorRunner._tick calls this when it cannot run the command. Like the other tick methods, it uses _tick so the claim is released consistently after the schedule is updated.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore._tick`  (lines 354–386)

```
async def _tick(self, row: Monitor, *, probes_run: int, quiet_streak: int, failure_streak: int, skipped: int, last_probe_at: datetime | None, next_probe_at: datetime) -> None
```

**Purpose**: Writes the common database update after a monitor tick and releases the runner’s claim. It is the shared “save the new counters and schedule” step for quiet, failed, and skipped outcomes.

**Data flow**: It receives a claimed Monitor plus the new counter values, last probe time, and next probe time. It refuses to proceed if the monitor has no claim. Then it updates only the matching row in the same workspace with the same claim ID, clears the claim, and refreshes updated_at.

**Call relations**: MonitorStore.quiet_tick, MonitorStore.failed_tick, and MonitorStore.skipped_tick all call this after deciding what kind of tick happened. The claim check prevents an old or unclaimed worker from overwriting a row it no longer owns.

*Call graph*: called by 3 (failed_tick, quiet_tick, skipped_tick); 1 external calls (update).


##### `MonitorStore.claim_holds`  (lines 388–415)

```
async def claim_holds(self, row: Monitor) -> bool
```

**Purpose**: Checks whether a runner still owns the monitor it is about to fire. This avoids sending a fire for a monitor that was stopped while the probe was running.

**Data flow**: It receives a Monitor that should have a claim ID. It rejects unclaimed monitors, then looks for a row with the same monitor ID, workspace ID, and claim ID, locking that row while it checks. It returns true if the row still exists under that claim, otherwise false.

**Call relations**: MonitorRunner._fire calls this immediately before delivering a fire. If the member disarmed the monitor and the row is gone, this check lets the runner back away instead of firing after the watch was stopped.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `MonitorStore.retire`  (lines 417–429)

```
async def retire(self, row: Monitor) -> None
```

**Purpose**: Deletes a monitor after its final fire has been delivered. A monitor is meant to end in exactly one fire, so retirement removes it from future scheduling.

**Data flow**: It receives a claimed Monitor. It refuses unclaimed monitors, then deletes the row only if the ID, workspace ID, and claim ID still match. It returns no value.

**Call relations**: MonitorRunner._fire calls this after firing. The claim guard means an expired or lost claim cannot delete work that another runner may now own.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `MonitorStore.disarm`  (lines 431–439)

```
async def disarm(self, row: Monitor) -> bool
```

**Purpose**: Stops watching a monitor because a user or member asked for it to be removed. It reports whether a row was actually deleted.

**Data flow**: It receives a Monitor. It deletes the matching row in the current workspace, regardless of any claim, and returns true if exactly one row was removed or false if nothing was deleted.

**Call relations**: This is the manual stop path for monitors. It can race with a runner, so claim_holds exists on the firing side to notice when disarm removed the row before a fire is sent.

*Call graph*: 1 external calls (delete).


### Paused workflow timers
Pause scheduling code stores waiting conversations and fires each due pause once when its timer expires.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pause_runner.py`

`orchestration` · `recurring scheduled background tick`

A pause is a stored instruction that says, in effect, “if nobody has responded by this time, resume the workflow with this prompt.” This file is the worker that wakes up on a schedule, finds pauses whose time has arrived, and tries to resume them safely.

The main class, `PauseRunner`, asks `PauseStore` for due pauses and claims them with a short lease. A lease is like putting a temporary sticky note on a task saying “I’m working on this,” so another overlapping runner does not do the same work at the same time.

For each claimed pause, the runner first checks that the pause’s protection markers still hold. These markers record where the conversation was when the pause began. If a member has spoken since then, the timer should not resume anything, because the human reply already ended the wait. If no member has spoken, the runner invokes the stored prompt as a scheduled turn, using an idempotency key. Idempotency means that if the same action is retried after a crash, the system recognizes it as the same action instead of doing it twice.

After a successful invoke, or after learning that a member already answered, the pause is retired. If the agent has been archived, no turn is admitted and the pause is left in place so it can be dealt with later if the app is restored.

#### Function details

##### `PauseRunner.run`  (lines 33–42)

```
async def run(self) -> None
```

**Purpose**: Runs one scheduled tick of the pause worker. It finds pauses that are due now, tries to fire each one, and reports a combined error if any individual pause failed.

**Data flow**: It starts with the runner’s extension context and lease length. It creates a `PauseStore`, asks the store for pauses due at the current UTC time, then sends each pause to `_fire`. If any pause raises an unexpected error, it records the conversation and error type; after all pauses have been attempted, it raises one summary error if there were failures.

**Call relations**: This is the outer loop for the file. A scheduler calls it periodically, it creates the storage helper it needs, uses the current time to find due work, and delegates the real “should this pause resume or retire?” decision to `PauseRunner._fire` for each row.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `PauseRunner._fire`  (lines 44–60)

```
async def _fire(self, store: PauseStore, row: Pause) -> None
```

**Purpose**: Attempts to complete one due pause. It either resumes the conversation with the stored prompt, retires the pause because the wait is over, or leaves it alone if the agent is archived.

**Data flow**: It receives a `PauseStore` and one stored `Pause` row. First it asks the store whether the pause’s hold markers still allow the timer to act; if not, it stops. If the hold is valid, it invokes the stored prompt for the conversation and agent, using the member who created the pause as the acting member and using recorded sequence markers to avoid racing against a human reply. If the agent is archived, it returns without retiring the pause. Otherwise, it tells the store to retire the pause so it will not be fired again.

**Call relations**: `PauseRunner.run` calls this for every due pause it claimed. This function sits between storage and conversation execution: it asks `PauseStore.claim_holds` whether the timer is still allowed to act, calls the extension context to perform the scheduled invoke, and then calls `PauseStore.retire` when the pause has reached an ending.

*Call graph*: calls 2 internal fn (claim_holds, retire); called by 1 (run).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pauses.py`

`domain_logic` · `background scheduling and pause resume processing`

A pause is like a reminder note for a conversation: “resume this agent in this conversation at this time, using this prompt.” This file defines the database table for those reminder notes and the small store object, PauseStore, that reads and writes them.

The important rule is that each conversation can have only one active pause. If a workflow arms a new pause for the same conversation, the old one is replaced. The replacement gets a fresh identity so an old worker cannot accidentally treat the new wait as the old one.

The file also supports background workers that look for pauses whose time has arrived. To avoid two workers waking the same conversation, a worker first “claims” a pause for a short lease. A lease is a temporary ownership mark in the database. Before firing, the worker checks that its claim still holds. After firing, it retires the pause, but only if it still owns the same claimed row.

The code is careful about workspaces. The extension’s database connection is not automatically limited to one workspace, so every query explicitly filters by workspace_id. It also normalizes timestamps so that consumers always see timezone-aware UTC times, even when SQLite returns simpler timestamp values.

#### Function details

##### `_aware`  (lines 76–77)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: This small helper makes sure a time value includes timezone information. If the database gives back a plain time with no timezone, it treats it as UTC.

**Data flow**: It receives a datetime value. If the value already says what timezone it belongs to, it returns it unchanged. If not, it adds UTC as the timezone and returns that safer version.

**Call relations**: _row calls this whenever it builds a Pause from a database row, so the rest of the pause system does not need to keep checking or fixing timestamp formats.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 80–95)

```
def _row(row: sa.RowMapping) -> Pause
```

**Purpose**: This converts a raw database row into a Pause object that the rest of the code can use comfortably. It is the single doorway from stored pause data into in-memory pause data.

**Data flow**: It receives one row from the pause table. It copies the row’s IDs, times, sequence numbers, prompt, creator, and claim marker into a Pause value, using _aware to make all stored times timezone-safe. It returns that Pause object.

**Call relations**: PauseStore.arm, PauseStore.armed, and PauseStore.claim_due all call this after reading rows from the database. That keeps the conversion rules in one place instead of scattering them across every query.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 98–99)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for “this pause can be claimed now.” A pause is available if nobody owns it, or if the old ownership lease has expired.

**Data flow**: It receives the current time. It creates a SQL condition that checks whether claimed_by is empty or claim_expires_at is earlier than that time. The result is not a Python true/false value yet; it is a database filter used inside later queries.

**Call relations**: PauseStore.claim_due uses this when actually claiming due pauses. due_pause_workspaces.due uses the same rule when deciding which workspaces are worth waking up for pause processing.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_pause_workspaces`  (lines 102–118)

```
def due_pause_workspaces() -> WorkspaceCandidates
```

**Purpose**: This tells the job system how to find workspaces that have at least one pause ready to run. It is a narrow bridge between the pause table and the broader background job scheduler.

**Data flow**: It defines a query-making function that finds workspace IDs with due, claimable pauses. It passes that function to owner_candidates, which turns the query into the form expected by the job ownership system. The result is a WorkspaceCandidates object.

**Call relations**: This function hands its inner due query to owner_candidates. The scheduler can then use that candidate source to avoid checking every workspace when only some have timers ready.

*Call graph*: 1 external calls (owner_candidates).


##### `due_pause_workspaces.due`  (lines 107–116)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This inner function builds the actual database query for workspaces with pauses that are ready and not under a live claim.

**Data flow**: It reads the current UTC time, asks _claim_available for the matching claim filter, and builds a select query for distinct workspace IDs whose pause resume_at time has passed. The output is a SQL query, not the final list itself.

**Call relations**: due_pause_workspaces gives this query builder to owner_candidates. It reuses _claim_available so candidate discovery and real claiming agree about what “available” means.

*Call graph*: calls 1 internal fn (_claim_available); 2 external calls (now, select).


##### `PauseStore.arm`  (lines 127–183)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, resume_at: datetime, origin_seq: int, origin_arrival_seq: int, prompt: str, created_by_member_id: UUID | None) -> Pause
```

**Purpose**: This creates or replaces the active pause for one conversation. It is used when a workflow decides, “stop now and resume later.”

**Data flow**: It receives the conversation, agent, resume time, sequence watermarks, prompt, and optional member who created the pause. It creates a fresh pause ID, clears any existing claim, and writes the row to the database. If that conversation already has a pause in this workspace, it updates that row instead of adding a second one. It returns the stored Pause object.

**Call relations**: This method calls uuid4 to give each newly armed wait its own identity, even when replacing an old pause. After the database upsert returns the stored row, it calls _row to turn that row into a Pause for the caller.

*Call graph*: calls 1 internal fn (_row); 1 external calls (uuid4).


##### `PauseStore.armed`  (lines 185–191)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Pause, ...]
```

**Purpose**: This lists currently armed pauses in the workspace, optionally limited to one conversation. It is useful for inspecting what waits are still scheduled.

**Data flow**: It receives an optional conversation ID. It builds a database query for this workspace, adds the conversation filter if provided, orders results by resume time, and reads the rows. It converts each row through _row and returns a tuple of Pause objects.

**Call relations**: This is a read-only view over the pause table. It calls SQLAlchemy’s select builder to form the query and _row to give callers normalized Pause values instead of raw database rows.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `PauseStore.claim_due`  (lines 193–234)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_PAUSES) -> tuple[Pause, ...]
```

**Purpose**: This leases a batch of pauses whose scheduled time has arrived, so one worker can process them without another worker doing the same work. It is the main “take the next due reminders” operation.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of pauses to claim. It creates a unique claim ID, finds the oldest due pauses in this workspace whose claims are free or expired, and updates them with that claim ID and a new expiration time. It returns the claimed rows as Pause objects.

**Call relations**: This method uses _claim_available so it only takes free work. It uses one database update that both marks and returns the rows, which helps overlapping workers split the work instead of duplicating it. It then calls _row for each returned row.

*Call graph*: calls 2 internal fn (_claim_available, _row); 4 external calls (timedelta, select, update, uuid4).


##### `PauseStore.claim_holds`  (lines 236–261)

```
async def claim_holds(self, row: Pause) -> bool
```

**Purpose**: This checks whether a worker still owns the pause it is about to fire. It prevents a worker from resuming a workflow after that pause was replaced or claimed differently.

**Data flow**: It receives a Pause that should already have a claim ID. If there is no claim ID, it raises an error because an unclaimed pause should not be fired. Otherwise it looks for the same pause ID in the same workspace with the same claim marker. It returns true if that exact claim still exists, false if not.

**Call relations**: PauseRunner._fire calls this immediately before firing a pause. The check is important because PauseStore.arm can replace a pause and clear the old claim while a worker is still running.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `PauseStore.retire`  (lines 263–276)

```
async def retire(self, row: Pause) -> None
```

**Purpose**: This removes a pause after the worker has finished with it, but only if the worker still owns the same claimed pause. It is the cleanup step after a pause has fired or has been settled.

**Data flow**: It receives a claimed Pause. If the Pause has no claim ID, it raises an error because there is no safe ownership marker to delete by. Otherwise it deletes the row matching the pause ID, workspace ID, and claim ID. It does not return a value; the database row is removed if it still matches.

**Call relations**: PauseRunner._fire calls this after processing a pause. The claim guard means an expired lease or a newly re-armed pause will not be deleted by an old worker.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


### Recurring scheduled tasks
Scheduled-task runtime code names, validates, stores, runs, reschedules, and protects recurring prompts sent back into conversations.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `recurring scheduled job`

This runner is the part of the scheduled-tasks extension that wakes up on a regular interval and asks, “Which tasks should run now?” Without it, saved schedules would just sit in storage and never turn into real work.

The file is careful about duplicate runs. Before firing a task, it claims it with a short lease, like putting a temporary “I’m working on this” sticky note on it. That way, if two runner ticks overlap, they should not both fire the same scheduled occurrence. It also checks whether a task has expired before running it, and it rechecks that its claim still holds before sending anything into the conversation system.

When a task is ready, the runner builds the message that will be delivered as a scheduled turn. A scheduled turn means the system treats it as its own independent run, tied to the task creator’s permissions. The run also gets an idempotency key, which is a “do not do this twice” label based on the task and exact scheduled time.

If the conversation accepts the turn, the runner advances the schedule to the next cron occurrence. If the fire fails, it leaves the current occurrence in place so it can be retried later. If the app is archived, that is not counted as a failure; the task stays put until the app can accept work again.

#### Function details

##### `fire_body`  (lines 42–60)

```
def fire_body(task: ScheduledTask, runtime_instruction: str | None) -> tuple[str, str]
```

**Purpose**: Builds the text that will be sent into a conversation when a scheduled task fires, plus the unique key used to prevent the same scheduled occurrence from being admitted twice. Someone would use this when turning a stored schedule row into the actual message the agent will see.

**Data flow**: It takes a scheduled task and an optional runtime instruction. It reads the task’s next run time, prompt, and id, then formats a small scheduled-task block followed by the user’s prompt and, when present, an extra instruction block. It returns two things: the finished inbound message and a stable idempotency key for that exact task occurrence.

**Call relations**: This is called by ScheduledTaskRunner._fire after the runner has confirmed that the task is still due and still claimed. It hands the finished message and duplicate-prevention key back to _fire, which then gives them to the extension context so the scheduled turn can be admitted.

*Call graph*: called by 1 (_fire); 1 external calls (scheduled_fire_key).


##### `ScheduledTaskRunner.run`  (lines 68–77)

```
async def run(self) -> None
```

**Purpose**: Performs one full runner tick. It finds due scheduled tasks, tries to fire each one, and reports if any fires failed.

**Data flow**: It starts with the runner’s extension context. It creates a schedule store, reads the current time, and asks the store for tasks that are due and can be claimed under a lease. For each claimed task, it passes the task into _fire. If any task reports a failure, it collects the task names and raises one combined error at the end; otherwise it finishes quietly.

**Call relations**: This is the top-level method used by the extension’s recurring job. It coordinates the store and the per-task firing logic, while ScheduledTaskRunner._fire does the detailed work for each individual scheduled task.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `ScheduledTaskRunner._fire`  (lines 79–116)

```
async def _fire(self, store: ScheduleStore, task: ScheduledTask, tick_at: datetime, expiry_checked_at: datetime) -> str | None
```

**Purpose**: Attempts to fire one claimed scheduled task exactly once for its current scheduled occurrence. It decides whether the task should retire, run normally, run as its final permitted fire, retry later, or advance to its next scheduled time.

**Data flow**: It receives the schedule store, one claimed task, the tick time, and the time used for checking expiry. First it asks the store to retire the task if it has expired. If it should continue, it calculates the following cron fire time and chooses the instruction to send: a normal reporting instruction, or a final-fire instruction if the next occurrence would be beyond the task’s expiry. It confirms the claim still holds, builds the inbound message and idempotency key, and invokes the conversation as a scheduled turn. If the app is archived or the turn is not accepted, it leaves the schedule unchanged. If invocation raises another error, it returns a short failure label. If the turn is accepted, it reschedules the task to the following fire time and records the accepted turn id.

**Call relations**: ScheduledTaskRunner.run calls this once for each task it successfully claimed. Inside, _fire relies on the schedule store to retire, confirm, and reschedule tasks; it uses next_fire to calculate the next cron occurrence; and it uses fire_body to prepare the exact message and duplicate-prevention key before handing the work to the extension context.

*Call graph*: calls 4 internal fn (fire_body, claim_holds, reschedule, retire_if_expired); called by 1 (run); 1 external calls (next_fire).


### `core/src/ufo/runtime/ext/scheduled_fire.py`

`domain_logic` · `scheduled task admission and run lookup`

Scheduled tasks need a durable “admission key”: a string that proves a particular task was allowed to run at a particular scheduled time. This matters because the system may retry work, show runs in a portal, or compare old and new records after a deploy. If the key format changed accidentally, the same scheduled run might look like a different run, or the portal might fail to connect a run back to its task.

This file keeps that contract in one small place. `scheduled_fire_key` builds the key by joining the task’s unique ID and the scheduled fire time with a colon. The time is written using Python’s standard `isoformat()` form, including details like `+00:00` for UTC, because old stored keys must keep matching exactly.

`scheduled_fire_task_id` does the reverse, but only partly: it looks at the part before the first colon and tries to read it as a UUID, which is a standard unique identifier. If that first part is not a valid UUID, the function returns `None`. That is important because not every run key comes from a scheduled task; for example, a timer resume from a durable pause may use a different kind of key. In that case, this parser politely says, “this is not a scheduled fire key.”

#### Function details

##### `scheduled_fire_key`  (lines 14–16)

```
def scheduled_fire_key(task_id: UUID, fire_at: datetime) -> str
```

**Purpose**: Builds the exact text key used to identify one scheduled occurrence of one task. Someone uses this when admitting a scheduled task run so retries or later lookups can recognize the same occurrence.

**Data flow**: It takes a task ID and a scheduled time. It turns the time into its standard text form with `isoformat()`, joins the task ID, a colon, and that time, and returns the resulting string. It does not change any outside state.

**Call relations**: When the scheduled-tasks runner needs to record or deduplicate a fire, it calls this builder to create the agreed key. The function hands off only a string, but that string becomes the durable link used elsewhere to recognize the scheduled run.

*Call graph*: 1 external calls (isoformat).


##### `scheduled_fire_task_id`  (lines 19–25)

```
def scheduled_fire_task_id(key: str) -> UUID | None
```

**Purpose**: Tries to recover the task ID from a run key, but only when the key looks like a scheduled fire key. If the key belongs to some other kind of run, it returns `None` instead of pretending it understands it.

**Data flow**: It takes a key string, splits off the text before the first colon, and tries to turn that text into a UUID. If that succeeds, it returns the UUID. If the text is not a valid UUID, it returns `None`. It does not inspect or validate the scheduled time part.

**Call relations**: When the portal or run feed wants to connect a run back to a scheduled task, it can call this parser. The parser delegates UUID validation to Python’s UUID reader, then gives back either the task ID for scheduled fires or `None` for keys created by other mechanisms.

*Call graph*: 1 external calls (UUID).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is like a label on a folder saying, “this folder is an importable package.” This particular file is empty, which means it does not define settings, functions, or startup behavior. Its job is still useful: it lets other parts of the project refer to code inside `ufo_ext_scheduled_tasks` using normal Python imports. Without this package marker, some tools or older Python import setups might not recognize the folder as a package, and extension modules inside it could be harder or impossible to load reliably. Think of it as the cover on a binder: it does not contain the pages, but it tells the system that the pages belong together.


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `scheduled task creation and runner polling`

Scheduled tasks need a way to say “run every day at 9” or “run every five minutes.” This file keeps that timing language in one place. The rest of the task store only needs a concrete date and time for the next run; it does not need to understand cron expressions itself.

A cron expression is a compact five-part schedule string, usually meaning minute, hour, day of month, month, and day of week. For example, it is like a timetable written in shorthand. This file first makes sure the timetable has exactly five parts and that the `croniter` library can understand it. If not, it raises a clear error instead of letting a bad schedule drift deeper into the system.

It also computes the next run time after a given moment. Importantly, it asks for the next time strictly after that moment. That means if the runner was late or paused, missed schedule windows collapse into one next catch-up run rather than creating a sudden pile of old runs.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: Checks that a schedule string is a valid five-field cron expression. Someone would use this before saving or accepting a scheduled task, so bad timing rules are rejected early with a helpful error.

**Data flow**: It receives a schedule as text. First it splits the text into space-separated parts and makes sure there are exactly five; then it asks `croniter`, an outside cron-parsing library, whether the expression is valid. If either check fails, it raises a `ValueError`; if both pass, it returns the original schedule unchanged.

**Call relations**: This function is the gatekeeper for cron schedules in this extension. When called, it delegates the deeper syntax check to `croniter.croniter.is_valid`, while keeping the project’s own rule that only five-field cron expressions are accepted.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: Finds the next datetime when a cron schedule should run after a given moment. It is used when the system needs to turn a repeating schedule into one concrete upcoming run time.

**Data flow**: It receives a schedule string and an `after` datetime. It gives both to `croniter`, which builds a schedule iterator starting from that moment, then asks for the next datetime. The result is returned as the next planned fire time, and nothing else is changed.

**Call relations**: This function is the calculator used after a cron schedule has been accepted. It hands the actual date-and-time math to `croniter.croniter`, then returns the single next run time that the scheduled-task store can order and compare.

*Call graph*: 1 external calls (croniter).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/schedules.py`

`domain_logic` · `member requests and scheduler polling`

A scheduled task is like a calendar reminder for an agent: at a certain time, the system should re-enter a conversation and deliver a prompt. This file defines the database table for those reminders and a ScheduleStore class that is the safe doorway into that table.

The file protects several important rules. A task belongs to one workspace, one agent, and one conversation. A user-facing operation only sees tasks for the current object agent, so one agent cannot accidentally edit another agent's reminders. Before creating a task, the store checks that the target conversation is actually bound to the agent that will run it.

The scheduler side works differently. It looks across the whole workspace for tasks that are due. To stop two scheduler workers from firing the same task, due tasks are “claimed” with a short lease, like putting a temporary sticky note on a row saying “I am working on this.” Before firing, the runner can check that the claim still holds. After firing, the task is either rescheduled for its next time, retired if expired, or left for a later retry if something changed.

The file also turns raw database rows into small value objects, and normalizes date-times to UTC so callers do not have to worry about database differences.

#### Function details

##### `_utc`  (lines 124–125)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: Makes sure a date-time value is marked as UTC time. This is needed because some databases may return a time without saying what time zone it belongs to.

**Data flow**: It receives a datetime. If the datetime already has time-zone information, it returns it unchanged. If it has no time-zone information, it returns the same clock time marked as UTC.

**Call relations**: It is used whenever this file turns database times into task objects or inspection results. _utc_opt calls it for optional times, and _task and ScheduleStore.inspect_many use it so the rest of the scheduled-task system gets consistent UTC values.

*Call graph*: called by 3 (inspect_many, _task, _utc_opt); 1 external calls (replace).


##### `_utc_opt`  (lines 128–129)

```
def _utc_opt(value: datetime | None) -> datetime | None
```

**Purpose**: Does the same UTC cleanup as _utc, but for date-times that may be missing. It keeps missing values as missing instead of forcing callers to check first.

**Data flow**: It receives either a datetime or None. None comes back as None. A datetime is passed to _utc and comes back as a UTC-aware datetime.

**Call relations**: It supports _task and ScheduleStore.inspect_many, where fields such as last run time or expiry time may not exist yet.

*Call graph*: calls 1 internal fn (_utc); called by 2 (inspect_many, _task).


##### `_claim_available`  (lines 132–136)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for whether a scheduled task can be taken by a worker. A task is available if no one has claimed it, or if an old claim has timed out.

**Data flow**: It receives the current time. It produces a SQL condition that matches rows with no claim or with a claim expiry earlier than that time.

**Call relations**: The workspace finder and ScheduleStore.claim_due both use this same rule, so the system only wakes scheduler workspaces that actually have claimable work.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_expired`  (lines 139–143)

```
def _expired(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for whether a scheduled task has passed its expiry time. Expired tasks should be removed instead of fired again.

**Data flow**: It receives the current time. It produces a SQL condition that matches rows whose expires_at value exists and is at or before that time.

**Call relations**: The workspace finder uses it to notice workspaces with old tasks to clean up, and ScheduleStore.claim_due uses it to delete expired tasks before leasing due ones.

*Call graph*: called by 2 (claim_due, due); 1 external calls (and_).


##### `_task`  (lines 146–165)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: Turns one raw database row into a ScheduledTask object that the rest of the code can use comfortably. It also fixes date-time values so they are consistently UTC-aware.

**Data flow**: It receives a row mapping from the scheduled_task table. It copies out the task identity, conversation, agent, schedule, prompt, claim, pause state, and timing fields, normalizes the date-times, and returns a ScheduledTask value.

**Call relations**: All main reads that return scheduled tasks flow through this builder: create, update, list, and claim_due. That keeps every caller from repeating row-to-object conversion rules.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 4 (claim_due, create, list, update); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 168–191)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: Tells the job system how to find workspaces that may have scheduled-task work waiting. This prevents the runner from scanning every workspace blindly.

**Data flow**: It creates a small database query factory for due or expired scheduled tasks, then wraps it with owner_candidates so the wider job system can ask for candidate workspaces.

**Call relations**: The scheduled-task runner uses this as its candidate source. Inside it, the due inner function builds the actual query that owner_candidates will run when looking for work.

*Call graph*: 1 external calls (owner_candidates).


##### `due_task_workspaces.due`  (lines 174–189)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the query that finds workspace IDs with at least one scheduled task needing attention. Attention means either the task is expired and ready for cleanup, or it is due to run and not paused.

**Data flow**: It reads the current UTC time, then creates a SQL query over scheduled_task. The query filters for claimable rows that are expired or due, and returns distinct workspace IDs.

**Call relations**: This inner function is handed to owner_candidates by due_task_workspaces. It reuses _claim_available and _expired so workspace discovery follows the same rules as actual task claiming.

*Call graph*: calls 2 internal fn (_claim_available, _expired); 5 external calls (now, and_, not_, or_, select).


##### `ScheduleStore.workspace_id`  (lines 205–206)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace ID attached to this store's extension context. It gives all database operations a consistent workspace boundary.

**Data flow**: It reads ctx.workspace_id from the ScheduleStore instance and returns that UUID. It does not change anything.

**Call relations**: The store's methods use this property when building database filters, so every read and write stays inside the current workspace.


##### `ScheduleStore.create`  (lines 208–267)

```
async def create(self, conversation_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None, expires_at: datetime | None=None,
```

**Purpose**: Creates a new recurring scheduled task for the current agent and a specific conversation. It refuses to create the task if the conversation does not belong to that agent or if a task with the same name already exists for that agent in the workspace.

**Data flow**: It receives the conversation, task name, schedule text, prompt, description, next run time, optional creator, optional expiry, and pause state. It checks the current object agent, verifies the conversation-agent match, inserts a row with a new ID, and returns the new ScheduledTask. If the insert conflicts with an existing name, it raises an error.

**Call relations**: This is the user-facing entry for adding tasks. It calls object_agent_id to bind the task to the active agent, uses the database insert operation, then passes the returned row through _task.

*Call graph*: calls 1 internal fn (_task); 2 external calls (object_agent_id, uuid4).


##### `ScheduleStore.update`  (lines 269–323)

```
async def update(self, expected: ScheduledTask, schedule: str, prompt: str, description: str, next_run_at: datetime, expires_at: datetime | None=None, *, paused: bool) -> ScheduledTask
```

**Purpose**: Edits an existing scheduled task while making sure it is still the same task the caller originally saw. It changes the schedule, prompt, description, next run time, expiry, and pause state, but keeps run history intact.

**Data flow**: It receives the expected ScheduledTask plus new editable values. It checks that the task still belongs to the current agent, then updates only a row matching the expected ID, agent, conversation, name, workspace, and creator. It clears any active claim because the old leased version is no longer valid, and returns the updated ScheduledTask.

**Call relations**: User-facing edit flows call this after reading a task. It uses _creator_matches to avoid updating the wrong creator's task and _task to convert the returned database row. If no row matches, it reports that the task changed while editing.

*Call graph*: calls 2 internal fn (_creator_matches, _task); 2 external calls (update, object_agent_id).


##### `ScheduleStore.cancel`  (lines 325–341)

```
async def cancel(self, expected: ScheduledTask) -> None
```

**Purpose**: Deletes a scheduled task, but only if it still matches the task the caller intended to cancel. This prevents a stale view from deleting a different or changed task.

**Data flow**: It receives the expected ScheduledTask. It checks the current agent, then deletes a row matching the task's workspace, ID, agent, conversation, name, and creator. It returns nothing on success, and raises an error if no row was deleted.

**Call relations**: User-facing cancel flows use this method. It shares the same identity-check idea as update and calls _creator_matches so creatorless tasks and member-created tasks are distinguished correctly.

*Call graph*: calls 1 internal fn (_creator_matches); 2 external calls (delete, object_agent_id).


##### `ScheduleStore._creator_matches`  (lines 343–348)

```
def _creator_matches(self, expected: ScheduledTask) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says whether a row has the same creator as an expected task. It is careful with creatorless tasks, because database NULL values must be checked with “is null,” not normal equality.

**Data flow**: It receives an expected ScheduledTask. If the task has no creator, it returns a SQL condition requiring the row creator to be null. Otherwise, it returns a condition requiring the row creator to equal that member ID.

**Call relations**: ScheduleStore.update and ScheduleStore.cancel use this helper as part of their stale-change protection. It keeps edits and deletes from crossing between member-owned tasks and creatorless tasks.

*Call graph*: called by 2 (cancel, update).


##### `ScheduleStore._listing`  (lines 350–371)

```
def _listing(self, selected: tuple[sa.ColumnElement[Any], ...], *, conversation_id: UUID | None, names: tuple[str, ...] | None, visible_to_member_id: UUID | None, include_all_owners: bool, limit: int
```

**Purpose**: Builds the shared database query used to list scheduled tasks. It applies the current workspace and agent boundary, then adds optional filters such as conversation, names, visible owner, and limit.

**Data flow**: It receives the columns to select and optional listing filters. It starts with tasks in the current workspace for the current object agent, narrows the query as requested, orders by task name, and returns the SQL query object.

**Call relations**: ScheduleStore.list calls this to avoid duplicating listing rules. It calls object_agent_id so listings stay scoped to the active agent.

*Call graph*: called by 1 (list); 2 external calls (select, object_agent_id).


##### `ScheduleStore.list`  (lines 373–392)

```
async def list(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -> tuple[S
```

**Purpose**: Returns scheduled tasks visible under the requested filters. This is the basic task listing used by member-facing views and by richer listing code.

**Data flow**: It receives optional filters for conversation, task names, member visibility, ownership scope, and limit. It asks _listing to build the SQL query, runs it in a transaction, converts each row with _task, and returns a tuple of ScheduledTask objects.

**Call relations**: ScheduleStore.list_reported builds on this method when it needs conversation audience details too. Internally, list depends on _listing for the query and _task for row conversion.

*Call graph*: calls 2 internal fn (_listing, _task); called by 1 (list_reported).


##### `ScheduleStore.list_reported`  (lines 394–428)

```
async def list_reported(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -
```

**Purpose**: Lists scheduled tasks together with facts about the conversation each task reports into, such as who can see it and its surface label. This is used when a user-facing screen needs to decide or explain visibility.

**Data flow**: It receives the same filters as list. It first gets matching ScheduledTask objects, then asks the context for conversation facts for their conversation IDs. It returns ListedTask objects only for tasks whose conversations still exist in those facts.

**Call relations**: This method sits on top of ScheduleStore.list. After getting tasks, it enriches them with live conversation information from the extension context instead of relying on possibly stale copied labels.

*Call graph*: calls 1 internal fn (list); 1 external calls (__init__).


##### `ScheduleStore.claim_due`  (lines 430–486)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: Finds due scheduled tasks, removes expired ones, and leases a limited batch so workers can fire them without stepping on each other. A lease is a temporary claim that says one runner is responsible for this task for a short time.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum batch size. In one transaction, it deletes expired claim-available rows, selects the oldest due unpaused claim-available rows, marks them with a new claim ID and claim expiry, and returns them as ScheduledTask objects.

**Call relations**: The scheduler runner calls this when polling a workspace for work. It reuses _expired and _claim_available, and converts the claimed rows through _task. Later runner steps use claim_holds, retire_if_expired, and reschedule on the returned claimed tasks.

*Call graph*: calls 3 internal fn (_claim_available, _expired, _task); 6 external calls (timedelta, delete, not_, select, update, uuid4).


##### `ScheduleStore.claim_holds`  (lines 488–519)

```
async def claim_holds(self, task: ScheduledTask) -> bool
```

**Purpose**: Checks whether a claimed task is still exactly the task that was leased before the runner fires it. This shrinks the chance that a task fires after a user edited or cancelled it.

**Data flow**: It receives a ScheduledTask that must have a claim ID. It re-reads the database row while locking it, requiring the same workspace, ID, claim, conversation, agent, name, and schedule. It returns true if such a row still exists, false otherwise.

**Call relations**: ScheduledTaskRunner._fire calls this immediately before invoking the scheduled prompt. If this check fails, the runner can skip the fire because the leased task version is no longer valid.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `ScheduleStore.retire_if_expired`  (lines 521–535)

```
async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool
```

**Purpose**: Deletes a claimed task if its expiry time has already passed before it is fired. This stops an overdue or expired recurring task from being invoked.

**Data flow**: It receives a claimed ScheduledTask and the current time. If the task has no expiry or its expiry is still in the future, it returns false. If it is expired, it deletes the matching claimed row and returns true.

**Call relations**: ScheduledTaskRunner._fire calls this as part of deciding what to do with a claimed task. It is one of the safety checks before or during firing.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `ScheduleStore.reschedule`  (lines 537–567)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: Moves a successfully fired task to its next run time, records when it last ran, optionally records the turn that was created, and releases the claim. This is how a recurring task becomes ready for its next cycle.

**Data flow**: It receives a claimed ScheduledTask, the next run time, the last run time, and optionally the last turn ID. It updates the matching claimed row with the new timing information, clears the claim fields, and returns whether a row was actually updated.

**Call relations**: ScheduledTaskRunner._fire calls this after a task has fired or been admitted as a turn. The recorded last_turn_id is later used by inspection methods to show the latest outcome.

*Call graph*: called by 1 (_fire); 1 external calls (update).


##### `ScheduleStore.inspect`  (lines 569–573)

```
async def inspect(self, expected: ScheduledTask) -> TaskInspection | None
```

**Purpose**: Returns the live status information for one scheduled task, such as next run time, last run time, expiry, and latest response. It is a convenience wrapper for inspecting a single task.

**Data flow**: It receives one expected ScheduledTask. It calls inspect_many with a one-item tuple, then returns the inspection for that task ID or None if the task no longer matches.

**Call relations**: This method delegates the real work to ScheduleStore.inspect_many. It is used when callers only need the status of one task instead of a batch.

*Call graph*: calls 1 internal fn (inspect_many).


##### `ScheduleStore.inspect_many`  (lines 575–611)

```
async def inspect_many(self, expected: tuple[ScheduledTask, ...]) -> dict[UUID, TaskInspection]
```

**Purpose**: Fetches live status details for several scheduled tasks at once. It also looks up the outcome of each task's most recent fired turn so status displays can show what happened last.

**Data flow**: It receives expected ScheduledTask objects. It queries matching rows in the current workspace and current agent, collects any last_turn_id values, asks the context for those turn outcomes, and builds a dictionary from task ID to TaskInspection. Rows whose name or conversation no longer match the expected task are skipped.

**Call relations**: ScheduleStore.inspect calls this for a single task, while batch status code can call it directly. It uses _utc and _utc_opt for time normalization and the context's turn_outcomes lookup to attach the latest turn status and response text.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 1 (inspect); 3 external calls (__init__, select, object_agent_id).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/visibility.py`

`domain_logic` · `permission checking during scheduled task viewing`

Scheduled tasks can report their results into different kinds of conversations. Some conversations are shared by the whole workspace, while others belong to one specific member. This file contains the rule that connects those two facts: if a task posts into a shared place, its original content is also safe for everyone to read; if it posts into a private member conversation, only the right person should see it.

The key idea is that visibility follows the task’s audience, not just the task’s creator. That matters because a task might have no recorded creator, or a creator might not be the same as the person whose private conversation the task reports into. The code treats the audience like the address on an envelope: if the envelope is addressed to the whole office, anyone in the office can read it; if it is addressed to one person, others cannot.

The single function checks this in a careful order. First, shared audience means visible to all. Second, if there is no current member, private content is not shown. Third, if the task’s audience is exactly the current member’s own subject, it is visible. Finally, the task is also visible to the member who created it.

#### Function details

##### `task_content_visible`  (lines 7–20)

```
def task_content_visible(listed: ListedTask, member_id: UUID | None) -> bool
```

**Purpose**: Decides whether a given member may read a scheduled task’s prompt and description. It protects private task content while allowing shared workspace tasks, member-owned tasks, and creator-owned tasks to be read by the right people.

**Data flow**: It receives a listed task and either a member ID or no member ID. It first checks whether the task’s audience is shared with the whole workspace; if so, it returns true. If there is no member ID, it returns false for anything not shared. Otherwise it compares the task’s audience with that member’s personal subject, and also checks whether the member created the task. The output is a simple true or false answer, and the function does not change the task or member data.

**Call relations**: When some other part of the scheduled task feature needs to decide whether to show task content, it can call this function. Inside the decision, it asks `subject_shared` whether the audience is workspace-wide, and uses `member_subject` to build the current member’s private audience label so it can compare it with the task’s audience.

*Call graph*: 2 external calls (member_subject, subject_shared).


### External wakeup hooks
Import hooks and source-trigger rules support scheduled digests and conversations that wake when shared sources change.

### `extensions/report_digest/ufo_ext_report_digest/__init__.py`

`other` · `import/package discovery`

In Python, an `__init__.py` file is like a label on a folder that says, “this folder is a package you can import from.” This particular file is empty, so it does not create objects, run setup code, or expose helper functions. Its value is structural: it helps Python and project tooling recognize `extensions/report_digest/ufo_ext_report_digest` as the home of the report digest extension’s code. Without this file, some import systems or older tooling might not treat the folder as a normal package, which could make the extension harder or impossible to load in certain environments. Think of it like a blank cover page for a chapter: it does not contain the chapter’s content, but it helps the book’s organization make sense.


### `extensions/sources/ufo_ext_sources/triggers.py`

`domain_logic` · `request handling and alert processing`

A source trigger is like a standing reminder: “when this source has new changes, tell this conversation.” This file defines the database table for those reminders, the small Python objects used to represent them, and a store class that is the only place this extension reads or changes them.

The important safety idea is workspace scoping. The database connection is not automatically limited to one workspace, so every query in this file explicitly includes the current workspace ID. Without that, one workspace could accidentally see or alter another workspace’s triggers.

The file also protects which agent is allowed to be woken. When a trigger is created, it checks that the target conversation belongs to the currently running agent. That prevents a trigger created under one agent from secretly invoking another.

There are two main read paths. The alerting path asks, “which conversations should wake for this source binding?” and gets plain `SourceTrigger` records. The member-facing listing path asks, “what triggers should this user-facing view show?” and attaches live conversation facts such as audience and surface label, so renamed channels show their current names. If a conversation no longer exists, its trigger is left out of that listing.

#### Function details

##### `_utc`  (lines 85–86)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: This helper makes sure a stored time has timezone information. If the database gives back a time without a timezone, it treats it as UTC, which is the common “world clock” baseline.

**Data flow**: It receives a `datetime` value. If the value already says what timezone it belongs to, it returns it unchanged; otherwise it adds UTC timezone information. The result is a safer time value for the rest of the code to compare or display.

**Call relations**: It is used by `_trigger` while turning database rows into `SourceTrigger` objects. That means every trigger read from storage gets consistent timestamp treatment before the rest of the system sees it.

*Call graph*: called by 1 (_trigger); 1 external calls (replace).


##### `_trigger`  (lines 89–104)

```
def _trigger(row: sa.RowMapping) -> SourceTrigger
```

**Purpose**: This helper turns one raw database row into a `SourceTrigger`, the in-memory object the rest of this file works with. It also checks that the trigger’s delivery mode is one of the two modes the code understands.

**Data flow**: It receives a row from the `source_trigger` table. It checks the `delivery` field, copies the row’s values into a `SourceTrigger`, and normalizes the created and updated times through `_utc`. It returns a clean Python object, or raises an error if the row contains an unknown delivery value.

**Call relations**: The store methods call this after database reads or inserts. `create`, `waking`, and `list_reported` all rely on it so callers receive the same well-shaped trigger object no matter which database query produced it.

*Call graph*: calls 1 internal fn (_utc); called by 3 (create, list_reported, waking); 1 external calls (__init__).


##### `SourceTriggerStore.workspace_id`  (lines 114–115)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property gives the store the workspace ID from its extension context. It is used so every database action stays inside the current workspace.

**Data flow**: It reads `workspace_id` from `self.ctx` and returns it. It does not change anything; it simply provides the workspace boundary used in later queries.

**Call relations**: The other store methods use this value when creating, deleting, and selecting trigger rows. It is the small access point that keeps workspace filtering consistent across the file.


##### `SourceTriggerStore.create`  (lines 117–166)

```
async def create(self, conversation_id: UUID, binding: str, delivery: SourceTriggerDelivery, created_by_member_id: UUID | None=None) -> SourceTrigger
```

**Purpose**: This creates a new trigger saying that one conversation should be notified when a specific source binding changes. It refuses unsafe or duplicate requests in plain application terms instead of letting a database error leak out.

**Data flow**: It receives a conversation ID, a source binding name, a delivery mode, and optionally the member who created it. It finds the currently executing agent, checks that the conversation belongs to that agent, then inserts a new row with a fresh ID and timestamps. If the same conversation already watches the same binding, it raises a clear error; otherwise it returns the new `SourceTrigger`.

**Call relations**: This is called when something wants to subscribe a conversation to a source. It asks `object_agent_id` which agent is active, uses the context to check the conversation’s owner, writes through the database transaction, and hands the inserted row to `_trigger` so the caller gets a normal trigger object back.

*Call graph*: calls 1 internal fn (_trigger); 2 external calls (object_agent_id, uuid4).


##### `SourceTriggerStore.remove`  (lines 168–183)

```
async def remove(self, expected: SourceTrigger) -> None
```

**Purpose**: This removes one specific trigger, but only if it still matches the trigger the caller expected. That protects against deleting the wrong rule if something changed between reading and removing it.

**Data flow**: It receives the expected `SourceTrigger`. It checks that the trigger still belongs to the currently executing agent, then deletes a row matching the workspace, trigger ID, agent, conversation, and binding. If no row was deleted, it reports that the trigger changed while removal was attempted.

**Call relations**: This is used when a known trigger should be unsubscribed. It calls `object_agent_id` for the current agent and uses a SQL delete. Unlike `remove_binding`, it is careful and narrow: it removes exactly the trigger the caller already read.

*Call graph*: 2 external calls (delete, object_agent_id).


##### `SourceTriggerStore.remove_binding`  (lines 185–194)

```
async def remove_binding(self, binding: str) -> None
```

**Purpose**: This removes every trigger in the current workspace for one source binding. It is what you want when the source itself is removed, because no conversation should keep watching something that no longer exists.

**Data flow**: It receives a binding string. It deletes all rows in the current workspace whose binding matches that string. It returns nothing and does not care which agents created those rows, because the source belongs to the whole workspace.

**Call relations**: This sits on the cleanup path for source removal. It uses a SQL delete directly and deliberately works across agents inside the same workspace, unlike member-facing listing methods that focus on the current agent.

*Call graph*: 1 external calls (delete).


##### `SourceTriggerStore.waking`  (lines 196–209)

```
async def waking(self, binding: str) -> tuple[SourceTrigger, ...]
```

**Purpose**: This finds all triggers that should wake when a particular source binding has new activity. It is the lookup used by the alert sweep before notifications or agent work are started.

**Data flow**: It receives a binding string. It selects all matching trigger rows in the current workspace, ordered by creation time and ID for stable processing, converts each row through `_trigger`, and returns them as a tuple.

**Call relations**: Alert processing calls this to answer, “who is waiting on this source?” It builds a SQL select, reads the database inside a transaction, then hands every row to `_trigger` so the alerting code receives ready-to-use `SourceTrigger` objects.

*Call graph*: calls 1 internal fn (_trigger); 1 external calls (select).


##### `SourceTriggerStore.list_reported`  (lines 211–245)

```
async def list_reported(self, *, conversation_id: UUID | None=None) -> tuple[ListedTrigger, ...]
```

**Purpose**: This lists triggers for user-facing views, including information about the conversation where each trigger fires. It adds audience and surface label so the caller can decide what a member is allowed to see and can show a current conversation name.

**Data flow**: It optionally receives a conversation ID to narrow the list. It selects triggers in the current workspace for the currently executing agent, converts them into `SourceTrigger` objects, then asks the context for live facts about their conversations. It returns `ListedTrigger` objects that pair each trigger with its conversation audience and label, skipping triggers whose conversation facts no longer exist.

**Call relations**: This is used when the system needs to show triggers to a member rather than run alerts. It calls `object_agent_id` to stay within the current agent’s object namespace, uses `_trigger` to shape database rows, then asks the extension context for conversation facts before building `ListedTrigger` results.

*Call graph*: calls 1 internal fn (_trigger); 3 external calls (__init__, select, object_agent_id).
