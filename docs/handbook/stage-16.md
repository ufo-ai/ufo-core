# Scheduled jobs, notifications, monitors, and maintenance loops  `stage-16`

This stage is the system’s night shift: work that happens outside a live user request. The core job runner gathers scheduled job definitions, finds workspaces with pending work, and runs each job safely inside one workspace so runs do not duplicate or leak across teams. A shared scheduled-fire key format keeps delayed task IDs consistent.

Several loops then do useful upkeep. Notification draining batches pending notices and wakes the right agent conversation. Scheduled task and pause runners use cron-style schedules, meaning repeating time rules, to fire due tasks or resume waiting workflows once. Monitor jobs poll user-created monitors and alert agents when something changes.

Other jobs improve stored information. The memory condenser cleans and merges remembered facts. Report digest code summarizes new scheduled reports and skips unchanged ones. The preview renderer fills in missing shared-file cover images. Source-style maintenance includes retry-safe background patterns, product metrics that show workspace progress, and a small homepage cleanup for chat-first workspaces.

The self-improvement loop is more cautious. It proposes prompt changes, replays old conversations without rerunning tools, grades results with a model, and only promotes changes that pass the gate repeatedly. Package files simply make extensions importable.

## Files in this stage

### Runtime job orchestration
Core runtime files discover eligible workspaces, identify scheduled firings, expose product progress metrics, and turn job declarations into safe per-workspace background execution.

### `core/src/ufo/product.py`

`domain_logic` · `scheduled census ticks and onboarding event moments`

This file is the product analytics census taker. Instead of relying on one-off tracking events, it looks at the database and re-derives the truth each time it runs. That matters because the definition of a stage can change later; if the answer comes from stored rows, the system can recalculate history using the new definition.

The main idea is simple: once per scheduled tick, for the currently bound workspace, ask questions like “Has anyone chatted?”, “Has a connector been granted?”, “Has a teammate been invited?”, and “Has money been charged?” Each yes or no becomes a metric. A paid workspace also still counts as having reached earlier stages, so the dashboard reads like a funnel ladder.

The file also counts what the workspace has attached: surfaces, proven addresses, credential slots, connector providers, and app names. These are tagged by broad kind and name, not by workspace or member, which keeps the number of metric series from exploding as the fleet grows.

A second census focuses on onboarding steps. It finds the first time each setup milestone happened, then counts that step only during the tick when it first appears. For steps that leave no database row, such as a skipped first-run screen or a failed initialization step, callers use record_onboarding_step to report them immediately.

#### Function details

##### `_member_turn`  (lines 76–84)

