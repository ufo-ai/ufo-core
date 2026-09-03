# Scheduled, recurring, and long-running background work  `stage-16`

This stage is the system’s background crew. It runs during normal operation, after startup, and keeps working even when no person is actively clicking. Its timer and monitor parts act like alarm clocks and watchmen: they fire scheduled conversations, resume paused work when a deadline passes, and poll outside signals until something changes.

The self-improvement and objective parts keep longer efforts on track. Objective tools record plans, steps, evidence, and blockers so progress is based on real state, not guesses. The self-improvement loop studies past failures, proposes prompt changes, tests them on old cases, and only offers safer improvements for human approval.

The runtime files provide the machinery that makes all this safe. candidates.py finds which workspaces may have pending work without breaking workspace boundaries. jobs.py turns job definitions into scheduled runs and prevents runaway piles of work. runtime_instance.py records which server processes are alive and cleans up work left stuck by crashes. product.py reports product usage metrics. The report digest files summarize scheduled reports into short feed entries and avoid reprocessing reports with no useful changes.

## Sub-stages

- [Timers, pauses, monitors, and scheduled turns](stage-16.1.md) `stage-16.1` — 13 files
- [Self-improvement and objective maintenance](stage-16.2.md) `stage-16.2` — 10 files

## Files in this stage

### Workspace metrics and selection
These files report workspace product-state metrics and provide the safe cross-workspace discovery pattern used by background jobs.

### `core/src/ufo/product.py`

`domain_logic` · `scheduled metrics tick`

This file exists so the product team can understand adoption without storing separate “funnel event” records. Instead of remembering every moment when a workspace became invited, active, paid, or connected, it looks at the database as it is now and re-computes the answer on a regular tick. That is useful because if the meaning of a funnel stage changes later, the metric can be re-derived from the real source data.

The main idea is a census. For the workspace currently bound to the running task, the file asks: does this workspace have a seated member, a connector grant, an invited member, a user-made app, a member chat, recent activity, or a paid purchase? Each answer becomes a 0-or-1 metric for that workspace on this tick.

It also reports what the workspace has attached. These are grouped by a simple kind and name: installed surfaces, proved addresses, non-member credentials, connector providers, and provisioned app names. For example, this lets the fleet count things like Slack or GitHub without hard-coding those names here.

A key safety detail is that every database query explicitly includes the workspace ID. It does not rely only on row-level security, which is a database guardrail that hides rows from the wrong workspace. If this code counted rows under the wrong workspace, the metrics would be multiplied instead of obviously failing.

#### Function details

##### `product_census`  (lines 50–152)

```
async def product_census() -> None
```

**Purpose**: This function takes one census of the currently bound workspace. It checks which product funnel stages the workspace has reached, finds what it has attached, and emits metrics that the monitoring system can add up across the fleet.

**Data flow**: It starts with the current workspace ID and the current time. It builds database questions that return yes-or-no answers for funnel stages, such as whether the workspace has invited members, recent member chats, connector grants, or paid purchases. It also builds one combined database question for attached items, such as surfaces, proved addresses, credentials, connectors, and apps. Inside a workspace database transaction, it runs those questions. The stage answers come back as booleans and are turned into 1 or 0 metric values. The attached items come back as kind/name pairs and are emitted as attachment metrics. The function does not store new rows; its visible output is the metrics it sends.

**Call relations**: This function is meant to be run by the product census scheduled job for each workspace. Inside the function, it uses the current workspace binding to know which workspace to inspect, uses SQLAlchemy query builders to describe the database checks, opens a workspace transaction with `ufo.db.workspace_tx`, and then hands the final numbers to `emit_metric` so the observability system can record them.

*Call graph*: 10 external calls (now, timedelta, and_, exists, literal, select, union_all, workspace_tx, emit_metric, ws_current).


### `core/src/ufo/runtime/candidates.py`

`domain_logic` · `main loop`

This file solves a careful security problem: a job scheduler needs to know which workspaces need attention, but it must not freely read tenant data across all workspaces. In this project, workspace data is normally protected by RLS, or row-level security, which means the database only shows rows belonging to the current workspace. The one exception here is a narrow “candidate” read: it may bypass that protection only to ask, “Which workspace IDs have pending work?” It must not return the actual work data.

The main idea is like checking mailbox labels in an apartment building. The scheduler may walk the hallway and note which apartment numbers have mail, but it cannot open anyone’s mail there. Later, it goes to each apartment under the proper rules.

Extensions provide a small query builder that selects distinct workspace IDs from their own tables. The builder is called fresh each time the scheduler checks, so time-based rules such as “due before now” use the current time instead of an old frozen timestamp. The core code then runs that query through `owner_tx`, the special cross-workspace database path, extracts only the first column from each row, and returns those workspace IDs. The dispatcher later binds each ID with the normal workspace scope before running any job handler.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: This function turns a workspace-ID query builder into a callable that the scheduler can use to ask, “Which workspaces have pending work right now?” It keeps extensions away from the privileged cross-workspace database connection while still letting them describe where their work is.

**Data flow**: It receives `due`, a no-argument function that builds a database `Select` query returning one column: workspace IDs. It wraps that builder in an async inner function. The result is a `WorkspaceCandidates` callable that, when run later, will execute the freshly built query and return the workspace IDs as a tuple.

**Call relations**: This is the public seam used when an extension declares how to find its pending work. It does not run the query immediately; it prepares `owner_candidates.candidates`, which the dispatcher or scheduler can call on each tick before binding and running work inside each returned workspace.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner async function actually performs the privileged candidate read. It asks the database for workspace IDs only, not tenant row contents, and returns them to the scheduler.

**Data flow**: When called, it opens `owner_tx`, the special database transaction that can read across workspaces. Inside that transaction it calls `due()` to build the current query, executes it, collects all rows, closes the transaction, and converts the first value from each row into a tuple of workspace UUIDs. It changes no application data; it only reads candidate workspace IDs.

**Call relations**: This function is created by `owner_candidates` and is later called when the runtime checks for work. Its only direct handoff is to `ufo.db.owner_tx`, which provides the controlled cross-workspace read path. After it returns workspace IDs, the wider dispatcher is expected to enter each workspace’s normal scope before any handler does real work.

*Call graph*: 1 external calls (owner_tx).


### Job scheduling and process health
These files materialize scheduled jobs, run them inside the right workspace boundaries, and keep process and job state recoverable after crashes or cancellations.

### `core/src/ufo/runtime/jobs.py`

`orchestration` · `startup and scheduled background job execution`

This file is the background-jobs control room for the system. At startup, core jobs and extension-provided jobs are collected, named, and registered with DBOS, the durable workflow system that stores job progress so work can resume safely after failures. Some jobs run on a schedule, like every minute. Others are one-shot jobs that are enqueued once. Either way, a scheduled “tick” first asks which workspaces actually need work, then fans out one durable job execution per workspace. That is important because one slow or stuck workspace should not block all the others.

The file also contains several core job drivers. TurnDispatcher recovers queued or parked conversation turns and puts them back onto the correct execution queues, while preserving the rule that only one turn in a conversation runs at a time. PageChangeRunner finds extension hooks that want to hear about changed pages, keeps a separate cursor for each hook, and replays page changes in batches. JobRunner is the main registrar and dispatcher: it registers schedules, deduplicates repeated enqueues, builds the right ExtensionContext for each job, provisions agents for a workspace if needed, and logs failures clearly.

Without this file, background work would either not start, run in the wrong tenant workspace, duplicate unsafe work, or stall across the whole fleet when one workspace misbehaved.

#### Function details

##### `ResultDeliverer.run`  (lines 85–85)

```
async def run(self) -> None
```

