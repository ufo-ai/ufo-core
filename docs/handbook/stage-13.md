# Scheduled jobs, periodic maintenance, and autonomous follow-up  `stage-13`

This stage is the system’s “night shift.” It runs work that should happen outside a live user turn: scheduled prompts, delayed follow-ups, cleanup, syncing, and experiments to improve agents. At startup, jobs.py registers built-in and extension jobs as dependable workflows, while runtime_instance.py lets processes report that they are alive and cleans up work left behind by dead or cancelled processes.

Scheduling.py is the durable calendar inside each workspace. It stores tasks, lets workers claim them safely, and prevents two workers from doing the same job. candidates.py safely finds which workspaces have pending work, then makes sure the actual work happens inside the right workspace. The scheduled-tasks extension adds cron.py to validate repeating time rules, tools.py so users or agents can create recurring tasks or pause until a reply or timer, and runner.py to fire due tasks and advance repeats.

The self-improvement extension is a background learning loop. corpus.py gathers failed past conversations, proposer.py suggests prompt changes, replay.py retests old tasks safely, evaluation.py judges results, gate.py decides if evidence is strong enough, and cron.py runs this check over time.

## Files in this stage

### Background job runtime
Core infrastructure discovers workspace-scoped pending work, registers recurring workflows, and keeps fleet state and cancellations clean.

### `core/src/ufo/candidates.py`

`domain_logic` · `background job scheduling`

In this system, a job is not allowed to run in a vague, global way. It must be tied to a workspace, so it only sees and changes data that belongs to that workspace. This file provides the small bridge needed before that can happen: it asks, “Which workspaces currently have work to do?”

That question is special because it briefly needs to look across all workspaces. The code does this through `owner_tx`, a privileged database path that bypasses normal workspace isolation rules. To keep that safe, this file only uses it to read workspace IDs, not customer or tenant data. Think of it like reading the labels on locked filing cabinets, not opening the drawers.

The main helper, `owner_candidates`, takes a function that can build a database query. That query must return one column: `workspace_id`. The helper turns it into an async callable that the dispatcher can run on each scheduling tick. Building the query each tick matters because “due now” changes over time; the query can calculate the current cutoff fresh instead of using an old frozen time.

The result is a tuple of workspace UUIDs. Later, the dispatcher opens each workspace separately before running the actual handler. If no workspace IDs are returned, the job runs nowhere. If IDs are returned, every real handler run is safely scoped.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: This function wraps a caller-supplied database query builder into a safe workspace-candidate reader. It lets extensions say which workspaces may have pending work without giving them direct access to the privileged cross-workspace database connection.

**Data flow**: It receives `due`, a zero-argument function that builds a SQL query selecting workspace IDs. It creates and returns an async `candidates` function. Nothing is queried immediately; the returned function will build and run the query later, each time the scheduler asks for current candidates.

**Call relations**: This is the public seam for code that needs to declare candidate workspaces. It prepares the nested `owner_candidates.candidates` function, which does the actual privileged read when the dispatcher or scheduling code asks for candidates.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This async function actually asks the database which workspace IDs are due for work right now. It uses the privileged `owner_tx` path, but only long enough to read IDs, not full workspace data.

**Data flow**: When called, it opens an `owner_tx` database connection, calls `due()` to build a fresh query, executes that query, collects all returned rows, and then extracts the first column from each row. It returns those values as a tuple of workspace UUIDs and does not modify data.

**Call relations**: This function is the returned candidate reader from `owner_candidates`. During its run it calls `ufo.db.owner_tx` to open the special cross-workspace read path, executes the caller-built query there, and hands the resulting workspace IDs back so later job execution can be rebound safely to each workspace.

*Call graph*: 1 external calls (owner_tx).


### `core/src/ufo/jobs.py`

`orchestration` · `startup and scheduled background execution`

This file is the project’s background-job switchboard. At boot, the system discovers jobs from the core app and installed extensions, gives each job a stable name, and registers it with DBOS, a workflow system that can run work reliably even across crashes or restarts. Without this file, scheduled maintenance and extension jobs would not fire consistently, queued conversation turns might get stuck, idle sandbox containers could leak, and page-change consumers would not be fed new pages.

The main pattern is simple: first a lightweight “tick” happens on a schedule or one-shot enqueue. That tick asks, “Which workspaces actually have work?” Then it starts one durable job run per candidate workspace. This avoids opening every workspace on every tick and prevents one slow workspace from blocking its neighbors. A deduplication key acts like a cloakroom ticket: if the same workspace already has that job running, another copy is not stacked on top.

The file also defines several core jobs. TurnDispatcher recovers queued or parked conversation turns and places them on the turn worker queue in the right order. SandboxReaper destroys disposable sandboxes for conversations that have been idle long enough. PageChangeRunner feeds changed pages to extension hooks, keeping a separate cursor, or bookmark, for each consumer. JobRunner ties everything together by registering schedules and providing the DBOS workflow entry points that call the right handler inside the right workspace context.

#### Function details

##### `TurnDispatcher.run`  (lines 126–141)

```
async def run(self) -> None
```

**Purpose**: Finds conversation turns that are ready to be put back onto the worker queue. For parked turns, it first checks whether the relevant user has a seat and whether spending rules allow the work to resume.

**Data flow**: It starts with the current workspace’s dispatchable turns from the database. For each turn, it may read seat and spending information, skips turns that are still blocked, and sends allowed turns to the enqueue step. The visible result is that eligible turn rows are offered to the durable turn workflow queue.

**Call relations**: This is the handler used by the core turn-dispatch job. It asks _dispatchable_turns for candidates, uses gate_member, Seats, and SpendEvaluator for parked-turn checks, then hands each accepted turn to _enqueue so DBOS can run it.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 4 external calls (__init__, __init__, workspace_tx, gate_member).


##### `TurnDispatcher.candidate_workspaces`  (lines 143–151)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces have queued or parked turns that may need dispatching. This lets the scheduled sweep avoid opening workspaces that have no turn work.

**Data flow**: It computes a cutoff time for stale dispatch attempts, reads the owner-level database view, filters turns through the same eligibility rule used later inside a workspace, and returns distinct workspace IDs.

**Call relations**: JobRunner calls this through the job’s candidates function before starting per-workspace turn-dispatch runs. It relies on _eligible so the fleet-wide scan and the per-workspace scan agree about what counts as ready.

*Call graph*: calls 1 internal fn (_eligible); 4 external calls (now, timedelta, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 153–192)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: Loads a small ordered batch of turns in the current workspace that are safe to offer to workers. It keeps conversation order by only allowing the earliest queued or parked turn in a conversation.

**Data flow**: It reads turn and conversation rows from the workspace database, filters them with _eligible, orders queued work before parked work and then by creation/sequence, limits the batch size, and converts rows into _DispatchTurn records.

**Call relations**: TurnDispatcher.run calls this at the start of a dispatch sweep. The records it returns are later checked for seats and spending if parked, then passed to _enqueue.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 6 external calls (__init__, now, timedelta, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 194–224)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: Atomically marks one turn as offered and places it on the DBOS turn queue. The atomic mark prevents two sweepers from offering the same turn at the same time.

**Data flow**: It receives a _DispatchTurn, re-checks that the row is still stale, still in the same status, and still first in its conversation order, then updates its dispatch timestamp. If that update succeeds, it builds enqueue options and submits the turn to DBOS; if not, it does nothing.

**Call relations**: TurnDispatcher.run calls this after finding an eligible turn. It uses _first_in_status and _stale to guard the database update, then hands the final work item to DBOSClient.enqueue_async.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 1 (run); 5 external calls (now, timedelta, update, workspace_tx, uuid4).


##### `TurnDispatcher._eligible`  (lines 226–234)