```
def _member_turn(workspace_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Defines what counts as a real member-started chat turn for this product reporting. Keeping this in one helper means the funnel stage and onboarding step use the same definition of “a member chatted.”

**Data flow**: It receives a workspace ID. It builds a database condition that says: the turn belongs to this workspace, came from a member admission, and is not a child turn inside another turn. It returns that condition for larger database queries to reuse.

**Call relations**: product_census uses this when deciding whether the workspace has ever chatted and whether it was active recently. onboarding_census uses the same condition when finding the first member chat and the first invited-member chat, so both reports agree about what a member turn means.

*Call graph*: called by 2 (onboarding_census, product_census); 1 external calls (and_).


##### `product_census`  (lines 87–185)

```
async def product_census() -> None
```

**Purpose**: Counts the current workspace’s product funnel stages and attached tools or surfaces, then emits those counts as metrics. This is what feeds the dashboard view of how many workspaces reached each major product milestone.

**Data flow**: It reads the current workspace from runtime context and gets the current time. It builds database queries that answer yes-or-no questions for stages such as seated, connector attached, invited, app built, chatted, recently active, and paid. It also queries distinct attachments, such as installed surfaces, verified addresses, credentials, connector providers, and app names. After reading those results inside a workspace database transaction, it emits one metric per stage and one metric per attachment.

**Call relations**: This is a scheduled census function for one bound workspace. It calls _member_turn so chat-related stages share one definition, uses database query helpers to read stored facts, and hands the final numbers to the observability metric emitter.

*Call graph*: calls 1 internal fn (_member_turn); 9 external calls (now, timedelta, exists, literal, select, union_all, workspace_tx, emit_metric, ws_current).


##### `_utc`  (lines 188–189)

```
def _utc(moment: datetime) -> datetime
```

**Purpose**: Makes sure a datetime has a timezone, treating timezone-less values as UTC. This avoids bad latency math when some timestamps know their timezone and others do not.

**Data flow**: It receives a datetime. If the datetime already has timezone information, it returns it unchanged. If it does not, it returns a copy marked as UTC.

**Call relations**: _elapsed_ms calls this before subtracting two times. onboarding_census also uses it directly when deciding whether a step happened within the most recent census window.

*Call graph*: called by 2 (_elapsed_ms, onboarding_census); 1 external calls (replace).


##### `_elapsed_ms`  (lines 192–193)

```
def _elapsed_ms(created_at: datetime, at: datetime) -> int
```

**Purpose**: Calculates how many milliseconds passed between workspace creation and a later onboarding moment. It clamps negative answers to zero, so clock oddities do not produce impossible negative durations.

**Data flow**: It receives two timestamps: the earlier creation time and the later event time. It normalizes both through _utc, subtracts them, converts the difference to milliseconds, and returns a non-negative integer.

**Call relations**: record_onboarding_step uses this to report how long an immediate, manually recorded step took. onboarding_census uses it for derived steps found in the database before passing the result to _emit_step.

*Call graph*: calls 1 internal fn (_utc); called by 2 (onboarding_census, record_onboarding_step).


##### `_emit_step`  (lines 196–203)

```
def _emit_step(step: str, status: str, surface: str, provider: str, latency_ms: int | None) -> None
```

**Purpose**: Emits the metric for one onboarding step, and optionally emits a timing histogram for how long that step took. A histogram is a metric that records a spread of values, useful for seeing typical and slow setup times.

**Data flow**: It receives the step name, status, surface, provider, and optional latency in milliseconds. It always emits a count metric tagged with the step details. If latency is present, it also emits a latency histogram tagged by step, status, and surface.

**Call relations**: Both onboarding paths end here. record_onboarding_step calls it for steps reported at the moment they happen, while onboarding_census calls it for steps discovered later from database rows.

*Call graph*: called by 2 (onboarding_census, record_onboarding_step); 2 external calls (emit_histogram, emit_metric).


##### `record_onboarding_step`  (lines 206–231)

```
async def record_onboarding_step(workspace_id: UUID, step: str, status: str, *, surface: str, provider: str=NO_PROVIDER) -> None
```

**Purpose**: Records an onboarding step whose outcome is not otherwise stored in the database. It is used for cases like skipped screens or failed setup actions, where a later census would have no row to discover.

**Data flow**: It receives the workspace ID, step name, status, surface, and optionally a provider. It reads the workspace creation time from the database, calculates how long after creation the step happened if that time exists, and emits the step metric with that latency.

**Call relations**: Call sites use this at the moment a non-derivable onboarding result happens. It opens a workspace database transaction to read the founding time, uses _elapsed_ms for timing, and hands the final metric data to _emit_step.

*Call graph*: calls 2 internal fn (_elapsed_ms, _emit_step); 3 external calls (now, select, workspace_tx).


##### `onboarding_census`  (lines 234–326)

```
async def onboarding_census() -> None
```

**Purpose**: Finds setup milestones that the current workspace reached during the most recent census period and emits them once. This shows not only whether a workspace got somewhere, but how long it took to get there.

**Data flow**: It reads the current workspace and current time. In one database query, it asks for the workspace creation time and the earliest time each milestone happened, such as first member chat, first connector grant, first surface install, first invitation, first owned app, and first invited-member chat. If there is no workspace creation time, it stops. Otherwise it checks each milestone: if it happened within the last census window, it emits a completed onboarding step with latency from workspace creation to that milestone.

**Call relations**: This scheduled census complements product_census. It calls _member_turn so chat milestones match the funnel, uses _utc to compare timestamps safely, uses _elapsed_ms to calculate setup speed, and sends each completed step through _emit_step.

*Call graph*: calls 4 internal fn (_elapsed_ms, _emit_step, _member_turn, _utc); 5 external calls (now, timedelta, select, workspace_tx, ws_current).


### `core/src/ufo/runtime/candidates.py`

`domain_logic` · `job scheduling / dispatch tick`

Jobs in this system are not meant to run “in the open.” They must run while bound to a specific workspace, so tenant data stays separated. This file provides the small bridge that tells the dispatcher which workspaces need attention.

The important idea is that candidate discovery is allowed to look across workspaces, but only in a very limited way. It may return workspace IDs, not actual tenant rows. Think of it like a building receptionist reading a lobby board that says which offices have mail, without opening anyone’s mail. After that, the dispatcher enters each named workspace and runs the job there.

The file defines `WorkspaceCandidates`, a callable that asynchronously returns a tuple of workspace UUIDs. Its main helper, `owner_candidates`, accepts a function that builds a database query. That query must select only workspace IDs from the caller’s own tables. The query is built fresh each time candidates are checked, which matters for time-based work: “due now” should be calculated using the current time, not a time frozen when the extension was loaded.

The actual database read uses `owner_tx`, a privileged database transaction that bypasses row-level security, meaning the usual per-workspace filtering is temporarily not applied. This file keeps that powerful access contained and predictable.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: This function turns a query-building function into a safe workspace-candidate finder. It gives extensions a way to say “these workspaces have work waiting” without giving them direct access to the privileged cross-workspace database connection.

**Data flow**: It receives `due`, a no-argument function that builds a database `Select` query returning workspace IDs. It wraps that query builder inside an async `candidates` function. The result is a callable that, when later run, will execute the fresh query and return the workspace IDs as a tuple.

**Call relations**: This is the public seam in this file: other parts of the runtime or extensions can call it when they need to declare how candidate workspaces are found. It does not run the database query immediately; instead, it hands back `owner_candidates.candidates`, which performs the actual privileged read later when the dispatcher asks for candidates.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner async function actually asks the database which workspaces currently have pending work. It uses the privileged `owner_tx` path, but only to read workspace IDs.

**Data flow**: It starts with no direct inputs, but it closes over the `due` query builder passed to `owner_candidates`. When called, it opens an `owner_tx` database connection, builds and executes the current query, reads the first value from each returned row, and returns those values as a tuple of workspace UUIDs. It does not return tenant row data, only workspace identifiers.

**Call relations**: This function is the delayed action created by `owner_candidates`. When the dispatcher or scheduling code later invokes it, it calls `ufo.db.owner_tx` to perform the one sanctioned cross-workspace read. After it returns workspace IDs, the wider runtime is expected to bind each workspace before running the actual job work.

*Call graph*: 1 external calls (owner_tx).


### `core/src/ufo/runtime/ext/scheduled_fire.py`

`util` · `scheduled task firing and run lookup`

A scheduled task can run many times, so the system needs a durable label for “this exact task, at this exact scheduled time.” This file provides that label. The label is a string made from the task’s unique ID, a colon, and the scheduled time written in the standard datetime `isoformat()` form. That string acts like a ticket stub: later, another part of the system can look at it and tell which task admitted the run.

This matters because these keys are used for deduplication, meaning they help prevent the same scheduled occurrence from being admitted twice. The exact spelling of the time is important. The comment notes that even details like keeping `+00:00` for UTC time must not change, because old runs may already have been stored with that exact key format.

The file also includes the reverse operation: given a key, try to pull out the task ID. If the key was not made by the scheduled-task system, such as a different kind of timer resume key, parsing safely returns `None` instead of pretending it found a task.

#### Function details

##### `scheduled_fire_key`  (lines 14–16)

```
def scheduled_fire_key(task_id: UUID, fire_at: datetime) -> str
```

**Purpose**: Builds the stable admission key for one scheduled occurrence of one task. Code uses it when it needs a repeatable label that says, “this task was scheduled to fire at this time.”

**Data flow**: It receives a task ID and a scheduled datetime. It turns the datetime into its standard ISO text form, joins the task ID and time with a colon, and returns the finished string. It does not change anything outside itself.

**Call relations**: When the scheduled-task runner admits a fire, this helper is the single place that creates the key. Inside the function, it relies on the datetime object’s `isoformat()` method so the time is written consistently.

*Call graph*: 1 external calls (isoformat).


##### `scheduled_fire_task_id`  (lines 19–25)

```
def scheduled_fire_task_id(key: str) -> UUID | None
```

**Purpose**: Tries to recover the task ID from a scheduled fire key. If the key is not in this scheduled-fire format, it returns `None` so callers can ignore it safely.

**Data flow**: It receives a key string. It takes the part before the first colon and tries to read it as a UUID, which is a standard unique identifier. If that succeeds, it returns the UUID; if the text is not a valid UUID, it returns `None`.

**Call relations**: When another part of the system, such as a runs feed, needs to connect a run back to its scheduled task, it can call this parser. The function hands the first part of the key to `UUID` for validation and conversion, and treats a conversion failure as proof that the key came from some other source.

*Call graph*: 1 external calls (UUID).


### `core/src/ufo/runtime/jobs.py`

`orchestration` · `startup and scheduled background execution`

This file is the background-job coordinator for the runtime. A background job is work the system does on a schedule or as a one-time boot task, such as syncing sources, dispatching queued conversation turns, reacting to changed pages, delivering subagent results, counting product usage, or rendering previews. Without this file, those jobs might never be registered, might run in the wrong workspace, or might run twice at the same time and corrupt state.

The main idea is a two-step fan-out. First, a lightweight scheduled workflow called a tick asks, “Which workspaces actually have work for this job?” Then it queues one durable job workflow per workspace. Durable means DBOS records the work so it can resume or avoid repeating completed steps after a crash. This is like a dispatcher reading a route list, then handing one delivery ticket to each driver instead of sending one driver to every house.

The file also defines special core runners. `TurnDispatcher` finds conversation turns stuck in queued or parked states and safely offers them to DBOS queues. `PageChangeRunner` gives each page-change hook its own cursor, so one slow extension hook does not block another. `JobRunner` registers schedules at startup, enqueues one-shot jobs, builds the right extension context, and runs handlers inside the selected workspace. It also treats spending refusals as expected deferrals, not system failures, and can notify a member once when background work is paused by spend limits.

#### Function details

##### `ResultDeliverer.run`  (lines 106–106)

```
async def run(self) -> None
```

**Purpose**: This protocol method describes the work needed to deliver finished child-agent results back into the main turn flow. It is only a contract here, so this file can schedule that work without importing the turn-loop implementation directly.

**Data flow**: A concrete deliverer receives no arguments here. It reads whatever finished child results it owns, posts the needed arrivals, and returns nothing after updating the system state elsewhere.

**Call relations**: The core job list wraps this method in a scheduled job. When `JobRunner.fire` runs that job for a workspace, the wrapper calls this method to let the turn subsystem do its own result-delivery sweep.


##### `ResultDeliverer.candidate_workspaces`  (lines 108–108)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This protocol method asks which workspaces have child-agent results waiting to be delivered. It lets the job system avoid opening workspaces that have nothing to do.

**Data flow**: It receives no arguments, looks at the deliverer's own records, and returns a tuple of workspace IDs that should be swept.

**Call relations**: The core result-delivery job uses this as its candidate finder. `JobRunner.tick` calls it before queuing per-workspace job workflows.


##### `TurnDispatcher.run`  (lines 177–213)

```
async def run(self) -> None
```

**Purpose**: This function finds conversation turns that are ready to be put back onto execution queues. It also rechecks important gates for parked turns, such as whether the member has a seat and whether spending and balance rules allow the turn to continue.

**Data flow**: It starts by reading dispatchable turns for the current workspace. For each parked turn, it reads related inbound speakers, seat state, spending policy, and balance state; if any gate refuses, that turn is skipped. Turns that pass are marked and enqueued for execution.

**Call relations**: A scheduled core job calls this through `core_jobs._dispatch_turns`. It relies on `_dispatchable_turns` to find possible work and `_enqueue` to safely stamp and offer each accepted turn.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 7 external calls (__init__, __init__, __init__, select, workspace_tx, authority_member_id, turn_authority).


##### `TurnDispatcher.candidate_workspaces`  (lines 215–223)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This function finds workspaces that contain at least one turn eligible for dispatch. It prevents the dispatcher job from running in every workspace on every tick.

**Data flow**: It reads the owner-level database view, applies the same eligibility rules used by the dispatcher, and returns distinct workspace IDs that have queued or parked work ready.

**Call relations**: The turn-dispatch job gives this function to `JobRunner.tick`. The tick uses the returned workspace IDs to queue one dispatcher workflow per workspace.

*Call graph*: calls 1 internal fn (_eligible); 3 external calls (now, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 225–266)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: This helper loads the specific turns that the dispatcher should consider in the current workspace. It limits the batch so one sweep cannot monopolize the process.

**Data flow**: It reads the current time and queries turn and conversation rows that satisfy `_eligible`. It orders queued turns before parked ones, then by creation and sequence, and converts the database rows into `_DispatchTurn` records.

**Call relations**: `TurnDispatcher.run` calls this at the start of its sweep. The returned records are then checked and passed one by one to `_enqueue`.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 5 external calls (__init__, now, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 268–299)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: This helper safely marks one turn as offered and places it onto the correct DBOS queue. It is careful not to enqueue a turn if another process already claimed it first.

**Data flow**: It receives a `_DispatchTurn`, computes the grace cutoff, and updates the turn row only if it is still in the expected state, still due, still stale, and still first in line. If that update succeeds, it builds enqueue options and sends the turn ID and workspace ID to DBOS.

**Call relations**: `TurnDispatcher.run` calls this after a turn passes its checks. It uses `_first_in_status`, `_retry_due`, and `_stale` to repeat the safety rules at the exact moment of claiming.

*Call graph*: calls 3 internal fn (_first_in_status, _retry_due, _stale); called by 1 (run); 6 external calls (now, timedelta, update, workspace_tx, turn_queue_for, uuid4).


##### `TurnDispatcher._eligible`  (lines 301–322)

```
def _eligible(self, now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This function builds the database condition for turns that are allowed to be considered for dispatch. It protects conversation order by allowing only the first queued or parked turn when no sibling turn is running.

**Data flow**: It receives the current time and produces a SQL condition. The condition checks turn status, stale dispatch stamp, retry time, absence of a running sibling, and whether this turn is first among turns of its same status.

**Call relations**: `candidate_workspaces` uses this to find workspaces with possible work, and `_dispatchable_turns` uses it to fetch actual turn rows. `_enqueue` repeats parts of the rule when it claims a row.

*Call graph*: calls 3 internal fn (_first_in_status, _retry_due, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 5 external calls (timedelta, and_, exists, or_, select).


##### `TurnDispatcher._retry_due`  (lines 324–325)

```
def _retry_due(self, now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This small helper expresses the rule that a turn may be retried only when it has no retry time or its retry time has arrived.

**Data flow**: It receives the current time and returns a SQL condition comparing that time with the turn's `retry_at` column.

**Call relations**: `_eligible` uses it during scanning, and `_enqueue` uses it again during the final update so a race cannot sneak in a not-yet-due turn.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._stale`  (lines 327–331)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This helper expresses whether a turn's previous dispatch offer is old enough to be retried. It is a safety net for cases where a process stamped a row but crashed before enqueueing it.

**Data flow**: It receives a cutoff time and returns a SQL condition that accepts rows with no dispatch stamp or a stamp older than the cutoff.

**Call relations**: `_eligible` uses it to find candidates, and `_enqueue` uses it again when atomically claiming a turn.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 333–342)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: This helper makes sure a conversation's turns do not overtake each other. For a given status, it accepts only the turn with the lowest sequence number.

**Data flow**: It receives a turn status and returns a SQL condition saying there must be no earlier turn in the same workspace and conversation with that status.

**Call relations**: `_eligible` uses this to scan only first-in-line turns, and `_enqueue` uses it again during the final claim.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `_page_beyond_cursor`  (lines 345–350)

```
def _page_beyond_cursor(revision: int, page_id: UUID, cursor: object) -> bool
```

**Purpose**: This helper answers whether a page position is newer than a stored page-change cursor. It is used to decide whether a workspace has page changes a hook has not seen yet.

**Data flow**: It receives a page revision, a page ID, and a cursor value. With no cursor, it returns true. Otherwise it parses the cursor and compares revision first, then page ID, returning true only if the page is past that cursor.

**Call relations**: `PageChangeRunner.workspaces_with_changes` calls this while checking each workspace's newest page against that consumer's stored cursor.

*Call graph*: called by 1 (workspaces_with_changes); 1 external calls (page_cursor).


##### `PageChangeConsumer.spec_name`  (lines 371–373)

```
def spec_name(self) -> str
```

**Purpose**: This property gives a page-change consumer its unique job-spec name. The name includes the extension and handler discriminator so two hooks do not share one schedule or cursor by accident.

**Data flow**: It reads the consumer's extension name and discriminator, then returns a string in the form `page_change:<extension>:<discriminator>`.

**Call relations**: `core_jobs` uses this property when creating one core job per page-change consumer.


##### `PageChangeConsumer.job`  (lines 376–381)

```
def job(self) -> str
```

**Purpose**: This property gives the binding key used when the core runner executes this consumer's hook. It also gives metrics and model usage a precise job label.

**Data flow**: It reads the consumer's generated spec name and returns it under the core namespace, because the scheduler is core-owned even though the hook belongs to an extension.

**Call relations**: `PageChangeRunner._context_for` passes this key into the extension context so hook work is attributed to the correct page-change consumer.


##### `PageChangeRunner.consumers`  (lines 425–449)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: This function discovers all page-change hooks declared by active extension manifests. It turns each hook into an independent consumer so one hook's cursor, schedule, and failures do not affect another's.

**Data flow**: It reads each manifest's credential declarations and hook list. For hooks whose event is `page_change`, it uses the handler function name as a discriminator, rejects duplicate discriminators within one extension, and returns `PageChangeConsumer` objects.

**Call relations**: `core_jobs` calls this when building the deploy's core job list. Each returned consumer becomes its own scheduled page-change job.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 451–519)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: This function finds the workspaces where a specific page-change consumer has pending pages to process. It avoids running the consumer in workspaces whose newest page is already at or behind that consumer's cursor.

**Data flow**: It receives a `PageChangeConsumer`, builds that consumer's cursor key, and reads stored cursors plus each funded workspace's newest page. It compares newest page positions to cursor positions, treats invalid cursors as pending for that workspace, and returns workspace IDs with work.

**Call relations**: The candidate function created by `core_jobs._consumer_candidates` calls this. `JobRunner.tick` then uses the returned workspace IDs to enqueue page-change workflows only where needed.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 4 external calls (select, owner_tx, warn, funded).


##### `PageChangeRunner.drive`  (lines 521–571)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: This function runs one page-change consumer inside the currently bound workspace. It reads changed pages in batches, calls the hook, and advances that hook's cursor only after the hook succeeds.

**Data flow**: It receives a `PageChangeConsumer`, builds an extension context, reads the stored cursor, fetches a batch of changed pages, and passes those changes to the hook. If the hook succeeds, it writes the next cursor with a compare-and-set check; if the hook fails, it logs and counts the stall and leaves the cursor unchanged.

**Call relations**: The handler produced by `core_jobs._drive_consumer` calls this during a per-workspace job workflow. It uses `_context_for` to give the hook the right extension tools and model configuration.

*Call graph*: calls 1 internal fn (_context_for); 6 external calls (__init__, __init__, emit_metric, formatted_stack, log_error, ws_current).


##### `PageChangeRunner._context_for`  (lines 573–591)

```
def _context_for(self, consumer: PageChangeConsumer) -> ExtensionContext
```

**Purpose**: This helper builds the extension context used by a page-change hook. The context is the bundle of workspace-scoped tools the hook may use, such as pages, models, blobs, indexes, and turn invocation.

**Data flow**: It receives a `PageChangeConsumer`, optionally creates a turn invoker for the current workspace, chooses the background model registry, and returns an `ExtensionContext` scoped to that consumer's extension and job key.

**Call relations**: `PageChangeRunner.drive` calls this before invoking the hook. It hands off to the shared `context_for` builder and uses `_background_registry` to apply the background-job model choice.

*Call graph*: calls 1 internal fn (_background_registry); called by 1 (drive); 2 external calls (context_for, ws_current).


##### `_background_registry`  (lines 594–605)

```
def _background_registry(registry: ModelRegistry | None, background_model: str | None) -> ModelRegistry | None
```

**Purpose**: This helper returns the model registry a background job should use. If a background model is configured, it swaps the registry's default automatic model to that one while leaving the rest of the registry intact.

**Data flow**: It receives an optional model registry and optional background model name. If either is missing, it returns the original registry. Otherwise it returns a copied registry whose `auto_model` points at the background model.

**Call relations**: `PageChangeRunner._context_for` uses this for page-change hooks, and `JobRunner.fire` uses it for jobs that do not explicitly require the deploy's normal model.

*Call graph*: called by 2 (fire, _context_for); 1 external calls (replace).


##### `model_key_slots`  (lines 608–617)

```
def model_key_slots(registry: ModelRegistry | None) -> tuple[str, ...]
```

**Purpose**: This helper lists the credential key slots used by models in the registry. The job system uses this information to recognize workspaces that pay for model calls with their own keys.

**Data flow**: It receives an optional model registry. If none is present, it returns an empty tuple; otherwise it collects all non-empty model key slots, removes duplicates, sorts them, and returns them.

**Call relations**: The returned slots are meant for candidate and funding checks such as page-change workspace selection, so background work can be admitted correctly when a workspace supplies its own model key.


##### `core_jobs`  (lines 620–726)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, page_change_runner: PageChangeRunner, delivery_sweep: ResultDeliverer, preview_renderer: PreviewRenderer | None) -> tuple[JobSpe
```

**Purpose**: This function builds the list of background jobs that the core product always provides. It wraps core services such as source sync, page-change fan-out, turn dispatch, result delivery, census counting, and optional preview rendering into `JobSpec` objects.

**Data flow**: It receives the core runner objects and optional preview renderer. It creates small async handler functions and candidate functions, asks the page-change runner for consumers, and returns a tuple of job specifications with names, schedules, handlers, and candidate finders.

**Call relations**: Startup code can pass this output to `bindings_from` along with extension jobs. Later, `JobRunner.launch`, `tick`, and `fire` use those specs to schedule and execute the core background work.

*Call graph*: calls 1 internal fn (consumers); 2 external calls (__init__, seated_member_workspaces).


##### `core_jobs._sync_sources`  (lines 644–645)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: This inner handler runs the source-sync driver for one workspace. It is the scheduled path that polls configured sources and brings their pages into the system.

**Data flow**: It receives an extension context but does not need to read it directly. It calls the sync driver, which reads source state and writes updated page data, then returns nothing.

**Call relations**: The source-sync `JobSpec` uses this as its handler. `JobRunner.fire` calls it after binding the target workspace.


##### `core_jobs._dispatch_turns`  (lines 647–648)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: This inner handler runs the turn dispatcher for one workspace. It is the scheduled repair sweep for queued or parked conversation turns.

**Data flow**: It receives an extension context but delegates the actual work to `turn_dispatcher.run`. That dispatcher reads turn rows, checks gates, stamps eligible rows, and queues them.

**Call relations**: The turn-dispatch `JobSpec` uses this handler. `JobRunner.fire` invokes it during the scheduled dispatch job.


##### `core_jobs._deliver_results`  (lines 650–651)

```
async def _deliver_results(context: ExtensionContext) -> None
```

**Purpose**: This inner handler runs the result-delivery sweep for one workspace. It lets completed delegated work be handed back to the main conversation flow.

**Data flow**: It receives an extension context, calls `delivery_sweep.run`, and returns after the deliverer has processed its pending results.

**Call relations**: The result-delivery `JobSpec` uses this handler. The concrete deliverer is supplied from outside this file through the `ResultDeliverer` protocol.


##### `core_jobs._census_product`  (lines 653–655)

```
async def _census_product(context: ExtensionContext) -> None
```

**Purpose**: This inner handler updates product and onboarding census information. It is used to count broad product usage and funnel state from core tables.

**Data flow**: It receives an extension context, calls `product_census`, then calls `onboarding_census`. Those routines read product data and write or emit their census results.

**Call relations**: The product-census `JobSpec` uses this handler. `JobRunner.fire` runs it in each candidate workspace chosen by `seated_member_workspaces`.

*Call graph*: 2 external calls (onboarding_census, product_census).


##### `core_jobs._render_previews`  (lines 657–659)

```
async def _render_previews(context: ExtensionContext) -> None
```

**Purpose**: This inner handler runs preview rendering for one workspace when preview rendering is configured. It is omitted entirely when no renderer is supplied.

**Data flow**: It receives an extension context, confirms the renderer exists, calls `preview_renderer.run`, and returns after previews have been rendered or updated.

**Call relations**: When `preview_renderer` is not `None`, `core_jobs` places this handler in the preview-render job specification.


##### `core_jobs._preview_candidates`  (lines 661–663)

```
async def _preview_candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner candidate finder asks the preview renderer which workspaces need preview work. It keeps preview rendering from running where there is nothing to render.

**Data flow**: It receives no arguments, confirms the renderer exists, calls its candidate finder, and returns the workspace IDs it reports.

**Call relations**: The optional preview-render `JobSpec` uses this as its candidate function. `JobRunner.tick` calls it before queuing preview jobs.


##### `core_jobs._drive_consumer`  (lines 665–671)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: This inner factory creates a job handler for one page-change consumer. It closes over the consumer so each generated job drives exactly one hook and cursor.

**Data flow**: It receives a `PageChangeConsumer` and returns an async handler function. The returned handler later receives an extension context and calls the page-change runner for that same consumer.

**Call relations**: `core_jobs` calls this while building page-change `JobSpec` objects. The returned `core_jobs._drive_consumer._handler` is what `JobRunner.fire` eventually invokes.


##### `core_jobs._drive_consumer._handler`  (lines 668–669)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: This generated handler runs the page-change cursor loop for the consumer captured by its outer factory. It is the actual job handler stored in the page-change `JobSpec`.

**Data flow**: It receives an extension context but delegates to `page_change_runner.drive`, which reads the cursor, loads changed pages, calls the hook, and advances the cursor.

**Call relations**: `core_jobs._drive_consumer` creates this function. `JobRunner.fire` calls it when a page-change job workflow runs for a workspace.


##### `core_jobs._consumer_candidates`  (lines 673–677)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: This inner factory creates a candidate finder for one page-change consumer. It makes sure each page-change job only runs in workspaces with pending pages for that exact hook.

**Data flow**: It receives a `PageChangeConsumer` and returns an async candidate function. The returned function later asks `PageChangeRunner.workspaces_with_changes` for workspace IDs.

**Call relations**: `core_jobs` uses this when creating page-change `JobSpec` objects. `JobRunner.tick` calls the generated candidate function before enqueueing workflows.


##### `core_jobs._consumer_candidates._candidates`  (lines 674–675)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: This generated candidate function returns workspaces with pending page changes for its captured consumer. It is specific to one extension hook and one cursor.

**Data flow**: It receives no arguments, calls `page_change_runner.workspaces_with_changes` with the captured consumer, and returns the resulting workspace IDs.

**Call relations**: `core_jobs._consumer_candidates` creates this function. It is later called by `JobRunner.tick` through the page-change job's `candidates` field.


##### `bindings_from`  (lines 738–769)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...], disabled: frozenset[str]=frozenset()) -> tuple[_Binding, ...]
```

**Purpose**: This function combines core jobs and extension jobs into runnable bindings with stable keys. A binding says which extension owns the job, which credentials it declared, and what job spec to run.

**Data flow**: It receives manifests, core job specs, and an optional set of disabled job keys. It creates core bindings under the core namespace, extension bindings under each extension name, rejects disabled keys that do not exist, removes disabled bindings, and returns the final tuple.

**Call relations**: Startup code uses this before creating `JobRunner`. `JobRunner` then uses the bindings to register schedules, find candidates, build extension contexts, and run handlers.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 799–823)

```
def launch(self) -> None
```

**Purpose**: This startup method publishes the active job runner and registers all discovered jobs with DBOS. It schedules repeating jobs and enqueues one-shot jobs without relying on import-time decorators.

**Data flow**: It stores itself in the module-level `_firing` variable, walks each binding, and either enqueues an immediate tick with a deduplication key or builds a schedule record. It logs what it did and applies all schedules through DBOS.

**Call relations**: The serving process calls this during boot. Later, DBOS invokes `job_tick`, which looks up this same runner through `_firing`.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 825–847)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: This method handles one scheduled firing of a job. It fans the job out to only the workspaces that currently have work, while deduplicating by job and workspace.

**Data flow**: It receives the scheduled time and job key. If this process does not know the key, it logs a warning and stops. Otherwise it asks for candidate workspaces and enqueues one `job_workflow` per workspace with a deduplication ID.

**Call relations**: `job_tick` calls this when DBOS fires a schedule or one-shot tick. It calls `candidates`, which uses the job binding's candidate finder, then hands each workspace to DBOS for `job_workflow` execution.

*Call graph*: calls 2 internal fn (_registered, candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 849–850)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: This method returns the workspaces that should receive a run for a given job key. It is a thin safety wrapper around the job spec's candidate function.

**Data flow**: It receives a job key, resolves the binding, calls that binding's `spec.candidates` function, and returns the tuple of workspace IDs.

**Call relations**: `JobRunner.tick` calls this after confirming the job is registered. If the key is unexpectedly missing, `_binding` raises a clear fault.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 852–896)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: This method runs one job handler inside one workspace. It binds workspace context, provisions agents once per process for that workspace, builds the extension context, and handles expected spend refusals separately from real failures.

**Data flow**: It receives a job key and workspace ID. It resolves the binding, enters the workspace scope, applies provisioning if needed, creates the right model and extension context, and awaits the job handler. Spend refusals are deferred and may notify a member; other exceptions are logged, counted, and re-raised.

**Call relations**: `job_fire` calls this as the DBOS step that actually executes job code. It uses `_binding`, `_background_registry`, `_deferred_on_spend`, and the shared `context_for` mechanism.

*Call graph*: calls 3 internal fn (_binding, _deferred_on_spend, _background_registry); 7 external calls (__init__, failed_statement, emit_metric, formatted_stack, log_error, context_for, ws).


##### `JobRunner._deferred_on_spend`  (lines 898–946)

```
async def _deferred_on_spend(self, key: str, workspace_id: UUID, refusal: OffTurnSpendRefused) -> None
```

**Purpose**: This helper treats a spending refusal as a planned pause rather than a job failure. It records that the workspace has already been told about this model-specific refusal and tries to notify a member once.

**Data flow**: It receives the job key, workspace ID, and refusal object. It logs the deferral, reads a scoped store mark for the refused model, writes the new outcome only if appropriate, and asks `_tell_the_member` to create a notice turn. If no notice can be created, it removes the mark so a later refusal can try again.

**Call relations**: `JobRunner.fire` calls this when a handler raises `OffTurnSpendRefused`. It may call `_tell_the_member` to hand the explanation back through the normal turn system.

*Call graph*: calls 1 internal fn (_tell_the_member); called by 1 (fire); 3 external calls (__init__, log, spend_refusal_notice_key).


##### `JobRunner._tell_the_member`  (lines 948–1019)

```
async def _tell_the_member(self, key: str, workspace_id: UUID, refusal: str) -> UUID | None
```

**Purpose**: This helper opens a normal scheduled turn to tell a member that background work is paused by spend rules. It chooses a recent seated member's own or shared conversation so the message reaches someone appropriate inside the workspace.

**Data flow**: It receives the job key, workspace ID, and refusal text. If no invoker is available, it returns `None`. Otherwise it queries for the most recent eligible member turn in a safe audience and with a live agent, then invokes a new scheduled turn carrying the refusal explanation. It returns the new turn ID, or `None` if nobody could be told.

**Call relations**: `JobRunner._deferred_on_spend` calls this after claiming the notice mark. It uses the invoker factory to hand the notice to the turn-invocation subsystem.

*Call graph*: called by 1 (_deferred_on_spend); 8 external calls (__init__, and_, or_, select, workspace_tx, warn, agent_is_live, uuid4).


##### `JobRunner._registered`  (lines 1021–1022)

```
def _registered(self, key: str) -> _Binding | None
```

**Purpose**: This helper checks whether this process has a binding for a job key. It allows stale or peer-owned schedules to be skipped without crashing the process.

**Data flow**: It receives a job key, scans the runner's bindings, and returns the matching binding or `None`.

**Call relations**: `JobRunner.tick` uses this to skip unknown scheduled keys politely. `_binding` uses it when a missing key should be treated as a programming or routing fault.

*Call graph*: called by 2 (_binding, tick).


##### `JobRunner._binding`  (lines 1024–1031)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: This helper returns the binding for a job key or raises a clear error if none exists. It is used in paths where the caller expects the job to be runnable by this process.

**Data flow**: It receives a key, calls `_registered`, and either returns the binding or raises `RuntimeError` naming the missing key.

**Call relations**: `JobRunner.candidates` and `JobRunner.fire` call this before using a job spec. It relies on `_registered` for the actual lookup.

*Call graph*: calls 1 internal fn (_registered); called by 2 (candidates, fire).


##### `job_tick`  (lines 1038–1042)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: This DBOS workflow is the durable scheduled entry point for a job tick. It asks the active `JobRunner` to fan one scheduled firing out to candidate workspaces.

**Data flow**: It receives the scheduled time and job key from DBOS. It reads the module-level runner, raises if jobs were not launched, and passes the values to `runner.tick`.

**Call relations**: `JobRunner.launch` registers this workflow with DBOS schedules or one-shot enqueues. It is the first durable step in the background-job fan-out.


##### `job_workflow`  (lines 1046–1047)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: This DBOS workflow represents one job run for one workspace. It exists so each workspace execution is durable and deduplicated separately.

**Data flow**: It receives the scheduled time, job key, and workspace ID string. It does not use the time directly; it calls `job_fire` with the key and workspace ID.

**Call relations**: `JobRunner.tick` enqueues this workflow once per candidate workspace. It hands the actual execution to `job_fire`, which is a DBOS step.

*Call graph*: calls 1 internal fn (job_fire).


##### `job_fire`  (lines 1051–1055)

```
async def job_fire(key: str, workspace_id: str) -> None
```

**Purpose**: This DBOS step performs the actual job handler call for a workspace. Marking it as a step lets DBOS record completion so recovery does not rerun already completed handler work unnecessarily.

**Data flow**: It receives a job key and workspace ID string, reads the active runner, converts the workspace ID to a UUID, and calls `runner.fire`. It returns nothing after the job finishes or propagates any real failure.

**Call relations**: `job_workflow` calls this. It is the final bridge from DBOS durable workflow plumbing into `JobRunner.fire`, where workspace binding and handler execution happen.

*Call graph*: called by 1 (job_workflow); 1 external calls (UUID).


### Maintenance drains and cleanup
These recurring jobs repair missed previews, drain pending app notifications, curate stored memory, and remove stale seeded homepage bindings.

### `core/src/ufo/runtime/media/preview_renderer.py`

`orchestration` · `scheduled background job`

When someone shares a document, the system tries to create a preview image right away. That first attempt is deliberately best-effort: if the preview service is briefly down, the file is still shared, but the database row is left with empty preview fields. This file provides the follow-up job that catches those missed previews.

It works like a delivery coordinator. It does not download the document itself. Instead, it gives the preview service two temporary signed links: one link to read the original file, and one link to upload the generated PNG preview. A signed link is a short-lived permission slip for storage. The preview service fetches the source, renders the image, uploads it, and reports the image size. The core app then stores the preview key, media type, and size in the database.

The job only retries files created within a one-hour window. That matters because some files are permanently impossible to preview, such as corrupt documents. Without the time window, the system could waste work retrying the same bad file forever. Each run takes only a small batch, and failed items are simply left for a future scheduled run.

#### Function details

##### `_eligible`  (lines 58–59)

```
def _eligible(filename_column: sa.Column) -> sa.ColumnElement[bool]
```

**Purpose**: This helper builds the database test for whether a filename looks like a document type the preview system can draw. It keeps the retry job focused on supported file extensions instead of trying every shared file.

**Data flow**: It receives a database column that contains filenames. It turns the known previewable suffixes, such as .pdf or .docx, into case-insensitive filename checks. It returns one combined database condition that means “the filename ends with one of these supported suffixes.”

**Call relations**: The main preview scan and the workspace discovery scan both call this helper when building their database queries. It gives them the same definition of “worth trying to preview,” so they do not drift apart.

*Call graph*: called by 2 (candidate_workspaces, run); 2 external calls (ilike, or_).


##### `render_document_cover`  (lines 72–115)

```
async def render_document_cover(blob: WorkspaceBlobStore, service_url: str, blob_key: str, filename: str, client: httpx.AsyncClient) -> DrawnCover | None
```

**Purpose**: This function asks the external preview service to create one PNG cover image for one stored document. It is designed so the application never has to read the document bytes itself.

**Data flow**: It receives the blob store, the preview service address, the stored file key, the original filename, and an already-open HTTP client. It checks the filename extension to decide what kind of file this is. If the type is unsupported, it returns nothing. Otherwise, it creates a new preview storage key, asks the blob store for a temporary read link for the source and a temporary upload link for the preview, then posts those links and rendering limits to the preview service. If the service succeeds, it returns a DrawnCover containing the preview key and reported byte size. If signing fails, the service cannot be reached, or the service refuses the request, it logs where useful and returns nothing.

**Call relations**: PreviewRenderer._render_one calls this when it is ready to try a specific shared file. This function hands work off to the blob store for signed URLs and to the preview service over HTTP. It hands back only the information needed to update the database, not the preview image itself.

*Call graph*: calls 2 internal fn (presigned_get, presigned_put_unmeasured); called by 1 (_render_one); 6 external calls (__init__, post, dumps, PurePosixPath, log, uuid4).


##### `PreviewRenderer.run`  (lines 129–151)

```
async def run(self) -> None
```

**Purpose**: This is the main body of one preview-retry pass for a workspace. It finds a small batch of recent shared files that still have no preview and tries to render each one.

**Data flow**: It starts by calculating the cutoff time, one retry window before now. Inside a workspace-scoped database transaction, it selects recent shared artifacts whose preview key is still empty and whose filename is eligible. It limits the result to the configured batch size. If there are no rows, it stops. If there are rows, it opens one HTTP client with the render timeout and passes each file to _render_one.

**Call relations**: A scheduler or jobs layer constructs PreviewRenderer and calls run for a workspace that may need preview repairs. run uses _eligible to build the database filter, then delegates each actual rendering attempt to PreviewRenderer._render_one.

*Call graph*: calls 2 internal fn (_render_one, _eligible); 4 external calls (now, AsyncClient, select, workspace_tx).


##### `PreviewRenderer._render_one`  (lines 153–169)

```
async def _render_one(self, client: httpx.AsyncClient, blob_key: str, filename: str) -> None
```

**Purpose**: This function tries to render and record the preview for one shared artifact. It is the step that connects a successful render result back to the database row.

**Data flow**: It receives an HTTP client, the source blob key, and the filename. It calls render_document_cover to ask the preview service to create the PNG. If no preview was produced, it changes nothing. If a preview was produced, it opens a workspace-scoped database transaction and updates the matching shared artifact row, but only if its preview is still missing. It writes the preview blob key, the PNG media type, and the preview size in bytes.

**Call relations**: PreviewRenderer.run calls this once for each candidate row in the batch. This function relies on render_document_cover for the external rendering work, then performs the database update that makes the preview visible to the rest of the system.

*Call graph*: calls 1 internal fn (render_document_cover); called by 1 (run); 2 external calls (update, workspace_tx).


##### `PreviewRenderer.candidate_workspaces`  (lines 171–185)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This function finds which workspaces currently have recent shared files that may still need preview images. It lets the scheduler avoid running the retry job for workspaces with nothing to fix.

**Data flow**: It calculates the same retry cutoff time used by the renderer. In an owner-level database transaction, which can see across workspaces, it selects distinct workspace IDs from shared artifacts whose preview is missing, whose creation time is still inside the retry window, and whose filename is eligible. It returns those workspace IDs as a tuple.

**Call relations**: The jobs layer can call candidate_workspaces before running workspace-specific preview repair. It uses _eligible so workspace discovery and actual rendering agree on which file types count as preview candidates.

*Call graph*: calls 1 internal fn (_eligible); 3 external calls (now, select, owner_tx).


### `extensions/app_notification/ufo_ext_app_notification/drain.py`

`orchestration` · `recurring background tick`

This file is like a mailroom worker who comes by on a schedule, picks up open notices from each inbox lane, and delivers a bundled envelope to the correct conversation. A “lane” is the notification stream for one member and one agent. The drain only works when the notification feature flag is enabled, and it only wakes lanes belonging to the inbox agent provisioned by this extension. That matters because a member’s authority should not be used to run work for some unrelated agent.

On each tick, the drain asks the notification store which lanes have untriaged notifications and are not inside the cooldown window. The cooldown is a safety delay that stops a conversation from immediately waking itself again from notifications it just read. For each eligible lane, it claims a small batch under a temporary lease, so another drain tick will not grab the same rows at the same time. It then opens or reuses the lane’s conversation and invokes a turn carrying all notifications inline.

The invoke happens before rows are marked triaged. This is deliberate. If the process crashes after invoking but before marking, retrying uses the same idempotency key for the same batch, so it does not create duplicate work. The notification bodies are wrapped as untrusted data so a notification cannot smuggle instructions that break out of the batch format.

#### Function details

##### `InboxDrain.run`  (lines 53–70)

```
async def run(self) -> None
```

**Purpose**: Runs one scheduled pass over the workspace’s notification inbox lanes. It decides whether the drain should run at all, finds eligible pending notifications, and asks the wake-up step to deliver each claimed batch.

**Data flow**: It starts with the extension context stored on the InboxDrain. It reads the notification feature flag, looks up this extension’s inbox agent, creates a notification store, and asks the store for lanes with untriaged rows that are old enough to process. For each lane that belongs to the inbox agent, it claims up to the batch limit under a lease; if rows are claimed, it passes the store, lane, and notifications onward. It returns nothing, but it may cause notification rows to be claimed and then later triaged through the wake-up path.

**Call relations**: This is the top-level method for the drain’s clock tick. It calls the flag service first so disabled deployments wake nobody, asks the store layer for lanes and claims, and calls InboxDrain._wake only after it has a real batch for the correct inbox agent.

*Call graph*: calls 1 internal fn (_wake); 3 external calls (__init__, flag_enabled, inbox_agent_id).


##### `InboxDrain._wake`  (lines 72–96)

```
async def _wake(self, store: NotificationStore, lane: Lane, batch: tuple[Notification, ...]) -> None
```

**Purpose**: Turns one claimed batch of notifications into a conversation turn for the member’s lane. It also marks the notifications as triaged, but only after the conversation invoke succeeds.

**Data flow**: It receives the notification store, the lane being processed, and the claimed notification rows. It opens or reuses a conversation for that member and agent, builds a hash from each notification row’s id and occurrence count, and uses that hash as part of an idempotency key, meaning the same exact batch will not create duplicate work if retried. It formats the batch into a message, invokes the agent turn using the member’s own authority, and if a turn id is returned, writes that turn id back to the notification store as the triage mark. If the agent has been archived, that error is suppressed and the rows are not marked triaged here.

**Call relations**: InboxDrain.run calls this after it has claimed a batch. This method hands the human-readable batch body to drain_message, asks the runtime context to open and invoke the conversation, uses authority_from_member_id so the work runs as the addressed member, and then calls NotificationStore.mark_triaged so the same notifications are no longer considered open.

*Call graph*: calls 2 internal fn (drain_message, mark_triaged); called by 1 (run); 3 external calls (suppress, sha256, authority_from_member_id).


##### `drain_message`  (lines 99–112)

```
def drain_message(batch: tuple[Notification, ...]) -> str
```

**Purpose**: Builds the text message that the agent will read for a batch of notifications. It presents each notification’s reference, subject, count, producer, timing, and body in a safe wrapper.

**Data flow**: It receives a tuple of notification records. It starts a notifications block with the batch count, adds one section per row with identifying details and timestamps, and wraps the notification body with wall, which treats the body as untrusted quoted data rather than instructions. Before returning the final string, it escapes any closing notifications tag that appears inside the content so the batch cannot be prematurely closed by the notification text.

**Call relations**: InboxDrain._wake calls this just before invoking the conversation turn. This function depends on the untrusted-data wrapper from the SDK so the message can include another agent’s words safely while still giving the receiving agent enough context to triage the batch.

*Call graph*: called by 1 (_wake); 1 external calls (wall).


### `extensions/memory/ufo_ext_memory/condenser.py`

`domain_logic` · `background page-change consumption and periodic memory maintenance`

The memory system stores many small rows: facts from synced pages, notes written by tools, summaries, and profile entries. Without this file, those rows would pile up forever. A source page could change while its old facts stayed visible, repeated statements would crowd out useful ones, and summary paragraphs would describe old data instead of what the workspace currently knows.

The file has several cooperating workers. FactDeriver reads changed source pages and asks a model to pull out concrete facts, then replaces the older facts from those same page revisions. MemoryConsolidator looks for older, related tool-written facts and turns clusters of them into one broader semantic summary. MemoryDeduper finds near-duplicate tool-written rows and points the older copies at the newest one, without using a model. SectionWriter and OverviewWriter rewrite the short paragraphs that sit above wiki sections and the shared workspace page, so the prose matches the live facts underneath. ProfileWriter writes a role and current focus for each member into a separate people table. PagePass reads an entire subject page at once and retires page-derived rows that repeat another surviving row.

A key theme is caution. Model calls happen before database write transactions, inputs are capped, model output is validated, and destructive actions have safety checks. The file treats memory like a living notebook: add useful notes, combine repeated ones, update headings, and cross out only what is safely replaced.

#### Function details

##### `section_headings`  (lines 176–182)

```
def section_headings(subject: str) -> dict[MemoryKind, str]
```

**Purpose**: Chooses the section titles for a memory page. Shared workspace pages use team-oriented wording, while a person’s own page uses wording addressed to that person.

**Data flow**: It receives a subject string, checks whether that subject is the shared workspace subject, and returns the matching dictionary of memory-kind-to-heading labels.

**Call relations**: SectionWriter uses this when deciding which bands can be summarized and when telling the model what heading a paragraph will sit under. PagePass uses it when rebuilding a subject’s page for whole-page curation.

*Call graph*: called by 3 (_page, _sections, _summarize); 1 external calls (subject_shared).


##### `live_page_link`  (lines 244–257)

```
def live_page_link() -> ColumnElement[bool]
```

**Purpose**: Builds the database condition that says a fact still belongs to the current version of the source page it came from. This prevents old facts from being treated as live after their source page has moved on.

**Data flow**: It reads no rows itself. It returns a SQL condition joining memory rows to mirrored page rows by page id, workspace, subject, and revision.

**Call relations**: member_servable uses it to decide which rows a member could see. PagePass uses it directly because it only curates page-derived rows whose source page is still current.

*Call graph*: called by 2 (_page, member_servable); 1 external calls (and_).


##### `member_servable`  (lines 260–272)

```
def member_servable() -> ColumnElement[bool]
```

**Purpose**: Builds the database condition for rows that can be shown to a member. Tool-written rows count as live on their own, while page-derived rows must still match the current page revision.

**Data flow**: It returns a SQL condition: either the memory row has no source page, or a matching current page exists through live_page_link.

**Call relations**: SectionWriter, OverviewWriter, and ProfileWriter use this condition before writing prose based on facts, so their summaries match what readers can actually see.

*Call graph*: calls 1 internal fn (live_page_link); called by 4 (_facts, _facts, _facts, _sections); 2 external calls (or_, select).


##### `ExtractedFact.within_row_budget`  (lines 312–313)

```
def within_row_budget(cls, body: str) -> str
```

**Purpose**: Keeps an extracted fact short enough to fit the memory row size limit. It cuts at a word boundary so stored text does not end mid-word.

**Data flow**: It receives a model-written fact body, trims it with clip_to_word if needed, and returns the safe body text.

**Call relations**: Pydantic calls this during ExtractedFact validation inside FactDeriver._extract, before any extracted fact can be committed to the memory store.

*Call graph*: 1 external calls (clip_to_word).


##### `FactDeriver.apply`  (lines 349–364)

```
async def apply(self, changes: tuple[PageChange, ...]) -> None
```

**Purpose**: Processes a delivered batch of changed source pages. It retires facts for deleted or machine-status pages, and derives new facts for eligible live pages.

**Data flow**: It receives page changes, asks the store which pages are still live, filters out deleted, tiny, and skipped streams, batches the rest, derives facts for each batch, and supersedes old page facts only where replacements landed.

**Call relations**: The page-change runner calls this worker. It hands eligible batches to FactDeriver._derive and then tells the store which newly committed fact ids should replace earlier facts from each page.

*Call graph*: calls 1 internal fn (_derive); 1 external calls (batched).


##### `FactDeriver._derive`  (lines 366–405)

```
async def _derive(self, pages: tuple[PageChange, ...]) -> dict[UUID, frozenset[UUID]]
```

**Purpose**: Turns one small group of current source pages into stored memory facts. It double-checks that each page is still at the revision being processed before and during the write.

**Data flow**: It receives page changes, reloads current page state, asks _extract for model-produced facts, validates that each referenced page is still current, commits each fact as a MemoryWrite, and returns page ids mapped to the fact ids that landed.

**Call relations**: FactDeriver.apply calls this for each bounded batch. It relies on _extract for the model work and then hands the settled fact ids back so apply can retire the older page-derived facts.

*Call graph*: calls 1 internal fn (_extract); called by 1 (apply); 1 external calls (__init__).


##### `FactDeriver._extract`  (lines 407–484)

```
async def _extract(self, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]
```

**Purpose**: Asks the model to read source page text and record concrete facts through a structured tool call. It validates each returned fact and removes restatements within the same reply.

**Data flow**: It receives current pages, builds a capped JSON payload with page id, title, stream, and body, sends a forced model tool request, reads the tool arguments, validates entries as ExtractedFact, drops invalid entries, collapses near-restatements, and returns the kept facts.

**Call relations**: FactDeriver._derive calls this before opening write transactions. It uses _restates to avoid storing two facts from the same page that say the same thing.

*Call graph*: calls 1 internal fn (_restates); called by 1 (_derive); 4 external calls (__init__, __init__, __init__, dumps).


##### `_content_words`  (lines 487–488)

```
def _content_words(body: str) -> frozenset[str]
```

**Purpose**: Extracts the meaningful words from a sentence-like body. Common filler words are removed so overlap checks focus on the actual claim.

**Data flow**: It receives text, lowercases and splits it into words, removes empty pieces and filler words, and returns a set of content words.

**Call relations**: _restates uses this helper to compare two extracted facts by their meaningful word overlap.

*Call graph*: called by 1 (_restates); 1 external calls (split).


##### `_restates`  (lines 491–503)

```
def _restates(kept: str, candidate: str) -> bool
```

**Purpose**: Decides whether two extracted fact bodies say the same claim in different words. It protects the store from receiving duplicate facts from one model extraction reply.

**Data flow**: It receives two strings, turns each into content-word sets, rejects very short comparisons, and returns true if the shorter claim is mostly covered by the other.

**Call relations**: FactDeriver._extract calls this while building its list of facts. If a new fact restates an earlier one, the longer version is kept.

*Call graph*: calls 1 internal fn (_content_words); called by 1 (_extract).


##### `cosine`  (lines 506–514)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Measures how close two embedding vectors are. An embedding is a list of numbers representing meaning; cosine closeness is used here to group similar memory text.

**Data flow**: It receives two numeric vectors, computes their dot product divided by their sizes, returns 0.0 if either vector has no magnitude, otherwise returns the closeness score.

**Call relations**: MemoryConsolidator._clusters and MemoryDeduper._clusters call this while comparing each row to cluster heads.

*Call graph*: called by 2 (_clusters, _clusters); 2 external calls (sqrt, sumprod).


##### `MemoryConsolidator.run`  (lines 544–553)

```
async def run(self) -> None
```

**Purpose**: Runs the periodic consolidation pass that turns clusters of older related facts into one semantic summary. It skips all work when no model is configured.

**Data flow**: It reads aged facts, groups them by subject, embeds each large-enough group, clusters similar facts in a worker thread, and consolidates each cluster that is big enough.

**Call relations**: The scheduler calls this job. It coordinates _aged_facts, _buckets, _embed, _clusters, and _consolidate in order.

*Call graph*: calls 4 internal fn (_aged_facts, _buckets, _consolidate, _embed); 1 external calls (to_thread).


##### `MemoryConsolidator._aged_facts`  (lines 555–586)

```
async def _aged_facts(self) -> tuple[_AgedFact, ...]
```

**Purpose**: Finds old, live, tool-written fact rows that are candidates for consolidation. Page-derived facts are excluded because they have a different lifecycle.

**Data flow**: It computes an age cutoff, queries memory_item for unsuperseded, unretired fact rows in the workspace that are older than the cutoff and not page-derived, and returns them as _AgedFact objects.

**Call relations**: MemoryConsolidator.run starts with this read before grouping and clustering the candidates.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryConsolidator._buckets`  (lines 588–597)

```
def _buckets(self, facts: tuple[_AgedFact, ...]) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]
```

**Purpose**: Groups consolidation candidates by subject so facts about different people or pages are not merged together.

**Data flow**: It receives aged facts, collects them under their subject, sorts each group by recency, caps each group size, and returns ordered subject buckets.

**Call relations**: MemoryConsolidator.run calls this after reading candidates and before embedding each subject’s facts.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._embed`  (lines 599–603)

```
async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Converts fact text into meaning vectors used for clustering. The text is capped so the embedding request stays bounded.

**Data flow**: It receives facts, sends their clipped bodies to the embedding client, and returns a mapping from fact id to vector.

**Call relations**: MemoryConsolidator.run calls this for each eligible subject bucket, then passes the vectors to _clusters.

*Call graph*: called by 1 (run).


##### `MemoryConsolidator._clusters`  (lines 605–626)

```
def _clusters(self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_AgedFact, ...], ...]
```

**Purpose**: Groups related facts by comparing their embedding vectors. It uses a simple newest-first greedy method so newer facts become cluster anchors.

**Data flow**: It receives facts and their vectors, walks facts newest first, adds each fact to the first close-enough cluster head, or starts a new cluster, then returns all clusters.

**Call relations**: MemoryConsolidator.run runs this in a worker thread so CPU-heavy vector comparisons do not block the async event loop. The clusters it returns are later passed to _consolidate.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryConsolidator._consolidate`  (lines 628–692)

```
async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None
```

**Purpose**: Writes one semantic summary for a cluster and marks the original facts as superseded by it. It checks that the facts have not changed since clustering before making that permanent change.

**Data flow**: It receives a cluster, asks _summarize for summary text, opens a transaction, locks or verifies the donor rows, inserts the summary row, and updates the donor rows with superseded_by pointing to the summary.

**Call relations**: MemoryConsolidator.run calls this for each large-enough cluster. It delegates the model wording to _summarize and owns the database replacement step.

*Call graph*: calls 1 internal fn (_summarize); called by 1 (run); 4 external calls (insert, select, update, uuid4).


##### `MemoryConsolidator._summarize`  (lines 694–704)

```
async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str
```

**Purpose**: Asks the model to turn several related facts into one short summary. The result is trimmed to the same paragraph budget used for overviews.

**Data flow**: It receives a model and cluster, builds a capped list of fact bodies, sends a completion request, strips the text, trims it with _to_overview_budget, and returns the summary string.

**Call relations**: MemoryConsolidator._consolidate calls this before opening its transaction, so database locks are not held while waiting for the model.

*Call graph*: calls 2 internal fn (complete, _to_overview_budget); called by 1 (_consolidate); 3 external calls (__init__, __init__, dumps).


##### `_to_overview_budget`  (lines 707–719)

```
def _to_overview_budget(summary: str) -> str
```

**Purpose**: Trims a model-written paragraph to the allowed length and sentence count. It prefers cutting whole sentences, then falls back to cutting at a word boundary.

**Data flow**: It receives summary text, accumulates sentences until adding another would exceed word or character limits, and returns the kept text or a clipped fallback.

**Call relations**: MemoryConsolidator, SectionWriter, and OverviewWriter all use this after model calls so stored summaries obey the same reader-friendly size limit.

*Call graph*: called by 3 (_summarize, _write, _summarize); 1 external calls (clip_to_word).


##### `_recency`  (lines 722–723)

```
def _recency(fact: _AgedFact) -> tuple[datetime, UUID]
```

**Purpose**: Provides a stable sorting key for facts by creation time and id. This makes newest-first clustering deterministic.

**Data flow**: It receives an _AgedFact and returns its created_at timestamp paired with its id.

**Call relations**: MemoryConsolidator uses this key when ordering facts inside buckets and clusters.


##### `_Group.key`  (lines 743–744)

```
def key(self) -> tuple[str, str]
```

**Purpose**: Returns the identity used by the deduplication cursor. A group is identified by subject and item class.

**Data flow**: It reads the _Group’s subject and item_class fields and returns them as a tuple.

**Call relations**: MemoryDeduper.run uses this key to decide which duplicate group comes next in its rotating sweep.


##### `_Group.fingerprint`  (lines 747–748)

```
def fingerprint(self) -> list[JsonValue]
```

**Purpose**: Returns a compact sign of whether a duplicate group has changed. It records the live copy count and latest update time.

**Data flow**: It reads the group’s copies and latest timestamp, converts the timestamp to text, and returns them as a small JSON-compatible list.

**Call relations**: MemoryDeduper.run compares this value with a saved store value to skip groups that have not changed since the last completed sweep.


##### `MemoryDeduper.run`  (lines 792–804)

```
async def run(self) -> None
```

**Purpose**: Runs one step of the periodic duplicate cleanup. It processes at most one group per tick and skips unchanged groups.

**Data flow**: It reads duplicate groups, loads the saved cursor, picks the next group after that cursor, saves the new cursor, compares the group fingerprint, runs deduplication if needed, and stores the completed fingerprint.

**Call relations**: The scheduler calls this job repeatedly. It coordinates _groups, _cursor, and _dedup_group, using the scoped store for cursor and fingerprint state.

*Call graph*: calls 3 internal fn (_cursor, _dedup_group, _groups).


##### `MemoryDeduper._cursor`  (lines 806–818)

```
def _cursor(self, stored: JsonValue | None) -> tuple[str, ...]
```

**Purpose**: Validates and decodes the saved deduplication cursor. It treats malformed cursor data as an error instead of silently restarting.

**Data flow**: It receives a stored JSON value, returns an empty tuple when absent, returns a subject/item_class tuple when valid, or raises if the shape is wrong.

**Call relations**: MemoryDeduper.run calls this before choosing which group to sweep next.

*Call graph*: called by 1 (run).


##### `MemoryDeduper._groups`  (lines 820–846)

```
async def _groups(self) -> tuple[_Group, ...]
```

**Purpose**: Finds groups of live tool-written rows that have enough copies to be worth checking for duplicates.

**Data flow**: It queries memory_item for unsuperseded, unretired, non-section rows older than the minimum age, groups by subject and item class, counts copies, records latest update time, and returns _Group objects.

**Call relations**: MemoryDeduper.run uses this list as the ordered route for its cursor-based sweep.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `MemoryDeduper._dedup_group`  (lines 848–853)

```
async def _dedup_group(self, group: _Group) -> None
```

**Purpose**: Deduplicates one selected group by embedding its live copies, clustering near-matches, and collapsing each duplicate cluster.

**Data flow**: It receives a _Group, reads its live copies, embeds their text, clusters them in a worker thread, and collapses clusters with at least two copies.

**Call relations**: MemoryDeduper.run calls this only for a due group. It delegates reading to _live_copies, vector work to _embed and _clusters, and database updates to _collapse.

*Call graph*: calls 3 internal fn (_collapse, _embed, _live_copies); called by 1 (run); 1 external calls (to_thread).


##### `MemoryDeduper._live_copies`  (lines 855–870)

```
async def _live_copies(self, group: _Group) -> tuple[_LiveCopy, ...]
```

**Purpose**: Reads the live rows in one duplicate group, newest first. Newest-first order matters because the newest row becomes the survivor in each duplicate cluster.

**Data flow**: It receives a _Group, queries matching live rows using _live_group, orders them by newest creation time and id, caps the count, and returns _LiveCopy objects.

**Call relations**: MemoryDeduper._dedup_group calls this before embedding and clustering the group.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (__init__, select).


##### `MemoryDeduper._embed`  (lines 872–879)

```
async def _embed(self, copies: tuple[_LiveCopy, ...]) -> dict[UUID, tuple[float, ...]]
```

**Purpose**: Embeds duplicate-candidate row text in bounded batches. This gives clustering a numerical meaning comparison instead of relying on exact text.

**Data flow**: It receives live copies, splits them into fixed-size batches, sends clipped bodies to the embedding client, and returns a mapping from row id to vector.

**Call relations**: MemoryDeduper._dedup_group calls this before sending vectors to _clusters.

*Call graph*: called by 1 (_dedup_group); 1 external calls (batched).


##### `MemoryDeduper._clusters`  (lines 881–910)

```
def _clusters(self, copies: tuple[_LiveCopy, ...], embeddings: dict[UUID, tuple[float, ...]]) -> tuple[tuple[_LiveCopy, ...], ...]
```

**Purpose**: Groups near-duplicate copies by embedding closeness. It keeps the first, newest copy as the cluster head.

**Data flow**: It receives copies and vectors, walks copies in the already-newest-first order, compares each copy to existing cluster heads with cosine, joins a close cluster or starts a new one, and returns clusters.

**Call relations**: MemoryDeduper._dedup_group runs this in a worker thread and then passes duplicate-sized clusters to _collapse.

*Call graph*: calls 1 internal fn (cosine).


##### `MemoryDeduper._collapse`  (lines 912–940)

```
async def _collapse(self, group: _Group, cluster: tuple[_LiveCopy, ...]) -> None
```

**Purpose**: Marks older duplicate copies as superseded by the newest copy. It locks and verifies rows first so it does not point rows at a survivor that changed or disappeared.

**Data flow**: It receives a group and cluster, treats the first copy as the head, verifies all cluster rows are still live with the same bodies, updates donor rows to superseded_by the head id, and raises if the locked donor count changes unexpectedly.

**Call relations**: MemoryDeduper._dedup_group calls this for each duplicate cluster. It uses _live_group to apply the same live-row definition during verification and update.

*Call graph*: calls 1 internal fn (_live_group); called by 1 (_dedup_group); 2 external calls (select, update).


##### `MemoryDeduper._live_group`  (lines 942–951)

```
def _live_group(self, group: _Group) -> tuple[ColumnElement[bool], ...]
```

**Purpose**: Builds the shared database filters for rows that belong to a deduplication group and are old enough to touch.

**Data flow**: It receives a _Group and returns SQL conditions for workspace, subject, item class, tool-written rows, live status, and minimum age.

**Call relations**: MemoryDeduper._live_copies and _collapse both use this so they read and update the same population.

*Call graph*: called by 2 (_collapse, _live_copies); 1 external calls (now).


##### `_rewrite_in_place`  (lines 972–1022)

```
async def _rewrite_in_place(connection: AsyncConnection, workspace_id: UUID, standing: _Standing, paragraph: str, confidence: int) -> None
```

**Purpose**: Writes a new standing paragraph and supersedes the previous live paragraph at the same place. This keeps section and overview prose from accumulating like a changelog.

**Data flow**: It receives an open database connection, workspace id, paragraph identity, text, and confidence; locks current live matching paragraphs, inserts a new row, and updates old rows to point to the new one.

**Call relations**: SectionWriter.run and OverviewWriter.run call this after their model paragraph is ready. It centralizes the “exactly one live paragraph here” rule.

*Call graph*: called by 2 (run, run); 5 external calls (execute, insert, select, update, uuid4).


##### `_retire_standing`  (lines 1025–1053)

```
async def _retire_standing(connection: AsyncConnection, workspace_id: UUID, standing: _Standing) -> None
```

**Purpose**: Removes a standing section or overview paragraph when its underlying facts no longer meet the threshold for having one. It prevents old prose from floating above too few or no supporting rows.

**Data flow**: It receives an open connection, workspace id, and paragraph identity, then updates matching live rows with retired_at and clears embedding fields so they leave the search index.

**Call relations**: SectionWriter.run uses this for bands that no longer qualify. OverviewWriter.run uses it when the shared page no longer has enough facts.

*Call graph*: called by 2 (run, run); 2 external calls (execute, update).


##### `SectionWriter.run`  (lines 1079–1102)

```
async def run(self) -> None
```

**Purpose**: Rewrites the paragraph above each fact band that has enough live facts, and retires paragraphs for bands that no longer qualify.

**Data flow**: It exits if no model exists, reads eligible sections, reads currently standing paragraphs, retires stale standing paragraphs, gathers facts for each eligible section, asks the model for a paragraph, and writes it in place with the highest source confidence.

**Call relations**: The scheduler calls this periodic pass. It coordinates _sections, _standing, _facts, _summarize, _retire_standing, and _rewrite_in_place.

*Call graph*: calls 6 internal fn (_facts, _sections, _standing, _summarize, _retire_standing, _rewrite_in_place).


##### `SectionWriter._sections`  (lines 1104–1137)

```
async def _sections(self) -> tuple[_Standing, ...]
```

**Purpose**: Finds subject-and-kind bands that have enough live facts to deserve a summary paragraph. It also checks that each memory kind has a real heading for that subject.

**Data flow**: It queries servable live fact rows, groups them by subject and memory kind, filters groups by minimum count, validates headings through section_headings, and returns _Standing identities for section paragraphs.

**Call relations**: SectionWriter.run calls this first to know which bands should be written this pass.

*Call graph*: calls 2 internal fn (member_servable, section_headings); called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._standing`  (lines 1139–1157)

```
async def _standing(self) -> tuple[_Standing, ...]
```

**Purpose**: Finds section paragraphs that are currently live, whether or not their underlying facts still qualify. This lets the writer retire paragraphs that have become stale.

**Data flow**: It queries live, unsuperseded SECTION rows for the workspace, groups them by subject and memory kind, and returns their _Standing identities.

**Call relations**: SectionWriter.run compares this with _sections to find paragraphs that should be retired.

*Call graph*: called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._facts`  (lines 1159–1180)

```
async def _facts(self, section: _Standing) -> tuple[_SectionFact, ...]
```

**Purpose**: Reads the live facts under one section band, newest first. These are the facts the model will summarize for that band.

**Data flow**: It receives a _Standing section identity, queries servable live fact rows for that subject and memory kind, caps the count, and returns body/confidence pairs.

**Call relations**: SectionWriter.run calls this for each eligible section before asking _summarize to write the paragraph.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 2 external calls (__init__, select).


##### `SectionWriter._summarize`  (lines 1182–1201)

```
async def _summarize(self, model: ModelAccess, section: _Standing, facts: tuple[_SectionFact, ...]) -> str
```

**Purpose**: Asks the model to write a short paragraph for one section band. The section heading is included so the model writes for the right part of the page.

**Data flow**: It receives a model, section identity, and facts, builds a capped JSON payload with heading and fact bodies, sends a completion request, strips and trims the result, and returns the paragraph.

**Call relations**: SectionWriter.run calls this before opening the write transaction that stores the paragraph through _rewrite_in_place.

*Call graph*: calls 3 internal fn (complete, _to_overview_budget, section_headings); called by 1 (run); 3 external calls (__init__, __init__, dumps).


##### `OverviewWriter.run`  (lines 1225–1246)

```
async def run(self) -> None
```

**Purpose**: Rewrites the shared workspace overview paragraph, or retires it if there are too few live facts. This is the top-level summary of where the workspace stands.

**Data flow**: It exits if no model exists, reads shared facts, retires the overview if below the threshold, otherwise reads the workspace domain, asks the model to write the overview, and rewrites the standing overview row.

**Call relations**: The scheduler calls this periodic pass. It uses _facts and _write for the read-and-model phase, then _rewrite_in_place or _retire_standing for the database change.

*Call graph*: calls 4 internal fn (_facts, _write, _retire_standing, _rewrite_in_place); 2 external calls (__init__, workspace_domain).


##### `OverviewWriter._facts`  (lines 1248–1269)

```
async def _facts(self) -> tuple[_SectionFact, ...]
```

**Purpose**: Reads the newest live facts for the shared workspace subject across all bands. These are the inputs for the opening overview paragraph.

**Data flow**: It queries servable, live, unsuperseded shared FACT rows, orders newest first, caps the list, and returns body/confidence pairs.

**Call relations**: OverviewWriter.run calls this before deciding whether to retire or rewrite the overview.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 2 external calls (__init__, select).


##### `OverviewWriter._write`  (lines 1271–1291)

```
async def _write(self, model: ModelAccess, domain: str | None, facts: tuple[_SectionFact, ...]) -> str
```

**Purpose**: Asks the model to write the shared workspace overview. It includes the workspace domain when available so the prose can name the company context.

**Data flow**: It receives a model, optional domain, and facts, builds a capped JSON payload, sends a completion request, strips the answer, trims it to budget, and returns the paragraph.

**Call relations**: OverviewWriter.run calls this before storing the result with _rewrite_in_place.

*Call graph*: calls 2 internal fn (complete, _to_overview_budget); called by 1 (run); 3 external calls (__init__, __init__, dumps).


##### `WrittenProfile.within_role_budget`  (lines 1328–1329)

```
def within_role_budget(cls, role: str) -> str
```

**Purpose**: Keeps a model-written role phrase short. This prevents a role from turning into a long sentence in the People band.

**Data flow**: It receives role text, clips it to the role character limit at a word boundary, and returns the clipped phrase.

**Call relations**: Pydantic calls this while ProfileWriter._write validates each WrittenProfile from the model’s tool output.

*Call graph*: 1 external calls (clip_to_word).


##### `WrittenProfile.within_row_budget`  (lines 1333–1334)

```
def within_row_budget(cls, focus: str) -> str
```

**Purpose**: Keeps a model-written focus sentence within the normal memory row budget. It cuts cleanly at a word boundary.

**Data flow**: It receives focus text, clips it to the memory body limit, and returns safe text.

**Call relations**: Pydantic calls this while ProfileWriter._write validates each profile entry.

*Call graph*: 1 external calls (clip_to_word).


##### `ProfileWriter.run`  (lines 1372–1387)

```
async def run(self) -> None
```

**Purpose**: Writes the People profile entries for workspace members: one role and one current focus per roster member. It skips all work if no model is configured.

**Data flow**: It reads the roster, reads shared workspace facts, asks the model for people entries, matches returned names to roster members exactly, and stores the matched entries.

**Call relations**: The scheduler calls this periodic pass. It coordinates _roster, _facts, _write, and _store.

*Call graph*: calls 4 internal fn (_facts, _roster, _store, _write).


##### `ProfileWriter._roster`  (lines 1389–1408)

```
async def _roster(self) -> tuple[_Rostered, ...]
```

**Purpose**: Reads the workspace membership list and converts it into the small roster format sent to the model. It includes each member’s email and standing, such as admin/member and seated/unseated.

**Data flow**: It opens a transaction, asks Seats for a snapshot, takes up to the roster limit, and returns _Rostered entries in roster order.

**Call relations**: ProfileWriter.run calls this before model writing so model output can be matched back to real member ids.

*Call graph*: called by 1 (run); 2 external calls (__init__, __init__).


##### `ProfileWriter._facts`  (lines 1410–1427)

```
async def _facts(self) -> tuple[str, ...]
```

**Purpose**: Reads the shared facts that everyone in the workspace can already see. Profiles are based only on shared facts so private member-specific memory is not leaked.

**Data flow**: It queries servable, live, unsuperseded shared FACT rows, orders newest first, caps the result, and returns fact bodies.

**Call relations**: ProfileWriter.run sends these facts to _write along with the roster.

*Call graph*: calls 1 internal fn (member_servable); called by 1 (run); 1 external calls (select).


##### `ProfileWriter._write`  (lines 1429–1477)

```
async def _write(self, model: ModelAccess, roster: tuple[_Rostered, ...], facts: tuple[str, ...]) -> tuple[WrittenProfile, ...]
```

**Purpose**: Asks the model to write people profile entries through a structured tool call. Invalid entries are dropped without losing the rest.

**Data flow**: It receives a model, roster, and facts, builds a capped JSON payload, forces a write_people tool call, reads the people list from tool arguments, validates each entry as WrittenProfile, and returns the valid profiles.

**Call relations**: ProfileWriter.run calls this before matching names and storing rows. It raises if the model does not make the expected tool call.

*Call graph*: calls 1 internal fn (turn); called by 1 (run); 4 external calls (__init__, __init__, __init__, dumps).


##### `ProfileWriter._store`  (lines 1479–1508)

```
async def _store(self, entries: tuple[tuple[UUID, WrittenProfile], ...]) -> None
```

**Purpose**: Stores profile entries into the memory_profile table, replacing the previous entry for each member. This is an upsert: insert if missing, update if already present.

**Data flow**: It receives member/profile pairs, records one timestamp, opens a transaction, chooses the database-specific insert helper, and writes each row with conflict update on workspace and member.

**Call relations**: ProfileWriter.run calls this after filtering model output to roster members.

*Call graph*: called by 1 (run); 3 external calls (now, insert, insert).


##### `admitted_curation`  (lines 1562–1597)

```
def admitted_curation(bands: tuple[tuple[int, ...], ...], retire: tuple[RetiredRow, ...]) -> AdmittedCuration
```

**Purpose**: Checks which page-curation retirements are safe enough to apply. It prevents the model from removing rows without a surviving duplicate or from removing too much of one band at once.

**Data flow**: It receives the row indexes sent by band and the model’s requested retirements, drops unknown ids, requires each retired row to point to a surviving duplicate, enforces the per-band retirement cap, and returns either admitted indexes or a refusal reason.

**Call relations**: PagePass._retiring calls this before any database write. If it refuses the page, PagePass logs a warning and retires nothing for that subject.

*Call graph*: called by 1 (_retiring); 1 external calls (__init__).


##### `PagePass.run`  (lines 1645–1658)

```
async def run(self) -> None
```

**Purpose**: Runs whole-page curation for every subject with enough facts. It uses the model to spot repeated page-derived rows and retires only the admitted ones.

**Data flow**: It exits if no model exists, reads eligible subjects, builds each subject’s page bands, asks the model to curate the page, converts admitted row indexes to database ids, and applies retirements when any survive.

**Call relations**: The scheduler calls this periodic pass. It coordinates _subjects, _page, _curate, _retiring, and _apply.

*Call graph*: calls 5 internal fn (_apply, _curate, _page, _retiring, _subjects).


##### `PagePass._subjects`  (lines 1660–1676)

```
async def _subjects(self) -> tuple[str, ...]
```

**Purpose**: Finds subjects whose pages are large enough to be worth whole-page curation. Small pages are skipped because a reader can already scan them easily.

**Data flow**: It queries live, unsuperseded FACT rows by subject, keeps subjects meeting the minimum row count, orders them, and returns subject strings.

**Call relations**: PagePass.run calls this to decide which pages to inspect.

*Call graph*: called by 1 (run); 1 external calls (select).


##### `PagePass._page`  (lines 1678–1724)

```
async def _page(self, subject: str) -> tuple[_Band, ...]
```

**Purpose**: Builds the model payload view of one subject’s page: section headings, current section summary, and page-derived fact rows. It assigns short numeric indexes so the model can refer to rows cheaply.

**Data flow**: It receives a subject, loops through that subject’s headings, reads live page-derived fact rows for each memory kind using live_page_link, reads the current section paragraph if any, assigns sequential indexes, and returns _Band objects.

**Call relations**: PagePass.run calls this before _curate. It uses section_headings to mirror the page layout and live_page_link to avoid touching stale or tool-written rows.

*Call graph*: calls 2 internal fn (live_page_link, section_headings); called by 1 (run); 3 external calls (__init__, __init__, select).


##### `PagePass._curate`  (lines 1726–1775)

```
async def _curate(self, model: ModelAccess, bands: tuple[_Band, ...]) -> CuratedPage
```

**Purpose**: Asks the model to read the whole page and name rows that should be retired as duplicates of surviving rows. The reply must come through a structured tool call.

**Data flow**: It receives a model and page bands, builds a capped JSON payload of sections, summaries, and numbered rows, forces a curate_page tool call, validates each requested retirement as RetiredRow, and returns a CuratedPage object.

**Call relations**: PagePass.run calls this after building a page. Its output is only a recommendation until _retiring checks it with admitted_curation.

*Call graph*: calls 1 internal fn (turn); called by 1 (run); 5 external calls (__init__, __init__, __init__, __init__, dumps).


##### `PagePass._retiring`  (lines 1777–1788)

```
def _retiring(self, subject: str, bands: tuple[_Band, ...], retire: tuple[RetiredRow, ...]) -> frozenset[UUID]
```

**Purpose**: Turns model-requested row indexes into actual memory row ids, but only after safety admission. It logs and skips the whole page if the request is too risky.

**Data flow**: It receives a subject, bands, and requested retirements, calls admitted_curation, warns and returns an empty set on refusal, otherwise maps admitted row indexes to their stored UUIDs.

**Call relations**: PagePass.run calls this between _curate and _apply, making it the safety gate before destructive database updates.

*Call graph*: calls 1 internal fn (admitted_curation); called by 1 (run); 1 external calls (warn).


##### `PagePass._apply`  (lines 1790–1806)

```
async def _apply(self, retiring: frozenset[UUID]) -> None
```

**Purpose**: Retires selected memory rows and removes their embedding claim so the search index can drop them. It only touches rows that are still live.

**Data flow**: It receives a set of memory row ids, opens a transaction, updates matching rows in the workspace with retired_at, clears embedding fields, and refreshes updated_at.

**Call relations**: PagePass.run calls this after _retiring returns stored ids. This is the only database mutation PagePass performs.

*Call graph*: called by 1 (run); 1 external calls (update).


### `extensions/sites/ufo_ext_sites/main_homepage.py`

`domain_logic` · `scheduled background sweep`

This file fixes a subtle transition problem. Older setup code could attach a hosted page as the homepage for every agent, including the workspace’s main agent. Later, when the chat app takes over that main agent, the chat screen is supposed to come from the app’s own bundled files, not from a database row. But the homepage lookup checks the bound database row first, so the old seeded page can hide the chat screen.

The file defines a scheduled sweep called `release_main_homepage`. Think of it like a janitor that walks through workspaces every few minutes and removes only the stale sign taped over the real front door. It looks for workspaces where the main agent is now the declared chat agent, where a hosted site is still bound as that agent’s homepage, and where this release has not already been recorded.

When it finds such a workspace, it releases the binding. Releasing does not delete the page; it just stops that page from being forced as the main agent’s homepage. The file is careful about visibility: once the page is no longer protected by the agent binding, it chooses the narrower of the page’s own visibility and the agent’s visibility, so the cleanup does not accidentally show the page to more people. Finally, it writes a marker so future sweeps do not undo a member’s later choice to set a new homepage.

#### Function details

##### `_the_chat_main_agent`  (lines 67–75)

```
def _the_chat_main_agent() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for the exact agent this cleanup cares about: the workspace’s main agent after it has been provisioned as the chat app. This prevents the sweep from touching ordinary agents or a main agent that has not yet been adopted by chat.

**Data flow**: It reads no live rows by itself. It creates a reusable database condition that says: the agent is marked as main, was provisioned by the chat extension, and has the declared chat name. That condition is returned for other queries to use.

**Call relations**: The candidate search uses this condition to find workspaces with a bound homepage on the chat-owned main agent. The release job uses the same condition when checking the current workspace, so both the broad search and the actual cleanup agree on which agent is safe to touch.

*Call graph*: called by 2 (release_main_homepage, with_a_bound_main_homepage); 1 external calls (and_).


##### `unreleased_main_homepage_workspaces`  (lines 78–97)

```
def unreleased_main_homepage_workspaces(extension: str) -> WorkspaceCandidates
```

**Purpose**: Declares which workspaces should be offered to the scheduled cleanup job. It looks for workspaces that still have a homepage bound to the chat-owned main agent and have not yet been marked as released.

**Data flow**: It receives the extension name used for the release marker. It builds a workspace-candidate query around an inner database query that finds matching workspace IDs. It returns a `WorkspaceCandidates` object, which is the job system’s way of saying, “run this job for these workspaces.”

**Call relations**: The job scheduler calls this kind of candidate provider before running workspace jobs. This function hands the job system a query through `owner_candidates`, so the scheduler can run `release_main_homepage` only where there is likely cleanup work to do.

*Call graph*: 1 external calls (owner_candidates).


##### `unreleased_main_homepage_workspaces.with_a_bound_main_homepage`  (lines 84–95)

```
def with_a_bound_main_homepage() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the actual database query that finds workspaces with stale main-agent homepage bindings. It excludes workspaces that already have the release marker.

**Data flow**: It uses the hosted-site table, the agent table, and the extension-store marker table. It joins hosted sites to agents, keeps only rows where the homepage is bound to the chat-owned main agent, filters out rows with the release marker, groups by workspace, and produces workspace IDs.

**Call relations**: This is the detailed search used inside `unreleased_main_homepage_workspaces`. It relies on `_the_chat_main_agent` so the candidate query matches the same definition of “chat main agent” used later by the release job.

*Call graph*: calls 1 internal fn (_the_chat_main_agent); 4 external calls (exists, literal, select, join).


##### `released_visibility`  (lines 100–104)

```
def released_visibility(site: str, agent: str) -> Visibility
```

**Purpose**: Chooses the safe visibility level for a page after it is no longer bound to the main agent. It prevents the cleanup from accidentally making the page visible to a wider audience.

**Data flow**: It receives two visibility labels: one from the hosted site row and one from the agent. It converts both into ordered visibility levels, compares them, and returns the narrower one. The result is the visibility that should be written back when the homepage binding is released.

**Call relations**: `release_main_homepage` calls this right before releasing a bound homepage. The release job hands the chosen visibility to the hosted-sites store so the page resumes life as a normal page without becoming more public than it was.

*Call graph*: called by 1 (release_main_homepage); 1 external calls (visibility_level).


##### `release_main_homepage`  (lines 107–130)

```
async def release_main_homepage(ctx: ExtensionContext) -> None
```

**Purpose**: Performs the cleanup for one workspace. It finds the workspace’s chat-owned main agent, releases any hosted page bound as that agent’s homepage, and writes a marker so the release is not repeated.

**Data flow**: It receives an extension context, which gives it the current workspace, database transactions, and the extension key-value store. First it queries for the current workspace’s chat-owned main agent. If there is none, it stops. If there is one, it asks the hosted-sites store whether that agent has a bound homepage. If a bound page exists, it releases the binding using the safe visibility from `released_visibility`. Finally, it stores a release marker containing the agent ID.

**Call relations**: This is the function the scheduled job ultimately runs for each candidate workspace. It uses `_the_chat_main_agent` to identify the correct agent, `HostedSites` to inspect and release the homepage binding, and `released_visibility` to keep access safe. If a crash happens after the release but before the marker is written, the next sweep can run again harmlessly: it will find no bound homepage and then write the marker.

*Call graph*: calls 3 internal fn (transaction, _the_chat_main_agent, released_visibility); 2 external calls (__init__, select).


### Monitors and report digests
Monitor and digest extensions poll user-defined watches and summarize newly published scheduled reports into skim-friendly entries.

### `extensions/monitors/ufo_ext_monitors/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like putting a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Here, the drawer is `extensions/monitors/ufo_ext_monitors`, which likely contains monitor-related extension code elsewhere in the project. Because this file is empty, importing the package does not automatically set up monitors, load settings, or run any code. Its value is structural: it makes imports predictable and gives the project a clear place where package-level setup could be added later if needed. Without it, some Python environments or tooling might not recognize this directory as a regular package, which could make imports or extension discovery fail.


### `extensions/monitors/ufo_ext_monitors/monitor_runner.py`

`orchestration` · `recurring scheduled monitor tick`

A monitor is like a smoke alarm for a command: run the command now and then, compare the result with what was expected, and alert the agent if something changes, keeps failing, or reaches a deadline. This file is the clock-driven runner for those monitors. It claims due monitors under a short lease, which means two overlapping runner jobs should not test the same monitor at the same time. For each claimed monitor, it first checks the deadline. If the deadline has passed, it fires immediately. Otherwise it runs the monitor’s command through the probe system, using the same member authority that created the monitor, so private connections are only available while that member still has access. If the command cannot run because that authority or terminal is unavailable, the tick is counted as skipped and rescheduled. If the probe fails, repeated failures are counted, and the third failure fires the monitor. If the probe succeeds, its output is compared to the saved baseline. Same output means a quiet tick; changed output fires the monitor. When a monitor fires, this file posts a structured message back to the agent, then retires the monitor so it will not fire again. It deliberately invokes first and retires second, so a crash can safely retry the same fire using an idempotency key rather than losing the alert.

#### Function details

##### `MonitorRunner.run`  (lines 54–64)

```
async def run(self) -> None
```

**Purpose**: This is the top-level pass of the monitor runner. It finds monitors that are due right now, asks each one to take a single tick, and reports if any of those ticks failed unexpectedly.

**Data flow**: It starts with the extension context stored on the runner. From that it creates a monitor store, reads the current time, and asks the store for due monitors it can claim under a temporary lease. Each claimed monitor row is passed into the per-monitor tick function. If a tick raises an unexpected error, the monitor name and error type are collected; after all claimed monitors have been tried, those failures are turned into one runtime error.

**Call relations**: This function is the scheduled entry into the file’s work. It calls `MonitorRunner._tick` once for each claimed monitor, so `_tick` contains the decision-making for a single monitor while `run` coordinates the whole sweep.

*Call graph*: calls 1 internal fn (_tick); 2 external calls (__init__, now).


##### `MonitorRunner._tick`  (lines 66–108)

```
async def _tick(self, store: MonitorStore, row: Monitor) -> None
```

**Purpose**: This function performs one monitor check. It decides whether the monitor should fire, be counted as quiet, be counted as failed, or be skipped and tried later.

**Data flow**: It receives the monitor store and one claimed monitor row. It calculates the monitor’s spacing from its interval, checks whether its deadline has already passed, and, if not, runs the saved command through the probe capability using the creator’s authority. If access is unavailable, it records a skipped tick and sets the next probe time. If the command exits with an error, it either records another failure or fires once the failure threshold is reached. If the command succeeds, it caps the output for safe posting, compares it to the baseline, records a quiet tick if unchanged, or fires if changed. The store is updated with the new state or the monitor is handed off to `_fire`.

**Call relations**: `MonitorRunner.run` calls this for each monitor it has claimed. When this function decides an alert is needed, it calls `MonitorRunner._fire`; otherwise it records the result through the monitor store’s quiet, failed, or skipped tick methods.

*Call graph*: calls 4 internal fn (_fire, failed_tick, quiet_tick, skipped_tick); called by 1 (run); 5 external calls (now, timedelta, authority_from_member_id, capped, stderr_tail).


##### `MonitorRunner._fire`  (lines 110–132)

```
async def _fire(self, store: MonitorStore, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> None
```

**Purpose**: This function sends the actual monitor alert to the agent and retires the monitor afterward. It makes sure only one runner gets to perform the final fire for that monitor.

**Data flow**: It receives the store, the monitor row, the reason for firing, the message payload, any full output that must be stored separately, and the probe count. First it asks the store to claim the monitor’s final hold; if that fails, another runner already has it and this function exits. If it succeeds, it builds the message body, invokes the agent in the original conversation using the monitor creator’s authority, and uses a stable idempotency key so a retry does not create duplicate work. If the agent has been archived, it stops without retiring. Otherwise it retires the monitor in the store.

**Call relations**: `MonitorRunner._tick` calls this when a deadline, changed output, or repeated failure means the monitor must alert the agent. `_fire` calls `MonitorRunner._body` to create the message the agent will read, then uses the extension context to invoke the agent, and finally asks the store to retire the monitor.

*Call graph*: calls 3 internal fn (_body, claim_holds, retire); called by 1 (_tick); 1 external calls (authority_from_member_id).


##### `MonitorRunner._body`  (lines 134–153)

```
async def _body(self, row: Monitor, cause: str, payload: str, spill: str | None, probes_run: int) -> str
```

**Purpose**: This function builds the text that the agent sees when a monitor fires. It includes the monitor’s reason, suggested next steps, metadata, counters, and any probe output in a form that is safe to read as data rather than instructions.

**Data flow**: It receives the monitor row, the fire cause, a short payload, optional full output, and the number of probes run. It assembles a structured block of text with the monitor name, cause, saved explanation, next steps, metadata, and counters. If there is full output too large for the message, it stores that output through `_spilled` and includes the returned file path. It escapes the closing marker so command output cannot pretend to end the monitor block, then appends the probe payload through `wall`, a safety wrapper for untrusted command output. The result is a complete string ready to send to the agent.

**Call relations**: `MonitorRunner._fire` calls this just before invoking the agent. If the probe output was too large to fit directly, `_body` calls `MonitorRunner._spilled` so the message can point the agent to a saved file instead of losing the full output.

*Call graph*: calls 1 internal fn (_spilled); called by 1 (_fire); 2 external calls (dumps, wall).


##### `MonitorRunner._spilled`  (lines 155–163)

```
async def _spilled(self, row: Monitor, output: str) -> str
```

**Purpose**: This function saves oversized probe output into the conversation’s workspace and returns the path to that saved file. It lets the alert message stay readable while preserving the complete output for the agent.

**Data flow**: It receives the monitor row and the full command output. It checks that the extension has access to conversation files; if not, it raises an error because there is nowhere to put the oversized output. It creates a timestamped filename using the monitor name, writes the output bytes under the monitor spill directory for that conversation, and returns the path produced by the file system service.

**Call relations**: `MonitorRunner._body` calls this only when the capped alert text is not enough and the full probe output needs to be stored separately. The returned path is inserted into the fire message that `_fire` sends to the agent.

*Call graph*: called by 1 (_body); 1 external calls (now).


### `extensions/report_digest/ufo_ext_report_digest/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. That matters because other parts of the project may need to load code from `ufo_ext_report_digest`, and Python uses this file as the package’s front door. Think of it like a blank sign on an office door: it does not do the work inside the office, but it makes the office recognizable and reachable. Because the file contains no code, it does not set up settings, create objects, register features, or run any startup logic. Its value is structural: without it, depending on the Python version and packaging setup, imports for this extension could fail or behave differently.


### `extensions/report_digest/ufo_ext_report_digest/digest.py`

`domain_logic` · `digest creation and validation`

A report can be long, but the digest is meant to be the smallest useful teaser: enough to help a reader decide whether to open the full report. This file sets that contract. It says what a digest entry contains, how long each part may be, and how to remove repeated information so the reader does not waste rows seeing the same point twice.

The main data shapes are `DigestEntry`, which represents one report’s digest, and `DigestPoint`, which represents one short finding line plus an optional actor, meaning who or what the report says caused it. They use Pydantic, a library that checks and cleans data when an object is created. Instead of rejecting a digest because one field is too long, the file trims text at word boundaries. That keeps the entry usable while avoiding ugly cut-off words.

The file also has a simple novelty check. It turns text into meaningful word roots, ignores common filler words like “the” and “and,” and asks whether a summary or point adds enough new information beyond what was already said. Like editing a noticeboard, it keeps the strongest distinct lines and removes duplicates.

Finally, it can load the digest-writing standard from prompt and skill files on disk, combining those instructions with a delivery register block so different writers follow the same rules.

#### Function details

##### `_clipped`  (lines 96–107)

```
def _clipped(value: str, ceiling: int) -> str
```

**Purpose**: Shortens a piece of prose to a maximum length without cutting through the middle of a word. It exists so an overlong title, summary, point, or actor name does not make the whole digest fail.

**Data flow**: It receives a text value and a character limit. It trims outside whitespace, checks whether the text already fits, and if not, cuts it down near the limit at the last space and removes trailing punctuation. It returns the cleaned, possibly shortened text.

**Call relations**: The field validators for `DigestEntry` and `DigestPoint` call this whenever they clean title, summary, point text, or actor text. It is the shared trimming tool that keeps all digest fields within their display budgets.

*Call graph*: called by 4 (_summary, _title, _actor, _text).


##### `_stem`  (lines 110–117)

```
def _stem(word: str) -> str
```

**Purpose**: Reduces a word to a short root-like form so related words can be compared more easily. For example, this helps treat words with common endings as similar when checking whether a line repeats earlier information.

**Data flow**: It receives one word. It focuses on the part after the last hyphen when that part is long enough, removes a known suffix such as “ing” or “tion” when safe, then returns only the first few characters of the resulting root. The output is a compact comparison key, not a word meant for readers.

**Call relations**: `_content` calls this for every meaningful word it finds. It is a small helper inside the duplicate-detection path.

*Call graph*: called by 1 (_content).


##### `_content`  (lines 120–121)

```
def _content(text: str) -> tuple[str, ...]
```

**Purpose**: Extracts the meaningful comparison words from a sentence or line of text. It removes common stopwords, meaning everyday filler words such as “the,” “and,” or “of,” then normalizes the remaining words.

**Data flow**: It receives text. It lowercases it, finds word-like pieces, skips stopwords, passes each remaining word through `_stem`, and returns the resulting sequence of word roots. The original prose is not changed.

**Call relations**: `_adds_to` uses this to measure whether a line is new enough, and `DigestEntry._said_once` uses it to remember what the title, summary, and kept points have already said.

*Call graph*: calls 1 internal fn (_stem); called by 2 (_said_once, _adds_to).


##### `_adds_to`  (lines 124–126)

```
def _adds_to(text: str, said: set[str]) -> bool
```

**Purpose**: Decides whether a new line adds enough fresh information to be worth showing. It prevents the digest from spending space on a summary or point that mostly repeats the title or an earlier point.

**Data flow**: It receives a text line and a set of word roots that have already appeared. It extracts the line’s meaningful roots with `_content`, counts how many are new, and compares that share with the required novelty threshold. It returns true only when the line has content and enough of it is new.

**Call relations**: `DigestEntry._said_once` calls this while reading the entry from top to bottom. It acts like a gatekeeper, letting through only summaries and points that add value for the reader.

*Call graph*: calls 1 internal fn (_content); called by 1 (_said_once).


##### `DigestPoint._text`  (lines 139–140)

```
def _text(cls, value: str) -> str
```

**Purpose**: Cleans the text of one digest point so it fits the allowed length. This keeps each finding short enough to work as a single digest line.

**Data flow**: When a `DigestPoint` is created, it receives the proposed point text. It passes that text to `_clipped` with the point-length limit and stores the shortened result in the model.

**Call relations**: Pydantic calls this validator during `DigestPoint` creation. It relies on `_clipped` for the actual trimming, so point text follows the same word-boundary rule as other prose fields.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestPoint._actor`  (lines 144–145)

```
def _actor(cls, value: str) -> str
```

**Purpose**: Cleans the actor field of one digest point so the named person, group, or system stays within the allowed length. This avoids a long actor name crowding out the finding itself.

**Data flow**: When a `DigestPoint` is created, it receives the proposed actor string. It sends that string to `_clipped` with the actor-length limit and stores the resulting shortened actor text.

**Call relations**: Pydantic calls this validator during `DigestPoint` creation. It uses the same trimming helper as the point text validator, keeping field length rules consistent.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._decoded`  (lines 161–167)

```
def _decoded(cls, value: object) -> object
```

**Purpose**: Accepts point data even when a provider returns it as a JSON string instead of as a normal list. This makes the digest parser more tolerant of a common language-model output mistake.

**Data flow**: Before the `points` field is fully validated, it receives the raw value. If the value is a string, it parses it as JSON. If the parsed value is a dictionary with a `points` key, it uses that nested value; otherwise it uses the parsed value itself. Non-string values pass through unchanged.

**Call relations**: Pydantic calls this before validating `DigestEntry.points`. It hands cleaned point data onward to Pydantic so the points can then be turned into `DigestPoint` objects.

*Call graph*: 1 external calls (loads).


##### `DigestEntry._title`  (lines 171–172)

```
def _title(cls, value: str) -> str
```

**Purpose**: Cleans the digest title and keeps only the first finding if the title tries to pack in more than one. The digest standard wants the title to carry the top-ranked finding, not a mini-list.

**Data flow**: When a `DigestEntry` is created, it receives the proposed title. It splits the title at the first semicolon, keeps the part before it, trims that part to the title length limit with `_clipped`, and stores the result.

**Call relations**: Pydantic calls this validator during `DigestEntry` creation. It uses `_clipped` for length control and prepares the title before later checks compare the rest of the entry against it.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._summary`  (lines 176–177)

```
def _summary(cls, value: str) -> str
```

**Purpose**: Cleans the digest summary so it stays short enough to act as one compact clause. It keeps the digest from becoming a second report.

**Data flow**: When a `DigestEntry` is created, it receives the proposed summary. It passes that summary to `_clipped` with the summary-length limit and stores the shortened result.

**Call relations**: Pydantic calls this validator during `DigestEntry` creation. The cleaned summary is later examined by `DigestEntry._said_once` to see whether it adds new information beyond the title.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._titled`  (lines 180–183)

```
def _titled(self) -> 'DigestEntry'
```

**Purpose**: Enforces the rule that a digest entry claiming to contain a real change must have a title. Without a title, the digest would say there is something important but give the reader no usable hook.

**Data flow**: After a `DigestEntry` is assembled, it looks at `holds_a_change` and `title`. If the entry says it holds a change but the title is empty, it raises an error. Otherwise it returns the entry unchanged.

**Call relations**: Pydantic calls this after the individual fields have been validated. It is a final consistency check before the entry is accepted.


##### `DigestEntry._said_once`  (lines 186–205)

```
def _said_once(self) -> 'DigestEntry'
```

**Purpose**: Removes summary and point lines that repeat what the reader has already learned from earlier lines. It keeps the digest compact by preserving only distinct information, with at most two useful points.

**Data flow**: After a `DigestEntry` is assembled, it starts with the meaningful word roots from the title. It tests the summary against those roots; if the summary does not add enough new content, it clears it. Then it walks through the points in order, keeping only points that add enough new content, updating the remembered words as it goes, and stopping after the maximum number of points. It returns the cleaned entry.

**Call relations**: Pydantic calls this after field validation. It uses `_content` to remember what has already been said and `_adds_to` to judge each later line. This is the main quality-control step that makes one digest entry read like a concise set of non-overlapping findings.

*Call graph*: calls 2 internal fn (_adds_to, _content).


##### `bounded`  (lines 208–210)

```
def bounded(report: str) -> str
```

**Purpose**: Cuts a full report down to the maximum amount the digest writer is allowed to read. This limits cost and keeps the digest based on the report’s front section, where the findings are expected to appear.

**Data flow**: It receives the report text. It returns only the first fixed number of characters and does not otherwise inspect or rewrite the report.

**Call relations**: No in-file caller is listed, so this is meant to be used by outside digest-writing code before sending a report to the writer. It prepares the report input before a digest entry is generated.


##### `writing_standard`  (lines 213–227)

```
def writing_standard() -> str
```

**Purpose**: Builds the instruction text that tells the digest writer how to write entries. It combines the local digest prompt, the shared delivery rules, and the digest skill’s own standard into one block.

**Data flow**: It reads the skill document from disk, removes its frontmatter, reads the digest prompt file, and takes the delivery register block imported from the SDK. It joins those pieces with blank lines and returns the final instruction string.

**Call relations**: No in-file caller is listed, so outside code imports this when it needs to prepare a digest-writing job. It hands back the exact standard that should guide the model or agent producing `DigestEntry` data.


### `extensions/report_digest/ufo_ext_report_digest/writer.py`

`domain_logic` · `scheduled digest writing and rebuild maintenance`

Scheduled apps can publish markdown reports, but a feed full of full-length reports is hard to scan. This file is the writer for a report digest: it finds recent scheduled reports that have not yet been summarized, reads the report text, asks a language model to write a structured digest entry, and stores the result in the database.

It is careful about cost and fairness. Each run only processes a small batch, like taking eight papers from the top of an inbox instead of emptying the whole filing cabinet at once. It only looks back seven days, so a broken or missing report cannot block the job forever. It also reads only a limited number of bytes from each report file, so a huge file cannot overwhelm the worker.

The main flow is in `DigestWriter`. It finds eligible reports, reads each report body from blob storage, builds a plain description of who the reader is, asks the model to produce a `DigestEntry`, and then saves either a digest row or a small “unchanged” marker. If one report fails, the writer skips it and continues with the rest of the batch.

`DigestRebuild` supports rebuilding recent digest entries by deleting the writer’s own rows inside the same time window. The original reports remain stored, so the normal writer can create fresh entries later. `undigested_workspaces` builds a database query that helps find workspaces with pending digest work.

#### Function details

##### `DigestWriter.run`  (lines 117–126)

```
async def run(self) -> None
```

**Purpose**: Runs one digest-writing tick. It looks for reports that still need digest entries and tries to process each one without letting one bad report stop the rest.

**Data flow**: It starts with the writer’s context, model access, and blob store. It asks `_unwritten` for a small list of eligible reports, then sends each report to `_digest`. If `_digest` raises an error for one report, this method catches it and moves on, leaving that report for a future tick while still processing later reports.

**Call relations**: This is the top-level method for `DigestWriter`. It first calls `DigestWriter._unwritten` to discover work, then calls `DigestWriter._digest` for each report it found.

*Call graph*: calls 2 internal fn (_digest, _unwritten).


##### `DigestWriter._digest`  (lines 128–139)

```
async def _digest(self, report: Report) -> None
```

**Purpose**: Processes one report from start to finish. It reads the report, asks the model for a digest entry, and stores either the digest or a marker saying the report contained no change.

**Data flow**: A `Report` goes in. The method reads its body with `_body`; if the body is missing, it stops. It builds a reader description with `_reader`, sends the body and reader to `_written`, and examines the returned `DigestEntry`. If the entry says there was a real change, it stores the digest with `_store`; otherwise it stores an unchanged marker with `_store_unchanged`.

**Call relations**: This method is called by `DigestWriter.run` once for each report in the batch. It is the central assembly line that connects blob reading, reader wording, model writing, and database storage.

*Call graph*: calls 5 internal fn (_body, _reader, _store, _store_unchanged, _written); called by 1 (run).


##### `DigestWriter._unwritten`  (lines 141–213)

```
async def _unwritten(self) -> tuple[Report, ...]
```

**Purpose**: Finds recent scheduled reports that are still waiting to be read by the digest writer. It deliberately excludes reports that already have a digest entry or were already marked as unchanged.

**Data flow**: It reads the current workspace ID from the extension context and builds a database query. The query looks for completed scheduled turns from the last seven days that published a markdown artifact, chooses the first markdown artifact for each turn, and filters out turns already recorded in the digest tables. It returns a tuple of `Report` objects with the turn ID, blob key, app name, audience, and owner email.

**Call relations**: This method is called by `DigestWriter.run` at the start of a tick. It uses database selection logic to define exactly what work is due, then hands `Report` objects back to `run` for processing.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `DigestWriter._body`  (lines 215–228)

```
async def _body(self, report: Report) -> str | None
```

**Purpose**: Reads the markdown report text from blob storage while enforcing size limits. This protects the worker and the model bill from unexpectedly huge files.

**Data flow**: It receives a `Report` with a blob key. It streams bytes from blob storage until it reaches the read limit, stops early if needed, and decodes the bytes into text. It then passes the text through `bounded`, which trims it to the amount the model should see. If the blob is missing, it returns `None` instead of raising an error.

**Call relations**: This method is called by `DigestWriter._digest` before any model work happens. It supplies the report text that `_written` will later send to the model.

*Call graph*: called by 1 (_digest); 1 external calls (bounded).


##### `DigestWriter._reader`  (lines 230–239)

```
def _reader(self, report: Report) -> str
```

**Purpose**: Creates a short plain-language description of who the digest is being written for. This helps the model aim the summary at the right audience.

**Data flow**: It receives a `Report` containing the audience, owner email, and app name. If the report belongs to a specific member conversation and an owner email is available, it describes that person as the reader. Otherwise, it describes the whole workspace as the reader. The result is a string used in the model prompt and stored with the digest entry.

**Call relations**: This method is called by `DigestWriter._digest` after the body is read. Its output is passed to `DigestWriter._written` so the model knows the intended reader, and later to `DigestWriter._store` so the saved entry records that audience.

*Call graph*: called by 1 (_digest).


##### `DigestWriter._written`  (lines 241–278)

```
async def _written(self, body: str, reader: str) -> DigestEntry | None
```

**Purpose**: Asks the language model to turn a report into a structured digest entry. It accepts only the model’s tool-formatted answer, which keeps the saved digest in a predictable shape.

**Data flow**: It receives the report body and reader description. It builds a model request with a system instruction from `writing_standard`, a compact JSON user message containing the report and reader, and a tool schema based on `DigestEntry`. The model reply comes back; if it is plain text, the method returns `None`. If the reply contains the expected tool call, it validates the tool input as a `DigestEntry` and returns that object.

**Call relations**: This method is called by `DigestWriter._digest` after the report text and reader are ready. It calls into the model layer and hands a validated digest entry back to `_digest`, which decides whether to store it as a change or mark the report unchanged.

*Call graph*: called by 1 (_digest); 7 external calls (__init__, __init__, __init__, model_json_schema, model_validate, dumps, writing_standard).


##### `DigestWriter._store_unchanged`  (lines 280–286)

```
async def _store_unchanged(self, report: Report) -> None
```

**Purpose**: Records that a report was read and found to contain no meaningful change. This prevents the same quiet report from costing another model call on every scheduled tick.

**Data flow**: It receives a `Report`. Inside a database transaction, it inserts the workspace ID and turn ID into the `report_digest_unchanged` table. It does not return a value; the lasting result is the new database row.

**Call relations**: This method is called by `DigestWriter._digest` when the model’s `DigestEntry` says the report does not hold a change. It is the final step for reports that should not appear as digest entries.

*Call graph*: called by 1 (_digest); 1 external calls (insert).


##### `DigestWriter._store`  (lines 288–301)

```
async def _store(self, report: Report, entry: DigestEntry, reader: str) -> None
```

**Purpose**: Saves a completed digest entry in the database. This is what makes the digest visible to later readers of the report feed.

**Data flow**: It receives the original `Report`, the validated `DigestEntry`, and the reader description. Inside a database transaction, it inserts the workspace ID, turn ID, title, summary, bullet points, reader, model name, and current write time into the `report_digest_entry` table. It returns nothing; the saved row is the output.

**Call relations**: This method is called by `DigestWriter._digest` when the model found a real change in the report. It is the final handoff from model output to durable database record.

*Call graph*: called by 1 (_digest); 2 external calls (now, insert).


##### `DigestRebuild.run`  (lines 321–339)

```
async def run(self) -> int
```

**Purpose**: Clears recent digest-writer records so they can be written again. This is useful when the digest standard changes and recent reports should be reprocessed under the new rules.

**Data flow**: It reads the workspace ID from the context and defines the same recent time window used by the writer. In one database transaction, it deletes matching rows from both the digest entry table and the unchanged-marker table. It returns the total number of rows deleted.

**Call relations**: This method belongs to `DigestRebuild`, a maintenance path rather than the normal write path. After it removes recent writer-owned rows, the regular `DigestWriter` flow can rediscover those reports through `_unwritten` and rebuild their digest results.

*Call graph*: 3 external calls (now, delete, select).


##### `undigested_workspaces`  (lines 342–366)

```
def undigested_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds a database query that finds workspaces with at least one recent completed scheduled markdown report still waiting for digest processing. This lets a scheduler avoid waking the writer for workspaces that have nothing to do.

**Data flow**: It takes no direct input, but builds a SQLAlchemy `Select` query using the digest entry table, the unchanged-marker table, turns, and shared artifacts. The query looks for completed scheduled markdown reports inside the time window and excludes reports already read by the writer. The output is a query object that will return workspace IDs when executed.

**Call relations**: No caller is shown in the provided function graph, but this function is shaped for a higher-level scheduler or job picker. It mirrors the writer’s own due-work rules, then hands back a query that can be executed elsewhere to decide which workspaces need a digest tick.

*Call graph*: 2 external calls (now, select).


### Scheduled conversations
The scheduled-tasks extension validates cron schedules, resumes paused conversations, and fires due scheduled tasks exactly once before advancing them.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can contain an `__init__.py` file to say, “this folder is a package,” meaning its modules can be imported using package-style names. Here, it belongs to the scheduled tasks extension, so it helps Python recognize `extensions/scheduled_tasks/ufo_ext_scheduled_tasks` as one importable unit.

There is no executable code, configuration, or data in this file. Nothing runs when it is imported beyond Python’s normal package setup. Its value is structural: without it, some tooling or older Python import behavior might not treat this directory as a regular package, which could make imports less reliable.

An everyday analogy is a blank cover page in a binder. The cover page does not contain instructions, but it tells readers and filing systems that the pages behind it belong together.


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `scheduled task creation and run scheduling`

Scheduled tasks need a way to say “run every day at 9” or “run every five minutes.” This file provides that timing language using cron, a common text format for repeating schedules. A cron expression here must have exactly five fields, such as minute, hour, day of month, month, and day of week. Keeping this logic in the extension matters because the core task store only cares about concrete dates like “next run at 10:05.” It does not need to understand cron rules itself.

The file has two jobs. First, it validates a schedule before the system accepts it, so mistakes are caught early instead of turning into confusing runtime behavior later. Second, it asks the external `croniter` library to calculate the next scheduled time after a given moment. That “after” detail is important: the next run is strictly in the future relative to the provided time. If the runner was asleep or delayed, missed schedule windows collapse into one catch-up run instead of creating a sudden pile of old runs.

An everyday analogy is a calendar reminder: the task store writes down the next exact reminder time, while this file understands the repeating rule that produces that next reminder.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: Checks whether a schedule string is a valid cron expression in the format this extension accepts. It is used to reject schedules that are malformed or not understandable before they are saved or used.

**Data flow**: It receives a text schedule. It first splits the text into space-separated parts and makes sure there are exactly five. Then it asks `croniter`, an external cron-parsing library, whether the expression is valid. If either check fails, it raises a `ValueError` with a clear message. If everything is valid, it returns the original schedule unchanged.

**Call relations**: This function relies on `croniter.croniter.is_valid` for the detailed cron rules after doing the simple five-field check itself. In the bigger scheduled-task flow, it is the gatekeeper that turns user-provided schedule text into something the rest of the extension can safely trust.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: Calculates the next time a cron schedule should run after a given datetime. Someone would use it whenever a scheduled task has just been created or has just run and needs its next exact run time.

**Data flow**: It receives a validated schedule string and a datetime called `after`. It gives both to `croniter`, which walks the repeating schedule forward from that point. The function returns the next matching datetime, leaving the inputs unchanged.

**Call relations**: This function hands the actual calendar math to `croniter.croniter`. In the broader task runner flow, it converts a repeating rule into a concrete `next_run_at` timestamp that the core store can sort and poll without needing to understand cron syntax.

*Call graph*: 1 external calls (croniter).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pause_runner.py`

`orchestration` · `recurring scheduled task tick`

A “pause” here is a saved instruction to continue a conversation later, like setting a kitchen timer before coming back to a task. This file is the timer worker. On each scheduled tick, it asks the pause storage for pauses whose time has arrived and claims them with a short lease, meaning another overlapping worker should not pick up the same pause at the same time.

For each claimed pause, it tries to fire the saved prompt back into the conversation as a scheduled turn. It does this using the authority of the member who originally created the pause, so the resumed work is attributed correctly. It also sends two safety checks, called watermarks: recorded positions in the conversation from when the pause began. These let the conversation lock reject the scheduled turn if a member has spoken since then. In plain terms: if a human already answered, the timer does not answer too.

After the invoke attempt, the pause is retired when the wait is over, whether the timer produced a turn or the member already did. One exception matters: if the agent is archived, no turn can be admitted, so the pause is left in place for a future restore. The file also uses an idempotency key, a repeated-use safety label, so if the process crashes after invoking but before retiring, a retry does not create a duplicate turn.

#### Function details

##### `PauseRunner.run`  (lines 34–43)

```
async def run(self) -> None
```

**Purpose**: This is the main tick of the pause runner. It finds pauses that are due now, tries to fire each one, and reports at the end if any of them failed.

**Data flow**: It starts with the runner’s extension context and lease length. It builds a PauseStore, asks it for pauses due at the current UTC time, then sends each pause to _fire. Successful pauses move on silently; failed pauses are remembered by conversation ID and error type. If any failures happened, it raises one combined error so the scheduler or logs can show that the tick was not fully successful.

**Call relations**: A scheduler calls this method each time the recurring job runs. It creates the storage helper, uses the current time to claim due work, and delegates the actual resume-or-retire decision to PauseRunner._fire. It does not stop at the first bad pause; it keeps trying the rest, then raises a summary if needed.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `PauseRunner._fire`  (lines 45–61)

```
async def _fire(self, store: PauseStore, row: Pause) -> None
```

**Purpose**: This function tries to complete one paused wait. It either resumes the conversation with the saved prompt, retires the pause because the wait has ended, or leaves it alone if the agent has been archived.

**Data flow**: It receives a PauseStore and one saved Pause row. First it asks the store to confirm the pause still holds, which prevents firing if its guard conditions no longer apply. If the hold is valid, it invokes the saved prompt in the conversation using the original member’s authority, a fixed idempotency key based on the pause ID, and the recorded conversation watermarks. If the agent is archived, it returns without changing the row. Otherwise, once the invoke step is accepted or safely skipped because a member already spoke, it retires the pause from the active set.

**Call relations**: PauseRunner.run calls this once for each claimed due pause. Inside, it relies on PauseStore.claim_holds to make sure the pause is still legitimate, authority_from_member_id to act on behalf of the member who armed the pause, the extension context to submit the scheduled conversation turn, and PauseStore.retire to mark the wait as finished afterward.

*Call graph*: calls 2 internal fn (claim_holds, retire); called by 1 (run); 1 external calls (authority_from_member_id).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `recurring background tick`

This file is the clock-driven worker for scheduled tasks. Think of it like a careful office clerk who checks a tray every few minutes, takes only the jobs that are due, stamps them so no other clerk works the same job at the same time, and then files each job for its next appointment after it is accepted.

The runner starts each tick by asking the schedule store for tasks that are due now. Each claimed task has a short lease, meaning the claim expires after a few minutes. This protects the system if two runner ticks overlap or if one crashes: a task should not fire twice for the same scheduled occurrence.

Before firing, the runner checks whether the task has expired. If it has, it retires the task instead of running it. Otherwise it works out the next cron time, builds the message that will be delivered to the agent, and uses an idempotency key. An idempotency key is a unique label that lets the system recognize “this exact scheduled run” if it is retried, so it does not create duplicate turns.

If the agent’s app is archived, the runner treats that as neither success nor failure; the task keeps its current occurrence and can try again later. If the turn is accepted, the schedule is advanced. If firing fails, the runner reports the task name and error type after the tick finishes.

#### Function details

##### `fire_body`  (lines 43–61)

```
def fire_body(task: ScheduledTask, runtime_instruction: str | None) -> tuple[str, str]
```

**Purpose**: Builds the message that a scheduled task sends into a conversation, plus the unique key used to recognize this exact scheduled occurrence. This is what turns a stored schedule row into a concrete scheduled run.

**Data flow**: It receives a scheduled task and an optional extra instruction for this run. It reads the task’s next run time and prompt, formats the run time for the agent to read, appends the prompt and any instruction, and creates a scheduled-fire key from the task id and exact occurrence time. It returns the finished inbound message and the key that will be used when admitting the turn.

**Call relations**: ScheduledTaskRunner._fire calls this after it has confirmed the task is still claimed and ready to run. fire_body hands back the message and idempotency key that _fire then passes into the extension context invocation, so retries of the same scheduled occurrence can settle on the same admitted turn instead of duplicating work.

*Call graph*: called by 1 (_fire); 1 external calls (scheduled_fire_key).


##### `ScheduledTaskRunner.run`  (lines 69–78)

```
async def run(self) -> None
```

**Purpose**: Performs one full runner tick: find due scheduled tasks, try to fire each one, and report if any failed. This is the top-level method the recurring job calls on the clock.

**Data flow**: It starts with the runner’s extension context. It creates a ScheduleStore for reading and updating schedules, records the current time, and asks the store for due tasks to claim under a lease. For each claimed task, it calls _fire. It collects any failure names returned by _fire, and if there were failures, it raises one combined error; otherwise it finishes silently.

**Call relations**: This method is the coordinator for the file. It sets up the store and time for the tick, then delegates the per-task decision-making to ScheduledTaskRunner._fire. _fire returns either no problem or a short failure label, and run turns those labels into a single tick-level exception.

*Call graph*: calls 1 internal fn (_fire); 2 external calls (__init__, now).


##### `ScheduledTaskRunner._fire`  (lines 80–117)

```
async def _fire(self, store: ScheduleStore, task: ScheduledTask, tick_at: datetime, expiry_checked_at: datetime) -> str | None
```

**Purpose**: Attempts to run one claimed scheduled task exactly once for its current occurrence. It also decides whether the task should be retired, retried later, or advanced to its next cron time.

**Data flow**: It receives the schedule store, one claimed task, the tick time, and the time used for the expiry check. First it asks the store to retire the task if it has expired. If not expired, it calculates the following cron occurrence. It chooses a normal reporting instruction, or a final-run instruction if the next occurrence would be beyond the task’s expiry time. It then rechecks that the claim still belongs to this runner. If the claim holds, it builds the inbound message and key, invokes the conversation as a scheduled turn using the task creator’s authority, and watches the result. If the app is archived or no turn is admitted, it leaves the task where it is. If a turn is accepted, it reschedules the task to the next fire time. If an unexpected error happens, it returns a readable failure label.

**Call relations**: ScheduledTaskRunner.run calls this once for every due task it claimed. Inside the per-task flow, _fire asks ScheduleStore to retire expired tasks, confirm the lease, and later reschedule successful fires. It uses next_fire to find the next cron time, fire_body to prepare the scheduled message and key, and authority_from_member_id so the scheduled turn runs under the task creator’s permission.

*Call graph*: calls 4 internal fn (fire_body, claim_holds, reschedule, retire_if_expired); called by 1 (run); 2 external calls (authority_from_member_id, next_fire).


### Self-improvement loop
The self-improvement extension proposes prompt changes, replays archived work, judges results, and gates repeated improvements before human-governed promotion.

### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled background tick`

This file is the heartbeat of the self-improvement extension. On each scheduled tick, it reviews recent agent trajectories, grouped by agent, and decides whether there is a prompt change worth proposing. A trajectory is a record of an agent's past conversation or task attempt; the file uses these records as evidence for what the agent is good or bad at.

The important safety idea is that this code never directly changes an agent's prompt. Instead, it may open a proposal for a member to approve. Think of it like a workshop assistant: it can draft a suggested repair, test it more than once, and put it on the manager's desk, but it cannot install the repair itself.

For each agent, the file keeps one candidate prompt in a scoped store, keyed by that agent. The candidate is tied to the current prompt digest, which is a fingerprint of the prompt version it was made for. If the prompt changes later, the old candidate no longer applies and a new one may be opened. If a candidate is rejected or promoted, it is suppressed so the same idea is not proposed again and again.

The loop asks a proposer to create a candidate from a task class, then asks an evaluator to replay and grade held-out examples. Held-out examples are test cases kept aside so the candidate is judged on evidence it was not directly built from. A candidate must pass for a configured number of consecutive scheduled runs before this file submits an AgentChange proposal.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: Runs one full self-improvement tick across all agents that have trajectories. It is the top-level routine for this scheduled pass.

**Data flow**: It asks the extension context for all available trajectories. It groups those records by agent, then sends each agent's group forward for individual processing. It does not return a value; its effect is any candidate state or proposal created during the pass.

**Call relations**: This is the entry into the file's workflow. It uses _by_agent to sort the raw trajectory list into per-agent bundles, then calls ImproveCron._advance once for each agent so the rest of the logic can focus on one agent at a time.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Moves one agent's self-improvement candidate one step forward. It either finds or opens a candidate prompt, then tests whether that candidate is ready to keep, reject, or promote.

**Data flow**: It receives an agent ID and that agent's trajectories. It reads the prompt digest from the first trajectory, builds the store key for this agent, asks for an active candidate or opens a new one, and stops if there is nothing to evaluate. If a candidate exists, it passes the candidate and evidence into the gate step.

**Call relations**: ImproveCron.run calls this after grouping trajectories by agent. This function connects the two main stages: ImproveCron._active_or_open decides what candidate, if any, is under consideration, and ImproveCron._gate decides what happens to that candidate after testing.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: Finds the current candidate prompt for an agent, or creates a new one if appropriate. It prevents duplicate work by reusing an existing evaluating candidate tied to the same prompt version.

**Data flow**: It receives a store key, the current prompt digest, and the agent's trajectories. It first reads the scoped store. If it finds a candidate for this same prompt version and the candidate is still being evaluated, it returns that candidate; if the candidate was already promoted or rejected, it returns nothing. If there is no usable stored candidate, it builds task classes from the trajectories, asks the prompt proposer for a new prompt suggestion, stores the new candidate with its held-out conversation IDs, and returns it.

**Call relations**: ImproveCron._advance calls this before any evaluation happens. It relies on task_classes to organize trajectories into improvement targets, and it creates a CandidateState when the proposer supplies a usable prompt. The returned candidate is then handed to ImproveCron._gate.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Tests a candidate prompt and decides whether it should be rejected, kept under evaluation, or submitted as a proposal. This is the safety gate that stops a candidate from being promoted after only one lucky pass.

**Data flow**: It receives the agent, the candidate, the current prompt digest, and the agent's trajectories. It rebuilds held-out examples: some from the candidate's original task and some from other task classes as a broader safety check. It asks the evaluator to compare the candidate prompt against the current prompt. If the verdict fails, it saves the candidate as rejected with zero passes. If it passes but has not yet reached the required stability count, it saves the increased pass count and keeps evaluating. If it has passed enough consecutive ticks, it creates an AgentChange proposal and saves the candidate as promoted with the proposal ID.

**Call relations**: ImproveCron._advance calls this after a candidate has been found or opened. This function uses _held_out to reconstruct test examples, task_classes to collect broader held-out checks, ImproveCron._save to persist each state change, and AgentChange to package the final governed proposal.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: Writes an updated candidate state to the extension's scoped store. It is used so later scheduled ticks remember what happened to this candidate.

**Data flow**: It receives the store key, the current candidate, and the new status, pass count, and optional proposal ID. It makes a copied candidate with those updated fields, converts it into JSON-friendly data, and stores it under the same key. It returns nothing; the changed stored record is the result.

**Call relations**: ImproveCron._gate calls this whenever an evaluation result changes the candidate's state. By centralizing the store write here, the gate can simply say whether the candidate is rejected, still evaluating, or promoted.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: Sorts a mixed set of trajectories into separate bundles for each agent. This lets the cron process each agent independently.

**Data flow**: It receives a tuple of trajectories. It reads each trajectory's agent ID, groups records with the same agent ID together, and returns a mapping from agent ID to that agent's tuple of trajectories.

**Call relations**: ImproveCron.run uses this at the start of a tick. After this helper separates the records, run calls ImproveCron._advance for each resulting agent group.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: Rebuilds the candidate's held-out test examples from the latest available trajectories. It only includes examples that are still considered bad or flagged, because those are the cases the improved prompt should fix.

**Data flow**: It receives all trajectories for an agent and a tuple of conversation ID strings that were saved with the candidate. It looks up each saved conversation ID in the current trajectories. When the trajectory exists, it asks bad_trajectory whether it represents a problem case; if so, it keeps the associated TaskExample. It returns the collected examples as a tuple.

**Call relations**: ImproveCron._gate calls this when preparing the evaluation set for a candidate. This helper uses bad_trajectory to filter raw trajectory records into meaningful test examples before the evaluator grades the candidate prompt.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).


### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `active whenever the extension asks the model to propose, replay, or grade`

This file is the extension’s “model doorway.” Instead of letting every part of the self-improvement code build its own model request, it defines a narrow shape for the two things the extension needs: ask for a plain text completion, or ask the model to take a tool-using turn. That matters because model calls are metered, tied to a workspace, and easy to configure inconsistently if each caller does it by hand.

The file first defines two protocols. A protocol is like a promise: any object with the named async method can be used, even if it is not a specific class. ModelLeg promises a complete method that returns text. ReplayLeg promises a turn method that returns a full Message, possibly involving tool choices.

ModelAccessLeg is the real adapter. It holds the SDK’s ModelAccess object and turns simple inputs — a system instruction, prior messages, and sometimes tool schemas — into a ModelRequest. It applies shared settings every time: a maximum output size, a short conversation cache time, and reasoning turned off. Like a ticket counter that fills out the official form for every passenger, it keeps the rest of the extension from needing to know the SDK’s full request format.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This defines the minimal promise for something that can ask the model for a text answer. Code can depend on this simple shape instead of depending on the full SDK model object.

**Data flow**: The caller provides a system instruction and a tuple of conversation messages. An implementation is expected to send those to a model and return the model’s answer as plain text.

**Call relations**: This is a protocol method, so it is a contract rather than working code here. ModelAccessLeg.complete is one concrete method that follows this shape, letting other extension code use the simpler ModelLeg idea while the adapter deals with the SDK request.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This defines the minimal promise for something that can ask the model to produce a full conversation turn, including possible tool use. It is useful for replay-style flows where the model must respond in the same structured way it would during an interactive run.