**Purpose**: This is a contract method for a result-delivery sweep. A concrete implementation uses it to find finished child-agent results and deliver them back to the parent conversation.

**Data flow**: It takes no explicit input beyond the implementing object → the implementation performs the delivery sweep → it returns nothing, but may update conversation state elsewhere.

**Call relations**: The jobs layer does not implement this work directly. core_jobs wraps this method as a core scheduled job, so JobRunner can run it through the same workspace-scoped background-job path as every other job.


##### `ResultDeliverer.candidate_workspaces`  (lines 87–87)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This is a contract method that tells the job system which workspaces have result-delivery work waiting. It lets the scheduler avoid opening workspaces that have nothing to do.

**Data flow**: It takes no explicit input beyond the implementing object → the implementation checks its source of pending result deliveries → it returns a tuple of workspace IDs.

**Call relations**: core_jobs uses this method as the candidate finder for the result-delivery job. JobRunner.tick calls the candidate finder before enqueueing per-workspace job executions.


##### `TurnDispatcher.run`  (lines 155–191)

```
async def run(self) -> None
```

**Purpose**: This sweeps for conversation turns that should be started or resumed and offers them to the correct durable execution queue. It also keeps parked turns blocked if seats, spending rules, or balance checks still say they cannot run.

**Data flow**: It reads dispatchable turn rows for the current workspace → for parked turns, it checks required seated members, spend permission, and account balance → eligible turns are stamped and enqueued; ineligible parked turns are left alone.

**Call relations**: JobRunner.fire runs this through the scheduled turn-dispatch core job. It first asks _dispatchable_turns for possible work, then hands each allowed turn to _enqueue so DBOS can start the turn workflow.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 7 external calls (__init__, __init__, __init__, select, workspace_tx, authority_member_id, turn_authority).


##### `TurnDispatcher.candidate_workspaces`  (lines 193–201)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This finds workspaces that have queued or parked turns ready, or stale enough, to be considered for dispatch. It prevents the turn-dispatch job from running in workspaces with no relevant turns.

**Data flow**: It computes a grace-period cutoff time → reads the owner-level database view for distinct workspace IDs with eligible turns → returns those workspace IDs.

**Call relations**: core_jobs registers this as the candidate finder for the turn-dispatch job. JobRunner.tick uses it before creating one per-workspace job execution.

*Call graph*: calls 1 internal fn (_eligible); 4 external calls (now, timedelta, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 203–244)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: This gathers the actual turn rows in the current workspace that the dispatcher may try to enqueue. It limits the batch so one sweep cannot take unbounded work.

**Data flow**: It computes a stale-dispatch cutoff → queries queued or parked turns that pass the eligibility rules, ordered so older queued work is preferred → converts database rows into _DispatchTurn records.

**Call relations**: TurnDispatcher.run calls this at the start of a workspace sweep. The eligibility test is delegated to _eligible so the same rule can be shared with candidate_workspaces.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 6 external calls (__init__, now, timedelta, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 246–275)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: This safely marks one turn as offered for execution and then enqueues the DBOS workflow for that turn. The database stamp acts like a claim ticket so two sweepers do not both launch the same turn.

**Data flow**: It receives a _DispatchTurn → atomically updates that turn only if it is still in the same state, still stale, and still first in line → if claimed, it builds enqueue options and asks DBOS to run the turn workflow.

**Call relations**: TurnDispatcher.run calls this after any needed parked-turn checks pass. It uses _first_in_status and _stale to repeat the safety checks at claim time, not just at scan time.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 1 (run); 6 external calls (now, timedelta, update, workspace_tx, turn_queue_for, uuid4).


##### `TurnDispatcher._eligible`  (lines 277–297)

```
def _eligible(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database rule for which turns are safe to consider for dispatch. The rule protects conversation order and avoids starting a new turn while another turn in the same conversation is running.

**Data flow**: It receives a cutoff time → creates a SQL condition for queued or parked turns whose dispatch stamp is missing or old, with no running sibling and no earlier same-status turn → returns that condition for use in queries.

**Call relations**: candidate_workspaces uses this to find workspaces with possible turn work. _dispatchable_turns uses the same rule to fetch the specific turn rows inside one workspace.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 4 external calls (and_, exists, or_, select).


##### `TurnDispatcher._stale`  (lines 299–303)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the small part of the turn-dispatch rule that says an enqueue attempt is either missing or old enough to retry. It is the recovery valve for a process that stamped a turn but crashed before enqueueing it.

**Data flow**: It receives a cutoff time → compares each turn's dispatch_enqueued_at value with that cutoff, also allowing empty values → returns a database condition.

**Call relations**: _eligible uses it while scanning for possible work. _enqueue uses it again during the atomic claim so stale information from an earlier scan cannot cause an unsafe enqueue.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 305–314)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the rule that only the earliest queued turn, or earliest parked turn, in a conversation may be offered. It prevents later turns from overtaking earlier ones.

**Data flow**: It receives a turn status such as queued or parked → looks for any earlier turn in the same workspace and conversation with that status → returns a database condition that is true only when none exists.

**Call relations**: _eligible uses this to filter scans. _enqueue uses it again when claiming a turn, so a race with another process cannot accidentally dispatch a later turn first.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `_page_beyond_cursor`  (lines 317–322)

```
def _page_beyond_cursor(revision: int, page_id: UUID, cursor: object) -> bool
```

**Purpose**: This answers whether a page change comes after a saved cursor. A cursor is a bookmark that records the last page position a consumer has processed.

**Data flow**: It receives a page revision, page ID, and stored cursor → if there is no cursor, it treats the page as pending; otherwise it parses the cursor and compares revision and ID → returns true when the page is newer than the cursor.

**Call relations**: PageChangeRunner.workspaces_with_changes uses this while deciding which workspaces have page changes waiting for a particular page-change consumer.

*Call graph*: called by 1 (workspaces_with_changes); 1 external calls (page_cursor).


##### `PageChangeConsumer.spec_name`  (lines 343–345)

```
def spec_name(self) -> str
```

**Purpose**: This gives one page-change consumer its unique job-spec name. The name separates different hooks, even when they belong to the same extension.

**Data flow**: It reads the consumer's extension name and discriminator → formats them with the page-change job prefix → returns the job-spec name string.

**Call relations**: PageChangeRunner.consumers creates PageChangeConsumer objects. core_jobs later reads this property when registering one core job for each consumer.


##### `PageChangeConsumer.job`  (lines 348–353)

```
def job(self) -> str
```

**Purpose**: This gives the full job key used for model-spend and latency attribution for this page-change consumer. It says that the runner is core-owned while still naming the extension hook being driven.

**Data flow**: It reads the consumer's computed spec name → prefixes it with the core namespace → returns a binding-style job key string.

**Call relations**: PageChangeRunner._context_for uses this when building the ExtensionContext, so work done by a page-change hook is attributed to the correct logical job.


##### `PageChangeRunner.consumers`  (lines 396–420)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: This discovers every registered extension hook that listens for page changes and turns each one into an independent consumer. It also rejects duplicate handler names inside the same extension because those would collide on cursor storage.

**Data flow**: It reads all active manifests → filters their hooks to the page_change event, records declared credential slots, and checks uniqueness → returns a tuple of PageChangeConsumer objects.

**Call relations**: core_jobs calls this while building the list of core jobs. Each returned consumer becomes its own scheduled page-change job with its own candidates and cursor.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 422–487)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: This finds the workspaces where a particular page-change consumer has unread page changes. It avoids running page-change jobs in quiet workspaces.

**Data flow**: It reads each workspace's newest page and that consumer's saved cursor from owner-level database access → compares newest page positions to cursors, treating bad cursors as pending and warning about them → returns workspace IDs with pending changes.

**Call relations**: core_jobs wraps this as the candidate finder for each page-change consumer. JobRunner.tick calls that wrapper before enqueueing per-workspace page-change runs.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 3 external calls (select, owner_tx, warn).


##### `PageChangeRunner.drive`  (lines 489–539)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: This runs one page-change consumer inside the currently bound workspace. It reads changed pages in batches, calls the extension's hook, and advances the consumer's cursor only after the hook succeeds.

**Data flow**: It builds an extension context and reads the stored cursor → repeatedly fetches a batch of changed pages, passes them to the hook, and conditionally writes the next cursor → returns when there is no more work, the batch is short, or another writer already advanced the cursor; on hook failure it logs and raises without moving the cursor.

**Call relations**: The per-consumer job created by core_jobs calls this through its nested handler. It relies on _context_for for the extension context and on the page feed for batched changes.

*Call graph*: calls 1 internal fn (_context_for); 6 external calls (__init__, __init__, emit_metric, formatted_stack, log_error, ws_current).


##### `PageChangeRunner._context_for`  (lines 541–559)

```
def _context_for(self, consumer: PageChangeConsumer) -> ExtensionContext
```

**Purpose**: This builds the ExtensionContext used by a page-change hook. The context is the hook's toolbox: storage, page feed, model access, blob store, invoker, and observability probes as configured.

**Data flow**: It reads the current workspace ID and runner dependencies → optionally creates a turn invoker and swaps in the background model registry → returns an ExtensionContext scoped to the consumer's extension and job key.

**Call relations**: PageChangeRunner.drive calls this before invoking a hook. It uses _background_registry so background page-change work can use the configured background model.

*Call graph*: calls 1 internal fn (_background_registry); called by 1 (drive); 2 external calls (context_for, ws_current).


##### `_background_registry`  (lines 562–573)

```
def _background_registry(registry: ModelRegistry | None, background_model: str | None) -> ModelRegistry | None
```

**Purpose**: This adjusts model selection for background jobs. If a separate background model is configured, it returns a copy of the model registry whose default model points there.

**Data flow**: It receives an optional registry and optional background model name → if either is missing, it returns the registry unchanged; otherwise it copies the registry with auto_model replaced → returns the registry to use for the job context.

**Call relations**: PageChangeRunner._context_for uses it for page-change hook contexts. JobRunner.fire uses it for normal jobs unless a job explicitly needs the deploy's default model.

*Call graph*: called by 2 (fire, _context_for); 1 external calls (replace).


##### `core_jobs`  (lines 576–681)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, page_change_runner: PageChangeRunner, delivery_sweep: ResultDeliverer, preview_renderer: PreviewRenderer | None) -> tuple[JobSpe
```

