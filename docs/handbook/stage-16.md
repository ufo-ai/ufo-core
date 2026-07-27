# Scheduled jobs, billing automation, and self-improvement loops  `stage-16`

This stage is the system’s night shift: background work that happens outside a user’s live request. The core job machinery finds which workspaces need attention, schedules future work, and lets workers safely claim jobs so the same task is not run twice. The scheduled-tasks extension adds clock rules, including cron schedules, plus tools to create, edit, run, or delete tasks. Its runner wakes up on ticks, starts due tasks, skips expired ones, and reschedules repeating ones. It can also pause a workflow until a person replies or a timer ends.

Other jobs keep the business side moving. The Slack Connect job creates customer channels and invitations safely, even after retries. The Metronome extension syncs usage, billing, Stripe setup, and seat counts, and gives owners chat tools for billing and seats.

The self-improvement loop works like a cautious training lab. It mines past failures into test examples, asks a model for prompt changes, replays old conversations without causing real-world side effects, grades the results, and uses statistical checks before opening a governed improvement proposal.

## Files in this stage

### Job scheduling substrate
Core scheduling primitives discover workspace-scoped work, register recurring jobs, and persist claimable scheduled runs.

### `core/src/ufo/candidates.py`

`domain_logic` · `job candidate discovery during dispatch ticks`

In this system, jobs are not allowed to run in a vague, global way. Before a job handler runs, the dispatcher must know which workspace it should run inside. This file provides the small but important bridge that answers: “Which workspaces have work due right now?”

Normally, workspace data is protected by row-level security, meaning database reads are scoped so one workspace cannot see another workspace’s data. There is one special exception: a core-only database path called owner_tx, which can read across workspaces. This file keeps that exception narrow and safe. It only uses that powerful read to fetch workspace IDs, not tenant row data.

The main helper, owner_candidates, accepts a function that builds a database query. That query must return one column: the workspace_id values that currently have pending work. The query is built fresh each time candidates are checked, which matters because “due now” depends on the current time. If the query were built once at startup, its idea of “now” could go stale.

The returned candidate function opens the special owner-level database connection, runs the query, pulls the first value from each row, and returns those UUIDs. Later, the dispatcher re-enters each workspace normally before doing real work. Like reading room numbers from a master schedule, this file tells the system where to go, but it does not let anyone rummage through the rooms.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: This function turns a query-builder into a safe workspace-candidate reader. Extensions use it to say which workspaces may have pending work, without getting direct access to the powerful cross-workspace database connection.

**Data flow**: It receives due, a no-argument function that builds a database select query returning workspace IDs. It wraps that builder in an async candidates function. The result is a callable that, when later run, will execute the freshly built query and return the workspace IDs as a tuple.

**Call relations**: This is the public seam between extension code and core scheduling. An extension supplies the query-building part, and owner_candidates returns the function the dispatcher can call when it is time to find work. The actual database read is delegated to the nested owner_candidates.candidates function.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner async function performs the actual candidate lookup. It uses the core-only owner_tx database path to read workspace IDs across workspaces, while deliberately returning only IDs and no workspace row data.

**Data flow**: When called, it opens an owner-level database transaction with owner_tx. Inside that connection, it calls due() to build the current query, runs it, collects all returned rows, and takes the first column from each row. It closes the transaction and returns those values as a tuple of workspace UUIDs.

**Call relations**: This function is the callable produced by owner_candidates. During a dispatch tick, the scheduler can call it to learn which workspaces to enter. Its only recorded external call is to ufo.db.owner_tx, which provides the special cross-workspace database access used for this narrow lookup.

*Call graph*: 1 external calls (owner_tx).


### `core/src/ufo/jobs.py`

`orchestration` · `startup registration and scheduled background work`

This file is the background job control room. At startup, the system discovers jobs from the core product and installed extensions, then registers timed jobs and one-time jobs with DBOS, the durable workflow system that keeps work recorded in the database. Without this file, source syncing, queued turn recovery, page-change hooks, and sandbox cleanup would not reliably happen in the background.

The file uses a two-step pattern. First, a lightweight "tick" fires for a job. The tick asks, "Which workspaces actually have work to do?" Then it queues one real job run per matching workspace. This matters because one slow workspace should not block another, and repeated timer fires should not stack endless duplicate runs for the same workspace.

Several core jobs live here. The turn dispatcher finds conversation turns that are queued or parked and offers them to the turn worker queue in the correct order. The sandbox reaper destroys idle disposable containers so unused compute does not linger. The page-change runner finds extension hooks that want to react to changed pages and feeds each hook only the pages it has not yet seen, using a cursor like a bookmark.

The key safety idea is workspace binding. A handler is never run "globally" by accident. Each actual job run opens the correct workspace context first, then builds an extension context with the right services and credentials. This keeps tenant data separated and makes core and extension jobs follow the same path.

#### Function details

##### `TurnDispatcher.run`  (lines 126–141)

```
async def run(self) -> None
```

**Purpose**: Finds turns that are ready to be sent to the conversation worker queue and sends them, while respecting limits for parked turns. This is the recovery sweep that keeps queued work from getting stranded.

**Data flow**: It starts by reading the dispatchable turn rows for the current workspace. For parked turns, it checks whether the relevant member has a seat and whether spending rules allow the work; turns that fail those checks stay parked. For every allowed turn, it marks and enqueues the turn so a worker can process it.

**Call relations**: This is the main body of the turn-dispatch job. It relies on _dispatchable_turns to choose candidates, calls gate and spending checks for parked rows, and hands each approved turn to _enqueue so DBOS can run the real turn workflow.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 4 external calls (__init__, __init__, workspace_tx, gate_member).


##### `TurnDispatcher.candidate_workspaces`  (lines 143–151)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces contain turns that might need dispatching. This prevents the scheduled sweep from opening every workspace when most have nothing to do.

**Data flow**: It computes a grace cutoff time, then reads the owner-level database view for distinct workspace IDs with eligible queued or parked turns. It returns those workspace IDs as the list the job runner should bind and run.

**Call relations**: The job system calls this before running the turn-dispatch job. It uses _eligible to express the same rules that the workspace-local scan later uses, so the broad fleet scan and the actual per-workspace work agree.

*Call graph*: calls 1 internal fn (_eligible); 4 external calls (now, timedelta, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 153–192)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: Selects a small ordered batch of turn rows in the current workspace that are safe to offer to workers. It protects conversation order so later turns do not jump ahead of earlier ones.

**Data flow**: It computes a stale-offer cutoff, queries the workspace database for eligible turns, orders queued turns before parked ones and older turns before newer ones, then converts each row into a small _DispatchTurn record. The result is a tuple of turn descriptions ready for the dispatcher loop.

**Call relations**: TurnDispatcher.run calls this at the start of its sweep. The method uses _eligible to share the same eligibility rules with candidate_workspaces, then returns records that run either gates or direct enqueue.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 6 external calls (__init__, now, timedelta, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 194–224)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: Atomically claims a turn for dispatch and offers it to the DBOS turn queue. This makes duplicate sweepers safe because only one process can successfully stamp the row first.

**Data flow**: It receives one _DispatchTurn, checks that it is still in the same state, still stale enough, and still first in its conversation for that state. If the database update succeeds, it builds queue options with the correct workflow ID and conversation partition key, then enqueues the turn workflow. If another process got there first, it returns without doing anything.

**Call relations**: TurnDispatcher.run calls this for each allowed candidate. It uses _first_in_status and _stale to guard the database update, then hands the work to the DBOS client so the separate turn-processing workflow can claim it.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 1 (run); 5 external calls (now, timedelta, update, workspace_tx, uuid4).


##### `TurnDispatcher._eligible`  (lines 226–234)

```
def _eligible(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for turns that are worth dispatching. It captures the rule that only queued or parked turns with no fresh dispatch stamp, and no earlier same-state turn in the same conversation, are candidates.

**Data flow**: It receives a cutoff time and combines several database predicates: status must be queued or parked, the dispatch stamp must be missing or old, and the turn must be first among turns of that status in its conversation. It returns this combined condition for a SQL query.

**Call relations**: candidate_workspaces uses this for the fleet-wide workspace scan, and _dispatchable_turns uses it for the workspace-local batch. That shared helper keeps both scans aligned.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 2 external calls (and_, or_).


##### `TurnDispatcher._stale`  (lines 236–240)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for a turn whose previous dispatch offer is absent or old enough to retry. This lets the system recover if a process stamped a row but crashed before enqueueing.

**Data flow**: It receives a cutoff time and returns a condition that is true when dispatch_enqueued_at is empty or earlier than the cutoff. Nothing is changed; it only creates a query fragment.

**Call relations**: _eligible uses it to find candidates, and _enqueue uses it again during the atomic update. Rechecking at update time is what prevents stale scan results from causing unsafe duplicate offers.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 242–251)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a turn is the earliest turn of its status within a conversation. This preserves turn order.

**Data flow**: It receives a status, creates an alias for earlier turn rows, and returns a condition that no earlier row with the same workspace, conversation, status, and lower sequence number exists. It produces a SQL expression rather than data by itself.

**Call relations**: _eligible uses this to identify rows worth scanning, and _enqueue uses it again while stamping a row. That second check means a later turn cannot be offered if an earlier one appeared or remained ahead of it.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `SandboxReaper.run`  (lines 281–291)

```
async def run(self) -> None
```

**Purpose**: Destroys idle sandbox containers for conversations in the current workspace. This saves resources while relying on durable workspace data so the next turn can recreate the sandbox if needed.

**Data flow**: It reads conversations with idle sandbox handles, ignores handles that belong to a different backend, rechecks that the conversation has not become active, asks the carrier to destroy the container, and then clears the stored handle from the conversation row. The outside container and the database row both move from "possibly reusable" to "gone; create fresh next time."

**Call relations**: This is the main body of the sandbox reaping job. It depends on _idle_sandboxes for candidates, _now_active for the last safety check, the carrier for the actual destroy call, and _clear to remove the stale handle afterward.

*Call graph*: calls 3 internal fn (_clear, _idle_sandboxes, _now_active); 2 external calls (__init__, sandbox_handle_id).


##### `SandboxReaper.candidate_workspaces`  (lines 293–312)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds workspaces that have at least one sandbox idle long enough to reap. This keeps the reaper from opening workspaces with no live sandbox handles.

**Data flow**: It computes an idle cutoff, reads the owner-level database for distinct workspaces whose conversations have a sandbox handle and no recent or in-flight turns, and returns those workspace IDs. It does not destroy anything itself.

**Call relations**: The job runner calls this before binding each workspace for the sandbox reap job. It shares the _active_since condition with the workspace-local scan so both stages use the same idea of "still busy."

*Call graph*: calls 1 internal fn (_active_since); 4 external calls (now, timedelta, select, owner_tx).


##### `SandboxReaper._clear`  (lines 314–320)

```
async def _clear(self, conversation_id: UUID) -> None
```

**Purpose**: Removes a sandbox handle from a conversation row after the container has been destroyed. This prevents later turns from trying to attach to a dead or released container.

**Data flow**: It receives a conversation ID, opens the current workspace transaction, and updates that conversation so sandbox_handle becomes null. It returns no value; the database row is changed.

**Call relations**: SandboxReaper.run calls this after the carrier destroy call succeeds. It is the database cleanup half of reaping.

*Call graph*: called by 1 (run); 2 external calls (update, workspace_tx).


##### `SandboxReaper._now_active`  (lines 322–340)

```
async def _now_active(self, conversation_id: UUID) -> bool
```

**Purpose**: Checks right before destruction whether a conversation has become active again. This reduces the chance of deleting a sandbox that a new turn has just started using.

**Data flow**: It receives a conversation ID and queries the current workspace for any non-terminal turn in that conversation. It returns true if one is found and false otherwise.

**Call relations**: SandboxReaper.run calls this after reading an idle snapshot but before destroying the container. It acts as a final guard between the database scan and the out-of-database carrier operation.

*Call graph*: called by 1 (run); 2 external calls (select, workspace_tx).


##### `SandboxReaper._idle_sandboxes`  (lines 342–361)

```
async def _idle_sandboxes(self) -> tuple[tuple[UUID, str], ...]
```

**Purpose**: Lists sandbox handles in the current workspace that look idle. These are the concrete containers the reaper will consider destroying.

**Data flow**: It computes the idle cutoff, queries conversations with a stored sandbox handle and no active or recently updated turns, and returns pairs of conversation ID and stored handle string. It only reads the database.

**Call relations**: SandboxReaper.run calls this to begin a workspace-local sweep. It uses _active_since, the same busy-test helper used by candidate_workspaces.

*Call graph*: calls 1 internal fn (_active_since); called by 1 (run); 4 external calls (now, timedelta, select, workspace_tx).


##### `SandboxReaper._active_since`  (lines 363–375)

```
def _active_since(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a conversation has recent or in-flight turn activity. The reaper uses the opposite of this condition to find idle conversations.

**Data flow**: It receives a cutoff time and returns a SQL exists condition for turns in the conversation that are either not finished or updated after the cutoff. It does not query by itself; it supplies a reusable query fragment.

**Call relations**: candidate_workspaces and _idle_sandboxes both use this helper. That makes the broad workspace scan and the bound workspace scan agree on which sandboxes are idle.

*Call graph*: called by 2 (_idle_sandboxes, candidate_workspaces); 3 external calls (exists, or_, select).


##### `_page_beyond_cursor`  (lines 378–390)

```
def _page_beyond_cursor(updated_at: datetime, page_id: UUID, cursor: object) -> bool
```

**Purpose**: Decides whether a page change is newer than a stored page-change cursor. The cursor works like a bookmark saying, "this consumer has processed up through this exact page and time."

**Data flow**: It receives a page timestamp, page ID, and stored cursor object. If the cursor is not a string, it treats the page as pending. Otherwise it parses the cursor timestamp and boundary page ID, normalizes timestamps to UTC when needed, and returns true if the page comes after that cursor in the feed order.

**Call relations**: PageChangeRunner.workspaces_with_changes calls this while deciding which workspaces still have pending page changes for a specific consumer.

*Call graph*: called by 1 (workspaces_with_changes); 3 external calls (fromisoformat, replace, UUID).


##### `PageChangeRunner.consumers`  (lines 448–472)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: Discovers all extension hooks that listen for page changes and gives each one a unique consumer record. This lets each hook keep its own cursor and fail independently.

**Data flow**: It reads the active manifests, collects declared credential names, filters hooks whose event is page_change, checks that no two hooks in the same extension share the same handler name, and returns PageChangeConsumer records. If two hooks would collide, it raises an error instead of silently sharing state.

**Call relations**: core_jobs calls this when creating built-in page-change jobs. Each returned consumer becomes its own scheduled JobSpec.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 474–526)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: Finds the workspaces where a particular page-change consumer has new pages to process. This avoids running that consumer in workspaces where its cursor is already up to date.

**Data flow**: It builds the consumer's cursor key, reads stored cursors from the extension store, reads each workspace's newest page, and compares the newest page against that workspace's cursor. It returns only workspace IDs whose newest page is beyond the cursor.

**Call relations**: The job runner uses this through the candidate function created in core_jobs. It calls _page_beyond_cursor for the final bookmark comparison.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 2 external calls (select, owner_tx).


##### `PageChangeRunner.drive`  (lines 528–546)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: Runs one page-change consumer inside the currently bound workspace. It feeds changed pages to the extension hook in batches and advances that consumer's cursor only after the hook succeeds.

**Data flow**: It builds the extension context, reads the stored cursor, asks the page feed for a batch of pages changed since that cursor, calls the hook with a PageChangeBatch, stores the new cursor, and repeats until there are no more pages or the final batch is smaller than the batch size. If the hook raises an error, the cursor is not advanced past that failed batch.

