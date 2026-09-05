# Runtime fleet coordination and crash recovery  `stage-8`

This stage is behind-the-scenes support for a running fleet of serve processes. A serve process is a live worker that can pick up and run conversations or workflows. The job here is to make sure the fleet knows which workers are alive, which work is claimed, and what to do when something stops halfway through.

`runtime_instance.py` is the fleet’s attendance sheet and cleanup crew. Each running process records that it is alive so other processes do not mistake it for a dead one. It also runs background repairs. If a workflow was cancelled, abandoned, or left in an uncertain state after a crash, this code finds it and moves it toward a safe restart or cleanup. It also fixes “child” work that was launched by a “parent” turn when the parent crashed or was cancelled.

`delivery.py` is a fallback mail carrier for child-agent results. Normally a child’s finished result is handed back directly to the parent conversation. If that handoff was missed, this file finds the result and delivers it so the parent can continue.

## Files in this stage

### Fleet liveness and recovery
Tracks live runtime instances and repairs workflows or delegated child-agent results left behind by crashes, cancellations, or missed handoffs.

### `core/src/ufo/runtime/runtime_instance.py`

`orchestration` · `background during serve process runtime`

A serve process is one running copy of the service. This file gives each process a “seat” in the database and keeps that seat fresh with a heartbeat, like a worker regularly tapping a badge reader to prove they are still in the building. Other background loops use those heartbeats to decide which work is safe to recover.

The main pieces are small repeated sweeps. `Heartbeat` updates this process’s row every few seconds and deletes it during graceful shutdown. `ExecutorRecovery` looks for DBOS workflows that are still pending under an executor id whose heartbeat is stale or missing, then asks DBOS to recover them. DBOS is the durable workflow system here: it records workflow progress so work can resume after failures.

`CancelReconciler` spreads cancellation down a turn tree. A “turn” is a unit of conversation or agent work. Cancelling one turn only marks that turn; this reconciler finds descendant turns that should also stop and cancels them safely.

`StrandedTurnReconciler` fixes a different stuck state: a turn marked running, but whose workflow attempt no longer exists or can no longer advance. After a grace period, it cancels those turns so the conversation does not wait forever on work nobody can complete.

#### Function details

##### `record_fleet_seat`  (lines 42–57)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: This creates the database row that says, “this serve process exists.” It is written before DBOS starts so the process is not mistaken for dead while it is booting.

**Data flow**: It receives the process instance id. It opens an owner database transaction, inserts a `runtime_instance` row with no workspace attached, stamps the current time as the heartbeat and creation time, then logs that the fleet seat was recorded.

**Call relations**: This is the first part of the liveness story. Later, `Heartbeat.beat` keeps this row fresh, and `ExecutorRecovery._live_executors` reads these rows to decide which executors are still alive.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 70–80)

```
async def run(self) -> None
```

**Purpose**: This is the repeating heartbeat loop for one serve process. It keeps calling `beat` so the database continues to show that the process is alive.

**Data flow**: It uses the `Heartbeat` object’s instance id. Each loop tries to update the heartbeat through `Heartbeat.beat`; if a database error happens, it logs the failure instead of stopping; then it waits for the configured heartbeat interval and tries again.

**Call relations**: This loop drives `Heartbeat.beat` for as long as the process is running. Its steady updates are what keep `ExecutorRecovery.sweep` from recovering work that still belongs to a live process.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 82–92)

```
async def beat(self) -> None
```

**Purpose**: This writes one fresh liveness stamp for the current process. It is the actual database update behind the heartbeat loop.

**Data flow**: It reads the instance id from the `Heartbeat` object. It opens an owner database transaction and updates that process’s `runtime_instance` row so `heartbeat_at` and `updated_at` become the current database time. It returns nothing, but the database row is now fresh.

**Call relations**: `Heartbeat.run` calls this every few seconds. `ExecutorRecovery._live_executors` later reads the updated timestamp to decide that this executor should not be recovered by another process.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 94–100)

