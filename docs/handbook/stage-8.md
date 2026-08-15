# Background job dispatch and scheduled wakeups  `stage-8`

This stage is the server’s alarm clock and background dispatcher. It runs behind the scenes, outside normal user requests, but often feeds work back into the same conversation and sync paths that user actions use. At startup, core/src/ufo/jobs.py finds registered background jobs and turns them into durable DBOS work, meaning work stored and retried reliably by the system. It schedules repeating jobs, starts one-off jobs, and runs each job inside the right workspace and extension context.

core/src/ufo/candidates.py helps decide which workspaces may have pending background work, but does it safely. Extensions can point to workspace IDs without getting broad access to every workspace’s data. core/src/ufo/ext/scheduled_fire.py keeps the shared key format for “this task fired at this time,” so the scheduler and history display agree.

The scheduled-tasks extension supplies the actual task runners. Its package marker makes the code importable. runner.py claims due scheduled tasks, sends each into the correct conversation once, then advances its next run time. pause_runner.py wakes paused conversations when their wait expires and cleans up the pause whichever event ended it.

## Files in this stage

### Core dispatch infrastructure
Shared core code discovers eligible workspace work, standardizes scheduled-run keys, and turns background job definitions into durable DBOS executions.

### `core/src/ufo/candidates.py`

`domain_logic` · `job scheduling and dispatch`

Background jobs in this system always run inside a specific workspace. Before a job can run, the dispatcher needs a list of workspace IDs where that job has something to do. This file provides that list-making mechanism.

The important safety rule is that the lookup may cross workspace boundaries, but it must only return workspace IDs, not the actual tenant data inside those workspaces. Think of it like checking a building directory to see which apartment numbers have mail, without opening anyone's mailbox. The actual job work happens later, after the dispatcher re-enters each workspace one at a time.

The main helper, `owner_candidates`, accepts a small query builder from an extension. That builder creates a database query selecting distinct workspace IDs from the extension's own tables. The helper then runs that query through `owner_tx`, a special database path that can read across workspaces and bypass normal row-level security, meaning the database rule that normally hides other workspaces' rows. This bypass is deliberately kept inside core code.

One subtle but important detail is that the query is built each time candidates are requested. That lets time-based work stay fresh, such as “find work due before now,” instead of freezing the time when the extension was first loaded.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: This function turns an extension-provided workspace-ID query builder into a callable the dispatcher can use. It lets extensions declare where work is waiting, while keeping the cross-workspace database access inside trusted core code.

**Data flow**: It receives `due`, a no-argument function that builds a database `SELECT` query returning workspace IDs. It wraps that builder in an async `candidates` function. The result is a callable that, when later run, will execute the fresh query and return the workspace IDs as a tuple.

**Call relations**: This is the public seam used when something needs to declare candidate workspaces. It does not run the database query immediately; instead, it returns `owner_candidates.candidates`, which is called later during a scheduling tick or dispatch pass.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner async function actually asks the database which workspaces currently have pending work. It returns only workspace IDs, so the dispatcher can later bind to each workspace before doing any real work.

**Data flow**: It starts with no direct input, but it closes over the original `due` query builder. When called, it opens an `owner_tx` database connection, builds and executes the current query, reads the first value from each returned row, and returns those values as a tuple of workspace UUIDs. It does not return the underlying rows or tenant data.

**Call relations**: This function is the callable produced by `owner_candidates`. When the scheduler or dispatcher asks for candidates, it runs this function. Inside, it calls `ufo.db.owner_tx` to get the special cross-workspace connection, then hands the resulting workspace IDs back so the dispatcher can run the job separately inside each workspace.

*Call graph*: 1 external calls (owner_tx).


### `core/src/ufo/ext/scheduled_fire.py`

`util` · `scheduled task admission and run lookup`

A scheduled task can run many times, so the system needs a durable way to name one specific run opportunity: “this task, at this time.” This file creates and reads that name. The name is a simple string made from the task’s unique ID, a colon, and the scheduled time written in standard ISO format. It works like a claim ticket: later, when looking at a run, the system can read the ticket and tell which scheduled task admitted it.

The important detail is stability. The timestamp keeps Python’s normal `isoformat()` spelling, including timezone text like `+00:00`, because these keys are used for deduplication. Deduplication means preventing the same scheduled occurrence from being admitted twice. If the spelling changed after a deploy, old and new code might treat the same occurrence as two different tickets.