**Purpose**: This builds the list of built-in jobs that every deployment should know about. These include source syncing, page-change fan-out, turn dispatch, result delivery, product census, and optional preview rendering.

**Data flow**: It receives the core service objects needed by those jobs → wraps their methods in JobSpec handlers and candidate finders, also creating one JobSpec for each page-change consumer → returns a tuple of JobSpec objects.

**Call relations**: Startup code can pass this output into bindings_from along with extension manifests. The nested wrapper functions inside core_jobs are later called by JobRunner.fire when their jobs execute.

*Call graph*: calls 1 internal fn (consumers); 2 external calls (__init__, seated_member_workspaces).


##### `core_jobs._sync_sources`  (lines 600–601)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: This small wrapper runs the source-sync driver as a job handler. It exists so the source-sync method fits the standard JobSpec handler shape.

**Data flow**: It receives an ExtensionContext, which it does not need → calls the sync driver to poll and land source pages → returns nothing after the sync completes.

**Call relations**: core_jobs installs this as the handler for the source-sync core job. JobRunner.fire calls it when that job is executed for a candidate workspace.


##### `core_jobs._dispatch_turns`  (lines 603–604)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs the TurnDispatcher as a job handler. It adapts the dispatcher to the common job interface.

**Data flow**: It receives an unused ExtensionContext → asks the turn dispatcher to sweep the current workspace → returns nothing after dispatching eligible turns.

**Call relations**: core_jobs installs this as the handler for the turn-dispatch core job. JobRunner.fire calls it inside a workspace selected by TurnDispatcher.candidate_workspaces.


##### `core_jobs._deliver_results`  (lines 606–607)

```
async def _deliver_results(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs the result-delivery sweep as a job handler. It lets result hand-back work use the same job runner as all other background work.

**Data flow**: It receives an unused ExtensionContext → calls the delivery sweep's run method → returns nothing after delivery work completes.

**Call relations**: core_jobs installs this as the handler for the result-delivery job. JobRunner.fire calls it for each workspace reported by the delivery sweep's candidate finder.


##### `core_jobs._census_product`  (lines 609–610)

```
async def _census_product(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs the product census job, which counts product usage and funnel state. It is a core job because it reads broad core schema data.

**Data flow**: It receives an unused ExtensionContext → calls product_census → returns nothing once census collection finishes.

**Call relations**: core_jobs installs this as the product-census handler. Its candidates come from seated_member_workspaces, and JobRunner.fire runs it through the same workspace binding path.

*Call graph*: 1 external calls (product_census).


##### `core_jobs._render_previews`  (lines 612–614)

```
async def _render_previews(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs preview rendering when a preview renderer is configured. It turns pending preview work into a standard scheduled job.

**Data flow**: It receives an unused ExtensionContext → confirms a preview renderer exists, then calls its run method → returns nothing after previews are rendered.

**Call relations**: core_jobs includes this handler only when preview_renderer is not None. JobRunner.fire calls it for workspaces returned by _preview_candidates.


##### `core_jobs._preview_candidates`  (lines 616–618)

```
async def _preview_candidates() -> tuple[UUID, ...]
```

**Purpose**: This wrapper asks the preview renderer which workspaces have preview work waiting. It keeps the preview job from running everywhere unnecessarily.

**Data flow**: It reads the configured preview renderer → asks it for candidate workspace IDs → returns those IDs.

**Call relations**: core_jobs uses this as the candidate finder for the optional render-previews job. JobRunner.tick calls it before enqueueing preview-rendering workflows.


##### `core_jobs._drive_consumer`  (lines 620–626)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: This creates a job handler for one page-change consumer. It captures which consumer should be driven when the scheduled job fires.

**Data flow**: It receives a PageChangeConsumer → creates an async handler that calls PageChangeRunner.drive for that consumer → returns that handler function.

**Call relations**: core_jobs calls this while creating per-consumer page-change JobSpecs. The returned _handler is later invoked by JobRunner.fire.


##### `core_jobs._drive_consumer._handler`  (lines 623–624)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: This is the actual job handler for one page-change consumer. It runs that consumer's cursor loop in the current workspace.

**Data flow**: It receives an ExtensionContext, which the page-change runner rebuilds in its own way → calls page_change_runner.drive with the captured consumer → returns when that consumer has caught up or stops for safety.

**Call relations**: JobRunner.fire invokes this handler for a page-change JobSpec. The handler delegates the real batching, hook call, and cursor update to PageChangeRunner.drive.


##### `core_jobs._consumer_candidates`  (lines 628–632)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: This creates a candidate finder for one page-change consumer. It captures which consumer's cursor should be checked.

**Data flow**: It receives a PageChangeConsumer → creates an async candidate function that asks PageChangeRunner for workspaces with changes for that consumer → returns that function.

**Call relations**: core_jobs uses this when creating per-consumer page-change JobSpecs. JobRunner.tick later calls the returned _candidates function.


##### `core_jobs._consumer_candidates._candidates`  (lines 629–630)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: This candidate finder returns workspaces where the captured page-change consumer has pending page changes. It is the per-consumer bridge into PageChangeRunner's workspace scan.

**Data flow**: It takes no explicit input → calls page_change_runner.workspaces_with_changes for the captured consumer → returns the workspace IDs that need a page-change run.

**Call relations**: JobRunner.tick calls this before enqueueing page-change workflows. It hands the workspace selection decision to PageChangeRunner.workspaces_with_changes.


##### `bindings_from`  (lines 693–724)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...], disabled: frozenset[str]=frozenset()) -> tuple[_Binding, ...]
```

**Purpose**: This assigns every job a full binding key and connects it to the extension namespace and credential slots it should use. It also applies a disabled-job list and rejects disabled names that were never registered.

**Data flow**: It receives manifests, core JobSpecs, and optional disabled job keys → creates core bindings under the core namespace and extension bindings under each manifest's name → returns only bindings not disabled, or raises if the disabled set names unknown jobs.

**Call relations**: Startup code uses this before creating JobRunner. JobRunner later uses the returned bindings to register schedules, find candidates, and build the correct ExtensionContext for each job.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 754–778)