**Call relations**: The page-change job handler created by core_jobs calls this. It uses _context_for to give the hook the same scoped services that other extension jobs receive.

*Call graph*: calls 1 internal fn (_context_for); 2 external calls (__init__, __init__).


##### `PageChangeRunner._context_for`  (lines 548–563)

```
def _context_for(self, extension: str, declared: frozenset[str]) -> ExtensionContext
```

**Purpose**: Builds the ExtensionContext used by a page-change hook. That context is the hook's toolbox: scoped storage, page access, model access, optional turn scheduling, and other services.

**Data flow**: It receives an extension name and declared credential slots, optionally creates an invoker for the current workspace, and passes the available service objects into context_for. It returns a ready-to-use ExtensionContext.

**Call relations**: PageChangeRunner.drive calls this before invoking a hook. It reads the current workspace binding so any invoker it creates is tied to the correct workspace.

*Call graph*: called by 1 (drive); 2 external calls (context_for, ws_current).


##### `core_jobs`  (lines 566–635)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, reaper: SandboxReaper, page_change_runner: PageChangeRunner) -> tuple[JobSpec, ...]
```

**Purpose**: Creates the built-in jobs that every deployment should run. These include source syncing, page-change fan-out, turn dispatch recovery, and sandbox cleanup.

**Data flow**: It receives the core job helpers, defines small adapter handlers around them, asks the page-change runner for its consumers, and returns JobSpec records with names, schedules, handlers, and candidate functions. The output is a tuple of jobs ready to be bound and registered.

**Call relations**: Startup code uses this before bindings_from and JobRunner.launch. It is the bridge from concrete core services into the generic job system.

*Call graph*: calls 1 internal fn (consumers); 1 external calls (__init__).


##### `core_jobs._sync_sources`  (lines 583–584)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: Adapter handler that runs the source sync driver as a job. It exists so source syncing fits the same JobSpec handler shape as extension jobs.

**Data flow**: It receives an ExtensionContext but does not need to read from it. It calls the sync driver, which polls sources and lands pages, then returns nothing.

**Call relations**: core_jobs places this handler in the source-sync JobSpec. JobRunner.fire later invokes it inside a bound workspace context.


##### `core_jobs._dispatch_turns`  (lines 586–587)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: Adapter handler that runs the turn dispatcher as a job. It keeps turn dispatch inside the standard job execution path.

**Data flow**: It receives an ExtensionContext, ignores it, calls the turn dispatcher's run method, and returns when the dispatch sweep is done.

**Call relations**: core_jobs places this handler in the turn-dispatch JobSpec. JobRunner.fire invokes it after binding the target workspace.


##### `core_jobs._reap_sandboxes`  (lines 589–590)

```
async def _reap_sandboxes(context: ExtensionContext) -> None
```

**Purpose**: Adapter handler that runs the sandbox reaper as a job. It lets sandbox cleanup use the same scheduling and workspace binding as every other job.

**Data flow**: It receives an ExtensionContext, ignores it, calls the reaper's run method, and returns after idle sandboxes in that workspace have been considered.

**Call relations**: core_jobs places this handler in the sandbox-reap JobSpec. JobRunner.fire calls it inside the workspace that candidate_workspaces selected.


##### `core_jobs._drive_consumer`  (lines 592–598)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: Creates a job handler for one page-change consumer. This turns a specific extension hook into a scheduled job function.

**Data flow**: It receives a PageChangeConsumer and returns an async handler function. The returned handler will later call page_change_runner.drive for that same consumer.

**Call relations**: core_jobs calls this once per discovered page-change consumer while building JobSpec records. The nested handler is what JobRunner.fire eventually invokes.


##### `core_jobs._drive_consumer._handler`  (lines 595–596)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: Runs the page-change cursor loop for the consumer captured by _drive_consumer. It is the actual handler stored in the consumer's JobSpec.

**Data flow**: It receives an ExtensionContext from the job runner, but the page-change runner builds the exact hook context it needs itself. It calls page_change_runner.drive with the captured consumer and returns when that consumer has drained its current page batch work.

**Call relations**: JobRunner.fire calls this as a job handler. It hands control to PageChangeRunner.drive, which reads pages, calls the extension hook, and saves the cursor.


##### `core_jobs._consumer_candidates`  (lines 600–604)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: Creates a candidate-workspace function for one page-change consumer. This gives each consumer its own workspace selection logic.

**Data flow**: It receives a PageChangeConsumer and returns an async function. That returned function later asks the page-change runner which workspaces have pending changes for that consumer.

**Call relations**: core_jobs stores this generated function in each page-change JobSpec. JobRunner.tick calls it through JobRunner.candidates before enqueueing per-workspace work.


##### `core_jobs._consumer_candidates._candidates`  (lines 601–602)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the workspace IDs where the captured page-change consumer has work waiting. It is the concrete candidate function used by that consumer's job.

**Data flow**: It has no direct inputs when called. It calls page_change_runner.workspaces_with_changes for the captured consumer and returns the resulting tuple of workspace IDs.

**Call relations**: JobRunner.tick reaches this through the JobSpec candidates field. Its output determines which workspace-specific job_workflow runs are enqueued.


##### `bindings_from`  (lines 646–671)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...]) -> tuple[_Binding, ...]
```

**Purpose**: Combines core jobs and extension jobs into uniquely named bindings. A binding says which extension namespace a job belongs to, which credential slots it declared, and what JobSpec should run.

**Data flow**: It receives extension manifests and core JobSpec records. It creates core bindings under the core namespace, then creates bindings for each extension job using that extension's name and credential declarations. It returns all bindings as a tuple.

**Call relations**: Startup code uses this before creating a JobRunner. JobRunner later looks up bindings by key to find candidate functions and handlers.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 693–717)

```
def launch(self) -> None
```

**Purpose**: Registers all known jobs with DBOS at process startup. Timed jobs become schedules, and one-shot jobs are enqueued once with deduplication so repeated boots do not start duplicates.

**Data flow**: It stores this runner in the module-level firing slot, walks every binding, and either enqueues a one-shot tick or collects a schedule entry. It logs what it registered, warns if a one-shot was already queued, and finally applies all cron-like schedules to DBOS.

**Call relations**: This is called during service startup. Later DBOS workflow functions job_tick and job_workflow use the stored runner to get back to the live job configuration.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 719–731)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: Responds to one scheduled fire by fanning the job out to the workspaces that actually have work. It prevents one workspace from accumulating many duplicate runs of the same job.

**Data flow**: It receives the scheduled time and job key, asks the job's candidate function for workspace IDs, and enqueues one job_workflow per workspace using a deduplication ID made from the job key and workspace ID. If that workspace already has that job in progress or queued, it logs a skip instead of stacking another copy.

**Call relations**: job_tick calls this durable method. It calls JobRunner.candidates to discover work and hands each workspace run to JOB_QUEUE as job_workflow.

*Call graph*: calls 1 internal fn (candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 733–734)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: Runs the candidate-workspace function for a job key. It is the small lookup step between a generic scheduled tick and the job-specific way of finding work.

**Data flow**: It receives a job key, finds the matching binding, calls that binding's JobSpec candidates function, and returns the workspace IDs. It does not enqueue or run the job itself.

**Call relations**: JobRunner.tick calls this before queueing workspace-specific workflows. It relies on _binding to turn the key into the correct JobSpec.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 736–755)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: Runs one job handler for one workspace, inside the correct workspace and extension context. This is the only path that actually executes a job's handler.

**Data flow**: It receives a job key and workspace ID, finds the binding, opens that workspace context, builds an ExtensionContext with the proper services and declared credential slots, and awaits the handler. If the handler raises an error, it logs the failure and re-raises so the workflow is marked failed.

**Call relations**: job_workflow calls this after DBOS dequeues a workspace-specific job run. It uses _binding to find the handler and context_for to build the handler's environment.

*Call graph*: calls 1 internal fn (_binding); 3 external calls (context_for, log_error, ws).


##### `JobRunner._binding`  (lines 757–761)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: Finds the registered binding for a job key or raises a clear error if the key is unknown. This protects the runner from silently doing nothing for a bad job name.

**Data flow**: It receives a key, searches the runner's binding tuple, and returns the matching _Binding. If no binding matches, it raises a RuntimeError.

**Call relations**: JobRunner.candidates and JobRunner.fire both call this. It is the common lookup point for both phases of job execution: deciding where to run and then actually running.

*Call graph*: called by 2 (candidates, fire).


##### `job_tick`  (lines 768–772)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: DBOS workflow entry for a scheduled job tick. It reconnects durable DBOS execution to the in-process JobRunner that was registered at startup.

**Data flow**: It receives the scheduled time and job key from DBOS. It reads the module-level runner, raises an error if jobs were not launched, and otherwise calls runner.tick. The result is fan-out enqueueing, not direct handler execution.

**Call relations**: DBOS schedules and one-shot enqueues call this workflow. It hands off to JobRunner.tick, which queues the per-workspace job_workflow runs.


##### `job_workflow`  (lines 776–780)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: DBOS workflow entry for one job running in one workspace. It is the durable wrapper around the actual handler execution.

**Data flow**: It receives the scheduled time, job key, and workspace ID as a string. It checks that a JobRunner is registered, converts the workspace ID into a UUID, and calls runner.fire. The handler then runs inside the workspace context.

**Call relations**: JobRunner.tick enqueues this workflow for each candidate workspace. It hands off to JobRunner.fire, which builds the extension context and executes the job handler.

*Call graph*: 1 external calls (UUID).


### `core/src/ufo/scheduling.py`

`domain_logic` · `scheduled task creation, polling, firing, status checks, and cleanup`

Scheduled tasks are like calendar reminders for the system. A row in the database says: at this time, re-enter this conversation with this agent and deliver this prompt. Without this file, reminders, recurring jobs, and one-time pauses would either be forgotten after a restart or could be fired twice by overlapping workers.

The file defines the shape of a scheduled task, then provides ScheduleStore, a workspace-scoped tool for creating, listing, canceling, claiming, inspecting, and advancing those tasks. “Workspace-scoped” means every read and write is limited to the currently active workspace, so one customer or project cannot accidentally see another one’s scheduled work.

A key idea is claiming. When a runner looks for due tasks, it does not simply read them. It leases them by writing a temporary claim into the database. That lease acts like putting a sticky note on a shared task card: “I am working on this until this time.” If another runner polls at the same moment, it should skip those claimed rows. Expired tasks can be removed, recurring tasks can be moved to their next run time, and one-time pauses have special safeguards so a member reply and a timer wake-up converge on the same durable conversation turn instead of racing each other.

#### Function details

##### `ScheduleInvoker.invoke_scheduled`  (lines 55–57)

```
async def invoke_scheduled(self, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: This is the interface for something that knows how to actually run a scheduled task. ScheduleStore stores and leases tasks, but this method is the doorway to the outside runner that turns a due task into conversation activity.

**Data flow**: It receives a ScheduledTask and an optional instruction for how to run it. An implementation uses that information to invoke the right agent in the right conversation, then returns the new turn identifier if one was created, or nothing if no turn resulted.

**Call relations**: ScheduleStore.invoke delegates to this method when an invoker has been wired in. The concrete implementation lives outside this file, so this file can describe the contract without depending on the runner’s details.


##### `_utc`  (lines 93–96)

```
def _utc(value: datetime | None) -> datetime | None
```

**Purpose**: This helper makes a stored datetime easier to compare and display by marking it as UTC when it has no time zone attached. UTC is the common world clock used here to avoid local-time confusion.

**Data flow**: It receives either a datetime or nothing. If there is no value, it returns nothing; if the datetime already has a time zone, it returns it unchanged; otherwise it returns the same clock time labeled as UTC.

**Call relations**: _task uses this while building ScheduledTask objects from database rows, and ScheduleStore.inspect uses it while preparing status information. It is a small cleanup step before data leaves the database layer.

*Call graph*: called by 2 (inspect, _task); 1 external calls (replace).


##### `_claim_available`  (lines 99–103)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for tasks that are free to be claimed. A task is available if nobody has claimed it, or if its previous claim has timed out.

**Data flow**: It receives the current time. It produces a database expression that says: claimed_by is empty, or claim_expires_at is earlier than now.

**Call relations**: due_task_workspaces.candidates uses this to find workspaces worth polling, and ScheduleStore.claim_due uses the same rule when leasing actual tasks. Sharing the rule keeps the discovery step and the claiming step in agreement.

*Call graph*: called by 2 (claim_due, candidates); 1 external calls (or_).


##### `_expired`  (lines 106–110)

```
def _expired(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for tasks whose expiration time has passed. Expired tasks should no longer be fired.

**Data flow**: It receives the current time. It produces a database expression that matches rows with an expires_at value that is at or before that time.

**Call relations**: due_task_workspaces.candidates uses it to notice workspaces that need cleanup, and ScheduleStore.claim_due uses it to delete expired tasks before claiming due ones.

*Call graph*: called by 2 (claim_due, candidates); 1 external calls (and_).


##### `_task`  (lines 113–131)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: This turns a raw database row into a ScheduledTask object that the rest of the Python code can use. It is the translator between table columns and the in-memory value object.

**Data flow**: It receives a row mapping from a database query. It copies the task fields into a ScheduledTask, normalizing the expiration time through _utc, and returns that object.

**Call relations**: ScheduleStore.list and ScheduleStore.claim_due both read rows from the scheduled_task table, then hand each row to _task so callers receive consistent ScheduledTask objects instead of raw database records.

*Call graph*: calls 1 internal fn (_utc); called by 2 (claim_due, list); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 134–161)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: This provides the scheduled-task runner with a way to find which workspaces might have work ready. It avoids scanning every workspace when only a few have due or expired tasks.

**Data flow**: It takes no direct input. It returns an async candidate function that, when called, reads the database and returns workspace identifiers that contain claimable due work or claimable expired rows.

**Call relations**: This is the seam between the global runner and workspace-specific task processing. The runner can ask for candidate workspaces first, then bind each workspace before using ScheduleStore to touch that workspace’s tasks.


##### `due_task_workspaces.candidates`  (lines 141–159)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner function performs the actual database lookup for workspaces with scheduled-task activity. It looks for either due tasks or expired tasks that are not protected by a live claim.

**Data flow**: It reads the current UTC time, builds the same availability and expiry tests used by the claiming code, then queries scheduled_task rows across workspaces for distinct workspace identifiers. It returns those identifiers as a tuple.

**Call relations**: It is created by due_task_workspaces and is later called by the runner’s candidate system. It uses owner-level database access only to find workspace ids; actual task changes still happen later inside a workspace-bound ScheduleStore.

*Call graph*: calls 2 internal fn (_claim_available, _expired); 4 external calls (now, or_, select, owner_tx).


##### `ScheduleStore.workspace_id`  (lines 173–174)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property returns the workspace that the current operation is bound to. It is the guardrail that keeps all ScheduleStore operations inside one workspace.

**Data flow**: It reads the current workspace context and extracts its workspace_id. It returns that UUID to be used in database filters.

**Call relations**: Most ScheduleStore methods use this property when reading or writing scheduled_task rows. It ties each operation to the ambient workspace set up by the caller.

*Call graph*: 1 external calls (ws_current).


##### `ScheduleStore.invoke`  (lines 176–181)

