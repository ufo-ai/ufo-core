# Scheduled Jobs, Maintenance Loops, and Autonomous Wakeups  `stage-14`

This stage is the system’s background alarm clock. It runs work that should happen later or repeatedly, without waiting for a user to click anything. Some of this happens during normal operation, like scheduled prompts, paused conversations, monitor checks, and source-change wakeups. These pieces watch the clock or shared data, safely claim work that is due, wake the right conversation or workflow, and then either finish it or schedule the next run.

Other parts are quiet maintenance. They retry missing file previews, clean old homepage settings, create short report digests, and run cautious self-improvement checks for agents. Those checks gather past failures, test possible prompt changes, and only move forward when the results look safe.

The shared job machinery ties this together across workspaces. The candidates file is the safe doorway for finding which workspaces might have pending work, without mixing their private data. The jobs file turns background job definitions into real scheduled runs. It makes sure each job runs inside the correct workspace, avoids duplicate runs, and can recover after a restart.

## Sub-stages

- [User-Visible Scheduled Work](stage-14.1.md) `stage-14.1` — 13 files
- [Offline Product and Quality Maintenance](stage-14.2.md) `stage-14.2` — 12 files

## Files in this stage

### Workspace Job Scheduling
Finds workspaces with pending background work and turns their job definitions into safely coordinated scheduled execution.

### `core/src/ufo/runtime/candidates.py`

`domain_logic` · `main loop`

Jobs in this system are always tied to a workspace, which is like a tenant or separate customer area. Before a job handler can run, the dispatcher needs to know which workspaces actually have work waiting. This file solves that problem in a controlled way.

Normally, database reads are protected by RLS, or row-level security, meaning a query only sees rows for the currently selected workspace. Candidate discovery is the one exception: it may look across all workspaces, but only to return workspace IDs, not private row data. Think of it like checking a building directory to see which offices have mail, without opening anyone’s mail.

The main helper, `owner_candidates`, accepts a small query builder from an extension. That builder creates a database query that selects distinct workspace IDs from the extension’s own tables. The helper turns it into an async candidate function. Each time that function runs, it rebuilds the query, runs it through `owner_tx`, the special cross-workspace database access path, and returns only the IDs it found.

Rebuilding the query each time matters because “due” work often depends on the current time. A query made once at startup could freeze an old cutoff time. With this design, every scheduler tick gets a fresh view of which workspaces are due, while keeping the dangerous cross-workspace read tightly limited.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: This function turns a workspace-ID query builder into a callable that the job dispatcher can use to ask, “Which workspaces have work right now?” It keeps extensions from directly using the special cross-workspace database access path.

**Data flow**: It receives `due`, a no-argument function that builds a database `SELECT` query returning workspace IDs. It wraps that builder inside an async `candidates` function. The result is a reusable callable that, when invoked later, will run the fresh query and produce a tuple of workspace UUIDs.

**Call relations**: This is the setup step. An extension or core sweep supplies the recipe for finding due work, and `owner_candidates` returns the function that will actually be called during scheduling. The returned inner function is where the database read happens.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This async function performs the actual candidate lookup. It reads across workspaces only long enough to collect workspace IDs, so the dispatcher can later run real job work inside each workspace safely.

**Data flow**: It opens an `owner_tx` database transaction, which is the controlled path that bypasses normal per-workspace row filtering. Inside that transaction, it calls `due()` to build a fresh query, executes it, collects all returned rows, and then closes the transaction. It returns a tuple made from the first column of each row, which should be workspace UUIDs.

**Call relations**: This function is the runtime piece produced by `owner_candidates`. When the scheduler or dispatcher needs to know where work exists, it invokes this callable. During that lookup, it calls `ufo.db.owner_tx` to perform the one permitted cross-workspace read, then hands back only workspace IDs for later workspace-bound execution.

*Call graph*: 1 external calls (owner_tx).


### `core/src/ufo/runtime/jobs.py`

`orchestration` · `startup and scheduled background execution`

This file is the background-job engine for the runtime. At startup, the system has a set of job descriptions from core code and installed extensions. This file registers those jobs with DBOS, the durable workflow system used here to remember and retry work. Without it, source syncing, page-change hooks, queued turn recovery, result delivery, preview rendering, and product census jobs would not reliably run.

