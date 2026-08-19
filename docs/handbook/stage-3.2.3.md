# State, objectives, scheduling, and background-job manifests  `stage-3.2.3`

This stage is shared behind-the-scenes support. It is not where the agent does the main work itself. Instead, it is like a set of sign-up sheets that tell the host system what long-lived abilities and background routines exist, when they should run, and what tools or memory they should make available.

The memory manifest registers memory tools, automatic recall, page indexing, cleanup jobs, and search, so past information can be found and kept tidy. The monitors manifest exposes monitor objects and a tool for checking them, plus a clock-based job that runs on a schedule. The objectives manifest adds tools and reminders for long-running goals, so the agent does not lose track of plans, blockers, or unfinished steps between turns. The scheduled-tasks manifest declares task objects, a pause-and-wait tool, scheduled background jobs, and the skill instructions needed to plan timed work. The self-improvement manifest loads evaluation work and defines what happens when that recurring job runs. Together, these manifests make persistent work visible, remembered, and regularly maintained.

## Files in this stage

### Memory state manifest
Declares the persistent memory surface, including tools, recall hooks, indexing hooks, cleanup jobs, and search integration.

### `extensions/memory/ufo_ext_memory/manifest.py`

`orchestration` · `startup, tool handling, prompt hooks, page-change handling, and scheduled background jobs`

This file tells the host application how the memory feature plugs in. Without it, the agent would not know that it can search or write durable memory, old notes would not be indexed for search, source pages would not be turned into memory facts, and background cleanup jobs would never run.

The file defines two tool inputs: one for searching memory and one for writing a new memory item. The search path accepts a few focused queries and optional date limits, then looks in both saved memory items and indexed source pages. The update path writes a durable fact, preference, decision, event, or task-like memory for the current audience.

It also defines an automatic recall hook. Before the model answers a user prompt, this hook tries to find relevant memories and injects a short “Relevant memory” note into the model context. This is deliberately best-effort: if search is slow or broken, it logs the problem and lets the conversation continue.

Finally, the file wires background work: indexing new memory items, indexing changed source pages, deriving facts from page changes, consolidating old facts into summaries, and deduplicating repeated memories. Think of it like the extension’s switchboard: it connects user-facing tools, automatic hooks, and scheduled maintenance to the right memory machinery.

#### Function details

##### `_date_bound`  (lines 158–169)

```
def _date_bound(value: str | None, *, end: bool) -> datetime | None
```

**Purpose**: Turns an optional ISO-style date or date-time string into a timezone-aware UTC boundary for memory search. It makes an end date inclusive for ordinary calendar dates by moving it to the start of the next day.

**Data flow**: It receives either no value or a date string, plus a flag saying whether this is the end of a range. If there is no value, it returns nothing. If there is a value, it parses it, adds UTC when no timezone was supplied, and for a bare end date shifts it forward one day. The result is a datetime object that can be used in a database search window.

**Call relations**: The memory search tool handler calls this before searching, so user-provided start_date and end_date values become clear boundaries for the store query. If parsing fails, the tool reports a recoverable error instead of silently guessing.

*Call graph*: called by 1 (memory_search_handler); 2 external calls (fromisoformat, timedelta).


##### `MemorySearchService.search`  (lines 178–256)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Runs the main memory search workflow used by the memory tool and by other parts of the system that need memory search. It searches saved memories and source-document snippets at the same time, then merges the results fairly across the user’s queries.

**Data flow**: It receives one to three query strings, a source reader that describes what subjects the caller may read, and optional start and end dates. It asks the memory store to recall matching memory items and to search matching source passages for each query in parallel. It then interleaves results from the different queries, removes duplicates, converts each result into a common MemoryMatch shape, and returns one combined tuple of memory matches and source matches.

**Call relations**: Tool handling reaches this through memory_search_handler, and the manifest also exposes MemorySearchService as the extension’s memory search provider. Inside, it asks store_for for the workspace’s memory store, uses concurrent gathers so all query legs run together, and wraps returned items with ObjectRef values so callers can later open the full memory item or page.

*Call graph*: 5 external calls (__init__, __init__, gather, zip_longest, store_for).


##### `MemorySearchService.listable_kinds`  (lines 258–261)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which kinds of memory items can be listed. It reads the allowed item classes from the shared type definition so this file does not need a second hard-coded list.

