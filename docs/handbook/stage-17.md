# Result publication, teardown, recovery, and cleanup  `stage-17`

This stage is the system’s “put everything away safely” phase. It runs after a turn finishes, is stopped, crashes, or gets stuck. A turn is one unit of work in a conversation. The goal is to publish the final state, cancel what should no longer run, save useful records, and prevent half-finished work from being left behind.

The stop surface is the front door for a user or member asking to stop a running turn. It checks whether stopping is allowed, marks the turn as cancelled, may start the next needed turn, and notifies live listeners that the old work ended. The cancellation helper does the careful inner step: it stops the workflow before recording cancellation in the database.

Workspace change tracking records what files changed, using the sandbox’s file scanner as the trusted source. Delivery cleanup is a safety net for child turns whose results were saved but not handed back to their parent. Runtime instance cleanup keeps running server processes visible and sweeps for stuck processes, workflows, child turns, and turns that look busy but cannot move forward.

## Files in this stage

### Recovery sweeps
Background recovery loops keep completed child work, stuck turns, workflows, and crashed serve processes from remaining stranded.

### `core/src/ufo/runtime/delivery.py`

`orchestration` · `background scheduled sweep`

When one agent delegates work to a child agent, the child is supposed to report its final result back to the parent. Usually that happens as part of the child’s normal execution path. But some endings happen from the outside, such as another process cancelling the child, or a crash happening at just the wrong time. In those cases, the database may show that the child is finished, but the parent never receives the wake-up it was waiting for.

`DeliverySweep` is the backstop for that gap. Think of it like a postal worker checking a bin of completed but undelivered letters. It looks in durable database state, not in a running task’s memory, for child turns that are terminal, still marked as pending delivery, and whose parent agent is not archived. It groups them by the parent conversation so that if many child results finish together, the parent can be woken once with the whole set rather than repeatedly.

The sweep also has a cooldown. If a conversation was already woken recently by another delivery path, this pass skips it and leaves its pending children for a later tick. That prevents a tight loop where a woken parent immediately spawns more children that wake it again. If the parent app is archived, delivery is skipped without stopping the whole sweep; the pending result can be delivered later if the app is restored.

#### Function details

##### `DeliverySweep.run`  (lines 54–72)

```
async def run(self) -> None
```

**Purpose**: This is the main body of the delivery sweep. It finds finished child turns that still need to report back, avoids conversations that were just woken, and asks the normal subagent result delivery path to hand each result to its parent.

**Data flow**: It starts with no direct input beyond the sweep object’s `invoker_for` factory and subagent registry. It asks `_outstanding` for pending finished children grouped by parent conversation, checks `_woken_since` to find conversations recently woken, builds a `SubagentResult` for the current workspace, and then tries to deliver each child that is safe to process. The visible result is database and runtime side effects: child results may be marked delivered and parent conversations may be woken. If a parent agent is archived, that child is simply left for a later pass.

**Call relations**: The scheduled job calls this method when it is time to sweep a workspace. Inside the flow, it relies on `_outstanding` to say what work exists, `_woken_since` to avoid waking the same conversation too often, `ws_current` to know which workspace it is operating in, and `SubagentResult` to reuse the same delivery mechanism used by the normal event path.

*Call graph*: calls 2 internal fn (_outstanding, _woken_since); 4 external calls (__init__, now, timedelta, ws_current).


##### `DeliverySweep.candidate_workspaces`  (lines 74–93)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This finds which workspaces are worth running the sweep for. It prevents the system from scanning every workspace when only a few have finished child turns waiting to be delivered.

**Data flow**: It reads from the owner-level database view across workspaces. It looks for child turns marked as pending delivery, already terminal, and attached to a parent agent that has not been archived. It returns a tuple of workspace IDs where such work exists, with duplicates removed.

**Call relations**: A higher-level scheduler can call this before running the sweep so it knows which workspaces need attention. The method opens an owner database transaction and builds a query with SQLAlchemy, then hands the scheduler only the workspace identifiers rather than the full turn data.

*Call graph*: 2 external calls (select, owner_tx).


