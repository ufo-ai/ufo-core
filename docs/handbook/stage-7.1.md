# Process heartbeats, abandoned work cleanup, and cancellation propagation  `stage-7.1`

This stage is behind-the-scenes housekeeping for the running system. While the main service is doing conversation work, it also needs to prove that each serve process is still alive, notice when work has been abandoned, and stop related work when a parent task is cancelled.

The key piece is core/src/ufo/runtime_instance.py. It acts like a check-in desk and cleanup crew. Each running process regularly records a “heartbeat,” which is a small sign of life that other parts of the fleet can see. If those signs stop, the system can treat that process as gone instead of waiting forever. The same file runs background cleanup jobs that look for turns, meaning units of conversation work, that were left marked as running after a crash or timeout. It releases or corrects that state so later conversation work is not blocked. It also carries cancellation downward: if a parent turn is cancelled, any child turns started from it are cancelled too, keeping the whole workflow consistent.

## Files in this stage

### Process heartbeats, abandoned work cleanup, and cancellation propagation
### `core/src/ufo/runtime_instance.py`

`orchestration` · `background during serve process lifetime`

This file is the fleet’s “is everyone still alive?” system, plus the cleanup crews that act on that information. Each serve process writes a small database row called a runtime instance seat. It then updates that row every few seconds, like signing a visitor log to prove it is still in the building. Other processes use those fresh signatures to decide which workflow executors are alive and which ones have disappeared.

The file runs three main periodic sweeps. ExecutorRecovery looks for DBOS workflows that are still pending under executor IDs whose runtime instance heartbeat is no longer fresh. DBOS is the durable workflow system, meaning it records enough state to restart work safely. If the executor is dead, this sweep asks DBOS to recover that pending work.

CancelReconciler spreads cancellation down a turn tree. Cancelling one turn only marks that turn itself; this sweep finds live descendant turns under a cancelled ancestor and cancels them too.

StrandedTurnReconciler catches a different stuck state: a turn marked running, but whose workflow attempt has already ended or disappeared. Since nothing can advance that turn anymore, it is safely cancelled after a grace period. All loops log temporary failures and continue, so one bad database tick does not stop the background safety net.

#### Function details

##### `record_fleet_seat`  (lines 42–57)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: Creates this process’s runtime instance row before workflow execution starts. This row is the process’s public proof that it exists, so other processes do not mistake its newly assigned work for abandoned work.

**Data flow**: It receives the process instance ID. It opens an owner-level database transaction, inserts a runtime_instance row with no workspace attached, and stamps the current time as its heartbeat and creation/update time. It then writes a log event saying the fleet seat was recorded.

**Call relations**: This is an early setup step for a serve process. After it records the seat, the Heartbeat loop keeps that same row fresh, and ExecutorRecovery later reads these rows to decide which executor IDs are still alive.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 70–80)

```
async def run(self) -> None
```

**Purpose**: Runs forever, regularly refreshing this process’s liveness mark in the database. If one refresh fails because of a temporary database problem, it logs the failure and keeps trying instead of letting the heartbeat loop die.

**Data flow**: It starts with the instance ID stored on the Heartbeat object. On each cycle it calls Heartbeat.beat to update the database row, catches database errors, logs them, then waits for the heartbeat interval before repeating. It does not return during normal operation.

**Call relations**: This is the outer loop that drives Heartbeat.beat. Its fresh timestamps are what ExecutorRecovery later treats as evidence that an executor is alive and should not be recovered by another process.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 82–92)

```
async def beat(self) -> None
```

**Purpose**: Writes one fresh heartbeat timestamp for this process. It is the single database update that says, “this serve process is still alive right now.”

**Data flow**: It reads the Heartbeat object’s instance ID, opens a database transaction, and updates the matching runtime_instance row with the current heartbeat and update time. It changes only that process’s row and produces no separate return value.

**Call relations**: Heartbeat.run calls this on every heartbeat tick. ExecutorRecovery._live_executors later reads these timestamps to separate live executors from dead ones.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 94–100)

```
async def retire(self) -> None
```

**Purpose**: Removes this process’s runtime instance row during graceful shutdown. That lets peers see immediately that the process has left instead of waiting for its heartbeat to become old.

**Data flow**: It takes the Heartbeat object’s instance ID, opens a database transaction, and deletes the matching runtime_instance row. The database changes from “this process has a seat” to “this process is gone.”

