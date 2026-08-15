# Cancellation, teardown, cleanup, and crash recovery  `stage-20`

This stage is the system’s safety and cleanup crew. It is used when a piece of work is cancelled, when a server process stops, or when a crash leaves unfinished work behind. Its job is to make sure the system does not keep wasting resources, does not leave child tasks running after a parent stops, and does not confuse old abandoned work with work that is still alive.

`cancellation.py` provides the careful order for cancelling one “turn,” meaning one unit of user work. First it tells the running workflow to stop. Only after that does it mark the turn as cancelled in the database, so the record matches what actually happened.

`runtime_instance.py` keeps track of which server processes are alive, like a sign-in sheet for workers. Other parts of the system can use this to tell whether a turn is truly active or was left behind by a crashed process. It also helps recover abandoned turns and passes cancellation from parent turns to their child turns, so related work shuts down together.

## Files in this stage

### Runtime cancellation and recovery
Shared cancellation logic and runtime presence tracking coordinate safe turn cancellation, crash recovery, and propagation of cancellation through related work.

### `core/src/ufo/cancellation.py`

`domain_logic` · `cancel handling and reconciliation`

A “turn” is a unit of work that may be running as a durable DBOS workflow, meaning DBOS can remember and recover it across crashes. Cancelling is tricky because a turn can also have child turns, and stopping one workflow does not automatically stop the others. This file deliberately solves only the smallest safe step: cancel exactly one turn. Other parts of the system can repeat this step to cancel a whole tree.

The important rule here is “cancel first, mark cancelled second.” The code first checks the turn row in the database. If the turn does not exist, or if it already finished in any final state, it leaves it alone. If the turn is still active, it asks DBOS to cancel the workflow using the turn id. Only after that request is made does it update the turn row to the final cancelled state.

That order matters. If the process crashes halfway through, the database will not falsely claim the turn is cancelled before DBOS was told to stop it. It is like turning off a machine before putting a “shut down” label on it. The file also records a metric when this function is the one that actually changes the turn into cancelled, so cancellation counts stay accurate.

#### Function details

