# Scheduled, Billing, Evaluation, and Self-Improvement Background Work  `stage-18`

This stage is the system’s behind-the-scenes workshop. It runs work that is not part of answering a user message, such as timed jobs, billing updates, test worlds, and prompt improvement. When the server starts, the jobs layer turns declared background jobs into durable queued work, while the candidates helper safely finds which workspaces need attention and then processes them one at a time. The scheduling layer stores timers in the database so they survive restarts, and the scheduled-task runner ticks the clock, claims due tasks, fires them once, retires expired tasks, and reschedules repeating ones.

Billing work connects workspaces to Metronome for usage, Stripe for payments, and chat tools for seat and plan administration, with safeguards against double billing. Evaluation support creates a predictable fake email, calendar, and code-search world for tests.

The self-improvement pieces form a careful feedback loop. They collect failed tool conversations, call the model in a controlled way, propose better prompts, replay old tasks without running real tools, judge results, and gate changes. Governance then requires approval before any prompt is replaced.

## Files in this stage

### Background job scheduling
Core background-job infrastructure discovers workspace work, schedules durable jobs, and executes due scheduled tasks safely.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `recurring scheduled tick`

This runner is the part of the scheduled-task system that actually wakes up on a timer and does the work. Think of it like a careful office clerk checking a calendar: it gathers the tasks whose reminder time has arrived, marks each one as temporarily claimed so another overlapping clerk cannot do the same job, and then sends each task back into its conversation to be carried out.

The main safety idea is the lease: a short claim on a task that prevents duplicate firing if two runner ticks overlap. Before firing, the runner asks the schedule store whether the task has expired. If it has, the task is retired instead of invoked. If it is still valid, the runner invokes it. For repeating schedules, it also calculates the next fire time and reschedules the task, but only after the current occurrence has been accepted.

There is one special user-facing behavior near the end of a repeating task’s allowed lifetime. If the next scheduled time would be at or beyond the task’s expiry, the runner adds an instruction telling the task this is its final permitted fire and asking the user whether to continue, change, or stop the cadence.

If firing a task fails, the runner records the task name and error type, leaves the leased occurrence available for retry later, and reports all failures after the tick finishes.

#### Function details

##### `ScheduledTaskRunner.run`  (lines 36–47)

```
async def run(self) -> None
```

**Purpose**: This is the top-level tick for scheduled tasks. It checks that a scheduler is available, claims every task due right now, fires them one by one, and reports if any of them failed.

**Data flow**: It starts with the runner’s context, which should contain a scheduler. It reads the current UTC time, asks the scheduler for due tasks while placing a temporary lease on them, then passes each task into _fire. Any task names returned as failures are collected; if the list is not empty, the function raises one combined error. If all tasks either succeed, are retired, or do not need action, it finishes with no returned value.

**Call relations**: This is the public entry into the file’s work. It is called when the extension’s recurring job fires. For each claimed task, it hands the detailed decision-making to ScheduledTaskRunner._fire, while it stays responsible for the overall batch and final failure report.

*Call graph*: calls 1 internal fn (_fire); 1 external calls (now).


##### `ScheduledTaskRunner._fire`  (lines 49–83)

```
async def _fire(self, scheduler: ScheduleStore, task: ScheduledTask, tick_at: datetime, expiry_checked_at: datetime) -> str | None
```

**Purpose**: This function decides what should happen to one claimed scheduled task. It retires the task if it has expired, invokes it if it is still valid, and reschedules it if it is a repeating task that fired successfully.

**Data flow**: It receives the schedule store, one claimed task, the time of the runner tick, and the time used for expiry checking. First it asks the scheduler to retire the task if its expiry has passed. If not expired, it calculates the next fire time for repeating schedules. If this is the last allowed fire before expiry, it prepares a special instruction for the conversation. It then asks the scheduler to invoke the task. If invocation raises an error, it returns a short failure label. If invocation produces no turn to track, it stops quietly. If invocation succeeds and the task repeats, it asks the scheduler to store the next fire time. The result is either no failure or a text description of the failed task.

**Call relations**: ScheduledTaskRunner.run calls this once for each task it successfully claimed. Inside, _fire relies on the schedule store for the durable actions: retiring expired tasks, invoking active ones, and saving the next occurrence. It also calls next_fire to work out the next calendar time for recurring schedules.

*Call graph*: calls 3 internal fn (invoke, reschedule, retire_if_expired); called by 1 (run); 1 external calls (next_fire).


### `core/src/ufo/candidates.py`

`domain_logic` · `job scheduling`

This file solves a safety problem in a multi-workspace system. Normally, data is protected by workspace boundaries, so code for one workspace cannot accidentally read another workspace’s rows. But a job scheduler has one special question it must ask across the whole system: “Which workspaces need this job to run?”

The answer must be just workspace IDs, not private row data. This file creates that narrow doorway. An extension supplies a small query builder that selects distinct `workspace_id` values from its own tables. The helper `owner_candidates` wraps that builder in a callable the dispatcher can run whenever it checks for due work.

The important detail is that the query is built fresh each time. That matters for time-based work. For example, a job may be due only if a timestamp is older than “now.” If the query were built once at startup, “now” would be frozen. Building per tick keeps the due check current.

The actual cross-workspace read uses `owner_tx`, a privileged database transaction that bypasses normal row-level security. Row-level security means the database itself limits which rows a workspace can see. Here, that bypass is tightly limited: it returns only workspace IDs. The dispatcher later re-enters each workspace using those IDs before running the job handler.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: This function turns a query builder into a reusable candidate finder for the job dispatcher. It lets extensions say which workspaces have work waiting without giving them direct access to the privileged cross-workspace database connection.

**Data flow**: It receives `due`, a no-argument function that builds a database query selecting workspace IDs. It wraps that builder inside an async `candidates` function. The result is a callable that, when run later, will execute the fresh query and return the workspace IDs as a tuple.

**Call relations**: This is the public seam used by code that needs to declare job candidates. It does not run the database query immediately; it prepares the inner `owner_candidates.candidates` function so the dispatcher can call it on each scheduling tick.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner async function actually asks the database which workspaces currently have pending work. It uses the privileged cross-workspace path, but only to read workspace IDs.

**Data flow**: It takes no direct arguments, but it closes over the `due` query builder given to `owner_candidates`. When called, it opens an `owner_tx` database transaction, builds and executes the current query, reads the first column from each returned row, and returns those values as a tuple of workspace UUIDs. It does not return the underlying job rows or tenant data.

**Call relations**: The dispatcher calls this candidate function when deciding where a job should run. Inside, it calls `ufo.db.owner_tx` to perform the one allowed row-level-security-bypassing read. After it returns workspace IDs, the dispatcher is expected to bind each workspace before running the real job handler.

*Call graph*: 1 external calls (owner_tx).


### `core/src/ufo/jobs.py`

`orchestration` · `startup and scheduled background work`

This file is the system’s background-job dispatcher. Extensions and core code declare jobs, but those jobs are not known until startup, so this file registers them then. Without it, source syncing, page-change hooks, and recovery of queued conversation turns would not reliably run.

The main idea is: first find a job, then find which workspaces need it, then run one isolated copy per workspace. DBOS, a durable workflow system, is used so queued work survives process crashes. A database-backed queue limits how many job workers run at once, which prevents a burst of jobs from creating an uncontrolled number of threads or tasks.

There are three main parts. `JobRunner` registers all jobs at boot and is the single path that actually fires a job handler. `TurnDispatcher` scans conversation turns that are waiting or parked, checks whether they are allowed to proceed, and puts them on the conversation worker queue in the right order. `PageChangeRunner` finds page-change hooks from extensions and replays changed pages to each hook using its own cursor, like a bookmark that says where that hook last stopped.

A key safety behavior is deduplication. If a job for the same workspace is already running, another tick does not stack another copy behind it. That keeps slow work from blocking unrelated workspaces and avoids duplicate processing.

#### Function details

##### `TurnDispatcher.run`  (lines 124–162)

```
async def run(self) -> None
```

**Purpose**: Finds conversation turns that are ready to be offered to the worker queue. For parked turns, it also checks seats and spending limits before letting them resume.

**Data flow**: It starts by reading dispatchable turn rows from the database. For each parked row, it gathers the relevant members, checks whether they have seats, and asks the spend evaluator whether the agent may spend. If the turn passes those checks, or if it was simply queued, it passes the turn to `_enqueue`; otherwise it leaves the row untouched for a later sweep.

**Call relations**: This is called by the core turn-dispatch job created in `core_jobs`. It relies on `_dispatchable_turns` to choose possible rows and `_enqueue` to mark and offer each accepted row to the durable turn workflow.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 5 external calls (__init__, __init__, select, workspace_tx, gate_member).


##### `TurnDispatcher.candidate_workspaces`  (lines 164–172)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Reports which workspaces currently have queued or parked turns that may need dispatching. This lets the job system avoid opening workspaces that have no turn work.

**Data flow**: It computes a cutoff time for stale dispatch stamps, reads the owner-level database view for distinct workspace IDs matching `_eligible`, and returns those IDs as a tuple.

**Call relations**: The turn-dispatch `JobSpec` uses this as its candidate finder. `JobRunner.tick` calls it before creating one turn-dispatch workflow per workspace.

*Call graph*: calls 1 internal fn (_eligible); 4 external calls (now, timedelta, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 174–213)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: Loads a small ordered batch of turn rows that look ready to be offered to workers. It keeps the batch bounded so one sweep does not monopolize the system.

**Data flow**: It calculates the stale cutoff, queries the workspace database for eligible turns and their conversation/member details, orders queued turns before parked turns and older turns before newer ones, limits the result, and converts each row into a `_DispatchTurn` value.

**Call relations**: `TurnDispatcher.run` calls this at the start of a sweep. The eligibility test is shared with `candidate_workspaces` through `_eligible`, so the fleet-level scan and workspace-level scan agree.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 6 external calls (__init__, now, timedelta, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 215–245)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: Atomically marks one turn as offered and then enqueues its DBOS workflow. The database mark prevents two sweepers from offering the same turn at the same time.

**Data flow**: It receives a `_DispatchTurn`, checks that the row is still in the same status, still stale, and still first in line for its conversation/status. If the update succeeds, it builds enqueue options, choosing a safe workflow ID, and sends the turn to the DBOS client; if the update finds nothing, it returns without doing anything.

**Call relations**: `TurnDispatcher.run` calls this after any parked-turn gates pass. It uses `_first_in_status` and `_stale` to protect ordering and retry safety before handing work to DBOS.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 1 (run); 5 external calls (now, timedelta, update, workspace_tx, uuid4).


##### `TurnDispatcher._eligible`  (lines 247–255)

```
def _eligible(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for turns that may be dispatched. It captures the rule that only queued or parked turns with no fresh dispatch offer, and only the first such turn in a conversation, are eligible.

**Data flow**: It takes a cutoff time and combines smaller SQL conditions: status must be queued or parked, the dispatch stamp must be missing or old, and the turn must be first among turns of that status in the conversation. The output is a SQL expression used in queries.

**Call relations**: `candidate_workspaces` uses this for a broad workspace scan, and `_dispatchable_turns` uses it for the actual per-workspace row fetch. It delegates the timestamp test to `_stale` and the ordering test to `_first_in_status`.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 2 external calls (and_, or_).


##### `TurnDispatcher._stale`  (lines 257–261)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for a dispatch offer that is missing or old enough to retry. This is what lets the system recover if a process marked a turn but crashed before enqueueing it.

**Data flow**: It takes a cutoff time and returns a SQL expression that is true when `dispatch_enqueued_at` is null or earlier than the cutoff.

**Call relations**: `_eligible` uses it while selecting possible turns, and `_enqueue` uses it again during the atomic update so a stale scan cannot race with another fresh offer.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 263–272)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a turn is the earliest turn of a given status in its conversation. This prevents later turns from overtaking earlier ones.

**Data flow**: It takes a turn status, creates an alias for earlier turns, and returns a SQL “not exists” condition: there must be no same-workspace, same-conversation, same-status turn with a smaller sequence number.

**Call relations**: `_eligible` uses this to choose only first-in-line turns, and `_enqueue` repeats the check while claiming the row. That double check keeps ordering correct even when another worker is acting at the same time.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `_page_beyond_cursor`  (lines 275–280)

```
def _page_beyond_cursor(revision: int, page_id: UUID, cursor: object) -> bool
```

**Purpose**: Answers whether a page position is newer than a stored page-change cursor. It is a small helper for deciding if a workspace has pending page-change work.

**Data flow**: It receives a page revision, a page ID, and a cursor value. If there is no cursor, it says the page is pending. Otherwise it parses the cursor into its saved revision and ID and compares the page’s position against that saved boundary.

**Call relations**: `PageChangeRunner.workspaces_with_changes` calls this while checking each workspace’s newest page. The helper relies on `page_cursor` to parse the stored cursor format.

*Call graph*: called by 1 (workspaces_with_changes); 1 external calls (page_cursor).


##### `PageChangeRunner.consumers`  (lines 339–363)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: Finds every registered `page_change` hook in the active extension manifests. Each hook becomes its own independent consumer with its own job name and cursor.

**Data flow**: It walks through manifests, records the credential slots each extension declared, filters hooks to the `page_change` event, and uses the hook function name as a discriminator. It returns `PageChangeConsumer` objects, but raises an error if one extension has two page-change handlers with the same function name.

**Call relations**: `core_jobs` calls this when creating the core page-change jobs. Its output tells the job builder how many separate page-change workflows to register.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 365–430)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: Finds the workspaces where one page-change consumer actually has new pages to process. This keeps scheduled page-change jobs from opening workspaces that have not changed.

**Data flow**: It builds that consumer’s cursor key, reads stored cursors from extension storage, reads each workspace’s newest page position, and compares the newest page with the saved cursor using `_page_beyond_cursor`. Workspaces with no pages are skipped; workspaces with invalid cursors are warned about and treated as pending.

**Call relations**: The candidate function produced by `core_jobs._consumer_candidates` calls this. Its returned workspace IDs are then used by `JobRunner.tick` to enqueue one `PageChangeRunner.drive` workflow per workspace that has work.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 3 external calls (select, owner_tx, warn).


##### `PageChangeRunner.drive`  (lines 432–456)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: Runs one page-change consumer inside the currently bound workspace. It replays changed pages in batches, calls the extension hook, and advances that hook’s cursor only after the hook succeeds.

**Data flow**: It builds an extension context, reads the stored cursor, asks the page feed for a batch after that cursor, and passes the batch to the hook as a `HookContext`. After the hook finishes, it saves the next cursor with a compare-and-set check, meaning it only writes if the cursor is still what it read. It repeats until there are no more full batches or another writer has moved the cursor.

**Call relations**: The handler created by `core_jobs._drive_consumer` calls this. It uses `_context_for` to give the extension access to declared services, and it deliberately lets hook errors escape so the workflow fails and retries from the old cursor.

*Call graph*: calls 1 internal fn (_context_for); 2 external calls (__init__, __init__).


##### `PageChangeRunner._context_for`  (lines 458–474)

```
def _context_for(self, extension: str, declared: frozenset[str]) -> ExtensionContext
```

**Purpose**: Builds the extension-facing context used by a page-change hook. The context is the safe bundle of services, storage, credentials, and optional model/index tools that the extension is allowed to use.

**Data flow**: It receives an extension name and declared credential slots. If an invoker factory exists, it creates an invoker for the current workspace. Then it calls `context_for` with the runner’s configured services and returns the resulting `ExtensionContext`.

**Call relations**: `PageChangeRunner.drive` calls this before invoking a hook. It consults `ws_current` because page-change work is already running inside a workspace binding created by `JobRunner.fire`.

*Call graph*: called by 1 (drive); 2 external calls (context_for, ws_current).


##### `core_jobs`  (lines 477–536)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, page_change_runner: PageChangeRunner) -> tuple[JobSpec, ...]
```