```
async def retire(self) -> None
```

**Purpose**: This removes the process’s liveness row during a clean shutdown. It lets peers see immediately that the process has left, instead of waiting for its heartbeat to become stale.

**Data flow**: It reads the instance id from the `Heartbeat` object. It opens an owner database transaction and deletes the matching `runtime_instance` row. The database no longer advertises this process as alive.

**Call relations**: The serve shutdown path calls this through `core/src/ufo/serve._stop_executor`. It is the graceful counterpart to heartbeat expiry: instead of timing out, the process actively gives up its seat.

*Call graph*: called by 1 (_stop_executor); 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 120–126)

```
async def run(self) -> None
```

**Purpose**: This is the repeating loop that looks for workflows abandoned by dead processes. It makes crash recovery happen automatically without a human restarting each job.

**Data flow**: It uses the configured recovery interval. Each cycle waits, calls `ExecutorRecovery.sweep`, logs database or DBOS workflow errors if they occur, and keeps looping rather than dying on one failed tick.

**Call relations**: This loop is the driver for `ExecutorRecovery.sweep`. Every serve process can run it, so any surviving process can recover work left behind by another one.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 128–136)

```
async def sweep(self) -> None
```

**Purpose**: This finds executors that have pending workflow work but no fresh heartbeat, then asks DBOS to recover that work. In plain terms, it finds tasks assigned to workers who appear to be gone and puts those tasks back into motion.

**Data flow**: It asks `_pending_executors` for executor ids attached to pending DBOS workflows, and `_live_executors` for executor ids with fresh database heartbeats. It subtracts live executors from pending executors. For each remaining stranded executor, it calls DBOS recovery in a worker thread and logs how many workflows were recovered.

**Call relations**: `ExecutorRecovery.run` calls this on a schedule. It relies on `Heartbeat.beat` having kept live process rows fresh, and it hands stranded executor ids to DBOS so the durable workflow system can re-dispatch their work.

