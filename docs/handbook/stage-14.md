# Background schedulers, recurring jobs, and offline maintenance  `stage-14`

This stage is the system’s night shift. It runs outside the normal request path, using clocks and cleanup loops to keep work moving when no user is actively clicking. The candidates helper safely finds which workspaces may have pending work, then hands each workspace back to its normal protected area before anything real happens. The jobs runner turns registered job definitions into scheduled runs, making sure they use the right workspace and extension settings and do not pile up duplicates.

Several runners act like alarm clocks. The monitor runner checks saved watches when they are due and notifies agents about changes, repeated failures, or deadlines. The scheduled task runner fires timed tasks once and advances repeat tasks to their next time. The pause runner resumes conversations whose wait time has ended, unless a person already resumed them.

Other pieces clean up loose ends. The delivery loop returns missed child-task results to their parent conversation. The runtime instance records which server processes are alive, recovers abandoned work, and passes cancellations down to child work. The self-improvement cron tests possible prompt changes and only proposes ones that repeatedly pass checks.

## Files in this stage

### Workspace Job Dispatch
Discovers workspaces with pending background work and turns job definitions into safe, deduplicated scheduled execution.

### `core/src/ufo/candidates.py`

`domain_logic` · `job scheduling and dispatch preparation`

This file solves a delicate problem: a background job needs to know where work exists, but it must not freely read data from every workspace. In this system, workspace data is normally protected by row-level security, often shortened to RLS, which means the database only shows rows that belong to the currently selected workspace. The one exception here is a special cross-workspace read called `owner_tx`. This file keeps that exception narrow and controlled.

The main idea is simple: extensions are allowed to describe a database question that returns only workspace IDs, not actual tenant data. `owner_candidates` wraps that question in a callable that core can run whenever the scheduler ticks. Running it each time matters because “due now” can depend on the current time; building the query once too early would freeze that decision.

The result is like a receptionist making a list of room numbers where help is needed, without entering the rooms or reading anyone’s papers. Later, the dispatcher takes each workspace ID and opens the normal workspace-scoped context before running the job. If no IDs are returned, the job runs nowhere. If IDs are returned, every real handler run is safely bound to one workspace.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: This function turns a query-builder into a safe workspace-candidate finder. An extension gives it a function that builds a database query returning workspace IDs, and it returns an async callable that core can run later during scheduling.

**Data flow**: It receives `due`, a no-argument function that builds a SQL query selecting one column: workspace IDs. It wraps that builder inside an async `candidates` function. The result that comes out is not the IDs immediately, but a callable that will fetch fresh IDs each time it is awaited.

**Call relations**: This is the public seam extensions use instead of touching the cross-workspace database path directly. The scheduler or dispatcher can call the returned candidate function when it needs to decide which workspaces should receive a job run.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner async function actually performs the controlled cross-workspace read. It runs the latest query built by `due()` and returns only the first column of each row as workspace IDs.

**Data flow**: When called, it opens `owner_tx`, the special database transaction that can read across workspaces. Inside that transaction it builds and executes the current `due()` query, collects all result rows, closes the transaction, and returns a tuple of UUID workspace IDs taken from the first column of each row.

**Call relations**: It is produced by `owner_candidates` and later called by core scheduling code when checking for due work. Its only direct handoff is to `ufo.db.owner_tx`, which provides the temporary cross-workspace connection; after that, the dispatcher is expected to re-bind each returned workspace ID before any real job handler runs.

*Call graph*: 1 external calls (owner_tx).


### `core/src/ufo/jobs.py`

`orchestration` · `startup and scheduled background job execution`

This file is the background-job control room. At startup, the system discovers core jobs and extension-provided jobs. This file registers the repeating ones with DBOS, the durable workflow system, and queues one-time jobs. DBOS is used so jobs survive restarts and are not lost if a process dies.

The main pattern is two-step. First, a small “tick” workflow wakes up for a job and asks which workspaces actually need that job. Then it queues one separate workflow per workspace. This matters because one slow or stuck workspace should not block others, and repeated ticks should not stack duplicate work for the same job and workspace.

The file also defines several core jobs. One polls external sources for changed pages. One dispatches queued conversation turns back onto the turn-processing queue. One delivers finished subagent results. One runs page-change hooks declared by extensions, each with its own cursor so two hooks do not block or overwrite each other.

A useful analogy is a train dispatcher. The schedule says when a train may leave, but the dispatcher checks which stations actually have passengers, sends one train per station, and avoids sending a second train to the same station while the first is still running.

#### Function details

##### `ResultDeliverer.run`  (lines 67–67)

```
async def run(self) -> None
```

**Purpose**: This is the interface method for sweeping finished subagent results back into the main conversation flow. The actual work lives elsewhere; this file only needs to know that such a sweep can be run as a job.

**Data flow**: It takes no explicit input besides the implementing object → the implementation looks for finished child work and posts the result where it belongs → it returns nothing, but may change conversation state.

**Call relations**: The core job list wraps this method in a job handler through core_jobs._deliver_results, and JobRunner later runs that handler inside a workspace.


##### `ResultDeliverer.candidate_workspaces`  (lines 69–69)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This is the interface method for asking which workspaces have subagent results waiting to be delivered. It lets the job system avoid opening workspaces with no relevant work.

**Data flow**: It takes no explicit input besides the implementing object → the implementation checks its result-delivery backlog → it returns the workspace IDs that should receive a delivery sweep.

**Call relations**: core_jobs uses this method as the candidate finder for the result-delivery job. JobRunner.tick calls the candidate finder before queuing per-workspace executions.


##### `TurnDispatcher.run`  (lines 132–163)

```
async def run(self) -> None
```

**Purpose**: This scans for conversation turns that are ready, or ready again, to be placed on the conversation worker queue. It also rechecks parked turns to make sure the member has a seat and the workspace is allowed to spend before letting them resume.

**Data flow**: It reads dispatchable turn rows from the workspace database → for parked turns, it checks seating and spending permission → eligible turns are stamped and enqueued for actual turn processing; ineligible parked turns are left alone.

**Call relations**: A scheduled core job calls this through core_jobs._dispatch_turns. It first asks TurnDispatcher._dispatchable_turns what can move, and then hands each eligible turn to TurnDispatcher._enqueue.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 5 external calls (__init__, __init__, select, workspace_tx, gate_member).


##### `TurnDispatcher.candidate_workspaces`  (lines 165–173)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This finds workspaces that have queued or parked turns ready for the dispatcher. It prevents the dispatcher job from running in workspaces that have no turn work.

**Data flow**: It computes a cutoff time for stale dispatch stamps → queries the owner-level database view for distinct workspaces with eligible turns → returns those workspace IDs.

**Call relations**: core_jobs gives this method to the turn-dispatch job as its candidate finder. JobRunner.tick calls it before queuing one dispatch workflow per workspace.

*Call graph*: calls 1 internal fn (_eligible); 4 external calls (now, timedelta, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 175–214)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: This gathers a small batch of turn rows that are allowed to be offered to the worker queue. It preserves order within each conversation so later turns cannot jump ahead of earlier ones.

**Data flow**: It computes a stale-stamp cutoff → queries the current workspace for eligible queued or parked turns, ordered by status, creation time, and sequence → returns lightweight _DispatchTurn records.

**Call relations**: TurnDispatcher.run calls this at the start of its sweep. The eligibility rule it uses is built by TurnDispatcher._eligible.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 6 external calls (__init__, now, timedelta, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 216–246)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: This safely offers one selected turn to the DBOS turn-processing queue. It first marks the row as dispatched in the database so two dispatchers do not both offer the same turn.