```
def _eligible(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database rule for turns that may be dispatched. A turn must be queued or parked, have no recent dispatch offer, and be the first turn of its status in its conversation.

**Data flow**: It takes a cutoff time and produces a SQLAlchemy condition, which is a Python object representing a SQL WHERE clause. No rows are changed; the result is a reusable filter for database queries.

**Call relations**: Both candidate_workspaces and _dispatchable_turns use this rule. That keeps the broad workspace scan and the detailed per-workspace scan aligned.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 2 external calls (and_, or_).


##### `TurnDispatcher._stale`  (lines 236–240)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the rule for a turn whose previous dispatch offer is missing or old enough to retry. This is how the system recovers if a process stamped a turn but crashed before the worker actually claimed it.

**Data flow**: It takes a cutoff time and returns a database condition checking whether dispatch_enqueued_at is empty or earlier than that cutoff. It does not read or write by itself.

**Call relations**: _eligible uses it while searching, and _enqueue uses it again while stamping the row. The second use closes the race where another process may have offered the turn first.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 242–251)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the rule that says a turn is the earliest turn with a given status in its conversation. This prevents later turns from jumping ahead of earlier ones.

**Data flow**: It takes a turn status such as queued or parked and returns a database condition that rejects any turn with an earlier same-status turn in the same workspace and conversation.

**Call relations**: _eligible uses this while selecting possible turns, and _enqueue uses it again just before offering a turn. Together they preserve per-conversation ordering even with concurrent sweepers.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `SandboxReaper.run`  (lines 281–291)

```
async def run(self) -> None
```

**Purpose**: Destroys disposable sandbox containers for conversations that have been idle long enough. The durable workspace remains; only the cached container is removed.

**Data flow**: It reads idle sandbox handles in the current workspace, extracts the container ID for this sandbox backend, re-checks that the conversation did not become active, destroys the container through the carrier, and clears the stored handle in the database.

**Call relations**: This is the handler used by the core sandbox-reap job. It depends on _idle_sandboxes to find work, _now_active to avoid disrupting fresh turns, and _clear to remove the stale handle after destruction.

*Call graph*: calls 3 internal fn (_clear, _idle_sandboxes, _now_active); 2 external calls (__init__, sandbox_handle_id).


##### `SandboxReaper.candidate_workspaces`  (lines 293–312)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds workspaces that currently have at least one idle sandbox handle worth checking. This avoids running the reaper inside workspaces with no live sandbox cache.

**Data flow**: It computes the idle cutoff, reads conversations from the owner-level database view, filters for stored sandbox handles with no recent or in-flight turns, and returns distinct workspace IDs.

**Call relations**: JobRunner uses this as the candidate finder for the sandbox reaper job. It uses _active_since so the fleet-wide selection matches the later per-workspace cleanup.

*Call graph*: calls 1 internal fn (_active_since); 4 external calls (now, timedelta, select, owner_tx).


##### `SandboxReaper._clear`  (lines 314–320)

```
async def _clear(self, conversation_id: UUID) -> None
```

**Purpose**: Removes the stored sandbox handle from a conversation after its container has been destroyed. This ensures a later turn creates a fresh sandbox instead of trying to reuse a dead one.

**Data flow**: It receives a conversation ID, opens a workspace database transaction, and sets that conversation’s sandbox_handle field to null. It returns no value.

**Call relations**: SandboxReaper.run calls this after carrier.destroy succeeds. It is the database cleanup half of the reaping process.

*Call graph*: called by 1 (run); 2 external calls (update, workspace_tx).


##### `SandboxReaper._now_active`  (lines 322–340)

```
async def _now_active(self, conversation_id: UUID) -> bool
```

**Purpose**: Checks whether a conversation has become active immediately before destroying its sandbox. This protects a newly admitted turn from losing the sandbox it is about to use.

**Data flow**: It receives a conversation ID, queries the workspace database for any non-terminal turn, and returns true if one is found. It does not change data.

**Call relations**: SandboxReaper.run calls this after the initial idle list is read but before destroying a container. It narrows the race between database scanning and the out-of-database sandbox destroy call.

*Call graph*: called by 1 (run); 2 external calls (select, workspace_tx).


##### `SandboxReaper._idle_sandboxes`  (lines 342–361)

```
async def _idle_sandboxes(self) -> tuple[tuple[UUID, str], ...]
```

**Purpose**: Lists sandbox handles in the current workspace that appear idle and safe to reap. The database handle is the source of truth, so sandboxes made by older processes can still be reclaimed.

**Data flow**: It computes an idle cutoff, reads conversations with a non-empty sandbox handle and no active/recent turns, and returns pairs of conversation ID and stored handle.

**Call relations**: SandboxReaper.run uses this as its starting list. It shares the _active_since rule with candidate_workspaces so both stages agree about idle conversations.

*Call graph*: calls 1 internal fn (_active_since); called by 1 (run); 4 external calls (now, timedelta, select, workspace_tx).


##### `SandboxReaper._active_since`  (lines 363–375)

```
def _active_since(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database rule for whether a conversation is busy. A conversation is busy if it has an in-flight turn or a turn updated after the idle cutoff.

**Data flow**: It takes a cutoff time and returns a SQLAlchemy condition that looks for matching turn rows. It does not execute the query by itself.

**Call relations**: candidate_workspaces and _idle_sandboxes both negate this rule to find idle sandboxes. This keeps the broad and workspace-specific reaper scans consistent.

*Call graph*: called by 2 (_idle_sandboxes, candidate_workspaces); 3 external calls (exists, or_, select).


##### `_page_beyond_cursor`  (lines 378–390)

```
def _page_beyond_cursor(updated_at: datetime, page_id: UUID, cursor: object) -> bool
```

**Purpose**: Decides whether a page change is newer than a stored page-change cursor. The cursor is a bookmark made from the page’s change time and ID.

**Data flow**: It receives a page timestamp, a page ID, and a stored cursor value. If the cursor is missing or not a string, it treats the page as pending; otherwise it parses the cursor and compares time first, then ID, returning true or false.

**Call relations**: PageChangeRunner.workspaces_with_changes calls this after reading each workspace’s newest page and stored cursor. It keeps the workspace selection in the same order used by the page feed.

*Call graph*: called by 1 (workspaces_with_changes); 3 external calls (fromisoformat, replace, UUID).


##### `PageChangeRunner.consumers`  (lines 448–472)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: Discovers all registered page_change hooks from the active extension manifests. It gives each hook its own consumer record so each one can have a separate schedule and cursor.

**Data flow**: It reads manifests, collects declared credential names, filters hooks whose event is page_change, checks that hook function names are unique within an extension, and returns PageChangeConsumer objects.

**Call relations**: core_jobs calls this while building the built-in job list. Each returned consumer becomes its own JobSpec, so one slow or failing hook does not block another.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 474–526)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces have pages newer than a particular page-change consumer’s cursor. This keeps page-change jobs focused on workspaces with real pending changes.

**Data flow**: It builds the consumer’s cursor key, reads stored cursors and each workspace’s newest page from the owner-level database view, compares each newest page with _page_beyond_cursor, and returns workspace IDs that need replay.

**Call relations**: The candidate function created in core_jobs calls this for each page-change consumer. The resulting workspace IDs are then fanned out by JobRunner.tick into per-workspace workflows.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 2 external calls (select, owner_tx).


##### `PageChangeRunner.drive`  (lines 528–546)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: Runs one page-change consumer inside the current workspace. It replays changed pages in batches, calls the extension hook, and advances that consumer’s cursor only after the hook succeeds.

**Data flow**: It builds an ExtensionContext, reads the stored cursor, repeatedly asks the PageFeed for pages changed since that cursor, passes each batch to the hook, stores the next cursor, and stops when there are no changes or the final batch is smaller than the batch size.

**Call relations**: The per-consumer handler built by core_jobs calls this. It uses _context_for to create the extension environment and then hands each batch to the consumer’s hook.

*Call graph*: calls 1 internal fn (_context_for); 2 external calls (__init__, __init__).


##### `PageChangeRunner._context_for`  (lines 548–563)

```
def _context_for(self, extension: str, declared: frozenset[str]) -> ExtensionContext
```

**Purpose**: Creates the extension context used while running a page-change hook. The context is the hook’s toolbox: store, page feed, model access, blob storage, and optional invokers.

**Data flow**: It receives the extension name and declared credential slots, optionally creates an invoker for the current workspace, and calls context_for with the runner’s shared services. It returns an ExtensionContext.

**Call relations**: PageChangeRunner.drive calls this before invoking a hook. It connects the generic page-change loop to the specific extension’s scoped resources.

*Call graph*: called by 1 (drive); 2 external calls (context_for, ws_current).


##### `core_jobs`  (lines 566–635)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, reaper: SandboxReaper, page_change_runner: PageChangeRunner) -> tuple[JobSpec, ...]
```

**Purpose**: Builds the list of background jobs that the core system always registers. These include source syncing, page-change fan-out, turn dispatch, and sandbox cleanup.

**Data flow**: It receives the core service objects, wraps their run methods in job handler functions, creates one page-change job per discovered consumer, and returns a tuple of JobSpec objects.

**Call relations**: Startup code uses this before combining core jobs with extension jobs. The small nested functions inside it adapt concrete services to the common JobSpec shape expected by JobRunner.

*Call graph*: calls 1 internal fn (consumers); 1 external calls (__init__).


##### `core_jobs._sync_sources`  (lines 583–584)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: Adapts the source sync driver to the common job-handler shape. It ignores the extension context because source syncing already has the dependencies it needs.

**Data flow**: It receives an ExtensionContext, does not read from it, calls sync_driver.run, and returns when syncing has completed.

**Call relations**: core_jobs stores this function as the handler for the source-sync JobSpec. JobRunner.fire eventually calls it inside each candidate workspace.


##### `core_jobs._dispatch_turns`  (lines 586–587)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: Adapts TurnDispatcher.run to the common job-handler shape. It lets the turn dispatcher be scheduled like any other core or extension job.

**Data flow**: It receives an ExtensionContext, does not use it, calls turn_dispatcher.run, and returns after eligible turns have been offered to the worker queue.

**Call relations**: core_jobs stores this as the handler for the turn-dispatch JobSpec. JobRunner.fire calls it during scheduled dispatch sweeps.


##### `core_jobs._reap_sandboxes`  (lines 589–590)

```
async def _reap_sandboxes(context: ExtensionContext) -> None
```

**Purpose**: Adapts SandboxReaper.run to the common job-handler shape. It lets sandbox cleanup run through the same job path as other scheduled work.

**Data flow**: It receives an ExtensionContext, ignores it, calls reaper.run, and returns after idle sandboxes in the bound workspace have been checked and possibly destroyed.

**Call relations**: core_jobs stores this as the handler for the sandbox-reap JobSpec. JobRunner.fire invokes it for each workspace selected by the reaper’s candidate finder.


##### `core_jobs._drive_consumer`  (lines 592–598)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: Creates a job handler for one page-change consumer. This wrapper remembers which consumer should be driven when the scheduled job fires.

**Data flow**: It receives a PageChangeConsumer and returns an async handler function. The returned handler will later call PageChangeRunner.drive for that exact consumer.

**Call relations**: core_jobs calls this while creating page-change JobSpecs. The nested _handler it returns is what JobRunner.fire eventually invokes.


##### `core_jobs._drive_consumer._handler`  (lines 595–596)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: Runs the page-change loop for the consumer captured by _drive_consumer. It is the actual job handler stored in a page-change JobSpec.

**Data flow**: It receives an ExtensionContext from the job system, does not use that context directly, calls page_change_runner.drive with the captured consumer, and returns when the consumer is caught up or paused by an error.

**Call relations**: JobRunner.fire calls this as a normal job handler. It hands control to PageChangeRunner.drive, where the cursor and hook invocation happen.


##### `core_jobs._consumer_candidates`  (lines 600–604)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: Creates a candidate-workspace finder for one page-change consumer. This wrapper remembers which consumer’s cursor should be checked.

**Data flow**: It receives a PageChangeConsumer and returns an async candidate function. The returned function will later ask PageChangeRunner which workspaces have changes for that consumer.

**Call relations**: core_jobs uses this when building each page-change JobSpec. JobRunner.tick later calls the nested _candidates function before enqueueing per-workspace work.


##### `core_jobs._consumer_candidates._candidates`  (lines 601–602)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: Finds workspaces that have pending page changes for the captured consumer. It is the candidate callback stored in that consumer’s JobSpec.

**Data flow**: It takes no arguments, calls page_change_runner.workspaces_with_changes with the captured consumer, and returns the workspace IDs that need a run.

**Call relations**: JobRunner.tick reaches this through JobRunner.candidates. Its output decides which per-workspace page-change workflows are enqueued.


##### `bindings_from`  (lines 646–671)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...]) -> tuple[_Binding, ...]
```

**Purpose**: Combines core jobs and extension jobs into one registered job table. Each binding records the job’s stable key, owning extension namespace, credential slots, and JobSpec.

**Data flow**: It receives extension manifests and core JobSpecs, creates core bindings under the core namespace, then creates extension bindings under each extension’s name using that extension’s declared credentials. It returns all bindings as a tuple.

**Call relations**: Startup code uses this before constructing JobRunner. JobRunner later looks up these bindings by key when scheduling, finding candidates, or firing a job.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 693–717)

```
def launch(self) -> None
```

**Purpose**: Registers all jobs with DBOS at startup. Scheduled jobs become DBOS schedules, while one-shot jobs are enqueued once with deduplication so duplicate boots do not start duplicates.

**Data flow**: It stores this JobRunner in the module-level _firing variable, walks every binding, enqueues jobs with no schedule, collects scheduled jobs into ScheduleInput records, logs what it did, and calls DBOS.apply_schedules for cron-like jobs.