**Purpose**: Creates the built-in jobs that every deployment should run: source syncing, page-change delivery, and turn dispatch recovery. These jobs are treated the same way as extension jobs later in the pipeline.

**Data flow**: It receives the source sync driver, turn dispatcher, and page-change runner. It defines small handler and candidate wrapper functions, asks the page-change runner for consumers, creates one `JobSpec` per page-change consumer, and returns all core `JobSpec` objects as a tuple.

**Call relations**: Startup code uses this before calling `bindings_from`. The nested functions become the handlers and candidate finders that `JobRunner` later calls through each `JobSpec`.

*Call graph*: calls 1 internal fn (consumers); 1 external calls (__init__).


##### `core_jobs._sync_sources`  (lines 493–494)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: Runs the source synchronization driver for a workspace. This is the body of the core source-sync job.

**Data flow**: It receives an extension context, though this wrapper does not use it directly, and calls the sync driver’s `run` method. The output is whatever side effects the sync driver performs, such as updating page data.

**Call relations**: `core_jobs` stores this function in the source-sync `JobSpec`. `JobRunner.fire` later invokes it inside a bound workspace.


##### `core_jobs._dispatch_turns`  (lines 496–497)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: Runs the turn dispatcher as a core job. It is a small adapter so the dispatcher fits the generic job-handler shape.

**Data flow**: It receives an extension context, does not use it directly, and calls `turn_dispatcher.run`. The result is that eligible queued or parked turns may be offered to the turn worker queue.

**Call relations**: `core_jobs` stores this function in the turn-dispatch `JobSpec`. `JobRunner.fire` invokes it on the workspaces returned by `turn_dispatcher.candidate_workspaces`.


##### `core_jobs._drive_consumer`  (lines 499–505)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: Creates a job handler for one page-change consumer. This lets each page-change hook run as its own scheduled job.

**Data flow**: It receives a `PageChangeConsumer` and returns an async handler function that closes over that consumer. The returned handler later ignores the generic context and calls the page-change runner for that specific consumer.

**Call relations**: `core_jobs` calls this while building page-change `JobSpec` objects. The returned `_handler` is eventually invoked by `JobRunner.fire`.


##### `core_jobs._drive_consumer._handler`  (lines 502–503)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: Runs the page-change cursor loop for the consumer captured by `_drive_consumer`. It is the actual handler stored in a page-change job spec.

**Data flow**: It receives the generic job context, then calls `page_change_runner.drive` with its captured consumer. Changed pages go from the page feed into that consumer’s hook, and the cursor may advance.

**Call relations**: `JobRunner.fire` calls this through the page-change `JobSpec`. It hands the real work to `PageChangeRunner.drive`.


##### `core_jobs._consumer_candidates`  (lines 507–511)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: Creates a candidate-workspace finder for one page-change consumer. This keeps each consumer’s scheduled job focused only on workspaces with new pages for that consumer.

**Data flow**: It receives a `PageChangeConsumer` and returns an async `_candidates` function that closes over it.

**Call relations**: `core_jobs` uses this when building each page-change `JobSpec`. `JobRunner.tick` later calls the returned `_candidates` function through `JobRunner.candidates`.


##### `core_jobs._consumer_candidates._candidates`  (lines 508–509)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the workspace IDs that have pending page changes for its captured consumer.

**Data flow**: It has no direct inputs beyond the captured consumer. It calls `page_change_runner.workspaces_with_changes` and returns that tuple of workspace IDs.

**Call relations**: `JobRunner.tick` reaches this through the `JobSpec` candidate callback. Its result controls which workspace-specific page-change workflows are enqueued.