**Data flow**: It receives a _DispatchTurn → atomically updates that database row only if it is still in the same state, still stale, and still first in line → if the update succeeds, it enqueues the DBOS workflow with an appropriate workflow ID; if not, it does nothing.

**Call relations**: TurnDispatcher.run calls this after a turn passes any parked-turn checks. It uses TurnDispatcher._first_in_status and TurnDispatcher._stale to make the database update safe under concurrency.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 1 (run); 5 external calls (now, timedelta, update, workspace_tx, uuid4).


##### `TurnDispatcher._eligible`  (lines 248–256)

```
def _eligible(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for turns that the dispatcher may consider. It captures the rules: the turn must be queued or parked, its previous dispatch stamp must be missing or old, and it must be the earliest turn of that status in its conversation.

**Data flow**: It receives a cutoff time → combines status, staleness, and ordering checks into a SQL condition → returns that condition for use in queries.

**Call relations**: TurnDispatcher.candidate_workspaces uses it to find workspaces with work, and TurnDispatcher._dispatchable_turns uses it to fetch the actual turn rows.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 2 external calls (and_, or_).


##### `TurnDispatcher._stale`  (lines 258–262)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for a dispatch offer that is safe to retry. A turn is stale if it has never been enqueued or if its enqueue stamp is older than the grace window.

**Data flow**: It receives a cutoff time → checks whether dispatch_enqueued_at is empty or earlier than that cutoff → returns a SQL condition.

**Call relations**: TurnDispatcher._eligible uses it while scanning, and TurnDispatcher._enqueue uses it again during the final atomic update so stale information cannot cause a duplicate offer.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 264–273)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition that says a turn is the first turn of its status in its conversation. It protects conversation order.

**Data flow**: It receives a turn status such as queued or parked → looks for any earlier turn in the same workspace and conversation with that same status → returns a condition that is true only when no earlier matching turn exists.

**Call relations**: TurnDispatcher._eligible uses it to scan only first-in-line rows, and TurnDispatcher._enqueue uses it again before stamping the row.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `_page_beyond_cursor`  (lines 276–281)

```
def _page_beyond_cursor(revision: int, page_id: UUID, cursor: object) -> bool
```

**Purpose**: This answers whether a page change is newer than a stored cursor. A cursor is a bookmark showing how far a page-change consumer has already read.

**Data flow**: It receives a page revision, page ID, and cursor value → treats a missing cursor as meaning everything is new, otherwise parses the cursor and compares revision then ID → returns true if the page lies after the cursor.

**Call relations**: PageChangeRunner.workspaces_with_changes uses this when deciding which workspaces have page changes pending for one consumer.

*Call graph*: called by 1 (workspaces_with_changes); 1 external calls (page_cursor).


##### `PageChangeConsumer.spec_name`  (lines 302–304)

```
def spec_name(self) -> str
```

**Purpose**: This gives one page-change consumer its unique job-spec name. The name separates hooks by extension and handler function so each hook can run and track progress independently.

**Data flow**: It reads the consumer’s extension name and discriminator → formats them into a page_change job name → returns that string.

**Call relations**: core_jobs uses PageChangeRunner.consumers, whose consumers expose this property when building one JobSpec per page-change hook.


##### `PageChangeConsumer.job`  (lines 307–312)

```
def job(self) -> str
```

**Purpose**: This gives the binding key used when a page-change consumer runs as a core-driven job. It is also used for attribution, such as charging model use to the right background job.

**Data flow**: It reads the consumer’s spec_name → prefixes it with the core extension namespace → returns the full job key.

**Call relations**: PageChangeRunner._context_for uses this value when building the ExtensionContext passed to the hook.


##### `PageChangeRunner.consumers`  (lines 355–379)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: This discovers all registered page_change hooks from the active extension manifests. It also rejects duplicate handler names inside the same extension because those would collide on job names and cursor keys.

**Data flow**: It reads each manifest’s credentials and hooks → keeps only hooks whose event is page_change → creates PageChangeConsumer records with extension name, declared credential slots, hook spec, and handler-name discriminator → returns the full tuple of consumers.

**Call relations**: core_jobs calls this while building the deploy’s core jobs. Each returned consumer becomes its own scheduled page-change job.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 381–446)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: This finds the workspaces where a specific page-change consumer has unread page changes. It keeps the periodic page-change jobs from doing empty work.

**Data flow**: It reads each workspace’s newest page and that consumer’s stored cursor → compares the newest page to the cursor, treating bad cursors as pending and logging a warning → returns workspace IDs where the newest page is beyond the cursor.

**Call relations**: core_jobs._consumer_candidates wraps this as the candidate finder for each page-change job. It uses _page_beyond_cursor for the cursor comparison.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 3 external calls (select, owner_tx, warn).


##### `PageChangeRunner.drive`  (lines 448–472)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: This runs one page-change consumer inside one already-selected workspace. It reads changed pages in batches, calls the hook, and advances that hook’s cursor only after the hook succeeds.

**Data flow**: It receives a PageChangeConsumer → builds an extension context, reads the stored cursor, asks the page feed for changed pages after that cursor, and passes each batch to the hook → after each successful batch it stores the next cursor; if there are no changes or another writer moved the cursor, it stops.

**Call relations**: The page-change job handler created by core_jobs._drive_consumer calls this. It relies on PageChangeRunner._context_for to create the hook’s runtime context.

*Call graph*: calls 1 internal fn (_context_for); 2 external calls (__init__, __init__).


##### `PageChangeRunner._context_for`  (lines 474–492)

```
def _context_for(self, consumer: PageChangeConsumer) -> ExtensionContext
```

**Purpose**: This builds the ExtensionContext used by a page-change hook. The context is the hook’s toolbox: store access, page feed, model access, optional indexing, blobs, sandboxes, and probes.

**Data flow**: It receives a PageChangeConsumer → optionally creates a turn invoker for the current workspace and swaps the model registry to the configured background model → returns a ready ExtensionContext for that extension and job.

**Call relations**: PageChangeRunner.drive calls this before invoking the page-change handler. It delegates model selection to _background_registry and uses the current workspace binding.

*Call graph*: calls 1 internal fn (_background_registry); called by 1 (drive); 2 external calls (context_for, ws_current).


##### `_background_registry`  (lines 495–506)

```
def _background_registry(registry: ModelRegistry | None, background_model: str | None) -> ModelRegistry | None
```

**Purpose**: This returns a model registry adjusted for background jobs. If a background model is configured, it replaces the registry’s default automatic model while keeping the rest of the registry intact.

**Data flow**: It receives an optional registry and optional background model name → if either is missing, it returns the registry unchanged; otherwise it copies the registry with auto_model replaced → returns the registry to use for job model calls.

**Call relations**: PageChangeRunner._context_for uses it for page-change hooks, and JobRunner.fire uses it for ordinary jobs unless a job explicitly needs the deploy’s normal model.

*Call graph*: called by 2 (fire, _context_for); 1 external calls (replace).


##### `core_jobs`  (lines 509–580)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, page_change_runner: PageChangeRunner, delivery_sweep: ResultDeliverer) -> tuple[JobSpec, ...]
```

**Purpose**: This builds the set of jobs the core system always contributes. These include source syncing, page-change hook driving, turn dispatch recovery, and subagent result delivery.

**Data flow**: It receives the core service objects for syncing, turn dispatch, page-change running, and result delivery → wraps their methods in JobSpec objects with schedules and candidate finders → returns the complete tuple of core JobSpec definitions.

