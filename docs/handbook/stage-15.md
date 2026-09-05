# Scheduled jobs, automations, and asynchronous apps  `stage-15`

This stage is the system’s background shift. It runs work that should happen later, repeat regularly, or continue after a user has stopped waiting. The durable schedules and wake-ups pieces act like an alarm clock: they store future tasks, pauses, monitors, source-change triggers, and notification inbox items, then let only one worker claim each due item. The job runtime decides which workspaces have pending work, keeps each job inside that workspace’s safety boundary, and runs both built-in and extension-declared jobs without duplicates.

Several extensions plug into this loop. Scheduled-task runners fire due tasks and pauses. Monitor tools set up watches on outside systems, and the monitor runner checks them until something changes, fails, or expires. Notification drain and delivery turn stored app updates into controlled chat messages. Report digest code summarizes newly published reports once. Preview rendering retries missing document thumbnails. Homepage cleanup removes an old seeded page only when the chat app has replaced it.

Finally, the offline improvement loop studies past failures, replays saved conversations with proposed instruction changes, and opens only cautious, human-reviewable improvements.

## Sub-stages

- [Durable schedules and wake-ups](stage-15.1.md) `stage-15.1` — 7 files
- [Offline evaluation and improvement loops](stage-15.2.md) `stage-15.2` — 8 files

## Files in this stage

### Job orchestration runtime
Core runtime files discover workspace-scoped pending work and execute scheduled jobs safely across core features and extensions.

### `core/src/ufo/runtime/candidates.py`

`domain_logic` · `job scheduling / dispatcher candidate lookup`

Jobs in this system are not supposed to run in a vague, global context. They must run inside a specific workspace, so tenant data stays separated. This file exists to answer one narrow question before a job runs: “Which workspaces might have work ready?”

Normally, database reads are protected by row-level security, or RLS, which means the database only shows rows belonging to the current workspace. But finding candidate workspaces requires a special cross-workspace read. This file makes that exception small and controlled. It reads only workspace IDs, not the actual job data. Think of it like looking at mailbox labels in an apartment building, not opening anyone’s mail.

The main helper, `owner_candidates`, lets an extension provide a query builder. That builder creates a database query that selects distinct workspace IDs from the extension’s own tables. The query is built fresh each time the scheduler checks for work, so time-based rules like “due before now” use the current time instead of a stale time captured at startup.

The returned `candidates` function runs that query through `owner_tx`, the privileged database path that can see across workspaces. It then returns just the first column from each row as a tuple of workspace UUIDs. The dispatcher can then enter each workspace safely before running the actual job handler.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: Turns a caller-provided database query builder into a reusable candidate finder for jobs. It gives extensions a safe way to say which workspaces may have pending work without giving them direct access to the privileged cross-workspace database connection.

**Data flow**: It receives `due`, a no-argument function that builds a SQL query selecting workspace IDs. It wraps that builder in an async `candidates` function. The result is a callable that, when later run, will build the fresh query, execute it safely through the core-owned privileged path, and return only workspace UUIDs.

**Call relations**: This is the outer setup step. An extension or core job supplies the query-building recipe, and `owner_candidates` packages it into the shape the dispatcher expects: an async function returning workspace IDs. The actual database read is deferred to the inner `owner_candidates.candidates` function so it happens each time the scheduler checks for due work.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Runs the candidate query and returns the workspace IDs it names. This is the carefully limited place where the system uses the privileged cross-workspace read path.

**Data flow**: It starts with no direct arguments, but it closes over the `due` query builder from `owner_candidates`. Each time it runs, it asks `due()` to build a current SQL query, opens `owner_tx()` to get a privileged database connection, executes the query, collects all rows, and returns a tuple containing the first value from each row, which should be a workspace UUID. It does not return the underlying tenant rows or job data.

**Call relations**: This function is the candidate finder that the dispatcher can call before running a job. Inside, it calls `ufo.db.owner_tx` because this one read must see across workspaces. After it returns IDs, the dispatcher is expected to bind and run the real handler separately inside each named workspace, so the privileged read is not used for the actual work.

*Call graph*: 1 external calls (owner_tx).


### `core/src/ufo/runtime/jobs.py`

`orchestration` · `startup and scheduled background execution`

This file is the background-job traffic controller for the runtime. At startup, jobs are discovered from the core system and from installed extensions, then registered with DBOS, a durable workflow system that stores work so it can survive crashes and retries. Without this file, source syncing, page-change hooks, queued turn recovery, result delivery, product census, previews, and extension jobs would not reliably run.

The design has two stages. A small scheduled “tick” asks, “Which workspaces actually have work for this job?” Then it queues one durable execution per matching workspace. This matters because one slow or stuck workspace should not block every other workspace, and repeated ticks should not pile up duplicate copies of the same job.

The file also protects ordering and safety. Turn dispatch only starts a turn when no earlier turn in the same conversation is ahead of it. Page-change hooks each keep their own cursor, like a bookmark in a book, so every consumer resumes where it left off. Job handlers always run inside a specific workspace context, so they see the right tenant data and credentials. If a job is refused because spending limits block model use, the file treats that as a pause, not a crash, and tries to tell the member once.

#### Function details

##### `ResultDeliverer.run`  (lines 105–105)

```
async def run(self) -> None
```

**Purpose**: This protocol method describes the work needed to sweep finished child-agent results back into their parent conversations. It is a contract: anything used as a result deliverer must provide this operation.

**Data flow**: The caller provides no direct data. An implementation is expected to read finished child results from the system, post whatever arrivals are due, and finish without returning a value.

**Call relations**: Core job setup wraps this method in a scheduled job. The actual implementation lives outside this file, so this file can schedule the sweep without importing the turn-loop internals.


##### `ResultDeliverer.candidate_workspaces`  (lines 107–107)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This protocol method identifies which workspaces have result-delivery work waiting. It lets the job runner avoid opening workspaces that have nothing to do.

**Data flow**: The caller provides no direct data. An implementation checks its own storage or state and returns workspace IDs where finished child results may need delivery.

**Call relations**: The core result-delivery job uses this as its candidate finder. JobRunner later fans the job out only to the returned workspaces.


##### `TurnDispatcher.run`  (lines 176–212)

```
async def run(self) -> None
```

**Purpose**: This scans for turns that are ready to be offered to the turn-processing queues. It also rechecks parked turns against seats, spending rules, and balance rules before letting them run.

**Data flow**: It reads dispatchable turn records from the current workspace. For parked turns, it gathers the relevant members, checks whether they have seats, asks the spend evaluator and balance gate whether the work is allowed, and skips blocked turns. Ready turns are passed to the enqueue step; nothing is returned.

**Call relations**: A scheduled core job calls this through core_jobs. It first asks _dispatchable_turns for safe candidates, then hands each approved turn to _enqueue so DBOS can run the turn workflow.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 7 external calls (__init__, __init__, __init__, select, workspace_tx, authority_member_id, turn_authority).


##### `TurnDispatcher.candidate_workspaces`  (lines 214–222)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This finds workspaces that have queued or parked turns worth checking. It is the broad fleet-level filter before running the dispatcher inside each workspace.

**Data flow**: It reads the owner-level database view, builds the same eligibility test used by the dispatcher, and returns distinct workspace IDs that contain eligible turns.

**Call relations**: JobRunner calls this during a turn-dispatch tick. The returned workspaces are then scheduled separately, so each workspace dispatch pass runs in its own workspace context.

*Call graph*: calls 1 internal fn (_eligible); 3 external calls (now, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 224–265)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: This selects the actual turn rows that can be considered for dispatch in the current workspace. It keeps the batch bounded so one run does not try to process unlimited work.

**Data flow**: It reads turns joined with their conversation data, filters them with _eligible, orders queued turns before parked ones and older turns before newer ones, then converts database rows into _DispatchTurn objects.

**Call relations**: TurnDispatcher.run calls this first. The resulting turn objects carry the information run needs for seat and spend checks and for later enqueueing.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 5 external calls (__init__, now, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 267–298)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: This claims one turn for dispatch and offers it to the right DBOS queue. The database claim prevents two sweepers from offering the same turn at the same time.

**Data flow**: It receives a _DispatchTurn, checks that the row is still in the same state, still due, still stale enough to retry, and still first in order. If the update succeeds, it builds enqueue options, chooses a workflow ID, and queues the turn workflow. If the row was already claimed or no longer eligible, it does nothing.

**Call relations**: TurnDispatcher.run calls this after a turn passes any parked-turn gates. It relies on _first_in_status, _retry_due, and _stale to repeat the safety checks at the moment of claiming.

*Call graph*: calls 3 internal fn (_first_in_status, _retry_due, _stale); called by 1 (run); 6 external calls (now, timedelta, update, workspace_tx, turn_queue_for, uuid4).


##### `TurnDispatcher._eligible`  (lines 300–321)