**Call relations**: This is the boot-time setup step for the file. The DBOS workflows job_tick and job_workflow later use the _firing runner set here to route work back into this object.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 719–731)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: Runs one scheduled firing of a job and fans it out to the workspaces that actually have work. It enqueues one per-workspace workflow and deduplicates by job plus workspace.

**Data flow**: It receives the scheduled time and job key, asks candidates for workspace IDs, then enqueues job_workflow for each workspace. If a duplicate is already active, it logs a skipped tick instead of stacking another run.

**Call relations**: job_tick calls this inside the durable DBOS tick workflow. It calls JobRunner.candidates first, then hands each workspace to JOB_QUEUE via job_workflow.

*Call graph*: calls 1 internal fn (candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 733–734)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: Looks up a job by key and asks that job which workspaces have pending work. It is the shared path for all core and extension candidate selection.

**Data flow**: It receives a job key, finds the matching binding with _binding, calls the JobSpec’s candidates function, and returns the resulting workspace IDs.

**Call relations**: JobRunner.tick calls this before enqueueing per-workspace workflows. It delegates the real selection logic to the specific job’s candidate callback.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 736–755)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: Runs one job handler inside one workspace. This is the only path that actually executes a job’s handler, ensuring every job runs with the right workspace and extension context.

**Data flow**: It receives a job key and workspace ID, finds the binding, enters the workspace scope, builds an ExtensionContext with shared services and optional invokers, then calls the handler. If the handler raises an error, it logs the failure and re-raises it so the workflow records the failure.

**Call relations**: job_workflow calls this inside the durable per-workspace workflow. It uses _binding for lookup, ws for workspace scoping, and context_for to give the handler its extension-specific toolbox.

*Call graph*: calls 1 internal fn (_binding); 3 external calls (context_for, log_error, ws).


##### `JobRunner._binding`  (lines 757–761)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: Finds the registered binding for a job key. It turns a stable string key into the job definition and extension information needed to run it.

**Data flow**: It receives a key, searches the runner’s bindings, returns the matching _Binding, or raises an error if no job was registered with that key.

**Call relations**: JobRunner.candidates and JobRunner.fire both call this. It is the central lookup point between DBOS’s string job keys and the in-memory job definitions.

*Call graph*: called by 2 (candidates, fire).


##### `job_tick`  (lines 768–772)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: DBOS workflow entry point for a job tick. It is the durable, scheduled wrapper that starts fan-out for a job key.

**Data flow**: It receives a scheduled time and job key from DBOS, reads the module-level JobRunner set during launch, and calls runner.tick. If launch has not happened, it raises an error instead of silently losing work.

**Call relations**: DBOS invokes this because JobRunner.launch registers or enqueues it. It hands off immediately to JobRunner.tick, which decides which workspaces need job_workflow runs.


##### `job_workflow`  (lines 776–780)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: DBOS workflow entry point for one job running in one workspace. It is the durable wrapper around the actual job handler call.

**Data flow**: It receives the scheduled time, job key, and workspace ID string, checks that a JobRunner has been registered, converts the workspace ID to a UUID, and calls runner.fire. It returns when that job handler finishes or fails.

**Call relations**: JobRunner.tick enqueues this for each candidate workspace. It hands off to JobRunner.fire, which binds the workspace and calls the correct core or extension handler.

*Call graph*: 1 external calls (UUID).


### `core/src/ufo/runtime_instance.py`

`orchestration` · `startup, main loop, shutdown`

This file is the fleet's pulse-check and janitor. Each serve process writes a small database row saying, in effect, “I am alive,” then refreshes that row every few seconds. That row is used as a liveness signal for DBOS, the durable workflow system that remembers and resumes long-running work. If a workflow is still marked as pending under an executor id, but that executor no longer has a fresh heartbeat row, the file treats that work as stranded and asks DBOS to recover it. The important safety rule is that live executors are never recovered, because that could start a second copy of work that is already running. The file also contains a cancel reconciler. Cancelling one turn is local: it marks that turn as cancelled. The reconciler periodically looks for still-running descendant turns beneath any cancelled ancestor and cancels those too. This is like a building evacuation alarm that is checked floor by floor: even if one checker crashes, another process will run the same sweep soon and finish the job. All of these loops tolerate temporary database or DBOS errors by logging the problem and trying again later.

#### Function details

##### `record_fleet_seat`  (lines 38–53)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: Creates this process's initial runtime-instance row in the database. This gives the rest of the fleet an immediate sign that the process exists before durable workflow work can be assigned to it.

**Data flow**: It receives an instance id. It opens an owner-level database transaction, inserts a runtime_instance row with no workspace attached, sets the heartbeat and timestamps to the current database time, commits through the transaction context, and writes a log message. The visible result is a fresh liveness row for this process.

**Call relations**: Startup code uses this before DBOS begins running work, so recovery sweeps do not mistake this brand-new process for a dead executor. Inside the function, the database insert records the seat and the log call leaves an audit trail.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 66–76)

```
async def run(self) -> None
```

**Purpose**: Runs the endless heartbeat loop for one process. It keeps the process's liveness row fresh so peers know not to recover work that is actually still running.

**Data flow**: It starts with the instance id stored on the Heartbeat object. On each cycle it calls Heartbeat.beat to update the database row, catches database errors so one failed tick does not stop the loop, logs failures, then sleeps for the configured heartbeat interval before trying again. It does not return during normal operation.

**Call relations**: This is the long-running driver for Heartbeat.beat. The serve process runs it in the background; if beat succeeds, the process remains visible as alive, and if beat briefly fails, the loop logs the problem and keeps going.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 78–88)

```
async def beat(self) -> None
```

**Purpose**: Performs one heartbeat update for this process. It is the single database write that says “this instance is still alive right now.”

**Data flow**: It reads the instance id from the Heartbeat object. It opens an owner-level database transaction and updates the matching runtime_instance row, replacing heartbeat_at and updated_at with the current database time. It produces no direct return value, but the database row becomes fresh.

**Call relations**: Heartbeat.run calls this once per loop cycle. The executor recovery sweep later reads these heartbeat timestamps through ExecutorRecovery._live_executors to decide which executors are safe to leave alone.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 90–96)

```
async def retire(self) -> None
```

**Purpose**: Removes this process's runtime-instance row during graceful shutdown. This lets other processes see immediately that the seat is gone instead of waiting for the heartbeat to become stale.

**Data flow**: It reads the instance id from the Heartbeat object. It opens an owner-level database transaction and deletes the runtime_instance row with that id. The output is a database state where this process no longer appears live.

**Call relations**: Shutdown code can call this when a process exits cleanly. It uses the same database table that Heartbeat.beat refreshes, and it affects what ExecutorRecovery._live_executors will later consider alive.

*Call graph*: 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 116–122)

```
async def run(self) -> None
```

**Purpose**: Runs the endless recovery loop that looks for work stranded by dead processes. It makes sure pending durable workflows do not remain stuck forever after a crash.

**Data flow**: It uses the interval configured on the ExecutorRecovery object. Each cycle waits for that interval, calls ExecutorRecovery.sweep, catches database or DBOS workflow-system errors, logs any failure, and then continues. In normal operation it runs for the lifetime of the process.

**Call relations**: This is the background driver for ExecutorRecovery.sweep. Every serve process can run it, so any surviving process can recover work left behind by a failed peer.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 124–132)

```
async def sweep(self) -> None
```

**Purpose**: Finds executor ids that still own pending workflows but no longer look alive, then asks DBOS to recover those workflows. This is the core repair step after a process crash.

**Data flow**: It asks ExecutorRecovery._pending_executors for executor ids attached to pending workflows, and asks ExecutorRecovery._live_executors for executor ids with fresh heartbeat rows. It subtracts the live set from the pending set. For each remaining stranded executor, it calls DBOS recovery in a worker thread and logs how many workflows were recovered.

**Call relations**: ExecutorRecovery.run calls this on a timer. This function coordinates the two pieces of evidence: pending work from DBOS and liveness from the runtime_instance table. It then hands stranded executor ids to DBOS._recover_pending_workflows so the durable workflow system can re-dispatch the work.