**Call relations**: The serve shutdown path calls this through core/src/ufo/serve._stop_executor. After retirement, recovery sweeps no longer see this executor as live.

*Call graph*: called by 1 (_stop_executor); 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 120–126)

```
async def run(self) -> None
```

**Purpose**: Runs the executor recovery sweep on a timer. Its job is to keep abandoned pending workflows from staying stuck after a serve process crashes.

**Data flow**: It repeatedly waits for its interval, calls ExecutorRecovery.sweep, and logs database or DBOS workflow-system errors without stopping the loop. Under normal operation it keeps running for the lifetime of the process.

**Call relations**: This is the periodic driver for ExecutorRecovery.sweep. Every serve process can run it, so any surviving process can help recover work left behind by a dead peer.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 128–136)

```
async def sweep(self) -> None
```

**Purpose**: Finds executor IDs that still own pending workflows but no longer have a fresh heartbeat, then asks DBOS to recover their work. This prevents queued durable work from being tied forever to a process that has died.

**Data flow**: It asks _pending_executors for executor IDs attached to pending DBOS workflows and _live_executors for executor IDs with fresh runtime_instance rows. It subtracts the live set from the pending set, then for each stranded executor calls DBOS recovery in a worker thread. It logs how many workflows were recovered for each executor.

**Call relations**: ExecutorRecovery.run calls this on each timer tick. It depends on Heartbeat.beat keeping live rows fresh and hands stranded executor IDs to DBOS so the workflow system can re-dispatch their pending work.

