# Recurring jobs, scheduled automation, reports, and self-improvement  `stage-15`

While the server is running, this stage is the backstage crew for work not tied to one live request. candidates.py safely finds workspaces with pending work, and jobs.py schedules each job in the right workspace without duplicate pileups. delivery.py rescues missed child-task results, preview_renderer.py fills in missing file thumbnails, and product.py recounts funnel metrics from existing data.

The scheduled-task pieces are the clockwork: schedules.py stores recurring prompts, cron.py checks repeat rules, scheduled_fire.py defines the exact run key, runner.py fires due tasks once, pauses.py stores timed waits, and pause_runner.py wakes them. Monitors use monitors.py for watch records and monitor_runner.py to run checks and notify agents. Reports use digest.py for summary rules and writer.py to create feed entries. Other helpers clean old site homepages, store source-change wakeups, and refresh web start suggestions.

Self-improvement builds test cases from past failures, proposes prompt rewrites, replays old conversations without re-running tools, judges answers, gates promotion, uses one model access path, and runs these checks on a schedule.

## Files in this stage

### Background job framework
These files discover runnable workspace background work, execute it safely, and run core catch-up or census jobs.

### `core/src/ufo/runtime/candidates.py`

`orchestration` · `main loop`

In this system, work is always done inside a workspace, like entering a specific room before touching anything in it. The job dispatcher first needs a list of rooms to visit. This file provides that list-making step.

The important safety rule is that normal database reads are scoped to one workspace, so one tenant cannot see another tenant’s data. There is one special cross-workspace read path, called `owner_tx`, which bypasses that protection. This file keeps that dangerous tool tightly contained: it may only be used to read workspace IDs, not actual tenant rows.

Extensions do not call `owner_tx` directly. Instead, they provide a small query builder, `due`, that creates a database query selecting distinct `workspace_id` values from their own tables. `owner_candidates` wraps that builder in a callable the dispatcher can run on each scheduling tick. Building the query fresh each time matters because “due now” often depends on the current time; a query built once at startup could become stale.

The result is a clean boundary: extensions say which workspaces might need work, core performs the one permitted cross-workspace lookup, and the dispatcher later re-enters each returned workspace before running the actual job.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: This function turns a query builder into a workspace-candidate provider. It gives callers a safe way to declare which workspaces have pending work, while keeping the privileged cross-workspace database access inside core code.

**Data flow**: It receives `due`, a no-argument function that builds a database `Select` query whose first column is a workspace ID. It creates and returns an async `candidates` function. Nothing is read immediately; the actual database lookup happens later each time the returned function is called.

**Call relations**: This is the public seam for code that needs to name workspaces without touching the privileged database path itself. It packages the caller’s query builder together with the inner `owner_candidates.candidates` function, which will run the query when the dispatcher or another runtime component asks for candidates.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This async function performs the actual privileged read of workspace IDs. It opens the special owner-level database transaction, runs the freshly built query, and returns only the IDs from the first column of each row.

**Data flow**: When called, it opens `owner_tx`, the cross-workspace database access path. Inside that connection, it calls `due()` to build the current query, executes it, collects all returned rows, and then converts the first value of each row into a tuple of workspace UUIDs. It does not return any other row data.

**Call relations**: This function is the runtime action produced by `owner_candidates`. Its key handoff is to `ufo.db.owner_tx`, which provides the temporary privileged connection needed to look across workspaces. After it returns the tuple of IDs, the dispatcher can bind to each workspace separately before running real work there.

*Call graph*: 1 external calls (owner_tx).


### `core/src/ufo/runtime/jobs.py`

`orchestration` · `startup registration and scheduled background execution`

This file is the project’s background-job switchboard. At startup, the system discovers core jobs and extension-provided jobs, gives each one a stable key, and registers it with DBOS, the durable workflow system that can schedule and resume work safely. Without this file, source syncing, page-change hooks, queued turn recovery, result delivery, preview rendering, and product census tasks would not reliably run after boot.

The main idea is two-step firing. A scheduled tick first asks, “Which workspaces actually have work for this job?” Then it enqueues one real job run for each workspace. This avoids wasting work on quiet workspaces and stops one slow workspace from blocking others. Duplicate protection is built in, so if a job is already running for a workspace, another tick does not stack up behind it.

The file also handles special job families. `TurnDispatcher` rescues queued or parked conversation turns and puts them back onto the turn-processing queue only when seats, spending limits, and balance checks allow it. `PageChangeRunner` lets extension hooks process changed pages using a stored cursor, like a bookmark in a book, so each hook resumes where it left off. `JobRunner` is the central launcher and executor: it registers schedules, fans ticks out by workspace, builds the extension context, provisions agents if needed, and logs failures clearly.

#### Function details

##### `ResultDeliverer.run`  (lines 82–82)

```
async def run(self) -> None
```

**Purpose**: This protocol method describes the action a result-delivery sweep must provide. It represents the work of finding finished child agent results and handing them back into the conversation flow.

**Data flow**: It takes no direct inputs beyond the implementing object. The implementer performs its delivery sweep and returns nothing, but may change database state by posting or recording delivered results.

**Call relations**: This file does not implement the body; it names the shape expected from another subsystem. `core_jobs._deliver_results` calls this method when the scheduled result-delivery job fires.


##### `ResultDeliverer.candidate_workspaces`  (lines 84–84)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This protocol method asks which workspaces have result-delivery work waiting. It lets the job system avoid opening every workspace when only some need attention.

**Data flow**: It takes no direct inputs beyond the implementing object. The implementer looks up pending result-delivery work and returns a tuple of workspace IDs.

**Call relations**: The core job definition uses this method as the candidate finder for the result-delivery job. `JobRunner.tick` eventually calls candidate functions like this before enqueueing per-workspace runs.


##### `TurnDispatcher.run`  (lines 149–183)

```
async def run(self) -> None
```

**Purpose**: This function scans for conversation turns that are ready to be offered to the turn-processing workers. It also keeps parked turns parked when the user lacks a seat, spending approval, or balance.

**Data flow**: It reads dispatchable turn rows from the current workspace. For parked rows, it checks relevant members, seating, spending policy, and balance. Rows that pass are marked and enqueued; rows that fail are left alone for a later sweep.

**Call relations**: Scheduled turn-dispatch jobs reach this through `core_jobs._dispatch_turns`. It gets possible turns from `_dispatchable_turns`, performs gating checks, and hands approved turns to `_enqueue` so DBOS can run them on the turn queue.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 6 external calls (__init__, __init__, __init__, select, workspace_tx, gate_member).


##### `TurnDispatcher.candidate_workspaces`  (lines 185–193)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This function finds workspaces that contain queued or parked turns eligible for dispatch. It is the fleet-wide pre-check before opening individual workspaces.

**Data flow**: It computes a grace-period cutoff time, reads the owner-level database view for distinct workspace IDs matching `_eligible`, and returns those IDs.

**Call relations**: The turn-dispatch `JobSpec` uses this as its candidate finder. `JobRunner.tick` calls it before enqueueing one turn-dispatch workflow per workspace.

*Call graph*: calls 1 internal fn (_eligible); 4 external calls (now, timedelta, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 195–234)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: This private helper fetches the specific turn rows that this workspace should try to dispatch now. It limits the batch so one sweep cannot grab unbounded work.

**Data flow**: It computes a stale cutoff, queries the workspace database for eligible turns joined with their conversation information, orders them so queued work comes before parked work, and converts rows into `_DispatchTurn` records.

**Call relations**: `TurnDispatcher.run` calls this first. The returned records are then either checked for park-related gates or sent straight to `_enqueue`.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 6 external calls (__init__, now, timedelta, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 236–266)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: This private helper safely stamps one turn as offered and enqueues it for actual turn processing. It prevents two sweepers from offering the same turn at the same time.

**Data flow**: It receives a `_DispatchTurn`, rechecks that the row is still stale and first in order, updates its dispatch timestamp, chooses a safe workflow ID, and enqueues the turn into the DBOS turn queue. If another process already claimed it, it returns without doing anything.

**Call relations**: `TurnDispatcher.run` calls this after a turn is considered ready. It relies on `_first_in_status` and `_stale` to build the guarded update before handing the turn to DBOS.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 1 (run); 5 external calls (now, timedelta, update, workspace_tx, uuid4).


##### `TurnDispatcher._eligible`  (lines 268–276)

```
def _eligible(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This private helper builds the database condition for turns that may be dispatched. It encodes the rule that only stale queued or parked turns, and only the first such turn in a conversation, are eligible.

**Data flow**: It receives a cutoff time. It returns a SQL condition that checks turn status, stale dispatch timestamp, and ordering within the conversation.

**Call relations**: `candidate_workspaces` uses this condition to find workspaces with possible work, and `_dispatchable_turns` uses the same rule to fetch actual turn rows.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 2 external calls (and_, or_).


##### `TurnDispatcher._stale`  (lines 278–282)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This private helper builds the database condition for an old or missing dispatch stamp. A stale stamp means a previous attempt may have failed before the worker accepted the turn.

**Data flow**: It receives a cutoff time and returns a SQL condition saying the dispatch timestamp is absent or older than that cutoff.

**Call relations**: `_eligible` uses this while scanning, and `_enqueue` uses it again while claiming a specific row. That second check closes the race where another dispatcher may have just claimed the turn.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 284–293)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: This private helper enforces conversation order. It checks that there is no earlier turn in the same workspace and conversation with the same status.

**Data flow**: It receives a turn status and returns a SQL condition that excludes any turn with a smaller sequence number in that same status.

**Call relations**: `_eligible` uses it while finding dispatchable work, and `_enqueue` uses it again during the update so a later turn cannot overtake an earlier one.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `_page_beyond_cursor`  (lines 296–301)

```
def _page_beyond_cursor(revision: int, page_id: UUID, cursor: object) -> bool
```

**Purpose**: This helper answers whether a page position is newer than a stored page-change cursor. The cursor acts like a bookmark for each page-change consumer.

**Data flow**: It receives a page revision, page ID, and cursor value. If there is no cursor it returns true; otherwise it parses the cursor and compares revision first, then page ID.

**Call relations**: `PageChangeRunner.workspaces_with_changes` uses this while deciding which workspaces have pages that a specific consumer has not seen yet.

*Call graph*: called by 1 (workspaces_with_changes); 1 external calls (page_cursor).


##### `PageChangeConsumer.spec_name`  (lines 322–324)

```
def spec_name(self) -> str
```

**Purpose**: This property gives a page-change consumer its unique job-spec name. It keeps different extension hooks separate even when they all react to page changes.

**Data flow**: It reads the consumer’s extension name and discriminator and returns a string such as `page_change:<extension>:<handler>`.

**Call relations**: `core_jobs` uses consumers produced by `PageChangeRunner.consumers`; their `spec_name` becomes the name of each generated page-change job.


##### `PageChangeConsumer.job`  (lines 327–332)

```
def job(self) -> str
```

**Purpose**: This property gives the accounting and context key for a page-change consumer. It says that the core runner is driving work on behalf of a particular extension hook.

**Data flow**: It reads the consumer’s extension name and spec name, prefixes it with the core namespace, and returns the resulting job key string.

**Call relations**: `PageChangeRunner._context_for` passes this key into `context_for`, so model usage and logging can be attributed to the right page-change consumer.


##### `PageChangeRunner.consumers`  (lines 375–399)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: This function discovers all registered page-change hooks across active extension manifests. It turns them into independent consumers, each with its own job and cursor.

**Data flow**: It reads manifests, gathers declared credential slot names, filters hooks whose event is `page_change`, checks that handler names are not duplicated inside one extension, and returns `PageChangeConsumer` objects.

**Call relations**: `core_jobs` calls this when building the core job list. Each returned consumer becomes its own scheduled page-change job.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 401–466)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: This function finds which workspaces have page changes waiting for one specific page-change consumer. It avoids running the hook in workspaces whose cursor is already up to date.

**Data flow**: It reads each workspace’s newest page and that consumer’s stored cursor. It compares them with `_page_beyond_cursor`; invalid cursors are warned about and treated as pending. It returns only workspace IDs with pending changes.

**Call relations**: The page-change candidate function made by `core_jobs._consumer_candidates` calls this. Its output tells `JobRunner.tick` which per-workspace page-change workflows to enqueue.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 3 external calls (select, owner_tx, warn).


##### `PageChangeRunner.drive`  (lines 468–518)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: This function runs one page-change consumer inside one already-bound workspace. It replays changed pages in batches, calls the extension hook, and advances the cursor only after success.

**Data flow**: It builds an extension context, reads the stored cursor, asks the page feed for changed pages, wraps them in a hook payload, and calls the consumer’s handler. After a successful batch it updates the cursor with a compare-and-set write; on failure it logs, emits a metric, and leaves the cursor unchanged.

**Call relations**: The handler created by `core_jobs._drive_consumer` calls this. It uses `_context_for` to build the extension-facing tools, and it is the place where page-change hooks actually receive their batches.

*Call graph*: calls 1 internal fn (_context_for); 6 external calls (__init__, __init__, emit_metric, formatted_stack, log_error, ws_current).


##### `PageChangeRunner._context_for`  (lines 520–538)

```
def _context_for(self, consumer: PageChangeConsumer) -> ExtensionContext
```

**Purpose**: This private helper builds the `ExtensionContext` used by a page-change handler. That context is the bundle of tools an extension is allowed to use, such as model access, page access, and storage.

**Data flow**: It reads the current workspace, optionally creates a turn invoker for it, adjusts the model registry for background work, and passes all available service objects into `context_for`. It returns the completed extension context.

**Call relations**: `PageChangeRunner.drive` calls this before invoking a hook. It delegates model-choice adjustment to `_background_registry`.

*Call graph*: calls 1 internal fn (_background_registry); called by 1 (drive); 2 external calls (context_for, ws_current).


##### `_background_registry`  (lines 541–552)

```
def _background_registry(registry: ModelRegistry | None, background_model: str | None) -> ModelRegistry | None
```

**Purpose**: This helper swaps the default model in a model registry to the configured background-job model. It lets background tasks use a cheaper or more suitable model without changing foreground member turns.

**Data flow**: It receives an optional registry and optional background model name. If either is missing it returns the registry unchanged; otherwise it returns a copy of the registry with `auto_model` replaced.

**Call relations**: `PageChangeRunner._context_for` uses this for page-change hooks, and `JobRunner.fire` uses it for ordinary jobs unless a job explicitly needs the deploy’s normal model.

*Call graph*: called by 2 (fire, _context_for); 1 external calls (replace).


##### `core_jobs`  (lines 555–660)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, page_change_runner: PageChangeRunner, delivery_sweep: ResultDeliverer, preview_renderer: PreviewRenderer | None) -> tuple[JobSpe
```

**Purpose**: This function defines the background jobs that the core system always contributes. It packages existing services, such as source sync and turn dispatch, into `JobSpec` records the runner can schedule.

**Data flow**: It receives service objects and builds handler and candidate functions around them. It also asks `PageChangeRunner.consumers` for extension page-change hooks and returns a tuple of core `JobSpec` objects.

**Call relations**: Startup code can combine this output with extension job specs through `bindings_from`. The returned specs are later registered and fired by `JobRunner`.

*Call graph*: calls 1 internal fn (consumers); 2 external calls (__init__, seated_member_workspaces).


##### `core_jobs._sync_sources`  (lines 579–580)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: This local handler runs the source sync driver for a workspace. It is the body of the core source-sync job.

**Data flow**: It receives an extension context from the job system, does not use it directly, calls the sync driver, and returns nothing when syncing finishes.

**Call relations**: `core_jobs` places this function into the source-sync `JobSpec`. `JobRunner.fire` later calls it through that spec’s handler.


##### `core_jobs._dispatch_turns`  (lines 582–583)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: This local handler runs the turn dispatcher. It is the body of the scheduled job that recovers queued and parked turns.

**Data flow**: It receives an extension context, does not use it directly, calls `turn_dispatcher.run`, and returns when dispatching is done.

**Call relations**: `core_jobs` places this handler into the turn-dispatch `JobSpec`. When `JobRunner.fire` executes that spec, this function hands control to `TurnDispatcher.run`.


##### `core_jobs._deliver_results`  (lines 585–586)

```
async def _deliver_results(context: ExtensionContext) -> None
```

**Purpose**: This local handler runs the result-delivery sweep. It is the body of the scheduled job that hands completed child-agent work back to conversations.

**Data flow**: It receives an extension context, does not use it directly, calls `delivery_sweep.run`, and returns after the sweep completes.

**Call relations**: `core_jobs` uses this as the result-delivery job handler. The concrete result-delivery subsystem supplies the `ResultDeliverer` implementation.


##### `core_jobs._census_product`  (lines 588–589)

```
async def _census_product(context: ExtensionContext) -> None
```

**Purpose**: This local handler runs the product census job. That job counts product and workspace activity for analytics or funnel reporting.

**Data flow**: It receives an extension context, does not use it directly, calls `product_census`, and returns when the census is finished.

**Call relations**: `core_jobs` installs this function as the product-census `JobSpec` handler. It is later invoked by `JobRunner.fire` like any other core job.

*Call graph*: 1 external calls (product_census).


##### `core_jobs._render_previews`  (lines 591–593)

```
async def _render_previews(context: ExtensionContext) -> None
```

**Purpose**: This local handler runs preview rendering when preview rendering is enabled. It updates generated previews for workspaces that need them.

**Data flow**: It receives an extension context, checks that a preview renderer exists, calls its `run` method, and returns after rendering work completes.

**Call relations**: `core_jobs` includes this handler only when a preview renderer was provided. `JobRunner.fire` invokes it through the optional render-previews job spec.


##### `core_jobs._preview_candidates`  (lines 595–597)

```
async def _preview_candidates() -> tuple[UUID, ...]
```

**Purpose**: This local candidate finder asks the preview renderer which workspaces need preview work. It prevents the preview job from running everywhere.

**Data flow**: It checks that a preview renderer exists, calls its `candidate_workspaces` method, and returns the workspace IDs it reports.

**Call relations**: `core_jobs` attaches this function to the optional render-previews `JobSpec`. `JobRunner.tick` calls it before enqueueing preview-rendering workflows.


##### `core_jobs._drive_consumer`  (lines 599–605)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: This local factory creates a job handler for one page-change consumer. It closes over that consumer so each generated job knows which hook it is driving.

**Data flow**: It receives a `PageChangeConsumer` and returns an async handler function. The returned handler will call `page_change_runner.drive` for that consumer.

**Call relations**: `core_jobs` calls this while creating one page-change `JobSpec` per consumer. The returned `_handler` is later called by `JobRunner.fire`.


##### `core_jobs._drive_consumer._handler`  (lines 602–603)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: This generated handler runs the page-change cursor loop for its captured consumer. It is the actual body of a generated page-change job.

**Data flow**: It receives an extension context from the job system but relies on the page-change runner to build the specific hook context. It calls `page_change_runner.drive` and returns when the consumer is caught up or stops.

**Call relations**: `core_jobs._drive_consumer` returns this function. `JobRunner.fire` later invokes it as the handler of a page-change `JobSpec`.


##### `core_jobs._consumer_candidates`  (lines 607–611)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: This local factory creates a candidate finder for one page-change consumer. It lets each hook ask only for workspaces where that hook’s own cursor is behind.

**Data flow**: It receives a `PageChangeConsumer` and returns an async candidate function. That candidate function will call `page_change_runner.workspaces_with_changes` for the consumer.

**Call relations**: `core_jobs` uses this while constructing page-change job specs. The returned `_candidates` function is called by `JobRunner.tick`.


##### `core_jobs._consumer_candidates._candidates`  (lines 608–609)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: This generated candidate function returns workspaces with pending page changes for its captured consumer. It is the candidate source for one generated page-change job.

**Data flow**: It takes no direct inputs, asks `page_change_runner.workspaces_with_changes` about the captured consumer, and returns the workspace IDs with pending changes.

**Call relations**: `core_jobs._consumer_candidates` returns this function. `JobRunner.tick` calls it through the page-change job spec before enqueueing per-workspace runs.