**Data flow**: The caller provides a system instruction, prior messages, and the tools the model is allowed to use. An implementation is expected to pass that information to a model and return a Message representing the model’s next turn.

**Call relations**: This is a protocol method, so it describes what compatible objects must provide. ModelAccessLeg.turn is the concrete version in this file, translating the simple replay request into the SDK’s ModelRequest format.


##### `ModelAccessLeg.complete`  (lines 28–38)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This sends a plain text completion request through the SDK model access object using the extension’s standard settings. It is the safe, shared path for asking the model for text without tools.

**Data flow**: It receives a system instruction and prior messages. It builds a ModelRequest with the selected model name, the messages, a 2048-token output limit, a five-minute conversation cache, and reasoning disabled, then sends that request to the SDK model and returns the resulting text.

**Call relations**: When extension code needs the ModelLeg.complete behavior, this method provides it using the real SDK model connection. Its main handoff is to ModelRequest.__init__, which packages the inputs into the official request object before self.model.complete sends it onward.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 40–53)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This sends a structured model turn request through the SDK model access object, including the tools the model may choose from. It is used when the caller needs a full Message back, not just text.

**Data flow**: It receives a system instruction, prior messages, and tool descriptions. It builds a ModelRequest containing the model name, conversation, token limit, cache setting, available tools, and disabled reasoning, then sends it to the SDK model and returns the Message produced by the model.