The file also deliberately accepts that not every key belongs to scheduled tasks. Some other system paths, such as a durable pause resuming from a timer, may use different keys. When the parser sees a key that does not start with a valid UUID, it returns `None` instead of pretending it found a scheduled task.

#### Function details

##### `scheduled_fire_key`  (lines 14–16)

```
def scheduled_fire_key(task_id: UUID, fire_at: datetime) -> str
```

**Purpose**: Builds the stable idempotency key for one scheduled occurrence of one task. Someone would use it when admitting a scheduled run so the system can recognize that exact task-and-time pair later.

**Data flow**: It receives a task ID and a scheduled time. It turns the time into ISO text, joins the task ID and time with a colon, and returns that combined string. It does not change anything outside itself.

**Call relations**: This is the key-writing half of the pair. In the larger flow, the scheduled-tasks runner uses this kind of key when it admits a fire. It relies on `datetime.datetime.isoformat` to spell the time in a standard, stable way so later code can match the same occurrence.

*Call graph*: 1 external calls (isoformat).


##### `scheduled_fire_task_id`  (lines 19–25)

```
def scheduled_fire_task_id(key: str) -> UUID | None
```

**Purpose**: Reads a key and tries to recover the scheduled task ID from it. It returns `None` when the key is not shaped like a scheduled-fire key, so other kinds of run keys can pass through safely.

**Data flow**: It receives a text key. It takes the part before the first colon and tries to interpret that text as a UUID, which is a standard unique identifier. If that works, it returns the UUID; if it fails, it returns `None` and leaves everything else unchanged.

**Call relations**: This is the key-reading half of the pair. In the larger flow, the portal or runs feed can use it to connect a recorded run back to the scheduled task that admitted it. It hands the first piece of the key to `uuid.UUID`, which either confirms it is a valid task ID or raises an error that this function turns into `None`.

*Call graph*: 1 external calls (UUID).


### `core/src/ufo/jobs.py`

`orchestration` · `startup registration and scheduled background work`

This file is the background-job switchboard for the system. Jobs may come from the core product or from installed extensions, so they cannot all be registered when Python imports the module. Instead, at boot, this file gathers the known jobs, gives each one a stable key, and registers it with DBOS, a durable workflow system that stores queued work so it can survive crashes and restarts.

The main flow has two layers. First, a lightweight “tick” happens when a schedule fires. The tick asks, “Which workspaces actually have work for this job?” Then it queues one real job run per workspace. This matters because one slow workspace should not block another, and repeated ticks should not stack up duplicate work for the same workspace.

The file also defines core jobs that every deployment needs. One syncs source pages. One notices page changes and calls extension hooks in batches. One recovers conversation turns that are queued or parked. One delivers results back from finished subagents. Each of these uses the same path: find candidate workspaces, bind to one workspace, build an ExtensionContext, and run the handler there.

A key safety idea is “do not guess ownership.” Old schedules are skipped if this process no longer knows the job, rather than deleted, because several app versions may share the same schedule table.

#### Function details

##### `ResultDeliverer.run`  (lines 66–66)

```
async def run(self) -> None
```

**Purpose**: This is the promised shape of a result-delivery sweep: it runs the work that hands completed child-agent results back to their parent conversation. The actual implementation lives elsewhere, but this file can schedule it without importing that subsystem directly.

**Data flow**: It takes no direct inputs besides the deliverer object. When called, it is expected to look for finished child results, post their arrival where needed, and finish without returning a value.

**Call relations**: The core job list wraps this method in a scheduled job. JobRunner later calls that wrapper inside each candidate workspace, so result delivery uses the same durable job path as all other jobs.


##### `ResultDeliverer.candidate_workspaces`  (lines 68–68)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This is the promised method for asking which workspaces have result-delivery work waiting. It lets the scheduler avoid opening every workspace on every tick.

**Data flow**: It reads whatever state the real result-delivery subsystem uses, then returns a tuple of workspace IDs that need a sweep. It does not itself deliver results.

**Call relations**: The core result-delivery JobSpec uses this method as its candidate finder. JobRunner.tick calls it before queuing per-workspace result delivery runs.


##### `TurnDispatcher.run`  (lines 131–166)

```
async def run(self) -> None
```

**Purpose**: This method finds conversation turns that are ready to be offered to the turn-processing queue. It also checks parked turns against seat and spending rules before letting them resume.