##### `DeliverySweep._outstanding`  (lines 95–131)

```
async def _outstanding(self) -> dict[UUID, list[Turn]]
```

**Purpose**: This gathers the actual child turns in the current workspace that are finished but still not delivered. It organizes them under the parent conversation that should receive the results.

**Data flow**: It reads the workspace database for child turns whose delivery status is pending, whose terminal result exists, and whose parent agent is not archived. It also reads the parent turn’s conversation ID so each child can be grouped under the right conversation. It returns a dictionary where each key is a parent conversation ID and each value is a list of validated `Turn` records, ordered so related fan-out work is processed together and older finished children come first.

**Call relations**: `DeliverySweep.run` calls this at the start of a sweep pass. This helper does the database lookup and record-building work, then gives `run` a clean conversation-to-children map so the main method can focus on delivery decisions.

*Call graph*: called by 1 (run); 3 external calls (model_validate, select, workspace_tx).


##### `DeliverySweep._woken_since`  (lines 133–160)

```
async def _woken_since(self, cutoff: datetime, conversations: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: This checks whether any of the candidate parent conversations were already woken recently by a delivered child result. It is the sweep’s guard against repeatedly waking the same conversation too quickly.

**Data flow**: It receives a cutoff time and a tuple of conversation IDs. It queries the workspace database for delivered child turns connected to those conversations whose update time is newer than the cutoff. It returns a frozen set of conversation IDs that should be skipped for now.

**Call relations**: `DeliverySweep.run` calls this after finding outstanding work and computing the cooldown cutoff time. The returned set tells `run` which conversation groups to leave untouched until a later sweep, while all other groups can be passed on to `SubagentResult` for delivery.

*Call graph*: called by 1 (run); 2 external calls (select, workspace_tx).


### `core/src/ufo/runtime/runtime_instance.py`

`orchestration` · `background during serve process lifetime`

A serve process is one running copy of the system. This file gives each process a “seat” in the database and keeps that seat fresh with a heartbeat, like regularly raising a hand to say “I’m still here.” Other processes use those heartbeats to decide whether work owned by a process is still alive or has been abandoned.

The file also defines three recurring safety sweeps. ExecutorRecovery looks for DBOS workflows, meaning durable background jobs recorded by DBOS, that are still pending under an executor whose heartbeat is stale or missing. It asks DBOS to recover those workflows so another live process can continue them. CancelReconciler spreads cancellation down a tree of turns: if a parent turn is cancelled, its dependent child turns should be cancelled too. StrandedTurnReconciler fixes turns marked running when their recorded workflow attempt has already ended or disappeared, cancelling them so later conversation flow is not blocked.

These loops are deliberately periodic rather than instant. That makes them crash-safe and simple: if one tick fails because the database or DBOS is temporarily unavailable, the error is logged and the next tick tries again. The overall design is conservative: it avoids touching work that still belongs to a live process, because recovering or cancelling live work could create duplicate execution or incorrect shutdown.

#### Function details

##### `record_fleet_seat`  (lines 42–57)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: Creates this process’s fleet “seat” in the runtime_instance table before DBOS starts running work. This makes the process visible as alive so recovery code does not mistake its new work for abandoned work.

**Data flow**: It receives an instance_id, opens an owner database transaction, inserts a runtime_instance row with no workspace, current heartbeat time, and timestamps, then writes a log message. The visible result is a fresh database row that other fleet processes can read as this process’s liveness signal.

**Call relations**: This is the first part of the liveness story. It writes the row that Heartbeat.beat later refreshes, and that ExecutorRecovery._live_executors later reads when deciding which executors are safe to leave alone.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 70–80)

```
async def run(self) -> None
```

**Purpose**: Runs forever, periodically refreshing this process’s heartbeat. It is meant to keep one failed database update from making a healthy process look dead.

**Data flow**: It starts with the Heartbeat object’s instance_id. On each loop, it calls Heartbeat.beat to update the database row; if a database error happens, it logs the failure instead of stopping. It then waits for the configured heartbeat interval and repeats.

**Call relations**: This is the repeating driver for Heartbeat.beat. Other parts of the system rely on its repeated updates indirectly, especially ExecutorRecovery._live_executors, which treats recently updated rows as live executors.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 82–92)

```
async def beat(self) -> None
```

**Purpose**: Writes one heartbeat stamp for this process. A caller uses it to say, in the database, “this runtime instance is alive right now.”

**Data flow**: It reads the Heartbeat object’s instance_id, opens an owner database transaction, and updates that runtime_instance row’s heartbeat_at and updated_at fields to the database’s current time. It returns no separate value; the change is the refreshed row.

**Call relations**: Heartbeat.run calls this on every heartbeat tick. Its database update is later interpreted by ExecutorRecovery._live_executors as evidence that the executor should not be recovered by another process.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 94–100)

```
async def retire(self) -> None
```

**Purpose**: Removes this process’s runtime_instance row during graceful shutdown. This lets peers see immediately that the seat is gone instead of waiting for the heartbeat to become stale.

**Data flow**: It reads the Heartbeat object’s instance_id, opens an owner database transaction, and deletes the matching runtime_instance row. It returns nothing; the database no longer shows this process as occupying a live seat.

**Call relations**: core/src/ufo/serve._stop_executor calls this when stopping the executor. It is the clean shutdown counterpart to record_fleet_seat and Heartbeat.beat.

*Call graph*: called by 1 (_stop_executor); 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 120–126)

```
async def run(self) -> None
```

**Purpose**: Runs the executor recovery sweep on a loop. It keeps checking for pending DBOS workflows that belong to processes that are no longer alive.

**Data flow**: It waits for the configured recovery interval, calls ExecutorRecovery.sweep, logs database or DBOS errors, and then repeats. Its output is not a returned value; its effect is repeated attempts to recover abandoned workflows.

**Call relations**: This is the periodic driver for ExecutorRecovery.sweep. It keeps recovery work separate from the main request or turn flow, so temporary failures are logged and retried later.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 128–136)

```
async def sweep(self) -> None
```

**Purpose**: Finds executors that still have pending workflows but no fresh heartbeat, then asks DBOS to recover their work. This is how surviving serve processes pick up work after another process crashes.

**Data flow**: It asks ExecutorRecovery._pending_executors for executor IDs attached to pending workflows and ExecutorRecovery._live_executors for executor IDs with fresh runtime rows. It subtracts live executors from pending executors; for each remaining stranded executor, it runs DBOS recovery in a worker thread and logs how many workflows were recovered.

**Call relations**: ExecutorRecovery.run calls this every interval. It depends on the heartbeat data maintained by record_fleet_seat and Heartbeat.beat, and hands actual workflow recovery off to DBOS.

*Call graph*: calls 2 internal fn (_live_executors, _pending_executors); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_executors`  (lines 138–150)