**Call relations**: When extension code needs the ReplayLeg.turn behavior, this method supplies it through the real model access layer. It first hands the collected inputs to ModelRequest.__init__ so they are packaged correctly, then passes that request to self.model.turn for the actual model call.

*Call graph*: 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal phase`

This file is part of a self-improvement loop. Its job is to take a current system prompt, look at a task class where the agent had trouble, and ask another model to suggest a careful improvement. A system prompt is the instruction text that shapes how an AI agent behaves. Without this step, the system could collect examples of failure but would not have a way to turn them into a better prompt.

The main worker is PromptProposer. It is given a ModelLeg, which is the model call used for this one step. When asked to propose a change, it first checks whether the task class has mined examples. These examples are real cases where something went wrong or felt difficult. If there are none, it returns nothing, because there is no evidence to improve from.

If examples exist, it builds a clear request for the model: here is the task class, here is the current prompt, and here are a few shortened examples of requests and problems. The system instruction tells the model to make the smallest useful change, preserve the agent’s broader behavior, and return only the full revised prompt.

After the model replies, the file cleans the text. This matters because models sometimes wrap answers in code fences, even when told not to. Finally, it rejects empty replies and unchanged prompts, so later approval steps do not waste time on a no-op.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This function tries to create one improved prompt candidate for a task class the agent struggled with. It only returns a candidate when there are examples to learn from and the suggested prompt is not empty or identical to the current one.

**Data flow**: It receives the current system prompt and a task class. If the task class has no mined examples, it stops and returns nothing. Otherwise, it builds a user-facing prompt with PromptProposer._prompt, sends it with the proposer system instruction to the model, cleans the model’s reply with _clean, compares it against the current prompt, and returns a PromptCandidate containing the task class name and revised prompt if the reply is useful.

**Call relations**: This is the main public action in the file. When the wider self-improvement process wants a possible prompt rewrite, it calls this function. This function delegates the request text construction to PromptProposer._prompt, sends that text through the configured model using a Message object, cleans the returned text with _clean, and packages the successful result as a PromptCandidate.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This function writes the actual request that will be shown to the model. It lays out the task class, the current prompt, and a small set of problem examples so the model has enough context to suggest a focused improvement.

**Data flow**: It receives the current prompt and the task class. It takes up to the configured maximum number of mined examples, shortens each request and problem to the configured character limit, numbers them, and combines them into one plain text instruction. The result is a complete user message asking for the full revised system prompt.

**Call relations**: PromptProposer.propose calls this right before asking the model for a rewrite. It acts like preparing a briefing packet: it gathers the relevant evidence and formats it so the model can respond with a concrete prompt revision.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This helper tidies the model’s answer so the rest of the system can compare and store the proposed prompt safely. In particular, it removes surrounding code fences if the model wrapped the prompt in them.

**Data flow**: It receives raw text from the model. It trims whitespace from the beginning and end. If the answer starts with a triple-backtick code block, it removes the opening fence and a closing fence if present, then trims again. It returns the cleaned prompt text.

**Call relations**: PromptProposer.propose calls this after the model responds. The cleaned text is then checked for being empty or unchanged before being turned into a PromptCandidate, which prevents accidental formatting from making a bad or fake proposal look valid.

*Call graph*: called by 1 (propose).


### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `self-improvement evaluation`

This file is the evidence-gathering step for prompt self-improvement. A new prompt should not be accepted just because it sounds better. It must prove itself on real held-out tasks that were not used to create it.

The main class, CandidateEvaluation, compares two versions of the agent: one using the current prompt and one using the candidate prompt. For each saved task, it replays the same conversation and archived tool behavior, so the prompt is meant to be the only meaningful difference. This is like testing two recipes with the same ingredients and oven, then comparing only the final dish.

After each replay, the file asks a separate judge model a simple question: did this answer satisfy the original user request? The judge is instructed to return only a small JSON result, such as {"accepted": true}. If the judge's reply is missing valid JSON, the answer is treated as not accepted. This conservative behavior matters because unclear judging should not accidentally approve a weak prompt.

The file produces success-or-failure labels for both the old and new prompt. It does this separately for the candidate's target task class and for broader held-out tasks. Those labels are then passed to the two-stage gate, which checks that the candidate improves where it is supposed to improve and does not harm other kinds of tasks.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: This is the main entry point for judging a candidate prompt. It compares the candidate prompt against the current prompt on local held-out tasks and optional global held-out tasks, then asks the gate whether the candidate has earned acceptance.

**Data flow**: It receives the candidate prompt, the current prompt, and two groups of saved task examples. It first turns the local examples into outcome labels, then does the same for the global examples. It gives both sets of labels to the two-stage gate, and returns the gate's final verdict.

**Call relations**: When the self-improvement system needs to decide whether a prompt candidate is good enough, it calls this method. This method delegates the repeated replay-and-judge work to CandidateEvaluation._labels, then hands the gathered evidence to two_stage_gate so the statistical acceptance rule can make the final decision.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: This helper creates the raw pass-or-fail evidence for a group of held-out tasks. For every task, it runs both prompt versions and records whether each resulting answer was accepted by the judge.

**Data flow**: It receives a candidate prompt, the current prompt, and a tuple of saved task examples. It builds a ReplayEvaluation object, then loops through each example twice: once with the current prompt marked as not present, and once with the candidate prompt marked as present. For each replayed answer, it asks CandidateEvaluation._accepts whether the answer satisfies the request, wraps that result in an OutcomeLabel, and finally returns all labels as a tuple.

**Call relations**: CandidateEvaluation.evaluate calls this method once for local tasks and once for global tasks. Inside the loop, this method uses ReplayEvaluation to regenerate an answer under a specific prompt, then calls CandidateEvaluation._accepts to judge that answer before packaging the result as an OutcomeLabel for the gate.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: This helper asks the judge model whether one answer correctly satisfies one user request. It turns the judge's reply into a simple true-or-false decision.

**Data flow**: It receives the original request and the answer produced by replay. It sends both to the judge model in a user message, along with grading instructions that require a JSON object. It searches the judge's text for a JSON-shaped object, tries to parse it, and returns true only when the parsed object is a dictionary with accepted set to true. If the judge response is malformed or cannot be parsed, it returns false.

**Call relations**: CandidateEvaluation._labels calls this method after each replayed answer is produced. This method is the bridge between free-form model judging and the clean boolean success value that OutcomeLabel and the later gate logic need.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `self-improvement evaluation`

This file supports “counterfactual replay”: asking, “What would the model have answered on this old task if we had used a different prompt?” It does this without repeating any real-world actions. That matters because tools may read files, call services, spend money, or change state. A replay should be like watching a recorded cooking show with a new narrator, not turning the stove back on.

The replay starts from an archived conversation. It removes the original final assistant answer, because that is the part being regenerated. It also removes model reasoning blocks, because those belong to the old model run and may be rejected or invalid in a new run. It keeps the user messages, the tool calls, and the tool results that shaped the original task.

The file then builds a small fake tool catalog from the tools that appeared in the archive. When the model asks for a tool, the code does not execute anything. Instead, it looks up the matching archived result using the tool name and a stable version of the input. If the model asks for a tool call that was not in the archive, the replay has “diverged,” meaning it left the safe recorded path. In that case, it returns the best text produced so far. A round limit prevents endless replay loops.

#### Function details

##### `_canonical_input`  (lines 40–41)

```
def _canonical_input(value: object) -> str
```

**Purpose**: Turns a tool input value into a stable text key. This lets the replay compare two tool calls by meaning, not by accidental dictionary ordering.

**Data flow**: It receives any input value, usually the arguments passed to a tool. It converts that value to compact JSON text with keys sorted in a predictable order. The result is a string that can be used as part of a lookup key.

**Call relations**: The archive-indexing step and the replay-feeding step both call this helper. Because both sides use the same conversion, a replayed tool call can be matched against the original archived tool result reliably.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 44–60)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: Prepares the archived conversation for replay by removing the original final answer and stripping reasoning-only content. This gives the model the same task context without showing it the answer it is supposed to regenerate.

**Data flow**: It receives the full archived message history. It walks backward through trailing assistant messages and removes final-answer messages that do not contain tool calls. Then it passes each remaining message through a cleanup step that removes reasoning blocks. It returns the cleaned conversation prefix.

**Call relations**: ReplayEvaluation.replay calls this at the start of a replay. It relies on _without_reasoning for the per-message cleanup before the model is asked to continue the conversation under the new system prompt.

*Call graph*: calls 1 internal fn (_without_reasoning); called by 1 (replay).


##### `_without_reasoning`  (lines 63–71)

```
def _without_reasoning(message: Message) -> Message
```

**Purpose**: Removes hidden or provider-specific reasoning blocks from a message while keeping ordinary text, tool calls, and tool results. This avoids feeding old model reasoning back into a new replay.

**Data flow**: It receives one message. If the message content is plain text, it returns it unchanged. If the content is made of blocks, it filters out thinking or reasoning blocks and builds a new message with the remaining blocks. The returned message has the same role but safer content.

**Call relations**: replay_head calls this for every message that survives the final-answer trimming. It is a small cleanup step that makes the replay context acceptable for the model leg used later.

*Call graph*: called by 1 (replay_head); 1 external calls (__init__).


##### `archived_tool_results`  (lines 74–96)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: Builds a lookup table that connects each archived tool call to the exact tool result it originally received. This is what makes replay safe: tools are answered from the recording instead of being run again.

**Data flow**: It receives the archived messages. First it collects tool result blocks by their tool-use id. Then it scans tool-use blocks and pairs each one with its matching result. For each pair, it stores the result under a key made from the tool name and canonicalized input. It returns that lookup table.

**Call relations**: ReplayEvaluation.replay calls this before the replay loop begins. Later, _feed_archived uses the table to answer the model’s replayed tool requests with archived results.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 99–116)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: Creates a simple tool list containing the distinct tools that appeared in the archived run. This tells the model which tool names it may reproduce during replay.

**Data flow**: It receives the archived messages and scans them for tool-use blocks. It records each tool name once, in the order first seen. It then creates permissive tool schemas for those names, allowing object-shaped inputs with any properties. It returns the schemas as a tuple.

**Call relations**: ReplayEvaluation.replay calls this before asking the model to continue the conversation. The returned tool schemas are passed into the model turn so the model can request the same kinds of calls the archive already contains.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 119–135)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: Answers the model’s current tool requests using saved archive results. If any requested tool call cannot be found in the archive, it signals that the replay has left the recorded path.

**Data flow**: It receives the tool calls the model just asked for and the archived-result lookup table. For each call, it makes the same name-and-input key used when the archive was indexed. If every call has a match, it creates a user message containing tool result blocks with the new call ids but the old result contents. If any call has no match, it returns None.

**Call relations**: ReplayEvaluation.replay calls this whenever the model asks for tools. A successful result is appended to the conversation so replay can continue. A None result tells the replay loop to stop early because it cannot safely answer without executing a real tool.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 148–169)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: Runs the full safe replay for one archived task under one candidate system prompt. It asks the model to regenerate the conversation’s ending while reusing archived tool results and stopping if the replay diverges.

**Data flow**: It receives an archived message history and a system prompt. It builds the archived tool-result lookup, builds the replay tool list, and prepares the cleaned conversation head. Then it repeatedly asks the model for the next assistant turn. If the model gives a final text answer with no tool calls, it returns that text. If the model asks for tools, it tries to feed matching archived results back in. If matching results are missing or the round limit is reached, it returns the latest text it has seen.

**Call relations**: This is the main flow in the file. It calls archived_tool_results, replay_tools, and replay_head for setup, then calls _feed_archived inside the turn-by-turn loop. It returns a ReplayResult that other self-improvement code can grade or compare against other prompt arms.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation before prompt promotion`