**Data flow**: It first asks for dispatchable turn rows in the current workspace. For each parked turn, it gathers the member IDs that must be seated, checks whether those members currently have seats, and asks the spending evaluator whether the turn is allowed. Turns that pass, and queued turns that do not need those checks, are stamped and enqueued for processing.

**Call relations**: A scheduled core job calls this through JobRunner.fire. It relies on _dispatchable_turns to find possible work and _enqueue to safely claim and submit each turn to the DBOS turn workflow.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 5 external calls (__init__, __init__, select, workspace_tx, gate_member).


##### `TurnDispatcher.candidate_workspaces`  (lines 168–176)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This method finds workspaces that contain queued or parked turns worth checking. It keeps the dispatcher from running inside workspaces that have no eligible turn rows.

**Data flow**: It computes a grace cutoff time, reads the owner-level database view, filters turns using _eligible, and returns the distinct workspace IDs that have matching rows.

**Call relations**: The turn-dispatch JobSpec uses this as its candidate finder. JobRunner.tick calls it first, then queues one TurnDispatcher.run execution for each returned workspace.

*Call graph*: calls 1 internal fn (_eligible); 4 external calls (now, timedelta, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 178–217)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: This helper loads a small ordered batch of turn records that may be ready to dispatch in the currently bound workspace. It enforces the rule that earlier turns in the same conversation go first.

**Data flow**: It builds a cutoff time, queries the workspace database for eligible queued or parked turns, joins each turn to its conversation for needed member data, orders them so queued and older turns are considered first, and converts the rows into _DispatchTurn objects.

**Call relations**: TurnDispatcher.run calls this at the start of a sweep. The returned _DispatchTurn objects are then either checked for gates, if parked, or passed onward to _enqueue.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 6 external calls (__init__, now, timedelta, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 219–249)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: This helper safely claims one turn for dispatch and submits it to the durable turn queue. It prevents two sweepers from offering the same turn at the same time.

**Data flow**: It receives a _DispatchTurn, rechecks that the database row still has the same status, is stale enough to claim, and is still first in its conversation for that status. If the update succeeds, it chooses a safe workflow ID and enqueues the turn-processing workflow; if the update finds nothing, it quietly stops.

**Call relations**: TurnDispatcher.run calls this after a turn passes any parked-turn checks. It uses _stale and _first_in_status to protect ordering and duplicate dispatch, then hands the work to DBOS.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 1 (run); 5 external calls (now, timedelta, update, workspace_tx, uuid4).


##### `TurnDispatcher._eligible`  (lines 251–259)