```
async def _pending_executors(self) -> set[str]
```

**Purpose**: Collects the executor IDs that currently own pending DBOS workflows. These are the processes that might need recovery if their heartbeat is not fresh.

**Data flow**: It asks DBOS for up to the configured limit of pending workflows without loading their inputs or outputs. If the result hits the limit, it logs that the scan may be capped. It returns a set of executor_id strings from the workflow status records that have one.

**Call relations**: ExecutorRecovery.sweep calls this as one half of its comparison. The returned set is later reduced by ExecutorRecovery._live_executors so only abandoned executors are recovered.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 152–162)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: Reads the database to find which executor IDs have heartbeats recent enough to count as alive. This protects live work from being recovered by mistake.

**Data flow**: It computes a cutoff time using the current time minus the stale-after window. Then it selects runtime_instance rows whose heartbeat_at is newer than that cutoff and returns their IDs as strings.

**Call relations**: ExecutorRecovery.sweep calls this after gathering pending executors. Any executor returned here is removed from the recovery target list, because its process is still considered alive.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `CancelReconciler.run`  (lines 186–192)

```
async def run(self) -> None
```

**Purpose**: Runs the cancellation reconciliation sweep forever. It makes cancellation eventually spread from a cancelled turn to dependent turns below it.

**Data flow**: It waits for the configured cancellation interval, calls CancelReconciler.sweep, logs database or DBOS errors, and repeats. It does not return a result; its effect is repeated cleanup of descendants that should also be cancelled.

