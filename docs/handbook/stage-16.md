# Scheduled, recurring, billing, monitoring, and offline improvement jobs  `stage-16`

This stage is the system’s background shift. It runs when no one is actively sending a message, like an alarm clock, bookkeeper, cleaner, and quality tester working behind the scenes. Conversation wakeups and recurring work handle timed automations: they remember jobs, wake paused workflows, retry missed child-task results, watch shell-command monitors for changes, send daily briefs, and notice shared source updates. This keeps long-running conversations moving without needing a user to poke them.

Billing and self-improvement background processing keeps the service sustainable and safer over time. The billing pieces measure usage, enforce spending limits, report charges, and manage prepaid credit or automatic top-ups. The self-improvement pieces replay old conversations in a controlled way, test proposed prompt changes, and accept only changes that score better without repeating real-world actions.

The jobs.py file connects all discovered background job definitions to DBOS, the durable workflow system that stores queued work in the database so it can survive restarts. preview_renderer.py fills in missing document preview images later, by finding recent gaps, rendering previews, and saving the results.

## Sub-stages

- [Conversation wakeups and recurring work](stage-16.1.md) `stage-16.1` — 16 files
- [Billing and self-improvement background processing](stage-16.2.md) `stage-16.2` — 10 files

## Files in this stage

### Background Job Scheduling
Registers durable background jobs and runs maintenance work such as recovering missing document previews.

### `core/src/ufo/jobs.py`

`orchestration` · `startup and scheduled background execution`

This file is the project’s background-job control room. At startup, the system discovers jobs from core code and installed extensions, then this file registers timed jobs and starts one-time jobs. Without it, source syncing, page-change hooks, stuck turn recovery, delegated-result delivery, and preview rendering would not reliably run in the background.

The design is careful because the app may run in more than one process. A schedule can outlive the code that created it, so unknown job keys are skipped instead of deleted. Each job tick first asks which workspaces actually need work, then queues one durable workflow per workspace. Think of this like a post office: the tick sorts mail by neighborhood, and each workspace gets its own delivery route. If one workspace is still busy from the last run, it does not block the others, and duplicate runs are avoided with a deduplication key.

The file also includes two important built-in job engines. TurnDispatcher finds conversation turns that are queued or parked and safely offers them to the turn worker queue. PageChangeRunner tracks page updates with per-consumer cursors, so each extension hook resumes where it left off and failures do not advance the cursor. JobRunner ties everything together: it registers schedules, fans out ticks, binds execution to a workspace, builds the extension context, and calls the handler.

#### Function details

##### `ResultDeliverer.run`  (lines 68–68)

```
async def run(self) -> None
```

**Purpose**: This is a protocol method, meaning it describes what a result-delivery object must provide without implementing it here. The method is expected to sweep for finished child-agent work and deliver those results back to the main conversation flow.

**Data flow**: It takes no direct input except the object’s own stored setup. When implemented elsewhere, it reads finished child results, posts or records their arrival, and returns when that sweep is done.

**Call relations**: Core job construction accepts any object matching this shape. The result-delivery job later calls this method through the small wrapper created inside core_jobs, so this file can schedule the sweep without importing the turn-loop implementation directly.


##### `ResultDeliverer.candidate_workspaces`  (lines 70–70)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This protocol method says a result-delivery object must be able to name the workspaces where there may be results to deliver. That lets the job system avoid opening every workspace on every tick.

**Data flow**: It takes no direct input. An implementation looks at its own storage or queues, finds workspace IDs that have pending delivery work, and returns those IDs as a tuple.

**Call relations**: The result-delivery JobSpec uses this as its candidate finder. JobRunner.tick calls the JobSpec’s candidate function before queuing per-workspace work.


##### `TurnDispatcher.run`  (lines 136–170)

```
async def run(self) -> None
```

**Purpose**: This scans one workspace for conversation turns that should be put back onto the worker queue. It also checks whether parked turns are now allowed to run, based on seats, spending rules, and balance limits.