*Call graph*: calls 2 internal fn (_live_executors, _pending_executors); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_executors`  (lines 134–146)

```
async def _pending_executors(self) -> set[str]
```

**Purpose**: Reads DBOS workflow state to find executor ids that currently have pending work. It answers the question, “which processes appear to be holding unfinished durable workflows?”

**Data flow**: It calls DBOS.list_workflows in a worker thread, requesting pending workflows up to a fixed scan limit and skipping workflow inputs and outputs to keep the read lighter. If the scan reaches the limit, it logs that the result may be capped. It returns a set of non-empty executor ids found on those pending workflow records.

**Call relations**: ExecutorRecovery.sweep calls this before deciding what to recover. Its result is compared with ExecutorRecovery._live_executors, so pending work owned by a live process is left alone while pending work owned only by dead executors can be recovered.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 148–158)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: Reads the runtime-instance table to find which executors have heartbeated recently enough to count as alive. It protects active work from being recovered twice.

**Data flow**: It calculates a cutoff time by subtracting the stale-after window from the current UTC time. It opens an owner-level database transaction, selects runtime_instance ids whose heartbeat_at is newer than that cutoff, and returns those ids as strings in a set.

**Call relations**: ExecutorRecovery.sweep calls this alongside ExecutorRecovery._pending_executors. The live set is subtracted from the pending set, which is the safety check that prevents recovery from touching workflows that may still be running.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `CancelReconciler.run`  (lines 182–188)

```
async def run(self) -> None
```

**Purpose**: Runs the endless cancellation reconciliation loop. It makes cancellation spread from a cancelled parent turn to still-running descendant turns.

**Data flow**: It uses the DBOS client and interval stored on the CancelReconciler object. Each cycle waits for the interval, calls CancelReconciler.sweep, catches database or DBOS errors, logs any failure, and continues. It normally runs as a background task for the life of the process.

**Call relations**: This is the timed driver for CancelReconciler.sweep. Because every serve process can run it, cancellation cleanup continues even if the process that originally cancelled a turn crashes.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 190–197)

```
async def sweep(self) -> None
```

**Purpose**: Finds live turns that sit underneath a cancelled ancestor and cancels them. This is what turns a local cancellation into a whole-subtree cancellation.

**Data flow**: It opens an owner-level database transaction and runs the query built by CancelReconciler._orphans_query. For each returned turn id and workspace id, it enters that workspace context, calls cancel_one_turn with the DBOS client and turn id, and logs the turn if cancellation actually happened. The database and DBOS workflow state may be changed by cancel_one_turn.

**Call relations**: CancelReconciler.run calls this on a timer. The sweep relies on CancelReconciler._orphans_query to identify affected turns, then hands each one to the shared cancel_one_turn primitive so cancellation is performed the same way as other parts of the system.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (cancel_one_turn, owner_tx, log, ws).


##### `CancelReconciler._orphans_query`  (lines 199–234)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: Builds the database query that finds non-finished turns with a cancelled ancestor. It is careful to catch not only direct children, but also deeper descendants even if an intermediate turn already finished.

**Data flow**: It reads the turn table definition and constructs a recursive SQL query. The query starts from every non-terminal turn, walks upward through parent_turn_id links, stops once it reaches a cancelled ancestor, and selects the original turn id plus its workspace id. It returns the query object, not the rows themselves.

**Call relations**: CancelReconciler.sweep calls this, then executes the returned query inside a database transaction. The results drive which turns are passed to cancel_one_turn for actual cancellation.

*Call graph*: called by 1 (sweep); 1 external calls (select).


### Durable scheduled tasks
Scheduled-task support stores due work durably, validates recurrence rules, exposes user-facing scheduling tools, and runs claimed tasks when their time arrives.

### `core/src/ufo/scheduling.py`

`domain_logic` · `request handling and background scheduled-task polling`

Scheduled tasks are work the system must do later, even if the process restarts. This file stores that work as database rows. A row says which workspace, conversation, and agent the task belongs to, what prompt should be delivered, and when it is next due. Think of it like a shared calendar with a sign-out sheet: a worker can claim an overdue item for a short lease, do the work, then either advance it to the next time or let the claim expire so another worker can recover it.

The file has two main data shapes. ScheduledTask is the internal picture of one task that a runner can fire. TaskInspection is the status view used when someone asks what happened most recently. ScheduleStore is the workspace-scoped doorway to the scheduled_task table. It always reads the current workspace first, so code using it cannot accidentally see another workspace’s tasks.

There are two kinds of schedules. Normal recurring tasks have caller-chosen names and can be updated or cancelled by name. One-time workflow pauses use a reserved @once schedule and an internal @pause: name. Pause creation has extra safeguards so a member’s newer reply and the timer recovery path agree on one durable turn instead of racing each other.

The most important behavior is atomic claiming. When several pollers look for due tasks, the database update both selects and marks tasks as claimed, so overlapping workers split the work instead of duplicating it.

#### Function details

##### `ScheduleInvoker.invoke_scheduled`  (lines 52–52)

```
async def invoke_scheduled(self, task: ScheduledTask) -> UUID | None
```

**Purpose**: This is the contract for something that knows how to actually fire a scheduled task. ScheduleStore can store and lease tasks, but an invoker supplies the project-specific action that turns a due task into a conversation turn.

**Data flow**: A ScheduledTask goes in. The invoker uses the task’s conversation, agent, prompt, and timing information to run the scheduled action. It returns the identifier of the created turn if one was admitted, or nothing if no turn was created.

**Call relations**: ScheduleStore.invoke depends on this method when a due task is ready to fire. The scheduled-task runner calls ScheduleStore.invoke, and the store hands the task through to whatever invoker was wired into it.


##### `_task`  (lines 87–102)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: This helper turns a raw database result row into a ScheduledTask object that the rest of the code can use safely and clearly. It keeps the database column layout from leaking into every caller.

**Data flow**: A row from the scheduled_task table goes in. The helper reads named fields such as id, conversation_id, schedule, next_run_at, and claimed_by. A ScheduledTask value comes out with those fields placed under plain Python attribute names.

**Call relations**: ScheduleStore.list and ScheduleStore.claim_due use this after reading rows from the database. Those methods focus on finding the right rows, then hand each row to _task so callers receive normal task objects instead of database mappings.

*Call graph*: called by 2 (claim_due, list); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 105–131)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: This builds a workspace finder for the scheduled-task runner. Its job is to answer: which workspaces currently have at least one due task that is not already safely claimed by another worker?

**Data flow**: No immediate database data goes in. The function creates and returns an async candidate function. When that returned function runs, it reads the scheduled_task table and returns workspace IDs that have due, available work.

**Call relations**: This is a seam between core scheduling storage and the runner that dispatches work per workspace. The runner can ask for candidate workspaces without knowing how scheduled_task rows are queried or needing direct owner-level database access.


##### `due_task_workspaces.candidates`  (lines 113–129)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner function performs the actual lookup for workspaces with overdue scheduled tasks. It skips rows that are still under an unexpired claim, so a workspace is not reopened just because another worker is already firing its task.

**Data flow**: The current UTC time is read. The function opens an owner-level database transaction, scans scheduled_task rows whose next_run_at time has passed, and filters to rows with no claim or an expired claim. It returns a tuple of distinct workspace IDs.

**Call relations**: due_task_workspaces returns this function as the ready-to-use selector. When the dispatcher awaits it, candidates uses the same availability rule as ScheduleStore.claim_due, so workspace selection and task claiming agree about what counts as runnable.

*Call graph*: 4 external calls (now, or_, select, owner_tx).


##### `ScheduleStore.workspace_id`  (lines 143–144)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property tells the store which workspace it is currently operating inside. It is a safety rail: every ScheduleStore database operation is scoped to this workspace.

**Data flow**: No argument goes in. The property reads the current workspace context through ws_current and pulls out its workspace_id. The UUID for the active workspace comes out.

**Call relations**: Most ScheduleStore methods use this property while building database queries. That keeps create, pause, cancel, list, claim, reschedule, and inspect tied to the workspace that the surrounding request or background job has already bound.

*Call graph*: 1 external calls (ws_current).


##### `ScheduleStore.invoke`  (lines 146–149)

```
async def invoke(self, task: ScheduledTask) -> UUID | None
```

**Purpose**: This asks the configured invoker to fire a scheduled task. It also fails loudly if the store was created without an invoker, because storing tasks alone is not enough to run them.

**Data flow**: A ScheduledTask goes in. If no invoker is present, the function raises an error. Otherwise it passes the task to the invoker and returns the turn ID the invoker reports, or nothing if no turn was created.

**Call relations**: The scheduled-task runner calls this while firing claimed work. ScheduleStore.invoke is the handoff point from durable scheduling data to the outside component that actually re-enters the conversation.

*Call graph*: called by 1 (_fire).


##### `ScheduleStore.create`  (lines 151–184)

```
async def create(self, conversation_id: UUID, agent_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None) -> ScheduledTask
```

**Purpose**: This creates or updates a recurring scheduled task by name. It prevents callers from using names and schedule markers reserved for one-time pauses, so recurring jobs and workflow pauses cannot be confused.

**Data flow**: The caller supplies the target conversation, agent, task name, schedule text, prompt, description, next run time, and optionally the member who created it. The function validates that this is a normal recurring task, then sends the data to _upsert. A ScheduledTask comes back, or an error is raised if no row was produced.

**Call relations**: Higher-level code uses this when a user or agent defines recurring scheduled work. create does the public validation and delegates the database insert-or-update details to ScheduleStore._upsert.

*Call graph*: calls 1 internal fn (_upsert).


##### `ScheduleStore.pause`  (lines 186–207)

```
async def pause(self, conversation_id: UUID, agent_id: UUID, prompt: str, description: str, next_run_at: datetime, origin_seq: int, created_by_member_id: UUID | None=None) -> ScheduledTask | None
```

**Purpose**: This arms a one-time timer pause for a conversation. It records the conversation sequence that asked for the pause so later member replies and timer recovery can resolve to one consistent next turn.

**Data flow**: The caller supplies the conversation, agent, prompt, description, due time, origin sequence number, and optionally the creating member. The function gives the task a reserved pause name, marks it with the one-time schedule value, and calls _upsert. It returns the created ScheduledTask, or nothing if a newer member action means the pause should not be armed.

**Call relations**: Workflow code uses this instead of create for one-time pauses. pause delegates the complicated database and race-prevention checks to ScheduleStore._upsert, while supplying the reserved name and schedule that identify the row as a pause.

*Call graph*: calls 1 internal fn (_upsert).


##### `ScheduleStore._upsert`  (lines 209–343)

```
async def _upsert(self, conversation_id: UUID, agent_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, origin_seq: int | None, created_by_member_id: UUID | None
```

**Purpose**: This is the shared database routine for creating or replacing a scheduled task row. It is careful about pauses: before arming a one-time resume, it checks whether a member already replied or whether a newer queued member turn should be resumed immediately instead.

**Data flow**: Task details go in: conversation, agent, name, schedule, prompt, description, due time, optional origin sequence, and optional creator. The function locks the conversation row, performs pause-specific checks when origin_seq is present, chooses the effective agent and due time, then inserts a new row or updates the existing row with the same workspace and name. It returns a ScheduledTask reflecting the stored row, or nothing when a pause should not be created.

**Call relations**: ScheduleStore.create and ScheduleStore.pause both call this after adding their public rules. _upsert is where those two paths meet: recurring task updates and one-time pause arming share the same durable scheduled_task row machinery.

*Call graph*: called by 2 (create, pause); 6 external calls (__init__, now, exists, select, workspace_tx, uuid4).


##### `ScheduleStore.cancel`  (lines 345–353)

```
async def cancel(self, name: str) -> bool
```

**Purpose**: This removes a scheduled task by name within the current workspace. It is how a recurring job, or any named scheduled row, is stopped from firing in the future.

**Data flow**: A task name goes in. The function opens a workspace-scoped transaction and deletes the scheduled_task row whose workspace and name match. It returns true if a row was actually deleted, or false if there was nothing to cancel.

**Call relations**: Callers use this as the simple stop button for named schedules. It does not call the firing path or rescheduling path; it directly removes the durable row so future polling will no longer find it.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `ScheduleStore.list`  (lines 355–371)

```
async def list(self) -> tuple[ScheduledTask, ...]
```

**Purpose**: This returns the recurring scheduled tasks visible in the current workspace. It intentionally excludes one-time pauses, because those are internal workflow timers rather than user-facing recurring schedules.

**Data flow**: No task-specific input goes in. The function reads scheduled_task rows for the current workspace where the schedule is not @once, ordered by name. Each database row is converted through _task, and a tuple of ScheduledTask objects comes out.

**Call relations**: User-facing or tool-facing code can call this to show existing recurring schedules. list performs the workspace-filtered read, then relies on _task to package each row into the same object shape used by claiming and firing.

*Call graph*: calls 1 internal fn (_task); 2 external calls (select, workspace_tx).


##### `ScheduleStore.claim_due`  (lines 373–424)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: This leases a small batch of overdue tasks so one worker can fire them without another worker firing the same rows at the same time. A lease is a temporary claim that expires if the worker crashes or takes too long.

**Data flow**: The caller supplies the current time, a lease length in seconds, and an optional maximum number of tasks. The function creates a fresh claim ID, computes the expiration time, finds the oldest due rows that are unclaimed or whose claims expired, and updates those rows with the claim in one atomic database operation. It returns the claimed rows as ScheduledTask objects.

**Call relations**: Background polling code uses this after binding a workspace that has due work. claim_due feeds the runner claimed tasks to fire, and its returned claim_id is later checked by ScheduleStore.reschedule so only the worker that claimed a row can advance it.

*Call graph*: calls 1 internal fn (_task); 6 external calls (timedelta, or_, select, update, workspace_tx, uuid4).


##### `ScheduleStore.reschedule`  (lines 426–459)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: This advances a recurring task after it has fired. It records when it ran, optionally records which conversation turn was created, clears the claim, and sets the next due time.

**Data flow**: A claimed ScheduledTask, the next run time, the last run time, and optionally a last turn ID go in. The function rejects unclaimed tasks and one-time pauses, builds the fields to update, and updates only the row whose id and claim_id still match. It returns true if the row was updated, or false if the claim no longer matched.

**Call relations**: The scheduled-task runner calls this after ScheduleStore.invoke finishes firing a task. This closes the loop started by claim_due: claim_due leases the row, the runner fires it, and reschedule clears the lease and moves the recurring task forward.

*Call graph*: called by 1 (_fire); 2 external calls (update, workspace_tx).


##### `ScheduleStore.inspect`  (lines 461–502)

```
async def inspect(self, name: str) -> TaskInspection | None
```

**Purpose**: This builds a live status view for one recurring scheduled task. It answers not only what the schedule says, but also where it reports and what happened on its latest fired turn.

**Data flow**: A task name goes in. The function reads the scheduled task, its conversation surface, and the task’s last recorded turn if present, while excluding one-time pauses. If no matching recurring task exists, it returns nothing. Otherwise it returns a TaskInspection containing timing fields, the last turn status, and the last terminal text response when available.

**Call relations**: Status-rendering code can call this when it needs to display a scheduled task object. inspect does not fire or alter the task; it joins the schedule row with conversation and turn information to present the latest observable state.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `scheduled task creation and runner polling`

Scheduled tasks need a way to say “run every day at 9” or “run every five minutes.” This file supplies that timing language using cron expressions, which are compact five-part strings for minute, hour, day of month, month, and day of week. The main task store only cares about a concrete date and time called `next_run_at`; it does not understand cron itself. That keeps the general storage simple, while this extension owns the cron-specific rules.

There are two jobs here. First, `validate_cron` makes sure a schedule has exactly five fields and that the `croniter` library accepts it as a real cron expression. This catches bad schedules early, before they can be saved or used. Second, `next_fire` asks `croniter` for the next run time after a given moment.

An important detail is that the next time is strictly after the supplied `after` time. If a worker was asleep or delayed and missed several scheduled moments, this design does not create a pile of missed runs. Instead, it finds the next catch-up point from “now,” more like checking the next bus after you reach the stop rather than trying to board every bus you missed.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: Checks whether a schedule string is a valid five-field cron expression. This is used to reject malformed schedules before the system tries to run them.

**Data flow**: It receives a schedule as plain text. It splits the text by spaces to confirm there are exactly five cron fields, then asks the `croniter` library whether the expression is valid. If either check fails, it raises a `ValueError`; if both pass, it returns the original schedule unchanged.

**Call relations**: This function is the gatekeeper before a cron schedule is trusted. Its key outside helper is `croniter.croniter.is_valid`, which performs the detailed cron syntax check after this file has first enforced the project’s five-field format.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: Calculates the next date and time when a cron schedule should run after a given moment. Scheduled-task runners use this to turn a repeating rule into one concrete upcoming run time.

**Data flow**: It receives a valid cron schedule and a datetime named `after`. It gives both to `croniter`, which builds a schedule calculator, then asks that calculator for the next datetime. The result is a new datetime strictly later than `after`; the function does not change any stored state itself.

**Call relations**: This function is called when the system needs to advance a task’s next run time. It hands the actual calendar math to `croniter.croniter`, then returns the computed datetime so the broader scheduled-task flow can store or act on it.

*Call graph*: 1 external calls (croniter).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `object operations and tool calls during a conversation turn`

This file is the bridge between ordinary agent actions and long-lived scheduled work. A scheduled task is treated like a workspace object: it has a name, a schedule, a prompt, an owner, and status that can be listed or inspected. Without this file, an agent could not reliably say, “run this prompt every weekday morning,” nor could it safely stop mid-workflow and resume later.

The file defines the shape of a scheduled task with `ScheduledTaskSpec`: a cron schedule, a prompt, and an optional short description. A cron schedule is a compact time pattern, such as “9am every weekday.” Before saving a task, the file checks that this schedule is valid and computes the next time it should run.

`ScheduledTaskObjects` plugs scheduled tasks into the generic object system. It lists only normal recurring tasks, shows their saved definition and status, creates or updates them, and cancels them. Ownership matters: tasks are private to the member who created them, with limited power for the workspace owner.

The second major feature is `pause_and_wait`. It creates a hidden one-time schedule row, not a user-visible task. Think of it like leaving a sealed reminder note for the system: either a new member message opens it early, or the timer opens it later and resumes the workflow.

#### Function details

##### `_require_scheduler`  (lines 64–67)

```
def _require_scheduler(ctx: ToolContext) -> ScheduleStore
```

**Purpose**: This helper makes sure the current tool call has access to the schedule store. It gives the rest of the file one safe way to fetch that store, and fails clearly if the scheduled-tasks extension was not wired in.

**Data flow**: It receives the current tool context, looks inside the extension context for a scheduler, and returns that scheduler when present. If the scheduler is missing, it raises an error instead of letting later code fail in a confusing way.

**Call relations**: All code that needs to read or change scheduled rows calls this first. Listing, finding, inspecting, creating, deleting, and pausing all pass through it before touching the schedule store.

*Call graph*: called by 6 (_apply_owned, _delete_owned, _find, _owned_rows, _status, pause_and_wait).


##### `_summary`  (lines 70–71)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: This helper creates the short text shown when scheduled tasks are listed. It combines the schedule with either the task description or, if there is no description, the prompt.

**Data flow**: It receives a scheduled task row, builds a readable string like “schedule — description,” and trims it to the configured maximum length. The result is a compact summary for object listings.

**Call relations**: It is used when `ScheduledTaskObjects._owned_rows` turns stored tasks into listable workspace objects. It keeps listing output short and useful.

*Call graph*: called by 1 (_owned_rows).


##### `ScheduledTaskObjects._owned_rows`  (lines 86–94)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]
```

**Purpose**: This method returns the scheduled tasks that should appear in the object listing system, with ownership information attached. It lets the generic object layer know which task names exist and who owns them.

**Data flow**: It takes the current tool context, gets the scheduler, asks it for visible scheduled tasks, and converts each task into an owned object row. Each row contains the task name, a short summary, and the member who created it.

**Call relations**: The object framework calls this when it needs to list scheduled task objects. This method asks `_require_scheduler` for the store, uses `_summary` to make display text, then hands the object rows back to the broader object system.

*Call graph*: calls 2 internal fn (_require_scheduler, _summary); 2 external calls (__init__, __init__).


##### `ScheduledTaskObjects._spec`  (lines 96–102)

```
async def _spec(self, ctx: ToolContext, name: str) -> ScheduledTaskSpec | None
```

**Purpose**: This method returns the saved definition of one scheduled task. It is used when someone asks to inspect or retrieve the manifest for a named scheduled task.

**Data flow**: It receives a context and a task name, searches for the matching task, and returns a `ScheduledTaskSpec` containing its schedule, prompt, and description. If no task with that name is found, it returns nothing.

**Call relations**: The object system calls this when it needs the stored specification for a task. It delegates the lookup to `ScheduledTaskObjects._find`, then converts the stored row into the public spec model.

*Call graph*: calls 1 internal fn (_find); 1 external calls (__init__).


##### `ScheduledTaskObjects._status`  (lines 104–130)

```
async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This method reports runtime details about a scheduled task, such as where it posts results, when it will next run, and what happened during its latest run. It helps users understand whether a task is active and what it last did.

**Data flow**: It receives a context and task name, asks the scheduler to inspect that task, and turns the inspection data into a plain status dictionary. Dates are converted to text, and the last response is shortened so status output does not become too large.

**Call relations**: The object system calls this when it needs status information beyond the saved task definition. It goes straight through `_require_scheduler` to the schedule store and returns a readable snapshot of the task’s current state.

*Call graph*: calls 1 internal fn (_require_scheduler).


##### `ScheduledTaskObjects._apply_owned`  (lines 132–152)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: This method creates or updates a recurring scheduled task after checking ownership and validating the schedule. It is the point where a user-facing object apply becomes a durable schedule row.

**Data flow**: It receives the context, task name, desired spec, any old spec, and ownership information. If the task belongs to someone else, it refuses the change. Otherwise it validates the cron schedule, calculates the next run time, and saves the task with the current conversation, agent, prompt, description, and creator member ID.

**Call relations**: The object framework calls this when a scheduled task manifest is applied. It uses `_require_scheduler` to reach the store, `validate_cron` to reject bad schedules, and `next_fire` to choose the first future run time before handing the completed task definition to the scheduler.

*Call graph*: calls 1 internal fn (_require_scheduler); 4 external calls (__init__, now, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 154–155)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: This method cancels a scheduled task that the caller is allowed to delete. It removes the recurring future work from the scheduler.

**Data flow**: It receives the current context, task name, and owner information. The ownership gate has already been handled by the surrounding object system, so this method simply asks the scheduler to cancel the named task.

**Call relations**: The object framework calls this during deletion of a scheduled task object. It uses `_require_scheduler` to reach the schedule store and then hands off the cancellation.

*Call graph*: calls 1 internal fn (_require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 157–160)

```
async def _find(self, ctx: ToolContext, name: str) -> ScheduledTask | None
```

**Purpose**: This helper searches the scheduler’s visible task list for one named task. It gives other methods a simple way to look up a scheduled task row.

**Data flow**: It receives the current context and a task name, asks the scheduler for the list of scheduled tasks, and returns the first task whose name matches. If none match, it returns nothing.

**Call relations**: `ScheduledTaskObjects._spec` calls this when it needs to turn a stored task row into a public task spec. The helper itself goes through `_require_scheduler` before reading the schedule list.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 1 (_spec).


##### `pause_and_wait`  (lines 190–227)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: This tool pauses the current workflow until either a member sends a new message or a durable timer expires. It is useful for real-world waiting points such as approval, email verification, or a cooldown period.

**Data flow**: It receives the tool context and pause instructions, including the message to show now, how long to wait, what to do after resuming, and optional metadata. It calculates the resume time, stores a hidden one-time pause with the scheduler, and returns text telling the agent how to end the current turn. If a newer member message has already arrived, it skips arming the timer and returns instructions for resuming from that message instead.

**Call relations**: The tool system calls this when the agent invokes the `pause_and_wait` tool. It first gets the scheduler through `_require_scheduler`, then asks the scheduler to create the pause row, and finally wraps the resume instructions in a `ToolResult` so the agent can reply correctly before stopping.

*Call graph*: calls 1 internal fn (_require_scheduler); 5 external calls (__init__, __init__, now, timedelta, dumps).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `recurring scheduler tick`

This file is the clock-driven worker for scheduled tasks. Think of it like a person checking a shared appointment book every few minutes: it looks for appointments that are due, puts a temporary hold on each one so nobody else does the same work, then carries them out. That temporary hold is a lease, meaning a task is marked as claimed for a limited time to prevent overlapping runner ticks from firing it twice.

The runner gets its schedule store from the extension context. On each run, it records the current time, asks the scheduler for all due tasks it can claim, and tries to fire each one. Firing means asking the scheduler to invoke the task back into its conversation or workflow. If the task is a repeating cron-style task, and the fire was accepted, the runner calculates the next time it should run and saves that new schedule. One-time tasks are not rescheduled.

A key behavior is that failures are not silently swallowed. If one or more tasks fail to fire, the runner collects their names and raises an error after trying all claimed tasks. This lets the wider system notice trouble while still giving every due task in the batch a chance to run.

#### Function details

##### `ScheduledTaskRunner.run`  (lines 27–38)

```
async def run(self) -> None
```

**Purpose**: This is the main tick of the scheduled-task runner. It checks that a scheduler is available, claims tasks that are due now, asks each one to fire, and reports any failures after the batch is done.

**Data flow**: It starts with the runner's context and lease length. It reads the scheduler from the context, gets the current UTC time, asks the scheduler for due tasks that can be temporarily claimed, then passes each claimed task to `_fire`. It produces no normal return value, but if any task failed it raises an error naming the failed tasks.

**Call relations**: This is the top-level action for this file. When the extension's recurring job calls it, it uses the current time and delegates the per-task work to `ScheduledTaskRunner._fire`. It does not directly invoke or reschedule tasks itself; it coordinates the batch and decides whether the whole tick should be considered failed.

*Call graph*: calls 1 internal fn (_fire); 1 external calls (now).


##### `ScheduledTaskRunner._fire`  (lines 40–53)

```
async def _fire(self, scheduler: ScheduleStore, task: ScheduledTask, now: datetime) -> str | None
```

**Purpose**: This function tries to fire one already-claimed scheduled task. If the task repeats and the fire succeeds, it advances the task to its next scheduled time.

**Data flow**: It receives the schedule store, one scheduled task, and the current time. It asks the scheduler to invoke the task. If invocation throws an error, it records a short failure label. If invocation succeeds but returns no turn ID, it stops without rescheduling. If invocation succeeds for a repeating task, it calculates the next fire time from the task's schedule and saves that new time through the scheduler. It returns either a failure string or `None` for success/no reportable failure.

**Call relations**: `ScheduledTaskRunner.run` calls this once for each claimed due task. Inside, it hands the actual firing to `ScheduleStore.invoke`, uses `next_fire` to compute the next occurrence for repeating tasks, and then hands that update to `ScheduleStore.reschedule` so future runner ticks know when to pick the task up again.

*Call graph*: calls 2 internal fn (invoke, reschedule); called by 1 (run); 1 external calls (next_fire).


### Self-improvement preparation
The self-improvement loop builds evaluation corpora from past failures, periodically searches for candidates, and proposes prompt changes from useful evidence.

### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus construction`

The self-improvement system needs real examples of where the assistant struggled. This file treats a tool error inside a conversation as that signal: if a tool call failed, the transcript contains a concrete problem worth improving. Without this file, the system would not have a clean way to choose which past failures to learn from or how to keep test examples separate from training examples.

The file defines two simple records. A TaskExample is one failed conversation, reduced to the pieces the improvement loop needs: the conversation id, the user's original request, the full message history, and a plain-language description of the tool problem. A TaskClass is a group of examples for one kind of failure, named by the tool that errored, such as "tool:search".

The main flow scans each trajectory, which is a saved conversation. It finds the first real user request and the first failed tool result. To know which tool failed, it first maps tool-use ids to tool names, then matches the failed result back to the tool call it answered. If either the request or the error is missing, the trajectory is ignored.

Finally, examples are grouped by failed tool. Each group must be large enough to split. The split is deterministic: examples are sorted by conversation id, then divided into a held-out evaluation set and a mine set. This matters because the system should not judge a proposed improvement on the exact examples used to propose it.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

**Purpose**: Finds the first usable user request in a conversation. This gives the improvement loop the original task that the assistant was trying to satisfy.

**Data flow**: It receives the full list of messages from a trajectory. It reads them in order until it finds a message from the user whose content is plain, non-empty text. It returns that text, or returns nothing if no suitable user request is found.

**Call relations**: bad_trajectory calls this when deciding whether a transcript can become a useful example. If there is no user request, bad_trajectory drops the transcript because there is no clear task to grade against.

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

**Purpose**: Finds the first failed tool round in a conversation and identifies which tool failed. This turns a raw transcript error into a specific improvement target.

**Data flow**: It receives the full message list. First it scans tool-use blocks and remembers which tool name belongs to each tool-use id. Then it scans again for a tool-result block marked as an error. When it finds one, it uses the stored id-to-name map to return the tool name together with the error text. If no matching tool error is found, it returns nothing.

**Call relations**: bad_trajectory calls this before building a TaskExample. The returned tool name becomes the class label, and the returned error text becomes part of the human-readable problem description.

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

**Purpose**: Decides whether one saved conversation is a useful failure example. A conversation qualifies only if it has both a user request and a tool error.

**Data flow**: It receives one trajectory, including its conversation id and messages. It asks first_tool_error for the failed tool and error text, and asks first_request for the user's task. If either is missing, it returns nothing. If both are present, it creates a TaskExample containing the conversation id, request, full messages, and a short problem statement, then returns it paired with a class name like "tool:<name>".

**Call relations**: task_classes calls this for every trajectory it is given. bad_trajectory is the filter in the middle of the flow: it turns only the useful failed transcripts into examples and lets the rest pass by unused.

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

**Purpose**: Builds the full set of task classes from many trajectories. It groups failures by which tool errored, removes groups that are too small, and returns the remaining groups in a stable, useful order.

**Data flow**: It receives a tuple of trajectories. For each one, it calls bad_trajectory; qualifying examples are collected under their class name. Then it calls _split on each group so every surviving class has a mine set and a held-out set. It returns the resulting TaskClass objects sorted so larger classes come first, with names used as a tie-breaker.

**Call relations**: This is the main function other parts of the self-improvement system would call when they need an evaluation corpus. It delegates transcript filtering to bad_trajectory and delegates train/test-style splitting to _split.

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

**Purpose**: Splits one group of examples into examples to learn from and examples to test on. It refuses groups that are too small to support both sides of that split.

**Data flow**: It receives a class name and all examples for that class. If there are not enough examples, it returns nothing. Otherwise, it sorts examples by conversation id for a repeatable order, chooses an evaluation count that keeps at least the required minimum on both sides, and returns a TaskClass with held-out examples first and mine examples after them.

**Call relations**: task_classes calls this after it has grouped examples by failed tool. _split is the gatekeeper that ensures each returned TaskClass can be used fairly: the proposer has mine examples, while the evaluator has separate held-out examples.

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled background cron tick`

This file is the safety gate for automatic prompt improvement. It does not directly change an agent. Instead, it works like a cautious reviewer: it finds one possible new prompt for an agent, tests it, waits for repeated passing results, and then asks the wider system to approve the change.

On each scheduled run, it gathers past trajectories, meaning records of agent conversations and outcomes, and groups them by agent. For each agent, it checks whether there is already a saved candidate prompt in the extension’s scoped store. That store is important because the process runs over time; without it, the system would forget which candidate it was testing and might keep proposing the same rejected or already-promoted prompt.

If there is no active candidate for the agent’s current prompt version, the file asks a PromptProposer to suggest a new prompt based on one task class. It saves that candidate along with a held-out test set, which is data kept aside for judging rather than training.

Then the candidate goes through a gate. CandidateEvaluation compares the candidate prompt against the current prompt using held-out examples. If it fails, it is marked rejected. If it passes, its pass count increases. Only after enough consecutive passing scheduled runs does this file call propose_change, creating a governed proposal for humans or members to approve.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: This is the top-level scheduled action for the self-improvement loop. It fetches recent trajectories, groups them by agent, and advances each agent’s candidate prompt review.

**Data flow**: It starts with no direct input beyond the ImproveCron object, which contains the extension context, proposer, evaluator, and stability setting. It reads trajectories from the context, groups them by agent, and then sends each agent’s group onward for review. It returns nothing; its effect is to update saved candidate state or open proposals through later steps.

**Call relations**: This function begins the cron flow. It calls _by_agent to sort all trajectories into per-agent bundles, then calls ImproveCron._advance once for each bundle so every agent can be checked separately.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: This moves one agent one step through the self-improvement process. It either finds or opens a candidate prompt for the agent’s current prompt version, then tests that candidate if one exists.

**Data flow**: It receives an agent ID and that agent’s trajectories. It reads the current prompt digest from the first trajectory, builds the storage key for that agent, and asks for an active candidate or a newly opened one. If there is no candidate, it stops. If there is a candidate, it passes the candidate and trajectories into the gate step.

**Call relations**: ImproveCron.run calls this after grouping trajectories by agent. This function connects the candidate-finding step, ImproveCron._active_or_open, with the candidate-testing step, ImproveCron._gate.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: This finds the saved candidate prompt for an agent’s current prompt version, or creates a new candidate if none is active. It also prevents already rejected or promoted candidates from being proposed again for the same prompt digest.

**Data flow**: It receives a store key, the agent’s current prompt digest, and the agent’s trajectories. It first reads the scoped store. If the stored candidate matches the current digest and is still evaluating, it returns that candidate. If the stored candidate is finished, it returns nothing. If no suitable candidate exists, it builds task classes from the trajectories, asks the proposer for a new prompt, saves the new CandidateState, and returns it. If there are no task classes or no proposal, it returns nothing.

**Call relations**: ImproveCron._advance calls this before any evaluation happens. It uses task_classes to find useful task groups from the trajectories, creates a CandidateState for a new proposal, and writes that state to the context store so later cron ticks can continue from the same place.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: This is the main safety check for a candidate prompt. It evaluates the candidate against held-out examples, counts consecutive passing ticks, and opens a formal change proposal only after the candidate has been stable long enough.

**Data flow**: It receives the agent ID, storage key, current prompt digest, candidate state, and trajectories. It builds a global held-out set from other task classes, rebuilds the candidate’s own held-out examples, and asks the evaluator whether the candidate passed. If it fails, it saves the candidate as rejected with zero passes. If it passes but has not passed enough times yet, it saves the increased pass count. If it has passed enough times, it creates an AgentChange proposal and saves the candidate as promoted with the proposal ID.

**Call relations**: ImproveCron._advance calls this after a candidate is found or opened. This function calls _held_out to rebuild examples for the candidate’s saved test set, calls task_classes to gather other held-out examples, uses AgentChange to describe the proposed prompt change, and relies on ImproveCron._save to persist every final decision.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: This writes an updated version of a candidate state back to the scoped store. It is used whenever the candidate’s status, pass count, or proposal ID changes.

**Data flow**: It receives the store key, the current candidate state, and the new status information. It copies the candidate with the updated fields, turns it into JSON-friendly data, and stores it under the same key. It returns nothing; the lasting result is the saved state for future cron ticks.

**Call relations**: ImproveCron._gate calls this after each evaluation outcome. It is the small persistence step that lets the larger self-improvement process remember whether a candidate is still being tested, rejected, or promoted.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: This groups trajectories by the agent that produced them. It lets the cron process review each agent separately instead of mixing their histories together.

**Data flow**: It receives a tuple of trajectories. It walks through them, collects trajectories with the same agent ID into the same list, then returns a mapping from each agent ID to that agent’s tuple of trajectories. It does not change the trajectories themselves.

**Call relations**: ImproveCron.run calls this right after loading all trajectories. Its output sets up the rest of the flow, because ImproveCron._advance expects one agent’s trajectories at a time.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: This rebuilds the candidate’s saved held-out test examples from the latest trajectories. It only includes examples that are present and are considered bad trajectories, because those are useful for checking whether the new prompt fixes known problems.

**Data flow**: It receives all trajectories for an agent and a tuple of held-out conversation IDs saved in the candidate state. It creates a lookup from conversation ID to trajectory, checks each saved ID, skips missing conversations, and calls bad_trajectory on each found trajectory. When bad_trajectory identifies a usable failing example, it adds that TaskExample to the result. It returns the collected examples as a tuple.

**Call relations**: ImproveCron._gate calls this before evaluation. The examples it returns become part of the test material passed into CandidateEvaluation, so the evaluator can judge whether the candidate prompt improves on past weak spots.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).


### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal phase`

This file is one step in a self-improvement loop. Imagine an agent has a standing instruction sheet, called a system prompt, and it keeps stumbling on a certain kind of request. This code prepares a careful request to another model: “Here is the current instruction sheet, here is the task type, and here are examples of what went wrong. Rewrite the instruction sheet just enough to fix this pattern without changing the agent’s whole job.”

The main worker is `PromptProposer`. It receives the current prompt and a `TaskClass`, which is a grouped set of similar problem cases. If that task class has no mined examples, there is nothing concrete to learn from, so it stops. Otherwise it builds a readable prompt containing the task name, the current system prompt, and a limited number of short examples. It sends that to the model with a strict system instruction asking for only the full revised prompt.

After the model replies, the file cleans up common formatting noise, especially Markdown code fences. If the result is blank or exactly the same as the original prompt, it returns nothing. That matters because later quality checks would reject a no-change proposal anyway. If the model produced a real new prompt, it wraps it in a small `PromptCandidate` record with the task name so later steps can review or test it.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This is the main entry point for asking the model to draft an improved system prompt for one task class. It only produces a candidate when there are examples of past trouble and the model returns a meaningful change.

**Data flow**: It receives the current system prompt and a task class. First it checks whether the task class contains mined examples; if not, it returns nothing. If examples exist, it builds the model-facing request with `PromptProposer._prompt`, wraps it in a user `Message`, and sends it to the configured model along with the proposer’s system instruction. It then passes the model’s text through `_clean`. If the cleaned text is empty or matches the original prompt after trimming whitespace, it returns nothing. Otherwise it creates and returns a `PromptCandidate` containing the task class name and the revised prompt text.

**Call relations**: This function sits at the point where the self-improvement system turns evidence into a concrete proposal. During that proposal step, outside code calls it with the current prompt and a task class. It asks `PromptProposer._prompt` to assemble the evidence in a model-readable form, calls the model to get a rewrite, uses `_clean` to remove presentation clutter, and finally hands back a `PromptCandidate` for later review or gating.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This helper writes the actual user message that will be sent to the model. It presents the task class, the current prompt, and selected failure examples in a clear format so the model has enough context to suggest a focused improvement.

**Data flow**: It receives the current prompt and a task class. It takes up to the configured maximum number of mined examples, shortens each request and problem description to the configured character limit, numbers them, and combines them into one text block. It returns a complete instruction message that names the task class, shows the current system prompt, lists the examples, and asks for the full revised prompt.

**Call relations**: This function is called by `PromptProposer.propose` right before the model request is made. It does not contact the model itself; it prepares the evidence package that `propose` sends onward.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This helper removes simple unwanted wrapping from the model’s answer. It is especially there because models sometimes put answers inside Markdown code fences, even when asked not to.

**Data flow**: It receives raw text from the model. It trims leading and trailing whitespace. If the remaining text starts with a triple-backtick code fence, it removes the opening fence and, when present, the closing fence. It then trims the result again and returns the cleaned prompt body.

**Call relations**: This function is called by `PromptProposer.propose` after the model replies. Its cleaned output is what `propose` compares against the original prompt and, if it is a real change, stores in a `PromptCandidate`.

*Call graph*: called by 1 (propose).


### Self-improvement evaluation
Candidate prompts are replayed safely against archived tasks and gated conservatively before any replacement can be accepted.

### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `candidate evaluation before accepting a prompt change`

This file is the evidence-gathering step for self-improvement. A new prompt should not be trusted just because it looks better; it must prove itself on real examples. The file compares two “arms,” like an A/B test: the current prompt is the absent arm, and the candidate prompt is the present arm. Both are replayed on the same saved tasks, using archived tool results, so the prompt text is meant to be the only meaningful difference.

The main class, CandidateEvaluation, receives two model-like helpers: one that can replay an agent run, and one that can judge the final answer. For each held-out task, it runs the old prompt and the candidate prompt, then sends the original request and regenerated answer to the judge. The judge is instructed to return a tiny JSON object saying whether the answer was accepted.

Those yes/no results are turned into OutcomeLabel records, which say which arm was tested and whether it succeeded. Finally, the file sends the local task results and the broader global task results to a two-stage gate. That gate decides whether the candidate improves its target area without making other areas worse. Without this file, prompt changes would have no consistent, evidence-based approval step.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: This is the top-level check for a candidate prompt. It compares the candidate against the current prompt on both target examples and broader safety/regression examples, then returns the gate’s verdict.

**Data flow**: It takes the candidate prompt, the current prompt, a group of local held-out task examples, and optionally a group of global held-out examples. It asks _labels to turn each group into pass/fail evidence for old versus new prompt. It then feeds those two evidence sets into two_stage_gate, which returns the final GateVerdict saying whether the candidate passes.

**Call relations**: This method starts the evaluation story. It calls _labels twice: first for the local examples the candidate is supposed to improve, then for the wider examples that should not get worse. After collecting both sets of labels, it hands them to ufo_ext_self_improvement.gate.two_stage_gate to make the acceptance decision.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: This helper creates the raw comparison data for a set of saved tasks. For every task, it tests both the current prompt and the candidate prompt, then records whether each answer was judged successful.

**Data flow**: It receives the two prompt texts and a tuple of TaskExample objects. It builds a ReplayEvaluation using the configured replay model and round limit. For each task, it replays the task once with the current prompt and once with the candidate prompt, sends each final answer to _accepts, and stores the result as an OutcomeLabel showing whether the candidate prompt was present and whether the answer succeeded. It returns all labels as a tuple.

**Call relations**: CandidateEvaluation.evaluate calls this when it needs evidence for either the local or global held-out set. Inside, this method creates a ReplayEvaluation to regenerate answers, calls _accepts to judge each final answer, and wraps each judgment in an OutcomeLabel for the gate to read later.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: This helper asks the judge model whether one answer satisfies one user request. It turns the judge’s JSON response into a simple true or false.

**Data flow**: It receives the original request and the answer produced by replay. It sends both to the judge model with instructions to return only JSON like {"accepted": true} or {"accepted": false}. It then looks for a JSON object in the judge’s text, parses it, and returns true only if the parsed object has accepted set to true. If the judge response is missing JSON or cannot be parsed, it returns false.

**Call relations**: CandidateEvaluation._labels calls this after each replayed answer is produced. This method creates the user Message sent to the judge and uses json.loads to read the judge’s response. Its boolean result becomes the success value stored in each OutcomeLabel.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation before prompt promotion`

This file is the quality-control gate for self-improvement. Imagine testing a new recipe against an old one: you do not want to switch just because a few tasters happened to like it by chance. Here, the “tasters” are replayed task examples, and “liked it” means a judge accepted the agent’s answer.

The file compares two groups, called arms: examples where the candidate prompt was present, and examples where it was absent. It counts how often each group succeeded, then estimates the improvement in acceptance rate. Because small tests are noisy, it does not trust the raw difference alone. Instead, it uses Wilson confidence bounds, which are cautious ranges around success rates, and a Newcombe-style bound for the difference between the two rates. In plain terms: it asks, “Even if we account for uncertainty, is the candidate still better by enough?”

There are two checks. The local check asks whether the candidate improves the task class it was designed for, with enough examples on both sides. The global check asks whether the same candidate clearly damages other task classes. Lack of proof of global safety is allowed at small sample sizes, but clear evidence of harm blocks promotion. The final `two_stage_gate` combines these rules into one verdict with a pass/fail result and a human-readable reason.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This computes a cautious lower estimate for a success rate. Someone uses it when they want to avoid overreacting to a small number of lucky successes.

**Data flow**: It receives the number of accepted examples, the total number of examples, and optionally a confidence setting. If there are no examples, it returns 0. Otherwise it calculates a Wilson lower bound using the observed success rate and a square-root uncertainty term, then returns a value no lower than 0.

**Call relations**: The lift calculations call this when they need the pessimistic side of one group’s success rate. It supplies part of the uncertainty math used by both `lift_lower_bound` and `lift_upper_bound`.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This computes a cautious upper estimate for a success rate. It is used to ask how good a group might plausibly be after allowing for statistical uncertainty.

**Data flow**: It receives the number of accepted examples, the total number of examples, and optionally a confidence setting. If there are no examples, it returns 1. Otherwise it calculates a Wilson upper bound using the observed success rate and a square-root uncertainty term, then returns a value no higher than 1.

**Call relations**: The lift calculations call this when they need the optimistic side of one group’s success rate. Together with `wilson_lower_bound`, it helps define the possible range of the candidate’s improvement or regression.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: This estimates the worst believable improvement of the candidate prompt over the current prompt. It answers: “After accounting for noise, how much better can we confidently say the candidate is?”

**Data flow**: It receives a `Contingency`, which is a four-number summary of accepted and total examples for the candidate-present and candidate-absent groups. If either group has no examples, it returns 0. Otherwise it compares the two raw success rates, subtracts an uncertainty penalty built from Wilson bounds, and returns the conservative lower bound on the lift.

**Call relations**: `score_gate` calls this during the local promotion check. This function relies on `wilson_lower_bound`, `wilson_upper_bound`, and square-root math to turn raw replay counts into a cautious improvement estimate.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: This estimates the best believable improvement of the candidate prompt over the current prompt. It is mainly used to decide whether a candidate is clearly harmful elsewhere.

**Data flow**: It receives a `Contingency` summary of the two groups. If either group has no examples, it returns 0. Otherwise it compares success rates, adds an uncertainty allowance from Wilson bounds, and returns the optimistic upper bound on the lift.

**Call relations**: `global_non_inferior` calls this for the global safety check. If even this optimistic estimate is still too negative, the candidate is treated as a real regression.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: This turns a list of replay outcomes into the simple counts needed for the statistical checks. It separates examples where the candidate prompt was present from examples where it was absent, then counts successes in each group.

**Data flow**: It receives a tuple of `OutcomeLabel` records. Each record says whether the candidate prompt was present and whether the answer succeeded. The function splits those records into present and absent groups, counts accepted examples and totals for each, and returns a `Contingency` object with those four counts.

**Call relations**: Both `score_gate` and `global_non_inferior` call this first, because the later math works on counts rather than individual replay records. It is the small counting step before the confidence-bound calculations begin.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: This performs the local promotion test for the task class the candidate is meant to improve. It passes only if there are enough replay examples on both sides and the conservative improvement clears the required floor.

**Data flow**: It receives replay labels, plus optional thresholds for the minimum acceptable lift and minimum examples per group. It converts labels into counts with `contingency`, computes the conservative lift with `lift_lower_bound`, then returns a `GateVerdict`. The verdict says whether the candidate passed, why, what lower-bound lift was seen, and how many examples were in each group.

**Call relations**: `two_stage_gate` calls this as the first stage. If this local check fails, the full gate stops immediately and returns its failure verdict; if it passes, the candidate moves on to the global safety check.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: This checks whether the candidate clearly makes other task classes worse. It is deliberately lenient when there is too little evidence, but strict when the data strongly shows harm.

**Data flow**: It receives replay labels for the broader global set, plus optional settings for the allowed regression margin and minimum examples per group. It counts the outcomes with `contingency`. If either group has too few examples, it returns true. Otherwise it computes the optimistic lift with `lift_upper_bound` and returns true only if that optimistic value is not worse than the allowed negative margin.

**Call relations**: `two_stage_gate` calls this only after the local gate has passed. It uses `lift_upper_bound` because the question is not “did the candidate definitely help globally?” but “is it definitely harmful globally?”

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: This gives the final promotion verdict by combining the local improvement test with the global regression test. A candidate must help its target area and must not clearly damage the rest of the agent’s work.

**Data flow**: It receives two sets of replay labels: one for the local task class and one for the held-out global tasks. It first runs `score_gate` on the local labels. If that fails, it returns the local failure verdict. If it passes, it runs `global_non_inferior`; a global regression creates a new failing `GateVerdict`, while a safe global result returns the passing local verdict.

**Call relations**: This is the top-level decision function in the file. It calls `score_gate` for evidence of a local win, then `global_non_inferior` for evidence of wider harm, and returns the verdict that downstream self-improvement code can use to promote or reject the candidate prompt.

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `self-improvement evaluation / replay scoring`

This file answers a careful question: “If we had used this other prompt, would the model have given a better final answer?” To make that comparison fair and safe, it does not rerun tools, touch outside systems, or create new side effects. Instead, it replays only the model parts of an archived task.

The archived conversation is treated like a recorded cooking show. The model may ask for the same ingredients, meaning the same tool calls, but the file serves the already-recorded tool results rather than going back to the store. If the model asks for a tool call that was not in the archive, the replay has “diverged”: it has left the known path, so the system stops and scores whatever answer text it has so far.

The main flow is in `ReplayEvaluation.replay`. It first removes the original final answer, builds a lookup table of old tool results, and creates a small fake tool catalog based on tools the original run used. Then it lets the model take turns. Each time the model asks for tools, `_feed_archived` supplies matching archived results. If the model produces plain final text, replay ends successfully. A round limit prevents endless loops.

#### Function details

##### `_canonical_input`  (lines 35–36)

```
def _canonical_input(value: object) -> str
```

**Purpose**: This function turns a tool input value into a stable text key. It makes sure two inputs with the same content look identical even if their dictionary keys were originally ordered differently.

**Data flow**: It receives any input value, usually the arguments passed to a tool. It converts that value into compact JSON text with sorted keys. The returned string can then be used as part of a lookup key for matching replayed tool calls to archived tool results.

**Call relations**: When archived results are indexed, `archived_tool_results` uses this to record each tool call in a consistent form. Later, `_feed_archived` uses the same conversion on the model’s replayed call so it can find the matching old result.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 39–52)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function prepares the archived conversation for replay by removing the original final answer. The new prompt should generate its own answer, not see the answer produced by the old prompt.

**Data flow**: It receives the full archived message history. Starting from the end, it removes trailing assistant messages that are just final answer text, but it keeps assistant messages that contain tool requests because those are part of the tool-use path. It returns the shortened conversation as the starting context for replay.

**Call relations**: `ReplayEvaluation.replay` calls this near the beginning of a replay. The returned conversation becomes the model’s starting point when testing the new system prompt.

*Call graph*: called by 1 (replay).


##### `archived_tool_results`  (lines 55–77)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: This function builds a lookup table that connects each archived tool call to the result it originally received. That table is what lets replay answer tool requests without actually running tools again.

**Data flow**: It receives the archived messages. First it scans for tool result blocks and stores them by their tool-use ID. Then it scans for tool-use blocks and pairs each one with its stored result, using the tool name and canonicalized input as the final key. It returns a dictionary from “tool name plus input” to the old tool result.

**Call relations**: `ReplayEvaluation.replay` calls this before asking the model to replay the task. `_feed_archived` later relies on this table to answer each replayed tool request with the matching archived result.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 80–97)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: This function creates the list of tools the replayed model is allowed to ask for. It only includes tool names that appeared in the archived conversation.

**Data flow**: It receives the archived messages and scans them for tool-use blocks. For each distinct tool name, it creates a permissive tool description that accepts object-shaped input. It returns these tool descriptions as the tool catalog for replay.

**Call relations**: `ReplayEvaluation.replay` calls this during setup. The resulting tool catalog is passed into the model turn so the model can reproduce the archived tool calls, but it does not give access to the live system’s real tool registry.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 100–116)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: This function answers the model’s replayed tool calls using archived results. If any requested call does not match the archive, it reports that replay cannot safely continue on the known path.

**Data flow**: It receives the tool calls the model just requested and the archived result lookup table. For each call, it searches for a result with the same tool name and canonicalized input. If all calls match, it builds a user message containing tool result blocks with the archived content. If any call is missing, it returns `None` to signal divergence.

**Call relations**: `ReplayEvaluation.replay` calls this after each model turn that contains tool requests. If it returns a message, that message is appended to the replay conversation. If it returns `None`, replay stops and the result is marked as diverged.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 129–150)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: This is the main replay procedure. It reruns an archived task under a new system prompt, reusing old tool results, until the model gives a final answer, leaves the archived tool path, or reaches the round limit.

**Data flow**: It receives an archived conversation and a candidate system prompt. It builds archived tool-result data, prepares the allowed replay tools, removes the old final answer, and then repeatedly asks the model for the next assistant message. If the model returns final text with no tool calls, it returns that text as a non-diverged result. If the model asks for tools, it feeds back archived results when possible. If a tool request cannot be matched, or the round limit is reached, it returns the best partial text and marks the replay as diverged.

**Call relations**: This method ties together the helpers in the file. It uses `archived_tool_results` and `replay_tools` for setup, `replay_head` to create the replay starting point, and `_feed_archived` after each tool-using model turn. It packages the outcome as a `ReplayResult` so later grading code can judge the answer and see whether the replay stayed on the archived path.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-effective-config` — The combined settings that tell the service which features, adapters, limits, and deployment options to use.
- `reg-workspace-context` — The current workspace and member context that keeps every request acting inside the right tenant boundary.
- `reg-workspaces-members-agents` — The durable records for workspaces, their members, and the agents that can act for them.
- `reg-extension-pack-lock` — The saved choice of active packs and installed extensions for a workspace.
- `reg-extension-state-store` — The per-workspace key-value storage area where extensions keep their own persistent settings and data.
- `reg-capability-registry` — The loaded menu of extension-provided routes, tools, skills, hooks, jobs, credentials, models, and search backends.
- `reg-model-catalog` — The model switchboard that maps model names to providers, credentials, request formats, and prices.
- `reg-connector-catalog` — The shared directory of external service connectors and broker-backed provider access.
- `reg-search-index-state` — The searchable content indexes, chunks, embeddings, and search backend choices used for recall and source replay.
- `reg-turn-queue-run-state` — The durable state of each agent turn, including whether it is queued, claimed, running, parked, finished, failed, or cancelled.
- `reg-source-sync-state` — The remembered external sources, imported pages, raw bodies, change records, cursors, errors, and deletion markers.
- `reg-memory-store` — The workspace memory facts, episodes, ownership labels, confidence, and consolidation indexes used for recall.
- `reg-knowledge-graph` — The stored people, companies, things, and relationships used as structured background knowledge.
- `reg-scheduled-task-calendar` — The durable calendar of one-time and repeating tasks that workers can safely claim and run later.
- `reg-runtime-instance-fleet` — The heartbeat records for live worker and runtime processes, used to detect dead workers and clean up abandoned work.
- `reg-accounting-ledger-spend` — The usage ledger, spend caps, exports, and cost totals for models, egress, and other billable work.
- `reg-seat-billing-state` — The workspace seat limits, granted seats, included seats, and external billing integration state.
- `reg-observability-trace-state` — The logs, metrics, traces, traceparent links, and redaction rules used to monitor work safely.
- `reg-workspace-object-catalog` — The named workspace objects and object-type registry used to list, inspect, validate, change, or delete stored things.
- `reg-prompt-governance` — The saved prompt digests, prompt-change proposals, approvals, and experiment evidence that control instruction changes.
- `reg-background-job-registry` — The registered set of built-in and extension background workflows that the scheduler can run.
- `reg-database-connection-pool` — The process-wide database engine/session factory and connection pool used by request handlers, workers, schedulers, and persistence code.
- `reg-page-alert-watch-state` — The stored watches/subscriptions and notification targets used to replay source page changes into chat alerts.
- `reg-evaluation-replay-state` — Durable evaluation corpora, replay runs, scores, and judgments used by self-improvement and conformance workflows beyond prompt approval records.
- `reg-process-lifecycle-state` — Process-wide startup/shutdown state: background task handles, drain/cancel signals, and resource close callbacks created during service bootstrap.