**Call relations**: Startup code can combine this output with extension jobs through bindings_from. Inside it, PageChangeRunner.consumers supplies one JobSpec per page-change consumer.

*Call graph*: calls 1 internal fn (consumers); 1 external calls (__init__).


##### `core_jobs._sync_sources`  (lines 528–529)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: This small job handler runs the source synchronization driver. It is the function DBOS ultimately calls when the source-sync job fires inside a workspace.

**Data flow**: It receives an ExtensionContext but does not need to read it → calls the sync driver’s run method → returns nothing after source pages have been polled and stored.

**Call relations**: core_jobs places this function inside the SOURCE_SYNC_JOB JobSpec. JobRunner.fire later invokes it through that JobSpec.


##### `core_jobs._dispatch_turns`  (lines 531–532)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: This small job handler runs the turn dispatcher sweep. It exists so the dispatcher can fit the common JobSpec handler shape.

**Data flow**: It receives an ExtensionContext but does not use it → calls TurnDispatcher.run → returns after eligible queued or parked turns have been offered to the turn queue.

**Call relations**: core_jobs places this function inside the TURN_DISPATCH_JOB JobSpec. JobRunner.fire invokes it when that scheduled job is assigned to a workspace.


##### `core_jobs._deliver_results`  (lines 534–535)

```
async def _deliver_results(context: ExtensionContext) -> None
```

**Purpose**: This small job handler runs the subagent result-delivery sweep. It adapts the ResultDeliverer interface to the common job handler shape.

**Data flow**: It receives an ExtensionContext but does not use it → calls delivery_sweep.run → returns after any deliverable results have been processed.

**Call relations**: core_jobs places this function inside the RESULT_DELIVERY_JOB JobSpec. JobRunner.fire later runs it in each candidate workspace.


##### `core_jobs._drive_consumer`  (lines 537–543)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: This creates a job handler for one page-change consumer. Each consumer gets its own handler so it can have its own schedule entry and cursor.

**Data flow**: It receives a PageChangeConsumer → builds and returns an async handler function that closes over that consumer → the returned handler will drive that exact consumer when called.

**Call relations**: core_jobs calls this while constructing page-change JobSpecs. The returned core_jobs._drive_consumer._handler is what JobRunner.fire eventually invokes.


##### `core_jobs._drive_consumer._handler`  (lines 540–541)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: This is the actual page-change job handler for one consumer. It asks PageChangeRunner to replay changed pages to that consumer’s hook.

**Data flow**: It receives an ExtensionContext from the job framework but does not use it directly → calls PageChangeRunner.drive with the captured consumer → returns when that consumer’s pending page batches are drained or the cursor cannot safely advance.

**Call relations**: core_jobs._drive_consumer creates this function. It hands off the real work to PageChangeRunner.drive.


##### `core_jobs._consumer_candidates`  (lines 545–549)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: This creates a candidate-finder function for one page-change consumer. It lets each consumer decide independently which workspaces have unread page changes.

**Data flow**: It receives a PageChangeConsumer → builds and returns an async candidate function that closes over that consumer → the returned function later returns workspace IDs with pending changes.

**Call relations**: core_jobs calls this while constructing each page-change JobSpec. The returned core_jobs._consumer_candidates._candidates is called by JobRunner.tick through JobRunner.candidates.


##### `core_jobs._consumer_candidates._candidates`  (lines 546–547)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: This asks the page-change runner which workspaces have pending work for the captured consumer. It is the candidate finder used by one page-change job.

**Data flow**: It takes no explicit input → calls PageChangeRunner.workspaces_with_changes for the captured consumer → returns the workspace IDs with unread page changes.

**Call relations**: core_jobs._consumer_candidates creates this function. JobRunner.tick reaches it through the JobSpec candidate path before queuing workspace-specific workflows.