**Data flow**: It starts with no explicit input and reads dispatchable turns from the workspace database. For parked turns, it gathers the relevant members, checks whether they have seats, whether spending is allowed, and whether balance rules permit the turn. Turns that pass are stamped and enqueued; turns that fail stay parked.

**Call relations**: The turn-dispatch core job calls this after JobRunner has bound execution to a specific workspace. It first asks _dispatchable_turns for candidates, then hands each eligible turn to _enqueue.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 6 external calls (__init__, __init__, __init__, select, workspace_tx, gate_member).


##### `TurnDispatcher.candidate_workspaces`  (lines 172–180)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This finds which workspaces have queued or parked turns that might need dispatching. It keeps the periodic turn-dispatch job from doing empty work in workspaces with nothing pending.

**Data flow**: It computes a cutoff time for stale dispatch stamps, reads the owner-level database view, filters with _eligible, and returns distinct workspace IDs. Nothing is changed in the database.

**Call relations**: The turn-dispatch JobSpec uses this as its candidate finder. JobRunner.tick calls it before queuing per-workspace TurnDispatcher.run executions.

*Call graph*: calls 1 internal fn (_eligible); 4 external calls (now, timedelta, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 182–221)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: This reads a limited batch of turns in the current workspace that are safe candidates for dispatch. It prefers queued turns before parked turns and preserves conversation order.

**Data flow**: It computes a stale-stamp cutoff, queries the workspace database for turns matching _eligible, joins in conversation information, and converts each database row into a small _DispatchTurn record. The output is a tuple of turn records ready for further checks or enqueueing.

**Call relations**: TurnDispatcher.run calls this at the start of a sweep. Its output is the list that run either gate-checks, for parked turns, or sends to _enqueue.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 6 external calls (__init__, now, timedelta, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 223–253)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: This safely offers one turn to the DBOS turn worker queue. It first marks the database row so two sweepers do not offer the same turn at the same time.

**Data flow**: It receives a _DispatchTurn. It checks that the turn is still in the same status, still stale enough to offer, and still first in its conversation for that status; if so, it updates the dispatch timestamp and reads the running attempt. It then builds queue options, chooses a workflow ID, and enqueues the turn; if the row could not be claimed, it returns without doing anything.

**Call relations**: TurnDispatcher.run calls this for each turn that should be offered. It uses _first_in_status and _stale to protect ordering and avoid duplicate dispatch.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 1 (run); 5 external calls (now, timedelta, update, workspace_tx, uuid4).


##### `TurnDispatcher._eligible`  (lines 255–263)