*Call graph*: calls 2 internal fn (_live_executors, _pending_executors); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_executors`  (lines 138–150)

```
async def _pending_executors(self) -> set[str]
```

**Purpose**: Collects the executor IDs currently attached to pending DBOS workflows. These are possible recovery candidates, but only if their executors are not alive anymore.

**Data flow**: It asks DBOS for a limited list of pending workflows without loading their full inputs or outputs. From those workflow status records, it extracts executor IDs that are present. If the scan reaches the configured limit, it logs that the result may be capped.

**Call relations**: ExecutorRecovery.sweep calls this before comparing against _live_executors. Its output is the broad pool of executors that might need recovery.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 152–162)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: Finds executor IDs whose runtime instance rows have been updated recently enough to count as alive. This protects live work from being recovered twice.

**Data flow**: It computes a cutoff time by subtracting the stale window from the current time. It then queries runtime_instance rows whose heartbeat is newer than that cutoff and returns their IDs as strings. The result is a set of currently live executor IDs.

**Call relations**: ExecutorRecovery.sweep calls this alongside _pending_executors. Any executor in this live set is removed from the recovery target list, so DBOS recovery is only requested for work owned by dead or missing executors.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `CancelReconciler.run`  (lines 186–192)

```
async def run(self) -> None
```

**Purpose**: Runs the cancellation cleanup sweep on a timer. It makes cancellation eventually spread from a cancelled turn to the live descendant turns beneath it.

**Data flow**: It repeatedly waits for its interval, calls CancelReconciler.sweep, and logs database or DBOS errors without ending the loop. It normally produces no final return because it is a long-running background task.

**Call relations**: This is the periodic driver for CancelReconciler.sweep. It keeps cancellation reliable even if the original canceller crashed before all descendants were marked.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 194–201)

```
async def sweep(self) -> None
```

**Purpose**: Finds live turns that sit below a cancelled ancestor and cancels them one by one. This is what turns a local cancellation into a whole-subtree cancellation where appropriate.

**Data flow**: It builds and runs the orphan query in a database transaction, receiving turn IDs and workspace IDs. For each matching turn, it enters that workspace context and calls cancel_one_turn. If the turn was actually cancelled, it logs the reconciliation event.

**Call relations**: CancelReconciler.run calls this periodically. It uses _orphans_query to find the work and then hands each turn to the shared cancellation primitive, so cancellation behavior stays consistent with other parts of the system.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (cancel_one_turn, owner_tx, log, ws).


##### `CancelReconciler._orphans_query`  (lines 203–243)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: Builds the database query that finds non-finished turns with a cancelled ancestor above them. It is careful to climb beyond direct parents, so grandchildren and deeper descendants are found too.

**Data flow**: It starts from every non-terminal turn, then builds a recursive database query that walks upward through parent_turn_id links. The query stops once it finds a cancelled ancestor and returns the original live turn’s ID and workspace. It does not execute the query itself; it returns the query object for sweep to run.

**Call relations**: CancelReconciler.sweep calls this to decide which turns should be cancelled. While building the parent chain, it calls _profile_child_parent to avoid crossing agent-child boundaries that should remain independent.

*Call graph*: calls 1 internal fn (_profile_child_parent); called by 1 (sweep); 1 external calls (select).


##### `CancelReconciler._profile_child_parent`  (lines 245–246)

```
def _profile_child_parent(self, turn: sa.Table | sa.FromClause) -> sa.ColumnElement
```

**Purpose**: Defines which parent link should count when walking the cancellation tree. It prevents cancellation from crossing into spawned agent children that are meant to act independently.

**Data flow**: It receives a turn table or table-like object. It returns a SQL expression: if the turn has no subagent profile, the walk treats it as having no cancellable parent; otherwise it uses parent_turn_id. This expression becomes part of the larger orphan-finding query.

**Call relations**: CancelReconciler._orphans_query calls this while building both the starting rows and recursive ancestor steps. Its rule shapes exactly which descendants are considered part of the cancelled subtree.

*Call graph*: called by 1 (_orphans_query); 2 external calls (case, null).


##### `StrandedTurnReconciler.run`  (lines 280–286)

```
async def run(self) -> None
```

**Purpose**: Runs the stranded-running-turn cleanup on a timer. It prevents turns marked as running from staying that way forever after their workflow attempt is no longer able to advance them.

**Data flow**: It repeatedly waits for its interval, calls StrandedTurnReconciler.sweep, and logs database or DBOS errors while keeping the loop alive. Like the other background loops, it normally runs until the process stops.

**Call relations**: This is the periodic driver for StrandedTurnReconciler.sweep. It complements ExecutorRecovery: executor recovery restarts pending workflow work, while this loop cancels running turn rows whose workflow attempt has ended or vanished.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `StrandedTurnReconciler.sweep`  (lines 288–304)

```
async def sweep(self) -> None
```

**Purpose**: Cancels running turns whose recorded workflow attempt is no longer pending, enqueued, or delayed in DBOS. In plain terms, it clears turns that say “I’m running” even though no workflow is still carrying them forward.

**Data flow**: It queries older running claimed turns through _claimed_query. If the scan hits its limit, it logs that fact. It then asks _advancing_attempts which recorded workflow attempts are still active enough to advance. For each claimed turn whose attempt is not in that active set, it enters the turn’s workspace and calls cancel_one_turn, then logs successful cancellations.

**Call relations**: StrandedTurnReconciler.run calls this periodically. It uses _claimed_query to choose safe candidates, _advancing_attempts to avoid touching live workflow attempts, and cancel_one_turn to make the final state change safely.

*Call graph*: calls 2 internal fn (_advancing_attempts, _claimed_query); called by 1 (run); 4 external calls (cancel_one_turn, owner_tx, log, ws).


##### `StrandedTurnReconciler._claimed_query`  (lines 306–323)

```
def _claimed_query(self) -> sa.Select
```

**Purpose**: Builds the query for old running turns that have a recorded workflow attempt. These are the only turns that might be stranded in the specific way this reconciler fixes.

**Data flow**: It computes a cutoff time using the grace period, then creates a database query for turns with RUNNING status, a non-empty running_attempt, and an updated_at time older than the cutoff. It orders oldest first and limits the number returned. It returns the query object rather than executing it.

**Call relations**: StrandedTurnReconciler.sweep calls this before reading candidate rows from the database. The grace period in this query helps avoid racing with a turn that was just claimed and is still starting up.

*Call graph*: called by 1 (sweep); 3 external calls (now, timedelta, select).


##### `StrandedTurnReconciler._advancing_attempts`  (lines 325–336)

```
async def _advancing_attempts(self, attempts: list[str]) -> set[str]
```

**Purpose**: Checks which workflow attempt IDs are still in a DBOS state that can move a turn forward. This prevents the reconciler from cancelling work that is still legitimately queued or delayed.

**Data flow**: It receives a list of workflow attempt IDs. If the list is empty, it returns an empty set immediately so it does not accidentally ask DBOS for everything. Otherwise it asks DBOS for workflows among those IDs whose status is pending, enqueued, or delayed, and returns the workflow IDs that are still carried by DBOS.

**Call relations**: StrandedTurnReconciler.sweep calls this after finding claimed running turns. The sweep compares each turn’s running_attempt against this returned set to decide whether to leave it alone or cancel it as stranded.

*Call graph*: called by 1 (sweep).