##### `cancel_one_turn`  (lines 24–71)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> bool
```

**Purpose**: Cancels one turn safely and records that cancellation only if the turn was still unfinished. Callers use it as the single shared cancellation step, whether the request came from a user-facing tool, an evaluation driver, or a background reconciler.

**Data flow**: It receives a DBOS client and a turn id. It reads the turn’s current status and profile from the database; if the turn is missing or already final, it returns False. Otherwise it asks DBOS to cancel the workflow whose id matches the turn id, then updates the database row to status cancelled with a terminal cancellation record and a fresh update time. If that update really changed the row, it emits a cancellation metric and returns True; if another process finished or cancelled the turn first, it returns False.

**Call relations**: This function is the shared cancellation primitive used by higher-level cancel paths. Inside, it opens database transactions with workspace_tx, builds SQL queries with SQLAlchemy, asks DBOSClient.cancel_workflow_async to stop the durable workflow, and then records observability data through emit_metric and turn_profile after a successful transition.

*Call graph*: 6 external calls (cancel_workflow_async, select, update, workspace_tx, emit_metric, turn_profile).


### `core/src/ufo/runtime_instance.py`

`orchestration` · `main loop background maintenance`

This file is the fleet’s background caretaker. Each server process records a small database row saying “I am here,” then refreshes that row every few seconds. That row is a heartbeat, like a regular pulse check. Other processes use it to decide whether work assigned to that process is still being actively handled or has been stranded by a crash.

The file has three main jobs. First, `record_fleet_seat` creates the process’s row before durable work starts. Second, `Heartbeat` keeps that row fresh and removes it during graceful shutdown. Third, `ExecutorRecovery` periodically looks for pending DBOS workflows whose executor has no fresh heartbeat. DBOS is the durable workflow system; here, an executor id identifies the process that was running a workflow. If that process looks dead, recovery asks DBOS to re-dispatch the abandoned work.

The last piece, `CancelReconciler`, fixes cancellation across parent-child turn trees. Cancelling one turn only directly marks that turn. This reconciler periodically finds still-running descendant turns under any cancelled ancestor and cancels them too. This makes cancellation eventually reach the whole branch, even if a process crashed midway. The design is deliberately periodic and fault-tolerant: a failed database or DBOS call is logged, then the next tick tries again.

#### Function details

##### `record_fleet_seat`  (lines 38–53)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: This function creates the database row that says a server process exists and is alive enough to start work. It is run before DBOS launches so this process’s own work is not immediately mistaken for abandoned work.

**Data flow**: It receives an `instance_id`, which is the unique id for this process. It opens an owner-level database transaction, inserts a `runtime_instance` row with no workspace attached, stamps the current time as the heartbeat and creation/update time, then writes a log message. The database now has a fresh liveness marker for this process.

**Call relations**: This is the first step in the liveness story. It uses the shared database transaction helper to write the row and the logging system to record success. Later, heartbeat updates keep this row fresh, and recovery sweeps use the row to decide whether pending work belongs to a live process.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 66–76)

```
async def run(self) -> None
```

**Purpose**: This is the never-ending loop that keeps one process’s heartbeat row fresh. It protects the process from being falsely treated as dead after one temporary database problem.

**Data flow**: It repeatedly calls `Heartbeat.beat` to update the heartbeat time. If the database update fails with a SQLAlchemy error, it logs the failure instead of stopping. It then waits for the configured heartbeat interval and tries again.

**Call relations**: A server process runs this loop in the background after its fleet seat has been recorded. The loop delegates the actual database update to `Heartbeat.beat`; recovery logic elsewhere depends on these updates to know this executor is still alive.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 78–88)

```
async def beat(self) -> None
```

**Purpose**: This function performs one heartbeat update for the current process. It is the single pulse that tells the fleet, “this process is still alive now.”

**Data flow**: It reads the `instance_id` stored on the `Heartbeat` object. It opens a database transaction and updates the matching `runtime_instance` row so `heartbeat_at` and `updated_at` become the database’s current time. It returns nothing, but the database row is now fresh.

**Call relations**: `Heartbeat.run` calls this on every heartbeat tick. The executor recovery sweep later reads these timestamps through `_live_executors` to avoid recovering work that is still being run by a healthy process.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 90–96)

```
async def retire(self) -> None
```

**Purpose**: This removes the process’s heartbeat row during graceful shutdown. It lets peers see immediately that the process has left, instead of waiting for the row to become stale.

**Data flow**: It reads the `instance_id` from the `Heartbeat` object. It opens a database transaction and deletes the matching `runtime_instance` row. The result is that the fleet no longer sees this process as occupying a live seat.

**Call relations**: The server shutdown path calls this through `serve._stop_executor`. It is the clean ending counterpart to `record_fleet_seat` and `Heartbeat.beat`: record the seat at startup, refresh it while alive, delete it when leaving.

*Call graph*: called by 1 (_stop_executor); 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 116–122)

```
async def run(self) -> None
```

**Purpose**: This is the background loop that periodically searches for durable workflows left behind by crashed processes. It keeps pending work from staying stuck forever.

**Data flow**: It sleeps for the configured recovery interval, then calls `ExecutorRecovery.sweep`. If the sweep fails because of a database or DBOS workflow-system error, it logs the failure and continues to the next interval. It returns nothing and is meant to run indefinitely.

**Call relations**: Every server process can run this loop. It hands the real recovery decision to `ExecutorRecovery.sweep`, so any surviving process can help reclaim work abandoned by another process.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 124–132)

```
async def sweep(self) -> None
```

**Purpose**: This function compares pending workflow owners with live process heartbeats and recovers the workflows owned by dead processes. It is the main “unstick abandoned work” step.

**Data flow**: It asks `_pending_executors` for executor ids attached to pending workflows, then asks `_live_executors` for executor ids with fresh heartbeat rows. It subtracts the live set from the pending set. For each remaining stranded executor, it calls DBOS recovery in a worker thread and logs how many workflows were recovered.

**Call relations**: `ExecutorRecovery.run` calls this on each recovery tick. It relies on `_pending_executors` to learn where pending work is assigned and `_live_executors` to learn which processes are still alive, then hands stranded executor ids to DBOS so DBOS can re-dispatch their workflows.

*Call graph*: calls 2 internal fn (_live_executors, _pending_executors); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_executors`  (lines 134–146)

```
async def _pending_executors(self) -> set[str]
```

**Purpose**: This function finds executor ids that currently own pending DBOS workflows. These are candidates for recovery, but only if their executor is no longer alive.

**Data flow**: It asks DBOS, in a separate thread, for up to the configured limit of workflows with status `PENDING`. It does not load workflow inputs or outputs, only status information. It logs if the scan hit the limit, then returns the set of non-empty executor ids found in those pending workflow records.