##### `bindings_from`  (lines 672–703)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...], disabled: frozenset[str]=frozenset()) -> tuple[_Binding, ...]
```

**Purpose**: This function assigns stable keys and extension context information to all jobs. It combines core jobs and extension jobs into the binding table used by the runner.

**Data flow**: It receives manifests, core job specs, and an optional disabled-job set. It creates core bindings under the `core` namespace, extension bindings under each extension’s namespace, validates that disabled keys exist, filters disabled jobs out, and returns the remaining bindings.

**Call relations**: The resulting bindings are what `JobRunner` registers and executes. If a disabled job name is unknown, this function fails early rather than silently ignoring a typo.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 733–757)

```
def launch(self) -> None
```

**Purpose**: This method registers all jobs with DBOS at startup. Cron-style jobs become schedules, while one-shot jobs are immediately enqueued once with duplicate protection.

**Data flow**: It stores this runner in the module-level `_firing` variable, walks all bindings, enqueues unscheduled jobs, collects scheduled jobs, logs what it did, and applies schedules through DBOS.

**Call relations**: Server startup calls this before DBOS workflows can fire. The global `_firing` is later read by `job_tick` and `job_workflow` so those workflow entrypoints can call back into this runner.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 759–781)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: This method handles one scheduled firing of a job. It fans the job out into one durable workflow per workspace that actually has work.

**Data flow**: It receives the scheduled time and job key. If this process has no binding for the key, it warns and skips. Otherwise it asks `candidates` for workspace IDs, then enqueues `job_workflow` for each one using a deduplication ID based on job and workspace.

**Call relations**: `job_tick` calls this when DBOS fires a schedule. It calls `_registered` to tolerate old or foreign schedules, calls `candidates`, and then hands each workspace run to the jobs queue.

*Call graph*: calls 2 internal fn (_registered, candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 783–784)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: This method asks the bound job spec which workspaces have work. It is a small wrapper that first resolves the job key to a binding.

**Data flow**: It receives a job key, looks up the matching binding with `_binding`, calls that spec’s candidate function, and returns the workspace IDs.

**Call relations**: `JobRunner.tick` calls this during fan-out. If the key is not known, `_binding` raises because a runnable job cannot be found.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 786–820)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: This method runs one job for one workspace. It binds workspace scope, prepares extension tools, provisions agents if needed, and calls the job handler.

**Data flow**: It receives a job key and workspace ID. It resolves the binding, enters that workspace context, applies agent provisioning once per workspace per runner, builds an `ExtensionContext`, calls the handler, and logs detailed failure information if the handler raises.

**Call relations**: `job_workflow` calls this for each per-workspace job execution. It uses `_binding` to find the spec, `_background_registry` to choose the model for background work, and `context_for` to prepare the handler’s context.

*Call graph*: calls 2 internal fn (_binding, _background_registry); 6 external calls (__init__, failed_statement, context_for, formatted_stack, log_error, ws).


##### `JobRunner._registered`  (lines 822–823)

```
def _registered(self, key: str) -> _Binding | None
```

**Purpose**: This private helper checks whether this process knows a job key. It returns the binding if present, or `None` if the schedule belongs to another version or removed extension.

**Data flow**: It receives a key, scans the runner’s bindings, and returns the first matching binding or no value.

**Call relations**: `JobRunner.tick` uses this forgiving check so stale schedules can be skipped. `_binding` uses it as the stricter lookup step before raising on missing keys.

*Call graph*: called by 2 (_binding, tick).


##### `JobRunner._binding`  (lines 825–832)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: This private helper returns the binding for a job key or raises an error. It is used once the code expects the job to be runnable by this process.

**Data flow**: It receives a key, calls `_registered`, and either returns the binding or raises a runtime error explaining that no job is registered.

**Call relations**: `JobRunner.candidates` and `JobRunner.fire` call this because both need a real job spec. `JobRunner.tick` uses `_registered` first because scheduled ticks may legitimately refer to jobs this process no longer owns.

*Call graph*: calls 1 internal fn (_registered); called by 2 (candidates, fire).


##### `job_tick`  (lines 839–843)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: This DBOS workflow is the durable entrypoint for scheduled job ticks. It bridges DBOS schedule firing back into the active `JobRunner`.

**Data flow**: It receives the scheduled time and job key from DBOS, reads the module-level runner, raises if jobs were not launched, and then calls `runner.tick`.

**Call relations**: `JobRunner.launch` registers this workflow in schedules and one-shot enqueues. When DBOS runs it, it starts the fan-out phase rather than doing workspace work directly.


##### `job_workflow`  (lines 847–851)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: This DBOS workflow is the durable entrypoint for one job running in one workspace. It is the second stage after a tick has chosen candidate workspaces.

**Data flow**: It receives the scheduled time, job key, and workspace ID string. It reads the module-level runner, converts the workspace ID into a UUID, and calls `runner.fire`.

**Call relations**: `JobRunner.tick` enqueues this workflow for each candidate workspace. The workflow then hands execution to `JobRunner.fire`, where workspace binding and handler invocation happen.

*Call graph*: 1 external calls (UUID).


### `core/src/ufo/loop/delivery.py`

`orchestration` · `background result-delivery sweep`

When one conversation delegates work to a child turn, the normal path is simple: the child finishes, its result is sent back, and the parent is notified. But some endings happen outside that normal path. For example, another process may cancel the child, or the system may crash after recording that the child is done but before notifying the parent. Without this file, the parent could wait forever for a result that already exists.

DeliverySweep is the backstop. It looks in durable storage, meaning the database record that survives process crashes, for child turns that are terminal, meaning finished, but still marked as not delivered. It groups those children by the parent conversation they belong to, so a parent with many completed children can be woken once with all the relevant results instead of being poked over and over.

It also avoids waking the same conversation too frequently. If a conversation already received a delivered child recently, the sweep skips it until the next pass. This is like waiting a moment before ringing a doorbell again after someone just answered.

Archived parent agents are skipped rather than treated as fatal errors. That way, one retired app does not block delivery for everyone else, and if it is later restored, its pending child results can still be delivered.

#### Function details

##### `DeliverySweep.run`  (lines 54–72)

```
async def run(self) -> None
```

**Purpose**: This is the main pass of the delivery sweep. It finds finished child turns that still owe their result to a parent, skips parents that were woken very recently, and tries to deliver each remaining child result.

**Data flow**: It starts with no inputs beyond the sweep's stored helpers: a way to create a turn invoker and a subagent registry. It asks the database-facing helper for outstanding child turns, checks which parent conversations were woken within the cooldown window, builds a SubagentResult delivery helper for the current workspace, and sends each eligible child through it. The result is no returned value; instead, database-backed child results may be marked delivered and parent conversations may be woken. If a parent agent has been archived, that child is left pending and the sweep continues with the rest.

**Call relations**: This function is the coordinator for the file. It calls DeliverySweep._outstanding first to learn what work exists, then DeliverySweep._woken_since to avoid waking conversations too often. It uses the current workspace to create a SubagentResult object, then hands each child turn to that object for the actual delivery.

*Call graph*: calls 2 internal fn (_outstanding, _woken_since); 4 external calls (__init__, now, timedelta, ws_current).


##### `DeliverySweep.candidate_workspaces`  (lines 74–93)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This function tells the scheduler which workspaces have missed child results waiting to be delivered. It lets the job avoid running inside workspaces that have nothing useful to do.

**Data flow**: It reads from the owner-level database, which can see workspace-wide ownership information. It searches for child turns that are finished, still marked as pending delivery, and whose parent agent is not archived. It returns a tuple of workspace IDs where at least one such child exists.

**Call relations**: This function supports the larger scheduled-job flow. A job runner can call it before running the sweep in individual workspaces, so DeliverySweep.run is only invoked where there is likely pending delivery work.

*Call graph*: 2 external calls (select, owner_tx).


##### `DeliverySweep._outstanding`  (lines 95–131)

```
async def _outstanding(self) -> dict[UUID, list[Turn]]
```

**Purpose**: This function finds the actual child turns in the current workspace that are finished but whose results have not yet been handed back. It groups them under the parent conversation that needs to be woken.

**Data flow**: It reads the workspace database and joins each child turn to its parent turn and parent agent. It keeps only children that are terminal, still marked as pending delivery, and connected to a non-archived agent. It orders them by parent conversation and by the time each child was updated, limits the batch size, converts database rows into Turn objects, and returns a dictionary from parent conversation ID to a list of child turns.

**Call relations**: DeliverySweep.run calls this at the start of a pass to get the work queue. The grouped shape is important because run can then treat a whole parent conversation's outstanding children together instead of handling each child as an unrelated item.

*Call graph*: called by 1 (run); 3 external calls (model_validate, select, workspace_tx).


##### `DeliverySweep._woken_since`  (lines 133–160)

```
async def _woken_since(self, cutoff: datetime, conversations: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: This function checks which parent conversations were already woken recently by a delivered child result. It prevents the sweep from repeatedly waking the same conversation in a tight loop.

**Data flow**: It receives a cutoff time and a set of conversation IDs to check. It reads the workspace database for child turns already marked delivered after that cutoff, joins them back to their parent turns, and returns the matching parent conversation IDs as a frozenset. It does not change the database.

**Call relations**: DeliverySweep.run calls this after finding outstanding work. Run then uses the returned set as a temporary do-not-disturb list: conversations in the set are skipped for this sweep pass, leaving their still-pending children for a later tick.

*Call graph*: called by 1 (run); 2 external calls (select, workspace_tx).


### `core/src/ufo/runtime/media/preview_renderer.py`

`orchestration` · `background scheduled preview retry job`

When a user shares a document, the system tries to create a small preview image right away. That first attempt is only “best effort”: if the preview service is briefly down, the shared file is still saved, but its preview fields stay empty. This file exists to clean up those missed previews later.

The main class, PreviewRenderer, works in small batches. It looks for shared artifacts that have no preview yet, were created recently, and have a filename type the preview service can understand. The recent-time rule matters: if a file is corrupt or impossible to render, retrying forever would waste work. After an hour, such files simply age out of retry.

For each candidate, the renderer creates two temporary signed web links: one lets the preview service download the original file, and the other lets it upload the generated PNG preview. These are “presigned” links, meaning they grant short-lived permission without sending file bytes through this core service. Like giving a courier two timed access codes, core never carries the package itself.

If the preview service succeeds, this file updates the shared artifact row with the preview’s storage key, media type, and size. If the service is unreachable or refuses the file, it logs the problem and leaves the row untouched so the next scheduled run can try again.

#### Function details

##### `_eligible`  (lines 44–45)

```
def _eligible(filename_column: sa.Column) -> sa.ColumnElement[bool]
```

**Purpose**: Checks whether a filename column matches one of the file endings that can have previews, such as supported document suffixes. It is used to avoid asking the preview service to render file types it is not meant to handle.

**Data flow**: It receives a database column that contains filenames. It builds a database condition that says “the filename ends with any supported preview suffix,” using case-insensitive matching. The result is not a yes-or-no value for one file, but a filter that can be placed inside a database query.

**Call relations**: PreviewRenderer.run uses this filter when choosing which missing previews to render in the current workspace. PreviewRenderer.candidate_workspaces uses the same filter when looking across workspaces for places where the retry job has work to do.

*Call graph*: called by 2 (candidate_workspaces, run); 2 external calls (ilike, or_).


##### `PreviewRenderer.run`  (lines 59–81)

```
async def run(self) -> None
```

**Purpose**: Runs one batch of preview retries for the current workspace. It finds recent shared files with missing previews and sends each one to the preview-rendering step.

**Data flow**: It starts by calculating the retry cutoff time, one hour before now. It opens a workspace-scoped database transaction and reads up to a small batch of shared artifacts whose preview is missing, whose creation time is still inside the retry window, and whose filename is eligible. If there are no rows, it stops. Otherwise, it opens an HTTP client and passes each file’s blob key and filename to _render_one.

**Call relations**: This is the main body the scheduled job calls after choosing a workspace. It relies on _eligible to build the database filter, uses workspace_tx so reads happen inside the current workspace’s data boundary, and hands each selected row to PreviewRenderer._render_one to do the actual service call and database update.

*Call graph*: calls 2 internal fn (_render_one, _eligible); 4 external calls (now, AsyncClient, select, workspace_tx).


##### `PreviewRenderer._render_one`  (lines 83–119)

```
async def _render_one(self, client: httpx.AsyncClient, blob_key: str, filename: str) -> None
```

**Purpose**: Attempts to render a preview for one shared file. It prepares temporary access links, asks the preview service to create a PNG, and records the preview if the service succeeds.

**Data flow**: It receives an HTTP client, the stored file’s blob key, and the original filename. From the filename it chooses the file kind and builds a new storage key for the PNG preview. It asks the blob store for a short-lived download link for the source file and a short-lived upload link for the preview. It sends those links, along with size limits, to the preview service. If the service cannot be reached or returns an error status, it logs the failure and changes nothing. If the service returns success, it reads the reported preview size and updates the matching shared artifact row, but only if the preview is still missing.

**Call relations**: PreviewRenderer.run calls this once for each row in its retry batch. This function talks outward to the preview service through the provided HTTP client, talks to blob storage through presigned links, logs recoverable failures, and finally uses workspace_tx to write the preview details back into the workspace database.

*Call graph*: called by 1 (run); 7 external calls (post, dumps, PurePosixPath, update, workspace_tx, log, uuid4).


##### `PreviewRenderer.candidate_workspaces`  (lines 121–135)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces currently have shared files that may need preview retries. This lets the scheduler avoid running the renderer in workspaces with no relevant work.

**Data flow**: It calculates the same one-hour retry cutoff used by run. It opens an owner-level database transaction, which can see workspace identifiers, and selects distinct workspace IDs from shared artifacts whose previews are missing, whose rows are recent enough, and whose filenames are eligible. It returns those workspace IDs as a tuple.

**Call relations**: A higher-level job scheduler can call this before running workspace-specific preview work. It uses _eligible so it looks for the same kinds of files that PreviewRenderer.run will later process, and it uses owner_tx because choosing workspaces happens above any single workspace’s transaction.

*Call graph*: calls 1 internal fn (_eligible); 3 external calls (now, select, owner_tx).


### `core/src/ufo/product.py`

`domain_logic` · `scheduled metrics tick`

This file answers a business question: “How far has each workspace gotten in using the product, and what has it connected?” A workspace is counted as reaching stages such as having an invited member, a connected service, an app, recent activity, or a paid purchase. The code derives those stages from ordinary database rows instead of relying on events that might be missed or become outdated. That means if the team changes the definition of a stage later, the next census can recalculate it from history.

The main job runs for the workspace that is currently bound to the running task. It explicitly includes that workspace’s ID in every database query, so it does not accidentally count another workspace’s data. It asks the database two questions: first, which funnel stages are true for this workspace; second, which things are attached to it, such as installed surfaces, proved addresses, credentials, connectors, and apps.

After reading the answers, it emits metrics. Each true stage becomes one `product_stage_total` metric, and each attached item becomes one `product_attach_total` metric with a kind and name. In everyday terms, this file is a periodic clipboard checklist: once every tick, it looks at one workspace, marks what milestones it has reached, lists what tools it has attached, and sends those marks to the monitoring system.

#### Function details

##### `product_census`  (lines 49–149)

```
async def product_census() -> None
```

**Purpose**: Counts the current workspace’s product funnel stages and attached product surfaces, then sends those facts as metrics. It is used when the scheduled product census runs, so dashboards can show adoption and usage without storing separate funnel events.

**Data flow**: It starts with the currently bound workspace ID and the current time. It builds database questions that check whether rows exist for milestones like seated members, connector grants, invited members, member chats, recent activity, and paid purchases. It also builds one combined database question that lists attached items, such as installed surfaces, proved addresses, credential slots, connector providers, and provisioned app names. It opens a workspace database transaction, reads those results, then emits one metric for every stage that is true and one metric for every attached item it found.

**Call relations**: This function is the census worker for the file. During a scheduled run, it calls the workspace context to learn which workspace is being counted, uses SQLAlchemy helpers to build safe database queries, opens a workspace transaction through `ufo.db.workspace_tx`, and finally hands the finished counts to the observability layer through metric emission. No other local helper functions sit between these steps, so this function contains the full flow from database facts to product metrics.

*Call graph*: 10 external calls (now, timedelta, and_, exists, literal, select, union_all, workspace_tx, emit_metric, ws_current).


### Scheduled tasks and pauses
These files wake delayed workflows, fire recurring scheduled prompts, and maintain the durable schedule and pause records behind them.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pause_runner.py`

`orchestration` · `scheduled background tick`

A pause is like setting an alarm clock inside a conversation: if nobody replies before the alarm time, the system should continue the workflow automatically. This file is the alarm clock runner. It is meant to run repeatedly on a schedule. On each tick, it asks the pause storage for pauses that are due now and temporarily claims them with a lease, which is a short ownership period that stops overlapping runners from doing the same work twice.

For each claimed pause, it tries to “fire” it by invoking the saved prompt as a scheduled turn in the original conversation. It does this on behalf of the member who created the pause, so the resumed workflow keeps the right identity and context. It also passes two safety checks called watermarks: recorded positions in the conversation that let the system ask, “Has a member spoken since this pause began?” If yes, the member’s message already ended the wait, so the timer should not add another turn.

After the invoke step, the pause is retired, meaning removed from future firing. The order matters: it invokes first and retires second. If the process crashes between those steps, the same idempotency key lets a retry recognize the already-admitted turn instead of creating a duplicate. If the app or agent is archived, no turn can be admitted, so the pause is left in place for a future restore.

#### Function details

##### `PauseRunner.run`  (lines 33–42)

```
async def run(self) -> None
```

**Purpose**: This is the main tick of the pause runner. It finds pauses whose time has arrived, tries to fire each one, and reports any failures together at the end instead of stopping at the first problem.

**Data flow**: It starts with the runner’s extension context and the current time. It creates a pause store, asks that store to claim due pauses for a limited lease period, then sends each claimed pause to the private firing step. If some pauses fail, it collects their conversation identifiers and error types, and finally raises one combined error; if none fail, it finishes quietly.

**Call relations**: This method is the outer loop for the file. It creates the storage helper, uses the clock to decide what is due, and calls PauseRunner._fire for each claimed pause. In practice, the scheduler calls this method repeatedly, and this method delegates the careful per-pause work to _fire.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `PauseRunner._fire`  (lines 44–60)

```
async def _fire(self, store: PauseStore, row: Pause) -> None
```

**Purpose**: This function tries to complete one waiting pause. It checks that the pause’s conversation conditions still hold, invokes the saved scheduled turn if allowed, and retires the pause once the wait is settled.

**Data flow**: It receives a pause store and one pause row. First it asks the store whether the recorded “hold” conditions are still valid; if not, it stops because the pause should not fire. If valid, it invokes the stored prompt in the original conversation using a stable firing key, the member identity that armed the pause, and the recorded watermarks that prevent firing after a member has already spoken. If the agent is archived, it leaves the pause untouched. Otherwise, after the invoke succeeds or is safely recognized as already done, it retires the pause in storage.

**Call relations**: PauseRunner.run calls this once for every due pause it has claimed. Inside, _fire relies on PauseStore.claim_holds before invoking so that member activity and conversation state get the final say. After the invoke, it hands the row back to PauseStore.retire so the same completed wait is not offered again on later scheduler ticks.

*Call graph*: calls 2 internal fn (claim_holds, retire); called by 1 (run).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `recurring scheduled-task tick`

This file is the clock-driven worker for scheduled tasks. Think of it like a mail carrier with a checklist: on each round, it picks up only the tasks that are due, marks them so another carrier does not deliver the same one, and then delivers each task to the right conversation.

The main class, ScheduledTaskRunner, is called periodically by the extension. It asks the schedule store for tasks due now and gives each task a short lease. A lease is a temporary claim that prevents overlapping runner ticks from firing the same scheduled task twice.

Before firing a task, the runner checks whether it has expired. If it has, the task is retired instead of invoked. If it is still valid, the runner calculates the next cron time. A cron schedule is a standard repeating time pattern, such as “every Monday at 9.” The runner then builds the message body that the agent will see, including a stable idempotency key. An idempotency key is a “same delivery” label, so retries or deploys do not create duplicate turns.

If the app is archived, the runner does not count that as failure; the task stays in place and can run later when restored. If invocation succeeds, the task is rescheduled to its next occurrence. If firing fails for another reason, the runner reports the failed task names.

#### Function details

##### `fire_body`  (lines 42–60)

```
def fire_body(task: ScheduledTask, runtime_instruction: str | None) -> tuple[str, str]
```

**Purpose**: This function prepares the exact message that will be delivered for one scheduled task, plus the stable key used to recognize that scheduled fire. It exists so every scheduled run is described consistently and duplicate delivery can be avoided.

**Data flow**: It receives a scheduled task and an optional instruction for this particular run. It reads the task’s next run time, prompt, and id, formats the run time for the message the agent will read, adds the task prompt and optional instruction, and asks scheduled_fire_key to make the matching deduplication key. It returns two things: the inbound message text and the key used when admitting the turn.

**Call relations**: ScheduledTaskRunner._fire calls this after it has confirmed the task is still claimed and ready to run. fire_body hands back the message and key that _fire then passes into the extension context invocation, so the scheduled task becomes an admitted conversation turn in a repeat-safe way.

*Call graph*: called by 1 (_fire); 1 external calls (scheduled_fire_key).


##### `ScheduledTaskRunner.run`  (lines 68–77)

```
async def run(self) -> None
```

**Purpose**: This is the top-level action for one runner tick. It finds tasks due right now, attempts to fire each one, and raises a combined error if any task failed in a way that should be noticed.

**Data flow**: It starts with the runner’s extension context and lease length. It creates a ScheduleStore for reading and updating scheduled-task rows, records the current time, and asks the store to claim tasks that are due. For each claimed task, it calls _fire and collects any failure message. If no failures are returned, it finishes quietly; if there are failures, it raises an error naming them.

**Call relations**: This method is the entry point used by the extension’s recurring job. It sets up the schedule store and time window, then delegates the careful per-task work to ScheduledTaskRunner._fire. The result of each _fire call decides whether the tick completes cleanly or reports failed fires.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `ScheduledTaskRunner._fire`  (lines 79–116)

```
async def _fire(self, store: ScheduleStore, task: ScheduledTask, tick_at: datetime, expiry_checked_at: datetime) -> str | None
```

**Purpose**: This function performs the safe, one-task firing process. It checks expiry, confirms the lease is still valid, invokes the scheduled task in its conversation, and reschedules it only after the invocation is accepted.

**Data flow**: It receives the schedule store, one claimed task, the tick time, and the time to use for expiry checking. First it asks the store to retire the task if it has expired. If not expired, it calculates the next cron fire time. It chooses a normal reporting instruction, or a final-run instruction if the next fire would land beyond the task’s expiry. It then confirms the claim still holds, builds the inbound message and idempotency key with fire_body, and invokes the task through the extension context. If the agent’s app is archived or no turn is admitted, it leaves the task as-is. If invocation succeeds, it tells the store to reschedule the task to the next fire time. If an unexpected exception happens, it returns a short failure label; otherwise it returns nothing.

**Call relations**: ScheduledTaskRunner.run calls this once for every due task it claimed. Inside, _fire coordinates the store methods that protect task state, next_fire for calculating the next cron occurrence, and fire_body for composing the scheduled turn. It then hands the prepared turn to the extension context, and, after acceptance, hands the new schedule position back to the store.

*Call graph*: calls 4 internal fn (fire_body, claim_holds, reschedule, retire_if_expired); called by 1 (run); 1 external calls (next_fire).


### `core/src/ufo/runtime/ext/scheduled_fire.py`

`domain_logic` · `scheduled task admission and run lookup`

Scheduled tasks need a durable way to say, “this exact task firing at this exact time has already been admitted.” This file provides that small but important contract. The key is a string made from two pieces: the task’s unique ID and the scheduled time, separated by a colon. Think of it like a claim ticket: the ticket must be printed the same way every time, or the coat-check desk cannot recognize it later.

The builder, `scheduled_fire_key`, creates this ticket from a task ID and a date-time. It deliberately uses Python’s normal `isoformat()` date text, including details like `+00:00` for UTC time, because old keys may already exist in storage. Changing the spelling would break duplicate detection for tasks admitted before the change.

The parser, `scheduled_fire_task_id`, reads a key back and tries to recover the task ID from the part before the colon. If the key is not in this scheduled-task format, it returns `None`. That matters because not every run key belongs to a scheduled task; for example, another kind of timer resume may use a different key shape.

#### Function details

##### `scheduled_fire_key`  (lines 14–16)

```
def scheduled_fire_key(task_id: UUID, fire_at: datetime) -> str
```

**Purpose**: This function creates the stable key for one scheduled occurrence of one task. It is used when the system needs a repeat-safe label so the same scheduled firing is not admitted more than once.

**Data flow**: It receives a task ID and the time that task is supposed to fire. It turns the time into standard ISO date text, joins the task ID and time with a colon, and returns the resulting string. It does not change anything outside itself.

**Call relations**: When the scheduled-task runner admits a fire, it calls this builder to produce the exact key used for deduplication. Inside, it relies on `datetime.datetime.isoformat` to spell the time in the agreed durable form.

*Call graph*: 1 external calls (isoformat).


##### `scheduled_fire_task_id`  (lines 19–25)

```
def scheduled_fire_task_id(key: str) -> UUID | None
```

**Purpose**: This function tries to find the task ID named by a scheduled-fire key. It is useful when another part of the system sees a run key and wants to know whether it came from a scheduled task.

**Data flow**: It receives a key string, takes the text before the first colon, and tries to read that text as a UUID, which is a standard unique identifier. If that succeeds, it returns the UUID. If the text is not a valid UUID, it returns `None`, meaning this key is not recognized as a scheduled-fire key.

**Call relations**: The portal’s runs feed can use this parser to connect a run back to the scheduled task that admitted it. The function hands the first part of the key to `uuid.UUID` to validate and convert it; if that conversion fails, it treats the key as belonging to some other mechanism.

*Call graph*: 1 external calls (UUID).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `schedule creation and task rescheduling`

Scheduled tasks need a way to say “run every day at 9” or “run every five minutes.” This file supports that using cron expressions, which are compact time patterns made of five fields: minute, hour, day of month, month, and day of week. The rest of the task system stores only the next exact run time, so it does not need to understand cron rules itself. This file keeps that special cron knowledge inside the scheduled-tasks extension.

There are two small but important jobs here. First, `validate_cron` rejects schedules that are not five-field cron expressions or that the cron parser says are invalid. This prevents bad schedules from being saved and later confusing the runner. Second, `next_fire` asks the `croniter` library to calculate the next datetime after a given moment.

A key detail is that the next time is strictly after the supplied `after` time. This matters when a runner is late or has been stopped. Instead of trying to replay every missed run, the system can move forward to one next catch-up time. In everyday terms, it behaves more like setting the next alarm than reading every missed chime from a clock.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: This function checks whether a schedule string is a valid five-part cron expression. It is used to catch mistakes early, before a bad schedule can be stored or used by the task runner.

**Data flow**: It receives a text schedule. First it splits the text into fields and makes sure there are exactly five pieces. Then it asks the cron parsing library whether the expression is valid. If anything is wrong, it raises a `ValueError` with a clear message; if everything is fine, it returns the original schedule unchanged.

**Call relations**: When some higher-level scheduled-task code needs to accept or save a cron schedule, it can call this function as the gatekeeper. Inside, this function hands the detailed syntax check to `croniter.croniter.is_valid`, because that library knows the cron rules.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: This function calculates the next time a cron-based task should run after a given moment. It turns a repeating schedule pattern into one concrete datetime the rest of the system can store and compare.

**Data flow**: It receives a cron schedule and an `after` datetime. It gives both to the cron library, which walks forward through the schedule and finds the next matching datetime. The function returns that datetime and does not change anything else.

**Call relations**: After a task has run, or when the system needs to decide its next run time, scheduled-task code can call this function. It delegates the actual calendar math to `croniter.croniter`, then returns the next fire time for storage or scheduling.

*Call graph*: 1 external calls (croniter).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pauses.py`

`domain_logic` · `scheduled task polling and pause firing`

A “pause” is a workflow that has said, in effect, “come back to me at this time.” This file defines the database table for those pauses and the small store object that reads and writes it. Without it, scheduled tasks could be lost, fired twice, or fired after they had already been replaced by a newer wait.

The table keeps one pause per conversation. If the same conversation schedules another wait, the old one is overwritten because a workflow can only be waiting for one scheduled thing at a time. Each pause stores the conversation, the agent to re-enter, the wake-up time, the prompt to send back into the workflow, and two sequence numbers. Those sequence numbers are like two different counters on a turnstile: one counts conversation turns, the other counts message arrivals. Keeping both lets the later firing code decide whether a person spoke after the pause was armed.

The file also protects against multiple background workers doing the same work. A worker must “claim” due pauses for a short lease before firing them. That lease is like putting a temporary sticky note on a row saying, “I’m working on this.” If the worker crashes, the lease expires and another worker can try later. Before firing or deleting a pause, the code checks that the same claim still owns it, so a newer re-armed pause is not accidentally resumed or removed.

#### Function details

##### `_aware`  (lines 76–77)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: This helper makes sure a datetime has a timezone. If the database gives back a plain time with no timezone attached, it treats it as UTC so the rest of the code can compare times safely.

**Data flow**: It receives a datetime value. If the value already says what timezone it belongs to, it returns it unchanged; otherwise it adds UTC as the timezone. The output is always safe for timezone-aware time comparisons.

**Call relations**: It is used by _row when turning database rows into Pause objects. That means every pause read from storage leaves this file with consistent time values, instead of making each caller remember to fix them.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 80–95)