```
def launch(self) -> None
```

**Purpose**: This publishes all known jobs to DBOS at startup. Scheduled jobs are registered with cron-like schedules, and one-shot jobs are enqueued once with deduplication.

**Data flow**: It stores this runner in the module-level firing slot → walks every binding, either enqueueing an immediate tick or collecting a schedule definition → applies schedules to DBOS and logs what was registered or skipped.

**Call relations**: This is the setup step that makes job_tick able to find the active JobRunner. The DBOS scheduler and queue later call job_tick using the keys registered here.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 780–802)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: This handles one scheduled firing of a job key. It fans the job out to the workspaces that currently have work and deduplicates each workspace execution so repeated ticks do not pile up.

**Data flow**: It receives the scheduled time and job key → skips unknown keys, otherwise asks for candidate workspaces → enqueues one job_workflow per workspace using a deduplication ID made from job key and workspace ID.

**Call relations**: The DBOS workflow function job_tick calls this. It uses candidates to choose workspaces, and each successful enqueue later runs job_workflow and job_fire.

*Call graph*: calls 2 internal fn (_registered, candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 804–805)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: This asks a registered job which workspaces need it. It is a thin safety wrapper around the job binding's own candidate finder.

**Data flow**: It receives a job key → looks up the binding or raises if missing → calls the JobSpec's candidates function → returns workspace IDs.

**Call relations**: JobRunner.tick calls this after checking that the key is registered. It relies on _binding to turn the key into the correct JobSpec.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 807–841)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: This runs one job handler inside one specific workspace. It prepares workspace scope, applies agent provisioning once per workspace, builds the extension context, and logs failures.

**Data flow**: It receives a job key and workspace ID → finds the binding, enters that workspace, provisions agents if needed, builds the context with the right services and model registry → awaits the job handler; on error it logs details and re-raises.

**Call relations**: job_fire calls this as the durable DBOS step for per-workspace job execution. It uses _binding for lookup and _background_registry for most background-job model selection.

*Call graph*: calls 2 internal fn (_binding, _background_registry); 6 external calls (__init__, failed_statement, formatted_stack, log_error, context_for, ws).


##### `JobRunner._registered`  (lines 843–844)

```
def _registered(self, key: str) -> _Binding | None
```

**Purpose**: This checks whether the current process knows a job key. It returns nothing instead of raising because old schedules may still exist for jobs this process no longer owns.

**Data flow**: It receives a job key → searches the runner's bindings → returns the matching binding or None.

**Call relations**: JobRunner.tick uses this to skip stale or foreign schedules safely. JobRunner._binding uses it as the lookup step before deciding whether to raise.

*Call graph*: called by 2 (_binding, tick).


##### `JobRunner._binding`  (lines 846–853)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: This returns the binding for a job key, or raises if the key is not registered in this process. It is used when the code is about to do real work and missing registration is a fault.

**Data flow**: It receives a job key → calls _registered → returns the binding if found, otherwise raises a RuntimeError.

**Call relations**: JobRunner.candidates and JobRunner.fire call this when they need the JobSpec and context metadata. It builds on _registered's safe lookup behavior.

*Call graph*: calls 1 internal fn (_registered); called by 2 (candidates, fire).


##### `job_tick`  (lines 860–864)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: This is the durable DBOS workflow for a scheduled job tick. It hands the tick to the active JobRunner.

**Data flow**: It receives a scheduled time and job key from DBOS → reads the module-level JobRunner set during launch → calls runner.tick, or raises if jobs were never registered.

**Call relations**: JobRunner.launch registers or enqueues this workflow for every job key. DBOS invokes it, and it delegates all fan-out decisions to JobRunner.tick.


##### `job_workflow`  (lines 868–869)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: This is the durable DBOS workflow for one job running in one workspace. It exists so each workspace execution has its own durable workflow record.

**Data flow**: It receives the scheduled time, job key, and workspace ID string → forwards the job key and workspace ID to job_fire → returns when the step completes.

**Call relations**: JobRunner.tick enqueues this workflow once per candidate workspace. It immediately hands off to job_fire, which is the step that calls JobRunner.fire.

*Call graph*: calls 1 internal fn (job_fire).


##### `job_fire`  (lines 873–877)

```
async def job_fire(key: str, workspace_id: str) -> None
```

**Purpose**: This is the DBOS step that actually fires a job handler for one workspace. Marking it as a step lets DBOS avoid re-running a completed handler during recovery.

**Data flow**: It receives a job key and workspace ID string → reads the active JobRunner, converts the workspace ID to a UUID → calls runner.fire; it raises if no runner was launched.

**Call relations**: job_workflow calls this for every per-workspace job execution. It is the final bridge from DBOS durable workflow plumbing into JobRunner.fire's workspace-scoped handler execution.

*Call graph*: called by 1 (job_workflow); 1 external calls (UUID).


### `core/src/ufo/runtime/runtime_instance.py`

`orchestration` · `background during serve process lifetime`

Think of every serve process as a worker wearing a badge. This file writes that badge into the database, refreshes it every few seconds, and removes it when the process shuts down cleanly. Other processes use those badges to tell who is still alive.

That liveness signal matters because durable work is tied to an executor id, which is the same id as the runtime instance. If a workflow is still waiting under an executor whose badge is no longer fresh, the process probably died. The executor recovery loop finds those stranded workflows and asks DBOS, the durable workflow system, to recover them.

The file also runs two turn cleanup loops. A “turn” is a unit of conversation or agent work. The cancel reconciler makes cancellation flow down a turn tree: if a parent turn is cancelled, dependent child turns are eventually cancelled too. The stranded turn reconciler catches a different problem: a turn marked running, but whose workflow has ended or disappeared, so nothing can ever advance it. It cancels those rows after a grace period.

All these loops are deliberately periodic and forgiving. A single database or DBOS error is logged, not allowed to kill the loop. Every serve process runs the same sweeps, so if one process dies, another survivor can clean up after it.

#### Function details

##### `record_fleet_seat`  (lines 42–57)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: This writes the current serve process into the shared runtime table before DBOS starts running work. It gives the rest of the fleet an immediate sign that this executor is alive.

**Data flow**: It receives an instance id. It opens an owner-level database transaction, inserts a runtime_instance row with no workspace, current timestamps, and that id, then logs that the fleet seat was recorded. The database now has a fresh liveness record for this process.

**Call relations**: This is the first half of the liveness story. Later, Heartbeat.beat keeps the same row fresh, and ExecutorRecovery._live_executors reads these rows to avoid recovering work that belongs to a still-live process.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 70–80)

```
async def run(self) -> None
```