The design has two stages. First, a lightweight “tick” happens on a schedule or as a one-time enqueue. The tick asks, “Which workspaces actually need this job?” Then it queues one real job workflow per workspace. This is like a mail sorter: the schedule rings one bell, then the sorter puts one envelope in each workspace’s mailbox that has work waiting.

The file also protects against duplicates. Each job/workspace pair gets a deduplication key, so if a previous run is still active, the new tick is absorbed instead of piling up more copies. The actual handler always runs inside a workspace context, so extension code sees the right data and credentials.

Several core job helpers live here too. TurnDispatcher rescues queued or parked conversation turns and offers them to worker queues when seats, spending, and balance allow it. PageChangeRunner feeds changed pages to extension hooks using per-hook cursors, so one slow hook does not block another.

#### Function details

##### `ResultDeliverer.run`  (lines 84–84)

```
async def run(self) -> None
```

**Purpose**: This protocol method describes the action a result-delivery sweep must provide. A concrete implementation uses it to find completed child-agent results and hand them back to the parent conversation.

**Data flow**: It receives only the object it is called on. The implementation is expected to inspect whatever result state it owns, perform the delivery work, and return nothing when done.

**Call relations**: This file does not implement the method; it names the shape expected by core job registration. The core result-delivery job calls it through the wrapper made in core_jobs, so this jobs layer can schedule delivery without importing the turn-loop implementation directly.


##### `ResultDeliverer.candidate_workspaces`  (lines 86–86)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This protocol method describes how result delivery reports which workspaces have pending delivery work. It lets the job runner avoid opening workspaces that have nothing to do.

**Data flow**: It receives the deliverer object, reads its underlying state in the concrete implementation, and returns a tuple of workspace IDs that should run the delivery sweep.

**Call relations**: core_jobs attaches this method to the result-delivery JobSpec. Later, JobRunner.tick asks that JobSpec for candidates before queueing per-workspace workflows.


##### `TurnDispatcher.run`  (lines 154–188)

```
async def run(self) -> None
```

**Purpose**: This is the main sweep that moves eligible conversation turns back onto worker queues. It recovers turns that were queued but not claimed, and it also rechecks parked turns that were waiting for seats, spending permission, or balance.

**Data flow**: It starts by reading dispatchable turn rows for the current workspace. For parked turns, it checks whether required members are seated, whether spending is allowed, and whether the balance gate admits the work. Turns that pass are stamped and enqueued; turns that do not pass are left in place for a later sweep.

**Call relations**: This is called by the core turn-dispatch job wrapper from core_jobs. It relies on _dispatchable_turns to find candidates and _enqueue to safely offer each turn to DBOS, while using the seat, spend, and balance checks before parked turns may resume.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 6 external calls (__init__, __init__, __init__, select, workspace_tx, gate_member).


##### `TurnDispatcher.candidate_workspaces`  (lines 190–198)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This finds workspaces that have at least one stale queued or parked turn worth checking. It prevents the scheduled sweep from entering every workspace when only a few have pending turns.

**Data flow**: It computes a grace-period cutoff time, reads owner-level database state across workspaces, filters using the same eligibility rules as the real sweep, and returns distinct workspace IDs.

**Call relations**: JobRunner.tick calls this through the turn-dispatch JobSpec created in core_jobs. It shares _eligible with _dispatchable_turns so the fleet-wide candidate scan and the per-workspace run agree on what counts as pending work.

*Call graph*: calls 1 internal fn (_eligible); 4 external calls (now, timedelta, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 200–241)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: This reads the actual turn rows that the dispatcher may try to enqueue in the current workspace. It keeps the batch bounded so one sweep cannot grab unlimited work.

**Data flow**: It computes the stale-offer cutoff, queries queued or parked turns that are eligible, orders them so older queued work comes first, converts each database row into a small _DispatchTurn record, and returns those records.

**Call relations**: TurnDispatcher.run calls this at the start of a sweep. The returned records are then checked, if parked, and passed to _enqueue for the final atomic claim and queue offer.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 6 external calls (__init__, now, timedelta, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 243–272)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: This safely offers one eligible turn to the DBOS worker queue. It first marks the row as offered, so two sweepers do not enqueue the same stale turn at the same time.

**Data flow**: It receives a _DispatchTurn, recomputes the stale cutoff, updates the matching database row only if it is still stale and still first in its status, then builds DBOS enqueue options. If the update succeeds, it queues the turn workflow; if not, it returns without doing anything.