```
def _row(row: sa.RowMapping) -> Pause
```

**Purpose**: This helper turns a raw database row into a Pause object that the rest of the code can use. It also normalizes all stored times so callers do not need to know database-specific quirks.

**Data flow**: It receives a row mapping from a database query. It pulls out the pause fields, runs the datetime fields through _aware, and builds a Pause value. The result is a clean in-memory description of one armed pause.

**Call relations**: PauseStore.arm, PauseStore.armed, and PauseStore.claim_due all call this after database reads. It is the single doorway from database-shaped data into application-shaped pause data.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 98–99)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition that says whether a pause can be claimed by a worker. A pause is available if nobody has claimed it, or if the previous claim has expired.

**Data flow**: It receives the current time. It creates a SQL condition comparing that time with the pause row’s claim fields. The output is not a true-or-false value yet; it is a database expression used inside later queries.

**Call relations**: PauseStore.claim_due uses it when actually claiming rows, and due_pause_workspaces.due uses it when deciding which workspaces have runnable pauses. Keeping this rule in one helper makes those two checks match.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_pause_workspaces`  (lines 102–118)

```
def due_pause_workspaces() -> WorkspaceCandidates
```

**Purpose**: This tells the job system how to find workspaces that currently have pauses ready to run. It is a bridge between the pause table and the background scheduler.

**Data flow**: It defines a query-producing helper that looks for workspaces with due, claimable pause rows. It passes that helper to owner_candidates, which wraps it in the project’s workspace candidate mechanism. The result is an object the job system can ask for runnable workspaces.

**Call relations**: The scheduled pause runner uses this candidate source indirectly through the job system. Inside it, the nested due query applies the same claim-availability rule used by PauseStore.claim_due, so the runner is not woken for work that is still leased by someone else.

*Call graph*: 1 external calls (owner_candidates).


##### `due_pause_workspaces.due`  (lines 107–116)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This nested helper builds the actual database query for workspaces that have at least one due pause. It only includes pauses whose timer has passed and whose claim is free or expired.

**Data flow**: It reads the current UTC time, builds a claim-availability condition with _claim_available, and creates a SQL query selecting distinct workspace IDs. The output is a query object, not the final rows; the job candidate system runs it later.

**Call relations**: due_pause_workspaces hands this helper to owner_candidates. That lets the broader scheduler ask, “Which workspace owners might have pause work now?” without embedding pause-table details in the scheduler itself.

*Call graph*: calls 1 internal fn (_claim_available); 2 external calls (now, select).


##### `PauseStore.arm`  (lines 127–183)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, resume_at: datetime, origin_seq: int, origin_arrival_seq: int, prompt: str, created_by_member_id: UUID | None) -> Pause
```

**Purpose**: This creates or replaces the scheduled pause for one conversation. It is used when a workflow says it should be resumed later.

**Data flow**: It receives the conversation, agent, wake-up time, sequence counters, prompt, and optional member who created the pause. It opens the extension’s database transaction and performs an upsert, meaning it inserts a new row or updates the existing row for that conversation. It always gives the pause a fresh ID and clears any old claim, then returns the saved row as a Pause object.

**Call relations**: This is the write path for arming a wait. It calls _row to convert the returned database row into a Pause. Its fresh-ID behavior is important for the pause runner: an old worker claim must not be able to fire or delete a newly re-armed wait that happens to occupy the same conversation slot.

*Call graph*: calls 1 internal fn (_row); 1 external calls (uuid4).


##### `PauseStore.armed`  (lines 185–191)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Pause, ...]
```

**Purpose**: This reads the currently armed pauses for the workspace. It can return all pauses or just the pause for one conversation.

**Data flow**: It optionally receives a conversation ID. It builds a database query scoped to the current workspace, adds the conversation filter if provided, orders pauses by wake-up time, and reads the rows. Each row is converted through _row, and the result is a tuple of Pause objects.

**Call relations**: This is the simple read path for code that needs to inspect what is currently scheduled. It does not claim or modify anything; it only presents stored pauses in a consistent in-memory form.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `PauseStore.claim_due`  (lines 193–234)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_PAUSES) -> tuple[Pause, ...]
```

**Purpose**: This leases a batch of due pauses so one worker can try to fire them without racing other workers. It prevents two background ticks from resuming the same pause at the same time.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of rows to claim. It creates a unique claim ID, selects the oldest due and claimable pauses in the current workspace, and updates those rows with the claim ID and an expiration time. It returns the claimed rows as Pause objects.

**Call relations**: The pause runner calls this when looking for work to fire. It uses _claim_available so its idea of “claimable” matches due_pause_workspaces.due, and it uses _row to hand back normal Pause objects. The database update and return happen as one atomic step, so competing workers split the work instead of duplicating it.

*Call graph*: calls 2 internal fn (_claim_available, _row); 4 external calls (timedelta, select, update, uuid4).


##### `PauseStore.claim_holds`  (lines 236–261)

```
async def claim_holds(self, row: Pause) -> bool
```

**Purpose**: This checks that a worker still owns the pause it is about to fire. It is a last safety check before sending the resume action into the workflow.

**Data flow**: It receives a Pause that should already have a claim ID. If there is no claim ID, it raises an error because an unclaimed pause must not be fired. Otherwise it queries the database for the same pause ID, workspace, and claim ID, locking the row while it checks. It returns true if the claim still matches, false if the row disappeared or was replaced.

**Call relations**: PauseRunner._fire calls this immediately before firing a pause. This matters because PauseStore.arm can replace a pause and clear the old claim while a worker is still running. If the claim no longer holds, the runner should not fire stale instructions.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `PauseStore.retire`  (lines 263–276)

```
async def retire(self, row: Pause) -> None
```

**Purpose**: This deletes a pause after the worker has finished with the claimed wait. It removes only the row still owned by that exact claim.

**Data flow**: It receives a claimed Pause. If the pause has no claim ID, it raises an error because unclaimed rows must not be retired this way. Otherwise it deletes the row matching the pause ID, workspace, and claim ID. It returns nothing; the database is changed only if the claim still matches.

**Call relations**: PauseRunner._fire calls this after a pause has either fired or been judged no longer needed. The claim check protects newer re-armed pauses: if another action replaced the row, this delete will not remove the replacement.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/schedules.py`

`domain_logic` · `request handling and background scheduled-task sweeps`

A scheduled task is like a calendar reminder for an agent: at a certain time, the system should send a saved prompt into a saved conversation. This file owns the database table for those reminders and provides `ScheduleStore`, the safe doorway used by the rest of the extension.

The store keeps each task tied to one workspace, one agent, and one reporting conversation. That matters because the database connection is not automatically scoped; every query must explicitly say which workspace it belongs to, or one workspace could accidentally see or change another workspace’s tasks. Member-facing operations also stay inside the current agent’s object namespace.

The file supports two main kinds of users. Member-facing code can create, edit, cancel, list, and inspect tasks. The background runner can find due tasks, lease them so two runners do not fire the same task at once, verify the lease still holds just before firing, retire expired tasks, and reschedule tasks after they run.

The lease is the key safety idea. It works like putting a temporary “reserved” tag on a task. While one runner has that tag, another runner should skip it. If the lease expires, another runner can pick it up. Edits and cancellations clear or remove the old version, so the runner checks again before firing.

#### Function details

##### `_utc`  (lines 124–125)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: Makes sure a date-and-time value is marked as UTC time. This avoids confusion between timezone-aware times and plain times returned by some databases.

**Data flow**: It receives a `datetime`. If it already has timezone information, it leaves it alone. If it has no timezone, it returns the same clock reading with UTC attached.

**Call relations**: Rows read from the database pass through `_task` or `inspect_many`, and those readers call `_utc` so the rest of the system receives consistent UTC times. `_utc_opt` also uses it for fields that may be missing.

*Call graph*: called by 3 (inspect_many, _task, _utc_opt); 1 external calls (replace).


##### `_utc_opt`  (lines 128–129)

```
def _utc_opt(value: datetime | None) -> datetime | None
```

**Purpose**: Does the same UTC cleanup as `_utc`, but for optional times that may be `None`. It prevents every caller from needing to repeat the same null check.

**Data flow**: It receives either a `datetime` or `None`. `None` stays `None`; a real time is passed to `_utc` and comes back as a UTC-aware time.

**Call relations**: The row builder `_task` and the status reader `ScheduleStore.inspect_many` use this for fields such as `last_run_at` and `expires_at`, where a task may not have run or may not expire.

*Call graph*: calls 1 internal fn (_utc); called by 2 (inspect_many, _task).


##### `_claim_available`  (lines 132–136)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for deciding whether a task can be claimed by a runner. A task is available if nobody has claimed it, or if its old claim has timed out.

**Data flow**: It receives the current time. It produces a SQL condition that matches rows with no claimant or with a claim expiry before that time.

**Call relations**: Both the workspace finder and `ScheduleStore.claim_due` use the same condition, so they agree about which tasks are worth opening and which are still safely reserved by another runner.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_expired`  (lines 139–143)