**Purpose**: This is the never-ending heartbeat loop for one process. It keeps calling the single heartbeat update so peers know the process is still alive.

**Data flow**: It uses the Heartbeat object's instance id. On each cycle it tries to write a fresh timestamp through Heartbeat.beat; if the database update fails, it logs the failure; then it waits for the configured heartbeat interval and repeats. Its output is not a return value, but an ongoing stream of freshness updates.

**Call relations**: A serve process starts this loop after its seat exists. The loop delegates the actual database write to Heartbeat.beat and uses logging only when one tick fails, so the heartbeat can survive temporary database trouble.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 82–92)

```
async def beat(self) -> None
```

**Purpose**: This performs one heartbeat update. It stamps the process's runtime row with the current database time so other processes can treat it as alive.

**Data flow**: It reads the Heartbeat object's instance id. It opens a database transaction and updates the matching runtime_instance row's heartbeat and updated timestamps. Afterward, that row looks fresh to recovery sweeps.

**Call relations**: Heartbeat.run calls this on every tick. ExecutorRecovery._live_executors later reads the timestamp this function writes and uses it to decide which executors must not be recovered.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 94–100)

```
async def retire(self) -> None
```

**Purpose**: This removes the process's liveness row during graceful shutdown. It lets other processes see right away that this seat is gone instead of waiting for the heartbeat to become stale.

**Data flow**: It reads the Heartbeat object's instance id. It opens a database transaction and deletes the runtime_instance row with that id. The visible result is that the process no longer appears in the live fleet table.

**Call relations**: The serve shutdown path calls this through ufo.serve._stop_executor. It is the clean exit counterpart to record_fleet_seat and Heartbeat.beat.

*Call graph*: called by 1 (_stop_executor); 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 120–126)

```
async def run(self) -> None
```

**Purpose**: This is the repeating loop that looks for durable workflows owned by dead executors. It keeps recovery running in the background for the whole serve process.

**Data flow**: It uses the configured interval on the ExecutorRecovery object. Each cycle waits, calls ExecutorRecovery.sweep, logs database or DBOS workflow-system errors if they happen, and then continues. It produces no direct return value; its effect is continued recovery attempts over time.

**Call relations**: Every serve process can run this loop. It delegates one recovery pass to ExecutorRecovery.sweep, so any surviving process can clean up work left by a crashed peer.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 128–136)

```
async def sweep(self) -> None
```

**Purpose**: This performs one pass of executor recovery. It finds executors with pending workflows but no fresh heartbeat, then asks DBOS to recover their work.

**Data flow**: It gathers two sets: executor ids with pending workflows, and executor ids with fresh runtime_instance rows. It subtracts live executors from pending executors; for each remaining stranded executor, it calls DBOS recovery in a worker thread and logs how many workflows were recovered.

**Call relations**: ExecutorRecovery.run calls this on each interval. It relies on ExecutorRecovery._pending_executors to learn where pending work exists and ExecutorRecovery._live_executors to avoid touching work that belongs to a live process.