```
def _eligible(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for turns that are worth considering. A turn is eligible if it is queued or parked, has no recent dispatch stamp, and is the earliest such turn in its conversation.

**Data flow**: It receives a cutoff time. It combines smaller database conditions from _stale and _first_in_status into one SQL expression, which callers use in SELECT queries.

**Call relations**: candidate_workspaces uses it to find workspaces with possible work. _dispatchable_turns uses it to fetch the actual turns inside one workspace.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 2 external calls (and_, or_).


##### `TurnDispatcher._stale`  (lines 265–269)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the condition that says a turn’s previous dispatch offer is old enough to retry, or was never made. It is the safety valve for crashes after a turn was stamped but before the external queue offer completed.

**Data flow**: It receives a cutoff time. It returns a database expression that is true when dispatch_enqueued_at is missing or older than that cutoff.

**Call relations**: _eligible uses this while scanning. _enqueue uses it again during the claiming update, so a turn must still be stale at the exact moment it is stamped.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 271–280)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the condition that a turn is the earliest turn with a given status in its conversation. It prevents later turns from jumping ahead of earlier ones.

**Data flow**: It receives a turn status such as queued or parked. It creates a database expression that says no earlier turn in the same workspace and conversation has that same status.

**Call relations**: _eligible uses it during scans, and _enqueue repeats the check while claiming the row. That double check keeps ordering correct even when another process is working at the same time.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `_page_beyond_cursor`  (lines 283–288)

```
def _page_beyond_cursor(revision: int, page_id: UUID, cursor: object) -> bool
```

**Purpose**: This answers whether a page version comes after a saved page-change cursor. It is a small helper for deciding if a workspace has changes a consumer has not seen yet.

**Data flow**: It receives a page revision, a page ID, and a stored cursor value. If there is no cursor, it returns true. Otherwise it parses the cursor into its boundary revision and ID, then returns true only when the page is later in that ordered stream.

**Call relations**: PageChangeRunner.workspaces_with_changes calls this while comparing each workspace’s newest page with that consumer’s saved cursor.

*Call graph*: called by 1 (workspaces_with_changes); 1 external calls (page_cursor).


##### `PageChangeConsumer.spec_name`  (lines 309–311)

```
def spec_name(self) -> str
```

**Purpose**: This gives one page-change consumer its unique JobSpec name. The name includes the extension and handler discriminator so separate hooks do not collide.

**Data flow**: It reads the consumer’s extension name and discriminator. It returns a string like page_change:extension:handler_name.

**Call relations**: core_jobs reads this property while creating one core JobSpec per page-change hook.


##### `PageChangeConsumer.job`  (lines 314–319)

```
def job(self) -> str
```

**Purpose**: This gives the tracking key used when this page-change consumer runs as a core job. The key is also used for attributing model usage and latency to the right consumer.

**Data flow**: It reads the consumer’s spec_name and prefixes it with the core namespace. It returns a stable string key.

**Call relations**: PageChangeRunner._context_for passes this key into context_for so the hook’s background work is labeled correctly.


##### `PageChangeRunner.consumers`  (lines 362–386)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: This discovers all registered page_change hooks from the active extension manifests. It turns hook declarations into PageChangeConsumer records that can each become an independent background job.

**Data flow**: It reads every manifest, records the credential slots each extension declared, filters hooks whose event is page_change, checks that handler names are unique within an extension, and returns the resulting consumers. If two hooks would share the same cursor key, it raises an error instead of silently mixing their progress.

**Call relations**: core_jobs calls this when building the list of built-in jobs. Each returned consumer becomes a separate scheduled JobSpec.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 388–453)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: This finds the workspaces where one page-change consumer has unseen page updates. It avoids running the consumer in workspaces where the newest page is already at or behind that consumer’s cursor.

**Data flow**: It receives a PageChangeConsumer. It reads that consumer’s stored cursors and each workspace’s newest page from the owner-level database view, compares them with _page_beyond_cursor, warns if a cursor cannot be parsed, and returns workspace IDs that still have pending changes.

**Call relations**: The candidate function created by core_jobs calls this for each page-change consumer. JobRunner.tick uses the returned workspaces to queue only useful per-workspace runs.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 3 external calls (select, owner_tx, warn).


##### `PageChangeRunner.drive`  (lines 455–505)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: This runs one page-change consumer inside the currently bound workspace. It reads changed pages in batches, calls the extension hook, and advances that consumer’s cursor only after the hook succeeds.

**Data flow**: It receives a PageChangeConsumer and builds an ExtensionContext for it. It reads the saved cursor from the extension store, repeatedly fetches pages after that cursor, wraps them in a PageChangeBatch, and calls the hook. On success it stores the next cursor with a compare-and-set check; on failure it logs and counts the stall, leaves the cursor unchanged, and re-raises the error.

**Call relations**: The handler wrapper made by core_jobs calls this during a page-change job execution. It relies on _context_for to create the hook environment and is normally reached through JobRunner.fire.

*Call graph*: calls 1 internal fn (_context_for); 6 external calls (__init__, __init__, emit_metric, formatted_stack, log_error, ws_current).


##### `PageChangeRunner._context_for`  (lines 507–525)

```
def _context_for(self, consumer: PageChangeConsumer) -> ExtensionContext
```

**Purpose**: This builds the ExtensionContext used by a page-change hook. The context is the bundle of services the extension can use, such as page access, model access, blob storage, and optional turn invocation.

**Data flow**: It receives a PageChangeConsumer. It may create a workspace-specific turn invoker, swaps the model registry to the configured background model when appropriate, and passes all available services to context_for. The result is an ExtensionContext scoped to that extension and workspace.

**Call relations**: PageChangeRunner.drive calls this before invoking a hook. It uses _background_registry so background page-change work can use the background model setting.

*Call graph*: calls 1 internal fn (_background_registry); called by 1 (drive); 2 external calls (context_for, ws_current).


##### `_background_registry`  (lines 528–539)

```
def _background_registry(registry: ModelRegistry | None, background_model: str | None) -> ModelRegistry | None
```

**Purpose**: This returns a model registry adjusted for background jobs. If a background model is configured, it replaces the registry’s automatic default model while keeping the rest of the registry intact.

**Data flow**: It receives an optional ModelRegistry and optional background model name. If either is missing, it returns the registry unchanged; otherwise it returns a copied registry with auto_model changed.

**Call relations**: PageChangeRunner._context_for uses it for page-change hooks. JobRunner.fire uses it for ordinary jobs unless a job explicitly says it needs the deployment’s normal model.

*Call graph*: called by 2 (fire, _context_for); 1 external calls (replace).


##### `core_jobs`  (lines 542–634)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, page_change_runner: PageChangeRunner, delivery_sweep: ResultDeliverer, preview_renderer: PreviewRenderer | None) -> tuple[JobSpe
```

