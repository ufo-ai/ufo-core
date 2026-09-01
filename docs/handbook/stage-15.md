# Per-Run Cleanup, Recovery, and Teardown  `stage-15`

This stage is the system’s safety and cleanup crew. It runs after a piece of work finishes, is stopped, fails, or when a process disappears unexpectedly. Its job is to make sure no rented resources, running tasks, or live updates are left hanging. It also helps recover work safely so the same job is not picked up twice.

runtime_instance.py keeps each running server process visible to the rest of the fleet, like a sign saying “I am alive and responsible for this work.” It also runs background sweeps that find abandoned or cancelled work and move it toward a safe final state.

stop.py handles a user or system request to stop an active conversation turn. A “turn” means one unit of agent work. It checks that stopping is allowed, triggers cancellation, and notifies live listeners that the turn is over.

cancellation.py contains the shared cancellation steps. It stops the active workflow first, then records in the database that the turn was cancelled, so the system’s saved state matches what really happened.

## Files in this stage

### Runtime recovery sweeps
Keeps active serve processes visible and performs background cleanup so abandoned work converges safely.

### `core/src/ufo/runtime/runtime_instance.py`

`orchestration` · `cross-cutting background runtime maintenance`

This file is the fleet’s “pulse and cleanup crew.” Each serve process writes a small database row saying “I am here,” then refreshes that row every few seconds. That row is important because durable workflows are tied to the process, or executor, that started them. If a process stops heartbeating, other processes can safely assume its pending work is abandoned and ask DBOS, the durable workflow system, to recover it.

The file also runs two turn-cleanup sweeps. A turn is a unit of conversation or work. The cancel reconciler spreads cancellation downward: if a parent turn was cancelled, any still-live dependent child turns are cancelled too. This is done on a timer instead of immediately, so crashes or missed moments are harmless; the next sweep catches up.

The stranded-turn reconciler looks for turns marked as running even though the workflow attempt that claimed them is no longer moving. Those rows would otherwise look alive forever and block later messages from being handled correctly. After a grace period, it checks DBOS to see whether the workflow is still pending, queued, or delayed. If not, it cancels the turn.

Together, these loops are like night watchmen: they do not perform the main work, but they keep the system honest when processes crash, database writes fail briefly, or cancellation needs to ripple through a tree of work.

#### Function details

##### `record_fleet_seat`  (lines 42–57)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: Creates the database row that represents this serve process as a live member of the shared fleet. This is done before DBOS starts so the process is not mistaken for dead while it begins taking durable work.

**Data flow**: It receives this process's instance ID. It opens an owner-level database transaction, inserts a runtime_instance row with no workspace attached, stamps creation and heartbeat times, then writes a log message saying the fleet seat was recorded.

**Call relations**: This is the first half of the liveness story. After it creates the row, the Heartbeat loop keeps that row fresh, and the executor recovery sweep later reads these rows to decide which executors are still alive.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 70–80)

```
async def run(self) -> None
```

**Purpose**: Runs forever, refreshing this process's liveness row on a short interval. If one database update fails, it logs the problem and keeps going so a temporary database hiccup does not stop the heartbeat permanently.

**Data flow**: It starts with the Heartbeat object's instance ID. On each loop, it asks Heartbeat.beat to update the database timestamp, logs database errors if they happen, then sleeps before trying again.

**Call relations**: This is the ongoing driver for Heartbeat.beat. The recovery sweep depends on these regular updates: a fresh heartbeat means “do not recover this executor's work,” while an old or missing heartbeat means the process may be gone.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 82–92)

```
async def beat(self) -> None
```

**Purpose**: Writes one fresh heartbeat timestamp for this process. It is the actual database update behind the heartbeat loop.

**Data flow**: It reads the Heartbeat object's instance ID, opens a database transaction, finds the matching runtime_instance row, and updates its heartbeat and updated timestamps to the current database time. It returns nothing; the database row is the output.