*Call graph*: calls 2 internal fn (_live_executors, _pending_executors); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_executors`  (lines 138–150)

```
async def _pending_executors(self) -> set[str]
```

**Purpose**: This asks DBOS which executors currently hold pending workflows. These are candidates for recovery if their owning process is no longer alive.

**Data flow**: It calls DBOS.list_workflows in a thread, filtering for workflows with status PENDING and limiting the scan size. It logs if the scan hits the limit, then returns the executor ids found on those workflows. The result is a set of executor id strings.

**Call relations**: ExecutorRecovery.sweep calls this before comparing against live executors. Its result is only a candidate list; ExecutorRecovery._live_executors decides which of those candidates are safe to recover.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 152–162)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: This reads the database to find executor ids whose heartbeat is still fresh. It protects live processes from having their work recovered by mistake.

**Data flow**: It calculates a cutoff time using the current time minus the stale threshold. It selects runtime_instance rows whose heartbeat is newer than that cutoff and returns their ids as strings. The result is the set of executors considered alive.

**Call relations**: ExecutorRecovery.sweep calls this alongside ExecutorRecovery._pending_executors. The sweep subtracts this live set from the pending set before invoking DBOS recovery.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `CancelReconciler.run`  (lines 186–192)

```
async def run(self) -> None
```

**Purpose**: This is the repeating loop that makes cancellation spread through dependent turns. It ensures cancellation is eventually applied even if the original canceller did not or could not walk the whole tree immediately.

**Data flow**: It uses the configured interval and DBOS client on the CancelReconciler object. Each cycle waits, calls CancelReconciler.sweep, logs database or DBOS errors, and repeats. Its effect is eventual cleanup of live descendant turns under cancelled ancestors.

**Call relations**: Every serve process can run this loop. It delegates one reconciliation pass to CancelReconciler.sweep, which finds and cancels the affected turns.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 194–201)

```
async def sweep(self) -> None
```

**Purpose**: This performs one cancellation reconciliation pass. It finds non-finished turns that depend on a cancelled ancestor and cancels them one by one.

**Data flow**: It builds and runs the orphan query in a database transaction. For each returned turn id and workspace id, it enters that workspace context, calls cancel_one_turn through the DBOS client, and logs if the turn was actually cancelled. The database changes happen through cancel_one_turn, not directly in this function.

**Call relations**: CancelReconciler.run calls this periodically. This function uses CancelReconciler._orphans_query to identify targets, ws to switch into the right workspace, and cancel_one_turn to apply the same cancellation primitive used elsewhere.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (owner_tx, log, cancel_one_turn, ws).


##### `CancelReconciler._orphans_query`  (lines 203–243)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: This builds the database query that finds live turns with a cancelled dependent ancestor. It is the search map used by the cancellation sweep.

**Data flow**: It starts from all non-terminal turns, then recursively walks upward through dependent parent links. If the walk reaches a cancelled ancestor, the original live turn is selected with its workspace id. The output is a SQLAlchemy Select object, which is a database query description rather than immediate data.

**Call relations**: CancelReconciler.sweep calls this and then executes the query. This function calls CancelReconciler._dependent_parent to decide which parent links count as cancellation-dependent and which should stop the climb.

*Call graph*: calls 1 internal fn (_dependent_parent); called by 1 (sweep); 1 external calls (select).


##### `CancelReconciler._dependent_parent`  (lines 245–255)

```
def _dependent_parent(self, turn: sa.Table | sa.FromClause) -> sa.ColumnElement
```

**Purpose**: This defines which parent relationship should carry cancellation upward for the orphan search. It keeps independent spawned agents from being cancelled just because their spawner was cancelled.

**Data flow**: It receives a turn table or alias. It builds a SQL expression: return the parent_turn_id when the turn is a dependent subagent turn or an admitted intent, otherwise return null. The result becomes part of the recursive cancellation query.

**Call relations**: CancelReconciler._orphans_query uses this helper while building both the starting query and the recursive parent climb. It is the small rule that shapes the whole cancellation boundary.

*Call graph*: called by 1 (_orphans_query); 3 external calls (case, null, or_).


##### `StrandedTurnReconciler.run`  (lines 289–295)

```
async def run(self) -> None
```

**Purpose**: This is the repeating loop that looks for running turns whose workflow can no longer advance them. It prevents conversation state from being stuck forever on work that has no active carrier.

**Data flow**: It uses the configured interval and DBOS client on the StrandedTurnReconciler object. Each cycle waits, calls StrandedTurnReconciler.sweep, logs database or DBOS errors, and repeats. Its ongoing effect is to cancel turns that are truly stranded.

**Call relations**: Every serve process can run this loop. It delegates each pass to StrandedTurnReconciler.sweep, which performs the actual scan and cancellation decisions.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `StrandedTurnReconciler.sweep`  (lines 297–313)

```
async def sweep(self) -> None
```

**Purpose**: This performs one pass looking for running turns whose recorded workflow attempt is no longer active. It cancels those turns because nothing is left that can finish them normally.

**Data flow**: It runs the claimed-turn query to get old RUNNING turns with a running_attempt value. It logs if the scan hits its limit. It asks DBOS which of those attempts are still advancing; for any row whose attempt is not in that active set, it enters the row's workspace, calls cancel_one_turn, and logs if cancellation happened.

**Call relations**: StrandedTurnReconciler.run calls this periodically. It uses StrandedTurnReconciler._claimed_query to find possible stranded rows and StrandedTurnReconciler._advancing_attempts to separate still-live workflow attempts from dead or missing ones.

*Call graph*: calls 2 internal fn (_advancing_attempts, _claimed_query); called by 1 (run); 4 external calls (owner_tx, log, cancel_one_turn, ws).


##### `StrandedTurnReconciler._claimed_query`  (lines 315–332)

```
def _claimed_query(self) -> sa.Select
```

**Purpose**: This builds the database query for old running turns that have claimed a workflow attempt. These are the only turns the stranded-turn sweep should inspect.

**Data flow**: It calculates a cutoff time using the current time minus the grace window. It builds a query for turns with status RUNNING, a non-empty running_attempt, and an updated_at older than the cutoff, ordered oldest first and capped at the scan limit. The output is a SQLAlchemy Select object to be executed by the sweep.

**Call relations**: StrandedTurnReconciler.sweep calls this before reading candidate rows. The grace window in this query protects very recent claims from being mistaken for stranded work.

*Call graph*: called by 1 (sweep); 3 external calls (now, timedelta, select).


##### `StrandedTurnReconciler._advancing_attempts`  (lines 334–345)

```
async def _advancing_attempts(self, attempts: list[str]) -> set[str]
```

**Purpose**: This asks DBOS which workflow attempts from a given list are still able to move forward. It prevents the sweep from cancelling a turn whose workflow is merely waiting or delayed.

**Data flow**: It receives a list of workflow attempt ids. If the list is empty, it returns an empty set without querying DBOS. Otherwise it asks DBOS for workflows with those ids in advancing states such as pending, enqueued, or delayed, then returns the workflow ids found.

**Call relations**: StrandedTurnReconciler.sweep calls this after collecting candidate running turns. The sweep uses the returned set as a safety filter: attempts in the set are left alone, and only missing or non-advancing attempts are cancelled.

*Call graph*: called by 1 (sweep).


### Report digest generation
These files define concise report digest entries and generate them from recently published scheduled reports while avoiding repeated work on unchanged reports.

### `extensions/report_digest/ufo_ext_report_digest/digest.py`

`domain_logic` · `digest creation`

This file is the “quality gate” for report digests. A report may be long, but the digest should be only the smallest useful preview: a title, a short summary, and up to two distinct points. Without this file, digest entries could become too long, repeat themselves, include malformed data from a model provider, or charge an external text-writing API for more report text than needed.

The file uses Pydantic models, which are Python classes that validate and clean data as it is created. `DigestPoint` represents one finding line and an optional actor, meaning the person, group, or system the report says did something. `DigestEntry` represents the whole digest for one report.

The main rules are practical. Text fields are clipped to fixed lengths, but on word boundaries so they do not end in broken words. Titles are cut before a semicolon because the title is meant to carry only the top finding. Points and summaries are checked against what has already been said. If a line mostly repeats the title or summary, it is removed. Think of this like packing a small lunchbox: every item must earn its space.

The file also provides `bounded`, which limits how much of a report the digest writer may read, and `writing_standard`, which assembles the instructions given to the writer from prompt and skill files.

#### Function details

##### `_clipped`  (lines 96–107)

```
def _clipped(value: str, ceiling: int) -> str
```

**Purpose**: Shortens a piece of prose to a maximum length without rejecting the whole digest. It cuts at a word boundary so the result still looks intentional and readable.

**Data flow**: It receives a text value and a character limit. It trims surrounding spaces, checks whether the text already fits, and if not, cuts it down before the limit and removes any dangling punctuation or separators. It returns the cleaned, shortened string.

**Call relations**: The field validators for digest titles, summaries, point text, and actors call this helper whenever those fields are created. It gives all prose fields the same gentle trimming behavior instead of letting one overlong field spoil the whole entry.

*Call graph*: called by 4 (_summary, _title, _actor, _text).


##### `_stem`  (lines 110–117)

```
def _stem(word: str) -> str
```

**Purpose**: Reduces a word to a short root-like form so similar word forms can be compared as the same idea. For example, this helps treat related words like a repeated concept rather than as totally new text.

**Data flow**: It receives one word. It focuses on the last part after a hyphen when that part is long enough, removes known endings such as plural or past-tense endings when safe, then returns only the first few characters of the result. The output is a compact comparison key for that word.

**Call relations**: `_content` calls this for every meaningful word it finds. The digest repetition check depends on these shortened forms to notice when a later line is mostly saying what an earlier line already said.

*Call graph*: called by 1 (_content).


##### `_content`  (lines 120–121)

```
def _content(text: str) -> tuple[str, ...]
```

**Purpose**: Pulls out the meaningful words from a piece of text for comparison. It ignores common filler words such as “the” and “and,” then normalizes the remaining words so repetition is easier to spot.

**Data flow**: It receives a text string. It lowercases the text, finds word-like pieces, skips stopwords, and passes each remaining word through `_stem`. It returns a tuple of compact word roots that represent the content of the text.

**Call relations**: `_adds_to` uses this to judge whether a line contains new information. `DigestEntry._said_once` also uses it to remember what the title and surviving lines have already said.

*Call graph*: calls 1 internal fn (_stem); called by 2 (_said_once, _adds_to).


##### `_adds_to`  (lines 124–126)

```
def _adds_to(text: str, said: set[str]) -> bool
```

**Purpose**: Decides whether a line adds enough new information to be worth keeping. It protects the digest from wasting a row on a sentence that mostly repeats earlier text.

**Data flow**: It receives a text string and a set of content words that have already appeared. It turns the new text into content words, counts how many were not already known, and compares that share against the minimum novelty rule. It returns true if the line is meaningfully new, otherwise false.

**Call relations**: `DigestEntry._said_once` calls this while reading the digest from top to bottom. It is the small decision-maker that tells the entry validator whether the summary or each point earns its place.

*Call graph*: calls 1 internal fn (_content); called by 1 (_said_once).


##### `DigestPoint._text`  (lines 139–140)

```
def _text(cls, value: str) -> str
```

**Purpose**: Keeps the text of a single digest point within the allowed length. This makes each finding fit the compact digest format.

**Data flow**: It receives the proposed point text during `DigestPoint` validation. It sends that text to `_clipped` with the point-text limit, then stores the shortened readable result as the point’s text.

**Call relations**: Pydantic calls this validator when a `DigestPoint` is built. It relies on `_clipped` so point text follows the same word-safe trimming rule used elsewhere in the digest.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestPoint._actor`  (lines 144–145)

```
def _actor(cls, value: str) -> str
```

**Purpose**: Keeps the actor field short enough for the digest. The actor is optional, but when present it should not crowd out the finding.

**Data flow**: It receives the proposed actor text during `DigestPoint` validation. It clips the text to the actor length limit and returns the cleaned value that will be stored on the point.