**Call relations**: This is the loop that drives CancelReconciler.sweep. It keeps cascading cancellation outside the real-time turn path, so cancellation work can be retried safely if a tick fails.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 194–201)

```
async def sweep(self) -> None
```

**Purpose**: Finds live turns that sit under a cancelled dependent ancestor and cancels them. This prevents child work from continuing after the work it depends on has been cancelled.

**Data flow**: It builds and runs CancelReconciler._orphans_query inside an owner database transaction. For each returned turn and workspace, it enters that workspace context, calls cancel_one_turn through the DBOS client, and logs when a turn was actually cancelled.

**Call relations**: CancelReconciler.run calls this every interval. It uses CancelReconciler._orphans_query to decide what needs attention, and delegates the actual safe cancellation to cancel_one_turn.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (owner_tx, log, cancel_one_turn, ws).


##### `CancelReconciler._orphans_query`  (lines 203–243)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: Builds the database query that identifies non-terminal turns with a cancelled dependent ancestor. In plain terms, it finds live child or grandchild turns that should no longer keep running.

**Data flow**: It starts from turns whose status is not terminal, then recursively climbs their parent chain using CancelReconciler._dependent_parent. The climb stops when it finds a cancelled ancestor or can go no higher. It returns a SQL query selecting distinct orphan turn IDs and workspace IDs.

**Call relations**: CancelReconciler.sweep calls this before reading the database. It relies on CancelReconciler._dependent_parent to know which parent links count as dependency links for cancellation.

*Call graph*: calls 1 internal fn (_dependent_parent); called by 1 (sweep); 1 external calls (select).


##### `CancelReconciler._dependent_parent`  (lines 245–255)

```
def _dependent_parent(self, turn: sa.Table | sa.FromClause) -> sa.ColumnElement
```

**Purpose**: Decides whether a turn’s parent should count as a dependency for cancellation. This matters because not every parent-child relationship means the child must die when the parent is cancelled.

**Data flow**: It receives a turn table or table-like object and builds a SQL expression. If the turn has a subagent profile or came from intent admission, the expression returns its parent_turn_id; otherwise it returns null, which stops the ancestor climb.

**Call relations**: CancelReconciler._orphans_query calls this while constructing its recursive parent-walking query. Its decision shapes exactly which descendants CancelReconciler.sweep will later cancel.

*Call graph*: called by 1 (_orphans_query); 3 external calls (case, null, or_).


##### `StrandedTurnReconciler.run`  (lines 289–295)

```
async def run(self) -> None
```

**Purpose**: Runs the stranded-turn cleanup sweep forever. It looks for turns marked running even though their workflow attempt can no longer move them forward.

**Data flow**: It waits for the configured stranded-turn interval, calls StrandedTurnReconciler.sweep, logs database or DBOS errors, and repeats. It returns no value; its work is to keep the turn table from accumulating impossible running states.

**Call relations**: This is the periodic driver for StrandedTurnReconciler.sweep. Like the other loops in this file, it turns a potentially rare failure mode into a repeated, recoverable background check.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `StrandedTurnReconciler.sweep`  (lines 297–313)

```
async def sweep(self) -> None
```

**Purpose**: Cancels running turns whose recorded workflow attempt is no longer pending, enqueued, or delayed in DBOS. This frees conversations from turns that look active but have no live workflow behind them.

**Data flow**: It queries old enough running claimed turns using StrandedTurnReconciler._claimed_query. If the scan reaches the limit, it logs that fact. It then asks StrandedTurnReconciler._advancing_attempts which recorded attempts are still carried by DBOS. Any claimed row whose attempt is not in that live set is cancelled inside its workspace, and successful cancellations are logged.

**Call relations**: StrandedTurnReconciler.run calls this every interval. It uses _claimed_query to find possible problems, _advancing_attempts to avoid touching work still moving through DBOS, and cancel_one_turn to safely terminalize truly stranded turns.