**Call relations**: Heartbeat.run calls this repeatedly. ExecutorRecovery._live_executors later reads the timestamps written here to decide which executors must be protected from recovery.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 94–100)

```
async def retire(self) -> None
```

**Purpose**: Removes this process's liveness row during graceful shutdown. This lets the rest of the fleet see immediately that the seat is gone instead of waiting for the heartbeat to become stale.

**Data flow**: It reads the Heartbeat object's instance ID, opens a database transaction, deletes that runtime_instance row, and returns nothing. The visible change is that this process no longer appears as a live executor.

**Call relations**: The serve shutdown path calls this when stopping the executor. It complements Heartbeat.run: the run loop keeps the row alive while the process runs, and retire removes it when the process exits cleanly.

*Call graph*: called by 1 (_stop_executor); 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 120–126)

```
async def run(self) -> None
```

**Purpose**: Runs the executor recovery sweep on a timer. Its job is to keep durable workflows from staying pending forever when the process that owned them has died.

**Data flow**: It uses the configured interval, sleeps between checks, then calls ExecutorRecovery.sweep. If the database or DBOS reports a recoverable failure, it logs the error and continues looping.

**Call relations**: This is the scheduler for ExecutorRecovery.sweep. Every serve process can run it, so if one process crashes, any surviving peer can eventually recover that abandoned work.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 128–136)

```
async def sweep(self) -> None
```

**Purpose**: Finds executors that have pending DBOS workflows but no fresh heartbeat, then asks DBOS to recover those workflows. This prevents work from being stranded under a dead process ID.

**Data flow**: It gathers two sets: executor IDs with pending workflows and executor IDs with live heartbeat rows. It subtracts the live set from the pending set. For each remaining executor, it runs DBOS recovery in a worker thread and logs how many workflows were recovered.

**Call relations**: ExecutorRecovery.run calls this each interval. It relies on ExecutorRecovery._pending_executors to find possible stranded work and ExecutorRecovery._live_executors to avoid touching work that belongs to a still-running process.