**Call relations**: Pydantic calls this validator when creating a `DigestPoint`. It uses `_clipped`, matching the same readable shortening behavior used for point text, titles, and summaries.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._decoded`  (lines 161–167)

```
def _decoded(cls, value: object) -> object
```

**Purpose**: Accepts point data even when a provider returns it as JSON text instead of a normal nested list. This makes the digest parser tolerant of a common formatting mistake from external model services.

**Data flow**: It receives the raw value supplied for `points` before normal validation. If the value is a string, it parses it as JSON. If that parsed value is a dictionary containing `points`, it extracts that field; otherwise it uses the parsed value itself. Non-string values pass through unchanged.

**Call relations**: Pydantic calls this before validating the `points` field of a `DigestEntry`. It hands cleaned point input onward so the normal `DigestPoint` validation can proceed instead of failing only because the provider wrapped the data oddly.

*Call graph*: 1 external calls (loads).


##### `DigestEntry._title`  (lines 171–172)

```
def _title(cls, value: str) -> str
```

**Purpose**: Cleans and limits the digest title. It keeps only the first finding before a semicolon and makes sure the title fits the expected short headline length.

**Data flow**: It receives the proposed title. It splits the title at the first semicolon and keeps the part before it, then sends that text to `_clipped` with the title limit. It returns the final title to store on the entry.

**Call relations**: Pydantic calls this when validating a `DigestEntry`. It uses `_clipped` so titles stay readable, and it enforces the file’s rule that the title should not try to carry multiple findings.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._summary`  (lines 176–177)

```
def _summary(cls, value: str) -> str
```

**Purpose**: Keeps the digest summary short enough to be a single compact clause. This helps the digest stay skimmable.

**Data flow**: It receives the proposed summary text. It clips the summary to the summary length limit using `_clipped`, then returns the cleaned summary for the entry.

**Call relations**: Pydantic calls this during `DigestEntry` validation. Later, `DigestEntry._said_once` may remove the summary entirely if it does not add enough new information beyond the title.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._titled`  (lines 180–183)

```
def _titled(self) -> 'DigestEntry'
```

**Purpose**: Makes sure any digest entry that claims there is a real change has a title. A change without a title would give the reader no useful hook for deciding whether to open the report.

**Data flow**: It receives the fully built `DigestEntry` after field validation. It checks whether `holds_a_change` is true and the title is empty. If so, it raises an error; otherwise it returns the entry unchanged.

**Call relations**: Pydantic calls this after the individual fields have been cleaned. It acts as a final consistency check before the entry is accepted.


##### `DigestEntry._said_once`  (lines 186–205)

```
def _said_once(self) -> 'DigestEntry'
```

**Purpose**: Removes summary and point lines that mostly repeat what the reader has already seen. It keeps the digest dense: title first, then only genuinely new supporting lines.

**Data flow**: It starts with the content words from the title as already known. It checks whether the summary adds enough new words; if not, it clears the summary. Then it walks through the proposed points in order, keeping only points that add enough new information, and stops after the maximum number of points. It updates the entry’s summary and points, then returns the entry.

**Call relations**: Pydantic calls this after a `DigestEntry` has been created and basic fields have been validated. It uses `_content` to remember what has already been said and `_adds_to` to decide whether each later line deserves space.

*Call graph*: calls 2 internal fn (_adds_to, _content).


##### `bounded`  (lines 208–210)

```
def bounded(report: str) -> str
```

**Purpose**: Limits how much of a long report is sent to the digest writer. This keeps the digest based on the front part of the report and controls the amount of text sent to an external service.

**Data flow**: It receives the full report text. It takes only the first fixed number of characters and returns that shortened report text. It does not change the original report.

**Call relations**: Code that prepares a report for digest writing can call this before sending text to the writer. It is a simple boundary line between the full report and the smaller input the digest process is allowed to read.


##### `writing_standard`  (lines 213–227)

```
def writing_standard() -> str
```

**Purpose**: Builds the instruction text used by the digest writer. It combines the digest prompt, the delivery rules, and the skill instructions into one standard that tells the writer how to produce entries.

**Data flow**: It reads the skill file from disk, removes its frontmatter section, reads the digest prompt file, adds the shared delivery register text, and joins these pieces with blank lines. It returns the combined instruction string.

**Call relations**: The digest-writing setup calls this when it needs the exact rules to give to the writer. It pulls in prompt and skill files from disk and includes `DELIVERY_REGISTER_BLOCK` so generated digest entries follow the same delivery standard as the rest of the system.


### `extensions/report_digest/ufo_ext_report_digest/writer.py`

`domain_logic` · `scheduled background tick and rebuild maintenance`

This file is the background writer for report digests. Scheduled app runs can publish Markdown reports, but raw reports may be long and hard to scan. The writer looks for recent finished scheduled runs that shared a Markdown report and do not already have a digest row. For each one, it reads a limited amount of the report from blob storage, asks the model to produce a structured digest, and stores the result in the database.

The file is careful about cost and fairness. It only processes a small batch per tick, so switching the feature on for a workspace with many old reports does not spend everything at once. It only looks back seven days, so a broken or missing report cannot block the job forever. It also records “unchanged” reports in a separate table. That is like putting a sticky note on a document saying “already checked; nothing new here,” so future ticks can skip it.

The main class, DigestWriter, performs one scheduled pass: find due reports, read each body, decide who the digest is written for, ask the model for a DigestEntry, then store either the entry or the unchanged marker. DigestRebuild is a reset tool: it deletes recent digest rows and unchanged markers so they can be recreated, for example after the digest-writing rules change. The standalone undigested_workspaces query helps the scheduler find only workspaces that actually have work waiting.

#### Function details

##### `DigestWriter.run`  (lines 117–126)

```
async def run(self) -> None
```

**Purpose**: Runs one digest-writing pass. It finds reports that still need digest work and tries to process each one without letting one bad report stop the rest.

**Data flow**: It starts with the writer’s context, model access, and blob store. It asks _unwritten for the current batch of due reports, then sends each report to _digest. If any single report fails because its blob is gone, the model refuses, or something else goes wrong, the error is swallowed and the loop moves on, leaving that report for a future tick while later reports still get a chance.

**Call relations**: This is the top-level method for the writer’s scheduled pass. It first calls DigestWriter._unwritten to learn what work exists, then calls DigestWriter._digest for each report in that batch.

*Call graph*: calls 2 internal fn (_digest, _unwritten).


##### `DigestWriter._digest`  (lines 128–139)

```
async def _digest(self, report: Report) -> None
```

**Purpose**: Processes one report from start to finish. It reads the report, asks the model for a digest, and stores either the digest or a note that the report contained no change worth summarizing.

**Data flow**: A Report object goes in. The method reads its body with _body; if the body cannot be read, it stops. It builds a human description of the intended reader with _reader, sends the body and reader to _written, then looks at the returned DigestEntry. If the entry says the report has a real change, _store writes it to the digest table. If it says there is no change, _store_unchanged records that this report has already been checked.

**Call relations**: DigestWriter.run calls this once for each candidate report. This method is the central handoff point between reading from blob storage, asking the model, and writing the database result.

*Call graph*: calls 5 internal fn (_body, _reader, _store, _store_unchanged, _written); called by 1 (run).


##### `DigestWriter._unwritten`  (lines 141–213)

```
async def _unwritten(self) -> tuple[Report, ...]
```

**Purpose**: Finds the reports that are due to be digested in this tick. It returns only recent, successful scheduled runs that published a Markdown report and have not already been digested or marked unchanged.

**Data flow**: It reads the workspace id from the extension context and builds a database query. The query joins turns, conversations, agents, members, and shared artifacts so each result includes the report’s turn id, blob key, app name, audience, and owner email. It filters out old reports, failed runs, non-Markdown artifacts, reports that already have digest entries, and reports already marked unchanged. The output is a tuple of Report objects, limited to the batch size.

**Call relations**: DigestWriter.run calls this at the start of each tick. The Report objects it returns become the input to DigestWriter._digest.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `DigestWriter._body`  (lines 215–228)

```
async def _body(self, report: Report) -> str | None
```

**Purpose**: Reads the Markdown report text from blob storage, while enforcing size limits so very large files do not overload the process or the model request.

**Data flow**: A Report goes in, mainly for its blob key. The method streams bytes from the blob store in chunks, stops once it reaches the read ceiling, decodes the bytes into text, and then passes the text through bounded to enforce the model-facing character limit. If the blob is missing, it returns None instead of raising an error.

**Call relations**: DigestWriter._digest calls this before doing any model work. Its output is the report text that DigestWriter._written will later send to the model.

*Call graph*: called by 1 (_digest); 1 external calls (bounded).


##### `DigestWriter._reader`  (lines 230–239)

```
def _reader(self, report: Report) -> str
```

**Purpose**: Builds a plain-language description of who the digest is meant for. This helps the model write a summary with the right audience in mind.

**Data flow**: A Report goes in with its audience, owner email, and app name. If the report belongs to a specific member conversation and an owner email is available, the method returns a sentence naming that member as the reader. Otherwise, it returns a workspace-level reader description. The output is just text.

**Call relations**: DigestWriter._digest calls this after reading the body and before asking the model to write the digest. The reader text is passed into DigestWriter._written and later stored with the digest entry.

*Call graph*: called by 1 (_digest).


##### `DigestWriter._written`  (lines 241–278)

```
async def _written(self, body: str, reader: str) -> DigestEntry | None
```

**Purpose**: Asks the language model to turn a report into a structured DigestEntry. It only accepts the model’s answer if it comes back through the expected tool-shaped response, which is a structured format rather than loose prose.

**Data flow**: The report body and reader description go in. The method creates a ModelRequest containing the digest-writing instructions, a JSON message with the report and reader, a token limit, and a required tool schema based on DigestEntry. It sends that request through the model. If the model replies as ordinary text, the method returns None. If the model calls the expected finish tool, the tool input is validated as a DigestEntry and returned.

**Call relations**: DigestWriter._digest calls this after preparing the report body and reader. This method relies on writing_standard for the instruction text and DigestEntry’s schema and validation rules to keep the model output in a reliable shape.

*Call graph*: called by 1 (_digest); 7 external calls (__init__, __init__, __init__, model_json_schema, model_validate, dumps, writing_standard).


##### `DigestWriter._store_unchanged`  (lines 280–286)

```
async def _store_unchanged(self, report: Report) -> None
```

**Purpose**: Records that a report was read and found to contain no meaningful change. This prevents the same quiet report from being reread and billed again on every scheduled tick.

**Data flow**: A Report goes in. The method opens a database transaction and inserts the workspace id and turn id into the report_digest_unchanged table. It does not return a value; the lasting effect is the new database row.

**Call relations**: DigestWriter._digest calls this when the model returns a valid digest result whose holds_a_change flag is false. Future calls to DigestWriter._unwritten and undigested_workspaces use this marker to skip the report.

*Call graph*: called by 1 (_digest); 1 external calls (insert).


##### `DigestWriter._store`  (lines 288–301)

```
async def _store(self, report: Report, entry: DigestEntry, reader: str) -> None
```

**Purpose**: Writes a finished digest entry into the database. This is what makes the summarized report available to the feed or other readers.

**Data flow**: A Report, a DigestEntry, and the reader text go in. The method opens a database transaction and inserts a row containing the workspace id, turn id, digest title, summary, bullet points, reader description, model name, and current write time. It returns nothing; the database row is the output.

**Call relations**: DigestWriter._digest calls this when the model says the report contains a change worth showing. Future candidate searches then see that this turn already has an entry and skip it.

*Call graph*: called by 1 (_digest); 2 external calls (now, insert).


##### `DigestRebuild.run`  (lines 321–339)

```
async def run(self) -> int
```

**Purpose**: Clears recent digest rows and unchanged markers so the writer can recreate them. This is useful when the digest rules or model prompt change and recent reports should be summarized again.

**Data flow**: It reads the workspace id from the context and defines the same seven-day window used by the writer. Inside a transaction, it deletes digest entries for turns in that window, then deletes unchanged markers for the same set of turns. It returns the total number of deleted rows, combining both deletes.

**Call relations**: This method is separate from the normal writer tick. After it runs, DigestWriter._unwritten can see those recent reports as due again, so later DigestWriter.run calls rebuild their digest state.

*Call graph*: 3 external calls (now, delete, select).


##### `undigested_workspaces`  (lines 342–366)

```
def undigested_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds a database query that finds workspaces with at least one report waiting for digest work. A scheduler can use this to avoid waking the writer for quiet workspaces.