##### `bindings_from`  (lines 547–572)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...]) -> tuple[_Binding, ...]
```

**Purpose**: Combines core jobs and extension jobs into a single list of registered job bindings. A binding gives each job a unique key and records which extension context it should run under.

**Data flow**: It receives active manifests and core job specs. It prefixes core jobs with the `core` namespace, then prefixes each extension job with that extension’s name and attaches the extension’s declared credential slots. It returns immutable `_Binding` records.

**Call relations**: Startup code calls this before constructing a `JobRunner`. `JobRunner` later uses these bindings to register schedules, find candidates, and build the right extension context when firing handlers.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 595–619)

```
def launch(self) -> None
```

**Purpose**: Publishes this runner as the active job runner and registers all jobs with DBOS. Recurring jobs become schedules; one-shot jobs are enqueued once with deduplication.

**Data flow**: It stores itself in the module-level `_firing` variable, loops through bindings, and separates jobs with schedules from jobs without schedules. One-shot jobs are immediately enqueued with a deduplication ID; scheduled jobs are collected as `ScheduleInput` objects and then applied through DBOS. It logs successful registration and warns when a duplicate one-shot enqueue is skipped.

**Call relations**: This is called during server startup. Later, DBOS workflow entry functions `job_tick` and `job_workflow` rely on `_firing` being set here so they can call back into this runner.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 621–643)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: Handles one scheduled or one-shot firing of a job by fanning it out to workspaces. It does not run the job itself; it queues one workspace-specific workflow for each candidate workspace.

**Data flow**: It receives the scheduled time and job key. If this process does not have that job registered, it logs a warning and stops. Otherwise it asks the job for candidate workspaces and enqueues `job_workflow` once per workspace using a deduplication ID made from the job key and workspace ID.

**Call relations**: `job_tick`, the DBOS workflow, calls this. It uses `_registered` to tolerate stale or foreign schedules, `candidates` to find workspaces, and DBOS queue deduplication to avoid stacking duplicate workspace executions.

*Call graph*: calls 2 internal fn (_registered, candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 645–646)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: Calls the candidate finder for a registered job. Candidate finders answer which workspaces currently have work for that job.

**Data flow**: It receives a job key, looks up the matching binding, calls that binding’s `spec.candidates` function, and returns the resulting tuple of workspace IDs.

**Call relations**: `JobRunner.tick` calls this after confirming the key is registered. It uses `_binding`, which raises if the key is unexpectedly missing.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 648–668)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: Runs one job handler inside one workspace. This is the only normal path where a job’s handler actually executes.

**Data flow**: It receives a job key and workspace ID, finds the binding, opens a workspace scope with `ws`, builds an extension context with the right services and declared credentials, and awaits the handler. If the handler raises an error, it logs the failure and re-raises so DBOS sees the workflow as failed.

**Call relations**: `job_workflow`, the DBOS workflow for actual workspace execution, calls this. It uses `_binding` to find the job and `context_for` to prepare the environment the handler receives.

*Call graph*: calls 1 internal fn (_binding); 3 external calls (context_for, log_error, ws).


##### `JobRunner._registered`  (lines 670–671)

```
def _registered(self, key: str) -> _Binding | None
```

**Purpose**: Looks up a job binding by key and returns `None` if this process does not know that job. This soft lookup is useful because shared schedules can outlive the code that created them.

**Data flow**: It receives a key and scans the runner’s bindings for the first matching binding. It returns that binding or `None`.

**Call relations**: `JobRunner.tick` uses this to skip unknown scheduled jobs safely. `JobRunner._binding` uses it as the first step of a stricter lookup.

*Call graph*: called by 2 (_binding, tick).


##### `JobRunner._binding`  (lines 673–680)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: Looks up a job binding by key and treats a missing key as a real error. This is used once the code is on a path that should only involve registered jobs.

**Data flow**: It receives a key, calls `_registered`, and either returns the binding or raises a runtime error explaining that no job is registered for the key.

**Call relations**: `JobRunner.candidates` and `JobRunner.fire` call this when they need the actual binding. Unlike `tick`, these paths do not silently skip missing jobs because work has already been routed as if the job exists.

*Call graph*: calls 1 internal fn (_registered); called by 2 (candidates, fire).


##### `job_tick`  (lines 687–691)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: DBOS workflow entry point for a job tick. It connects durable DBOS scheduling to the in-process `JobRunner`.

**Data flow**: It receives the scheduled time and job key from DBOS. It reads the module-level `_firing` runner; if no runner was launched, it raises an error. Otherwise it calls `runner.tick`.

**Call relations**: DBOS invokes this for both applied schedules and queued one-shot ticks. `JobRunner.launch` registers or enqueues this function, and `JobRunner.tick` does the fan-out work.


##### `job_workflow`  (lines 695–699)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: DBOS workflow entry point for one job running in one workspace. It is the durable wrapper around `JobRunner.fire`.

**Data flow**: It receives the scheduled time, job key, and workspace ID as a string. It checks that `_firing` is set, converts the workspace ID string into a UUID, and calls `runner.fire` for that job and workspace.

**Call relations**: `JobRunner.tick` enqueues this workflow once per candidate workspace. The function then hands off to `JobRunner.fire`, which binds the workspace and invokes the real job handler.

*Call graph*: 1 external calls (UUID).


### `core/src/ufo/scheduling.py`

`domain_logic` · `object requests and background scheduled-task polling`

This file is the project’s durable “calendar” for agent work. A scheduled task is not just an in-memory timer; it is a database row that says which workspace, conversation, and agent should be re-entered, what prompt should be sent, when it is due, and whether it has already been claimed by a worker. Without this file, scheduled work could be lost after a restart, fired twice by competing workers, or delivered into the wrong conversation.

The main value type is ScheduledTask, a plain snapshot of one database row. ScheduleStore is the safe doorway for creating, editing, cancelling, listing, claiming, inspecting, and advancing those rows. It always works inside the current workspace and current object-agent boundary, so a task belongs to the right tenant and agent.

A background runner uses due_task_workspaces to find workspaces that may have due or expired tasks. Then ScheduleStore.claim_due leases a small batch. A lease is like putting a temporary “I’m working on this” sticky note on rows, so two workers do not fire the same task at once. After firing, the runner either reschedules the task for its next time or retires it if it expired. One-time pauses are special: they use an internal @once schedule and @pause: name so normal recurring-task tools cannot accidentally edit or cancel them.

#### Function details

##### `ScheduleInvoker.invoke_scheduled`  (lines 57–59)

```
async def invoke_scheduled(self, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: Defines the shape of something that can actually fire a scheduled task. ScheduleStore depends on this promise, but this file does not decide how the agent run is started.

**Data flow**: It receives a ScheduledTask and an optional runtime instruction, then is expected to start the scheduled work. It returns the new turn identifier if one was created, or nothing if no turn was admitted.

**Call relations**: ScheduleStore.invoke calls this method when a runner wants to fire a task. The concrete implementation lives outside this file, which keeps storage concerns separate from the actual agent invocation.


##### `_utc`  (lines 96–97)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: Makes sure a datetime has UTC timezone information. This prevents different parts of the system from disagreeing about the same moment in time.

**Data flow**: It receives a datetime. If the datetime already has timezone information, it passes it through; if it is missing that information, it labels it as UTC. The output is always a timezone-aware datetime.

**Call relations**: Several readers use this helper when turning database values into application values. It is also used when building the firing key, so time comparisons and identifiers stay consistent.

*Call graph*: called by 5 (_upsert_pause, inspect, _task, _utc_opt, firing_key); 1 external calls (replace).


##### `_utc_opt`  (lines 100–101)

```
def _utc_opt(value: datetime | None) -> datetime | None
```

**Purpose**: Does the same UTC cleanup as _utc, but safely accepts an empty value. It is used for optional timestamps such as “last ran at” or “expires at.”

**Data flow**: It receives either a datetime or None. None stays None; a datetime is passed through _utc and comes back marked as UTC if needed.

**Call relations**: _task and ScheduleStore.inspect use this when reading optional database columns. It keeps all optional timing fields in the same format as required timing fields.

*Call graph*: calls 1 internal fn (_utc); called by 2 (inspect, _task).


##### `firing_key`  (lines 104–109)

```
def firing_key(task_id: UUID, next_run_at: datetime) -> str
```

**Purpose**: Builds a stable unique text key for one specific firing of one task. This helps the system recognize “this exact scheduled event” and avoid admitting it twice.

**Data flow**: It receives a task id and the task’s next run time. It normalizes the time to UTC and combines the id and timestamp into one string.

**Call relations**: This helper is used wherever a scheduled fire needs the same repeatable identity. It relies on _utc so a timestamp read from different databases still produces the same key.

*Call graph*: calls 1 internal fn (_utc).


##### `_claim_available`  (lines 112–116)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for whether a task can be claimed by a worker. A task is available if nobody has claimed it or if the old claim has expired.

**Data flow**: It receives the current time. It returns a SQL condition, not a Python boolean, that the database can use to filter rows.

**Call relations**: due_task_workspaces uses this to avoid waking workers for rows that are already leased. ScheduleStore.claim_due uses the same rule when actually claiming tasks, so discovery and claiming agree.

*Call graph*: called by 2 (claim_due, candidates); 1 external calls (or_).


##### `_expired`  (lines 119–123)

```
def _expired(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for whether a scheduled task has passed its expiry time. Expired tasks should be removed rather than fired.

**Data flow**: It receives the current time. It returns a SQL condition that matches rows with an expires_at value at or before that time.

**Call relations**: due_task_workspaces uses this to find workspaces that need cleanup. ScheduleStore.claim_due uses it to delete expired rows before leasing due work.

*Call graph*: called by 2 (claim_due, candidates); 1 external calls (and_).


##### `_task`  (lines 126–147)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: Turns a raw database row into a ScheduledTask object that the rest of the code can read safely. It also normalizes all datetime fields to UTC.

**Data flow**: It receives a row mapping from the scheduled_task table. It copies the important fields, fixes timestamp timezone information, and returns a ScheduledTask value.

**Call relations**: Create, update, list, and claim operations all funnel their database results through this builder. That means callers get one consistent shape no matter which database produced the row.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); called by 4 (claim_due, create, list, update); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 150–180)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: Provides a way for the scheduled-task runner to ask, “Which workspaces might have scheduled work right now?” It returns a callable that performs that lookup when needed.

**Data flow**: It takes no input. It creates and returns the nested candidates function, which will later read the database and return workspace ids.

**Call relations**: This is the seam between the global runner and workspace-specific task handling. The runner can first find candidate workspaces, then bind to each workspace before touching its actual tasks.


##### `due_task_workspaces.candidates`  (lines 157–178)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Finds workspaces that have at least one claimable due task or claimable expired task. It avoids selecting workspaces where all matching tasks are already under live leases.

**Data flow**: It reads the current UTC time and queries the scheduled_task table using owner-level database access. It filters for expired rows or unpaused rows whose next run time has arrived, then returns distinct workspace ids.

**Call relations**: This function is returned by due_task_workspaces and used by the scheduled-task runner’s workspace candidate system. It uses _claim_available and _expired so it matches the same rules used later by ScheduleStore.claim_due.

*Call graph*: calls 2 internal fn (_claim_available, _expired); 6 external calls (now, and_, not_, or_, select, owner_tx).


##### `ScheduleStore.workspace_id`  (lines 194–195)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace that this store is currently operating inside. This keeps every read and write scoped to the active workspace.

**Data flow**: It reads the ambient current workspace context. It returns that workspace’s UUID.

**Call relations**: Most ScheduleStore methods use this property while building database queries. It prevents a store call from accidentally reading or changing tasks in another workspace.

*Call graph*: 1 external calls (ws_current).


##### `ScheduleStore.invoke`  (lines 197–202)

```
async def invoke(self, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: Asks the wired invoker to actually fire a scheduled task. It is the bridge from stored task data to running agent work.

**Data flow**: It receives a ScheduledTask and an optional instruction. If no invoker was provided, it raises an error; otherwise it forwards the task and instruction to the invoker and returns the turn id, if any.

**Call relations**: The scheduled-tasks runner calls this during its fire flow. This method then hands off to ScheduleInvoker.invoke_scheduled, keeping ScheduleStore from needing to know the details of agent execution.

*Call graph*: called by 1 (_fire).


##### `ScheduleStore.create`  (lines 204–280)

```
async def create(self, conversation_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None, expires_at: datetime | None=None,
```

**Purpose**: Creates a new recurring scheduled task for the current object agent and a specific conversation. It rejects reserved one-time pause schedules and reserved pause names.

**Data flow**: It receives the conversation, name, schedule text, prompt, description, next run time, optional creator, optional expiry, and paused flag. It checks that the conversation belongs to the current workspace and agent, inserts a new row if the name is not already used, and returns the new ScheduledTask. If the name already exists or the conversation is wrong, it raises an error.

**Call relations**: Member-facing scheduling tools use this to create durable recurring tasks. It calls object_agent_id to bind the task to the current agent, uses workspace_tx for the database transaction, and passes the inserted row through _task before returning it.

*Call graph*: calls 1 internal fn (_task); 4 external calls (select, workspace_tx, object_agent_id, uuid4).


##### `ScheduleStore.update`  (lines 282–343)

```
async def update(self, expected: ScheduledTask, schedule: str, prompt: str, description: str, next_run_at: datetime, expires_at: datetime | None=None, *, paused: bool) -> ScheduledTask
```

**Purpose**: Edits an existing recurring scheduled task without changing its identity. It refuses to treat one-time pauses as normal recurring tasks.

**Data flow**: It receives the task snapshot the caller expects to edit plus the new schedule, prompt, description, next run time, expiry, and paused state. It checks that the current agent still matches, updates only the exact matching row, clears old run and claim information, and returns the updated ScheduledTask. If the row no longer matches, it reports that the task changed while editing.

**Call relations**: This is used when a caller wants to replace the definition of a recurring task. It relies on object_agent_id and the stored expected fields to avoid overwriting someone else’s changed task, then uses _task to return a clean value.

*Call graph*: calls 1 internal fn (_task); 3 external calls (update, workspace_tx, object_agent_id).


##### `ScheduleStore.pause`  (lines 345–362)

```
async def pause(self, conversation_id: UUID, prompt: str, description: str, next_run_at: datetime, origin_seq: int, created_by_member_id: UUID | None=None) -> ScheduledTask | None
```

**Purpose**: Creates or refreshes a one-time pause timer for a conversation. This is used for workflow pauses that should resume later, not for normal recurring schedules.

**Data flow**: It receives the conversation id, prompt, description, wake-up time, originating conversation sequence, and optional creator. It forwards those details to _upsert_pause and returns either the pause task or None if no timer should be armed.

**Call relations**: This is the public entry point for one-time pause scheduling. It delegates the detailed safety checks and database upsert to ScheduleStore._upsert_pause.

*Call graph*: calls 1 internal fn (_upsert_pause).


##### `ScheduleStore._upsert_pause`  (lines 364–507)

```
async def _upsert_pause(self, conversation_id: UUID, prompt: str, description: str, next_run_at: datetime, origin_seq: int, created_by_member_id: UUID | None) -> ScheduledTask | None
```

**Purpose**: Creates or updates the durable row for a conversation’s one-time pause. It also notices if a member has already replied, so the timer does not fight with real user input.

**Data flow**: It receives the pause details and current conversation sequence. It verifies the conversation belongs to the current workspace and agent, checks for pending member messages, checks for newer member turns, and either returns None or writes a special @once pause row. If a queued newer member turn exists, it records that turn as the resume target and makes the pause due immediately.

**Call relations**: ScheduleStore.pause calls this helper. It uses workspace_tx for one atomic database operation, object_agent_id for agent scoping, and _utc when returning timestamps from the row.

*Call graph*: calls 1 internal fn (_utc); called by 1 (pause); 7 external calls (__init__, now, exists, select, workspace_tx, object_agent_id, uuid4).


##### `ScheduleStore.cancel`  (lines 509–532)

```
async def cancel(self, expected: ScheduledTask) -> None
```

**Purpose**: Deletes an existing recurring scheduled task. It refuses to cancel one-time workflow pauses through the recurring-task path.

**Data flow**: It receives the ScheduledTask the caller expects to cancel. It checks the current agent, deletes only the row matching that exact task identity and creator, and returns nothing. If no row was deleted, it raises an error because the task likely changed or disappeared.

**Call relations**: This is used by member-facing task controls to remove recurring tasks. It uses the same exact-match pattern as update so cancellation does not accidentally delete a different task.

*Call graph*: 3 external calls (delete, workspace_tx, object_agent_id).


##### `ScheduleStore.list`  (lines 534–552)

```
async def list(self) -> tuple[ScheduledTask, ...]
```

**Purpose**: Lists the recurring scheduled tasks for the current object agent in the current workspace. One-time pause rows are intentionally hidden.

**Data flow**: It reads the current agent id and queries scheduled_task rows for this workspace and agent, excluding @once pause rows. It orders them by name and converts each row into a ScheduledTask.

**Call relations**: Status or scheduling interfaces call this when they need to show a user’s normal tasks. It relies on _task so every returned item has normalized UTC timestamps.

*Call graph*: calls 1 internal fn (_task); 3 external calls (select, workspace_tx, object_agent_id).


##### `ScheduleStore.claim_due`  (lines 554–610)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: Leases a limited batch of due tasks for a worker to fire, while also deleting expired claimable tasks. This is the main protection against two workers doing the same scheduled job.

**Data flow**: It receives the current time, lease length, and maximum number of tasks. It creates a fresh claim id, deletes expired rows that are not under a live claim, selects the oldest due unpaused available rows, stamps them with the claim and lease expiry, and returns them as ScheduledTask objects.

**Call relations**: A workspace-bound scheduled-task runner calls this after due_task_workspaces has identified a candidate workspace. It uses _claim_available and _expired to match discovery rules, and _task to hand back safe task snapshots.

*Call graph*: calls 3 internal fn (_claim_available, _expired, _task); 7 external calls (timedelta, delete, not_, select, update, workspace_tx, uuid4).


##### `ScheduleStore.retire_if_expired`  (lines 612–626)

```
async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool
```

**Purpose**: Deletes a claimed task if it has expired before the worker invokes it. This prevents firing work that is no longer valid.

**Data flow**: It receives a claimed ScheduledTask and the current time. If the task has no claim, it raises an error; if it is not expired, it returns false. If it is expired, it deletes the exact row with the matching claim and returns true.

**Call relations**: The scheduled-task runner calls this during its fire flow before invoking the task. It only deletes rows claimed by that runner, so a stale worker cannot retire someone else’s leased task.

*Call graph*: called by 1 (_fire); 2 external calls (delete, workspace_tx).


##### `ScheduleStore.reschedule`  (lines 628–661)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: Moves a claimed recurring task to its next run time after it has fired. It also clears the claim so future workers can pick it up later.

**Data flow**: It receives the claimed task, the next run time, the last run time, and optionally the turn id created by the fire. It rejects unclaimed tasks and one-time pauses, updates the row if the claim still matches, records the last run details, clears the lease, and returns whether the update succeeded.

**Call relations**: The scheduled-task runner calls this after a successful recurring fire. The claim check ties the update to the exact leased version, so overlapping or late workers cannot overwrite each other’s progress.

*Call graph*: called by 1 (_fire); 2 external calls (update, workspace_tx).


##### `ScheduleStore.inspect`  (lines 663–702)

```
async def inspect(self, expected: ScheduledTask) -> TaskInspection | None
```

**Purpose**: Reads the live status of one recurring scheduled task, including its next run time and the outcome of its latest fired turn. This is used for status display rather than editing.

**Data flow**: It receives the task the caller wants to inspect. It queries the matching recurring task in the current workspace and agent, joins to the latest recorded turn if present, and returns a TaskInspection with timing, turn status, and final response text. If the task is gone or not visible, it returns None.

**Call relations**: Object status rendering uses this to show what happened most recently. It uses object_agent_id for scoping and _utc/_utc_opt so displayed times are consistent.

*Call graph*: calls 2 internal fn (_utc, _utc_opt); 4 external calls (__init__, select, workspace_tx, object_agent_id).


### Billing synchronization
Metronome and Stripe background workflows keep usage, seats, plans, and billing administration synchronized without double-counting.

### `extensions/metronome/ufo_ext_metronome.py`

`orchestration` · `cross-cutting: scheduled billing and metering jobs plus chat tool handling`

This extension is the billing bridge for the system. It has three main jobs. First, it ships settled usage records to Metronome, the billing and metering service. It sends only frozen, already-settled usage deltas, and it gives each event a stable transaction ID, like a receipt number. If the process crashes and sends the same event again, Metronome can recognize it as the same receipt instead of charging twice.

Second, it ships a daily seat-count snapshot. Seats decide which workspace members the agent will answer. The file also exposes chat tools so an admin can grant, revoke, or list seats. When included seats are full, it can ask an admin in chat whether a waiting member should get a paid overage seat.

Third, it supports billing setup. An admin can ask the agent for setup, status, or a billing portal link. Setup creates or reuses a Stripe Customer, stores the intended Metronome package, and returns a short-lived Stripe portal URL where the admin can save a card. A scheduled activation job later checks Stripe for a saved payment method, creates or finds the matching Metronome customer and contract, and tells the original conversation when the plan is live.

The important theme is durable identity. Customers, contracts, usage events, and seat snapshots all use stable keys, so retrying is safe and conflicts are reconciled instead of papered over.

#### Function details

##### `UsageShipper.run`  (lines 197–212)

```
async def run(self) -> None
```

**Purpose**: Ships one workspace's pending usage records to Metronome in batches. It exists so settled usage becomes billable meter events without losing records or double-counting them after a retry.

**Data flow**: It reads the Metronome bearer token from the environment and gets a fixed backfill floor for the workspace. It repeatedly asks the extension context for pending usage exports after that floor, turns them into Metronome events, posts them, logs the shipment, and only then marks those exports as acknowledged. It stops when there is no more work or the last batch was smaller than the batch size.

**Call relations**: This is the main worker used by the scheduled usage job through `_ship`. It relies on `_floor` to decide how far back to look, `_events` to shape internal usage exports into Metronome payloads, `_ingest` to send them, and `_require_env` to fail clearly if billing cannot be reached.

*Call graph*: calls 4 internal fn (_events, _floor, _ingest, _require_env); 1 external calls (log).


##### `UsageShipper._floor`  (lines 214–224)

```
async def _floor(self) -> datetime
```

**Purpose**: Finds or creates the earliest time from which this workspace's usage should be shipped. This prevents a first run from sending an unlimited historical backfill while still keeping later delayed records eligible.

**Data flow**: It reads a stored timestamp from the workspace extension store. If none exists, it writes a new timestamp set to seven days before the current time and returns it. If one already exists, it parses that saved timestamp and returns the same floor forever.

**Call relations**: UsageShipper.run calls this before asking for pending exports. Its returned time becomes the lower bound used by the core usage-export seam.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._events`  (lines 226–245)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts internal usage export records into the event shape Metronome expects. Each event includes stable identifying information and billing labels such as model, amount, price, and whether the workspace used its own provider key.

**Data flow**: It takes a tuple of `UsageExport` records and reads the workspace ID from the context. For each export, it builds a dictionary with a deterministic transaction ID, customer ID, event type, timestamp, and string-valued properties. The result is a list of event dictionaries ready to post to Metronome.

**Call relations**: UsageShipper.run calls this right before `_ingest`. It uses `_rfc3339` to format the occurrence time in a provider-friendly timestamp format.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 248–249)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry function for usage shipping. It creates a `UsageShipper` for the current workspace context and starts it.

**Data flow**: It receives an extension context from the job runner. It wraps that context, plus the optional test transport, in a `UsageShipper` and awaits its run. It returns nothing; the effect is that pending usage may be sent and acknowledged.

**Call relations**: The manifest registers `_ship` as the handler for the usage shipping job. `_ship` is intentionally small: it hands the real work to `UsageShipper.run`.

*Call graph*: 1 external calls (__init__).


##### `SeatShipper.run`  (lines 262–278)

```
async def run(self) -> None
```

**Purpose**: Sends Metronome one daily snapshot of how many seats a workspace has in use. It also initializes the workspace's seat limit and included-seat allowance if they have not been set yet.

**Data flow**: It reads the Metronome token, checks whether today's seat snapshot was already shipped, and exits if so. Otherwise it opens a database transaction, ensures default seat settings exist, reads the current seat snapshot, posts a single Metronome event, logs it, and records today's date as shipped.

**Call relations**: This is called by `_ship_seats`, the scheduled daily seat job. It uses `_event` to build the payload, `_ingest` to send it, and `_require_env` to make missing provider configuration fail loudly.

*Call graph*: calls 3 internal fn (_event, _ingest, _require_env); 3 external calls (__init__, now, log).


##### `SeatShipper._event`  (lines 280–291)

```
def _event(self, snapshot: SeatSnapshot, today: str) -> dict[str, object]
```

**Purpose**: Builds the Metronome event for a workspace's daily seat count. The event is keyed by workspace and date so retrying the same day is safe.

**Data flow**: It takes a seat snapshot and today's date string. It reads the workspace ID, makes a transaction ID like `seats:<workspace>:<date>`, adds the current timestamp, and includes seat count and seat limit as properties. It returns one event dictionary.

**Call relations**: SeatShipper.run calls this after reading the seat snapshot and before sending it through `_ingest`. It uses `_rfc3339` for the event timestamp.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 1 external calls (now).


##### `_ship_seats`  (lines 294–295)

```
async def _ship_seats(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry function for daily seat shipping. It starts a `SeatShipper` for the workspace being processed.

**Data flow**: It receives an extension context, creates a `SeatShipper` with that context and optional test transport, and awaits its run. It returns nothing; its side effect is the daily seat event if one is due.

**Call relations**: The manifest registers `_ship_seats` as the handler for the seat shipping job. It delegates the real work to `SeatShipper.run`.

*Call graph*: 1 external calls (__init__).


##### `SeatApprovals.run`  (lines 311–336)

```
async def run(self) -> None
```

**Purpose**: Finds unseated members who need admin approval once included seats are full, and asks an admin about them in chat. This keeps overage-seat consent inside the normal agent conversation instead of adding a separate approval screen.

**Data flow**: It reads the current seat snapshot in a transaction. If there is still included capacity, it stops. Otherwise it looks at unseated members, skips anyone already marked as asked, finds an admin conversation, invokes the agent with an approval prompt, and then stores a marker saying this member has already been asked about.

**Call relations**: The scheduled seat-approval job calls this through `_ask_seat_approvals`. It uses the seats subsystem to inspect membership and `admin_conversation` to find where to send the request.

*Call graph*: 3 external calls (__init__, now, admin_conversation).


##### `_ask_seat_approvals`  (lines 339–340)

```
async def _ask_seat_approvals(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry function for seat approval prompts. It creates a `SeatApprovals` runner for the current workspace.

**Data flow**: It receives an extension context, creates `SeatApprovals`, and awaits its run. The result is no direct return value, but it may create a chat prompt and store an asked marker.

**Call relations**: The manifest registers this as the handler for the frequent seat approval job. It delegates to `SeatApprovals.run`.

*Call graph*: 1 external calls (__init__).


##### `BillingConfig.from_env`  (lines 359–378)

```
def from_env(cls) -> 'BillingConfig'
```

**Purpose**: Reads and validates the environment variables needed for billing setup and activation. It prevents a half-configured deployment from creating provider objects and then failing midway.

**Data flow**: It reads the Stripe secret key, Stripe portal configuration ID, Metronome bearer token, and Metronome package alias from environment variables. If any are missing, it raises one error listing all missing names. If all exist, it returns a frozen `BillingConfig` object containing those values.

**Call relations**: Billing tool actions and the billing activation job call this before making Stripe or Metronome billing calls. Usage and seat shipping do not use this full config, because they only need the Metronome bearer token.


##### `BillingActivation.run`  (lines 415–442)

```
async def run(self) -> None
```

**Purpose**: Turns a workspace's saved card into an active Metronome contract. It is the background follow-through after an admin receives a Stripe payment setup link.

**Data flow**: It reads the stored billing record and exits if there is none or it is already activated. It loads billing configuration, checks Stripe for a default payment method, creates or finds the Metronome customer, stores that ID, creates or finds the Metronome contract, stores that ID, and then notifies the original conversation.

**Call relations**: The scheduled billing activation job calls this through `_activate_billing`. It coordinates helper functions for reading state, checking Stripe, creating Metronome objects, saving progress with `_store`, and notifying with `_notify`.

*Call graph*: calls 7 internal fn (_notify, _store, _billing_record, _contract_key, _has_default_payment_method, _metronome_contract, _metronome_customer).


##### `BillingActivation._store`  (lines 444–446)

```
async def _store(self, record: BillingRecord) -> BillingRecord
```

**Purpose**: Saves the current billing record back to the workspace extension store. It is used after each durable step so the next job tick can resume from the right place.

**Data flow**: It takes a `BillingRecord`, converts it into JSON-friendly data, writes it under the billing store key, and returns the same record. The workspace store changes; the record content does not.

**Call relations**: BillingActivation.run calls this after discovering or creating provider IDs. BillingActivation._notify also calls it after marking activation complete.

*Call graph*: called by 2 (_notify, run); 1 external calls (model_dump).


##### `BillingActivation._notify`  (lines 448–461)

```
async def _notify(self, record: BillingRecord) -> None
```

**Purpose**: Tells the original admin conversation that billing is now active, then marks the billing record as activated. This makes the confirmation happen once.

**Data flow**: It receives a billing record with provider IDs. It sends an internal agent invocation to the saved conversation and agent, using an idempotency key based on the workspace. Then it stores a copy of the record with `activated_at` set to the current time and logs the activation.

**Call relations**: BillingActivation.run calls this after the Metronome contract is known. It hands persistence back to `_store` so the activation mark is saved after the notification is accepted.

*Call graph*: calls 1 internal fn (_store); called by 1 (run); 3 external calls (now, model_copy, log).


##### `_activate_billing`  (lines 464–465)

```
async def _activate_billing(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry function for billing activation. It starts the activation workflow for one workspace.

**Data flow**: It receives an extension context, creates `BillingActivation` with that context and optional test transport, and awaits its run. It returns nothing, but may update billing state and send a chat notification.

**Call relations**: The manifest registers `_activate_billing` as the billing activation job handler. It delegates the real workflow to `BillingActivation.run`.

*Call graph*: 1 external calls (__init__).


##### `_billing_record`  (lines 468–470)

```
async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None
```

**Purpose**: Loads the stored billing setup state for a workspace, if any. This is the shared way the file remembers Stripe and Metronome provider IDs across tool calls and job ticks.

**Data flow**: It reads the billing key from the extension store. If nothing is stored, it returns `None`. If data exists, it validates and converts it into a `BillingRecord` object.

**Call relations**: BillingActivation.run, `_billing_setup`, `_billing_status`, and `_billing_portal` all call this before deciding what billing action is possible.

*Call graph*: called by 4 (run, _billing_portal, _billing_setup, _billing_status).


##### `_contract_key`  (lines 473–477)

```
def _contract_key(workspace_id: UUID) -> str
```

**Purpose**: Creates the permanent Metronome contract identity for a workspace. This stable key lets the code recognize the workspace's own contract later, even if other contracts exist on the same customer.

**Data flow**: It takes a workspace UUID and formats it into a string beginning with `ufo-contract:`. It returns that string and changes nothing.

**Call relations**: BillingActivation.run uses this before creating or finding the contract. `_billing_status` uses the same key to report whether this workspace's plan is live.

*Call graph*: called by 2 (run, _billing_status).


##### `grant_seat`  (lines 515–521)

```
async def grant_seat(ctx: ToolContext, args: GrantSeatInput) -> ToolResult
```

**Purpose**: Chat tool handler that grants a seat to a workspace member by email. It lets an admin make the agent start answering that member.

**Data flow**: It verifies the speaker can administer seats, opens a transaction, grants the seat through the core seats subsystem, reads the updated snapshot, and returns the snapshot as JSON text. The seat table changes if the grant succeeds.

**Call relations**: The manifest exposes this as the `grant_seat` tool. It calls `_admin_seats` for permission and seat access, then `_snapshot_result` to format the response.

*Call graph*: calls 2 internal fn (_admin_seats, _snapshot_result).


##### `revoke_seat`  (lines 524–534)

```
async def revoke_seat(ctx: ToolContext, args: RevokeSeatInput) -> ToolResult
```

**Purpose**: Chat tool handler that removes a member's seat by email. It lets an admin stop the agent from answering that member, subject to core rules such as not revoking the last seated admin.

**Data flow**: It verifies the speaker can administer seats, opens a transaction, revokes the seat, reads the updated snapshot, and then stores a marker so the member is not automatically asked about again. It returns the updated seat snapshot as JSON text.

**Call relations**: The manifest exposes this as the `revoke_seat` tool. It shares permission checking with `grant_seat` through `_admin_seats` and response formatting through `_snapshot_result`.

*Call graph*: calls 2 internal fn (_admin_seats, _snapshot_result); 1 external calls (now).


##### `list_seats`  (lines 537–541)

```
async def list_seats(ctx: ToolContext, args: ListSeatsInput) -> ToolResult
```

**Purpose**: Chat tool handler that reports the workspace's seat limit, included allowance, overage count, and member seat status. It is read-only.

**Data flow**: It opens a transaction, reads the seat snapshot for the current workspace, and returns that snapshot as JSON text. It does not change seat state.

**Call relations**: The manifest exposes this as the `list_seats` tool. It calls the seats subsystem directly and uses `_snapshot_result` for the tool response.

*Call graph*: calls 1 internal fn (_snapshot_result); 1 external calls (__init__).


##### `manage_billing`  (lines 544–553)

```
async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Chat tool handler for billing setup, billing status, and Stripe portal access. It is the agent-facing doorway for admins to start or inspect paid billing.

**Data flow**: It verifies the speaker can manage billing, loads billing configuration, checks the requested action, and dispatches to setup, status, or portal helper functions. It returns a tool result containing JSON text, or raises an error if the action cannot be completed.

**Call relations**: The manifest exposes this as the `manage_billing` tool. It calls `_admin_billing` first, then routes to `_billing_setup`, `_billing_status`, or `_billing_portal`.

*Call graph*: calls 4 internal fn (_admin_billing, _billing_portal, _billing_setup, _billing_status).


##### `_admin_billing`  (lines 556–562)

```
async def _admin_billing(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Checks that a billing tool call is being made by a real speaking workspace admin. This prevents non-admins or system-only turns from changing billing.

**Data flow**: It reads the tool context to make sure there is a speaker member. It asks the context whether that speaker is an admin. If either check fails, it raises an error; otherwise it returns the extension context needed for billing state.

**Call relations**: manage_billing calls this before any billing action. It delegates the admin check to the tool context's `speaker_is_admin` method.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 1 (manage_billing).


##### `_billing_setup`  (lines 565–605)

```
async def _billing_setup(ctx: ToolContext, ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Starts billing setup by creating or reusing the workspace's Stripe Customer, saving the intended plan, and returning a Stripe portal link for adding a payment method.

**Data flow**: It reads any existing billing record. If none exists, it creates a Stripe Customer, builds a new billing record with the configured Metronome package and the initiating conversation, and stores it. Then it creates a short-lived payment-method portal session and returns the URL, customer ID, and package as JSON text.

**Call relations**: manage_billing calls this for the `setup` action. It uses `_billing_record` to avoid overwriting existing progress, `_stripe_customer` and `_portal_session` for Stripe calls, `_text_result` for the response, and logging for observability.

*Call graph*: calls 4 internal fn (_billing_record, _portal_session, _stripe_customer, _text_result); called by 1 (manage_billing); 3 external calls (__init__, now, log).


##### `_billing_status`  (lines 608–636)

```
async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Reports what Stripe and Metronome currently say about this workspace's billing. It uses provider truth instead of trusting only local stored flags.

**Data flow**: It loads the billing record. If there is none, it returns `configured: false`. Otherwise it asks Stripe whether a default payment method exists and, if a Metronome customer is known, asks Metronome whether the workspace's own contract exists. It returns these facts as JSON text.

**Call relations**: manage_billing calls this for the `status` action. It uses `_has_default_payment_method`, `_contract_for`, and `_contract_key` to check provider state, then `_text_result` to send the answer back.

*Call graph*: calls 5 internal fn (_billing_record, _contract_for, _contract_key, _has_default_payment_method, _text_result); called by 1 (manage_billing).


##### `_billing_portal`  (lines 639–647)

```
async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Returns a fresh Stripe Customer Portal link for an already configured workspace. Admins use it for invoices, payment methods, and billing details.

**Data flow**: It loads the billing record and raises an error if setup has never been run. If a record exists, it creates a general portal session for the stored Stripe Customer and returns the URL as JSON text.

**Call relations**: manage_billing calls this for the `portal` action. It uses `_billing_record` to find the customer and `_portal_session` to ask Stripe for the link.

*Call graph*: calls 3 internal fn (_billing_record, _portal_session, _text_result); called by 1 (manage_billing).


##### `_admin_seats`  (lines 650–655)

```
async def _admin_seats(ctx: ToolContext) -> Seats
```

**Purpose**: Checks that a seat-changing tool call is made by a real speaking workspace admin, then returns the seat helper for that workspace.

**Data flow**: It reads the tool context to ensure there is a speaking member and asks whether that member is an admin. If either check fails, it raises an error. If both pass, it returns a `Seats` object for the current workspace.

**Call relations**: grant_seat and revoke_seat both call this before changing seats. It centralizes the permission check so both tools follow the same rule.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 2 (grant_seat, revoke_seat); 1 external calls (__init__).


##### `_snapshot_result`  (lines 658–672)

```
def _snapshot_result(snapshot: SeatSnapshot) -> ToolResult
```

**Purpose**: Formats a seat snapshot into the JSON response returned by seat tools. It turns internal seat data into an easy-to-read summary for the agent.

**Data flow**: It takes a `SeatSnapshot`, calculates billed overage seats as seated members beyond the included allowance, and builds a dictionary containing limits, counts, and member entries. It wraps that dictionary as a text tool result.

**Call relations**: grant_seat, revoke_seat, and list_seats call this after reading a snapshot. It hands off to `_text_result` for the final tool-result packaging.

*Call graph*: calls 1 internal fn (_text_result); called by 3 (grant_seat, list_seats, revoke_seat).


##### `_text_result`  (lines 675–676)

```
def _text_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary as a tool response whose content is JSON text. It provides one consistent output shape for the chat tools.

**Data flow**: It takes a dictionary, serializes it with JSON, places the string in a `TextContent` object, and returns a `ToolResult` containing that content. It does not change any stored state.

**Call relations**: Billing and seat response helpers call this whenever they need to return structured information to the agent.

*Call graph*: called by 4 (_billing_portal, _billing_setup, _billing_status, _snapshot_result); 3 external calls (__init__, __init__, dumps).


##### `_require_env`  (lines 708–712)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads a required environment variable and raises a clear error if it is missing. This avoids quiet failures when provider credentials are not configured.

**Data flow**: It takes an environment variable name, looks it up in the process environment, and returns its value if present. If the value is empty or missing, it raises a runtime error naming the missing setting.

**Call relations**: UsageShipper.run and SeatShipper.run use this before sending Metronome ingest events.

*Call graph*: called by 2 (run, run).


##### `_stripe_customer`  (lines 715–732)

```
async def _stripe_customer(config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or reuses the workspace's one Stripe Customer. It uses a stable idempotency key so retrying does not create duplicate customers.

**Data flow**: It takes billing config, a workspace ID, and optional HTTP transport. It sends Stripe a customer creation request with workspace metadata and a deterministic idempotency key. It extracts and returns the customer ID from Stripe's response.

**Call relations**: _billing_setup calls this when no local billing record exists yet. It uses `_stripe` for the HTTP call and `_as_str` to validate the returned ID.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_setup).


##### `_portal_session`  (lines 735–751)

```
async def _portal_session(config: BillingConfig, customer_id: str, flow: str | None, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates a short-lived Stripe Customer Portal URL. During setup it can be narrowed to payment-method update only; later it can open the broader billing portal.

**Data flow**: It takes billing config, a Stripe customer ID, an optional flow name, and optional transport. It builds Stripe form data, includes the portal configuration, optionally includes the flow type, sends the request, and returns the session URL.

**Call relations**: _billing_setup calls this for payment setup links, and `_billing_portal` calls it for general portal links. It uses `_stripe` for the request and `_as_str` to validate the URL.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 2 (_billing_portal, _billing_setup).


##### `_has_default_payment_method`  (lines 754–764)

```
async def _has_default_payment_method(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> bool
```

**Purpose**: Checks whether Stripe has a default payment method saved for a customer. This is the gate that decides whether billing activation may proceed.

**Data flow**: It fetches the Stripe Customer by ID and looks inside `invoice_settings` for a string default payment method. It returns `true` if one exists and `false` otherwise.

**Call relations**: BillingActivation.run calls this before creating Metronome billing objects. `_billing_status` also calls it to report whether a card is on file.

*Call graph*: calls 1 internal fn (_stripe); called by 2 (run, _billing_status).


##### `_stripe`  (lines 767–785)

```
async def _stripe(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, data: dict[str, str] | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Sends a low-level HTTP request to Stripe and returns the decoded JSON response. It centralizes Stripe authentication, API version pinning, timeout, and error handling.

**Data flow**: It takes billing config, HTTP method, Stripe path, optional form data, optional idempotency key, and optional transport. It builds headers, sends the request with `httpx`, raises `StripeError` for non-success responses, and returns the JSON body for successful responses.

**Call relations**: _stripe_customer, `_portal_session`, and `_has_default_payment_method` all use this instead of talking to Stripe directly.

*Call graph*: called by 3 (_has_default_payment_method, _portal_session, _stripe_customer); 2 external calls (__init__, AsyncClient).


##### `_metronome_customer`  (lines 788–833)

```
async def _metronome_customer(config: BillingConfig, alias: str, stripe_customer_id: str, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Finds or creates the workspace's Metronome customer, tied to the workspace ID as an ingest alias. This makes usage events land on the same customer that owns the billing contract.

**Data flow**: It first asks Metronome whether a customer already has the workspace alias. If found, it returns that customer ID. If not, it creates a customer with the alias and Stripe billing-provider configuration. If creation conflicts, it looks up the alias again and returns the reconciled customer ID.

**Call relations**: BillingActivation.run calls this after Stripe has a default payment method and before creating the contract. It uses `_customer_by_alias` for lookup and `_metronome` for provider calls.

*Call graph*: calls 2 internal fn (_customer_by_alias, _metronome); called by 1 (run); 1 external calls (__init__).


##### `_customer_by_alias`  (lines 836–845)

```
async def _customer_by_alias(config: BillingConfig, alias: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Looks up a Metronome customer by ingest alias. The alias is the workspace ID stamped on usage events.

**Data flow**: It sends a Metronome customer-list request filtered by the alias. If the response contains at least one customer with an ID, it returns that ID. Otherwise it returns `None`.

**Call relations**: _metronome_customer` calls this before creating a customer and again when reconciling a conflict. It uses `_metronome` for the HTTP request.

*Call graph*: calls 1 internal fn (_metronome); called by 1 (_metronome_customer).


##### `_metronome_contract`  (lines 848–884)

```
async def _metronome_contract(config: BillingConfig, customer_id: str, record: BillingRecord, uniqueness_key: str, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Finds or creates the workspace's Metronome contract for the intended package. The contract is the live plan, and it is identified by a stable uniqueness key.

**Data flow**: It first checks whether a contract with the workspace's uniqueness key already exists for the customer. If found, it returns the contract ID. If not, it posts a contract creation request using the stored package and stored start time. If creation conflicts, it checks again and returns the reconciled contract ID.

**Call relations**: BillingActivation.run calls this after the Metronome customer ID is known. It relies on `_contract_for` for lookup, `_metronome` for creation, and `_rfc3339` to format the stored start time.

*Call graph*: calls 3 internal fn (_contract_for, _metronome, _rfc3339); called by 1 (run); 1 external calls (__init__).


##### `_contract_for`  (lines 887–910)

```
async def _contract_for(config: BillingConfig, customer_id: str, uniqueness_key: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Finds this workspace's own live Metronome contract on a customer by matching the stable uniqueness key. It avoids mistaking another contract on the customer for this workspace's plan.

**Data flow**: It asks Metronome to list contracts for the customer. It scans the returned contracts and returns the ID of the one whose `uniqueness_key` matches the requested key. If no matching contract is found, it returns `None`.

**Call relations**: _metronome_contract` uses this before creating and after conflicts. `_billing_status` uses it to report whether the workspace's plan is active.

*Call graph*: calls 1 internal fn (_metronome); called by 2 (_billing_status, _metronome_contract).


##### `_metronome`  (lines 913–933)

```
async def _metronome(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, body: dict[str, object] | None=None, params: dict[str, str] | None=None, idempotency_key
```

**Purpose**: Sends a low-level HTTP request to Metronome and returns the decoded JSON response. It centralizes bearer-token authentication, timeout, conflict detection, and error handling.

**Data flow**: It takes billing config, HTTP method, API path, optional JSON body, optional query parameters, optional idempotency key, and optional transport. It sends the request with `httpx`, raises `MetronomeConflict` for HTTP 409, raises `MetronomeError` for other failures, and returns the JSON body on success.

**Call relations**: Customer and contract helper functions use this instead of making Metronome billing API calls directly.

*Call graph*: called by 4 (_contract_for, _customer_by_alias, _metronome_contract, _metronome_customer); 3 external calls (__init__, __init__, AsyncClient).


##### `_as_str`  (lines 936–940)

```
def _as_str(value: object, field: str) -> str
```

**Purpose**: Validates that a provider response field is a non-empty string. It catches malformed or unexpected provider responses close to where they are read.

**Data flow**: It receives any value and a human-readable field name. If the value is a non-empty string, it returns it. Otherwise it raises a value error naming the missing field.

**Call relations**: _stripe_customer uses this to read a Stripe Customer ID, and `_portal_session` uses it to read a Stripe portal URL.

*Call graph*: called by 2 (_portal_session, _stripe_customer).


##### `_ingest`  (lines 943–951)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Posts usage or seat events to Metronome's ingest endpoint. This is the shared transport function for metered events.

**Data flow**: It takes a bearer token, a list of event dictionaries, and optional HTTP transport. It sends the events as JSON with authorization. If Metronome rejects the request, it raises `MetronomeError`; otherwise it returns nothing.

**Call relations**: UsageShipper.run calls this for usage batches, and SeatShipper.run calls it for daily seat snapshots.

*Call graph*: called by 2 (run, run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 954–956)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: Formats a Python datetime as an RFC 3339-style timestamp string. That is the timestamp format expected by the external APIs used here.

**Data flow**: It takes a datetime. If the datetime has no timezone, it treats it as UTC; otherwise it leaves its timezone intact. It returns the ISO-formatted timestamp string.

**Call relations**: UsageShipper._events and SeatShipper._event use this for ingest event timestamps. `_metronome_contract` uses it for contract start times.

*Call graph*: called by 3 (_event, _events, _metronome_contract); 1 external calls (replace).


##### `manifest`  (lines 959–1009)

```
def manifest() -> Manifest
```

**Purpose**: Describes the extension to the UFO host system: its name, tools, scheduled jobs, prompt text, and credential slot. Without this, the host would not know what this extension offers or when to run it.

**Data flow**: It builds and returns a `Manifest` object. The manifest contains four chat tools, four scheduled jobs with candidate workspace selectors, two prompt sections that teach the agent about seats and billing, and one credential slot for a workspace-provided Anthropic API key.

**Call relations**: The extension loader calls this to register the Metronome extension. The scheduled jobs point back to `_ship`, `_ship_seats`, `_ask_seat_approvals`, and `_activate_billing`, while the tools point to the seat and billing handlers in this file.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, metered_workspaces, member_workspaces).


### Evaluation fixtures
The evaluation environment supplies controlled connector-backed email, calendar, and code-search data for repeatable tests.

### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `evaluation setup and connector request handling`

This file is like a practice office for an AI agent: it has a mailbox, a calendar, and a code search tool, but all of them are fake in the useful sense that evaluators can seed their exact starting data and later check the exact ending data. The important point is that the agent does not talk to a mock shortcut. It still goes through the normal connector system: discover tools, inspect schemas, and call tools.

The file declares three connector providers: evaluation email, evaluation calendar, and evaluation code search. Email and calendar use database tables because the agent can change them by sending mail, creating events, updating events, or cancelling events. Code search is read-only and returns a pre-seeded response from workspace-scoped storage, so tests can control the exact bytes the agent sees.

`EvalEnvBroker` is the main worker. It advertises available tools, validates incoming tool arguments with Pydantic models, performs database reads and writes, and formats results back into plain dictionaries. `_EvalEnvOAuth` is a minimal stand-in for OAuth, which is the web sign-in flow normally used to connect accounts. Evaluations seed credentials directly, but the connector registry still expects an OAuth-shaped object. Finally, `manifest()` packages all of this so the host application can load the evaluation extension.

#### Function details

##### `_transaction`  (lines 171–175)

```
def _transaction()
```

**Purpose**: Opens a database transaction for this extension's workspace-scoped storage. The broker uses it whenever email or calendar rows need to be read or changed safely.

**Data flow**: It takes no direct inputs. It builds an extension context with a scoped store for `eval_env` and no declared credential access, then returns a transaction object that callers enter before running database commands.

**Call relations**: Email and calendar operations call this before touching their tables. It is the shared doorway that `_send_email`, `_list_emails`, `_create_event`, `_list_events`, and `_change_event` use to make their database work durable.

*Call graph*: called by 5 (_change_event, _create_event, _list_emails, _list_events, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 178–182)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns an ISO 8601 time string into a real datetime value. If the string has no time zone, it treats it as UTC so stored calendar times are always time-zone aware.

**Data flow**: It receives a text timestamp, parses it, checks whether a time zone is present, and adds UTC when one is missing. It returns a datetime object ready to store in the calendar table.

**Call relations**: Calendar creation and updates call this before writing event times. That keeps `_create_event` and `_update_event` from storing ambiguous plain strings.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 190–198)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the tools available for one evaluation provider, optionally filtered by a search phrase. This is how the connector can answer, “what can this email, calendar, or code provider do?”

**Data flow**: It receives a workspace id, provider name, and query text. It looks up that provider's catalog, compares the query against tool names and descriptions, and returns matching tools; if nothing matches, it falls back to the full catalog.

**Call relations**: The broker's `search` method calls this when the host asks for searchable connector tools. It provides the tool list that is wrapped into a `BrokerSearch` response.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 200–204)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the input schema for one named tool. A schema is the recipe that tells the agent what arguments a tool accepts.

**Data flow**: It receives a workspace id, provider name, and tool slug. It scans the provider's catalog and returns the matching `BrokerTool`; if no such tool exists, it raises an unknown-tool error.

**Call relations**: This supports the normal connector flow after tools have been discovered. If the host asks about a tool name this provider does not own, it hands back a clear failure instead of guessing.

*Call graph*: 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 206–245)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Routes an actual tool call to the right email, calendar, or code-search action. It is the central dispatcher for all evaluation connector calls.

**Data flow**: It receives the workspace, provider, tool slug, raw argument mapping, account id, and idempotency key. It validates the raw arguments against the correct argument model, calls the matching private helper, and returns that helper's result dictionary. Unknown provider/tool combinations become an unknown-tool error.

**Call relations**: The connector runtime calls this when the agent invokes a tool. From there it hands work to `_send_email`, `_list_emails`, `_create_event`, `_list_events`, `_update_event`, `_cancel_event`, or `_search_code` depending on the request.

*Call graph*: calls 7 internal fn (_cancel_event, _create_event, _list_emails, _list_events, _search_code, _send_email, _update_event); 1 external calls (__init__).


##### `EvalEnvBroker._search_code`  (lines 247–255)

```
async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]
```

**Purpose**: Returns a pre-seeded code-search response for the exact query the agent asked. It intentionally fails if the fixture is missing, so a broken evaluation setup is noticed immediately.

**Data flow**: It receives validated search arguments containing a query string. It reads scoped storage using a key made from the code-search prefix plus that query, checks that the stored value is a dictionary, copies it, and returns it.

**Call relations**: `execute` calls this for the `search_code` tool. Unlike email and calendar helpers, it does not write tables; it reads the fixture that the evaluation author prepared beforehand.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 257–272)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Adds a sent email to the evaluation mailbox. This lets a test verify that the agent actually sent the expected message.

**Data flow**: It receives a workspace id and validated email arguments. It creates a new email id, records the sender as the fixed evaluation address, stores recipients, subject, body, current time, and the `sent` folder in the email table, then returns the new id and sent status.

**Call relations**: `execute` calls this for the `send_email` tool. It uses `_transaction` to make the insert durable so a grader in another process can later inspect the same stored row.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 274–309)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Reads emails from the evaluation mailbox, newest first, with optional text filtering. This gives the agent a realistic way to inspect seeded inbox or sent mail.

**Data flow**: It receives a workspace id and validated list arguments. It builds database conditions for workspace and folder, optionally adds a case-insensitive substring match over sender, subject, and body, queries the email table up to the requested limit, and returns a list of email dictionaries.

**Call relations**: `execute` calls this for the `list_emails` tool. It uses `_transaction` for the database read and returns data in the shape the connector response expects.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 311–325)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Creates a confirmed calendar event in the evaluation calendar. This gives tests a persistent record of appointments the agent scheduled.

**Data flow**: It receives a workspace id and validated event arguments. It creates a new event id, parses the start and end strings into datetime values, stores title, times, attendees, and confirmed status in the event table, and returns the id and status.

**Call relations**: `execute` calls this for the `create_event` tool. It relies on `_moment` for time parsing and `_transaction` for the database insert.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 327–340)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Reads calendar events for the workspace in start-time order, optionally filtered by title. This lets the agent see both active and cancelled events.

**Data flow**: It receives a workspace id and validated list arguments. It builds a workspace filter, optionally adds a title substring filter, queries the event table up to the requested limit, converts each row with `_event_json`, and returns them under an `events` key.

**Call relations**: `execute` calls this for the `list_events` tool. It uses `_transaction` to read the table and `_event_json` so listed events use the same output format as changed events.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 342–354)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Prepares changes for an existing calendar event. It only changes fields the caller supplied, and rejects empty updates because they would do nothing.

**Data flow**: It receives a workspace id and validated update arguments. It builds a changes dictionary from any provided title, start, end, or attendees; parses supplied times; then asks `_change_event` to write those changes and return the updated event.

**Call relations**: `execute` calls this for the `update_event` tool. It does the argument-to-database-field translation, while `_change_event` performs the shared update-and-read-back work.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 356–357)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Marks an existing event as cancelled without deleting it. Keeping the row visible lets tests and agents see that cancellation happened.

**Data flow**: It receives a workspace id and validated cancel arguments. It builds a single change setting the event status to cancelled, then returns the result from `_change_event`.

**Call relations**: `execute` calls this for the `cancel_event` tool. It reuses `_change_event` so cancellation follows the same workspace checks and output formatting as other event updates.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 359–378)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Applies a set of changes to one calendar event and returns the fresh event record. It also protects workspace boundaries by only updating an event that belongs to the given workspace.

**Data flow**: It receives a workspace id, event id string, and dictionary of new values. It converts the event id to a UUID, updates the matching row, checks that exactly one row changed, reads the updated row back, and returns it as a plain event dictionary.

**Call relations**: `_update_event` and `_cancel_event` both call this after deciding what should change. It uses `_transaction` for the database work and `_event_json` to produce the final response.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 380–388)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Converts a database event row into the plain response format returned to agents and graders. It hides database-specific details such as UUID and datetime objects.

**Data flow**: It receives a row from the event table. It turns the id and times into strings, copies title, attendees, and status, and returns a dictionary suitable for JSON-style connector output.

**Call relations**: `_list_events` uses this for each listed event, and `_change_event` uses it after updating or cancelling an event. This keeps event responses consistent across read and write operations.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 390–391)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: States that these evaluation tools do not produce downloadable files. The connector interface asks for this hook, so the broker answers with an empty list.

**Data flow**: It receives a tool response dictionary but does not inspect it. It always returns an empty tuple, meaning there are no broker files to attach.

**Call relations**: This fits the broker interface alongside tool execution. Since email, calendar, and code search here return inline data only, nothing is handed off for file output.


##### `EvalEnvBroker.stage_upload`  (lines 393–402)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for the evaluation providers. None of these tools accept uploaded files, so accepting one would hide a test or caller mistake.

**Data flow**: It receives workspace, provider, tool, filename, mimetype, and checksum information. Instead of creating an upload target, it raises an error saying uploads are not supported.

**Call relations**: The connector runtime may call this for providers that support file inputs. In this broker, it deliberately stops that path because all supported evaluation tools are text-and-data only.


##### `EvalEnvBroker.search`  (lines 404–405)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps the provider's matching tools in the standard search response object. This is used when the host searches connector capabilities.

**Data flow**: It receives a workspace id, provider name, and query. It asks `tools` for matching broker tools, then returns a `BrokerSearch` object containing them.

**Call relations**: This is a thin connector-facing layer over `tools`. The host asks for a search result, and this method supplies one in the shape the connector system expects.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 407–408)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a simple bearer credential for an evaluation account. A bearer credential is a token-like string used to prove access, but here it is deterministic test data.

**Data flow**: It receives a workspace id, provider name, and account id. It builds and returns a credential whose bearer value includes the evaluation account name.

**Call relations**: The connector system calls this when it needs credentials to call a provider. Because the provider is local to the evaluation environment, this creates a harmless synthetic credential instead of contacting a real service.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 419–420)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a pretend OAuth authorization URL for the evaluation provider. OAuth is the normal browser-based account connection flow, but evaluations usually do not drive it.

**Data flow**: It receives a state value and redirect URI. It combines them with the provider's fake host into an authorization URL string and returns it.

**Call relations**: The manifest includes `_EvalEnvOAuth` objects because connector providers require an OAuth descriptor. If a connect flow asks for a URL, this gives a plausible stub URL.


##### `_EvalEnvOAuth.exchange`  (lines 422–425)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the pretend OAuth exchange by returning the fixed evaluation account id. It does not validate a real external service code.

**Data flow**: It receives an authorization code, redirect URI, workspace id, and state. It ignores external verification and returns an `OAuthAccount` with the known evaluation account id.

**Call relations**: This is present to satisfy the connector registration contract. Evaluations normally seed grants directly, but if the exchange path is exercised, it still returns a consistent account object.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 428–450)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension manifest that tells the host application about the evaluation email, calendar, and code-search connectors. Without it, the extension would not be discoverable.

**Data flow**: It creates one shared `EvalEnvBroker`, creates three connector provider entries with labels, OAuth stubs, and that broker, then returns a `Manifest` containing the extension name, version, and connectors.

**Call relations**: The extension loader calls this at startup or registration time. It wires together `_EvalEnvOAuth`, `EvalEnvBroker`, and the connector provider metadata so later tool discovery and execution can reach the broker.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### Self-improvement proposal inputs
Self-improvement begins by mining failed conversations, making controlled model calls, and proposing candidate prompt changes.

### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus building`

The self-improvement loop needs examples of real problems to learn from and separate examples to test against. This file builds those examples from recorded trajectories, meaning saved conversations with their tool calls and results. It looks for the first tool error in each conversation and treats that as a useful “friction signal”: like a warning light on a machine, it marks where the system had trouble.

Each useful conversation becomes a TaskExample. That example keeps the conversation ID, the user’s original request, all messages needed to replay the situation, and a plain text description of what went wrong. Examples are grouped into TaskClass objects by the tool that failed, using names like `tool:search` or `tool:edit`. This matters because improvements are judged per kind of failure, not all mixed together.

The file also protects evaluation fairness. For each task class, it splits examples into two sets: `mine`, used by the proposer to learn from, and `held_out`, used later to test whether a proposed improvement really works. This is like studying from practice problems but taking a test on different ones. Very small classes are discarded, because they cannot provide both learning and testing examples.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

**Purpose**: Finds the first real user request in a conversation. This gives the self-improvement system the original task it should later judge against.

**Data flow**: It receives the full message history. It scans from the beginning until it finds a message from the user whose content is plain, non-empty text. It returns that text, or returns nothing if no suitable user request exists.

**Call relations**: bad_trajectory calls this when deciding whether a saved conversation can become a training or evaluation example. If there is no clear user request, the conversation is skipped because there is no reliable goal to grade against.

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

**Purpose**: Finds the first failed tool result in a conversation and identifies which tool caused it. This is how the file detects the problem that makes a trajectory worth studying.

**Data flow**: It receives the full message history. First it builds a lookup table from tool-use IDs to tool names, because tool results refer back to earlier tool calls by ID. Then it scans again for the first tool result marked as an error. If it can match that error to a tool name, it returns the tool name and the error text; otherwise it returns nothing.

**Call relations**: bad_trajectory calls this before creating an example. The returned tool name becomes the task class, and the error text becomes part of the human-readable problem description.

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

**Purpose**: Decides whether one saved conversation is useful for self-improvement. A conversation is useful only if it has both a user request and a tool error.

**Data flow**: It receives one trajectory, which includes a conversation ID and messages. It asks first_tool_error for the earliest failed tool round and first_request for the original user request. If either is missing, it returns nothing. If both exist, it creates a TaskExample containing the request, messages, conversation ID, and problem description, then returns it together with a class name based on the failed tool.

**Call relations**: task_classes calls this for every trajectory it is given. bad_trajectory is the filter between raw conversation logs and the cleaner set of examples that the self-improvement loop can actually use.

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

**Purpose**: Builds the final grouped corpus from many saved conversations. It collects useful failures by failed tool and prepares each group for learning and evaluation.

**Data flow**: It receives a tuple of trajectories. For each one, it calls bad_trajectory; skipped trajectories disappear, while useful examples are grouped under names such as `tool:<name>`. It then calls _split for each group to divide examples into mining and held-out sets, removes groups that are too small, sorts the surviving classes by size and name, and returns them as an immutable tuple.

**Call relations**: This is the main public builder in the file. It coordinates the lower-level steps: bad_trajectory extracts usable examples, and _split turns each group into a TaskClass with separate study and test examples.

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

**Purpose**: Divides one tool-failure group into examples to learn from and examples to test on. It also rejects groups that are too small to split fairly.

**Data flow**: It receives a class name and all examples for that class. If there are not enough examples to provide both a mine set and a held-out set, it returns nothing. Otherwise it sorts examples by conversation ID for stable, repeatable results, chooses how many should be held out for evaluation, and returns a TaskClass containing the two sets.

**Call relations**: task_classes calls this after grouping examples by failed tool. _split is the fairness checkpoint: it makes sure a proposed improvement is not tested on the exact same conversations it learned from.

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `active whenever the extension asks the language model for a completion or tool-using turn`

The self-improvement extension needs to ask a language model for different kinds of help: proposing ideas, replaying steps, and grading results. This file acts like a narrow doorway to that model. Instead of every part of the extension building its own model request, they all use the same small interface here.

There are two protocol classes, which describe what a usable model connection must be able to do. A protocol is like a checklist: anything with the right method shape can be used, even if it is a different concrete class. `ModelLeg` is for simple text completion: give it a system instruction and previous messages, and it returns text. `ReplayLeg` is for a tool-using turn: give it instructions, messages, and tool descriptions, and it returns a full model message.

`ModelAccessLeg` is the real adapter to the SDK's `ModelAccess`, which is the project’s metered model access point. “Metered” means usage can be tracked and charged or limited. It builds a `ModelRequest` with the chosen model name, the conversation, a fixed maximum output size, and reasoning turned off. This keeps calls predictable. Without this file, model use would likely be scattered, easier to misconfigure, and harder to measure consistently.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This defines the shape of a simple model-completion method. Anything that claims to be a `ModelLeg` must accept a system instruction and conversation messages, then produce a text answer.

**Data flow**: It receives a system prompt and a tuple of messages as inputs. The protocol itself does not implement the work; it only states that an implementation will turn those inputs into a string result.

**Call relations**: Other parts of the extension can depend on this small promise instead of depending on a specific model class. `ModelAccessLeg.complete` is one concrete method that follows this shape.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This defines the shape of a model method that can take a full turn with tools available. It is used when the model may need to respond in a structured way that includes or relates to tool use.

**Data flow**: It receives a system prompt, previous messages, and tool descriptions. The protocol does not run anything itself; it says that an implementation will return a `Message`, meaning the model's next message in the conversation.

**Call relations**: Replay or tool-aware flows can ask for any object matching this protocol, rather than tying themselves to the SDK directly. `ModelAccessLeg.turn` is the concrete adapter method that satisfies this contract.


##### `ModelAccessLeg.complete`  (lines 28–37)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This sends a plain text-completion request to the SDK model connection using the extension's standard settings. It is the common path for asking the model to produce text without tools.

**Data flow**: It takes a system instruction and a tuple of conversation messages. It wraps them in a `ModelRequest`, adds the selected model name from `self.model`, caps the answer at `MAX_OUTPUT_TOKENS`, and turns reasoning mode off. It then sends that request through `self.model.complete` and returns the resulting text.

**Call relations**: When extension code needs a simple model answer, it can call this method through the `ModelLeg` interface. This method creates the `ModelRequest` and hands it to the SDK's model access layer, which performs the actual model call.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 39–51)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This sends a tool-aware model turn through the SDK model connection using the extension's standard settings. It is used when the model should see a list of available tools and return a full conversation message.

**Data flow**: It takes a system instruction, conversation messages, and tool schemas, which describe the tools the model may use. It packages those into a `ModelRequest`, adds the configured model name, the shared output-token limit, and reasoning set to off. It sends the request through `self.model.turn` and returns the model's `Message` result.

**Call relations**: When replay or similar flows need the model to act with tool information available, they can call this method through the `ReplayLeg` interface. This method prepares the request and hands it off to the SDK model layer for the actual interaction.

*Call graph*: 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal generation`

This file is part of a self-improvement loop. Its job is to take an agent’s current system prompt, look at a group of past problem examples, and ask another model to rewrite the prompt so the agent can do better next time. In plain terms, it is like showing a coach the current instruction sheet plus a few moments where the worker got stuck, then asking the coach to edit the instruction sheet without changing the worker’s whole job.

The main class, PromptProposer, is given a ModelLeg, which is the model connection used to request the rewrite. When asked to propose a prompt, it first checks whether the task class has mined examples. If there are no examples, it has no useful evidence, so it does nothing. If examples exist, it builds a clear request containing the task name, the current prompt, and a limited number of short request/problem examples. It then sends that to the model with strict instructions: return only the full revised prompt.

After the model replies, the file cleans up common formatting mistakes, such as wrapping the answer in code fences. It rejects empty answers and answers identical to the current prompt. This matters because later parts of the system should only review meaningful proposed changes, not no-ops.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This is the main action: it tries to create a new prompt candidate for a task class the agent struggled with. It only returns a candidate if there are mined examples and the model produces a changed, non-empty prompt.

**Data flow**: It receives the current system prompt and a TaskClass containing the task name and mined friction examples. If the task class has no examples, it returns nothing. Otherwise, it builds a user-facing prompt, sends it with the proposer system instruction to the model, cleans the model’s text, compares it with the current prompt, and returns a PromptCandidate containing the task name and revised prompt if the change is meaningful.

**Call relations**: This function drives the file’s whole flow. It calls PromptProposer._prompt to turn the current prompt and task examples into a model request, wraps that request in a Message for the model, then calls _clean on the model’s reply before deciding whether to create a PromptCandidate.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This builds the text that will be shown to the model when asking for a prompt rewrite. It organizes the task name, the current system prompt, and a few shortened examples into one clear instruction block.

**Data flow**: It receives the current prompt and a TaskClass. It takes up to the allowed maximum number of mined examples, trims each request and problem to the allowed character limit, formats them as numbered examples, and returns one combined text prompt asking for the full revised system prompt.

**Call relations**: PromptProposer.propose calls this just before contacting the model. Its output becomes the user message that gives the model the concrete evidence it needs to suggest a careful prompt improvement.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This removes simple unwanted wrapping from the model’s answer, especially Markdown code fences. It helps turn the model response into the plain prompt text the rest of the system expects.

**Data flow**: It receives raw text from the model. It trims surrounding whitespace, removes an opening and closing triple-backtick block if present, trims again, and returns the cleaned prompt body.

**Call relations**: PromptProposer.propose calls this after the model responds. The cleaned result is then checked for emptiness and compared with the old prompt so the proposer can reject blank answers or unchanged rewrites.

*Call graph*: called by 1 (propose).


### Replay evaluation gates
Candidate prompts are tested by replaying old tasks, judging outcomes, and deciding whether the evidence supports replacement.

### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `candidate evaluation`

This file is the evidence-gathering step for self-improvement. A new prompt should not be accepted just because it sounds good; it must prove that it helps on real tasks and does not hurt elsewhere. The file does that by running a fair side-by-side comparison: each saved task is replayed once with the current prompt and once with the candidate prompt. The replay uses archived task history, so the comparison is like testing two recipes with the same ingredients and oven. The main difference is the prompt text.

After each replay, the file asks a separate judge model to decide whether the final answer satisfies the original user request. The judge must answer in a tiny JSON format, such as {"accepted": true}. Those yes/no results become OutcomeLabel records, marked as either “present” for the candidate prompt or “absent” for the current prompt.

Finally, the labels are passed to the two-stage gate. The local held-out tasks check whether the candidate improves the task class it was meant to improve. The global held-out tasks check that it does not make other task classes worse. Without this file, the system would lack a controlled, repeatable way to decide whether a prompt change is genuinely useful.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: Runs the full comparison for a candidate prompt. It gathers judge labels for local tasks and global tasks, then asks the gate whether the candidate should be accepted.

**Data flow**: It receives the candidate prompt, the current prompt, and two sets of saved task examples: local examples for the target task type and optional global examples for broader safety. It turns each set into acceptance labels by calling the labeling helper. It then feeds both label groups into the two-stage gate and returns the gate's verdict.

**Call relations**: This is the public entry point of the evaluator. When some higher-level self-improvement flow wants to test a prompt candidate, it calls this method. The method delegates the repeated replay-and-judge work to CandidateEvaluation._labels, then hands the summarized evidence to ufo_ext_self_improvement.gate.two_stage_gate to make the final pass/fail decision.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: Creates the raw evidence for a set of held-out tasks. For every task, it tests both prompt versions and records whether the judge accepted the resulting answer.

**Data flow**: It receives a candidate prompt, the current prompt, and a tuple of saved task examples. It creates a replay runner using the configured replay model and round limit. For each example, it replays the task with the current prompt, then with the candidate prompt. After each replay, it sends the original request and final answer to CandidateEvaluation._accepts. Each yes/no result is wrapped as an OutcomeLabel showing whether it came from the candidate prompt, and the function returns all labels as a tuple.

**Call relations**: CandidateEvaluation.evaluate calls this once for local held-out tasks and once for global held-out tasks. Inside the loop, this method relies on ReplayEvaluation to regenerate the answer under a chosen prompt, then calls CandidateEvaluation._accepts to turn that answer into a simple accepted-or-not result.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: Asks the judge model whether one answer satisfies one user request. It converts the judge's text response into a plain true-or-false decision.

**Data flow**: It receives the original request and the answer produced by replay. It builds a user message containing both, sends it to the judge model with grading instructions, then looks for a JSON object inside the judge's reply. If the JSON can be read and contains {"accepted": true}, it returns true. If the judge response is missing valid JSON, cannot be parsed, or does not explicitly accept the answer, it returns false.

**Call relations**: CandidateEvaluation._labels calls this after every replayed answer. This method is the bridge between free-form model output and the clean success labels needed by the gate: it uses Message to format the judge input and json.loads to read the judge's required JSON verdict.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation`

This file is a safety gate for self-improvement. When the system tries a revised prompt, it must not promote the new version just because of a lucky small sample. The gate asks two questions: did the candidate prompt improve acceptance on the task it was meant to improve, and did it avoid a clear regression on other tasks?

The inputs are replay outcomes. Each outcome says whether the candidate prompt was present and whether the judge accepted the answer. The file groups those outcomes into a simple table: candidate-present successes and total, candidate-absent successes and total. From that, it estimates the “lift,” meaning the difference between the candidate’s acceptance rate and the old prompt’s acceptance rate.

The important detail is that the file does not trust the raw difference alone. It uses Wilson confidence bounds, which are cautious statistical estimates for proportions, then combines them with Newcombe’s method to estimate a cautious lower bound or optimistic upper bound for the difference. In everyday terms, it asks: “Even after allowing for uncertainty, does this still look like a real improvement?”

A candidate passes the local gate only if both sides have enough replay examples and the cautious lift clears a minimum floor. Then a second global check rejects the candidate only if there is confident evidence that it harms other task classes. This keeps the system from over-promoting noisy wins while still avoiding unnecessary rejection when evidence is limited.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This computes a cautious lower estimate for a success rate, such as “at least how good might this prompt really be?” It is used when the system wants to avoid being fooled by a small number of lucky successes.

**Data flow**: It receives a count of accepted examples, a total number of examples, and an optional confidence setting. If there are no examples, it returns 0. Otherwise it turns the raw success rate into a lower confidence bound using the Wilson formula and returns a number between 0 and 1.

**Call relations**: This is a building block for the lift calculations. The lower-lift calculation uses it to be cautious about the candidate’s success rate, while the upper-lift calculation uses it to be cautious about the old prompt’s comparison rate.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This computes a cautious upper estimate for a success rate, such as “how good could this prompt plausibly be?” It is used when the system needs the optimistic side of the uncertainty range.

**Data flow**: It receives a count of accepted examples, a total number of examples, and an optional confidence setting. If there are no examples, it returns 1. Otherwise it applies the Wilson formula and returns an upper confidence bound between 0 and 1.

**Call relations**: This supports both lift calculations. The lower-lift calculation uses it to give the old prompt the benefit of the doubt, while the upper-lift calculation uses it to give the candidate the benefit of the doubt.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: This estimates the cautious lower bound of the candidate prompt’s improvement over the old prompt. It answers: “After accounting for uncertainty, how much improvement can we still safely claim?”

**Data flow**: It receives a contingency table containing success counts and totals for candidate-present and candidate-absent runs. If either side has no examples, it returns 0. Otherwise it computes both raw success rates, gets Wilson bounds for each side, combines their uncertainty, and returns a conservative estimate of the candidate’s lift.

**Call relations**: This is called by score_gate during the local promotion decision. It relies on wilson_lower_bound, wilson_upper_bound, and a square-root calculation to build the cautious difference estimate that score_gate compares against the required improvement floor.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: This estimates the optimistic upper bound of the candidate prompt’s lift over the old prompt. It is mainly used to decide whether there is clear evidence of harm elsewhere.

**Data flow**: It receives a contingency table with candidate-present and candidate-absent results. If either side has no examples, it returns 0. Otherwise it computes raw rates, uses Wilson bounds to estimate the most favorable plausible difference, and returns that upper estimate.

**Call relations**: This is called by global_non_inferior. That global check uses the optimistic upper bound because it rejects only when even the best plausible reading still shows a meaningful regression.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: This turns a list of replay outcomes into the four counts needed for the statistical comparison. It is like sorting scorecards into two piles, candidate-present and candidate-absent, then counting wins in each pile.

**Data flow**: It receives outcome labels, each saying whether the candidate prompt was present and whether the answer succeeded. It separates present from absent outcomes, counts accepted examples and totals for both groups, and returns a Contingency record with those four numbers.

**Call relations**: Both score_gate and global_non_inferior call this before doing their statistical checks. It prepares the raw replay labels into the compact form that lift_lower_bound and lift_upper_bound can work with.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: This makes the local promotion decision for the task class the candidate was meant to improve. It requires enough examples on both sides and a cautious improvement estimate above the configured floor.

**Data flow**: It receives local replay labels plus optional thresholds for the minimum lift and minimum examples per side. It converts labels into counts, computes the lower confidence bound for lift, checks whether both arms have enough examples, then checks whether the cautious lift is high enough. It returns a GateVerdict saying pass or fail, with a human-readable reason and the key numbers.

**Call relations**: two_stage_gate calls this first. If score_gate fails, the full two-stage process stops immediately, because there is no point checking global safety for a candidate that did not prove a local win.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: This checks whether the candidate avoids a clear regression on other task classes. It is deliberately permissive when evidence is thin: it blocks only when the data confidently shows meaningful harm.

**Data flow**: It receives global replay labels, a regression margin, and a minimum example count. It builds the contingency counts. If either side has too few examples, it returns true, meaning the candidate is not blocked by this check. Otherwise it computes the optimistic upper bound of lift and returns true only if that bound is not below the allowed negative margin.

**Call relations**: two_stage_gate calls this after the local gate has passed. It uses lift_upper_bound because the global stage is looking for confident evidence of damage, not demanding strong proof of equal performance.

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: This gives the final promotion verdict for a candidate prompt. It combines the local improvement test with the broader “do no clear harm” test.

**Data flow**: It receives two sets of replay labels: one for the target task class and one for other task classes. It first asks score_gate whether the candidate has a strong enough local lift. If not, it returns that failure. If the local check passes, it asks global_non_inferior whether the candidate avoids clear global regression. If the global check fails, it returns a failing GateVerdict with a regression reason. Otherwise it returns the successful local verdict.

**Call relations**: This is the top-level decision function in the file. It coordinates score_gate and global_non_inferior so a candidate is promoted only when it both wins locally and does not clearly damage the rest of the agent’s work.

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `offline prompt evaluation / replay`

This file solves a very practical evaluation problem: how can you compare two prompts on the same past task without accidentally sending emails, changing files, or calling live services again? It does this by replaying only the model parts of an archived conversation. When the model asks to use a tool, the file looks up the exact result that tool produced in the original run and feeds that result back instead of executing the tool.

Think of it like a flight simulator built from a real flight recording. The pilot can make new decisions, but the weather and instrument readings are replayed from the original trip. That makes the comparison safer and more focused.

The replay starts by removing the original final answer, so the model must generate a new one under the swapped prompt. It builds a small tool catalog from the tools that appeared in the archived conversation. It also indexes old tool results by tool name and input, so repeated tool calls can be answered from the archive. If the model asks for a tool call that was not in the original path, the replay is marked as “diverged.” That is important: the result is still returned, but it is a weaker signal because the replay no longer has archived facts to continue safely.

#### Function details

##### `_canonical_input`  (lines 35–36)

```
def _canonical_input(value: object) -> str
```

**Purpose**: This small helper turns a tool input into a stable text form. It is used so the same input object can be matched reliably, even if its keys were written in a different order.

**Data flow**: It receives any input value, usually the arguments sent to a tool. It converts that value to compact JSON text with sorted keys. It returns that text so other code can use it as part of a lookup key.

**Call relations**: The archive-indexing step uses this when saving old tool results, and the replay-feeding step uses it again when checking whether a new tool call matches an old one. Because both sides use the same conversion, the lookup is consistent.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 39–52)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares the archived conversation for replay by removing the original final assistant answer. The new prompt then has to produce its own answer from the same earlier context.

**Data flow**: It receives the full archived message history. It walks backward through the end of the conversation and removes trailing assistant messages that are plain final answers, while keeping assistant messages that contain tool calls. It returns the shortened message history that should be shown to the model during replay.

**Call relations**: ReplayEvaluation.replay calls this near the start of a replay. The shortened conversation becomes the starting point for each model turn, so the replay is based on the original task and tool history but not on the original final wording.

*Call graph*: called by 1 (replay).


##### `archived_tool_results`  (lines 55–77)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: This builds a lookup table of old tool answers, so replay can reuse them instead of running tools again. It is the safety mechanism that makes replay side-effect free.

**Data flow**: It receives the archived messages. First it records tool result blocks by their tool-use id. Then it finds each old tool call, matches it to its result, and stores that result under the pair of tool name and normalized input. It returns a dictionary that can answer “what result did this exact tool call get last time?”

**Call relations**: ReplayEvaluation.replay calls this before asking the model to regenerate anything. Later, _feed_archived relies on the lookup it produced to answer replayed tool calls from the archive.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 80–97)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: This creates the limited list of tools that the replayed model is allowed to see. The list is based only on tools that appeared in the archived conversation.

**Data flow**: It receives the archived messages and scans them for tool-use blocks. It collects each distinct tool name in first-seen order. It returns simple tool schemas that allow flexible object-shaped inputs, because the archived conversation itself shows the model the expected call shape.

**Call relations**: ReplayEvaluation.replay calls this during setup. The returned tool catalog is passed into the model turn so the model can reproduce archived calls, without needing access to the live agent’s real tool registry.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 100–116)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: This answers the model’s requested tool calls using saved results from the archived run. If any requested call cannot be matched, it signals that the replay has left the archived path.

**Data flow**: It receives the tool calls requested in the current model turn and the archived-result lookup. For each call, it normalizes the input and searches for the matching saved result. If all calls match, it builds a user message containing tool-result blocks with the old content but the new call ids. If any call has no saved result, it returns None.

**Call relations**: ReplayEvaluation.replay calls this whenever the model asks for tools. A real result message lets the replay continue safely; a None result tells ReplayEvaluation.replay to stop and mark the run as diverged.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 129–150)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: This is the main replay flow for testing one archived task against one candidate system prompt. It repeatedly asks the model what it would do, feeds back archived tool results when possible, and stops when there is a final answer or the replay can no longer stay on the archived path.

**Data flow**: It receives an archived conversation and a system prompt to test. It builds the archived tool-result lookup, creates the replay-only tool list, and trims off the old final answer. On each round, it asks the model for the next assistant message. If the model gives a final text answer with no tool calls, it returns that answer as a non-diverged ReplayResult. If the model asks for tools, it tries to feed back archived results and continues. If a tool call cannot be matched, or the round limit is reached, it returns the best text seen so far and marks the result as diverged.

**Call relations**: This method ties together the whole file. It calls replay_head to prepare the conversation, archived_tool_results to make old tool answers searchable, replay_tools to expose only archived tools, and _feed_archived to continue each tool round without real side effects. Its final ReplayResult is what the prompt-evaluation code can grade.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).


### Prompt approval workflow
Approved self-improvement candidates are advanced through scheduled checks and protected by human-governed prompt proposal controls.

### `core/src/ufo/governance.py`

`domain_logic` · `request handling`

This file is a safety gate for changing an agent’s configuration, specifically its prompt. Instead of writing a new prompt directly onto an agent, it records a proposed change with a fingerprint of the prompt that the proposer thought was current. That fingerprint is a digest: a short, fixed string made from the prompt text, like a tamper-evident seal on a document.

The main idea is a “compare-and-swap” check. When a proposal is created, the system saves both the old prompt digest and the new prompt. Later, when someone approves the proposal, the system locks the agent row in the database and recalculates the digest of the agent’s current prompt. If the current digest still matches the proposal’s original digest, the change is safe to apply. If it does not match, something changed in the meantime, so the proposal is rejected instead of overwriting newer work.

The Governance class is scoped to one workspace, so proposals cannot accidentally affect agents from another workspace. It also records which extension or system component opened the proposal. Without this file, prompt updates could race with each other, and an approval might accidentally replace a prompt that had already been edited by someone else.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: This function turns a prompt into a stable fingerprint using SHA-256, a common hashing method that produces a fixed-length summary of text. The system uses that fingerprint to tell whether a prompt is still exactly the same later.

**Data flow**: It takes prompt text in, encodes it as bytes, runs it through SHA-256, and returns the result as a readable hexadecimal string. It does not change anything outside itself.

**Call relations**: When a proposal is opened, Governance.propose_change uses this to record the fingerprint of the proposed new prompt. When a proposal is approved, Governance.approve_proposal uses it again to compare the saved original fingerprint with the agent’s current prompt.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: This method opens a new proposal to change an agent’s prompt, but does not apply the change yet. It first checks that the target agent really exists inside this governance object’s workspace.

**Data flow**: It receives an AgentChange containing the target agent, the expected old prompt digest, and the new prompt. Inside a workspace database transaction, it checks for the agent, creates a new proposal ID, stores the proposal with pending status, saves the new prompt in the proposal body, and records digests for before and after. It returns a ProposalRef containing the new proposal ID.

**Call relations**: This is the start of the governance flow. A caller uses it when some extension or core code wants to request a prompt change. It calls prompt_digest to fingerprint the new prompt, uses workspace_tx to keep the database work grouped safely, and returns a ProposalRef so later code can pass that ID to Governance.approve_proposal.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: This method tries to approve and apply a pending proposal. It only updates the agent if the agent’s current prompt still matches the prompt version that the proposal was based on.

**Data flow**: It receives a proposal ID. Inside a workspace database transaction, it loads the proposal for this workspace, rejects missing or non-pending proposals with an error, locks and reads the target agent’s current prompt, and compares its digest with the proposal’s saved starting digest. If they differ, it marks the proposal rejected and logs that result. If they match, it writes the proposed prompt to the agent, marks the proposal approved, and logs the approval after the transaction completes.

**Call relations**: This is the second half of the governance flow, after Governance.propose_change has created a pending proposal. It calls prompt_digest to perform the safety check, uses database updates to either approve or reject the proposal, and sends events to ufo.o11y.log so the outcome is visible to observability or audit tools.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).


### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled background tick`

This file is the safety gate for automatic prompt improvement. It does not directly rewrite an agent. Instead, it works like a cautious quality inspector: it suggests a possible better prompt, tests it on saved examples, waits for repeated passing results, and then asks the normal approval system to review the change.

On each scheduled tick, `ImproveCron` reads past trajectories, which are records of agent conversations and results. It groups them by agent, because each agent has its own prompt and its own improvement candidate. For each agent, it checks whether there is already a stored candidate prompt for the current prompt version. The prompt version is tracked by a digest, which is like a fingerprint of the prompt text. If a candidate was already rejected or promoted for that same fingerprint, the file will not keep proposing it again.

If there is no active candidate, the file asks `PromptProposer` to suggest one from the current prompt and a task class found in the trajectories. It stores that candidate in the extension's scoped store, along with the held-out examples used for later testing.

Then it evaluates the candidate. A failure marks it rejected. A pass increments a stability counter. Only after enough consecutive passing ticks does it call `ctx.propose_change`, which opens a governed proposal for approval. This matters because it prevents a one-off lucky test result from changing an agent.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: Runs one full self-improvement tick. It gathers all available trajectories, groups them by agent, and advances the improvement process separately for each agent.

**Data flow**: It reads trajectories from the extension context. Those trajectories are grouped by agent ID, then each agent's group is passed forward for candidate opening, testing, or promotion. It returns nothing, but it may cause stored candidate state or governed proposals to be created later in the flow.

**Call relations**: This is the top-level method for the cron job. It uses `_by_agent` to split the workspace history into per-agent batches, then calls `ImproveCron._advance` once for each batch so each agent is considered independently.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Moves one agent one step through the improvement process. It finds or opens a candidate prompt for the agent's current prompt version, then sends that candidate through the evaluation gate.

**Data flow**: It receives an agent ID and that agent's trajectories. It takes the prompt digest from the first trajectory as the current prompt fingerprint, builds the store key for this agent, asks for an active candidate or opens a new one, and if a candidate exists, passes it to the gate. It returns nothing, but may update stored candidate state or trigger a proposal through later calls.

**Call relations**: `ImproveCron.run` calls this after grouping trajectories. This method sits between the broad cron loop and the detailed steps: it delegates candidate lookup or creation to `ImproveCron._active_or_open`, then delegates testing and possible proposal creation to `ImproveCron._gate`.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: Finds the current in-progress candidate for an agent, or creates a new one if it is allowed and possible. It also prevents the same rejected or already-promoted candidate from being reopened for the same prompt version.

**Data flow**: It receives a store key, the current prompt digest, and the agent's trajectories. It first reads the scoped store. If it finds a candidate for the same digest and it is still evaluating, it returns that candidate. If the stored candidate is already terminal, it returns nothing. If there is no usable stored candidate, it looks for task classes in the trajectories, asks the proposer for a new prompt, stores the new `CandidateState`, and returns it. If no task class or proposal exists, it returns nothing.

**Call relations**: `ImproveCron._advance` calls this before any evaluation happens. It uses `task_classes` to find suitable groups of examples and asks the `PromptProposer` to create a candidate prompt. The candidate it returns is then handed back to `_advance`, which sends it to `ImproveCron._gate`.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Tests a candidate prompt and decides whether it should keep waiting, be rejected, or be submitted for human approval. This is the main safety checkpoint before any proposed prompt change can enter governance.

**Data flow**: It receives the agent ID, store key, current prompt digest, candidate state, and trajectories. It builds held-out test examples for the candidate's own task and also collects held-out examples from other tasks as a wider safety check. It asks the evaluator to compare the candidate prompt against the current prompt. If the verdict fails, it stores the candidate as rejected. If it passes but has not passed enough consecutive ticks, it stores a higher pass count. If it reaches the stability requirement, it opens an `AgentChange` proposal and stores the candidate as promoted with the proposal ID.

**Call relations**: `ImproveCron._advance` calls this after a candidate is found or opened. This method uses `_held_out` to rebuild the candidate's test examples, uses `task_classes` for the broader held-out set, calls the evaluation service, and then calls `ImproveCron._save` to persist the result. On final success, it hands the proposed prompt to `ctx.propose_change` rather than changing the agent directly.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: Writes an updated version of a candidate's state to the scoped store. It is used to remember whether the candidate is still being tested, rejected, or promoted.

**Data flow**: It receives the store key, the existing candidate, and the new status details such as pass count and optional proposal ID. It creates a copied candidate with those fields changed, converts it to JSON-friendly data, and writes it into the context store. It returns nothing, but the stored record is changed.

**Call relations**: `ImproveCron._gate` calls this whenever an evaluation result must be remembered. It is the small persistence step that makes the cron job stateful across ticks, so repeated passes can accumulate and rejected or promoted candidates can be suppressed.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: Groups trajectories by the agent that produced them. This lets the cron job treat each agent's prompt improvement process separately.

**Data flow**: It receives a tuple of trajectories. It reads each trajectory's agent ID, collects trajectories with the same ID into the same group, and returns a mapping from agent ID to a tuple of that agent's trajectories. It does not change the trajectories.

**Call relations**: `ImproveCron.run` calls this at the start of a tick. Its grouped output determines how many times `ImproveCron._advance` runs and which trajectories each agent's improvement check receives.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: Rebuilds the candidate's held-out test examples from the current trajectories. It keeps only matching conversations that are identified as bad examples by the corpus helper.

**Data flow**: It receives all trajectories for an agent and a tuple of held-out conversation IDs stored on the candidate. It builds a lookup table by conversation ID, checks each requested ID, skips missing conversations, and asks `bad_trajectory` whether the trajectory should become a task example. It returns the collected `TaskExample` objects as a tuple.

**Call relations**: `ImproveCron._gate` calls this when preparing the candidate-specific test set for evaluation. `_held_out` relies on `bad_trajectory` to turn raw trajectory records into the examples that the evaluator expects.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).

## 📊 State Registers Touched

- `reg-extension-catalog` — The loaded list of extensions and packs that tells the system which extra tools, routes, jobs, skills, and backends exist.
- `reg-workspace-tenant-state` — The saved customer workspace boundary, including its owners, admins, limits, main agent, and tenant separation rules.
- `reg-agent-profile` — The saved assistant setup for each workspace, including model choice, audience, internet access, skills, and control settings.
- `reg-transcript-state` — The shared conversation notebook containing saved messages, model events, summaries, and compaction records.
- `reg-turn-run-state` — The durable job ticket for each agent turn, including admission source, queue status, claim owner, parent turn, and final result.
- `reg-cancellation-state` — The shared stop signal and cancellation record used to safely halt turns, child turns, jobs, and cleanup work.
- `reg-runtime-fleet-state` — The live fleet heartbeat table that says which runtime processes are alive and what stranded work they may own.
- `reg-model-provider-catalog` — The shared list of AI models and providers, including how to call them, what keys they need, and what features they support.
- `reg-usage-accounting-ledger` — The spending ledger that records model usage, egress usage, prices, caps, billing exports, and payment-related state.
- `reg-connector-account-state` — The connected-app state for OAuth, hosted connector accounts, GitHub installations, Slack setup, and provider action access.
- `reg-source-page-sync-state` — The source records, page bodies, cursors, revisions, deletion markers, and replay feed used to keep external content synchronized.
- `reg-memory-search-index` — The long-term memory and searchable text index that stores remembered facts, chunks, embeddings, and recall results.
- `reg-skill-inventory` — The declared and user-created skill inventory, including skill ownership, dependencies, files, and the load order copied into a sandbox.
- `reg-subagent-workflow-state` — The shared parent-child workflow state used when an agent delegates work to helper agents and waits for or cancels them.
- `reg-scheduled-task-state` — The durable timers and recurring jobs that remember what should run later, whether it is paused, expired, claimed, or rescheduled.
- `reg-prompt-governance-state` — The prompt templates, rendered fingerprints, proposals, evaluations, approvals, and replacement decisions used to change agent behavior safely.
- `reg-observability-trace-state` — The shared logging, metrics, trace IDs, trace parents, and redaction context used to follow work across processes without leaking secrets.
- `reg-background-job-state` — The durable and in-memory background job registry, candidate queue, claims, retries, and worker progress for non-turn jobs such as sync, billing, evaluation, and cleanup.
- `reg-extension-kv-store` — The generic per-workspace extension JSON store used by add-ons to persist small feature-specific state outside core tables.
- `reg-evaluation-fixture-state` — The persistent fake-world data for evaluations, including test email, calendar, code-search, and other deterministic benchmark records.
- `reg-member-seat-state` — The durable workspace membership and seat-assignment state used to decide who belongs, who is an admin, and whether a member may admit or run work.
- `reg-service-lifecycle-state` — The process-wide lifecycle state containing startup task handles, shutdown signals, and service cleanup hooks drained during teardown.
- `reg-redis-coordination-state` — The live Redis coordination backend state, including clients, stream/consumer metadata, and cross-process fan-out or coordination wiring.
- `reg-self-improvement-feedback-state` — Collected failed-task examples, replay inputs/results, judgments, and candidate feedback buffers used by self-improvement background loops before governance approval.