```
async def invoke(self, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: This asks the configured invoker to actually run a scheduled task. If no invoker was provided, it fails early so a task is not silently dropped.

**Data flow**: It receives a ScheduledTask and an optional runtime instruction. If the store has an invoker, it forwards both values and returns the turn identifier produced by the invocation; if not, it raises an error.

**Call relations**: The scheduled task runner calls this during firing. ScheduleStore itself does not know how to talk to agents; it hands the task to the ScheduleInvoker contract for that part.

*Call graph*: called by 1 (_fire).


##### `ScheduleStore.create`  (lines 183–218)

```
async def create(self, conversation_id: UUID, agent_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None, expires_at: dateti
```

**Purpose**: This creates or updates a recurring scheduled task by name. It is for normal repeated jobs, not one-time pauses.

**Data flow**: It receives the conversation, agent, task name, schedule text, prompt, description, first run time, optional creator, and optional expiry. It rejects reserved one-time or pause names, then passes the details to _upsert and returns the resulting ScheduledTask.

**Call relations**: Callers use this when a user or tool defines a recurring schedule. The method relies on _upsert for the actual database insert-or-update so recurring tasks and pause tasks share the same core writing logic.

*Call graph*: calls 1 internal fn (_upsert).


##### `ScheduleStore.pause`  (lines 220–242)

```
async def pause(self, conversation_id: UUID, agent_id: UUID, prompt: str, description: str, next_run_at: datetime, origin_seq: int, created_by_member_id: UUID | None=None) -> ScheduledTask | None
```

**Purpose**: This arms a one-time wake-up for a conversation. It is used when a workflow should pause until a later time, then resume once.

**Data flow**: It receives the conversation, agent, prompt, description, wake-up time, the conversation sequence that requested the pause, and an optional creator. It builds a reserved pause name and calls _upsert with the one-time schedule marker, returning a ScheduledTask or nothing if a newer member reply makes the pause unnecessary.

**Call relations**: Pause uses the same _upsert machinery as recurring task creation, but with extra origin information. That origin lets member replies and timer recovery meet on one durable turn instead of creating competing work.

*Call graph*: calls 1 internal fn (_upsert).


##### `ScheduleStore._upsert`  (lines 244–386)

```
async def _upsert(self, conversation_id: UUID, agent_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, origin_seq: int | None, created_by_member_id: UUID | None
```

**Purpose**: This is the shared database write path for creating or replacing a scheduled-task row. “Upsert” means insert if missing, or update the existing row if the same workspace and name already exist.

**Data flow**: It receives all task details, including optional pause origin information and expiry. It locks the conversation row, checks special pause conditions, possibly redirects a pause to a newer queued member turn, then inserts or updates the scheduled_task row and returns a ScheduledTask. If a pending or already-handled member reply means the pause should not be armed, it returns nothing.

**Call relations**: ScheduleStore.create and ScheduleStore.pause both hand their work to this function. It is the place where name uniqueness, workspace scoping, claim clearing, pause race checks, and the final database row all come together.

*Call graph*: called by 2 (create, pause); 6 external calls (__init__, now, exists, select, workspace_tx, uuid4).


##### `ScheduleStore.cancel`  (lines 388–396)

```
async def cancel(self, name: str) -> bool
```

**Purpose**: This removes a scheduled task by name in the current workspace. It lets callers stop future fires for a known task name.

**Data flow**: It receives a task name. It deletes matching scheduled_task rows for the current workspace and returns true if at least one row was removed, otherwise false.

**Call relations**: This is the cleanup counterpart to create. Because names are unique per workspace, the name a caller used to create a recurring task is also the name used to cancel it.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `ScheduleStore.list`  (lines 398–414)

```
async def list(self) -> tuple[ScheduledTask, ...]
```

**Purpose**: This returns all recurring scheduled tasks in the current workspace. It deliberately leaves out one-time pause rows because those are internal workflow wake-ups, not user-facing recurring schedules.

**Data flow**: It reads scheduled_task rows for the current workspace where the schedule is not the one-time marker, orders them by name, converts each row with _task, and returns them as a tuple.

**Call relations**: Status pages or tools that show configured schedules can call this method. It depends on _task so the result has the same shape as tasks returned by claiming.

*Call graph*: calls 1 internal fn (_task); 2 external calls (select, workspace_tx).


##### `ScheduleStore.claim_due`  (lines 416–470)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: This leases due scheduled tasks so a runner can safely fire them. The lease prevents two overlapping pollers from both taking the same task.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of tasks. In one database transaction it deletes expired claimable rows, selects the oldest due claimable tasks up to the limit, stamps them with a new claim id and claim expiry, converts the returned rows with _task, and returns them.

**Call relations**: The scheduled runner uses this when processing a workspace. It shares _claim_available and _expired with workspace discovery so candidates and actual claims follow the same rules.

*Call graph*: calls 3 internal fn (_claim_available, _expired, _task); 7 external calls (timedelta, delete, not_, select, update, workspace_tx, uuid4).


##### `ScheduleStore.retire_if_expired`  (lines 472–486)

```
async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool
```

**Purpose**: This removes a claimed task if it expired before the runner could invoke it. It is a last safety check right before firing.

**Data flow**: It receives a claimed ScheduledTask and the current time. If the task has no claim, it raises an error; if it is not expired, it returns false; if it is expired, it deletes that exact claimed row and returns true.

**Call relations**: The scheduled task runner calls this during its fire flow. It only deletes the row when the claim id still matches, so it does not accidentally remove a task another worker has reclaimed.

*Call graph*: called by 1 (_fire); 2 external calls (delete, workspace_tx).


##### `ScheduleStore.reschedule`  (lines 488–521)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: This advances a recurring claimed task after it has fired. It records when it ran, optionally records the turn it created, clears the lease, and sets the next run time.

**Data flow**: It receives a claimed ScheduledTask, the next run time, the last run time, and optionally the turn id created by the fire. It rejects unclaimed tasks and one-time pauses, then updates that exact claimed row and returns whether the update succeeded.

**Call relations**: The scheduled task runner calls this after invoking a recurring task. It is the handoff from “this task has been fired” back to “this task is waiting for its next scheduled time.”

*Call graph*: called by 1 (_fire); 2 external calls (update, workspace_tx).


##### `ScheduleStore.inspect`  (lines 523–559)

```
async def inspect(self, name: str) -> TaskInspection | None
```

**Purpose**: This returns a live status view for one recurring scheduled task. It includes timing information plus the latest turn’s status and final response text, if there is one.

**Data flow**: It receives a task name. It looks up that recurring task in the current workspace and left-joins its last recorded turn, meaning the task can still be shown even if there is no last turn. It returns a TaskInspection object, or nothing if no matching recurring task exists.

**Call relations**: Tools that render scheduled-task status use this read path. It connects the task definition to the last turn recorded by reschedule, so users can see not only when the task runs, but what happened last time.

*Call graph*: calls 1 internal fn (_utc); 3 external calls (__init__, select, workspace_tx).


### Scheduled task extension
The scheduled-tasks extension defines cron timing, user-facing task tools, and the worker that ticks due tasks forward.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `scheduled task setup and run scheduling`

Scheduled tasks need a way to say “run every day at 9” or “run every five minutes.” This file keeps that timing language in one small place. The main system only stores a concrete next run time, called `next_run_at`; it does not need to understand cron itself. That separation matters because cron is a special schedule format, and this extension owns the rules for reading it.

A cron expression is a compact text pattern with fields for minute, hour, day of month, month, and day of week. For example, it is like a reusable calendar rule rather than a single appointment. This file expects exactly five fields. If the text has too many or too few parts, it rejects it before anything is saved or used.

For deeper validation and date calculation, it uses `croniter`, an external library that understands cron expressions. `validate_cron` confirms the schedule is well-formed. `next_fire` asks croniter for the next matching date after a given moment. One important detail is that the next date is strictly after the supplied time. So if a task runner falls behind, missed schedule slots do not become a burst of old runs; the system moves forward to one catch-up run.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: This function checks whether a schedule string is a valid 5-field cron expression. It is used before accepting a schedule, so bad timing rules are caught early with a clear error.

**Data flow**: It receives a schedule as text. First it splits the text into separate fields and makes sure there are exactly five. Then it asks `croniter` whether the expression is valid cron. If anything is wrong, it raises a `ValueError` with a helpful message; if everything is right, it returns the original schedule unchanged.

**Call relations**: When the scheduled-tasks extension needs to accept or store a cron schedule, this function is the gatekeeper. Inside that check, it hands the expression to `croniter.croniter.is_valid` so the external cron parser can confirm the detailed cron rules.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: This function calculates the next time a cron schedule should run after a given moment. It turns a repeating calendar rule into one concrete datetime the scheduler can store and compare.

**Data flow**: It receives a cron schedule string and an `after` datetime. It gives both to `croniter`, which walks forward through the calendar until it finds the next matching time. The function returns that next datetime and does not change anything else.

**Call relations**: When the task system needs to update `next_run_at`, this function provides the new timestamp. It delegates the actual cron math to `croniter.croniter`, keeping this file focused on the project’s simple rule: always compute the next fire strictly after the supplied time.

*Call graph*: 1 external calls (croniter).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `request handling and scheduled resume`

This file is the bridge between user-facing scheduling features and the durable schedule store, which is the place that remembers future work even after the current turn ends. Without it, an agent could not expose recurring tasks as normal workspace objects, and long-running workflows could not reliably wait for a future event.

A scheduled task here is a named object with a cron schedule, meaning a compact text pattern such as “9am every weekday.” The file defines what a valid task looks like, checks that expiry times are written in UTC, and registers the task kind so the platform’s generic object commands can list it, inspect it, apply changes, and delete it.

The main helper class, ScheduledTaskObjects, adapts the schedule store to the object system. It turns stored task rows into object summaries, detailed views, and status reports. When a task is applied, it validates the cron text, calculates the next fire time, and stores the task tied to the current conversation, agent, and creator. This matters because a later scheduled run should “come back” into the same conversation and act as the same member.

The file also defines pause_and_wait. Think of it like leaving a note on a desk plus setting an alarm clock. If a member replies first, the workflow resumes from that message. If nobody replies, the durable timer wakes it up later.

#### Function details

##### `ScheduledTaskSpec.validate_utc_expiry`  (lines 65–68)

```
def validate_utc_expiry(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This checks that a task expiry time, if one is provided, is written as a real UTC time. UTC is the shared world clock used here so scheduled work does not shift unexpectedly because of local time zones.

**Data flow**: It receives the proposed expires_at value from the task specification. If there is no expiry, it passes that through unchanged. If there is an expiry, it checks that the value has timezone information and that its offset is exactly zero; otherwise it rejects the value with a clear error.

**Call relations**: This validator runs as part of building or accepting a ScheduledTaskSpec. It protects later scheduling code from receiving an ambiguous local timestamp that could make cancellation happen at the wrong moment.

*Call graph*: 2 external calls (utcoffset, timedelta).


##### `_require_scheduler`  (lines 85–88)

```
def _require_scheduler(ctx: ToolContext) -> ScheduleStore
```

**Purpose**: This small guard finds the schedule store attached to the current tool context. If scheduling has not been installed for this context, it stops immediately with an error instead of letting later code fail in a confusing way.

**Data flow**: It takes the current ToolContext, looks inside its extension area, and returns the scheduler store when present. If either the extension context or the scheduler is missing, it raises a runtime error and nothing is changed.

**Call relations**: All of the scheduled-task operations call this before touching stored schedules. ScheduledTaskObjects uses it when listing, inspecting, applying, deleting, and finding tasks, and pause_and_wait uses it before creating a pause timer.

*Call graph*: called by 6 (_apply_owned, _delete_owned, _find, _owned_rows, _status, pause_and_wait).


##### `_summary`  (lines 91–92)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: This creates the short line shown for a scheduled task in listings. It combines the cron schedule with either the task description or, if no description was supplied, the prompt itself.

**Data flow**: It receives one stored ScheduledTask. It builds a text summary from the task’s schedule and human-facing description or prompt, then trims it to the maximum listing length.

**Call relations**: ScheduledTaskObjects._owned_rows calls this while turning stored task records into object-list rows. Its output is what helps people recognize a task quickly without opening the full details.

*Call graph*: called by 1 (_owned_rows).


##### `ScheduledTaskObjects._owned_rows`  (lines 113–121)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]
```

**Purpose**: This produces the list-view version of all visible scheduled tasks. Each row includes the task name, a short summary, and the member who owns it.

**Data flow**: It receives the current tool context, gets the scheduler store, asks it for scheduled tasks, and converts each stored task into an OwnedRow. The result is a tuple of rows that the object system can use for listing and ownership checks.

**Call relations**: The generic object layer calls this when someone lists scheduled_task objects. It relies on _require_scheduler to reach the store and _summary to make each task readable.

*Call graph*: calls 2 internal fn (_require_scheduler, _summary); 2 external calls (__init__, __init__).


##### `ScheduledTaskObjects._detail`  (lines 123–142)

```
async def _detail(self, ctx: ToolContext, name: str) -> ObjectDetail[ScheduledTaskSpec] | None
```

**Purpose**: This builds the full object view for one scheduled task. It shows the task’s editable specification, timestamps, and a link back to the conversation where the task reports.

**Data flow**: It receives a context and task name, searches for that task, and returns nothing if it is absent. If found, it copies the stored schedule, prompt, description, expiry, creation time, update time, and reporting conversation into an ObjectDetail.

**Call relations**: The object system calls this when someone asks to get or inspect one scheduled_task object. It delegates the lookup to ScheduledTaskObjects._find, then packages the stored row into the standard object-detail shape.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `ScheduledTaskObjects._status`  (lines 144–168)

```
async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This reports runtime information about one scheduled task, such as when it will run next and what happened on its latest run. It separates live status from the task’s editable definition.

**Data flow**: It receives a context and task name, asks the scheduler to inspect that task, and returns nothing if the task is unknown. If inspection data exists, it formats the next run time, last run time, expiry time, and a shortened excerpt of the latest response into a plain dictionary.

**Call relations**: The object system calls this when it needs status for a scheduled_task. It gets its information directly from the scheduler store through _require_scheduler, instead of rebuilding status from the object specification.

*Call graph*: calls 1 internal fn (_require_scheduler).


##### `ScheduledTaskObjects._apply_owned`  (lines 170–192)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: This creates or updates a scheduled task after checking that the current member is allowed to do so. It is where the human task definition becomes a durable future schedule.

**Data flow**: It receives the context, object name, new task specification, any old specification, and the object owner. If the task already has a creator and the current acting member is not that creator, it refuses the change. Otherwise it validates the cron schedule, calculates the next fire time from the current UTC time, and writes the task into the scheduler store tied to the current conversation, agent, member, prompt, description, and expiry.

**Call relations**: The generic object apply flow calls this when a scheduled_task manifest is applied. It calls cron validation before storing anything, uses next_fire to decide the first future run, and hands the final schedule to the scheduler store obtained through _require_scheduler.

*Call graph*: calls 1 internal fn (_require_scheduler); 4 external calls (__init__, now, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 194–195)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: This cancels a scheduled task by name. It is the object-system delete action translated into the scheduler store’s cancellation operation.

**Data flow**: It receives the current context, the task name, and the owner information already checked by the surrounding object system. It gets the scheduler store and asks it to cancel the named task; it returns no data.

**Call relations**: The generic object delete flow calls this after ownership rules allow deletion. It does not repeat the visibility policy itself; it simply hands the cancellation to the scheduler through _require_scheduler.

*Call graph*: calls 1 internal fn (_require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 197–200)

```
async def _find(self, ctx: ToolContext, name: str) -> ScheduledTask | None
```

**Purpose**: This searches the scheduler’s task list for one named scheduled task. It is a local lookup helper used when building detailed object output.

**Data flow**: It receives the context and desired task name. It gets the scheduler store, lists the tasks, scans them for a matching name, and returns the first matching task or returns nothing if none match.

**Call relations**: ScheduledTaskObjects._detail calls this before building a full object detail. The helper keeps the detail method focused on presentation while this function handles the simple store lookup.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 1 (_detail).


##### `pause_and_wait`  (lines 230–267)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: This tool pauses the current workflow until either a member sends a new message or a durable timer reaches its wake-up time. It is useful for things like waiting for an approval, an email verification, or an outside system cooldown.

**Data flow**: It receives the current tool context and pause instructions: the message to show now, how many minutes to wait, what to do after resuming, the reason, and optional saved metadata. It calculates the resume time, stores a one-time pause request in the scheduler with a prompt that includes the resume instructions, and then returns text telling the agent how to end the current turn. If a newer member message has already arrived, it returns instructions saying the member message will resume the workflow instead of arming a timer; otherwise it returns instructions saying the timer is now awaited.

**Call relations**: The tool framework calls this when the agent invokes the pause_and_wait tool. It uses _require_scheduler to reach durable scheduling, records the pause through the scheduler store, and returns a ToolResult containing TextContent so the agent knows exactly what to say and how to stop until the resume event.

*Call graph*: calls 1 internal fn (_require_scheduler); 5 external calls (__init__, __init__, now, timedelta, dumps).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `scheduled background tick`

Scheduled tasks need a reliable “alarm clock.” This file provides that alarm clock. It does not wait for each task row to wake itself up. Instead, a recurring runner wakes up at intervals, asks the schedule store for tasks that are due, and briefly claims them with a lease. A lease is like putting a sticky note on a task saying “I am working on this,” so another overlapping runner does not start the same task at the same time.

For each claimed task, the runner first checks whether it has already expired. If so, it retires it and does nothing else. If the task is still valid, the runner figures out whether there is another future occurrence. For repeating schedules, it uses the cron helper to calculate the next fire time; for one-time schedules, there is no next fire.

Then it invokes the task back into its conversation. If this is the last allowed occurrence before expiry, it adds a special instruction telling the conversation to finish the task and ask the user whether to continue, change, or stop the cadence. If invocation succeeds and the task repeats, the runner records the next scheduled time. If a fire fails, it leaves the claimed occurrence available for retry later and reports the failed task names at the end.

#### Function details

##### `ScheduledTaskRunner.run`  (lines 36–47)

```
async def run(self) -> None
```

**Purpose**: This is the main tick of the scheduled-task runner. It checks that a scheduler is available, claims every task due right now, asks each one to fire, and reports if any task could not be started.

**Data flow**: It starts with the runner’s extension context, which should contain a scheduler. It reads the current time, asks the scheduler for due tasks and a temporary claim on them, then sends each task to `_fire`. It collects any failure messages. If nothing failed, it finishes quietly; if one or more tasks failed, it raises an error naming them.

**Call relations**: This is the outer loop for the file. When the extension’s recurring job wakes up, this method drives the batch: it gets the time, obtains due tasks from the scheduler, and delegates the careful per-task decisions to `ScheduledTaskRunner._fire`.

*Call graph*: calls 1 internal fn (_fire); 1 external calls (now).


##### `ScheduledTaskRunner._fire`  (lines 49–83)

```
async def _fire(self, scheduler: ScheduleStore, task: ScheduledTask, tick_at: datetime, expiry_checked_at: datetime) -> str | None
```

**Purpose**: This function performs the full decision process for one claimed scheduled task. It retires expired tasks, invokes live tasks, adds a final-run instruction when needed, and reschedules repeating tasks after a successful fire.

**Data flow**: It receives the scheduler, one claimed task, the time of the runner tick, and the time used for the expiry check. First it asks the scheduler whether the task should be retired because it has expired. If not, it calculates the next fire time for repeating schedules. If that next fire would be at or beyond the expiry time, it prepares a final instruction for the conversation. It then asks the scheduler to invoke the task. A successful one-time fire ends there. A successful repeating fire is written back with its next fire time. If invocation raises an error, the function returns a short failure label instead of rescheduling.

**Call relations**: This method is called by `ScheduledTaskRunner.run` for each due task in the batch. It relies on the schedule store for the durable actions: retiring expired tasks, invoking the task into its conversation, and recording the next scheduled occurrence. It also uses the cron helper to compute the next time a repeating task should run.

*Call graph*: calls 3 internal fn (invoke, reschedule, retire_if_expired); called by 1 (run); 1 external calls (next_fire).


### Customer and billing automation
Customer-facing background automation provisions Slack Connect onboarding and synchronizes billing, usage, seats, and payment setup.

### `control/src/ufo_control/gateway_slack_connect.py`

`orchestration` · `background polling after gateway startup`

When a customer finishes signup, the main signup path does not wait for Slack. Instead, this file later notices the completed signup in the database, creates a public channel in UFO’s own Slack workspace, and sends a Slack Connect invitation to the customer’s email. Think of it like a mailroom: signup drops a durable note in a ledger, and this worker keeps checking the ledger until the invitation has really been sent.

The database table in this file records each delivery’s state: waiting, claimed by a worker, delivered, or failed. Multiple gateway replicas may run this worker, so each row is leased before work starts. A lease is like putting a temporary “I’m working on this” sticker on a task. If the worker dies, the sticker expires and another worker can continue.

The Slack client wraps outgoing Slack Web API calls. It separates temporary problems, such as rate limits or server errors, from permanent problems, such as bad configuration or an unusable recipient. The inviter uses that distinction to either retry later with backoff or mark the row failed for an operator to inspect. It also records key steps before risky Slack calls, so if a response is lost, later runs can reconcile with Slack instead of blindly sending a second invite.

#### Function details

##### `SlackTransientError.__init__`  (lines 167–169)

```
def __init__(self, message: str, retry_after: float | None=None) -> None
```

**Purpose**: This creates an error object for Slack problems that may go away, such as rate limiting, timeouts, or server failures. It can carry a suggested wait time before trying again.

**Data flow**: It receives a human-readable message and, optionally, a retry-after number of seconds. It stores the message as the normal exception text and saves the retry delay on the error object for later scheduling.

**Call relations**: SlackConnectClient._call creates this error when an HTTP request to Slack fails in a way that should not be treated as final. Later, the delivery flow catches it and reschedules the database row instead of failing it immediately.

*Call graph*: called by 1 (_call).


##### `rearm_failed_delivery`  (lines 188–203)

```
async def rearm_failed_delivery(pool: asyncpg.Pool, onboard_claim_id: UUID) -> datetime | None
```

**Purpose**: This is the operator recovery hook for a failed Slack Connect delivery. After a human fixes the cause, this function puts one failed row back into the waiting state so the background worker can try again.

**Data flow**: It receives a database pool and the signup claim ID to recover. It looks for that exact row only if it is currently failed, resets its worker, retry, timing, attempt, and error fields, and returns the time when the row was previously updated. If the row was not failed or does not exist, it returns nothing.

**Call relations**: This function talks directly to the database through asyncpg.Pool.fetchval. It does not call Slack; it only reopens work for the normal SlackConnectInviter polling loop to pick up later.

*Call graph*: 1 external calls (fetchval).


##### `SlackConnectClient.team_id`  (lines 215–216)

```
async def team_id(self) -> str
```

**Purpose**: This asks Slack which workspace the configured bot token belongs to. It is used as a safety check before creating or changing any channel.

**Data flow**: It sends an auth.test request to Slack, receives Slack’s response, and extracts the team_id field as text. The result is the Slack workspace ID connected to the token.

**Call relations**: It calls SlackConnectClient._call to make the outbound Slack request and SlackConnectClient._text to safely read the expected field. SlackConnectInviter._verify_team uses this check during delivery before any channel mutation.

*Call graph*: calls 2 internal fn (_call, _text).


##### `SlackConnectClient.create_channel`  (lines 218–220)

```
async def create_channel(self, name: str) -> str
```

**Purpose**: This creates a new public Slack channel with the deterministic customer channel name. It returns the Slack channel ID needed for later invitation steps.

**Data flow**: It receives a channel name, sends that name to Slack’s conversations.create API, and reads channel.id from the response. If Slack accepts the request, the output is the new channel’s ID.

**Call relations**: It relies on SlackConnectClient._call for the HTTP request and SlackConnectClient._text for response extraction. In the broader workflow, SlackConnectInviter._open_channel uses it when a delivery row does not already have a channel ID.

*Call graph*: calls 2 internal fn (_call, _text).


##### `SlackConnectClient.channel_id_by_name`  (lines 222–243)

```
async def channel_id_by_name(self, name: str) -> str
```

**Purpose**: This finds an existing Slack channel by its exact name, including archived channels. It is used to recover when Slack says a channel name is already taken, which may mean an earlier create call succeeded but its response was lost.

**Data flow**: It receives the deterministic channel name, pages through Slack’s channel list, and compares each channel’s name. If it finds a match, it returns that channel’s ID; if the name was supposedly taken but no channel can be found, it raises a permanent error for operator review.

**Call relations**: It calls SlackConnectClient._call to fetch channel pages and SlackConnectClient._text to extract the ID. SlackConnectInviter._open_channel uses this recovery path after a name-taken result from channel creation.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.outgoing_invite_id`  (lines 245–276)

```
async def outgoing_invite_id(self, channel_id: str) -> str | None
```

**Purpose**: This checks whether Slack already has a live outgoing Slack Connect invitation for a channel. It prevents the system from sending a duplicate invite after an uncertain earlier attempt.

**Data flow**: It receives a channel ID, pages through Slack’s listed Connect invitations, and looks for entries for that channel. If it finds a live invitation, it returns its invite ID; if it finds only dead invitations, it returns nothing; if Slack reports an unknown status or too many pages, it raises a permanent error because the situation is unclear.

**Call relations**: It uses SlackConnectClient._call to read Slack’s invitation pages and SlackConnectClient._text to pull out the invite ID. SlackConnectInviter._invite uses this during reconciliation when the database shows that an invite was already attempted.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.is_externally_shared`  (lines 278–281)

```
async def is_externally_shared(self, channel_id: str) -> bool
```

**Purpose**: This asks Slack whether a channel is already shared or pending shared with an outside workspace. It gives the worker another way to confirm that a previous invitation worked.

**Data flow**: It receives a channel ID, calls Slack for channel information, and reads the sharing flags from the returned channel object. It returns true if Slack says the channel is externally shared or pending external sharing, otherwise false.

**Call relations**: It calls SlackConnectClient._call to get channel details. SlackConnectInviter._invite uses this after checking for a live invitation, as part of deciding whether it is safe or necessary to send another invite.

*Call graph*: calls 1 internal fn (_call).


##### `SlackConnectClient.invite_shared`  (lines 283–290)

```
async def invite_shared(self, channel_id: str, email: str) -> str
```

**Purpose**: This sends the actual Slack Connect invitation email for a channel. It also rejects email addresses that are too long before asking Slack.

**Data flow**: It receives a channel ID and recipient email. If the email is within the allowed length, it calls Slack’s conversations.inviteShared API with limited external access enabled, then extracts and returns the invite ID from Slack’s response.

**Call relations**: It calls SlackConnectClient._call for the Slack API request and SlackConnectClient._text for the returned invite ID. SlackConnectInviter._invite uses it only after recording that an invite attempt is about to happen.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.redact`  (lines 292–293)

```
def redact(self, message: str) -> str
```

**Purpose**: This removes the bot token from error text before the text is saved or logged. It protects secrets from appearing in logs, database rows, or debugging output.

**Data flow**: It receives a message string, replaces any copy of the bot token with a fixed placeholder, and trims the result to the maximum stored error length. The output is a safer, bounded message.

**Call relations**: Delivery error paths use this before writing failures or retry reasons. It is part of the safety layer around SlackConnectClient, even though it does not call other functions.


##### `SlackConnectClient._call`  (lines 295–323)

```
async def _call(self, method: str, params: dict[str, str | int]) -> dict[str, Any]
```

**Purpose**: This is the common low-level method for all Slack Web API requests in this file. It turns HTTP responses and Slack error codes into clear temporary or permanent exceptions.

**Data flow**: It receives a Slack method name and request parameters, removes empty parameters, sends a POST request with the bot token, and reads the JSON response. Successful Slack replies come back as a dictionary; rate limits, server errors, network failures, name conflicts, and other Slack errors become specific exceptions.

**Call relations**: All public SlackConnectClient methods call this instead of talking to httpx directly. It uses httpx.AsyncClient for the network call, _retry_after to honor Slack rate-limit timing, and creates SlackTransientError, SlackNameTakenError, or SlackTerminalError so the inviter can decide whether to retry, recover, or fail.

*Call graph*: calls 2 internal fn (__init__, _retry_after); called by 6 (channel_id_by_name, create_channel, invite_shared, is_externally_shared, outgoing_invite_id, team_id); 3 external calls (__init__, __init__, AsyncClient).


##### `SlackConnectClient._text`  (lines 325–331)

```
def _text(self, payload: dict[str, Any], *path: str) -> str
```

**Purpose**: This safely extracts a required text value from a nested Slack response. It prevents the rest of the code from silently continuing with a malformed response.

**Data flow**: It receives a response dictionary and a path of keys to follow. It walks through those keys, and if any key is missing or the structure is wrong, it raises a permanent Slack error; otherwise it returns the found value as text.

**Call relations**: SlackConnectClient.team_id, create_channel, channel_id_by_name, outgoing_invite_id, and invite_shared all use it after _call returns. It is the shared response-checking helper for required Slack fields.

*Call graph*: called by 5 (channel_id_by_name, create_channel, invite_shared, outgoing_invite_id, team_id); 1 external calls (__init__).


##### `_retry_after`  (lines 334–338)

```
def _retry_after(response: httpx.Response) -> float | None
```

**Purpose**: This reads Slack’s rate-limit waiting hint from an HTTP response. It caps the wait so Slack cannot make this worker sleep for an unbounded time.

**Data flow**: It receives an HTTP response, checks the retry-after header, and returns a number of seconds if the header is a clean number. If the header is absent or invalid, it returns nothing; if it is very large, it returns the configured maximum.

**Call relations**: SlackConnectClient._call uses this when Slack returns HTTP 429, meaning “too many requests.” The resulting delay is stored inside SlackTransientError for the retry scheduler.

*Call graph*: called by 1 (_call).


##### `SlackConnectInviter.run`  (lines 364–388)

```
async def run(self) -> None
```

**Purpose**: This is the never-ending background loop that keeps Slack Connect deliveries moving. It keeps running even when one sweep fails, so later signups are not stranded behind a still-healthy gateway.

**Data flow**: It starts with a failure counter at zero, repeatedly calls poll, and resets the counter after a successful sweep. If poll finds no claimed work, it sleeps for the configured interval; if an unexpected error happens, it logs it, increments the counter, and continues.

**Call relations**: It calls SlackConnectInviter.poll for each sweep and asyncio.sleep when there is no immediate work. The gateway lifespan would run this task after startup, and cancellation is the normal way to stop it.

*Call graph*: calls 1 internal fn (poll); 1 external calls (sleep).


##### `SlackConnectInviter.poll`  (lines 390–405)

```
async def poll(self) -> bool
```

**Purpose**: This performs one unit of background work: discover eligible signups, claim one due delivery, and advance it toward completion. It returns whether it claimed work so the main loop can drain a busy queue without sleeping.

**Data flow**: It first materializes completed signup claims into delivery rows, then tries to lease one due row. If none is available, it returns false. If it claims one, it starts a lease-renewal task, advances the delivery through Slack work, cancels the renewal task, waits for cleanup, and returns true.

**Call relations**: SlackConnectInviter.run calls this on every loop. Inside the sweep it calls _materialize, _claim, starts _renew_lease with asyncio.create_task, calls _advance, and then uses asyncio.gather to absorb the renewal task’s cancellation.

*Call graph*: calls 4 internal fn (_advance, _claim, _materialize, _renew_lease); called by 1 (run); 2 external calls (create_task, gather).


##### `SlackConnectInviter._materialize`  (lines 407–421)

```
async def _materialize(self) -> None
```

**Purpose**: This turns completed invite-code signup claims into Slack Connect delivery rows. It is the step that makes the durable signup record visible to the Slack invitation worker.

**Data flow**: It opens a database connection and transaction, takes a PostgreSQL advisory lock, and runs an insert-from-select statement. The statement chooses the first completed claim per workspace and inserts a pending delivery row with the deterministic channel name, skipping conflicts.

**Call relations**: SlackConnectInviter.poll calls this before trying to claim work. The advisory lock and conflict-skipping mean multiple gateway replicas can run the poller without racing or blocking all customers because one readable channel name collided.

*Call graph*: called by 1 (poll).


##### `SlackConnectInviter._claim`  (lines 423–435)

```
async def _claim(self) -> _Delivery | None
```

**Purpose**: This leases one delivery row for the current worker. Leasing stops two replicas from sending the same Slack invitation at the same time.

**Data flow**: It runs the claim query with this worker’s ID and the lease length. If the database returns no row, it returns nothing. If a row is claimed, it copies the row’s claim ID, email, channel data, invitation data, previous attempt time, and attempt count into a _Delivery object.

**Call relations**: SlackConnectInviter.poll calls this after materializing rows. If it returns a _Delivery, poll starts lease renewal and passes the delivery to _advance.

*Call graph*: called by 1 (poll); 1 external calls (__init__).


##### `SlackConnectInviter._renew_lease`  (lines 437–459)

```
async def _renew_lease(self, onboard_claim_id: UUID) -> None
```

**Purpose**: This keeps a claimed row reserved while slow Slack calls are in flight. Without renewal, another replica could take over midway and send a duplicate invitation.

**Data flow**: It receives the claim ID, sleeps for the renewal interval, then updates that row’s lease expiry if it is still owned by this worker. It repeats until cancelled, logging database renewal failures but trying again later.

**Call relations**: SlackConnectInviter.poll starts it as a background task with asyncio.create_task while _advance works on the delivery. Poll cancels it and gathers the task when the delivery attempt ends.

*Call graph*: called by 1 (poll); 1 external calls (sleep).


##### `SlackConnectInviter._advance`  (lines 461–488)

```
async def _advance(self, delivery: _Delivery) -> None
```

**Purpose**: This is the main delivery decision path for one claimed row. It verifies the Slack workspace, opens or finds the channel, sends or reconciles the invitation, and records the final state.

**Data flow**: It receives a _Delivery row snapshot. It verifies the bot token’s team, gets a channel ID if one is missing, gets an invitation ID or proof of sharing if one is missing, and then writes the row as delivered. Temporary errors are rescheduled, permanent errors are marked failed, lost leases are abandoned, and unexpected errors are logged and failed.

**Call relations**: SlackConnectInviter.poll calls this after claiming a row. It calls _verify_team, _open_channel, _invite, _reschedule, _fail, and _write as needed to move the row through the delivery state machine.

*Call graph*: calls 6 internal fn (_fail, _invite, _open_channel, _reschedule, _verify_team, _write); called by 1 (poll).


##### `SlackConnectInviter._verify_team`  (lines 490–498)

```
async def _verify_team(self) -> None
```

**Purpose**: This confirms that the configured Slack bot token belongs to the expected operator workspace. It prevents a misconfigured token from creating channels in the wrong Slack workspace.

**Data flow**: It asks Slack for the token’s team ID and compares it with the configured expected team ID. If they match, nothing changes; if not, it raises a configuration error before any channel is changed.

**Call relations**: SlackConnectInviter._advance calls this first for every delivery. The function creates a SlackConnectConfigError on mismatch, which _advance treats as a terminal failure for the row.

*Call graph*: called by 1 (_advance); 1 external calls (__init__).


##### `SlackConnectInviter._open_channel`  (lines 500–506)

```
async def _open_channel(self, delivery: _Delivery) -> str
```

**Purpose**: This obtains the Slack channel ID for a delivery, either by creating the channel or by finding the existing channel when Slack says the name is taken. It then saves that ID so future retries do not need to create again.

**Data flow**: It receives the delivery record, tries to create the deterministic channel name, recovers from a name-taken case by looking up the channel by name, writes the resulting channel ID to the database, and returns the ID.

**Call relations**: SlackConnectInviter._advance calls this when the delivery row lacks a channel ID. After Slack has supplied or revealed the ID, this function calls _write so the leased row records the progress.

*Call graph*: calls 1 internal fn (_write); called by 1 (_advance).


##### `SlackConnectInviter._invite`  (lines 508–525)

```
async def _invite(self, delivery: _Delivery, channel_id: str) -> str | None
```

**Purpose**: This sends the Slack Connect invitation, but only after checking whether an earlier uncertain attempt already produced a live invite or shared channel. This is the key guard against duplicate customer invitations.

**Data flow**: It receives the delivery and channel ID. If the row shows a previous invite attempt, it first looks for a live outgoing invite and then checks whether the channel is already externally shared. If neither proves success, it records invite_attempted_at, sends a new invitation to the customer email, saves the invitation ID, and returns it.

**Call relations**: SlackConnectInviter._advance calls this after a channel ID is available. Within its own flow it calls _write before making the risky invite call and _persist_invitation when it has an invitation ID to store.

*Call graph*: calls 2 internal fn (_persist_invitation, _write); called by 1 (_advance).


##### `SlackConnectInviter._persist_invitation`  (lines 527–529)

```
async def _persist_invitation(self, delivery: _Delivery, invitation_id: str) -> str
```

**Purpose**: This saves Slack’s invitation ID on the delivery row. It makes future retries and operator inspection know exactly which Slack invitation was created.

**Data flow**: It receives the delivery and the Slack invitation ID. It writes that ID into the leased database row and then returns the same ID to the caller.

**Call relations**: SlackConnectInviter._invite calls this after finding an existing live invitation or receiving a new invitation ID from Slack. It delegates the actual guarded database update to _write.

*Call graph*: calls 1 internal fn (_write); called by 1 (_invite).


##### `SlackConnectInviter._reschedule`  (lines 531–550)

```
async def _reschedule(self, delivery: _Delivery, error: SlackTransientError) -> None
```

**Purpose**: This puts a delivery back into the waiting state after a temporary Slack problem. It spaces retries out, using Slack’s own delay hint when available or exponential backoff otherwise.

**Data flow**: It receives the delivery and a temporary error. If the row has already reached the maximum attempt count, it marks the row failed. Otherwise it calculates a delay, clears the worker lease, sets the next attempt time, stores a redacted error message, and logs the retry.

**Call relations**: SlackConnectInviter._advance calls this when it catches SlackTransientError. _reschedule may call _fail if the retry limit is exhausted, or _write to store the pending retry schedule.

*Call graph*: calls 2 internal fn (_fail, _write); called by 1 (_advance); 1 external calls (timedelta).


##### `SlackConnectInviter._fail`  (lines 552–563)

```
async def _fail(self, delivery: _Delivery, error: Exception) -> None
```

**Purpose**: This marks a delivery as failed for human review. It is used when the problem is permanent or when repeated temporary failures have exceeded the retry limit.

**Data flow**: It receives the delivery and the error that stopped progress. It clears the worker lease and next retry time, stores a redacted error message, changes the state to failed, and logs the failure.

**Call relations**: SlackConnectInviter._advance calls this for terminal or unexpected errors. SlackConnectInviter._reschedule also calls it when a temporary error has happened too many times.

*Call graph*: calls 1 internal fn (_write); called by 2 (_advance, _reschedule).


##### `SlackConnectInviter._write`  (lines 565–574)

```
async def _write(self, onboard_claim_id: UUID, assignment: str, *values: object) -> None
```

**Purpose**: This performs a guarded database update for a delivery row only if the current worker still owns the lease. It prevents stale workers from overwriting progress made by another replica.

**Data flow**: It receives a claim ID, a SQL assignment fragment, and optional values for that assignment. It updates the row only where both the claim ID and worker ID match, refreshes updated_at, and returns nothing. If no row was updated, it raises a lease-lost error.

**Call relations**: _advance, _open_channel, _invite, _persist_invitation, _reschedule, and _fail all use this to record progress safely. If it raises _LeaseLost, the delivery flow backs away rather than writing over another worker’s claim.

*Call graph*: called by 6 (_advance, _fail, _invite, _open_channel, _persist_invitation, _reschedule); 1 external calls (__init__).


##### `slack_connect_from_env`  (lines 577–592)

```
def slack_connect_from_env(pool: asyncpg.Pool) -> SlackConnectInviter | None
```

**Purpose**: This builds the Slack Connect inviter from environment variables, or disables it when the feature switch is off. It fails loudly if the feature is enabled but required configuration is missing or malformed.

**Data flow**: It receives a database pool and reads the enable flag from the process environment. If disabled, it returns nothing. If enabled, it requires the Slack bot token and expected team ID, creates a SlackConnectClient and SlackConnectInviter, and gives the worker a name based on hostname and process ID. If the enable flag is not a boolean-like value, it raises an error.

**Call relations**: Startup code would call this to decide whether to launch the background Slack Connect poller. It calls _require_env for mandatory settings and constructs SlackConnectClient and SlackConnectInviter for the run loop.

*Call graph*: calls 1 internal fn (_require_env); 4 external calls (__init__, __init__, getpid, gethostname).


##### `_require_env`  (lines 595–599)

```
def _require_env(name: str) -> str
```

**Purpose**: This reads one required environment variable and reports a clear startup error if it is missing. It is used only when Slack Connect delivery has been enabled.

**Data flow**: It receives the environment variable name, reads its value from the process environment, and returns the value if it is present and non-empty. If not, it raises a runtime error explaining that the variable is required.

**Call relations**: slack_connect_from_env calls this for the bot token and expected Slack team ID. By failing during setup, it prevents a half-configured worker from reaching Slack later.

*Call graph*: called by 1 (slack_connect_from_env).


### `extensions/metronome/ufo_ext_metronome.py`

`domain_logic` · `scheduled jobs and chat tool handling`

This extension is the bridge between what happens inside UFO and the outside billing systems. Without it, settled model usage would not be reported to Metronome, daily seat counts would not be billed, owners could not grant or revoke seats through chat, and billing setup would not turn a saved card into an active Metronome plan.

It has four main jobs. The usage job gathers already-settled usage records and sends them to Metronome in batches. It uses stable transaction IDs, like putting the same barcode on the same parcel, so retries after a crash do not double-bill. The seat job sends one daily snapshot of how many members have seats. The approval job notices unseated members when included seats are full and asks the owner in chat whether to grant a billable overage seat. The billing activation job waits until Stripe says a payment method is saved, then creates or finds the matching Metronome customer and contract.

The file also defines owner-only chat tools: grant a seat, revoke a seat, list seats, and manage billing. Billing setup returns a short-lived Stripe portal link rather than storing card details itself. The extension carefully uses durable identities, such as workspace IDs and contract keys, so retrying a failed step resumes the same billing objects instead of creating duplicates.

#### Function details

##### `UsageShipper.run`  (lines 198–213)

```
async def run(self) -> None
```

**Purpose**: Sends one workspace's settled usage records to Metronome. It is designed so that if the process crashes after sending but before marking records done, the next run safely resends the same events without double-counting.

**Data flow**: It reads the Metronome token from the environment, finds this workspace's fixed backfill floor, then repeatedly asks the core system for pending usage exports. Each batch is turned into Metronome event dictionaries, posted to Metronome, logged, and only then acknowledged as shipped in the local store.

**Call relations**: The scheduled usage job enters through _ship, which creates a UsageShipper and calls this method. During the run it relies on _floor to decide how far back to look, _events to shape records for Metronome, _ingest to send them, and _require_env to fail early if the token is missing.

*Call graph*: calls 4 internal fn (_events, _floor, _ingest, _require_env); 1 external calls (log).


##### `UsageShipper._floor`  (lines 215–225)

```
async def _floor(self) -> datetime
```

**Purpose**: Finds or creates the earliest time from which this workspace's usage should be shipped. This prevents the first run from trying to backfill too far into the past.

**Data flow**: It reads a stored timestamp from the extension store. If none exists, it writes a new timestamp set to several days ago and returns it; if one exists, it parses that saved text back into a datetime.

**Call relations**: UsageShipper.run calls this before asking for pending usage exports. The returned time becomes the lower boundary for all later export reads.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._events`  (lines 227–246)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts internal usage export records into the event shape Metronome expects. It preserves important billing labels such as model, amount, price, and whether the workspace used its own provider key.

**Data flow**: It receives a tuple of UsageExport objects and reads the workspace ID from the context. For each export it builds a dictionary with a stable transaction ID, timestamp, customer ID, event type, and string properties; the result is a list ready to send.

**Call relations**: UsageShipper.run calls this just before sending a batch. It uses _rfc3339 so timestamps are formatted consistently for Metronome.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 249–250)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the small scheduled-job wrapper for usage shipping. It exists so the extension manifest can point the scheduler at a simple function.

**Data flow**: It receives an ExtensionContext for one workspace, creates a UsageShipper with that context and the configured ingest transport, and starts the shipper's run.

**Call relations**: The manifest registers this as the handler for the usage shipping job. It hands the real work to UsageShipper.run.

*Call graph*: 1 external calls (__init__).


##### `SeatShipper.run`  (lines 263–279)

```
async def run(self) -> None
```

**Purpose**: Sends Metronome one daily snapshot of how many seats a workspace is using. This lets seat billing be based on daily counts rather than individual seat-change events.

**Data flow**: It reads the Metronome token, checks whether today's snapshot was already shipped, ensures the workspace has a seat limit and included-seat count, reads the current seat snapshot, posts one event to Metronome, logs it, and stores today's date as shipped.

**Call relations**: _ship_seats creates a SeatShipper and calls this method on the daily seat schedule. It uses _event to build the event, _ingest to send it, and _require_env to require billing credentials.

*Call graph*: calls 3 internal fn (_event, _ingest, _require_env); 3 external calls (__init__, now, log).


##### `SeatShipper._event`  (lines 281–292)

```
def _event(self, snapshot: SeatSnapshot, today: str) -> dict[str, object]
```

**Purpose**: Builds the single Metronome event for a workspace's daily seat count. The event ID includes the workspace and date so retries of the same day are recognized as the same snapshot.

**Data flow**: It receives a SeatSnapshot and a date string, reads the workspace ID, and returns a dictionary containing transaction ID, customer ID, event type, current timestamp, seat count, and seat limit.

**Call relations**: SeatShipper.run calls this after it has read the seat snapshot. It uses _rfc3339 to format the timestamp before _ingest sends the event.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 1 external calls (now).


##### `_ship_seats`  (lines 295–296)

```
async def _ship_seats(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled-job wrapper for daily seat-count shipping.

**Data flow**: It receives an ExtensionContext, creates a SeatShipper using the shared ingest transport, and runs it.

**Call relations**: The manifest registers this as the seat shipping job handler. It delegates the actual work to SeatShipper.run.

*Call graph*: 1 external calls (__init__).


##### `SeatApprovals.run`  (lines 312–337)

```
async def run(self) -> None
```

**Purpose**: Finds unseated workspace members who need the owner's approval because included seats are full. It asks the owner in chat once per member instead of silently granting billable seats.

**Data flow**: It reads the current seat snapshot, stops if there are still included seats available, then checks each unseated member for a stored ask marker. For each never-asked member it finds the owner's private conversation, invokes the agent with an approval prompt, and records that the ask was made.

**Call relations**: _ask_seat_approvals runs this on a schedule. It uses the core Seats API for the snapshot and owner_conversation to find where to send the chat request.

*Call graph*: 3 external calls (__init__, now, owner_conversation).


##### `_ask_seat_approvals`  (lines 340–341)

```
async def _ask_seat_approvals(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled-job wrapper that starts the seat approval scan.

**Data flow**: It receives an ExtensionContext, creates a SeatApprovals object, and runs it.

**Call relations**: The manifest registers this as the seat approval job handler. SeatApprovals.run does the actual scan and chat invocation.

*Call graph*: 1 external calls (__init__).


##### `BillingConfig.from_env`  (lines 360–379)

```
def from_env(cls) -> 'BillingConfig'
```

**Purpose**: Loads the required Stripe and Metronome billing settings from environment variables. It refuses to proceed if any required setting is missing, so the system does not create half-finished provider records.

**Data flow**: It reads four environment variables, collects all missing names, raises an error if anything is absent, and otherwise returns a validated BillingConfig object.

**Call relations**: BillingActivation.run and manage_billing use this before making provider calls. That makes configuration checks happen at the edge of each billing operation.


##### `BillingActivation.run`  (lines 414–441)

```
async def run(self) -> None
```

**Purpose**: Turns a workspace's pending billing setup into an active Metronome plan once Stripe has a saved payment method. It resumes safely if a previous run stopped halfway.

**Data flow**: It reads the workspace billing record, exits if there is nothing pending, loads billing configuration, checks Stripe for a default payment method, creates or finds the Metronome customer if needed, creates or finds the Metronome contract if needed, stores each new ID, and finally notifies the owner.

**Call relations**: _activate_billing creates BillingActivation and calls this on the billing activation schedule. It coordinates _billing_record, _has_default_payment_method, _metronome_customer, _metronome_contract, _store, and _notify.

*Call graph*: calls 7 internal fn (_notify, _store, _billing_record, _contract_key, _has_default_payment_method, _metronome_contract, _metronome_customer).


##### `BillingActivation._store`  (lines 443–445)

```
async def _store(self, record: BillingRecord) -> BillingRecord
```

**Purpose**: Saves the current billing record for a workspace. It is used after each successful provider step so the next job tick can continue from the right place.

**Data flow**: It receives a BillingRecord, converts it into JSON-friendly data, writes it under the billing key in the extension store, and returns the same record.

**Call relations**: BillingActivation.run calls this after provider IDs are discovered or created. BillingActivation._notify also calls it to record that the owner was told and activation is complete.

*Call graph*: called by 2 (_notify, run); 1 external calls (model_dump).


##### `BillingActivation._notify`  (lines 447–465)

```
async def _notify(self, record: BillingRecord) -> None
```

**Purpose**: Tells the workspace owner in chat that billing is now active. It records activation only after the notification has been accepted for delivery.

**Data flow**: It finds the owner's private conversation, sends a short internal prompt to the agent with an idempotency key, updates the billing record with the activation time, stores it, and logs the activation. If no owner conversation exists, it leaves the record pending.

**Call relations**: BillingActivation.run calls this after the Metronome customer and contract exist. It uses owner_conversation to find the destination and _store to persist the final activated state.

*Call graph*: calls 1 internal fn (_store); called by 1 (run); 4 external calls (now, model_copy, log, owner_conversation).


##### `_activate_billing`  (lines 468–469)

```
async def _activate_billing(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled-job wrapper for billing activation.

**Data flow**: It receives an ExtensionContext, creates a BillingActivation object with the configured billing transport, and runs it.

**Call relations**: The manifest registers this as the billing activation job handler. BillingActivation.run contains the step-by-step activation workflow.

*Call graph*: 1 external calls (__init__).


##### `_billing_record`  (lines 472–474)

```
async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None
```

**Purpose**: Reads the saved billing state for a workspace, if one exists. This is the local memory of which Stripe and Metronome objects belong to the workspace.

**Data flow**: It reads the billing key from the extension store. If nothing is stored it returns None; otherwise it validates the stored data into a BillingRecord object.

**Call relations**: BillingActivation.run uses it to find pending work. The setup, status, and portal billing helpers use it to know whether billing has been started and which provider IDs to query.

*Call graph*: called by 4 (run, _billing_portal, _billing_setup, _billing_status).


##### `_contract_key`  (lines 477–481)

```
def _contract_key(workspace_id: UUID) -> str
```

**Purpose**: Creates the permanent Metronome contract identity for a workspace. This stable key lets later retries or status checks recognize the exact plan this workspace bought.

**Data flow**: It receives a workspace UUID and returns a string built from a fixed prefix plus that UUID.

**Call relations**: BillingActivation.run passes this key when creating or finding the contract. _billing_status uses the same key so it reports only this workspace's own plan.

*Call graph*: called by 2 (run, _billing_status).


##### `grant_seat`  (lines 506–512)

```
async def grant_seat(ctx: ToolContext, args: GrantSeatInput) -> ToolResult
```

**Purpose**: Chat tool that grants a seat to a workspace member by email. It is owner-only and returns the updated seat picture so the agent can explain the result.

**Data flow**: It receives the tool context and an email argument, verifies the speaker can change seats, opens a transaction, grants the seat, reads the new snapshot, and returns that snapshot as JSON text.

**Call relations**: The manifest exposes this as the grant_seat tool. It depends on _owner_seats for permission checking and _snapshot_result for the response format.

*Call graph*: calls 2 internal fn (_owner_seats, _snapshot_result).


##### `revoke_seat`  (lines 515–525)

```
async def revoke_seat(ctx: ToolContext, args: RevokeSeatInput) -> ToolResult
```

**Purpose**: Chat tool that removes a member's seat by email. It also records that this member has already been considered, so the approval job does not immediately ask about them again.

**Data flow**: It receives the tool context and email, verifies owner permission, revokes the seat inside a transaction, reads the updated snapshot, writes an approval marker for that email, and returns the snapshot as JSON text.

**Call relations**: The manifest exposes this as the revoke_seat tool. It uses _owner_seats for authorization and _snapshot_result for the result returned to the agent.

*Call graph*: calls 2 internal fn (_owner_seats, _snapshot_result); 1 external calls (now).


##### `list_seats`  (lines 528–532)

```
async def list_seats(ctx: ToolContext, args: ListSeatsInput) -> ToolResult
```

**Purpose**: Chat tool that shows the current seat limit, included seats, overage count, and member seating status.

**Data flow**: It receives the tool context, reads a SeatSnapshot for the current workspace inside a transaction, and returns the snapshot as JSON text.

**Call relations**: The manifest exposes this as the list_seats tool. It uses the core Seats API to read data and _snapshot_result to shape the answer.

*Call graph*: calls 1 internal fn (_snapshot_result); 1 external calls (__init__).


##### `manage_billing`  (lines 535–544)

```
async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Chat tool for owner billing actions: setup, status, or portal. It is the single chat-facing doorway into Stripe and Metronome billing workflows.

**Data flow**: It receives a tool context and an action, verifies the request is from the owner in their private conversation, loads billing configuration, then routes setup to _billing_setup, status to _billing_status, or portal to _billing_portal.

**Call relations**: The manifest exposes this as the manage_billing tool. It relies on _owner_billing to protect billing access before any provider call is made.

*Call graph*: calls 4 internal fn (_billing_portal, _billing_setup, _billing_status, _owner_billing).


##### `_owner_billing`  (lines 547–558)

```
async def _owner_billing(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Enforces that billing actions are only done by the workspace owner in their own private conversation. This prevents other members or shared channels from creating billing records or seeing billing links.

**Data flow**: It examines the tool context for a speaking member, checks that the audience is the same member, asks whether the speaker is the owner, and returns the extension context if all checks pass. Otherwise it raises a clear error.

**Call relations**: manage_billing calls this before dispatching setup, status, or portal. Its job is to stop unauthorized billing work before Stripe or Metronome are contacted.

*Call graph*: calls 1 internal fn (speaker_is_owner); called by 1 (manage_billing).


##### `_billing_setup`  (lines 561–595)

```
async def _billing_setup(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Starts billing setup for a workspace and returns a Stripe portal link where the owner can save a payment method. It records the intended plan before returning the link so the activation job can finish later.

**Data flow**: It reads any existing billing record. If none exists, it creates or reuses a Stripe customer, builds a BillingRecord with the package and contract start time, and stores it. Then it creates a payment-method portal session and returns the URL plus basic billing identifiers.

**Call relations**: manage_billing calls this for the setup action. It uses _billing_record, _stripe_customer, _portal_session, _text_result, and logging; the later BillingActivation job follows up on the stored record.

*Call graph*: calls 4 internal fn (_billing_record, _portal_session, _stripe_customer, _text_result); called by 1 (manage_billing); 3 external calls (__init__, now, log).


##### `_billing_status`  (lines 598–626)

```
async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Reports what the billing providers currently say about a workspace. It checks Stripe for a saved payment method and Metronome for this workspace's own contract.

**Data flow**: It reads the billing record. If none exists, it returns configured false. Otherwise it asks Stripe whether a payment method is on file, asks Metronome for the matching contract when there is a Metronome customer ID, and returns a JSON summary.

**Call relations**: manage_billing calls this for the status action. It uses _has_default_payment_method for Stripe truth and _contract_for with _contract_key for Metronome truth.

*Call graph*: calls 5 internal fn (_billing_record, _contract_for, _contract_key, _has_default_payment_method, _text_result); called by 1 (manage_billing).


##### `_billing_portal`  (lines 629–637)

```
async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Returns a fresh Stripe customer portal link for an already-set-up workspace. The link lets the owner manage payment methods, billing details, and invoices.

**Data flow**: It reads the billing record, raises an error if setup has never happened, creates a general portal session for the stored Stripe customer, and returns the URL as JSON text.

**Call relations**: manage_billing calls this for the portal action. It uses _billing_record to find the customer and _portal_session to create the Stripe link.

*Call graph*: calls 3 internal fn (_billing_record, _portal_session, _text_result); called by 1 (manage_billing).


##### `_owner_seats`  (lines 640–645)

```
async def _owner_seats(ctx: ToolContext) -> Seats
```

**Purpose**: Checks that a seat-changing tool call comes from the workspace owner. If allowed, it returns the Seats helper for the current workspace.

**Data flow**: It inspects the tool context for a speaking member, asks whether that speaker is the owner, raises errors for invalid callers, and returns a Seats object tied to the workspace.

**Call relations**: grant_seat and revoke_seat call this before changing any seat state. It keeps the permission rule in one place.

*Call graph*: calls 1 internal fn (speaker_is_owner); called by 2 (grant_seat, revoke_seat); 1 external calls (__init__).


##### `_snapshot_result`  (lines 648–662)

```
def _snapshot_result(snapshot: SeatSnapshot) -> ToolResult
```

**Purpose**: Turns a seat snapshot into the JSON-style tool response the agent can read. It includes both summary counts and per-member status.

**Data flow**: It receives a SeatSnapshot, calculates billed overage seats when included-seat data exists, builds a dictionary with limits and members, and passes it to _text_result.

**Call relations**: grant_seat, revoke_seat, and list_seats use this to return seat information in a consistent format.

*Call graph*: calls 1 internal fn (_text_result); called by 3 (grant_seat, list_seats, revoke_seat).


##### `_text_result`  (lines 665–666)

```
def _text_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary as a text ToolResult. This is how the extension returns structured information to the agent using a plain text payload.

**Data flow**: It receives a dictionary, converts it to a JSON string, puts that string in TextContent, and returns a ToolResult containing it.

**Call relations**: Seat and billing helpers call this whenever they need to return data from a tool. It is the final packaging step before the agent sees the response.

*Call graph*: called by 4 (_billing_portal, _billing_setup, _billing_status, _snapshot_result); 3 external calls (__init__, __init__, dumps).


##### `_require_env`  (lines 698–702)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads a required environment variable and fails clearly if it is missing. This prevents scheduled shipping jobs from quietly running without credentials.

**Data flow**: It receives an environment variable name, looks it up, returns the value if present, and raises a RuntimeError if empty or missing.

**Call relations**: UsageShipper.run and SeatShipper.run call this before posting events to Metronome.

*Call graph*: called by 2 (run, run).


##### `_stripe_customer`  (lines 705–722)

```
async def _stripe_customer(config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or reuses the one Stripe Customer for a workspace. A stable idempotency key tells Stripe that repeated attempts are the same request, not requests for new customers.

**Data flow**: It receives billing config, a workspace ID, and an optional test transport. It posts customer details to Stripe, then extracts and returns the customer ID from the response.

**Call relations**: _billing_setup calls this when no local billing record exists. It uses _stripe for the HTTP request and _as_str to validate the returned ID.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_setup).


##### `_portal_session`  (lines 725–741)

```
async def _portal_session(config: BillingConfig, customer_id: str, flow: str | None, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates a short-lived Stripe Customer Portal URL. During setup it can be narrowed to only updating a payment method; later it can open the general billing portal.

**Data flow**: It receives billing config, a Stripe customer ID, an optional flow type, and transport. It posts a portal-session request to Stripe and returns the URL from the response.

**Call relations**: _billing_setup calls this to give the owner a card-saving link. _billing_portal calls it to give the owner a general billing-management link.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 2 (_billing_portal, _billing_setup).


##### `_has_default_payment_method`  (lines 744–754)

```
async def _has_default_payment_method(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> bool
```

**Purpose**: Asks Stripe whether a customer has a default payment method saved. This is the gate that decides whether a pending billing setup can become an active plan.

**Data flow**: It receives billing config, a Stripe customer ID, and transport, fetches the customer from Stripe, checks the invoice settings for a default payment method, and returns true or false.

**Call relations**: BillingActivation.run uses this before creating Metronome billing objects. _billing_status uses it to report card-on-file status.

*Call graph*: calls 1 internal fn (_stripe); called by 2 (run, _billing_status).


##### `_stripe`  (lines 757–775)

```
async def _stripe(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, data: dict[str, str] | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Performs authenticated HTTP requests to Stripe and turns failed responses into clear StripeError exceptions. It centralizes Stripe headers, timeout, API version, and optional idempotency keys.

**Data flow**: It receives config, HTTP method, path, optional form data, optional idempotency key, and optional transport. It sends the request to Stripe, raises on non-success, and returns the parsed JSON body.

**Call relations**: _stripe_customer, _portal_session, and _has_default_payment_method all use this instead of making Stripe calls directly.

*Call graph*: called by 3 (_has_default_payment_method, _portal_session, _stripe_customer); 2 external calls (__init__, AsyncClient).


##### `_metronome_customer`  (lines 778–823)

```
async def _metronome_customer(config: BillingConfig, alias: str, stripe_customer_id: str, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Finds or creates the Metronome customer for a workspace. It ties the Metronome customer to the workspace ID used on usage events and to the Stripe customer used for charging.

**Data flow**: It first looks for a Metronome customer with the workspace alias. If found, it returns that ID. Otherwise it posts a create request with ingest aliases and Stripe billing configuration; if a conflict happens, it looks up the alias again and returns the existing customer.

**Call relations**: BillingActivation.run calls this after Stripe has a payment method and before creating a contract. It uses _customer_by_alias for lookup and _metronome for provider requests.

*Call graph*: calls 2 internal fn (_customer_by_alias, _metronome); called by 1 (run); 1 external calls (__init__).


##### `_customer_by_alias`  (lines 826–835)

```
async def _customer_by_alias(config: BillingConfig, alias: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Looks up a Metronome customer by the workspace ingest alias. This lets the system recover the correct customer even if a previous create succeeded but the local record was not saved.

**Data flow**: It receives config, an alias, and transport, asks Metronome for customers matching that alias, and returns the first customer ID if one is present or None if not.

**Call relations**: _metronome_customer uses this before creating a customer and again after a create conflict.

*Call graph*: calls 1 internal fn (_metronome); called by 1 (_metronome_customer).


##### `_metronome_contract`  (lines 838–874)

```
async def _metronome_contract(config: BillingConfig, customer_id: str, record: BillingRecord, uniqueness_key: str, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Finds or creates the Metronome contract that represents the workspace's purchased plan. It uses a stable uniqueness key so retries point to the same contract.

**Data flow**: It first searches for an existing contract with the expected key. If none exists, it posts a contract creation request using the stored package and start time. If Metronome reports a conflict, it searches again and returns the reconciled contract ID.

**Call relations**: BillingActivation.run calls this after the Metronome customer exists. It uses _contract_for to avoid duplicates, _metronome to call the provider, and _rfc3339 to format the contract start time.

*Call graph*: calls 3 internal fn (_contract_for, _metronome, _rfc3339); called by 1 (run); 1 external calls (__init__).


##### `_contract_for`  (lines 877–900)

```
async def _contract_for(config: BillingConfig, customer_id: str, uniqueness_key: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Finds this workspace's live Metronome contract on a customer by matching the stored uniqueness key. It does not assume the first contract on the customer belongs to this workspace.

**Data flow**: It receives config, customer ID, uniqueness key, and transport, asks Metronome to list contracts for the customer, scans the returned contracts for the matching key, and returns that contract ID or None.

**Call relations**: _metronome_contract uses this before creation and after conflicts. _billing_status uses it to report whether the workspace's own plan is active.

*Call graph*: calls 1 internal fn (_metronome); called by 2 (_billing_status, _metronome_contract).


##### `_metronome`  (lines 903–923)

```
async def _metronome(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, body: dict[str, object] | None=None, params: dict[str, str] | None=None, idempotency_key
```

**Purpose**: Performs authenticated HTTP requests to Metronome's management APIs and turns provider failures into clear exceptions. It treats conflict responses specially because they often mean a stable identity already exists.

**Data flow**: It receives config, method, path, optional JSON body, optional query parameters, optional idempotency key, and transport. It sends the request, raises MetronomeConflict on HTTP 409, raises MetronomeError on other failures, and returns parsed JSON on success.

**Call relations**: _customer_by_alias, _metronome_customer, _contract_for, and _metronome_contract all use this shared Metronome request helper.

*Call graph*: called by 4 (_contract_for, _customer_by_alias, _metronome_contract, _metronome_customer); 3 external calls (__init__, __init__, AsyncClient).


##### `_as_str`  (lines 926–930)

```
def _as_str(value: object, field: str) -> str
```

**Purpose**: Validates that a provider response field is a non-empty string. It gives a clear error when Stripe returns an unexpected shape.

**Data flow**: It receives an arbitrary value and a human-readable field name. If the value is a non-empty string, it returns it; otherwise it raises a ValueError naming the missing field.

**Call relations**: _stripe_customer uses this for Stripe customer IDs, and _portal_session uses it for Stripe portal URLs.

*Call graph*: called by 2 (_portal_session, _stripe_customer).


##### `_ingest`  (lines 933–941)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Posts usage or seat events to Metronome's ingest endpoint. This is the shared sender for metered events.

**Data flow**: It receives a bearer token, a list of event dictionaries, and optional transport. It sends the list as JSON to Metronome with authorization and raises MetronomeError if the post fails.

**Call relations**: UsageShipper.run uses this for usage batches, and SeatShipper.run uses it for daily seat snapshots.

*Call graph*: called by 2 (run, run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 944–946)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: Formats a datetime as an ISO/RFC3339-style timestamp for provider APIs. If the datetime has no timezone, it treats it as UTC.

**Data flow**: It receives a datetime, adds UTC timezone information when missing, and returns the ISO-formatted string.

**Call relations**: UsageShipper._events and SeatShipper._event use it for ingest timestamps. _metronome_contract uses it for the contract start time sent to Metronome.

*Call graph*: called by 3 (_event, _events, _metronome_contract); 1 external calls (replace).


##### `manifest`  (lines 949–999)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the UFO host: its tools, scheduled jobs, prompt instructions, and credential slot. This is how the rest of the system discovers what the Metronome extension can do.

**Data flow**: It builds and returns a Manifest containing tool definitions, job schedules and handlers, workspace candidate selectors, prompt sections for the agent, and a credential slot for Anthropic BYOK usage.

**Call relations**: The extension loader calls this to register the extension. The returned manifest connects user-facing tools to functions like grant_seat and manage_billing, and scheduled jobs to wrappers like _ship, _ship_seats, _ask_seat_approvals, and _activate_billing.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, metered_workspaces, member_workspaces).


### Self-improvement model loop
The self-improvement loop mines failure examples, proposes prompt revisions, replays old tasks, and gates candidates before promotion.

### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled background self-improvement tick`

This file is the careful “quality gate” for the self-improvement extension. Its job is not to rewrite an agent directly. Instead, it periodically reviews recent agent trajectories, meaning records of what an agent did in past conversations, and asks whether a better prompt should be proposed.

The loop works agent by agent. For each agent, it keeps at most one candidate prompt tied to the agent’s current prompt digest, which is like a fingerprint for the current prompt version. If there is already a candidate for that exact prompt version, it continues testing it. If a candidate was already rejected or promoted, it stays quiet until the agent’s prompt digest changes, so the same idea is not proposed again and again.

When opening a new candidate, it groups examples into task classes, asks a PromptProposer for a possible improved prompt, and stores the candidate in the extension’s scoped store. Later ticks replay and grade held-out examples, which are examples saved for testing rather than training. A candidate must pass for a configured number of consecutive ticks before this file calls the platform’s proposal system. That proposal still needs human or governance approval; this file only asks for a change, it does not apply one. Think of it like a lab test that must pass twice before a change request reaches the review desk.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: Runs one scheduled pass of the self-improvement loop. It collects all available trajectories, groups them by agent, and advances the improvement process separately for each agent.

**Data flow**: It starts by asking the extension context for stored trajectories. It groups those records by agent, then sends each agent’s bundle of trajectories onward for candidate opening or testing. It returns nothing; its effect is to drive the stored candidate state and possible proposal creation through later steps.

**Call relations**: This is the top-level method for this cron-style worker. It uses _by_agent to split the work into one pile per agent, then calls ImproveCron._advance for each pile so each agent’s prompt candidate can move forward independently.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Moves one agent one step through the self-improvement process. It either finds or opens a candidate prompt for the agent’s current prompt version, then tests that candidate if one is available.

**Data flow**: It receives an agent ID and that agent’s trajectories. It reads the current prompt digest from the first trajectory and builds the store key where this agent’s candidate is saved. It asks for an active candidate or a new one; if none exists, it stops. If a candidate exists, it sends it to the gate that evaluates whether it should be rejected, kept under test, or proposed.

**Call relations**: ImproveCron.run calls this once per agent after grouping trajectories. This method is the small bridge between candidate discovery in ImproveCron._active_or_open and candidate testing in ImproveCron._gate.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: Finds the current candidate prompt for an agent, or opens a new one if it is safe and useful to do so. It prevents repeated proposals for the same already-finished candidate.

**Data flow**: It receives the store key, the agent’s current prompt digest, and the agent’s trajectories. First it checks the extension store for a saved candidate. If that candidate belongs to the same prompt digest and is still evaluating, it returns it; if it was already promoted or rejected, it returns nothing. If no usable candidate exists, it looks for task classes in the trajectories and asks the proposer for a new prompt based on the current prompt and one task class. If a proposal is produced, it saves a new CandidateState and returns it.

**Call relations**: ImproveCron._advance calls this before any evaluation happens. It relies on task_classes to find useful groups of examples, asks the PromptProposer to create a candidate, and creates a CandidateState record so later ticks can continue from the same place.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Tests a candidate prompt and decides its next state. A candidate can fail and be rejected, pass once and keep waiting, or pass enough times to become a formal change proposal.

**Data flow**: It receives the agent, store key, current prompt digest, candidate state, and trajectories. It gathers two sets of test examples: the candidate’s own held-out examples and held-out examples from other task classes. It asks the evaluator to compare the candidate prompt against the current prompt on those examples. If the candidate fails, it saves it as rejected. If it passes but has not passed enough consecutive ticks, it saves the higher pass count and keeps it evaluating. If it reaches the stability threshold, it creates an AgentChange proposal and saves the candidate as promoted with the proposal ID.

**Call relations**: ImproveCron._advance calls this only after a candidate has been found or opened. This function calls _held_out to rebuild the candidate’s test examples, uses task_classes to collect wider safety checks, calls the evaluator for the verdict, uses the platform proposal API when promotion is allowed, and delegates all state writes to ImproveCron._save.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: Writes an updated candidate state back to the extension store. It keeps the saved record in sync after a candidate is rejected, continues evaluating, or is promoted.

**Data flow**: It receives the store key, the old candidate, and the new status information. It makes a copied candidate record with the updated status, pass count, and optional proposal ID. It serializes that record into JSON-friendly data and stores it under the same key. It returns nothing; the store is the thing that changes.

**Call relations**: ImproveCron._gate calls this whenever the evaluation result changes a candidate’s state. This keeps the decision-making code in _gate separate from the persistence step, like handing a completed form to a clerk for filing.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: Splits a mixed list of trajectories into separate groups for each agent. This lets the self-improvement loop judge each agent using only that agent’s own history.

**Data flow**: It receives a tuple of trajectory records. It reads each trajectory’s agent ID, collects records with the same agent ID together, and returns a mapping from each agent ID to that agent’s tuple of trajectories. It does not modify the trajectories.

**Call relations**: ImproveCron.run uses this at the start of each tick. The grouped result becomes the input to ImproveCron._advance, so every later step works on one agent at a time.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: Rebuilds the candidate’s held-out test examples from the available trajectories. It only includes examples that are still considered bad or useful for testing improvement.

**Data flow**: It receives all trajectories for an agent and a tuple of conversation IDs that were saved when the candidate was opened. It looks up each saved conversation ID in the current trajectories. If the trajectory still exists, it passes it to bad_trajectory, which identifies a failed or problematic example. When such an example is found, it adds the task example to the output tuple.

**Call relations**: ImproveCron._gate calls this before evaluation. It supplies the evaluator with the candidate-specific test cases, while bad_trajectory provides the judgment about which stored conversations should become concrete examples for grading.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).


### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus building`

This file is a filter and organizer for the self-improvement loop. The project cannot rely on users explicitly saying “that went badly,” so it uses a practical signal that is visible in the transcript: a tool call returned an error. Each errored conversation becomes a task example, with the original user request, the full message history, and a short description of what went wrong.

The file then groups these examples into task classes. A class is named after the failed tool, such as “tool:search” or “tool:browser”. This matters because improvements should be aimed at the kind of failure that happened, not mixed together randomly.

Each task class is split into two parts. The “mine” set is used by the proposer to learn from examples and suggest an improvement. The “held_out” set is kept separate for replay and grading, so a candidate improvement is not judged on the exact conversations that inspired it. This is like studying with practice questions, then taking a different quiz to see if you really learned the pattern.

Very small classes are discarded because they cannot be split fairly. The remaining classes are sorted so the biggest, most useful groups come first.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal phase`

This file is part of a self-improvement loop for an AI agent. Its job is to propose a better system prompt: the instruction text that tells the agent how to behave. When the system has found a group of similar tasks where the agent ran into trouble, this file packages that evidence and asks another model to suggest a small, careful rewrite.

The main worker is `PromptProposer`. It receives the current prompt and a `TaskClass`, which is a named group of tasks plus examples of what went wrong. If there are no mined examples, it stops immediately, because there is no evidence to learn from. Otherwise, it builds a clear request for the model: here is the task class, here is the current system prompt, and here are a few short examples showing the request and the problem.

The model is told to return only the full revised prompt, not an explanation. The result is then cleaned up, especially in case the model wrapped it in Markdown code fences. If the model returns an empty answer, or the exact same prompt as before, the file treats that as no useful proposal. Otherwise it returns a `PromptCandidate`, which is like a draft amendment ready for later checking. Without this file, the self-improvement system could identify weak spots but would not have a focused way to turn them into prompt changes.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This function tries to create one improved system prompt for a task class the agent struggled with. It uses the evidence in that task class to ask a model for a careful rewrite, then rejects empty or unchanged answers.

**Data flow**: It starts with the current system prompt and a task class. If the task class has no mined examples, it returns `None`. Otherwise it builds a user-facing prompt with `_prompt`, sends that plus the proposer instructions to the model, cleans the model’s text with `_clean`, compares it with the original prompt, and returns either `None` or a `PromptCandidate` containing the task class name and the revised prompt.

**Call relations**: This is the main entry point in the file. When another part of the self-improvement system wants a prompt revision, it calls this method. During that flow, this method calls `PromptProposer._prompt` to assemble the evidence for the model, creates a `Message` to send to the model, calls `_clean` to tidy the model’s answer, and finally creates a `PromptCandidate` if the answer is worth considering.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This function writes the actual request that will be shown to the model. It lays out the task class, the current system prompt, and a limited set of examples so the model has enough context to suggest a useful rewrite.

**Data flow**: It receives the current prompt and the task class. It takes only up to the configured maximum number of examples, trims each request and problem to the configured character limit, formats them as numbered examples, and returns one complete text block asking for the full revised system prompt.

**Call relations**: `PromptProposer.propose` calls this helper just before sending work to the model. It does not talk to the model itself; it prepares the evidence packet that `propose` wraps in a `Message` and passes along.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This function tidies the model’s returned text so the rest of the system can compare and store it as a plain prompt. Its main job is to remove extra whitespace and strip Markdown code fences if the model added them despite being told not to.

**Data flow**: It receives raw text from the model. It trims leading and trailing whitespace, checks whether the answer starts with a triple-backtick code block, removes the opening and closing fence when present, trims again, and returns the cleaned prompt text.

**Call relations**: `PromptProposer.propose` calls this immediately after the model responds. The cleaned result is then checked for being empty or unchanged before `propose` decides whether to return a new `PromptCandidate`.

*Call graph*: called by 1 (propose).


### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `self-improvement evaluation`

This file is the evidence-gathering part of a self-improvement system. When the system invents a new prompt, it should not trust it just because it sounds better. It must prove that the new prompt improves answers on relevant held-out tasks and does not make the agent worse elsewhere.

The main class, CandidateEvaluation, compares two “arms”: the current prompt and the candidate prompt. For each saved task example, it replays the agent using archived messages and tool results, so the task setup stays the same. The important idea is a fair test: like asking two cooks to make the same recipe with the same ingredients, changing only the recipe card.

After each replay, the file asks a separate judge model to grade the final answer. The judge must return a small JSON result saying whether the answer was accepted. Those accepted/not-accepted results become OutcomeLabel records, each marked as either “present” for the candidate prompt or “absent” for the current prompt.

Finally, the labels are sent to a two-stage gate. The gate checks local improvement on the candidate’s target task type and also checks global safety on other task types. Without this file, the system could promote prompt changes based on guesswork instead of repeatable comparisons.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: This is the top-level test for a candidate prompt. It compares the candidate prompt against the current prompt on local held-out examples and, separately, on broader global examples, then asks the gate whether the candidate should pass.

**Data flow**: It receives the candidate prompt, the current prompt, and two sets of saved task examples. It turns each set into success/failure labels by replaying the tasks and judging the answers. It then gives the local and global labels to the gate, which returns a GateVerdict saying whether the candidate is good enough.

**Call relations**: This method starts the evaluation flow. It calls CandidateEvaluation._labels twice, once for the candidate’s target area and once for broader safety checks. When both batches of labels are ready, it hands them to two_stage_gate, which makes the final accept-or-reject decision.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: This method creates the raw comparison evidence for a set of saved tasks. For every task, it runs both prompts and records whether each resulting answer was accepted.

**Data flow**: It receives the candidate prompt, the current prompt, and a tuple of held-out task examples. For each example, it first replays the task with the current prompt and then with the candidate prompt. Each final answer is sent to CandidateEvaluation._accepts for judging, and the method returns a tuple of OutcomeLabel values showing which prompt was used and whether it succeeded.

**Call relations**: CandidateEvaluation.evaluate calls this method to build the local and global evidence. Inside, it creates a ReplayEvaluation object to rerun saved tasks under a prompt, then calls CandidateEvaluation._accepts to turn each generated answer into a simple accepted-or-not result.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: This method asks the judge model whether one answer satisfies one user request. It turns the judge’s text response into a plain true-or-false result.

**Data flow**: It receives the original request and the answer produced by replay. It sends both to the judge model with instructions to return only JSON, then looks for a JSON object in the judge’s response. If the JSON can be read and contains {"accepted": true}, it returns true; if the response is missing, malformed, or says anything else, it returns false.

**Call relations**: CandidateEvaluation._labels calls this after each replayed answer is produced. This method builds the judge message using Message and parses the judge’s response with json.loads, providing the success/failure signal that later feeds into the gate decision.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation`

This file is the safety gate for prompt self-improvement. Imagine trying two recipes with only a handful of taste tests: if the new recipe wins 3 out of 4 times, that might be real, or it might be chance. This gate asks, “Do we have enough evidence that the candidate prompt improves acceptance, and does it avoid hurting other kinds of tasks?”

The inputs are replay results. Each result says whether the candidate prompt was present, and whether the judge accepted the answer. The file first counts wins and totals for the candidate side and the current-prompt side. It then estimates the “lift,” meaning the candidate’s acceptance rate minus the current prompt’s acceptance rate.

The important part is that it does not trust the raw difference alone. It builds a confidence range, which is a cautious estimate of how much uncertainty remains. For the local task class, the pessimistic lower end of the lift must still clear a minimum improvement floor. Each side also needs enough replay examples.

There is a second, global check too. A candidate may win on the task it was designed for but damage other task classes. This file blocks promotion only when there is confident evidence of that broader harm. If the global evidence is too thin or merely noisy, it does not block the candidate.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This function gives a cautious lower estimate of a success rate. It answers: “Given this many accepted results out of this many tries, how low might the true success rate reasonably be?”

**Data flow**: It takes an accepted count, a total count, and an optional confidence setting. If there are no examples, it returns 0. Otherwise it computes the observed success rate, adjusts it for uncertainty, and returns the lower end of the Wilson confidence interval, never below 0.

**Call relations**: The lift calculations call this when they need the pessimistic side of a success rate. It uses square root math as part of the confidence calculation, then hands the cautious bound back to the lift functions.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This function gives a cautious upper estimate of a success rate. It answers: “Given these results, how high might the true success rate reasonably be?”

**Data flow**: It takes an accepted count, a total count, and an optional confidence setting. If there are no examples, it returns 1, meaning the rate is completely uncertain. Otherwise it computes the observed success rate, adjusts it for uncertainty, and returns the upper end of the Wilson confidence interval, never above 1.

**Call relations**: The lift calculations call this when they need the optimistic side of a success rate. It pairs with wilson_lower_bound so the code can reason about both best-case and worst-case interpretations of the replay results.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: This function estimates the pessimistic lower bound of the candidate prompt’s improvement over the current prompt. It is used to decide whether the local win is strong enough to trust.

**Data flow**: It takes a Contingency object containing accepted counts and total counts for candidate-present and candidate-absent examples. If either side has no examples, it returns 0. Otherwise it computes the raw acceptance-rate difference, measures uncertainty on both sides using Wilson bounds, subtracts that uncertainty, and returns the cautious lower estimate of lift.

**Call relations**: score_gate calls this after replay results have been counted. lift_lower_bound relies on wilson_lower_bound and wilson_upper_bound to avoid treating a noisy small sample as a proven improvement.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: This function estimates the optimistic upper bound of the candidate prompt’s improvement over the current prompt. It is mainly used to detect whether broader performance is confidently worse.

**Data flow**: It takes a Contingency object with counts for candidate-present and candidate-absent examples. If either side has no examples, it returns 0. Otherwise it computes the raw lift, adds the uncertainty from both sides, and returns the most favorable reasonable estimate of the candidate’s lift.

**Call relations**: global_non_inferior calls this during the global safety check. If even this optimistic value is below the allowed harm margin, the candidate is treated as a real regression.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: This function turns individual replay labels into the four counts the statistics need. It separates examples where the candidate prompt was present from examples where it was absent, then counts successes in each group.

**Data flow**: It takes a tuple of OutcomeLabel records. It splits them into candidate-present and candidate-absent groups, counts how many examples are in each group, counts how many were accepted, and returns a Contingency record with those totals.

**Call relations**: score_gate and global_non_inferior both call this before doing statistical checks. It is the small counting step that turns raw replay outcomes into the structured numbers used by lift_lower_bound and lift_upper_bound.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: This function makes the local promotion decision for the task class the candidate was meant to improve. It requires both enough replay examples and a confidently positive acceptance lift.

**Data flow**: It takes replay labels, plus optional thresholds for the required lower-bound lift and minimum examples per side. It counts the labels with contingency, computes the cautious lower lift with lift_lower_bound, then returns a GateVerdict saying whether the candidate passed, why, the measured lower bound, and the sample sizes.

**Call relations**: two_stage_gate calls this first. If score_gate says the candidate did not clearly improve the local task class, the full gate stops there and does not bother with the global regression check.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: This function checks whether the candidate avoids clearly harming other task classes. It is deliberately forgiving when evidence is thin, because proving “no harm at all” is hard with small samples.

**Data flow**: It takes replay labels from the broader held-out task set, plus an allowed regression margin and minimum examples per side. It counts the labels. If either side has too few examples, it returns true. Otherwise it computes the optimistic upper lift and returns true unless even that optimistic estimate shows a meaningful drop.

**Call relations**: two_stage_gate calls this only after the local gate has passed. It uses contingency to prepare the counts and lift_upper_bound to decide whether the candidate is confidently harmful globally.

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: This function gives the final promotion verdict. A candidate must both show a trusted local improvement and avoid a confident global regression.

**Data flow**: It takes local replay labels and global replay labels. First it sends the local labels through score_gate. If that fails, it returns that failure verdict unchanged. If the local check passes, it sends the global labels through global_non_inferior. If the global check finds clear harm, it returns a failing GateVerdict with that reason. Otherwise it returns the successful local verdict.

**Call relations**: This is the top-level decision function in the file. It ties together the local improvement check and the wider safety check, using score_gate first and global_non_inferior second to decide whether the candidate prompt may be promoted.

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `cross-cutting during model calls for proposing, replay, and grading`

The self-improvement extension needs to ask a language model for two slightly different things. Sometimes it wants plain text back, such as a proposal or judgment. Other times it wants a full assistant turn, possibly including tool use. This file defines those two needs as small interfaces, then provides an adapter that connects them to the wider SDK's model access system.

Think of it like a standard power plug. The extension does not need to know the details of the building's wiring; it just needs a safe socket with the right shape. Here, ModelAccessLeg is that socket. It takes the SDK's ModelAccess object and turns each request into a ModelRequest with the same important settings every time: the chosen model, the system instructions, the conversation messages, a maximum output size, and reasoning turned off.

The shared token limit matters because model output can otherwise grow too large or cost too much. The narrow interfaces also make the rest of the extension easier to test or replace, because code can depend on “something that can complete text” or “something that can produce a turn” rather than the full SDK object.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This defines the promise for anything that can ask a model to produce plain text. Code can rely on this shape without caring which model service or test double is behind it.

**Data flow**: It receives system instructions and a tuple of conversation messages. An implementation is expected to send those to a model and return the model's text response as a string.

**Call relations**: This is a protocol method, meaning it describes the expected behavior rather than doing the work itself. ModelAccessLeg.complete is the concrete version that fulfills this promise using the SDK's model access object.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This defines the promise for anything that can ask a model to produce a full assistant message during replay, including possible tool choices. It is used when the caller needs more than simple text.

**Data flow**: It receives system instructions, conversation messages, and a set of tool descriptions that tell the model what tools are available. An implementation is expected to return one Message representing the model's next turn.

**Call relations**: This is a protocol method, so it acts like a contract for replay-capable model callers. ModelAccessLeg.turn is the concrete implementation that packages the inputs into an SDK request.


##### `ModelAccessLeg.complete`  (lines 28–37)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This sends a plain text completion request through the SDK's metered model access path. It is used when the extension wants a string answer and does not need tool-calling behavior.

**Data flow**: It starts with system instructions and conversation messages from the caller. It builds a ModelRequest using the configured model name, a fixed output limit of 2048 tokens, and reasoning set to off, then passes that request to the SDK model access object. The returned model text comes back as the function's result.

**Call relations**: This is the working implementation of the ModelLeg.complete contract. When extension code asks for a text completion through this adapter, this function creates the ModelRequest and hands it to the underlying SDK model object.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 39–51)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This sends a full model-turn request through the SDK, including the tools the model is allowed to use. It is used for replay-style flows where the model may need to return a structured assistant message rather than just text.

**Data flow**: It receives system instructions, conversation messages, and tool schemas. It wraps them in a ModelRequest with the selected model, the same 2048-token output cap, and reasoning turned off, then sends the request through the SDK model access object. The result is a Message representing the model's next turn.

**Call relations**: This is the concrete implementation of the ReplayLeg.turn contract. Replay code can call it through the narrow replay interface, while this function takes care of translating that simple request into the SDK's ModelRequest format.

*Call graph*: 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `self-improvement evaluation replay`

This file is about fair, low-risk comparison. The system has an archived conversation where a model may have called tools, received results, and then gave a final answer. To test a new prompt, this code removes the old final answer and asks the model to continue from the same earlier conversation. If the model asks for a tool, the code does not actually run that tool. Instead, it looks up the matching tool call from the archive and feeds back the exact old result. This is like replaying a recorded cooking show: the chef can make new choices in narration, but the ingredients already on the counter are the same, and no new shopping trip happens.

The main value is isolation. The prompt text is the thing being tested, so tool results should not change and tools should not create side effects. The file also notices when the replayed model asks for a tool call that does not match anything in the archive. That means the replay has wandered off the recorded path. In that case, it stops and marks the result as “diverged,” while still returning whatever useful text was produced so far. A small round limit prevents the replay from going on forever.

#### Function details

##### `_canonical_input`  (lines 35–36)

```
def _canonical_input(value: object) -> str
```

**Purpose**: This helper turns a tool’s input into a stable text form so two inputs can be compared reliably. It matters because the replay must decide whether a new tool call is the same as one that happened in the archived conversation.

**Data flow**: It receives any input value from a tool call. It converts that value to JSON text with keys sorted and unnecessary spacing removed, so the same data always becomes the same string. It returns that string for use as part of a lookup key.

**Call relations**: When the archive is indexed, archived_tool_results uses this to label old tool calls. Later, _feed_archived uses the same conversion on replayed tool calls, so both sides can be matched using the same rules.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 39–52)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares the archived conversation for replay by removing the old final assistant answer. The new prompt should regenerate that answer rather than seeing the answer it is being judged against.

**Data flow**: It receives the full archived message history. Starting from the end, it removes trailing assistant messages that are plain final answers and keeps earlier user messages, assistant tool requests, and tool-result messages. It returns the shortened conversation as the starting point for replay.

**Call relations**: ReplayEvaluation.replay calls this near the start of a replay. The returned conversation becomes the context sent to the model for the first replayed turn.

*Call graph*: called by 1 (replay).


##### `archived_tool_results`  (lines 55–77)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: This builds a lookup table of old tool results, keyed by the tool name and the exact input that produced each result. It is what lets replay answer tool calls from history instead of executing real tools.

**Data flow**: It receives the archived messages. First it collects tool result blocks by their tool-use id, then it walks the messages again to find tool-use blocks and pair each one with its matching result. It returns a dictionary where each key means “this tool with this input” and each value is the archived result block.

**Call relations**: ReplayEvaluation.replay calls this before the replay loop begins. _feed_archived later relies on this table to answer each replayed tool request with the matching archived result.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 80–97)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: This creates the limited tool catalog shown to the model during replay. It includes only the tools that appeared in the archived conversation, so the replay stays scoped to what the old run actually used.

**Data flow**: It receives the archived messages and scans them for tool-use blocks. For each distinct tool name, it creates a permissive tool description that accepts object-shaped input. It returns those tool schemas as the available tools for the replay.

**Call relations**: ReplayEvaluation.replay calls this before asking the model to continue. The model receives these tool schemas during each replayed turn, which nudges it to reproduce archived tool calls when needed.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 100–116)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: This answers one round of replayed tool requests using the archived results. If any requested call cannot be found in the archive, it reports that by returning nothing, which means the replay has diverged.

**Data flow**: It receives the tool calls the replayed model just requested and the lookup table of archived results. For each call, it searches for the same tool name and same canonical input. If every call matches, it creates a user message containing tool-result blocks with the archived content but the new call ids. If any call is missing, it returns None.

**Call relations**: ReplayEvaluation.replay calls this whenever the model asks for tools. If it returns a message, that message is appended to the replay conversation; if it returns None, ReplayEvaluation.replay stops and marks the replay as diverged.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 129–150)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: This is the main replay procedure. It tests one archived task under one candidate system prompt, safely reusing archived tool results until the model gives a final answer, diverges, or reaches the round limit.

**Data flow**: It receives the archived conversation and the system prompt being tested. It builds the archived tool-result lookup, builds the replay-only tool catalog, and strips the old final answer from the conversation. Then it repeatedly asks the model for the next assistant message. If the model gives a final text answer with no tool calls, it returns that answer as a non-diverged ReplayResult. If the model asks for tools, it tries to feed back archived results and continues. If a tool call cannot be matched, or if too many rounds pass, it returns the best text seen so far and marks the result as diverged.

**Call relations**: This method ties together all helpers in the file. It calls archived_tool_results, replay_tools, and replay_head during setup, then uses _feed_archived inside the replay loop. Its final output is a ReplayResult that downstream grading code can score, with the divergence flag kept visible rather than hidden.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-extension-set` — The saved and loaded set of extensions, packs, manifests, and contributed capabilities available to the runtime.
- `reg-workspace-directory` — The shared record of workspaces, members, owners, agents, and workspace boundaries.
- `reg-seat-entitlements` — The shared seat and access-limit state that decides which members may use the agent in a workspace.
- `reg-turn-state` — The durable status of each unit of agent work, including whether it is waiting, running, paused, finished, failed, or cancelled.
- `reg-runtime-fleet` — The shared heartbeat and ownership records that show which server processes are alive and which work they are responsible for.
- `reg-cancellation-state` — The shared stop signal state used to cancel active turns, child tasks, tools, and abandoned work safely.
- `reg-prompt-state` — The agent instructions, rendered prompt templates, fingerprints, and governed prompt-change proposals.
- `reg-source-pages` — The source connections, sync cursors, imported pages, removal markers, and page-change records from outside systems.
- `reg-memory-store` — The durable memories, memory pages, recall events, and consolidation state used for long-term recall.
- `reg-scheduled-jobs` — The background job and scheduled-task state that records what should run later, what is claimed, and what repeats.
- `reg-accounting-ledger` — The usage, price, spend-cap, billing, export, and cost records used to track and limit money spent by workspaces and turns.
- `reg-extension-store` — The per-workspace extension-owned storage where plugins keep their own durable records without private tables.
- `reg-outbound-delivery-queue` — The durable pending, sent, failed, and retry state for replies or notifications that must be delivered back to external surfaces such as Slack.
- `reg-page-alert-subscriptions` — The stored page-change watch rules, subscribed conversations, topic alerts, and pending alert notifications triggered by synced content changes.
- `reg-self-improvement-evals` — The mined failure examples, replay results, grades, and statistical evidence used by self-improvement jobs before proposing prompt changes.
- `reg-source-sync-backoff` — Per-source sync error counters, retry/backoff state, and last-result throttling used to decide when background imports should run again.
- `reg-slack-connect-provisioning` — Durable Slack Connect customer-channel and invitation provisioning state, including retry/idempotency progress for admin background jobs.
- `reg-turn-admission-context` — Durable per-turn requester/speaker/on-behalf-of, timezone, surface context, and authorization-link metadata used to attribute, resume, and safely handle work.