*Call graph*: calls 2 internal fn (_live_executors, _pending_executors); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_executors`  (lines 138–150)

```
async def _pending_executors(self) -> set[str]
```

**Purpose**: Asks DBOS which executor IDs currently have pending workflows. These are possible recovery candidates, but only if the executor is no longer live.

**Data flow**: It queries DBOS for up to the configured number of pending workflows without loading their full inputs or outputs. It logs if the scan hit the limit, then returns the set of executor IDs found on those workflow records.

**Call relations**: ExecutorRecovery.sweep calls this before comparing against live heartbeat rows. Its output is only a first pass; ExecutorRecovery._live_executors filters out executors that are still safely running.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 152–162)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: Finds executor IDs whose heartbeat is still recent enough to count as alive. This protects active processes from having their workflows recovered a second time.

**Data flow**: It calculates a cutoff time from the current time minus the stale-after window. It selects runtime_instance rows with heartbeat timestamps newer than that cutoff and returns their IDs as strings.

**Call relations**: ExecutorRecovery.sweep calls this alongside ExecutorRecovery._pending_executors. The difference between those two sets is the list of executors considered dead and safe to recover.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `CancelReconciler.run`  (lines 186–192)

```
async def run(self) -> None
```

**Purpose**: Runs the cancellation cleanup sweep on a timer. It makes sure cancellation spreads from a cancelled turn to dependent descendant turns even if the original canceller did not do that work directly.

**Data flow**: It sleeps for the configured interval, calls CancelReconciler.sweep, logs database or DBOS failures, and then repeats. It returns nothing because it is meant to run as a background loop.

**Call relations**: This is the scheduler for CancelReconciler.sweep. Every serve process may run it, so cancellation cleanup can continue even if the process that first cancelled a turn crashes.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 194–201)

```
async def sweep(self) -> None
```

**Purpose**: Finds live turns that sit underneath a cancelled ancestor and cancels them one by one. This turns a local cancellation into a consistent cancellation through the dependent turn tree.

**Data flow**: It builds and runs the orphan-turn query, receiving turn IDs and workspace IDs. For each result, it enters that workspace context, calls cancel_one_turn to cancel the turn safely, and logs when a cancellation actually happened.

**Call relations**: CancelReconciler.run calls this each interval. It uses CancelReconciler._orphans_query to decide which turns need attention and hands each selected turn to the shared cancellation primitive, cancel_one_turn, so the actual terminal update follows the normal cancellation path.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (owner_tx, log, cancel_one_turn, ws).


##### `CancelReconciler._orphans_query`  (lines 203–243)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: Builds the database query that identifies non-terminal turns with a cancelled dependent ancestor. In plain terms, it finds live child or grandchild work that should no longer be running because something above it was cancelled.

**Data flow**: It starts from every non-terminal turn, then recursively walks upward through dependent parent links. If the walk reaches a cancelled ancestor, the original turn is selected with its workspace ID. The result is a SQL query object, not the rows themselves.

**Call relations**: CancelReconciler.sweep calls this and executes the query. During query construction it calls CancelReconciler._dependent_parent so the upward walk follows only relationships where cancellation should propagate.

*Call graph*: calls 1 internal fn (_dependent_parent); called by 1 (sweep); 1 external calls (select).


##### `CancelReconciler._dependent_parent`  (lines 245–255)

```
def _dependent_parent(self, turn: sa.Table | sa.FromClause) -> sa.ColumnElement
```

**Purpose**: Decides which parent link counts as a cancellation dependency. It prevents cancellation from crossing into independent spawned agents while still allowing prepared intents to remain attached to their parent.

**Data flow**: It receives a turn table or table-like alias. It produces a SQL expression: use parent_turn_id when the turn has a subagent profile or came from intent admission; otherwise treat the parent as null so the recursive search stops.

**Call relations**: CancelReconciler._orphans_query uses this expression while building its recursive parent walk. This small rule is what keeps the cancel reconciler from cancelling independent agent work by accident.

*Call graph*: called by 1 (_orphans_query); 3 external calls (case, null, or_).


##### `StrandedTurnReconciler.run`  (lines 289–295)

```
async def run(self) -> None
```

**Purpose**: Runs the stranded-running-turn cleanup sweep on a timer. It looks for turns that say they are running even though their owning workflow attempt is no longer able to advance them.

**Data flow**: It sleeps for the configured interval, calls StrandedTurnReconciler.sweep, logs database or DBOS failures, and repeats indefinitely.

**Call relations**: This is the scheduler for StrandedTurnReconciler.sweep. It is separate from executor recovery because it fixes a different failure mode: a turn row that remains claimed after its workflow has ended or disappeared.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `StrandedTurnReconciler.sweep`  (lines 297–313)

```
async def sweep(self) -> None
```

**Purpose**: Cancels running turns whose claimed workflow attempt is no longer pending, queued, or delayed in DBOS. This frees conversations from being stuck behind work that will never resume.

**Data flow**: It queries old enough running turns that have a recorded running_attempt. It logs if the scan reaches its limit. It asks DBOS which of those attempts are still advancing, skips those, and cancels the rest inside their workspace context. Successful cancellations are logged.

**Call relations**: StrandedTurnReconciler.run calls this each interval. It uses StrandedTurnReconciler._claimed_query to find candidates, StrandedTurnReconciler._advancing_attempts to check DBOS, and cancel_one_turn to safely terminalize turns that are truly stranded.

*Call graph*: calls 2 internal fn (_advancing_attempts, _claimed_query); called by 1 (run); 4 external calls (owner_tx, log, cancel_one_turn, ws).


##### `StrandedTurnReconciler._claimed_query`  (lines 315–332)

```
def _claimed_query(self) -> sa.Select
```

**Purpose**: Builds the database query for running turns that have held a workflow claim longer than the grace window. The grace window avoids mistaking a newly claimed turn for a stranded one.

**Data flow**: It computes a cutoff time from the current time minus the grace period. It returns a SQL query selecting running turns with a non-empty running_attempt and an old updated_at timestamp, ordered oldest first and capped at the scan limit.

**Call relations**: StrandedTurnReconciler.sweep calls this and executes it to get candidate rows. The candidates are not cancelled immediately; they are first checked against DBOS by StrandedTurnReconciler._advancing_attempts.

*Call graph*: called by 1 (sweep); 3 external calls (now, timedelta, select).


##### `StrandedTurnReconciler._advancing_attempts`  (lines 334–345)

```
async def _advancing_attempts(self, attempts: list[str]) -> set[str]
```

**Purpose**: Checks which workflow attempts from a candidate list are still carried by DBOS in an advancing state. An advancing state means the turn should not be cancelled by the stranded-turn sweep.

**Data flow**: It receives a list of workflow attempt IDs. If the list is empty, it returns an empty set to avoid accidentally querying the whole workflow store. Otherwise it asks DBOS for matching workflows in pending, enqueued, or delayed states and returns the workflow IDs that were found.

**Call relations**: StrandedTurnReconciler.sweep calls this after reading candidate running turns. Its result is the keep-alive set: any candidate attempt in this set is skipped, while missing attempts are treated as stranded and passed to cancel_one_turn.

*Call graph*: called by 1 (sweep).


### Turn stop and cancellation
Handles authorized stop requests for running conversation turns and applies the shared safe cancellation path.

### `core/src/ufo/runtime/surfaces/stop.py`

`orchestration` · `request handling`

A “turn” is one active unit of work in a conversation. This file covers the case where a member presses stop while that turn is still running. Without this workflow, a stop button could cancel the wrong turn, leave waiting screens unaware that the turn ended, or fail to move the conversation forward when the member has already sent another message.

The main piece is the `MemberStop` class. It first checks the database to confirm that the turn being stopped really belongs to the requested conversation in the given workspace. This is like checking the label on a package before returning it: the system refuses to act if the package is not the one the member is allowed to touch.

If the turn is valid, it calls the shared cancellation code, which records the cancellation in the durable system state. If the turn had already finished, it returns a result saying nothing was newly ended. If cancellation did happen, the file asks admission logic whether a waiting member message should start a new turn. If so, it publishes a hub message saying that pending arrival was absorbed into the new turn. Finally, it publishes the cancelled terminal message for the stopped turn, so any live listeners wake up immediately instead of waiting for their next check.

#### Function details

##### `MemberStop.stop`  (lines 38–61)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops one running turn for a member, but only if that turn belongs to the conversation the member is acting on. It records the cancellation, optionally starts the next waiting turn, and notifies live listeners so screens can update right away.

**Data flow**: It receives a workspace ID, conversation ID, and turn ID. It reads the database inside `workspace_tx` to find which conversation owns that turn in that workspace. If the owner does not match, it raises an error and changes nothing. If the turn is valid, it calls `cancel_one_turn`; if there is nothing left to cancel, it returns `Stopped` with `ended` set to false. If cancellation succeeds, it asks admission to redispatch any waiting arrival into a new turn. When a new turn is founded, it publishes an `Absorbed` hub event for that new turn. It then publishes a `Terminal` hub event for the cancelled turn and returns `Stopped` with `ended` set to true and, when applicable, the new turn ID.

**Call relations**: This function is the stop workflow from end to end. It uses `sqlalchemy.select` and `workspace_tx` to verify ownership before doing anything dangerous. It hands the actual durable cancellation to `cancel_one_turn`, then uses the admission component to start a follow-up turn if one is waiting. It finishes by publishing hub events with `Absorbed` and `Terminal`, and wraps the outcome in `Stopped` for the surface layer that called it.

*Call graph*: 6 external calls (__init__, __init__, __init__, select, workspace_tx, cancel_one_turn).


### `core/src/ufo/runtime/turns/cancellation.py`

`domain_logic` · `cancel handling and cancel reconciliation`

A “turn” can have work running in DBOS, the workflow system used here to run durable background jobs. Cancelling is tricky because the database row and the running workflow must not disagree. If the database said “cancelled” before the workflow was really told to stop, the system could believe work had ended while it was still running.

This file solves that by giving all cancel paths one careful primitive: cancel exactly one turn, in the right order. It first reads the turn row. If the turn is already finished, it leaves it alone. If it is still active, it asks DBOS to cancel the workflow attempt that currently owns the turn. Only after that does it lock and reread the row, check that the same attempt still owns it, and write the final cancelled state.

The loop matters because ownership can change while cancellation is happening, like trying to stop a taxi just as another driver takes the job. If the owner changed, the code goes back and cancels the new owner instead. After the cancellation is committed, it records a metric and asks the dispatcher to start the next waiting turn in the same conversation.

#### Function details

##### `cancel_one_turn`  (lines 24–101)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Cancels one turn safely and durably. It is used when the system needs to stop a turn without accidentally marking it cancelled before its running workflow has actually been told to stop.

**Data flow**: It receives a DBOS client and a turn ID. It reads the turn from the database inside a workspace transaction; if the row is missing or already finished, it returns nothing. Otherwise it asks DBOS to cancel the workflow named by the turn’s current running attempt, or by the turn ID if no attempt is recorded. It then locks the database row, checks that the turn is still unfinished and still owned by the same attempt, builds a cancelled TerminalFrame containing any objects the turn had already created, and writes that terminal state back to the row. After a successful write, it emits a metric, dispatches the next turn for the conversation, and returns the committed TerminalFrame.

**Call relations**: This is the shared cancellation primitive used by higher-level cancel paths such as the evaluation driver, the cancel-spawn tool, and the reconciler. Inside its flow it opens database transactions with workspace_tx, uses SQLAlchemy select and update calls to read and write the turn row, calls DBOSClient.cancel_workflow_async before committing cancellation, creates ObjectRef and TerminalFrame objects to describe the final cancelled result, reports the outcome through emit_metric and turn_profile, then hands control to dispatch_next_turn so the conversation can continue with whatever turn should run next.

*Call graph*: 9 external calls (__init__, model_validate, cancel_workflow_async, select, update, workspace_tx, emit_metric, turn_profile, dispatch_next_turn).

## 📊 State Registers Touched

- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-live-turn-stream` — The live event feed that lets clients and other processes watch a running turn and learn how it ended.
- `reg-transcript-history` — The saved conversation transcript, summaries, compactions, and access records that preserve what happened in a chat.
- `reg-sandbox-runtime` — The durable sandbox and browser workspace handles where agent commands, files, web browsing, and hosted previews run safely.
- `reg-background-jobs` — The shared job schedule, due-work candidates, claims, retries, and worker state for background and autonomous work.
- `reg-runtime-fleet-heartbeats` — The fleet-wide record of which runtime processes are alive and what work they may be responsible for.
- `reg-delegation-workflows` — The saved state for subagents, parent-child turns, objectives, workflow checkpoints, pending deliveries, and recovery.
- `reg-observability-trace` — The logs, metrics, traces, health signals, and trace links used to understand what the system is doing.
- `reg-database-connection-pool` — The shared SQLAlchemy engine/session and connection-pool state used by requests, turns, workers, migrations, and persistence helpers to access the database safely.
- `reg-surface-listener-leases` — The stored claims/leases that coordinate which runtime instance is allowed to listen on a shared surface installation or address, avoiding duplicate external listeners.
- `reg-active-turn-cancellation-handles` — The in-process registry of currently running turn/workflow tasks and cancellation handles used to stop active work before marking it cancelled durably.
- `reg-redis-service-pool` — The shared Redis client/connection and stream/cache coordination state used by live turn streaming, workers, and Redis-backed extension stores.
- `reg-turn-runtime-snapshot` — The per-turn frozen runtime configuration and generated references used to run, recover, bill, and debug a turn consistently after settings change.