```
def _eligible(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This helper builds the database condition for turns that a dispatcher is allowed to consider. It combines status, stale dispatch stamp, and conversation ordering rules.

**Data flow**: It takes a cutoff time and returns a SQL condition. That condition matches only queued or parked turns whose previous dispatch stamp is missing or old, and whose sequence is the earliest matching status in that conversation.

**Call relations**: candidate_workspaces uses this to find workspaces with possible dispatch work. _dispatchable_turns uses it again inside a specific workspace to load the actual turn rows.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 2 external calls (and_, or_).


##### `TurnDispatcher._stale`  (lines 261–265)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This helper describes when a turn’s previous dispatch attempt is old enough to retry. It is how the system recovers from a process that stamped a turn but crashed before enqueueing it.

**Data flow**: It takes a cutoff time and returns a SQL condition that is true when dispatch_enqueued_at is empty or earlier than that cutoff.

**Call relations**: _eligible uses it while scanning for candidate turns. _enqueue uses it again during the final update so a race cannot claim a turn that another worker just stamped.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 267–276)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: This helper enforces per-conversation order for turns with the same status. It stops a later queued or parked turn from jumping ahead of an earlier one.

**Data flow**: It takes a turn status and returns a SQL condition saying, in effect, “there is no earlier turn in this workspace and conversation with this same status.”

**Call relations**: _eligible uses this while finding possible turns. _enqueue uses it again at claim time, so the ordering rule still holds even if rows changed after the first scan.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `_page_beyond_cursor`  (lines 279–284)

```
def _page_beyond_cursor(revision: int, page_id: UUID, cursor: object) -> bool
```

**Purpose**: This small helper answers whether a page position is newer than a stored page-change cursor. It is used to decide whether a workspace has page changes a hook has not seen yet.

**Data flow**: It receives a page revision, a page ID, and a cursor value. If there is no cursor, it returns true. Otherwise it parses the cursor into its boundary revision and ID, then compares the page’s position against that boundary.

**Call relations**: PageChangeRunner.workspaces_with_changes calls this while checking each workspace’s newest page against that consumer’s saved cursor.

*Call graph*: called by 1 (workspaces_with_changes); 1 external calls (page_cursor).


##### `PageChangeRunner.consumers`  (lines 344–368)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: This method discovers all registered page_change hooks from the active extension manifests. Each hook becomes its own independent consumer with its own job name and cursor.

**Data flow**: It reads each manifest’s declared credential slots and hook list. For hooks whose event is page_change, it records the extension, declared slots, hook spec, and handler function name as a discriminator. If two hooks in the same extension would share the same discriminator, it raises an error.

**Call relations**: core_jobs calls this while building the built-in page-change jobs. The returned PageChangeConsumer objects are later used to find changed workspaces and to drive each hook’s cursor loop.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 370–435)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: This method finds only the workspaces where a particular page-change consumer has pending page updates. It avoids running a hook in workspaces whose pages have not changed since that hook’s own cursor.

**Data flow**: It builds the consumer’s cursor key, reads saved cursors from extension storage, and reads each workspace’s newest page position. For every workspace with pages, it compares the newest page to that workspace’s cursor. If the cursor is missing, older than the newest page, or unparsable, the workspace is returned as pending.

**Call relations**: The page-change JobSpec produced by core_jobs uses this as its candidate finder. It calls _page_beyond_cursor for the comparison and logs a warning if one workspace has a bad cursor value.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 3 external calls (select, owner_tx, warn).


##### `PageChangeRunner.drive`  (lines 437–461)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: This method runs one page-change consumer inside the currently bound workspace. It reads changed pages in batches, calls the extension hook with those changes, and advances that consumer’s cursor only after the hook succeeds.

**Data flow**: It builds an ExtensionContext, reads the stored cursor, then repeatedly asks the page feed for pages changed since that cursor. Each non-empty batch is wrapped in a PageChangeBatch and passed to the hook. After the hook returns, it writes the new cursor with a compare-and-set check so another writer cannot be overwritten by an older value.

**Call relations**: The page-change handler wrapper created by core_jobs calls this. It uses _context_for to build the same kind of extension context that normal jobs receive, then hands work to the hook declared by the extension.

*Call graph*: calls 1 internal fn (_context_for); 2 external calls (__init__, __init__).


##### `PageChangeRunner._context_for`  (lines 463–480)

```
def _context_for(self, extension: str, declared: frozenset[str]) -> ExtensionContext
```

**Purpose**: This helper creates the ExtensionContext used by page-change hooks. That context is the hook’s toolbox: storage, page access, model access, embeddings, blobs, sandboxes, and optional turn invocation.

**Data flow**: It receives an extension name and its declared credential slots. If an invoker factory exists, it creates an invoker for the current workspace. Then it passes all available services into context_for and returns the resulting ExtensionContext.

**Call relations**: PageChangeRunner.drive calls this before invoking a page-change hook. It depends on ws_current because drive is expected to already be running inside one workspace selected by JobRunner.

*Call graph*: called by 1 (drive); 2 external calls (context_for, ws_current).


##### `core_jobs`  (lines 483–554)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, page_change_runner: PageChangeRunner, delivery_sweep: ResultDeliverer) -> tuple[JobSpec, ...]
```

**Purpose**: This function builds the set of background jobs that the core product always contributes. These include source syncing, page-change hook fan-out, turn dispatch, and result delivery.

**Data flow**: It receives the core service objects, wraps their methods in job handlers that fit the JobSpec shape, discovers page-change consumers, and returns a tuple of JobSpec objects with schedules, handlers, and candidate finders.

**Call relations**: Startup code uses this before bindings_from and JobRunner.launch. The nested wrappers it creates are later called through JobRunner.fire, so the underlying services run inside the normal workspace and extension context.

*Call graph*: calls 1 internal fn (consumers); 1 external calls (__init__).


##### `core_jobs._sync_sources`  (lines 502–503)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: This nested handler runs the source synchronization job. It polls configured sources and lands their pages through the provided sync driver.

**Data flow**: It receives an ExtensionContext because all job handlers share that shape, but it simply calls sync_driver.run and returns no value.

**Call relations**: core_jobs places this handler into the SOURCE_SYNC_JOB JobSpec. JobRunner.fire invokes it when the source-sync job runs for a candidate workspace.


##### `core_jobs._dispatch_turns`  (lines 505–506)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: This nested handler runs the turn-dispatch sweep for one workspace. It is the adapter between the generic job system and TurnDispatcher.run.