```
def _expired(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for deciding whether a task has passed its expiry time. Expired tasks are removed instead of fired.

**Data flow**: It receives the current time. It produces a SQL condition that matches rows whose `expires_at` value exists and is at or before that time.

**Call relations**: The candidate workspace query uses it to wake the runner for work that is only cleanup. `ScheduleStore.claim_due` uses it to delete expired tasks before claiming runnable ones.

*Call graph*: called by 2 (claim_due, due); 1 external calls (and_).


##### `_task`  (lines 146–165)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: Turns a raw database row into a `ScheduledTask` object that application code can use. It also standardizes all time fields to UTC.

**Data flow**: It receives a row from the `scheduled_task` table. It copies IDs, names, schedule text, prompt text, status flags, claim information, and timestamps into a `ScheduledTask`, converting timestamp fields through `_utc` and `_utc_opt`.

**Call relations**: All main reads that return scheduled tasks funnel through this builder: creation, update, listing, and due-task claiming. That keeps the rest of the code from depending on database row details.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 4 (claim_due, create, list, update); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 168–191)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: Provides the background job system with a way to find workspaces that might have scheduled-task work to do. It looks for workspaces with tasks that are due to run or expired tasks ready to be cleaned up.

**Data flow**: It creates a nested query function and gives it to `owner_candidates`. The result is a `WorkspaceCandidates` object the job system can use to decide which workspace owners should be polled.

**Call relations**: The scheduled-task runner relies on this as its entry point for discovering candidate workspaces. The nested `due` query does the actual database selection.

*Call graph*: 1 external calls (owner_candidates).


##### `due_task_workspaces.due`  (lines 174–189)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query that finds workspaces containing claimable due or expired tasks. It skips tasks that are already under a live lease.

**Data flow**: It reads the current UTC time, then builds a SQL query selecting distinct workspace IDs. A workspace qualifies if it has a claim-available task that is either expired or unpaused and due to run.

**Call relations**: This query is handed to `owner_candidates` by `due_task_workspaces`. It uses `_claim_available` and `_expired` so workspace discovery follows the same rules as later claiming.

*Call graph*: calls 2 internal fn (_claim_available, _expired); 5 external calls (now, and_, not_, or_, select).


##### `ScheduleStore.workspace_id`  (lines 205–206)

```
def workspace_id(self) -> UUID
```

**Purpose**: Gives quick access to the workspace ID from the extension context. Every database statement needs this value to stay inside the current workspace.

**Data flow**: It reads `workspace_id` from `self.ctx` and returns it unchanged.

**Call relations**: Most methods in `ScheduleStore` use this property while building SQL filters or inserted rows. It keeps the workspace boundary close at hand.


##### `ScheduleStore.create`  (lines 208–267)

```
async def create(self, conversation_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None, expires_at: datetime | None=None,
```

**Purpose**: Creates a new scheduled task for the current agent and a specific conversation. It refuses to create the task if the conversation is not bound to the same agent that will execute it.

**Data flow**: It receives the conversation, task name, schedule text, prompt, description, next run time, optional creator, optional expiry, and paused flag. It checks the current object agent against the conversation’s agent, inserts a new row with a new UUID, and returns the saved task. If a task with the same workspace, agent, and name already exists, it raises an error.

**Call relations**: Member-facing setup code calls this when a user or tool creates a recurring task. It uses `_task` to turn the inserted row into the returned object and uses `object_agent_id` to bind the task to the current agent namespace.

*Call graph*: calls 1 internal fn (_task); 2 external calls (object_agent_id, uuid4).


##### `ScheduleStore.update`  (lines 269–323)

```
async def update(self, expected: ScheduledTask, schedule: str, prompt: str, description: str, next_run_at: datetime, expires_at: datetime | None=None, *, paused: bool) -> ScheduledTask
```

**Purpose**: Edits an existing scheduled task’s schedule, prompt, description, next run time, expiry, and paused state without changing its identity or run history. It protects against overwriting a task that changed since it was read.

**Data flow**: It receives the previously read `ScheduledTask` plus new editable values. It confirms the task still belongs to the current agent, updates only the exact matching row, clears any active claim, and returns the refreshed task. If no exact row matches, it raises an error saying the task changed while editing.

**Call relations**: Member-facing edit flows call this after reading a task. It uses `_creator_matches` to distinguish creator-owned and creatorless tasks, and `_task` to return a clean `ScheduledTask`.

*Call graph*: calls 2 internal fn (_creator_matches, _task); 2 external calls (update, object_agent_id).


##### `ScheduleStore.cancel`  (lines 325–341)

```
async def cancel(self, expected: ScheduledTask) -> None
```

**Purpose**: Deletes a scheduled task, but only if it still matches the exact task the caller expected. This prevents a stale view from deleting a newer or different task by accident.

**Data flow**: It receives an expected `ScheduledTask`. It checks that the task’s agent is still the current object agent, then deletes the row matching the workspace, ID, agent, conversation, name, and creator. If nothing was deleted, it raises an error.

**Call relations**: Member-facing cancellation code calls this. It shares the exact creator check with `ScheduleStore.update` through `_creator_matches`.

*Call graph*: calls 1 internal fn (_creator_matches); 2 external calls (delete, object_agent_id).


##### `ScheduleStore._creator_matches`  (lines 343–348)

```
def _creator_matches(self, expected: ScheduledTask) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says whether the stored creator matches the expected task creator. It is careful with tasks that have no creator recorded.

**Data flow**: It receives the expected task. If the expected creator is missing, it returns a condition requiring the database value to be NULL. Otherwise, it returns a condition requiring the creator ID to equal the expected creator ID.

**Call relations**: `ScheduleStore.update` and `ScheduleStore.cancel` use this as part of their exact-match safety checks. This stops an operation authorized for one creator’s task from landing on a creatorless task.

*Call graph*: called by 2 (cancel, update).


##### `ScheduleStore._listing`  (lines 350–371)

```
def _listing(self, selected: tuple[sa.ColumnElement[Any], ...], *, conversation_id: UUID | None, names: tuple[str, ...] | None, visible_to_member_id: UUID | None, include_all_owners: bool, limit: int
```

**Purpose**: Builds the shared database query used to list scheduled tasks. It applies workspace, agent, conversation, name, owner, sorting, and limit filters in one place.

**Data flow**: It receives the columns to select and optional filters. It starts with tasks in the current workspace and current object agent, then narrows by conversation, names, visible owner, and limit when requested. It returns a SQL query, not the actual rows.

**Call relations**: `ScheduleStore.list` calls this before executing the query. Keeping the query-building here means plain listing and reported listing use the same filtering rules.

*Call graph*: called by 1 (list); 2 external calls (select, object_agent_id).


##### `ScheduleStore.list`  (lines 373–392)

```
async def list(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -> tuple[S
```

**Purpose**: Reads scheduled tasks visible under the requested filters. This is the basic task listing operation for member-facing code.

**Data flow**: It receives optional filters such as conversation ID, task names, member owner, whether to include all owners, and a limit. It asks `_listing` for the SQL query, runs it in a transaction, converts each row through `_task`, and returns a tuple of `ScheduledTask` objects.

**Call relations**: User-facing pages or tools can call this directly when they only need task definitions. `ScheduleStore.list_reported` calls it first, then adds conversation visibility facts.

*Call graph*: calls 2 internal fn (_listing, _task); called by 1 (list_reported).


##### `ScheduleStore.list_reported`  (lines 394–428)

```
async def list_reported(self, *, conversation_id: UUID | None=None, names: tuple[str, ...] | None=None, visible_to_member_id: UUID | None=None, include_all_owners: bool=True, limit: int | None=None) -
```

**Purpose**: Lists tasks together with information about the conversations they report into, such as audience and display label. This helps member-facing surfaces decide what a user may see and how to label it.

**Data flow**: It receives the same filters as `list`. It first gets matching tasks, then asks the context for facts about their conversations. It returns `ListedTask` objects for tasks whose conversations still exist in those facts.

**Call relations**: This builds on `ScheduleStore.list` instead of duplicating listing logic. It hands each task plus live conversation facts into `ListedTask`, so UI or object-rendering code can make visibility decisions.

*Call graph*: calls 1 internal fn (list); 1 external calls (__init__).


##### `ScheduleStore.claim_due`  (lines 430–486)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: Lets a background runner reserve a batch of due tasks so it can fire them without racing another runner. It also removes expired tasks that are available to clean up.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum batch size. In one transaction, it deletes expired claim-available tasks, selects the oldest unpaused due tasks, stamps them with a new claim ID and claim expiry, and returns the claimed tasks. Tasks beyond the limit remain due for a later sweep.

**Call relations**: The scheduled-task runner calls this when sweeping a workspace. It uses `_expired` and `_claim_available` so claiming follows the same rules as candidate discovery, and `_task` turns claimed rows into runnable task objects.

*Call graph*: calls 3 internal fn (_claim_available, _expired, _task); 6 external calls (timedelta, delete, not_, select, update, uuid4).


##### `ScheduleStore.claim_holds`  (lines 488–519)

```
async def claim_holds(self, task: ScheduledTask) -> bool
```

**Purpose**: Checks whether a runner’s lease still owns the same task version just before firing it. This narrows the chance that a canceled or edited task still runs.

**Data flow**: It receives a claimed `ScheduledTask`. If the task has no claim ID, it raises an error. Otherwise, it rereads the row under a database lock and requires the workspace, ID, claim, conversation, agent, name, and schedule to still match. It returns `true` if the exact claim still holds, otherwise `false`.

**Call relations**: The runner’s `_fire` step calls this immediately before invoking the task. It does not hand off to other helpers, but it is a final gate between claiming and actual delivery.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `ScheduleStore.retire_if_expired`  (lines 521–535)

```
async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool
```

**Purpose**: Deletes a claimed task if its expiry time has already passed before it is invoked. This prevents a task from firing after its allowed lifetime.

**Data flow**: It receives a claimed task and the current time. If the task has no claim ID, it raises an error. If it has no expiry or expires in the future, it returns `false`. If it is expired, it deletes the claimed row and returns `true`.

**Call relations**: The runner’s `_fire` flow calls this before firing. It is the cleanup branch for a task that was claimed but should not run anymore.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `ScheduleStore.reschedule`  (lines 537–567)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: Advances a claimed recurring task after a fire attempt and releases its lease. It records when the task ran and optionally which turn was created by that run.

**Data flow**: It receives the claimed task, the next run time, the last run time, and optionally a turn ID. It updates the row with the new timing information, clears `claimed_by` and `claim_expires_at`, records the turn ID when supplied, and returns whether a row was actually updated.

**Call relations**: The runner’s `_fire` flow calls this after invoking a task. Later, `inspect_many` can use the recorded `last_turn_id` to show the latest run outcome.

*Call graph*: called by 1 (_fire); 1 external calls (update).


##### `ScheduleStore.inspect`  (lines 569–573)

```
async def inspect(self, expected: ScheduledTask) -> TaskInspection | None
```

**Purpose**: Reads the live status picture for one scheduled task. It is a convenience wrapper around the bulk inspection method.

**Data flow**: It receives one expected task, calls `inspect_many` with a one-item tuple, and returns that task’s `TaskInspection` if found. If the task is missing or no longer matches, it returns `None`.

**Call relations**: Status-rendering code can call this for a single task. It delegates all real work to `ScheduleStore.inspect_many` so single-task and multi-task status reads stay consistent.

*Call graph*: calls 1 internal fn (inspect_many).


##### `ScheduleStore.inspect_many`  (lines 575–611)

```
async def inspect_many(self, expected: tuple[ScheduledTask, ...]) -> dict[UUID, TaskInspection]
```

**Purpose**: Reads current timing and latest-run outcome information for several scheduled tasks. This is used to show status without changing the tasks.

**Data flow**: It receives expected `ScheduledTask` objects. It queries matching rows in the current workspace and current agent, gathers any recorded last turn IDs, asks the context for those turn outcomes, and builds a dictionary from task ID to `TaskInspection`. Rows whose name or conversation no longer match the expected task are skipped.

**Call relations**: `ScheduleStore.inspect` calls this for one task, and other callers can use it for batches. It uses `_utc` and `_utc_opt` for clean times and asks the wider context for turn outcomes so status can include the latest response text and status.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 1 (inspect); 3 external calls (__init__, select, object_agent_id).


### Monitor watches
These files run periodic monitor checks and store the watch state needed to fire or retire them exactly once.

### `extensions/monitors/ufo_ext_monitors/monitor_runner.py`

`orchestration` · `recurring scheduled job`

A monitor is like a reminder alarm with a test attached: “run this command every so often, compare the result with what I saw before, and tell me if it changes or goes bad.” This file is the clock-driven worker that does that job.

On each run, it asks the monitor store for monitors that are due and temporarily claims them, so two overlapping workers do not check the same monitor at the same time. For each claimed monitor, it first checks whether the monitor’s deadline has passed. If so, it fires the monitor and retires it. Otherwise it checks whether the member who created the monitor still has a seat in the workspace. That matters because probes can use that member’s connected accounts; if their seat was revoked, the monitor skips the probe instead of keeping their old access alive.

If probing is allowed, the runner executes the saved command. A normal, unchanged result is counted as quiet. A failed command increments a failure streak, and the third failure fires the monitor. A changed output fires immediately. Very large output is saved to a file and only a capped preview is included in the agent message.

When a monitor fires, the runner invokes the agent first and retires the monitor second. That ordering is intentional: if the process crashes after notifying but before retiring, retrying uses the same idempotency key, so the agent does not receive a duplicate meaningful fire.

#### Function details

##### `MonitorRunner.run`  (lines 55–65)

```
async def run(self) -> None
```

**Purpose**: This is the top-level scheduled sweep. It finds monitors that are due, gives each one a turn, and reports at the end if any monitor checks crashed.

**Data flow**: It starts with the extension context and the current time. It builds a monitor store, asks that store to claim due monitor rows for a short lease, then sends each claimed row into the per-monitor tick routine. If a row fails with an exception, it records that monitor’s name instead of stopping the whole sweep immediately. After all rows have had their chance, it raises one combined error if any failed.

**Call relations**: The extension’s recurring job calls this method. It creates the store used for database work, gets the current clock time for the claim, and then delegates the real per-monitor decision-making to MonitorRunner._tick.

*Call graph*: calls 1 internal fn (_tick); 2 external calls (__init__, now).


##### `MonitorRunner._tick`  (lines 67–112)

```
async def _tick(self, store: MonitorStore, row: Monitor) -> None
```

**Purpose**: This gives one claimed monitor its scheduled turn. It decides whether the monitor should fire, skip, record a quiet check, record a failed check, or run its command and compare the output.

**Data flow**: It receives the monitor store and one monitor row. It reads the row’s interval, deadline, creator, command, baseline output, and counters. If the deadline has passed, it fires the monitor. If the creator is no longer seated, or the terminal is gone, it records a skipped tick and schedules the next probe. If the command runs and fails, it either records another failure or fires after the failure threshold. If the command succeeds, it compares the capped output with the saved baseline: matching output becomes a quiet tick, changed output becomes a fire. It updates the store through the appropriate store method or retires the monitor through the fire path.

**Call relations**: MonitorRunner.run calls this once for each claimed monitor. During the turn it asks MonitorRunner._acts_for_a_seated_member whether the monitor may still run with the creator’s authority, uses the probe capability to execute the saved command, writes quiet/failed/skipped outcomes through MonitorStore, and hands final changed, failed, or deadline cases to MonitorRunner._fire.

*Call graph*: calls 5 internal fn (_acts_for_a_seated_member, _fire, failed_tick, quiet_tick, skipped_tick); called by 1 (run); 4 external calls (now, timedelta, capped, stderr_tail).


##### `MonitorRunner._acts_for_a_seated_member`  (lines 114–126)

```
async def _acts_for_a_seated_member(self, row: Monitor) -> bool
```

**Purpose**: This answers a permission question: may this monitor still act as the member who created it? It prevents a monitor from continuing to use a former member’s access after that person has lost their seat.

**Data flow**: It receives a monitor row and reads the creator member id. If there is no creator member id, it returns true because the monitor is not acting as a specific person. If there is a creator, it opens a transaction, asks the seat system whether that member is still admitted to the workspace, and returns that yes-or-no answer.

**Call relations**: MonitorRunner._tick calls this before running a probe, so revoked access stops future command checks. MonitorRunner._fire calls it again just before notifying the agent, so even a deadline fire does not carry a revoked member’s authority.

*Call graph*: called by 2 (_fire, _tick); 1 external calls (__init__).


##### `MonitorRunner._fire`  (lines 128–151)

```
async def _fire(self, store: MonitorStore, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> None
```

**Purpose**: This sends the final monitor-fired message to the agent and then retires the monitor so it will not keep running. It is used when the deadline arrives, the output changes, or failures reach the threshold.

**Data flow**: It receives the store, the monitor row, the cause of the fire, a message payload, optional full output to save separately, and the probe count to report. It first checks that this worker still holds the claim for the row. It then decides whether the fire may act on behalf of the creator, builds the message body, and invokes the agent with an idempotency key that prevents duplicate meaningful posts. If the agent is archived, it stops without retiring. Otherwise, after the invocation succeeds, it marks the monitor retired in the store.

**Call relations**: MonitorRunner._tick calls this for deadline, changed-output, and repeated-failure cases. This method checks authority through MonitorRunner._acts_for_a_seated_member, asks MonitorRunner._body to build the readable message, uses the context to invoke the agent, and finally calls MonitorStore.retire.

*Call graph*: calls 4 internal fn (_acts_for_a_seated_member, _body, claim_holds, retire); called by 1 (_tick).


##### `MonitorRunner._body`  (lines 153–172)

```
async def _body(self, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> str
```

**Purpose**: This builds the text that the agent sees when a monitor fires. It includes enough context for the agent to understand the fire even if older conversation history has been compacted or is no longer visible.

**Data flow**: It receives the monitor row, the fire cause, the probe output or failure text, optional full output that was too large to inline, and the number of probes run. It creates a structured monitor_fired block with the monitor name, cause, reason, next steps, metadata, and counters. If there is oversized output, it saves that output through MonitorRunner._spilled and includes the resulting file path. It escapes the closing marker inside the body so command output cannot pretend to end the block early. If there is a payload, it appends it behind a safety wrapper for untrusted command output.

**Call relations**: MonitorRunner._fire calls this just before invoking the agent. This method may call MonitorRunner._spilled when full output must be stored outside the message, and it uses the untrusted-output wall helper so probe output is shown as data rather than treated like instructions.

*Call graph*: calls 1 internal fn (_spilled); called by 1 (_fire); 2 external calls (dumps, wall).


##### `MonitorRunner._spilled`  (lines 174–182)

```
async def _spilled(self, row: Monitor, output: str) -> str
```

**Purpose**: This saves large probe output to a workspace file and returns the path that can be mentioned in the fire message. It keeps the agent message readable while preserving the complete output.

**Data flow**: It receives the monitor row and the full output text. It checks that conversation file storage is available, creates a timestamped filename under the monitor spill directory, writes the output bytes into the conversation’s runtime files, and returns the saved file path.

**Call relations**: MonitorRunner._body calls this only when the probe output was too large to include fully in the fire message. The returned path is inserted into the body so the agent can find and read the complete output if needed.

*Call graph*: called by 1 (_body); 1 external calls (now).


### `extensions/monitors/ufo_ext_monitors/monitors.py`

`domain_logic` · `monitor setup and periodic runner ticks`

A monitor is like an alarm clock with a checklist attached. It remembers which conversation and agent should be woken up, what shell command should be checked, how often to check it, what output is considered normal, and when the watch must end. This file owns the monitor database table for the extension, so it does not depend on the main application schema. Because database connections are not automatically limited to one workspace, every query explicitly filters by workspace to avoid touching another workspace’s monitors.

The file has three main jobs. First, it defines the monitor table and the in-memory Monitor object that code uses after reading a row. Second, it provides small helpers for safe names, bounded command output, and consistent time zones. Third, MonitorStore offers the actions the rest of the monitor system needs: list armed monitors, insert a new one, claim due monitors for a worker, record probe results, check whether a claim is still valid, remove a monitor after it fires, or disarm it when a user says to stop.

The important safety idea is the claim, which works like putting a temporary sticky note on a row saying “this worker has it.” Updates and deletes usually require that same claim, so two runners do not probe or fire the same monitor at once.

#### Function details

##### `qualified_name`  (lines 73–84)

```
def qualified_name(conversation_id: UUID, slug: str) -> str
```

**Purpose**: Builds the stored object name for a monitor by combining a short prefix from the conversation ID with the human-chosen slug. This keeps names unique across a whole workspace while still letting different conversations reuse friendly names like “ci-run”.

**Data flow**: It takes a conversation UUID and a short slug string. It keeps the first hexadecimal characters of the conversation ID, adds a dash, then adds the slug. The result is the workspace-wide monitor name stored in the database.

**Call relations**: This helper is used when monitor names need to fit the object naming rules and still avoid collisions. It does not call other project code; it is a small naming rule shared by code that arms or refers to monitors.


##### `capped`  (lines 87–97)

```
def capped(output: str) -> str
```

**Purpose**: Shortens probe output so a monitor fire cannot carry an unlimited amount of text. This protects storage and messages from being flooded by huge command output.

**Data flow**: It takes the command output as text. If the encoded text is small enough, it returns it unchanged. If it is too large, it keeps the beginning and end, inserts a clear omitted-bytes marker in the middle, and returns that bounded text.

**Call relations**: Probe-running code can use this before comparing or reporting output. It stands alone and does not hand off to other local functions.


##### `stderr_tail`  (lines 100–105)

```
def stderr_tail(stderr: str) -> str
```

**Purpose**: Keeps only the end of a failed command’s standard error text, where command-line tools usually print the useful error message. This makes failure reports readable and bounded.

**Data flow**: It takes stderr text from a failed probe. If it is within the allowed size, it returns the whole thing. If it is too long, it returns only the final bytes decoded back into text.

**Call relations**: This helper is meant for failure reporting around probe execution. It is independent of the database code and does not call other local functions.


##### `_aware`  (lines 137–138)

```
def _aware(when: datetime) -> datetime
```

**Purpose**: Ensures a datetime has a time zone, defaulting to UTC when one is missing. This prevents later code from mixing ambiguous times with explicit UTC times.

**Data flow**: It receives a datetime value. If the value already has time zone information, it passes it through. If not, it returns a copy marked as UTC.

**Call relations**: _row calls this while turning database rows into Monitor objects. That matters because some databases, especially SQLite, may return times without time zone labels even when the code expects UTC.

*Call graph*: called by 1 (_row); 1 external calls (replace).


##### `_row`  (lines 141–168)

```
def _row(row: sa.RowMapping) -> Monitor
```

**Purpose**: Converts a raw database row into a Monitor object that the rest of the extension can use comfortably. It centralizes the cleanup of database values, especially datetime values.

**Data flow**: It receives a row mapping from a database query. It reads every monitor field, normalizes stored times through _aware, preserves optional fields such as metadata and last_probe_at, and returns a Monitor dataclass instance.

**Call relations**: MonitorStore.armed, MonitorStore.arm, and MonitorStore.claim_due all call this after reading rows. That way, every path from the database produces the same shape of Monitor object before monitor runner code uses it.

*Call graph*: calls 1 internal fn (_aware); called by 3 (arm, armed, claim_due); 1 external calls (__init__).


##### `_claim_available`  (lines 171–172)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this monitor is not currently held by another worker.” A claim is considered available if there is no claimant or the old claim has expired.

**Data flow**: It receives the current time. It creates a SQLAlchemy condition, meaning a Python object that represents SQL, checking for an empty claimed_by field or a claim expiration time before now.

**Call relations**: MonitorStore.claim_due and due_monitor_workspaces.due use this same condition so workspace selection and actual monitor claiming agree about what is available.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `_due`  (lines 175–176)

```
def _due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for “this monitor needs attention now.” A monitor is due if its next probe time has arrived or its deadline has arrived.

**Data flow**: It receives the current time. It returns a SQLAlchemy condition that can be placed into a database query to find monitors whose next check or final deadline is at or before that time.

**Call relations**: MonitorStore.claim_due and due_monitor_workspaces.due both use this helper. This keeps the scheduler’s first-pass workspace search aligned with the later per-workspace claim query.

*Call graph*: called by 2 (claim_due, due); 1 external calls (or_).


##### `due_monitor_workspaces`  (lines 179–196)

```
def due_monitor_workspaces() -> WorkspaceCandidates
```

**Purpose**: Provides the job scheduler with a way to find workspaces that may have monitor work ready. It is the bridge between the monitor table and the broader background job system.

**Data flow**: It defines a small query-making function that finds workspace IDs with due, claimable monitors whose agents are still live. It passes that query maker to owner_candidates, which turns it into a WorkspaceCandidates object for the scheduler.

**Call relations**: The background monitor runner uses this as its candidate seam before opening a workspace. Inside it, the nested due function performs the actual database query construction.

*Call graph*: 1 external calls (owner_candidates).


##### `due_monitor_workspaces.due`  (lines 184–194)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the concrete database query that lists workspaces containing monitor rows ready to be claimed. It filters out rows already leased and rows whose agents are no longer live.

**Data flow**: It reads the current UTC time, builds conditions for claim availability and due status, adds an agent-is-live check, and selects distinct workspace IDs from the monitor table. The output is a SQL query object, not the final list itself.

**Call relations**: due_monitor_workspaces hands this nested function to owner_candidates. It relies on _claim_available and _due so its idea of ready work matches MonitorStore.claim_due.

*Call graph*: calls 2 internal fn (_claim_available, _due); 3 external calls (now, select, agent_is_live).


##### `MonitorStore.armed`  (lines 205–211)

```
async def armed(self, conversation_id: UUID | None=None) -> tuple[Monitor, ...]
```

**Purpose**: Lists the currently armed monitors in this store’s workspace, optionally limited to one conversation. This is how other code asks, “what watches are active right now?”

**Data flow**: It starts with the workspace ID from the ExtensionContext. It builds a select query, optionally adds a conversation filter, runs it in a transaction, orders rows by name, then converts each row into a Monitor object with _row. It returns a tuple of monitors.

**Call relations**: This is a read entry point on MonitorStore. It uses SQLAlchemy to build the query and _row to provide normalized Monitor objects to callers.

*Call graph*: calls 1 internal fn (_row); 1 external calls (select).


##### `MonitorStore.arm`  (lines 213–268)

```
async def arm(self, *, conversation_id: UUID, agent_id: UUID, name: str, audience: str, command: str, interval_minutes: int, deadline_at: datetime, reason: str, next_steps: str, metadata: dict[str, Js
```

**Purpose**: Creates a new armed monitor row. Use this when an agent or member asks the system to start watching a command output over time.

**Data flow**: It receives all monitor details: conversation, agent, name, command, interval, deadline, explanation text, metadata, creator, baseline output, and first probe time. It inserts a new row with a fresh UUID, zeroed counters, no claim, and current timestamps, then returns the inserted row as a Monitor object.

**Call relations**: This is the write path for creating monitors. It calls uuid4 to make the monitor ID, uses SQLAlchemy insert to write the database row, and passes the returned row through _row.

*Call graph*: calls 1 internal fn (_row); 2 external calls (insert, uuid4).


##### `MonitorStore.claim_due`  (lines 270–313)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_MONITORS) -> tuple[Monitor, ...]
```

**Purpose**: Temporarily leases a batch of due monitors for this runner so it can probe them without racing another runner. It is the “take a ticket before doing the work” step.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of monitors. It finds due rows in this workspace whose claims are free and whose agents are live, marks them with a new claim ID and expiration time, and returns the claimed rows as Monitor objects.

**Call relations**: The monitor runner calls this before probing. It uses _due and _claim_available for consistent filtering, agent_is_live to avoid dead agents, and _row to hand back usable Monitor objects. The update-and-return pattern makes overlapping runners split the work instead of duplicating it.

*Call graph*: calls 3 internal fn (_claim_available, _due, _row); 5 external calls (timedelta, select, update, agent_is_live, uuid4).


##### `MonitorStore.quiet_tick`  (lines 315–325)

```
async def quiet_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records a successful probe whose output matched the baseline. It advances the quiet streak, clears the failure streak, and schedules the next probe.

**Data flow**: It receives the claimed Monitor row, the time the probe ran, and the next probe time. It calculates updated counters, then passes them to _tick. The database row is updated and its claim is released.

**Call relations**: MonitorRunner._tick calls this when a probe ran normally and nothing changed. This method is a friendly wrapper around the lower-level _tick update.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.failed_tick`  (lines 327–339)

```
async def failed_tick(self, row: Monitor, probed_at: datetime, next_probe_at: datetime) -> None
```

**Purpose**: Records a probe that ran but exited with an error, when that error is not yet enough to fire the monitor. It counts consecutive failures and resets the quiet streak.

**Data flow**: It receives the claimed Monitor row, the probe time, and the next scheduled probe time. It increments probes_run and failure_streak, sets quiet_streak to zero, leaves skipped unchanged, and sends those values to _tick for storage.

**Call relations**: MonitorRunner._tick calls this after a failed command that should be tracked but not yet reported as the final fire. It delegates the database write to _tick.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore.skipped_tick`  (lines 341–352)

```
async def skipped_tick(self, row: Monitor, next_probe_at: datetime) -> None
```

**Purpose**: Records that a probe could not run at all, for example because the client sandbox was unreachable. This is counted separately from a command failure because no command result was actually observed.

**Data flow**: It receives the claimed Monitor row and the next probe time. It leaves probe and streak counters as they were, increments the skipped count, preserves the previous last_probe_at value, and passes the update to _tick.

**Call relations**: MonitorRunner._tick calls this when the runner could not perform the probe. Like the other tick helpers, it uses _tick to make the actual database change and release the claim.

*Call graph*: calls 1 internal fn (_tick); called by 1 (_tick).


##### `MonitorStore._tick`  (lines 354–386)

```
async def _tick(self, row: Monitor, *, probes_run: int, quiet_streak: int, failure_streak: int, skipped: int, last_probe_at: datetime | None, next_probe_at: datetime) -> None
```

**Purpose**: Writes the common database update after any non-firing probe outcome. It also releases the worker’s claim so the monitor can be picked up again later.

**Data flow**: It receives a claimed Monitor plus the exact counter values and timestamps that should replace the old ones. It refuses to run if the monitor has no claim. Then it updates only the row with the matching monitor ID, workspace ID, and claim ID, clears the claim fields, and updates the timestamp.

**Call relations**: MonitorStore.quiet_tick, MonitorStore.failed_tick, and MonitorStore.skipped_tick all funnel through this method. The claim check is important because only the worker that leased a monitor should be able to write that tick result.

*Call graph*: called by 3 (failed_tick, quiet_tick, skipped_tick); 1 external calls (update).


##### `MonitorStore.claim_holds`  (lines 388–415)

```
async def claim_holds(self, row: Monitor) -> bool
```

**Purpose**: Checks whether this worker still owns the monitor row immediately before firing it. This avoids posting a fire after someone has already disarmed the monitor or another worker has taken over.

**Data flow**: It receives a Monitor that should have a claim ID. If there is no claim, it raises an error. Otherwise it looks for a row with the same monitor ID, workspace ID, and claim ID, locking the row while checking. It returns true if the row is still there under that claim, false otherwise.

**Call relations**: MonitorRunner._fire calls this just before invoking the fire behavior. It uses a select query rather than assuming the old lease is still valid, because the row may have been deleted by disarm.

*Call graph*: called by 1 (_fire); 1 external calls (select).


##### `MonitorStore.retire`  (lines 417–429)

```
async def retire(self, row: Monitor) -> None
```

**Purpose**: Deletes a monitor after its fire has been delivered. This makes sure an armed monitor ends in one fire, not repeated fires every tick.

**Data flow**: It receives a claimed Monitor. If there is no claim, it raises an error. Otherwise it deletes the row only if the monitor ID, workspace ID, and claim ID all match. It returns nothing; the important result is that the database row is gone if the claim still owned it.

**Call relations**: MonitorRunner._fire calls this after delivering the fire. The claim guard means an expired or stolen lease cannot accidentally delete a row now owned by another runner.

*Call graph*: called by 1 (_fire); 1 external calls (delete).


##### `MonitorStore.disarm`  (lines 431–439)

```
async def disarm(self, row: Monitor) -> bool
```

**Purpose**: Stops watching a monitor because a user or member requested it. It reports whether the row was actually removed.

**Data flow**: It receives a Monitor row. It deletes the row matching that monitor ID and workspace ID, without requiring a claim, because this is the explicit stop action. It returns true if exactly one row was deleted and false if there was nothing left to delete.

**Call relations**: This is the user-driven removal path, separate from retire, which is used after a fire. It can remove a monitor even while a runner previously had a copy of it, which is why claim_holds checks again before firing.

*Call graph*: 1 external calls (delete).


### Report digests
These files summarize newly published scheduled reports into short feed entries and enforce the digest-writing rules.

### `extensions/report_digest/ufo_ext_report_digest/digest.py`

`domain_logic` · `digest generation`

This file is the quality gate for report digests. A report may be long, but a digest entry must be short enough for someone scanning a list to quickly decide whether to open the full report. Without this file, digest entries could become too long, repeat themselves, include multiple findings where only one belongs, or vary depending on hidden outside context.

The file defines two main shapes of data. `DigestPoint` is one short finding line, optionally with the actor responsible. `DigestEntry` is the whole digest for one report: whether the report contains a real change, plus a title, summary, and up to two useful points.

The validators work like an editor with a red pen. They trim fields to fixed limits, cut only at word boundaries so text does not end mid-word, and reject a “changed” entry that has no title. They also remove repeated information. To do that, the code turns words into rough roots, ignores common filler words like “the” and “and”, then checks whether each later line adds enough new meaning compared with earlier lines.

Finally, the file limits how much of the original report is sent to the writer, and builds the writing standard by combining prompt text, the delivery register, and the report-digest skill instructions.

#### Function details

##### `_clipped`  (lines 96–107)

```
def _clipped(value: str, ceiling: int) -> str
```

**Purpose**: Shortens a piece of prose to a maximum length without rejecting the whole digest entry. It tries to cut at a word boundary, so the result still looks intentional rather than broken.

**Data flow**: It receives a text value and a character limit. It trims outside spaces, checks whether the text already fits, and if not cuts it down before the limit and removes any dangling punctuation or separator characters. It returns the shortened text.

**Call relations**: This is the shared trimming tool used by the field validators for titles, summaries, point text, and actors. Whenever those model fields are loaded, their validators call this helper so every digest field obeys its display budget in the same way.

*Call graph*: called by 4 (_summary, _title, _actor, _text).


##### `_stem`  (lines 110–117)

```
def _stem(word: str) -> str
```

**Purpose**: Reduces a word to a rough root so that similar forms count as the same idea. For example, this helps treat related words like “reported” and “reporting” as overlapping content.

**Data flow**: It receives one word. It looks at the part after a hyphen when that part is long enough, removes a known ending such as “ing” or “tion” when safe, and then keeps only the first few characters of the resulting root. It returns that compact root string.

**Call relations**: This helper is used by `_content`, which prepares text for the repetition check. It is not used directly by the digest models; it sits one layer below the code that decides whether a line says something new.

*Call graph*: called by 1 (_content).


##### `_content`  (lines 120–121)

```
def _content(text: str) -> tuple[str, ...]
```

**Purpose**: Extracts the meaningful word roots from a piece of text. It ignores very common words so the novelty check is based on substance rather than filler.

**Data flow**: It receives a text string. It lowercases the text, finds word-like pieces, skips stopwords such as “the” and “with”, passes each remaining word through `_stem`, and returns the resulting roots as a tuple.

**Call relations**: This is the text-preparation step for both `_adds_to` and `DigestEntry._said_once`. The digest entry validator uses it to remember what the title and earlier lines have already said.

*Call graph*: calls 1 internal fn (_stem); called by 2 (_said_once, _adds_to).


##### `_adds_to`  (lines 124–126)

```
def _adds_to(text: str, said: set[str]) -> bool
```

**Purpose**: Decides whether a new line adds enough fresh information to be worth showing. It prevents the digest from spending a row on a sentence that mostly repeats what the reader already saw.

**Data flow**: It receives some text and a set of word roots that have already appeared. It converts the text into meaningful roots with `_content`, measures what share of those roots are new, and returns true only when the line has content and enough of it is new.

**Call relations**: This helper is called by `DigestEntry._said_once` while it reviews the summary and points in reading order. It supplies the yes-or-no decision for keeping or dropping each later line.

*Call graph*: calls 1 internal fn (_content); called by 1 (_said_once).


##### `DigestPoint._text`  (lines 139–140)

```
def _text(cls, value: str) -> str
```

**Purpose**: Keeps the text of one digest point within the allowed length. This protects the digest layout from a finding line that is too long.

**Data flow**: It receives the proposed point text during model validation. It sends that text to `_clipped` with the point-text limit, then stores the clipped result as the point’s text.

**Call relations**: Pydantic, the data validation library used here, calls this automatically when creating a `DigestPoint`. It relies on `_clipped` so point text follows the same word-boundary trimming rule as other prose fields.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestPoint._actor`  (lines 144–145)

```
def _actor(cls, value: str) -> str
```

**Purpose**: Keeps the actor name for a digest point within the allowed length. This lets the digest name who did something without letting that name crowd out the finding.

**Data flow**: It receives the proposed actor string during model validation. It passes the string to `_clipped` with the actor-length limit, then stores the shortened version.

**Call relations**: Pydantic calls this as part of building a `DigestPoint`. Like the point-text validator, it delegates the actual trimming to `_clipped` for consistent behavior.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._decoded`  (lines 161–167)

```
def _decoded(cls, value: object) -> object
```

**Purpose**: Accepts point data even when an outside provider returns it as a JSON string instead of a normal nested list. This makes the digest reader tolerant of a common formatting mistake from model or API output.

**Data flow**: It receives the raw value supplied for `points` before normal validation. If the value is a string, it parses it as JSON. If the parsed value is an object with a `points` field, it uses that field; otherwise it uses the parsed value itself. If the original value is not a string, it passes it through unchanged.

**Call relations**: Pydantic calls this before validating the `points` field of a `DigestEntry`. It hands cleaned-up point data onward so the normal tuple of `DigestPoint` objects can be created.

*Call graph*: 1 external calls (loads).


##### `DigestEntry._title`  (lines 171–172)

```
def _title(cls, value: str) -> str
```

**Purpose**: Cleans and shortens the digest title so it carries only the top finding and fits the title space. If a model tries to join two findings with a semicolon, this keeps only the first part.

**Data flow**: It receives the proposed title. It splits the title at the first title-join marker, keeps the first section, clips that section to the title limit, and returns the cleaned title.

**Call relations**: Pydantic calls this when creating a `DigestEntry`. It uses `_clipped` after removing any extra joined finding, so the later digest checks work with a title that follows the standard.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._summary`  (lines 176–177)

```
def _summary(cls, value: str) -> str
```

**Purpose**: Keeps the digest summary to one short clause. This helps the summary act as a quick reason to open the report rather than becoming a mini-report itself.

**Data flow**: It receives the proposed summary text during validation. It sends the text to `_clipped` with the summary limit and returns the shortened result.

**Call relations**: Pydantic calls this as part of `DigestEntry` creation. Its output is later reviewed by `DigestEntry._said_once`, which may remove the summary entirely if it repeats the title.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._titled`  (lines 180–183)

```
def _titled(self) -> 'DigestEntry'
```

**Purpose**: Ensures that any digest entry claiming the report contains a real change has a title. A changed report with no title would give the reader no useful hook.

**Data flow**: It receives the fully assembled `DigestEntry` after field-level validation. If `holds_a_change` is true and the title is empty, it raises an error; otherwise it returns the entry unchanged.

**Call relations**: Pydantic runs this after the individual fields are validated. It acts as a whole-entry rule before the entry is accepted for use.


##### `DigestEntry._said_once`  (lines 186–205)

```
def _said_once(self) -> 'DigestEntry'
```

**Purpose**: Removes summary and point lines that do not add enough new information. It keeps the digest compact by making each surviving line earn its place.

**Data flow**: It starts with the accepted title and records its meaningful word roots. It checks the summary against what the title already said; if the summary is not novel enough, it clears it. Then it walks through the points in order, keeping only points that add enough new content, and stops once it has kept the allowed maximum. It updates the entry’s summary and points, then returns the entry.

**Call relations**: Pydantic runs this after a `DigestEntry` has its fields. It calls `_content` to remember what has already been said and `_adds_to` to decide whether each later line is worth keeping.

*Call graph*: calls 2 internal fn (_adds_to, _content).


##### `bounded`  (lines 208–210)

```
def bounded(report: str) -> str
```

**Purpose**: Cuts a full report down to the maximum amount the digest writer is allowed to read. This controls cost and keeps the digest based on the report’s opening findings rather than the whole argument.

**Data flow**: It receives the full report text. It takes only the first configured number of characters and returns that shortened report text.

**Call relations**: Code that prepares a report for digest writing would call this before sending the report into the writer. It does not call other project functions; it simply applies the shared report-size rule.


##### `writing_standard`  (lines 213–227)

```
def writing_standard() -> str
```

**Purpose**: Builds the instruction text used to tell the digest-writing model how to write entries. It combines the digest prompt, the delivery register, and the skill instructions into one standard.

**Data flow**: It reads the report-digest skill file from disk, removes its frontmatter metadata, reads the subagent digest prompt, adds the delivery register text, and joins those pieces with blank lines. It returns the complete instruction string.

**Call relations**: Digest-writing setup code would call this when it needs the model’s instructions. It pulls from prompt and skill files, plus `DELIVERY_REGISTER_BLOCK`, so the generated digest follows the same rules as an agent using the skill directly.


### `extensions/report_digest/ufo_ext_report_digest/writer.py`

`domain_logic` · `scheduled background tick and rebuild maintenance`

This file is the worker behind the report digest feature. Think of it like a careful newsletter editor: it looks for recent reports, reads each one once, asks an AI model to write a short entry for the right audience, and saves the result so the feed can show it later without paying to read and summarize the same report again.

The worker only considers scheduled runs that finished successfully and published a markdown report. It ignores failed or partial runs because the feed can already explain that the run failed, and summarizing a half-finished report could hide the real story. It also only looks back seven days, so broken or missing reports do not block the job forever.

The main path is `DigestWriter`. On each tick, it fetches a small batch of eligible reports, oldest unsettled work among recent items, reads each report blob with a size limit, decides who the digest is for, and asks the model to return a structured `DigestEntry`. If the model says the report contains a real change, the entry is stored. If the report is quiet, a small “unchanged” row is stored instead. That row matters because it prevents the worker from spending another model call on the same quiet report.

`DigestRebuild` is the reset button. It removes recent digest rows and unchanged markers so the same reports can be summarized again, for example after changing the writing rules.

#### Function details

##### `DigestWriter.run`  (lines 117–126)

```
async def run(self) -> None
```

**Purpose**: Runs one scheduled digest-writing pass. It looks for reports that still need digest entries and tries to process each one without letting one bad report stop the rest.

**Data flow**: It starts with the current workspace context, model access, and blob store already attached to the writer. It asks `_unwritten` for the next batch of reports, then sends each report to `_digest`. If one report fails because the model refuses, the blob is missing, or another unexpected error occurs, it skips that report and continues with the next one.

**Call relations**: This is the top-level action for `DigestWriter`. It first relies on `_unwritten` to decide what work is due, then calls `_digest` for each candidate report. Its main safety role is to keep the batch moving even when one report cannot be completed.

*Call graph*: calls 2 internal fn (_digest, _unwritten).


##### `DigestWriter._digest`  (lines 128–139)

```
async def _digest(self, report: Report) -> None
```

**Purpose**: Processes one report from start to finish. It reads the report, asks the model for a digest, and stores either the digest or a marker saying the report had no important change.

**Data flow**: A `Report` goes in. The function reads its body with `_body`; if the blob cannot be read, it stops. It builds a human description of the intended reader with `_reader`, asks `_written` to produce a structured digest entry, and then checks whether that entry says there was a change. A changed report becomes a stored digest row; an unchanged report becomes a stored unchanged marker.

**Call relations**: `run` calls this once for each report in the batch. `_digest` is the small assembly line that connects the lower-level steps: blob reading, reader wording, model writing, and database storage.

*Call graph*: calls 5 internal fn (_body, _reader, _store, _store_unchanged, _written); called by 1 (run).


##### `DigestWriter._unwritten`  (lines 141–213)

```
async def _unwritten(self) -> tuple[Report, ...]
```

**Purpose**: Finds the reports this tick should work on. It returns recent successful scheduled markdown reports that have not already been summarized or marked unchanged.

**Data flow**: It reads the workspace id from the extension context and builds a database query. The query looks at turns, conversations, agents, members, shared artifacts, existing digest entries, and unchanged markers. It filters to recent scheduled runs that are done, have a markdown artifact, and have not been settled before. The result is converted into `Report` objects containing the turn id, blob key, agent name, audience, and possible owner email.

**Call relations**: `run` calls this before doing any digest work. The reports it returns become the input to `_digest`. This function is also where the batch size, seven-day window, and “do not process the same turn twice” rules come together.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `DigestWriter._body`  (lines 215–228)

```
async def _body(self, report: Report) -> str | None
```

**Purpose**: Reads the markdown report text from blob storage, while enforcing size limits so a huge file cannot overwhelm the worker or the model request.

**Data flow**: It receives a `Report` with a blob key. It streams bytes from the blob store, stops after a fixed byte limit, decodes the bytes into text, and then passes the text through `bounded`, which trims it to the model payload limit. If the blob no longer exists, it returns `None` instead of raising an error.

**Call relations**: `_digest` calls this as the first real processing step. If `_body` returns no text, `_digest` stops for that report. If it returns text, the text is handed on to `_written` for model summarization.

*Call graph*: called by 1 (_digest); 1 external calls (bounded).


##### `DigestWriter._reader`  (lines 230–239)

```
def _reader(self, report: Report) -> str
```

**Purpose**: Describes who the digest is being written for. This helps the model write the entry in the right voice and with the right assumptions.

**Data flow**: It receives a `Report` containing the conversation audience, app name, and possibly the member email. If the report belongs to a specific member conversation and that member is known, it returns a sentence naming that owner. Otherwise, it returns a sentence saying the whole workspace is the reader.

**Call relations**: `_digest` calls this after reading the report body. The returned reader description is sent into `_written` along with the report text, and it is also stored with the final digest entry so later readers can see who the entry was written for.

*Call graph*: called by 1 (_digest).


##### `DigestWriter._written`  (lines 241–278)

```
async def _written(self, body: str, reader: str) -> DigestEntry | None
```

**Purpose**: Asks the model to turn a report into a structured digest entry. It only accepts an answer if the model uses the expected tool-shaped response, which keeps the saved data predictable.

**Data flow**: It takes the report body and reader description. It builds a model request with the digest writing instructions, a compact JSON user message, a maximum output length, and a required tool schema based on `DigestEntry`. The model reply comes back either as plain text or as tool-use blocks. Plain text is rejected. A matching tool-use block is validated into a `DigestEntry`; if no valid block is found, the function returns `None`.

**Call relations**: `_digest` calls this after `_body` and `_reader`. It uses the writing standard and `DigestEntry` schema from the digest module to make the model produce a clean object. `_digest` then decides whether to store that object as a digest row or mark the report unchanged.

*Call graph*: called by 1 (_digest); 7 external calls (__init__, __init__, __init__, model_json_schema, model_validate, dumps, writing_standard).


##### `DigestWriter._store_unchanged`  (lines 280–286)

```
async def _store_unchanged(self, report: Report) -> None
```

**Purpose**: Records that a report was read and found to contain no meaningful change. This prevents the worker from paying to summarize the same quiet report again on the next tick.

**Data flow**: It receives a `Report`. Inside a database transaction, it inserts the workspace id and report turn id into the unchanged table. It does not return a value; the important result is the new database row.

**Call relations**: `_digest` calls this when the model returns a valid digest entry whose `holds_a_change` flag is false. That unchanged marker is later used by `_unwritten` and `undigested_workspaces` to keep this report out of future due-work lists.

*Call graph*: called by 1 (_digest); 1 external calls (insert).


##### `DigestWriter._store`  (lines 288–301)

```
async def _store(self, report: Report, entry: DigestEntry, reader: str) -> None
```

**Purpose**: Saves a completed digest entry for a report. This is the durable row the feed can later read instead of asking the model again.

**Data flow**: It receives the original `Report`, a validated `DigestEntry`, and the reader description. Inside a database transaction, it inserts the workspace id, turn id, title, summary, point list, reader text, model name, and current write time into the digest entry table. It returns nothing; the saved database row is the output.

**Call relations**: `_digest` calls this when `_written` produced an entry that says the report contains a change. Once this row exists, `_unwritten` and `undigested_workspaces` treat the report as settled.

*Call graph*: called by 1 (_digest); 2 external calls (now, insert).


##### `DigestRebuild.run`  (lines 321–339)

```
async def run(self) -> int
```

**Purpose**: Clears recent digest results so they can be written again. This is useful when the digest format or writing standard changes and recent reports should be regenerated.

**Data flow**: It reads the workspace id from the context and finds turns in that workspace within the same seven-day window used by the writer. In one database transaction, it deletes matching rows from both the digest entry table and the unchanged table. It returns the total number of rows removed.

**Call relations**: This is the maintenance counterpart to `DigestWriter`. By deleting both completed entries and unchanged markers, it makes those reports visible again to the normal writer flow. Reports outside the window are left alone because the writer will not revisit them.

*Call graph*: 3 external calls (now, delete, select).


##### `undigested_workspaces`  (lines 342–366)

```
def undigested_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds a database query that identifies workspaces with at least one recent report still needing digest work. A scheduler can use this to avoid waking the writer for quiet workspaces.

**Data flow**: It does not execute the query itself. It creates a SQL query that looks for successful scheduled turns with markdown artifacts inside the seven-day window, excluding turns that already have digest entries or unchanged markers. The query returns workspace ids grouped so each workspace appears once.

**Call relations**: This function supports the scheduling side of the feature. It mirrors the same due-work rules used by `DigestWriter._unwritten`, but at the workspace level, so the system can decide where a digest-writing tick is actually needed.

*Call graph*: 2 external calls (now, select).


### Workspace surfaces and source hooks
These files maintain background-facing user surfaces and record wake-up rules for conversations that depend on changing sources.

### `extensions/sites/ufo_ext_sites/main_homepage.py`

`domain_logic` · `background scheduled job`

This file solves a rollout problem. Older setup code could attach a hosted page to every agent, including the workspace’s main agent. Later, when the chat app becomes that main agent, the app already has its own built-in homepage. But the system checks for an attached hosted page first, so the old attached page would hide the real chat homepage. This file defines a background sweep that finds those workspaces and releases the old binding once.

Think of it like removing an old sign taped over a shop’s new front door. The shop already has the right sign built in, but customers see the taped-on one first until someone removes it.

The job is careful for two reasons. First, not every workspace is updated at the same time, so a one-time database migration would miss some of them. A repeating sweep can catch workspaces whenever they become ready. Second, members may later choose their own homepage for the main agent. The file writes a marker after the first release so it does not keep removing pages that people intentionally add later.

When releasing a page, it also chooses a safe visibility level. Visibility means who is allowed to see the page. The released page keeps the narrower of its own stored visibility and the agent’s visibility, so the cleanup does not accidentally make a page visible to more people than before.

#### Function details

##### `_the_chat_main_agent`  (lines 67–75)

```
def _the_chat_main_agent() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for “the workspace’s main agent that has already been taken over by the chat app.” This prevents the cleanup from touching an agent too early, before the chat app is actually responsible for it.

**Data flow**: It reads no live data itself. It produces a database condition made from three checks: the agent is marked as main, it was provisioned by the chat extension, and its provisioned name is the declared chat agent name. That condition is later used inside database queries.

**Call relations**: The workspace candidate query uses this condition to find only relevant agents, and release_main_homepage uses it again before making any change. It relies on SQLAlchemy’s and_ helper to combine the checks into one database expression.

*Call graph*: called by 2 (release_main_homepage, with_a_bound_main_homepage); 1 external calls (and_).


##### `unreleased_main_homepage_workspaces`  (lines 78–97)

```
def unreleased_main_homepage_workspaces(extension: str) -> WorkspaceCandidates
```

**Purpose**: Declares which workspaces should be considered by the cleanup sweep. A workspace qualifies if it still has a hosted homepage bound to the chat-controlled main agent and has not yet been marked as released.

**Data flow**: It takes the extension name used for the marker records. Inside, it prepares a query that can find matching workspace IDs. It then wraps that query with owner_candidates, which turns the raw database search into the candidate source expected by the job system.

**Call relations**: This is the job’s front door for deciding where work is needed. It hands the actual workspace-finding query to owner_candidates, so the broader job framework can schedule the release work for each matching workspace.

*Call graph*: 1 external calls (owner_candidates).


##### `unreleased_main_homepage_workspaces.with_a_bound_main_homepage`  (lines 84–95)

```
def with_a_bound_main_homepage() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the exact database query that finds workspaces still needing this release. It looks for hosted site rows bound to the chat-controlled main agent, while excluding workspaces that already have the release marker.

**Data flow**: It starts from the hosted_site table, joins it to the agent table so it can inspect the agent, and filters to the chat main agent condition. It also creates an “already released?” check against ext_store and rejects rows where that marker exists. The result is a grouped list of workspace IDs.

**Call relations**: This helper is created inside unreleased_main_homepage_workspaces and supplies its candidate read. It calls _the_chat_main_agent to stay aligned with the release logic, and uses SQL-building helpers such as select, exists, literal, and join to express the database search.

*Call graph*: calls 1 internal fn (_the_chat_main_agent); 4 external calls (exists, literal, select, join).


##### `released_visibility`  (lines 100–104)

```
def released_visibility(site: str, agent: str) -> Visibility
```

**Purpose**: Chooses the safe visibility level for a page after it is no longer bound to the agent. It makes sure the release never widens who can see the page.

**Data flow**: It receives two visibility names: one from the hosted page and one from the agent. It converts each name into its ordered visibility level, compares them, and returns the narrower one according to the project’s visibility order.

**Call relations**: release_main_homepage calls this right before unbinding a page. It uses the store’s visibility_level helper so it compares visibility values in the same way the rest of the hosted-sites system does.

*Call graph*: called by 1 (release_main_homepage); 1 external calls (visibility_level).


##### `release_main_homepage`  (lines 107–130)

```
async def release_main_homepage(ctx: ExtensionContext) -> None
```

**Purpose**: Performs the actual one-time cleanup for a workspace. It finds the chat-controlled main agent, releases any hosted homepage still bound to it, and records that this workspace has been processed.

**Data flow**: It receives an ExtensionContext, which gives access to the current workspace, database transaction, and extension store. It first opens a transaction and looks up the workspace’s chat main agent. If none exists, it stops. If one exists, it asks HostedSites for the homepage bound to that agent. When a bound page is found, it releases the binding using a safe visibility chosen by released_visibility. Finally, it writes a marker key to the extension store so future sweeps know not to undo member-created homepages.

**Call relations**: This is the action run after the candidate system has selected a workspace. It uses _the_chat_main_agent to confirm the target, constructs HostedSites to work with hosted homepage data, calls released_visibility before releasing a page, and uses the context transaction and SQLAlchemy select to read the agent row safely.

*Call graph*: calls 3 internal fn (transaction, _the_chat_main_agent, released_visibility); 2 external calls (__init__, select).


### `extensions/sources/ufo_ext_sources/triggers.py`

`domain_logic` · `source subscription setup, alert sweep, listing, and cleanup`

A “source trigger” is like a standing instruction: “when this source binding changes, notify this conversation through this agent.” This file defines the database table for those instructions and a small store class, SourceTriggerStore, that is the only place other code should use to read or change them.

The important safety rule is workspace separation. The database connection is not automatically limited to one workspace, so every query in this file explicitly checks the current workspace id. That prevents one workspace from seeing or changing another workspace’s triggers.

The store also protects the link between a trigger and its agent. When a trigger is created, it checks that the target conversation belongs to the currently executing agent. That stops an agent from creating a trigger that would wake some other agent’s conversation.

There are two main reading paths. The alert path asks “which triggers should wake for this binding?” and gets all matching triggers across agents in the workspace. The member-facing listing path asks “which triggers should this agent show to a user?” and enriches each trigger with live conversation facts such as audience and surface label, so renamed channels show the current name. Cleanup is supported both for one exact trigger and for all triggers attached to a removed binding.

#### Function details

##### `_utc`  (lines 85–86)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: This helper makes sure a saved time value has timezone information. If the database gives back a plain time with no timezone attached, it treats it as UTC, the common world time standard.

**Data flow**: It receives a datetime value. If the value already says what timezone it belongs to, it returns it unchanged; if not, it adds UTC timezone information. The output is always a datetime that the rest of this file can compare and display consistently.

**Call relations**: _trigger calls this helper while turning a database row into a SourceTrigger. That keeps all trigger objects created by this file using timezone-aware timestamps.

*Call graph*: called by 1 (_trigger); 1 external calls (replace).


##### `_trigger`  (lines 89–104)

```
def _trigger(row: sa.RowMapping) -> SourceTrigger
```

**Purpose**: This helper turns one database row into a SourceTrigger object that the rest of the code can use safely. It also rejects any unknown delivery mode, so bad stored data does not silently move through the system.

**Data flow**: It receives a row read from the source_trigger table. It checks that the delivery value is one of the known choices, normalizes the created and updated times through _utc, and builds a SourceTrigger object. The result is a clean in-memory description of one wake-up rule.

**Call relations**: SourceTriggerStore.create uses this after inserting a new row, while SourceTriggerStore.waking and SourceTriggerStore.list_reported use it after reading rows. It is the shared doorway from raw database results into the file’s plain trigger object.

*Call graph*: calls 1 internal fn (_utc); called by 3 (create, list_reported, waking); 1 external calls (__init__).


##### `SourceTriggerStore.workspace_id`  (lines 114–115)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property gives quick access to the current workspace id from the extension context. The store uses that id to keep every database operation inside the right workspace.

**Data flow**: It reads the workspace id already held by the ExtensionContext and returns it. It does not change anything; it simply exposes the workspace boundary in one convenient place.

**Call relations**: The store’s database methods rely on this property when they add workspace filters to their SQL statements. That is how create, remove, remove_binding, waking, and list_reported avoid crossing workspace boundaries.


##### `SourceTriggerStore.create`  (lines 117–166)

```
async def create(self, conversation_id: UUID, binding: str, delivery: SourceTriggerDelivery, created_by_member_id: UUID | None=None) -> SourceTrigger
```

**Purpose**: This method creates a new trigger saying that one conversation wants updates from one source binding. It also enforces that the conversation belongs to the current agent, so an agent cannot make another agent’s conversation wake up.

**Data flow**: It receives a conversation id, a source binding name, a delivery style, and optionally the member who requested it. It gets the current agent id, checks the conversation’s agent, opens a database transaction, inserts a new row with a fresh id and timestamps, and returns the new row as a SourceTrigger. If the same conversation already watches the same binding, it raises a clear ValueError instead of exposing a raw database conflict.

**Call relations**: Higher-level source subscription code would call this when a user or agent asks to watch a source. Inside the method, object_agent_id supplies the current agent, uuid4 supplies the new trigger id, and _trigger converts the inserted row into the returned object.

*Call graph*: calls 1 internal fn (_trigger); 2 external calls (object_agent_id, uuid4).


##### `SourceTriggerStore.remove`  (lines 168–183)

```
async def remove(self, expected: SourceTrigger) -> None
```

**Purpose**: This method removes one exact trigger, but only if it still matches the trigger the caller expected. That protects against deleting the wrong rule if something changed between reading and removing it.

**Data flow**: It receives a SourceTrigger that represents the trigger the caller intends to delete. It checks that the trigger’s agent is still the current agent, then deletes a row matching the workspace, trigger id, agent id, conversation id, and binding. If no row was deleted, it raises an error to say the trigger changed or disappeared before removal.

**Call relations**: Higher-level unsubscribe code would call this after it has read the trigger to remove. The method calls object_agent_id to verify the current executor and uses SQLAlchemy’s delete builder to issue the database deletion.

*Call graph*: 2 external calls (delete, object_agent_id).


##### `SourceTriggerStore.remove_binding`  (lines 185–194)

```
async def remove_binding(self, binding: str) -> None
```

**Purpose**: This method removes every trigger in the workspace for one source binding. It is used when the source itself is removed, because no conversation should keep watching a binding that no longer exists.

**Data flow**: It receives a binding name. It opens a transaction and deletes all rows in the current workspace with that binding. It does not return a list or count; after it finishes, those wake-up rules are gone.

**Call relations**: Source cleanup code would call this when deleting a shared source or binding. Unlike remove, it intentionally works across agents in the workspace because the source is shared at workspace level.

*Call graph*: 1 external calls (delete).


##### `SourceTriggerStore.waking`  (lines 196–209)

```
async def waking(self, binding: str) -> tuple[SourceTrigger, ...]
```

**Purpose**: This method finds all triggers that should wake when a particular source binding has new activity. It is the read path used by the alert sweep.

**Data flow**: It receives a binding name. It selects matching rows from the current workspace, ordered by creation time and id for stable processing, then turns each row into a SourceTrigger. The output is a tuple of wake-up rules to process.

**Call relations**: Alerting code would call this when a source reports changes. The method uses SQLAlchemy’s select builder to read the database and _trigger to turn each row into the in-memory form that downstream wake-up logic can use.

*Call graph*: calls 1 internal fn (_trigger); 1 external calls (select).


##### `SourceTriggerStore.list_reported`  (lines 211–245)

```
async def list_reported(self, *, conversation_id: UUID | None=None) -> tuple[ListedTrigger, ...]
```

**Purpose**: This method lists triggers that the current agent can report to a member-facing surface, optionally limited to one conversation. It adds conversation audience and label information so the caller can decide what the member is allowed to see and show a current name.

**Data flow**: It optionally receives a conversation id filter. It reads triggers in the current workspace for the current agent, converts rows into SourceTrigger objects, then asks the context for facts about the conversations those triggers belong to. It returns ListedTrigger objects, each pairing a trigger with the conversation’s audience and surface label; triggers whose conversations no longer exist are left out.

**Call relations**: User-facing listing code would call this when showing what sources are being watched. Inside the method, object_agent_id limits the query to this agent, SQLAlchemy’s select builder reads the trigger rows, _trigger converts those rows, and ListedTrigger packages each trigger with live conversation facts.

*Call graph*: calls 1 internal fn (_trigger); 3 external calls (__init__, select, object_agent_id).


### `extensions/web/ufo_ext_web/starters.py`

`domain_logic` · `request handling`

The start screen wants to show a few useful things a member can ask the assistant to build. This file creates that list, called a slate, from three kinds of information: what the system remembers about the member’s work, what applications already exist in the workspace, and what applications the product knows how to create.

The important idea is that suggestions are made only when someone actually opens the screen. There is no background job constantly preparing them. A saved slate is reused for 30 minutes, unless the instructions used to create it have changed. That keeps the screen fast and avoids spending model calls when nobody is looking.

The file also protects against waste and failure. If two browser tabs ask at the same time, a short-lived claim acts like a “someone is already cooking this” note, so only one request pays for a new slate. If the model fails, the file returns whatever older slate it has and sets a cooldown, so the system does not keep retrying immediately. If the model gives messy output, only valid, known, non-duplicate suggestions are kept.

In short, this is the start screen’s suggestion engine: personalized, cached, cautious about cost, and designed to degrade gracefully instead of leaving the member with a frozen or empty pane.

#### Function details

##### `Slate.fresh`  (lines 112–113)

```
def fresh(self, now: datetime) -> bool
```

**Purpose**: This checks whether a saved slate is still safe to reuse. A slate is considered fresh only if it is young enough and was made with the current ranking instructions.

**Data flow**: It receives the current time and reads the slate’s saved creation time and prompt digest. It compares the age against the 30-minute limit and compares the saved prompt marker against the current one. It returns true if both checks pass, otherwise false.

**Call relations**: When StarterCache.read finds a stored slate, it asks Slate.fresh whether that slate can be returned immediately. If it is fresh, the read finishes without calling the model or touching the regeneration flow.


##### `starters_key`  (lines 124–125)

```
def starters_key(member_id: UUID) -> str
```

**Purpose**: This builds the storage key used to save and retrieve a member’s cached starter slate. It gives each member their own named slot in the shared store.

**Data flow**: It takes a member ID, turns it into the project’s standard member subject string, adds the starters prefix, and returns the final key string.

**Call relations**: StarterCache._held uses this key to look up an existing slate. StarterCache.read uses it again to save a newly generated slate after ranking succeeds.

*Call graph*: called by 2 (_held, read); 1 external calls (member_subject).


##### `claim_key`  (lines 128–129)

```
def claim_key(member_id: UUID) -> str
```

**Purpose**: This builds the storage key for the temporary claim that says one reader is already generating a new slate. It prevents duplicate model calls when the same member’s start screen is opened in more than one place.

**Data flow**: It takes a member ID, converts it to the standard member subject string, adds the claim prefix, and returns the key string.

**Call relations**: StarterCache._claim uses this key while trying to reserve the right to generate. StarterCache.read deletes the same key afterward so future reads are not blocked by a completed claim.

*Call graph*: called by 2 (_claim, read); 1 external calls (member_subject).


##### `cooldown_key`  (lines 132–133)

```
def cooldown_key(member_id: UUID) -> str
```

**Purpose**: This builds the storage key for remembering that slate generation recently failed. That cooldown stops repeated reads from immediately hitting the same failing model path.

**Data flow**: It takes a member ID, converts it to the standard member subject string, adds the cooldown prefix, and returns the key string.

**Call relations**: StarterCache._may_generate reads this key to decide whether it is too soon to retry after a failure. StarterCache.read writes to this key when generation throws an exception.

*Call graph*: called by 2 (_may_generate, read); 1 external calls (member_subject).


##### `_stamped`  (lines 136–145)

```
def _stamped(held: object, key: str) -> datetime | None
```

**Purpose**: This safely reads a timestamp from a small stored record. If the record is missing, old, malformed, or half-written, it treats it as absent instead of crashing.

**Data flow**: It receives an unknown stored value and the name of the timestamp field to read. It checks that the value is a dictionary, that the field is a string, and that the string can be parsed as a date-time. It returns the parsed date-time, or None if anything is not readable.

**Call relations**: StarterCache._may_generate uses this to read failure cooldown times. StarterCache._claim uses it to read existing claim times. In both cases, an unreadable stamp simply means the caller can safely continue as if no useful stamp existed.

*Call graph*: called by 2 (_claim, _may_generate); 1 external calls (fromisoformat).


##### `StarterCache.read`  (lines 168–185)

```
async def read(self) -> Slate | None
```

**Purpose**: This is the main entry point for getting the member’s start-screen slate. It returns a fresh cached slate when possible, falls back to a stale one if needed, and only generates a new one when it is safe and worthwhile.

**Data flow**: It starts by getting the current time and reading any stored slate. If the stored slate is fresh, it returns it. If no regeneration should happen, it returns the stored slate even if stale. If regeneration is allowed, it asks the model to rank a new slate, stores it, and returns it. If anything fails during generation, it records a cooldown, logs a warning, removes the claim, and returns the old slate instead.

**Call relations**: This method ties together the whole file. It calls _held to read the cache, _may_generate to decide whether this request should refresh it, _rank to ask the model for a new slate, and the key helpers to update storage. It is the method the rest of the start-screen flow would call when it needs suggestions.

*Call graph*: calls 6 internal fn (_held, _may_generate, _rank, claim_key, cooldown_key, starters_key); 2 external calls (now, warn).


##### `StarterCache._held`  (lines 187–194)

```
async def _held(self) -> Slate | None
```

**Purpose**: This reads the currently stored slate for the member, if one exists and still has a valid shape. It protects the rest of the code from bad stored data.

**Data flow**: It builds the member’s starters storage key, reads the stored value, and checks that it looks like a dictionary. It then validates that dictionary as a Slate. If validation works, it returns the Slate; otherwise it returns None.

**Call relations**: StarterCache.read calls this first. The result becomes either the immediate answer, the fallback answer during regeneration, or None if the member has no usable cached slate.

*Call graph*: calls 1 internal fn (starters_key); called by 1 (read).


##### `StarterCache._may_generate`  (lines 196–206)

```
async def _may_generate(self, now: datetime) -> bool
```

**Purpose**: This decides whether the current read is allowed to create a new slate. It avoids model work when there is no model, no useful memory, no account balance to spend, a recent failure, or another reader already generating.

**Data flow**: It receives the current time and checks the cache object’s model, recalled memory, and solvent flag. It then reads the failure cooldown stamp and compares its age with the cooldown window. If all of that allows generation, it tries to claim the right to generate. It returns true only if this read should proceed.

**Call relations**: StarterCache.read calls this after finding that the cached slate is missing or stale. If _may_generate says no, read returns the old slate. If it says yes, read moves on to _rank.

*Call graph*: calls 3 internal fn (_claim, _stamped, cooldown_key); called by 1 (read).


##### `StarterCache._claim`  (lines 208–225)

```
async def _claim(self, now: datetime) -> bool
```

**Purpose**: This tries to reserve slate generation for one reader at a time. It is like putting a temporary sticky note on the member’s cache saying, “I’m refreshing this now.”

**Data flow**: It builds the claim key and prepares a record with the current time. It first tries to write that record only if no claim exists. If a claim already exists, it reads the old claim time. A recent claim blocks generation; an expired claim may be replaced, but only if the stored value has not changed since it was read. It returns true if this reader successfully owns the claim.

**Call relations**: StarterCache._may_generate calls this as the final gate before allowing model work. StarterCache.read later deletes the claim after the attempt finishes, whether ranking succeeded or failed.

*Call graph*: calls 2 internal fn (_stamped, claim_key); called by 1 (_may_generate); 1 external calls (isoformat).


##### `StarterCache._rank`  (lines 227–254)

```
async def _rank(self, now: datetime) -> Slate
```

**Purpose**: This asks the language model to rank the best starter rows for the member. It packages the member’s memory, existing applications, and buildable catalog into a strict request and expects the model to answer through a structured tool call.

**Data flow**: It reads the cache object’s recalled memory, agent names, model choice, and the unlock catalog. It serializes those into a compact JSON message, sends them with the ranking instructions and a schema describing the expected answer, then receives the model’s reply. It passes that reply to settle_slate and returns the cleaned Slate.

**Call relations**: StarterCache.read calls this only after _may_generate has allowed regeneration. After the model responds, _rank hands the raw reply to settle_slate so the rest of the system gets a validated slate rather than trusting model output directly.

*Call graph*: calls 1 internal fn (settle_slate); called by 1 (read); 4 external calls (__init__, __init__, __init__, dumps).


##### `settle_slate`  (lines 257–290)

```
def settle_slate(reply: Message, generated_at: datetime) -> Slate
```

**Purpose**: This turns the model’s structured reply into a safe Slate. It keeps useful valid rows, drops rows that are malformed, unknown, or repeated, and raises an error if the model never made the required recording call.

**Data flow**: It receives a model reply and the generation time. It searches the reply for the expected record_slate tool call. From that call, it reads the ranked entries, validates each one, keeps only entries that name known catalog unlocks and are not duplicates, and validates the optional check-in. It returns a Slate with the cleaned rows, prompt digest, and timestamp. If there is no tool call at all, it raises an error.

**Call relations**: StarterCache._rank calls this immediately after the model replies. If settle_slate succeeds, read can store the new slate. If it raises because the model did not follow the required contract, StarterCache.read catches that failure, records a cooldown, and falls back to the old slate.

*Call graph*: called by 1 (_rank); 1 external calls (__init__).


### Self-improvement loop
These files build improvement corpora, propose prompt changes, replay and judge saved tasks, and gate successful prompt promotions.

### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `self-improvement evaluation`

This file is the evidence-gathering step for self-improvement. A candidate prompt should not be accepted just because it sounds good. It must prove, on saved examples, that it helps the agent answer better without making other kinds of tasks worse.

The main class, CandidateEvaluation, compares two “arms”: the current prompt and the candidate prompt. For each held-out task example, it replays the task twice using the same archived conversation and tool results: once with the current prompt and once with the candidate prompt. This is like testing two recipes with the same ingredients and oven, so the recipe is the only meaningful difference.

After each replay, the file asks a separate judge model to grade the final answer. The judge is told to return a tiny JSON result saying whether the answer was accepted. Those accept/reject results become OutcomeLabel records, marked as either “present” for the candidate prompt or “absent” for the current prompt.

Finally, the local task results and broader global task results are passed to a two-stage gate. The local check asks, “Did the candidate improve the task type it was mined for?” The global check asks, “Did it avoid breaking other task types?” Without this file, the system would have no consistent, evidence-based way to decide whether a self-improvement candidate should be trusted.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: This is the main entry point for judging a candidate prompt against the current prompt. It runs the comparison on local held-out tasks and optional global held-out tasks, then asks the gate whether the candidate should pass.

**Data flow**: It receives the candidate prompt, the current prompt, a set of local examples, and optionally a set of broader global examples. It turns each example set into accept/reject labels by calling _labels, then gives those two groups of labels to two_stage_gate. The output is a GateVerdict, which says whether the candidate cleared the required improvement checks.

**Call relations**: When some other part of the self-improvement system needs a final decision about a candidate, it calls evaluate. evaluate does not judge answers itself; it delegates replay-and-grading work to _labels, then hands the collected evidence to two_stage_gate for the final statistical decision.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: This helper creates the raw comparison data for a set of saved tasks. For every task, it runs both prompts and records whether each resulting answer was accepted.

**Data flow**: It receives the candidate prompt, the current prompt, and a tuple of held-out task examples. It creates a ReplayEvaluation object, then for each example replays the task once with the current prompt and once with the candidate prompt. Each replay produces final text, which is sent to _accepts for grading. The function returns a tuple of OutcomeLabel objects, where each label says whether the candidate prompt was used and whether the answer succeeded.

**Call relations**: evaluate calls _labels once for local examples and once for global examples. Inside the loop, _labels relies on ReplayEvaluation to regenerate answers under controlled conditions, then calls _accepts to turn each generated answer into a simple pass/fail result.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: This helper asks the judge model whether one answer satisfies one user request. It converts the judge’s response into a plain true or false decision.

**Data flow**: It receives the original request and the answer produced by a replay. It sends both to the judge model with grading instructions, then looks for a JSON object in the judge’s text. If the JSON can be parsed and contains {"accepted": true}, it returns true. If the judge gives malformed output, missing JSON, or anything other than an explicit accepted true, it returns false.

**Call relations**: _labels calls _accepts after each replay, because the gate needs clean success/failure labels rather than free-form model text. _accepts builds the judge message using Message, asks the configured judge model to complete it, and uses json.loads to read the judge’s required JSON verdict.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus building before proposing and evaluating changes`

The self-improvement loop needs real examples of where the system struggled. This file finds those examples by reading saved trajectories, which are full conversation records. Since the system does not have a separate “I had trouble here” signal, it treats a tool error as the clue that something went wrong.

Each useful trajectory is reduced to a TaskExample: the conversation ID, the user’s original request, the full message history, and a short description of the tool error. The failed tool becomes the task class name, such as “tool:browser” or “tool:shell”. This matters because improvements should be judged against the kind of problem they are meant to fix.

The file then groups examples by tool and splits each group in two. One part, called “mine”, is available for proposing an improvement. The other part, called “held_out”, is saved for replaying and judging that improvement. This is like studying from some practice questions but taking the test on different questions, so the system cannot pass just by memorizing the examples it learned from.

Very small groups are skipped, because they cannot provide both a learning example and a test example.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

**Purpose**: Finds the first real user request in a conversation. This gives the self-improvement system the original task it should judge the conversation against.

**Data flow**: It receives the conversation messages. It reads them in order until it finds a message from the user whose content is non-empty plain text. It returns that text, or returns nothing if no suitable user request exists.

**Call relations**: bad_trajectory calls this after looking at a trajectory. If no user request is found, bad_trajectory rejects the trajectory because there is no clear task to grade.

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

**Purpose**: Finds the first tool call in a conversation that ended in an error, and reports which tool it was. This is the file’s main way of detecting that a trajectory is worth learning from.

**Data flow**: It receives the conversation messages. First it scans tool-use blocks and remembers which tool name belongs to each tool-use ID. Then it scans again for an error result block. When it finds one, it uses the stored ID-to-name link to return the tool name and the error text. If no matching error is found, it returns nothing.

**Call relations**: bad_trajectory calls this to decide whether a trajectory contains a useful failure signal. The tool name it returns becomes the task class, so later examples are grouped by the specific tool that failed.

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

**Purpose**: Turns one failed conversation into a labeled training-and-evaluation example. If the conversation has no tool error or no user request, it is ignored.

**Data flow**: It receives one Trajectory, which includes a conversation ID and its messages. It asks first_tool_error for the earliest tool failure and first_request for the original user request. If both exist, it creates a TaskExample containing the ID, request, full messages, and a readable problem statement such as “the X tool errored: ...”. It returns that example together with a class name based on the failed tool.

**Call relations**: task_classes calls this for every trajectory it is given. bad_trajectory acts as the filter and reducer: it passes only useful failed trajectories onward, already labeled by the tool that caused the failure.

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

**Purpose**: Builds the final set of task classes from many conversation trajectories. Each task class contains examples for one failed tool, split into learning and held-out testing sets.

**Data flow**: It receives a tuple of trajectories. For each one, it calls bad_trajectory. Useful examples are grouped under names like “tool:<name>”. Each group is then passed to _split, which may return a TaskClass or reject the group if it is too small. The finished classes are sorted so larger classes come first, with names used as a tie-breaker, and returned as a tuple.

**Call relations**: This is the main entry function in the file for callers that need an evaluation corpus. It coordinates the flow: read trajectories, extract failures with bad_trajectory, split valid groups with _split, and return the ready-to-use classes.

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

**Purpose**: Splits one group of examples into “mine” examples and “held_out” examples. It also rejects groups that are too small to support both learning and testing.

**Data flow**: It receives a class name and all examples for that class. If there are not enough examples, it returns nothing. Otherwise it sorts the examples by conversation ID for a stable order, chooses a held-out count of at least one while leaving at least one mine example, and returns a TaskClass containing the mine examples and held-out examples.

**Call relations**: task_classes calls this after grouping examples by failed tool. _split is the step that protects evaluation quality: it makes sure the examples used to propose an improvement are not the exact same ones used to judge it.

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled background cron tick`

This file is the safety-minded “metronome” for the self-improvement extension. On each scheduled tick, it looks at past agent conversations, groups them by agent, and decides whether there is a prompt improvement worth testing or proposing. It does not directly rewrite an agent. Instead, it can only open a governed change proposal that someone else must approve.

The main idea is cautious trial before promotion. For each agent, the file keeps a saved CandidateState in the extension’s scoped store. That state records which current prompt version the candidate was made from, the proposed new prompt, which task it targets, which examples are held out for testing, how many times it has passed, and whether it is still being evaluated, already promoted, or rejected.

If there is no active candidate for the agent’s current prompt version, the cron asks PromptProposer to suggest one from the available task classes. Then _gate tests the candidate through CandidateEvaluation. A single pass is not enough: the candidate must pass for stability_count consecutive ticks, like requiring a student to pass the same safety check on multiple days before graduating. If it fails, it is marked rejected so it will not be proposed again for the same prompt version. If it passes enough times, the file asks the host system to create an AgentChange proposal.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: This is the top-level action for one self-improvement tick. It gathers all known trajectories, separates them by agent, and advances the improvement process for each agent independently.

**Data flow**: It reads trajectories from the extension context. It groups those conversation records by agent ID, then sends each agent’s group of trajectories into the per-agent advancement step. It returns no value; its effect is that candidates may be opened, tested, saved, rejected, or promoted as proposals.

**Call relations**: When the scheduled cron job runs, this method starts the flow. It uses _by_agent to split the workspace history into per-agent batches, then calls ImproveCron._advance once for each batch so every agent gets its own self-improvement decision.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: This moves one agent one step forward in the candidate-testing process. It either finds or opens a candidate for the agent’s current prompt version, then sends that candidate through the safety gate.

**Data flow**: It receives an agent ID and that agent’s trajectories. It takes the prompt digest from the first trajectory as the version marker, builds the storage key for this agent, and asks for an active candidate or a new one. If there is no candidate, it stops. If there is one, it passes the candidate and trajectories into the gate stage.

**Call relations**: ImproveCron.run calls this after grouping trajectories by agent. This method is the bridge between candidate setup in ImproveCron._active_or_open and candidate evaluation in ImproveCron._gate.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: This finds an existing candidate for the agent’s current prompt version, or asks the proposer to create a new one. It prevents repeated proposals for a candidate that was already promoted or rejected for the same prompt digest.

**Data flow**: It receives a storage key, the current prompt digest, and the agent’s trajectories. First it checks the scoped store for saved candidate data. If the saved candidate belongs to this same prompt version and is still evaluating, it returns that candidate; if it is already terminal, it returns nothing. If no usable candidate exists, it derives task classes from the trajectories, asks the proposer for a prompt candidate, saves the new CandidateState, and returns it.

**Call relations**: ImproveCron._advance calls this before any evaluation happens. This method calls task_classes to find meaningful task groups and uses CandidateState to store the candidate in a durable form, so later cron ticks continue the same trial instead of starting over.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: This is the decision point for a candidate prompt. It evaluates the candidate, counts consecutive successful checks, rejects failures, and opens a governed proposal only after enough repeated passes.

**Data flow**: It receives the agent ID, store key, prompt digest, current candidate, and trajectories. It builds two sets of test examples: the candidate’s own held-out examples and other held-out examples from different task classes. It asks the evaluator whether the candidate prompt beats or preserves behavior compared with the current prompt. If the verdict fails, it saves the candidate as rejected. If it passes but has not yet reached the required stability count, it saves the increased pass count. If it reaches the threshold, it creates an AgentChange proposal and saves the candidate as promoted with the proposal ID.

**Call relations**: ImproveCron._advance calls this after a candidate is available. It uses _held_out to rebuild saved held-out examples from trajectories, task_classes to collect broader comparison examples, ImproveCron._save to persist each outcome, and AgentChange when it is time to hand the proposed prompt change to the host approval system.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: This writes an updated version of a candidate’s status back to the scoped store. It keeps the candidate’s main details the same while changing its evaluation state, pass count, and optional proposal ID.

**Data flow**: It receives the storage key, the current candidate, and the new status information. It copies the candidate with the requested updates, turns it into JSON-friendly data, and writes it to the extension store. It returns no value; the stored candidate is the lasting result.

**Call relations**: ImproveCron._gate calls this whenever the candidate’s state changes: after a failure, after an intermediate pass, or after promotion. It relies on CandidateState.model_copy so updates are made as a clean new model rather than by mutating fields one by one.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: This helper sorts trajectories into buckets by agent. It lets the cron treat each agent’s history separately instead of mixing everyone’s conversations together.

**Data flow**: It receives a tuple of trajectories. It reads each trajectory’s agent ID, appends the trajectory to that agent’s group, and returns a mapping from agent ID to a tuple of that agent’s trajectories.

**Call relations**: ImproveCron.run calls this at the start of a tick. Its output determines how many times ImproveCron._advance runs and which trajectories each agent-specific run sees.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: This helper reconstructs the candidate’s saved held-out test examples from the latest trajectories. It only returns examples that are still present and are considered bad trajectories, because those are the cases useful for testing improvement.

**Data flow**: It receives all trajectories for an agent and a tuple of saved conversation ID strings. It builds a lookup table from conversation ID to trajectory, then walks through the saved IDs. For each matching trajectory, it asks bad_trajectory whether that trajectory should be treated as a failure example. If so, it keeps the TaskExample part and returns all kept examples as a tuple.

**Call relations**: ImproveCron._gate calls this when preparing the candidate’s evaluation set. This function hands the evaluator the specific held-out examples the candidate was originally tied to, while bad_trajectory supplies the judgment about whether each trajectory is useful as a failure example.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation`

This file is a safety gate for prompt self-improvement. When the system tries a new prompt, it gets replay results: did the judge accept the answer when the candidate prompt was present, and did it accept the answer when the candidate was absent? The file turns those yes/no results into a careful verdict.

The important idea is that a small sample can be misleading. Four wins out of four looks great, but it is still uncertain. So this file uses statistical confidence bounds: a conservative way to ask, “How much better is this candidate, even after allowing for luck?” It uses Wilson score bounds, which are a standard way to estimate uncertainty for success rates, and combines them into a lower bound for the lift, meaning the improvement in acceptance rate.

The gate has two stages. First, the candidate must show enough local improvement on the task class it was meant to improve, with enough examples on both sides. Second, it must not show confident harm on other task classes. A noisy or too-small global sample is allowed through, but clear evidence of regression blocks promotion. Like a hiring trial, the candidate must prove it helps at its main job and must not clearly damage the rest of the team.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: Computes the cautious lower estimate of a success rate. Someone uses it when they want to know the lowest plausible acceptance rate after accounting for small-sample uncertainty.

**Data flow**: It takes a count of accepted examples and a total number of examples. If there are no examples, it returns 0. Otherwise it calculates the observed success rate, widens it by a 95% uncertainty margin, and returns the lower end, never below 0.

**Call relations**: This is a building block for lift calculations. The local improvement check calls it through lift_lower_bound, and the global regression check calls it through lift_upper_bound, so both stages can reason about uncertainty instead of raw percentages.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: Computes the optimistic upper estimate of a success rate. Someone uses it when they want to know the highest plausible acceptance rate after allowing for uncertainty.

**Data flow**: It takes accepted and total counts. If there are no examples, it returns 1, meaning the rate is completely unconstrained. Otherwise it calculates the observed success rate, adds a 95% uncertainty margin, and returns the upper end, never above 1.

**Call relations**: This pairs with wilson_lower_bound. lift_lower_bound uses it to be cautious about how well the old prompt might really be doing, while lift_upper_bound uses it to see whether the candidate might still plausibly be acceptable globally.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: Estimates the conservative lower bound of the candidate prompt’s improvement over the old prompt. This is the key number used to decide whether the candidate has proved a real local win.

**Data flow**: It takes a Contingency summary: accepted and total counts for candidate-present and candidate-absent runs. It compares the two observed acceptance rates, then subtracts an uncertainty allowance built from Wilson bounds. The result is the lowest plausible improvement; if either side has no examples, it returns 0.

**Call relations**: score_gate calls this after grouping replay labels into counts. It relies on wilson_lower_bound, wilson_upper_bound, and square-root math to produce a cautious lift estimate that avoids promoting a candidate based only on lucky samples.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: Estimates the optimistic upper bound of the candidate prompt’s lift. It is used to decide whether a candidate is clearly harmful on other task classes.

**Data flow**: It takes a Contingency summary for candidate-present and candidate-absent runs. It compares their acceptance rates, then adds an uncertainty allowance. The result is the best plausible improvement; if either side has no examples, it returns 0.

**Call relations**: global_non_inferior calls this during the second stage of the gate. If even this optimistic lift is below the allowed negative margin, the system treats the candidate as a confident regression.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: Turns individual replay outcomes into a simple four-number summary. This makes later calculations easier by separating candidate-present results from candidate-absent results.

**Data flow**: It receives a tuple of OutcomeLabel records. Each record says whether the candidate prompt was present and whether the answer was accepted. The function counts accepted and total examples for the present side and the absent side, then returns a Contingency object with those four counts.

**Call relations**: Both score_gate and global_non_inferior call this before doing statistics. It acts like sorting ballots into two piles before counting wins in each pile.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: Makes the local promotion decision for the task class the candidate was meant to improve. It requires enough replay examples on both sides and a conservative improvement above the configured floor.

**Data flow**: It takes replay outcome labels, plus optional thresholds for minimum lift and minimum examples per side. It first summarizes the labels with contingency, then computes the conservative lift with lift_lower_bound. It returns a GateVerdict explaining pass or fail, including the lower-bound lift and sample counts.

**Call relations**: two_stage_gate calls this first. If score_gate fails, the full gate stops immediately, because there is no reason to consider global safety unless the candidate has first proved a local win.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: Checks whether the candidate avoids clear harm on other task classes. It does not demand proof that the candidate is globally better; it only blocks candidates when the evidence confidently shows a meaningful regression.

**Data flow**: It takes replay labels from the broader held-out set, plus optional settings for the allowed regression margin and minimum sample count. It summarizes the labels, allows the candidate through if either side has too few examples, and otherwise computes the optimistic lift. It returns true unless even the optimistic estimate is worse than the allowed negative margin.

**Call relations**: two_stage_gate calls this only after the local gate has passed. It uses contingency and lift_upper_bound to answer a narrow safety question: “Do we have strong evidence this candidate hurts other classes?”

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: Combines the local improvement test and the global safety test into one final verdict. This is the main decision point for whether a candidate prompt should be promoted.

**Data flow**: It receives two groups of replay labels: local labels for the targeted task class and global labels for other task classes. It first runs score_gate on the local labels. If that fails, it returns that failure. If the local gate passes, it runs global_non_inferior; a global regression creates a new failing GateVerdict, otherwise the original local pass is returned.

**Call relations**: This function sits at the top of the file’s decision flow. It delegates the local evidence check to score_gate and the broader safety check to global_non_inferior, then packages the final answer for the rest of the self-improvement system.

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `cross-cutting during proposing, replay, and grading`

This file is a small adapter between the self-improvement extension and the project’s model SDK. The real problem it solves is consistency: several parts of the extension need to ask a language model for text or for a tool-using turn, and they should all do that with the same limits, caching, and billing-aware access point. Without this file, each caller might build model requests differently, which could lead to uneven costs, different behavior, or duplicated setup code.

It defines two simple promises, called protocols: `ModelLeg` means “anything with a `complete` method that returns plain text,” and `ReplayLeg` means “anything with a `turn` method that returns a full model message, possibly using tools.” A protocol is like saying, “I do not care what brand of appliance this is, as long as it has the right button.”

`ModelAccessLeg` is the concrete adapter. It wraps the SDK’s `ModelAccess`, then builds a `ModelRequest` with shared settings: the selected model, the system instruction, the message history, a fixed output limit, short conversation caching, and reasoning turned off. For replay-style calls, it also includes available tools. The result is a single, predictable model gateway for this extension.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This is the promised shape for something that can ask a model to produce plain text. Code can depend on this small promise instead of depending on one specific SDK class.

**Data flow**: It receives a system instruction and a tuple of prior messages. An implementation is expected to send that context to a model and return the model’s text as a string.

**Call relations**: This protocol method is a contract for callers that only need simple text completion. `ModelAccessLeg.complete` is the concrete implementation in this file that fulfills the contract by building and sending an SDK model request.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This is the promised shape for something that can ask a model for one full conversation turn, including possible tool use. It lets replay code work with any compatible model adapter, not just one concrete class.

**Data flow**: It receives a system instruction, the message history, and the tool descriptions the model is allowed to use. An implementation is expected to return the next model `Message`, which may include a tool-related response depending on the SDK’s behavior.

**Call relations**: This protocol method is the interface replay-style code can rely on when it needs a whole message back rather than just text. `ModelAccessLeg.turn` is the concrete implementation in this file and passes the tool list into the SDK request.


##### `ModelAccessLeg.complete`  (lines 28–38)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This method asks the wrapped SDK model for a plain text completion using the extension’s standard settings. It is used when the caller wants only the model’s final text answer.

**Data flow**: It takes a system prompt and previous messages, combines them with the wrapped model name and fixed options such as a 2048-token output limit, five-minute conversation cache, and reasoning turned off. It builds a `ModelRequest`, sends it through `self.model.complete`, and returns the resulting text string.

**Call relations**: This is the concrete version of `ModelLeg.complete`. When extension code needs a metered text-only model call, it comes here; this method creates the SDK `ModelRequest` and hands it off to the SDK model access object.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 40–53)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This method asks the wrapped SDK model for the next full conversation message, with a set of tools available to the model. It is useful for replay flows where the model may need to produce structured tool-related output instead of just plain text.

**Data flow**: It takes a system prompt, previous messages, and tool schemas. It packages them with the wrapped model name and the same standard options used by text completion, then builds a `ModelRequest`, sends it through `self.model.turn`, and returns the resulting `Message`.

**Call relations**: This is the concrete version of `ReplayLeg.turn`. Replay-style code can call this adapter without knowing how the SDK request is assembled; this method adds the tools to the request and passes it to the SDK model access object.

*Call graph*: 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal phase`

This file is part of a self-improvement loop. Its job is to turn past failures into a concrete prompt change. Think of it like a coach reading a worker's current instructions, looking at a few examples where the worker got stuck, and drafting a clearer version of the instructions without changing the whole job.

The main piece is PromptProposer. It receives the agent's current system prompt and a TaskClass, which represents a category of tasks plus mined examples of friction. If there are no examples, it does nothing, because there is no evidence to improve from. If examples exist, it builds a clear request for a model: here is the task class, here is the current prompt, here are the problem cases, now return the full revised prompt.

The model is told to make the smallest helpful change, preserve the original voice and scope, and not overfit the agent to only this one task class. After the model answers, the file cleans up common formatting noise, such as Markdown code fences. If the answer is empty or exactly the same as the current prompt, it returns nothing. Otherwise it packages the revised prompt as a PromptCandidate, recording which task class motivated the change.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This is the main action in the file: it tries to create a better system prompt for one task class. Someone would use it when they have evidence that the agent struggled with a category of tasks and want a model-generated improvement candidate.

**Data flow**: It receives the current prompt and a task class. If the task class has no mined problem examples, it stops and returns nothing. Otherwise it builds a user message with PromptProposer._prompt, sends that plus the fixed system instruction to the model, cleans the model's text with _clean, and checks whether the result is empty or unchanged. If the proposal is useful, it returns a PromptCandidate containing the task class name and the revised prompt.

**Call relations**: This function is the doorway into the proposer. During a self-improvement run, another part of the system calls it when it wants a possible prompt rewrite. It relies on PromptProposer._prompt to prepare the evidence for the model, uses Message to wrap that evidence as a chat message, calls the configured ModelLeg to get a completion, then hands the raw answer to _clean before creating the final PromptCandidate.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This builds the actual text shown to the model as the user's request. It lays out the task class, the current system prompt, and a limited set of examples where the agent had trouble.

**Data flow**: It receives the current prompt and the task class. It takes up to the allowed number of mined examples, trims each request and problem description to the allowed character length, and formats them into a readable block. It returns one complete instruction string asking the model to produce the full revised system prompt.

**Call relations**: PromptProposer.propose calls this right before asking the model for a rewrite. Its output becomes the content of the user Message, so it is the bridge between the stored failure examples and the model's prompt-improvement task.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This removes simple formatting wrapper text from the model's answer so the system can compare and store the prompt itself. It is especially meant to strip Markdown code fences, which models sometimes add even when asked not to.

**Data flow**: It receives the raw text returned by the model. It trims whitespace, checks whether the answer starts with a triple-backtick code block, removes the opening and closing fence lines when present, then trims again. It returns the cleaned prompt text.

**Call relations**: PromptProposer.propose calls this after the model responds and before deciding whether the proposal is empty, unchanged, or worth returning. This cleanup step helps prevent a good prompt from being rejected or stored with accidental presentation formatting.

*Call graph*: called by 1 (propose).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `self-improvement evaluation replay`

This file is like a flight simulator for past agent runs. It takes an archived conversation, removes the original final answer, swaps in a new system prompt, and asks the model to continue from that point. If the model asks to use a tool, the file does not actually call the tool. Instead, it looks up the matching tool result from the archived run and feeds that old result back. This matters because prompt experiments should be safe and fair: they should test the prompt, not accidentally send emails, edit files, call APIs, or get different tool data than the original run.

The replay is “tool-aware” because it understands tool requests and tool results in the conversation. It builds a small tool catalog from the tools the archived run already used, strips out model-private reasoning blocks that should not be replayed, and then runs the model for a limited number of rounds. If the model follows the archived tool path, it receives the same old tool answers. If it asks for a tool call that was not in the archive, the replay is considered to have diverged, and the best text produced so far is returned. The result is a controlled counterfactual: what this same task might have looked like under a different prompt.

#### Function details

##### `_canonical_input`  (lines 40–41)

```
def _canonical_input(value: object) -> str
```

**Purpose**: This turns a tool input into a stable text form so two inputs can be compared reliably. It is used to decide whether a replayed tool call matches one from the archived conversation.

**Data flow**: It receives any value used as a tool input. It converts that value to JSON text with sorted keys and compact formatting, so the same input always produces the same string. It returns that string for use as part of a lookup key.

**Call relations**: When archived tool results are indexed, archived_tool_results uses this to label each old tool call. Later, _feed_archived uses the same conversion on the replayed tool call, so it can find the matching archived result instead of running the tool again.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 44–60)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares the archived conversation for replay by removing the old final answer. It keeps the earlier user messages and tool exchanges because those are the shared context for the new prompt experiment.

**Data flow**: It receives the full archived message history. It walks backward through trailing assistant messages and removes final-answer-style messages that do not contain tool calls, then cleans each remaining message of model reasoning blocks. It returns the trimmed, safe-to-replay message tuple.

**Call relations**: ReplayEvaluation.replay calls this near the start of a replay. replay_head delegates the reasoning cleanup to _without_reasoning, then hands the cleaned conversation back as the starting point for the model’s new attempt.

*Call graph*: calls 1 internal fn (_without_reasoning); called by 1 (replay).


##### `_without_reasoning`  (lines 63–71)

```
def _without_reasoning(message: Message) -> Message
```

**Purpose**: This removes hidden or provider-specific reasoning blocks from a message. That prevents replay from sending back internal thinking data that may be invalid, encrypted, or tied to a different model run.

**Data flow**: It receives one message. If the message is plain text, it leaves it alone. If the message is made of structured blocks, it filters out thinking and reasoning blocks and returns a new message with the same role and only the allowed content blocks.

**Call relations**: replay_head calls this for every message it keeps. It is a safety cleanup step before ReplayEvaluation.replay sends the archived context into the model again.

*Call graph*: called by 1 (replay_head); 1 external calls (__init__).


##### `archived_tool_results`  (lines 74–96)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: This builds a lookup table from old tool calls to their old tool results. It is what lets replay answer tool requests from the archive instead of causing real side effects.

**Data flow**: It receives the archived messages. First it collects tool result blocks by their tool-use ID. Then it finds each tool-use block, pairs it with its recorded result, and stores that result under a key made from the tool name and the canonical form of the tool input. It returns this dictionary of reusable tool answers.

**Call relations**: ReplayEvaluation.replay calls this before the model starts replaying. The returned lookup is later passed to _feed_archived, which uses it to answer each replayed tool call safely.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 99–116)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: This creates the small list of tools the replay model is allowed to see. The list comes only from tools that appeared in the archived run.

**Data flow**: It receives the archived messages. It scans them for tool-use blocks, records each distinct tool name once, and creates a permissive tool schema for each name. It returns those schemas as the replay tool catalog.

**Call relations**: ReplayEvaluation.replay calls this before asking the model for a replay turn. The resulting tool schemas are passed into the model so it can reproduce archived-style tool calls, but not discover unrelated live tools.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 119–135)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: This answers the model’s replayed tool calls using the archived results. If any requested call cannot be matched to the archive, it reports that replay has gone off the known path.