**Data flow**: No live database rows are read inside the function itself; it returns a SQLAlchemy Select object, which is a database query description. The query looks for recent successful scheduled turns with Markdown artifacts, then excludes turns that already have a digest entry or an unchanged marker. The result, when executed elsewhere, is a grouped list of workspace ids that have pending digest work.

**Call relations**: This is a helper for the broader scheduling flow rather than for DigestWriter.run directly. It mirrors the writer’s own due-work rules so scheduling and actual processing agree about what counts as undigested.

*Call graph*: 2 external calls (now, select).

## 📊 State Registers Touched

- `reg-extension-registry` — The live catalog of installed extensions and the capabilities each one has registered.
- `reg-database-schema` — The durable database layout and connection layer used to store and retrieve system records safely.
- `reg-workspace-directory` — The saved list of workspaces, members, agents, admins, and workspace-level settings.
- `reg-acting-authority` — The shared record of whether work is acting as a member, an agent, or only the workspace.
- `reg-billing-ledger` — The shared meter and wallet state for usage costs, spend caps, prepaid balances, and billing identity.
- `reg-turn-queue` — The durable queue of conversation turns waiting, running, parked, resumed, or blocked as duplicates.
- `reg-live-workflow-state` — The shared run-state for active turns, including locks, progress, retry guards, cancellation, and completion markers.
- `reg-source-index-memory` — The saved external pages, search chunks, embeddings, memories, and recall indexes used as workspace knowledge.
- `reg-scheduled-jobs` — The durable background work list for timers, recurring conversations, monitors, reports, and long-running tasks.
- `reg-surface-routing` — The saved routing state that maps web, Slack, iMessage, terminal, and other surfaces to workspaces and agents.
- `reg-inbound-message-queue` — The durable inbox of external messages waiting to be admitted into conversations exactly once.
- `reg-object-system` — The common address book and audit trail for durable workspace objects such as agents, members, artifacts, and connectors.
- `reg-extension-store` — The per-workspace storage area where extensions keep their own durable settings and small JSON records.
- `reg-runtime-instances` — The shared record of which server processes are alive and which background or surface duties they have claimed.
- `reg-observability-trace` — The tracing, metrics, health, logs, and saved step history used to understand what the system did.
- `reg-conversation-slots-ui` — The shared side-panel and workspace UI state for artifacts, sources, tasks, sites, automations, and app home screens.
- `reg-source-sync-state` — The source-ingestion control state: source definitions, cursors/change-feed positions, error counters, backoff or parked status, and removal markers.
- `reg-objective-state` — The durable goal/objective records holding plans, steps, evidence, blockers, and progress used by objective tools and background follow-up.
- `reg-self-improvement-state` — The saved failure cases, prompt-change proposals, evaluation results, and approval status used by the self-improvement loop.
- `reg-ledger-export-state` — Saved progress and options for exporting billing/ledger records, including BYOK-related export bookkeeping.
- `reg-runtime-message-bus` — Shared Redis/pub-sub or message-hub connection state used to coordinate live updates, workers, and cross-process runtime events.