**Data flow**: It takes no extra input beyond the service instance. It reads the ItemClass type choices and returns them as a tuple of strings. Nothing is changed.

**Call relations**: This supports consumers that browse memory by kind rather than searching by text. It depends on the shared ItemClass definition, so if the store adds a new memory class, this listing helper can expose it automatically.

*Call graph*: 1 external calls (get_args).


##### `MemorySearchService.list_recent`  (lines 263–312)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Returns one page of recent live memory items for specific subjects, newest first. It is the browsing path for memory: no search query, no similarity scoring, just a paged timeline of stored memories.

**Data flow**: It receives a set of subjects, a page size, optional memory kinds to include, and an optional paging cursor. It builds a database query for non-superseded memory items in the current workspace that match those subjects and kinds. It runs the query inside a transaction, applies shared cursor-based paging, and returns a ListingPage of MemoryMatch objects.

**Call relations**: This sits beside full-text memory search as a simpler listing seam for consumers that want recent items. It uses the shared listing helpers page_query and page_of so memory pages behave like other paged lists in the system.

*Call graph*: 3 external calls (select, page_of, page_query).


##### `match_line`  (lines 315–322)

```
def match_line(match: MemoryMatch) -> str
```

**Purpose**: Formats one memory search hit into a readable line for the agent. The line includes the kind, the snippet text, and, when available, a reference and date.

**Data flow**: It receives a MemoryMatch. It starts with a bullet containing the match kind and text. If the match has a reference, it appends that reference and the created date if one exists. It returns a single string ready to show in a tool result.

**Call relations**: memory_search_handler uses this after the search service returns matches. It is the final presentation step before results are packed into TextContent for the model.

*Call graph*: called by 1 (memory_search_handler).


##### `memory_search_handler`  (lines 325–345)

```
async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult
```

**Purpose**: Implements the user-facing memory_search tool. It converts tool arguments into a real search, then returns a plain text list of matching memory items and source snippets.

**Data flow**: It receives the tool context and parsed search arguments. It checks that an extension context exists, converts optional date strings into datetime bounds, builds a source reader from the tool context, and calls MemorySearchService.search. If nothing matches, it returns a ToolResult saying so. Otherwise it formats each match into a line and returns them as one text result.

**Call relations**: manifest registers this as the handler for the memory_search tool. It calls _date_bound to prepare date filters, delegates the actual search to MemorySearchService, and uses match_line to make the returned matches readable.

*Call graph*: calls 3 internal fn (source_reader, _date_bound, match_line); 3 external calls (__init__, __init__, __init__).


##### `memory_update_handler`  (lines 348–362)

```
async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult
```

**Purpose**: Implements the user-facing memory_update tool. It writes a durable memory item for the current conversation audience, such as a preference or stable fact about the user.

**Data flow**: It receives the tool context and parsed write arguments. It checks that an extension context exists, derives the subject from the effective audience, builds a MemoryWrite record with the body, kind, confidence, and optional source reference, and commits it through the memory store. It returns a short confirmation message naming the subject that was written.

**Call relations**: manifest registers this as the handler for the side-effecting memory_update tool. It hands the actual database write to the store returned by store_for, while the handler itself focuses on translating tool input into a memory write.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_for).


##### `recall_hook`  (lines 365–447)

```
async def recall_hook(ctx: HookContext) -> HookOutcome
```

**Purpose**: Automatically adds relevant remembered facts to the model’s context before a user prompt is answered. It is intentionally safe and best-effort: memory recall should help when available, but must not block the user’s turn if it fails.

**Data flow**: It receives a hook context. It first checks that the event is a user prompt and that there is a turn to attach to. It skips certain internal machine-only turns that have no user text. Otherwise it computes readable subjects, creates a SourceReader, and asks the store to recall relevant memory under a short timeout. It filters out topic-only items, truncates long lines, keeps the total injected text under a fixed size, logs which memories were used, and returns InjectContext with a “Relevant memory” block. On timeout or error, it logs and returns nothing.

**Call relations**: manifest registers this for user_prompt_submit, which runs before the model is called. The hook calls recall_subjects to decide what memory is visible, store_for to perform recall, and InjectContext to pass the final note onward. Its design protects the larger prompt flow: even if recall breaks, the turn continues.