**Data flow**: It receives the standard job context, calls turn_dispatcher.run, and returns when dispatching attempts for that workspace are done.

**Call relations**: core_jobs places this handler into the TURN_DISPATCH_JOB JobSpec. JobRunner.fire calls it after binding the selected workspace.


##### `core_jobs._deliver_results`  (lines 508–509)

```
async def _deliver_results(context: ExtensionContext) -> None
```

**Purpose**: This nested handler runs result delivery for one workspace. It adapts the ResultDeliverer protocol to the generic JobSpec handler shape.

**Data flow**: It receives the standard job context, calls delivery_sweep.run, and returns no separate result.

**Call relations**: core_jobs places this handler into the RESULT_DELIVERY_JOB JobSpec. JobRunner.fire calls it during scheduled result-delivery runs.


##### `core_jobs._drive_consumer`  (lines 511–517)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: This nested factory creates a job handler for one specific page-change consumer. It lets every page-change hook become its own scheduled job.

**Data flow**: It receives a PageChangeConsumer and returns an async handler function. That returned handler will later call page_change_runner.drive for exactly that consumer.

**Call relations**: core_jobs calls this once per discovered page-change consumer while building JobSpec objects. The returned _handler is what JobRunner.fire eventually invokes.


##### `core_jobs._drive_consumer._handler`  (lines 514–515)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: This generated handler drives one page-change consumer inside a bound workspace. It exists so the generic job runner can call page-change hooks through the same handler interface as other jobs.

**Data flow**: It receives the standard ExtensionContext, though the actual page-change context is built inside PageChangeRunner.drive. It calls drive for the captured consumer and returns when that consumer’s batch loop finishes.

**Call relations**: JobRunner.fire invokes this handler when a page-change JobSpec fires. It hands control to PageChangeRunner.drive, which reads pages, calls the hook, and advances the cursor.


##### `core_jobs._consumer_candidates`  (lines 519–523)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: This nested factory creates a candidate-workspace function for one page-change consumer. It lets each hook decide workspaces based on its own saved cursor.

**Data flow**: It receives a PageChangeConsumer and returns an async function. That returned function will ask PageChangeRunner which workspaces have page changes for that consumer.

**Call relations**: core_jobs attaches the returned _candidates function to each page-change JobSpec. JobRunner.tick later calls it before queueing per-workspace executions.


##### `core_jobs._consumer_candidates._candidates`  (lines 520–521)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: This generated candidate finder returns the workspaces where one page-change consumer has pending work. It is specific to the consumer captured by its enclosing factory.

**Data flow**: It takes no direct arguments, calls page_change_runner.workspaces_with_changes for the captured consumer, and returns the workspace IDs it gets back.

**Call relations**: JobRunner.tick calls this through the JobSpec candidate hook. It hands the result back to JobRunner.tick, which queues one page-change workflow per workspace.