##### `bindings_from`  (lines 593–620)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...]) -> tuple[_Binding, ...]
```

**Purpose**: This combines core job specs and extension job specs into executable bindings. A binding records the job key, the extension namespace, the declared credential slots, and any extension manifest details needed at runtime.

**Data flow**: It receives active manifests and core JobSpec objects → creates core bindings under the core namespace and extension bindings under each extension’s own namespace → returns all bindings as a tuple.

**Call relations**: JobRunner is constructed with these bindings. Later, JobRunner.launch registers them and JobRunner.fire uses them to build the correct ExtensionContext.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 648–672)

```
def launch(self) -> None
```

**Purpose**: This publishes the current JobRunner globally and registers every job with DBOS. Repeating jobs become schedules; one-shot jobs are enqueued once with deduplication so duplicate process starts do not create duplicate work.

**Data flow**: It reads the runner’s bindings → for unscheduled jobs, enqueues a job_tick with a deduplication key; for scheduled jobs, builds schedule definitions → applies schedules through DBOS and logs what happened.

**Call relations**: This is called during service startup. The DBOS workflows job_tick and job_workflow depend on the global runner set here.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 674–696)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: This is one scheduled firing of a job. It fans the job out into one queued workflow per workspace that actually has work.

**Data flow**: It receives the scheduled time and job key → skips keys this process does not know, otherwise asks for candidate workspaces → enqueues job_workflow once per workspace with a deduplication key based on job and workspace.

**Call relations**: The DBOS workflow job_tick calls this. It uses JobRunner.candidates to find workspaces and queues job_workflow for the real per-workspace execution.

*Call graph*: calls 2 internal fn (_registered, candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 698–699)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: This asks a job binding which workspaces should run for a given job key. It is a thin doorway to the candidate finder stored in the JobSpec.

**Data flow**: It receives a job key → looks up the binding → calls that binding’s JobSpec candidates function → returns the workspace IDs.

**Call relations**: JobRunner.tick calls this after confirming the key is registered. It depends on JobRunner._binding for the lookup.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 701–728)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: This runs one job handler for one workspace. It binds the workspace, prepares the extension context, provisions extension agents if needed, and calls the handler.

**Data flow**: It receives a job key and workspace ID → finds the binding, enters that workspace’s scope, optionally applies agent provisioning, builds an ExtensionContext with the right model registry and tools → calls the job handler; if it fails, it logs the error and re-raises it.

**Call relations**: The DBOS workflow job_workflow calls this for each workspace-specific job run. It uses JobRunner._binding for lookup and _background_registry when the job should use the background model.

*Call graph*: calls 2 internal fn (_binding, _background_registry); 4 external calls (__init__, context_for, log_error, ws).


##### `JobRunner._registered`  (lines 730–731)

```
def _registered(self, key: str) -> _Binding | None
```

**Purpose**: This checks whether the current process knows a binding for a job key. It allows old or foreign schedule entries to be skipped safely.

**Data flow**: It receives a job key → searches the runner’s bindings → returns the matching binding, or None if this process does not own that key.

**Call relations**: JobRunner.tick uses this to skip unregistered schedules without failing. JobRunner._binding uses it when a binding is required.

*Call graph*: called by 2 (_binding, tick).


##### `JobRunner._binding`  (lines 733–740)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: This retrieves the binding for a job key and treats a missing binding as an error. It is used when the caller is about to run real work and must have a valid binding.

**Data flow**: It receives a job key → calls JobRunner._registered → returns the binding if found, otherwise raises a RuntimeError.

**Call relations**: JobRunner.candidates and JobRunner.fire call this before using a job’s candidate finder or handler.

*Call graph*: calls 1 internal fn (_registered); called by 2 (candidates, fire).


##### `job_tick`  (lines 747–751)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: This is the DBOS durable workflow for a job tick. It wakes up for a scheduled or one-shot job and asks the active JobRunner to fan it out by workspace.

**Data flow**: It receives the scheduled time and job key from DBOS → checks that JobRunner.launch has installed a runner → calls runner.tick; if no runner exists, it raises an error.

**Call relations**: JobRunner.launch registers or enqueues this workflow. It hands off immediately to JobRunner.tick.


##### `job_workflow`  (lines 755–759)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: This is the DBOS durable workflow for running one job in one workspace. It is the final step after a tick has chosen candidate workspaces.

**Data flow**: It receives the scheduled time, job key, and workspace ID string → checks that a runner exists, converts the workspace ID into a UUID → calls runner.fire to do the actual job work.

**Call relations**: JobRunner.tick enqueues this workflow for each candidate workspace. It hands off to JobRunner.fire, which creates the workspace-scoped context and invokes the handler.

*Call graph*: 1 external calls (UUID).


### Runtime Recovery
Keeps background execution healthy by returning missed child-task results, tracking live processes, cleaning abandoned work, and propagating cancellations.

### `core/src/ufo/loop/delivery.py`

`orchestration` · `background scheduled result-delivery sweep`

When one conversation delegates work to a child turn, the parent expects to be woken up when that child finishes. Usually that happens naturally at the end of the child’s run. But some endings happen from the outside, such as cancellation by another process or a crash after the result was saved but before the wake-up was posted. In those cases, the result is safely stored in the database, but the parent may never hear about it.

This file provides the backstop sweep for that situation. Think of it like a mailroom worker who periodically checks for completed packages that were never delivered. It looks in durable database state, not in the live execution flow, for child turns that are finished but still marked as needing result delivery. It groups those children by the parent conversation they belong to, so if several children finish around the same time, the parent can be woken once with a batch of results instead of being disturbed repeatedly.

It also has a cooldown rule. If a conversation was already woken recently by another delivery path, this sweep skips it for now. That avoids a loop where a parent wakes, starts more children, and is immediately woken again. The actual delivery is done through `SubagentResult`, the same path used by normal event-based delivery, so both routes can race safely and only one successful arrival is recorded.

#### Function details

##### `DeliverySweep.run`  (lines 50–65)

```
async def run(self) -> None
```

**Purpose**: This is the main pass of the delivery sweep. It finds child turns whose results are ready but not yet delivered, skips parent conversations that were already woken very recently, and sends the remaining results back to their parents.

**Data flow**: It starts with no direct input beyond the sweep object’s configured invoker factory and subagent registry. It asks `_outstanding` for finished child turns still waiting for delivery, calculates a recent-time cutoff, asks `_woken_since` which parent conversations were already woken after that cutoff, then builds a `SubagentResult` delivery helper for the current workspace. For each not-recently-woken parent conversation, it feeds each child turn to the delivery helper. The visible output is not a returned value; the important change is that pending child results are posted back toward their parent conversations.

**Call relations**: The scheduled job calls this as the sweep body. Inside the pass, it first relies on `_outstanding` to discover work, then on `_woken_since` to avoid waking the same conversation too aggressively. It hands each eligible child turn to `SubagentResult.deliver`, using the same delivery mechanism as the normal event path.

*Call graph*: calls 2 internal fn (_outstanding, _woken_since); 4 external calls (__init__, now, timedelta, ws_current).


##### `DeliverySweep.candidate_workspaces`  (lines 67–79)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This finds which workspaces may need the delivery sweep to run. It lets the scheduler avoid checking every workspace when only some have finished child turns waiting for result delivery.

**Data flow**: It opens an owner-level database transaction, which can see workspace ownership-wide data. It searches the turn table for distinct workspace IDs where a turn is terminal, meaning finished, and still marked as pending result delivery. It returns those workspace IDs as a tuple. It does not change the database.

**Call relations**: This supports the wider job scheduling flow: before running the sweep inside a workspace, the scheduler can ask this method where there is likely work. It uses `owner_tx` for a broad database read and SQLAlchemy’s query builder to form the database query.

*Call graph*: 2 external calls (select, owner_tx).


##### `DeliverySweep._outstanding`  (lines 81–114)

```
async def _outstanding(self) -> dict[UUID, list[Turn]]
```

**Purpose**: This gathers the actual child turns in the current workspace whose results need the safety-net delivery. It groups them under the parent conversation that should be woken.

**Data flow**: It reads from the workspace database. It joins each pending, finished child turn to its parent turn, so it can learn the parent conversation ID. It orders the rows by parent conversation and by the child’s update time, then limits the number of children in one batch. Each database row is converted into a `Turn` record, and the function returns a dictionary: parent conversation ID → list of child turns awaiting delivery.

**Call relations**: `DeliverySweep.run` calls this at the start of a sweep to find work. This function does not deliver anything itself; it prepares the grouped list that lets `run` wake one parent conversation with its outstanding child results together.

*Call graph*: called by 1 (run); 3 external calls (model_validate, select, workspace_tx).


##### `DeliverySweep._woken_since`  (lines 116–143)

```
async def _woken_since(self, cutoff: datetime, conversations: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: This checks whether any of the parent conversations under consideration were already woken by a child-result delivery recently. It prevents the sweep from waking the same conversation too often.

**Data flow**: It receives a cutoff time and a tuple of conversation IDs to check. It queries the workspace database for delivered child turns whose parent conversation is in that set and whose delivery timestamp is newer than the cutoff. It returns a frozen set of conversation IDs that should be skipped for this sweep pass. It only reads data; it does not change anything.

**Call relations**: `DeliverySweep.run` calls this after finding outstanding child results. The returned set acts like a temporary do-not-disturb list: `run` leaves those conversations’ children pending for the next sweep, while continuing delivery for conversations not found in the set.

*Call graph*: called by 1 (run); 2 external calls (select, workspace_tx).


### `core/src/ufo/runtime_instance.py`

`orchestration` · `main loop / cross-cutting background maintenance`

A serve process is one running copy of the system. This file gives each process a “seat” in the database, like signing a name on a whiteboard to say “I am here.” The process regularly refreshes that row with a heartbeat timestamp. Other processes use that timestamp to decide whether the process is still alive.

That liveness signal matters because DBOS, the durable workflow system used here, records which executor process owns pending work. If a process crashes, some workflows may still be marked as pending under that dead executor. The ExecutorRecovery loop compares pending workflow owners with fresh heartbeat rows. If an owner has gone stale, it asks DBOS to recover that work so another live process can continue it. It avoids touching fresh executors, because doing so could start the same workflow twice.

The file also contains a CancelReconciler loop. Cancelling one turn directly only stops that one turn. This loop periodically looks for still-running descendant turns underneath a cancelled ancestor and cancels them too. It walks up parent links so grandchildren are caught, but it deliberately does not cross certain agent boundaries where a spawned agent should keep running independently.

Together, these loops make the fleet self-healing: every process does the same sweeps, so if one dies, the survivors can clean up.

#### Function details

##### `record_fleet_seat`  (lines 38–53)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: This records that the current serve process exists by inserting a runtime instance row in the database. It is done before DBOS starts so other recovery loops do not mistake this process’s future work for abandoned work.

**Data flow**: It receives the process instance id. It opens an owner-level database transaction, writes a runtime_instance row with that id, no workspace, and current timestamps, then logs that the fleet seat was recorded. It does not return a value; the lasting result is the database row.

**Call relations**: This is the first step in making a process visible to the fleet. It uses the shared database transaction helper to write the row, and the heartbeat loop later keeps that same row fresh.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 66–76)