*Call graph*: 6 external calls (__init__, __init__, timeout, log, recall_subjects, store_for).


##### `index_memory`  (lines 450–459)

```
async def index_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that turns committed memory items into searchable index chunks. This lets later searches find memories by meaning rather than only by exact text.

**Data flow**: It receives the extension context. It verifies that both the index backend and embedding backend are available. Then it builds a MemoryIndexer with the index, embedding service, transaction function, text chunker, and page-state tracking, and runs it. The result is stored indexing work; the function itself returns nothing.

**Call relations**: manifest registers this as the memory_index job. The job candidates come from _items_awaiting_index, so this handler is run for workspaces that still have memory rows without indexing information.

*Call graph*: 2 external calls (__init__, __init__).


##### `index_pages`  (lines 462–478)

```
async def index_pages(ctx: HookContext) -> HookOutcome
```

**Purpose**: Processes batches of source page changes and indexes their text for memory search. It also mirrors page information needed by the memory extension.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it does nothing. If it is a page-change batch, it checks for the index and embedding backends, builds a PageIndexer with workspace and chunking details, and applies the delivered page changes. It returns no hook outcome.

**Call relations**: manifest registers this as one consumer of page_change events. The core runner owns the cursor and supplies batches; this function applies one batch through PageIndexer so changed pages become searchable.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_facts`  (lines 481–492)

```
async def derive_facts(ctx: HookContext) -> HookOutcome
```

**Purpose**: Turns changed source pages into durable fact memories. This lets the system remember useful facts from synced pages, not just search the page text directly.

**Data flow**: It receives a hook context. If the payload is not a page-change batch, it does nothing. For a real page-change batch, it requires a model backend, creates a FactDeriver with the memory store and model, and asks it to process the changes. The deriver writes replacement facts and retires older page-derived facts as needed.

**Call relations**: manifest registers this as a second page_change consumer, independent from page indexing. It uses store_for to write through the memory store and FactDeriver to perform the model-based distillation.

*Call graph*: 2 external calls (__init__, store_for).


##### `consolidate_memory`  (lines 495–503)