*Call graph*: calls 2 internal fn (_advancing_attempts, _claimed_query); called by 1 (run); 4 external calls (owner_tx, log, cancel_one_turn, ws).


##### `StrandedTurnReconciler._claimed_query`  (lines 315–332)

```
def _claimed_query(self) -> sa.Select
```

**Purpose**: Builds the database query for running turns that have held the same workflow claim long enough to be suspicious. The grace window prevents newly claimed turns from being mistaken for stranded ones.

**Data flow**: It computes a cutoff time from the current time minus the grace period. It returns a SQL query selecting turn ID, workspace ID, and running_attempt for RUNNING turns with a non-empty running_attempt whose updated_at is older than the cutoff, ordered oldest first and capped at the scan limit.

**Call relations**: StrandedTurnReconciler.sweep calls this before reading candidate turns from the database. The candidates it finds are then checked against DBOS by StrandedTurnReconciler._advancing_attempts.

*Call graph*: called by 1 (sweep); 3 external calls (now, timedelta, select).


##### `StrandedTurnReconciler._advancing_attempts`  (lines 334–345)

```
async def _advancing_attempts(self, attempts: list[str]) -> set[str]
```

**Purpose**: Checks which workflow attempt IDs are still in a DBOS status that means they can advance. It separates genuinely stranded turns from turns whose workflow is merely waiting its turn.

**Data flow**: It receives a list of workflow attempt IDs. If the list is empty, it returns an empty set to avoid accidentally scanning the whole workflow store. Otherwise it asks DBOS for workflows among those IDs whose status is pending, enqueued, or delayed, and returns the workflow IDs that DBOS still carries.

**Call relations**: StrandedTurnReconciler.sweep calls this after finding claimed running turns. Any attempt ID returned here is treated as still alive, while missing IDs cause the sweep to hand the turn to cancel_one_turn.

*Call graph*: called by 1 (sweep).


### Stop and cancellation
User-initiated stopping validates the request, cancels the active turn safely, and publishes the resulting completion state.

### `core/src/ufo/runtime/surfaces/stop.py`

`orchestration` · `request handling`

This file is the “stop button” workflow for a conversation turn. A turn is one running unit of work inside a conversation. When a member asks to stop it, the system must be careful: it should not cancel the wrong turn, it should not disturb a turn that already finished on its own, and it should wake up any clients that are waiting for live updates.

The main piece is `MemberStop`, which is given three collaborators. A `DBOSClient` is used by the shared cancellation code, a `Hub` is used to publish live events, and `Admission` can create or select the next turn after the stop.

The workflow is deliberately ordered. First it opens a workspace database transaction and checks that the requested turn really belongs to the requested conversation in the requested workspace. This is like checking the label on a package before throwing it away. If the label does not match, it refuses the request.

Then it calls the common turn-cancellation primitive. If that code says the turn was already finished, this file returns a harmless “nothing ended” answer. If cancellation succeeds, it asks admission to redispatch the conversation so the member can move on to the next turn. Only after that does it publish the cancelled terminal event to the hub, so live tails wake up and see the replacement turn already exists.

#### Function details

##### `MemberStop.stop`  (lines 35–52)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops one running turn for a member, but only if that turn belongs to the given conversation and workspace. It makes the cancellation durable, starts or finds the follow-up turn, and returns a clear result saying whether anything actually ended.

**Data flow**: It receives a workspace ID, conversation ID, and turn ID. It reads the database to confirm that the turn belongs to that conversation inside that workspace. If not, it raises an error instead of touching anything. If the ownership check passes, it asks the shared cancellation code to cancel the turn. If the turn was already finished, it returns `Stopped` with `ended` set to false. If the cancellation happened, it asks admission to redispatch the conversation, publishes a terminal cancellation event to the hub, and returns `Stopped` with `ended` true and the follow-up turn ID.

**Call relations**: This function is the end-to-end stop path. It uses `workspace_tx` and `sqlalchemy.select` to safely check ownership in the database. It then hands the actual cancellation to `cancel_one_turn`, so cancellation rules stay shared with other cancel paths. After admission has prepared the next turn, it wraps the cancellation frame in `Terminal` and publishes it through the hub, then reports the outcome with `Stopped`.