**Call relations**: TurnDispatcher.run calls this after any required gates pass. It uses _first_in_status and _stale as guards, then hands the turn to DBOS with the correct queue name and workflow ID.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 1 (run); 6 external calls (now, timedelta, update, workspace_tx, turn_queue_for, uuid4).


##### `TurnDispatcher._eligible`  (lines 274–294)

```
def _eligible(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for turns that are safe to dispatch. It enforces the rule that one conversation should not run multiple sibling turns at once and that later turns cannot jump ahead of earlier ones.

**Data flow**: It receives a cutoff time and produces a SQL condition. That condition matches queued or parked turns whose dispatch stamp is missing or old, that have no running sibling in the same conversation, and that are first among turns with the same status.

**Call relations**: candidate_workspaces uses this condition for the broad workspace scan, and _dispatchable_turns uses it for the per-workspace row read. It calls _stale and _first_in_status to keep the rule in one place.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 4 external calls (and_, exists, or_, select).


##### `TurnDispatcher._stale`  (lines 296–300)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for an old or missing dispatch offer. A stale offer means a previous attempt may have failed before the worker actually claimed the turn.

**Data flow**: It receives a cutoff time and returns a SQL condition that is true when dispatch_enqueued_at is absent or earlier than that cutoff.

**Call relations**: _eligible uses it to find turns worth scanning, and _enqueue uses it again during the final update so a race with another sweeper does not create duplicate offers.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 302–311)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition that says a turn is the earliest turn of its status in its conversation. It is the ordering guard that stops later queued or parked turns from overtaking earlier ones.

**Data flow**: It receives a turn status and returns a SQL condition that is true only when there is no earlier turn with the same workspace, conversation, status, and a smaller sequence number.

**Call relations**: _eligible uses this for the normal scan, and _enqueue uses it again when claiming the row. That double use keeps the scan and the final update consistent.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `_page_beyond_cursor`  (lines 314–319)

```
def _page_beyond_cursor(revision: int, page_id: UUID, cursor: object) -> bool
```

**Purpose**: This answers whether a page change comes after a saved cursor. A cursor is the saved bookmark showing how far a page-change consumer has already read.

**Data flow**: It receives a page revision, page ID, and stored cursor. With no cursor it returns true, meaning everything is pending. Otherwise it parses the cursor and compares revision first, then page ID, returning whether the page is newer than the bookmark.

**Call relations**: PageChangeRunner.workspaces_with_changes calls this while deciding which workspaces have page changes waiting for a specific consumer.

*Call graph*: called by 1 (workspaces_with_changes); 1 external calls (page_cursor).


##### `PageChangeConsumer.spec_name`  (lines 340–342)

```
def spec_name(self) -> str
```

**Purpose**: This property gives a page-change consumer its unique job name. The name includes the extension and handler discriminator so separate hooks do not collide.

**Data flow**: It reads the consumer’s extension name and discriminator, combines them with the page-change prefix, and returns a string job name.

**Call relations**: core_jobs uses this property when creating one JobSpec per page-change consumer. That lets each hook get its own schedule, workflow, and cursor.


##### `PageChangeConsumer.job`  (lines 345–350)

```
def job(self) -> str
```

**Purpose**: This property gives the full job key used for attribution and context. It marks page-change execution as core-driven while still identifying the extension hook being run.

**Data flow**: It reads spec_name and prefixes it with the core namespace, returning a single key string.

**Call relations**: PageChangeRunner._context_for passes this key into context_for. That means model usage and job identity are attributed to this exact page-change consumer.


##### `PageChangeRunner.consumers`  (lines 393–417)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: This discovers all registered page_change hooks from the active extension manifests. It turns each hook into a PageChangeConsumer with its own identity and cursor scope.

**Data flow**: It reads each manifest’s credential slots and hooks, keeps only hooks for the page_change event, checks that handler names are unique within an extension, and returns PageChangeConsumer objects.

**Call relations**: core_jobs calls this while building core job specs. If two hooks in one extension would share the same cursor name, this function raises early instead of letting them corrupt each other’s progress.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 419–484)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: This finds the workspaces where a particular page-change consumer has unread page changes. It avoids running a consumer in workspaces whose newest page is already at or before that consumer’s saved cursor.

**Data flow**: It receives a PageChangeConsumer, builds that consumer’s cursor key, reads stored cursors and each workspace’s newest page, compares each newest page to the cursor, logs invalid cursors as warnings, and returns workspace IDs with pending changes.

**Call relations**: The page-change JobSpec candidate function calls this through core_jobs._consumer_candidates._candidates. It uses _page_beyond_cursor for the comparison and supplies the candidate list that JobRunner.tick fans out.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 3 external calls (select, owner_tx, warn).


##### `PageChangeRunner.drive`  (lines 486–536)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: This runs one page-change consumer inside one workspace. It reads batches of changed pages, calls the extension’s hook, and advances that hook’s cursor only after the hook succeeds.

**Data flow**: It builds an extension context, reads the stored cursor, asks the page feed for a batch after that cursor, passes the batch to the hook, and then writes the next cursor with a compare-and-set check. If the hook fails, it logs and counts the stall, leaves the cursor unchanged, and raises the error.

**Call relations**: The handler returned by core_jobs._drive_consumer calls this during a job workflow. It calls _context_for to create the extension-facing context and hands batches to the hook through HookContext and PageChangeBatch.

*Call graph*: calls 1 internal fn (_context_for); 6 external calls (__init__, __init__, emit_metric, formatted_stack, log_error, ws_current).


##### `PageChangeRunner._context_for`  (lines 538–556)

```
def _context_for(self, consumer: PageChangeConsumer) -> ExtensionContext
```

**Purpose**: This builds the ExtensionContext used by a page-change hook. The context is the safe bundle of services, credentials, storage, model access, and workspace identity that extension code receives.

**Data flow**: It receives a PageChangeConsumer, optionally creates a turn invoker for the current workspace, swaps in the configured background model if needed, and returns a context_for result for that extension and job key.

**Call relations**: PageChangeRunner.drive calls this before invoking a hook. It relies on _background_registry so page-change background work can use the configured background model instead of the normal interactive model.

*Call graph*: calls 1 internal fn (_background_registry); called by 1 (drive); 2 external calls (context_for, ws_current).


##### `_background_registry`  (lines 559–570)

```
def _background_registry(registry: ModelRegistry | None, background_model: str | None) -> ModelRegistry | None
```

**Purpose**: This returns a model registry adjusted for background jobs. If a background model is configured, it replaces the default automatic model while preserving the rest of the registry.

**Data flow**: It receives an optional registry and optional background model name. If either is missing, it returns the registry unchanged; otherwise it returns a copy of the registry with auto_model set to the background model.

**Call relations**: PageChangeRunner._context_for and JobRunner.fire call this when building contexts for background work. Jobs that explicitly need the deploy model bypass this adjustment in JobRunner.fire.

*Call graph*: called by 2 (fire, _context_for); 1 external calls (replace).


##### `core_jobs`  (lines 573–678)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, page_change_runner: PageChangeRunner, delivery_sweep: ResultDeliverer, preview_renderer: PreviewRenderer | None) -> tuple[JobSpe
```