```
async def run(self) -> None
```

**Purpose**: This is the never-ending heartbeat loop for one process. It keeps trying to refresh the process’s liveness row so peers can tell the process is still alive.

**Data flow**: It reads the instance id stored on the Heartbeat object. On each cycle it calls Heartbeat.beat to update the database row, logs database failures without stopping, then waits a short interval before trying again. It normally produces no final result because it is meant to run until shutdown.

**Call relations**: The serve process runs this loop in the background after its seat exists. It repeatedly hands the actual database update to Heartbeat.beat, and uses sleep to space out heartbeats so the database is not updated continuously.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 78–88)

```
async def beat(self) -> None
```

**Purpose**: This performs one heartbeat update. It stamps the current process’s runtime instance row with the current database time.

**Data flow**: It uses the Heartbeat object’s instance id. It opens a database transaction and updates the matching runtime_instance row’s heartbeat_at and updated_at fields to now. It returns nothing; after it finishes, the row says this process was recently alive.

**Call relations**: Heartbeat.run calls this on every heartbeat tick. ExecutorRecovery._live_executors later reads these timestamps to decide which executors are safe to leave alone.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 90–96)

```
async def retire(self) -> None
```

**Purpose**: This removes the process’s liveness row during graceful shutdown. It lets the rest of the fleet see immediately that this process has left instead of waiting for its heartbeat to grow stale.

**Data flow**: It uses the Heartbeat object’s instance id. It opens a database transaction and deletes the matching runtime_instance row. It returns nothing; the database no longer lists this process as present.

**Call relations**: The serve shutdown path calls this through core/src/ufo/serve._stop_executor. After retirement, recovery sweeps can treat any still-pending work under that executor as needing recovery.

*Call graph*: called by 1 (_stop_executor); 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 116–122)

```
async def run(self) -> None
```

**Purpose**: This is the recurring background loop that looks for work stranded under dead executor processes. It keeps the system from leaving pending workflows stuck after a crash.

**Data flow**: It reads its configured interval from the ExecutorRecovery object. Each cycle waits for that interval, calls ExecutorRecovery.sweep, and logs database or DBOS errors without ending the loop. It has no normal final output because it is meant to keep running.

**Call relations**: Every serve process can run this loop. It delegates the real recovery decision to ExecutorRecovery.sweep, so any surviving process can reclaim work left behind by a failed peer.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 124–132)

```
async def sweep(self) -> None
```

**Purpose**: This performs one recovery pass. It finds executors that own pending workflows but do not have a fresh heartbeat, then asks DBOS to recover their pending workflows.

**Data flow**: It gathers two sets: executor ids with pending workflows, and executor ids that are currently live. It subtracts live ids from pending ids to find stranded executors. For each stranded executor, it calls DBOS recovery in a worker thread and logs how many workflows were recovered. It returns nothing; the effect is that abandoned workflows are re-dispatched by DBOS.

**Call relations**: ExecutorRecovery.run calls this on each interval. This method relies on ExecutorRecovery._pending_executors to learn who has pending work and ExecutorRecovery._live_executors to learn who is still alive, then hands stranded executor ids to DBOS recovery.