*Call graph*: 5 external calls (__init__, __init__, select, workspace_tx, cancel_one_turn).


### `core/src/ufo/runtime/turns/cancellation.py`

`domain_logic` · `cancel handling`

This file solves a safety problem: cancelling a turn is not just flipping a database flag. Each turn may be running as a durable DBOS workflow, meaning work can survive crashes and resume later. If the database said “cancelled” before the workflow was actually told to stop, the system could believe the turn was dead while the work kept running in the background. This file prevents that.

The main idea is “cancel first, record second.” It looks up the turn row in the database. If the turn is already finished, it leaves it alone. If it is still active, it asks DBOS to cancel the workflow for the current running attempt. Then it locks and rereads the database row, like checking the label on a package before sealing it, to make sure no other worker claimed or changed the turn in the meantime. If the owner changed, it starts over and cancels the new owner instead.

Only after the correct workflow has been cancelled does it write the turn’s terminal result as cancelled. It keeps any objects the turn already created, so callers can still learn what existed before cancellation. Finally, it records a metric and asks the dispatcher to start the next eligible turn in the same conversation.

#### Function details

##### `cancel_one_turn`  (lines 24–102)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Cancels exactly one turn in a durable and race-safe way. It is used when any part of the system needs to stop a turn, while making sure the running workflow is told to stop before the turn is marked cancelled in the database.

**Data flow**: It receives a DBOS client and a turn ID. It reads the turn’s current status and running workflow attempt from the database; if the turn does not exist or is already terminal, it returns nothing. Otherwise it asks DBOS to cancel the workflow, rereads and locks the turn row, and checks that the same attempt still owns it. If ownership changed, it loops and tries again with the new attempt. Once the cancel is safely tied to the current owner, it writes a cancelled terminal frame into the turn row, clears any retry time, updates the timestamp, emits a cancellation metric, dispatches the next turn for the conversation, and returns the cancelled terminal frame.

**Call relations**: This is the shared cancellation primitive used by cancel initiators such as the evaluation driver, the cancel-spawn tool, and the cancellation reconciler. Inside its flow it opens database transactions with workspace_tx, builds SQL queries with SQLAlchemy, asks DBOSClient.cancel_workflow_async to stop the durable workflow, builds a TerminalFrame containing already-created ObjectRef values, reports the result through emit_metric and turn_profile, and then hands control to dispatch_next_turn so the conversation can continue if another turn is ready.

*Call graph*: 9 external calls (__init__, model_validate, cancel_workflow_async, select, update, workspace_tx, emit_metric, turn_profile, dispatch_next_turn).


### Workspace change records
Final workspace snapshots preserve the file-system changes made by a conversation for later display after the turn ends.

### `core/src/ufo/runtime/turns/workspace_changes.py`

`domain_logic` · `turn-end background refresh`

A conversation can change files in ways that are not fully captured by tool messages alone. For example, a shell command might delete a file, rename a folder, or modify many files without naming them one by one. This file solves that gap by asking the workspace itself what changed, much like checking a workbench after someone has finished using the tools.

First, it works out which parts of the workspace are worth checking. File-writing tools point to specific paths, while a shell command can affect anything, so it marks the workspace root as a target. It also keeps watching directories that had changes in the previous scan, so a changed checkout stays visible until it becomes clean again.

The main class, WorkspaceChangeRecorder, runs after a turn has already completed. It asks the sandbox to scan selected directories, validates the answer into strict data shapes, and stores the result in the database. If two turns using the same sandbox finish around the same time, it carefully merges their results instead of blindly overwriting one with the other. If scanning fails, it logs the problem but does not fail the already-finished turn; an old snapshot is better than breaking the conversation.

#### Function details

##### `change_targets`  (lines 37–55)

```
def change_targets(calls: Iterable[ToolUseBlock]) -> tuple[str, ...]
```

**Purpose**: This function reads the tool calls from a turn and extracts the workspace paths that may have been changed. It is used to decide where the later change scan should look, instead of scanning an entire possibly huge workspace every time.