**Purpose**: This builds the list of built-in job descriptions that every deployment should know about. These include source syncing, page-change hook driving, turn dispatch, result delivery, product census, and optionally preview rendering.

**Data flow**: It receives the core service objects, wraps their methods in job handler functions, discovers page-change consumers, creates JobSpec objects with schedules and candidate functions, and returns them as a tuple.

**Call relations**: Startup code uses this before bindings_from and JobRunner.launch. Its nested wrapper functions connect the generic job system to the concrete core services without running the services immediately.

*Call graph*: calls 1 internal fn (consumers); 2 external calls (__init__, seated_member_workspaces).


##### `core_jobs._sync_sources`  (lines 597–598)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: This small wrapper runs the source sync driver as a job handler. It adapts the sync driver’s no-argument run method to the job-handler shape that receives an ExtensionContext.

**Data flow**: It receives a job context but does not need to read it. It calls the sync driver’s run operation and returns when syncing is complete.

**Call relations**: core_jobs places this wrapper into the source-sync JobSpec. JobRunner.fire later calls it inside a workspace context.


##### `core_jobs._dispatch_turns`  (lines 600–601)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs the turn dispatcher as a job handler. It lets the generic job runner trigger turn recovery using the same handler interface as extension jobs.

**Data flow**: It receives a job context, ignores it, calls turn_dispatcher.run, and returns after eligible turns have been offered to worker queues.