This file is the safety gate for prompt self-improvement. A candidate prompt may look better on a few examples just by luck, so the gate asks a stricter question: “Are we confident this prompt improves acceptance, and do we have enough examples to trust that?” It compares two groups, called arms: examples where the candidate prompt was present, and examples where it was absent. Each replay is recorded as an OutcomeLabel: whether the candidate was present and whether the answer was accepted.

The file turns those labels into a Contingency count, like a small scorecard: accepted and total for the candidate group, and accepted and total for the baseline group. It then estimates the improvement in acceptance rate. Instead of trusting the raw difference, it computes a confidence interval, meaning a cautious range of plausible true values. This is like refusing to call a coin “better” after only two lucky flips.

There are two checks. First, score_gate requires the candidate to clear a local improvement floor with enough examples on both sides. Second, global_non_inferior checks other task classes and rejects only when there is confident evidence of harm. two_stage_gate combines both: win locally, and do not clearly regress globally.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This computes a cautious lower estimate for an acceptance rate. Someone uses it when they want to know the low end of what the true success rate might be, rather than trusting a small sample at face value.

**Data flow**: It takes the number of accepted examples, the total number of examples, and a confidence setting. If there are no examples, it returns 0. Otherwise it calculates the observed acceptance rate, adjusts it for uncertainty, and returns a lower bound between 0 and 1.