**Data flow**: It receives a sequence of tool-use records. For write and edit calls, it takes the requested file path, checks that it really belongs inside the workspace, and stores the workspace-relative path. For bash calls, it stores the workspace root because a shell command may change anything. It ignores invalid paths and unrelated tools, removes duplicates while preserving first-seen order, and returns the resulting paths as a tuple.

**Call relations**: This is the front-door helper for deciding scan targets from tool activity. It relies on workspace_path to reject paths outside the workspace and PurePosixPath to turn accepted paths into clean workspace-relative names.

*Call graph*: 2 external calls (PurePosixPath, workspace_path).


##### `WorkspaceChangeRecorder.record`  (lines 101–114)

```
async def record(self) -> None
```

**Purpose**: This is the top-level action that refreshes the stored record of workspace changes after a turn. It gathers the previous scan, decides what to check next, asks the sandbox for fresh changes, and saves the merged result.

**Data flow**: It starts with the recorder’s sandbox, conversation, workspace id, and target paths. If the sandbox was never created and there are no targets, it does nothing. Otherwise it reads the last recorded changes, expands them into directories to watch, scans those directories, and stores the new answer. If anything goes wrong, it writes a log entry and leaves the old stored scan untouched.

**Call relations**: This method coordinates the whole file’s workflow. It calls recorded_workspace_changes to get the old snapshot, _directories to choose scan locations, _scan to ask the sandbox what changed, and _store to write the result. It logs failures instead of raising them because the turn has already completed.

*Call graph*: calls 4 internal fn (_directories, _scan, _store, recorded_workspace_changes); 1 external calls (log).


##### `WorkspaceChangeRecorder._directories`  (lines 116–129)

```
def _directories(self, recorded: WorkspaceChanges) -> list[str]
```

**Purpose**: This function turns file-level targets and previously changed files into a limited list of directories to scan. It keeps watching old changed directories so changes remain visible until the scanner reports they are gone.

**Data flow**: It receives the last recorded WorkspaceChanges object. It combines the recorder’s current target paths with paths from the previous scan, takes each path’s parent directory, sorts the unique directory names, and caps the list if it is too large. If it has to drop extra directories, it logs how many were dropped. It returns the final list of directory strings.

**Call relations**: record calls this just before scanning. The result is handed to _scan as the exact set of places the sandbox should inspect, balancing accuracy with safety so a huge workspace does not produce an unlimited scan request.

*Call graph*: called by 1 (record); 2 external calls (PurePosixPath, log).


##### `WorkspaceChangeRecorder._scan`  (lines 131–136)

```
async def _scan(self, directories: list[str]) -> WorkspaceChanges
```

**Purpose**: This function asks the sandbox’s file-system helper to report changes under the chosen directories. It also checks that the sandbox’s answer has the expected shape before the rest of the code trusts it.

**Data flow**: It receives a list of workspace-relative directories. It sends those paths to the sandbox command named changes. The raw response is then validated as a WorkspaceChanges object, which contains individual changed paths, patches, and truncation information. If the response is malformed, it raises a clear runtime error.

**Call relations**: record calls this after _directories has chosen where to look. Its validated result is passed to _store, which persists it. This function is the bridge between the recorder’s Python logic and the sandbox’s actual file-system scan.

*Call graph*: called by 1 (record).


##### `WorkspaceChangeRecorder._store`  (lines 138–161)

```
async def _store(self, scanned: WorkspaceChanges, asked: frozenset[str]) -> None
```

**Purpose**: This function writes the latest scan into the database without accidentally erasing work recorded by another turn at the same time. It treats the database row as the shared copy of the conversation’s current workspace-change snapshot.

**Data flow**: It receives the freshly scanned changes and the set of directories that were actually asked about. Inside a database transaction, it creates the conversation-change row if it does not already exist. Then it locks and reads the current stored scan, merges that stored scan with the fresh scan, and updates the row with the merged JSON data.

**Call relations**: record calls this after a successful scan. It calls _merged to decide what should survive from the old stored data. The database transaction and row lock are important because two recorders can finish at nearly the same time for the same workspace.