**Call relations**: core_jobs installs it in the turn-dispatch JobSpec. JobRunner.fire invokes it for each workspace selected by TurnDispatcher.candidate_workspaces.


##### `core_jobs._deliver_results`  (lines 603–604)

```
async def _deliver_results(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs the result-delivery sweep as a job handler. It adapts the ResultDeliverer protocol to the common JobSpec handler shape.

**Data flow**: It receives a job context, calls delivery_sweep.run, and returns when pending result hand-backs for that workspace have been processed.

**Call relations**: core_jobs installs it in the result-delivery JobSpec. JobRunner.fire later calls it in each candidate workspace.


##### `core_jobs._census_product`  (lines 606–607)

```
async def _census_product(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs the product census job. The census counts product funnel and usage facts from core tables.

**Data flow**: It receives a job context, calls product_census, and returns after the census has been recorded.

**Call relations**: core_jobs places it in the product-census JobSpec. The candidate function for that spec comes from seated_member_workspaces, so JobRunner.tick only queues workspaces relevant to the census.

*Call graph*: 1 external calls (product_census).


##### `core_jobs._render_previews`  (lines 609–611)

```
async def _render_previews(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs preview rendering when a preview renderer is configured. Preview rendering prepares visual previews in the background.

**Data flow**: It receives a job context, confirms a preview renderer exists, calls preview_renderer.run, and returns when rendering work is done.

**Call relations**: core_jobs includes this wrapper only when preview_renderer is not None. JobRunner.fire calls it for workspaces returned by the matching preview candidate function.


##### `core_jobs._preview_candidates`  (lines 613–615)

```
async def _preview_candidates() -> tuple[UUID, ...]
```

**Purpose**: This wrapper asks the preview renderer which workspaces need preview work. It adapts the renderer’s candidate method to the job system.

**Data flow**: It confirms a preview renderer exists, calls preview_renderer.candidate_workspaces, and returns the workspace IDs.

**Call relations**: core_jobs uses it as the candidates function for the preview-rendering JobSpec. JobRunner.tick later calls it before queueing preview workflows.


##### `core_jobs._drive_consumer`  (lines 617–623)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: This creates a job handler for one page-change consumer. It packages a specific consumer into a callable that the generic job runner can invoke later.

**Data flow**: It receives a PageChangeConsumer and returns an async handler function that closes over that consumer.

**Call relations**: core_jobs calls this while creating page-change JobSpecs. The returned handler later calls PageChangeRunner.drive for that same consumer.


##### `core_jobs._drive_consumer._handler`  (lines 620–621)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: This is the actual job handler produced for one page-change consumer. It runs that consumer’s cursor loop in the current workspace.

**Data flow**: It receives a job context, does not use it directly, calls page_change_runner.drive with the captured consumer, and returns after the consumer catches up or stops.

**Call relations**: JobRunner.fire calls this through the page-change JobSpec. It hands off the real work to PageChangeRunner.drive.


##### `core_jobs._consumer_candidates`  (lines 625–629)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: This creates a candidate-workspace function for one page-change consumer. It packages the consumer so the scheduler can ask where that exact hook has pending page changes.

**Data flow**: It receives a PageChangeConsumer and returns an async function that closes over that consumer.

**Call relations**: core_jobs uses this when creating each page-change JobSpec. The returned function later calls PageChangeRunner.workspaces_with_changes.


##### `core_jobs._consumer_candidates._candidates`  (lines 626–627)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: This is the candidate function produced for one page-change consumer. It asks which workspaces have unread pages for that specific hook.

**Data flow**: It takes no explicit inputs, calls page_change_runner.workspaces_with_changes with the captured consumer, and returns workspace IDs.

**Call relations**: JobRunner.tick calls this through the JobSpec before queueing page-change workflows. It hands the selection work to PageChangeRunner.workspaces_with_changes.


##### `bindings_from`  (lines 690–721)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...], disabled: frozenset[str]=frozenset()) -> tuple[_Binding, ...]
```

**Purpose**: This combines core jobs and extension jobs into a single list of runnable bindings. A binding gives every job a unique key, extension namespace, declared credential slots, and JobSpec.

**Data flow**: It receives manifests, core JobSpecs, and an optional set of disabled job keys. It creates core bindings under the core namespace, extension bindings under each extension name, checks that disabled keys are real, filters disabled jobs out, and returns the final tuple.

**Call relations**: Startup code uses this before creating a JobRunner. JobRunner later uses these bindings to register schedules, look up candidates, and build the correct ExtensionContext when firing a job.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 751–775)