##### `bindings_from`  (lines 565–590)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...]) -> tuple[_Binding, ...]
```

**Purpose**: This function gives every job a full, unique binding that says which namespace it belongs to and what credentials it may use. Core jobs go under the core namespace; extension jobs go under their extension’s namespace.

**Data flow**: It receives extension manifests and the already-built core JobSpecs. It creates _Binding records for each core job with no declared credential slots, then creates _Binding records for each extension job using that extension’s declared credential slot names.

**Call relations**: Startup code uses this before constructing JobRunner. JobRunner later uses these bindings to register schedules, find candidate functions, and build the right ExtensionContext when a job fires.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 614–638)

```
def launch(self) -> None
```

**Purpose**: This method publishes this JobRunner as the active runner and registers all jobs with DBOS. Repeating jobs become schedules; one-shot jobs are enqueued once with deduplication so duplicate boots do not start duplicates.

**Data flow**: It stores itself in the module-level _firing variable, walks every binding, and checks whether the job has a schedule. Unscheduled jobs are enqueued immediately using the binding key as a deduplication ID. Scheduled jobs are collected as ScheduleInput records and applied through DBOS.apply_schedules.

**Call relations**: The server startup path calls this after discovering jobs. Later, DBOS invokes job_tick or job_workflow, and those workflow functions find this runner through _firing.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 640–662)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: This method handles one scheduled firing of a job. It fans that firing out into one durable job execution per workspace that actually has work.

**Data flow**: It receives the scheduled time and job key. If this process does not know that key, it logs a warning and skips it. Otherwise it asks the job’s candidate finder for workspace IDs, then enqueues job_workflow once per workspace using a deduplication ID made from the job key and workspace ID.

**Call relations**: DBOS calls job_tick, which delegates here. This method calls candidates to find workspaces and submits job_workflow to the job queue for the actual per-workspace run.

*Call graph*: calls 2 internal fn (_registered, candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 664–665)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: This method asks the registered job for the workspaces where it has pending work. It is a thin, safe lookup around the JobSpec’s candidate function.

**Data flow**: It receives a job key, finds the matching binding, calls that binding’s spec.candidates function, and returns the workspace IDs.

**Call relations**: JobRunner.tick calls this during fan-out. It uses _binding so an unknown key is treated as a programming or routing fault at this point.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 667–687)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: This method runs one job for one workspace. It is the only path that actually calls a job handler, and it always does so inside the selected workspace.

**Data flow**: It receives a job key and workspace ID, finds the binding, enters the workspace context, creates an ExtensionContext with the correct extension name, credentials, and services, then calls the job handler. If the handler raises an error, it logs the failure and re-raises so the durable workflow records the failure.

**Call relations**: DBOS calls job_workflow, which delegates here. This method hands execution to the core or extension handler stored in the JobSpec.

*Call graph*: calls 1 internal fn (_binding); 3 external calls (context_for, log_error, ws).


##### `JobRunner._registered`  (lines 689–690)

```
def _registered(self, key: str) -> _Binding | None
```

**Purpose**: This helper checks whether this process has a binding for a job key. It lets the runner distinguish an expected stale schedule from a real failure later in the flow.

**Data flow**: It receives a job key, scans the runner’s bindings, and returns the matching _Binding if found or None if not.

**Call relations**: JobRunner.tick uses this to skip old or unknown schedules safely. JobRunner._binding uses it as the first step before either returning the binding or raising an error.

*Call graph*: called by 2 (_binding, tick).


##### `JobRunner._binding`  (lines 692–699)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: This helper returns the binding for a job key and raises if none exists. It is used when the caller expects the job to be registered and missing data means something is wrong.

**Data flow**: It receives a job key, calls _registered, and either returns the binding or raises a RuntimeError naming the missing key.

**Call relations**: JobRunner.candidates and JobRunner.fire call this before accessing a JobSpec. JobRunner.tick does a softer _registered check first because old shared schedules are expected.

*Call graph*: calls 1 internal fn (_registered); called by 2 (candidates, fire).


##### `job_tick`  (lines 706–710)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: This is the DBOS workflow entry for a job schedule firing. It wakes the active JobRunner and asks it to fan the job out to candidate workspaces.

**Data flow**: It receives the scheduled time and job key from DBOS. It reads the module-level _firing runner, raises if jobs were not launched, and otherwise calls runner.tick.

**Call relations**: JobRunner.launch registers this workflow with DBOS schedules and one-shot enqueues. It delegates all real logic to JobRunner.tick.


##### `job_workflow`  (lines 714–718)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: This is the DBOS workflow entry for one job running in one workspace. It is the durable wrapper around the actual job handler.

**Data flow**: It receives the scheduled time, job key, and workspace ID as a string. It reads the active runner, converts the workspace ID into a UUID, and calls runner.fire to execute the handler inside that workspace.

**Call relations**: JobRunner.tick enqueues this workflow after it finds candidate workspaces. The workflow delegates to JobRunner.fire, which builds the context and calls the registered handler.

*Call graph*: 1 external calls (UUID).


### Scheduled wakeup runners
The scheduled-tasks extension package provides runners that wake expired pauses and due scheduled tasks, claim each item safely, and advance or clean up state.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/__init__.py`

`other` · `import time`