**Call relations**: The lift calculations call this when they need the pessimistic side of a success rate. It uses square root math to measure uncertainty, then hands that cautious bound back to lift_lower_bound and lift_upper_bound.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This computes a cautious upper estimate for an acceptance rate. It is used when the code needs the optimistic end of what the true success rate might be.

**Data flow**: It takes accepted count, total count, and a confidence setting. If there are no examples, it returns 1, meaning the upper estimate is completely open. Otherwise it calculates the observed rate, adds uncertainty, and returns an upper bound no higher than 1.

**Call relations**: The lift calculations call this alongside wilson_lower_bound. It supplies the optimistic side of a rate so the file can build a confidence range for the difference between candidate and baseline.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: This estimates the cautious lower end of the candidate prompt’s improvement over the baseline. It answers: “Even after allowing for sampling noise, how much better does the candidate look at minimum?”

**Data flow**: It takes a Contingency scorecard with accepted and total counts for candidate-present and candidate-absent examples. If either side has no examples, it returns 0. Otherwise it compares the two observed acceptance rates, subtracts uncertainty from both sides, and returns the lower confidence bound for the lift.

**Call relations**: score_gate calls this after building the scorecard. Inside, it asks wilson_lower_bound and wilson_upper_bound for cautious rate limits, uses square root math to combine their uncertainty, and gives score_gate the number used for the promotion decision.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: This estimates the optimistic upper end of the candidate prompt’s improvement or harm. It is mainly used to decide whether the candidate is clearly bad on other tasks.