*Call graph*: calls 2 internal fn (_live_executors, _pending_executors); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_executors`  (lines 134–146)

```
async def _pending_executors(self) -> set[str]
```

**Purpose**: This asks DBOS which executor ids currently own pending workflows. It is the recovery sweep’s view of “who has unfinished durable work.”

**Data flow**: It takes no direct input beyond configuration constants. It runs DBOS.list_workflows in a separate thread, requesting pending workflows without loading their full inputs or outputs. It returns a set of executor id strings found on those workflow status records, and logs a warning if the scan reached its limit.

**Call relations**: ExecutorRecovery.sweep calls this before deciding what to recover. Its result is compared with ExecutorRecovery._live_executors so only work owned by non-live executors is recovered.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 148–158)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: This reads the database to find executor ids whose heartbeat is still fresh. These executors are treated as alive and must not have their workflows recovered elsewhere.

**Data flow**: It reads the stale-after setting from the ExecutorRecovery object. It computes a cutoff time, opens a database transaction, selects runtime_instance rows with heartbeat_at at or after that cutoff, and returns their ids as strings. It does not change the database.

**Call relations**: ExecutorRecovery.sweep calls this together with ExecutorRecovery._pending_executors. Its output protects live processes from accidental duplicate recovery.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `CancelReconciler.run`  (lines 182–188)

```
async def run(self) -> None
```

**Purpose**: This is the recurring background loop that spreads cancellation from cancelled turns down to their still-running descendants. It makes cancellation eventually reach the whole affected subtree.

**Data flow**: It reads the interval and DBOS client stored on the CancelReconciler object. Each cycle waits, calls CancelReconciler.sweep, and logs database or DBOS failures without stopping. It normally never returns because it is designed as a background service.

**Call relations**: Every serve process may run this loop. It delegates the actual search and cancellation work to CancelReconciler.sweep, so a surviving process can finish cancellation cleanup even if another process crashed.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 190–197)

```
async def sweep(self) -> None
```

**Purpose**: This performs one cancellation cleanup pass. It finds live turns that sit below a cancelled ancestor and cancels each one through the normal turn-cancellation path.

**Data flow**: It opens a database transaction and runs the query built by CancelReconciler._orphans_query. For each returned turn id and workspace id, it enters that workspace context, calls cancel_one_turn with the DBOS client, and logs when a turn was actually cancelled. It returns nothing; the effect is that descendant turns become cancelled over time.

**Call relations**: CancelReconciler.run calls this on each interval. This method uses CancelReconciler._orphans_query to find the targets, then hands each target to ufo.cancellation.cancel_one_turn so cancellation happens through the same trusted primitive used elsewhere.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (cancel_one_turn, owner_tx, log, ws).


##### `CancelReconciler._orphans_query`  (lines 199–239)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: This builds the database query that finds non-finished turns with a cancelled ancestor. In plain terms, it asks: “Which still-running turns are underneath something already cancelled?”

**Data flow**: It reads the turn table structure and status constants. It builds a recursive SQL query, meaning a query that can walk step by step up a parent chain. Starting from live turns, it climbs through parent_turn_id links until it finds a cancelled ancestor or stops, while also carrying the turn’s workspace id. It returns the query object, not the rows themselves.

**Call relations**: CancelReconciler.sweep calls this and then executes the returned query. While building the parent-walking logic, it calls CancelReconciler._profile_child_parent to decide which parent links count for cancellation inheritance.

*Call graph*: calls 1 internal fn (_profile_child_parent); called by 1 (sweep); 1 external calls (select).


##### `CancelReconciler._profile_child_parent`  (lines 241–242)

```
def _profile_child_parent(self, turn: sa.Table | sa.FromClause) -> sa.ColumnElement
```

**Purpose**: This decides whether a turn’s parent should be followed when looking for inherited cancellation. It prevents the cancellation walk from crossing an agent-child boundary that should be treated as independent.

**Data flow**: It receives a SQL table-like object for turns. It builds a SQL expression: if subagent_profile is null, the expression yields null, otherwise it yields parent_turn_id. The result is not an immediate Python value; it is a piece of a database query used later.

**Call relations**: CancelReconciler._orphans_query uses this while constructing its recursive parent-chain query. That query then guides CancelReconciler.sweep in choosing which descendant turns to cancel.

*Call graph*: called by 1 (_orphans_query); 2 external calls (case, null).


### Recurring Extension Tasks
Runs due monitors, resumes paused conversations, and advances scheduled tasks on clock ticks under the correct authority and safety boundaries.

### `extensions/monitors/ufo_ext_monitors/monitor_runner.py`

`domain_logic` · `recurring background monitor tick`

A monitor is like an alarm clock tied to a command. Someone arms it with a baseline result, and this runner wakes up on a schedule to check whether the command still looks the same. Without this file, monitors would sit in storage and never probe, never report changes, and never end at their deadline.

The runner starts each pass by asking the monitor store for rows that are due, using a short lease so two overlapping runs do not check the same monitor at the same time. For each claimed monitor, it decides what should happen next. If the monitor has passed its deadline, it fires immediately. If the member who created it no longer has a seat in the workspace, the probe is skipped rather than using authority that has been revoked. If probing is allowed, it runs the saved command through the probes capability.

The result is then sorted into clear outcomes. The same output is a quiet tick. A failed command is counted, and the third failure fires the monitor. Changed output fires at once. If the output is too large, the runner stores the full text in a file and puts only a capped version in the message. A fire is sent before the monitor is retired, with an idempotency key, meaning a crash can retry the same fire without duplicating it.

#### Function details

##### `MonitorRunner.run`  (lines 55–65)

```
async def run(self) -> None
```

**Purpose**: Runs one scheduled sweep of all monitors that are due right now. It claims due monitors, ticks each one, and reports at the end if any of them failed to process.

**Data flow**: It starts with the extension context stored on the runner. From that, it creates a monitor store, reads the current time, and asks the store for due monitors under a lease. Each returned monitor row is passed into the per-monitor tick logic. If any tick raises an error, the monitor name and error type are collected, and after the sweep a single RuntimeError is raised naming the failures.

**Call relations**: This is the top-level method for the recurring job. It creates the MonitorStore, gets the current time, and calls MonitorRunner._tick once for each claimed monitor. It does not decide monitor outcomes itself; it delegates that detailed decision-making to _tick and only gathers failures from the sweep.

*Call graph*: calls 1 internal fn (_tick); 2 external calls (__init__, now).


##### `MonitorRunner._tick`  (lines 67–112)

```
async def _tick(self, store: MonitorStore, row: Monitor) -> None
```

**Purpose**: Processes one claimed monitor and decides whether it should be skipped, counted as quiet, counted as failed, or fired. This is where the monitor's main rules are applied.

**Data flow**: It receives a monitor store and one monitor row. It reads the monitor's interval, deadline, creator, command, current baseline, and streak counters. If the deadline has arrived, it fires the monitor. If the creator is no longer allowed to act, or the terminal is gone, it records a skipped tick. Otherwise it runs the saved probe command. A non-zero exit code adds to the failure streak or fires on the threshold. Matching output records a quiet tick. Different output creates a fire message, with large output split into a separate spill file if needed.

**Call relations**: MonitorRunner.run calls this after claiming a due row. During the tick it asks MonitorRunner._acts_for_a_seated_member whether the stored creator can still authorize the probe, calls the probe capability from the context, updates the MonitorStore for quiet, failed, or skipped ticks, and calls MonitorRunner._fire when the monitor must notify the agent and retire.

*Call graph*: calls 5 internal fn (_acts_for_a_seated_member, _fire, failed_tick, quiet_tick, skipped_tick); called by 1 (run); 4 external calls (now, timedelta, capped, stderr_tail).


##### `MonitorRunner._acts_for_a_seated_member`  (lines 114–126)

```
async def _acts_for_a_seated_member(self, row: Monitor) -> bool
```

**Purpose**: Checks whether the monitor is still allowed to act as the member who created it. This prevents a saved monitor from continuing to use a person's connected accounts after that person has lost their workspace seat.

**Data flow**: It receives a monitor row and reads its created_by_member_id. If there is no creator member, it returns true because the monitor is not using personal authority. If there is a creator, it opens a transaction and asks the Seats service whether that member is still admitted to the workspace. The result is a simple true or false.

**Call relations**: MonitorRunner._tick calls this before running a probe, because probing may use the creator's forwarded connections. MonitorRunner._fire calls it again before sending the final notification, so a deadline fire for an unseated member still arrives, but no longer claims to act on that member's behalf.

*Call graph*: called by 2 (_fire, _tick); 1 external calls (__init__).


##### `MonitorRunner._fire`  (lines 128–148)

```
async def _fire(self, store: MonitorStore, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> None
```

**Purpose**: Sends the final monitor-fired message to the agent and then retires the monitor so it will not keep probing. It is used for deadlines, changed output, and repeated failures.

**Data flow**: It receives the store, the monitor row, the cause of the fire, the message payload, any full spilled output, and the updated probe count. First it checks that this runner still holds the claim for the row. If not, it stops. Then it decides whether the fire may act on behalf of the creator, builds the message body, invokes the agent with a stable idempotency key, and finally marks the monitor retired in storage.

**Call relations**: MonitorRunner._tick calls this whenever a monitor reaches a final outcome. _fire asks MonitorStore.claim_holds whether it still owns the lease, uses MonitorRunner._acts_for_a_seated_member to choose the authority for the invocation, asks MonitorRunner._body to construct the text the agent will see, sends that text through the extension context, and then calls MonitorStore.retire.

*Call graph*: calls 4 internal fn (_acts_for_a_seated_member, _body, claim_holds, retire); called by 1 (_tick).


##### `MonitorRunner._body`  (lines 150–169)

```
async def _body(self, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> str
```

**Purpose**: Builds the text message that tells the agent a monitor has fired. It includes the reason, next steps, metadata, counters, and any probe output in a safe format.

**Data flow**: It receives the monitor row, the fire cause, a payload, optional spilled full output, and the probe count. It turns the monitor's metadata into JSON text, assembles a tagged monitor_fired block, escapes any accidental closing tag inside the content, and adds a path to the full output file when needed. If there is probe output to show, it wraps that output with the core wall helper, which marks untrusted command output so it cannot masquerade as instructions.

**Call relations**: MonitorRunner._fire calls this just before invoking the agent. If the output was too large to fit directly, _body calls MonitorRunner._spilled to write the full output to conversation files. It also uses json.dumps for metadata and wall to safely include command output.

*Call graph*: calls 1 internal fn (_spilled); called by 1 (_fire); 2 external calls (dumps, wall).


##### `MonitorRunner._spilled`  (lines 171–179)

```
async def _spilled(self, row: Monitor, output: str) -> str
```

**Purpose**: Writes oversized probe output to a conversation file and returns the path that can be shown in the fire message. This keeps the message readable while preserving the full output for the agent to inspect.

**Data flow**: It receives the monitor row and the full output string. It checks that the extension context has a files capability, creates a timestamped filename under the .monitors directory, encodes the output as bytes, writes it into the monitor's conversation workspace, and returns the written file path.

**Call relations**: MonitorRunner._body calls this only when a fire has more output than should be placed directly in the message. It reads the current time to make a unique filename and hands the stored path back to _body, which includes that path in the final monitor-fired text.

*Call graph*: called by 1 (_body); 1 external calls (now).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pause_runner.py`

`orchestration` · `recurring scheduled job`

A “pause” here is a stored reminder to resume a workflow later, much like setting an alarm clock for a conversation. This file is the alarm-clock runner. It is meant to be called again and again on a schedule. Each time it wakes up, it asks the pause store for pauses that are due now and temporarily claims them with a lease, which is a time-limited hold that stops overlapping runners from doing the same work twice.

For each claimed pause, the runner checks that the pause is still valid. The important race is between two possible endings of the same wait: either the timer fires, or a member sends a message before the timer fires. The stored pause includes “watermarks,” meaning recorded conversation positions from when the pause began. When the runner invokes the saved prompt, those watermarks tell the system: only resume if no member has spoken since then.

The order matters. It invokes the scheduled turn first, then retires the pause row. If the process crashes after invoking but before retiring, the same pause may be tried again, but it uses the same idempotency key, which is like a receipt number that prevents duplicate turns. If any pause fails during a tick, the runner keeps trying the rest, then raises one combined error naming the failed conversations.

#### Function details

##### `PauseRunner.run`  (lines 32–41)

```
async def run(self) -> None
```

**Purpose**: This is the main tick of the pause runner. It finds pauses whose time has arrived, tries to fire each one, and reports any failures after giving every due pause a chance.

**Data flow**: It starts with the runner’s extension context and lease length. It creates a PauseStore, asks it for pause rows due at the current UTC time, then sends each row to _fire. If _fire raises an error for a row, this function records the conversation id and error type. At the end, it returns nothing if all went well, or raises one RuntimeError describing the failed pause fires.

**Call relations**: A scheduler or recurring job calls this method when it is time to process pauses. It builds the store, uses the current time to claim due work, and delegates the actual firing of each pause to PauseRunner._fire. This lets one tick coordinate many pause rows while keeping the per-pause decision in a smaller helper.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `PauseRunner._fire`  (lines 43–56)

```
async def _fire(self, store: PauseStore, row: Pause) -> None
```

**Purpose**: This tries to complete one paused wait. It resumes the stored conversation turn if the pause still owns its required holds, then marks the pause as finished.

**Data flow**: It receives a PauseStore and one Pause row. First it asks the store whether the pause’s holds can still be claimed; if not, it stops and changes nothing else. If the holds are valid, it invokes the saved prompt for the stored conversation and agent, on behalf of the member who created the pause, using an idempotency key based on the pause id. It also passes the recorded watermarks so the turn is only admitted if no member has spoken since the pause began. After that invoke completes, it retires the pause in the store.

**Call relations**: PauseRunner.run calls this once for each due pause it claimed. This function consults PauseStore.claim_holds before doing anything visible, then uses the extension context to schedule the conversation turn, and finally calls PauseStore.retire so the same wait is not considered active anymore.

*Call graph*: calls 2 internal fn (claim_holds, retire); called by 1 (run).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `recurring scheduler tick`

This file solves a common scheduling problem: when a task is due, the system must run it once, not zero times and not twice, even if two scheduler ticks overlap or a deployment happens mid-run. Think of it like a careful office assistant who checks the calendar, puts a temporary hold on each due appointment, confirms it is still valid, sends the work to the right person, and then writes the next appointment back onto the calendar.

The main class, ScheduledTaskRunner, is run as a recurring job. It asks ScheduleStore for due tasks and claims them with a short lease. A lease is a temporary ownership marker, so another overlapping runner should not process the same task at the same time.

For each claimed task, the runner first retires it if it has expired. If it is still valid, it calculates the next cron time. A cron schedule is a compact text schedule such as “every day at 9.” The runner then builds the message that will be delivered into the task’s conversation. That message includes the exact scheduled time and instructions about whether this is a normal report run or the final allowed run before expiry.

The runner uses an idempotency key, meaning a stable “same work” label, based on the task and exact occurrence. If the same scheduled fire is retried, the system can recognize it instead of creating a duplicate. Successful fires are rescheduled; failed fires keep their leased occurrence so they can be retried.

#### Function details

##### `fire_body`  (lines 41–59)

```
def fire_body(task: ScheduledTask, runtime_instruction: str | None) -> tuple[str, str]
```

**Purpose**: Builds the actual text that will be injected into a conversation when a scheduled task fires. It also creates the stable key used to recognize this exact scheduled occurrence if it is retried.

**Data flow**: It receives a ScheduledTask and an optional runtime instruction. It reads the task’s next scheduled time, prompt, and id, then creates two outputs: the inbound message for the conversation and an idempotency key for that task occurrence. The message shows the time in a human/model-friendly UTC form ending in Z, while the key keeps the SDK’s expected timestamp shape so older admitted turns still match.

**Call relations**: ScheduledTaskRunner._fire calls this after it has confirmed the task is still claimed and should run. fire_body hands back the message and key that _fire passes into the extension context’s invoke call, and it relies on scheduled_fire_key to format the duplicate-prevention key consistently.

*Call graph*: called by 1 (_fire); 1 external calls (scheduled_fire_key).


##### `ScheduledTaskRunner.run`  (lines 67–76)

```
async def run(self) -> None
```

**Purpose**: Runs one scheduler tick. It claims all tasks that are due now, tries to fire each one, and reports a combined error if any task failed to start properly.

**Data flow**: It starts with the runner’s ExtensionContext and current time. It creates a ScheduleStore, asks the store to claim due tasks for a limited lease window, then sends each claimed task to _fire. It collects failure names as it goes. If there were no failures, it finishes quietly; if there were failures, it raises an error naming them.

**Call relations**: This is the top-level method for the recurring scheduled-task job. It calls ScheduleStore to find work, calls _fire for each task, and uses the current time to make the tick consistent. _fire does the detailed per-task decision-making while run coordinates the batch.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `ScheduledTaskRunner._fire`  (lines 78–113)

```
async def _fire(self, store: ScheduleStore, task: ScheduledTask, tick_at: datetime, expiry_checked_at: datetime) -> str | None
```

**Purpose**: Processes one claimed scheduled task. It decides whether the task should be retired, fired normally, fired as its final run, retried later, or rescheduled to its next cron occurrence.

**Data flow**: It receives the schedule store, one claimed task, the tick time, and the time at which expiry is checked. First it asks the store to retire the task if it has expired. If not expired, it calculates the following scheduled fire time. If that following time would be beyond the task’s expiry, it uses the final-run instruction; otherwise it uses the normal reporting instruction. It confirms the claim still holds, builds the inbound message and key, and invokes the task back into its conversation as a scheduled turn. If invocation is accepted and returns a turn id, it writes the next run time back to the store. If invocation raises an exception, it returns a short failure label; otherwise it returns nothing.

**Call relations**: ScheduledTaskRunner.run calls this once for each task it claimed. _fire consults ScheduleStore to retire expired tasks, verify the lease, and reschedule successful tasks. It calls next_fire to compute the next cron occurrence and fire_body to prepare the conversation message before handing the work to the extension context for actual invocation.

*Call graph*: calls 4 internal fn (fire_body, claim_holds, reschedule, retire_if_expired); called by 1 (run); 1 external calls (next_fire).


### Self-Improvement Cron
Periodically tests prompt-improvement candidates and opens human-governed proposals only after repeated successful evaluation.

### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled self-improvement tick`

This file is the safety gate for automatic prompt improvement. It does not directly change an agent. Instead, it periodically reviews past agent work, asks a proposer for a possible better prompt, tests that prompt on saved examples, and, only after repeated success, creates a proposal that a person can approve.

The main object is `ImproveCron`, a scheduled worker. Think of it like a cautious quality-control inspector. For each agent, it keeps at most one prompt candidate tied to the current prompt version. That tie is important: if the agent prompt changes, old candidates no longer apply. Candidate progress is saved in the extension’s scoped store, so the system remembers whether a candidate is still being tested, was rejected, or was already promoted to a proposal.

The key rule is stability. A candidate must pass evaluation for `stability_count` consecutive ticks before it can be proposed. If it fails, it is marked rejected and will not be reopened for the same prompt version. If it passes enough times, the file creates an `AgentChange` proposal instead of editing the agent directly. This keeps self-improvement under governance: automation can suggest, but approval is the only path to promotion.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: Runs one scheduled self-improvement pass across the workspace. It gathers all recorded agent trajectories and processes them agent by agent.

**Data flow**: It reads trajectories from the extension context. It groups those trajectories by agent, then sends each agent’s group onward for candidate opening, testing, or proposal creation. It returns nothing; its effects happen through later store updates or governed proposals.

**Call relations**: This is the top of the cron flow. It calls `_by_agent` to sort the raw trajectory list into per-agent bundles, then calls `ImproveCron._advance` once for each agent so each agent’s prompt candidate can move one step forward.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Moves one agent through one improvement step. It either finds or opens a candidate prompt for the agent, then sends that candidate through the evaluation gate.

**Data flow**: It receives an agent ID and that agent’s trajectories. It reads the current prompt digest from the first trajectory, builds the store key for this agent’s candidate, asks `_active_or_open` for a usable candidate, and stops if none exists. If there is a candidate, it passes the candidate and trajectories into `_gate` for testing.

**Call relations**: This is called by `ImproveCron.run` for each agent. It connects the candidate-selection phase, handled by `ImproveCron._active_or_open`, to the safety-testing phase, handled by `ImproveCron._gate`.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: Finds the current candidate prompt for an agent, or creates a new one if it is safe and useful to do so. It prevents already resolved candidates from being proposed again for the same prompt version.

**Data flow**: It receives a store key, the current prompt digest, and the agent’s trajectories. It first checks the scoped store for a saved candidate. If that saved candidate belongs to the current prompt digest and is still evaluating, it returns it; if it was already promoted or rejected, it returns nothing. If there is no usable saved candidate, it groups trajectories into task classes, asks the proposer for a new prompt candidate, saves that candidate with its held-out example IDs, and returns it.

**Call relations**: This is called by `ImproveCron._advance` before any evaluation happens. It uses `task_classes` to find meaningful groups of examples and creates a `CandidateState` when the proposer supplies a candidate. The returned candidate is then handed to `ImproveCron._gate`.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Tests a candidate prompt and decides whether it should keep waiting, be rejected, or be turned into a governed change proposal. This is the main safety checkpoint.

**Data flow**: It receives the agent, candidate, current prompt digest, store key, and trajectories. It rebuilds the candidate’s held-out examples, also collects held-out examples from other task classes as a broader safety check, then asks the evaluator to compare the candidate prompt with the current prompt. If the candidate fails, it saves it as rejected. If it passes but has not passed enough consecutive ticks, it saves the higher pass count. If it has passed enough times, it creates an `AgentChange` proposal and saves the candidate as promoted with the proposal ID.

**Call relations**: This is called by `ImproveCron._advance` after a candidate has been found or opened. It calls `_held_out` to recover test examples, uses `task_classes` for broader held-out checks, calls `_save` whenever candidate state must be recorded, and creates an `AgentChange` only after the stability threshold is met.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: Writes an updated candidate state to the scoped store. It is used whenever evaluation changes the candidate’s status, pass count, or proposal reference.

**Data flow**: It receives the store key, the previous candidate record, and the new status information. It copies the candidate with the updated fields, converts it to JSON-friendly data, and stores it under the same key. It returns nothing, but the persisted record changes.

**Call relations**: This helper is called only by `ImproveCron._gate`. It keeps the gate logic simpler by centralizing the act of updating and saving a `CandidateState`.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: Groups a mixed list of trajectories into separate bundles for each agent. This lets the cron process each agent independently.

**Data flow**: It receives all trajectories from the workspace. It sorts them by `agent_id`, building one tuple of trajectories per agent, and returns a mapping from agent ID to that agent’s trajectory tuple. It does not change the trajectories themselves.

**Call relations**: This is called by `ImproveCron.run` at the start of a scheduled pass. Its output determines how many times `ImproveCron._advance` runs and which trajectories each agent receives.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: Rebuilds the held-out test examples for a candidate from the latest available trajectories. These examples are the candidate’s private exam set.

**Data flow**: It receives the agent’s trajectories and a tuple of held-out conversation IDs saved in the candidate state. It looks up matching trajectories, skips any missing ones, and uses `bad_trajectory` to turn relevant failed or flagged trajectories into `TaskExample` records. It returns the collected examples as a tuple.

**Call relations**: This is called by `ImproveCron._gate` just before evaluation. It supplies the candidate-specific test set that the evaluator uses to decide whether the new prompt is safe and useful.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system what is enabled, safe, and available in this run.
- `reg-extension-catalog` — The shared list of installed extensions and the capabilities they registered for this deployment.
- `reg-database-schema` — The durable database layout and migration version that every runtime component must agree on.
- `reg-db-session` — The active database connection, transaction, and workspace-safe persistence context used while work is running.
- `reg-workspace-roster` — The saved list of workspaces, members, admins, seats, and membership rules.
- `reg-agent-definitions` — The saved assistant agents for each workspace, including their settings, tools, model choices, and provisioning source.
- `reg-identity-context` — The current answer to who is acting, in which workspace, and on behalf of which member or agent.
- `reg-conversation-state` — The durable conversation records, titles, audience, surface labels, sandbox links, and visible thread metadata.
- `reg-turn-queue` — The durable queue of conversation turns, including admitted work, claimed work, failures, retries, and completion state.
- `reg-cancellation-state` — The shared stop-and-recovery state used to cancel running turns and prevent abandoned work from continuing.
- `reg-live-delivery` — The live stream and delivery state for partial replies, tool updates, terminal output, final status, and missed messages.
- `reg-runtime-fleet` — The records of which runtime processes and workers are alive, what they own, and when they last checked in.
- `reg-sandbox-state` — The per-conversation sandbox handle, size, filesystem environment, command execution state, and cleanup state.
- `reg-surface-ingress` — The shared records that connect external surfaces like web, Slack, shell, OAuth, and inbound messages to conversations and replies.
- `reg-source-feeds` — The registered external content sources, sync cursors, backoff state, ownership, grants, and wake-up triggers.
- `reg-scheduled-work` — The saved jobs, scheduled tasks, pauses, monitors, due times, retry state, and duplicate-run guards.
- `reg-workflow-plans` — The longer-running goals, objective steps, blockers, todos, delegated work, and progress evidence that survive across turns.
- `reg-subagent-delivery` — The parent-child turn links and pending result records used when helper agents run work and report back.
- `reg-usage-ledger` — The shared cost and usage records for model calls, tools, sandboxes, connectors, network use, and generated media.
- `reg-observability` — The shared logs, traces, metrics, trace links, and sanitized diagnostic records used to understand system behavior.
- `reg-prompt-governance` — The saved prompt proposals, approval status, evaluation results, and safety checks for changing agent instructions.
- `reg-coding-review-state` — The coding extension’s durable review inbox and review-run records, including links to the agent, conversation, and turn that handle review automation.
- `reg-ephemeral-cache-bus` — The selected Redis/cache/pub-sub backend and its ephemeral keys, locks, and connection state used to coordinate live delivery, workers, and shared runtime services.
- `reg-db-engine-pool` — The process-wide database engine, DSN binding, and connection pool from which per-request sessions and migration runners obtain connections.