```
def launch(self) -> None
```

**Purpose**: This publishes all jobs to DBOS at startup. Scheduled jobs are registered with cron-like schedules, while one-shot jobs are immediately enqueued once with deduplication.

**Data flow**: It stores this JobRunner in the module-level _firing variable, walks all bindings, either enqueues one-shot ticks or collects schedule definitions, logs what it did, and finally applies all schedules through DBOS.

**Call relations**: This is the startup bridge between discovered JobSpecs and DBOS. Later, DBOS calls job_tick, which finds this runner through _firing and delegates to JobRunner.tick.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 777–799)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: This handles one scheduled or one-shot firing of a job. It fans the job out into one durable job workflow per candidate workspace.

**Data flow**: It receives the scheduled time and job key, checks whether this process knows that key, asks for candidate workspaces, and enqueues job_workflow once per workspace using a job/workspace deduplication ID. Unknown keys are skipped with a warning.

**Call relations**: job_tick calls this when DBOS fires a tick. It calls candidates for workspace selection and queues job_workflow for actual execution.

*Call graph*: calls 2 internal fn (_registered, candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 801–802)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: This asks the registered job for its candidate workspaces. It is the narrow lookup point between a job key and that job’s candidate function.

**Data flow**: It receives a job key, finds the matching binding, calls the binding’s JobSpec candidates function, and returns the resulting workspace IDs.

**Call relations**: JobRunner.tick calls this before enqueueing per-workspace workflows. It uses _binding, which raises if the key is not registered.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 804–838)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: This runs one job handler inside one workspace. It prepares workspace provisioning and the extension context, then calls the handler and logs failures with useful details.

**Data flow**: It receives a job key and workspace ID, finds the binding, enters that workspace context, provisions agents once per workspace for this runner, builds a TurnInvoker and ExtensionContext, chooses the background or deploy model registry as appropriate, then awaits the job handler. If the handler fails, it logs the error and re-raises it so DBOS sees the workflow as failed.

**Call relations**: job_fire calls this from inside a DBOS step. It uses _binding for lookup, _background_registry for model choice, AgentProvisioning before the first run in a workspace, and context_for to provide the handler’s environment.

*Call graph*: calls 2 internal fn (_binding, _background_registry); 6 external calls (__init__, failed_statement, formatted_stack, log_error, context_for, ws).


##### `JobRunner._registered`  (lines 840–841)

```
def _registered(self, key: str) -> _Binding | None
```

**Purpose**: This checks whether a job key is known to this process. It returns the binding if found and None if not.

**Data flow**: It receives a key, scans the runner’s bindings, and returns the first matching binding or None.

**Call relations**: JobRunner.tick uses this to quietly skip old or foreign schedules. JobRunner._binding uses it as the underlying lookup but treats a missing key as an error.

*Call graph*: called by 2 (_binding, tick).


##### `JobRunner._binding`  (lines 843–850)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: This returns the binding for a job key, or fails loudly if the key is not registered. It is used once work is definitely supposed to run in this process.

**Data flow**: It receives a key, calls _registered, and either returns the binding or raises a RuntimeError.

**Call relations**: JobRunner.candidates and JobRunner.fire call this when they need the JobSpec or context information. Unlike JobRunner.tick, they should not silently ignore missing bindings.

*Call graph*: calls 1 internal fn (_registered); called by 2 (candidates, fire).


##### `job_tick`  (lines 857–861)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: This is the DBOS workflow entry for a scheduled job tick. It delegates the tick to the active JobRunner.

**Data flow**: It receives the scheduled time and job key from DBOS, reads the module-level _firing runner, raises if jobs were not launched, and awaits runner.tick.

**Call relations**: JobRunner.launch registers or enqueues this workflow. DBOS calls it, and it hands control to JobRunner.tick for candidate lookup and per-workspace enqueueing.


##### `job_workflow`  (lines 865–866)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: This is the DBOS workflow for one job running in one workspace. It keeps each workspace execution durable and separately retryable.