```
async def consolidate_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that combines older related facts into higher-level semantic summaries. This keeps memory useful as it grows by replacing clusters of old small facts with cleaner summaries.

**Data flow**: It receives the extension context. It checks that the embedding backend is available, then builds a MemoryConsolidator with embedding, transaction, workspace, and optional model access. It runs the consolidator, which may create summary memories and supersede originals. The function returns nothing.

**Call relations**: manifest registers this as the memory_consolidate job. Candidate workspaces are selected by _consolidatable_workspaces so the job is aimed at workspaces with enough old live facts to form a useful cluster.

*Call graph*: 1 external calls (__init__).


##### `dedup_memory`  (lines 506–514)

```
async def dedup_memory(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled job that collapses duplicate memory items onto the newest copy. This reduces clutter when the same fact has been stored multiple times.

**Data flow**: It receives the extension context. It checks that embeddings are available, then builds a MemoryDeduper with embedding, transaction, workspace, and store access. It runs the deduper, which finds duplicate groups and marks older copies as superseded. The function returns nothing.

**Call relations**: manifest registers this as the memory_dedup job. Candidate workspaces are selected by _dedupable_workspaces, so the job only runs where there appears to be a duplicate backlog worth sweeping.

*Call graph*: 1 external calls (__init__).


##### `_items_awaiting_index`  (lines 517–522)

```
def _items_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces with memory items still missing embedding/index information. It is used to decide where the indexing job should run.

**Data flow**: It takes no runtime input. It creates a SQL query selecting distinct workspace IDs from memory items whose embedding digest is empty. It returns that query object rather than executing it.

**Call relations**: manifest passes this query builder into owner_candidates for the memory_index job. The job scheduler can then bind indexing work only to workspaces that actually have unindexed memory.

*Call graph*: 1 external calls (select).


##### `_consolidatable_workspaces`  (lines 525–541)

```
def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces where memory consolidation could do useful work. It looks for enough old, live, tool-written fact memories to form a cluster.

**Data flow**: It takes no direct input. It computes an age cutoff based on the current time and the minimum age rule, then builds a SQL query grouped by workspace. The query selects workspaces with at least the required number of eligible facts. It returns the query object without running it.

**Call relations**: manifest gives this to owner_candidates for the memory_consolidate job. That means the scheduler avoids running consolidation in workspaces where facts are too new, too few, already superseded, or page-derived.

*Call graph*: 2 external calls (now, select).


##### `_dedupable_workspaces`  (lines 544–563)

```
def _dedupable_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query that finds workspaces with likely duplicate memory items old enough for a scheduled deduplication sweep. Fresh writes are left to the normal commit path instead.

**Data flow**: It takes no direct input. It computes an age cutoff from the current time and deduplication age rule, then builds a SQL query grouped by workspace, subject, and item class. It keeps groups with at least the required number of live copies and returns distinct workspace IDs. It returns the query object, not the results.

**Call relations**: manifest gives this to owner_candidates for the memory_dedup job. The scheduler uses it to run deduplication only where there is likely a real duplicate group to collapse.

*Call graph*: 2 external calls (now, select).


##### `manifest`  (lines 566–647)

```
def manifest() -> Manifest
```

**Purpose**: Declares the complete memory extension to the host application. It names the extension and registers its tools, object type, hooks, scheduled jobs, search provider, and web surface.

**Data flow**: It takes no input. It constructs ToolDef entries for memory_search and memory_update, HookSpec entries for prompt recall and page changes, JobSpec entries for indexing, consolidation, and deduplication, a MemorySearchProviderSpec, and a SurfaceSpec for the memory UI routes. It returns one Manifest object containing all of these declarations.

**Call relations**: This is the file’s main assembly point. At startup, the host asks for the manifest and learns which handlers to call later: tool requests go to memory_search_handler or memory_update_handler, prompt submission goes to recall_hook, page changes go to index_pages and derive_facts, and scheduled jobs go to index_memory, consolidate_memory, and dedup_memory.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### Ongoing work manifests
Registers monitor, objective, and scheduled-task capabilities that let the system track long-running state and resume time-based work.

### `extensions/monitors/ufo_ext_monitors/manifest.py`

`config` · `extension load and recurring scheduled checks`

This is the extension’s sign-up sheet. It declares the monitors extension by name and version, then lists the pieces the main system should plug in. A “monitor” here means a durable watch: the system repeatedly runs a shell check inside a conversation’s sandbox, on a schedule, and records whether the result is changing or failing over time.

The file connects three things. First, it exposes the monitor tool, which is how users or agents create and work with monitors. Second, it exposes the monitor object kind, which tells the system that monitors are a stored kind of object with their own meaning. Third, it registers a recurring job called the monitor runner. That job wakes up once per minute and asks for only the workspaces that actually have monitors due to be checked, so idle workspaces do not waste effort.

An important detail is that this runner is separate from scheduled tasks. A scheduled task waits until a time and then does a requested action. A monitor keeps probing repeatedly and maintains its own history, such as baselines and streak counts. This file is the small bridge that makes those monitor-specific parts visible to the wider application.

#### Function details

##### `_probe`  (lines 28–29)

```
async def _probe(ctx: ExtensionContext) -> None
```

**Purpose**: This is the small function the scheduler runs when it is time to check monitors. It creates a monitor runner for the current extension context and tells it to do one pass of monitor probing.

**Data flow**: It receives an ExtensionContext, which is the system’s bundle of runtime services and workspace information for this extension. It uses that context to create a MonitorRunner, then waits for the runner to finish its run. It does not return a useful value; its effect is that due monitors may be probed and their stored state may be updated elsewhere by the runner.

**Call relations**: The job declared by manifest uses this function as its scheduled handler. When the clock fires, the platform calls _probe, and _probe hands the real work to MonitorRunner so this file stays focused on registration rather than probe details.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–46)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the official declaration for the monitors extension. The host system calls it to learn what tools, object types, and background jobs this extension provides.

**Data flow**: It starts from constants in this file, such as the extension name, version, job name, and schedule. It includes the monitor tool and monitor object imported from nearby files. It also asks due_monitor_workspaces for a candidate selector, which tells the scheduler which workspaces have monitor work waiting. It returns a Manifest object containing all of that information.

**Call relations**: During extension loading, the host system calls manifest to register the extension. Inside that declaration, it creates a JobSpec for the recurring monitor runner, attaches _probe as the function to call on each tick, and uses due_monitor_workspaces so the scheduler only wakes workspaces that have due monitor checks.

*Call graph*: 3 external calls (__init__, __init__, due_monitor_workspaces).


### `extensions/objectives/ufo_ext_objectives/manifest.py`

`config` · `extension load and user prompt handling`

This file is the front door for the objectives extension. An objective is durable work that lasts beyond one chat turn, such as a multi-step task that may involve waiting, delegation, or later follow-up. The file teaches the agent when to create such an objective, what counts as a real step, and how to record progress without pretending work is done before outside conditions prove it.

The most important behavior is the prompt injection hook. A hook is code the host system runs at a specific moment; here, it runs when a user prompt is submitted. If the current conversation has an objective, the hook looks up its current “frontier”: the unfinished or relevant steps the agent needs to remember. It then inserts a compact objective summary into the agent’s context, including the directive, progress count, open conditions, blockers already raised with the user, and warnings about attempted-but-unverified steps. This is like putting the current job ticket back on the worker’s desk every time they start a shift.

The manifest also registers four objective tools and a standing prompt section. Together, these make objectives both available as actions and visible as expectations during normal agent work.

#### Function details

##### `_inject_frontier`  (lines 51–88)

```
async def _inject_frontier(ctx: HookContext) -> HookOutcome
```

**Purpose**: This function gives the agent a fresh reminder of any active objective whenever a new user turn begins. It prevents long-running work from disappearing just because the agent’s short-term working memory was reset between turns.

**Data flow**: It receives a hook context from the host system. If there is no current turn, it returns nothing. Otherwise it opens an extension database transaction, finds the objective tied to the current conversation and workspace, and stops if none exists. When an objective is found, it records a metric, builds a readable text block containing the objective name, directive, progress, open steps, unmet conditions, blockers, and special warnings, then returns that text as an InjectContext so it can be added to the agent’s prompt.

**Call relations**: This function is registered by manifest as the handler for the user_prompt_submit hook. During that hook, it asks agent_current for the active workspace, creates an Objectives view over the extension storage, uses condition_summary to turn each acceptance condition into readable text, emits a metric about whether the injected frontier includes a blocker, and finally hands the assembled reminder back through InjectContext.

*Call graph*: 5 external calls (__init__, __init__, agent_current, emit_metric, condition_summary).


##### `manifest`  (lines 91–103)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the objectives extension to the host system. It says the extension’s name and version, which tools it provides, which hook should run, and what instructional prompt text should always be available.

**Data flow**: It takes no input. It packages constants from this file, the four imported objective tools, the prompt section text, and the hook specification into a Manifest object. The result is a complete declaration the host can load to activate the extension.

**Call relations**: When the extension is loaded, the host calls this function to learn what the extension contributes. Inside it, HookSpec connects user_prompt_submit events to _inject_frontier, PromptSection wraps the agent-facing guidance, and Manifest combines those pieces with the objective tools so the rest of the system can use them.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `extension startup and recurring job setup`

This file answers the question: “When the scheduled-tasks extension is installed, what should the platform know about it?” Without it, the rest of the system would not know that scheduled tasks exist, would not run the background jobs that wake them up, and would not load the agent guidance for creating them.

The extension declares two separate recurring jobs. One looks for scheduled tasks whose time has arrived. The other looks for paused conversations that are ready to resume. They run on the same clock schedule, but they are deliberately separate. That way, if one kind of work gets stuck, it does not block the other. Think of them as two alarm clocks on the same shelf: one rings for planned tasks, the other rings for pauses ending.

The jobs do not scan every workspace blindly. Each job is given a “candidate” finder that names only workspaces with due work. That keeps the dispatcher from spending effort on workspaces where nothing is waiting.

The file also registers the pause tool, the scheduled-task object kind, a conversation slot used for automations, and a dependency on memory search. Finally, it points the system at the task-scheduling skill folder, so the agent has the right instructions before it tries to schedule anything.

#### Function details

##### `_run`  (lines 35–36)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: This is the job handler for due scheduled tasks. When the scheduler fires this job, it creates a scheduled-task runner and asks it to process the work that is ready.

**Data flow**: It receives an extension context, which is the bundle of services and workspace information the extension can use. It passes that context into a ScheduledTaskRunner, then waits while the runner does its work. It returns nothing; the visible effect is that due scheduled tasks may be invoked.

**Call relations**: The manifest registers this function as the handler for the scheduled-task runner job. When the platform’s job scheduler decides there is due scheduled-task work, it calls this function, which hands control to ScheduledTaskRunner for the actual processing.

*Call graph*: 1 external calls (__init__).


##### `_resume`  (lines 39–40)

```
async def _resume(ctx: ExtensionContext) -> None
```

**Purpose**: This is the job handler for paused conversations that should now continue. When the pause runner job fires, it creates a pause runner and asks it to resume ready pauses.

**Data flow**: It receives the extension context from the platform. It uses that context to build a PauseRunner, then waits for the runner to finish. It returns nothing; the important result is that conversations waiting on a time delay may be resumed.

**Call relations**: The manifest registers this function as the handler for the pause-runner job. The platform calls it when pause records are due, and it delegates the real resume work to PauseRunner.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 43–66)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension’s official declaration for the platform. It says what tools, object types, background jobs, skills, dependencies, and conversation slots belong to scheduled tasks.

**Data flow**: It starts from constants in this file, such as the extension name, version, job names, schedule, and skill folder. It also asks helper functions for workspace candidate finders: one for due scheduled tasks and one for due pauses. It packages all of that into a Manifest object, which the platform can read when loading the extension.

**Call relations**: The platform calls this during extension loading. Inside it, JobSpec objects connect the two recurring jobs to their handlers and candidate finders, SkillSpec objects point to the skill files, and the finished Manifest hands the complete extension declaration back to the host system.

*Call graph*: 5 external calls (__init__, __init__, __init__, due_pause_workspaces, due_task_workspaces).


### Self-improvement job manifest
Defines the self-improvement extension loading behavior and the recurring evaluation job that runs in the background.

### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`config` · `startup registration and scheduled job execution`

This file is the extension’s sign-up sheet. It declares the extension’s name, version, and one scheduled job that runs once an hour. That job is the self-improvement evaluation tick: it proposes possible prompt changes, replays past work, and grades the result so the system can learn whether a change is better.

The important detail is that this job needs access to the same kind of model used during deployment, not a cheaper background model. The reason is practical: replaying an archived conversation can mean sending a large compacted transcript back to the model, and that transcript was prepared to fit the deployment model’s limits.

When the scheduled tick runs, the file first checks that model access has actually been provided. Without that, the self-improvement loop cannot propose or judge anything, so it fails clearly instead of doing silent partial work. It then wraps the provided model in `ModelAccessLeg`, which is a small adapter used by the proposer, replay, and grader pieces. Finally, it builds an `ImproveCron` object with a prompt proposer and candidate evaluator, and asks it to run.

In short, this file does not contain the improvement logic itself. It connects the extension to the host scheduler and wires the main self-improvement parts together at the right time.

#### Function details

##### `_tick`  (lines 26–34)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the actual body of the scheduled self-improvement job. It checks that model access is available, then builds the proposer and evaluator pieces needed to run one improvement cycle.

**Data flow**: It receives an extension context, which includes shared services such as model access. If the context has no model, it raises an error. Otherwise it wraps the model in `ModelAccessLeg`, gives that shared model wrapper to `PromptProposer` and `CandidateEvaluation`, places both into `ImproveCron`, and awaits the cron runner until the cycle is complete. It returns nothing, but it may create proposals, run replays, and produce evaluations through the objects it starts.

**Call relations**: The scheduler calls this function when the job declared by `manifest` fires. Inside that run, `_tick` creates `ModelAccessLeg` so all model-using parts go through the same controlled access path, creates `PromptProposer` to suggest prompt candidates, creates `CandidateEvaluation` to replay and judge them, then hands both to `ImproveCron` to coordinate the full cycle.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 37–50)

```
def manifest() -> Manifest
```

**Purpose**: This function declares the self-improvement extension to the host system. It says the extension’s name and version, and registers the hourly evaluation job that should call `_tick`.

**Data flow**: It takes no input. It creates a `JobSpec` describing the scheduled job: its name, cron-style schedule, handler function, candidate workspaces, and need for the deployment model. It then places that job specification into a `Manifest` object and returns it to the host system.

**Call relations**: The host calls `manifest` while discovering or loading extensions. `manifest` asks `trajectory_workspaces` for the set of workspaces the job can operate on, builds a `JobSpec` that points to `_tick`, and returns a `Manifest` so the scheduler knows what to run and when.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).