**Data flow**: It takes a Contingency scorecard. If one side has no examples, it returns 0. Otherwise it compares candidate-present and candidate-absent acceptance rates, adds uncertainty in the favorable direction, and returns the upper confidence bound for the lift.

**Call relations**: global_non_inferior calls this when checking for regressions outside the local task class. It uses wilson_lower_bound, wilson_upper_bound, and square root math to decide whether even the best plausible reading still shows meaningful harm.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: This turns individual replay results into a compact scorecard. It separates examples where the candidate prompt was present from examples where it was absent, then counts successes in each group.

**Data flow**: It takes a tuple of OutcomeLabel records. It splits them into present and absent groups, counts how many succeeded in each group, counts total examples in each group, and returns a Contingency object containing those four numbers.

**Call relations**: score_gate and global_non_inferior call this first, because both need counts before doing statistical checks. It prepares the raw replay labels for lift_lower_bound and lift_upper_bound.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: This gives the local promotion verdict for the task class the candidate was meant to improve. It requires enough replay examples on both sides and a cautious improvement estimate above the configured floor.

**Data flow**: It takes replay labels, plus optional thresholds for the minimum lift and minimum examples per side. It builds a Contingency scorecard, computes the lower bound on improvement, and returns a GateVerdict. The verdict includes pass or fail, a human-readable reason, the calculated lower bound, and the example counts.

**Call relations**: two_stage_gate calls this as the first checkpoint. score_gate calls contingency to summarize labels and lift_lower_bound to judge improvement, then creates the GateVerdict that either stops the candidate early or lets it continue to the global safety check.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: This checks whether the candidate prompt avoids clearly hurting other task classes. It is intentionally forgiving when evidence is thin: lack of proof is not treated as failure unless the data confidently shows regression.

**Data flow**: It takes replay labels from the broader global set, plus optional margin and minimum-example settings. It builds the present-versus-absent scorecard. If either side has too few examples, it returns True. Otherwise it computes the optimistic upper bound of lift and returns whether that bound is still above the allowed harm margin.

**Call relations**: two_stage_gate calls this only after the local gate has passed. It calls contingency for counts and lift_upper_bound for the best plausible lift, then tells two_stage_gate whether the candidate is safe enough globally.

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: This is the full promotion decision. It requires the candidate to prove a local win and also avoid confident evidence of global harm.

**Data flow**: It takes two sets of replay labels: local labels for the task class being improved, and global labels for other task classes. It first runs score_gate on the local labels. If that fails, it returns that failure. If the local check passes, it runs global_non_inferior. If the global check fails, it returns a new failing GateVerdict with a regression reason. Otherwise it returns the passing local verdict.

**Call relations**: This is the top-level function in this file’s decision flow. It coordinates score_gate and global_non_inferior, so callers can ask one simple question: should this candidate prompt be promoted?

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-persistence-handles` — The shared database and blob-storage connections used to read and save durable system data.
- `reg-feature-flags` — The shared on/off switches that let the service enable or disable behavior at runtime.
- `reg-extension-registry` — The discovered set of installed extensions and the capabilities each extension contributes.
- `reg-workspace-directory` — The shared record of workspaces, members, seats, admins, invitations, and onboarding status.
- `reg-runtime-authority` — The current workspace, agent, and member identity under which work is allowed to act.
- `reg-turn-queue-state` — The durable state of conversation turns, including pending, running, paused, cancelled, and finished work.
- `reg-surface-routing-state` — The saved routing state for web, Slack, iMessage, terminal, and other public conversation surfaces.
- `reg-source-index` — The stored external sources, synced pages, permissions, indexing status, and retry/backoff state.
- `reg-memory-store` — The remembered facts and searchable memory chunks that can be retrieved or condensed later.
- `reg-artifact-blob-store` — The shared files, media blobs, previews, metadata, and signed-download records created by agent work.
- `reg-scheduled-work-store` — The durable records for recurring tasks, delayed resumes, scheduled fires, and background job claims.
- `reg-notification-inbox` — The stored pending notifications and delivery state used to batch notices and wake conversations.
- `reg-monitor-objective-state` — The saved monitors, objectives, plans, steps, evidence, and blocks that survive across turns.
- `reg-runtime-fleet-liveness` — The shared record of running service and worker instances, heartbeats, listener claims, and stuck work.
- `reg-usage-ledger-balance` — The money and usage ledger that tracks costs, prepaid balances, limits, exports, and billing status.
- `reg-telemetry-context` — The shared trace, metric, log, health, and redaction context used to observe work across the system.
- `reg-extension-state-store` — Generic per-workspace extension-owned durable key/value or configuration state not covered by a named core store.
- `reg-self-improvement-state` — Saved prompt-change proposals, replay/evaluation results, promotion gates, and corpus entries used by the self-improvement loop.
- `reg-shared-infra-clients` — Long-lived non-database infrastructure clients and connection pools such as Redis, HTTP, provider, and service clients shared by workers and request handlers.
- `reg-rate-limit-buckets` — Shared throttling counters, leases, and cooldown state for ingress, provider/model calls, connector actions, and background workers, separate from spend-cap accounting.