**Data flow**: It receives the scheduled time, job key, and workspace ID string. It does not use the time directly here; it calls job_fire with the key and workspace ID.

**Call relations**: JobRunner.tick enqueues this workflow for each candidate workspace. It immediately hands off to job_fire, which is the DBOS step that invokes the runner.

*Call graph*: calls 1 internal fn (job_fire).


##### `job_fire`  (lines 870–874)

```
async def job_fire(key: str, workspace_id: str) -> None
```

**Purpose**: This DBOS step performs the actual handoff from durable workflow machinery into the JobRunner. Marking it as a step lets DBOS record completion and avoid re-running successful handler work during recovery.

**Data flow**: It receives a job key and workspace ID string, reads the active _firing runner, converts the workspace ID into a UUID, and calls runner.fire. If no runner is installed, it raises an error.

**Call relations**: job_workflow calls this for every per-workspace job execution. It is the final bridge into JobRunner.fire, where workspace binding, context creation, and handler invocation happen.

*Call graph*: called by 1 (job_workflow); 1 external calls (UUID).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system how it should run in this deployment.
- `reg-selected-pack-services` — The chosen product pack and the shared service objects it wires up for the rest of the app.
- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-workspace-member-agent-state` — The saved list of workspaces, people, memberships, seats, and agents.
- `reg-agent-configuration` — Each agent’s saved settings, such as model choice, reasoning mode, tools, visibility, internet access, sandbox size, and setup needs.
- `reg-extension-registry` — The loaded set of extensions and the routes, tools, hooks, jobs, skills, agents, and backends they contribute.
- `reg-extension-install-store` — The saved record of which extensions are installed, removed, or holding extension-specific data.
- `reg-surface-routing` — The shared routing state that maps browser, Slack, iMessage, terminal, site, and object requests to the right workspace, agent, and conversation.
- `reg-credential-connections` — The encrypted accounts, secrets, connection grants, and credential fulfillments that let agents use outside services safely.
- `reg-billing-spend-ledger` — The shared accounting state for spend caps, usage charges, prepaid balances, BYOK billing, and ledger exports.
- `reg-feature-flags` — The rollout switches that turn product and infrastructure behavior on or off across the system.
- `reg-memory-index-state` — The stored knowledge, embeddings, chunks, and memory indexes that agents can search later.
- `reg-source-config-sync-state` — The configured external sources plus their sync progress, errors, backoff, ownership, and access grants.
- `reg-skill-prompt-library` — The reusable instructions, skills, prompt rules, and agent setup guidance loaded into turns.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-inbound-message-queue` — The saved queue of incoming external messages waiting to be rendered, ordered, deduplicated, and admitted as turns.
- `reg-blob-artifact-store` — The shared file, blob, artifact, preview, download, and hosted media storage used by turns and surfaces.
- `reg-background-jobs` — The shared job schedule, due-work candidates, claims, retries, and worker state for background and autonomous work.
- `reg-runtime-fleet-heartbeats` — The fleet-wide record of which runtime processes are alive and what work they may be responsible for.
- `reg-delegation-workflows` — The saved state for subagents, parent-child turns, objectives, workflow checkpoints, pending deliveries, and recovery.
- `reg-object-journal` — The shared naming and change history for workspace objects such as tasks, prompts, skills, monitors, memories, and reports.
- `reg-database-connection-pool` — The shared SQLAlchemy engine/session and connection-pool state used by requests, turns, workers, migrations, and persistence helpers to access the database safely.
- `reg-surface-listener-leases` — The stored claims/leases that coordinate which runtime instance is allowed to listen on a shared surface installation or address, avoiding duplicate external listeners.
- `reg-outbound-surface-delivery-queue` — The durable outgoing reply/writeback state, including mid-turn replies and surface deliveries that must be claimed, sent, retried, and acknowledged exactly once.
- `reg-redis-service-pool` — The shared Redis client/connection and stream/cache coordination state used by live turn streaming, workers, and Redis-backed extension stores.
- `reg-workspace-object-store` — The current persisted workspace object records, such as tasks, monitors, todos, reports, prompts, and site metadata, read and mutated through object APIs, tools, jobs, and slots.
- `reg-provider-rate-limit-budgets` — Shared per-provider throttle, retry, and backoff budget state for model, search, connector, and external API calls so workers avoid overrunning provider limits.