**Call relations**: `ExecutorRecovery.sweep` calls this before deciding what is stranded. Its result is only half the story: the sweep compares it with `_live_executors` so it does not recover work that a living process is still running.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 148–158)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: This function finds process ids whose heartbeat rows are recent enough to count as alive. It prevents recovery from starting a second copy of work that is already being run.

**Data flow**: It computes a cutoff time by subtracting the stale threshold from the current UTC time. It opens a database transaction, selects `runtime_instance` rows with `heartbeat_at` newer than that cutoff, and returns their ids as strings. The output is the set of executors considered live right now.

**Call relations**: `ExecutorRecovery.sweep` calls this alongside `_pending_executors`. The sweep uses this live set as a safety filter: pending work owned by these executors is left alone, while pending work owned by missing or stale executors can be recovered.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `CancelReconciler.run`  (lines 182–188)

```
async def run(self) -> None
```

**Purpose**: This is the background loop that repeatedly pushes cancellation from cancelled turns down to their still-running descendants. It makes cancellation eventually consistent across a whole turn tree.

**Data flow**: It sleeps for the configured cancellation reconciliation interval, then calls `CancelReconciler.sweep`. If the database or DBOS reports an error, it logs the failure and continues. It produces no direct result and is intended to keep running.

**Call relations**: Every server process can run this loop. It delegates each actual scan-and-cancel pass to `CancelReconciler.sweep`, so a surviving process can finish cancellation cleanup even if the process that originally cancelled a parent turn is gone.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 190–197)

```
async def sweep(self) -> None
```

**Purpose**: This function finds live turns that sit underneath a cancelled ancestor and cancels them one by one. It is the practical step that turns “this parent was cancelled” into “the whole descendant branch stops too.”

**Data flow**: It opens a database transaction and runs `_orphans_query` to find non-terminal descendant turns with cancelled ancestors, along with their workspace ids. For each result, it enters that workspace context, calls `cancel_one_turn` using the DBOS client, and logs the turn id if a cancellation actually happened. The database and DBOS workflow state may change as each turn is cancelled.

**Call relations**: `CancelReconciler.run` calls this on every reconciliation tick. It asks `_orphans_query` which turns need attention, then hands each selected turn to the shared `cancel_one_turn` cancellation primitive so cancellation is performed the same way as direct user- or deadline-driven cancellation.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (cancel_one_turn, owner_tx, log, ws).


##### `CancelReconciler._orphans_query`  (lines 199–234)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: This builds the database query that identifies still-running turns with any cancelled ancestor above them. It catches not only direct children of a cancelled turn, but also deeper grandchildren and beyond.

**Data flow**: It uses the `turn` table to build a recursive common table expression, which is a SQL query that repeatedly walks from a turn to its parent. It starts from non-terminal turns, climbs their `parent_turn_id` chain, stops once it reaches a cancelled ancestor, and returns each matching turn id with its workspace id. The output is a SQL query object, not the rows themselves.

**Call relations**: `CancelReconciler.sweep` calls this when it needs the latest list of descendants to cancel. The query supplies the sweep with the targets; the sweep then performs the actual cancellation through `cancel_one_turn`.

*Call graph*: called by 1 (sweep); 1 external calls (select).

## 📊 State Registers Touched

- `reg-turn-queue` — The durable waiting line and status record for each unit of agent work, from queued to running to finished or failed.
- `reg-live-hub` — The live stream state that lets browsers, terminals, and operators watch progress and reconnect without losing updates.
- `reg-runtime-fleet` — The sign-in sheet of running server and worker instances used to detect active work, crashes, and abandoned turns.
- `reg-background-job-schedule` — The durable list of background jobs and dispatch rules used to retry and run work outside user requests.
- `reg-sandbox-workspace` — The per-conversation isolated workbench, including its handle, files, execution backend, and recorded file changes.
- `reg-browser-session` — The leased browser instance for a turn, including tabs, page state, downloads, dialogs, and provider connection details.
- `reg-subagent-state` — The helper-agent catalog and child-conversation handoff state used when one agent delegates work to another.
- `reg-observability-context` — Trace IDs, metrics, logs, and sanitized operational events used to understand work across services and turns.
- `reg-inflight-cancellation-handles` — Live cancellation signals, workflow handles, and parent-child cancellation propagation state for active turns and delegated work before final durable status is written.
- `reg-durable-workflow-state` — DBOS/workflow-runtime execution metadata for durable job and turn workflows, including workflow IDs, retries, scheduled starts, cancellation, and resume bookkeeping.