**Purpose**: This defines the built-in background jobs that every deployment should run. It packages source syncing, page-change fan-out, turn dispatch, result delivery, and optional preview rendering as JobSpec objects.

**Data flow**: It receives the service objects that know how to do each kind of core work. It creates small async handler functions and candidate functions around them, discovers page-change consumers, and returns a tuple of JobSpec records with names, schedules, handlers, and candidate finders.

**Call relations**: Startup code can call this before bindings_from. The resulting JobSpecs are later registered and run by JobRunner just like extension-provided jobs.

*Call graph*: calls 1 internal fn (consumers); 1 external calls (__init__).


##### `core_jobs._sync_sources`  (lines 562–563)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: This is the handler for the core source-sync job. It asks the SyncDriver to poll sources and bring page data into the system.

**Data flow**: It receives an ExtensionContext because all JobSpec handlers share that shape, but it does not use it. It calls the sync driver and returns when syncing finishes.

**Call relations**: core_jobs places this inside the SOURCE_SYNC_JOB JobSpec. JobRunner.fire calls it when that job runs for a workspace.


##### `core_jobs._dispatch_turns`  (lines 565–566)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: This is the handler for the core turn-dispatch job. It asks TurnDispatcher to recover queued or newly allowed parked turns.

**Data flow**: It receives the standard job context but does not use it directly. It calls turn_dispatcher.run, which reads and enqueues eligible turns, then returns.

**Call relations**: core_jobs places this inside the TURN_DISPATCH_JOB JobSpec. JobRunner.fire calls it after binding a workspace.


##### `core_jobs._deliver_results`  (lines 568–569)

```
async def _deliver_results(context: ExtensionContext) -> None
```

**Purpose**: This is the handler for the core result-delivery job. It triggers the sweep that posts finished delegated child-agent results back into the conversation flow.

**Data flow**: It receives the standard job context but does not use it directly. It calls delivery_sweep.run and returns when the sweep completes.

**Call relations**: core_jobs places this inside the RESULT_DELIVERY_JOB JobSpec. The actual delivery implementation lives elsewhere behind the ResultDeliverer protocol.


##### `core_jobs._render_previews`  (lines 571–573)

```
async def _render_previews(context: ExtensionContext) -> None
```

**Purpose**: This is the handler for the optional preview-rendering job. It asks the preview renderer to produce or refresh previews for the current workspace.

**Data flow**: It receives the standard job context, checks that a preview renderer exists, calls its run method, and returns when rendering work is done.

**Call relations**: core_jobs only includes a render-previews JobSpec when a PreviewRenderer was provided. JobRunner.fire calls this handler for that job.


##### `core_jobs._preview_candidates`  (lines 575–577)

```
async def _preview_candidates() -> tuple[UUID, ...]
```

**Purpose**: This finds workspaces that need preview-rendering work. It delegates the decision to the preview renderer.