*Call graph*: calls 1 internal fn (_merged); called by 1 (record); 5 external calls (model_dump, and_, select, update, workspace_tx).


##### `WorkspaceChangeRecorder._merged`  (lines 163–178)

```
def _merged(self, scanned: WorkspaceChanges, stored: WorkspaceChanges, asked: frozenset[str]) -> WorkspaceChanges
```

**Purpose**: This function combines a fresh scan with the previously stored scan. Its job is to replace information for directories that were just checked, while preserving older information for directories this scan did not cover.

**Data flow**: It receives the new scanned changes, the old stored changes, and the set of directories scanned this time. It keeps old changes only when their parent directory was not part of this scan and the same path was not already reported freshly. It then puts fresh changes first, appends the kept old changes, caps the total number of changes, and marks the result as truncated if any information may have been left out.

**Call relations**: _store calls this while holding the database row lock. It is the conflict-resolution step that lets concurrent recorders share one stored snapshot without one scan wiping out another scan’s untouched directories.

*Call graph*: called by 1 (_store); 2 external calls (__init__, PurePosixPath).


##### `recorded_workspace_changes`  (lines 181–205)

```
async def recorded_workspace_changes(conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: This function reads the last stored workspace-change snapshot for a conversation. If the conversation is a subagent sharing a parent workspace, it resolves to the parent conversation’s stored snapshot instead.

**Data flow**: It receives a conversation id. It opens a database transaction, looks up the conversation that owns the sandbox workspace, then reads that owner’s stored change scan. If there is no conversation row or no stored scan yet, it returns the shared NOTHING_CHANGED value. If a scan exists, it validates and returns it as a WorkspaceChanges object.

**Call relations**: WorkspaceChangeRecorder.record calls this before deciding what directories to scan next. It provides the previous snapshot that keeps already-known changed directories under watch across turns and across subagents sharing the same workspace.

*Call graph*: called by 1 (record); 2 external calls (select, workspace_tx).

## 📊 State Registers Touched

- `reg-conversation-transcripts` — The durable conversation history, compacted records, audiences, and readable timeline data.
- `reg-turn-queue-state` — The durable state of conversation turns, including pending, running, paused, cancelled, and finished work.
- `reg-live-update-streams` — The shared live progress channels that stream text, status, costs, and completion events to clients.
- `reg-inbound-delivery-ledger` — The durable deduplication and delivery records for inbound messages, writebacks, and mid-turn replies.
- `reg-sandbox-handles` — The remembered sandbox workspaces and conversation sandbox handles used to resume or clean up execution.
- `reg-browser-sessions` — The active browser automation workbench for a turn, including Chrome sessions, tabs, and downloads.
- `reg-subagent-delivery-state` — The parent-child task links and owed-result records used when agents spawn helper agents.
- `reg-object-store-and-journal` — The shared workspace object records and change history for agents, tasks, memories, sites, and related items.
- `reg-hosted-site-registry` — The saved hosted-site names, owners, visibility, ports, files, previews, and ingress routing state.
- `reg-scheduled-work-store` — The durable records for recurring tasks, delayed resumes, scheduled fires, and background job claims.
- `reg-runtime-fleet-liveness` — The shared record of running service and worker instances, heartbeats, listener claims, and stuck work.
- `reg-telemetry-context` — The shared trace, metric, log, health, and redaction context used to observe work across the system.
- `reg-workspace-change-log` — Durable per-conversation sandbox file-change snapshots and summaries used after tool execution and shown in workspace-change slots.
- `reg-active-workflow-handles` — In-process handles for currently executing turns/workflows, including cancellation tokens and cleanup callbacks used to stop, tear down, or recover live work.
- `reg-workflow-checkpoints` — Durable per-turn workflow checkpoints, serialized runner state, and step/tool-output idempotency records used to resume, cancel, or recover work without rerunning completed actions.
- `reg-proposal-review-state` — Durable reviewable-change proposals with source/target digests, creator, approval state, and publication lifecycle outside the self-improvement prompt-promotion loop.