This file does not contain any code, but it still has a job. In Python projects, an `__init__.py` file is commonly used to say, “the files in this folder belong together as one importable package.” Here, it identifies `ufo_ext_scheduled_tasks` as the package for the scheduled tasks extension. Think of it like a label on a folder: the label does not do the work inside the folder, but it helps the rest of the system find and refer to that folder correctly. Without this file, depending on the Python version and packaging setup, imports or packaging tools might not treat this directory in the expected way. Because it is empty, it does not set up state, expose helper functions, or run any startup behavior.


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pause_runner.py`

`orchestration` · `recurring scheduled task`

A “pause” is a stored reminder to continue a conversation later, unless a member has already spoken and made the reminder unnecessary. This file is the clock-driven worker for those reminders. On each run, it asks the pause store for pauses that are due now and temporarily claims them with a lease, which is like putting a sticky note on the row saying “I am working on this” so another overlapping worker does not fire it too.

For each claimed pause, the runner first checks that the pause’s related “holds” are still claimable. If not, it quietly skips that row because some other path has already made it irrelevant. If the hold is still valid, it asks the main extension context to invoke the stored prompt as a scheduled conversation turn, acting on behalf of the member who originally created the pause. It also passes two saved watermarks, which are sequence markers used to ask: “Has a member spoken since this pause began?” If the answer is yes, the scheduled turn is not admitted; if no, the paused workflow resumes.

The important ordering is deliberate: invoke first, retire second. If the process crashes after invoking but before retiring the pause, the next run will retry using the same idempotency key, meaning the system recognizes it as the same scheduled action rather than creating a duplicate. Failures are collected and reported after the tick finishes trying all due pauses.

#### Function details

##### `PauseRunner.run`  (lines 32–41)

```
async def run(self) -> None
```

**Purpose**: This is the main tick of the pause runner. It finds pauses whose time has arrived, tries to fire each one, and reports which ones failed instead of stopping at the first error.

**Data flow**: It starts with the runner’s extension context and lease length. It creates a PauseStore, reads the current UTC time, asks the store to claim due pause rows, then sends each row to _fire. If any row raises an error while firing, it records that conversation id and error type; after all rows have been tried, it raises one combined RuntimeError if there were failures, otherwise it returns nothing.

**Call relations**: This method is the outer loop for the file. It creates the PauseStore, uses the current time to find work, and calls PauseRunner._fire for each claimed pause so the detailed resume-or-skip decision happens in one place.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `PauseRunner._fire`  (lines 43–56)

```
async def _fire(self, store: PauseStore, row: Pause) -> None
```

**Purpose**: This function tries to complete one specific pause. It either resumes the conversation as a scheduled turn or discovers the pause no longer needs to run, then retires the pause after a successful scheduled invocation.

**Data flow**: It receives a PauseStore and one Pause row. First it asks the store to claim the pause’s holds; if that fails, it stops without changing anything else. If the holds are still valid, it calls the extension context to invoke the saved prompt for the conversation, using a stable idempotency key based on the pause id and checking the saved member-message watermarks. After that invocation completes, it tells the store to retire the pause so it will not be considered due again.

**Call relations**: PauseRunner.run calls this once for each due pause it has claimed. Inside, it relies on PauseStore.claim_holds to confirm the pause still owns the right to fire, then hands the actual scheduled conversation turn to the extension context, and finally calls PauseStore.retire to remove the completed wait from future ticks.

*Call graph*: calls 2 internal fn (claim_holds, retire); called by 1 (run).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `recurring scheduled-task tick`

This file is the clock-driven worker for scheduled tasks. Think of it like a careful office assistant who checks a calendar every so often, picks up the items due now, and makes sure each one is delivered exactly once. That care matters because the worker may overlap with another copy of itself, or a deploy may interrupt and retry work. Without the safeguards here, the same scheduled task could fire twice, fire after it expired, or lose its place in the schedule.

The runner asks the schedule store for tasks that are due and temporarily “leases” them, meaning it marks them as claimed for a short time so another runner will not take the same work. Before firing a task, it checks whether the task has expired. It also calculates the following cron occurrence, because every schedule here is a cron-style repeating schedule. If the next occurrence would be beyond the task’s expiry time, it adds a special final instruction telling the agent to finish the task and ask the user whether to continue, change, or stop.

The actual message sent to the conversation is built by `fire_body`. It includes the scheduled time and the user’s task prompt, plus a stable idempotency key. An idempotency key is a “same job” label: if the same fire is retried, the system can recognize it instead of creating a duplicate turn. If the conversation accepts the scheduled turn, the stored task is advanced to its next run time. If invocation fails, the task keeps its leased occurrence so it can be retried.

#### Function details

##### `fire_body`  (lines 36–54)

```
def fire_body(task: ScheduledTask, runtime_instruction: str | None) -> tuple[str, str]
```

**Purpose**: Builds the message that will be delivered for one scheduled task fire, along with the stable key used to avoid duplicate delivery. Someone uses this when they are about to invoke the task inside its conversation.

**Data flow**: It takes a scheduled task and an optional extra instruction. It reads the task’s next scheduled time and prompt, formats a small scheduled-task block plus the prompt, optionally appends the runtime instruction, and creates a deduplication key from the task id and exact fire time. It returns both the finished inbound message and that key.

**Call relations**: This is called by `ScheduledTaskRunner._fire` after the runner has confirmed the task should still fire. It hands the prepared message and key back to `_fire`, which then passes them into the extension context invocation. Inside this helper, `scheduled_fire_key` supplies the stable key format expected by the wider system.

*Call graph*: called by 1 (_fire); 1 external calls (scheduled_fire_key).


##### `ScheduledTaskRunner.run`  (lines 62–71)

```
async def run(self) -> None
```

**Purpose**: Performs one full scheduled-task polling pass. It finds tasks due at the current time, tries to fire each one, and reports if any of those fires failed.

**Data flow**: It starts with the runner’s extension context. It creates a schedule store, records the current time, asks the store to claim due tasks for a limited lease period, and then processes each claimed task through `_fire`. It collects any failure messages and, if there were failures, raises an error naming them; otherwise it finishes quietly.

**Call relations**: This is the main entry for a recurring job tick. It sets up the `ScheduleStore`, uses the current time as the basis for claiming due work, and delegates the detailed safety checks and conversation invocation to `ScheduledTaskRunner._fire` for each task.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `ScheduledTaskRunner._fire`  (lines 73–108)

```
async def _fire(self, store: ScheduleStore, task: ScheduledTask, tick_at: datetime, expiry_checked_at: datetime) -> str | None
```

**Purpose**: Safely fires one claimed scheduled task, if it is still valid to do so. It checks expiry, verifies the claim still belongs to this runner, invokes the scheduled conversation turn, and advances the schedule after the turn is accepted.

**Data flow**: It receives the schedule store, one claimed task, the tick time, and the time at which expiry is being checked. First it asks the store to retire the task if it has expired. If not expired, it calculates the next cron fire time. If that next time would pass the task’s expiry, it prepares a final-fire instruction. It then checks that the claim still holds. If the claim is valid, it builds the inbound message and idempotency key, invokes the conversation as a scheduled turn, and, when a turn id comes back, stores the next run time. It returns no failure for success or harmless skips, and returns a short failure label if invocation raises an exception.

**Call relations**: `ScheduledTaskRunner.run` calls this once for each due task it has claimed. This function coordinates the lower-level pieces: it asks `ScheduleStore` to retire expired tasks, confirm the lease, and reschedule accepted fires; it asks `next_fire` for the next cron occurrence; and it asks `fire_body` to compose the message before sending it through the extension context.

*Call graph*: calls 4 internal fn (fire_body, claim_holds, reschedule, retire_if_expired); called by 1 (run); 1 external calls (next_fire).

## 📊 State Registers Touched

- `reg-workspace-membership` — The roster of workspaces, members, admins, seats, and which people belong where.
- `reg-extension-pack-registry` — The selected packs and loaded extensions that decide which features, tools, routes, jobs, and backends exist.
- `reg-extension-store` — Per-workspace extension pins and extension-owned settings saved so enabled add-ons survive restarts.
- `reg-turn-queue` — The durable waiting line and status record for each unit of agent work, from queued to running to finished or failed.
- `reg-background-job-schedule` — The durable list of background jobs and dispatch rules used to retry and run work outside user requests.
- `reg-scheduled-automation` — Future and repeating tasks, pauses, wakeups, and their last-run state for long-running automation.
- `reg-source-sync-state` — External source records, sync cursors, saved pages, deletion markers, and retry or backoff status.
- `reg-automation-objectives` — Durable objectives, steps, monitors, checks, blockers, and evidence for work that continues across turns.
- `reg-database-connection-pool` — Per-process database engine/session pools and workspace-scoped connection context used by servers, workers, migrations, and persistence code.
- `reg-outbound-delivery-queue` — Pending outbound surface writebacks and retry state for messages or notifications sent back to external channels such as Slack.
- `reg-execution-scope-context` — Per-request, per-job, and per-turn scoped context carrying the active workspace, member, agent, turn, permissions, credentials, billing, and service handles through core code.
- `reg-source-trigger-state` — Durable source-change trigger subscriptions and wakeup markers that connect synced-record updates to conversations, reviews, monitors, or automation resumes.
- `reg-durable-workflow-state` — DBOS/workflow-runtime execution metadata for durable job and turn workflows, including workflow IDs, retries, scheduled starts, cancellation, and resume bookkeeping.