**Data flow**: It takes no direct input, checks that the renderer exists, asks it for candidate workspaces, and returns those IDs.

**Call relations**: core_jobs attaches this to the optional render-previews JobSpec. JobRunner.tick calls it before queuing preview-rendering workflows.


##### `core_jobs._drive_consumer`  (lines 579–585)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: This creates a JobSpec-compatible handler for one page-change consumer. It is a small adapter from the generic job-handler shape to PageChangeRunner.drive.

**Data flow**: It receives a PageChangeConsumer and returns an async handler function. The returned function will later ignore the generic context and call page_change_runner.drive for that consumer.

**Call relations**: core_jobs uses this while building one JobSpec per page-change hook. The returned _handler is what JobRunner.fire eventually invokes.


##### `core_jobs._drive_consumer._handler`  (lines 582–583)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: This is the actual per-consumer page-change job handler made by _drive_consumer. It runs the shared cursor loop for one specific consumer.

**Data flow**: It receives an ExtensionContext because job handlers must accept one, but the PageChangeRunner builds its own context for the target consumer. It calls page_change_runner.drive and returns when the consumer has caught up or stopped.

**Call relations**: JobRunner.fire calls this through the JobSpec handler field. It hands control to PageChangeRunner.drive, which performs batching, hook invocation, and cursor advancement.


##### `core_jobs._consumer_candidates`  (lines 587–591)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: This creates a candidate-workspace function for one page-change consumer. It lets each consumer decide pending work using its own cursor.

**Data flow**: It receives a PageChangeConsumer and returns an async function. That function later asks PageChangeRunner which workspaces have changes for exactly this consumer.

**Call relations**: core_jobs attaches the returned _candidates function to the consumer’s JobSpec. JobRunner.tick calls it before fan-out.


##### `core_jobs._consumer_candidates._candidates`  (lines 588–589)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: This is the candidate finder made for one page-change consumer. It returns only workspaces where that consumer has page changes to process.

**Data flow**: It takes no direct input. It calls page_change_runner.workspaces_with_changes with the captured consumer and returns the workspace IDs it finds.

**Call relations**: JobRunner.tick reaches this through the JobSpec candidate field. It hands off the real comparison work to PageChangeRunner.workspaces_with_changes.