```
def _eligible(self, now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for turns that may be dispatched. It enforces the rule that one conversation runs only one turn at a time and later turns cannot overtake earlier ones.

**Data flow**: It receives the current time and returns a SQL condition. The condition allows queued or parked turns that are stale, due for retry, first among turns of their status, and have no running sibling in the same conversation.

**Call relations**: candidate_workspaces uses this to find workspaces with possible work, and _dispatchable_turns uses it to fetch the concrete turns. It combines helper conditions from _stale, _retry_due, and _first_in_status.

*Call graph*: calls 3 internal fn (_first_in_status, _retry_due, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 5 external calls (timedelta, and_, exists, or_, select).


##### `TurnDispatcher._retry_due`  (lines 323–324)

```
def _retry_due(self, now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition that says a turn is no longer waiting for a future retry time. It keeps timed parks invisible until their retry time arrives.

**Data flow**: It receives the current time and returns a SQL condition accepting rows with no retry time or a retry time at or before now.

**Call relations**: _eligible uses it during scans, and _enqueue uses it again during the final claim so a race cannot enqueue a turn that became not-due.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._stale`  (lines 326–330)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for turns whose previous dispatch offer is missing or old enough to retry. It is the recovery path for a process that stamped a row but failed before enqueueing it.

**Data flow**: It receives a cutoff time and returns a SQL condition accepting rows with no dispatch timestamp or one older than the cutoff.

**Call relations**: _eligible uses it when searching for dispatchable work, and _enqueue repeats it while claiming the row.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 332–341)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition that a turn has no earlier turn in the same conversation with the same status. It protects turn order.

**Data flow**: It receives a turn status and returns a SQL condition that rejects a row if another row in the same workspace and conversation has that status with a lower sequence number.

**Call relations**: _eligible uses it to decide which queued or parked rows can be considered. _enqueue uses it again before claiming, so an earlier turn inserted or changed by another worker is respected.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `_page_beyond_cursor`  (lines 344–349)

```
def _page_beyond_cursor(revision: int, page_id: UUID, cursor: object) -> bool
```

**Purpose**: This answers whether a page change is newer than a stored cursor. The cursor acts like a bookmark showing how far a page-change consumer has already read.

**Data flow**: It receives a page revision, a page ID, and a stored cursor value. If there is no cursor, it returns true. Otherwise it parses the cursor and compares revision first, then page ID, returning whether the page lies after that bookmark.

**Call relations**: PageChangeRunner.workspaces_with_changes uses this while deciding which workspaces have page changes pending for a specific consumer.

*Call graph*: called by 1 (workspaces_with_changes); 1 external calls (page_cursor).


##### `PageChangeConsumer.spec_name`  (lines 370–372)

```
def spec_name(self) -> str
```

**Purpose**: This creates the unique job name for one page-change hook. The name separates consumers so two hooks do not share a schedule or cursor by accident.

**Data flow**: It reads the consumer's extension name and discriminator, then returns a string shaped like a page-change job name.

**Call relations**: core_jobs uses this property indirectly when it builds one JobSpec per page-change consumer.


##### `PageChangeConsumer.job`  (lines 375–380)

```
def job(self) -> str
```

**Purpose**: This creates the accounting key used when the core page-change runner executes an extension hook. It lets model cost and latency be attributed to the exact consumer.

**Data flow**: It reads the consumer's generated spec name and returns a core-namespaced job key string.

**Call relations**: PageChangeRunner._context_for uses this when building the extension context for a hook, so model calls inside that hook are labeled correctly.


##### `PageChangeRunner.consumers`  (lines 423–447)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: This discovers all registered page-change hooks from active extension manifests. It turns each hook into a PageChangeConsumer with its own name and cursor identity.

**Data flow**: It reads every manifest, collects credential slot names, filters hooks whose event is page_change, checks that two hooks in the same extension do not share the same handler name, and returns the consumers.

**Call relations**: core_jobs calls this at setup time to create one scheduled JobSpec per consumer. If duplicate handler names would collide, this function stops startup with a clear error.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 449–514)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: This finds the workspaces where a particular page-change consumer has unread page changes. It avoids running page-change work for tenants whose pages have not changed.

**Data flow**: It receives a PageChangeConsumer, reads that consumer's stored cursor per workspace, reads each workspace's newest page position, compares the newest page to the cursor, and returns workspace IDs that are behind. If one cursor is malformed, that workspace is treated as pending and a warning is logged.

**Call relations**: The candidate function created by core_jobs calls this before each page-change tick. It uses _page_beyond_cursor for the comparison and feeds the result to JobRunner for per-workspace fan-out.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 3 external calls (select, owner_tx, warn).


##### `PageChangeRunner.drive`  (lines 516–566)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: This runs one page-change consumer inside one workspace until it catches up or reaches the batch limit. It gives the consumer batches of changed pages and advances its cursor only after the handler succeeds.

**Data flow**: It builds an extension context, reads the stored cursor, fetches changed pages after that cursor, calls the consumer's hook with a PageChangeBatch, and then tries to update the cursor from the old value to the new one. On handler failure, it logs and counts the stalled consumer, leaves the cursor unchanged, and raises the error.

**Call relations**: The per-consumer handler made by core_jobs calls this. It uses _context_for to build the safe extension environment, and it is run by JobRunner inside a workspace binding.

*Call graph*: calls 1 internal fn (_context_for); 6 external calls (__init__, __init__, emit_metric, formatted_stack, log_error, ws_current).


##### `PageChangeRunner._context_for`  (lines 568–586)

```
def _context_for(self, consumer: PageChangeConsumer) -> ExtensionContext
```

**Purpose**: This builds the ExtensionContext used by a page-change hook. That context is the hook's toolbox: store, model access, page feed, blobs, index, and other runtime services.

**Data flow**: It reads the consumer details and the current workspace, optionally creates a turn invoker, swaps in the background model if configured, and returns a context tied to the consumer's extension and job key.

**Call relations**: PageChangeRunner.drive calls this before invoking a hook. It relies on _background_registry to make page-change model calls use the background-job model when appropriate.

*Call graph*: calls 1 internal fn (_background_registry); called by 1 (drive); 2 external calls (context_for, ws_current).


##### `_background_registry`  (lines 589–600)

```
def _background_registry(registry: ModelRegistry | None, background_model: str | None) -> ModelRegistry | None
```

**Purpose**: This returns a model registry adjusted for background jobs. If a background model is configured, it replaces the default automatic model with that background model.

**Data flow**: It receives an optional registry and optional background model name. If either is missing, it returns the registry unchanged; otherwise it returns a copied registry with its automatic model changed.

**Call relations**: PageChangeRunner._context_for and JobRunner.fire use this while building contexts, so background jobs do not accidentally use the same default model as member-facing turns unless requested.

*Call graph*: called by 2 (fire, _context_for); 1 external calls (replace).


##### `core_jobs`  (lines 603–709)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, page_change_runner: PageChangeRunner, delivery_sweep: ResultDeliverer, preview_renderer: PreviewRenderer | None) -> tuple[JobSpe
```

**Purpose**: This builds the list of built-in jobs that every deployment may run. It wraps core services, such as source sync and turn dispatch, into the same JobSpec shape used by extensions.

**Data flow**: It receives service objects such as the sync driver, turn dispatcher, page-change runner, delivery sweep, and optional preview renderer. It creates small handler and candidate functions around those services, discovers page-change consumers, and returns a tuple of JobSpec objects.

**Call relations**: Startup code can combine this output with extension job specs through bindings_from. The returned jobs are later registered and run by JobRunner.

*Call graph*: calls 1 internal fn (consumers); 2 external calls (__init__, seated_member_workspaces).


##### `core_jobs._sync_sources`  (lines 627–628)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: This small wrapper runs source synchronization as a job handler. It exists so the sync driver fits the standard JobSpec handler signature.

**Data flow**: It receives an ExtensionContext but does not need to read it. It calls the sync driver's run method and returns nothing.

**Call relations**: core_jobs installs this as the handler for the source-sync JobSpec. JobRunner eventually calls it inside each candidate workspace.


##### `core_jobs._dispatch_turns`  (lines 630–631)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs the turn dispatcher as a job handler. It lets scheduled turn recovery use the same job machinery as every other background task.

**Data flow**: It receives an ExtensionContext but does not use it. It calls the turn dispatcher's run method and returns nothing.

**Call relations**: core_jobs installs this on the turn-dispatch JobSpec. JobRunner calls it for each workspace returned by the dispatcher's candidate finder.


##### `core_jobs._deliver_results`  (lines 633–634)

```
async def _deliver_results(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs the result-delivery sweep as a job handler. It connects finished child-agent result delivery to the shared job runner.

**Data flow**: It receives an ExtensionContext but does not use it. It calls the delivery sweep's run method and returns nothing.

**Call relations**: core_jobs installs this on the result-delivery JobSpec. The implementation behind the ResultDeliverer protocol does the real turn-loop work.


##### `core_jobs._census_product`  (lines 636–638)

```
async def _census_product(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs the product and onboarding census jobs. These jobs count product usage and onboarding state from core data.

**Data flow**: It receives an ExtensionContext but does not use it. It runs product_census and onboarding_census, then returns nothing.

**Call relations**: core_jobs installs this on the product census JobSpec. JobRunner schedules it for candidate workspaces chosen by seated_member_workspaces.

*Call graph*: 2 external calls (onboarding_census, product_census).


##### `core_jobs._render_previews`  (lines 640–642)

```
async def _render_previews(context: ExtensionContext) -> None
```

**Purpose**: This wrapper runs preview rendering when a preview renderer is configured. It adapts the renderer to the standard job handler shape.

**Data flow**: It receives an ExtensionContext, checks that the renderer exists, calls the renderer's run method, and returns nothing.

**Call relations**: core_jobs includes this handler only when preview rendering is available. JobRunner later executes it for workspaces returned by the preview candidate function.


##### `core_jobs._preview_candidates`  (lines 644–646)

```
async def _preview_candidates() -> tuple[UUID, ...]
```

**Purpose**: This wrapper asks the preview renderer which workspaces need preview work. It exists so preview rendering can participate in the normal candidate-and-fan-out flow.

**Data flow**: It checks that a preview renderer exists, calls its candidate_workspaces method, and returns those workspace IDs.

**Call relations**: core_jobs attaches this as the candidate finder for the preview-rendering JobSpec. JobRunner calls it during each preview tick.


##### `core_jobs._drive_consumer`  (lines 648–654)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: This creates a job handler for one page-change consumer. It closes over the consumer so each generated JobSpec drives the correct hook and cursor.

**Data flow**: It receives a PageChangeConsumer and returns an async handler function. That handler will later call PageChangeRunner.drive for the captured consumer.

**Call relations**: core_jobs calls this once per page-change consumer while building JobSpecs. The returned _handler is what JobRunner eventually invokes.


##### `core_jobs._drive_consumer._handler`  (lines 651–652)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: This is the actual job handler produced for a specific page-change consumer. It runs that consumer's cursor loop in the current workspace.

**Data flow**: It receives an ExtensionContext but relies on the runner's workspace binding rather than the argument. It calls page_change_runner.drive with the captured consumer and returns nothing.

**Call relations**: JobRunner calls this through the JobSpec made by core_jobs. The heavy lifting is handed to PageChangeRunner.drive.


##### `core_jobs._consumer_candidates`  (lines 656–660)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: This creates a candidate finder for one page-change consumer. It lets each consumer skip workspaces where its own cursor is already up to date.

**Data flow**: It receives a PageChangeConsumer and returns an async candidate function. That function later asks PageChangeRunner which workspaces have changes for that consumer.

**Call relations**: core_jobs uses this beside _drive_consumer when creating page-change JobSpecs. JobRunner calls the returned _candidates function during ticks.


##### `core_jobs._consumer_candidates._candidates`  (lines 657–658)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: This is the actual candidate finder for a specific page-change consumer. It returns only workspaces where that consumer has unread page changes.

**Data flow**: It receives no direct data. It calls page_change_runner.workspaces_with_changes for the captured consumer and returns the workspace IDs.

**Call relations**: JobRunner calls this during a page-change tick. PageChangeRunner.workspaces_with_changes performs the database comparison work.


##### `bindings_from`  (lines 721–752)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...], disabled: frozenset[str]=frozenset()) -> tuple[_Binding, ...]
```

**Purpose**: This turns core jobs and extension jobs into runnable bindings with stable keys. A binding says which extension owns a job, which credentials it declared, and what JobSpec should run.

**Data flow**: It receives manifests, core JobSpecs, and an optional set of disabled job keys. It creates core bindings under the core namespace, extension bindings under each extension name, checks that every disabled key actually exists, removes disabled bindings, and returns the rest.

**Call relations**: Startup code uses this before creating a JobRunner. JobRunner later uses the binding keys to register schedules, find candidates, and build the right ExtensionContext.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 782–806)

```
def launch(self) -> None
```

**Purpose**: This registers all jobs with DBOS at startup. Scheduled jobs are published as cron-like schedules, while one-shot jobs are enqueued once with deduplication.

**Data flow**: It stores this runner in the module-level firing slot, walks every binding, and either enqueues an immediate tick for unscheduled jobs or creates a ScheduleInput for scheduled ones. It logs what happened and applies all schedules in one call.

**Call relations**: This is the boot-time entry into the job system. The DBOS workflows job_tick and job_fire later use the stored runner to get back to this JobRunner instance.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 808–830)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: This handles one scheduled firing of one job key. It fans the job out to the workspaces that currently have work, while preventing duplicate per-workspace executions from stacking up.

**Data flow**: It receives the scheduled time and job key. If this process does not know that key, it logs a warning and stops. Otherwise it asks for candidate workspaces and enqueues one job_workflow per workspace using a deduplication ID made from the job key and workspace ID.

**Call relations**: The DBOS workflow job_tick calls this. It uses _registered to tolerate old or foreign schedules, candidates to ask the JobSpec where work exists, and DBOS queueing to hand off actual execution.

*Call graph*: calls 2 internal fn (_registered, candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 832–833)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: This asks a registered job which workspaces need to run it. It is a small lookup wrapper around the JobSpec's candidate function.

**Data flow**: It receives a job key, finds the matching binding, calls that binding's candidates function, and returns the workspace IDs.

**Call relations**: JobRunner.tick calls this after confirming the key is registered. _binding provides the binding or raises if the key is not runnable here.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 835–879)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: This runs one job for one workspace. It is the only path that actually calls a job handler, and it always binds the workspace first.

**Data flow**: It receives a job key and workspace ID, finds the binding, enters the workspace context, applies agent provisioning once per workspace per process, builds an ExtensionContext, and calls the job handler. Spend refusals are deferred and possibly reported to a member; other errors are logged, counted, and re-raised.

**Call relations**: job_fire calls this as a DBOS step inside job_workflow. It uses _background_registry for model choice and _deferred_on_spend when spending policy pauses the work.

*Call graph*: calls 3 internal fn (_binding, _deferred_on_spend, _background_registry); 7 external calls (__init__, failed_statement, emit_metric, formatted_stack, log_error, context_for, ws).


##### `JobRunner._deferred_on_spend`  (lines 881–929)

```
async def _deferred_on_spend(self, key: str, workspace_id: UUID, refusal: OffTurnSpendRefused) -> None
```

**Purpose**: This turns a spending refusal from a job into a quiet deferral instead of a job failure. It also tries to notify the member once for the same refusal outcome.

**Data flow**: It receives the job key, workspace ID, and refusal object. It logs the deferral, reads a scoped marker for that refused model, writes a new marker only if this outcome has not already been told, and asks _tell_the_member to open a notice turn. If no notice can be sent, it removes the marker so a later refusal can try again.

**Call relations**: JobRunner.fire calls this when a handler raises OffTurnSpendRefused. It uses the core scoped store to coordinate repeated refusals and delegates member notification to _tell_the_member.

*Call graph*: calls 1 internal fn (_tell_the_member); called by 1 (fire); 3 external calls (__init__, log, spend_refusal_notice_key).


##### `JobRunner._tell_the_member`  (lines 931–1002)

```
async def _tell_the_member(self, key: str, workspace_id: UUID, refusal: str) -> UUID | None
```

**Purpose**: This tries to create a conversation turn telling a member that background work is paused by spending rules. It chooses a recent valid member conversation rather than posting into an arbitrary room.

**Data flow**: It receives the job key, workspace ID, and refusal text. If no invoker is available, it returns None. Otherwise it searches for the latest seated member turn in a safe audience with a live agent, and if found invokes the agent with a one-off instruction and a fresh idempotency key. It returns the created turn ID or None.

**Call relations**: JobRunner._deferred_on_spend calls this after claiming the notice marker. It uses workspace database reads to find the right speaker and MemberAuthority so the notification is made on behalf of that member.

*Call graph*: called by 1 (_deferred_on_spend); 8 external calls (__init__, and_, or_, select, workspace_tx, warn, agent_is_live, uuid4).


##### `JobRunner._registered`  (lines 1004–1005)

```
def _registered(self, key: str) -> _Binding | None
```

**Purpose**: This checks whether the current process knows a job key. It is used when old schedules or jobs from another code version may still exist in the shared DBOS schedule table.

**Data flow**: It receives a key, scans this runner's bindings, and returns the matching binding or None.

**Call relations**: JobRunner.tick uses this to skip unknown scheduled fires harmlessly. JobRunner._binding uses it as the lookup step before deciding whether to raise.

*Call graph*: called by 2 (_binding, tick).


##### `JobRunner._binding`  (lines 1007–1014)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: This returns the binding for a job key and treats a missing binding as a real error. It is used only once the code expects the key to be runnable here.

**Data flow**: It receives a key, calls _registered, and either returns the binding or raises a RuntimeError.

**Call relations**: JobRunner.candidates and JobRunner.fire call this before using a JobSpec. Unlike tick, these paths should not silently ignore an unknown key because work has already been handed to this process.

*Call graph*: calls 1 internal fn (_registered); called by 2 (candidates, fire).


##### `job_tick`  (lines 1021–1025)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: This is the durable DBOS workflow for a job tick. It connects DBOS schedule fires back to the active JobRunner.

**Data flow**: It receives the scheduled time and job key from DBOS, reads the module-level runner set by JobRunner.launch, and calls runner.tick. If jobs were not launched, it raises an error.

**Call relations**: DBOS schedules and one-shot enqueues call this workflow. JobRunner.launch registers it, and the workflow delegates all real fan-out logic to JobRunner.tick.


##### `job_workflow`  (lines 1029–1030)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: This is the durable DBOS workflow for one job running in one workspace. It exists so each workspace execution is separately tracked and retried by DBOS.

**Data flow**: It receives the scheduled time, job key, and workspace ID string. It passes the key and workspace ID to job_fire; the scheduled time is part of the workflow identity and history rather than used directly here.

**Call relations**: JobRunner.tick enqueues this workflow for each candidate workspace. It hands off to job_fire, which is the DBOS step that actually calls JobRunner.fire.

*Call graph*: calls 1 internal fn (job_fire).


##### `job_fire`  (lines 1034–1038)

```
async def job_fire(key: str, workspace_id: str) -> None
```

**Purpose**: This DBOS step runs the actual job handler through the active JobRunner. Marking it as a step lets DBOS remember completed work during recovery instead of rerunning it unnecessarily.

**Data flow**: It receives the job key and workspace ID string, converts the workspace ID to a UUID, reads the module-level runner, and calls runner.fire. If no runner is registered, it raises an error.

**Call relations**: job_workflow calls this for every per-workspace job execution. It is the final bridge from durable workflow plumbing into JobRunner.fire.

*Call graph*: called by 1 (job_workflow); 1 external calls (UUID).


### Media preview retries
A core scheduled job retries failed preview generation for recently shared documents and records the outcome.

### `core/src/ufo/runtime/media/preview_renderer.py`

`domain_logic` · `scheduled background retry`

When someone shares a document, the system tries to make a preview image right away. That first try is only “best effort”: if the preview service is briefly down, the file is still shared, but the database row is left without preview information. This file is the safety net for that situation.

The job looks for shared artifacts whose preview fields are still empty, whose filenames look like supported document types, and whose share time is recent enough to be worth retrying. The time limit matters because some files can never be rendered, such as corrupt documents. Without a cutoff, the system would keep retrying hopeless files forever.

For each candidate, the code does not download the file itself. Instead, it creates short-lived signed URLs: one URL lets the preview service read the original file, and another lets it upload the PNG preview. This is like giving a courier two temporary keys: one to pick up a package and one to drop off the finished item. The core service only receives the preview size back, then writes the preview key, media type, and byte size into the database.

The work is done in small batches. If one file fails during this run, it is simply left for the next scheduled run rather than retried in a tight loop.

#### Function details

##### `_eligible`  (lines 42–43)

```
def _eligible(filename_column: sa.Column) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for whether a filename has one of the document extensions that the preview renderer knows how to handle. It is used to avoid sending unsupported files to the preview service.

**Data flow**: It receives a database column that contains filenames. It turns the list of supported suffixes, such as .pdf or .docx, into a combined “filename ends like one of these” condition. The result is not a true or false value yet; it is a database filter used later in a query.

**Call relations**: PreviewRenderer.run uses this filter when choosing rows inside one workspace to render now. PreviewRenderer.candidate_workspaces uses the same filter when looking across workspaces to find which ones have pending preview work.

*Call graph*: called by 2 (candidate_workspaces, run); 2 external calls (ilike, or_).


##### `PreviewRenderer.run`  (lines 57–79)

```
async def run(self) -> None
```

**Purpose**: Performs one batch of preview retry work for the current workspace. It finds recent shared files that still need previews, then asks the preview service to render each one.

**Data flow**: It calculates a cutoff time so only recent failed previews are considered. It opens a workspace-scoped database transaction, reads up to a small batch of matching shared artifacts, and stops if there are none. If rows are found, it opens an HTTP client and passes each file’s blob key and filename to _render_one.

**Call relations**: This is the main body of the scheduled renderer for a workspace. It calls _eligible to build the database search condition, then calls PreviewRenderer._render_one for each selected row so the actual preview request and database update happen file by file.

*Call graph*: calls 2 internal fn (_render_one, _eligible); 4 external calls (now, AsyncClient, select, workspace_tx).


##### `PreviewRenderer._render_one`  (lines 81–117)

```
async def _render_one(self, client: httpx.AsyncClient, blob_key: str, filename: str) -> None
```

**Purpose**: Tries to render a preview for one shared file and record it if successful. It prepares temporary read and write links, calls the preview service, and stores the returned preview information.

**Data flow**: It receives an HTTP client, the original file’s blob key, and the filename. From the filename it decides the document kind and creates a new preview storage key. It asks the blob store for a temporary download URL for the source file and a temporary upload URL for the preview image. It sends those URLs and rendering limits to the preview service. If the service cannot be reached or refuses the request, it logs the problem and leaves the database unchanged. If the service succeeds, it reads the preview size from the response and updates the shared artifact row with the preview key, PNG media type, and size.

**Call relations**: PreviewRenderer.run calls this once for each pending artifact in its batch. This function hands the heavy work to the external preview service through an HTTP request, then uses a workspace-scoped database transaction to save the result only if the artifact still has no preview.

*Call graph*: called by 1 (run); 7 external calls (post, dumps, PurePosixPath, update, workspace_tx, log, uuid4).


##### `PreviewRenderer.candidate_workspaces`  (lines 119–133)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces currently have shared documents that may need preview retry work. This lets the scheduler avoid running the renderer for workspaces that have nothing to do.

**Data flow**: It calculates the same recent-time cutoff used by the renderer. It opens an owner-level database transaction, which can see workspace identifiers across the system, and searches for distinct workspace IDs on shared artifact rows with no preview, a recent creation time, and an eligible filename. It returns those workspace IDs as a tuple.

**Call relations**: The scheduling layer can call this before running workspace-specific preview jobs. It uses _eligible for the same supported-file filter as PreviewRenderer.run, but instead of rendering files, it only reports where pending work exists.

*Call graph*: calls 1 internal fn (_eligible); 3 external calls (now, select, owner_tx).


### Notification draining
The Notification app limits and traces delivery, then drains pending notifications into the owned inbox agent on schedule.

### `extensions/app_notification/ufo_ext_app_notification/deliver.py`

`domain_logic` · `request handling during notification delivery`

Most notifications can sit safely in a portal, but some need to reach a person in the chat thread they already use. This file provides that bridge. Think of it like a trusted mail clerk: it checks who is allowed to send the message, chooses the best mailbox, sends one combined note, and records exactly what happened.

The public tool is `DELIVER`, backed by the `deliver` function. It accepts notification references and a short text message. Before sending anything, it confirms that the caller is really the Notification app’s own agent, not another app pretending to use the same action name. It then checks which named notifications are still undelivered for the current member.

If there is something to send, it asks the platform for the member’s recent durable chat conversations. “Durable” means the conversation can store the message so the member can see and reply later. It tries the newest suitable conversation first. If that conversation’s agent was archived, it skips to the next one.

A successful push is recorded on the notification rows with the turn id and surface. That record prevents loops and duplicate deliveries. If no chat surface is available, the notifications are marked as delivered only to the portal, so they remain readable there but are not pushed into chat.

#### Function details

##### `_require_ext`  (lines 85–88)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

*Call graph*: called by 1 (deliver).


##### `_refusal`  (lines 91–92)

```
def _refusal(text: str) -> ToolResult
```

*Call graph*: called by 1 (deliver); 2 external calls (__init__, __init__).


##### `_require_notification_agent`  (lines 95–97)

```
async def _require_notification_agent(ext: ExtensionContext, ctx: ToolContext) -> None
```

*Call graph*: called by 1 (deliver); 1 external calls (inbox_agent_id).


##### `_names`  (lines 100–102)

```
def _names(refs: tuple[str, ...]) -> tuple[str, ...]
```

*Call graph*: called by 1 (deliver).


##### `deliver`  (lines 105–135)

```
async def deliver(ctx: ToolContext, args: DeliverInput) -> ToolResult
```

*Call graph*: calls 4 internal fn (_names, _refusal, _require_ext, _require_notification_agent); 6 external calls (__init__, __init__, __init__, __init__, authority_member_id, wall).


### `extensions/app_notification/ufo_ext_app_notification/drain.py`

`orchestration` · `recurring scheduled job`

This file is the “drain” for the notification inbox. Think of the notification store as a set of mail slots. Notifications can pile up in each member’s slot, and this code periodically scoops up a small batch and delivers it into that member’s inbox conversation as one message.

The drain is careful because it is moving work between two systems: stored notification rows and agent conversation turns. On each run, it first finds the extension’s inbox agent. If there is no such agent, it does nothing. It then looks for lanes with notifications that have not yet been triaged, meaning they have not yet been turned into inbox work. It only processes lanes addressed to this extension’s own inbox agent; notifications for other agents are left alone.

For each lane, it claims a limited batch under a lease. A lease is a temporary claim, like putting a sticky note on a stack saying “I’m working on these.” If the process crashes, the lease eventually expires and another run can try again. The file opens or reuses a conversation for that member, builds a safe text message containing the batch, invokes the agent, and only then marks the rows as triaged. This order matters: if something fails after the agent call but before marking, retrying uses the same idempotency key, so it does not create duplicate work.

#### Function details

##### `InboxDrain.run`  (lines 51–62)

```
async def run(self) -> None
```

**Purpose**: This is the scheduled entry point for draining pending notifications into inbox conversations. It finds the correct inbox agent, scans for member lanes with waiting notifications, claims a small batch from each eligible lane, and asks `_wake` to deliver that batch.

**Data flow**: It starts with the extension context stored on the `InboxDrain`. From that context it looks up the inbox agent id and creates a notification store. It reads lanes that have untriaged notifications, skips lanes for any other agent, claims up to the configured batch size from each matching lane, and passes non-empty claimed batches onward. It returns nothing, but it may cause notifications to be claimed and then delivered into conversations.

**Call relations**: This function is the top-level driver for the file. It calls `inbox_agent_id` to learn which agent belongs to this extension, builds a `NotificationStore` to read and claim notification rows, and calls `InboxDrain._wake` whenever it has a real batch to deliver.

*Call graph*: calls 1 internal fn (_wake); 2 external calls (__init__, inbox_agent_id).


##### `InboxDrain._wake`  (lines 64–88)

```
async def _wake(self, store: NotificationStore, lane: Lane, batch: tuple[Notification, ...]) -> None
```

**Purpose**: This function delivers one claimed batch of notifications into the member’s inbox conversation. It also marks the batch as triaged, but only after the conversation turn has been successfully admitted.

**Data flow**: It receives a notification store, a lane, and a tuple of claimed notification rows. It opens or reuses the lane’s conversation for the member, creates a fingerprint of the exact rows and occurrence counts in the batch, and formats the batch with `drain_message`. It invokes the inbox agent using the member’s authority, which means the work is done as that member rather than as some unrelated system actor. If the invoke returns a turn id, it writes that turn id back to the store by marking the rows triaged. If the agent has been archived, that situation is suppressed and the batch is not marked as triaged here.

**Call relations**: `InboxDrain.run` calls this after it has claimed a batch. `_wake` hands message-building to `drain_message`, uses `authority_from_member_id` so the turn carries the member’s permissions, and then calls `NotificationStore.mark_triaged` after the agent invocation succeeds. The idempotency key is based on the exact batch contents, so a retry of the same batch does not create duplicate conversation work.

*Call graph*: calls 2 internal fn (drain_message, mark_triaged); called by 1 (run); 3 external calls (suppress, sha256, authority_from_member_id).


##### `drain_message`  (lines 91–104)

```
def drain_message(batch: tuple[Notification, ...]) -> str
```

**Purpose**: This function turns a batch of notification rows into the plain text message that the inbox agent will read. It includes the notification reference, subject, count, producer, timestamps, and body for each row.

**Data flow**: It receives a tuple of notification objects. It starts a `<notifications>` block with the batch count, then adds one readable block per notification. Each notification body is wrapped with `wall`, which marks it as untrusted data so it cannot pretend to be instructions or close the surrounding notification block. Finally it escapes any literal closing notification tag found in the assembled text and returns the complete message string.

**Call relations**: `InboxDrain._wake` calls this right before invoking the inbox agent. This function calls `ufo.sdk.untrusted.wall` to safely include another agent’s words inside the message, preventing notification content from breaking out of its data container and changing the meaning of the delivery.

*Call graph*: called by 1 (_wake); 1 external calls (wall).


### Monitor checks
Monitor automation sets up one-shot watches and runs scheduled probes that wake agents on changes, failures, or deadlines.

### `extensions/monitors/ufo_ext_monitors/monitor_tool.py`

`domain_logic` · `tool call during request handling, then background monitor lifecycle`

This file solves a practical waiting problem: an agent often needs to stop working until something changes, but it should not keep polling noisily inside the conversation. The `monitor` tool lets the agent say, “run this shell command every few minutes, and wake me once when the output changes, fails repeatedly, or the deadline arrives.”

The important safety choice is that the command is tested right away, during the current live turn. That means a broken command fails immediately instead of creating a useless background watch. The first successful output becomes the baseline, like taking a “before” photo. Later checks compare against that exact output.

`MonitorInput` describes what the caller must provide: a short name, the shell command, the polling interval, the deadline, what to say to the user now, and instructions for the future turn when the monitor fires. The main `monitor` function checks that the monitor extension is available, enforces limits such as the maximum number of armed monitors, prevents duplicate names, runs the command in the sandbox, and stores the watch in `MonitorStore`.

At the end, the tool returns a directive telling the agent to reply with the supplied waiting message and end its turn. Without this file, agents could not durably watch external state between turns using the monitor object kind.

#### Function details

##### `_require_ext`  (lines 77–80)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This function makes sure the monitor tool has the extension context it needs. The extension context is the shared environment that gives the tool access to monitor storage and related extension services.

**Data flow**: It receives a possible `ExtensionContext`. If the value is missing, it stops immediately by raising an error. If it is present, it returns the same context unchanged so the caller can safely use it.

**Call relations**: The `monitor` function calls this before creating a `MonitorStore`. This is an early guard: the tool cannot arm or read monitors unless the extension context exists.

*Call graph*: called by 1 (monitor).


##### `_refusal`  (lines 83–84)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: This function builds a standard error result for cases where the tool refuses to arm a monitor. It keeps all refusal responses shaped the same way.

**Data flow**: It receives a plain text explanation. It wraps that text in `TextContent`, then wraps the content in a `ToolResult` marked as an error. The returned result tells the caller why no monitor was created.

**Call relations**: The `monitor` function uses this whenever arming must stop, such as when there are already too many monitors, the name is duplicated, or the probe command fails. It hands the refusal directly back to the tool caller.

*Call graph*: called by 1 (monitor); 2 external calls (__init__, __init__).


##### `monitor`  (lines 87–134)

```
async def monitor(ctx: ToolContext, args: MonitorInput) -> ToolResult
```

**Purpose**: This is the actual tool handler that arms a new monitor. It checks whether the request is allowed, runs the shell probe once now, saves the successful result as the baseline, and returns instructions for the agent to end the turn.

**Data flow**: It receives the current tool context and the caller’s monitor settings. It reads the conversation and agent information from the context, looks up already armed monitors, rejects requests that exceed the cap or reuse a name, then runs the requested shell command in the sandbox. If the command fails, it returns an error and stores nothing. If it succeeds, it records the monitor with its command, interval, deadline, baseline output, reason, next steps, metadata, and creator information. It returns a tool result containing the user-facing waiting message plus JSON details about the armed monitor.

**Call relations**: This function is called as the handler for `MONITOR_TOOL`. It relies on `_require_ext` to get the extension context, uses `MonitorStore` to read and save monitor rows, calls `_refusal` for all clean rejection paths, uses the sandbox to run the first probe, and finally returns a `ToolResult` that tells the agent to reply with `ai_response` and end the turn.

*Call graph*: calls 2 internal fn (_refusal, _require_ext); 10 external calls (__init__, __init__, __init__, now, timedelta, dumps, authority_member_id, capped, qualified_name, stderr_tail).


### `extensions/monitors/ufo_ext_monitors/monitor_runner.py`

`domain_logic` · `recurring background monitor tick`

A monitor is like a watchman for a command: run this command every so often, compare its output with the saved baseline, and alert the agent if something important happens. This file is the watchman’s shift schedule and decision-maker.

On each run, `MonitorRunner` asks the monitor store for monitors that are due and temporarily claims them with a lease, so two overlapping runs do not check the same monitor at the same time. For each claimed monitor, it first checks whether the monitor’s deadline has passed. If so, it fires the monitor without running another probe. Otherwise it runs the saved command using the authority of the member who created the monitor, so private access stays tied to that member. If that authority is not currently available, or the terminal is gone, the tick is counted as skipped rather than treated as a failure.

Probe results are interpreted carefully. A successful probe with unchanged output just advances the next check time. Changed output fires the monitor. A failing command is tolerated for a short streak, but the third consecutive failure fires it. When a monitor fires, the file sends a message back into the conversation first, using an idempotency key so a crash retry does not duplicate the alert, and only then retires the monitor. Large output is written to conversation files and linked from the alert.

#### Function details

##### `MonitorRunner.run`  (lines 54–64)

```
async def run(self) -> None
```

**Purpose**: Runs one scheduled sweep of the monitor system. It claims every monitor that is due right now, checks each one, and reports if any checks crashed unexpectedly.

**Data flow**: It starts with the extension context stored on the runner. From that context it creates a monitor store, reads the current time, and asks the store for due monitors under a short lease. Each monitor row is passed into `_tick`. If a tick raises an unexpected error, the monitor name and error type are collected; after all rows are attempted, those collected failures become one runtime error.

**Call relations**: This is the top-level method the recurring extension job calls. It does not decide probe outcomes itself; it hands each claimed monitor to `_tick`, then acts as the sweep supervisor that makes sure one bad monitor does not stop the rest from being attempted.

*Call graph*: calls 1 internal fn (_tick); 2 external calls (__init__, now).


##### `MonitorRunner._tick`  (lines 66–108)

```
async def _tick(self, store: MonitorStore, row: Monitor) -> None
```

**Purpose**: Performs one monitor’s actual check and decides whether it should stay quiet, count a failure or skip, or fire an alert. This is where the monitor’s saved command is run and compared with its baseline.

**Data flow**: It receives the store and one claimed monitor row. It calculates the monitor’s interval, checks the deadline, and, if still active, runs the probe command through the context’s probe service using the creator member’s authority. A missing authority or gone terminal becomes a skipped tick with a new next-check time. A nonzero exit code becomes either a counted failure or, after the failure threshold, a fired monitor with the exit code and recent error output. A successful probe has its output capped for safe posting; matching output becomes a quiet tick, while changed output becomes a fire, with the full output saved separately if it was too large.

**Call relations**: `run` calls this once for each claimed due monitor. When the monitor needs to alert the agent, `_tick` hands off to `_fire`; when nothing alert-worthy happened yet, it records the outcome through the monitor store’s quiet, failed, or skipped tick methods.

*Call graph*: calls 4 internal fn (_fire, failed_tick, quiet_tick, skipped_tick); called by 1 (run); 5 external calls (now, timedelta, authority_from_member_id, capped, stderr_tail).


##### `MonitorRunner._fire`  (lines 110–132)

```
async def _fire(self, store: MonitorStore, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> None
```

**Purpose**: Sends the monitor-fired message to the agent and then retires the monitor so it will not keep firing. It also protects against duplicate firing when two pieces of work overlap or when a crash is retried.

**Data flow**: It receives the store, monitor row, fire cause, message payload, optional full-output spill text, and probe count. First it asks the store to claim the monitor’s holds, which is a final permission check that this runner is allowed to fire it. If the claim succeeds, it builds the message body, invokes the agent in the original conversation with the creator member’s authority and a stable idempotency key, then retires the monitor in the store. If the agent has been archived, it stops without retiring through the normal path.

**Call relations**: `_tick` calls this whenever a deadline, changed output, or repeated failure should alert the agent. `_fire` depends on `_body` to prepare the exact text the agent will read, then uses the monitor store to mark the monitor finished after the invoke succeeds.

*Call graph*: calls 3 internal fn (_body, claim_holds, retire); called by 1 (_tick); 1 external calls (authority_from_member_id).


##### `MonitorRunner._body`  (lines 134–153)

```
async def _body(self, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> str
```

**Purpose**: Builds the message that tells the agent a monitor fired. It includes the reason, next steps, metadata, run counts, and any relevant probe output in a form that is safe to insert into the conversation.

**Data flow**: It takes the monitor row, the fire cause, the short payload to show, optional oversized output, and the probe count. It assembles a structured text block with the monitor name, cause, reason, next steps, metadata as JSON, and counters. If there is oversized output, it calls `_spilled` to write the full text to a file and includes the returned path. It escapes the closing marker so command output cannot pretend to end the block and inject new instructions. If there is a payload, it appends it through `wall`, a safety wrapper for untrusted command output.

**Call relations**: `_fire` calls this right before invoking the agent. If the probe output was too large to include directly, `_body` delegates file writing to `_spilled`; otherwise it returns the complete fire message directly.

*Call graph*: calls 1 internal fn (_spilled); called by 1 (_fire); 2 external calls (dumps, wall).


##### `MonitorRunner._spilled`  (lines 155–163)

```
async def _spilled(self, row: Monitor, output: str) -> str
```

**Purpose**: Writes oversized probe output into the conversation’s runtime files and returns the path to that saved file. This keeps the agent message readable while still preserving the full command output.

**Data flow**: It receives the monitor row and the full output text. It checks that the extension context has file access available, creates a timestamped filename under the monitor spill directory, encodes the text as bytes, and writes it into the conversation’s workspace files. The returned value is the file path that `_body` can mention in the fired message.

**Call relations**: `_body` calls this only when the displayed output was capped and the full version needs to be saved elsewhere. It relies on the context’s file service; if that service is not wired, it raises an error because there would be nowhere safe to put the oversized output.

*Call graph*: called by 1 (_body); 1 external calls (now).


### Report digests
Report digest helpers define concise entries and the scheduled writer creates bounded summaries for newly published reports.

### `extensions/report_digest/ufo_ext_report_digest/digest.py`

`domain_logic` · `report digest generation and validation`

This file is the quality gate for turning a longer report into a tiny, useful digest. The digest is meant to answer a reader’s first question: “Is there something new here worth opening the full report for?” If not, the entry can say there is no change and avoid taking up space.

The file defines two main data shapes using Pydantic, a validation library that checks and reshapes incoming data. A DigestEntry is the whole digest for one report. It may contain a title, a short summary, and up to two DigestPoint lines. A DigestPoint is one concrete finding, with an optional actor, meaning the person, group, or system the report says did the thing.

The important behavior is that this file does not simply trust generated text. It trims fields to fixed lengths, cuts at word boundaries so text does not end mid-word, removes extra findings from titles, and filters out lines that mostly repeat what was already said above them. Think of it like an editor fitting a story into a small newspaper sidebar: the best new facts stay, repeated wording gets cut.

It also limits how much of the original report is sent to the digest writer, and builds the instruction text that tells the writer how to produce entries. Without this file, digest output could become too long, repetitive, inconsistent, or dependent on hidden context instead of the report itself.

#### Function details

##### `_clipped`  (lines 96–107)

```
def _clipped(value: str, ceiling: int) -> str
```

**Purpose**: Shortens a piece of prose to a maximum length without rejecting it outright. It tries to cut cleanly at a word boundary so the result still looks intentional and readable.

**Data flow**: It receives a text value and a character limit. It trims outside whitespace, checks whether the text already fits, and if not, cuts it down before the limit and removes any dangling punctuation or partial trailing phrase. It returns the shortened string and changes nothing else.

**Call relations**: The field validators for titles, summaries, point text, and actors call this whenever a digest field might be too long. It is the shared ruler that keeps every visible digest line inside its allotted space.

*Call graph*: called by 4 (_summary, _title, _actor, _text).


##### `_stem`  (lines 110–117)

```
def _stem(word: str) -> str
```

**Purpose**: Reduces a word to a short rough root so similar words can be compared as the same idea. For example, this helps treat words with common endings as related instead of completely different.

**Data flow**: It receives one lowercase word. It considers the last part after a hyphen, removes a known suffix when that leaves enough meaningful letters, then returns only the first few characters of the remaining root. It produces a compact comparison token.

**Call relations**: This is used by _content when digest text is being prepared for repetition checks. It supports the later decision about whether a summary or point adds genuinely new information.

*Call graph*: called by 1 (_content).


##### `_content`  (lines 120–121)

```
def _content(text: str) -> tuple[str, ...]
```

**Purpose**: Turns a sentence or line into the meaningful word roots used for novelty checks. It ignores very common words such as “the” and “and” because they do not tell the reader anything specific.

**Data flow**: It receives a block of text. It lowercases it, finds word-like pieces, drops stopwords, sends each remaining word through _stem, and returns the resulting roots as a tuple. The output is a simplified fingerprint of the text’s real content.

**Call relations**: It calls _stem for each useful word. _adds_to uses it to measure whether one line brings new content, and DigestEntry._said_once uses it to remember what the reader has already been told.

*Call graph*: calls 1 internal fn (_stem); called by 2 (_said_once, _adds_to).


##### `_adds_to`  (lines 124–126)

```
def _adds_to(text: str, said: set[str]) -> bool
```

**Purpose**: Decides whether a line says enough that is new to deserve space in the digest. This prevents the summary or later bullet points from repeating the title in different words.

**Data flow**: It receives a text line and a set of content roots already seen. It extracts the line’s content roots with _content, counts how many are new, and compares that share with the minimum novelty rule. It returns true when the line adds enough new content, otherwise false.

**Call relations**: DigestEntry._said_once calls this while reading the entry from top to bottom. It is the simple test that decides whether each lower line earns its place.

*Call graph*: calls 1 internal fn (_content); called by 1 (_said_once).


##### `DigestPoint._text`  (lines 139–140)

```
def _text(cls, value: str) -> str
```

**Purpose**: Keeps the text of one digest finding within the allowed length. This makes each point fit as a single compact line.

**Data flow**: It receives the proposed point text during model validation. It passes that text to _clipped with the point-text length limit, then stores the clipped result as the point’s text.

**Call relations**: Pydantic calls this automatically when a DigestPoint is built. It relies on _clipped so point text follows the same clean-cutting rule as other digest fields.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestPoint._actor`  (lines 144–145)

```
def _actor(cls, value: str) -> str
```

**Purpose**: Keeps the actor field short enough to fit the digest format. The actor is whoever or whatever the report says is responsible for the finding.

**Data flow**: It receives the proposed actor string during model validation. It clips the string to the actor length limit and returns the cleaned value for storage in the DigestPoint.

**Call relations**: Pydantic runs this while creating or validating a DigestPoint. It delegates the actual shortening to _clipped, matching the rest of the file’s text-cleaning behavior.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._decoded`  (lines 161–167)

```
def _decoded(cls, value: object) -> object
```

**Purpose**: Accepts point data even when a provider returns it as JSON text instead of a normal nested list. This makes the digest parser tolerant of a common model-output shape without accepting unrelated formats blindly.

**Data flow**: It receives the raw value supplied for points before normal validation. If that value is a string, it parses it as JSON; if the parsed value is a dictionary with a points field, it extracts that field, otherwise it uses the parsed value itself. If the input is not a string, it passes it through unchanged.

**Call relations**: Pydantic calls this before validating DigestEntry.points. It uses json.loads to turn JSON text into normal data so the later DigestPoint validation can proceed.

*Call graph*: 1 external calls (loads).


##### `DigestEntry._title`  (lines 171–172)

```
def _title(cls, value: str) -> str
```

**Purpose**: Cleans the digest title so it carries only the first finding and stays within the title length limit. This protects the title from becoming a packed list of multiple findings.

**Data flow**: It receives the proposed title. It splits the title at the configured join mark, keeps only the part before that mark, clips it to the title limit, and returns the final title string.

**Call relations**: Pydantic calls this when a DigestEntry is validated. It uses _clipped for the final length control, before later whole-entry checks decide whether the entry is complete and non-repetitive.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._summary`  (lines 176–177)

```
def _summary(cls, value: str) -> str
```

**Purpose**: Keeps the digest summary short enough to be a single useful clause. The goal is a quick hint, not a second mini-report.

**Data flow**: It receives the proposed summary text. It passes the text through _clipped with the summary length limit and returns the shortened result.

**Call relations**: Pydantic calls this during DigestEntry validation. Its result is later examined by DigestEntry._said_once, which may remove the summary entirely if it mostly repeats the title.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._titled`  (lines 180–183)

```
def _titled(self) -> 'DigestEntry'
```

**Purpose**: Enforces the rule that a digest claiming there is a real change must have a title. A change without a title would give the reader no clear reason to open the report.

**Data flow**: It receives the already-built DigestEntry. If holds_a_change is true and the title is empty, it raises a validation error; otherwise it returns the entry unchanged.

**Call relations**: Pydantic runs this after field-level validation. It acts as a final completeness check before the entry is accepted.


##### `DigestEntry._said_once`  (lines 186–205)

```
def _said_once(self) -> 'DigestEntry'
```

**Purpose**: Removes parts of a digest that do not add enough new information. It keeps the entry compact by making the title, summary, and points earn their space in reading order.

**Data flow**: It starts with the title’s content roots as already said. It checks whether the summary adds enough new content; if not, it clears the summary. Then it walks through the proposed points, keeping only points whose text adds enough new content, stopping once the maximum number of points is reached. It updates the entry’s summary and points, then returns the entry.

**Call relations**: Pydantic calls this after a DigestEntry is built. It uses _content to remember what has already been said and _adds_to to judge each later line, shaping the final digest that readers will see.

*Call graph*: calls 2 internal fn (_adds_to, _content).


##### `bounded`  (lines 208–210)

```
def bounded(report: str) -> str
```

**Purpose**: Cuts the original report down to the maximum amount the digest writer is allowed to read. This controls cost and keeps the digest focused on the report’s opening findings.

**Data flow**: It receives the full report text. It returns only the first configured number of characters and does not inspect or rewrite the content otherwise.

**Call relations**: Other digest-writing code can call this before sending a report into the writer. It provides the input boundary that matches the file’s rule that the digest should be reproducible from a limited report payload.


##### `writing_standard`  (lines 213–227)

```
def writing_standard() -> str
```

**Purpose**: Builds the instruction text given to the digest writer. It combines the local prompt, the shared delivery rules, and the report-digest skill instructions into one standard.

**Data flow**: It reads the skill document from disk, removes its frontmatter, reads the digest subagent prompt, adds the shared delivery register block, and joins these pieces with blank lines. It returns the complete instruction string.

**Call relations**: Digest-writing code can call this when preparing the model or agent that will create digest entries. It reads files from the prompts and skills folders and includes DELIVERY_REGISTER_BLOCK so job-written and agent-written entries follow the same rules.


### `extensions/report_digest/ufo_ext_report_digest/writer.py`

`domain_logic` · `scheduled background tick`

This file is the “digest writer” for report summaries. A scheduled app may publish a Markdown report, but raw reports can be long and hard to scan. This code finds recent reports that have not yet been read by the digest system, asks a language model to summarize each one in a strict format, and stores the result so the feed can show a useful short entry.

It is careful about cost and failure. Each tick only processes a small batch, like taking a few letters from an inbox instead of emptying the whole mailroom at once. It only looks back seven days, so a permanently broken report cannot block the job forever. It also reads only a limited number of bytes from each report file, so a huge file cannot overwhelm the process.

The main worker is `DigestWriter`. It finds candidate reports, reads their blobs, builds a reader description, asks the model for a `DigestEntry`, and stores either the digest or a small “unchanged” marker. That marker matters: if the model decides the report contains no meaningful change, the system records that fact so it does not pay to ask again every tick.

`DigestRebuild` is the reset button for recent digests. It deletes recent digest rows and unchanged markers so the writer can recreate them, for example after the digest rules change.

#### Function details

##### `DigestWriter.run`  (lines 117–126)

```
async def run(self) -> None
```

**Purpose**: Runs one digest-writing tick. It finds reports that still need digest work and processes them one by one, while making sure one bad report does not stop later reports.

**Data flow**: It reads the workspace context and calls `_unwritten` to get a small list of candidate reports. For each report, it calls `_digest`. If one report raises an error, the error is swallowed and the loop continues, leaving that report for a future attempt while allowing the rest of the batch to move forward.

**Call relations**: This is the top-level method for the writer. It starts by asking `_unwritten` what work is due, then hands each report to `_digest` for the actual read, model call, and database write.

*Call graph*: calls 2 internal fn (_digest, _unwritten).


##### `DigestWriter._digest`  (lines 128–139)

```
async def _digest(self, report: Report) -> None
```

**Purpose**: Processes one report from start to finish. It reads the report body, asks the model to create a digest, and records either the digest or the fact that the report had no meaningful change.

**Data flow**: A `Report` goes in. The method fetches its text with `_body`; if the blob is missing, nothing is written. It builds a reader description with `_reader`, asks the model for a structured digest with `_written`, then stores either a full digest entry through `_store` or an unchanged marker through `_store_unchanged`.

**Call relations**: This is called by `DigestWriter.run` for each candidate report. It is the central handoff point between storage, reader wording, model output, and final database records.

*Call graph*: calls 5 internal fn (_body, _reader, _store, _store_unchanged, _written); called by 1 (run).


##### `DigestWriter._unwritten`  (lines 141–213)

```
async def _unwritten(self) -> tuple[Report, ...]
```

**Purpose**: Finds recent scheduled reports in this workspace that still need digest attention. It skips reports that already have a digest and reports already marked as unchanged.

**Data flow**: It reads the current workspace id, the current time, and several database tables: turns, conversations, agents, members, shared artifacts, existing digest entries, and unchanged markers. It selects completed scheduled runs from the last seven days that published a Markdown report, chooses the first shared Markdown file for each run, limits the result to the batch size, and returns them as `Report` objects.

**Call relations**: This is called at the start of `DigestWriter.run`. It defines the job’s work queue, so later steps only see reports that are recent, successful, Markdown-based, and not already settled.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `DigestWriter._body`  (lines 215–228)

```
async def _body(self, report: Report) -> str | None
```

**Purpose**: Reads the report text safely from blob storage. It keeps the amount of data small enough for both the process and the model request.

**Data flow**: A `Report` with a blob key goes in. The function streams bytes from blob storage until it reaches the read limit, returns `None` if the blob is missing, decodes the bytes into text, and then uses `bounded` to cut the text to the model-facing size limit.

**Call relations**: This is called by `_digest` before any model work happens. If it cannot produce report text, `_digest` stops early and no database row is written.

*Call graph*: called by 1 (_digest); 1 external calls (bounded).


##### `DigestWriter._reader`  (lines 230–239)

```
def _reader(self, report: Report) -> str
```

**Purpose**: Builds a plain-language description of who the digest is for. This helps the model write the summary for the right audience.

**Data flow**: A `Report` goes in with its audience, owner email, and app name. If the report belongs to a specific member conversation and an owner email is known, it returns a sentence naming that member. Otherwise it returns a sentence describing the whole workspace as the reader.

**Call relations**: This is called by `_digest` after the report body is available. Its output is passed into `_written` for the model prompt and later into `_store` so the saved digest records who it was written for.

*Call graph*: called by 1 (_digest).


##### `DigestWriter._written`  (lines 241–278)

```
async def _written(self, body: str, reader: str) -> DigestEntry | None
```

**Purpose**: Asks the language model to turn a report into a structured digest entry. It accepts only the expected tool-style response, not free-form prose.

**Data flow**: The report body and reader description go in. The function builds a model request containing the writing instructions, the report and reader as compact JSON, the maximum output size, and a required tool schema based on `DigestEntry`. If the model returns the expected tool call, the tool input is validated into a `DigestEntry`; otherwise the function returns `None`.

**Call relations**: This is called by `_digest` after `_body` and `_reader`. It hands the digest-writing decision to the model, then gives `_digest` either a validated digest object to store or `None` to skip for now.

*Call graph*: called by 1 (_digest); 7 external calls (__init__, __init__, __init__, model_json_schema, model_validate, dumps, writing_standard).


##### `DigestWriter._store_unchanged`  (lines 280–286)

```
async def _store_unchanged(self, report: Report) -> None
```

**Purpose**: Records that a report was read and found to contain no meaningful change. This prevents the system from repeatedly paying to analyze the same quiet report.

**Data flow**: A `Report` goes in. The function opens a database transaction and inserts the workspace id and turn id into the `report_digest_unchanged` table. It returns nothing, but it changes the database so this report will no longer appear as unwritten.

**Call relations**: This is called by `_digest` when `_written` returns a valid digest result whose `holds_a_change` flag is false. It is the quiet-report counterpart to `_store`.

*Call graph*: called by 1 (_digest); 1 external calls (insert).


##### `DigestWriter._store`  (lines 288–301)

```
async def _store(self, report: Report, entry: DigestEntry, reader: str) -> None
```

**Purpose**: Saves a finished digest entry in the database. This is what makes the summarized report available to the feed or other readers.

**Data flow**: A `Report`, a validated `DigestEntry`, and the reader description go in. The function opens a database transaction and inserts the title, summary, bullet points, reader, model name, workspace id, turn id, and current timestamp into `report_digest_entry`. It returns nothing, but creates the permanent digest row for that report.

**Call relations**: This is called by `_digest` when the model says the report contains a change worth showing. After this row exists, `_unwritten` will skip the same report in future ticks.

*Call graph*: called by 1 (_digest); 2 external calls (now, insert).


##### `DigestRebuild.run`  (lines 321–339)

```
async def run(self) -> int
```

**Purpose**: Clears recent digest results so they can be rebuilt. This is useful when the digest format or writing standard changes and recent reports should be summarized again.

**Data flow**: It reads the workspace id and current time, finds turns in the rebuild window, then deletes matching rows from both the digest-entry table and the unchanged-marker table. It returns the total number of rows removed.

**Call relations**: This method is separate from the normal writer flow. By deleting the writer’s own recent records, it makes those reports eligible for `DigestWriter._unwritten` again on later scheduled ticks.

*Call graph*: 3 external calls (now, delete, select).


##### `undigested_workspaces`  (lines 342–366)

```
def undigested_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds a database query that identifies workspaces with at least one recent report still needing digest work. A scheduler can use this to avoid waking the writer for quiet workspaces.

**Data flow**: It uses the current time and database table definitions to construct a SQL query. The query looks for completed scheduled turns with Markdown artifacts inside the seven-day window, excluding turns that already have a digest entry or unchanged marker, and groups the result by workspace id. The function returns the query itself, not the final rows.

**Call relations**: This supports the larger scheduling flow outside this file. It mirrors the same due-work rules used by `DigestWriter._unwritten`, so the scheduler and the writer agree about which workspaces actually have pending digest work.

*Call graph*: 2 external calls (now, select).


### Scheduled conversation wakeups
Scheduled task runners fire due pauses and recurring tasks exactly once, retiring or rescheduling them as appropriate.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pause_runner.py`

`orchestration` · `recurring scheduled background tick`

A pause is like setting an alarm inside a conversation: if nobody responds before the alarm time, the system should continue the workflow automatically. This file is the alarm clock. On each scheduled tick, it asks the pause storage layer for pauses that are due, claims them for a short lease so another overlapping tick does not do the same work, and then tries to fire each one.

The important safety idea is that a pause can end in two ways: a member sends a message, or the timer fires. When firing, the runner asks the main conversation system to admit the saved resume prompt only if no member has spoken since the pause began. Those saved sequence markers are the “watermarks” that make the two paths meet cleanly. If a member already spoke, the scheduled turn is not admitted, but the pause is still finished because the wait has already ended.

The runner fires before it retires the pause. That order matters. If the process crashes after firing but before cleanup, the next tick may try again, but it uses the same idempotency key, meaning the conversation system can recognize it as the same scheduled action rather than a duplicate. If the app is archived, no turn is admitted and the pause is left in place so it can be restored later.

#### Function details

##### `PauseRunner.run`  (lines 34–43)

```
async def run(self) -> None
```

**Purpose**: This is the main tick of the pause runner. It finds pauses whose time has arrived, tries to fire each one, and reports if any of them failed.

**Data flow**: It starts with the runner’s extension context and lease length. It creates a pause store, asks for all pauses due at the current UTC time, then passes each claimed pause to the firing helper. Successful pauses continue normally; failed pauses are remembered by conversation id and error type. At the end, it either returns with no value if everything worked, or raises one combined error describing the failures.

**Call relations**: A scheduler or background job calls this method periodically. It creates the storage helper, gets due pause rows, and hands each row to PauseRunner._fire, which does the careful conversation-level work. The method collects errors instead of stopping at the first one, so one bad pause does not prevent later due pauses from being attempted.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `PauseRunner._fire`  (lines 45–61)

```
async def _fire(self, store: PauseStore, row: Pause) -> None
```

**Purpose**: This tries to complete one specific pause. It either resumes the conversation with the stored prompt, retires the pause because the wait is over, or leaves it alone if the app is archived.

**Data flow**: It receives a pause store and one pause row. First it asks the store to claim the row’s hold, which is an extra guard that says this runner is allowed to act on it now; if that fails, nothing changes. If the hold is claimed, it asks the conversation system to run the saved prompt as a scheduled turn, using the member who created the pause as the authority and using the pause id as a repeat-safe key. It also sends the saved “no member has spoken since then” markers. If the agent is archived, it stops and leaves the pause stored. Otherwise, whether the scheduled turn was admitted or skipped because a member already spoke, it retires the pause from the store.

**Call relations**: PauseRunner.run calls this once for each due pause it claimed. This helper coordinates the storage layer, the authority lookup from the original member id, and the conversation invocation. After the invocation attempt settles the wait, it hands back to the store to retire the pause, unless the archived-agent case means the pause should remain for a future restore.

*Call graph*: calls 2 internal fn (claim_holds, retire); called by 1 (run); 1 external calls (authority_from_member_id).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `recurring scheduled job`

This file is the clock-driven worker for scheduled tasks. Think of it like a delivery person who checks a mailbox every few minutes: it looks for tasks whose scheduled time has arrived, temporarily marks them as claimed so another worker will not take the same job, and then delivers the task into the right conversation.

The runner first asks the schedule store for tasks that are due. Before firing a task, it checks whether the task has expired. If it has, the task is retired instead of run. If it is still valid, the runner calculates the next time the task should run from its cron schedule. A cron schedule is a repeating time rule, such as “every Monday at 9.”

The file also builds the message that the agent will receive. That message includes the exact scheduled time and the user’s original task prompt. It also adds instructions: normally the agent should publish a report only if there is something worth reporting; on the final allowed run, it must ask the user whether to continue, change, or stop the schedule.

A key safety detail is the idempotency key, which is a unique label for this task and exact scheduled occurrence. If the same fire is retried after a deployment or temporary failure, the system can recognize it as the same run rather than starting a duplicate. Successful runs are rescheduled. Failed runs keep their claimed occurrence so they can be retried.

#### Function details

##### `fire_body`  (lines 43–61)

```
def fire_body(task: ScheduledTask, runtime_instruction: str | None) -> tuple[str, str]
```

**Purpose**: Builds the actual message sent to the agent for one scheduled task run, plus the unique key used to prevent duplicate fires. Someone uses this when a due task is about to be invoked in its conversation.

**Data flow**: It receives a scheduled task and an optional extra instruction for this run. It reads the task’s next scheduled time, prompt, and id, then formats a message that says when this scheduled fire is happening and what the task should do. It returns two things: the message text to deliver and a stable scheduled-fire key made from the task id and exact scheduled time.

**Call relations**: ScheduledTaskRunner._fire calls this after it has confirmed the task is still claimed and ready to run. fire_body hands the task id and time to scheduled_fire_key so the wider system can recognize retries of the same scheduled occurrence as the same event, not a new one.

*Call graph*: called by 1 (_fire); 1 external calls (scheduled_fire_key).


##### `ScheduledTaskRunner.run`  (lines 69–78)

```
async def run(self) -> None
```

**Purpose**: Performs one full polling tick for scheduled tasks. It finds tasks that are due now, tries to fire each one, and reports if any of those fires failed.

**Data flow**: It starts with the runner’s extension context, creates a ScheduleStore for reading and updating schedule rows, and records the current time. It asks the store to claim tasks due at that time for a limited lease period. For each claimed task, it calls _fire and collects any failure names. If no failures happen, it finishes quietly; if one or more tasks fail, it raises an error naming them.

**Call relations**: This is the top-level action for the recurring scheduled-task job. It creates the store, gets the current time, and delegates each individual task attempt to ScheduledTaskRunner._fire. _fire returns either no problem or a failure label, and run turns those labels into one combined error for the job tick.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `ScheduledTaskRunner._fire`  (lines 80–117)

```
async def _fire(self, store: ScheduleStore, task: ScheduledTask, tick_at: datetime, expiry_checked_at: datetime) -> str | None
```

**Purpose**: Attempts to run one claimed scheduled task once. It checks expiry, confirms the claim is still valid, invokes the agent, and only advances the schedule after the system accepts the new turn.

**Data flow**: It receives the schedule store, the claimed task, the tick time, and the time used for expiry checking. First it asks the store to retire the task if it has expired. If not expired, it calculates the following scheduled time with next_fire. It chooses either the normal reporting instruction or the final-run instruction if the next fire would be past the task’s expiry. It then asks the store whether this worker’s claim still holds. If the claim is valid, it builds the inbound message and idempotency key with fire_body, derives the creator’s authority from the member id, and invokes the conversation as a scheduled turn. If the agent is archived or the turn is not admitted, it leaves the task in place without counting it as a failure. If invocation succeeds with a turn id, it reschedules the task to its next fire time. If an unexpected exception happens, it returns a short failure label containing the task name and error type.

**Call relations**: ScheduledTaskRunner.run calls this once for each due task it claimed. _fire coordinates the lower-level pieces: ScheduleStore.retire_if_expired decides whether the task is already over, next_fire calculates the next cron occurrence, ScheduleStore.claim_holds protects against overlapping workers, fire_body prepares the message and duplicate-prevention key, authority_from_member_id gives the invocation the creator’s permissions, and ScheduleStore.reschedule records the next run only after the scheduled turn is accepted.

*Call graph*: calls 4 internal fn (fire_body, claim_holds, reschedule, retire_if_expired); called by 1 (run); 2 external calls (authority_from_member_id, next_fire).


### Homepage cleanup
A site maintenance job removes obsolete seeded homepages once the chat app has taken over the workspace’s main agent.

### `extensions/sites/ufo_ext_sites/main_homepage.py`

`domain_logic` · `scheduled background sweep`

This file fixes a careful one-time transition problem. Older setup code could attach a hosted site page as the homepage for every agent, including the workspace’s main agent. Later, the chat app became the thing behind that main agent, and its real home screen is served from the app bundle, not from a hosted-site database row. If the old hosted page stays attached, it wins the lookup and users see the wrong home screen.

The file defines a background sweep, like a cleaner walking through rooms that are ready to be tidied. It looks only for workspaces where the main agent has already been adopted by the chat app, where a hosted page is still bound as that agent’s homepage, and where this cleanup has not already been recorded. When it finds one, it releases the binding so the chat bundle can show through, then writes a small marker saying this workspace has been processed.

The marker matters because the main agent can still be used by members. If a member later chooses a homepage, the sweep must not remove that new choice. The file also protects privacy when releasing the old page: while a page is bound to an agent, its own visibility setting is dormant. When the binding is removed, the file chooses the narrower of the page’s stored visibility and the agent’s visibility, so releasing the page does not accidentally make it visible to more people.

#### Function details

##### `_the_chat_main_agent`  (lines 67–75)

```
def _the_chat_main_agent() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for the exact agent this cleanup cares about: the workspace’s main agent after it has been provisioned as the chat app’s declared agent. This prevents the cleanup from touching an agent before the chat app has actually taken over.

**Data flow**: It reads no live rows by itself. Instead, it creates a reusable database condition from three facts: the agent is marked as main, it was provisioned by the chat extension, and its provisioned name is the chat declaration. The result is a filter that other queries can include when looking for the right agent.

**Call relations**: The candidate search uses this condition to find workspaces that still have an old bound homepage on the chat main agent. The release job also uses it before making any change, so both the search step and the cleanup step agree about which agent is safe to touch.

*Call graph*: called by 2 (release_main_homepage, with_a_bound_main_homepage); 1 external calls (and_).


##### `unreleased_main_homepage_workspaces`  (lines 78–97)

```
def unreleased_main_homepage_workspaces(extension: str) -> WorkspaceCandidates
```

**Purpose**: Declares which workspaces are eligible for this cleanup job. A workspace qualifies only if it still has a homepage bound to the chat-owned main agent and has not already been marked as released.

**Data flow**: It receives the extension name used for the marker record. It builds a candidate query that finds matching workspace IDs, then wraps that query in the job system’s owner-candidate format. The output is a workspace candidate provider that the scheduled job machinery can use to decide where to run.

**Call relations**: This is the doorway between the scheduler and the cleanup logic. It hands the job system a way to discover workspaces needing attention, and that discovery relies on the inner query plus the shared chat-main-agent test.

*Call graph*: 1 external calls (owner_candidates).


##### `unreleased_main_homepage_workspaces.with_a_bound_main_homepage`  (lines 84–95)

```
def with_a_bound_main_homepage() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query for workspaces that still have an old hosted-site homepage attached to the chat main agent. It also excludes workspaces that already have the release marker.

**Data flow**: It uses the hosted-site table, the agent table, and the extension store table. It joins hosted sites to agents through the homepage binding, checks that the agent is the chat main agent, checks that no release marker exists for that workspace, groups by workspace, and produces workspace IDs as the query result.

**Call relations**: This helper sits inside the candidate provider. It calls the shared chat-main-agent condition so it searches for the same target that the release job later verifies before changing anything.

*Call graph*: calls 1 internal fn (_the_chat_main_agent); 4 external calls (exists, literal, select, join).


##### `released_visibility`  (lines 100–104)

```
def released_visibility(site: str, agent: str) -> Visibility
```

**Purpose**: Chooses the visibility level a page should have after it is no longer hidden behind an agent binding. It deliberately picks the more restrictive of the page’s stored visibility and the agent’s visibility.

**Data flow**: It receives two visibility names: one from the hosted page and one from the agent. It converts both names into their ordered visibility levels, compares them using the project’s visibility ordering, and returns the narrower setting. The result is used as the released page’s active visibility.

**Call relations**: The release job calls this just before unbinding the homepage. It gives the storage layer the safe visibility value to write, so the act of releasing the page does not accidentally widen who can see it.

*Call graph*: called by 1 (release_main_homepage); 1 external calls (visibility_level).


##### `release_main_homepage`  (lines 107–130)

```
async def release_main_homepage(ctx: ExtensionContext) -> None
```

**Purpose**: Performs the one-workspace cleanup: find the chat main agent, release any hosted-site homepage bound to it, and record that this workspace has been processed. This is the action the scheduled sweep ultimately exists to run.

**Data flow**: It receives an extension context, which provides the current workspace, a database transaction, and a small key-value store for markers. First it looks up the workspace’s chat main agent. If none exists, it stops. If it finds one, it asks the hosted-sites storage for that agent’s homepage. If a bound page exists, it releases the binding using the safe visibility chosen by `released_visibility`. Finally, it writes the release marker with the agent ID, so future sweeps know not to remove a member’s later homepage choice.

**Call relations**: This function is the cleanup step after the candidate system has selected a workspace. It uses `_the_chat_main_agent` to confirm the target, uses `HostedSites` to read and release the homepage binding, and uses `released_visibility` to preserve the safest audience setting during the release.

*Call graph*: calls 3 internal fn (transaction, _the_chat_main_agent, released_visibility); 2 external calls (__init__, select).

## 📊 State Registers Touched

- `reg-pack-extension-registry` — The approved set of installed packs and extensions, including what tools, jobs, agents, hooks, providers, and surfaces they add.
- `reg-extension-store` — The per-workspace saved data that extensions use to remember their own settings and state.
- `reg-agent-records` — The saved assistant profiles, including their model choice, tools policy, setup needs, visibility, reasoning level, and spawn contracts.
- `reg-workspace-member-seat-state` — The shared record of workspaces, members, admins, invitations, seats, and workspace-level limits.
- `reg-authority-context` — The current acting identity for runtime work, saying which workspace, member, and agent are allowed to act.
- `reg-surface-routing-state` — The saved routing information that maps web, Slack, iMessage, terminal, hosted app, and public-link traffic to the right workspace and conversation.
- `reg-conversation-records` — The durable conversation state, including conversation identity, title, surface label, sandbox handle, audience, and related metadata.
- `reg-turn-queue-state` — The durable queue of conversation turns, including admission source, run claim, parked state, resume state, and final status.
- `reg-live-updates-delivery` — The live reply and notification delivery state used to stream running turns and safely deliver mid-turn or delayed messages once.
- `reg-runtime-fleet-claims` — The attendance and claim sheet for running service processes, including heartbeats, work ownership, and surface listener claims.
- `reg-credential-vault-connections` — The lockbox of account connections, OAuth grants, API keys, BYOK attempts, and agent permissions to use outside services.
- `reg-source-sync-state` — The saved state for connected information sources, including cursors, pages, deletions, warnings, backoff, and source access grants.
- `reg-object-artifact-site-store` — The shared store of workspace objects, files, artifacts, previews, reports, websites, todos, and objective records.
- `reg-schedules-automations` — The durable alarm clock for future work, pauses, monitors, source-change triggers, notification inbox items, and extension jobs.
- `reg-billing-ledger-balance` — The shared money and usage record, including spend caps, model costs, sandbox and egress usage, prepaid balances, and export progress.
- `reg-portal-slots-ui-state` — The structured conversation portal display state that extensions can fill with artifacts, sources, tasks, sites, and automations.
- `reg-database-connection-pools` — Process-global database engines, sessions, transaction handles, and connection pools shared by serving, workers, migrations, and cleanup code.
- `reg-inbound-message-buffer` — Durable inbound messages from external surfaces waiting to be rendered, admitted, deduplicated, or converted into conversation work.
- `reg-improvement-proposals` — Durable proposed changes and offline-improvement candidates, including their pending, approved, or rejected review state.
- `reg-indexing-enrichment-work-queue` — Dirty-page and processing markers that tell background workers which synced pages need chunking, embedding, memory/profile derivation, or enrichment refresh.
- `reg-durable-workflow-checkpoints` — Saved workflow execution/checkpoint state used to resume, repair, cancel, or finalize long-running workflows after pauses, crashes, or worker handoff.
- `reg-provider-rate-limit-backoff` — Shared throttling, retry-after, backoff, and concurrency state for AI providers and external connector APIs, separate from billing spend caps.
- `reg-user-feedback-buffer` — Collected user/operator feedback events, ratings, comments, and review signals used by telemetry, diagnostics, and offline improvement loops.
- `reg-service-worker-lifecycle-state` — Process-local supervisor state for background loops and workers, including async task handles, startup readiness, shutdown signals, and drain status not represented by durable job tables.