**Data flow**: It receives the tool calls from the current model turn and the archived-result lookup. For each call, it searches by tool name and canonical input. If every call has a match, it creates a user message containing tool result blocks with the new call IDs but the old result contents. If any call has no match, it returns None.

**Call relations**: ReplayEvaluation.replay calls this whenever the model asks to use tools. If _feed_archived returns a message, the replay continues with that archived answer; if it returns None, ReplayEvaluation.replay stops because the model has diverged from the archived tool path.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 148–169)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: This runs the full counterfactual replay for one archived task and one candidate system prompt. It returns the final text the model produced under that prompt, while never executing real tools.

**Data flow**: It receives archived messages and a system prompt. It builds archived tool-result lookups, builds the replay tool list, and trims the conversation to remove the original final answer. Then it repeatedly asks the model for the next assistant turn. If the model gives final text with no tool calls, that text becomes the result. If the model asks for tools, the method feeds back archived results. If the tool request cannot be matched, or the round limit is reached, it returns the best text seen so far.

**Call relations**: This is the main flow that ties the helper functions together. It calls archived_tool_results, replay_tools, and replay_head for setup, then uses _feed_archived inside the replay loop. At the end it wraps the chosen final text in a ReplayResult for the caller that is evaluating prompt arms.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-extension-catalog` — The installed extension and pack catalog that says which extra tools, routes, agents, skills, jobs, and backends are available.
- `reg-workspace-records` — The saved workspace records that identify each customer space and hold its limits, setup state, balance settings, and routing boundaries.
- `reg-member-identity` — The shared record of who each user is, how they logged in, what workspace they belong to, and what timezone or invitation state is known.
- `reg-agent-registry` — The durable list of agents, including their names, visibility, owners, purposes, model behavior, provisioning source, and tool policy.
- `reg-conversation-turn-state` — The conversation and turn queue state that tracks each unit of agent work from admission through running, completion, cancellation, or recovery.
- `reg-inbound-message-log` — The saved incoming-message log that keeps outside chat, terminal, web, and scheduled events ordered, unique, and ready to become turns.
- `reg-surface-routing` — The shared mapping from external surfaces such as Slack, iMessage, web, terminal, and hosted sites to the right workspace, agent, and conversation.
- `reg-transcript-history` — The saved conversation transcript, including compacted summaries and durable final results that later stages read instead of relying on memory.
- `reg-live-update-hub` — The live activity stream that carries turn progress, tool status, cancellations, mid-turn replies, and final updates to connected viewers.
- `reg-runtime-fleet-liveness` — The heartbeat and listener-claim records that show which long-running service instances are alive and what work they currently own.
- `reg-workflow-claims` — The workflow attempt and run-claim state that prevents two workers from running the same turn, scheduled task, listener, or cleanup job at once.
- `reg-cancellation-state` — The shared brake state that marks work as stopping or cancelled so model calls, tools, workflows, and retries do not continue stale work.
- `reg-model-catalog` — The shared AI model catalog that records available providers, model names, prices, limits, API routes, key sources, and reasoning support.
- `reg-billing-ledger` — The usage ledger, spend caps, price versions, exports, and prepaid balance records used to meter and charge workspace activity.
- `reg-feature-flags` — The workspace feature switches that let the system turn capabilities on or off without changing the code.
- `reg-observability-traces` — The shared logs, metrics, traces, traceparent links, and safety-filtered operator views used to understand what the system is doing.
- `reg-object-store` — The workspace object records and change journal for agents, members, files, credentials, sites, connectors, memory records, reports, and extension objects.
- `reg-source-page-sync-state` — The source and page records that remember connected feeds, cursors, backoff, deletes, ownership, grants, and the latest synced content.
- `reg-index-memory-store` — The searchable index and long-term memory store built from synced pages, embeddings, recalled facts, and deduplicated notes.
- `reg-scheduled-jobs` — The durable background-job state for scheduled tasks, pauses, monitor checks, report writing, thumbnail repair, product metrics, and self-improvement runs.
- `reg-subagent-tasks` — The parent-child delegation state that tracks spawned helper agents, their inputs, outputs, names, costs, and undelivered results.
- `reg-hosted-site-registry` — The hosted-site records that remember who owns each site, which conversation created it, where it runs, and how previews or sharing are allowed.
- `reg-prompt-and-delivery-policy` — The prompt, delivery-rule, compaction, and prompt-change proposal state that controls what instructions are rendered and how replies should be shaped.
- `reg-extension-data-store` — The per-workspace extension storage area where optional features save their own small durable JSON state.
- `reg-service-connection-pools` — Process-local shared connection/client pools for database, Redis/pubsub, HTTP/provider calls, and other long-lived service clients reused by requests, workers, tools, and jobs.
- `reg-surface-delivery-outbox` — Durable outgoing surface delivery state for final replies, mid-turn replies, writebacks, claims, retries, and de-duplication across Slack, iMessage, web, terminal, and other surfaces.
- `reg-agent-setup-state` — Durable setup checklist and progress state for provisioned agents, including required account links, credentials, schedules, and extension-specific onboarding needs.
- `reg-objectives-plan-store` — Durable objective, plan, step, evidence, and blocker records used by multi-step agent workflows and objective-tracking extensions.
- `reg-evaluation-feedback-store` — Saved evaluation and self-improvement state, including test cases from failures, replay outputs, judge results, prompt-candidate gates, and promotion decisions.
- `reg-background-runner-handles` — Process-local async task handles, wakeup queues, and scheduler loop state for live background workers distinct from their durable job records.
- `reg-source-trigger-wakeup-queue` — Durable wakeup records created from source/page changes so background runners can later admit agent work without losing or duplicating change-triggered starts.
- `reg-durable-workflow-store` — Serialized durable workflow/checkpoint objects used to reload or resume long-running turns, jobs, and recovery work after crashes or code changes.
- `reg-rendered-prompt-audit` — Per-turn rendered prompt metadata, slot validation results, and prompt digests used to trace or replay the exact model prompt later.