*Call graph*: calls 2 internal fn (_live_executors, _pending_executors); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_executors`  (lines 138–150)

```
async def _pending_executors(self) -> set[str]
```

**Purpose**: This gathers the executor ids currently holding pending DBOS workflows. These are the candidates that might need recovery if their executor is no longer alive.

**Data flow**: It asks DBOS for up to the configured scan limit of workflows whose status is `PENDING`, without loading their full inputs or outputs. If the result hits the limit, it logs that the scan may have more to see. It returns a set of executor id strings found on those workflow records.

**Call relations**: `ExecutorRecovery.sweep` calls this before comparing against live executors. Its output is only a candidate list; `_live_executors` is used next to avoid touching work that still belongs to a healthy process.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 152–162)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: This reads the database to find process ids whose heartbeat is still recent. These are treated as live executors and are protected from recovery.

**Data flow**: It computes a cutoff time by subtracting the stale window from the current time. It opens an owner database transaction, selects `runtime_instance` rows with `heartbeat_at` newer than that cutoff, and returns their ids as strings.

**Call relations**: `ExecutorRecovery.sweep` calls this alongside `_pending_executors`. The difference between the two sets tells the sweep which executors are likely dead and safe to recover.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `CancelReconciler.run`  (lines 186–192)

```
async def run(self) -> None
```

**Purpose**: This is the repeating loop that spreads cancellation from a cancelled turn to its dependent descendants. It makes cancellation eventually cover the whole affected branch, even if the original canceller only stopped one turn.

**Data flow**: It uses the configured cancel reconciliation interval. Each cycle waits, calls `CancelReconciler.sweep`, logs database or DBOS errors if they happen, and continues looping.

**Call relations**: This loop drives `CancelReconciler.sweep`. Running it in every serve process means cancellation cleanup does not depend on the same process that originally issued the cancel surviving.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 194–201)

```
async def sweep(self) -> None
```

**Purpose**: This finds live turns that sit underneath a cancelled ancestor and cancels them one by one. It is the practical step that turns a parent cancellation into descendant cancellations.

**Data flow**: It opens an owner database transaction and runs `_orphans_query` to get affected turn ids and workspace ids. For each row, it enters that workspace context, calls `cancel_one_turn` to cancel the turn safely, and logs when a turn was actually cancelled.

**Call relations**: `CancelReconciler.run` calls this on a schedule. It depends on `_orphans_query` to identify the right descendants, and it hands each selected turn to the shared cancellation primitive `cancel_one_turn` so cancellation uses the same safe path as other parts of the system.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (owner_tx, log, cancel_one_turn, ws).


##### `CancelReconciler._orphans_query`  (lines 203–243)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: This builds the database query that finds non-terminal turns with a cancelled ancestor. It is careful to follow only parent links where cancellation should really flow.

**Data flow**: It starts from every turn whose status is not terminal. Using a recursive database query, it walks upward through each turn’s dependent parents until it either finds a cancelled ancestor or reaches a boundary where cancellation should not cross. It returns a query that selects the live descendant turn id and workspace id for every match.

**Call relations**: `CancelReconciler.sweep` calls this to know what to cancel. During query construction it calls `_dependent_parent`, which encodes the rule for whether a parent turn counts as part of the same cancellation chain.

*Call graph*: calls 1 internal fn (_dependent_parent); called by 1 (sweep); 1 external calls (select).


##### `CancelReconciler._dependent_parent`  (lines 245–255)

```
def _dependent_parent(self, turn: sa.Table | sa.FromClause) -> sa.ColumnElement
```

**Purpose**: This expresses the rule for when a turn’s parent should be followed during cancellation lookup. It prevents cancellation from crossing into independent spawned agents, while still following dependent subagent or intent relationships.

**Data flow**: It receives a turn table or turn-like database alias. It returns a SQL expression: if the turn has a subagent profile or came from intent admission, use its `parent_turn_id`; otherwise return null, which stops the ancestor walk.

**Call relations**: `CancelReconciler._orphans_query` uses this while building its recursive search. This small rule decides the shape of the cancellation tree that `CancelReconciler.sweep` will act on.

*Call graph*: called by 1 (_orphans_query); 3 external calls (case, null, or_).


##### `StrandedTurnReconciler.run`  (lines 289–295)

```
async def run(self) -> None
```

**Purpose**: This is the repeating loop that looks for running turns whose workflow can no longer move them forward. It prevents conversations from being stuck forever on work that has silently disappeared or already ended elsewhere.

**Data flow**: It uses the configured stranded-turn interval. Each cycle waits, calls `StrandedTurnReconciler.sweep`, logs database or DBOS errors if they occur, and keeps looping.

**Call relations**: This loop drives `StrandedTurnReconciler.sweep`. Like the other reconcilers, it runs in serve processes as background repair work.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `StrandedTurnReconciler.sweep`  (lines 297–313)

```
async def sweep(self) -> None
```

**Purpose**: This finds old running turns with claimed workflow attempts, checks whether those attempts are still active in DBOS, and cancels turns whose attempts are gone or no longer advancing.

**Data flow**: It runs `_claimed_query` in an owner database transaction to get running turns older than the grace period. If the scan reaches the limit, it logs that fact. It asks `_advancing_attempts` which listed workflow attempts are still pending, enqueued, or delayed in DBOS. For each turn whose attempt is not advancing, it enters the turn’s workspace, calls `cancel_one_turn`, and logs successful reconciliation.

**Call relations**: `StrandedTurnReconciler.run` calls this on a schedule. It uses `_claimed_query` to find possible stuck rows and `_advancing_attempts` to avoid cancelling work that DBOS still knows how to run.

*Call graph*: calls 2 internal fn (_advancing_attempts, _claimed_query); called by 1 (run); 4 external calls (owner_tx, log, cancel_one_turn, ws).


##### `StrandedTurnReconciler._claimed_query`  (lines 315–332)

```
def _claimed_query(self) -> sa.Select
```

**Purpose**: This builds the database query for running turns that are old enough to inspect for stranding. The grace period avoids mistaking a freshly claimed turn for a stuck one.

**Data flow**: It computes a cutoff time from the current time minus the grace window. It returns a query selecting turn id, workspace id, and running workflow attempt for turns that are `RUNNING`, have a non-empty `running_attempt`, were last updated before the cutoff, and are ordered oldest first up to the scan limit.

**Call relations**: `StrandedTurnReconciler.sweep` calls this before checking DBOS. Its output is the candidate list; `_advancing_attempts` then separates still-active workflow attempts from truly stranded ones.

*Call graph*: called by 1 (sweep); 3 external calls (now, timedelta, select).


##### `StrandedTurnReconciler._advancing_attempts`  (lines 334–345)

```
async def _advancing_attempts(self, attempts: list[str]) -> set[str]
```

**Purpose**: This asks DBOS which workflow attempts are still capable of advancing. It protects live or queued work from being cancelled by the stranded-turn sweep.

**Data flow**: It receives a list of workflow attempt ids. If the list is empty, it returns an empty set immediately so it does not accidentally query the whole workflow store. Otherwise it asks the DBOS client for workflows with those ids whose status is pending, enqueued, or delayed, and returns the matching workflow ids as a set.

**Call relations**: `StrandedTurnReconciler.sweep` calls this after finding candidate running turns. The sweep uses the returned set as a keep-alive list: attempts in the set are skipped, while attempts not in the set may be cancelled.

*Call graph*: called by 1 (sweep).


### `core/src/ufo/runtime/delivery.py`

`domain_logic` · `background scheduled result-delivery sweep`

When one agent delegates work to a child agent, the parent expects to be woken up when the child finishes. Usually that happens as part of the child’s normal run. But some endings happen from the outside, such as cancellation by another process, or a crash after the database was updated but before the wake-up was sent. Without this file, the child could be marked finished forever while the parent keeps waiting with no way to notice.

`DeliverySweep` is a background sweep, like a clerk checking a tray for messages that never got delivered. It looks in durable database state for child turns that are finished but still marked as needing result delivery. It groups those children by the parent conversation, so if several children finish around the same time, the parent can be woken once for the batch instead of repeatedly.

The sweep also uses a cooldown. If a conversation was just woken by a delivered child, it skips that conversation for this pass and leaves its still-pending children for the next tick. This prevents a fast loop where a woken parent immediately starts more children that wake it again.

If the parent agent has been archived, delivery is skipped rather than blocking the whole sweep. The child result stays pending, so restoring the agent later can still receive what it was owed.

#### Function details

##### `DeliverySweep.run`  (lines 54–72)

```
async def run(self) -> None
```

**Purpose**: This is the main pass of the delivery sweep. It finds finished child turns whose results have not reached their parent, skips recently woken conversations, and asks the normal subagent result-delivery path to deliver each remaining child result.

**Data flow**: It starts with no inputs beyond the sweep’s configured invoker factory and subagent registry. It reads outstanding finished children from the workspace database, checks which parent conversations were already woken recently, builds a `SubagentResult` delivery helper for the current workspace, then delivers each eligible child. If a parent agent is archived, it quietly skips that child and leaves it pending for a future pass.

**Call relations**: This function drives the file’s whole flow. It first calls `DeliverySweep._outstanding` to learn what needs delivery. If there is work, it calls `DeliverySweep._woken_since` using the current time minus the cooldown window, so it can avoid waking the same conversation too often. It then hands each child turn to `SubagentResult`, which is the same delivery route used by the normal event path.

*Call graph*: calls 2 internal fn (_outstanding, _woken_since); 4 external calls (__init__, now, timedelta, ws_current).


##### `DeliverySweep.candidate_workspaces`  (lines 74–93)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This function tells the scheduler which workspaces are worth running the sweep for. It looks for workspaces that have at least one finished, undelivered child turn whose parent agent is still active.

**Data flow**: It opens an owner-level database transaction, which can see across workspaces. It queries turn and agent records for child turns marked pending for result delivery, already terminal, and attached to a non-archived parent agent. It returns the distinct workspace IDs where such work exists.

**Call relations**: A scheduling layer can call this before running the sweep in individual workspaces. Instead of making every workspace run an empty check, this function narrows the work to places where the database says delivery is actually pending.

*Call graph*: 2 external calls (select, owner_tx).


##### `DeliverySweep._outstanding`  (lines 95–131)

```
async def _outstanding(self) -> dict[UUID, list[Turn]]
```

**Purpose**: This helper finds the child turns in the current workspace that are finished but whose results have not yet been delivered. It organizes them by the parent conversation that should receive the result.

**Data flow**: It opens a workspace database transaction and queries child turns whose delivery status is pending and whose terminal result exists. It joins each child to its parent turn and parent agent, ignoring archived parent agents. It orders results by parent conversation and child finish time, limits the batch size, converts each database row into a `Turn` record, and returns a dictionary from parent conversation ID to a list of child turns.

**Call relations**: `DeliverySweep.run` calls this at the start of a sweep pass. The grouped result lets `run` treat each parent conversation as a unit, so a fan-out of many child results can be drained together instead of waking the same conversation one child at a time.

*Call graph*: called by 1 (run); 3 external calls (model_validate, select, workspace_tx).


##### `DeliverySweep._woken_since`  (lines 133–160)

```
async def _woken_since(self, cutoff: datetime, conversations: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: This helper checks which of a set of parent conversations were already woken by a child result after a given cutoff time. It is used to enforce the cooldown between wake-ups.

**Data flow**: It receives a cutoff timestamp and a set of conversation IDs. It queries the workspace database for delivered child turns linked to parent turns in those conversations, where the child’s delivery timestamp is newer than the cutoff. It returns those conversation IDs as a frozen set, meaning the caller can safely use it as a read-only skip list.

**Call relations**: `DeliverySweep.run` calls this after finding outstanding work. The answer tells `run` which conversations to leave alone until the next sweep tick, whether the recent wake-up came from this sweep or from the normal event-based delivery path.

*Call graph*: called by 1 (run); 2 external calls (select, workspace_tx).

## 📊 State Registers Touched

- `reg-conversation-records` — The durable conversation state, including conversation identity, title, surface label, sandbox handle, audience, and related metadata.
- `reg-turn-queue-state` — The durable queue of conversation turns, including admission source, run claim, parked state, resume state, and final status.
- `reg-runtime-fleet-claims` — The attendance and claim sheet for running service processes, including heartbeats, work ownership, and surface listener claims.
- `reg-cancellation-cleanup-state` — The shared stop-and-cleanup state that records when active turns, workflows, child work, sandboxes, and streams are being wound down.
- `reg-schedules-automations` — The durable alarm clock for future work, pauses, monitors, source-change triggers, notification inbox items, and extension jobs.
- `reg-delegation-state` — The parent-child work state that tracks subagent turns, their contracts, trace links, pending results, and delivery back to the parent.
- `reg-observability-trace` — The tracing, health, logging, and traceparent state used to connect work across turns, subagents, workers, and cleanup.
- `reg-database-connection-pools` — Process-global database engines, sessions, transaction handles, and connection pools shared by serving, workers, migrations, and cleanup code.
- `reg-durable-workflow-checkpoints` — Saved workflow execution/checkpoint state used to resume, repair, cancel, or finalize long-running workflows after pauses, crashes, or worker handoff.
- `reg-service-worker-lifecycle-state` — Process-local supervisor state for background loops and workers, including async task handles, startup readiness, shutdown signals, and drain status not represented by durable job tables.