##### `bindings_from`  (lines 647–674)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...]) -> tuple[_Binding, ...]
```

**Purpose**: This turns core and extension JobSpecs into binding records with stable keys and extension metadata. A binding says, “this exact job name runs under this extension context with these credential slots.”

**Data flow**: It receives active manifests and core JobSpecs. It creates core bindings under the core namespace, then creates extension bindings under each manifest’s namespace, carrying declared credential slot names and manifest flags. It returns the full tuple of bindings.

**Call relations**: Startup code uses this before constructing JobRunner. JobRunner later uses the bindings to register schedules, find candidates, and build the right ExtensionContext for a job.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 702–726)

```
def launch(self) -> None
```

**Purpose**: This publishes the JobRunner as the active runner and registers every job with DBOS. Scheduled jobs become cron-like schedules; one-shot jobs are enqueued once with deduplication.

**Data flow**: It reads self.bindings, stores self in the module-level _firing variable, and for each binding either enqueues a one-shot tick or adds a ScheduleInput. It logs registrations, warns when a one-shot was already enqueued, and finally applies all schedules to DBOS.

**Call relations**: This should run during service startup. The DBOS workflow functions job_tick and job_workflow later look up the runner through _firing, so launch must happen before any job workflow fires.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 728–750)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: This handles one scheduled firing of a job key. It fans the job out to all workspaces that currently have work and queues one durable workflow per workspace.

**Data flow**: It receives the scheduled time and a job key. It first checks whether this process knows that key; unknown keys are warned and skipped. For each candidate workspace, it enqueues job_workflow with a deduplication ID based on job and workspace, warning if an identical run is already active.

**Call relations**: The DBOS workflow job_tick calls this. It uses _registered to tolerate stale schedules and candidates to get workspace IDs before enqueueing job_workflow.

*Call graph*: calls 2 internal fn (_registered, candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 752–753)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: This asks the binding for a job key which workspaces need that job. It is a thin wrapper that centralizes the binding lookup.

**Data flow**: It receives a job key, finds the matching binding, calls the binding’s JobSpec candidate function, and returns the resulting workspace IDs.

**Call relations**: JobRunner.tick calls this after confirming the key is registered. It relies on _binding to fail loudly if the key unexpectedly has no binding.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 755–782)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: This runs one job handler inside one workspace. It binds workspace context, prepares any extension agents, builds the ExtensionContext, and calls the job’s handler.

**Data flow**: It receives a job key and workspace ID. It finds the binding, enters the workspace scope, optionally provisions agents from the manifest, builds service access for the extension, chooses the right model registry, and awaits the handler. If the handler fails, it logs the job failure and re-raises the error so the workflow is marked failed.

**Call relations**: The DBOS workflow job_workflow calls this for each workspace-specific execution. It uses _binding for metadata and _background_registry when the job should use the background model.

*Call graph*: calls 2 internal fn (_binding, _background_registry); 4 external calls (__init__, context_for, log_error, ws).


##### `JobRunner._registered`  (lines 784–785)

```
def _registered(self, key: str) -> _Binding | None
```

**Purpose**: This checks whether the current process has a binding for a job key. It returns nothing instead of raising because stale or peer-owned schedules are expected in a multi-process deployment.

**Data flow**: It receives a key, scans self.bindings for a matching binding key, and returns that binding or null.

**Call relations**: JobRunner.tick uses this to skip unknown scheduled keys safely. JobRunner._binding uses it as the first step before deciding whether to raise an error.

*Call graph*: called by 2 (_binding, tick).


##### `JobRunner._binding`  (lines 787–794)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: This retrieves the binding for a job key and treats a missing binding as a real fault. It is used only when the caller expects the key to be valid for this process.

**Data flow**: It receives a key, calls _registered, and returns the binding if found. If no binding exists, it raises a RuntimeError.

**Call relations**: JobRunner.candidates and JobRunner.fire call this because they cannot proceed without the job’s metadata, candidate function, and handler.

*Call graph*: calls 1 internal fn (_registered); called by 2 (candidates, fire).


##### `job_tick`  (lines 801–805)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: This is the DBOS durable workflow for a job tick. It is the persisted entry point DBOS calls when a schedule fires or a one-shot job tick is enqueued.

**Data flow**: It receives the scheduled time and job key from DBOS. It reads the module-level active JobRunner and, if present, delegates to runner.tick; if launch has not registered a runner, it raises an error.

**Call relations**: JobRunner.launch registers or enqueues this workflow for every job. Its job is not to do the work directly, but to fan out workspace-specific job_workflow runs through JobRunner.tick.


##### `job_workflow`  (lines 809–813)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: This is the DBOS durable workflow for one job running in one workspace. It is the persisted unit of actual background-job execution.

**Data flow**: It receives the scheduled time, job key, and workspace ID as a string. It reads the active JobRunner, converts the workspace ID into a UUID, and calls runner.fire; if no runner was launched, it raises an error.

**Call relations**: JobRunner.tick enqueues this workflow once per candidate workspace. It hands off to JobRunner.fire, which binds the workspace and invokes the real job handler.

*Call graph*: 1 external calls (UUID).


### `core/src/ufo/preview_renderer.py`

`orchestration` · `scheduled background retry job`

When someone shares a file, the system tries to make a preview image right away. That first try is only “best effort”: if the preview service is briefly down, the file is still shared, but the database row is left with empty preview fields. This file provides the follow-up job that fixes those gaps.

The main class, `PreviewRenderer`, works like a scheduled repair crew. It looks for recently shared artifacts that have no preview yet and whose filenames look renderable, such as supported document suffixes. It only looks within a one-hour retry window. That limit is important: a permanently broken or unsupported file should not be retried forever.

For each candidate, it does not download the file into the core application. Instead, it creates two temporary signed URLs: one that lets the preview service read the source file, and one that lets it upload the generated PNG preview. A signed URL is like a short-lived permission slip for one storage action. The preview service fetches the source, renders the image, uploads it, and reports the size. Then this job writes the preview key, media type, and size back to the shared artifact row.

If the preview service cannot be reached or refuses the request, the job logs the problem and leaves the row untouched so a later scheduled run can try again.

#### Function details

##### `_eligible`  (lines 44–45)

```
def _eligible(filename_column: sa.Column) -> sa.ColumnElement[bool]
```

**Purpose**: This helper builds the database test for whether a filename looks like a type that can have a preview. It keeps the preview job from wasting time on files whose suffix is not in the supported preview list.

**Data flow**: It receives a database column that contains filenames. It compares that column against each supported preview suffix in a case-insensitive way. It returns one combined database condition meaning “the filename ends with one of these supported suffixes.”

**Call relations**: The preview search methods call this helper when they build their database queries. `PreviewRenderer.run` uses it to find rows in the current workspace that should be retried, and `PreviewRenderer.candidate_workspaces` uses it to find which workspaces have any such rows.

*Call graph*: called by 2 (candidate_workspaces, run); 2 external calls (ilike, or_).


##### `PreviewRenderer.run`  (lines 59–81)

```
async def run(self) -> None
```

**Purpose**: This is the main body of the retry job for one workspace. It finds a small batch of recent shared artifacts that still need previews, then tries to render each one.

**Data flow**: It starts by calculating the oldest share time still worth retrying. It reads the workspace database for shared artifact rows with no preview, created after that cutoff, and with an eligible filename. If there are no rows, it stops. Otherwise, it opens an HTTP client and sends each row’s blob key and filename to `_render_one`, which attempts the actual render and database update.

**Call relations**: A scheduler or job runner calls this method after constructing a `PreviewRenderer` for the workspace. It relies on `_eligible` to narrow the database search, then hands each selected artifact to `_render_one` to talk to storage, the preview service, and the database.

*Call graph*: calls 2 internal fn (_render_one, _eligible); 4 external calls (now, AsyncClient, select, workspace_tx).


##### `PreviewRenderer._render_one`  (lines 83–119)

```
async def _render_one(self, client: httpx.AsyncClient, blob_key: str, filename: str) -> None
```

**Purpose**: This function tries to create and record one missing preview. It prepares temporary storage URLs, asks the preview service to render the file, and updates the shared artifact row if the render succeeds.

**Data flow**: It receives an HTTP client, the stored source file key, and the original filename. From the filename it chooses the file kind and creates a new PNG preview key. It asks the blob store for a temporary read URL for the source and a temporary upload URL for the preview. It sends those URLs, the desired preview size, and page count to the preview service. If the service fails or returns a non-success status, it logs the failure and changes nothing. If the service succeeds, it reads the returned byte size and writes the preview key, PNG media type, and size into the matching database row, but only if that row still has no preview.

**Call relations**: `PreviewRenderer.run` calls this once for each candidate row in its batch. This function is the point where the retry job hands work to the external preview service. After the service has uploaded the preview directly to blob storage, `_render_one` records the result in the workspace database.

*Call graph*: called by 1 (run); 7 external calls (post, dumps, PurePosixPath, update, workspace_tx, log, uuid4).


##### `PreviewRenderer.candidate_workspaces`  (lines 121–135)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This method finds which workspaces currently have shared artifacts that may need preview retries. It lets a higher-level job decide where `PreviewRenderer.run` should be executed.

**Data flow**: It calculates the same retry cutoff time used by the main job. It reads from the owner-level database view of shared artifacts, looking for distinct workspace IDs where a recent eligible artifact still has no preview. It returns those workspace IDs as a tuple.

**Call relations**: A scheduler or coordinator can call this before running workspace-specific preview retries. It uses `_eligible` to avoid selecting workspaces that only have unsupported filenames, and it reads through `owner_tx` because it is looking across workspaces rather than inside just one workspace.

*Call graph*: calls 1 internal fn (_eligible); 3 external calls (now, select, owner_tx).

## 📊 State Registers Touched

- `reg-effective-config` — The merged deployment settings that tell the process how to run, which services to use, and which safety options are enabled.
- `reg-extension-registry` — The loaded list of installed extensions, packs, routes, tools, skills, jobs, credentials, backends, and migrations.
- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-credential-vault` — The encrypted store of API keys, OAuth tokens, and other secrets that can be injected only into approved places.
- `reg-connection-grants` — The saved account connections and per-agent permissions that say which outside accounts an agent may use.
- `reg-agent-settings` — The durable settings for each agent, including model choice, reasoning mode, internet access, sandbox size, tools, setup needs, icon, and visibility.
- `reg-surface-installations` — The saved bindings for web, Slack, iMessage, CLI, hosted sites, and other surfaces that connect outside channels to workspaces and agents.
- `reg-conversation-records` — The durable conversation rows that remember where a conversation came from, which agent owns it, its audience, title, sandbox, and current metadata.
- `reg-inbound-message-queue` — The saved holding area for incoming chat messages before they are admitted into a running or queued turn.
- `reg-turn-run-state` — The shared state of each unit of agent work, including queued, claimed, running, parked, canceled, recovered, or finished.
- `reg-runtime-fleet` — The records of running server or worker instances, their heartbeats, listener claims, and cleanup ownership.
- `reg-model-catalog` — The shared directory of available AI models, their providers, limits, prices, key requirements, and routing behavior.
- `reg-model-usage-accounting` — The recorded token, image, video, embedding, sandbox, egress, and cost usage used for billing and audit trails.
- `reg-spend-controls` — The workspace spending caps, prepaid balances, top-up settings, BYOK flags, and billing export state.
- `reg-file-blob-store` — The shared byte storage for uploads, generated files, previews, media, and other raw data, separated by workspace or deployment scope.
- `reg-artifact-registry` — The saved list of files deliberately shared with users, including ownership, access checks, preview metadata, and download links.
- `reg-source-sync-catalog` — The saved catalog of external sources, pages, sync cursors, deletion marks, retry backoff, and indexing needs.
- `reg-search-index` — The shared keyword and embedding indexes that let conversations, tools, and background jobs find relevant stored documents.
- `reg-memory-store` — The durable store of remembered facts and memory-search results that can be written, deduplicated, recalled, and shown later.
- `reg-scheduled-jobs` — The durable background job and scheduled task state used for recurring work, wakeups, retries, monitors, billing, and offline evaluation.
- `reg-subagent-delegation` — The shared state for spawned helper agents, including their catalog entries, parent-child turn links, required results, names, and cancellation state.
- `reg-extension-object-slots` — The extension-owned object and conversation-panel data, such as artifacts, sources, tasks, sites, automations, and custom workspace objects.
- `reg-observability-traces` — The shared trace, metric, log, and traceparent information that lets operators connect startup, turns, tools, subagents, and billing events.
- `reg-prompt-change-proposals` — The durable proposal and governance state for suggested agent prompt or behavior changes, including approval and offline-improvement outcomes before agent settings are rewritten.
- `reg-provider-runtime-controls` — The in-process model-provider client pools, retry/backoff state, rate-limit buckets, and request-budget guards shared by model calls and background work.
- `reg-extension-workflow-state` — Extension-owned durable workflow records that are not just UI slots, such as code-review inboxes, evaluation runs, objectives, pauses, briefs, notes, monitors, triggers, and web-chat state.
- `reg-extension-kv-store` — Per-workspace extension key/value JSON and setup marker state saved outside core schemas, used by extension setup, runtime behavior, jobs, and cleanup migrations.
- `reg-connector-action-cache` — Dynamic connector/MCP action schemas, allowed-action listings, and runtime client/session caches reused when exposing and executing external-service actions.
- `reg-seat-entitlements` — Workspace seat limits, included-seat counts, and seated-member marks that gate access and billing entitlement decisions.
- `reg-prompt-render-audit` — Rendered-prompt fingerprints, template provenance, and compaction/prompt hashes used to trace or reproduce the exact context sent to models.
